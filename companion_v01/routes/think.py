from __future__ import annotations

from contextlib import nullcontext
from ..plugin_turn_intents import HostTurnIntent, HostTurnResult, PluginTurnError, apply_turn_intent, normalize_turn_intent

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

import config
from ..client_protocol import ClientMode, default_capabilities_for_mode
from ..desktop_pet_contract import DESKTOP_PET_CONTRACT_VERSION, build_desktop_pet_error_payload
from ..durable_session_queue import DurableSessionWorkQueue, RetryableSessionWorkError, SessionWorkError
from ..host_completion_batch import (
    completion_batch_key,
    completion_input_fingerprint,
    completion_metadata,
    completion_timestamp,
    prepare_completion_turn,
    record_completion_delivery,
    record_completion_error,
)
from ..turn_coordination import cancellation_requested, cancellation_scope
from ..session_inbox import SessionInboxItem
from ..plugin_api import DIRECT_CONVERSATION_EVENT, PluginInvocationContext
from ..plugin_text_presentation import apply_plugin_text_presentation_policy
from ..turn_coordination import TurnCoordinator

logger = logging.getLogger("akane.think")


LogEvent = Callable[..., None]

_DESKTOP_AGENT_EVENT_PRESENTATION_FIELDS = (
    "trace_id",
    "status",
    "emotion",
    "speech",
    "speech_segments",
    "activity",
    "care_state",
    "character",
    "choices",
    "tool_events",
    "client_mode",
    "client",
)


def _desktop_agent_event_presentation(
    frame: dict[str, Any],
    request: HostTurnIntent,
) -> dict[str, Any]:
    """Keep the ordinary desktop output contract while excluding internals."""

    frame = apply_plugin_text_presentation_policy(
        dict(frame),
        strip_leading_addresses_from=getattr(request, "strip_leading_addresses", ()),
    )
    presentation = _desktop_frame_presentation(frame)
    presentation_mode = getattr(request, "presentation_mode", "default")
    if presentation_mode != "single_message":
        return presentation
    speech = str(frame.get("speech") or "").strip()
    if not speech:
        segments = frame.get("speech_segments")
        if isinstance(segments, (list, tuple)):
            speech = "\n".join(str(item or "").strip() for item in segments if str(item or "").strip())
    if not speech:
        return presentation
    completed = "\n".join(
        part
        for part in (
            str(getattr(request, "presentation_prefix", "") or "").strip(),
            speech,
            str(getattr(request, "presentation_suffix", "") or "").strip(),
        )
        if part
    )
    presentation["speech"] = completed
    presentation["speech_segments"] = [completed]
    return presentation


def _desktop_frame_presentation(frame: dict[str, Any]) -> dict[str, Any]:
    frame = _desktop_tool_completion_frame(frame)
    return {
        key: frame[key]
        for key in _DESKTOP_AGENT_EVENT_PRESENTATION_FIELDS
        if key in frame
    }


def _desktop_tool_completion_frame(frame: dict[str, Any]) -> dict[str, Any]:
    """Keep completed tool deliveries renderable without creating a reply cue."""
    if not frame.get("_deliberate_silence"):
        return frame
    if not any(event.get("type") in {"file_ready", "generated_file_ready"} and event.get("send_to_user")
               for event in frame.get("tool_events", ()) if isinstance(event, dict)):
        return frame
    # The desktop's legacy silence flag skips its entire render function.
    # File-only completion needs that existing function, but no reply effects.
    return {key: value for key, value in frame.items() if key not in {"_deliberate_silence", "emotion", "activity"}}


