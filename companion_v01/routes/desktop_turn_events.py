"""Relay background desktop turns through the existing event and satellite paths.

This is a transport envelope, not an extra model output contract. Final frames
still pass through the ordinary presentation policy and delivery receipt.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextlib import aclosing
import logging
from typing import Any, Callable
import uuid

from ..turn_coordination import cancellation_requested
from .think_stream import iterate_turn_events

logger = logging.getLogger("akane.think")

# Do not forward provider/debug/memory bodies as UI progress. These are the
# public fields already consumed by processThinkStream on the desktop.
_EVENT_FIELDS = {
    "stream_start": ("phase",),
    "turn_start": (),
    "turn_progress": ("phase", "elapsed_ms", "attempt", "transport"),
    "ui": ("emotion",),
    "speech_mode": ("speech_protocol", "emotion"),
    "speech_chunk": ("text",),
    "speech_segment": ("index", "text"),
    "speech_unit_text": ("unit_index", "text", "emotion"),
    "voice_segment": ("unit_index", "index", "text", "say", "style", "display_text", "emotion"),
    "voice_unit_end": ("unit_index", "display_text", "has_voice", "emotion"),
    "speech_warning": ("code", "unit_index"),
    "speech_reset": ("speech",),
    "assistant_stage_decision": ("has_tool_call", "tool_type", "tool_types", "tool_count", "rejected_tool_call"),
    "assistant_working": ("phase", "tool_type", "tool_types", "tool_count", "message"),
    "turn_steer_applied": (),
    "turn_steer_failed": (),
    "turn_stopped": (),
    "stream_error": ("error", "message", "retryable"),
    "stream_end": ("status",),
}


class DesktopTurnEventRelay:
    def __init__(self, payload: Mapping[str, Any], deliver: Callable | None, *, phase: str = "received",
                 suppress_text: bool = False, defer_start: bool = False) -> None:
        self.deliver = deliver
        self.stream_id = uuid.uuid4().hex
        self.sequence = 0
        self.identity = {
            "session_id": str(payload.get("user_id") or payload.get("session_id") or "default_session"),
            "profile_user_id": str(payload.get("real_user_id") or payload.get("user_id") or "default_session"),
            "character_pack_id": str(payload.get("character_pack_id") or ""),
        }
        self.started = False
        self.closed = False
        self.phase = phase
        self.suppress_text = suppress_text
        self.defer_start = defer_start
        self.pending_end = None

    async def __aenter__(self) -> DesktopTurnEventRelay:
        if not self.defer_start:
            await self.start(self.phase)
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        failed = exc is not None and not isinstance(exc, asyncio.CancelledError)
        stopped = isinstance(exc, asyncio.CancelledError) or cancellation_requested()
        if self.pending_end is not None:
            failed = failed or self.pending_end[0]
            stopped = stopped or self.pending_end[1]
        await self.end(failed=failed, stopped=stopped)

    def envelope(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        sequence, self.sequence = self.sequence, self.sequence + 1
        return {"_desktop_turn_event": {
            "stream_id": self.stream_id, "sequence": sequence,
            **self.identity, "events": events,
        }}

    async def emit(self, *events: dict[str, Any]) -> None:
        if self.deliver is None or self.closed or cancellation_requested():
            return
        projected = []
        for event in events:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("type") or "")
            if event_type not in _EVENT_FIELDS:
                continue
            if self.suppress_text and (event_type.startswith(("speech_", "voice_")) or event_type == "ui"):
                continue
            projected.append({"type": event_type, **{
                key: event[key] for key in _EVENT_FIELDS[event_type] if key in event
            }})
        if not projected:
            return
        result = self.deliver(self.envelope(projected))
        if hasattr(result, "__await__"):
            result = await result
        if not isinstance(result, dict) or result.get("ok") is not True:
            # A failed progress receipt cannot stand in for a successful final
            # delivery. Stop before advancing or executing the next tool step.
            raise RuntimeError("desktop_progress_delivery_failed")

    async def start(self, phase: str = "received") -> None:
        if self.deliver is not None and not self.started:
            await self.emit({"type": "stream_start", "phase": phase})
            self.started = True

    def final_presentation(self, presentation: dict[str, Any]) -> dict[str, Any]:
        if self.deliver is None or not self.started:
            return presentation
        self.closed = True
        return self.envelope([
            {"type": "final", "payload": presentation},
            {"type": "stream_end", "status": "ok"},
        ])

    async def end(self, *, failed: bool = False, stopped: bool = False) -> None:
        if self.closed:
            return
        if not self.started:
            self.closed = True
            return
        if self.deliver is not None and cancellation_requested():
            # No text/effects can cross a revoked scope. The relay owner may
            # send this control-only terminal after that scope has unwound;
            # the UI's stream identity still rejects stale/newer-scene work.
            self.pending_end = (failed, stopped)
            return
        events = []
        if stopped:
            events.append({"type": "turn_stopped"})
        elif failed:
            events.append({"type": "stream_error", "error": "desktop_session_turn_incomplete",
                           "message": "后台任务未能完成。", "retryable": True})
        events.append({"type": "stream_end", "status": "error" if failed else "stopped" if stopped else "ok"})
        try:
            await self.emit(*events)
        except Exception:
            # Keep the original model/tool/cancellation failure authoritative.
            logger.warning("desktop progress terminal delivery failed")
        finally:
            self.closed = True


async def process_background_desktop_turn(engine: Any, payload: dict[str, Any],
                                          relay: DesktopTurnEventRelay) -> dict[str, Any]:
    relay.suppress_text = relay.suppress_text or payload.get("plugin_text_delivery") == "single_message"
    streaming = getattr(engine, "process_turn_stream", None)
    if not callable(streaming) or (relay.deliver is None and callable(getattr(engine, "process_turn", None))):
        # Older embedded clients have no event transport; retain their existing
        # final-frame-only path. Production routers bind the relay explicitly.
        return await asyncio.to_thread(engine.process_turn, payload)
    await relay.start()
    frame = None
    try:
        async with aclosing(iterate_turn_events(lambda: streaming(payload))) as events:
            async for event in events:
                if event.get("type") in {"final", "final_ui"}:
                    frame = event.get("payload")
                    # Model completion and presentation revocation are separate
                    # facts. The delivery owner still checks the live scope.
                    continue
                if cancellation_requested():
                    frame = {"status": "stopped", "speech": ""}
                    break
                # Tool effects are delivered by the normal final frame, once,
                # rather than repeated through progress envelopes.
                await relay.emit(event)
        if isinstance(frame, dict) and frame.get("status") != "stopped" and not frame.get("_transient_final_failure"):
            return frame
        if cancellation_requested() or (isinstance(frame, dict) and frame.get("status") == "stopped"):
            await relay.end(stopped=True)
            return {"status": "stopped", "speech": ""}
        if not isinstance(frame, dict) or frame.get("_transient_final_failure"):
            raise RuntimeError("desktop_session_turn_incomplete")
        return frame
    except BaseException as exc:
        await relay.end(failed=not isinstance(exc, asyncio.CancelledError),
                        stopped=isinstance(exc, asyncio.CancelledError))
        raise
