"""Instance model bundles, settings and immutable per-turn routing."""

from __future__ import annotations

from typing import Any
import logging

from ..model_service_config import normalize_provider_model_id
from ..runtime_settings import BotSettingsView
from .models import ModelBundle, ModelExecutionTarget
import config


logger = logging.getLogger("akane.llm_runtime")


DEFAULT_MEMCORE_SUMMARY_MODEL_NAME = ""


def _safe_chat_model_override(value: Any) -> str:
    return normalize_provider_model_id(value)


class ModelTargetsMixin:
    """Methods use the state initialized by LLMRuntime."""

    def reload_from_config(self, *, settings: BotSettingsView | None = None) -> dict[str, str]:
        self.settings = settings or BotSettingsView.from_config(self._config_module)
        aux = self._build_aux_bundle()
        chat = self._build_chat_bundle()
        memcore_summary = self._build_memcore_summary_bundle(chat_bundle=chat)
        vision = self._build_vision_bundle(chat_bundle=chat)
        with self._bundle_lock:
            self.aux = aux
            self.chat = chat
            self.memcore_summary = memcore_summary
            self.vision = vision
        return {
            "status": "reloaded",
            "auxModel": aux.model,
            "chatModel": chat.model,
            "memcoreSummaryModel": memcore_summary.model if memcore_summary is not None else chat.model,
        }

    def _build_aux_bundle(self) -> ModelBundle:
        settings = self._settings_view()
        client = self._build_llm_client(
            api_key=settings.aux_api_key,
            base_url=settings.aux_base_url,
            protocol=settings.aux_api_protocol,
            timeout=90.0,
            max_retries=0,
        )
        setattr(client, "_akane_bundle_role", "aux")
        return ModelBundle(client=client, model=settings.aux_model_name)

    def _build_chat_bundle(self) -> ModelBundle:
        settings = self._settings_view()
        client = self._build_llm_client(
            api_key=settings.chat_api_key,
            base_url=settings.chat_base_url,
            protocol=settings.chat_api_protocol,
            timeout=120.0,
            max_retries=0,
        )
        setattr(client, "_akane_bundle_role", "chat")
        return ModelBundle(client=client, model=settings.chat_model_name)

    def _build_memcore_summary_bundle(self, *, chat_bundle: ModelBundle | None = None) -> ModelBundle | None:
        """An explicit summary model stays fixed; omission follows session chat."""
        config_module = getattr(self, "_config_module", config)
        dedicated_key = str(getattr(config_module, "MEMCORE_SUMMARY_API_KEY", "") or "").strip()
        dedicated_base = str(getattr(config_module, "MEMCORE_SUMMARY_BASE_URL", "") or "").strip()
        dedicated_model = str(
            getattr(config_module, "MEMCORE_SUMMARY_MODEL_NAME", "") or ""
        ).strip()
        if not dedicated_model:
            return None
        dedicated_protocol = str(
            getattr(config_module, "MEMCORE_SUMMARY_API_PROTOCOL", "auto") or "auto"
        ).strip()
        if dedicated_key and dedicated_base:
            client = self._build_llm_client(
                api_key=dedicated_key,
                base_url=dedicated_base,
                protocol=dedicated_protocol,
                timeout=90.0,
                max_retries=0,
            )
            setattr(client, "_akane_bundle_role", "memcore_summary")
            return ModelBundle(client=client, model=dedicated_model)

        if dedicated_key or dedicated_base:
            logger.warning("MemCore summary route unavailable: configure both API key and base URL")
            return ModelBundle(client=None, model=dedicated_model)
        chat = chat_bundle if chat_bundle is not None else self.chat
        return ModelBundle(client=chat.client, model=dedicated_model)

    def _build_vision_bundle(self, *, chat_bundle: ModelBundle | None = None) -> ModelBundle | None:
        settings = self._settings_view()
        if not settings.vision_enabled:
            return None
        vision_key = settings.vision_api_key
        vision_base = settings.vision_base_url
        vision_model = settings.vision_model_name
        vision_protocol = settings.vision_api_protocol
        if not (vision_key and vision_base and vision_model):
            return None
        chat_key = settings.chat_api_key
        chat_base = settings.chat_base_url
        chat_model = settings.chat_model_name
        chat_protocol = settings.chat_api_protocol
        # Same provider route as the chat bundle?  Reuse the chat client so a
        # single configured route is not duplicated and requests stay on the
        # exact endpoint the operator configured.  Routing still flows through
        # the same target resolver either way.
        if (
            vision_key == chat_key
            and vision_base.rstrip("/").lower() == chat_base.rstrip("/").lower()
            and vision_protocol.lower() == chat_protocol.lower()
            and vision_model == chat_model
        ):
            return chat_bundle if chat_bundle is not None else self.chat
        try:
            client = self._build_llm_client(
                api_key=vision_key,
                base_url=vision_base,
                protocol=vision_protocol,
                timeout=settings.vision_request_timeout,
                max_retries=0,
            )
        except Exception as exc:
            logger.warning("Vision bundle client init failed: %s", exc)
            return None
        setattr(client, "_akane_bundle_role", "vision")
        return ModelBundle(client=client, model=vision_model)

    def vision_bundle(self) -> ModelBundle | None:
        with self._bundle_lock:
            return getattr(self, "vision", None)

    def chat_supports_images(self) -> bool:
        return bool(self._settings_view().chat_supports_images)

    def vision_target_status(self, *, chat_model_override: str = "") -> dict[str, Any]:
        """Structured answer to “can real images be sent to a chat target this turn?”.

        This replaces the old hard equality between the chat and vision routes:
        a separately configured VISION_* bundle is itself a usable image target,
        and a chat bundle explicitly declared image-capable is the fallback.
        """
        settings = self._settings_view()
        if not settings.vision_enabled:
            return {"enabled": False, "reason": "vision_disabled"}
        vision_bundle = self.vision_bundle()
        if vision_bundle is not None:
            client = getattr(vision_bundle, "client", vision_bundle)
            protocol = str(
                getattr(client, "_akane_protocol", getattr(client, "protocol", "")) or ""
            ).strip().lower()
            return {
                "enabled": True,
                "reason": "vision_configured",
                "model": vision_bundle.model,
                "protocol": protocol,
            }
        if self.chat_supports_images():
            bundle = self._chat_bundle_for_override(chat_model_override)
            return {
                "enabled": True,
                "reason": "chat_supports_images",
                "model": bundle.model,
                "protocol": self._bundle_protocol(bundle),
            }
        return {"enabled": False, "reason": "multimodal_model_unavailable"}

    def resolve_turn_execution_target(
        self,
        *,
        has_real_images: bool,
        tool_image_upgrade: bool = False,
        chat_model_override: str = "",
    ) -> ModelExecutionTarget | dict[str, Any]:
        """Deterministically pick the immutable model target for one user turn.

        Decision order (no model-name guessing, no text heuristics):
          1. no real image this turn          -> chat target (or override)
          2. image present + vision bundle    -> vision target
          3. image present + no vision bundle + chat explicitly supports images
                                              -> chat target
          4. otherwise                        -> structured multimodal unavailable
        ``tool_image_upgrade`` only ever upgrades chat -> vision and is never
        used to downgrade a vision target back to chat.
        """
        if not has_real_images and not tool_image_upgrade:
            bundle = self._chat_bundle_for_override(chat_model_override)
            return ModelExecutionTarget(
                role="chat",
                bundle=bundle,
                model=bundle.model,
                reason="text_only",
            )
        if not self._settings_view().vision_enabled:
            return {
                "status": "unavailable",
                "reason": "vision_disabled",
                "required_modalities": ["image"],
            }
        vision_bundle = self.vision_bundle()
        if vision_bundle is not None:
            return ModelExecutionTarget(
                role="vision",
                bundle=vision_bundle,
                model=vision_bundle.model,
                reason="tool_image_upgrade" if tool_image_upgrade else "native_image_present",
            )
        if self.chat_supports_images():
            bundle = self._chat_bundle_for_override(chat_model_override)
            return ModelExecutionTarget(
                role="chat",
                bundle=bundle,
                model=bundle.model,
                reason="chat_supports_images",
            )
        return {
            "status": "unavailable",
            "reason": "multimodal_model_unavailable",
            "required_modalities": ["image"],
        }

    @staticmethod
    def _bundle_protocol(bundle: ModelBundle) -> str:
        client = getattr(bundle, "client", bundle)
        return str(getattr(client, "_akane_protocol", getattr(client, "protocol", "")) or "").strip().lower()

    def _request_bundle(
        self,
        *,
        execution_target: ModelExecutionTarget | None = None,
        chat_model_override: str = "",
    ) -> ModelBundle:
        if execution_target is not None:
            return execution_target.bundle
        return self._chat_bundle_for_override(chat_model_override)

    def _settings_view(self) -> BotSettingsView:
        current = getattr(self, "settings", None)
        if isinstance(current, BotSettingsView):
            return current
        config_module = getattr(self, "_config_module", config)
        current = BotSettingsView.from_config(config_module)
        self.settings = current
        return current

    def _chat_bundle_for_override(self, chat_model_override: str = "") -> ModelBundle:
        model_override = _safe_chat_model_override(chat_model_override)
        with self._bundle_lock:
            bundle = self.chat
        if not model_override:
            return bundle
        return ModelBundle(client=bundle.client, model=model_override)

    def chat_supports_deepseek_thinking(self, *, chat_model_override: str = "") -> bool:
        return self._supports_deepseek_thinking_control(
            self._chat_bundle_for_override(chat_model_override)
        )
