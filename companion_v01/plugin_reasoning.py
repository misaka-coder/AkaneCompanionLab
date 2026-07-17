"""Host-owned bounded model/tool reasoning surface for trusted plugins."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from typing import Any

from .plugin_api import PluginReasoningRequest, PluginReasoningResult


MAX_REASONING_MESSAGE_CHARS = 12_000
MAX_REASONING_CONTEXT_CHARS = 24_000
MAX_REASONING_OUTPUT_CHARS = 6_000
MAX_REASONING_EVIDENCE_EVENTS = 24
DEFAULT_REASONING_TIMEOUT_SECONDS = 120.0
_EVIDENCE_FIELDS = (
    "type",
    "status",
    "tool",
    "tool_name",
    "capability_id",
    "capabilityId",
    "provider",
    "source",
    "as_of",
    "reason",
)


class EnginePluginReasoningPort:
    """Translate a public request into one normal Akane proactive turn."""

    def __init__(self, engine: Any, *, timeout_seconds: float = DEFAULT_REASONING_TIMEOUT_SECONDS) -> None:
        if not callable(getattr(engine, "process_turn", None)):
            raise TypeError("invalid_reasoning_engine")
        self._engine = engine
        self._timeout_seconds = max(1.0, min(300.0, float(timeout_seconds)))

    async def analyze(self, request: PluginReasoningRequest) -> PluginReasoningResult:
        error = _validate_request(request)
        if error:
            return PluginReasoningResult(ok=False, status="invalid_request", reason=error)
        payload = {
            "user_id": request.session_id,
            "real_user_id": request.profile_user_id,
            "message": request.message.strip(),
            "timestamp": max(1, int(request.timestamp or time.time())),
            "trace_id": request.trace_id.strip(),
            "client_mode": "qq_text",
            "client_capabilities": ["speech_segments", "tool_actions"],
            "turn_kind": "plugin_proactive",
            "client_turn_kind": "proactive",
            "transient_user_message": True,
            "transient_assistant_message": True,
            # Proactive plugin requests already carry their bounded task
            # context.  Keep ordinary memory available through explicit tools,
            # but do not run the conversational pre-retrieval pipeline before
            # the plugin has asked for it.
            "pre_retrieval_enabled": False,
            "extra_context": request.extra_context.strip(),
        }
        if request.character_pack_id.strip():
            payload["character_pack_id"] = request.character_pack_id.strip()
        try:
            frame = await asyncio.wait_for(
                asyncio.to_thread(self._engine.process_turn, payload),
                timeout=self._timeout_seconds,
            )
        except (asyncio.TimeoutError, TimeoutError):
            return PluginReasoningResult(ok=False, status="timeout", reason="reasoning_timeout")
        except asyncio.CancelledError:
            raise
        except Exception:
            return PluginReasoningResult(ok=False, status="failed", reason="reasoning_failed")
        if not isinstance(frame, Mapping):
            return PluginReasoningResult(ok=False, status="failed", reason="invalid_reasoning_result")
        text = _frame_text(frame)
        if not text:
            return PluginReasoningResult(ok=False, status="failed", reason="empty_reasoning_result")
        return PluginReasoningResult(
            ok=True,
            status="completed",
            text=text[:MAX_REASONING_OUTPUT_CHARS],
            evidence_events=_project_evidence_events(frame.get("tool_events")),
        )


class PluginScopedReasoningPort:
    """Reject new work once PluginHost leaves its available lifecycle states."""

    def __init__(
        self,
        *,
        delegate: Any,
        availability_provider: Callable[[], bool],
    ) -> None:
        self._delegate = delegate
        self._availability_provider = availability_provider

    async def analyze(self, request: PluginReasoningRequest) -> PluginReasoningResult:
        try:
            available = bool(self._availability_provider())
        except Exception:
            available = False
        if not available:
            return PluginReasoningResult(
                ok=False,
                status="host_unavailable",
                reason="host_unavailable",
            )
        try:
            result = await self._delegate.analyze(request)
        except asyncio.CancelledError:
            raise
        except Exception:
            return PluginReasoningResult(ok=False, status="failed", reason="reasoning_port_failed")
        if not isinstance(result, PluginReasoningResult):
            return PluginReasoningResult(ok=False, status="failed", reason="invalid_reasoning_result")
        return result


def _validate_request(request: object) -> str:
    if not isinstance(request, PluginReasoningRequest):
        return "invalid_reasoning_request"
    fields = {
        "trace_id": (request.trace_id, 160),
        "profile_user_id": (request.profile_user_id, 200),
        "session_id": (request.session_id, 200),
        "message": (request.message, MAX_REASONING_MESSAGE_CHARS),
        "extra_context": (request.extra_context, MAX_REASONING_CONTEXT_CHARS),
        "character_pack_id": (request.character_pack_id, 120),
    }
    for name, (raw, maximum) in fields.items():
        if not isinstance(raw, str) or len(raw) > maximum or "\x00" in raw:
            return f"invalid_{name}"
    if not request.trace_id.strip() or not request.profile_user_id.strip() or not request.session_id.strip():
        return "reasoning_identity_required"
    if not request.message.strip():
        return "reasoning_message_required"
    return ""


def _frame_text(frame: Mapping[str, Any]) -> str:
    speech = str(frame.get("speech") or "").strip()
    if speech:
        return speech
    segments = frame.get("speech_segments")
    if not isinstance(segments, (list, tuple)):
        return ""
    return "\n".join(str(item or "").strip() for item in segments if str(item or "").strip())


def _project_evidence_events(raw_events: object) -> tuple[dict[str, Any], ...]:
    if not isinstance(raw_events, (list, tuple)):
        return ()
    projected: list[dict[str, Any]] = []
    for raw in raw_events[:MAX_REASONING_EVIDENCE_EVENTS]:
        if not isinstance(raw, Mapping):
            continue
        item: dict[str, Any] = {}
        for key in _EVIDENCE_FIELDS:
            value = raw.get(key)
            if isinstance(value, (str, int, float, bool)):
                text = str(value).strip()
                if text:
                    item[key] = text[:240]
        if item:
            projected.append(item)
    return tuple(projected)


__all__ = [
    "EnginePluginReasoningPort",
    "PluginScopedReasoningPort",
]
