"""QQPluginTurnAdapter: per-Bot state and explicit conversation dependencies."""

from __future__ import annotations

import asyncio
import time
from typing import Mapping

from companion_v01.channels.qq.turns.work import _durable_qq_work_payload, _session_work_key
from companion_v01.durable_session_queue import DurableSessionWorkQueue
from companion_v01.host_completion_batch import completion_input_fingerprint, completion_metadata, completion_timestamp
from companion_v01.plugin_turn_intents import HostTurnIntent, HostTurnResult, PluginTurnError, normalize_turn_intent


class QQPluginTurnAdapter:
    def __init__(
        self, *, qq_gateway, logger, session_queue, run_turn,
    ):
        self.qq_gateway = qq_gateway
        self.logger = logger
        self.session_queue = session_queue
        self.run_turn = run_turn

    async def submit(self,
        request: HostTurnIntent,
        resolved_reference: Mapping[str, str],
    ) -> HostTurnResult:
        """Run one plugin event through the ordinary QQ Agent turn path."""

        try:
            request = normalize_turn_intent(request)
        except PluginTurnError as exc:
            return HostTurnResult(False, "rejected", exc.reason)
        if request.requires_queue and not isinstance(self.session_queue.queue, DurableSessionWorkQueue):
            return HostTurnResult(False, "rejected", "agent_turn_queue_unavailable")
        if not isinstance(resolved_reference, Mapping):
            return HostTurnResult(False, "rejected", "event_context_unresolved")
        if str(resolved_reference.get("channel") or "") != "qq":
            return HostTurnResult(False, "rejected", "event_context_unresolved")
        conversation_kind = str(resolved_reference.get("kind") or "").strip().lower()
        if conversation_kind not in {"direct", "group"}:
            return HostTurnResult(False, "rejected", "unsupported_conversation_kind")
        memory_mode = str(getattr(request, "memory_mode", "current_turn")).strip().lower()
        if memory_mode not in {"current_turn", "timeline"}:
            return HostTurnResult(False, "rejected", "unsupported_memory_mode")
        text_delivery = str(getattr(request, "presentation_mode", "default") or "default").strip().lower()
        if text_delivery not in {"default", "single_message"}:
            return HostTurnResult(False, "rejected", "unsupported_text_delivery")

        recipient = str(resolved_reference.get("recipient") or "").strip()
        session_id = str(resolved_reference.get("session") or "").strip()
        profile_user_id = str(resolved_reference.get("profile") or "").strip()
        target_id = 0
        user_id = 0
        group_id = 0
        if conversation_kind == "group":
            raw_group_id = recipient.removeprefix("group:").strip() if recipient else ""
            if not raw_group_id and session_id.startswith("qq_group_shared_"):
                raw_group_id = session_id.removeprefix("qq_group_shared_")
            if not raw_group_id.isdigit() or int(raw_group_id) <= 0:
                return HostTurnResult(False, "rejected", "event_context_unresolved")
            group_id = int(raw_group_id)
            target_id = group_id
            resolved_session_id, resolved_profile_user_id = self.qq_gateway.resolve_identity(
                user_id=0,
                group_id=group_id,
            )
        else:
            raw_user_id = recipient.removeprefix("user:").strip() if recipient else ""
            if not raw_user_id and session_id.startswith("qq_pri_"):
                raw_user_id = session_id.removeprefix("qq_pri_")
            if not raw_user_id and session_id == "master":
                raw_user_id = str(getattr(self.qq_gateway, "master_qq", "") or "").strip()
            if not raw_user_id.isdigit() or int(raw_user_id) <= 0:
                return HostTurnResult(False, "rejected", "event_context_unresolved")
            user_id = int(raw_user_id)
            target_id = user_id
            resolved_session_id, resolved_profile_user_id = self.qq_gateway.resolve_identity(
                user_id=user_id,
                group_id=0,
            )

        # The signed reference must still resolve to the same live namespace.
        if session_id != resolved_session_id:
            return HostTurnResult(False, "rejected", "event_context_mismatch")
        if profile_user_id != resolved_profile_user_id:
            return HostTurnResult(False, "rejected", "event_context_mismatch")
        character_pack_id = str(resolved_reference.get("character") or "").strip()
        if not character_pack_id:
            return HostTurnResult(False, "rejected", "event_context_unresolved")
        message = str(request.message or "").strip()
        if not message:
            return HostTurnResult(False, "rejected", "agent_event_message_required")
        event_timestamp = completion_timestamp(request, int(time.time()))
        sender_label = "插件事件"
        if str(request.source or "").strip().lower().startswith("host."):
            sender_label = "系统事件"
        source_event_id = str(getattr(request, "idempotency_key", "") or request.trace_id or "").strip()
        synthetic_event = {
            "time": event_timestamp,
            "post_type": "message",
            "message_type": "group" if conversation_kind == "group" else "private",
            "group_id": group_id,
            "user_id": user_id,
            "message_id": "",
        }
        extra_context = self.qq_gateway.build_extra_context(
            event=synthetic_event,
            is_group=conversation_kind == "group",
            user_id=user_id,
            group_id=group_id,
            sender_label=sender_label,
            reply_mode=self.qq_gateway.resolve_reply_mode(resolved_session_id),
            session_id=resolved_session_id,
        )
        from companion_v01.channels.qq.context import QQMessageContext

        context = QQMessageContext(
            should_respond=True,
            reason="plugin_event",
            should_record=False,
            is_group=conversation_kind == "group",
            target_id=target_id,
            user_id=user_id,
            group_id=group_id,
            session_id=resolved_session_id,
            profile_user_id=resolved_profile_user_id,
            clean_message=message,
            raw_message=message,
            extra_context=extra_context,
            sender_label=sender_label,
            character_pack_id=character_pack_id,
            reply_mode=self.qq_gateway.resolve_reply_mode(resolved_session_id),
            chat_model_override=self.qq_gateway.resolve_chat_model_override(resolved_session_id),
            source_message_id=source_event_id,
        )
        turn_payload = context.to_turn_payload()
        causal_actor = str(resolved_reference.get("actor") or "").strip()
        causal_actor_profile = str(resolved_reference.get("actor_profile") or "").strip()
        if conversation_kind == "group" and causal_actor.startswith("qq:"):
            turn_payload["actor_stable_id"] = causal_actor
            if causal_actor_profile:
                turn_payload["actor_profile_user_id"] = causal_actor_profile
        turn_payload.update(
            {
                "message": message,
                "memory_message": message,
                "timestamp": event_timestamp,
                "turn_kind": "plugin_event",
                "transient_user_message": memory_mode == "current_turn",
                "plugin_external_event": request.event_payload(),
                "memory_idempotency_key": str(getattr(request, "idempotency_key", "") or "").strip(),
                "plugin_text_delivery": text_delivery,
                "plugin_text_prefix": str(getattr(request, "presentation_prefix", "") or "").strip(),
                "plugin_text_suffix": str(getattr(request, "presentation_suffix", "") or "").strip(),
                "plugin_text_strip_leading_addresses": list(
                    getattr(request, "strip_leading_addresses", ())
                ),
                "message_addressing": {
                    "mode": "current_request",
                    "trigger": "plugin_event",
                    "addressed_to_assistant": True,
                    "explicit_assistant_mention": False,
                    "primary_target": {"actor_id": "assistant"},
                    "mentions": [],
                },
            }
        )
        if isinstance(self.session_queue.queue, DurableSessionWorkQueue):
            queued = await self.session_queue.queue.enqueue(
                session_key=_session_work_key(context),
                profile_user_id=resolved_profile_user_id,
                session_id=resolved_session_id,
                kind="turn",
                payload={
                    **_durable_qq_work_payload(event=synthetic_event, turn_payload=turn_payload),
                    **({"plugin_turn_request_id": request.request_id} if request.request_id else {}),
                    "host_completion": completion_metadata(request, resolved_reference),
                },
                source="qq",
                source_event_id=f"plugin-turn:{request.request_id}" if request.request_id else source_event_id,
                input_fingerprint=completion_input_fingerprint(request, resolved_reference),
            )
            if not queued.get("ok"):
                return HostTurnResult(
                    False,
                    "failed",
                    str(queued.get("reason") or "agent_event_queue_failed"),
                    "not_sent",
                )
            queued_item = queued.get("item")
            queued_status = str(getattr(queued_item, "status", "") or "")
            if queued_status == "failed":
                return HostTurnResult(False, "failed", "agent_event_previously_failed", "not_sent")
            delivery_status = "suppressed" if queued_status == "committed" else "queued"
            return HostTurnResult(True, "accepted", "", delivery_status,
                                  str(queued.get("item_id") or getattr(queued_item, "item_id", "")))
        try:
            result = await self.run_turn(
                context=context,
                event=synthetic_event,
                turn_payload=turn_payload,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.exception("plugin Agent event delivery failed")
            return HostTurnResult(False, "failed", "agent_event_delivery_failed")
        result_payload = result if isinstance(result, dict) else {}
        send_result = result_payload.get("send_result")
        send_payload = send_result if isinstance(send_result, dict) else {}
        if result_payload.get("final_frame_received") is False or bool(
            result_payload.get("_transient_final_failure")
        ) or bool(
            result_payload.get("frame", {}).get("_transient_final_failure")
            if isinstance(result_payload.get("frame"), dict)
            else False
        ):
            return HostTurnResult(False, "failed", "agent_event_turn_incomplete", "not_sent")
        if not bool(send_payload.get("ok")):
            return HostTurnResult(
                False,
                "failed",
                str(send_payload.get("reason") or "agent_event_delivery_failed"),
                str(send_payload.get("status") or "not_sent"),
            )
        delivery_status = str(send_payload.get("status") or "delivered").strip().lower()
        if delivery_status not in {"queued", "sent", "delivered", "suppressed"}:
            delivery_status = "delivered"
        return HostTurnResult(True, "completed", "", delivery_status)
