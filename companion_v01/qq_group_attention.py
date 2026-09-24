from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable


ATTENTION_MODES = frozenset({"off", "engaged", "adaptive"})


@dataclass(frozen=True)
class AttentionTicket:
    key: str
    token: str
    deadline: float
    reason: str


class QQGroupAttentionState:
    """Mechanical scheduling state; conversation content stays in MemCore.

    One pending ticket owns a fixed deadline. Later passive messages may update
    MemCore, but never postpone the already scheduled observation.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        self._engaged_until: dict[str, float] = {}
        self._idle_cooldown_until: dict[str, float] = {}
        self._tickets: dict[str, AttentionTicket] = {}
        self._in_flight: dict[str, str] = {}
        self._dirty_during_flight: set[str] = set()

    @staticmethod
    def normalize_mode(value: Any, *, default: str = "engaged") -> str:
        mode = str(value or "").strip().lower()
        if mode in ATTENTION_MODES:
            return mode
        fallback = str(default or "engaged").strip().lower()
        return fallback if fallback in ATTENTION_MODES else "engaged"

    def arm(
        self,
        key: str,
        *,
        mode: str,
        delay_seconds: float,
    ) -> tuple[AttentionTicket | None, str]:
        normalized_key = str(key or "").strip()
        if not normalized_key:
            return None, "invalid_key"
        normalized_mode = self.normalize_mode(mode)
        now = self._clock()
        with self._lock:
            if normalized_mode == "off":
                return None, "mode_off"
            if normalized_key in self._in_flight:
                # Conversation content is already durable in MemCore.  Only
                # remember that a newer generation exists; do not create a
                # second observation while the current one is still running.
                self._dirty_during_flight.add(normalized_key)
                return None, "in_flight_dirty"
            existing = self._tickets.get(normalized_key)
            if existing is not None:
                return existing, "already_pending"
            engaged = self._engaged_until.get(normalized_key, 0.0) > now
            if normalized_mode == "engaged" and not engaged:
                return None, "outside_engagement"
            if normalized_mode == "adaptive" and not engaged:
                if self._idle_cooldown_until.get(normalized_key, 0.0) > now:
                    return None, "idle_cooldown"
                reason = "idle_observation"
            else:
                reason = "engaged_followup"
            ticket = AttentionTicket(
                key=normalized_key,
                token=uuid.uuid4().hex,
                deadline=now + max(0.0, float(delay_seconds)),
                reason=reason,
            )
            self._tickets[normalized_key] = ticket
            return ticket, "armed"

    def claim(self, ticket: AttentionTicket) -> bool:
        with self._lock:
            current = self._tickets.get(ticket.key)
            if current is None or current.token != ticket.token:
                return False
            self._tickets.pop(ticket.key, None)
            self._in_flight[ticket.key] = ticket.token
            self._dirty_during_flight.discard(ticket.key)
            return True

    def finish(self, ticket: AttentionTicket, *, discard_dirty: bool = False) -> bool:
        """Close one claimed generation and report whether newer facts arrived."""

        with self._lock:
            if self._in_flight.get(ticket.key) != ticket.token:
                return False
            self._in_flight.pop(ticket.key, None)
            dirty = ticket.key in self._dirty_during_flight
            self._dirty_during_flight.discard(ticket.key)
            return bool(dirty and not discard_dirty)

    def cancel(self, key: str) -> bool:
        normalized_key = str(key or "").strip()
        with self._lock:
            removed = self._tickets.pop(normalized_key, None) is not None
            dirty = normalized_key in self._dirty_during_flight
            self._dirty_during_flight.discard(normalized_key)
            return removed or dirty

    def mark_visible_reply(self, key: str, *, ttl_seconds: float) -> None:
        normalized_key = str(key or "").strip()
        if not normalized_key:
            return
        now = self._clock()
        with self._lock:
            self._engaged_until[normalized_key] = now + max(0.0, float(ttl_seconds))
            self._idle_cooldown_until.pop(normalized_key, None)

    def mark_idle_observed(self, key: str, *, cooldown_seconds: float) -> None:
        normalized_key = str(key or "").strip()
        if not normalized_key:
            return
        with self._lock:
            self._idle_cooldown_until[normalized_key] = self._clock() + max(
                0.0,
                float(cooldown_seconds),
            )

    def is_engaged(self, key: str) -> bool:
        with self._lock:
            return self._engaged_until.get(str(key or "").strip(), 0.0) > self._clock()

    def has_pending(self, key: str) -> bool:
        with self._lock:
            return str(key or "").strip() in self._tickets

    def is_in_flight(self, key: str) -> bool:
        with self._lock:
            return str(key or "").strip() in self._in_flight
