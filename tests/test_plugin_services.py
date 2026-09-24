"""Public services execute through host admission without model tool projection."""
import statistics
import asyncio
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import patch
import unittest
from capcore import InvocationContext
from akane_plugin import Plugin, ServiceContext, ToolContext, Services, ToolCallError
from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_host import PluginHost
from companion_v01.plugin_contribution_policy import TrustedStatefulPluginContributionPolicy
from companion_v01.plugin_capability_calls import EnginePluginCapabilityProvider
from companion_v01.plugin_tool_bridge import PluginCapabilityToolBridge
from tests.test_plugin_host import FakeEntryPoint
from tests.test_plugin_engine_bridge import EngineFacade
from companion_v01.plugin_active_generation import ActivePluginGeneration, PluginGenerationSnapshot
from companion_v01.plugin_generation import PluginGenerationProcess
from companion_v01.plugin_resources import ResourceInvocation
from companion_v01.tool_runtime import ToolExecutionContext


class PluginServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_host_service_entry_uses_service_admission_without_model_projection(self):
        plugin = Plugin("example.host-service")
        calls = []

        @plugin.service("host_math").method
        def plus(value: int) -> int:
            calls.append(value)
            return value + 1

        host, engine, provider = await self.start_host(plugin)
        context = ToolExecutionContext("owner", "session", 1, {}, client_mode="web")
        result = await provider.call_service("host_math", "plus", {"value": 4}, context=context)
        self.assertFalse(result.result.is_error, result)
        self.assertEqual(result.result.value, 5)
        self.assertEqual(calls, [4])
        self.assertEqual(result.origin["capability_id"], "example.host-service.service.host_math.v1.plus")
        self.assertTrue(result.origin["invocation_id"].startswith("host-service-"))
        self.assertFalse(result.cancellation_requested)
        self.assertNotIn(result.origin["capability_id"], engine._resolve_tool_handlers())
        missing = await provider.call_service("missing", "plus", {}, context=context)
        self.assertEqual(missing.result.reason, "service_unavailable")
        self.assertEqual(missing.origin, {})
        invalid = await provider.call_service("host_math", "plus", {"value": "wrong"}, context=context)
        self.assertTrue(invalid.result.is_error)
        self.assertEqual(calls, [4])

    async def test_host_service_entry_requires_bound_context_and_honors_pre_cancel(self):
        plugin = Plugin("example.host-context")
        calls = []

        @plugin.service("host_context").method
        def run() -> int:
            calls.append(True)
            return 1

        _, _, provider = await self.start_host(plugin)
        for context in (None, InvocationContext("owner", "session", "web"),
                        ToolExecutionContext("", "session", 1, {}),
                        ToolExecutionContext("owner", "session", 1, {}, global_scope=True)):
            result = await provider.call_service("host_context", "run", {}, context=context)
            self.assertEqual(result.result.reason, "service_context_required")
        cancelled = await provider.call_service("host_context", "run", {},
            context=ToolExecutionContext("owner", "session", 1, {}, cancel_requested=lambda: True))
        self.assertEqual(cancelled.result.reason, "invocation_cancelled")
        self.assertTrue(cancelled.cancellation_requested)
        self.assertEqual(calls, [])

    async def test_forwarded_experience_keeps_the_consumers_output_schema_gate(self):
        from akane_plugin import CapabilityResult, PluginResultPayload, PluginResultExperience

        producer = Plugin("example.experience")
        @producer.service("experience").method(output_schema={"type": "object"})
        def report():
            return CapabilityResult(is_error=False, status="ok",
                content=PluginResultPayload(content={"count": 1}, experience=PluginResultExperience(summary="One item.")))
        consumer = Plugin("example.schema-consumer", permissions=("capability.invoke",))
        @consumer.tool(output_schema={"type": "integer"})
        async def wrong_schema(ctx: ToolContext):
            return await ctx.services.call_result("experience", "report", {})
        host, _, _ = await self.start_host(producer, consumer)
        result = await host.invoke("example.schema-consumer.wrong_schema", {}, context=InvocationContext("owner", "session", "web"))
        self.assertEqual(result.reason, "plugin_result_schema_mismatch", result)
        self.assertEqual(result.content["execution_status"], "completed")
        self.assertFalse(result.content["retryable"])

    async def test_public_discovery_returns_contracts_without_business_execution(self):
        plugin = Plugin("example.catalog", permissions=("capability.invoke",))
        executions = []
        @plugin.service("math", version=2).method
        def plus(value: int) -> int:
            executions.append(value)
            return value + 1
        @plugin.tool(output_schema={"type": "array", "items": {"type": "object"}})
        async def discover(ctx: ToolContext):
            return await ctx.services.list("math")
        restricted = Plugin("example.restricted")
        @restricted.tool
        async def discover_denied(ctx: ToolContext) -> int:
            await ctx.services.list()
            return 1
        host, _, provider = await self.start_host(plugin, restricted)
        result = await host.invoke("example.catalog.discover", {}, context=InvocationContext("owner", "session", "web"))
        self.assertFalse(result.is_error, result)
        item, = result.value
        self.assertEqual((item["service_id"], item["version"], item["status"], item["selected_provider"]),
                         ("math", 2, "available", "example.catalog"))
        self.assertEqual(item["providers"][0]["methods"][0]["input_schema"]["required"], ["value"])
        self.assertEqual(item["providers"][0]["methods"][0]["output_schema"], {"type": "integer"})
        self.assertEqual(executions, [])
        # A returned contract is a copy, not mutable registry state.
        item["providers"][0]["methods"][0]["output_schema"]["type"] = "string"
        again = await host.invoke("example.catalog.discover", {}, context=InvocationContext("owner", "session", "web"))
        self.assertEqual(again.value[0]["providers"][0]["methods"][0]["output_schema"]["type"], "integer")
        with self.assertRaises(ToolCallError) as denied:
            await Services(None).list()
        self.assertEqual(denied.exception.result.reason, "capability_invoke_permission_required")
        denied_result = await host.invoke("example.restricted.discover_denied", {}, context=InvocationContext("owner", "session", "web"))
        self.assertEqual(denied_result.reason, "capability_invoke_permission_required", denied_result)
        scope = ResourceInvocation(plugin.manifest.plugin_id, InvocationContext("owner", "session", "web"),
                                   can_invoke_capabilities=True)
        missing = await provider.invoke({"operation": "services.list", "service_id": "missing"}, {}, invocation=scope)
        self.assertEqual(missing.value, [], missing)
        for target, arguments in (({"operation": "services.list", "service_id": None, "extra": 1}, {}),
                                  ({"operation": "services.list", "service_id": None}, {"override": True}),
                                  ({"operation": "services.list", "service_id": 5}, {})):
            bad = await provider.invoke(target, arguments, invocation=scope)
            self.assertEqual(bad.reason, "service_request_invalid", bad)
        scope.revoke()
        expired = await provider.invoke({"operation": "services.list", "service_id": None}, {}, invocation=scope)
        self.assertEqual(expired.reason, "capability_invocation_expired", expired)

    async def start_host(self, *plugins):
        def entry_point(plugin):
            def create():
                return plugin
            return FakeEntryPoint(plugin.manifest.plugin_id, create)
        host = PluginHost(tuple(PluginSelection(p.manifest.plugin_id, True) for p in plugins),
            entry_points_provider=lambda: tuple(entry_point(p) for p in plugins),
            contribution_policy=TrustedStatefulPluginContributionPolicy())
        engine = EngineFacade(PluginCapabilityToolBridge(host))
        provider = EnginePluginCapabilityProvider(engine)
        host.bind_capability_provider(provider)
        self.addAsyncCleanup(host.stop)
        status = await host.start()
        self.assertEqual(host.state, "active", status)
        return host, engine, provider

    async def test_program_calls_real_versioned_service_without_exposing_model_tools(self):
        provider = Plugin("example.statistics")
        statistics_service = provider.service("statistics", version=1)
        calls = []
        @statistics_service.method
        def mean(values: list[float], ctx: ServiceContext) -> float:
            calls.append((list(values), ctx.invocation.profile_user_id))
            return statistics.fmean(values)
        consumer = Plugin("example.consumer", permissions=("capability.invoke",))
        @consumer.tool
        async def average(values: list[float], ctx: ToolContext) -> float:
            return await ctx.services.call("statistics", "mean", {"values": values})
        host, engine, _ = await self.start_host(provider, consumer)
        self.assertEqual(provider.manifest.permissions, ("service.provide",))
        inventory = host.contribution_snapshots[1].as_dict()
        self.assertEqual(inventory["types"], ["services"])
        self.assertEqual(inventory["capabilities"], [])
        self.assertEqual(inventory["services"][0]["methods"][0]["output_schema"], {"type": "number"})
        self.assertEqual(set(engine._resolve_tool_handlers()), {"example.consumer.average"})
        result = await host.invoke("example.consumer.average", {"values": [1.0, 2.0, 6.0]},
                                   context=InvocationContext("owner", "session", "web"))
        self.assertFalse(result.is_error, result)
        self.assertEqual(result.value, 3.0)
        self.assertEqual(calls, [([1.0, 2.0, 6.0], "owner")])

    async def test_versions_ambiguity_permission_and_cycles_use_real_admitted_methods(self):
        plugin = Plugin("example.versions", permissions=("capability.invoke",))
        @plugin.service("math", version=1).method
        def calculate(value: int) -> int:
            return value + 1
        @plugin.service("math", version=2).method(name="calculate")
        def calculate_v2(value: int) -> int:
            return value + 2
        @plugin.service("recursive").method
        async def recurse(ctx: ServiceContext) -> int:
            return await ctx.services.call("recursive", "recurse", {})
        host, _, provider = await self.start_host(plugin)
        scope = ResourceInvocation("example.consumer", InvocationContext("owner", "session", "web"),
            capability_id="example.consumer.run", can_invoke_capabilities=True)
        for version in (1, 2):
            result = await provider.invoke({"service_id": "math", "method": "calculate", "version": version},
                {"value": 40}, invocation=scope)
            self.assertEqual(result.value, 40 + version, result)
        cycle = await provider.invoke({"service_id": "recursive", "method": "recurse", "version": 1}, {}, invocation=scope)
        self.assertEqual(cycle.reason, "capability_dependency_cycle", cycle)
        invalid = await provider.invoke({"service_id": "math", "method": "calculate", "version": True}, {}, invocation=scope)
        self.assertEqual(invalid.reason, "service_request_invalid", invalid)
        scope.can_invoke_capabilities = False
        denied = await provider.invoke({"service_id": "math", "method": "calculate", "version": 1}, {}, invocation=scope)
        self.assertEqual(denied.reason, "capability_invocation_expired", denied)
        other = Plugin("example.other")
        calls = []
        @other.service("math").method
        def different_method() -> int:
            calls.append(True)
            return 1
        await host.stop()
        host, _, provider = await self.start_host(plugin, other)
        scope.can_invoke_capabilities = True
        ambiguous = await provider.invoke({"service_id": "math", "method": "calculate", "version": 1},
            {"value": 40}, invocation=scope)
        self.assertEqual(ambiguous.reason, "service_provider_ambiguous", ambiguous)
        self.assertEqual(calls, [])

    async def test_event_context_consumes_service_without_a_model_tool(self):
        provider = Plugin("example.event-statistics")
        @provider.service("statistics").method
        def mean(values: list[float]) -> float:
            return statistics.fmean(values)
        consumer = Plugin("example.event-consumer", permissions=("capability.invoke",))
        discovered = []
        @consumer.on("example.data", sources=("@host",))
        async def summarize(event, ctx):
            discovered.extend(await ctx.services.list())
            return await ctx.services.call("statistics", "mean", {"values": event.data})
        host, engine, _ = await self.start_host(provider, consumer)
        self.assertEqual(engine._resolve_tool_handlers(), {})
        broker = host.build_event_broker()
        receipt = await broker.emit("example.data", [1.0, 2.0, 6.0], context=InvocationContext("owner", "session", "web"))
        async with asyncio.timeout(5):
            while not receipt.complete:
                await asyncio.sleep(0.01)
                receipt = await broker.receipt(receipt.dispatch_id)
        self.assertEqual(receipt.status, "completed", receipt)
        self.assertEqual(receipt.deliveries[0].value, 3.0)
        self.assertEqual(discovered[0]["selected_provider"], "example.event-statistics")

    async def test_worker_nested_service_chain_freezes_but_next_top_level_call_refreshes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = ActivePluginGeneration()
            bridge = PluginCapabilityToolBridge(runtime, config_base_dir=root)
            provider = EnginePluginCapabilityProvider(EngineFacade(bridge))
            processes = []
            async def create(role, revision, plugin_id=None):
                site = root / f"{role}-{revision}" / "site"
                package = site / "service_fixture"
                metadata = site / "service_fixture-0.1.0.dist-info"
                package.mkdir(parents=True)
                metadata.mkdir()
                source = textwrap.dedent('''
                    import asyncio
                    import statistics
                    import json
                    from pathlib import Path
                    from akane_plugin import Plugin, ServiceContext, ToolContext
                    plugin = Plugin("example.service-ROLE", permissions=("capability.invoke",))
                    if "ROLE" == "statistics":
                        api = plugin.service("statistics", version=1)
                        @api.method
                        def mean(values: list[float]) -> float:
                            if not values:
                                return "invalid output"
                            return statistics.fmean(values) + REVISION
                    elif "ROLE" == "pipeline":
                        api = plugin.service("report", version=1)
                        @api.method
                        async def calculate(values: list[float], directory: str, ctx: ServiceContext) -> float:
                            root = Path(directory)
                            (root / "entered").touch()
                            while not (root / "release").exists():
                                await asyncio.sleep(0.01)
                            (root / "discovered.json").write_text(json.dumps(await ctx.services.list("statistics")))
                            return await ctx.services.call("statistics", "mean", {"values": values})
                    else:
                        @plugin.tool(output_schema={"type": "array", "items": {"type": "object"}})
                        async def discover(ctx: ToolContext):
                            return await ctx.services.list("statistics")
                        @plugin.tool
                        async def average(values: list[float], ctx: ToolContext) -> float:
                            return await ctx.services.call("statistics", "mean", {"values": values})
                        @plugin.tool
                        async def report(values: list[float], directory: str, ctx: ToolContext) -> float:
                            return await ctx.services.call("report", "calculate", {"values": values, "directory": directory})
                    def create_plugin(): return plugin
                ''').replace("ROLE", role).replace("REVISION", str(revision))
                plugin_id = plugin_id or f"example.service-{role}"
                source = source.replace(f"example.service-{role}", plugin_id)
                (package / "__init__.py").write_text(source, encoding="utf-8")
                (metadata / "entry_points.txt").write_text(
                    f"[akane.plugins.v1]\n{plugin_id} = service_fixture:create_plugin\n", encoding="utf-8")
                (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: service-fixture\nVersion: 0.1.0\n", encoding="utf-8")
                process = PluginGenerationProcess(project_root=Path(__file__).resolve().parents[1], site_dir=site,
                    plugin_id=plugin_id, work_dir=site.parent / "worker")
                process.bind_capability_provider(provider)
                processes.append(process)
                await asyncio.to_thread(process.start)
                return process
            pending = None
            context = InvocationContext("owner", "session", "web")
            try:
                consumer, pipeline, old = [await create(role, 0) for role in ("consumer", "pipeline", "statistics")]
                selections = tuple(PluginSelection(item.plugin_id, True) for item in (consumer, pipeline, old))
                await runtime.publish(PluginGenerationSnapshot(selections, (consumer, pipeline, old)))
                self.assertEqual(set(bridge.build_tool_handlers()), {"example.service-consumer.average", "example.service-consumer.report", "example.service-consumer.discover"})
                with bridge.turn_scope():
                    pending = asyncio.create_task(runtime.invoke("example.service-consumer.report",
                        {"values": [1.0, 5.0], "directory": str(root)}, context=context))
                    async with asyncio.timeout(10):
                        while not (root / "entered").exists():
                            if pending.done():
                                self.fail(str(pending.result()))
                            await asyncio.sleep(0.01)
                    new = await create("statistics", 100)
                    alternative = await create("statistics", 200, "example.alternative-statistics")
                    await runtime.publish(PluginGenerationSnapshot((*selections, PluginSelection(alternative.plugin_id, True)),
                        (consumer, pipeline, new, alternative)))
                    self.assertTrue(old.running)
                    ambiguous = await runtime.invoke("example.service-consumer.average", {"values": [1.0, 5.0]}, context=context)
                    self.assertEqual(ambiguous.reason, "service_provider_ambiguous", ambiguous)
                    discovered = await runtime.invoke("example.service-consumer.discover", {}, context=context)
                    self.assertFalse(discovered.is_error, discovered)
                    item, = discovered.value
                    self.assertEqual(item["reason"], ambiguous.reason)
                    self.assertIsNone(item["selected_provider"])
                    self.assertEqual(len(item["providers"]), 2)
                    # The old nested chain must not admit newly installed peers.
                    (root / "release").touch()
                    frozen = await asyncio.wait_for(pending, 10)
                    self.assertEqual(frozen.value, 3.0, frozen)
                    import json
                    frozen_catalog, = json.loads((root / "discovered.json").read_text())
                    self.assertEqual(frozen_catalog["selected_provider"], "example.service-statistics")
                    self.assertEqual(len(frozen_catalog["providers"]), 1)
                    await runtime.publish(PluginGenerationSnapshot(selections, (consumer, pipeline, new)))
                    # The model turn remains old, but this is a new service chain.
                    fresh = await runtime.invoke("example.service-consumer.average", {"values": [1.0, 5.0]}, context=context)
                    self.assertEqual(fresh.value, 103.0, fresh)
                    discovered = await runtime.invoke("example.service-consumer.discover", {}, context=context)
                    self.assertEqual(discovered.value[0]["status"], "available", discovered)
                await asyncio.wait_for(runtime.drain_retired(), 5)
                self.assertFalse(old.running)
                invalid = await runtime.invoke("example.service-consumer.average", {"values": []}, context=context)
                self.assertEqual(invalid.reason, "plugin_result_schema_mismatch", invalid)
                scope = ResourceInvocation(consumer.plugin_id, context, can_invoke_capabilities=True)
                missing = await provider.invoke({"service_id": "statistics", "method": "mean", "version": 2},
                    {"values": [1.0]}, invocation=scope)
                self.assertEqual(missing.reason, "service_unavailable", missing)
                wrong_input = await provider.invoke({"service_id": "statistics", "method": "mean", "version": 1},
                    {"values": "invalid"}, invocation=scope)
                self.assertEqual(wrong_input.status, "validation_error", wrong_input)
                cancelling = root / "cancelling"
                cancelling.mkdir()
                pending = asyncio.create_task(runtime.invoke("example.service-consumer.report",
                    {"values": [1.0], "directory": str(cancelling)}, context=context))
                async with asyncio.timeout(5):
                    while not (cancelling / "entered").exists():
                        if pending.done():
                            self.fail(str(pending.result()))
                        await asyncio.sleep(0.01)
                pending.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(pending, 5)
                self.assertTrue(all(not life.scopes for life in runtime._lifetimes.values()))
            finally:
                (root / "release").touch()
                if pending is not None and not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                await runtime.stop()
                for process in processes:
                    await asyncio.to_thread(process.stop)

    async def test_install_independent_service_and_consumer_projects_through_extension_manager(self):
        from tests.image_plugin_harness import ImageHarness
        from companion_v01.plugin_tool_bridge import project_model_result

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            harness = await ImageHarness(root, None).start()
            try:
                harness.runtime.bind_capability_provider(EnginePluginCapabilityProvider(harness.engine))
                for name in ("akane_sdk_statistics_report", "akane_sdk_statistics"):
                    staged = await harness.service.stage_source(source_path=str(Path(__file__).resolve().parents[1] / "examples/plugins" / name))
                    self.assertTrue(staged["ok"], staged)
                    if name == "akane_sdk_statistics":
                        self.assertEqual(staged["permissions"], ["service.provide"])
                    installed = await harness.service.install_stage(stage_id=staged["stage_id"],
                        approved_permissions=staged["permissions"])
                    self.assertTrue(installed["ok"], installed)
                    if name == "akane_sdk_statistics_report":
                        self.assertEqual(installed["status"], "waiting_dependency", installed)
                        self.assertEqual(installed["runtime"]["reason"], "service_dependency_missing")
                        self.assertEqual(harness.handlers(), {})
                handlers = harness.handlers()
                self.assertEqual(set(handlers), {"example.statistics-report.summarize", "example.statistics-report.describe_service"})
                discovered = await harness.runtime.invoke("example.statistics-report.describe_service", {},
                    context=InvocationContext("owner", "session", "desktop_pet"))
                self.assertFalse(discovered.is_error, discovered)
                contract, = discovered.value
                self.assertEqual(contract["selected_provider"], "example.statistics")
                self.assertEqual(contract["status"], "available")
                self.assertEqual(contract["providers"][0]["methods"][0]["name"], "summarize")
                inventory = harness.service.public_snapshot()
                entry = next(item for item in inventory["plugins"] if item["plugin_id"] == "example.statistics")
                self.assertEqual(entry["runtime_status"], "active", entry)
                self.assertEqual(entry["contributions"]["capabilities"], [])
                self.assertEqual(entry["contributions"]["services"][0]["service_id"], "statistics")
                self.assertEqual(entry["contributions"]["services"][0]["methods"][0]["name"], "summarize")
                handler = handlers["example.statistics-report.summarize"]
                def invoke():
                    return handler.execute(call=handler.normalize_call({"type": handler.tool_type,
                        "arguments": {"values": [1.0, 2.0, 6.0]}}),
                        context=ToolExecutionContext("owner", "session", 1, {}, client_mode="desktop_pet"))
                with patch("companion_v01.plugin_tool_bridge.project_model_result", wraps=project_model_result) as projection:
                    result = await asyncio.to_thread(invoke)
                self.assertFalse(result.capability_result.is_error, result.capability_result)
                self.assertEqual(result.capability_result.value,
                    {"count": 3.0, "mean": 3.0, "median": 2.0, "minimum": 1.0, "maximum": 6.0})
                self.assertEqual(projection.call_count, 1)
                self.assertIn('"mean": 3.0', result.followup_context)
                self.assertEqual(result.stream_events, [{"type": "adapter_capability_completed",
                    "capabilityId": handler.tool_type, "status": "ok", "is_error": False}])
                disabled = await harness.service.set_enabled(plugin_id="example.statistics", enabled=False)
                self.assertTrue(disabled["ok"], disabled)
                stale = await asyncio.to_thread(invoke)
                self.assertEqual(stale.capability_result.reason, "plugin_capability_revoked", stale)
                self.assertEqual(harness.handlers(), {})
                self.assertEqual(harness.engine.plugin_capability_source.list_services("statistics"), [])
                entry = next(item for item in harness.service.public_snapshot()["plugins"] if item["plugin_id"] == "example.statistics-report")
                self.assertEqual(entry["runtime_status"], "waiting_dependency", entry)
                self.assertEqual(entry["reason"], "service_dependency_missing", entry)
            finally:
                await harness.close()
