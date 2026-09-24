"""Build/install the independent change-check project and run its real worker."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from capcore import InvocationContext
from companion_v01.background_tasks import BackgroundTaskRunner
from companion_v01.bot_runtime import _dispatch_host_job_completion
from companion_v01.capability_registry import ExecutorBroker
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.extension_management import ExtensionManagementService, PluginSelectionStore
from companion_v01.host_jobs import HostJobOwner, HostJobStore
from companion_v01.host_tool_jobs import HostToolJobRuntime
from companion_v01.mode_profiles import ModeProfileRegistry
from companion_v01.plugin_generation_candidate import PluginGenerationCandidateBuilder
from companion_v01.plugin_generation_runtime import PluginGenerationRuntime
from companion_v01.plugin_installation import ManagedPluginArtifactStore
from companion_v01.plugin_tool_bridge import PluginCapabilityToolBridge
from companion_v01.tool_runtime import ToolExecutionContext
from tests.test_plugin_engine_bridge import EngineFacade
from tests.test_plugin_resources import services
from tests.test_turn_mainline_contract import _Harness, _tool_round_output


class InstalledFollowupTests(unittest.IsolatedAsyncioTestCase):
    async def test_installed_sync_and_background_unchanged_results_create_no_extra_model_turn(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = ManagedPluginArtifactStore(root / "artifacts", instance_id="followup", project_root=project)
            selections = PluginSelectionStore(root / "selections.json", defaults=(), instance_id="followup")
            runtime = PluginGenerationRuntime((), candidate_builder=PluginGenerationCandidateBuilder(
                source_resolver=artifacts, project_root=project, work_root=root / "generations"))
            engine = EngineFacade(PluginCapabilityToolBridge(runtime, config_base_dir=root))
            engine.store, _, _ = services(root)
            engine.capability_config_base_dir = root
            engine.executor_broker = ExecutorBroker(None)
            engine._tool_hook_result_status = AkaneMemoryEngine._tool_hook_result_status
            engine._resolve_client_protocol_context = ModeProfileRegistry().resolve_from_payload
            timeline = []
            engine.record_plugin_timeline_event = lambda payload: timeline.append(payload) or {"ok": True}
            service = ExtensionManagementService(plugin_runtime=runtime, selection_store=selections, artifact_store=artifacts)
            await runtime.start()
            runner = BackgroundTaskRunner({"host-jobs": 1})
            jobs = HostJobStore(root / "jobs.db")
            router = SimpleNamespace(submit=AsyncMock(return_value=SimpleNamespace(ok=True, status="accepted", delivery_status="queued")))
            loop = asyncio.get_running_loop()
            background = HostToolJobRuntime(engine=engine, store=jobs, background_tasks=runner,
                conversation_ref_issuer=lambda context: "ref",
                terminal_callback=lambda job: asyncio.run_coroutine_threadsafe(
                    _dispatch_host_job_completion(engine, router, job), loop).result(5))
            runtime.bind_capability_revocation_listener(background.revoke_capabilities)
            try:
                staged = await service.stage_source(source_path=str(project / "examples/plugins/akane_sdk_change_check"))
                self.assertTrue(staged["ok"], staged)
                self.assertTrue((await service.install_stage(stage_id=staged["stage_id"], approved_permissions=staged["permissions"]))["ok"])
                first = await runtime.invoke("example.change-check.check", {"content": "真实内容"}, context=InvocationContext("owner", "session", "desktop_pet"))
                self.assertFalse(first.is_error, first)
                self.assertEqual(first.followup, "required")
                arguments = {"content": "真实内容", "previous_digest": first.value["digest"]}
                handler = engine._resolve_tool_handlers()["example.change-check.check"]
                harness = _Harness([_tool_round_output("", handler.tool_type, "unchanged")])
                harness.engine._execute_tool_call = lambda **kwargs: handler.execute(call={"arguments": arguments},
                    context=ToolExecutionContext("owner", "session", 1, {}, client_mode="desktop_pet"))
                final = await asyncio.to_thread(harness.run_sync, harness.payload())
                self.assertEqual(final["speech"], "")
                self.assertEqual(len(harness.script.generation_calls), 1)
                context = ToolExecutionContext("owner", "session", 1, {}, client_mode="desktop_pet")
                for number, content, mode in ((1, "真实内容", "none"), (2, "真实内容变了", "required")):
                    accepted = background.submit(capability_id="example.change-check.check_background", invocation_id=str(number),
                        call={"type": "example.change-check.check_background", "arguments": {**arguments, "content": content}}, context=context)
                    self.assertEqual(accepted.stream_events[0]["type"], "background_job_accepted", accepted)
                    self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-jobs", timeout=10))
                    self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-job-completions", timeout=5))
                    job = jobs.get(accepted.stream_events[0]["job_id"], owner=HostJobOwner("owner", "session"))
                    self.assertEqual(job.status, "succeeded")
                    self.assertEqual(job.result["capability_result"]["followup"], mode)
                    self.assertEqual(job.result["capability_result"]["value"]["changed"], mode == "required")
                    self.assertEqual(router.submit.await_count, number - 1)
                self.assertEqual(len(timeline), 1)
            finally:
                await asyncio.to_thread(runner.close)
                await runtime.stop()
