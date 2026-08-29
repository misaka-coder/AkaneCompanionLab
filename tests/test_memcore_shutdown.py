from __future__ import annotations

from concurrent.futures import Future
import threading
import unittest

from companion_v01.memcore_integration.manager import MemcoreManager


class _Closable:
    def __init__(self) -> None:
        self.closed = False
        self.shutdown_requested = False

    def request_shutdown(self) -> None:
        self.shutdown_requested = True

    def close(self) -> None:
        self.closed = True


class MemcoreShutdownTests(unittest.TestCase):
    @staticmethod
    def _manager_with_running_future() -> tuple[MemcoreManager, Future, _Closable, _Closable]:
        manager = MemcoreManager.__new__(MemcoreManager)
        manager._lock = threading.RLock()
        manager._shutdown_event = threading.Event()
        manager._closing = False
        manager._closed = False
        manager._close_finalizing = False
        manager._deferred_close = False
        manager._available = True
        manager._systems = {}
        manager._background_futures = set()
        manager._background_compactions = {}
        manager._pending_compactions = {}
        manager._compaction_retry_after = {}
        manager._index = object()
        manager._runtime = None
        manager._uses_process_runtime = False

        system = _Closable()
        store = _Closable()
        manager._systems[("tenant", "user", "conversation")] = system
        manager._store = store

        future: Future = Future()
        self_started = future.set_running_or_notify_cancel()
        if not self_started:  # pragma: no cover - a fresh Future always starts
            raise AssertionError("future did not enter running state")
        with manager._lock:
            manager._track_background_future_locked(future)
        return manager, future, system, store

    def test_close_deadline_defers_store_release_until_running_job_finishes(self) -> None:
        manager, future, system, store = self._manager_with_running_future()

        closed = manager.close(timeout=0.0)

        self.assertFalse(closed)
        self.assertTrue(system.shutdown_requested)
        self.assertFalse(store.closed)
        future.set_result({"status": "cancelled", "reason": "shutdown_requested"})
        self.assertTrue(store.closed)
        self.assertTrue(system.closed)
        self.assertTrue(manager._closed)


if __name__ == "__main__":
    unittest.main()
