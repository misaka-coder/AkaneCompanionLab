"""QQPluginInputBridge: per-Bot state and explicit conversation dependencies."""

from __future__ import annotations

from .plugin_api import DIRECT_CONVERSATION_EVENT
from .plugin_api import GROUP_CONVERSATION_EVENT
from .plugin_api import POKE_CONVERSATION_EVENT
from .plugin_turn_intents import PluginTurnError
from akane_plugin import PluginInvocationContext
from contextlib import nullcontext
from fastapi import Request
from typing import Any
import asyncio


class QQPluginInputBridge:
    def __init__(
        self, *, plugin_conversation_ref_issuer, plugin_event_broker_provider,
        plugin_turn_router, log_event, schedule_task,
    ):
        self.plugin_conversation_ref_issuer = plugin_conversation_ref_issuer
        self.plugin_event_broker_provider = plugin_event_broker_provider
        self.plugin_turn_router = plugin_turn_router
        self.log_event = log_event
        self.schedule_task = schedule_task

    def invocation_context(self, context):
        reference = self.plugin_conversation_ref_issuer(
            profile_user_id=context.profile_user_id, session_id=context.session_id,
            character_pack_id=context.character_pack_id, user_id=context.user_id, group_id=context.group_id,
            actor_stable_id=f"qq:{context.user_id}" if context.is_group else "",
            actor_profile_user_id=str(context.actor_profile_user_id or ""),
        ) if self.plugin_conversation_ref_issuer else ""
        return PluginInvocationContext(context.profile_user_id, context.session_id, "qq",
            character_pack_id=context.character_pack_id, conversation_ref=reference)

    def scope(self, context, marker=None):
        marker = marker or {}
        required = bool(marker.get("_plugin_inbound_required"))
        key = str(marker.get("_plugin_inbound_turn_id") or "")
        if required and (self.plugin_turn_router is None or not key):
            raise PluginTurnError("agent_turn_scope_expired")
        return self.plugin_turn_router.inbound(self.invocation_context(context), inbound_turn_id=key,
            required=required) if self.plugin_turn_router else nullcontext("")

    def marker(self, inbound):
        return {"_plugin_inbound_turn_id": inbound,
                "_plugin_inbound_required": self.plugin_turn_router.inbound_required(inbound)} if inbound else {}

    def defer_payload(self, inbound, payload):
        marker = self.marker(inbound)
        if inbound:
            self.plugin_turn_router.defer_inbound(inbound)
        return {**payload, **marker}

    def schedule_followup(self, coroutine, inbound):
        lease = self.plugin_turn_router.defer_inbound(inbound) if inbound else 0
        try:
            task = self.schedule_task(coroutine)
        except BaseException:
            coroutine.close()
            if inbound:
                self.plugin_turn_router.abandon_inbound(inbound, lease, reason="inbound_followup_schedule_failed")
            raise
        if inbound:
            task.add_done_callback(lambda _task: self.plugin_turn_router.abandon_inbound(
                inbound, lease, reason="inbound_followup_incomplete"))
        return task

    def settle(self, inbound, result):
        if not inbound:
            return
        frame = result.get("frame") or {}
        send = result.get("send_result") or {}
        status = str(frame.get("status") or "")
        if status in {"queued", "steer_accepted"} and send.get("ok"):
            return
        if status in {"stop_requested", "idle", "finalizing", "busy_other_actor", "duplicate", "queue_failed"}:
            self.plugin_turn_router.finish_inbound(inbound, model_status="not_started",
                reason="inbound_control_or_queue_handled")
            return
        model_status = "stopped" if status == "stopped" or frame.get("_turn_stopped") else "completed"
        model_failed = result.get("final_frame_received") is False or frame.get("_transient_final_failure")
        delivery_failed = not send.get("ok") or (result.get("file_send_result") or {}).get("ok") is False
        reason = "agent_turn_model_incomplete" if model_failed else "agent_turn_delivery_failed" if delivery_failed else ""
        self.plugin_turn_router.finish_inbound(inbound, model_status="failed" if model_failed else model_status,
            delivery_status=str(send.get("status") or ("sent" if send.get("ok") else "failed")), reason=reason)

    async def publish_message(self,
        *,
        context: Any,
        event: dict[str, Any],
        request: Request,
    ) -> None:
        """Publish one real QQ message as a typed host event.

        Publication is the only side effect: declared subscriptions decide what
        the host does with the event (observe, record, or request a turn).
        """

        inbound = getattr(context, "inbound_message", None)
        if inbound is None:
            return
        conversation_kind = str(getattr(inbound.conversation, "kind", "") or "").strip().lower()
        event_type = GROUP_CONVERSATION_EVENT if conversation_kind == "group" else DIRECT_CONVERSATION_EVENT
        try:
            broker = (
                self.plugin_event_broker_provider()
                if self.plugin_event_broker_provider is not None
                else getattr(request.app.state, "akane_plugin_event_broker", None)
            )
            if broker is None:
                return
        except asyncio.CancelledError:
            raise
        except Exception:
            self.log_event(
                "qq_plugin_event_degraded",
                session_id=str(getattr(context, "session_id", "") or ""),
                profile_user_id=str(getattr(context, "profile_user_id", "") or ""),
                broker_status="broker_unavailable",
                handler_failure_count=0,
            )
            return
        event_key = str(getattr(inbound, "event_id", "") or "").strip()
        try:
            reference = self.plugin_conversation_ref_issuer(
                profile_user_id=context.profile_user_id, session_id=context.session_id,
                character_pack_id=context.character_pack_id, user_id=context.user_id, group_id=context.group_id,
                actor_stable_id=f"qq:{context.user_id}" if context.is_group else "",
                actor_profile_user_id=str(context.actor_profile_user_id or ""),
            ) if self.plugin_conversation_ref_issuer else ""
            receipt = await broker.emit(event_type, {
                **inbound.public_summary(), "channel": "qq",
                "parts": [part.public_summary() for part in inbound.chain.parts],
            }, context=PluginInvocationContext(context.profile_user_id, context.session_id, "qq",
                character_pack_id=context.character_pack_id, conversation_ref=reference),
                event_key=event_key, occurred_at_ms=int(getattr(inbound, "timestamp", 0) or 0) * 1000 or None)
            if receipt.status == "rejected":
                self.log_event("qq_plugin_event_degraded", broker_status=receipt.status,
                          reason=receipt.reason, event_key=event_key)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.log_event("qq_plugin_event_degraded", broker_status="publication_failed", event_key=event_key)

    async def publish_poke(self,
        *,
        context: Any,
        event: dict[str, Any],
        outcome: Any,
        request: Request,
    ) -> None:
        """Publish a real QQ poke as a typed public event.

        The host has already applied its own care effect; subscribers only get
        the observed facts and the resolved outcome, never the host prompt text
        or the ability to choose the conversation.
        """

        inbound = getattr(context, "inbound_message", None)
        if inbound is None or outcome is None:
            return
        try:
            broker = (
                self.plugin_event_broker_provider()
                if self.plugin_event_broker_provider is not None
                else getattr(request.app.state, "akane_plugin_event_broker", None)
            )
            if broker is None or not broker.observes(POKE_CONVERSATION_EVENT):
                return
        except asyncio.CancelledError:
            raise
        except Exception:
            self.log_event(
                "qq_plugin_poke_degraded",
                session_id=str(getattr(context, "session_id", "") or ""),
                profile_user_id=str(getattr(context, "profile_user_id", "") or ""),
                broker_status="broker_unavailable",
            )
            return
        event_key = str(getattr(outcome, "event_id", "") or "").strip()
        if not event_key:
            event_key = str(getattr(inbound, "event_id", "") or "").strip()
        try:
            receipt = await broker.emit(
                POKE_CONVERSATION_EVENT,
                {
                    "event_kind": "poke",
                    "channel": "qq",
                    "conversation_kind": str(getattr(inbound.conversation, "kind", "") or "direct"),
                    "conversation_id": str(getattr(inbound.conversation, "id", "") or ""),
                    "actor_id": str(getattr(inbound.actor, "id", "") or ""),
                    "actor_label": str(getattr(outcome, "actor_label", "") or ""),
                    "outcome_kind": str(getattr(outcome, "outcome_kind", "") or "plain"),
                    "status": str(getattr(outcome, "status", "") or "ok"),
                    "reason": str(getattr(outcome, "reason", "") or ""),
                },
                context=self.invocation_context(context),
                event_key=event_key,
                occurred_at_ms=int(getattr(inbound, "timestamp", 0) or 0) * 1000 or None,
            )
            if receipt.status == "rejected":
                self.log_event(
                    "qq_plugin_poke_degraded",
                    session_id=str(getattr(context, "session_id", "") or ""),
                    profile_user_id=str(getattr(context, "profile_user_id", "") or ""),
                    broker_status=receipt.status,
                    reason=str(getattr(receipt, "reason", "") or ""),
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            self.log_event(
                "qq_plugin_poke_degraded",
                session_id=str(getattr(context, "session_id", "") or ""),
                profile_user_id=str(getattr(context, "profile_user_id", "") or ""),
                broker_status="publication_failed",
            )
