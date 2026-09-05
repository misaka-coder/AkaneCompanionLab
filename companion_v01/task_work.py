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
        self._lock = threading.Lock()

    def track(self, key: str, *, inspect: Callable, cancel: Callable) -> None:
        with self._lock:
            self._pending.setdefault(key, (inspect, cancel))

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
            ready = self.collect()
            if ready:
                return ready
            # No model request, busy loop or parent conversation lock while idle.
            time.sleep(0.2)
        return []

    @property
    def pending(self) -> bool:
        with self._lock:
            return bool(self._pending)

    def close(self) -> list[str]:
        """Request cancellation on task exit; never claim unconfirmed termination."""
        with self._lock:
            pending = tuple(self._pending.items())
        unconfirmed = []
        for key, (inspect, cancel) in pending:
            try:
                if inspect() is None:
                    cancel()
                    if inspect() is None:
                        unconfirmed.append(key)
            except Exception:
                unconfirmed.append(key)
        return unconfirmed
