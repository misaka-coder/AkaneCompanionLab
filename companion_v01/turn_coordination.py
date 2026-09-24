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
from collections import deque
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


_CANCELLATION_CHECKS: ContextVar[tuple] = ContextVar("turn_cancellation_checks", default=())


def current_cancellation_check():
    """Capture host controls for this invocation, without serializing them."""
    checks = _CANCELLATION_CHECKS.get()
    return (lambda: any(check() for check in checks)) if checks else None


def cancellation_requested():
    check = current_cancellation_check()
    return check is not None and check()


@contextmanager
def cancellation_scope(check):
    token = _CANCELLATION_CHECKS.set((*_CANCELLATION_CHECKS.get(), check))
    try:
        yield
    finally:
        _CANCELLATION_CHECKS.reset(token)


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
    native_user_images: tuple[dict[str, Any], ...] = ()
    receipt_item_id: str = ""
    receipt_claim_token: str = ""
    current_attachment_ids: tuple[str, ...] | None = None


@dataclass(slots=True)
class _ActiveTurn:
    token: str
    actor_id: str
    channel: str
    turn_kind: str = ""
    pending: list[SteeringInput] = field(default_factory=list)
    stop_requested: bool = False
    stop_reason: str = ""
    phase: str = "running"
    scope_check: Callable[[], bool] | None = None


@dataclass(frozen=True, slots=True)
class SessionWorkItem:
    """One in-process item waiting for a conversation timeline.

    The queue does not interpret payloads or execute conversation work.  It
    only preserves arrival order and lets the host coalesce adjacent items of
    explicitly declared kinds (for example passive observation writes).
    """

    sequence: int
    kind: str
    payload: Any
    enqueued_at: float


