from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from companion_v01.durable_session_queue import DurableSessionWorkQueue
from companion_v01.session_inbox import SessionInboxStore
from tests.test_bot_runtime import _runtime
from tests import test_session_inbox as inbox_fixtures


class SessionQueueShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = SessionInboxStore(Path(self.folder.name) / "inbox.db")

    def fields(self, name="one", session="p\0s"):
        return inbox_fixtures.DurableSessionWorkQueueTests._fields(session_key=session, event_id=name)

    async def test_drain_preserves_queued_inputs_and_never_cancels_running_thread(self):
        started, release = threading.Event(), threading.Event()
        calls = []

        def work():
            started.set()
            release.wait(3)
            calls.append("finished")

        async def handler(_key, items):
            await queue.begin_processing(items)
            await asyncio.to_thread(work)

        queue = DurableSessionWorkQueue(self.store, handler)
        first = await queue.enqueue(**self.fields())
        await asyncio.to_thread(started.wait, 1)
        second = await queue.enqueue(**self.fields("two"))
        try:
            result = await queue.close(timeout=0.01)
            self.assertEqual(result["reason"], "session_queue_drain_timeout")
            self.assertEqual(self.store.get(first["item_id"]).status, "claimed")
            self.assertEqual((await queue.enqueue(**self.fields("late")))["status"], "stopping")
            self.assertEqual(await queue.recover(), 0)
            await queue.schedule_session("another")
            self.assertNotIn("another", queue._workers)
        finally:
            release.set()
        self.assertEqual((await queue.close(timeout=2))["status"], "stopped")
        self.assertEqual(calls, ["finished"])
        self.assertEqual(self.store.get(first["item_id"]).status, "committed")
        self.assertEqual(self.store.get(second["item_id"]).status, "queued")
        self.assertEqual((await queue.close(timeout=1))["status"], "stopped")

    async def test_close_wakes_long_deferred_head_without_claiming_it(self):
        fields = {**self.fields(), "available_at": 9999999999}
        queue = DurableSessionWorkQueue(self.store)
        item = await queue.enqueue(**fields)
        await asyncio.sleep(0.03)
        self.assertEqual((await queue.close(timeout=0.5))["status"], "stopped")
        self.assertEqual(self.store.get(item["item_id"]).status, "queued")

    async def test_claim_racing_shutdown_is_returned_without_running_handler(self):
        started, release = threading.Event(), threading.Event()
        original = self.store.claim_next
        calls = []

        def delayed(*args, **kwargs):
            started.set()
            release.wait(3)
            return original(*args, **kwargs)

        async def handler(*_args):
            calls.append("unexpected")

        queue = DurableSessionWorkQueue(self.store, handler)
        with patch.object(self.store, "claim_next", delayed):
            item = await queue.enqueue(**self.fields())
            await asyncio.to_thread(started.wait, 1)
            queue.request_shutdown()
            release.set()
            self.assertEqual((await queue.close(timeout=2))["status"], "stopped")
        self.assertEqual(calls, [])
        self.assertEqual(self.store.get(item["item_id"]).status, "queued")

    async def test_direct_steer_claim_is_part_of_shutdown_fence(self):
        queue = DurableSessionWorkQueue(self.store)
        item = await queue.enqueue(**self.fields(), schedule=False)
        claim = await queue.claim_for_active_turn("p\0s", item["item_id"])
        # A short shutdown budget may expire on the final DB read under load;
        # both outcomes must be fail-closed, with the steer still owned.
        self.assertEqual((await queue.close(timeout=0.3))["status"], "degraded")
        self.assertEqual(self.store.get(item["item_id"]).status, "claimed")
        await queue.commit_claim(item["item_id"], claim_token=claim["claim_token"])
        self.assertEqual((await queue.close(timeout=1))["status"], "stopped")
        self.assertEqual((await queue.claim_for_active_turn("p\0s", item["item_id"]))["status"], "stopping")

    async def test_cancelled_enqueue_waiter_does_not_hide_inflight_database_write(self):
        started, release = threading.Event(), threading.Event()
        original = self.store.enqueue

        def delayed(**kwargs):
            started.set()
            release.wait(3)
            return original(**kwargs)

        queue = DurableSessionWorkQueue(self.store)
        with patch.object(self.store, "enqueue", delayed):
            task = asyncio.create_task(queue.enqueue(**self.fields()))
            await asyncio.to_thread(started.wait, 1)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            try:
                self.assertEqual((await queue.close(timeout=0.01))["reason"], "session_queue_drain_timeout")
            finally:
                release.set()
            self.assertEqual((await queue.close(timeout=2))["status"], "stopped")
        self.assertEqual(self.store.pending_count("p\0s"), 1)

    async def test_runtime_does_not_close_dependencies_until_queue_drains(self):
        runtime, plugins, engine, followups = _runtime()
        started, release = asyncio.Event(), asyncio.Event()

        async def handler(*_args):
            started.set()
            await release.wait()
            self.assertEqual(engine.close_count, 0)
            self.assertEqual(plugins.stop_count, 0)

        queue = DurableSessionWorkQueue(self.store, handler)
        runtime.session_work_queue = queue
        await queue.enqueue(**self.fields())
        await started.wait()
        original_close = queue.close

        async def short_close(**_kwargs):
            return await original_close(timeout=0.01)

        with patch.object(queue, "close", short_close):
            result = await runtime.stop()
        self.assertEqual(result["reason"], "session_queue_shutdown_incomplete")
        self.assertEqual((engine.close_count, plugins.stop_count, followups.close_count), (0, 0, 0))
        release.set()
        result = await runtime.stop()
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["queue_status"]["owned_claims"], 0)
        self.assertEqual((engine.close_count, plugins.stop_count, followups.close_count), (1, 1, 1))

    async def test_registry_cancellation_does_not_cancel_queue_handler(self):
        runtime, _plugins, engine, _followups = _runtime()
        started, release = asyncio.Event(), asyncio.Event()

        async def handler(*_args):
            started.set()
            await release.wait()

        queue = DurableSessionWorkQueue(self.store, handler)
        runtime.session_work_queue = queue
        item = await queue.enqueue(**self.fields())
        await started.wait()
        task = asyncio.create_task(runtime.stop())
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(engine.close_count, 0)
        self.assertEqual(self.store.get(item["item_id"]).status, "claimed")
        release.set()
        self.assertEqual((await runtime.stop())["status"], "stopped")
        self.assertEqual(self.store.get(item["item_id"]).status, "committed")
