from __future__ import annotations

import asyncio
import unittest
from unittest.mock import Mock

from capcore import InvocationContext

from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_active_generation import (
    ActivePluginGeneration,
    PluginActiveGenerationError,
    PluginGenerationSnapshot,
)
from companion_v01.plugin_generation_runtime import PluginGenerationRuntime
from tests.test_plugin_active_generation import FakeGeneration
from tests.test_plugin_generation_runtime import _Builder


CAPABILITY_ID = "akane.test.boundary.read.v1"
PLUGIN_ID = "akane.test.boundary"


def _snapshot(*processes):
    return PluginGenerationSnapshot(
        tuple(PluginSelection(process.plugin_id, True) for process in processes),
        processes,
    )


class ServiceGeneration(FakeGeneration):
    def __init__(self, value, *, begin_error=None, end_error=None, **kwargs):
        super().__init__(
            kwargs.pop("plugin_id", PLUGIN_ID),
            kwargs.pop("capability_id", CAPABILITY_ID),
            value,
            **kwargs,
        )
        self.begin_error = begin_error
        self.end_error = end_error
        self.begin_count = 0
        self.end_count = 0
        self.scope_open = False

    def begin_service_invocation(self):
        self.begin_count += 1
        self.scope_open = True
        if self.begin_error is not None:
            raise self.begin_error

    async def end_service_invocation(self):
        self.end_count += 1
        if self.end_error is not None:
            raise self.end_error
        self.scope_open = False


class ActiveGenerationBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejected_retiring_candidate_does_not_revoke_active_tasks(self):
        old, new = ServiceGeneration("old"), ServiceGeneration("new")
        runtime = ActivePluginGeneration()
        listener = Mock()
        runtime.bind_task_revocation_listener(listener)
        await runtime.publish(_snapshot(old))
        scope = runtime.freeze_invocation_scope()
        try:
            await runtime.publish(_snapshot(new))
            listener.reset_mock()
            with self.assertRaisesRegex(
                PluginActiveGenerationError, "plugin_generation_already_retiring"
            ):
                await runtime.publish(_snapshot(old))
            listener.assert_not_called()
            result = await runtime.invoke(CAPABILITY_ID, {}, context=InvocationContext())
            self.assertEqual(result.content, {"generation": "new"})
            self.assertEqual(new.stop_count, 0)
        finally:
            scope.close()
            await runtime.stop()

    async def test_service_prepare_failure_preserves_old_call_and_grant(self):
        started, release = asyncio.Event(), asyncio.Event()
        old = ServiceGeneration("old", started=started, release=release)
        broken = ServiceGeneration(
            "broken", capability_id="akane.test.boundary.replacement.v1",
            begin_error=RuntimeError("service_prepare_failed"),
        )
        runtime = ActivePluginGeneration()
        task_listener, capability_listener = Mock(), Mock()
        runtime.bind_task_revocation_listener(task_listener)
        runtime.bind_capability_revocation_listener(capability_listener)
        await runtime.publish(_snapshot(old))
        binding = runtime.capture_capability_bindings()[CAPABILITY_ID]
        call = asyncio.create_task(
            runtime.invoke(CAPABILITY_ID, {}, context=InvocationContext())
        )
        await started.wait()
        try:
            with self.assertRaisesRegex(RuntimeError, "service_prepare_failed"):
                await runtime.publish(_snapshot(broken))
            self.assertTrue(binding.is_live(CAPABILITY_ID))
            task_listener.assert_not_called()
            capability_listener.assert_not_called()
            self.assertNotIn(id(broken), runtime._lifetimes)
            self.assertEqual(runtime.status_snapshot()["generation"], 1)
            self.assertEqual((broken.begin_count, broken.end_count), (1, 1))
            self.assertEqual(old.stop_count, 0)
        finally:
            release.set()
            result = await call
            await runtime.stop()
        self.assertEqual(result.content, {"generation": "old"})

    async def test_failed_batch_closes_all_new_scopes_and_keeps_existing_scope(self):
        old = ServiceGeneration("old")
        first = ServiceGeneration(
            "first", plugin_id="akane.test.first", capability_id="akane.test.first.read.v1"
        )
        broken = ServiceGeneration(
            "broken", plugin_id="akane.test.second", capability_id="akane.test.second.read.v1",
            begin_error=RuntimeError("second_prepare_failed"),
        )
        runtime = ActivePluginGeneration()
        await runtime.publish(_snapshot(old))
        try:
            with self.assertRaisesRegex(RuntimeError, "second_prepare_failed"):
                await runtime.publish(_snapshot(old, first, broken))
            self.assertEqual((old.begin_count, old.end_count), (1, 0))
            self.assertTrue(old.scope_open)
            for process in (first, broken):
                self.assertEqual((process.begin_count, process.end_count), (1, 1))
                self.assertFalse(process.scope_open)
                self.assertNotIn(id(process), runtime._lifetimes)
                self.assertEqual(process.stop_count, 0)
            self.assertEqual(runtime.capability_ids, (CAPABILITY_ID,))
        finally:
            await runtime.stop()

    async def test_cancelled_service_prepare_rolls_back_and_keeps_old_generation(self):
        old = ServiceGeneration("old")
        broken = ServiceGeneration("cancelled", begin_error=asyncio.CancelledError())
        runtime = ActivePluginGeneration()
        await runtime.publish(_snapshot(old))
        try:
            with self.assertRaises(asyncio.CancelledError):
                await runtime.publish(_snapshot(broken))
            self.assertFalse(broken.scope_open)
            self.assertNotIn(id(broken), runtime._lifetimes)
            self.assertEqual(runtime.status_snapshot()["generation"], 1)
            result = await runtime.invoke(CAPABILITY_ID, {}, context=InvocationContext())
            self.assertEqual(result.content, {"generation": "old"})
        finally:
            await runtime.stop()

    async def test_failed_prepare_can_retry_after_successful_rollback(self):
        old = ServiceGeneration("old")
        candidate = ServiceGeneration("retry", begin_error=RuntimeError("prepare_failure"))
        runtime = ActivePluginGeneration()
        await runtime.publish(_snapshot(old))
        try:
            with self.assertRaisesRegex(RuntimeError, "prepare_failure"):
                await runtime.publish(_snapshot(candidate))
            candidate.begin_error = None
            status = await runtime.publish(_snapshot(candidate))
            self.assertTrue(status["ok"])
            self.assertEqual(status["generation"], 2)
            self.assertEqual((candidate.begin_count, candidate.end_count), (2, 1))
            result = await runtime.invoke(CAPABILITY_ID, {}, context=InvocationContext())
            self.assertEqual(result.content, {"generation": "retry"})
        finally:
            await runtime.stop()
        self.assertEqual((candidate.stop_count, candidate.end_count), (1, 2))

    async def test_worker_exiting_during_service_prepare_does_not_replace_active(self):
        old, candidate = ServiceGeneration("old"), ServiceGeneration("exiting")
        begin = candidate.begin_service_invocation

        def exiting_begin():
            begin()
            candidate.running = False

        candidate.begin_service_invocation = exiting_begin
        runtime = ActivePluginGeneration()
        await runtime.publish(_snapshot(old))
        try:
            with self.assertRaisesRegex(PluginActiveGenerationError, "plugin_generation_not_ready"):
                await runtime.publish(_snapshot(candidate))
            self.assertEqual(runtime.status_snapshot()["generation"], 1)
            self.assertFalse(candidate.scope_open)
            result = await runtime.invoke(CAPABILITY_ID, {}, context=InvocationContext())
            self.assertEqual(result.content, {"generation": "old"})
        finally:
            await runtime.stop()

    async def test_cancelling_slow_rollback_keeps_next_publish_serialized(self):
        closing, release = asyncio.Event(), asyncio.Event()

        class SlowCleanupGeneration(ServiceGeneration):
            async def end_service_invocation(self):
                closing.set()
                await release.wait()
                await super().end_service_invocation()

        old, new = ServiceGeneration("old"), ServiceGeneration("new")
        broken = SlowCleanupGeneration("broken", begin_error=RuntimeError("prepare_failure"))
        runtime = ActivePluginGeneration()
        await runtime.publish(_snapshot(old))
        rejected = asyncio.create_task(runtime.publish(_snapshot(broken)))
        queued = None
        try:
            await asyncio.wait_for(closing.wait(), timeout=2)
            rejected.cancel()
            queued = asyncio.create_task(runtime.publish(_snapshot(new)))
            await asyncio.sleep(0)
            self.assertFalse(queued.done())
            self.assertEqual(runtime.status_snapshot()["generation"], 1)
            self.assertTrue(runtime.capture_capability_bindings()[CAPABILITY_ID].is_live(CAPABILITY_ID))
            release.set()
            # Cancellation drains rollback; the existing drain contract still
            # propagates the original candidate failure once cleanup finishes.
            with self.assertRaisesRegex(RuntimeError, "prepare_failure"):
                await rejected
            self.assertEqual((await queued)["generation"], 2)
            self.assertFalse(broken.scope_open)
            self.assertEqual(broken.end_count, 1)
        finally:
            release.set()
            await asyncio.gather(rejected, *([queued] if queued is not None else []), return_exceptions=True)
            await runtime.stop()

    async def test_rollback_close_failure_retains_ownership_and_original_error(self):
        old = ServiceGeneration("old")
        broken = ServiceGeneration(
            "broken", begin_error=RuntimeError("original_prepare_failure"),
            end_error=ValueError("close_failure"),
        )
        runtime = ActivePluginGeneration()
        await runtime.publish(_snapshot(old))
        try:
            with self.assertRaisesRegex(RuntimeError, "original_prepare_failure"):
                await runtime.publish(_snapshot(broken))
            status = runtime.status_snapshot()
            self.assertEqual(status["cleanup_status"], "failed")
            self.assertEqual(status["cleanup_failures"], [{
                "plugin_id": PLUGIN_ID, "reason": "plugin_service_scope_close_failed"
            }])
            self.assertIn(id(broken), runtime._lifetimes)
            self.assertTrue(runtime._lifetimes[id(broken)].service_scope_open)
            self.assertTrue(runtime.capture_capability_bindings()[CAPABILITY_ID].is_live(CAPABILITY_ID))
            broken.begin_error = None
            with self.assertRaisesRegex(
                PluginActiveGenerationError, "plugin_generation_already_retiring"
            ):
                await runtime.publish(_snapshot(broken))
        finally:
            broken.end_error = None
            stopped = await runtime.stop()
        self.assertEqual(stopped["cleanup_status"], "completed")
        self.assertEqual(broken.stop_count, 1)
        self.assertFalse(broken.scope_open)

    async def test_retired_service_close_failure_is_visible_and_retained(self):
        old = ServiceGeneration("old", end_error=RuntimeError("close_failure"))
        new = ServiceGeneration("new")
        runtime = ActivePluginGeneration()
        await runtime.publish(_snapshot(old))
        try:
            status = await runtime.publish(_snapshot(new))
            self.assertFalse(status["ok"])
            self.assertEqual(status["cleanup_status"], "failed")
            self.assertEqual(status["cleanup_failures"], [{
                "plugin_id": PLUGIN_ID, "reason": "plugin_service_scope_close_failed"
            }])
            self.assertIn(id(old), runtime._lifetimes)
            self.assertTrue(runtime._lifetimes[id(old)].service_scope_open)
            self.assertEqual((old.stop_count, old.end_count), (1, 1))
            result = await runtime.invoke(CAPABILITY_ID, {}, context=InvocationContext())
            self.assertEqual(result.content, {"generation": "new"})
        finally:
            stopped = await runtime.stop()
        self.assertEqual(stopped["cleanup_status"], "failed")
        self.assertEqual((new.stop_count, new.end_count), (1, 1))

    async def test_stop_claiming_success_with_degraded_status_is_not_complete(self):
        old = ServiceGeneration("old", stop_result={"ok": True, "status": "degraded"})
        new = ServiceGeneration("new")
        runtime = ActivePluginGeneration()
        await runtime.publish(_snapshot(old))
        try:
            status = await runtime.publish(_snapshot(new))
            self.assertEqual(status["cleanup_status"], "failed")
            self.assertEqual(status["cleanup_failures"][0]["reason"],
                             "plugin_generation_stop_incomplete")
            self.assertIn(id(old), runtime._lifetimes)
            self.assertEqual(old.end_count, 1)
        finally:
            await runtime.stop()

    async def test_stop_claiming_stopped_while_running_keeps_installer_retention(self):
        old = ServiceGeneration("old")
        old.site_dir = "old-site"

        def incomplete_stop():
            old.stop_count += 1
            return {"ok": True, "status": "stopped"}

        old.stop = incomplete_stop
        runtime = ActivePluginGeneration()
        await runtime.publish(_snapshot(old))
        try:
            status = await runtime.publish(_snapshot(ServiceGeneration("new")))
            self.assertEqual(status["cleanup_status"], "failed")
            self.assertEqual(status["cleanup_failures"][0]["reason"],
                             "plugin_generation_stop_incomplete")
            self.assertIn("old-site", runtime.retained_site_dirs())
            self.assertEqual(old.end_count, 1)
        finally:
            await runtime.stop()

    async def test_same_process_republication_opens_one_service_scope(self):
        process = ServiceGeneration("same")
        runtime = ActivePluginGeneration()
        await runtime.publish(_snapshot(process))
        await runtime.publish(_snapshot(process))
        self.assertEqual((process.begin_count, process.end_count, process.stop_count), (1, 0, 0))
        stopped = await runtime.stop()
        self.assertEqual((process.begin_count, process.end_count, process.stop_count), (1, 1, 1))
        self.assertEqual(stopped["cleanup_status"], "completed")

    async def test_forked_scope_keeps_old_service_until_last_consumer_closes(self):
        old, new = ServiceGeneration("old"), ServiceGeneration("new")
        runtime = ActivePluginGeneration()
        await runtime.publish(_snapshot(old))
        parent = runtime.freeze_invocation_scope()
        child = parent.fork()
        try:
            switched = await runtime.publish(_snapshot(new))
            parent.close()
            self.assertEqual(switched["cleanup_status"], "draining")
            self.assertEqual((old.stop_count, old.end_count), (0, 0))
            with child.activate():
                result = await runtime.invoke(CAPABILITY_ID, {}, context=InvocationContext())
            self.assertEqual(result.content, {"generation": "old"})
        finally:
            parent.close()
            child.close()
            await runtime.drain_retired()
            await runtime.stop()
        self.assertEqual((old.stop_count, old.end_count), (1, 1))

    async def test_outer_runtime_failed_publish_keeps_actual_previous_route(self):
        old = ServiceGeneration("old")
        broken = ServiceGeneration(
            "broken", capability_id="akane.test.boundary.replacement.v1",
            begin_error=RuntimeError("service_prepare_failed"),
        )
        runtime = PluginGenerationRuntime(
            (PluginSelection(PLUGIN_ID, True),), candidate_builder=_Builder([old, broken])
        )
        await runtime.start()
        try:
            result = await runtime.restart()
            self.assertFalse(result["published"])
            self.assertEqual(result["reason"], "plugin_generation_publish_failed")
            self.assertEqual(result["active_generation"], 1)
            previous = await runtime.invoke(CAPABILITY_ID, {}, context=InvocationContext())
            self.assertEqual(previous.content, {"generation": "old"})
            self.assertEqual(old.stop_count, 0)
            self.assertEqual(broken.stop_count, 1)
            self.assertFalse(broken.scope_open)
        finally:
            await runtime.stop()


if __name__ == "__main__":
    unittest.main()
