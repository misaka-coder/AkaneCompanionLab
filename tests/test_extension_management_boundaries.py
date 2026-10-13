from __future__ import annotations

import asyncio
import inspect
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from companion_v01.extension_management import ExtensionManagementService, PluginSelectionStore
from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_installation import PluginInstallationError


PLUGIN_ID = "akane.test.management-boundary"
STAGE_ID = "b" * 32


class ControlledRuntime:
    """Inject publication delays/failures through the real management port."""

    runtime_loop = None
    code_reload_mode = "atomic_generation_switch"

    def __init__(self, selections=()):
        self.selections = tuple(selections)
        self.calls = []
        self.steps = []

    def status_snapshot(self):
        return {
            "ok": True,
            "status": "active",
            "plugins": [
                {
                    "plugin_id": item.plugin_id,
                    "enabled": item.enabled,
                    "status": "active" if item.enabled else "disabled",
                }
                for item in self.selections
            ],
        }

    async def reconfigure(self, selections):
        self.calls.append(tuple(selections))
        step = self.steps.pop(0) if self.steps else None
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            step = await step()
        if isinstance(step, dict):
            if step.get("published") is True:
                self.selections = tuple(selections)
            return dict(step)
        self.selections = tuple(selections)
        return {**self.status_snapshot(), "published": True}


class ExtensionManagementBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tasks = []
        self.releases = []
        self.configure()

    async def asyncTearDown(self):
        for release in self.releases:
            release.set()
        for task in self.tasks:
            if not task.done():
                task.cancel()
        if self.tasks:
            await asyncio.wait_for(asyncio.gather(*self.tasks, return_exceptions=True), 5)

    def configure(self, previous=()):
        self.store = PluginSelectionStore(
            Path(self.temp.name) / "plugin-selections.json", defaults=(), instance_id="boundary-test"
        )
        self.store.save(previous)
        self.runtime = ControlledRuntime(previous)
        self.installed = {item.plugin_id for item in previous}
        self.artifacts = Mock()
        self.artifacts.snapshot.side_effect = lambda: {
            "plugins": [{"plugin_id": item} for item in self.installed],
            "stages": [],
        }
        self.artifacts.reconcile_runtime.return_value = {"status": "ready", "reload_required": False}
        self.artifacts.publish_stage.side_effect = self.publish
        self.artifacts.remove_plugin.side_effect = self.remove
        self.artifacts.rollback_to_last_good.return_value = {"ok": True, "activation_pending": True}
        self.artifacts.discard_stage.return_value = {"ok": True, "status": "discarded", "stage_id": STAGE_ID}
        self.service = ExtensionManagementService(
            plugin_runtime=self.runtime, selection_store=self.store, artifact_store=self.artifacts
        )

    def publish(self, _stage_id, *, approved_permissions):
        self.installed.add(PLUGIN_ID)
        return {"ok": True, "status": "installed", "plugin_id": PLUGIN_ID, "permissions": []}

    def remove(self, plugin_id, **_retention):
        self.installed.discard(plugin_id)
        return {"ok": True, "status": "removed", "plugin_id": plugin_id}

    def start(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.append(task)
        return task

    def block_thread(self, operation):
        started, release = threading.Event(), threading.Event()
        self.releases.append(release)

        def blocked(*args, **kwargs):
            started.set()
            if not release.wait(5):
                raise RuntimeError("fixture_worker_not_released")
            return operation(*args, **kwargs)

        return started, release, blocked

    async def tick(self):
        # Deliver cancellation and let a queued management request reach its lock.
        for _ in range(4):
            await asyncio.sleep(0)

    async def test_queued_restart_reads_the_latest_persisted_selection(self):
        self.configure((PluginSelection(PLUGIN_ID, True),))
        await self.service._operation_lock.acquire()
        try:
            pending = self.start(self.service.restart(requested_plugin_id=PLUGIN_ID))
            await self.tick()
            latest = (PluginSelection(PLUGIN_ID, False),)
            self.store.save(latest)
            self.runtime.selections = latest
        finally:
            self.service._operation_lock.release()
        result = await pending
        self.assertTrue(result["ok"])
        self.assertEqual(self.runtime.calls, [latest])
        self.assertEqual(self.runtime.selections, self.store.load())

    async def test_queued_uninstall_observes_the_preceding_install(self):
        await self.service._operation_lock.acquire()
        try:
            pending = self.start(self.service.uninstall(plugin_id=PLUGIN_ID))
            await self.tick()
            self.installed.add(PLUGIN_ID)
            self.store.save((PluginSelection(PLUGIN_ID, True),))
            self.runtime.selections = self.store.load()
        finally:
            self.service._operation_lock.release()
        result = await pending
        self.assertTrue(result["ok"])
        self.assertEqual(self.store.load(), ())
        self.assertEqual(self.runtime.selections, ())
        self.assertNotIn(PLUGIN_ID, self.installed)

    async def test_cancelled_queued_mutation_does_not_start(self):
        await self.service._operation_lock.acquire()
        try:
            task = self.start(self.service.discard_stage(stage_id=STAGE_ID))
            await self.tick()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            self.service._operation_lock.release()
        self.artifacts.discard_stage.assert_not_called()

    async def test_cancelled_publish_finishes_activation_and_persistence_before_unlock(self):
        started, release, blocked = self.block_thread(self.publish)
        self.artifacts.publish_stage.side_effect = blocked
        installing = self.start(self.service.install_stage(stage_id=STAGE_ID, approved_permissions=()))
        self.assertTrue(await asyncio.to_thread(started.wait, 2))
        installing.cancel()
        await self.tick()
        installing.cancel()
        queued = self.start(self.service.restart())
        await self.tick()
        self.assertTrue(self.service._operation_lock.locked())
        self.assertFalse(installing.done())
        self.assertFalse(queued.done())
        self.assertEqual(self.runtime.calls, [])
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await installing
        self.assertTrue((await queued)["ok"])
        self.assertEqual(self.store.load(), (PluginSelection(PLUGIN_ID, True),))
        self.assertEqual(self.runtime.selections, self.store.load())
        self.assertIn(PLUGIN_ID, self.installed)

    async def test_cancelled_activation_finishes_before_following_disable(self):
        started, release = asyncio.Event(), asyncio.Event()
        self.releases.append(release)

        async def activate():
            started.set()
            await release.wait()

        self.runtime.steps = [activate]
        installing = self.start(self.service.install_stage(stage_id=STAGE_ID, approved_permissions=()))
        await asyncio.wait_for(started.wait(), 2)
        installing.cancel()
        queued = self.start(self.service.set_enabled(plugin_id=PLUGIN_ID, enabled=False))
        await self.tick()
        self.assertFalse(queued.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await installing
        self.assertTrue((await queued)["ok"])
        self.assertEqual(self.store.load(), (PluginSelection(PLUGIN_ID, False),))
        self.assertEqual(self.runtime.selections, self.store.load())

    async def assert_cancelled_thread_keeps_lock(self, coroutine, method_name, operation):
        started, release, blocked = self.block_thread(operation)
        getattr(self.artifacts, method_name).side_effect = blocked
        pending = self.start(coroutine)
        self.assertTrue(await asyncio.to_thread(started.wait, 2))
        pending.cancel()
        await self.tick()
        queued = self.start(self.service.restart())
        await self.tick()
        self.assertTrue(self.service._operation_lock.locked())
        self.assertFalse(pending.done())
        self.assertFalse(queued.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertTrue((await queued)["ok"])

    async def test_cancelled_discard_waits_for_its_worker(self):
        await self.assert_cancelled_thread_keeps_lock(
            self.service.discard_stage(stage_id=STAGE_ID), "discard_stage", lambda *_: {"ok": True}
        )

    async def test_cancelled_rollback_waits_for_its_worker(self):
        await self.assert_cancelled_thread_keeps_lock(
            self.service.rollback(plugin_id=PLUGIN_ID),
            "rollback_to_last_good",
            lambda *_: {"ok": True, "activation_pending": True},
        )

    async def test_cancelled_uninstall_finishes_removal_and_live_selection(self):
        self.configure((PluginSelection(PLUGIN_ID, True),))
        await self.assert_cancelled_thread_keeps_lock(
            self.service.uninstall(plugin_id=PLUGIN_ID), "remove_plugin", self.remove
        )
        self.assertEqual(self.store.load(), ())
        self.assertEqual(self.runtime.selections, ())
        self.assertNotIn(PLUGIN_ID, self.installed)

    async def test_cancelled_install_restoration_finishes_before_unlock(self):
        saved = self.store.save
        attempts = 0

        def fail_once(selections):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("fixture_persist_failed")
            return saved(selections)

        self.store.save = fail_once
        await self.assert_cancelled_thread_keeps_lock(
            self.service.install_stage(stage_id=STAGE_ID, approved_permissions=()), "remove_plugin", self.remove
        )
        self.assertEqual(self.store.load(), ())
        self.assertEqual(self.runtime.selections, ())
        self.assertNotIn(PLUGIN_ID, self.installed)

    async def cancelled_stage(self, result, *, market=False):
        started, release, blocked = self.block_thread(lambda *_args, **_kwargs: result)
        if market:
            self.service.market = Mock()
            self.service.market.stage.side_effect = blocked
            task = self.start(self.service.stage_market(plugin_id=PLUGIN_ID, digest="a" * 64))
        else:
            self.artifacts.stage_wheel.side_effect = blocked
            task = self.start(self.service.stage_wheel(wheel_path="fixture.whl"))
        self.assertTrue(await asyncio.to_thread(started.wait, 2))
        task.cancel()
        release.set()
        with self.assertRaises(asyncio.CancelledError) as caught:
            await task
        return caught.exception

    async def test_cancelled_market_failure_without_a_stage_id_remains_cancellation(self):
        await self.cancelled_stage({"ok": False, "status": "unavailable"}, market=True)
        self.artifacts.discard_stage.assert_not_called()

    async def test_cancelled_stage_cleanup_runs_off_the_event_loop(self):
        cleanup_threads = []
        self.artifacts.discard_stage.side_effect = lambda *_: (
            cleanup_threads.append(threading.get_ident()) or {"ok": True}
        )
        await self.cancelled_stage({"ok": True, "stage_id": STAGE_ID})
        self.assertEqual(len(cleanup_threads), 1)
        self.assertNotEqual(cleanup_threads[0], threading.get_ident())

    async def test_cancelled_stage_cleanup_failure_preserves_cancellation_and_reason(self):
        self.artifacts.discard_stage.side_effect = OSError("fixture_cleanup_failed")
        error = await self.cancelled_stage({"ok": True, "stage_id": STAGE_ID})
        self.assertEqual(str(error), "plugin_stage_discard_failed")

    async def test_install_activation_exception_restores_artifacts_and_selections(self):
        self.runtime.steps = [RuntimeError("fixture_activation_failed")]
        result = await self.service.install_stage(stage_id=STAGE_ID, approved_permissions=())
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "activation_failed")
        self.assertEqual(result["reason"], "plugin_runtime_reconfigure_failed")
        self.assertEqual(self.store.load(), ())
        self.assertEqual(self.runtime.selections, ())
        self.assertNotIn(PLUGIN_ID, self.installed)
        self.artifacts.remove_plugin.assert_called_once_with(PLUGIN_ID)

    def bad_restore(self):
        return {"ok": False, "status": "candidate_rejected", "published": False, "plugins": []}

    def bad_activation(self):
        return {
            "ok": False,
            "status": "degraded",
            "published": True,
            "plugins": [{"plugin_id": PLUGIN_ID, "status": "unavailable"}],
        }

    async def test_enable_activation_rollback_rejection_is_reported(self):
        previous = (PluginSelection(PLUGIN_ID, False),)
        self.configure(previous)
        self.runtime.steps = [self.bad_activation(), self.bad_restore()]
        result = await self.service.set_enabled(plugin_id=PLUGIN_ID, enabled=True)
        self.assertEqual(result["status"], "rollback_failed")
        self.assertEqual(result["reason"], "plugin_state_rollback_failed")
        self.assertEqual(self.store.load(), previous)

    async def test_enable_persistence_rollback_rejection_is_reported(self):
        self.configure((PluginSelection(PLUGIN_ID, False),))
        self.runtime.steps = [None, self.bad_restore()]
        self.store.save = Mock(side_effect=OSError("fixture_persist_failed"))
        result = await self.service.set_enabled(plugin_id=PLUGIN_ID, enabled=True)
        self.assertEqual(result["status"], "rollback_failed")
        self.assertEqual(result["reason"], "plugin_state_rollback_failed")

    async def test_enable_activation_exception_returns_a_restored_failure(self):
        previous = (PluginSelection(PLUGIN_ID, False),)
        self.configure(previous)
        self.runtime.steps = [RuntimeError("fixture_activation_failed")]
        result = await self.service.set_enabled(plugin_id=PLUGIN_ID, enabled=True)
        self.assertEqual(result["status"], "activation_failed")
        self.assertEqual(result["reason"], "plugin_runtime_reconfigure_failed")
        self.assertEqual(self.runtime.selections, previous)

    async def test_install_restore_requires_the_original_live_plugin_set(self):
        for restore in (
            {"ok": False, "status": "degraded", "published": True, "plugins": []},
            {
                "ok": True,
                "status": "active",
                "published": True,
                "plugins": [{"plugin_id": PLUGIN_ID, "status": "active"}],
            },
        ):
            with self.subTest(restore_status=restore["status"]):
                self.configure()
                self.runtime.steps = [None, restore]
                self.store.save = Mock(side_effect=[OSError("fixture_persist_failed"), None])
                result = await self.service.install_stage(stage_id=STAGE_ID, approved_permissions=())
                self.assertEqual(result["status"], "rollback_failed")
                self.assertEqual(result["reason"], "plugin_install_rollback_failed")

    async def test_uninstall_deactivation_rollback_rejection_is_reported(self):
        self.configure((PluginSelection(PLUGIN_ID, True),))
        self.runtime.steps = [self.bad_activation(), self.bad_restore()]
        result = await self.service.uninstall(plugin_id=PLUGIN_ID)
        self.assertEqual(result["status"], "rollback_failed")
        self.assertEqual(result["reason"], "plugin_uninstall_rollback_failed")
        self.artifacts.remove_plugin.assert_not_called()

    async def test_uninstall_persistence_rollback_rejection_is_reported(self):
        self.configure((PluginSelection(PLUGIN_ID, True),))
        self.runtime.steps = [None, self.bad_restore()]
        self.store.save = Mock(side_effect=OSError("fixture_persist_failed"))
        result = await self.service.uninstall(plugin_id=PLUGIN_ID)
        self.assertEqual(result["status"], "rollback_failed")
        self.assertEqual(result["reason"], "plugin_uninstall_rollback_failed")
        self.artifacts.remove_plugin.assert_not_called()

    async def test_uninstall_removal_rollback_rejection_is_reported(self):
        for error in (OSError("fixture_remove_failed"), PluginInstallationError("fixture_remove_failed")):
            with self.subTest(error_type=type(error).__name__):
                previous = (PluginSelection(PLUGIN_ID, True),)
                self.configure(previous)
                self.runtime.steps = [None, self.bad_restore()]
                self.artifacts.remove_plugin.side_effect = error
                result = await self.service.uninstall(plugin_id=PLUGIN_ID)
                self.assertEqual(result["status"], "rollback_failed")
                self.assertEqual(result["reason"], "plugin_uninstall_rollback_failed")
                self.assertEqual(self.store.load(), previous)

    async def test_uninstall_final_runtime_exception_reports_committed_removal(self):
        self.configure((PluginSelection(PLUGIN_ID, True),))
        self.runtime.steps = [None, RuntimeError("fixture_final_reconfigure_failed")]
        result = await self.service.uninstall(plugin_id=PLUGIN_ID)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "removed_runtime_degraded")
        self.assertTrue(result["reload_required"])
        self.assertEqual(self.store.load(), ())
        self.assertNotIn(PLUGIN_ID, self.installed)

    async def test_rejected_candidate_preserves_the_existing_generation(self):
        previous = (PluginSelection(PLUGIN_ID, False),)
        self.configure(previous)
        self.runtime.steps = [self.bad_restore()]
        result = await self.service.set_enabled(plugin_id=PLUGIN_ID, enabled=True)
        self.assertEqual(result["status"], "activation_failed")
        self.assertEqual(result["rollback_status"], "not_required")
        self.assertEqual(self.runtime.calls, [(PluginSelection(PLUGIN_ID, True),)])
        self.assertEqual(self.runtime.selections, previous)

    async def test_enabled_selection_with_a_failed_live_target_is_retried(self):
        self.configure((PluginSelection(PLUGIN_ID, True),))
        healthy = self.runtime.status_snapshot
        failed = {"ok": False, "status": "degraded", "plugins": [{"plugin_id": PLUGIN_ID, "status": "unavailable"}]}
        self.runtime.status_snapshot = Mock(side_effect=lambda: healthy() if self.runtime.calls else failed)
        result = await self.service.set_enabled(plugin_id=PLUGIN_ID, enabled=True)
        self.assertTrue(result["ok"])
        self.assertFalse(result["unchanged"])
        self.assertEqual(self.runtime.calls, [(PluginSelection(PLUGIN_ID, True),)])

    async def test_unchanged_waiting_dependency_keeps_its_real_status(self):
        self.configure((PluginSelection(PLUGIN_ID, True),))
        waiting = {
            "ok": True,
            "status": "active",
            "plugins": [{"plugin_id": PLUGIN_ID, "status": "waiting_dependency"}],
        }
        self.runtime.status_snapshot = Mock(return_value=waiting)
        result = await self.service.set_enabled(plugin_id=PLUGIN_ID, enabled=True)
        self.assertTrue(result["ok"])
        self.assertTrue(result["unchanged"])
        self.assertEqual(result["status"], "waiting_dependency")
        self.assertEqual(self.runtime.calls, [])

    async def test_sync_management_rejects_the_runtime_loop_thread(self):
        self.runtime.runtime_loop = asyncio.get_running_loop()
        result = self.service.execute_sync(action="discard_stage", stage_id=STAGE_ID)
        self.assertEqual(result["reason"], "plugin_management_requires_worker_thread")
        self.artifacts.discard_stage.assert_not_called()


class ExtensionManagementSubmissionBoundaryTests(unittest.TestCase):
    def test_closing_runtime_loop_does_not_leak_an_unscheduled_coroutine(self):
        runtime = ControlledRuntime()
        runtime.runtime_loop = Mock(is_running=Mock(return_value=True))
        service = ExtensionManagementService(plugin_runtime=runtime, selection_store=Mock(), artifact_store=Mock())
        submitted = []

        def closed_loop(coroutine, _loop):
            submitted.append(coroutine)
            raise RuntimeError("fixture_loop_closed")

        try:
            with patch.object(asyncio, "run_coroutine_threadsafe", side_effect=closed_loop):
                result = service.execute_sync(action="discard_stage", stage_id=STAGE_ID)
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["reason"], "plugin_runtime_loop_unavailable")
            self.assertEqual(inspect.getcoroutinestate(submitted[0]), inspect.CORO_CLOSED)
        finally:
            for coroutine in submitted:
                coroutine.close()


class ExtensionManagementInstalledBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_publication_reaches_a_really_callable_installed_worker(self):
        import hashlib
        import json

        from capcore import InvocationContext
        from companion_v01.plugin_generation_candidate import PluginGenerationCandidateBuilder
        from companion_v01.plugin_generation_runtime import PluginGenerationRuntime
        from companion_v01.plugin_installation import ManagedPluginArtifactStore, StagedPluginArtifact

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_root = Path(__file__).resolve().parents[1]
            artifacts = ManagedPluginArtifactStore(root / "artifacts", instance_id="settle", project_root=project_root)
            selections = PluginSelectionStore(root / "selections.json", defaults=(), instance_id="settle")
            stage = StagedPluginArtifact(
                STAGE_ID,
                PLUGIN_ID,
                "management-fixture",
                "1.0.0",
                hashlib.sha256(b"management-fixture").hexdigest(),
                ("capability.prompt.invoke",),
                {},
            )
            stage_dir = artifacts.staging_root / stage.stage_id
            site = stage_dir / "site"
            info = site / "management_fixture-1.0.0.dist-info"
            info.mkdir(parents=True)
            (info / "METADATA").write_text("Name: management-fixture\nVersion: 1.0.0\n", encoding="utf-8")
            (info / "entry_points.txt").write_text(
                f"[akane.plugins.v1]\n{PLUGIN_ID} = management_fixture:create_plugin\n", encoding="utf-8"
            )
            (site / "management_fixture.py").write_text(
                "from akane_plugin import Plugin\n"
                f"plugin = Plugin({PLUGIN_ID!r}, version='1.0.0')\n"
                "@plugin.tool\ndef ping() -> str: return 'settled'\n"
                "def create_plugin(): return plugin\n",
                encoding="utf-8",
            )
            (stage_dir / "stage.json").write_text(json.dumps(stage.as_dict()), encoding="utf-8")
            runtime = PluginGenerationRuntime(
                (),
                candidate_builder=PluginGenerationCandidateBuilder(
                    source_resolver=artifacts, project_root=project_root, work_root=root / "workers"
                ),
            )
            service = ExtensionManagementService(
                plugin_runtime=runtime, selection_store=selections, artifact_store=artifacts
            )
            started, release, finished = threading.Event(), threading.Event(), threading.Event()
            publish = artifacts.publish_stage

            def blocked(*args, **kwargs):
                started.set()
                try:
                    if not release.wait(5):
                        raise RuntimeError("fixture_worker_not_released")
                    return publish(*args, **kwargs)
                finally:
                    finished.set()

            await runtime.start()
            pending = None
            try:
                with patch.object(artifacts, "publish_stage", side_effect=blocked):
                    pending = asyncio.create_task(
                        service.install_stage(stage_id=stage.stage_id, approved_permissions=stage.permissions)
                    )
                    self.assertTrue(await asyncio.to_thread(started.wait, 2))
                    pending.cancel()
                    release.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await pending
                    self.assertTrue(await asyncio.to_thread(finished.wait, 2))
                self.assertEqual(selections.load(), (PluginSelection(PLUGIN_ID, True),))
                self.assertEqual(runtime.selections, selections.load())
                result = await service.invoke_capability(
                    PLUGIN_ID + ".ping", {}, context=InvocationContext("owner", "session", "plugin_admin")
                )
                self.assertFalse(result.is_error)
                self.assertEqual(result.value, "settled")
                self.assertFalse(artifacts.snapshot()["plugins"][0]["pending_activation"])
            finally:
                release.set()
                if pending is not None:
                    await asyncio.gather(pending, return_exceptions=True)
                    await asyncio.to_thread(finished.wait, 5)
                await runtime.stop()
