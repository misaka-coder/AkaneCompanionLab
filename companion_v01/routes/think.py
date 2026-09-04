from __future__ import annotations

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
from ..durable_session_queue import DurableSessionWorkQueue, RetryableSessionWorkError
from ..session_inbox import SessionInboxItem
from ..plugin_api import (
    DIRECT_CONVERSATION_EVENT,
    PluginAgentEventRequest,
    PluginAgentEventResult,
    PluginEventEnvelope,
    PluginExternalEvent,
)
from ..plugin_events import record_timeline_events, render_current_turn_events
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
    request: PluginAgentEventRequest,
) -> dict[str, Any]:
    """Keep the ordinary desktop output contract while excluding internals."""

    frame = apply_plugin_text_presentation_policy(
        dict(frame),
        strip_leading_addresses_from=request.text_strip_leading_addresses,
    )
    presentation = _desktop_frame_presentation(frame)
    if request.text_delivery != "single_message":
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
            str(request.text_prefix or "").strip(),
            speech,
            str(request.text_suffix or "").strip(),
        )
        if part
    )
    presentation["speech"] = completed
    presentation["speech_segments"] = [completed]
    return presentation


def _desktop_frame_presentation(frame: dict[str, Any]) -> dict[str, Any]:
    return {
        key: frame[key]
        for key in _DESKTOP_AGENT_EVENT_PRESENTATION_FIELDS
        if key in frame
    }


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
) -> APIRouter:
    router = APIRouter()
    turn_coordinator = turn_coordinator or TurnCoordinator()

    def _turn_identity(payload: dict[str, Any]) -> tuple[str, str, str]:
        session_id = str(payload.get("user_id") or payload.get("session_id") or "default_session")
        profile_user_id = str(payload.get("real_user_id") or session_id)
        actor_id = str(payload.get("actor_stable_id") or f"desktop:{profile_user_id}").strip()
        return profile_user_id, session_id, actor_id

    async def _dispatch_plugin_direct_event(request: Request | None, payload: dict[str, Any]) -> str:
        """Observe one desktop direct message without changing its memory record."""

        profile_user_id, session_id, actor_id = _turn_identity(payload)
        try:
            broker = (
                plugin_event_broker_provider()
                if plugin_event_broker_provider is not None
                else getattr(request.app.state, "akane_plugin_event_broker", None)
                if request is not None
                else None
            )
            if broker is None or not broker.observes(DIRECT_CONVERSATION_EVENT):
                return ""
        except asyncio.CancelledError:
            raise
        except Exception:
            log_event(
                "desktop_plugin_event_degraded",
                session_id=session_id,
                profile_user_id=profile_user_id,
                broker_status="broker_unavailable",
                handler_failure_count=1,
                timeline_failure_count=0,
            )
            return ""

        occurred_at = _positive_int(payload.get("timestamp"), default=int(time.time()))
        event_id = str(
            payload.get("source_message_id")
            or payload.get("turn_id")
            or payload.get("turnId")
            or f"desktop:{occurred_at}:{uuid.uuid4().hex}"
        ).strip()
        envelope = PluginEventEnvelope(
            event_id=event_id,
            event_type=DIRECT_CONVERSATION_EVENT,
            source="desktop_pet",
            occurred_at=occurred_at,
            subject=session_id,
            fields=tuple(
                (key, value)
                for key, value in (
                    ("conversation_kind", "direct"),
                    ("conversation_id", session_id),
                    ("actor_id", actor_id),
                    ("actor_display_name", str(payload.get("actor_display_name") or "").strip()),
                    ("character_pack_id", str(payload.get("character_pack_id") or "").strip()),
                    ("client_mode", str(payload.get("client_mode") or "desktop_pet").strip()),
                    ("text", str(payload.get("message") or "")),
                )
                if value
            ),
        )
        try:
            dispatch = await broker.dispatch(envelope)
        except asyncio.CancelledError:
            raise
        except Exception:
            log_event(
                "desktop_plugin_event_degraded",
                session_id=session_id,
                profile_user_id=profile_user_id,
                event_id=envelope.event_id,
                broker_status="dispatch_failed",
                handler_failure_count=1,
                timeline_failure_count=0,
            )
            return ""

        timeline_failures = await record_timeline_events(
            recorder=getattr(engine, "record_plugin_timeline_event", None),
            envelope=envelope,
            events=dispatch.timeline_events,
            user_id=session_id,
            real_user_id=profile_user_id,
            character_pack_id=str(payload.get("character_pack_id") or ""),
        )
        log_event(
            "desktop_plugin_event_degraded" if dispatch.failures or timeline_failures else "desktop_plugin_event_observed",
            session_id=session_id,
            profile_user_id=profile_user_id,
            event_id=envelope.event_id,
            broker_status=dispatch.status,
            handler_failure_count=len(dispatch.failures),
            timeline_failure_count=len(timeline_failures),
            current_turn_count=len(dispatch.current_turn_events),
            timeline_count=len(dispatch.timeline_events),
            request_agent_turn=bool(dispatch.request_agent_turn),
        )
        return render_current_turn_events(dispatch.current_turn_events)

    def _positive_int(value: Any, *, default: int) -> int:
        try:
            resolved = int(value or 0)
        except (TypeError, ValueError):
            resolved = 0
        return resolved if resolved > 0 else int(default)

    def _append_current_turn_context(payload: dict[str, Any], note: str) -> None:
        if not note:
            return
        current = str(payload.get("extra_context") or "").strip()
        payload["extra_context"] = "\n\n".join(part for part in (current, note) if part)

    def _desktop_delivery_is_available() -> bool:
        if desktop_agent_frame_delivery is None:
            return False
        try:
            return desktop_agent_event_available is None or bool(desktop_agent_event_available())
        except Exception:
            return False

    async def _deliver_desktop_frame(frame: dict[str, Any]) -> dict[str, Any]:
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
        event_request: PluginAgentEventRequest,
        resolved_reference: dict[str, str],
    ) -> PluginAgentEventResult:
        """Run a plugin event through the ordinary desktop Agent path."""

        if not isinstance(event_request, PluginAgentEventRequest) or not isinstance(
            event_request.event, PluginExternalEvent
        ):
            return PluginAgentEventResult(False, "rejected", "invalid_agent_event_request")
        if str(resolved_reference.get("channel") or "") != "desktop_pet":
            return PluginAgentEventResult(False, "rejected", "event_context_unresolved")
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
            return PluginAgentEventResult(False, "rejected", "event_context_unresolved")
        if not _desktop_delivery_is_available():
            return PluginAgentEventResult(False, "host_unavailable", "desktop_client_unavailable")

        message = str(event_request.message or "").strip()
        timestamp = int(time.time())
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
            "transient_user_message": event_request.delivery == "current_turn",
            "plugin_external_event": {
                "event_type": str(event_request.event.event_type or "").strip().lower(),
                "source": str(event_request.event.source or "plugin").strip(),
                "fields": {
                    str(key): str(value)
                    for key, value in event_request.event.fields
                    if str(key or "").strip() and str(value or "").strip()
                },
            },
            "memory_idempotency_key": str(event_request.memory_idempotency_key or "").strip(),
            "plugin_text_strip_leading_addresses": list(
                event_request.text_strip_leading_addresses
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
            return PluginAgentEventResult(False, "failed", "agent_event_turn_failed", "not_sent")
        if not isinstance(frame, dict) or bool(frame.get("_transient_final_failure")):
            return PluginAgentEventResult(False, "failed", "agent_event_turn_incomplete", "not_sent")

        presentation = _desktop_agent_event_presentation(frame, event_request)
        try:
            delivered = desktop_agent_frame_delivery(presentation)
            if hasattr(delivered, "__await__"):
                delivered = await delivered
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("desktop plugin Agent frame delivery failed")
            return PluginAgentEventResult(False, "failed", "agent_event_delivery_failed", "not_sent")
        delivery_result = delivered if isinstance(delivered, dict) else {}
        if delivery_result.get("ok") is not True:
            return PluginAgentEventResult(
                False,
                "failed",
                str(delivery_result.get("reason") or "agent_event_delivery_failed"),
                str(delivery_result.get("status") or "not_sent"),
            )
        return PluginAgentEventResult(True, "completed", "", str(delivery_result.get("status") or "queued"))

    if plugin_agent_event_handler_registrar is not None:
        plugin_agent_event_handler_registrar("desktop_pet", _submit_plugin_agent_event)

    async def _handle_queued_desktop_work(_key: str, items: list[SessionInboxItem]) -> None:
        if len(items) != 1:
            raise ValueError("desktop_session_work_single_item_required")
        item = items[0]
        stored = item.payload if isinstance(item.payload, dict) else {}
        payload = dict(stored.get("turn_payload") or {})
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
        payload["memory_idempotency_key"] = f"session-inbox:{item.item_id}"
        _append_current_turn_context(payload, await _dispatch_plugin_direct_event(None, payload))
        async with turn_coordinator.hold(
            profile_user_id,
            session_id,
            actor_id=actor_id,
            channel="desktop_pet",
        ) as turn_control_id:
            payload["_turn_control_id"] = turn_control_id
            frame = await asyncio.to_thread(engine.process_turn, payload)
        if not isinstance(frame, dict) or bool(frame.get("_transient_final_failure")):
            raise RuntimeError("desktop_session_turn_incomplete")
        delivery_result = await _deliver_desktop_frame(frame)
        if delivery_result.get("ok") is not True:
            raise RuntimeError(str(delivery_result.get("reason") or "desktop_delivery_failed"))
        log_event(
            "desktop_session_work_completed",
            session_id=session_id,
            profile_user_id=profile_user_id,
            source_event_id=item.source_event_id,
            delivery_status=str(delivery_result.get("status") or "queued"),
        )

    def _desktop_session_work_error(
        _key: str,
        items: list[SessionInboxItem],
        exc: BaseException,
    ) -> None:
        item = items[0] if items else None
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

        try:
            _append_current_turn_context(payload, await _dispatch_plugin_direct_event(request, payload))
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

        def _advance_stream(stream: Any) -> tuple[bool, Any]:
            try:
                return True, next(stream)
            except StopIteration:
                return False, None

        async def _stream_active():
            started_at = time.perf_counter()
            partial = {
                "emotion": "",
                "speech": "",
                "event_count": 0,
            }
            turn_stream = None
            ok = False
            disconnected = False
            yield json.dumps(
                {
                    "type": "stream_start",
                    "contract_version": DESKTOP_PET_CONTRACT_VERSION,
                },
                ensure_ascii=False,
            ) + "\n"
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
            except (GeneratorExit, asyncio.CancelledError):
                disconnected = True
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
                await _settle_desktop_turn(
                    reservation,
                    completed=ok,
                    reason="client_disconnected" if disconnected else "think_stream_failed",
                )
                if guard_decision.acquired:
                    public_guard.release()
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
            profile_user_id, session_id, actor_id = _turn_identity(payload)
            async with turn_coordinator.hold(
                profile_user_id,
                session_id,
                actor_id=actor_id,
                channel="desktop_pet",
            ) as turn_control_id:
                payload["_turn_control_id"] = turn_control_id
                active_stream = _stream_active()
                try:
                    async for chunk in active_stream:
                        yield chunk
                finally:
                    await active_stream.aclose()

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
        try:
            _append_current_turn_context(payload, await _dispatch_plugin_direct_event(request, payload))
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
        return JSONResponse(frame)

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
