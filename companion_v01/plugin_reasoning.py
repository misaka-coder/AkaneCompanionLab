"""Host-owned bounded model/tool reasoning surface for trusted plugins."""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections.abc import Callable, Mapping
from typing import Any

from .plugin_api import PluginReasoningRequest, PluginReasoningResult


MAX_REASONING_MESSAGE_CHARS = 12_000
MAX_REASONING_CONTEXT_CHARS = 24_000
MAX_REASONING_STABLE_SYSTEM_CHARS = 12_000
MAX_REASONING_IDEMPOTENCY_KEY_CHARS = 240
MAX_REASONING_OUTPUT_CHARS = 6_000
MAX_REASONING_EVIDENCE_EVENTS = 24
MAX_REASONING_EVENT_FIELDS = 16
MAX_REASONING_EVENT_FIELD_CHARS = 4_000
MAX_REASONING_EVENT_TOTAL_CHARS = 12_000
DEFAULT_REASONING_TIMEOUT_SECONDS = 120.0
REASONING_RESULT_REUSE_SECONDS = 600.0
MAX_REASONING_RESULT_TASKS = 256
_PROACTIVE_MESSAGE_HEADER = "【当前待处理的插件主动事件（不是用户发言）】"
_PROACTIVE_RESPONSE_DIRECTIVE = "请按系统约定的 JSON 最终答复格式完成本次处理。"
_EVENT_TYPE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_EVENT_FIELD_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_RESERVED_EVENT_FIELDS = frozenset(
    {
        "api_key",
        "apikey",
        "access_token",
        "authorization",
        "cached_path",
        "event_type",
        "file_path",
        "password",
        "path",
        "secret",
        "source",
        "storage_relpath",
        "token",
    }
)
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
        self._task_lock = asyncio.Lock()
        self._idempotent_tasks: dict[str, tuple[asyncio.Task[Any], float]] = {}

    async def analyze(self, request: PluginReasoningRequest) -> PluginReasoningResult:
        error = _validate_request(request)
        if error:
            return PluginReasoningResult(ok=False, status="invalid_request", reason=error)
        external_event_payload: dict[str, Any] | None = None
        if request.external_event is not None:
            external_event_payload = _project_external_event(request.external_event)
            try:
                persistent_message = _render_external_event(external_event_payload)
            except Exception:
                return PluginReasoningResult(
                    ok=False,
                    status="unavailable",
                    reason="external_event_renderer_unavailable",
                )
        else:
            persistent_message = _render_persistent_proactive_message(request.message)
        if len(persistent_message) > MAX_REASONING_MESSAGE_CHARS:
            return PluginReasoningResult(ok=False, status="invalid_request", reason="invalid_message")
        payload = {
            "user_id": request.session_id,
            "real_user_id": request.profile_user_id,
            "message": persistent_message,
            "timestamp": max(1, int(request.timestamp or time.time())),
            "trace_id": request.trace_id.strip(),
            "client_mode": "qq_text",
            "client_capabilities": ["speech_segments", "tool_actions"],
            "turn_kind": "plugin_proactive",
            "client_turn_kind": "proactive",
            "extra_context": request.extra_context.strip(),
            "memory_idempotency_key": request.memory_idempotency_key.strip(),
        }
        if request.stable_system_context.strip():
            payload["plugin_stable_system_context"] = request.stable_system_context.strip()
        if external_event_payload is not None:
            payload["plugin_external_event"] = external_event_payload
        if request.character_pack_id.strip():
            payload["character_pack_id"] = request.character_pack_id.strip()
        task = await self._reasoning_task(request, payload)
        try:
            # Cancelling ``to_thread`` only cancels the waiter, not the engine
            # thread. Keep the real task alive so an idempotent retry joins the
            # same work instead of opening a second MemCore/tool turn.
            frame = await asyncio.wait_for(
                asyncio.shield(task),
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
        if bool(frame.get("_transient_final_failure")):
            return PluginReasoningResult(ok=False, status="failed", reason="incomplete_reasoning_result")
        text = _frame_text(frame)
        if not text:
            return PluginReasoningResult(ok=False, status="failed", reason="empty_reasoning_result")
        return PluginReasoningResult(
            ok=True,
            status="completed",
            text=text[:MAX_REASONING_OUTPUT_CHARS],
            evidence_events=_project_evidence_events(frame.get("tool_events")),
        )

    async def _reasoning_task(
        self,
        request: PluginReasoningRequest,
        payload: dict[str, Any],
    ) -> asyncio.Task[Any]:
        key = self._reasoning_task_key(request)
        if not key:
            task = asyncio.create_task(asyncio.to_thread(self._engine.process_turn, payload))
            task.add_done_callback(self._consume_task_exception)
            return task
        async with self._task_lock:
            self._prune_reasoning_tasks_locked()
            existing = self._idempotent_tasks.get(key)
            if existing is not None:
                return existing[0]
            task = asyncio.create_task(asyncio.to_thread(self._engine.process_turn, payload))
            task.add_done_callback(self._consume_task_exception)
            if len(self._idempotent_tasks) >= MAX_REASONING_RESULT_TASKS:
                return task
            self._idempotent_tasks[key] = (task, time.monotonic())
            self._prune_reasoning_tasks_locked()
            return task

    @staticmethod
    def _reasoning_task_key(request: PluginReasoningRequest) -> str:
        idempotency_key = str(request.memory_idempotency_key or "").strip()
        if not idempotency_key:
            return ""
        scope = "\x00".join(
            (
                str(request.profile_user_id or ""),
                str(request.session_id or ""),
                str(request.character_pack_id or ""),
                idempotency_key,
            )
        )
        return hashlib.sha256(scope.encode("utf-8", errors="ignore")).hexdigest()

    def _prune_reasoning_tasks_locked(self) -> None:
        now = time.monotonic()
        removable = [
            key
            for key, (task, created_at) in self._idempotent_tasks.items()
            if task.done() and now - created_at >= REASONING_RESULT_REUSE_SECONDS
        ]
        for key in removable:
            self._idempotent_tasks.pop(key, None)
        overflow = max(0, len(self._idempotent_tasks) - MAX_REASONING_RESULT_TASKS)
        if overflow <= 0:
            return
        for key, (task, _created_at) in list(self._idempotent_tasks.items()):
            if overflow <= 0:
                break
            if task.done():
                self._idempotent_tasks.pop(key, None)
                overflow -= 1

    @staticmethod
    def _consume_task_exception(task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            return
        try:
            task.exception()
        except Exception:
            return


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
        "stable_system_context": (request.stable_system_context, MAX_REASONING_STABLE_SYSTEM_CHARS),
        "memory_idempotency_key": (
            request.memory_idempotency_key,
            MAX_REASONING_IDEMPOTENCY_KEY_CHARS,
        ),
        "character_pack_id": (request.character_pack_id, 120),
    }
    for name, (raw, maximum) in fields.items():
        if not isinstance(raw, str) or len(raw) > maximum or "\x00" in raw:
            return f"invalid_{name}"
    if not request.trace_id.strip() or not request.profile_user_id.strip() or not request.session_id.strip():
        return "reasoning_identity_required"
    if not request.message.strip():
        return "reasoning_message_required"
    event_error = _validate_external_event(request.external_event)
    if event_error:
        return event_error
    return ""


def _validate_external_event(value: object) -> str:
    from .plugin_api import PluginExternalEvent

    if value is None:
        return ""
    if not isinstance(value, PluginExternalEvent):
        return "invalid_external_event"
    if not isinstance(value.event_type, str) or _EVENT_TYPE_PATTERN.fullmatch(value.event_type) is None:
        return "invalid_external_event_type"
    if not isinstance(value.source, str) or len(value.source) > 240 or "\x00" in value.source:
        return "invalid_external_event_source"
    if not isinstance(value.fields, tuple) or not value.fields or len(value.fields) > MAX_REASONING_EVENT_FIELDS:
        return "invalid_external_event_fields"
    seen: set[str] = set()
    total_chars = len(value.event_type) + len(value.source)
    for item in value.fields:
        if not isinstance(item, tuple) or len(item) != 2:
            return "invalid_external_event_fields"
        key, field_value = item
        if (
            not isinstance(key, str)
            or _EVENT_FIELD_PATTERN.fullmatch(key) is None
            or key in _RESERVED_EVENT_FIELDS
            or key in seen
        ):
            return "invalid_external_event_fields"
        if (
            not isinstance(field_value, str)
            or not field_value.strip()
            or len(field_value) > MAX_REASONING_EVENT_FIELD_CHARS
            or "\x00" in field_value
        ):
            return "invalid_external_event_fields"
        seen.add(key)
        total_chars += len(key) + len(field_value)
    if total_chars > MAX_REASONING_EVENT_TOTAL_CHARS:
        return "invalid_external_event_fields"
    return ""


def _project_external_event(value: Any) -> dict[str, Any]:
    return {
        "event_type": value.event_type,
        "source": value.source.strip(),
        "fields": {
            key: field_value.strip()
            for key, field_value in value.fields
            if field_value.strip()
        },
    }


def _render_external_event(event: Mapping[str, Any]) -> str:
    from memcore.rendering import render_external_event_text

    rendered = render_external_event_text(
        event_type=str(event.get("event_type") or ""),
        fields=dict(event.get("fields") or {}),
        source=str(event.get("source") or ""),
    ).strip()
    if not rendered:
        raise ValueError("empty_external_event")
    return rendered


def _frame_text(frame: Mapping[str, Any]) -> str:
    speech = str(frame.get("speech") or "").strip()
    if speech:
        return speech
    segments = frame.get("speech_segments")
    if not isinstance(segments, (list, tuple)):
        return ""
    return "\n".join(str(item or "").strip() for item in segments if str(item or "").strip())


def _render_persistent_proactive_message(message: str) -> str:
    """Keep the actionable event cue inside the append-only memory turn.

    The exact string sent to the model is also what the engine persists.  A
    later request can therefore reuse the previous user-input cache boundary
    without reconstructing a different wrapper around the event.
    """

    clean = str(message or "").strip()
    return f"{_PROACTIVE_MESSAGE_HEADER}\n{clean}\n{_PROACTIVE_RESPONSE_DIRECTIVE}"


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
