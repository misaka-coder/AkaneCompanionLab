"""Frozen callers cannot acquire fresh authority by waiting for re-enable."""
from __future__ import annotations

import asyncio
import tempfile
import threading
import textwrap
import unittest
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

from capcore import CapabilityResult, InvocationContext

from companion_v01.background_tasks import BackgroundTaskRunner
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.host_jobs import HostJobOwner, HostJobStore
from companion_v01.host_tool_jobs import HostToolJobRuntime
from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_active_generation import ActivePluginGeneration, PluginGenerationSnapshot
from companion_v01.plugin_tool_bridge import PluginCapabilityToolBridge
from companion_v01.tool_continuation import job_followup
from tests.test_host_tool_jobs import _Engine, _context
from tests.test_plugin_active_generation import FakeGeneration


class _Process(FakeGeneration):
    def __init__(self, version):
        super().__init__("example.revocation", "example.revocation.check", version)
        descriptor = self.capability_descriptors["example.revocation.check"]
        self.capability_descriptors = MappingProxyType({descriptor.id: replace(descriptor,
            visible_in=("base", "qq", "desktop"), raw={"execution_class": "long_task", "followup": "none"})})
        self.calls = []

    async def invoke(self, capability_id, args, *, context):
        self.calls.append((capability_id, args))
        return await super().invoke(capability_id, args, context=context)


class CapabilityRevocationTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_revocation_recording_does_not_revalidate_old_binding_on_retry(self):
        old, new = _Process("old"), _Process("new")
        selection = (PluginSelection(old.plugin_id, True),)
        runtime = ActivePluginGeneration()
        await runtime.publish(PluginGenerationSnapshot(selection, (old,)))
        binding = runtime.capture_capability_bindings()["example.revocation.check"]
        def unavailable_store(_capabilities):
            raise RuntimeError("test_revocation_recording_failed")
        runtime.bind_capability_revocation_listener(unavailable_store)
        try:
            with self.assertRaisesRegex(RuntimeError, "test_revocation_recording_failed"):
                await runtime.publish(PluginGenerationSnapshot((), ()))
            self.assertFalse(binding.is_live("example.revocation.check"))
            revoked = []
            runtime.bind_capability_revocation_listener(revoked.append)
            await runtime.publish(PluginGenerationSnapshot(selection, (new,)))
            self.assertEqual(revoked, [("example.revocation.check",)])
            self.assertFalse(binding.is_live("example.revocation.check"))
            fresh = runtime.capture_capability_bindings()["example.revocation.check"]
            result = await fresh.invoke("example.revocation.check", {}, InvocationContext())
            self.assertFalse(result.is_error)
            self.assertEqual(len(new.calls), 1)
        finally:
            await runtime.stop()

    async def test_real_sdk_worker_retains_completed_file_without_delivery_after_revocation(self):
        from companion_v01.plugin_generation import PluginGenerationProcess
        from companion_v01.plugin_managed_artifacts import GeneratedFileManagedArtifactSink
        from tests.test_plugin_resources import services
        from tests.test_turn_mainline_contract import _Harness, _tool_round_output

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, files = services(root)
            site = root / "site"
            package, metadata = site / "revocation_fixture", site / "revocation_fixture-0.1.0.dist-info"
            package.mkdir(parents=True)
            metadata.mkdir()
            (package / "__init__.py").write_text(textwrap.dedent('''
                import asyncio
                from pathlib import Path
                from akane_plugin import Plugin, Result, CapabilityIOSlot, ManagedArtifactDraft, ManagedArtifactPayload
                plugin = Plugin("example.revocation", permissions=("artifact.write",))
                @plugin.tool(effects=("filesystem",), outputs=(CapabilityIOSlot("file", "file", required=True,
                    max_bytes=1024, delivery="generated_file"),))
                async def check(directory: str) -> Result:
                    root = Path(directory)
                    (root / "entered").touch()
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        (root / "cleaning").touch()
                        while not (root / "release").is_file():
                            await asyncio.sleep(0.01)
                        return Result(value={"actually_finished": True}, followup="required", content=ManagedArtifactPayload(
                            content={"actually_finished": True}, artifacts=(ManagedArtifactDraft(data=b"actual result",
                                title="finished", output_format="txt", mime_type="text/plain", send_to_user=True),)))
                def create_plugin(): return plugin
            '''), encoding="utf-8")
            (metadata / "entry_points.txt").write_text("[akane.plugins.v1]\nexample.revocation = revocation_fixture:create_plugin\n", encoding="utf-8")
            (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: revocation-fixture\nVersion: 0.1.0\n", encoding="utf-8")
            process = PluginGenerationProcess(project_root=Path(__file__).resolve().parents[1], site_dir=site,
                plugin_id="example.revocation", work_dir=root / "worker")
            process.bind_managed_artifact_sink(GeneratedFileManagedArtifactSink(files))
            runtime = ActivePluginGeneration()
            try:
                await asyncio.to_thread(process.start)
                await runtime.publish(PluginGenerationSnapshot((PluginSelection(process.plugin_id, True),), (process,)))
                handler = PluginCapabilityToolBridge(runtime, config_base_dir=root).build_tool_handlers()["example.revocation.check"]
                harness = _Harness([_tool_round_output("", handler.tool_type, "revoked-worker")])
                executed = []
                def execute(**kwargs):
                    result = handler.execute(call={"arguments": {"directory": str(root)}}, context=_context())
                    executed.append(result)
                    return result
                harness.engine._execute_tool_call = execute
                pending = asyncio.create_task(asyncio.to_thread(harness.run_sync, harness.payload()))
                async with asyncio.timeout(5):
                    while not (root / "entered").is_file():
                        self.assertFalse(pending.done(), pending.result() if pending.done() else "")
                        await asyncio.sleep(0.01)
                disabling = asyncio.create_task(runtime.publish(PluginGenerationSnapshot((), ())))
                async with asyncio.timeout(5):
                    while not (root / "cleaning").is_file():
                        await asyncio.sleep(0.01)
                self.assertFalse(pending.done())
                self.assertEqual((await disabling)["cleanup_status"], "draining")
                (root / "release").touch()
                frame = await pending
                await disabling
                await runtime.drain_retired()
                self.assertEqual(len(harness.script.generation_calls), 1)
                self.assertEqual(frame["speech"], "")
                self.assertFalse(any(event.get("send_to_user") for event in frame["tool_events"]))
                result = executed[0]
                self.assertFalse(result.capability_result.is_error, result)
                self.assertEqual(result.capability_result.value, {"actually_finished": True})
                artifact = result.capability_result.content["managed_artifacts"][0]
                saved = files.resolve_generated_artifact(profile_user_id="profile-a", session_id="session-a",
                    target=artifact["generated_handle"])
                self.assertEqual(Path(saved["absolute_path"]).read_bytes(), b"actual result")
                self.assertEqual(result.followup.reason, "scope_revoked")
                self.assertFalse(result.followup.requires_model)
                self.assertFalse(any(event.get("send_to_user") for event in result.stream_events))
            finally:
                (root / "release").touch()
                await runtime.stop()
                await asyncio.to_thread(process.stop)

    async def test_revocation_between_admission_and_store_create_is_durable(self):
        for before_store in (False, True):
            with self.subTest(before_store=before_store):
                await self._revocation_during_create(before_store)

    async def _revocation_during_create(self, before_store):
        with tempfile.TemporaryDirectory() as directory:
            old, new = _Process("old"), _Process("new")
            selection = (PluginSelection(old.plugin_id, True),)
            runtime = ActivePluginGeneration()
            await runtime.publish(PluginGenerationSnapshot(selection, (old,)))
            handler = PluginCapabilityToolBridge(runtime, config_base_dir=Path(directory)).build_tool_handlers()["example.revocation.check"]
            runner = BackgroundTaskRunner({"host-jobs": 1})
            store = HostJobStore(Path(directory) / "jobs.db")
            jobs = HostToolJobRuntime(engine=_Engine(handler), store=store,
                background_tasks=runner, conversation_ref_issuer=lambda context: "ref")
            runtime.bind_capability_revocation_listener(jobs.revoke_capabilities)
            entered, release = threading.Event(), threading.Event()
            create = store.create

            def delayed_create(**kwargs):
                saved = None if before_store else create(**kwargs)
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("test_create_barrier_timeout")
                return create(**kwargs) if before_store else saved

            try:
                with patch.object(store, "create", side_effect=delayed_create):
                    submission = asyncio.create_task(asyncio.to_thread(jobs.submit,
                        capability_id=handler.tool_type, invocation_id="late-after-admission",
                        call={"type": handler.tool_type, "arguments": {}}, context=_context(), handler=handler))
                    self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                    await runtime.publish(PluginGenerationSnapshot((), ()))
                    await runtime.publish(PluginGenerationSnapshot(selection, (new,)))
                    release.set()
                    result = await submission
                self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-jobs", timeout=3))
                job_id = result.stream_events[0]["job_id"]
                job = HostJobStore(Path(directory) / "jobs.db").get(job_id, owner=HostJobOwner("profile-a", "session-a"))
                self.assertEqual(job.status, "cancelled")
                self.assertEqual(job.scope_revoked_reason, "plugin_capability_revoked")
                self.assertFalse(job_followup(job).requires_model)
                self.assertEqual(new.calls, [])
                self.assertEqual(old.calls, [])
            finally:
                release.set()
                await asyncio.to_thread(runner.close)
                await runtime.stop()

    async def test_queued_job_keeps_original_grant_when_engine_has_fresh_handler(self):
        with tempfile.TemporaryDirectory() as directory:
            old, new = _Process("old"), _Process("new")
            selection = (PluginSelection(old.plugin_id, True),)
            runtime = ActivePluginGeneration()
            await runtime.publish(PluginGenerationSnapshot(selection, (old,)))
            bridge = PluginCapabilityToolBridge(runtime, config_base_dir=Path(directory))
            handler = bridge.build_tool_handlers()["example.revocation.check"]
            engine = _Engine(handler)
            engine._tool_hook_result_status = AkaneMemoryEngine._tool_hook_result_status
            runner = BackgroundTaskRunner({"host-jobs": 1})
            release = threading.Event()
            store = HostJobStore(Path(directory) / "jobs.db")
            jobs = HostToolJobRuntime(engine=engine, store=store, background_tasks=runner,
                conversation_ref_issuer=lambda context: "ref")
            try:
                runner.submit(lane="host-jobs", name="hold", fn=release.wait, args=(5,))
                accepted = jobs.submit(capability_id=handler.tool_type, invocation_id="queued",
                    call={"type": handler.tool_type, "arguments": {}}, context=_context(), handler=handler)
                await runtime.publish(PluginGenerationSnapshot((), ()))
                await runtime.publish(PluginGenerationSnapshot(selection, (new,)))
                engine.handler = bridge.build_tool_handlers()[handler.tool_type]
                release.set()
                self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-jobs", timeout=3))
                job = store.get(accepted.stream_events[0]["job_id"], owner=HostJobOwner("profile-a", "session-a"))
                self.assertEqual(job.status, "cancelled", job.last_error)
                self.assertEqual(job.scope_revoked_reason, "plugin_capability_revoked")
                self.assertFalse(job_followup(job).requires_model)
                self.assertEqual(new.calls, [])
                self.assertEqual(jobs._admitted_handlers, {})
            finally:
                release.set()
                await asyncio.to_thread(runner.close)
                await runtime.stop()

    async def test_running_revocation_waits_for_cleanup_and_preserves_suppressed_result(self):
        for suppress in (False, True):
            entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

            class Running(_Process):
                async def invoke(self, capability_id, args, *, context):
                    self.calls.append(capability_id)
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        cleaning.set()
                        await release.wait()
                        if suppress:
                            return CapabilityResult(is_error=False, status="ok", content={"actually_finished": True})
                        raise

            old = Running("old")
            runtime = ActivePluginGeneration()
            await runtime.publish(PluginGenerationSnapshot((PluginSelection(old.plugin_id, True),), (old,)))
            binding = runtime.capture_capability_bindings()["example.revocation.check"]
            invocation = asyncio.create_task(binding.invoke("example.revocation.check", {}, InvocationContext()))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                disabling = asyncio.create_task(runtime.publish(PluginGenerationSnapshot((), ())))
                await asyncio.wait_for(cleaning.wait(), 3)
                self.assertEqual((await disabling)["cleanup_status"], "draining")
                self.assertFalse(invocation.done())
                self.assertEqual(old.stop_count, 0)
                release.set()
                result = await invocation
                await disabling
                await runtime.drain_retired()
                self.assertEqual(result.is_error, not suppress)
                self.assertEqual(result.content, {"actually_finished": True} if suppress else None)
                self.assertEqual(old.stop_count, 1)
                self.assertFalse(binding.is_live("example.revocation.check"))
            finally:
                release.set()
                await runtime.stop()

    async def test_old_handler_does_not_reacquire_reenabled_capability_or_admit_a_job(self):
        with tempfile.TemporaryDirectory() as directory:
            old, new = _Process("old"), _Process("new")
            selection = (PluginSelection(old.plugin_id, True),)
            runtime = ActivePluginGeneration()
            await runtime.publish(PluginGenerationSnapshot(selection, (old,)))
            bridge = PluginCapabilityToolBridge(runtime, config_base_dir=Path(directory))
            handler = bridge.build_tool_handlers()["example.revocation.check"]
            await runtime.publish(PluginGenerationSnapshot((), ()))
            await runtime.publish(PluginGenerationSnapshot(selection, (new,)))
            runner = BackgroundTaskRunner({"host-jobs": 1})
            jobs = HostToolJobRuntime(engine=_Engine(handler), store=HostJobStore(Path(directory) / "jobs.db"),
                background_tasks=runner, conversation_ref_issuer=lambda context: "ref")
            try:
                result = await asyncio.to_thread(handler.execute,
                    call={"arguments": {}}, context=_context())
                self.assertEqual(new.calls, [], result)
                self.assertEqual(result.capability_result.reason, "plugin_capability_revoked")
                accepted = jobs.submit(capability_id=handler.tool_type, invocation_id="late",
                    call={"type": handler.tool_type, "arguments": {}}, context=_context(), handler=handler)
                self.assertNotIn("background_job_accepted", [event["type"] for event in accepted.stream_events])
                fresh = bridge.build_tool_handlers()[handler.tool_type]
                current = await asyncio.to_thread(fresh.execute, call={"arguments": {}}, context=_context())
                self.assertFalse(current.capability_result.is_error)
                self.assertEqual(len(new.calls), 1)
            finally:
                await asyncio.to_thread(runner.close)
                await runtime.stop()
