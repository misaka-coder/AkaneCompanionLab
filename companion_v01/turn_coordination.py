"""Cross-channel coordination for one in-flight conversational turn.

Execution stays owned by the host/engine.  This module only decides whether a
new user input starts a turn, steers the current turn at its next safe model
boundary, waits behind another actor, or requests an honest stop.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any


def _identity_key(profile_user_id: Any, session_id: Any) -> str:
    return f"{str(profile_user_id or '').strip()}\0{str(session_id or '').strip()}"


@dataclass(frozen=True, slots=True)
class SteeringInput:
    source_id: str
    content: str
    timestamp: int
    actor_id: str
    actor_display_name: str = ""
    channel: str = ""


@dataclass(slots=True)
class _ActiveTurn:
    token: str
    actor_id: str
    channel: str
    pending: list[SteeringInput] = field(default_factory=list)
    stop_requested: bool = False
    phase: str = "running"


class TurnCoordinator:
    """One runtime-wide registry shared by desktop, QQ, and the engine.

    The async lock preserves FIFO for independent turns.  The thread lock
    protects the active-turn mailbox because the engine polls it from a worker
    thread while HTTP/QQ handlers submit input from the event-loop thread.
    """

    def __init__(self) -> None:
        self._state_lock = threading.RLock()
        self._active: dict[str, _ActiveTurn] = {}
        self._async_locks: dict[str, tuple[asyncio.Lock, int]] = {}

    def is_busy(self, profile_user_id: Any, session_id: Any) -> bool:
        key = _identity_key(profile_user_id, session_id)
        with self._state_lock:
            return key in self._active

    def offer_steer(
        self,
        *,
        profile_user_id: Any,
        session_id: Any,
        actor_id: Any,
        content: Any,
        timestamp: int | None = None,
        actor_display_name: Any = "",
        channel: Any = "",
    ) -> dict[str, Any]:
        text = str(content or "").strip()
        actor = str(actor_id or "").strip()
        if not text or not actor:
            return {"ok": False, "status": "invalid", "reason": "actor_and_content_required"}
        key = _identity_key(profile_user_id, session_id)
        with self._state_lock:
            active = self._active.get(key)
            if active is None:
                return {"ok": False, "status": "idle", "reason": "no_active_turn"}
            if active.phase != "running":
                return {"ok": False, "status": "finalizing", "reason": "active_turn_finalizing"}
            if active.actor_id != actor:
                return {"ok": False, "status": "busy_other_actor", "reason": "actor_mismatch"}
            item = SteeringInput(
                source_id=f"steer_{uuid.uuid4().hex}",
                content=text,
                timestamp=int(timestamp or time.time()),
                actor_id=actor,
                actor_display_name=str(actor_display_name or "").strip(),
                channel=str(channel or "").strip(),
            )
            active.pending.append(item)
            return {
                "ok": True,
                "status": "accepted",
                "reason": "steer_queued_for_safe_boundary",
                "turn_token": active.token,
                "source_id": item.source_id,
                "pending_count": len(active.pending),
            }

    def request_stop(
        self,
        *,
        profile_user_id: Any,
        session_id: Any,
        actor_id: Any,
    ) -> dict[str, Any]:
        key = _identity_key(profile_user_id, session_id)
        actor = str(actor_id or "").strip()
        with self._state_lock:
            active = self._active.get(key)
            if active is None:
                return {"ok": False, "status": "idle", "reason": "no_active_turn"}
            if active.phase != "running":
                return {"ok": False, "status": "finalizing", "reason": "active_turn_finalizing"}
            if actor and active.actor_id != actor:
                return {"ok": False, "status": "busy_other_actor", "reason": "actor_mismatch"}
            active.stop_requested = True
            return {
                "ok": True,
                "status": "requested",
                "reason": "stop_requested_at_safe_boundary",
                "turn_token": active.token,
            }

    def drain(self, token: Any) -> dict[str, Any]:
        normalized = str(token or "").strip()
        with self._state_lock:
            active = next((item for item in self._active.values() if item.token == normalized), None)
            if active is None:
                return {"ok": False, "status": "stale", "stop_requested": False, "steers": []}
            pending = list(active.pending)
            active.pending.clear()
            return {
                "ok": True,
                "status": "stop_requested" if active.stop_requested else "running",
                "stop_requested": bool(active.stop_requested),
                "steers": pending,
            }

    def begin_finalization(self, token: Any) -> dict[str, Any]:
        normalized = str(token or "").strip()
        with self._state_lock:
            active = next((item for item in self._active.values() if item.token == normalized), None)
            if active is None:
                return {"ok": False, "status": "stale", "stop_requested": False, "steers": []}
            if active.pending or active.stop_requested:
                pending = list(active.pending)
                active.pending.clear()
                return {
                    "ok": True,
                    "status": "stop_requested" if active.stop_requested else "steer_pending",
                    "stop_requested": bool(active.stop_requested),
                    "steers": pending,
                }
            active.phase = "finalizing"
            return {"ok": True, "status": "finalizing", "stop_requested": False, "steers": []}

    @asynccontextmanager
    async def hold(
        self,
        profile_user_id: Any,
        session_id: Any,
        *,
        actor_id: Any = "",
        channel: Any = "",
    ):
        key = _identity_key(profile_user_id, session_id)
        with self._state_lock:
            entry = self._async_locks.get(key)
            lock = entry[0] if entry is not None else asyncio.Lock()
            self._async_locks[key] = (lock, (entry[1] if entry is not None else 0) + 1)
        token = ""
        try:
            async with lock:
                token = f"turnctl_{uuid.uuid4().hex}"
                with self._state_lock:
                    self._active[key] = _ActiveTurn(
                        token=token,
                        actor_id=str(actor_id or "").strip(),
                        channel=str(channel or "").strip(),
                    )
                yield token
        finally:
            with self._state_lock:
                active = self._active.get(key)
                if token and active is not None and active.token == token:
                    self._active.pop(key, None)
                current = self._async_locks.get(key)
                if current is not None and current[0] is lock:
                    remaining = current[1] - 1
                    if remaining <= 0:
                        self._async_locks.pop(key, None)
                    else:
                        self._async_locks[key] = (lock, remaining)


__all__ = ["SteeringInput", "TurnCoordinator"]