def build_think_router(
    *,
    engine: Any,
    public_guard: Any,
    runtime_metrics: Any,
    log_event: LogEvent,
    turn_coordinator: Any = None,
    session_work_queue: Any = None,
    plugin_event_broker_provider: Callable[[], Any] | None = None,
    plugin_agent_event_handler_registrar: Callable[[str, Any], None] | None = None,
    desktop_agent_frame_delivery: Callable[[dict[str, Any]], Any] | None = None,
    desktop_agent_event_available: Callable[[], bool] | None = None,
    plugin_turn_router: Any = None,
    plugin_conversation_ref_issuer: Callable[..., str] | None = None,
) -> APIRouter:
    router = APIRouter()
    turn_coordinator = turn_coordinator or TurnCoordinator()

    def _turn_identity(payload: dict[str, Any]) -> tuple[str, str, str]:
        session_id = str(payload.get("user_id") or payload.get("session_id") or "default_session")
        profile_user_id = str(payload.get("real_user_id") or session_id)
        actor_id = str(payload.get("actor_stable_id") or f"desktop:{profile_user_id}").strip()
        return profile_user_id, session_id, actor_id

    def _plugin_context(payload):
        profile, session, _ = _turn_identity(payload)
        character = str(payload.get("character_pack_id") or "").strip()
        reference = plugin_conversation_ref_issuer(profile_user_id=profile,
            session_id=session, character_pack_id=character) if plugin_conversation_ref_issuer else ""
        return PluginInvocationContext(profile, session, "desktop_pet",
            character_pack_id=character, conversation_ref=reference)

    def _inbound_scope(payload):
        return plugin_turn_router.inbound(_plugin_context(payload)) if plugin_turn_router else nullcontext("")

    def _inbound_processing(inbound, token):
        return plugin_turn_router.processing_inbound(inbound, token) if inbound else nullcontext()

    def _finish_inbound(inbound, *, frame=None, model_status="failed", delivery_status="not_sent", reason=""):
        if inbound:
            if isinstance(frame, dict):
                model_status = "stopped" if frame.get("status") == "stopped" else "completed"
                if frame.get("_transient_final_failure"):
                    model_status, reason = "failed", "desktop_session_turn_incomplete"
            plugin_turn_router.finish_inbound(inbound, model_status=model_status,
                delivery_status=delivery_status, reason=reason)

    async def _publish_plugin_direct_event(request: Request | None, payload: dict[str, Any]) -> None:
        """Publish one desktop direct message as a typed host event.

        Publication is the only side effect: declared subscriptions decide what
        the host does with the event (observe, record, or request a turn).
        """

        profile_user_id, session_id, actor_id = _turn_identity(payload)
        try:
            broker = (
                plugin_event_broker_provider()
                if plugin_event_broker_provider is not None
                else getattr(request.app.state, "akane_plugin_event_broker", None)
                if request is not None
                else None
            )
            if broker is None:
                return
        except asyncio.CancelledError:
            raise
        except Exception:
            log_event(
                "desktop_plugin_event_degraded",
                session_id=session_id,
                profile_user_id=profile_user_id,
                broker_status="broker_unavailable",
                handler_failure_count=0,
            )
            return

        occurred_at = _positive_int(payload.get("timestamp"), default=int(time.time()))
        event_key = str(
            payload.get("source_message_id")
            or payload.get("turn_id")
            or payload.get("turnId")
            or f"desktop:{occurred_at}:{uuid.uuid4().hex}"
        ).strip()
        try:
            character = str(payload.get("character_pack_id") or "").strip()
            reference = plugin_conversation_ref_issuer(profile_user_id=profile_user_id,
                session_id=session_id, character_pack_id=character) if plugin_conversation_ref_issuer else ""
            receipt = await broker.emit(DIRECT_CONVERSATION_EVENT, {
                "conversation_kind": "direct",
                "conversation_id": session_id,
                "actor_id": actor_id,
                "actor_display_name": str(payload.get("actor_display_name") or "").strip(),
                "character_pack_id": character,
                "client_mode": str(payload.get("client_mode") or "desktop_pet").strip(),
                "text": str(payload.get("message") or ""),
                "channel": "desktop_pet",
                "timestamp": occurred_at,
                "attachment_ids": list(payload.get("current_attachment_ids") or ()),
            }, context=PluginInvocationContext(profile_user_id, session_id, "desktop_pet",
                character_pack_id=character, conversation_ref=reference),
                event_key=event_key, occurred_at_ms=occurred_at * 1000)
            if receipt.status == "rejected":
                log_event("desktop_plugin_event_degraded", broker_status=receipt.status,
                          reason=receipt.reason, event_key=event_key)
        except asyncio.CancelledError:
            raise
        except Exception:
            log_event("desktop_plugin_event_degraded", broker_status="publication_failed", event_key=event_key)

    def _positive_int(value: Any, *, default: int) -> int:
        try:
            resolved = int(value or 0)
        except (TypeError, ValueError):
            resolved = 0
        return resolved if resolved > 0 else int(default)

    def _desktop_delivery_is_available() -> bool:
        if desktop_agent_frame_delivery is None:
            return False
        try:
            return desktop_agent_event_available is None or bool(desktop_agent_event_available())
        except Exception:
            return False

    async def _deliver_desktop_frame(frame: dict[str, Any]) -> dict[str, Any]:
        if cancellation_requested():
            return {"ok": False, "status": "cancelled", "reason": "turn_scope_revoked"}
        if _desktop_tool_completion_frame(frame).get("_deliberate_silence"):
            return {"ok": True, "status": "not_requested"}
        if desktop_agent_frame_delivery is None:
            return {"ok": False, "status": "unavailable", "reason": "desktop_client_unavailable"}
        delivered = desktop_agent_frame_delivery(_desktop_frame_presentation(frame))
        if hasattr(delivered, "__await__"):
            delivered = await delivered
        return delivered if isinstance(delivered, dict) else {
            "ok": False,
            "status": "failed",
            "reason": "desktop_delivery_result_invalid",
        }

    async def _deliver_completion_files(completion) -> dict[str, Any]:
        results = []
        for event in completion.direct_events():
            if not event.get("send_to_user"):
                continue
            target = event["generated_file"]
            with cancellation_scope(lambda target=target: not completion.allows_file_target(target)):
                try:
                    delivered = await _deliver_desktop_frame({
                        "status": "ok", "speech": "", "speech_segments": [],
                        "_deliberate_silence": True, "tool_events": [event],
                        "client_mode": "desktop_pet"})
                except Exception as exc:
                    delivered = {"ok": False, "status": "failed", "reason": type(exc).__name__}
            results.append({"generated_id": target["generated_id"], **delivered})
        all_ok = all(result.get("ok") for result in results)
        any_ok = any(result.get("ok") for result in results)
        return {"ok": all_ok, "status": "queued" if all_ok and results else "not_requested" if all_ok
                else "partial" if any_ok else "failed", "count": len(results), "results": results}

    async def _reserve_desktop_turn(payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(session_work_queue, DurableSessionWorkQueue):
            return {"ok": True, "status": "unmanaged"}
        profile_user_id, session_id, _actor_id = _turn_identity(payload)
        source_event_id = str(payload.get("source_message_id") or f"desktop:{uuid.uuid4().hex}").strip()
        payload["source_message_id"] = source_event_id
        persisted = await session_work_queue.enqueue(
            session_key=f"{profile_user_id}\0{session_id}",
            profile_user_id=profile_user_id,
            session_id=session_id,
            kind="turn",
            payload={"turn_payload": dict(payload)},
            source="desktop_pet",
            source_event_id=source_event_id,
            schedule=False,
        )
        if not persisted.get("ok"):
            return persisted
        existing = persisted.get("item")
        existing_status = str(getattr(existing, "status", "") or "")
        item_id = str(persisted.get("item_id") or getattr(existing, "item_id", "") or "")
        if persisted.get("status") == "duplicate" and existing_status in {"claimed", "committed"}:
            return {
                "ok": True,
                "status": "duplicate",
                "reason": f"source_event_already_{existing_status}",
                "item_id": item_id,
            }
        if persisted.get("status") == "duplicate" and existing_status == "failed":
            return {"ok": False, "status": "failed", "reason": "source_event_previously_failed"}
        session_key = f"{profile_user_id}\0{session_id}"
        claim = await session_work_queue.claim_for_active_turn(session_key, item_id)
        if not claim.get("ok"):
            await session_work_queue.schedule_session(session_key)
            return {
                "ok": True,
                "status": "queued",
                "reason": str(claim.get("reason") or "session_inbox_waiting"),
                "item_id": item_id,
                "session_key": session_key,
            }
        payload["memory_idempotency_key"] = f"session-inbox:{item_id}"
        return {
            "ok": True,
            "status": "claimed",
            "reason": "desktop_turn_claimed",
            "item_id": item_id,
            "claim_token": str(claim.get("claim_token") or ""),
            "session_key": session_key,
        }

    async def _settle_desktop_turn(reservation: dict[str, Any], *, completed: bool, reason: str = "") -> None:
        if reservation.get("status") != "claimed" or not isinstance(
            session_work_queue,
            DurableSessionWorkQueue,
        ):
            return
        item_id = str(reservation.get("item_id") or "")
        claim_token = str(reservation.get("claim_token") or "")
        session_key = str(reservation.get("session_key") or "")
        try:
            if completed:
                result = await session_work_queue.commit_claim(item_id, claim_token=claim_token)
            else:
                result = await session_work_queue.requeue_claim(
                    item_id,
                    claim_token=claim_token,
                    error=str(reason or "desktop_turn_interrupted"),
                )
                if result.get("ok"):
                    await session_work_queue.schedule_session(session_key)
        except Exception as exc:
            log_event(
                "desktop_session_receipt_failed",
                item_id=item_id,
                status="exception",
                reason=exc.__class__.__name__,
            )
            return
        if not result.get("ok"):
            log_event(
                "desktop_session_receipt_failed",
                item_id=item_id,
                status=str(result.get("status") or "failed"),
                reason=str(result.get("reason") or "session_receipt_settlement_failed"),
            )

    async def _submit_plugin_agent_event(
        event_request: HostTurnIntent,
        resolved_reference: dict[str, str],
    ) -> HostTurnResult:
        """Run a plugin event through the ordinary desktop Agent path."""

        try:
            event_request = normalize_turn_intent(event_request)
        except PluginTurnError as exc:
            return HostTurnResult(False, "rejected", exc.reason)
        if event_request.requires_queue and not isinstance(session_work_queue, DurableSessionWorkQueue):
            return HostTurnResult(False, "rejected", "agent_turn_queue_unavailable")
        if str(resolved_reference.get("channel") or "") != "desktop_pet":
            return HostTurnResult(False, "rejected", "event_context_unresolved")
        session_id = str(resolved_reference.get("session") or "").strip()
        profile_user_id = str(resolved_reference.get("profile") or "").strip()
        character_pack_id = str(resolved_reference.get("character") or "").strip()
        recipient = str(resolved_reference.get("recipient") or "").strip()
        if (
            str(resolved_reference.get("kind") or "") != "direct"
            or not session_id
            or not profile_user_id
            or not character_pack_id
            or recipient != f"desktop:{session_id}"
        ):
            return HostTurnResult(False, "rejected", "event_context_unresolved")
        message = str(event_request.message or "").strip()
        timestamp = completion_timestamp(event_request, int(time.time()))
        payload: dict[str, Any] = {
            "user_id": session_id,
            "session_id": session_id,
            "real_user_id": profile_user_id,
            "actor_stable_id": "host:plugin_event",
            "message": message,
            "memory_message": message,
            "timestamp": timestamp,
            "client_mode": ClientMode.DESKTOP_PET.value,
            "client_capabilities": list(default_capabilities_for_mode(ClientMode.DESKTOP_PET)),
            "character_pack_id": character_pack_id,
            "turn_kind": "plugin_event",
            "transient_user_message": getattr(event_request, "memory_mode", "current_turn") == "current_turn",
            "plugin_external_event": event_request.event_payload(),
            "memory_idempotency_key": str(getattr(event_request, "idempotency_key", "") or "").strip(),
            "plugin_text_strip_leading_addresses": list(
                getattr(event_request, "strip_leading_addresses", ())
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
        if isinstance(session_work_queue, DurableSessionWorkQueue):
            source_event_id = str(
                getattr(event_request, "idempotency_key", "") or event_request.trace_id or ""
            ).strip()
            queued = await session_work_queue.enqueue(
                session_key=f"{profile_user_id}\0{session_id}",
                profile_user_id=profile_user_id,
                session_id=session_id,
                kind="turn",
                payload={
                    "turn_payload": payload,
                    **({"plugin_turn_request_id": event_request.request_id} if event_request.request_id else {}),
                    "host_completion": completion_metadata(event_request, resolved_reference),
                },
                source="desktop_pet",
                source_event_id=f"plugin-turn:{event_request.request_id}" if event_request.request_id else source_event_id,
                input_fingerprint=completion_input_fingerprint(event_request, resolved_reference),
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
            return HostTurnResult(True, "accepted", "", delivery_status, str(getattr(queued_item, "item_id", "")))
        if not _desktop_delivery_is_available():
            return HostTurnResult(False, "host_unavailable", "desktop_client_unavailable")
        try:
            async with turn_coordinator.hold(
                profile_user_id,
                session_id,
                actor_id="host:plugin_event",
                channel="desktop_pet",
                turn_kind="plugin_event",
            ) as turn_control_id:
                payload["_turn_control_id"] = turn_control_id
                frame = await asyncio.to_thread(engine.process_turn, payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("desktop plugin Agent event failed")
            return HostTurnResult(False, "failed", "agent_event_turn_failed", "not_sent")
        if not isinstance(frame, dict) or bool(frame.get("_transient_final_failure")):
            return HostTurnResult(False, "failed", "agent_event_turn_incomplete", "not_sent")

        presentation = _desktop_agent_event_presentation(frame, event_request)
        try:
            delivered = desktop_agent_frame_delivery(presentation)
            if hasattr(delivered, "__await__"):
                delivered = await delivered
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("desktop plugin Agent frame delivery failed")
            return HostTurnResult(False, "failed", "agent_event_delivery_failed", "not_sent")
        delivery_result = delivered if isinstance(delivered, dict) else {}
        if delivery_result.get("ok") is not True:
            return HostTurnResult(
                False,
                "failed",
                str(delivery_result.get("reason") or "agent_event_delivery_failed"),
                str(delivery_result.get("status") or "not_sent"),
            )
        return HostTurnResult(True, "completed", "", str(delivery_result.get("status") or "queued"))

    if plugin_agent_event_handler_registrar is not None:
        plugin_agent_event_handler_registrar("desktop_pet", _submit_plugin_agent_event)

    async def _handle_queued_desktop_work(_key: str, items: list[SessionInboxItem]) -> None:
        if not items:
            raise ValueError("desktop_session_work_items_required")
        item = items[0]
        request_id = str(item.payload.get("plugin_turn_request_id") or "")
        if request_id:
            if plugin_turn_router is None:
                raise PluginTurnError("agent_turn_scope_expired")
            plugin_turn_router.require_pending(request_id)
        payload = dict(item.payload.get("turn_payload") or {})
        profile_user_id, session_id, actor_id = _turn_identity(payload)
        if profile_user_id != item.profile_user_id or session_id != item.session_id:
            raise ValueError("desktop_session_work_identity_mismatch")
        if not _desktop_delivery_is_available():
            raise RetryableSessionWorkError(
                "desktop_client_unavailable",
                retry_delay_seconds=5.0,
            )
        payload.setdefault("client_mode", ClientMode.DESKTOP_PET.value)
        payload.setdefault(
            "client_capabilities",
            list(default_capabilities_for_mode(ClientMode.DESKTOP_PET)),
        )
        if not completion_batch_key(item) and not request_id:
            payload["memory_idempotency_key"] = f"session-inbox:{item.item_id}"
        with _inbound_scope(payload) if payload.get("turn_kind") != "plugin_event" else nullcontext("") as inbound:
            if payload.get("turn_kind") != "plugin_event":
                await _publish_plugin_direct_event(None, payload)
            async with turn_coordinator.hold(
                profile_user_id,
                session_id,
                actor_id=f"desktop:{profile_user_id}" if request_id else actor_id,
                channel="desktop_pet",
                turn_kind=str(payload.get("turn_kind") or ""),
            ) as turn_control_id:
                if completion_batch_key(item) or request_id:
                    await session_work_queue.begin_processing(items)
                if completion_batch_key(item):
                    completion = prepare_completion_turn(engine, items)
                    payload, prepared_frame = completion.payload, completion.prepared_frame
                    payload.setdefault("client_mode", ClientMode.DESKTOP_PET.value)
                    payload.setdefault("client_capabilities", list(default_capabilities_for_mode(ClientMode.DESKTOP_PET)))
                else:
                    completion, prepared_frame = None, None
                payload["_turn_control_id"] = turn_control_id
                if request_id:
                    apply_turn_intent(payload, plugin_turn_router.begin(request_id, turn_control_id))
                with completion.processing(turn_coordinator, turn_control_id) if completion else nullcontext():
                    with plugin_turn_router.processing(request_id) if request_id else _inbound_processing(inbound, turn_control_id):
                        if prepared_frame is not None:
                            frame, model_status = prepared_frame, "not_requested"
                        elif cancellation_requested():
                            frame, model_status = {"status": "stopped", "speech": ""}, "not_requested"
                        else:
                            frame = await asyncio.to_thread(engine.process_turn, payload)
                            model_status = "stopped" if isinstance(frame, dict) and frame.get("status") == "stopped" else "completed"
                    if cancellation_requested():
                        frame = {"status": "stopped", "speech": ""}
                    if prepared_frame is not None and not completion.direct_events():
                        record_completion_delivery(engine, items, ok=True, model_status="not_requested",
                            delivery_status="not_requested")
                        return
                    if isinstance(frame, dict) and frame.get("status") == "stopped":
                        record_completion_delivery(engine, items, ok=False, model_status=model_status,
                            delivery_status="not_sent", reason="host_completion_stopped")
                        _finish_inbound(inbound, frame=frame)
                        if request_id:
                            plugin_turn_router.finish(request_id, model_status="stopped",
                                                      reason=str(frame.get("reason") or ""))
                        return
                    if not isinstance(frame, dict) or bool(frame.get("_transient_final_failure")):
                        if completion_batch_key(item):
                            raise SessionWorkError("host_completion_turn_incomplete")
                        raise RuntimeError("desktop_session_turn_incomplete")
                    try:
                        model_delivery = await _deliver_desktop_frame(frame)
                        files = (await _deliver_completion_files(completion)
                                 if completion and model_delivery.get("ok") else None)
                        delivery_result = model_delivery
                        if files and (not files["ok"] or model_delivery.get("status") == "not_requested"):
                            delivery_result = {"ok": files["ok"], "status": files["status"],
                                               "reason": "" if files["ok"] else "host_completion_file_delivery_incomplete"}
                    except Exception:
                        _finish_inbound(inbound, frame=frame, delivery_status="failed", reason="agent_turn_delivery_failed")
                        if request_id:
                            plugin_turn_router.finish(request_id, model_status="completed", delivery_status="failed",
                                                      reason="agent_turn_delivery_failed")
                        raise
                    record_completion_delivery(engine, items, ok=model_delivery.get("ok") is True,
                        model_status=model_status, delivery_status=str(model_delivery.get("status") or "failed"),
                        reason="" if model_delivery.get("ok") else "host_completion_frame_delivery_failed",
                        files=files, files_for_direct_jobs=True)
                    if delivery_result.get("ok") is not True:
                        _finish_inbound(inbound, frame=frame, delivery_status=str(delivery_result.get("status") or "failed"), reason="agent_turn_delivery_failed")
                        if request_id:
                            plugin_turn_router.finish(request_id, model_status="completed",
                                delivery_status=str(delivery_result.get("status") or "failed"), reason="agent_turn_delivery_failed")
                        if completion_batch_key(item):
                            raise SessionWorkError("host_completion_frame_delivery_failed")
                        raise RuntimeError(str(delivery_result.get("reason") or "desktop_delivery_failed"))
                    if request_id:
                        plugin_turn_router.finish(request_id, model_status="completed",
                                                  delivery_status=str(delivery_result.get("status") or "queued"))
                    _finish_inbound(inbound, frame=frame, delivery_status=str(delivery_result.get("status") or "queued"))
                    log_event(
                        "desktop_session_work_completed",
                        session_id=session_id,
                        profile_user_id=profile_user_id,
                        source_event_id=item.source_event_id,
                        delivery_status=str(delivery_result.get("status") or "queued"),
                        batch_count=len(items),
                    )

    def _desktop_session_work_error(
        _key: str,
        items: list[SessionInboxItem],
        exc: BaseException,
    ) -> None:
        item = items[0] if items else None
        if not isinstance(exc, RetryableSessionWorkError):
            record_completion_error(engine, items, exc)
        request_id = str(item.payload.get("plugin_turn_request_id") or "") if item else ""
        if request_id and plugin_turn_router is not None and not isinstance(exc, RetryableSessionWorkError):
            plugin_turn_router.fail(request_id, exc)
        log_event(
            "desktop_session_work_deferred"
            if isinstance(exc, RetryableSessionWorkError)
            else "desktop_session_work_failed",
            session_id=str(getattr(item, "session_id", "") or ""),
            profile_user_id=str(getattr(item, "profile_user_id", "") or ""),
            source_event_id=str(getattr(item, "source_event_id", "") or ""),
            reason=str(getattr(exc, "reason", "") or exc.__class__.__name__),
        )

    if isinstance(session_work_queue, DurableSessionWorkQueue):
        session_work_queue.register_handler(
            "desktop_pet",
            _handle_queued_desktop_work,
            on_error=_desktop_session_work_error,
            batch_key=completion_batch_key,
        )

    async def _control_payload(request: Request) -> dict[str, Any] | JSONResponse:
        try:
            payload = await request.json()
        except Exception as exc:
            return JSONResponse(
                build_desktop_pet_error_payload(
                    error="invalid_json",
                    message=f"无法读取控制请求：{str(exc)[:160]}",
                    retryable=False,
                ),
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )
        if not isinstance(payload, dict):
            return JSONResponse(
                build_desktop_pet_error_payload(
                    error="invalid_payload",
                    message="turn control payload must be a JSON object",
                    retryable=False,
                ),
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )
        return payload

    @router.post("/think/steer")
    async def steer_turn(request: Request):
        payload = await _control_payload(request)
        if isinstance(payload, JSONResponse):
            return payload
        profile_user_id, session_id, actor_id = _turn_identity(payload)
        if not isinstance(session_work_queue, DurableSessionWorkQueue) or not turn_coordinator.is_busy(
            profile_user_id,
            session_id,
        ):
            result = turn_coordinator.offer_steer(
                profile_user_id=profile_user_id,
                session_id=session_id,
                actor_id=actor_id,
                content=payload.get("message"),
                timestamp=int(payload.get("timestamp") or time.time()),
                actor_display_name=payload.get("actor_display_name"),
                channel="desktop_pet",
                current_attachment_ids=payload.get("current_attachment_ids"),
            )
            status_code = 202 if result.get("ok") else (409 if result.get("status") == "finalizing" else 404)
            return JSONResponse(result, status_code=status_code, headers={"Cache-Control": "no-store"})

        reservation = await _reserve_desktop_turn(payload)
        if not reservation.get("ok"):
            return JSONResponse(reservation, status_code=409, headers={"Cache-Control": "no-store"})
        if reservation.get("status") in {"queued", "duplicate"}:
            return JSONResponse(reservation, status_code=202, headers={"Cache-Control": "no-store"})
        item_id = str(reservation.get("item_id") or "")
        result = turn_coordinator.offer_steer(
            profile_user_id=profile_user_id,
            session_id=session_id,
            actor_id=actor_id,
            content=payload.get("message"),
            timestamp=int(payload.get("timestamp") or time.time()),
            actor_display_name=payload.get("actor_display_name"),
            channel="desktop_pet",
            source_id=f"steer_{item_id}",
            receipt_item_id=item_id,
            receipt_claim_token=str(reservation.get("claim_token") or ""),
            current_attachment_ids=payload.get("current_attachment_ids"),
        )
        if result.get("ok"):
            return JSONResponse(result, status_code=202, headers={"Cache-Control": "no-store"})
        requeued = await session_work_queue.requeue_claim(
            item_id,
            claim_token=str(reservation.get("claim_token") or ""),
            error=str(result.get("reason") or "steer_not_accepted"),
        )
        if not requeued.get("ok"):
            return JSONResponse(requeued, status_code=409, headers={"Cache-Control": "no-store"})
        await session_work_queue.schedule_session(str(reservation.get("session_key") or ""))
        return JSONResponse(
            {"ok": True, "status": "queued", "reason": "steer_not_accepted_requeued"},
            status_code=202,
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/think/stop")
    async def stop_turn(request: Request):
        payload = await _control_payload(request)
        if isinstance(payload, JSONResponse):
            return payload
        profile_user_id, session_id, actor_id = _turn_identity(payload)
        result = turn_coordinator.request_stop(
            profile_user_id=profile_user_id,
            session_id=session_id,
            actor_id=actor_id,
        )
        status_code = 202 if result.get("ok") else (409 if result.get("status") == "finalizing" else 404)
        return JSONResponse(result, status_code=status_code, headers={"Cache-Control": "no-store"})

    @router.post("/think")
    async def think(request: Request):
        try:
            payload = await request.json()
        except Exception as exc:
            return JSONResponse(
                build_desktop_pet_error_payload(
                    error="invalid_json",
                    message=f"无法读取 /think 请求：{str(exc)[:160]}",
                    retryable=False,
                ),
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )
        if not isinstance(payload, dict):
            return JSONResponse(
                build_desktop_pet_error_payload(
                    error="invalid_payload",
                    message="/think payload must be a JSON object",
                    retryable=False,
                ),
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )
        guard_decision = public_guard.try_acquire()
        if not guard_decision.allowed:
            runtime_metrics.incr("public_guard_blocked_total", 1)
            runtime_metrics.incr(f"public_guard_blocked_{guard_decision.reason}_total", 1)
            log_event(
                "public_guard_blocked",
                route="think",
                reason=guard_decision.reason,
                message=guard_decision.message,
                session_id=str(payload.get("user_id") or ""),
            )
            raise HTTPException(status_code=429, detail=guard_decision.message)

        reservation = await _reserve_desktop_turn(payload)
        if not reservation.get("ok"):
            if guard_decision.acquired:
                public_guard.release()
            return JSONResponse(reservation, status_code=409, headers={"Cache-Control": "no-store"})
        if reservation.get("status") in {"queued", "duplicate"}:
            if guard_decision.acquired:
                public_guard.release()
            return JSONResponse(
                {
                    "type": "turn_queued",
                    "contract_version": DESKTOP_PET_CONTRACT_VERSION,
                    "status": str(reservation.get("status") or "queued"),
                    "reason": str(reservation.get("reason") or "session_inbox_waiting"),
                },
                status_code=202,
                headers={"Cache-Control": "no-store", "X-Akane-Contract": DESKTOP_PET_CONTRACT_VERSION},
            )

        def _advance_stream(stream: Any) -> tuple[bool, Any]:
            try:
                return True, next(stream)
            except StopIteration:
                return False, None

        stream_outcome = {"ok": False, "disconnected": False}

        async def _stream_active(inbound, turn_control_id):
            started_at = time.perf_counter()
            partial = {
                "emotion": "",
                "speech": "",
                "event_count": 0,
            }
            turn_stream = None
            ok = False
            disconnected = False
            final_frame = None
            yield json.dumps(
                {
                    "type": "stream_start",
                    "contract_version": DESKTOP_PET_CONTRACT_VERSION,
                },
                ensure_ascii=False,
            ) + "\n"
            with _inbound_processing(inbound, turn_control_id):
                try:
                    # Keep an explicit handle so a client-side abort closes the
                    # engine stream in the same response cleanup path.  Relying on
                    # generator garbage collection can inject GeneratorExit into
                    # the inner stream from a different context, leaving an open
                    # MemCore turn behind for the next desktop-pet message.
                    turn_stream = engine.process_turn_stream(payload)
                    while True:
                        has_event, event = await asyncio.to_thread(_advance_stream, turn_stream)
                        if not has_event:
                            break
                        partial["event_count"] = int(partial.get("event_count", 0)) + 1
                        event_type = str(event.get("type") or "")
                        if event_type == "ui":
                            partial["emotion"] = str(event.get("emotion") or partial.get("emotion") or "")
                        elif event_type == "speech_chunk":
                            partial["speech"] = str(partial.get("speech") or "") + str(event.get("text") or "")
                        if str(event.get("type") or "") == "final":
                            final_payload = event.get("payload")
                            if isinstance(final_payload, dict):
                                final_frame = final_payload
                                print_debug(payload, final_payload)
                                log_turn_result(
                                    "think",
                                    payload,
                                    final_payload,
                                    (time.perf_counter() - started_at) * 1000,
                                    log_event=log_event,
                                )
                                partial["emotion"] = str(final_payload.get("emotion") or partial.get("emotion") or "")
                                partial["speech"] = str(final_payload.get("speech") or partial.get("speech") or "")
                        yield json.dumps(event, ensure_ascii=False) + "\n"
                    ok = True
                    stream_outcome["ok"] = True
                except (GeneratorExit, asyncio.CancelledError):
                    disconnected = True
                    stream_outcome["disconnected"] = True
                    raise
                except Exception as exc:
                    yield json.dumps(
                        {
                            "type": "stream_error",
                            "contract_version": DESKTOP_PET_CONTRACT_VERSION,
                            "error": "think_stream_failed",
                            "message": f"stream failed: {exc}",
                            "retryable": True,
                            "partial": partial,
                        },
                        ensure_ascii=False,
                    ) + "\n"
                    log_event("think_stream_error", session_id=str(payload.get("user_id") or ""), message=str(exc), partial=partial)
                finally:
                    close_stream = getattr(turn_stream, "close", None)
                    if callable(close_stream):
                        try:
                            close_stream()
                        except Exception as exc:
                            logger.warning("think stream cleanup failed: %s", type(exc).__name__)
                    _finish_inbound(inbound, frame=final_frame,
                        delivery_status="disconnected" if disconnected else "streamed" if ok else "failed",
                        reason="client_disconnected" if disconnected else "" if ok and final_frame is not None else "think_stream_incomplete")
                    duration_ms = (time.perf_counter() - started_at) * 1000
                    runtime_metrics.observe_request("think_stream", duration_ms=duration_ms, ok=ok)
                    if not disconnected:
                        yield json.dumps(
                            {
                                "type": "stream_end",
                                "contract_version": DESKTOP_PET_CONTRACT_VERSION,
                                "status": "ok" if ok else "error",
                                "partial": partial,
                            },
                            ensure_ascii=False,
                        ) + "\n"

        async def _stream():
            with _inbound_scope(payload) as inbound:
                try:
                    await _publish_plugin_direct_event(request, payload)
                    profile_user_id, session_id, actor_id = _turn_identity(payload)
                    async with turn_coordinator.hold(
                        profile_user_id, session_id, actor_id=actor_id, channel="desktop_pet",
                    ) as turn_control_id:
                        payload["_turn_control_id"] = turn_control_id
                        active_stream = _stream_active(inbound, turn_control_id)
                        try:
                            async for chunk in active_stream:
                                yield chunk
                        finally:
                            await active_stream.aclose()
                finally:
                    # One cleanup owner, including cancellation while waiting
                    # for the normal session lock or at stream_start.
                    await _settle_desktop_turn(reservation, completed=stream_outcome["ok"],
                        reason="client_disconnected" if stream_outcome["disconnected"] else "think_stream_failed")
                    if guard_decision.acquired:
                        public_guard.release()

        return StreamingResponse(
            _stream(),
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-store",
                "X-Akane-Contract": DESKTOP_PET_CONTRACT_VERSION,
                # Tell nginx not to buffer streamed NDJSON chunks; otherwise UI
                # state like BGM / choices may appear only after a refresh.
                "X-Accel-Buffering": "no",
            },
        )

    @router.post("/think_once")
    async def think_once(request: Request):
        started_at = time.perf_counter()
        try:
            payload = await request.json()
        except Exception as exc:
            runtime_metrics.observe_request("think_once", duration_ms=(time.perf_counter() - started_at) * 1000, ok=False)
            return JSONResponse(
                build_desktop_pet_error_payload(
                    error="invalid_json",
                    message=f"无法读取 /think_once 请求：{str(exc)[:160]}",
                    retryable=False,
                ),
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )
        if not isinstance(payload, dict):
            runtime_metrics.observe_request("think_once", duration_ms=(time.perf_counter() - started_at) * 1000, ok=False)
            return JSONResponse(
                build_desktop_pet_error_payload(
                    error="invalid_payload",
                    message="/think_once payload must be a JSON object",
                    retryable=False,
                ),
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )
        guard_decision = public_guard.try_acquire()
        if not guard_decision.allowed:
            runtime_metrics.incr("public_guard_blocked_total", 1)
            runtime_metrics.incr(f"public_guard_blocked_{guard_decision.reason}_total", 1)
            log_event(
                "public_guard_blocked",
                route="think_once",
                reason=guard_decision.reason,
                message=guard_decision.message,
                session_id=str(payload.get("user_id") or ""),
            )
            raise HTTPException(status_code=429, detail=guard_decision.message)

        reservation = await _reserve_desktop_turn(payload)
        if not reservation.get("ok"):
            if guard_decision.acquired:
                public_guard.release()
            runtime_metrics.observe_request(
                "think_once",
                duration_ms=(time.perf_counter() - started_at) * 1000,
                ok=False,
            )
            return JSONResponse(reservation, status_code=409, headers={"Cache-Control": "no-store"})
        if reservation.get("status") in {"queued", "duplicate"}:
            if guard_decision.acquired:
                public_guard.release()
            runtime_metrics.observe_request(
                "think_once",
                duration_ms=(time.perf_counter() - started_at) * 1000,
                ok=True,
            )
            return JSONResponse(
                {
                    "type": "turn_queued",
                    "contract_version": DESKTOP_PET_CONTRACT_VERSION,
                    "status": str(reservation.get("status") or "queued"),
                    "reason": str(reservation.get("reason") or "session_inbox_waiting"),
                },
                status_code=202,
                headers={"Cache-Control": "no-store", "X-Akane-Contract": DESKTOP_PET_CONTRACT_VERSION},
            )
        with _inbound_scope(payload) as inbound:
            try:
                await _publish_plugin_direct_event(request, payload)
            except asyncio.CancelledError:
                await _settle_desktop_turn(reservation, completed=False, reason="request_cancelled")
                if guard_decision.acquired:
                    public_guard.release()
                raise
            except Exception:
                await _settle_desktop_turn(reservation, completed=False, reason="plugin_event_dispatch_failed")
                if guard_decision.acquired:
                    public_guard.release()
                raise
            profile_user_id, session_id, actor_id = _turn_identity(payload)
            completed = False
            try:
                async with turn_coordinator.hold(
                    profile_user_id,
                    session_id,
                    actor_id=actor_id,
                    channel="desktop_pet",
                ) as turn_control_id:
                    payload["_turn_control_id"] = turn_control_id
                    with _inbound_processing(inbound, turn_control_id):
                        frame = await asyncio.to_thread(engine.process_turn, payload)
                completed = isinstance(frame, dict) and not bool(frame.get("_transient_final_failure"))
            except Exception as exc:
                duration_ms = (time.perf_counter() - started_at) * 1000
                runtime_metrics.observe_request("think_once", duration_ms=duration_ms, ok=False)
                log_event("think_once_error", session_id=str(payload.get("user_id") or ""), message=str(exc))
                raise
            finally:
                await _settle_desktop_turn(
                    reservation,
                    completed=completed,
                    reason="think_once_incomplete",
                )
                if guard_decision.acquired:
                    public_guard.release()
            print_debug(payload, frame)
            duration_ms = (time.perf_counter() - started_at) * 1000
            runtime_metrics.observe_request("think_once", duration_ms=duration_ms, ok=True)
            log_turn_result("think_once", payload, frame, duration_ms, log_event=log_event)
            try:
                response = JSONResponse(_desktop_tool_completion_frame(frame))
            except Exception:
                _finish_inbound(inbound, frame=frame, delivery_status="failed", reason="desktop_response_encoding_failed")
                raise
            _finish_inbound(inbound, frame=frame, delivery_status="response_ready")
            return response

    return router


def print_debug(payload: dict, frame: dict) -> None:
    # Only emit verbose debug output when at least one debug flag is enabled.
    # Without this gate, every /think and /think_once call dumps large JSON
    # blocks to stdout, making logs unreadable in normal operation.
    router_debug = bool(getattr(config, "ROUTER_DEBUG", False))
    verifier_debug = bool(getattr(config, "VERIFIER_DEBUG", False))
    final_debug = bool(getattr(config, "FINAL_DEBUG", False))
    if not (router_debug or verifier_debug or final_debug):
        return

    debug = frame.get("_debug") or {}
    retrieval_debug = debug.get("retrieval_result") or {}
    memory_snippets = list(retrieval_debug.get("memory_snippets") or [])
    selected_memory_snippets = list(retrieval_debug.get("selected_memory_snippets") or [])

    out: list[str] = []
    out.append("")
    out.append("=" * 18 + " Aihong Companion V0.1 " + "=" * 18)
    out.append(f"session_id: {payload.get('user_id', '')}")
    out.append(f"user_text: {payload.get('message', '')}")
    out.append("")
    out.append("=== 前置检索控制输出 ===")
    out.append(json.dumps(debug.get("router_output", {}), ensure_ascii=False, indent=2))
    out.append("")
    out.append("=== 前置检索控制耗时 ===")
    out.append(json.dumps(debug.get("router_timing", {}), ensure_ascii=False, indent=2))
    out.append("")
    out.append("=== 检索结果摘要 ===")
    out.append(json.dumps(debug.get("retrieval_result", {}), ensure_ascii=False, indent=2))
    out.append("")
    out.append("=== 检索校验输出 ===")
    out.append(json.dumps(debug.get("verifier_output", {}), ensure_ascii=False, indent=2))
    out.append("")
    out.append("=== 检索校验耗时 ===")
    out.append(json.dumps(debug.get("verifier_timing", {}), ensure_ascii=False, indent=2))
    out.append("")
    if debug.get("memory_tool"):
        out.append("=== Akane 主动记忆检索工具 ===")
        out.append(json.dumps(debug.get("memory_tool", {}), ensure_ascii=False, indent=2))
        out.append("")
    out.append("=== 检索片段（按编号） ===")
    if memory_snippets:
        for index, snippet in enumerate(memory_snippets, start=1):
            out.append(f"[{index}]")
            out.append(str(snippet))
            out.append("")
    else:
        out.append("(无)")
    out.append("=== 被选中的记忆片段 ===")
    if selected_memory_snippets:
        for item in selected_memory_snippets:
            label = f"[{item.get('index')}]" if item.get("index") is not None else "[fallback]"
            out.append(label)
            out.append(str(item.get("snippet") or ""))
            out.append("")
    else:
        out.append("(无)")
    out.append("")
    out.append("=== 最终回复 JSON ===")
    visible: dict[str, object] = {}
    for key in (
        "thought",
        "emotion",
        "speech",
        "speech_segments",
        "tool_call",
        "code_snippet",
        "status",
        "choices",
        "npc_turns",
        "client_mode",
        "client",
        "character",
        "scene",
        "persona",
        "memory_metadata",
    ):
        if key == "thought" and key not in frame:
            continue
        visible[key] = frame.get(key)
    out.append(json.dumps(visible, ensure_ascii=False, indent=2))
    out.append("=" * 48)

    logger.info("\n".join(out))


def log_turn_result(
    route: str,
    payload: dict,
    frame: dict,
    duration_ms: float,
    *,
    log_event: LogEvent,
) -> None:
    debug = frame.get("_debug") or {}
    log_event(
        "turn_complete",
        route=route,
        session_id=str(payload.get("user_id") or ""),
        trace_id=str(frame.get("trace_id") or ""),
        duration_ms=round(float(duration_ms), 1),
        router_ready_at_ms=debug.get("router_timing", {}).get("ready_at_ms"),
        verifier_mode=debug.get("verifier_timing", {}).get("mode"),
        final_status=str(frame.get("status") or ""),
        emotion=str(frame.get("emotion") or ""),
    )
