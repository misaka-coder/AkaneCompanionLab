"""Open-task continuations over existing execution/Job authorities, not a store."""

import threading
import time
from typing import Callable


class TaskWork:
    """Track receipts owned by one live task and collect terminal facts once.

    The driver is the only consumer. Tool workers may register concurrently.
    Restart never reconstructs this one-shot driver or replays its side effects.
    """

    def __init__(self):
        self._pending = {}
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._control_state = "running"
        self._resume_generation = 0

    def track(self, key: str, *, inspect: Callable, cancel: Callable) -> None:
        cancel_now = False
        with self._condition:
            if self._control_state in {"stopping", "stopped"}:
                cancel_now = True
            else:
                self._pending.setdefault(key, (inspect, cancel))
            self._condition.notify_all()
        if cancel_now:
            # A task stopped while a worker was admitting a new action must
            # not let that action become live after the stop boundary.
            cancel()

    def collect(self) -> list[dict]:
        with self._lock:
            pending = tuple(self._pending.items())
        ready = []
        for key, (inspect, _cancel) in pending:
            value = inspect()
            if value is None:
                continue
            ready.append({"id": key, **value})
            with self._lock:
                self._pending.pop(key, None)
        return ready

    def wait(self, cancelled: Callable[[], bool]) -> list[dict]:
        while self.pending and not cancelled():
            if not self.wait_for_resume(cancelled):
                return []
            ready = self.collect()
            if ready:
                return ready
            # No model request, busy loop or parent conversation lock while idle.
            time.sleep(0.2)
        return []

    def wait_for_resume(self, cancelled: Callable[[], bool]) -> bool:
        """Block while paused without dispatching another model/tool step."""

        with self._condition:
            while self._control_state == "paused" and not cancelled():
                self._condition.wait(timeout=0.2)
            return self._control_state == "running" and not cancelled()

    def control(self, action: str) -> dict[str, str | bool]:
        """Apply an in-process task control without fabricating termination."""

        normalized = str(action or "").strip().lower()
        if normalized not in {"pause", "resume", "stop"}:
            return {"ok": False, "status": "invalid", "reason": "task_control_action_invalid"}
        callbacks = ()
        with self._condition:
            state = self._control_state
            if normalized == "pause":
                if state == "paused":
                    return {"ok": True, "status": "paused", "reason": "task_already_paused"}
                if state in {"stopping", "stopped"}:
                    return {"ok": False, "status": state, "reason": "task_not_pauseable"}
                self._control_state = "paused"
                self._condition.notify_all()
                return {"ok": True, "status": "paused", "reason": "task_paused"}
            if normalized == "resume":
                if state == "running":
                    return {"ok": True, "status": "running", "reason": "task_already_running"}
                if state != "paused":
                    return {"ok": False, "status": state, "reason": "task_not_resumable"}
                self._control_state = "running"
                self._resume_generation += 1
                self._condition.notify_all()
                return {"ok": True, "status": "running", "reason": "task_resumed"}
            if state == "stopped":
                return {"ok": True, "status": "stopped", "reason": "task_already_stopped"}
            self._control_state = "stopping"
            callbacks = tuple(cancel for _inspect, cancel in self._pending.values())
            if not self._pending:
                self._control_state = "stopped"
            self._condition.notify_all()
        for cancel in callbacks:
            try:
                cancel()
            except Exception:
                # The worker still observes the stopping state; one bad
                # cancellation callback must not revive the task.
                pass
        return {
            "ok": True,
            "status": self.state,
            "reason": "task_stop_requested" if self.state == "stopping" else "task_stopped",
        }

    @property
    def pending(self) -> bool:
        with self._lock:
            return bool(self._pending)

    @property
    def state(self) -> str:
        with self._lock:
            return self._control_state

    @property
    def stop_requested(self) -> bool:
        return self.state in {"stopping", "stopped"}

    @property
    def resume_generation(self) -> int:
        with self._lock:
            return self._resume_generation

    def acknowledge(self, key: str) -> None:
        """A terminal fact already returned as a tool result needs no event."""
        with self._lock:
            self._pending.pop(key, None)

    def close(self) -> list[str]:
        """Request cancellation on task exit; never claim unconfirmed termination."""
        with self._condition:
            if self._control_state != "stopped":
                self._control_state = "stopping"
            pending = tuple(self._pending.items())
            self._condition.notify_all()
        unconfirmed = []
        for key, (inspect, cancel) in pending:
            try:
                if inspect() is None:
                    cancel()
                    if inspect() is None:
                        unconfirmed.append(key)
            except Exception:
                unconfirmed.append(key)
        with self._condition:
            if not unconfirmed and not self._pending:
                self._control_state = "stopped"
            self._condition.notify_all()
        return unconfirmed

    def finish(self) -> None:
        """Mark a driver that exited its loop as no longer controllable."""

        with self._condition:
            if not self._pending:
                self._control_state = "stopped"
            self._condition.notify_all()
