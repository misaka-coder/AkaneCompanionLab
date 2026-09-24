"""Public result choices run through Host, worker, Engine and normal delivery."""
from __future__ import annotations

import asyncio
import tempfile
import textwrap
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from capcore import InvocationContext
from akane_plugin import Plugin, Result, ToolCallError, Tools, ToolContext
from companion_v01.plugin_capability_calls import EnginePluginCapabilityProvider, ScopedPluginCapabilityPort
from companion_v01.plugin_generation import PluginGenerationProcess
from companion_v01.plugin_generation_codec import capability_result_from_wire, capability_result_to_wire
from companion_v01.plugin_resources import ResourceInvocation, current_resource_invocation
from companion_v01.plugin_result_projection import project_capability_result
from companion_v01.tool_continuation import can_finish_tool_batch
from companion_v01.tool_handlers.core import ToolExecutionContext, TaskExecutionScope
from companion_v01.tool_invocation import NATIVE_TOOL_CALLS_FIELD
from tests import test_plugin_sdk as _sdk
from tests.test_turn_mainline_contract import _Harness, _tool_round_output, _speech_output
from tests import test_plugin_turn_requests as turn_helpers


class IndependentTurnFollowupTests(unittest.IsolatedAsyncioTestCase):
    start_host = turn_helpers.TurnRequestTests.start_host
    start = turn_helpers.TurnRequestTests.start
    call = turn_helpers.TurnRequestTests.call
    settled = turn_helpers.TurnRequestTests.settled

    async def test_none_result_keeps_explicit_request_and_queued_silence_has_no_frame(self):
        from companion_v01.plugin_tool_bridge import PluginCapabilityToolBridge

        plugin = turn_helpers.board_plugin()
        requests = []

        @plugin.tool
        async def check(ctx: ToolContext) -> Result:
            receipt = await ctx.request_turn("Decide the next step", {"changed": True})
            requests.append(receipt.request_id)
            return Result(value=False, followup="none")

        await self.start(plugin)
        handler = PluginCapabilityToolBridge(self.host, config_base_dir=self.root,
            conversation_ref_issuer=lambda context: self.context.conversation_ref).build_tool_handlers()["example.board.check"]
        for silent in (False, True):
            if silent:
                self.model_frame = {"speech": "", "speech_segments": [], "emotion": "happy",
                    "activity": {"action": "pause"}, "_tool_finished_turn": True, "_deliberate_silence": True}
            harness = _Harness([_tool_round_output("", handler.tool_type, f"check-{silent}")])
            harness.engine._execute_tool_call = lambda **kw: handler.execute(call={"arguments": {}},
                context=ToolExecutionContext("owner", "session", 1, {}, character_pack_id="akane", client_mode="desktop_pet"))
            frame = await asyncio.to_thread(harness.run_sync, harness.payload())
            self.assertEqual(frame["speech"], "")
            self.assertEqual(len(harness.script.generation_calls), 1)
            receipt = await self.settled(requests[-1])
            self.assertEqual(receipt["status"], "completed")
            self.assertEqual(receipt["model_status"], "completed")
            self.assertEqual(receipt["delivery_status"], "not_requested" if silent else "queued")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(self.frames), 1)


