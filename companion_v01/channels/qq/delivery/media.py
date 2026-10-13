"""QQ media delivery policy and its owned state."""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from channelcore_onebot import (
    build_message_action,
    build_upload_file_action,
    image_segment,
    voice_segment,
)

from companion_v01.channels.qq.constants import QQ_INLINE_FILE_MAX_BYTES, QQ_INLINE_IMAGE_MAX_BYTES
from companion_v01.channels.qq.context import QQMessageContext
from companion_v01.channels.qq.validation import _safe_qq_file_name


class QQMediaDelivery:
    """Own media behavior; transport and cross-domain operations use the injected port."""

    def __init__(self, *, gateway: Any) -> None:
        self.gateway = gateway

    def send_image(self, context: QQMessageContext, *, image_path: str, name: str = "") -> dict[str, Any]:
        clean_path = str(image_path or "").strip()
        if not context.target_id or not clean_path:
            return {"ok": False, "reason": "empty_target_or_image"}

        path_obj = Path(clean_path)
        if not path_obj.exists():
            return {"ok": False, "reason": "image_not_found"}

        resolved_path = path_obj.resolve()
        reply_to = self.gateway._reply_reference_for_content(context, ("image",))
        onebot_path = self._onebot_file_path(resolved_path)
        file_candidates: list[tuple[str, str]] = []
        if onebot_path != str(resolved_path):
            file_candidates.append(("shared_path", onebot_path))
        file_candidates.extend(
            [
                ("file_uri", resolved_path.as_uri()),
                ("absolute_path", str(resolved_path)),
            ]
        )
        inline_fallback_skipped = False
        try:
            if resolved_path.stat().st_size <= QQ_INLINE_IMAGE_MAX_BYTES:
                encoded = base64.b64encode(resolved_path.read_bytes()).decode("ascii")
                file_candidates.append(("base64", f"base64://{encoded}"))
            else:
                inline_fallback_skipped = True
        except OSError:
            inline_fallback_skipped = True
        last_result = None
        for transport, file_value in file_candidates:
            try:
                plan = build_message_action(
                    self.gateway._outbound_target(context),
                    [image_segment(file_value, summary=name or path_obj.name)],
                    reply_to=reply_to,
                )
            except ValueError as exc:
                return self.gateway._outbound_plan_failure(exc)
            result = self.gateway._call_and_track_outbound(plan.action, plan.params(), timeout=20)
            if result.get("ok"):
                result["transport"] = transport
                return result
            last_result = result
        result = last_result or self.gateway._onebot_transport.call("unknown", {}).as_dict()
        result["inline_fallback_skipped"] = inline_fallback_skipped
        return result

    def send_voice(
        self,
        context: QQMessageContext,
        *,
        audio_path: str,
        name: str = "",
    ) -> dict[str, Any]:
        clean_path = str(audio_path or "").strip()
        if not context.target_id or not clean_path:
            return {"ok": False, "reason": "empty_target_or_audio"}

        path_obj = Path(clean_path)
        if not path_obj.exists():
            return {"ok": False, "reason": "audio_not_found"}

        resolved_path = path_obj.resolve()
        reply_to = self.gateway._reply_reference_for_content(context, ("record",))
        onebot_path = self._onebot_file_path(resolved_path)
        file_candidates: list[tuple[str, str]] = []
        if onebot_path != str(resolved_path):
            file_candidates.append(("shared_path", onebot_path))
        file_candidates.extend(
            [
                ("file_uri", resolved_path.as_uri()),
                ("absolute_path", str(resolved_path)),
            ]
        )
        last_result = None
        for transport, file_value in file_candidates:
            try:
                plan = build_message_action(
                    self.gateway._outbound_target(context),
                    [voice_segment(file_value, summary=name or path_obj.name)],
                    reply_to=reply_to,
                )
            except ValueError as exc:
                return self.gateway._outbound_plan_failure(exc)
            result = self.gateway._call_and_track_outbound(plan.action, plan.params(), timeout=30)
            if result.get("ok"):
                result["transport"] = transport
                return result
            last_result = result

        staged = self.gateway._onebot_transport.stage_file(
            path_obj,
            filename=name or path_obj.name,
        )
        if staged.ok:
            try:
                plan = build_message_action(
                    self.gateway._outbound_target(context),
                    [voice_segment(staged.file_ref, summary=name or path_obj.name)],
                    reply_to=reply_to,
                )
            except ValueError as exc:
                return self.gateway._outbound_plan_failure(exc)
            streamed_result = self.gateway._call_and_track_outbound(plan.action, plan.params(), timeout=30)
            if streamed_result.get("ok"):
                result = streamed_result
                result["transport"] = "stream_upload"
                return result
            last_result = streamed_result

        try:
            if path_obj.stat().st_size <= QQ_INLINE_FILE_MAX_BYTES:
                inline_ref = "base64://" + base64.b64encode(path_obj.read_bytes()).decode("ascii")
                plan = build_message_action(
                    self.gateway._outbound_target(context),
                    [voice_segment(inline_ref, summary=name or path_obj.name)],
                    reply_to=reply_to,
                )
                inline_result = self.gateway._call_and_track_outbound(plan.action, plan.params(), timeout=30)
                if inline_result.get("ok"):
                    result = inline_result
                    result["transport"] = "base64"
                    return result
                last_result = inline_result
        except (OSError, ValueError):
            pass
        result = last_result or self.gateway._onebot_transport.call("unknown", {}).as_dict()
        if not staged.ok:
            result["staging_reason"] = staged.code
        return result

    def send_voice_url(self, context: QQMessageContext, *, audio_url: str, name: str = "") -> dict[str, Any]:
        """Send an already preflighted public audio URL as a QQ voice message."""
        clean_url = str(audio_url or "").strip()
        if not context.target_id or not clean_url:
            return {"ok": False, "reason": "empty_target_or_audio_url"}
        try:
            plan = build_message_action(
                self.gateway._outbound_target(context),
                [voice_segment(clean_url, summary=str(name or "网络音频").strip() or "网络音频")],
                reply_to=self.gateway._reply_reference_for_content(context, ("record",)),
            )
        except ValueError as exc:
            return self.gateway._outbound_plan_failure(exc)
        result = self.gateway._send_outbound_plan(plan, timeout=30)
        result["transport"] = "public_url"
        return result

    def send_file(self, context: QQMessageContext, *, file_path: str, name: str = "") -> dict[str, Any]:
        clean_path = str(file_path or "").strip()
        if not context.target_id or not clean_path:
            return {"ok": False, "reason": "empty_target_or_file"}

        path_obj = Path(clean_path)
        if not path_obj.exists():
            return {"ok": False, "reason": "file_not_found"}
        requested_name = name or path_obj.name
        safe_name = _safe_qq_file_name(requested_name, fallback=path_obj.name)
        resolved_path = path_obj.resolve()
        onebot_path = self._onebot_file_path(resolved_path)
        direct_candidates: list[tuple[str, str]] = []
        if onebot_path != str(resolved_path):
            direct_candidates.append(("shared_path", onebot_path))
        direct_candidates.append(("local_path", str(resolved_path)))
        direct_result: dict[str, Any] = {"ok": False, "reason": "file_delivery_not_attempted"}
        for transport, file_ref in direct_candidates:
            try:
                plan = build_upload_file_action(
                    self.gateway._outbound_target(context),
                    file_ref=file_ref,
                    name=safe_name,
                )
            except ValueError as exc:
                return self.gateway._outbound_plan_failure(exc)
            direct_result = self.gateway._send_outbound_plan(plan, timeout=60)
            if direct_result.get("ok"):
                direct_result["transport"] = transport
                if safe_name != requested_name:
                    direct_result["filename_adjusted"] = True
                    direct_result["display_name"] = safe_name
                return direct_result

        staged = self.gateway._onebot_transport.stage_file(path_obj, filename=safe_name)
        if staged.ok:
            try:
                staged_plan = build_upload_file_action(
                    self.gateway._outbound_target(context),
                    file_ref=staged.file_ref,
                    name=safe_name,
                )
            except ValueError as exc:
                return self.gateway._outbound_plan_failure(exc)
            staged_result = self.gateway._send_outbound_plan(staged_plan, timeout=30)
            if staged_result.get("ok"):
                staged_result["transport"] = "stream_upload"
                if safe_name != requested_name:
                    staged_result["filename_adjusted"] = True
                    staged_result["display_name"] = safe_name
                return staged_result
            direct_result = staged_result

        try:
            if path_obj.stat().st_size <= QQ_INLINE_FILE_MAX_BYTES:
                inline_ref = "base64://" + base64.b64encode(path_obj.read_bytes()).decode("ascii")
                inline_plan = build_upload_file_action(
                    self.gateway._outbound_target(context),
                    file_ref=inline_ref,
                    name=safe_name,
                )
                inline_result = self.gateway._send_outbound_plan(inline_plan, timeout=30)
                if inline_result.get("ok"):
                    inline_result["transport"] = "base64"
                    if safe_name != requested_name:
                        inline_result["filename_adjusted"] = True
                        inline_result["display_name"] = safe_name
                    return inline_result
                direct_result = inline_result
        except (OSError, ValueError):
            pass
        if not staged.ok:
            direct_result["staging_reason"] = staged.code
        return direct_result

    def _onebot_file_path(self, path: Path) -> str:
        if self.gateway._channel_config is None:
            return str(path)
        return self.gateway._channel_config.project_local_file(path)
