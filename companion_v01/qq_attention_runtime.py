"""QQGroupAttentionRuntime: per-Bot state and explicit conversation dependencies."""

from __future__ import annotations

from .qq_artifact_delivery import _qq_has_visible_action_receipt
from .qq_group_attention import AttentionTicket
from .qq_group_attention import QQGroupAttentionState
from dataclasses import replace
from typing import Any
import asyncio
import time
from .qq_session_work import _session_work_key


GROUP_ATTENTION_REVIEW_CONTEXT = (
    "【本轮群聊主动观察】\n"
    "turn_kind: qq_attention"
)


def _group_attention_review_event(reason: Any) -> str:
    return (
        "event.group_attention_followup_review"
        if str(reason or "").strip().lower() == "engaged_followup"
        else "event.group_attention_idle_review"
    )


def _apply_group_attention_actor_scope(turn_payload: dict[str, Any], *, reason: Any) -> None:
    """Keep capability ownership only for a concrete engaged follow-up.

    An idle observation represents the room and must not inherit whichever
    member happened to speak last.  An engaged follow-up, however, is armed by
    a real inbound message from a concrete actor.  Keeping that actor scope
    lets actor-owned tools (for example project workspaces) operate without
    changing the separate ``message_addressing`` decision that still tells the
    model this is an observed, optional-reply turn.
    """

    if str(reason or "").strip().lower() == "engaged_followup":
        return
    for field in ("actor_stable_id", "actor_profile_user_id", "actor_display_name", "actor_platform"):
        turn_payload.pop(field, None)


