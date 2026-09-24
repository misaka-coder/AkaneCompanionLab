"""Version continuity belongs to the real invocation scope, not a tool name."""
from __future__ import annotations

import asyncio
import tempfile
import threading
import textwrap
import unittest
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, patch

from capcore import InvocationContext

from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_active_generation import ActivePluginGeneration, PluginGenerationSnapshot, PluginActiveGenerationError
from companion_v01.plugin_generation_runtime import PluginGenerationRuntime
from companion_v01.plugin_tool_bridge import PluginCapabilityToolBridge
from companion_v01.background_tasks import BackgroundTaskRunner
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.host_jobs import HostJobOwner, HostJobStore
from companion_v01.host_tool_jobs import HostToolJobRuntime
from companion_v01.plugin_capability_calls import EnginePluginCapabilityProvider
from companion_v01.plugin_generation import PluginGenerationProcess
from tests.test_host_tool_jobs import _Engine, _context
from tests.test_plugin_engine_bridge import EngineFacade
from tests.test_plugin_resources import services
from tests.test_plugin_active_generation import FakeGeneration
from tests.test_plugin_capability_revocation import _Process
from tests.test_turn_mainline_contract import _Harness, _speech_output, _tool_round_output


class GenerationFreezeTests(unittest.IsolatedAsyncioTestCase):
    async def test_capability_owner_change_does_not_break_frozen_prompt_or_restore_authority(self):
        runtime = ActivePluginGeneration()
        old = _Process("old")
        retained = FakeGeneration(old.plugin_id, "example.revocation.other", "current")
        owner = FakeGeneration("example.new-owner", "example.revocation.check", "moved")
        selection = (PluginSelection(old.plugin_id, True),)
        await runtime.publish(PluginGenerationSnapshot(selection, (old,)))
        try:
            with runtime.freeze_invocation_scope():
                await runtime.publish(PluginGenerationSnapshot((*selection, PluginSelection(owner.plugin_id, True)), (retained, owner)))
                self.assertEqual(runtime.stable_system_prompt_blocks(),
                    ("prompt:example.revocation:old", "prompt:example.new-owner:moved"))
                self.assertEqual(runtime.skill_roots(), ())
                result = await runtime.invoke("example.revocation.check", {}, context=InvocationContext())
                self.assertEqual(result.reason, "plugin_capability_revoked")
                old.running = False
                self.assertEqual(runtime.stable_system_prompt_blocks(), ("prompt:example.new-owner:moved",))
            await runtime.drain_retired()
        finally:
            await runtime.stop()

    async def test_new_event_handler_has_its_own_scope_instead_of_inheriting_old_emitter(self):
        runtime = ActivePluginGeneration()
        class EventProcess(_Process):
            async def invoke_event(self, *args, **kwargs):
                return await runtime.invoke("example.revocation.check", {}, context=InvocationContext())
        old, new = EventProcess("old"), EventProcess("new")
        selection = (PluginSelection(old.plugin_id, True),)
        await runtime.publish(PluginGenerationSnapshot(selection, (old,)))
        try:
            with runtime.freeze_invocation_scope():
                await runtime.publish(PluginGenerationSnapshot(selection, (new,)))
                registration = SimpleNamespace(plugin_id=new.plugin_id, generation_id=new.generation_id,
                    subscription=SimpleNamespace(subscription_id="test"))
                result = await runtime._invoke_event_registration(registration, None, context=InvocationContext(),
                    scope_id="", delivery_id="test", origin_context=None)
                self.assertEqual(result.content, {"generation": "new"})
                result = await runtime.invoke("example.revocation.check", {}, context=InvocationContext())
                self.assertEqual(result.content, {"generation": "old"})
            await runtime.drain_retired()
        finally:
            await runtime.stop()

    async def test_job_keeps_admitted_version_during_store_write_and_releases_duplicate_leases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = ActivePluginGeneration()
            old, new = _Process("old"), _Process("new")
            selection = (PluginSelection(old.plugin_id, True),)
            await runtime.publish(PluginGenerationSnapshot(selection, (old,)))
            bridge = PluginCapabilityToolBridge(runtime, config_base_dir=root)
            handler = bridge.build_tool_handlers()["example.revocation.check"]
            runner = BackgroundTaskRunner({"host-jobs": 1})
            lane_release, entered, release = threading.Event(), threading.Event(), threading.Event()
            runner.submit(lane="host-jobs", name="hold", fn=lane_release.wait, args=(10,))
            store = HostJobStore(root / "jobs.db")
            engine = _Engine(handler)
            engine._tool_hook_result_status = AkaneMemoryEngine._tool_hook_result_status
            jobs = HostToolJobRuntime(engine=engine, store=store, background_tasks=runner,
                conversation_ref_issuer=lambda context: "ref")
            create = store.create
            def delayed_create(**kwargs):
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test_job_store")
                return create(**kwargs)
            def submit():
                return jobs.submit(capability_id=handler.tool_type, invocation_id="same-invocation",
                    call={"type": handler.tool_type, "arguments": {}}, context=_context(), handler=handler)
            pending = None
            try:
                with patch.object(store, "create", side_effect=delayed_create):
                    pending = asyncio.create_task(asyncio.to_thread(submit))
                    self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                    switched = await runtime.publish(PluginGenerationSnapshot(selection, (new,)))
                    self.assertEqual(switched["cleanup_status"], "draining")
                    self.assertEqual(old.stop_count, 0)
                    release.set()
                    accepted = await pending
                job_id = accepted.stream_events[0]["job_id"]
                # A duplicate queued submission retains the first admission.
                handler = bridge.build_tool_handlers()[handler.tool_type]
                duplicate = await asyncio.to_thread(submit)
                self.assertEqual(duplicate.stream_events[0]["job_id"], job_id)
                lane_release.set()
                self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-jobs", timeout=3))
                job = store.get(job_id, owner=HostJobOwner("profile-a", "session-a"))
                self.assertEqual(job.status, "succeeded", job.last_error)
                self.assertEqual(job.result["capability_result"]["value"], {"generation": "old"})
                self.assertEqual(new.calls, [])
                await asyncio.wait_for(runtime.drain_retired(), 2)
                self.assertEqual(old.stop_count, 1)
                self.assertEqual(jobs._admitted_handlers, {})
            finally:
                release.set()
                lane_release.set()
                if pending is not None:
                    await pending
                await asyncio.to_thread(runner.close)
                await runtime.stop()

    async def test_multiple_upgrades_and_new_install_keep_existing_workers_until_scope_close(self):
        runtime = ActivePluginGeneration()
        old, middle, new = (_Process(value) for value in ("old", "middle", "new"))
        peer = FakeGeneration("example.added", "example.added.read", "added")
        selection = (PluginSelection(old.plugin_id, True),)
        await runtime.publish(PluginGenerationSnapshot(selection, (old,)))
        first = runtime.freeze_invocation_scope()
        second = None
        try:
            await runtime.publish(PluginGenerationSnapshot(selection, (middle,)))
            second = runtime.freeze_invocation_scope()
            installed = (*selection, PluginSelection(peer.plugin_id, True))
            await runtime.publish(PluginGenerationSnapshot(installed, (new, peer)))
            with self.assertRaisesRegex(PluginActiveGenerationError, "already_retiring"):
                await runtime.publish(PluginGenerationSnapshot(installed, (old, peer)))
            for scope, expected in ((first, "old"), (second, "middle")):
                with scope.activate():
                    bindings = runtime.capture_capability_bindings()
                    result = await bindings["example.revocation.check"].invoke("example.revocation.check", {}, InvocationContext())
                    self.assertEqual(result.content, {"generation": expected})
                    result = await bindings["example.added.read"].invoke("example.added.read", {}, InvocationContext())
                    self.assertEqual(result.content, {"generation": "added"})
                    self.assertIn(f"prompt:example.revocation:{expected}", runtime.stable_system_prompt_blocks())
            self.assertEqual([item.stop_count for item in (old, middle, new, peer)], [0, 0, 0, 0])
            first.close()
            second.close()
            await asyncio.wait_for(runtime.drain_retired(), 2)
            self.assertEqual([item.stop_count for item in (old, middle, new, peer)], [1, 1, 0, 0])
            await runtime.publish(PluginGenerationSnapshot(installed, (new, peer)))
            self.assertEqual(peer.stop_count, 0)
        finally:
            first.close()
            if second is not None:
                second.close()
            await runtime.stop()
        self.assertEqual([item.stop_count for item in (old, middle, new, peer)], [1, 1, 1, 1])

    async def test_stream_close_and_exception_release_context_bound_scope(self):
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as directory:
                runtime = ActivePluginGeneration()
                old, new = _Process("old"), _Process("new")
                selection = (PluginSelection(old.plugin_id, True),)
                await runtime.publish(PluginGenerationSnapshot(selection, (old,)))
                engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
                engine.plugin_capability_source = PluginCapabilityToolBridge(runtime, config_base_dir=Path(directory))
                engine._abort_open_memcore_turn_guard = lambda **kwargs: None
                def core(*args, **kwargs):
                    yield {"type": "scope-open"}
                    if fail:
                        raise RuntimeError("test_model_failure")
                engine._run_turn_core = core
                stream = engine.process_turn_stream({})
                try:
                    self.assertEqual(await asyncio.to_thread(next, stream), {"type": "scope-open"})
                    switched = await runtime.publish(PluginGenerationSnapshot(selection, (new,)))
                    self.assertEqual(switched["cleanup_status"], "draining")
                    if fail:
                        with self.assertRaisesRegex(RuntimeError, "test_model_failure"):
                            await asyncio.to_thread(next, stream)
                    else:
                        await asyncio.to_thread(stream.close)
                    await asyncio.wait_for(runtime.drain_retired(), 2)
                    self.assertEqual(old.stop_count, 1)
                finally:
                    stream.close()
                    await runtime.stop()

    async def test_cancelled_shutdown_waits_for_actual_invocation_cleanup(self):
        runtime = ActivePluginGeneration()
        entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        class Slow(_Process):
            async def invoke(self, *args, **kwargs):
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cleaning.set()
                    await release.wait()
                    raise
        old = Slow("old")
        await runtime.publish(PluginGenerationSnapshot((PluginSelection(old.plugin_id, True),), (old,)))
        invocation = asyncio.create_task(runtime.invoke("example.revocation.check", {}, context=InvocationContext()))
        await asyncio.wait_for(entered.wait(), 2)
        stopping = asyncio.create_task(runtime.stop())
        try:
            await asyncio.wait_for(cleaning.wait(), 2)
            stopping.cancel()
            await asyncio.sleep(0)
            self.assertFalse(stopping.done())
            self.assertEqual(runtime.state, "stopping")
            self.assertEqual(old.stop_count, 0)
            release.set()
            await invocation
            with self.assertRaises(asyncio.CancelledError):
                await stopping
            self.assertEqual(runtime.state, "stopped")
            self.assertEqual(old.stop_count, 1)
        finally:
            release.set()
            await runtime.stop()

    async def test_reconcile_failure_keeps_published_candidate_owned_and_observable(self):
        from tests.test_plugin_generation_runtime import _Builder
        old, new = _Process("old"), _Process("new")
        active = ActivePluginGeneration()
        runtime = PluginGenerationRuntime((PluginSelection(old.plugin_id, True),),
            candidate_builder=_Builder([old, new]), active_generation=active)
        await runtime.start()
        try:
            with patch.object(active._events, "reconcile", AsyncMock(side_effect=RuntimeError("test_event_store_failed"))):
                result = await runtime.restart()
            self.assertTrue(result["published"])
            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "plugin_event_reconcile_failed")
            self.assertEqual(runtime.status_snapshot()["reason"], "plugin_event_reconcile_failed")
            value = await runtime.invoke("example.revocation.check", {}, context=InvocationContext())
            self.assertEqual(value.content, {"generation": "new"})
            self.assertEqual(new.stop_count, 0)
            self.assertEqual(old.stop_count, 1)
        finally:
            await runtime.stop()
        self.assertEqual(new.stop_count, 1)

    async def test_real_sdk_background_job_retains_both_workers_after_parent_turn_ends(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = ActivePluginGeneration()
            bridge = PluginCapabilityToolBridge(runtime, config_base_dir=root)
            provider_engine = EngineFacade(bridge)
            provider_engine.store, _, _ = services(root)
            provider_engine.capability_config_base_dir = root
            provider = EnginePluginCapabilityProvider(provider_engine)
            processes = []
            runner = BackgroundTaskRunner({"host-jobs": 1})
            lane_entered, lane_release = threading.Event(), threading.Event()
            def occupy_lane():
                lane_entered.set()
                if not lane_release.wait(15):
                    raise TimeoutError("test_job_lane")
            runner.submit(lane="host-jobs", name="hold-before-plugin-job", fn=occupy_lane)
            self.assertTrue(await asyncio.to_thread(lane_entered.wait, 3))
            async def create(role, version):
                site = root / f"{role}-{version}" / "site"
                package, metadata = site / "freeze_fixture", site / "freeze_fixture-0.1.0.dist-info"
                package.mkdir(parents=True)
                metadata.mkdir()
                source = textwrap.dedent('''
                    import asyncio
                    from pathlib import Path
                    from akane_plugin import Plugin, ToolContext
                    plugin = Plugin("example.freeze-ROLE", permissions=("capability.invoke",))
                    @plugin.tool(execution_class="long_task", followup="none")
                    async def run(directory: str, ctx: ToolContext) -> dict[str, str]:
                        if "ROLE" == "peer":
                            return {"peer": "VERSION"}
                        root = Path(directory)
                        (root / "entered").touch()
                        while not (root / "release").exists():
                            await asyncio.sleep(0.01)
                        result = await ctx.tools.call("example.freeze-peer.run", {"directory": directory})
                        return {"caller": "VERSION", **result}
                    def create_plugin(): return plugin
                ''').replace("ROLE", role).replace("VERSION", version)
                (package / "__init__.py").write_text(source, encoding="utf-8")
                plugin_id = f"example.freeze-{role}"
                (metadata / "entry_points.txt").write_text(
                    f"[akane.plugins.v1]\n{plugin_id} = freeze_fixture:create_plugin\n", encoding="utf-8")
                (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: freeze-fixture\nVersion: 0.1.0\n", encoding="utf-8")
                process = PluginGenerationProcess(project_root=Path(__file__).resolve().parents[1], site_dir=site,
                    plugin_id=plugin_id, work_dir=site.parent / "worker")
                process.bind_capability_provider(provider)
                processes.append(process)
                await asyncio.to_thread(process.start)
                return process
            try:
                old = (await create("caller", "old"), await create("peer", "old"))
                selections = tuple(PluginSelection(process.plugin_id, True) for process in old)
                await runtime.publish(PluginGenerationSnapshot(selections, old))
                handler = bridge.build_tool_handlers()["example.freeze-caller.run"]
                store = HostJobStore(root / "jobs.db")
                engine = _Engine(handler)
                engine._tool_hook_result_status = AkaneMemoryEngine._tool_hook_result_status
                jobs = HostToolJobRuntime(engine=engine, store=store, background_tasks=runner,
                    conversation_ref_issuer=lambda context: "ref")
                runtime.bind_capability_revocation_listener(jobs.revoke_capabilities)
                with bridge.turn_scope():
                    handler = bridge.build_tool_handlers()[handler.tool_type]
                    accepted = await asyncio.to_thread(jobs.submit, capability_id=handler.tool_type,
                        invocation_id="frozen-background", call={"type": handler.tool_type, "arguments": {"directory": str(root)}},
                        context=_context(), handler=handler)
                    job_id = accepted.stream_events[0]["job_id"]
                    new = (await create("caller", "new"), await create("peer", "new"))
                    switched = await asyncio.wait_for(runtime.publish(PluginGenerationSnapshot(selections, new)), 3)
                    self.assertEqual(switched["cleanup_status"], "draining")
                self.assertTrue(all(process.running for process in old))
                lane_release.set()
                async with asyncio.timeout(5):
                    while not (root / "entered").exists():
                        await asyncio.sleep(0.01)
                (root / "release").touch()
                self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-jobs", timeout=5))
                job = store.get(job_id, owner=HostJobOwner("profile-a", "session-a"))
                self.assertEqual(job.status, "succeeded", (job.last_error, job.result))
                self.assertEqual(job.result["capability_result"]["value"], {"caller": "old", "peer": "old"})
                await asyncio.wait_for(runtime.drain_retired(), 3)
                self.assertTrue(all(not process.running for process in old))
                self.assertEqual(jobs._admitted_handlers, {})
                fresh = await runtime.invoke(handler.tool_type, {"directory": str(root)},
                    context=InvocationContext("profile-a", "session-a", "qq_text"))
                self.assertEqual(fresh.value, {"caller": "new", "peer": "new"}, fresh)
            finally:
                lane_release.set()
                (root / "release").touch()
                await asyncio.to_thread(runner.close)
                await runtime.stop()
                for process in processes:
                    await asyncio.to_thread(process.stop)

    async def test_real_sync_and_stream_turns_keep_versions_across_model_and_tool_rounds(self):
        for stream in (False, True):
            with self.subTest(stream=stream), tempfile.TemporaryDirectory() as directory:
                old, new = _Process("old"), _Process("new")
                for process in (old, new):
                    process.capability_descriptors = MappingProxyType({key: replace(value, raw={"followup": "required"})
                        for key, value in process.capability_descriptors.items()})
                runtime = ActivePluginGeneration()
                selections = (PluginSelection(old.plugin_id, True),)
                await runtime.publish(PluginGenerationSnapshot(selections, (old,)))
                bridge = PluginCapabilityToolBridge(runtime, config_base_dir=Path(directory))
                entered, release = threading.Event(), threading.Event()
                key = "example.revocation.check"
                harness = _Harness([_tool_round_output("", key, "first"), _tool_round_output("", key, "second"),
                                    _speech_output("done")])
                harness.engine.plugin_capability_source = bridge
                observed = []
                def execute(**kwargs):
                    handler = bridge.build_tool_handlers()[key]
                    result = handler.execute(call={"arguments": {}}, context=replace(_context(), followup_default="required"))
                    observed.append(result.capability_result.value)
                    return result
                harness.engine._execute_tool_call = execute
                next_output = harness.script._next
                def delayed_model():
                    if not entered.is_set():
                        # Discovery has happened inside the real model turn.
                        self.assertTrue(bridge.build_tool_handlers()[key].invocation_grant_active())
                        entered.set()
                        if not release.wait(5):
                            raise TimeoutError("test_model_barrier")
                    return next_output()
                harness.script._next = delayed_model
                run = harness.run_stream if stream else harness.run_sync
                pending = asyncio.create_task(asyncio.to_thread(run, harness.payload()))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                    switched = await asyncio.wait_for(runtime.publish(PluginGenerationSnapshot(selections, (new,))), 2)
                    self.assertEqual(switched["cleanup_status"], "draining")
                    # A concurrent independent turn gets the published version.
                    fresh = _Harness([_tool_round_output("", key, "fresh"), _speech_output("fresh done")])
                    fresh.engine.plugin_capability_source = bridge
                    fresh.engine._execute_tool_call = execute
                    await asyncio.to_thread(fresh.run_sync, fresh.payload())
                    self.assertEqual(observed, [{"generation": "new"}])
                    release.set()
                    await asyncio.wait_for(pending, 3)
                    self.assertEqual(observed, [{"generation": "new"}, {"generation": "old"}, {"generation": "old"}])
                    self.assertEqual(len(harness.script.generation_calls), 3)
                    await asyncio.wait_for(runtime.drain_retired(), 2)
                    self.assertEqual(old.stop_count, 1)
                finally:
                    release.set()
                    await pending
                    await runtime.stop()

    async def test_disable_and_reenable_cannot_restore_an_existing_turn(self):
        runtime = ActivePluginGeneration()
        old, new = _Process("old"), _Process("new")
        selection = (PluginSelection(old.plugin_id, True),)
        await runtime.publish(PluginGenerationSnapshot(selection, (old,)))
        try:
            with runtime.freeze_invocation_scope():
                key = "example.revocation.check"
                binding = runtime.capture_capability_bindings()[key]
                await runtime.publish(PluginGenerationSnapshot((), ()))
                self.assertEqual(old.stop_count, 1)
                await runtime.publish(PluginGenerationSnapshot(selection, (new,)))
                rebuilt = runtime.capture_capability_bindings()[key]
                for caller in (binding, rebuilt):
                    result = await caller.invoke(key, {}, InvocationContext())
                    self.assertEqual(result.reason, "plugin_capability_revoked")
                self.assertEqual(new.calls, [])
        finally:
            await runtime.stop()

    async def test_upgrade_inside_turn_publishes_without_waiting_for_its_own_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            old, new = _Process("old"), _Process("new")
            selections = (PluginSelection(old.plugin_id, True),)
            runtime = ActivePluginGeneration()
            await runtime.publish(PluginGenerationSnapshot(selections, (old,)))
            bridge = PluginCapabilityToolBridge(runtime, config_base_dir=Path(directory))
            try:
                with bridge.turn_scope():
                    handler = bridge.build_tool_handlers()["example.revocation.check"]
                    before = await asyncio.to_thread(handler.execute, call={"arguments": {}}, context=_context())
                    self.assertEqual(before.capability_result.value, {"generation": "old"})
                    switched = await asyncio.wait_for(runtime.publish(PluginGenerationSnapshot(selections, (new,))), 2)
                    self.assertTrue(switched["ok"])
                    self.assertEqual(switched["cleanup_status"], "draining")
                    self.assertEqual(old.stop_count, 0)
                    rebuilt = bridge.build_tool_handlers()[handler.tool_type]
                    after = await asyncio.to_thread(rebuilt.execute, call={"arguments": {}}, context=_context())
                    self.assertEqual(after.capability_result.value, {"generation": "old"})
                    self.assertEqual(new.calls, [])
                await asyncio.wait_for(runtime.drain_retired(), 2)
                self.assertEqual(old.stop_count, 1)
                with bridge.turn_scope():
                    current = bridge.build_tool_handlers()[handler.tool_type]
                    result = await asyncio.to_thread(current.execute, call={"arguments": {}}, context=_context())
                    self.assertEqual(result.capability_result.value, {"generation": "new"})
            finally:
                await runtime.stop()
