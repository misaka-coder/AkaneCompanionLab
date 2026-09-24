"""Deployment limits through real tool dispatch and installed cross-worker composition."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from capcore import InvocationContext
from akane_plugin import Plugin, ToolContext, Tools, ToolCallError
from companion_v01.capability_registry import ExecutorBroker
from companion_v01.execution_resource_policy import ExecutionResourcePolicy, ExecutionResourceBudget
from companion_v01.extension_management import ExtensionManagementService, PluginSelectionStore
from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_capability_calls import EnginePluginCapabilityProvider
from companion_v01.plugin_contribution_policy import TrustedStatefulPluginContributionPolicy
from companion_v01.plugin_generation_candidate import PluginGenerationCandidateBuilder
from companion_v01.plugin_generation_runtime import PluginGenerationRuntime
from companion_v01.plugin_host import PluginHost
from companion_v01.plugin_installation import ManagedPluginArtifactStore
from companion_v01.plugin_resources import ResourceInvocation
from companion_v01.plugin_tool_bridge import PluginCapabilityToolBridge
from companion_v01.tool_invocation import ToolInvocation
from companion_v01.tool_orchestration_engine import execute_tool_invocation
from tests.test_plugin_engine_bridge import EngineFacade
from tests.test_plugin_host import FakeEntryPoint
from tests.test_plugin_resources import services

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/plugins/akane_sdk_batch"


class ResourcePolicyTests(unittest.TestCase):
    def test_config_is_strict_and_budget_reservation_is_atomic(self):
        for invalid in ('{"max_input_bytes":true}', '{"max_dependency_depth":0}', '{"unknown":1}', '[]', 'private-path'):
            with self.assertRaisesRegex(ValueError, "^execution_resource_policy_invalid$"):
                ExecutionResourcePolicy.from_config(invalid)
        policy = ExecutionResourcePolicy.from_config({"max_dependency_calls": 7, "max_dependency_depth": None})
        budget = ExecutionResourceBudget(policy)
        with ThreadPoolExecutor(max_workers=8) as workers:
            outcomes = list(workers.map(lambda _: budget.reserve_dependency(depth=50), range(40)))
        self.assertEqual(sum(item is None for item in outcomes), 7)
        self.assertEqual(budget.snapshot(depth=50)["remaining_dependency_calls"], 0)
        self.assertIsNone(budget.snapshot(depth=50)["remaining_dependency_depth"])
        failed = next(item for item in outcomes if item is not None)
        self.assertEqual((failed.content["limit"], failed.content["actual"], failed.content["source"]), (7, 8, "deployment_config"))

    def test_broker_rejection_is_accurate_and_idempotent_for_arbitrary_dispatch_results(self):
        broker = ExecutorBroker(None, resource_policy=ExecutionResourcePolicy.from_config({"max_input_bytes": 8}))
        calls = []
        def dispatch():
            calls.append(True)
            return {"ok": True, "outputs": []}
        args = {"text": "中文数据"}
        first = broker.execute_server_local(tool_id="workflow", invocation_id="one", dispatch=dispatch,
                                           input_arguments=args, request_data=args)
        self.assertEqual(first.status, "resource_exhausted")
        self.assertEqual(first.resource_limit["actual"], len(json.dumps(args, ensure_ascii=False, separators=(",", ":")).encode()))
        self.assertEqual(first.resource_limit["execution_status"], "not_started")
        self.assertEqual(first.resource_limit["target"], "workflow")
        broker.resource_policy = ExecutionResourcePolicy()
        replay = broker.execute_server_local(tool_id="workflow", invocation_id="one", dispatch=dispatch,
                                            input_arguments=args, request_data=args)
        self.assertIs(replay, first)
        self.assertEqual(calls, [])


class ResourcePolicyIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_and_program_input_budget_share_the_real_dispatch_contract(self):
        plugin = Plugin("example.input")
        calls = []
        @plugin.tool(input_schema={"type": "object", "properties": {
            "type": {"type": "string"}, "arguments": {"type": "object"}, "_tool_note": {"type": "string"}},
            "required": ["type", "arguments", "_tool_note"], "additionalProperties": False}, output_schema={"type": "integer"})
        def inspect(arguments):
            calls.append(arguments)
            return len(arguments["_tool_note"])
        with tempfile.TemporaryDirectory() as directory:
            host = PluginHost((PluginSelection(plugin.manifest.plugin_id, True),),
                entry_points_provider=lambda: (FakeEntryPoint(plugin.manifest.plugin_id, lambda: plugin),),
                contribution_policy=TrustedStatefulPluginContributionPolicy())
            bridge = PluginCapabilityToolBridge(host, config_base_dir=Path(directory))
            engine = EngineFacade(bridge)
            engine.store, _, _ = services(Path(directory))
            engine.capability_config_base_dir = Path(directory)
            engine.executor_broker = ExecutorBroker(None)
            provider = EnginePluginCapabilityProvider(engine)
            try:
                await host.start()
                args = {"type": "business", "arguments": {"value": 1}, "_tool_note": "中文" * 20000}
                async def model():
                    result, envelope = await asyncio.to_thread(execute_tool_invocation, engine,
                        invocation=ToolInvocation("example.input.inspect", {"arguments": args}), profile_user_id="owner", session_id="session",
                        visual_payload={}, now_ts=1)
                    self.assertIsNotNone(result.capability_result, result)
                    return result.capability_result, envelope
                async def program():
                    scope = ResourceInvocation("example.caller", InvocationContext("owner", "session", "web"),
                        capability_id="example.caller.run", can_invoke_capabilities=True)
                    try:
                        return await provider.invoke("example.input.inspect", args, invocation=scope)
                    finally:
                        await scope.aclose()
                normal, _ = await model()
                composed = await program()
                self.assertFalse(normal.is_error, normal)
                self.assertEqual((normal.value, composed.value), (40000, 40000))
                engine.executor_broker.resource_policy = ExecutionResourcePolicy.from_config({"max_input_bytes": 1024})
                blocked_model, envelope = await model()
                blocked_program = await program()
                self.assertEqual(blocked_model.content, blocked_program.content)
                self.assertEqual(blocked_model.reason, "execution_resource_limit_exceeded")
                self.assertEqual(envelope.status, "error")
                self.assertIn("resource_limit", json.dumps(envelope.events))
                self.assertEqual(len(calls), 2)
            finally:
                await host.stop()

    async def test_configurable_depth_and_separate_top_level_budgets(self):
        plugin = Plugin("example.depth", permissions=("capability.invoke",))
        def stage(level):
            @plugin.tool(name=f"stage_{level}")
            async def run(ctx: ToolContext) -> int:
                if level == 7:
                    return (await ctx.tools.budget())["dependency_depth"]
                return await ctx.tools.call(f"example.depth.stage_{level + 1}", {})
        for level in range(8):
            stage(level)
        with tempfile.TemporaryDirectory() as directory:
            host = PluginHost((PluginSelection(plugin.manifest.plugin_id, True),),
                entry_points_provider=lambda: (FakeEntryPoint(plugin.manifest.plugin_id, lambda: plugin),),
                contribution_policy=TrustedStatefulPluginContributionPolicy())
            engine = EngineFacade(PluginCapabilityToolBridge(host, config_base_dir=Path(directory)))
            engine.store, _, _ = services(Path(directory))
            engine.capability_config_base_dir = Path(directory)
            engine.executor_broker = ExecutorBroker(None, resource_policy=ExecutionResourcePolicy.from_config({"max_dependency_depth": 4}))
            host.bind_capability_provider(EnginePluginCapabilityProvider(engine))
            try:
                await host.start()
                context = InvocationContext("owner", "session", "web")
                failed = await host.invoke("example.depth.stage_0", {}, context=context)
                self.assertEqual(failed.reason, "execution_resource_limit_exceeded", failed)
                self.assertEqual(failed.content["budget"], "max_dependency_depth")
                self.assertEqual((failed.content["actual"], failed.content["limit"]), (5, 4))
                engine.executor_broker.resource_policy = ExecutionResourcePolicy.from_config({"max_dependency_depth": 8})
                fresh = await host.invoke("example.depth.stage_0", {}, context=context)
                self.assertFalse(fresh.is_error, fresh)
                self.assertEqual(fresh.value, 7)
            finally:
                await host.stop()
        with self.assertRaises(ToolCallError):
            await Tools(None).budget()

    async def test_installed_workers_share_fanout_and_budget_queries_do_not_consume_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = ManagedPluginArtifactStore(root / "artifacts", instance_id="budget", project_root=ROOT)
            selections = PluginSelectionStore(root / "selections.json", defaults=(), instance_id="budget")
            runtime = PluginGenerationRuntime((), candidate_builder=PluginGenerationCandidateBuilder(
                source_resolver=artifacts, project_root=ROOT, work_root=root / "workers"))
            engine = EngineFacade(PluginCapabilityToolBridge(runtime, config_base_dir=root))
            engine.store, _, _ = services(root)
            engine.capability_config_base_dir = root
            engine.executor_broker = ExecutorBroker(None, resource_policy=ExecutionResourcePolicy.from_config({"max_dependency_calls": 6}))
            runtime.bind_capability_provider(EnginePluginCapabilityProvider(engine))
            service = ExtensionManagementService(plugin_runtime=runtime, selection_store=selections, artifact_store=artifacts)
            second = root / "peer"
            shutil.copytree(EXAMPLE, second, ignore=shutil.ignore_patterns("build", "dist", "__pycache__", "*.egg-info"))
            for path in (second / "pyproject.toml", second / "src/akane_sdk_batch/__init__.py"):
                path.write_text(path.read_text(encoding="utf-8").replace("example.batch", "example.batch-peer")
                    .replace('name = "akane-sdk-batch-example"', 'name = "akane-sdk-batch-peer"'), encoding="utf-8")
            try:
                await runtime.start()
                for source in (EXAMPLE, second):
                    staged = await service.stage_source(source_path=str(source))
                    self.assertTrue(staged["ok"], staged)
                    installed = await service.install_stage(stage_id=staged["stage_id"], approved_permissions=staged["permissions"])
                    self.assertTrue(installed["ok"], installed)
                async def call(count, parallel):
                    return await runtime.invoke("example.batch.batch", {"text": "中文" * 9000, "count": count,
                        "parallel": parallel, "peer": "example.batch-peer"}, context=InvocationContext("owner", "session", "web"))
                limited = await call(5, True)
                self.assertTrue(limited.is_error)
                self.assertEqual(limited.reason, "batch_incomplete")
                self.assertEqual(limited.content["before"]["used_dependency_calls"], 0)
                self.assertEqual(limited.content["after"]["used_dependency_calls"], 6, limited)
                self.assertLess(limited.content["completed"], 5)
                for outcome in limited.content["outcomes"]:
                    if isinstance(outcome, dict):
                        self.assertEqual(outcome["reason"], "execution_resource_limit_exceeded")
                        self.assertEqual(outcome["details"]["source"], "deployment_config")
                engine.executor_broker.resource_policy = ExecutionResourcePolicy.from_config({"max_dependency_calls": 100})
                expanded = await call(40, False)
                self.assertFalse(expanded.is_error, expanded)
                self.assertEqual(expanded.value["completed"], 40)
                self.assertEqual(expanded.value["before"]["used_dependency_calls"], 0)
                self.assertEqual(expanded.value["after"]["used_dependency_calls"], 80)
                self.assertEqual(expanded.value["outcomes"], [54000] * 40)
            finally:
                await runtime.stop()
