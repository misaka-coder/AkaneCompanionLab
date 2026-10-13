"""QQFollowupRuntime: per-Bot state and explicit conversation dependencies."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from typing import Any

from companion_v01.channels.qq.inbound.attachment_context import (
    _build_qq_image_vision_followup_note,
    _qq_image_followup_failure_message,
)
from companion_v01.channels.qq.inbound.attachments import (
    qq_attachment_ready_wait_seconds as _qq_attachment_ready_wait_seconds,
)
from companion_v01.channels.qq.inbound.attachments import (
    qq_pending_image_attachment_ids as _qq_pending_image_attachment_ids,
)


class QQFollowupRuntime:
    def __init__(
        self, *, engine, config_module, qq_gateway, logger, log_event,
        prepare_payload, run_turn,
    ):
        self.engine = engine
        self.config_module = config_module
        self.qq_gateway = qq_gateway
        self.logger = logger
        self.log_event = log_event
        self.prepare_payload = prepare_payload
        self.run_turn = run_turn

    async def resume_after_decision(self,
        *,
        context: Any,
        event: dict[str, Any],
        decision: str,
        capability_id: str,
        action_id: str,
        authorization_profile_user_id: str,
    ) -> None:
        """Resume through the normal turn path without inventing a user message."""

        decision_label = "批准" if decision == "approved" else "拒绝"
        target = action_id or capability_id or "该动作"
        original_actor_profile = str(authorization_profile_user_id or "").strip()
        original_user_id = int(getattr(context, "user_id", 0) or 0)
        if original_actor_profile.startswith("qq_") and original_actor_profile[3:].isdigit():
            original_user_id = int(original_actor_profile[3:])
        elif original_actor_profile == "master":
            original_user_id = int(getattr(self.qq_gateway, "master_qq", 0) or original_user_id)
        resume_context = replace(
            context,
            source_message_id="",
            clean_message="",
            raw_message="",
            user_id=original_user_id,
            sender_label=(
                str(getattr(context, "sender_label", "") or "")
                if original_user_id == int(getattr(context, "user_id", 0) or 0)
                else f"QQ {original_user_id}"
            ),
            actor_profile_user_id=original_actor_profile,
        )
        turn_payload = resume_context.to_turn_payload()
        turn_payload.update(
            {
                "message": f"【宿主能力审批结果】用户已{decision_label} {target}。请从刚才暂停的位置继续；若被拒绝，改用不需要该权限的方案或如实说明。",
                "memory_message": "",
                "source_message_id": "",
                "timestamp": int(time.time()),
                "turn_kind": "capability_approval_resume",
                "transient_user_message": True,
                "message_addressing": {
                    "mode": "current_request",
                    "trigger": "capability_approval_resume",
                    "addressed_to_assistant": True,
                    "explicit_assistant_mention": False,
                    "primary_target": {"actor_id": "assistant"},
                    "mentions": [],
                },
            }
        )
        try:
            await self.run_turn(
                context=resume_context,
                event={**event, "time": int(time.time()), "message_id": ""},
                turn_payload=turn_payload,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error_logger = getattr(self.logger, "error", None)
            if callable(error_logger):
                error_logger("qq approval auto-resume failed: %s", exc.__class__.__name__)
            self.log_event(
                "qq_capability_approval_resume_failed",
                session_id=str(getattr(context, "session_id", "") or ""),
                profile_user_id=str(getattr(context, "profile_user_id", "") or ""),
                capability_id=capability_id,
                action_id=action_id,
                decision=decision,
                reason=exc.__class__.__name__,
            )

    async def vision_followup(self,
        *,
        context: Any,
        event: dict[str, Any],
        attachment_ids: list[str],
        attachments_registered: list[dict[str, Any]],
        message_override: str = "",
        action_note: str = "",
        plugin_input: dict | None = None,
    ) -> None:
        started_at = time.perf_counter()
        try:
            if bool(getattr(context, "is_group", False)) and not self.qq_gateway.is_group_vision_enabled(
                getattr(context, "group_id", 0)
            ):
                self.log_event(
                    "qq_image_vision_followup_skipped",
                    session_id=context.session_id,
                    profile_user_id=context.profile_user_id,
                    reason="group_vision_disabled",
                    attachment_count=len(getattr(context, "attachments", None) or []),
                    duration_ms=round((time.perf_counter() - started_at) * 1000, 1),
                )
                return
            wait_result = await asyncio.to_thread(
                self.engine.wait_for_qq_attachments_settled,
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                attachment_ids=attachment_ids,
                timeout_seconds=_qq_attachment_ready_wait_seconds(context, self.config_module),
            )
            pending_image_ids = _qq_pending_image_attachment_ids(attachments_registered, wait_result)
            kinds_by_id = wait_result.get("kinds_by_id") if isinstance(wait_result.get("kinds_by_id"), dict) else {}
            native_images: list[dict[str, Any]] = []
            native_prepare = getattr(self.engine, "prepare_qq_native_image_inputs", None)
            if callable(native_prepare) and any(kind == "image" for kind in kinds_by_id.values()):
                native_result = await asyncio.to_thread(
                    native_prepare,
                    profile_user_id=context.profile_user_id,
                    session_id=context.session_id,
                    attachment_ids=attachment_ids,
                    chat_model_override=str(getattr(context, "chat_model_override", "") or ""),
                    timeout_seconds=0.0,
                )
                native_images = [
                    dict(item) for item in list(native_result.get("images") or [])
                    if isinstance(item, dict) and str(item.get("data_url") or "").startswith("data:image/")
                ][:5]
                native_ids = {str(item.get("attachment_id") or "") for item in native_images}
                pending_image_ids = [item_id for item_id in pending_image_ids if item_id not in native_ids]
            else:
                native_ids = set()
            failed_image_ids = [
                str(item or "").strip()
                for item in list(wait_result.get("failed") or [])
                if str(item or "").strip() and str(kinds_by_id.get(str(item or "").strip()) or "").lower() == "image"
                and str(item) not in native_ids
            ]
            if pending_image_ids or failed_image_ids:
                failure_send_result = await asyncio.to_thread(
                    self.qq_gateway.send_reply,
                    context,
                    _qq_image_followup_failure_message(
                        wait_result=wait_result if isinstance(wait_result, dict) else {},
                        pending_image_ids=pending_image_ids,
                        failed_image_ids=failed_image_ids,
                    ),
                )
                self.log_event(
                    "qq_image_vision_followup_not_ready",
                    session_id=context.session_id,
                    profile_user_id=context.profile_user_id,
                    pending_count=len(pending_image_ids),
                    failed_count=len(failed_image_ids),
                    attachment_count=len(getattr(context, "attachments", None) or []),
                    attachments_registered=len(attachments_registered),
                    failure_notice_sent=bool(failure_send_result.get("ok")),
                    duration_ms=round((time.perf_counter() - started_at) * 1000, 1),
                )
                return

            effective_message_override = (
                message_override.strip() or str(getattr(context, "clean_message", "") or "").strip()
                or "用户刚刚发送了一张图片。请只根据本轮实际提供的图片或视觉摘要自然回应。"
            )
            turn_payload = self.prepare_payload(
                context=context,
                event=event,
                message_override=effective_message_override,
                action_note=action_note,
                extra_context_note=_build_qq_image_vision_followup_note(
                    attachment_ids=attachment_ids,
                    wait_result=wait_result if isinstance(wait_result, dict) else {},
                    event_timestamp=int(event.get("time") or time.time()),
                ),
            )
            if attachment_ids:
                turn_payload["qq_current_attachment_ids"] = list(attachment_ids)
            if native_images:
                turn_payload["native_user_images"] = native_images
            turn_payload.update(plugin_input or {})
            turn_result = await self.run_turn(context=context, event=event, turn_payload=turn_payload)
            send_result = dict(
                turn_result.get("send_result") or {"ok": False, "reason": "missing_send_result", "results": []}
            )
            self.log_event(
                "qq_image_vision_followup_sent",
                session_id=context.session_id,
                profile_user_id=context.profile_user_id,
                sent=bool(send_result.get("ok")),
                attachment_count=len(getattr(context, "attachments", None) or []),
                attachments_registered=len(attachments_registered),
                duration_ms=round((time.perf_counter() - started_at) * 1000, 1),
            )
        except Exception as exc:
            self.logger.exception("qq image vision followup failed")
            self.log_event(
                "qq_image_vision_followup_error",
                session_id=getattr(context, "session_id", ""),
                profile_user_id=getattr(context, "profile_user_id", ""),
                reason=str(exc)[:500],
                duration_ms=round((time.perf_counter() - started_at) * 1000, 1),
            )