class PluginFollowupTests(unittest.IsolatedAsyncioTestCase):
    start = _sdk.PublicSdkTests.start

    async def test_none_managed_file_reaches_existing_sync_desktop_renderer(self):
        import json
        import subprocess
        from akane_plugin import CapabilityIOSlot, ManagedArtifactDraft, ManagedArtifactPayload, MANAGED_ARTIFACT_WRITE_PERMISSION
        from companion_v01.plugin_host import PluginHost
        from companion_v01.instance_profile import PluginSelection
        from companion_v01.plugin_contribution_policy import TrustedStatefulPluginContributionPolicy
        from companion_v01.plugin_managed_artifacts import GeneratedFileManagedArtifactSink
        from companion_v01.plugin_tool_bridge import PluginCapabilityToolBridge
        from companion_v01.routes.think import _desktop_tool_completion_frame
        from tests.test_plugin_host import FakeEntryPoint
        from tests.test_plugin_resources import services

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, files = services(root)
            plugin = Plugin("example.file", permissions=(MANAGED_ARTIFACT_WRITE_PERMISSION,))
            @plugin.tool(effects=("filesystem",), outputs=(CapabilityIOSlot(
                name="artifact", kind="file", required=True, delivery="generated_file", max_bytes=4096),))
            def create() -> Result:
                return Result(value={"created": True}, followup="none", content=ManagedArtifactPayload(
                    content={"created": True}, artifacts=(ManagedArtifactDraft(data="真实文件内容".encode(),
                        title="check", output_format="txt", mime_type="text/plain", send_to_user=True),)))
            host = PluginHost((PluginSelection(plugin.manifest.plugin_id, True),),
                entry_points_provider=lambda: (FakeEntryPoint(plugin.manifest.plugin_id, lambda: plugin),),
                contribution_policy=TrustedStatefulPluginContributionPolicy())
            host.bind_managed_artifact_sink(GeneratedFileManagedArtifactSink(files))
            try:
                self.assertEqual((await host.start())["status"], "active")
                handler = PluginCapabilityToolBridge(host, config_base_dir=root).build_tool_handlers()["example.file.create"]
                harness = _Harness([_tool_round_output("", handler.tool_type, "file-none")])
                harness.engine._execute_tool_call = lambda **kwargs: handler.execute(call={"arguments": {}},
                    context=ToolExecutionContext("owner", "session", 1, {}, client_mode="desktop_pet"))
                frame = await asyncio.to_thread(harness.run_sync, harness.payload())
                self.assertEqual(len(harness.script.generation_calls), 1)
                self.assertEqual(frame["speech"], "")
                ready = next(event for event in frame["tool_events"] if event["type"] == "generated_file_ready")
                saved = files.resolve_generated_artifact(profile_user_id="owner", session_id="session",
                    target=ready["generated_file"]["generated_handle"])
                self.assertEqual(Path(saved["absolute_path"]).read_text(encoding="utf-8"), "真实文件内容")
                projected = _desktop_tool_completion_frame(frame)
                smoke = await asyncio.to_thread(subprocess.run,
                    ["node", str(Path(__file__).resolve().parents[1] / "desktop_pet_next/scripts/plugin-followup-smoke.mjs")],
                    input=json.dumps(projected), text=True, capture_output=True, timeout=20)
                self.assertEqual(smoke.returncode, 0, smoke.stderr)
            finally:
                await host.stop()

    async def prepare(self, *, default="required", override="none", error=False, value=False):
        plugin = Plugin("example.followup")

        @plugin.tool(followup=default)
        def check() -> Result:
            return Result(value=value, followup=override, is_error=error, reason="check_failed" if error else "")

        host, engine = await self.start(plugin)
        return host, engine, engine._resolve_tool_handlers()["example.followup.check"]

    async def test_public_value_and_choice_survive_host_projection_and_wire(self):
        for value in (None, False, 0, [1, 2]):
            with self.subTest(value=value):
                host, _, _ = await self.prepare(value=value)
                result = await host.invoke("example.followup.check", {}, context=InvocationContext("owner", "session"))
                self.assertEqual((result.value, result.content, result.followup), (value, value, "none"))
                copied = capability_result_from_wire(capability_result_to_wire(result))
                self.assertEqual((copied.value, copied.followup), (value, "none"))
                self.assertEqual(project_capability_result(copied)["followup"], "none")

    async def test_none_override_and_defaults_use_real_sync_and_stream_model_loop(self):
        for default, override, expected_calls in (("required", "none", 1), ("none", None, 1),
                ("none", "required", 2), ("auto", None, 2)):
            host, _, handler = await self.prepare(default=default, override=override)
            for streaming in (False, True):
                with self.subTest(default=default, override=override, streaming=streaming):
                    script = [_tool_round_output("", handler.tool_type, "check-1")]
                    if expected_calls == 2:
                        script.append(_speech_output("已读取结果"))
                    harness = _Harness(script)
                    harness.engine._execute_tool_call = lambda **kw: handler.execute(
                        call=handler.normalize_call(kw["tool_call"]), context=ToolExecutionContext("owner", "session", 1, {}))
                    if streaming:
                        events = await asyncio.to_thread(harness.run_stream, harness.payload())
                        result = [event["payload"] for event in events if event.get("type") == "final"][0]
                    else:
                        result = await asyncio.to_thread(harness.run_sync, harness.payload())
                    self.assertEqual(len(harness.script.generation_calls), expected_calls)
                    self.assertEqual(result["speech"], "" if expected_calls == 1 else "已读取结果")
                    calls = harness.rec["record_memcore_tool_batch"].calls
                    self.assertEqual(len(calls), 1)
                    self.assertIn("false", calls[0][1]["items"][0][2])
            await host.stop()

    async def test_mixed_batch_and_unhandled_error_keep_model_consumer(self):
        for error in (False, True):
            _, _, handler = await self.prepare(error=error)
            script = _tool_round_output("", handler.tool_type, "check-1")
            if not error:
                script[NATIVE_TOOL_CALLS_FIELD].append({"type": "query", "id": "query-1", "arguments": {}})
            harness = _Harness([script, _speech_output("继续处理真实结果")])
            original = harness.engine._execute_tool_call
            harness.engine._execute_tool_call = lambda **kw: handler.execute(
                call=handler.normalize_call(kw["tool_call"]), context=ToolExecutionContext("owner", "session", 1, {})
            ) if kw["tool_call"]["type"] == handler.tool_type else original(**kw)
            result = await asyncio.to_thread(harness.run_sync, harness.payload())
            self.assertEqual(len(harness.script.generation_calls), 2)
            self.assertEqual(result["speech"], "继续处理真实结果")

    async def test_explicit_consumer_and_task_report_override_none_program_still_gets_value(self):
        _, engine, handler = await self.prepare(value=42)
        context = ToolExecutionContext("owner", "session", 1, {})
        for required_context in (replace(context, model_result_required=True),
                replace(context, execution_scope=TaskExecutionScope("C:/task", "task-1"))):
            result = await asyncio.to_thread(handler.execute, call={"arguments": {}}, context=required_context)
            self.assertTrue(result.followup.requires_model)
            self.assertEqual(result.followup.reason, "consumer_requires_result")
        scope = ResourceInvocation("example.caller", InvocationContext("owner", "session", "web"),
                                   can_invoke_capabilities=True)
        token = current_resource_invocation.set(scope)
        try:
            tools = Tools(ScopedPluginCapabilityPort("example.caller", EnginePluginCapabilityProvider(engine)))
            self.assertEqual(await tools.call(handler.tool_type, {}), 42)
            self.assertEqual((await tools.call_result(handler.tool_type, {})).followup, "none")
        finally:
            current_resource_invocation.reset(token)
            await scope.aclose()
        engine.llm.assert_not_called()

    async def test_program_error_with_none_returns_error_without_model(self):
        _, engine, handler = await self.prepare(error=True)
        scope = ResourceInvocation("example.caller", InvocationContext("owner", "session", "web"),
                                   can_invoke_capabilities=True)
        token = current_resource_invocation.set(scope)
        try:
            tools = Tools(ScopedPluginCapabilityPort("example.caller", EnginePluginCapabilityProvider(engine)))
            with self.assertRaises(ToolCallError) as caught:
                await tools.call(handler.tool_type, {})
            self.assertEqual(caught.exception.result.followup, "none")
            self.assertEqual(caught.exception.result.reason, "check_failed")
        finally:
            current_resource_invocation.reset(token)
            await scope.aclose()
        engine.llm.assert_not_called()

    async def test_closed_full_schema_can_own_finish_turn_as_business_data(self):
        plugin = Plugin("example.schema")
        schema = {"$defs": {"args": {"type": "object", "additionalProperties": False,
            "properties": {"finish_turn": {"type": "string"}}, "required": ["finish_turn"]}}, "$ref": "#/$defs/args"}
        seen = []
        @plugin.tool(input_schema=schema, followup="none")
        def check(arguments) -> str:
            seen.append(arguments)
            return arguments["finish_turn"]
        _, engine = await self.start(plugin)
        handler = engine._resolve_tool_handlers()["example.schema.check"]
        self.assertEqual(handler.tool_spec().input_schema, schema)
        result = await asyncio.to_thread(handler.execute,
            call=handler.normalize_call({"type": handler.tool_type, "arguments": {"finish_turn": "business"}}),
            context=ToolExecutionContext("owner", "session", 1, {}))
        self.assertEqual(seen, [{"finish_turn": "business"}])
        self.assertTrue(can_finish_tool_batch([result]))
        self.assertEqual(result.capability_result.value, "business")

    async def test_public_result_in_real_isolated_worker_reaches_model_consumer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            site = root / "site"
            package, metadata = site / "followup_fixture", site / "followup_fixture-0.1.0.dist-info"
            package.mkdir(parents=True)
            metadata.mkdir()
            (package / "__init__.py").write_text(textwrap.dedent('''
                from akane_plugin import Plugin, Result
                plugin = Plugin("example.followup")
                @plugin.tool
                def check() -> Result:
                    return Result(value={"changed": False}, followup="none")
                def create_plugin(): return plugin
            '''), encoding="utf-8")
            (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: followup-fixture\nVersion: 0.1.0\n", encoding="utf-8")
            (metadata / "entry_points.txt").write_text("[akane.plugins.v1]\nexample.followup = followup_fixture:create_plugin\n", encoding="utf-8")
            process = PluginGenerationProcess(project_root=Path(__file__).resolve().parents[1], site_dir=site,
                plugin_id="example.followup", work_dir=root / "worker")
            try:
                await asyncio.to_thread(process.start)
                result = await process.invoke("example.followup.check", {}, context=InvocationContext("owner", "session"))
                self.assertEqual((result.value, result.followup), ({"changed": False}, "none"))
                from companion_v01.plugin_tool_bridge import PluginCapabilityToolHandler
                adapter = SimpleNamespace(invoke=lambda capability_id, arguments, context: process.invoke(capability_id, arguments, context=context))
                handler = PluginCapabilityToolHandler(capability_id="example.followup.check", adapter=adapter,
                    descriptor=process.capability_descriptors["example.followup.check"], config_base_dir=root)
                harness = _Harness([_tool_round_output("", handler.tool_type, "worker-check")])
                harness.engine._execute_tool_call = lambda **kw: handler.execute(
                    call=handler.normalize_call(kw["tool_call"]), context=ToolExecutionContext("owner", "session", 1, {}))
                frame = await asyncio.to_thread(harness.run_sync, harness.payload())
                self.assertEqual(len(harness.script.generation_calls), 1)
                self.assertEqual(frame["speech"], "")
            finally:
                await asyncio.to_thread(process.stop)
