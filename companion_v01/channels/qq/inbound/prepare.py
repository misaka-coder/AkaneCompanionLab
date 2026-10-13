"""Prepare QQ references and attachments, including native pixels or a follow-up."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field, replace
from typing import Any

from fastapi.responses import JSONResponse

from companion_v01.channels.qq.inbound.attachment_context import _with_qq_sender_context
from companion_v01.channels.qq.inbound.attachments import (
    qq_attachment_ready_wait_seconds as _qq_attachment_ready_wait_seconds,
)
from companion_v01.channels.qq.inbound.attachments import (
    qq_pending_image_attachment_ids as _qq_pending_image_attachment_ids,
)
from companion_v01.channels.qq.inbound.references import prepare_message_references


@dataclass
class QQPreparedContent:
    context: Any
    attachments_registered: list[dict[str, Any]] = field(default_factory=list)
    attachment_ids: list[str] = field(default_factory=list)
    native_user_images: list[dict[str, Any]] = field(default_factory=list)
    extra_context_note: str = ""
    response: JSONResponse | None = None


class QQContentPreparation:
    def __init__(
        self, *,
        engine, config_module, qq_gateway, runtime_metrics, log_event, plugin_inputs,
        followups,
    ):
        self.engine = engine
        self.config_module = config_module
        self.qq_gateway = qq_gateway
        self.runtime_metrics = runtime_metrics
        self.log_event = log_event
        self.plugin_inputs = plugin_inputs
        self.followups = followups

    async def prepare(
        self, *, context, event, pre_resolved_quote, extra_context_note, action_note,
        message_override, plugin_inbound, started_at,
    ) -> QQPreparedContent:
        native_user_images: list[dict[str, Any]] = []
        context, extra_context_note = await prepare_message_references(
            context=context, event=event, gateway=self.qq_gateway,
            pre_resolved_quote=pre_resolved_quote, extra_context_note=extra_context_note,
            log_event=self.log_event,
        )

        images_allowed = not context.is_group or self.qq_gateway.is_group_vision_enabled(context.group_id)
        if not images_allowed:
            attachments = list(context.attachments or [])
            allowed_attachments = [
                item
                for item in attachments
                if not (isinstance(item, dict) and str(item.get("kind") or "").strip().lower() == "image")
            ]
            blocked_image_count = len(attachments) - len(allowed_attachments)
            if blocked_image_count or any(
                isinstance(item, dict) and str(item.get("kind") or "") in {"file", "document"}
                for item in allowed_attachments
            ):
                context = replace(context, attachments=allowed_attachments)
                vision_disabled_note = (
                    "【本群识图设置】本群已关闭图片识别；包括以文件发送的图片，本轮未送入视觉分析。"
                    "文件可能只登记基本信息，不代表已看过图片内容。"
                    "不要声称看到了图片；如果用户询问图片内容，请简短说明本群识图已关闭。"
                )
                extra_context_note = "\n".join(
                    part for part in (extra_context_note, vision_disabled_note) if part
                )
                self.log_event(
                    "qq_group_vision_bypassed",
                    session_id=context.session_id,
                    profile_user_id=context.profile_user_id,
                    blocked_image_count=blocked_image_count,
                )

        attachments_registered = []
        attachment_ids: list[str] = []
        if context.attachments:
            attachments_registered = await asyncio.to_thread(
                self.engine.ingest_qq_attachments,
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                attachments=_with_qq_sender_context(list(context.attachments), context),
                character_pack_id=str(getattr(context, "character_pack_id", "") or ""),
                timestamp=int(event.get("time") or time.time()),
                observe_images=images_allowed,
            )
            attachment_ids = [
                str(item.get("attachment_id") or "").strip()
                for item in attachments_registered
                if isinstance(item, dict) and str(item.get("attachment_id") or "").strip()
            ]
            debounce_token = self.qq_gateway.register_attachment_debounce(context, attachment_ids=attachment_ids)
            if bool(debounce_token.get("enabled")):
                await asyncio.sleep(float(debounce_token.get("delay_seconds") or 0.0))
                debounce_result = self.qq_gateway.consume_attachment_debounce(debounce_token)
                if not bool(debounce_result.get("process")):
                    duration_ms = (time.perf_counter() - started_at) * 1000
                    self.runtime_metrics.observe_request("qq_napcat_event", duration_ms=duration_ms, ok=True)
                    self.log_event(
                        "qq_napcat_event_buffered",
                        session_id=context.session_id,
                        profile_user_id=context.profile_user_id,
                        reason=str(debounce_result.get("reason") or "attachment_debounce"),
                        attachment_count=len(context.attachments or []),
                        attachments_registered=len(attachments_registered),
                        duration_ms=round(duration_ms, 1),
                    )
                    return self._buffered_result(context, attachments_registered, extra_context_note, response=JSONResponse(
                        {
                            "status": "buffered",
                            "reason": str(debounce_result.get("reason") or "attachment_debounce"),
                            "session_id": context.session_id,
                            "profile_user_id": context.profile_user_id,
                            "character_pack_id": str(getattr(context, "character_pack_id", "") or ""),
                            "attachments_registered": len(attachments_registered),
                        }
                    ))
                attachment_ids = [
                    str(item or "").strip()
                    for item in list(debounce_result.get("attachment_ids") or attachment_ids)
                    if str(item or "").strip()
                ]
            if attachment_ids:
                has_image_attachment = any(
                    isinstance(item, dict) and str(item.get("kind") or "").strip().lower() == "image"
                    for item in list(context.attachments or [])
                )
                may_contain_file_image = any(
                    isinstance(item, dict) and str(item.get("kind") or "").strip().lower() in {"file", "document"}
                    for item in list(context.attachments or [])
                )
                if images_allowed and (has_image_attachment or may_contain_file_image):
                    native_prepare = getattr(self.engine, "prepare_qq_native_image_inputs", None)
                    native_result: dict[str, Any] = {}
                    if callable(native_prepare):
                        native_wait_seconds = max(
                            0.0,
                            min(
                                15.0,
                                float(getattr(self.config_module, "QQ_ATTACHMENT_READY_WAIT_SECONDS", 8.0) or 0.0),
                            ),
                        )
                        native_result = await asyncio.to_thread(
                            native_prepare,
                            profile_user_id=context.profile_user_id,
                            session_id=context.session_id,
                            attachment_ids=attachment_ids,
                            chat_model_override=str(getattr(context, "chat_model_override", "") or ""),
                            timeout_seconds=native_wait_seconds,
                        )
                    native_user_images = [
                        dict(item)
                        for item in list(native_result.get("images") or [])
                        if isinstance(item, dict) and str(item.get("data_url") or "").startswith("data:image/")
                    ][:5]
                    if native_user_images:
                        self.log_event(
                            "qq_native_multimodal_images_ready",
                            session_id=context.session_id,
                            profile_user_id=context.profile_user_id,
                            image_count=len(native_user_images),
                            skipped_count=len(list(native_result.get("skipped") or [])),
                            native_status=str(native_result.get("status") or "ready"),
                        )
                    elif has_image_attachment:
                        self.plugin_inputs.schedule_followup(
                            self.followups.vision_followup(
                                context=context,
                                event=dict(event),
                                attachment_ids=attachment_ids,
                                attachments_registered=[
                                    item for item in attachments_registered if isinstance(item, dict)
                                ],
                                message_override=message_override,
                                action_note=action_note,
                                plugin_input=self.plugin_inputs.marker(plugin_inbound),
                            ), plugin_inbound,
                        )
                        duration_ms = (time.perf_counter() - started_at) * 1000
                        self.runtime_metrics.observe_request("qq_napcat_event", duration_ms=duration_ms, ok=True)
                        self.log_event(
                            "qq_image_vision_followup_scheduled",
                            session_id=context.session_id,
                            profile_user_id=context.profile_user_id,
                            attachment_count=len(context.attachments or []),
                            attachments_registered=len(attachments_registered),
                            native_reason=str(native_result.get("reason") or "native_image_unavailable"),
                            duration_ms=round(duration_ms, 1),
                        )
                        return self._buffered_result(context, attachments_registered, extra_context_note, response=JSONResponse(
                            {
                                "status": "buffered",
                                "reason": "qq_image_vision_followup_scheduled",
                                "session_id": context.session_id,
                                "profile_user_id": context.profile_user_id,
                                "character_pack_id": str(getattr(context, "character_pack_id", "") or ""),
                                "attachment_count": len(context.attachments or []),
                                "attachments_registered": len(attachments_registered),
                            }
                        ))
                if not native_user_images:
                    attachment_wait_result = await asyncio.to_thread(
                        self.engine.wait_for_qq_attachments_settled,
                        profile_user_id=context.profile_user_id,
                        session_id=context.session_id,
                        attachment_ids=attachment_ids,
                        timeout_seconds=_qq_attachment_ready_wait_seconds(context, self.config_module),
                    )
                    # A file can finish byte classification during this
                    # wait, after the first native-image attempt returned.
                    settled_kinds = attachment_wait_result.get("kinds_by_id") or {}
                    native_prepare = getattr(self.engine, "prepare_qq_native_image_inputs", None)
                    if images_allowed and callable(native_prepare) and any(
                        kind == "image" for kind in settled_kinds.values()
                    ):
                        native_result = await asyncio.to_thread(
                            native_prepare,
                            profile_user_id=context.profile_user_id,
                            session_id=context.session_id,
                            attachment_ids=attachment_ids,
                            chat_model_override=str(getattr(context, "chat_model_override", "") or ""),
                            timeout_seconds=0.0,
                        )
                        native_user_images = [
                            dict(item) for item in list(native_result.get("images") or [])
                            if isinstance(item, dict) and str(item.get("data_url") or "").startswith("data:image/")
                        ][:5]
                    pending_image_ids = (
                        _qq_pending_image_attachment_ids([], attachment_wait_result) if images_allowed else []
                    )
                    native_ids = {str(item.get("attachment_id") or "") for item in native_user_images}
                    pending_image_ids = [item_id for item_id in pending_image_ids if item_id not in native_ids]
                    if pending_image_ids:
                        self.plugin_inputs.schedule_followup(
                            self.followups.vision_followup(
                                context=context,
                                event=dict(event),
                                attachment_ids=attachment_ids,
                                attachments_registered=[
                                    item for item in attachments_registered if isinstance(item, dict)
                                ],
                                message_override=message_override,
                                action_note=action_note,
                                plugin_input=self.plugin_inputs.marker(plugin_inbound),
                            ), plugin_inbound,
                        )
                        duration_ms = (time.perf_counter() - started_at) * 1000
                        self.runtime_metrics.observe_request("qq_napcat_event", duration_ms=duration_ms, ok=True)
                        self.log_event(
                            "qq_image_vision_followup_scheduled",
                            session_id=context.session_id,
                            profile_user_id=context.profile_user_id,
                            attachment_count=len(context.attachments or []),
                            attachments_registered=len(attachments_registered),
                            pending_image_count=len(pending_image_ids),
                            duration_ms=round(duration_ms, 1),
                        )
                        return self._buffered_result(context, attachments_registered, extra_context_note, response=JSONResponse(
                            {
                                "status": "buffered",
                                "reason": "qq_image_vision_followup_scheduled",
                                "session_id": context.session_id,
                                "profile_user_id": context.profile_user_id,
                                "character_pack_id": str(getattr(context, "character_pack_id", "") or ""),
                                "attachment_count": len(context.attachments or []),
                                "attachments_registered": len(attachments_registered),
                                "pending_image_count": len(pending_image_ids),
                            }
                        ))

        return QQPreparedContent(
            context=context, attachments_registered=attachments_registered, attachment_ids=attachment_ids,
            native_user_images=native_user_images, extra_context_note=extra_context_note,
        )

    @staticmethod
    def _buffered_result(context, attachments_registered, extra_context_note, *, response):
        return QQPreparedContent(context=context, attachments_registered=attachments_registered,
                                  extra_context_note=extra_context_note, response=response)
