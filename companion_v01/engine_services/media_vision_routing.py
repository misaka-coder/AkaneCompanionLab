"""Media links prefetching, native vision preparation, and execution target routing."""
from __future__ import annotations

import logging
from typing import Any, Callable, Generator, Mapping

from ..tool_runtime import ToolExecutionResult
from ..model_image_inputs import image_source_metadata
from .. import media_bridge_engine

logger = logging.getLogger("akane.engine")


class MediaVisionAndRoutingMixin:
    """Mixin providing media links prefetch, native image/vision preparation, and turn execution target routing."""

    @staticmethod
    def _collect_visible_context_source_ids(
        *,
        recent_raw: list[dict[str, Any]],
        recent_episodic_summaries: list[dict[str, Any]],
        recent_semantic_summaries: list[dict[str, Any]],
        extra_source_ids: list[str] | None = None,
    ) -> list[str]:
        from .memory_facade import collect_visible_context_source_ids as _fn

        return _fn(
            recent_raw=recent_raw,
            recent_episodic_summaries=recent_episodic_summaries,
            recent_semantic_summaries=recent_semantic_summaries,
            extra_source_ids=extra_source_ids,
        )


    def _extract_desktop_screen_frame_images(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        from .turn_context import extract_desktop_screen_frame_images as _fn

        return _fn(payload)

    def _extract_native_user_images(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        raw_images = payload.get("native_user_images") if isinstance(payload, dict) else None
        if not isinstance(raw_images, list):
            return []
        images: list[dict[str, Any]] = []
        for raw in raw_images[:5]:
            if not isinstance(raw, dict):
                continue
            data_url = str(raw.get("data_url") or "").strip()
            if not data_url.startswith("data:image/"):
                continue
            images.append(
                {
                    "data_url": data_url,
                    "attachment_id": str(raw.get("attachment_id") or "").strip(),
                    "attachment_handle": str(raw.get("attachment_handle") or "").strip(),
                    "title": str(raw.get("title") or "").strip()[:80],
                    **image_source_metadata(raw),
                }
            )
        return images

    @staticmethod
    def _build_native_user_image_prompt_context(images: list[dict[str, Any]]) -> str:
        labels: list[str] = []
        for image in images:
            label = (
                str(image.get("attachment_handle") or "").strip()
                or str(image.get("title") or "").strip()
                or str(image.get("attachment_id") or "").strip()
            )
            if label and label not in labels:
                labels.append(label)
        suffix = "、".join(labels[:5]) or f"{len(images)} 张图片"
        return (
            "【本轮原生图片】\n"
            f"系统已通过 provider 原生多模态通道提供 {len(images)} 张当前图片：{suffix}。\n"
            "请直接依据这些原始图片和用户本轮文字回答；工作台里的视觉摘要只是辅助证据。"
            "不要声称只能看到摘要，也不要把旧图片、角色立绘或历史附件当成本轮图片。"
        )

    def _steering_native_user_images(
        self,
        steers: list[Any],
        *,
        applied_source_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        applied = {
            str(source_id or "").strip()
            for source_id in list(applied_source_ids or [])
            if str(source_id or "").strip()
        }
        raw_images: list[dict[str, Any]] = []
        # When several inputs coalesce at one safe boundary, the newest image is
        # the most likely correction to the active task and must not be pushed
        # out by the five-image provider cap.
        for steer in reversed(list(steers or [])):
            source_id = str(getattr(steer, "source_id", "") or "").strip()
            if applied and source_id not in applied:
                continue
            raw_images.extend(
                dict(item)
                for item in list(getattr(steer, "native_user_images", ()) or [])
                if isinstance(item, dict)
            )
        return self._merge_model_image_inputs([], raw_images)

    def _build_desktop_screen_frame_prompt_context(self, frames: list[dict[str, Any]]) -> str:
        from .turn_context import build_desktop_screen_frame_prompt_context as _fn

        return _fn(frames)

    def prefetch_remote_media_links_for_message(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        message: str,
        character_pack_id: str = "",
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        return media_bridge_engine.prefetch_remote_media_links_for_message(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            message=message,
            character_pack_id=character_pack_id,
            timestamp=timestamp,
        )

    def _recent_prefetchable_remote_media_urls(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        limit: int = 24,
    ) -> list[str]:
        return media_bridge_engine.recent_prefetchable_remote_media_urls(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            limit=limit,
        )

    def _message_requests_remote_media_retry(self, message: str) -> bool:
        return media_bridge_engine.message_requests_remote_media_retry(message)

    def _extract_prefetchable_remote_media_urls(self, message: str) -> list[str]:
        return media_bridge_engine.extract_prefetchable_remote_media_urls(message)

    def _message_requests_remote_media_fetch(self, message: str, *, urls: list[str]) -> bool:
        return media_bridge_engine.message_requests_remote_media_fetch(message, urls=urls)

    def wait_for_qq_attachments_settled(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        attachment_ids: list[str],
        timeout_seconds: float = 8.0,
    ) -> dict[str, Any]:
        return media_bridge_engine.wait_for_qq_attachments_settled(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            attachment_ids=attachment_ids,
            timeout_seconds=timeout_seconds,
        )

    def native_chat_vision_status(self, *, chat_model_override: str = "") -> dict[str, Any]:
        """Whether real images can be sent to a chat-capable target this turn.

        This no longer requires the chat and vision routes to be byte-identical.
        A separately configured VISION_* bundle is itself a usable image target;
        a chat bundle explicitly declared image-capable (CHAT_SUPPORTS_IMAGES)
        is the fallback.  Real image parsing still happens independently, so a
        present image marker without parsed bytes never routes as "seen".
        """
        settings = self._runtime_settings_view()
        if not settings.vision_enabled:
            return {"enabled": False, "reason": "vision_disabled"}
        vision_configured = bool(
            settings.vision_api_key and settings.vision_base_url and settings.vision_model_name
        )
        if vision_configured:
            return {
                "enabled": True,
                "reason": "vision_configured",
                "model": settings.vision_model_name,
                "protocol": settings.vision_api_protocol,
            }
        if settings.chat_supports_images:
            chat_model = str(chat_model_override or settings.chat_model_name).strip()
            return {
                "enabled": True,
                "reason": "chat_supports_images",
                "model": chat_model,
                "protocol": settings.chat_api_protocol,
            }
        return {"enabled": False, "reason": "multimodal_model_unavailable"}

    def _resolve_turn_execution_target(
        self,
        *,
        has_real_images: bool,
        tool_image_upgrade: bool = False,
        chat_model_override: str = "",
    ) -> Any:
        """Resolve the immutable per-turn model target through the runtime.

        Falls back to ``None`` for lightweight engines without a routing
        resolver so existing test harnesses keep the legacy override path.
        """
        runtime = getattr(self, "llm", None)
        resolver = getattr(runtime, "resolve_turn_execution_target", None)
        if callable(resolver):
            return resolver(
                has_real_images=has_real_images,
                tool_image_upgrade=tool_image_upgrade,
                chat_model_override=chat_model_override,
            )
        return None

    def _recompute_turn_execution_target(
        self,
        *,
        current_target: Any,
        tool_results: list[ToolExecutionResult],
        chat_model_override: str = "",
    ) -> Any:
        """Re-resolve the model target after a tool batch, one-way only.

        - a vision target never downgrades back to chat;
        - a chat target upgrades to vision only when the tool batch produced
          real ``data:image/...`` model inputs (not OCR text, summaries or plain
          tool feedback);
        - image loading failures produce no upgrade and the batch never guesses.
        """
        tool_images = self._merge_tool_model_image_inputs([], list(tool_results or []))
        return self._upgrade_turn_execution_target_for_images(
            current_target=current_target,
            model_image_inputs=tool_images,
            chat_model_override=chat_model_override,
            tool_image_upgrade=True,
        )

    def _upgrade_turn_execution_target_for_images(
        self,
        *,
        current_target: Any,
        model_image_inputs: list[dict[str, Any]],
        chat_model_override: str = "",
        tool_image_upgrade: bool = False,
    ) -> Any:
        if current_target is not None and str(getattr(current_target, "role", "") or "") == "vision":
            return current_target
        if not self._merge_model_image_inputs([], model_image_inputs):
            return current_target
        return self._resolve_turn_execution_target(
            has_real_images=not tool_image_upgrade,
            tool_image_upgrade=tool_image_upgrade,
            chat_model_override=chat_model_override,
        )

    def prepare_qq_native_image_inputs(self, **kwargs) -> dict[str, Any]:
        """Compatibility adapter for QQ callers; one shared image preparation path."""
        return self.prepare_native_image_inputs(**kwargs)

    def prepare_native_image_inputs(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        attachment_ids: list[str],
        chat_model_override: str = "",
        timeout_seconds: float = 8.0,
    ) -> dict[str, Any]:
        native_status = self.native_chat_vision_status(chat_model_override=chat_model_override)
        if not native_status.get("enabled"):
            return {"ok": False, "status": "disabled", "reason": native_status.get("reason"), "images": []}
        service = self._get_attachment_inbox_service()
        if service is None or not hasattr(service, "build_native_image_inputs"):
            return {"ok": False, "status": "unavailable", "reason": "attachment_service_unavailable", "images": []}
        result = service.build_native_image_inputs(
            profile_user_id=profile_user_id,
            session_id=session_id,
            attachment_ids=attachment_ids,
            timeout_seconds=timeout_seconds,
            max_count=5,
            max_bytes_per_image=self._runtime_settings_view().vision_max_image_bytes,
            max_total_bytes=20 * 1024 * 1024,
        )
        return {**dict(result or {}), "native_vision": native_status}