class SessionWorkQueue:
    """One FIFO worker per session without holding an inbound HTTP request.

    ``enqueue`` must be called from the owning event loop. Different session
    keys get independent workers; one key is always drained serially. Adjacent
    batchable items are passed to the handler together without reordering work
    across an intervening active turn.
    """

    def __init__(
        self,
        handler: Callable[[str, list[SessionWorkItem]], Awaitable[None]],
        *,
        schedule_task: Callable[[Awaitable[None]], Any] | None = None,
        batchable_kinds: set[str] | frozenset[str] = frozenset(),
        on_error: Callable[[str, list[SessionWorkItem], BaseException], None] | None = None,
    ) -> None:
        self._handler = handler
        self._schedule_task = schedule_task or asyncio.create_task
        self._batchable_kinds = frozenset(str(item or "").strip() for item in batchable_kinds)
        self._on_error = on_error
        self._queues: dict[str, deque[SessionWorkItem]] = {}
        self._workers: dict[str, Any] = {}
        self._sequence = 0

    def pending_count(self, key: Any) -> int:
        normalized = str(key or "").strip()
        return len(self._queues.get(normalized, ())) if normalized else 0

    def has_work(self, key: Any) -> bool:
        normalized = str(key or "").strip()
        return bool(normalized and (normalized in self._workers or self._queues.get(normalized)))

    def enqueue(self, key: Any, *, kind: Any, payload: Any) -> dict[str, Any]:
        normalized_key = str(key or "").strip()
        normalized_kind = str(kind or "").strip()
        if not normalized_key or not normalized_kind:
            return {"ok": False, "status": "invalid", "reason": "queue_key_and_kind_required"}
        self._sequence += 1
        item = SessionWorkItem(
            sequence=self._sequence,
            kind=normalized_kind,
            payload=payload,
            enqueued_at=time.perf_counter(),
        )
        queue = self._queues.setdefault(normalized_key, deque())
        queue.append(item)
        worker = self._workers.get(normalized_key)
        if worker is None or bool(getattr(worker, "done", lambda: False)()):
            coroutine = self._drain(normalized_key)
            try:
                self._workers[normalized_key] = self._schedule_task(coroutine)
            except Exception:
                coroutine.close()
                queue.pop()
                if not queue:
                    self._queues.pop(normalized_key, None)
                raise
        return {
            "ok": True,
            "status": "queued",
            "reason": "session_fifo",
            "sequence": item.sequence,
            "pending_count": len(queue),
        }

    async def _drain(self, key: str) -> None:
        try:
            while True:
                queue = self._queues.get(key)
                if not queue:
                    return
                first = queue.popleft()
                batch = [first]
                if first.kind in self._batchable_kinds:
                    while queue and queue[0].kind == first.kind:
                        batch.append(queue.popleft())
                try:
                    await self._handler(key, batch)
                except Exception as exc:
                    if self._on_error is not None:
                        self._on_error(key, batch, exc)
                    else:
                        asyncio.get_running_loop().call_exception_handler(
                            {
                                "message": "session work handler failed",
                                "exception": exc,
                            }
                        )
        finally:
            self._workers.pop(key, None)
            if not self._queues.get(key):
                self._queues.pop(key, None)


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
        self._stop_epochs: dict[str, int] = {}
        self._stop_listeners: list[Callable[..., Any]] = []

    def bind_stop_listener(self, listener):
        with self._state_lock:
            if listener not in self._stop_listeners:
                self._stop_listeners.append(listener)

    def stop_requested(self, token):
        self._refresh_scope(token)
        with self._state_lock:
            return any(item.token == token and item.stop_requested for item in self._active.values())

    def _refresh_scope(self, token):
        # Store reads may acquire their own locks; do not call them under the
        # coordinator lock. Recheck exact turn ownership after the read.
        with self._state_lock:
            active = next((item for item in self._active.values() if item.token == token), None)
            check = active.scope_check if active is not None else None
        if check is None or not check():
            return
        with self._state_lock:
            key = next((key for key, item in self._active.items() if item is active), None)
            if key is None or active.scope_check is not check:
                return
            active.stop_requested = True
            active.stop_reason = "execution_scope_cancelled"
            self._notify_stop(key, active)
            # Keep the check if notification fails, so child revocation retries.
            active.scope_check = None

    @contextmanager
    def execution_scope(self, token, *, cancelled):
        with self._state_lock:
            active = next((item for item in self._active.values() if item.token == token), None)
            if active is None or active.scope_check is not None:
                raise ValueError("turn_execution_scope_unavailable")
            active.scope_check = cancelled
        try:
            with cancellation_scope(lambda: self.stop_requested(token)):
                yield
        finally:
            with self._state_lock:
                if active.scope_check is cancelled:
                    active.scope_check = None

    def _notify_stop(self, key, active):
        profile, session = key.split("\0", 1)
        for listener in self._stop_listeners:
            listener(profile_user_id=profile, session_id=session, turn_token=active.token)

    def stop_epoch(self, profile_user_id, session_id):
        with self._state_lock:
            return self._stop_epochs.get(_identity_key(profile_user_id, session_id), 0)

    def active_token(self, profile_user_id, session_id):
        with self._state_lock:
            active = self._active.get(_identity_key(profile_user_id, session_id))
            return active.token if active else ""

    def request_stop_token(self, token):
        """Withdraw one owned request without accidentally stopping its successor."""
        with self._state_lock:
            active = next((item for item in self._active.values() if item.token == token), None)
            if active is None or active.phase != "running":
                return False
            active.stop_requested = True
            active.stop_reason = "agent_turn_cancel_requested"
            key = next(key for key, item in self._active.items() if item is active)
            self._notify_stop(key, active)
            return True

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
        native_user_images: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
        source_id: Any = "",
        receipt_item_id: Any = "",
        receipt_claim_token: Any = "",
        current_attachment_ids: list[str] | None = None,
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
            if active.turn_kind in {"qq_attention", "plugin_event"}:
                # Ambient attention and plugin events are host-initiated work,
                # not work owned by the passive message's sender. A real
                # addressed message must therefore become the next ordinary
                # turn rather than disappearing into the optional turn's steer
                # mailbox. Ask the engine to finish at its next safe boundary
                # while the QQ route queues the addressed message behind it.
                if not active.stop_requested:
                    active.stop_requested = True
                    active.stop_reason = "addressed_input_preempts_optional_turn"
                return {
                    "ok": False,
                    "status": "preempting_optional_turn",
                    "reason": "addressed_input_preempts_optional_turn",
                    "turn_token": active.token,
                }
            if active.actor_id != actor:
                return {"ok": False, "status": "busy_other_actor", "reason": "actor_mismatch"}
            safe_images = tuple(
                dict(raw)
                for raw in list(native_user_images or [])[:5]
                if isinstance(raw, dict) and str(raw.get("data_url") or "").startswith("data:image/")
            )
            item = SteeringInput(
                source_id=str(source_id or "").strip() or f"steer_{uuid.uuid4().hex}",
                content=text,
                timestamp=int(timestamp or time.time()),
                actor_id=actor,
                actor_display_name=str(actor_display_name or "").strip(),
                channel=str(channel or "").strip(),
                native_user_images=safe_images,
                receipt_item_id=str(receipt_item_id or "").strip(),
                receipt_claim_token=str(receipt_claim_token or "").strip(),
                current_attachment_ids=tuple(dict.fromkeys(
                    value.strip() for value in current_attachment_ids
                    if isinstance(value, str) and value.strip() and len(value) <= 120
                ))[:40] if isinstance(current_attachment_ids, list) else None,
            )
            active.pending.append(item)
            return {
                "ok": True,
                "status": "accepted",
                "reason": "steer_queued_for_safe_boundary",
                "turn_token": active.token,
                "source_id": item.source_id,
                "pending_count": len(active.pending),
                "native_image_count": len(safe_images),
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
            active.stop_reason = "user_stopped"
            self._stop_epochs[key] = self._stop_epochs.get(key, 0) + 1
            self._notify_stop(key, active)
            return {
                "ok": True,
                "status": "requested",
                "reason": "stop_requested_at_safe_boundary",
                "turn_token": active.token,
            }

    def drain(self, token: Any) -> dict[str, Any]:
        normalized = str(token or "").strip()
        self._refresh_scope(normalized)
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
                "stop_reason": active.stop_reason,
                "steers": pending,
            }

    def begin_finalization(self, token: Any) -> dict[str, Any]:
        normalized = str(token or "").strip()
        self._refresh_scope(normalized)
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
                    "stop_reason": active.stop_reason,
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
        turn_kind: Any = "",
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
                        turn_kind=str(turn_kind or "").strip(),
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


__all__ = ["SessionWorkItem", "SessionWorkQueue", "SteeringInput", "TurnCoordinator"]