class QQGroupAttentionRuntime:
    def __init__(
        self, *, engine, config_module, qq_gateway, logger, log_event,
        turn_coordinator, state, queue_has_work, run_turn, schedule_task,
    ):
        self.engine = engine
        self.config_module = config_module
        self.qq_gateway = qq_gateway
        self.logger = logger
        self.log_event = log_event
        self.turn_coordinator = turn_coordinator
        self.state = state
        self.queue_has_work = queue_has_work
        self.run_turn = run_turn
        self.schedule_task = schedule_task
        self._latest: dict[str, tuple] = {}
        self._pending_media: dict[str, list[dict[str, Any]]] = {}
        self._control_tokens: dict[str, str] = {}

    def bind_control(self, ticket: AttentionTicket, control_id: str) -> None:
        self._control_tokens[ticket.token] = control_id

    def release_control(self, ticket: AttentionTicket) -> None:
        self._control_tokens.pop(ticket.token, None)

    @staticmethod
    def actor_key(context: Any) -> str:
        user_id = int(getattr(context, "user_id", 0) or 0)
        if user_id:
            return f"qq:{user_id}"
        return str(getattr(context, "sender_label", "") or "").strip()

    def stage_media(self,
        context: Any,
        event: dict[str, Any],
        registered_items: list[dict[str, Any]],
        *,
        timeline_source_id: str,
    ) -> None:
        attachment_ids = tuple(
            str(item.get("attachment_id") or "").strip()
            for item in registered_items
            if str(item.get("kind") or "").strip().lower() == "image"
            and str(item.get("attachment_id") or "").strip()
        )
        if not attachment_ids:
            return
        key = _session_work_key(context)
        timestamp = int(event.get("time") or time.time())
        ttl = max(10, int(getattr(self.config_module, "QQ_GROUP_ATTENTION_TTL_SECONDS", 120) or 120))
        pending = [
            item
            for item in self._pending_media.get(key, [])
            if timestamp - int(item.get("timestamp") or 0) <= ttl
        ]
        pending.append(
            {
                "actor_key": self.actor_key(context),
                "timestamp": timestamp,
                "attachment_ids": attachment_ids,
                "timeline_source_id": str(timeline_source_id or "").strip(),
            }
        )
        self._pending_media[key] = pending[-16:]

    def _take_media(self, context: Any, event: dict[str, Any]) -> dict[str, tuple[str, ...]]:
        key = _session_work_key(context)
        timestamp = int(event.get("time") or time.time())
        actor_key = self.actor_key(context)
        ttl = max(10, int(getattr(self.config_module, "QQ_GROUP_ATTENTION_TTL_SECONDS", 120) or 120))
        pending = self._pending_media.get(key, [])
        eligible = [
            item
            for item in pending
            if str(item.get("actor_key") or "") == actor_key
            and 0 <= timestamp - int(item.get("timestamp") or 0) <= ttl
        ]
        selected = eligible[-1] if eligible else None
        retained = [
            item
            for item in pending
            if item is not selected and timestamp - int(item.get("timestamp") or 0) <= ttl
        ]
        if retained:
            self._pending_media[key] = retained
        else:
            self._pending_media.pop(key, None)
        if not selected:
            return {"attachment_ids": (), "timeline_source_ids": ()}
        timeline_source_id = str(selected.get("timeline_source_id") or "").strip()
        return {
            "attachment_ids": tuple(
                str(item or "").strip()
                for item in selected.get("attachment_ids") or ()
                if str(item or "").strip()
            ),
            "timeline_source_ids": (timeline_source_id,) if timeline_source_id else (),
        }

    def mode(self, context: Any) -> str:
        default_mode = str(getattr(self.config_module, "QQ_GROUP_ATTENTION_MODE", "engaged") or "engaged")
        resolver = getattr(self.qq_gateway, "resolve_group_attention_mode", None)
        if callable(resolver):
            return str(resolver(getattr(context, "group_id", 0), default=default_mode) or default_mode)
        return QQGroupAttentionState.normalize_mode(default_mode)

    def has_visible_delivery(self, result: Any) -> bool:
        payload = result if isinstance(result, dict) else {}
        frame = payload.get("frame") if isinstance(payload.get("frame"), dict) else {}
        if bool(payload.get("visible_action_delivered")) or _qq_has_visible_action_receipt(
            frame.get("tool_events")
        ):
            return True
        if frame.get("_transient_final_failure"):
            return False
        if list(payload.get("reply_messages") or []):
            return True
        file_result = payload.get("file_send_result") if isinstance(payload.get("file_send_result"), dict) else {}
        return bool(file_result.get("ok")) and int(file_result.get("count") or 0) > 0

    def mark_engaged(self, context: Any, result: Any) -> None:
        if getattr(context, "dialogue_target", None) is not None:
            return
        if not bool(getattr(context, "is_group", False)) or not self.has_visible_delivery(result):
            return
        self.state.mark_visible_reply(
            _session_work_key(context),
            ttl_seconds=float(getattr(self.config_module, "QQ_GROUP_ATTENTION_TTL_SECONDS", 120) or 120),
        )

    def cancel(self, context: Any) -> None:
        if not bool(getattr(context, "is_group", False)):
            return
        key = _session_work_key(context)
        self.state.cancel(key)
        self._latest.pop(key, None)

    def supersede(self, context: Any) -> None:
        key = _session_work_key(context)
        def stop_running(token: str) -> bool:
            control_token = self._control_tokens.get(token)
            return not control_token or self.turn_coordinator.request_stop_token(control_token)

        token = self.state.supersede_for_actor(
            key, self.actor_key(context), stop_running=stop_running,
        )
        if not token:
            return
        self.log_event(
            "qq_group_attention_superseded",
            session_id=context.session_id,
            profile_user_id=context.profile_user_id,
            group_id=int(getattr(context, "group_id", 0) or 0),
            reason="window_participant_followup",
        )

    def cancelled(self, ticket: AttentionTicket) -> bool:
        return not self.state.is_current(ticket) or self.state.is_superseded(ticket)

    def can_continue(self, ticket: AttentionTicket, context: Any) -> bool:
        reason = ""
        if not self.state.is_current(ticket):
            reason = "cancelled"
        elif self.state.is_superseded(ticket):
            reason = "window_participant_followup"
        elif self.mode(context) == "off":
            reason = "mode_off"
        if not reason:
            return True
        self.log_event(
            "qq_group_attention_skipped",
            session_id=context.session_id,
            profile_user_id=context.profile_user_id,
            group_id=int(getattr(context, "group_id", 0) or 0),
            reason=reason,
            attention_reason=ticket.reason,
        )
        return False

    async def _run_ticket(self, ticket: AttentionTicket) -> None:
        await asyncio.sleep(max(0.0, ticket.deadline - time.monotonic()))
        if not self.state.claim(ticket):
            return
        completed = False
        newer_generation_waiting = False
        snapshot = None
        try:
            snapshot = self._latest.pop(ticket.key, None)
            if snapshot is not None:
                completed = await self._observe_ticket(ticket, snapshot)
        except Exception as exc:
            if not self.cancelled(ticket) and ticket.reason == "idle_observation":
                self.state.mark_idle_observed(
                    ticket.key,
                    cooldown_seconds=float(
                        getattr(self.config_module, "QQ_GROUP_ATTENTION_IDLE_COOLDOWN_SECONDS", 60) or 60
                    ),
                )
            self.logger.exception("qq group attention observation failed")
            context = snapshot[0] if snapshot is not None else None
            self.log_event(
                "qq_group_attention_failed",
                session_id=str(getattr(context, "session_id", "") or ""),
                profile_user_id=str(getattr(context, "profile_user_id", "") or ""),
                group_id=int(getattr(context, "group_id", 0) or 0),
                attention_reason=ticket.reason,
                reason=exc.__class__.__name__,
            )
        finally:
            # Includes preparation failures and task cancellation. An obsolete
            # task must never remove a newer generation's snapshot or ticket.
            if self.state.is_current(ticket):
                restart = self.state.is_superseded(ticket)
                newer_generation_waiting = self.state.finish(ticket, discard_dirty=not (completed or restart))
                if not completed and not restart:
                    self._latest.pop(ticket.key, None)
        if not newer_generation_waiting:
            return
        next_snapshot = self._latest.get(ticket.key)
        if next_snapshot is None:
            return
        next_context = next_snapshot[0]
        next_ticket, next_reason = self.state.arm(
            ticket.key,
            mode=self.mode(next_context),
            actor_keys=next_snapshot[5],
            delay_seconds=float(getattr(self.config_module, "QQ_GROUP_ATTENTION_DELAY_SECONDS", 10.0) or 0.0),
        )
        if next_ticket is not None and next_reason == "armed":
            self.schedule_task(self._run_ticket(next_ticket))
            self.log_event(
                "qq_group_attention_rearmed",
                session_id=str(getattr(next_context, "session_id", "") or ""),
                profile_user_id=str(getattr(next_context, "profile_user_id", "") or ""),
                group_id=int(getattr(next_context, "group_id", 0) or 0),
                reason="newer_memcore_generation",
                attention_reason=next_ticket.reason,
            )
        elif next_reason not in {"already_pending", "in_flight_dirty"}:
            self._latest.pop(ticket.key, None)

    async def _observe_ticket(self, ticket: AttentionTicket, snapshot: tuple) -> bool:
        context, event, projection_anchor_source_id, attachment_ids, stimulus_source_ids, _participant_keys = snapshot
        # Ambient participation is based on the whole MemCore projection, not
        # mechanically on the last message that happened to update the ticket.
        # Clear only the delivery reply reference so Akane joins the discussion
        # naturally instead of quoting an arbitrary last line.
        context = replace(context, source_message_id="")
        if not self.can_continue(ticket, context):
            return False
        if self.turn_coordinator.is_busy(context.profile_user_id, context.session_id) or self.queue_has_work(
            ticket.key
        ):
            if ticket.reason == "idle_observation":
                self.state.mark_idle_observed(
                    ticket.key,
                    cooldown_seconds=float(
                        getattr(self.config_module, "QQ_GROUP_ATTENTION_IDLE_COOLDOWN_SECONDS", 60) or 60
                    ),
                )
            self.log_event(
                "qq_group_attention_skipped",
                session_id=context.session_id,
                profile_user_id=context.profile_user_id,
                group_id=int(getattr(context, "group_id", 0) or 0),
                reason="session_busy",
                attention_reason=ticket.reason,
            )
            return False
        review_event = _group_attention_review_event(ticket.reason)
        turn_payload = context.to_turn_payload()
        turn_payload.update(
            {
                "message": review_event,
                "timestamp": int(time.time()),
                "turn_kind": "qq_attention",
                "transient_user_message": True,
                # Request-scoped guidance follows the real history through the
                # existing volatile context path; never persist it as a message.
                "extra_context": "\n\n".join(
                    part for part in (
                        str(turn_payload.get("extra_context") or "").strip(),
                        GROUP_ATTENTION_REVIEW_CONTEXT,
                    ) if part
                ),
                # The review instruction exists only in this provider request.
                # Real group messages are already durable MemCore facts; these
                # IDs are lineage evidence for a hidden host-owned turn and
                # must never be relinked or copied into a synthetic message.
                "memory_attention_reference_source_ids": list(stimulus_source_ids),
                "message_addressing": {
                    "mode": "observed",
                    "trigger": review_event.removeprefix("event."),
                    "addressed_to_assistant": False,
                    "explicit_assistant_mention": False,
                    "primary_target": {},
                    "mentions": [],
                },
            }
        )
        if attachment_ids and self.qq_gateway.is_group_vision_enabled(context.group_id):
            turn_payload["qq_current_attachment_ids"] = list(attachment_ids)
            native_prepare = getattr(self.engine, "prepare_qq_native_image_inputs", None)
            if callable(native_prepare):
                native_result = await asyncio.to_thread(
                    native_prepare,
                    profile_user_id=context.profile_user_id,
                    session_id=context.session_id,
                    attachment_ids=list(attachment_ids),
                    chat_model_override=str(getattr(context, "chat_model_override", "") or ""),
                    timeout_seconds=max(
                        0.0,
                        min(
                            15.0,
                            float(getattr(self.config_module, "QQ_ATTACHMENT_READY_WAIT_SECONDS", 8.0) or 0.0),
                        ),
                    ),
                )
                native_images = [
                    dict(item)
                    for item in list((native_result or {}).get("images") or [])
                    if isinstance(item, dict) and str(item.get("data_url") or "").startswith("data:image/")
                ][:5]
                if native_images:
                    turn_payload["native_user_images"] = native_images
                self.log_event(
                    "qq_group_attention_media_bound",
                    session_id=context.session_id,
                    profile_user_id=context.profile_user_id,
                    group_id=int(getattr(context, "group_id", 0) or 0),
                    attachment_count=len(attachment_ids),
                    native_image_count=len(native_images),
                    native_status=str((native_result or {}).get("status") or "unavailable"),
                )
        _apply_group_attention_actor_scope(turn_payload, reason=ticket.reason)
        if not self.can_continue(ticket, context):
            return False
        result = await self.run_turn(
            context=context, event=event, turn_payload=turn_payload, attention_ticket=ticket,
        )
        if result.get("attention_skipped") or self.cancelled(ticket):
            return False
        if ticket.reason == "idle_observation" and not self.has_visible_delivery(result):
            self.state.mark_idle_observed(
                ticket.key,
                cooldown_seconds=float(
                    getattr(self.config_module, "QQ_GROUP_ATTENTION_IDLE_COOLDOWN_SECONDS", 60) or 60
                ),
            )
        visible_reply = self.has_visible_delivery(result)
        result_frame = dict(result.get("frame") or {}) if isinstance(result, dict) else {}
        self.log_event(
            "qq_group_attention_completed",
            session_id=context.session_id,
            profile_user_id=context.profile_user_id,
            group_id=int(getattr(context, "group_id", 0) or 0),
            attention_reason=ticket.reason,
            visible_reply=visible_reply,
            silent=bool(result_frame.get("_deliberate_silence")) and not visible_reply,
            silent_reason="model_decision" if bool(result_frame.get("_deliberate_silence")) else "",
        )
        return True

    def schedule(self,
        context: Any,
        event: dict[str, Any],
        *,
        projection_anchor_source_id: str,
        participant_keys: tuple[str, ...] = (),
        forward_references: tuple[dict[str, Any], ...] = (),
    ) -> dict[str, Any]:
        if not bool(getattr(context, "is_group", False)):
            return {"scheduled": False, "reason": "not_group"}
        key = _session_work_key(context)
        participant_keys = participant_keys or (self.actor_key(context),)
        anchor_source_id = str(projection_anchor_source_id or "").strip()

        def update_snapshot(media_evidence: dict[str, tuple[str, ...]]) -> None:
            previous = self._latest.get(key)
            attachment_ids = tuple(dict.fromkeys([
                *(previous[3] if previous is not None else ()),
                *media_evidence["attachment_ids"],
            ]))[-5:]
            stimulus_source_ids = tuple(dict.fromkeys([
                *(previous[4] if previous is not None else ()),
                *media_evidence["timeline_source_ids"],
                anchor_source_id,
            ]))
            self._latest[key] = (
                context, dict(event), anchor_source_id, attachment_ids, stimulus_source_ids,
                tuple(dict.fromkeys([*(previous[5] if previous is not None else ()), *participant_keys])),
            )

        inbound = getattr(context, "inbound_message", None)
        parts = tuple(getattr(getattr(inbound, "chain", None), "parts", ()) or ())
        if (
            forward_references
            and all(not item.get("nodes") for item in forward_references)
            and parts
            and all(str(getattr(part, "segment_type", "") or "") == "forward" for part in parts)
        ):
            if self.state.cancel_pending(key):
                self._latest.pop(key, None)
            return {"scheduled": False, "reason": "forward_content_unavailable"}
        self.state.note_pending_actors(key, participant_keys)
        if any(
            isinstance(item, dict) and str(item.get("kind") or "").strip().lower() == "image"
            for item in list(getattr(context, "attachments", None) or [])
        ):
            # Passive storage and active perception are separate concerns. An
            # unaddressed image alone does not start a model request. When a
            # review is already pending, bind its exact pixels to that review.
            # A running review restarts after it settles, using the new image.
            reason = self.state.refresh_if_active(key, participant_keys)
            if reason and anchor_source_id:
                media_evidence = self._take_media(context, event)
                if media_evidence["attachment_ids"]:
                    update_snapshot(media_evidence)
                    return {"scheduled": False, "reason": "passive_image_bound_to_attention"}
                if self.state.cancel_pending(key):
                    self._latest.pop(key, None)
            return {"scheduled": False, "reason": "passive_image_recorded"}
        if not anchor_source_id:
            return {"scheduled": False, "reason": "projection_anchor_missing"}
        ticket, reason = self.state.arm(
            key,
            mode=self.mode(context),
            actor_keys=participant_keys,
            delay_seconds=float(getattr(self.config_module, "QQ_GROUP_ATTENTION_DELAY_SECONDS", 10.0) or 0.0),
        )
        if ticket is None and reason != "in_flight_dirty":
            return {"scheduled": False, "reason": reason}
        update_snapshot(self._take_media(context, event))
        if reason != "armed":
            return {"scheduled": False, "reason": reason}
        self.schedule_task(self._run_ticket(ticket))
        return {"scheduled": True, "reason": ticket.reason, "deadline": ticket.deadline}
