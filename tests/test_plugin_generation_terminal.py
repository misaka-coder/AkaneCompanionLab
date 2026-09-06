"""Real isolated worker cancellation must preserve the actual terminal."""

from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import textwrap
import unittest
from types import SimpleNamespace

from capcore import InvocationContext
from companion_v01.plugin_generation import PluginGenerationProcess
from companion_v01.plugin_managed_artifacts import GeneratedFileManagedArtifactSink
from companion_v01.plugin_resources import GeneratedFileResourceProvider
from companion_v01.background_tasks import BackgroundTaskRunner
from companion_v01.capability_registry import ExecutorBroker
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.host_jobs import HostJobOwner, HostJobStore
from companion_v01.host_tool_jobs import HostToolJobRuntime
from companion_v01.plugin_tool_bridge import PluginCapabilityToolBridge
from companion_v01.tool_runtime import ToolExecutionContext
from tests.test_host_tool_jobs import _Engine
from tests.test_plugin_resources import services, add_attachment


ROOT = Path(__file__).resolve().parents[1]


def write_terminal_plugin(root):
    site = root / "site"
    package, metadata = site / "terminal_fixture", site / "terminal_fixture-0.1.0.dist-info"
    package.mkdir(parents=True)
    metadata.mkdir()
    (package / "__init__.py").write_text(
        textwrap.dedent("""
        import asyncio
        from capcore import CapabilityDescriptor, CapabilityIOSlot, CapabilityResult, HealthStatus
        from companion_v01.plugin_api import PluginManifest, ManagedArtifactPayload, ManagedArtifactDraft
        class Adapter:
            provider_id = "provider.test.terminal"
            def __init__(self, resources): self.resources = resources
            async def health(self): return HealthStatus(True, "ready")
            async def list_capabilities(self):
                return (CapabilityDescriptor(
                    id="test.terminal.run", display_name="Terminal fixture", short_hint="Test actual cancellation outcome.",
                    visible_in=("base", "web", "desktop", "qq"), prompt_exposed=True, risk="low", confirm="never",
                    effects=("filesystem",), trigger=None,
                    inputs=(CapabilityIOSlot("target", "string", required=True), CapabilityIOSlot("outcome", "string", required=True)),
                    outputs=(CapabilityIOSlot("file", "file", required=True, max_bytes=1024, delivery="generated_file"),),
                    raw={"execution_class":"long_task", "completion_mode":"agent", "memory_mode":"timeline"},
                ),)
            async def invoke(self, capability_id, args, context):
                source = await self.resources.open(args["target"])
                if not source.ok: return CapabilityResult(is_error=True, status="error", reason=source.reason)
                try:
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    await asyncio.sleep(0.15)
                    if args["outcome"] == "cancel": raise
                    if args["outcome"] == "failed":
                        return CapabilityResult(is_error=True, status="error", reason="remote_completion_unconfirmed")
                    return CapabilityResult(is_error=False, status="ok", content=ManagedArtifactPayload(
                        content={"actually_finished": True},
                        artifacts=(ManagedArtifactDraft(data=b"actual completed artifact", title="terminal", output_format="txt",
                                                        mime_type="text/plain", send_to_user=False),),
                    ))
            async def aclose(self): pass
        class Plugin:
            manifest = PluginManifest("test.terminal", "0.1.0", 1, ("capability.prompt.invoke", "resource.read", "artifact.write"))
            def register(self, registrar): registrar.add_capability_adapter(Adapter(registrar.get_resource_port()))
        def create_plugin(): return Plugin()
    """),
        encoding="utf-8",
    )
    (metadata / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: terminal-fixture\nVersion: 0.1.0\n", encoding="utf-8"
    )
    (metadata / "entry_points.txt").write_text(
        "[akane.plugins.v1]\ntest.terminal = terminal_fixture:create_plugin\n", encoding="utf-8"
    )
    return site


