"""Host-owned event broker for supervised plugin contributions.

The broker observes immutable event snapshots and returns typed delivery
intentions.  It never writes MemCore, starts an Agent turn, or sends to a
channel itself; the owning channel/runtime applies those decisions through its
existing authoritative paths.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Callable

from .plugin_api import (
    PluginEventEnvelope,
    PluginEventHandler,
    PluginEventResult,
    PluginExternalEvent,
)


DEFAULT_EVENT_HANDLER_TIMEOUT_SECONDS = 2.0
MAX_EVENT_FIELDS = 32
MAX_EVENT_FIELD_CHARS = 4_000
MAX_EVENT_TOTAL_CHARS = 16_000
_DELIVERIES = frozenset({"internal", "current_turn", "timeline"})
_EVENT_TYPE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,119}$")
_EVENT_FIELD_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,119}$")
_SAFE_REASON_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_RESERVED_EVENT_FIELDS = frozenset(
    {
        "api_key",
        "apikey",
        "access_token",
        "authorization",
        "cookie",
        "password",
        "secret",
        "token",
    }
)


@dataclass(frozen=True, slots=True)
class _PluginEventRegistration:
    plugin_id: str
    event_type: str
    handler: PluginEventHandler


@dataclass(frozen=True, slots=True)
class PluginEventDispatchResult:
    """Aggregate broker result consumed by the channel/runtime host."""

    ok: bool
    status: str
    current_turn_events: tuple[PluginExternalEvent, ...] = ()
    timeline_events: tuple[PluginExternalEvent, ...] = ()
    request_agent_turn: bool = False
    failures: tuple[tuple[str, str], ...] = ()


class PluginEventBroker:
    """Dispatch exact event types without changing the normal host path."""

    def __init__(
        self,
        registrations: tuple[_PluginEventRegistration, ...],
        *,
        handler_timeout_seconds: float = DEFAULT_EVENT_HANDLER_TIMEOUT_SECONDS,
        availability_provider: Callable[[], bool] | None = None,
        registrations_provider: Callable[[], tuple[_PluginEventRegistration, ...]] | None = None,
    ) -> None:
        self._handler_timeout_seconds = max(0.01, float(handler_timeout_seconds))
        self._availability_provider = availability_provider or (lambda: True)
        self._static_registrations = tuple(registrations)
        self._registrations_provider = registrations_provider

    @property
    def registered_event_types(self) -> tuple[str, ...]:
        return tuple(sorted({item.event_type for item in self._current_registrations()}))

    def observes(self, event_type: str) -> bool:
        normalized = _normalize_event_type(event_type)
        return any(item.event_type == normalized for item in self._current_registrations())

    async def dispatch(self, event: PluginEventEnvelope) -> PluginEventDispatchResult:
        """Observe one event; handler failures never block the caller's path."""

        if not isinstance(event, PluginEventEnvelope):
            return PluginEventDispatchResult(False, "invalid_event", failures=(("host", "invalid_event"),))
        event_type = _normalize_event_type(event.event_type)
        if not event.event_id or not event_type:
            return PluginEventDispatchResult(False, "invalid_event", failures=(("host", "invalid_event"),))
        try:
            available = bool(self._availability_provider())
        except Exception:
            available = False
        if not available:
            return PluginEventDispatchResult(False, "host_unavailable")

        registrations = tuple(
            item for item in self._current_registrations() if item.event_type == event_type
        )
        if not registrations:
            return PluginEventDispatchResult(True, "unobserved")

        outcomes = await asyncio.gather(
            *(self._call_handler(item, event) for item in registrations),
            return_exceptions=False,
        )
        current_turn: list[PluginExternalEvent] = []
        timeline: list[PluginExternalEvent] = []
        failures: list[tuple[str, str]] = []
        request_agent_turn = False
        for registration, outcome in zip(registrations, outcomes):
            if isinstance(outcome, str):
                failures.append((registration.plugin_id, outcome))
                continue
            delivery, emitted, wants_turn, reason = outcome
            request_agent_turn = request_agent_turn or wants_turn or delivery == "current_turn"
            if delivery == "current_turn" and emitted is not None:
                current_turn.append(emitted)
            elif delivery == "timeline" and emitted is not None:
                timeline.append(emitted)
            if reason:
                failures.append((registration.plugin_id, reason))

        return PluginEventDispatchResult(
            ok=not failures,
            status="observed" if not failures else "partially_observed",
            current_turn_events=tuple(current_turn),
            timeline_events=tuple(timeline),
            request_agent_turn=request_agent_turn,
            failures=tuple(failures),
        )

    async def _call_handler(
        self,
        registration: _PluginEventRegistration,
        event: PluginEventEnvelope,
    ) -> tuple[str, PluginExternalEvent | None, bool, str] | str:
        try:
            result = await asyncio.wait_for(
                registration.handler.handle_event(event),
                timeout=self._handler_timeout_seconds,
            )
        except (asyncio.TimeoutError, TimeoutError):
            return "handler_timeout"
        except asyncio.CancelledError:
            raise
        except Exception:
            return "handler_exception"
        if not isinstance(result, PluginEventResult):
            return "invalid_handler_result"

        delivery = str(result.delivery or "internal").strip().lower()
        if delivery not in _DELIVERIES:
            return "invalid_delivery"
        emitted = _normalize_external_event(result.event)
        if delivery in {"current_turn", "timeline"} and emitted is None:
            return "delivery_event_required"
        return (
            delivery,
            emitted,
            bool(result.request_agent_turn),
            _safe_reason(result.reason),
        )

    def _current_registrations(self) -> tuple[_PluginEventRegistration, ...]:
        if self._registrations_provider is not None:
            try:
                provided = self._registrations_provider()
            except Exception:
                provided = ()
            if isinstance(provided, tuple):
                try:
                    available = bool(self._availability_provider())
                except Exception:
                    available = False
                if available:
                    self._static_registrations = provided
        return self._static_registrations


