from __future__ import annotations

from concurrent.futures import Future
import threading
import unittest

from companion_v01.engine import AkaneMemoryEngine
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
    def test_engine_shutdown_signals_execution_and_memcore_before_joining(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine._embedding_reindex_stop = threading.Event()
        execution = _Closable()
        memcore = _Closable()
        engine.execution_provider = execution
        engine.memcore_manager = memcore

        engine.request_shutdown()

        self.assertTrue(engine._embedding_reindex_stop.is_set())
        self.assertTrue(execution.shutdown_requested)
        self.assertTrue(memcore.shutdown_requested)

    @staticmethod
    def _manager_with_running_future() -> tuple[MemcoreManager, Future, _Closable, _Closable]:
        manager = MemcoreManager.__new__(MemcoreManager)
        manager._lock = threading.RLock()
        manager._jobs_condition = threading.Condition(manager._lock)
        manager._shutdown_event = threading.Event()
        manager._closing = False
        manager._closed = False
        manager._close_finalizing = False
        manager._deferred_close = False
        manager._available = True
        manager._systems = {}
        manager._session_llms = {}
        manager._session_token_limits = {}
        manager._index_warmup_futures = {}
        manager._compaction_chat_model_overrides = {("owner", "session", "character"): "new-model"}
        manager._compaction_turn_model_hints = {("owner", "session", "character"): "old-model"}
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
        self.assertEqual(manager._compaction_chat_model_overrides, {})
        self.assertEqual(manager._compaction_turn_model_hints, {})

    def test_done_future_does_not_close_store_before_completion_callback_finishes(self) -> None:
        manager, future, system, store = self._manager_with_running_future()
        entered, release = threading.Event(), threading.Event()
        # Replace the tracking callback with a controlled completion callback.
        # A Future becomes done before this callback has released its DB use.
        delayed = Future()
        delayed.set_running_or_notify_cancel()
        manager._background_futures = {delayed}
        def callback(completed):
            entered.set()
            release.wait(2)
            with manager._jobs_condition:
                manager._background_futures.discard(completed)
                manager._jobs_condition.notify_all()
            manager._finalize_deferred_close_if_ready()
        delayed.add_done_callback(callback)
        worker = threading.Thread(target=lambda: delayed.set_result({"status": "completed"}))
        worker.start()
        self.addCleanup(worker.join, 2)
        self.addCleanup(release.set)
        self.assertTrue(entered.wait(1))
        self.assertTrue(delayed.done())
        self.assertFalse(manager.close(timeout=0))
        self.assertFalse(store.closed)
        release.set()
        worker.join(2)
        self.assertTrue(store.closed)


if __name__ == "__main__":
    unittest.main()
