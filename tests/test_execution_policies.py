"""Selected public policies through real dispatch, lifecycle and worker boundaries."""
import asyncio
import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
import shutil
import threading

from capcore import InvocationContext
from akane_plugin import Plugin, ServiceDependency, ToolContext
from companion_v01.capability_registry import ExecutorBroker
from companion_v01.execution_policies import ExecutionPolicyPipeline, load_execution_policies
from companion_v01.extension_management import ExtensionManagementService, PluginSelectionStore
from companion_v01.plugin_generation import PluginGenerationProcess
from companion_v01.plugin_generation_candidate import PluginGenerationCandidateBuilder
from companion_v01.plugin_generation_runtime import PluginGenerationRuntime
from companion_v01.plugin_installation import ManagedPluginArtifactStore
from companion_v01.plugin_resources import ResourceInvocation
from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_capability_calls import EnginePluginCapabilityProvider
from companion_v01.plugin_contribution_policy import TrustedStatefulPluginContributionPolicy
from companion_v01.plugin_host import PluginHost
from companion_v01.plugin_service_dependencies import resolve_dependencies
from companion_v01.plugin_tool_bridge import PluginCapabilityToolBridge
from companion_v01.tool_invocation import ToolInvocation
from companion_v01.tool_orchestration_engine import execute_tool_invocation
from tests.test_plugin_engine_bridge import EngineFacade
from tests.test_plugin_host import FakeEntryPoint
from tests.test_plugin_resources import services

ROOT = Path(__file__).resolve().parents[1]


class PolicyConfigurationTests(unittest.TestCase):
    def test_bot_factory_binds_deployment_policies_and_bootstrap_requirements(self):
        import config
        from companion_v01.bot_runtime import BotRuntimeFactory
        from companion_v01.settings_overrides import RuntimeConfigView
        from tests.test_bot_runtime import _runtime
        configuration = RuntimeConfigView(config, {"AKANE_EXECUTION_POLICIES": '[{"policy_id":"example.guard"}]',
            "AKANE_ADMIN_TOKEN": "test-admin", "QQ_BRIDGE_ENABLED": False, "AKANE_DESKTOP_SATELLITE_TOKEN": ""})
        factory = BotRuntimeFactory(config_module=configuration, assets_dir=ROOT / "web/assets")
        bot = replace(_runtime()[0].bot_config, care_enabled=False, plugins=())
        with tempfile.TemporaryDirectory() as directory:
            runtime = factory.create(data_root=Path(directory), bot_config=bot, explicit_data_root=True)
            try:
                pipeline = runtime.engine.executor_broker.execution_policies
                self.assertIs(pipeline.source, runtime.engine.plugin_capability_source)
                self.assertEqual(pipeline.selections[0].policy_id, "example.guard")
                self.assertEqual(runtime.plugin_runtime._builder.bootstrap_services, (ServiceDependency("execution_policy.example.guard"),))
            finally:
                asyncio.run(runtime.stop())
                runtime.instance_runtime.release()

    def test_configuration_and_bootstrap_use_the_service_dependency_graph(self):
        for invalid in ('{}', '[{"policy_id":"bad path"}]', '[{"policy_id":"example.guard","unknown":1}]',
                        '[{"policy_id":"example.guard"},{"policy_id":"example.guard"}]'):
            with self.assertRaisesRegex(ValueError, '^execution_policy_configuration_invalid$'):
                load_execution_policies(invalid)
        selected, = load_execution_policies('[{"policy_id":"example.guard"}]')
        contributions = {
            "aaa.business": {},
            "zzz.policy": {"services": [{"service_id": selected.dependency.service_id, "version": 1}],
                           "requires_services": [{"service_id": "database", "version": 1}]},
            "yyy.dependency": {"services": [{"service_id": "database", "version": 1}]},
        }
        plan = resolve_dependencies(contributions, bootstrap_services=(selected.dependency,))
        self.assertEqual(plan.activation_order, ("yyy.dependency", "zzz.policy", "aaa.business"))
        missing = resolve_dependencies({}, bootstrap_services=(selected.dependency,))
        self.assertEqual(missing.bootstrap_errors[0]["reason"], "service_dependency_missing")
        contributions["yyy.dependency"]["requires_services"] = [{"service_id": selected.dependency.service_id, "version": 1}]
        cyclic = resolve_dependencies(contributions, bootstrap_services=(selected.dependency,))
        self.assertTrue(cyclic.bootstrap_errors)
        self.assertNotIn("zzz.policy", cyclic.activation_order)


class PublicPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def start(self, *plugins):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        def entry(plugin):
            def factory():
                return plugin
            return FakeEntryPoint(plugin.manifest.plugin_id, factory)
        self.host = PluginHost(tuple(PluginSelection(item.manifest.plugin_id, True) for item in plugins),
            entry_points_provider=lambda: tuple(entry(item) for item in plugins),
            contribution_policy=TrustedStatefulPluginContributionPolicy())
        self.bridge = PluginCapabilityToolBridge(self.host, config_base_dir=root)
        self.engine = EngineFacade(self.bridge)
        self.engine.store, _, self.files = services(root)
        from companion_v01.plugin_managed_artifacts import GeneratedFileManagedArtifactSink
        self.host.bind_managed_artifact_sink(GeneratedFileManagedArtifactSink(self.files))
        self.engine.capability_config_base_dir = root
        self.engine.executor_broker = ExecutorBroker(None)
        self.host.bind_capability_provider(EnginePluginCapabilityProvider(self.engine))
        self.addAsyncCleanup(self.host.stop)
        status = await self.host.start()
        self.assertTrue(all(item["status"] == "active" for item in status["plugins"]), status)

    def select(self, selection):
        pipeline = ExecutionPolicyPipeline(load_execution_policies(selection), source=self.bridge)
        self.engine.executor_broker.execution_policies = pipeline
        return pipeline

    async def call(self, tool_id, arguments, invocation_id):
        return await asyncio.to_thread(execute_tool_invocation, self.engine,
            invocation=ToolInvocation(tool_id, {"arguments": arguments}, id=invocation_id),
            profile_user_id="owner", session_id="session", visual_payload={}, now_ts=1)

    async def test_transform_order_program_value_model_projection_and_read_only_observer(self):
        from akane_plugin import Result
        guard = Plugin("example.transforms")
        first, second = guard.policy("example.first"), guard.policy("example.second")
        seen = []
        @first.transform
        def transform_first(request):
            if request["tool_id"] != "example.business.value":
                return {"action": "keep"}
            seen.append(("first", request["outcome"]["value"]))
            return {"action": "replace", "value": request["outcome"]["value"] * 2}
        @second.transform
        def transform_second(request):
            if request["tool_id"] != "example.business.value":
                return {"action": "keep"}
            seen.append(("second", request["outcome"]["value"]))
            return {"action": "replace", "value": request["outcome"]["value"] + 1}
        @first.observe
        def observe(request):
            seen.append(("observe", request["outcome"].get("value")))
            request["outcome"]["value"] = -100
            raise RuntimeError("private observer exception")
        business = Plugin("example.business", permissions=("capability.invoke",))
        @business.tool(output_schema={"type": "integer"})
        def value():
            return Result(value=4, content={"summary": "old producer presentation"})
        @business.tool
        async def combined(ctx: ToolContext) -> int:
            return await ctx.tools.call("example.business.value", {})
        await self.start(guard, business)
        pipeline = self.select('[{"policy_id":"example.second"},{"policy_id":"example.first"}]')
        result, envelope = await self.call("example.business.value", {}, "transformed-model")
        self.assertEqual(result.capability_result.value, 10)
        self.assertEqual(envelope.status, "ok")
        self.assertIn('"value": 10', result.followup_context)
        self.assertNotIn("old producer", result.followup_context)
        self.assertEqual(seen, [("second", 4), ("first", 5), ("observe", 10)])
        self.assertEqual(pipeline.snapshot()["policies"][1]["stages"], ["transform", "observe"])
        combined, _ = await self.call("example.business.combined", {}, "transformed-program")
        self.assertEqual(combined.capability_result.value, 10)
        self.assertEqual(seen[-2:], [("observe", 10), ("observe", 10)])

    async def test_wrap_retries_failed_read_only_dispatch_and_exposes_attempt_lifecycle(self):
        from capcore import CapabilityResult

        guard = Plugin("example.wrap")
        policy = guard.policy("example.wrap")
        phases = []

        @policy.wrap
        def wrap(request):
            phases.append((request["phase"], request["attempt"], request.get("outcome", {}).get("status")))
            if request["phase"] == "before":
                return {"action": "continue", "max_attempts": 2}
            return {"action": "retry" if request["outcome"]["status"] == "temporary_failure" else "return"}

        business = Plugin("example.business")
        calls = []

        @business.tool(output_schema={"type": "integer"})
        def flaky():
            calls.append(len(calls) + 1)
            if len(calls) == 1:
                return CapabilityResult(is_error=True, status="temporary_failure", reason="upstream_busy")
            return 42

        await self.start(guard, business)
        pipeline = self.select('[{"policy_id":"example.wrap"}]')
        result, envelope = await self.call("example.business.flaky", {}, "wrapped-retry")
        self.assertEqual(result.capability_result.value, 42)
        self.assertEqual(envelope.status, "ok")
        self.assertEqual(calls, [1, 2])
        self.assertEqual(phases, [
            ("before", 1, None),
            ("after", 1, "temporary_failure"),
            ("before", 2, "temporary_failure"),
            ("after", 2, "ok"),
        ])
        self.assertEqual(pipeline.snapshot()["policies"][0]["stages"], ["wrap"])

    async def test_wrap_never_retries_effectful_dispatch_and_reports_real_diagnostic(self):
        from capcore import CapabilityResult

        guard = Plugin("example.wrap")
        policy = guard.policy("example.wrap")

        @policy.wrap
        def wrap(request):
            if request["phase"] == "before":
                return {"action": "continue", "max_attempts": 2}
            return {"action": "retry"}

        business = Plugin("example.business", permissions=("capability.prompt.invoke", "network.read"))
        calls = []

        @business.tool(output_schema={"type": "integer"}, effects=("network",))
        def effectful():
            calls.append(True)
            return CapabilityResult(is_error=True, status="temporary_failure", reason="upstream_busy")

        await self.start(guard, business)
        pipeline = self.select('[{"policy_id":"example.wrap"}]')
        result, envelope = await self.call("example.business.effectful", {}, "wrapped-effect")
        self.assertTrue(result.capability_result.is_error)
        self.assertEqual(result.capability_result.status, "temporary_failure")
        self.assertEqual(calls, [True])
        self.assertEqual(pipeline.snapshot()["diagnostics"][-1]["reason"],
                         "execution_policy_retry_forbidden_for_effectful_tool")

    async def test_transform_failure_keeps_execution_receipt_and_replay_never_reexecutes(self):
        guard = Plugin("example.transform")
        policy = guard.policy("example.transform")
        actions, observed = [], []
        @policy.transform
        def transform(request):
            fault = request["options"].get("fault")
            if fault == "throw":
                raise RuntimeError("private transform exception")
            if fault == "invalid":
                return {"action": "replace"}
            if fault == "mutate":
                request["outcome"]["value"] = 999
                return {"action": "keep"}
            return {"action": "replace", "value": "wrong output type"}
        @policy.observe
        def observe(request):
            observed.append(request["outcome"])
        business = Plugin("example.business")
        @business.tool
        def action() -> int:
            actions.append("executed")
            return 7
        await self.start(guard, business)
        for fault in ("schema", "throw", "invalid"):
            self.select([{"policy_id": "example.transform", "options": {"fault": fault}}])
            result, envelope = await self.call("example.business.action", {}, fault)
            self.assertEqual(result.capability_result.status, "result_processing_failed")
            self.assertEqual(envelope.status, "error")
            details = result.capability_result.content["policy_failure"]
            self.assertEqual(details["phase"], "transform")
            self.assertEqual(envelope.data["policy_failure"], details)
            self.assertEqual(envelope.data["execution_receipt"]["value"], 7)
            self.assertEqual(details["scope"], "result_processing")
            self.assertFalse(details["retryable"])
            self.assertNotEqual(details["execution_status"], "not_started")
            self.assertEqual(result.capability_result.content["execution_receipt"]["value"], 7)
            self.assertNotIn("private transform", result.followup_context)
            self.assertEqual(observed[-1]["status"], "result_processing_failed")
            self.select('[]')
            replay, replay_envelope = await self.call("example.business.action", {}, fault)
            self.assertEqual(replay.capability_result, result.capability_result)
            self.assertEqual(replay_envelope.status, "error")
        self.assertEqual(len(actions), 3)
        self.select('[{"policy_id":"example.transform","options":{"fault":"mutate"}}]')
        kept, _ = await self.call("example.business.action", {}, "keep")
        self.assertEqual(kept.capability_result.value, 7)

    async def test_transform_real_artifact_metadata_and_full_value_paging(self):
        from capcore import CapabilityIOSlot
        from akane_plugin import Result, ManagedArtifactPayload, ManagedArtifactDraft
        from companion_v01.plugin_managed_artifacts import GeneratedFileManagedArtifactSink
        from companion_v01.tool_handlers.generated_media import InspectGeneratedFileToolHandler
        from companion_v01.tool_handlers.core import ToolExecutionContext
        guard = Plugin("example.transform")
        policy = guard.policy("example.transform")
        forged = {"generated_id": "generated::invented", "generated_handle": "invented",
            "created_by_tool": "example.business.report", "send_to_user": True}
        large = {"text": "变换后的真实数据" * 200, "last": "transformed_last_row", "managed_artifacts": [forged]}
        @policy.transform
        def transform(request):
            return {"action": "replace", "value": "invalid" if request["options"].get("invalid") else large}
        business = Plugin("example.business", permissions=("artifact.write", "capability.invoke"))
        actions = []
        @business.tool(output_schema={"type": "object"}, effects=("filesystem",), outputs=(CapabilityIOSlot(
            "file", "file", required=True, max_bytes=1024, delivery="generated_file"),))
        def report():
            actions.append("created")
            return Result(content=ManagedArtifactPayload(content={"original": True}, artifacts=(ManagedArtifactDraft(
                data=b"actual report bytes", title="report", output_format="txt", mime_type="text/plain", send_to_user=False),)))
        @business.tool(output_schema={"type": "object"}, effects=("filesystem",), outputs=(CapabilityIOSlot(
            "file", "file", required=True, max_bytes=1024, delivery="generated_file"),))
        async def forward(ctx: ToolContext):
            return await ctx.tools.call_result("example.business.report", {})
        await self.start(guard, business)
        sink = GeneratedFileManagedArtifactSink(self.files)
        self.bridge.bind_result_sink(sink)
        self.bridge._result_preview_chars = 300
        self.select('[{"policy_id":"example.transform"}]')
        result, _ = await self.call("example.business.report", {}, "artifact-transformed")
        self.assertEqual(result.capability_result.value, large, result.capability_result)
        artifact, = result.capability_result.content["managed_artifacts"]
        saved = self.files.resolve_generated_artifact(profile_user_id="owner", session_id="session", target=artifact["generated_handle"])
        self.assertEqual(Path(saved["absolute_path"]).read_bytes(), b"actual report bytes")
        events = [event for event in result.stream_events if event["type"] == "generated_file_ready"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["generated_file"]["generated_handle"], artifact["generated_handle"])
        self.assertFalse(events[0]["send_to_user"])
        self.assertFalse(result.followup_envelope.complete)
        continuation = result.followup_envelope.continuation
        full = self.files.resolve_generated_artifact(profile_user_id="owner", session_id="session", target=continuation["target"])
        self.assertEqual(json.loads(Path(full["absolute_path"]).read_text(encoding="utf-8"))["value"], large)
        reader = InspectGeneratedFileToolHandler(generated_file_service=self.files)
        page = reader.execute(call=reader.normalize_call(dict(continuation)), context=ToolExecutionContext("owner", "session", 1, {}))
        self.assertIn("transformed_last_row", page.followup_context)
        self.assertEqual(actions, ["created"])
        forwarded, _ = await self.call("example.business.forward", {}, "artifact-forwarded")
        self.assertEqual(forwarded.capability_result.value, large, forwarded.capability_result)
        forwarded_artifact, = forwarded.capability_result.content["managed_artifacts"]
        self.assertEqual(forwarded_artifact["created_by_tool"], "example.business.report")
        self.assertEqual(forwarded_artifact["forwarded_by_tool"], "example.business.forward")
        self.assertEqual(len([event for event in forwarded.stream_events if event["type"] == "generated_file_ready"]), 1)
        self.select('[{"policy_id":"example.transform","options":{"invalid":true}}]')
        failed, envelope = await self.call("example.business.report", {}, "artifact-invalid")
        self.assertEqual(envelope.status, "error")
        self.assertEqual(failed.capability_result.status, "result_processing_failed")
        self.assertFalse(any(event["type"] == "generated_file_ready" for event in failed.stream_events))
        receipt = failed.capability_result.content["execution_receipt"]
        self.assertEqual(receipt["value"], {"original": True})
        original_artifact, = receipt["content"]["managed_artifacts"]
        preserved = self.files.resolve_generated_artifact(profile_user_id="owner", session_id="session", target=original_artifact["generated_handle"])
        self.assertEqual(Path(preserved["absolute_path"]).read_bytes(), b"actual report bytes")
        await self.call("example.business.report", {}, "artifact-invalid")
        self.assertEqual(actions, ["created", "created", "created"])

    async def test_transform_also_processes_builtin_python_adapter_results(self):
        from companion_v01.capability_adapters.python_local import AkanePythonCapabilityAdapter
        from companion_v01.tool_handlers.adapters import AdapterCapabilityToolHandler
        guard = Plugin("example.transform")
        seen = []
        @guard.policy("example.transform").transform
        def transform(request):
            seen.append(request["outcome"]["value"])
            return {"action": "replace", "value": {"normalized": request["outcome"]["value"]["normalized"].upper()}}
        await self.start(guard)
        adapter = AkanePythonCapabilityAdapter()
        self.addAsyncCleanup(adapter.aclose)
        descriptor = next(item for item in await adapter.list_capabilities() if item.id == "python.akane.normalize_text")
        self.engine.tool_handlers[descriptor.id] = AdapterCapabilityToolHandler(
            capability_id=descriptor.id, adapter=adapter, descriptor=descriptor,
            config_base_dir=self.engine.capability_config_base_dir)
        self.select('[{"policy_id":"example.transform"}]')
        result, envelope = await self.call(descriptor.id, {"text": "hello   world"}, "builtin")
        self.assertEqual(seen, [{"normalized": "hello world"}])
        self.assertEqual(result.capability_result.value, {"normalized": "HELLO WORLD"})
        self.assertIn("HELLO WORLD", result.followup_context)
        self.assertEqual(envelope.status, "ok")

    async def test_present_rewrites_model_text_in_order_without_touching_program_value(self):
        from akane_plugin import Result
        guard = Plugin("example.present")
        first, second = guard.policy("example.first"), guard.policy("example.second")
        seen = []

        @first.present
        def present_first(request):
            if request["tool_id"] != "example.business.value":
                return {"action": "keep"}
            seen.append(("first", request["rendered"], request["outcome"].get("value")))
            return {"action": "replace", "text": request["rendered"] + "|first"}

        @second.present
        def present_second(request):
            if request["tool_id"] != "example.business.value":
                return {"action": "keep"}
            seen.append(("second", request["rendered"], request["outcome"].get("value")))
            return {"action": "replace", "text": request["rendered"] + "|second"}

        business = Plugin("example.business", permissions=("capability.invoke",))

        @business.tool(output_schema={"type": "integer"})
        def value():
            return Result(value=4, content={"summary": "producer text"})

        @business.tool
        async def combined(ctx: ToolContext) -> int:
            return await ctx.tools.call_result("example.business.value", {})

        await self.start(guard, business)
        self.select('[{"policy_id":"example.second"},{"policy_id":"example.first"}]')
        result, _ = await self.call("example.business.value", {}, "present-model")
        self.assertEqual(result.capability_result.value, 4)
        self.assertTrue(result.followup_context.endswith("|second|first"), result.followup_context)
        self.assertEqual([item[0] for item in seen], ["second", "first"])
        self.assertEqual([item[2] for item in seen], [4, 4])

        combined_result, _ = await self.call("example.business.combined", {}, "present-program")
        self.assertEqual(combined_result.capability_result.value, 4)
        self.assertEqual([item[0] for item in seen], ["second", "first"])

    async def test_present_adapter_failure_keeps_original_text_and_program_value(self):
        from companion_v01.capability_adapters.python_local import AkanePythonCapabilityAdapter
        from companion_v01.tool_handlers.adapters import AdapterCapabilityToolHandler
        from companion_v01.tool_handlers.core import ToolExecutionContext
        guard = Plugin("example.present")
        seen = []

        @guard.policy("example.present").present
        def present(request):
            seen.append(request["tool_id"])
            fault = request["options"].get("fault")
            if fault == "throw":
                raise RuntimeError("private presenter exception")
            if fault == "invalid":
                return {"action": "replace"}
            return {"action": "replace", "text": "DISPLAY:" + request["rendered"]}

        await self.start(guard)
        adapter = AkanePythonCapabilityAdapter()
        self.addAsyncCleanup(adapter.aclose)
        descriptor = next(item for item in await adapter.list_capabilities() if item.id == "python.akane.normalize_text")
        handler = AdapterCapabilityToolHandler(
            capability_id=descriptor.id, adapter=adapter, descriptor=descriptor,
            config_base_dir=self.engine.capability_config_base_dir)
        self.engine.tool_handlers[descriptor.id] = handler

        self.select("[]")
        baseline, _ = await self.call(descriptor.id, {"text": "hello   world"}, "present-baseline")
        self.select('[{"policy_id":"example.present"}]')
        result, _ = await self.call(descriptor.id, {"text": "hello   world"}, "present-adapter")
        self.assertEqual(result.capability_result.value, baseline.capability_result.value)
        self.assertTrue(result.followup_context.startswith("DISPLAY:"), result.followup_context)

        program_result = await asyncio.to_thread(
            handler.execute,
            call=handler.normalize_call({"type": descriptor.id, "arguments": {"text": "hello   world"}}),
            context=ToolExecutionContext("owner", "session", 1, {}, result_consumer="program"),
        )
        self.assertEqual(program_result.capability_result.value, baseline.capability_result.value)
        self.assertEqual(seen, [descriptor.id])

        original_text = baseline.followup_context
        for fault in ("throw", "invalid"):
            pipeline = self.select([{"policy_id": "example.present", "options": {"fault": fault}}])
            failed, _ = await self.call(descriptor.id, {"text": "hello   world"}, "present-" + fault)
            self.assertEqual(failed.capability_result.value, baseline.capability_result.value)
            self.assertEqual(failed.followup_context, original_text)
            diagnostic, = [item for item in pipeline.snapshot()["diagnostics"] if item["phase"] == "present"]
            self.assertEqual(diagnostic["reason"], "execution_policy_presenter_failed")

    async def test_present_long_result_saves_final_display_text_for_continuation(self):
        from akane_plugin import Result
        guard = Plugin("example.present")
        expected = "最终展示文本：" + ("这一段来自展示策略。" * 220)

        @guard.policy("example.present").present
        def present(request):
            return {"action": "replace", "text": expected}

        business = Plugin("example.business")

        @business.tool(output_schema={"type": "object"})
        def report():
            return Result(value={"canonical": True}, content={"producer": "old"})

        await self.start(guard, business)
        from companion_v01.plugin_managed_artifacts import GeneratedFileManagedArtifactSink
        self.bridge.bind_result_sink(GeneratedFileManagedArtifactSink(self.files))
        self.bridge._result_preview_chars = 300
        self.select('[{"policy_id":"example.present"}]')
        result, _ = await self.call("example.business.report", {}, "present-long")
        self.assertEqual(result.capability_result.value, {"canonical": True})
        self.assertFalse(result.followup_envelope.complete)
        self.assertIn(expected[:300], result.followup_context)
        continuation = result.followup_envelope.continuation
        saved = self.files.resolve_generated_artifact(
            profile_user_id="owner", session_id="session", target=continuation["target"])
        self.assertEqual(saved["output_format"], "txt")
        self.assertEqual(Path(saved["absolute_path"]).read_text(encoding="utf-8"), expected)
        self.assertNotIn("producer", Path(saved["absolute_path"]).read_text(encoding="utf-8"))

        from companion_v01.tool_handlers.generated_media import InspectGeneratedFileToolHandler
        from companion_v01.tool_handlers.core import ToolExecutionContext
        reader = InspectGeneratedFileToolHandler(generated_file_service=self.files)
        page = reader.execute(
            call=reader.normalize_call(dict(continuation)),
            context=ToolExecutionContext("owner", "session", 1, {}),
        )
        self.assertIn("来自展示策略", page.followup_context)

    async def test_transform_preserves_explicit_falsy_values_and_cannot_rewrite_business_failure(self):
        from akane_plugin import Result
        guard = Plugin("example.transform")
        transformed = []
        @guard.policy("example.transform").transform
        def transform(request):
            transformed.append(request["tool_id"])
            return {"action": "replace", "value": request["options"]["value"]}
        business = Plugin("example.business")
        @business.tool(output_schema={"type": ["integer", "boolean", "null"]})
        def value():
            return 5
        @business.tool
        def failed() -> int:
            return Result(is_error=True, status="partial", reason="actual_delivery_failed", content={"sent": False})
        await self.start(guard, business)
        for index, value in enumerate((0, False, None)):
            self.select([{"policy_id": "example.transform", "options": {"value": value}}])
            result, envelope = await self.call("example.business.value", {}, f"falsy-{index}")
            self.assertIs(result.capability_result.value, value)
            self.assertTrue(result.capability_result.has_value)
            self.assertEqual(envelope.status, "ok")
            self.assertIn(json.dumps({"value": value}), result.followup_context)
        failure, _ = await self.call("example.business.failed", {}, "business-failure")
        self.assertEqual(failure.capability_result.reason, "actual_delivery_failed")
        self.assertEqual(failure.capability_result.content, {"sent": False})
        self.assertEqual(transformed, ["example.business.value"] * 3)

    async def test_selected_before_and_read_only_observer_keep_real_result_and_receipt(self):
        guard = Plugin("example.guard")
        policy = guard.policy("example.guard")
        observations, actions = [], []
        @policy.before
        def decide(request):
            request["arguments"]["text"] = "attempted mutation"
            if request["options"].get("raise"):
                raise RuntimeError("private exception must not be published")
            if request["options"].get("invalid"):
                return {"decision": "invented"}
            if request["options"].get("deny"):
                return {"decision": "deny", "reason": "deployment_disabled"}
            return {"decision": "allow"}
        @policy.observe
        def observe(request):
            observations.append(request)
            request["outcome"]["value"] = "attempted replacement"
            raise RuntimeError("private exception must not be published")
        business = Plugin("example.business", permissions=("capability.invoke",))
        @business.tool
        def echo(text: str) -> str:
            actions.append(text)
            return text
        @business.tool(output_schema={"type": "object"})
        async def inspect(ctx: ToolContext):
            return await ctx.tools.policies()
        await self.start(guard, business)
        result, _ = await self.call("example.business.echo", {"text": "unselected"}, "unselected")
        self.assertEqual(result.capability_result.value, "unselected")
        self.assertEqual(observations, [])
        pipeline = self.select('[{"policy_id":"example.guard"}]')
        result, envelope = await self.call("example.business.echo", {"text": "real"}, "allow")
        self.assertEqual(result.capability_result.value, "real")
        self.assertEqual(envelope.status, "ok")
        self.assertEqual(observations[0]["arguments"]["text"], "real")
        diagnostic, = pipeline.snapshot()["diagnostics"]
        self.assertEqual(diagnostic["phase"], "observe")
        self.assertNotIn("private", str(diagnostic))
        inspection = await self.host.invoke("example.business.inspect", {}, context=InvocationContext("owner", "session", "web"))
        self.assertEqual(inspection.value["policies"][0]["stages"], ["before", "observe"])
        self.assertNotIn("options", inspection.value["policies"][0])
        for fault in ("raise", "invalid"):
            self.select([{"policy_id": "example.guard", "options": {fault: True}}])
            failed, envelope = await self.call("example.business.echo", {"text": "never"}, fault)
            self.assertEqual(failed.capability_result.status, "policy_failed")
            self.assertEqual(envelope.status, "error")
            self.assertNotIn("private exception", str(failed.capability_result))
        self.select('[{"policy_id":"example.guard","options":{"deny":true}}]')
        denied, envelope = await self.call("example.business.echo", {"text": "blocked"}, "deny")
        self.assertEqual(denied.capability_result.status, "policy_rejected")
        self.assertEqual(envelope.status, "error")
        self.assertEqual(denied.capability_result.content["reason"], "deployment_disabled")
        self.select('[]')
        replay, _ = await self.call("example.business.echo", {"text": "blocked"}, "deny")
        self.assertEqual(replay.capability_result.status, "policy_rejected")
        self.assertEqual(actions, ["unselected", "real"])

    async def test_policy_dependency_calls_have_no_conversation_authority(self):
        guard = Plugin("example.guard", permissions=("capability.invoke",), requires_services=(ServiceDependency("calculation"),))
        dependency = Plugin("example.dependency")
        @dependency.service("calculation").method
        def double(value: int) -> int:
            return value * 2
        policy = guard.policy("example.guard")
        @policy.before
        async def decide(request, ctx: ToolContext):
            self.assertTrue(ctx.invocation.global_scope)
            self.assertEqual(ctx.invocation.profile_user_id, "")
            value = await ctx.services.call("calculation", "double", {"value": 21})
            self.assertEqual(value, 42)
            result = await ctx.tools.call_result("example.business.effect", {})
            self.assertEqual(result.reason, "capability_context_required")
            return {"decision": "allow"}
        business = Plugin("example.business")
        @business.tool
        def value() -> int:
            return 7
        @business.tool(effects=("network",), risk="low", confirm="never")
        def effect() -> int:
            raise AssertionError("effect must not execute in policy scope")
        @business.tool(effects=("network",), risk="high", confirm="always")
        def confirmed_effect() -> int:
            raise AssertionError("a policy allow decision is not user approval")
        business.manifest = replace(business.manifest,
            permissions=(*business.manifest.permissions, "network.read"))
        await self.start(guard, dependency, business)
        self.select('[{"policy_id":"example.guard"}]')
        result, _ = await self.call("example.business.value", {}, "one")
        self.assertEqual(result.capability_result.value, 7, result.capability_result)
        approval, _ = await self.call("example.business.confirmed_effect", {}, "approval")
        self.assertEqual(approval.capability_result.status, "approval_required", approval.capability_result)

    async def test_order_and_withdrawal_while_before_is_running(self):
        plugin = Plugin("example.ordered")
        first, second = plugin.policy("example.first"), plugin.policy("example.second")
        calls, entered, release = [], threading.Event(), threading.Event()
        @first.before
        def before_first(request):
            calls.append("first")
            return {"decision": "allow"}
        @second.before
        async def before_second(request):
            calls.append("second")
            if request["options"].get("pause"):
                entered.set()
                while not release.is_set():
                    await asyncio.sleep(0.01)
            return {"decision": "allow"}
        business = Plugin("example.business")
        @business.tool
        def value() -> int:
            calls.append("business")
            return 7
        await self.start(plugin, business)
        self.select('[{"policy_id":"example.second"},{"policy_id":"example.first"}]')
        result, _ = await self.call("example.business.value", {}, "ordered")
        self.assertEqual(result.capability_result.value, 7)
        self.assertEqual(calls, ["second", "first", "business"])
        self.select('[{"policy_id":"example.second","options":{"pause":true}}]')
        pending = asyncio.create_task(self.call("example.business.value", {}, "withdrawn"))
        reconfigured = None
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 3))
            reconfigured = asyncio.create_task(self.host.reconfigure((PluginSelection("example.business", True),)))
            async with asyncio.timeout(3):
                while self.host.state != "stopping":
                    await asyncio.sleep(0.01)
            release.set()
            denied, envelope = await asyncio.wait_for(pending, 5)
            await asyncio.wait_for(reconfigured, 5)
            self.assertTrue(denied.capability_result.is_error)
            self.assertEqual(envelope.status, "error")
            self.assertEqual(calls.count("business"), 1)
        finally:
            release.set()
            if not pending.done():
                await pending
            if reconfigured is not None and not reconfigured.done():
                await reconfigured

    async def test_native_presentation_and_remote_socket_dispatch_share_policy(self):
        from fastapi.testclient import TestClient
        from companion_v01.capability_registry import OPEN_BROWSER_TOOL_SPEC
        from companion_v01.desktop_satellite import DesktopSatelliteService
        from companion_v01.tool_runtime import OpenMusicSearchToolHandler
        from companion_v01.tool_orchestration_engine import _execute_open_browser_with_broker
        from tests.test_capability_fabric_m66 import CapabilityFabricM66Tests, _registration, _execution_message
        guard = Plugin("example.guard")
        policy = guard.policy("example.guard")
        seen = []
        @policy.before
        def before(request):
            seen.append((request["tool_id"], request["executor"]))
            return {"decision": "deny" if request["options"].get("deny") else "allow", "reason": "deployment_disabled"}
        await self.start(guard)
        self.engine.tool_handlers["open_music_search"] = OpenMusicSearchToolHandler()
        self.select('[{"policy_id":"example.guard","options":{"deny":true}}]')
        async def music(identifier):
            return await asyncio.to_thread(execute_tool_invocation, self.engine,
                invocation=ToolInvocation("open_music_search", {"title": "Song", "platform": "qq_music"}, id=identifier),
                profile_user_id="owner", session_id="session", visual_payload={}, now_ts=1)
        blocked, envelope = await music("native-blocked")
        self.assertEqual(blocked.capability_result.status, "policy_rejected")
        self.assertEqual(envelope.status, "error")
        self.assertFalse(any(event["type"] == "browser_open_requested" for event in blocked.stream_events))
        self.select('[{"policy_id":"example.guard"}]')
        allowed, _ = await music("native-allowed")
        self.assertTrue(any(event["type"] == "browser_open_requested" for event in allowed.stream_events))
        def remote():
            service = DesktopSatelliteService(instance_id="instance-a", token="test-device")
            broker = ExecutorBroker(service, execution_policies=self.select('[{"policy_id":"example.guard","options":{"deny":true}}]'))
            with TestClient(CapabilityFabricM66Tests()._app(service)) as client:
                with client.websocket_connect("/capabilities/satellite/ws", headers={"Authorization": "Bearer test-device"}) as websocket:
                    websocket.receive_json()
                    websocket.send_json(_registration("instance-a"))
                    registered = websocket.receive_json()
                    receipt = service.resolve_receipt(OPEN_BROWSER_TOOL_SPEC)
                    invocation = ToolInvocation("open_browser", {"url": "https://example.com"}, id="remote-blocked", execution_receipt=receipt.as_dict())
                    from types import SimpleNamespace
                    blocked, envelope = _execute_open_browser_with_broker(SimpleNamespace(executor_broker=broker),
                        invocation=invocation, profile_user_id="owner", session_id="session")
                    self.assertEqual(blocked.capability_result.status, "policy_rejected")
                    self.assertEqual(envelope.status, "error")
                    broker.execution_policies = self.select('[{"policy_id":"example.guard"}]')
                    result_box = {}
                    def execute():
                        result_box["result"] = broker.execute(spec=OPEN_BROWSER_TOOL_SPEC, receipt_value=receipt.as_dict(),
                            invocation_id="remote-allowed", arguments={"url": "https://example.com"})
                    worker = threading.Thread(target=execute)
                    worker.start()
                    message = websocket.receive_json()
                    self.assertEqual(message["invocation_id"], "remote-allowed")
                    websocket.send_json(_execution_message("accepted", registered, "remote-allowed"))
                    websocket.send_json(_execution_message("result", registered, "remote-allowed", status="succeeded"))
                    worker.join(5)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual(result_box["result"].status, "succeeded")
        await asyncio.to_thread(remote)
        self.assertIn(("open_music_search", "server_local"), seen)
        self.assertIn(("open_browser", "desktop_satellite"), seen)

    async def test_policy_rejection_settles_tool_and_workflow_jobs(self):
        from companion_v01.background_tasks import BackgroundTaskRunner
        from companion_v01.engine import AkaneMemoryEngine
        from companion_v01.host_jobs import HostJobOwner, HostJobStore
        from companion_v01.host_tool_jobs import HostToolJobRuntime
        from companion_v01.host_workflow_jobs import HostWorkflowJobRuntime, WorkflowJobAssetStore
        from tests.test_host_tool_jobs import _context
        from tests.test_host_workflow_jobs import _WorkflowRunner, _preflight
        guard = Plugin("example.guard")
        policy = guard.policy("example.guard")
        @policy.before
        def deny(request):
            if request["options"].get("transform"):
                return {"decision": "allow"}
            return {"decision": "deny", "reason": "deployment_disabled"}
        @policy.transform
        def invalid_transform(request):
            return {"action": "replace", "value": 99}
        business = Plugin("example.business")
        actions = []
        @business.tool(execution_class="long_task")
        def work(text: str) -> str:
            actions.append(text)
            return text
        await self.start(guard, business)
        self.select('[{"policy_id":"example.guard"}]')
        self.engine._tool_hook_result_status = AkaneMemoryEngine._tool_hook_result_status
        from companion_v01.mode_profiles import ModeProfileRegistry
        self.engine._resolve_client_protocol_context = ModeProfileRegistry().resolve_from_payload
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = HostJobStore(root / "jobs.db")
            runner = BackgroundTaskRunner({"host-jobs": 1, "workflow": 1})
            self.addCleanup(runner.close)
            completed = []
            jobs = HostToolJobRuntime(engine=self.engine, store=store, background_tasks=runner,
                conversation_ref_issuer=lambda _: "conversation-ref", terminal_callback=lambda job: completed.append(job) or True)
            accepted = jobs.submit(capability_id="example.business.work", invocation_id="policy-job",
                call={"type": "example.business.work", "arguments": {"text": "real work"}}, context=_context())
            job_id = accepted.stream_events[0]["job_id"]
            self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-jobs", timeout=5))
            self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-job-completions", timeout=5))
            job = store.get(job_id, owner=HostJobOwner("profile-a", "session-a"))
            self.assertEqual(job.status, "failed")
            self.assertEqual(job.result["capability_result"]["status"], "policy_rejected")
            self.assertEqual(actions, [])
            self.assertEqual(len(completed), 1)
            workflow_runner = _WorkflowRunner()
            workflows = HostWorkflowJobRuntime(store=store, asset_store=WorkflowJobAssetStore(root / "assets"),
                workflow_runner=workflow_runner, background_tasks=runner, executor_broker=self.engine.executor_broker)
            accepted = workflows.start(preflight=_preflight(), profile_user_id="owner", session_id="session", input_assets={})
            self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="workflow", timeout=5))
            job = workflows.get(accepted["jobId"], owner=HostJobOwner("owner", "session"))
            public = workflows.public(job)
            self.assertEqual(public["status"], "failed")
            self.assertEqual(public["policyFailure"]["reason"], "deployment_disabled")
            self.assertEqual(workflow_runner.requests, [])
            self.select('[{"policy_id":"example.guard","options":{"transform":true}}]')
            accepted = jobs.submit(capability_id="example.business.work", invocation_id="transformed-job",
                call={"type": "example.business.work", "arguments": {"text": "real work"}}, context=_context())
            self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-jobs", timeout=5))
            self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-job-completions", timeout=5))
            job = store.get(accepted.stream_events[0]["job_id"], owner=HostJobOwner("profile-a", "session-a"))
            self.assertEqual(job.status, "failed")
            capability = job.result["capability_result"]
            self.assertEqual(capability["status"], "result_processing_failed")
            self.assertEqual(capability["content"]["execution_receipt"]["value"], "real work")
            self.assertEqual(actions, ["real work"])
            self.assertEqual(len(completed), 2)


class InstalledPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_install_bootstrap_and_cross_worker_program_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            activation_order = []
            class RecordingProcess(PluginGenerationProcess):
                def activate(self):
                    activation_order.append(self.plugin_id)
                    return super().activate()
            artifacts = ManagedPluginArtifactStore(root / "artifacts", instance_id="policy", project_root=ROOT)
            selections = PluginSelectionStore(root / "selection.json", defaults=(), instance_id="policy")
            selected = load_execution_policies('[{"policy_id":"example.effect_guard"}]')
            builder = PluginGenerationCandidateBuilder(source_resolver=artifacts, project_root=ROOT,
                work_root=root / "workers", process_factory=RecordingProcess,
                bootstrap_services=tuple(item.dependency for item in selected))
            runtime = PluginGenerationRuntime((), candidate_builder=builder)
            bridge = PluginCapabilityToolBridge(runtime, config_base_dir=root)
            engine = EngineFacade(bridge)
            engine.store, _, _ = services(root)
            engine.capability_config_base_dir = root
            pipeline = ExecutionPolicyPipeline(selected, source=bridge)
            engine.executor_broker = ExecutorBroker(None, execution_policies=pipeline)
            provider = EnginePluginCapabilityProvider(engine)
            runtime.bind_capability_provider(provider)
            manager = ExtensionManagementService(plugin_runtime=runtime, selection_store=selections, artifact_store=artifacts)
            policy_source = root / "policy-source"
            shutil.copytree(ROOT / "examples/plugins/akane_sdk_execution_policy", policy_source,
                ignore=shutil.ignore_patterns("__pycache__", "build", "dist", "*.egg-info"))
            policy_module = policy_source / "src/akane_sdk_execution_policy/__init__.py"
            policy_code = policy_module.read_text(encoding="utf-8").replace("from akane_plugin import Plugin", "from akane_plugin import Plugin, ToolContext").replace(
                'Plugin("example.execution-policy")', 'Plugin("example.execution-policy", permissions=("capability.invoke",))').replace("    def decide(request):", '''    async def decide(request, ctx: ToolContext):
        import asyncio
        from pathlib import Path
        if request["options"].get("probe"):
            assert ctx.invocation.global_scope and not ctx.invocation.profile_user_id
            assert await ctx.tools.call("example.batch.measure", {"text": "probe"}) == 5
        if request["arguments"].get("text") == "wait":
            gate = Path(request["options"]["gate"])
            (gate / "entered").touch()
            while not (gate / "release").exists():
                await asyncio.sleep(0.01)''').replace("    return plugin", '''    @policy.observe
    def observe(request):
        if request["arguments"].get("text") == "wait":
            from pathlib import Path
            gate = Path(request["options"]["gate"])
            (gate / "observed").write_text("old:" + request["outcome"]["status"])
    return plugin''')
            policy_module.write_text(policy_code, encoding="utf-8")
            try:
                unavailable = await runtime.start()
                self.assertFalse(unavailable["ok"])
                for name in ("akane_sdk_execution_policy", "akane_sdk_batch"):
                    source = policy_source if name == "akane_sdk_execution_policy" else ROOT / "examples/plugins" / name
                    staged = await manager.stage_source(source_path=str(source))
                    self.assertTrue(staged["ok"], staged)
                    if name == "akane_sdk_execution_policy":
                        self.assertEqual(set(staged["permissions"]), {"policy.provide", "service.provide", "capability.invoke"})
                    installed = await manager.install_stage(stage_id=staged["stage_id"], approved_permissions=staged["permissions"])
                    self.assertTrue(installed["ok"], installed)
                self.assertEqual(activation_order[-2:], ["example.execution-policy", "example.batch"])
                self.assertNotIn("example.execution-policy", str(bridge.build_tool_handlers()))
                async def program():
                    invocation = ResourceInvocation("example.caller", InvocationContext("owner", "session", "web"),
                        capability_id="example.caller.run", can_invoke_capabilities=True)
                    try:
                        return await provider.invoke("example.batch.batch", {"text": "中文", "count": 2}, invocation=invocation)
                    finally:
                        await invocation.aclose()
                allowed = await program()
                self.assertFalse(allowed.is_error, allowed)
                self.assertEqual(allowed.value["outcomes"], [6, 6])
                engine.executor_broker.execution_policies = ExecutionPolicyPipeline(load_execution_policies([
                    {"policy_id": "example.effect_guard", "options": {"probe": True}}
                ]), source=bridge)
                probed = await program()
                self.assertFalse(probed.is_error, probed)
                self.assertEqual(probed.value["outcomes"], [6, 6])
                engine.executor_broker.execution_policies = ExecutionPolicyPipeline(load_execution_policies([
                    {"policy_id": "example.effect_guard", "options": {"numeric_factors": {"example.batch.measure": 2}}}
                ]), source=bridge)
                transformed = await program()
                self.assertFalse(transformed.is_error, transformed)
                self.assertEqual(transformed.value["outcomes"], [12, 12])
                engine.executor_broker.execution_policies = ExecutionPolicyPipeline(load_execution_policies([
                    {"policy_id": "example.effect_guard", "options": {"numeric_factors": {"example.batch.measure": 0.1}}}
                ]), source=bridge)
                invalid_transform = await program()
                self.assertEqual(invalid_transform.reason, "batch_incomplete")
                self.assertEqual(invalid_transform.content["outcomes"][0]["status"], "result_processing_failed")
                receipt = invalid_transform.content["outcomes"][0]["details"]["execution_receipt"]
                self.assertEqual(receipt["value"], 6)
                engine.executor_broker.execution_policies = ExecutionPolicyPipeline(load_execution_policies([
                    {"policy_id": "example.effect_guard", "options": {"blocked_tools": ["example.batch.measure"]}}
                ]), source=bridge)
                blocked = await program()
                self.assertEqual(blocked.reason, "batch_incomplete", blocked)
                self.assertEqual(blocked.content["outcomes"][0]["status"], "policy_rejected")
                self.assertEqual(blocked.content["outcomes"][0]["details"]["target"], "example.batch.measure")
                gate = root / "gate"
                gate.mkdir()
                engine.executor_broker.execution_policies = ExecutionPolicyPipeline(load_execution_policies([
                    {"policy_id": "example.effect_guard", "options": {"gate": str(gate),
                        "numeric_factors": {"example.batch.measure": 2}}}
                ]), source=bridge)
                async def model(text, identifier):
                    with bridge.turn_scope():
                        # Use a disclosed contract regardless of the developer's
                        # default exposure preference. A raw direct call to an
                        # on-demand id has no published native contract.
                        from companion_v01.capability_exposure import route_invocation
                        from companion_v01.tool_orchestration_engine import normalize_tool_invocation
                        selection = await asyncio.to_thread(engine._resolve_capability_selection,
                            profile_user_id="owner", session_id="session")
                        target = "example.batch.measure"
                        contract = selection.capability_catalog.load([target])["capabilities"][0]
                        call, selection = route_invocation({"type": "capability_invoke",
                            "capability_id": target, "contract_ref": contract["contract_ref"],
                            "arguments": {"text": text}}, selection)
                        invocation = normalize_tool_invocation(engine, call, capability_selection=selection)
                        invocation = replace(invocation, id=identifier)
                        return await asyncio.to_thread(execute_tool_invocation, engine,
                            invocation=invocation,
                            profile_user_id="owner", session_id="session", visual_payload={}, now_ts=1)
                pending = asyncio.create_task(model("wait", "old-policy"))
                try:
                    async with asyncio.timeout(5):
                        while not (gate / "entered").exists():
                            await asyncio.sleep(0.01)
                    policy_module.write_text(policy_code.replace('return {"decision": "allow"}',
                        'return {"decision": "deny", "reason": "new_deployment_policy"}').replace('"old:"', '"new:"').replace(
                            'value * factors[request["tool_id"]]', 'value * 100'), encoding="utf-8")
                    staged = await manager.stage_source(source_path=str(policy_source))
                    self.assertTrue(staged["ok"], staged)
                    upgraded = await manager.install_stage(stage_id=staged["stage_id"], approved_permissions=staged["permissions"])
                    self.assertTrue(upgraded["ok"], upgraded)
                    (gate / "release").touch()
                    old, _ = await asyncio.wait_for(pending, 5)
                    self.assertIsNotNone(old.capability_result, old)
                    self.assertEqual(old.capability_result.value, 8, old.capability_result)
                    self.assertEqual((gate / "observed").read_text(), "old:ok")
                    new, envelope = await model("next", "new-policy")
                    self.assertEqual(new.capability_result.content["reason"], "new_deployment_policy")
                    self.assertEqual(envelope.status, "error")
                finally:
                    (gate / "release").touch()
                    if not pending.done():
                        await pending
                disabled = await manager.set_enabled(plugin_id="example.execution-policy", enabled=False)
                self.assertFalse(disabled["ok"], disabled)
                self.assertEqual(bridge.list_services("execution_policy.example.effect_guard")[0]["status"], "available")
            finally:
                await runtime.stop()