def render_current_turn_events(events: tuple[PluginExternalEvent, ...]) -> str:
    """Render typed plugin facts as a compact request-local tail section."""

    rendered: list[str] = []
    for event in events:
        fields = {str(key): str(value) for key, value in event.fields}
        payload = {
            "event_type": event.event_type,
            **({"source": event.source} if event.source else {}),
            "fields": fields,
        }
        rendered.append(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    if not rendered:
        return ""
    return "【plugin.current_turn｜仅本轮】\n" + "\n".join(rendered)


def _normalize_event_type(value: object) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if _EVENT_TYPE_PATTERN.fullmatch(normalized) is not None else ""


def _normalize_external_event(value: object) -> PluginExternalEvent | None:
    if not isinstance(value, PluginExternalEvent):
        return None
    event_type = _normalize_event_type(value.event_type)
    if not event_type:
        return None
    if not isinstance(value.fields, tuple) or len(value.fields) > MAX_EVENT_FIELDS:
        return None
    fields: list[tuple[str, str]] = []
    seen: set[str] = set()
    total_chars = 0
    for field in value.fields:
        if not isinstance(field, tuple) or len(field) != 2:
            return None
        key, item = field
        if not isinstance(key, str) or not isinstance(item, str):
            return None
        clean_key = key.strip()
        if (
            _EVENT_FIELD_PATTERN.fullmatch(clean_key) is None
            or clean_key in seen
            or clean_key in _RESERVED_EVENT_FIELDS
        ):
            return None
        if len(item) > MAX_EVENT_FIELD_CHARS or "\x00" in item:
            return None
        seen.add(clean_key)
        total_chars += len(clean_key) + len(item)
        if total_chars > MAX_EVENT_TOTAL_CHARS:
            return None
        fields.append((clean_key, item))
    source = str(value.source or "").strip()
    if len(source) > 160 or "\x00" in source:
        return None
    return PluginExternalEvent(
        event_type=event_type,
        fields=tuple(fields),
        source=source,
    )


def _safe_reason(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return ""
    return normalized if _SAFE_REASON_PATTERN.fullmatch(normalized) is not None else "plugin_reported_error"


__all__ = [
    "DEFAULT_EVENT_HANDLER_TIMEOUT_SECONDS",
    "MAX_EVENT_FIELDS",
    "MAX_EVENT_FIELD_CHARS",
    "MAX_EVENT_TOTAL_CHARS",
    "PluginEventBroker",
    "PluginEventDispatchResult",
    "_PluginEventRegistration",
    "render_current_turn_events",
]
