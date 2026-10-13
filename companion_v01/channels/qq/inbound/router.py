"""Coordinate one admitted QQ event through conversation, command and media services."""

from __future__ import annotations

import asyncio
import time
from contextlib import ExitStack
from dataclasses import replace
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from companion_v01.channels.qq.commands.handler import _group_command_target_status
from companion_v01.channels.qq.commands.parser import resolve_qq_command
from companion_v01.channels.qq.inbound.references import _qq_quoted_message_reference
from companion_v01.desktop_pet_character_resources import load_character_care_shop_items


class QQInboundEventHandler:
    def __init__(
        self, *,
        engine, qq_gateway, runtime_metrics, logger, log_event, dialogue_registry,
        plugin_command_broker_provider, plugin_turn_router, plugin_inputs_bridge,
        attention_runtime, turn_runner, command_handler, passive, content_preparation, send_reply,
    ):
        self.engine = engine
        self.qq_gateway = qq_gateway
        self.runtime_metrics = runtime_metrics
        self.logger = logger
        self.log_event = log_event
        self.dialogue_registry = dialogue_registry
        self.plugin_command_broker_provider = plugin_command_broker_provider
        self.plugin_turn_router = plugin_turn_router
        self.plugin_inputs_bridge = plugin_inputs_bridge
        self.attention_runtime = attention_runtime
        self.turn_runner = turn_runner
        self.command_handler = command_handler
        self.passive = passive
        self.content_preparation = content_preparation
        self.send_reply = send_reply

    async def handle(self, *, request: Request, event: dict[str, Any], started_at: float) -> JSONResponse:
        context = None
        turn_timing: dict[str, float] = {}
        plugin_inputs = ExitStack()
        plugin_inbound = ""
        try:
            context = self.qq_gateway.build_message_context(event)
            command_broker = (
                self.plugin_command_broker_provider()
                if self.plugin_command_broker_provider is not None
                else getattr(request.app.state, "akane_plugin_command_broker", None)
            )
            directed = self.dialogue_registry.parse(
                context.clean_message, default_pack=context.character_pack_id, plugin_broker=command_broker,
            )
            if directed is not None:
                if context.is_group and not context.mentioned_bot:
                    return JSONResponse({"status": "ignored", "reason": "group_command_requires_explicit_mention"})
                if directed.error:
                    message = ("入口名称冲突，请用 /角色 选择正式角色。" if directed.error == "dialogue_command_conflict"
                               else "请在入口后加上要说的话。" if directed.error == "dialogue_body_required"
                               else "这个对话对象暂时不可用。")
                    sent = await self.send_reply(context, message)
                    return self.command_handler._local_command_response(context, started_at=started_at, reason="qq_directed_dialogue",
                        command_status=directed.error, command_ok=False, send_result=sent)
                context = replace(context, should_respond=True, should_record=False,
                    addressed_to_assistant=True, reason="qq_directed_dialogue", clean_message=directed.body,
                    character_pack_id=directed.target.memory_domain, dialogue_target=directed.target,
                    reply_mode="text" if directed.target.kind == "service" else context.reply_mode)
            command_target = _group_command_target_status(self.qq_gateway, context, command_broker)
            record_only_command = command_target == "unselected"
            if record_only_command:
                context = replace(
                    context,
                    should_respond=False,
                    should_record=True,
                    reason="group_command_requires_explicit_mention",
                    addressed_to_assistant=False,
                )
            # Resolve independent user addressing before plugin-only admission.
            pre_resolved_quote: dict[str, Any] = {}
            optional_reply = False
            if (
                not record_only_command
                and not context.should_respond
                and bool(getattr(context, "is_group", False))
                and context.inbound_message is not None
                and context.inbound_message.reply_to is not None
            ):
                quoted_resolver = getattr(self.qq_gateway, "resolve_quoted_message_evidence", None)
                if callable(quoted_resolver):
                    resolved_quote = await asyncio.to_thread(quoted_resolver, event, context=context)
                    pre_resolved_quote = resolved_quote if isinstance(resolved_quote, dict) else {}
                    reply_reference = _qq_quoted_message_reference(pre_resolved_quote)
                    if reply_reference:
                        context = replace(context, reply_reference=reply_reference)
                    quoted_message = (
                        pre_resolved_quote.get("quoted_message")
                        if isinstance(pre_resolved_quote.get("quoted_message"), dict)
                        else {}
                    )
                    if bool(pre_resolved_quote.get("ok")) and bool(quoted_message.get("actor_is_bot")):
                        context = replace(
                            context,
                            should_respond=True,
                            reason="group_reply_to_assistant",
                            addressed_to_assistant=True,
                        )
                        optional_reply = not bool(getattr(context, "mentioned_bot", False))
            if directed is None and not record_only_command and self.plugin_turn_router is not None and getattr(context, "inbound_message", None) is not None:
                plugin_inbound = plugin_inputs.enter_context(self.plugin_turn_router.inbound(
                    self.plugin_inputs_bridge.invocation_context(context), required=not context.should_respond))
            if directed is None and not record_only_command:
                await self.plugin_inputs_bridge.publish_message(context=context, event=event, request=request)
            if plugin_inbound and self.plugin_turn_router.inbound_requested(plugin_inbound) and not context.should_respond:
                context = replace(
                    context,
                    should_respond=True,
                    reason="plugin_event_requested_turn",
                    addressed_to_assistant=True,
                )
            if not context.should_respond:
                return await self.passive.handle(
                    context=context, event=event, started_at=started_at, record_only_command=record_only_command,
                )

            self.attention_runtime.cancel(context)

            command_context = context if directed is None else replace(context, clean_message="")
            resolution = resolve_qq_command(str(command_context.clean_message or "").strip())
            command_response = await self.command_handler.handle_early(
                context=context, command_context=command_context, request=request, started_at=started_at,
                resolution=resolution,
            )
            if command_response is not None:
                return command_response

            _qq_action_note = ""
            _qq_turn_message_override = ""
            _qq_turn_extra_context_note = "\n".join(part for part in (
                (
                    "【群聊媒体证据】历史中的 [图片] 和图片句柄若未完成视觉分析，只表示收到图片；"
                    "只有本轮实际提供的图片像素或已完成的视觉观察可用于描述画面。"
                    "合并转发也要以实际返回的节点为准，无法读取时请直说。"
                ) if context.is_group else "",
                "event.qq_optional_reply_review" if optional_reply else "",
            ) if part)

            if context.reason == "qq_poke":
                _care_module_getter = getattr(self.engine, "get_care_module", None)
                _care_module = _care_module_getter() if callable(_care_module_getter) else None
                _char_resources = getattr(self.engine, "desktop_pet_character_resources", None)
                _shop_items = (
                    load_character_care_shop_items(_char_resources, context.character_pack_id)
                    if _care_module is not None
                    and _care_module.enabled
                    and _char_resources
                    and context.character_pack_id
                    else None
                )
                poke_handler = getattr(self.qq_gateway, "handle_poke_event", None)
                poke_outcome = (
                    await asyncio.to_thread(
                        poke_handler,
                        context,
                        event,
                        care_module=_care_module,
                        shop_items=_shop_items,
                        now_ms=int(event.get("time") or time.time()) * 1000,
                    )
                    if callable(poke_handler)
                    else None
                )
                if poke_outcome is not None and str(getattr(poke_outcome, "status", "") or "") == "duplicate":
                    duration_ms = (time.perf_counter() - started_at) * 1000
                    self.runtime_metrics.observe_request("qq_napcat_event", duration_ms=duration_ms, ok=True)
                    self.log_event(
                        "qq_poke_duplicate",
                        session_id=context.session_id,
                        profile_user_id=context.profile_user_id,
                        event_id=str(getattr(poke_outcome, "event_id", "") or ""),
                    )
                    return JSONResponse(
                        {
                            "status": "duplicate",
                            "reason": "poke_event_already_applied",
                            "session_id": context.session_id,
                            "profile_user_id": context.profile_user_id,
                            "character_pack_id": str(getattr(context, "character_pack_id", "") or ""),
                        }
                    )
                if poke_outcome is not None:
                    _qq_turn_message_override = str(getattr(poke_outcome, "memory_text", "") or "").strip()
                    _qq_action_note = str(getattr(poke_outcome, "prompt_text", "") or "").strip()
                    if str(getattr(poke_outcome, "status", "") or "") == "failed":
                        self.log_event(
                            "qq_poke_reactor_failed",
                            session_id=context.session_id,
                            profile_user_id=context.profile_user_id,
                            event_id=str(getattr(poke_outcome, "event_id", "") or ""),
                            reason=str(getattr(poke_outcome, "reason", "") or "state_apply_failed"),
                        )
                    else:
                        self.log_event(
                            "qq_poke_reactor",
                            session_id=context.session_id,
                            profile_user_id=context.profile_user_id,
                            event_id=str(getattr(poke_outcome, "event_id", "") or ""),
                            outcome_kind=str(getattr(poke_outcome, "outcome_kind", "") or "plain"),
                            outcome_status=str(getattr(poke_outcome, "status", "") or "ok"),
                        )
                    await self.plugin_inputs_bridge.publish_poke(
                        context=context,
                        event=event,
                        outcome=poke_outcome,
                        request=request,
                    )

            command_outcome = await self.command_handler.handle(
                context=context, command_context=command_context, event=event, request=request,
                started_at=started_at, action_note=_qq_action_note, message_override=_qq_turn_message_override,
                resolution=resolution,
            )
            if command_outcome.response is not None:
                return command_outcome.response
            _qq_action_note = command_outcome.action_note
            _qq_turn_message_override = command_outcome.message_override

            prepared_content = await self.content_preparation.prepare(
                context=context, event=event, pre_resolved_quote=pre_resolved_quote,
                extra_context_note=_qq_turn_extra_context_note, action_note=_qq_action_note,
                message_override=_qq_turn_message_override, plugin_inbound=plugin_inbound, started_at=started_at,
            )
            if prepared_content.response is not None:
                return prepared_content.response
            context = prepared_content.context
            attachments_registered = prepared_content.attachments_registered
            attachment_ids = prepared_content.attachment_ids
            _qq_native_user_images = prepared_content.native_user_images
            _qq_turn_extra_context_note = prepared_content.extra_context_note

            turn_payload = self.turn_runner.prepare_payload(
                context=context,
                event=event,
                message_override=_qq_turn_message_override,
                action_note=_qq_action_note,
                extra_context_note=_qq_turn_extra_context_note,
            )
            if optional_reply:
                turn_payload["turn_kind"] = "qq_optional_reply"
            if _qq_native_user_images:
                turn_payload["native_user_images"] = _qq_native_user_images
            if attachment_ids:
                # Bind relative selectors such as inspect_attachment(latest) to
                # evidence from this QQ event, rather than the shared group's
                # historical workspace.
                turn_payload["qq_current_attachment_ids"] = list(attachment_ids)
            turn_payload.update(self.plugin_inputs_bridge.marker(plugin_inbound))
            turn_result = await self.turn_runner.run(context=context, event=event, turn_payload=turn_payload)
            raw_turn_timing = turn_result.get("timing") if isinstance(turn_result, dict) else None
            if isinstance(raw_turn_timing, dict):
                turn_timing = {
                    str(key): round(float(value), 1)
                    for key, value in raw_turn_timing.items()
                    if isinstance(value, (int, float))
                }
            frame = dict(turn_result.get("frame") or {})
            reply_messages = list(turn_result.get("reply_messages") or [])
            send_result = dict(
                turn_result.get("send_result") or {"ok": False, "reason": "missing_send_result", "results": []}
            )
            emotion_mface_result = dict(
                turn_result.get("emotion_mface_result") or {"ok": True, "status": "skipped", "reason": "missing_result"}
            )
            emotion_image_result = dict(
                turn_result.get("emotion_image_result") or {"ok": True, "status": "skipped", "reason": "missing_result"}
            )
            file_send_result = dict(turn_result.get("file_send_result") or {"ok": True, "count": 0, "results": []})
            final_failure_notice_result = dict(
                turn_result.get("final_failure_notice_result")
                or {"ok": True, "status": "skipped", "reason": "missing_result"}
            )
        except Exception as exc:
            return self.failure_response(exc, started_at=started_at, event=event, context=context)

        finally:
            plugin_inputs.close()

        duration_ms = (time.perf_counter() - started_at) * 1000
        delivery_status = str(send_result.get("status") or "")
        request_ok = bool(send_result.get("ok"))
        visibly_sent = request_ok and delivery_status not in {"queued", "suppressed", "stopped", "skipped"}
        self.runtime_metrics.observe_request("qq_napcat_event", duration_ms=duration_ms, ok=request_ok)
        self.log_event(
            "qq_napcat_event",
            session_id=context.session_id,
            profile_user_id=context.profile_user_id,
            character_pack_id=str(getattr(context, "character_pack_id", "") or ""),
            reason=context.reason,
            sent=visibly_sent,
            delivery_status=delivery_status,
            model_status=str(turn_result.get("model_status") or ""),
            failure_notice_sent=bool(send_result.get("failure_notice_sent")),
            partial_delivery=bool(send_result.get("partial_delivery")),
            attachment_count=len(context.attachments or []),
            attachments_registered=len(attachments_registered),
            duration_ms=round(duration_ms, 1),
            turn_timing=turn_timing,
        )
        return JSONResponse(
            {
                "status": "ok" if request_ok else "send_failed",
                "reason": context.reason,
                "session_id": context.session_id,
                "profile_user_id": context.profile_user_id,
                "character_pack_id": str(getattr(context, "character_pack_id", "") or ""),
                # NapCat / OneBot HTTP report supports "quick operation" replies.
                # Do not include a "reply" field here, or it may send a second
                # aggregated message after our explicit send_replies() call.
                "sent_count": len(reply_messages),
                "attachment_count": len(context.attachments or []),
                "attachments_registered": len(attachments_registered),
                "send_result": send_result,
                "emotion_mface_result": emotion_mface_result,
                "emotion_image_result": emotion_image_result,
                "file_send_result": file_send_result,
                "final_failure_notice_result": final_failure_notice_result,
            }
        )

    def failure_response(self, exc: Exception, *, started_at: float,
                         event: Any = None, context: Any = None) -> JSONResponse:
        duration_ms = (time.perf_counter() - started_at) * 1000
        self.runtime_metrics.observe_request("qq_napcat_event", duration_ms=duration_ms, ok=False)
        error_type = exc.__class__.__name__
        error_logger = getattr(self.logger, "error", None)
        if callable(error_logger):
            error_logger("qq_napcat_event failed: %s", error_type)
        self.log_event(
            "qq_napcat_event_error", session_id=getattr(context, "session_id", ""),
            profile_user_id=getattr(context, "profile_user_id", ""),
            post_type=str(event.get("post_type") or "") if isinstance(event, dict) else "",
            message_type=str(event.get("message_type") or "") if isinstance(event, dict) else "",
            reason=error_type, duration_ms=round(duration_ms, 1),
        )
        return JSONResponse({"status": "error", "reason": "qq_event_processing_failed",
                             "error_type": error_type, "message": "QQ 消息处理失败，请稍后重试。"})
