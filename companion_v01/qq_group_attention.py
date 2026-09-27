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
    MemCore, but never postpone the already scheduled observation. Once claimed,
    its participant set is frozen so same-participant follow-ups can supersede
    an unspoken response without letting newcomers interrupt it.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        self._engaged_until: dict[str, float] = {}
        self._idle_cooldown_until: dict[str, float] = {}
        self._tickets: dict[str, AttentionTicket] = {}
        self._in_flight: dict[str, str] = {}
        self._dirty_during_flight: set[str] = set()
        self._pending_actors: dict[str, set[str]] = {}
        self._active_actors: dict[str, set[str]] = {}
        self._superseded: set[str] = set()
        self._reply_started: set[str] = set()

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
        actor_keys: tuple[str, ...] = (),
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
                self._pending_actors.setdefault(normalized_key, set()).update(filter(None, actor_keys))
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
            self._pending_actors[normalized_key] = set(filter(None, actor_keys))
            return ticket, "armed"

    def claim(self, ticket: AttentionTicket) -> bool:
        with self._lock:
            current = self._tickets.get(ticket.key)
            if current is None or current.token != ticket.token:
                return False
            self._tickets.pop(ticket.key, None)
            self._in_flight[ticket.key] = ticket.token
            self._active_actors[ticket.key] = self._pending_actors.pop(ticket.key, set())
            self._superseded.discard(ticket.key)
            self._reply_started.discard(ticket.key)
            self._dirty_during_flight.discard(ticket.key)
            return True

    def note_pending_actors(self, key: str, actor_keys: tuple[str, ...]) -> None:
        with self._lock:
            if key in self._tickets:
                self._pending_actors.setdefault(key, set()).update(filter(None, actor_keys))

    def finish(self, ticket: AttentionTicket, *, discard_dirty: bool = False) -> bool:
        """Close one claimed generation and report whether newer facts arrived."""

        with self._lock:
            if self._in_flight.get(ticket.key) != ticket.token:
                return False
            self._in_flight.pop(ticket.key, None)
            dirty = ticket.key in self._dirty_during_flight
            self._dirty_during_flight.discard(ticket.key)
            self._active_actors.pop(ticket.key, None)
            self._superseded.discard(ticket.key)
            self._reply_started.discard(ticket.key)
            return bool(dirty and not discard_dirty)

    def cancel(self, key: str) -> bool:
        normalized_key = str(key or "").strip()
        with self._lock:
            removed = self._tickets.pop(normalized_key, None) is not None
            running = self._in_flight.pop(normalized_key, None) is not None
            dirty = normalized_key in self._dirty_during_flight
            self._dirty_during_flight.discard(normalized_key)
            self._pending_actors.pop(normalized_key, None)
            self._active_actors.pop(normalized_key, None)
            self._superseded.discard(normalized_key)
            self._reply_started.discard(normalized_key)
            return removed or running or dirty

    def supersede_for_actor(
        self, key: str, actor_key: str, *, stop_running: Callable[[str], bool] | None = None,
    ) -> str:
        """Invalidate an unspoken observation when a window participant adds input.

        Keep ownership until the worker exits: new observations must wait for
        its delivery/persistence cleanup rather than overlap the old request.
        """
        with self._lock:
            token = self._in_flight.get(key, "")
            if (not token or key in self._reply_started or key in self._superseded
                    or not actor_key or actor_key not in self._active_actors.get(key, set())):
                return ""
            # Once the engine has entered finalization, let persistence and
            # delivery finish together instead of hiding an already stored reply.
            if stop_running is not None and not stop_running(token):
                return ""
            self._superseded.add(key)
            return token

    def is_superseded(self, ticket: AttentionTicket) -> bool:
        with self._lock:
            return self._in_flight.get(ticket.key) == ticket.token and ticket.key in self._superseded

    def begin_reply(self, ticket: AttentionTicket) -> bool:
        """Atomically choose between restarting and beginning visible output."""
        with self._lock:
            if self._in_flight.get(ticket.key) != ticket.token or ticket.key in self._superseded:
                return False
            self._reply_started.add(ticket.key)
            return True

    def is_current(self, ticket: AttentionTicket) -> bool:
        """A cancelled generation cannot resume or clean up its replacement."""
        with self._lock:
            return self._in_flight.get(ticket.key) == ticket.token

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
