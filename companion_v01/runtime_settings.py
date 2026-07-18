"""Immutable per-Bot runtime settings snapshots.

The process-level ``config`` module remains the boot/default source during the
Slice 2 migration.  A BotRuntime captures the effective model settings once so
the LLM client no longer reads mutable module globals during a request.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class BotSettingsView:
    """Effective model settings for one BotRuntime.

    Secret fields are deliberately excluded from repr/public snapshots.  The
    view is immutable so a second Bot cannot be affected by another Bot's
    settings update or request-time provider selection.
    """

    text_api_key: str = field(default="", repr=False)
    text_base_url: str = ""
    text_model_name: str = "deepseek-chat"
    text_api_protocol: str = "auto"
    aux_api_key: str = field(default="", repr=False)
    aux_base_url: str = ""
    aux_model_name: str = "deepseek-chat"
    aux_api_protocol: str = "auto"
    chat_api_key: str = field(default="", repr=False)
    chat_base_url: str = ""
    chat_model_name: str = ""
    chat_api_protocol: str = "auto"
    vision_api_key: str = field(default="", repr=False)
    vision_base_url: str = ""
    vision_model_name: str = ""
    vision_api_protocol: str = "auto"
    vision_enabled: bool = True
    vision_request_timeout: float = 60.0
    vision_prompt_version: str = "v1"
    vision_auto_scene_observe: bool = True
    vision_auto_gift_observe: bool = True
    vision_auto_outfit_observe: bool = True
    vision_max_image_bytes: int = 8 * 1024 * 1024
    prompt_cache_hints_enabled: bool = True
    prompt_cache_hints_force: bool = False
    prompt_cache_namespace: str = "akane"
    prompt_cache_retention: str = ""
    llm_context_window: int = 0
    llm_auto_compact_token_limit: int = 0

    @classmethod
    def from_config(cls, config_module: Any) -> "BotSettingsView":
        return cls(
            text_api_key=_text(getattr(config_module, "TEXT_API_KEY", "")),
            text_base_url=_text(getattr(config_module, "TEXT_BASE_URL", "")),
            text_model_name=_text(getattr(config_module, "TEXT_MODEL_NAME", "deepseek-chat")) or "deepseek-chat",
            text_api_protocol=_text(getattr(config_module, "TEXT_API_PROTOCOL", "auto")) or "auto",
            aux_api_key=_text(getattr(config_module, "AUX_API_KEY", "")),
            aux_base_url=_text(getattr(config_module, "AUX_BASE_URL", "")),
            aux_model_name=_text(getattr(config_module, "AUX_MODEL_NAME", "deepseek-chat")) or "deepseek-chat",
            aux_api_protocol=_text(getattr(config_module, "AUX_API_PROTOCOL", "auto")) or "auto",
            chat_api_key=_text(getattr(config_module, "CHAT_API_KEY", "")),
            chat_base_url=_text(getattr(config_module, "CHAT_BASE_URL", "")),
            chat_model_name=_text(getattr(config_module, "CHAT_MODEL_NAME", "")),
            chat_api_protocol=_text(getattr(config_module, "CHAT_API_PROTOCOL", "auto")) or "auto",
            vision_api_key=_text(getattr(config_module, "VISION_API_KEY", "")),
            vision_base_url=_text(getattr(config_module, "VISION_BASE_URL", "")),
            vision_model_name=_text(getattr(config_module, "VISION_MODEL_NAME", "")),
            vision_api_protocol=_text(getattr(config_module, "VISION_API_PROTOCOL", "auto")) or "auto",
            vision_enabled=bool(getattr(config_module, "VISION_ENABLED", True)),
            vision_request_timeout=max(
                1.0,
                float(getattr(config_module, "VISION_REQUEST_TIMEOUT", 60.0) or 60.0),
            ),
            vision_prompt_version=_text(getattr(config_module, "VISION_PROMPT_VERSION", "v1")) or "v1",
            vision_auto_scene_observe=bool(getattr(config_module, "VISION_AUTO_SCENE_OBSERVE", True)),
            vision_auto_gift_observe=bool(getattr(config_module, "VISION_AUTO_GIFT_OBSERVE", True)),
            vision_auto_outfit_observe=bool(getattr(config_module, "VISION_AUTO_OUTFIT_OBSERVE", True)),
            vision_max_image_bytes=max(
                128 * 1024,
                int(getattr(config_module, "VISION_MAX_IMAGE_BYTES", 8 * 1024 * 1024) or 0),
            ),
            prompt_cache_hints_enabled=bool(getattr(config_module, "PROMPT_CACHE_HINTS_ENABLED", True)),
            prompt_cache_hints_force=bool(getattr(config_module, "PROMPT_CACHE_HINTS_FORCE", False)),
            prompt_cache_namespace=_text(getattr(config_module, "PROMPT_CACHE_NAMESPACE", "akane")) or "akane",
            prompt_cache_retention=_text(getattr(config_module, "PROMPT_CACHE_RETENTION", "")).lower(),
            llm_context_window=max(0, int(getattr(config_module, "LLM_CONTEXT_WINDOW", 0) or 0)),
            llm_auto_compact_token_limit=max(
                0,
                int(getattr(config_module, "LLM_AUTO_COMPACT_TOKEN_LIMIT", 0) or 0),
            ),
        )

    def overlay(self, overrides: Mapping[str, Any] | None = None) -> "BotSettingsView":
        if not overrides:
            return self
        allowed = {
            "text_api_key",
            "text_base_url",
            "text_model_name",
            "text_api_protocol",
            "aux_api_key",
            "aux_base_url",
            "aux_model_name",
            "aux_api_protocol",
            "chat_api_key",
            "chat_base_url",
            "chat_model_name",
            "chat_api_protocol",
            "vision_api_key",
            "vision_base_url",
            "vision_model_name",
            "vision_api_protocol",
            "vision_enabled",
            "vision_request_timeout",
            "vision_prompt_version",
            "vision_auto_scene_observe",
            "vision_auto_gift_observe",
            "vision_auto_outfit_observe",
            "vision_max_image_bytes",
            "prompt_cache_hints_enabled",
            "prompt_cache_hints_force",
            "prompt_cache_namespace",
            "prompt_cache_retention",
            "llm_context_window",
            "llm_auto_compact_token_limit",
        }
        unknown = sorted(str(key) for key in overrides if key not in allowed)
        if unknown:
            raise ValueError(f"bot_settings_unknown_field:{unknown[0]}")
        values = {key: _overlay_value(key, value) for key, value in overrides.items()}
        return replace(self, **values)

    def with_model_service(self, model_settings: Any) -> "BotSettingsView":
        api_key = _text(getattr(model_settings, "api_key", ""))
        base_url = _text(getattr(model_settings, "base_url", ""))
        model = _text(getattr(model_settings, "chat_model", ""))
        protocol = _text(getattr(model_settings, "protocol", "auto")) or "auto"
        use_for_vision = bool(getattr(model_settings, "use_for_vision", True))
        vision_model = _text(getattr(model_settings, "vision_model", "")) or model
        return replace(
            self,
            text_api_key=api_key,
            text_base_url=base_url,
            text_model_name=model,
            text_api_protocol=protocol,
            aux_api_key=api_key,
            aux_base_url=base_url,
            aux_model_name=model,
            aux_api_protocol=protocol,
            chat_api_key=api_key,
            chat_base_url=base_url,
            chat_model_name=model,
            chat_api_protocol=protocol,
            vision_api_key=api_key if use_for_vision else "",
            vision_base_url=base_url if use_for_vision else "",
            vision_model_name=vision_model if use_for_vision else "",
            vision_api_protocol=protocol,
        )

    def public_snapshot(self) -> dict[str, Any]:
        return {
            "text": {
                "base_url": self.text_base_url,
                "model": self.text_model_name,
                "protocol": self.text_api_protocol,
                "configured": _configured(
                    self.text_api_key,
                    self.text_base_url,
                    self.text_model_name,
                    self.text_api_protocol,
                ),
            },
            "aux": {
                "base_url": self.aux_base_url,
                "model": self.aux_model_name,
                "protocol": self.aux_api_protocol,
                "configured": _configured(
                    self.aux_api_key,
                    self.aux_base_url,
                    self.aux_model_name,
                    self.aux_api_protocol,
                ),
            },
            "chat": {
                "base_url": self.chat_base_url,
                "model": self.chat_model_name,
                "protocol": self.chat_api_protocol,
                "configured": _configured(
                    self.chat_api_key,
                    self.chat_base_url,
                    self.chat_model_name,
                    self.chat_api_protocol,
                ),
            },
            "vision": {
                "enabled": self.vision_enabled,
                "base_url": self.vision_base_url,
                "model": self.vision_model_name,
                "protocol": self.vision_api_protocol,
                "configured": self.vision_enabled
                and _configured(
                    self.vision_api_key,
                    self.vision_base_url,
                    self.vision_model_name,
                    self.vision_api_protocol,
                ),
            },
            "prompt_cache": {
                "hints_enabled": self.prompt_cache_hints_enabled,
                "hints_force": self.prompt_cache_hints_force,
                "namespace": self.prompt_cache_namespace,
                "retention": self.prompt_cache_retention,
            },
            "context": {
                "window": self.llm_context_window,
                "auto_compact_token_limit": self.llm_auto_compact_token_limit,
            },
        }


def _text(value: Any) -> str:
    return str(value or "").strip()


def _configured(api_key: str, base_url: str, model: str, protocol: str) -> bool:
    return bool(base_url and model and (protocol == "ollama" or api_key))


def _overlay_value(key: str, value: Any) -> Any:
    if key in {
        "vision_enabled",
        "vision_auto_scene_observe",
        "vision_auto_gift_observe",
        "vision_auto_outfit_observe",
        "prompt_cache_hints_enabled",
        "prompt_cache_hints_force",
    }:
        if not isinstance(value, bool):
            raise ValueError(f"bot_settings_boolean_required:{key}")
        return value
    if key in {
        "vision_max_image_bytes",
        "llm_context_window",
        "llm_auto_compact_token_limit",
    }:
        parsed = int(value)
        if key == "vision_max_image_bytes":
            return max(128 * 1024, parsed)
        return max(0, parsed)
    if key == "vision_request_timeout":
        return max(1.0, float(value))
    text = _text(value)
    if key == "prompt_cache_namespace":
        return text or "akane"
    if key == "prompt_cache_retention":
        return text.lower()
    if key in {"vision_prompt_version", "vision_api_protocol"}:
        return text or ("v1" if key == "vision_prompt_version" else "auto")
    return text


__all__ = ["BotSettingsView"]
