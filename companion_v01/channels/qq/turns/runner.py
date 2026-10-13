"""QQTurnRunner: per-Bot state and explicit conversation dependencies."""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from contextlib import nullcontext
from typing import Any, Callable

from companion_v01.channels.qq.commands.handler import _is_qq_stop_command
from companion_v01.channels.qq.resources import apply_qq_current_outfit_visual as _apply_qq_current_outfit_visual
from companion_v01.channels.qq.turns.attention_state import AttentionTicket
from companion_v01.channels.qq.turns.work import _session_work_key
from companion_v01.durable_session_queue import DurableSessionWorkQueue
from companion_v01.media_bridge_engine import redact_remote_media_urls_for_prompt
from companion_v01.turn_coordination import cancellation_scope


class QQTurnRunner:
    def __init__(
        self, *, engine, config_module, qq_gateway, log_event, turn_coordinator,
        tts_client, settings, plugin_conversation_ref_issuer, plugin_inputs,
        plugin_turn_router, attention, session_queue, stream_processor,
    ):
        self.engine = engine
        self.config_module = config_module
        self.qq_gateway = qq_gateway
        self.log_event = log_event
        self.turn_coordinator = turn_coordinator
        self.tts_client = tts_client
        self.settings = settings
        self.plugin_conversation_ref_issuer = plugin_conversation_ref_issuer
        self.plugin_inputs = plugin_inputs
        self.plugin_turn_router = plugin_turn_router
        self.attention = attention
        self.session_queue = session_queue
        self.stream_processor = stream_processor

    def prepare_payload(self,
        *,
        context: Any,
        event: dict[str, Any],
        message_override: str = "",
        action_note: str = "",
        extra_context_note: str = "",
    ) -> dict[str, Any]:
        turn_payload = context.to_turn_payload()
        turn_payload["timestamp"] = int(event.get("time") or time.time())
        if message_override:
            turn_payload["message"] = message_override
        _apply_qq_current_outfit_visual(turn_payload, qq_gateway=self.qq_gateway, context=context, engine=self.engine)
        if action_note:
            turn_payload["qq_action_note"] = action_note
        if extra_context_note:
            original_extra_context = str(turn_payload.get("extra_context") or "").strip()
            turn_payload["extra_context"] = "\n\n".join(
                part
                for part in (
                    original_extra_context,
                    extra_context_note,
                )
                if part
            )
        if context.reason == "qq_poke":
            self.log_event(
                "qq_poke_context",
                session_id=context.session_id,
                profile_user_id=context.profile_user_id,
                is_group=bool(context.is_group),
                group_id=int(getattr(context, "group_id", 0) or 0),
                event_user_id=str(event.get("user_id") or ""),
                event_sender_id=str(event.get("sender_id") or ""),
                event_operator_id=str(event.get("operator_id") or ""),
                event_target_id=str(event.get("target_id") or ""),
                event_self_id=str(event.get("self_id") or ""),
                resolved_user_id=int(getattr(context, "user_id", 0) or 0),
                sender_label=str(getattr(context, "sender_label", "") or ""),
                turn_message=str(turn_payload.get("message") or "")[:240],
            )
        return turn_payload

    async def run_unlocked(self,
        *,
        context: Any,
        event: dict[str, Any],
        turn_payload: dict[str, Any],
        prepared_frame: dict[str, Any] | None = None,
        additional_delivery_events: list[dict[str, Any]] | Callable | None = None,
        file_target_allowed: Callable | None = None,
        attention_ticket: AttentionTicket | None = None,
    ) -> dict[str, Any]:
        turn_payload = dict(turn_payload)
        # Only admitted QQ channel work may attest a memory-tool origin.  A
        # caller-supplied client_mode or delivery_context is not an authority.
        turn_payload.pop("_memory_qq_ref", None)
        if self.plugin_conversation_ref_issuer is not None:
            reference = self.plugin_conversation_ref_issuer(
                profile_user_id=str(getattr(context, "profile_user_id", "") or ""),
                session_id=str(getattr(context, "session_id", "") or ""),
                character_pack_id=str(getattr(context, "character_pack_id", "") or ""),
                user_id=int(getattr(context, "user_id", 0) or 0),
                group_id=int(getattr(context, "group_id", 0) or 0),
                actor_stable_id=(f"qq:{int(context.user_id)}" if bool(getattr(context, "is_group", False))
                                 and int(getattr(context, "user_id", 0) or 0) else ""),
                actor_profile_user_id=str(getattr(context, "actor_profile_user_id", "") or ""),
            )
            if reference:
                turn_payload["_memory_qq_ref"] = reference
        turn_kind = str(turn_payload.get("turn_kind") or "").strip().lower()
        is_plugin_event = turn_kind == "plugin_event"
        plugin_event_message = str(turn_payload.get("message") or "")
        if is_plugin_event and re.search(r"https?://", plugin_event_message, re.IGNORECASE):
            original_extra_context = str(turn_payload.get("extra_context") or "").strip()
            turn_payload["extra_context"] = "\n\n".join(
                part
                for part in (
                    original_extra_context,
                    (
                        "【插件事件来源链接】\n"
                        "本轮由插件触发，消息里的 URL 是事件来源或溯源标记，不是用户提交的媒体下载请求。"
                        "不要尝试把它作为音视频素材下载，也不要在回复中声称链接无法打开、平台不受支持，"
                        "或要求用户更换媒体直链；需要核验事实时仍可使用可用的只读检索工具。"
                    ),
                )
                if part
            )
        remote_prefetch_result = (
            await asyncio.to_thread(
                self.engine.prefetch_remote_media_links_for_message,
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                message=context.clean_message or context.raw_message,
                character_pack_id=str(getattr(context, "character_pack_id", "") or ""),
                timestamp=int(event.get("time") or time.time()),
            )
            if prepared_frame is None and not is_plugin_event
            else {}
        )
        if isinstance(remote_prefetch_result, dict) and remote_prefetch_result:
            turn_payload["message"] = redact_remote_media_urls_for_prompt(str(turn_payload.get("message") or ""))
            delivery_context = (
                dict(turn_payload.get("qq_delivery_context") or {})
                if isinstance(turn_payload.get("qq_delivery_context"), dict)
                else {}
            )
            if delivery_context:
                delivery_context["clean_message"] = redact_remote_media_urls_for_prompt(
                    str(delivery_context.get("clean_message") or "")
                )
                delivery_context["raw_message"] = redact_remote_media_urls_for_prompt(
                    str(delivery_context.get("raw_message") or "")
                )
                turn_payload["qq_delivery_context"] = delivery_context
        if (
            isinstance(remote_prefetch_result, dict)
            and str(remote_prefetch_result.get("followup_context") or "").strip()
        ):
            prefetch_context = str(remote_prefetch_result.get("followup_context") or "").strip()
            original_extra_context = str(turn_payload.get("extra_context") or "").strip()
            turn_payload["extra_context"] = "\n\n".join(
                part
                for part in (
                    original_extra_context,
                    "【链接素材预处理结果】\n" + prefetch_context,
                )
                if part
            )
        if attention_ticket is not None and not self.attention.can_continue(attention_ticket, context):
            return {"attention_skipped": True, "frame": {}, "reply_messages": []}
        turn_result = await asyncio.to_thread(
            self.stream_processor,
            engine=self.engine,
            qq_gateway=self.qq_gateway,
            context=context,
            turn_payload=turn_payload,
            config_module=self.config_module,
            tts_client=self.tts_client,
            settings=self.settings,
            prepared_frame=prepared_frame,
            additional_delivery_events=additional_delivery_events,
            file_target_allowed=file_target_allowed,
            **({"begin_attention_reply": lambda: self.attention.state.begin_reply(attention_ticket)}
               if attention_ticket is not None else {}),
        )
        file_send_result = dict(turn_result.get("file_send_result") or {"ok": True, "count": 0, "results": []})
        for item in list(file_send_result.get("results") or []):
            generated_id = str(item.get("generated_id") or "").strip()
            if not generated_id:
                continue
            await asyncio.to_thread(
                self.engine.mark_generated_file_delivery,
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                generated_id=generated_id,
                delivery_status="sent" if item.get("ok") else "failed",
                timestamp=int(time.time()),
            )
        self.attention.mark_engaged(context, turn_result)
        return turn_result

    async def run(self, *, context, event, turn_payload, attention_ticket=None):
        turn_payload = dict(turn_payload)
        marker = {key: turn_payload.pop(key) for key in ("_plugin_inbound_turn_id", "_plugin_inbound_required") if key in turn_payload}
        with self.plugin_inputs.scope(context, marker) as inbound:
            try:
                result = await self._run_admitted(context=context, event=event,
                    turn_payload=turn_payload, inbound=inbound, attention_ticket=attention_ticket)
            except BaseException:
                if inbound:
                    self.plugin_turn_router.finish_inbound(inbound, model_status="failed", reason="inbound_turn_failed")
                raise
            self.plugin_inputs.settle(inbound, result)
            return result

    async def _run_admitted(self,
        *,
        context: Any,
        event: dict[str, Any],
        turn_payload: dict[str, Any],
        inbound="",
        attention_ticket: AttentionTicket | None = None,
    ) -> dict[str, Any]:
        policy = getattr(self.engine, "owner_private_policy", None)
        if policy is not None and self.plugin_conversation_ref_issuer is not None:
            reference = self.plugin_conversation_ref_issuer(
                profile_user_id=context.profile_user_id, session_id=context.session_id,
                character_pack_id=str(getattr(context, "character_pack_id", "") or ""),
                user_id=int(getattr(context, "user_id", 0) or 0),
                group_id=int(getattr(context, "group_id", 0) or 0))
            policy.note_input({**turn_payload, "_memory_qq_ref": reference},
                              source_id=str(event.get("message_id") or ""))
        qq_user_id = int(getattr(context, "user_id", 0) or 0)
        actor_id = f"qq:{qq_user_id}" if qq_user_id else f"qq-profile:{context.profile_user_id}"
        master_qq = str(getattr(self.qq_gateway, "master_qq", "") or "").strip()
        requester_is_master = bool(master_qq) and str(qq_user_id) == master_qq
        steer_text = str(turn_payload.get("message") or "").strip()
        stop_requested = (getattr(context, "dialogue_target", None) is None
                          and _is_qq_stop_command(getattr(context, "clean_message", "")))
        if stop_requested:
            stop_result = self.turn_coordinator.request_stop(
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                # The owner is the host-level operator for this Bot instance and
                # may stop a group task started by another actor.
                actor_id="" if requester_is_master else actor_id,
            )
            if stop_result.get("ok"):
                acknowledgement = "收到，已请求在安全位置停止当前任务。"
                send_result = await asyncio.to_thread(self.qq_gateway.send_reply, context, acknowledgement)
                return {
                    "frame": {"status": "stop_requested", "speech": acknowledgement},
                    "reply_messages": [acknowledgement],
                    "send_result": send_result,
                }
            if stop_result.get("status") == "finalizing":
                acknowledgement = "当前任务已经在收尾，来不及再中止了。"
            elif stop_result.get("status") == "busy_other_actor":
                acknowledgement = "当前任务由另一位群成员发起；只有主人或任务发起者可以停止。"
            else:
                acknowledgement = "当前没有正在执行的任务。"
            send_result = await asyncio.to_thread(self.qq_gateway.send_reply, context, acknowledgement)
            return {
                "frame": {"status": str(stop_result.get("status") or "idle"), "speech": acknowledgement},
                "reply_messages": [acknowledgement],
                "send_result": send_result,
            }
        queue_key = _session_work_key(context)
        queued_for_later: dict[str, Any] | None = None
        durable_receipt: dict[str, str] = {}
        if (
            isinstance(self.session_queue.queue, DurableSessionWorkQueue)
            and self.turn_coordinator.is_busy(context.profile_user_id, context.session_id)
        ):
            persisted = await self.session_queue.enqueue(
                context=context,
                event=dict(event),
                turn_payload=self.plugin_inputs.defer_payload(inbound, turn_payload),
                kind="turn",
                schedule=False,
            )
            if not persisted.get("ok"):
                return {
                    "frame": {"status": "queue_failed", "speech": ""},
                    "reply_messages": [],
                    "send_result": {
                        "ok": False,
                        "status": "queue_failed",
                        "reason": str(persisted.get("reason") or "session_inbox_persist_failed"),
                        "results": [],
                    },
                }
            persisted_item = persisted.get("item")
            persisted_status = str(getattr(persisted_item, "status", "") or "")
            if persisted.get("status") == "duplicate" and persisted_status == "committed":
                return {
                    "frame": {"status": "duplicate", "speech": ""},
                    "reply_messages": [],
                    "send_result": {
                        "ok": True,
                        "status": "suppressed",
                        "reason": "source_event_already_committed",
                        "results": [],
                    },
                }
            if persisted.get("status") == "duplicate" and persisted_status == "failed":
                return {
                    "frame": {"status": "queue_failed", "speech": ""},
                    "reply_messages": [],
                    "send_result": {
                        "ok": False,
                        "status": "queue_failed",
                        "reason": "source_event_previously_failed",
                        "results": [],
                    },
                }
            item_id = str(persisted.get("item_id") or getattr(persisted_item, "item_id", "") or "")
            claim = await self.session_queue.queue.claim_for_active_turn(queue_key, item_id)
            if claim.get("ok"):
                durable_receipt = {
                    "source_id": f"steer_{item_id}",
                    "receipt_item_id": item_id,
                    "receipt_claim_token": str(claim.get("claim_token") or ""),
                }
            else:
                queued_for_later = persisted
                await self.session_queue.queue.schedule_session(queue_key)

        steer_source_id = durable_receipt.get("source_id") or f"steer_{uuid.uuid4().hex}"
        target_token = self.turn_coordinator.active_token(context.profile_user_id, context.session_id) if inbound else ""
        with self.plugin_turn_router.steering(inbound, target_token, steer_source_id) if inbound else nullcontext(lambda: None) as accept_steer:
            steer_result = (
                self.turn_coordinator.offer_steer(
                    profile_user_id=context.profile_user_id,
                    session_id=context.session_id,
                    actor_id=actor_id,
                    allow_other_actor=requester_is_master,
                    # Ordinary group questions each need a reply. Only an
                    # actual tool task may absorb follow-up input as steering.
                    require_tool_activity=bool(context.is_group),
                    memory_domain=str(getattr(context, "character_pack_id", "") or ""),
                    content=steer_text,
                    timestamp=int(event.get("time") or time.time()),
                    actor_display_name=str(getattr(context, "sender_label", "") or ""),
                    channel="qq",
                    native_user_images=[
                        dict(item)
                        for item in list(turn_payload.get("native_user_images") or [])[:5]
                        if isinstance(item, dict)
                    ],
                    **{**durable_receipt, "source_id": steer_source_id},
                )
                if queued_for_later is None and accept_steer is not None
                else {"ok": False, "status": "queued", "reason": "session_inbox_waiting"}
            )
            if steer_result.get("ok"):
                accept_steer()
        if steer_result.get("ok"):
            return {
                "frame": {"status": "steer_accepted", "speech": ""},
                "reply_messages": [],
                "send_result": {
                    "ok": True,
                    "status": "suppressed",
                    "reason": "steer_accepted_without_visible_ack",
                    "results": [],
                },
            }
        if durable_receipt:
            await self.session_queue.queue.requeue_claim(
                durable_receipt["receipt_item_id"],
                claim_token=durable_receipt["receipt_claim_token"],
                error=str(steer_result.get("reason") or "steer_not_accepted"),
            )
            await self.session_queue.queue.schedule_session(queue_key)
            queued_for_later = {
                "ok": True,
                "status": "queued",
                "reason": "steer_not_accepted_requeued",
                "item_id": durable_receipt["receipt_item_id"],
            }
        queue_behind_active_turn = str(steer_result.get("status") or "") in {
            "busy_chat",
            "busy_maintenance",
            "busy_other_actor",
            "busy_other_scope",
            "finalizing",
            "preempting_optional_turn",
            "queued",
        }
        if queued_for_later is not None or (
            context.is_group and (queue_behind_active_turn or self.session_queue.queue.has_work(queue_key))
        ):
            queued = queued_for_later or await self.session_queue.enqueue(
                context=context,
                event=dict(event),
                turn_payload=self.plugin_inputs.defer_payload(inbound, turn_payload),
                kind="turn",
            )
            self.log_event(
                "qq_group_turn_queued",
                session_id=context.session_id,
                profile_user_id=context.profile_user_id,
                group_id=int(getattr(context, "group_id", 0) or 0),
                user_id=qq_user_id,
                source_message_id=str(getattr(context, "source_message_id", "") or ""),
                queue_sequence=int(queued.get("sequence") or 0),
                pending_count=int(queued.get("pending_count") or 0),
                reason=str(steer_result.get("status") or "session_queue_busy"),
            )
            return {
                "frame": {"status": "queued", "speech": ""},
                "reply_messages": [],
                "send_result": {
                    "ok": bool(queued.get("ok")),
                    "status": "queued" if queued.get("ok") else "queue_failed",
                    "reason": str(queued.get("reason") or "session_fifo"),
                    "results": [],
                    "queue_sequence": int(queued.get("sequence") or 0),
                    "pending_count": int(queued.get("pending_count") or 0),
                },
            }
        queue_wait_started_at = time.perf_counter()
        async with self.turn_coordinator.hold(
            context.profile_user_id,
            context.session_id,
            actor_id=actor_id,
            channel="qq",
            turn_kind=str(turn_payload.get("turn_kind") or "").strip(),
            memory_domain=str(getattr(context, "character_pack_id", "") or ""),
        ) as turn_control_id:
            queue_wait_ms = max(0.0, (time.perf_counter() - queue_wait_started_at) * 1000)
            turn_payload["_turn_control_id"] = turn_control_id
            if attention_ticket is not None:
                self.attention.bind_control(attention_ticket, turn_control_id)
            try:
                with (self.plugin_turn_router.processing_inbound(inbound, turn_control_id) if inbound else nullcontext()), (
                    cancellation_scope(lambda: self.attention.cancelled(attention_ticket))
                    if attention_ticket is not None else nullcontext()
                ):
                    result = await self.run_unlocked(
                        context=context, event=event, turn_payload=turn_payload,
                        attention_ticket=attention_ticket,
                    )
            finally:
                if attention_ticket is not None:
                    self.attention.release_control(attention_ticket)
            if isinstance(result, dict):
                timing = dict(result.get("timing") or {})
                timing["queue_wait_ms"] = round(queue_wait_ms, 1)
                result["timing"] = timing
            return result