class GenerationTerminalTests(unittest.IsolatedAsyncioTestCase):
    async def test_host_job_uses_real_isolated_success_failure_and_cancel(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, attachments, files = services(root)
            _, attachment = add_attachment(root, attachments)
            generation = PluginGenerationProcess(
                project_root=ROOT,
                site_dir=write_terminal_plugin(root),
                plugin_id="test.terminal",
                work_dir=root / "worker",
            )
            generation.bind_managed_artifact_sink(GeneratedFileManagedArtifactSink(files))
            generation.bind_resource_provider(GeneratedFileResourceProvider(files, work_root=root / "copies"))
            await asyncio.to_thread(generation.start)
            facade = SimpleNamespace(
                state="active",
                capability_ids=tuple(generation.capability_descriptors),
                capability_descriptors=generation.capability_descriptors,
                invoke_from_consumer=generation.invoke,
            )
            handler = PluginCapabilityToolBridge(facade, config_base_dir=root).build_tool_handlers()[
                "test.terminal.run"
            ]
            engine = _Engine(handler)
            engine.executor_broker = ExecutorBroker(None)
            engine._tool_hook_result_status = AkaneMemoryEngine._tool_hook_result_status
            jobs, runner, completed = HostJobStore(root / "jobs.db"), BackgroundTaskRunner({"host-jobs": 1}), []
            runtime = HostToolJobRuntime(
                engine=engine,
                store=jobs,
                background_tasks=runner,
                conversation_ref_issuer=lambda _: "test-conversation",
                terminal_callback=lambda job: completed.append(job) or True,
            )
            owner = HostJobOwner("owner", "session")
            try:
                for outcome, status in (("cancel", "cancelled"), ("failed", "failed"), ("success", "succeeded")):
                    result = runtime.submit(
                        capability_id=handler.tool_type,
                        invocation_id=outcome,
                        call={
                            "type": handler.tool_type,
                            "arguments": {"target": attachment["attachment_handle"], "outcome": outcome},
                        },
                        context=ToolExecutionContext("owner", "session", 1, {}, client_mode="qq_text"),
                    )
                    job_id = result.stream_events[0]["job_id"]
                    for _ in range(1000):
                        if list((root / "copies").glob("input-*/*")):
                            break
                        await asyncio.sleep(0.01)
                    self.assertTrue(list((root / "copies").glob("input-*/*")))
                    await asyncio.sleep(0.05)
                    jobs.request_cancel(job_id, owner=owner)
                    jobs.request_cancel(job_id, owner=owner)
                    self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-jobs", timeout=10))
                    self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-job-completions", timeout=5))
                    job = jobs.get(job_id, owner=owner)
                    self.assertEqual(job.status, status)
                    self.assertEqual(len(job.artifacts), int(outcome == "success"))
                    self.assertEqual(list((root / "copies").iterdir()), [])
                self.assertEqual([job.status for job in completed], ["cancelled", "failed", "succeeded"])
                self.assertEqual(runtime.recover(), 0)
            finally:
                await asyncio.to_thread(runner.close, timeout=10)
                await asyncio.to_thread(generation.stop)

    async def test_real_worker_suppressed_cancel_preserves_success_failure_and_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, attachments, files = services(root)
            _, attachment = add_attachment(root, attachments)
            target = attachment["attachment_handle"]
            generation = PluginGenerationProcess(
                project_root=ROOT,
                site_dir=write_terminal_plugin(root),
                plugin_id="test.terminal",
                work_dir=root / "worker",
            )
            generation.bind_managed_artifact_sink(GeneratedFileManagedArtifactSink(files))
            generation.bind_resource_provider(GeneratedFileResourceProvider(files, work_root=root / "copies"))
            await asyncio.to_thread(generation.start)
            try:
                for outcome in ("cancel", "failed", "success"):
                    with self.subTest(outcome=outcome):
                        pending = asyncio.create_task(
                            generation.invoke(
                                "test.terminal.run",
                                {"target": target, "outcome": outcome},
                                context=InvocationContext("owner", "session", "web"),
                            )
                        )
                        for _ in range(1000):
                            if pending.done():
                                self.fail(f"invocation finished before cancellation: {pending.result()}")
                            if list((root / "copies").glob("input-*/*")):
                                break
                            await asyncio.sleep(0.01)
                        self.assertTrue(list((root / "copies").glob("input-*/*")))
                        await asyncio.sleep(0.05)
                        pending.cancel()
                        await asyncio.sleep(0.02)
                        pending.cancel()
                        if outcome == "cancel":
                            with self.assertRaises(asyncio.CancelledError):
                                await pending
                        else:
                            result = await pending
                            self.assertEqual(result.status, "error" if outcome == "failed" else "ok")
                            self.assertEqual(result.is_error, outcome == "failed")
                            if outcome == "failed":
                                self.assertEqual(result.reason, "remote_completion_unconfirmed")
                            else:
                                ref = result.content["managed_artifacts"][0]
                                resolved = files.resolve_input_resource(
                                    profile_user_id="owner",
                                    session_id="session",
                                    target=ref["generated_handle"],
                                    timestamp=None,
                                )
                                self.assertEqual(
                                    Path(resolved["absolute_path"]).read_bytes(), b"actual completed artifact"
                                )
                        self.assertEqual(list((root / "copies").iterdir()), [])
                        count = len(
                            files.store.list_generated_files(profile_user_id="owner", session_id="session", limit=10)
                        )
                        self.assertEqual(count, int(outcome == "success"))
            finally:
                await asyncio.to_thread(generation.stop)


if __name__ == "__main__":
    unittest.main()
