"""Real preparation, dependency waiting, configuration and provider switching."""
import asyncio
import tempfile
import textwrap
import unittest
from pathlib import Path

from capcore import InvocationContext
from akane_plugin import Plugin, ServiceDependency
from companion_v01.extension_management import ExtensionManagementService, PluginSelectionStore
from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_capability_calls import EnginePluginCapabilityProvider
from companion_v01.plugin_generation import PluginGenerationProcess
from companion_v01.plugin_generation_candidate import PluginGenerationCandidateBuilder
from companion_v01.plugin_generation_runtime import PluginGenerationRuntime
from companion_v01.plugin_installation import PluginGenerationSource, ManagedPluginArtifactStore
from companion_v01.plugin_tool_bridge import PluginCapabilityToolBridge
from tests.test_plugin_engine_bridge import EngineFacade
from tests.test_plugin_host import FakeEntryPoint
from companion_v01.plugin_host import PluginHost
from companion_v01.plugin_contribution_policy import TrustedStatefulPluginContributionPolicy


ROOT = Path(__file__).resolve().parents[1]


def write_plugin(root, plugin_id, *, service=None, requires=(), offset=0):
    site = root / plugin_id / "site"
    package, metadata = site / "dependency_fixture", site / "dependency_fixture-0.1.0.dist-info"
    package.mkdir(parents=True)
    metadata.mkdir()
    marker = root / plugin_id
    source = textwrap.dedent('''
        import asyncio
        from pathlib import Path
        from akane_plugin import Plugin, ServiceDependency, ServiceContext, ToolContext, HealthStatus
        PLUGIN_ID = PLUGIN_ID_VALUE
        SERVICE = SERVICE_VALUE
        REQUIRES = REQUIRES_VALUE
        MARKER = Path(MARKER_VALUE)
        plugin = Plugin(PLUGIN_ID, permissions=("capability.invoke", "job.run"),
                        requires_services=tuple(ServiceDependency(*item) for item in REQUIRES))
        async def calculate(value: int, ctx: ServiceContext, directory: str = "") -> int:
            if directory:
                gate = Path(directory)
                (gate / "entered").touch()
                while not (gate / "release").exists():
                    await asyncio.sleep(0.01)
            if REQUIRES:
                return await ctx.services.call(REQUIRES[0][0], "calculate", {"value": value}, version=REQUIRES[0][1])
            return value + OFFSET_VALUE
        if SERVICE:
            plugin.service(SERVICE).method(calculate)
        else:
            @plugin.tool
            async def run(value: int, ctx: ToolContext, directory: str = "") -> int:
                return await ctx.services.call(REQUIRES[0][0], "calculate", {"value": value, "directory": directory})
        class HealthProbe:
            provider_id = PLUGIN_ID + ".health"
            async def health(self):
                with (MARKER / "health").open("a") as stream:
                    stream.write("checked\\n")
                return HealthStatus(True, "ready")
            async def list_capabilities(self): return ()
            async def invoke(self, *args): raise AssertionError("no probe capability")
            async def aclose(self): pass
        class Job:
            async def start(self, controller):
                with (MARKER / "jobs").open("a") as stream:
                    stream.write("started\\n")
                await controller.wait_for_shutdown()
            async def stop(self): pass
        class Extension:
            manifest = plugin.manifest
            def register(self, registrar):
                plugin.register(registrar)
                registrar.add_capability_adapter(HealthProbe())
                registrar.add_background_service("worker", Job())
        def create_plugin(): return Extension()
    ''')
    for token, value in {"PLUGIN_ID_VALUE": plugin_id, "SERVICE_VALUE": service, "REQUIRES_VALUE": requires,
                         "MARKER_VALUE": str(marker), "OFFSET_VALUE": offset}.items():
        source = source.replace(token, repr(value))
    (package / "__init__.py").write_text(source, encoding="utf-8")
    (metadata / "entry_points.txt").write_text(f"[akane.plugins.v1]\n{plugin_id} = dependency_fixture:create_plugin\n", encoding="utf-8")
    (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: dependency-fixture\nVersion: 0.1.0\n", encoding="utf-8")
    return PluginGenerationSource(plugin_id, site)


class Resolver:
    def __init__(self, sources):
        self.sources = {source.plugin_id: source for source in sources}
    def resolve_generation_source(self, plugin_id):
        return self.sources[plugin_id]


class DependencyRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_provider_satisfies_its_own_contract_without_an_activation_cycle(self):
        plugin = Plugin("example.local-provider", requires_services=(ServiceDependency("local"),))
        @plugin.service("local").method
        def value() -> int:
            return 42
        def factory():
            return plugin
        host = PluginHost((PluginSelection(plugin.manifest.plugin_id, True),),
            entry_points_provider=lambda: (FakeEntryPoint(plugin.manifest.plugin_id, factory),),
            contribution_policy=TrustedStatefulPluginContributionPolicy())
        try:
            status = await host.start()
            self.assertEqual(status["plugins"][0]["status"], "active", status)
            catalog, = PluginCapabilityToolBridge(host).list_services("local")
            self.assertEqual(catalog["selected_provider"], plugin.manifest.plugin_id)
        finally:
            await host.stop()

    async def test_inprocess_host_does_not_advertise_a_consumer_with_missing_declared_dependency(self):
        plugin = Plugin("example.consumer", requires_services=(ServiceDependency("statistics"),))
        @plugin.tool
        def read() -> int:
            return 42
        def factory():
            return plugin
        host = PluginHost((PluginSelection(plugin.manifest.plugin_id, True),),
            entry_points_provider=lambda: (FakeEntryPoint(plugin.manifest.plugin_id, factory),),
            contribution_policy=TrustedStatefulPluginContributionPolicy())
        try:
            status = await host.start()
            self.assertEqual(status["plugins"][0]["status"], "waiting_dependency", status)
            self.assertEqual(status["plugins"][0]["reason"], "service_dependency_missing", status)
            self.assertEqual(PluginCapabilityToolBridge(host).build_tool_handlers(), {})
        finally:
            await host.stop()

    async def start_runtime(self, root, sources, selections, bindings=()):
        store = PluginSelectionStore(root / "selections.json", defaults=selections, instance_id="dependencies")
        store.save(selections, service_bindings=bindings)
        builder = PluginGenerationCandidateBuilder(source_resolver=Resolver(sources), project_root=ROOT,
            work_root=root / "workers", service_bindings_provider=store.load_service_bindings)
        runtime = PluginGenerationRuntime(store.load(), candidate_builder=builder)
        bridge = PluginCapabilityToolBridge(runtime, config_base_dir=root)
        runtime.bind_capability_provider(EnginePluginCapabilityProvider(EngineFacade(bridge)))
        service = ExtensionManagementService(plugin_runtime=runtime, selection_store=store)
        self.addAsyncCleanup(runtime.stop)
        status = await runtime.start()
        self.assertTrue(status["ok"], status)
        return runtime, bridge, store, service

    async def test_prepare_and_artifact_probe_do_not_run_health_or_background_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = write_plugin(root, "example.provider", service="statistics")
            artifacts = ManagedPluginArtifactStore(root / "artifacts", instance_id="dependencies", project_root=ROOT)
            inspected = await asyncio.to_thread(artifacts._probe, source.site_dir, source.plugin_id, root / "probe")
            self.assertEqual(inspected["status"], "prepared", inspected)
            self.assertFalse((root / source.plugin_id / "health").exists())
            self.assertFalse((root / source.plugin_id / "jobs").exists())
            process = PluginGenerationProcess(project_root=ROOT, site_dir=source.site_dir, plugin_id=source.plugin_id,
                work_dir=root / "worker")
            try:
                prepared = await asyncio.to_thread(process.prepare)
                self.assertEqual(prepared["status"], "prepared", prepared)
                descriptor, = process.capability_descriptors.values()
                blocked = await process.invoke(descriptor.id, {"value": 3}, context=InvocationContext("owner", "session", "web"))
                self.assertTrue(blocked.is_error, blocked)
                self.assertFalse((root / source.plugin_id / "health").exists())
                active = await asyncio.to_thread(process.activate)
                self.assertTrue(active["ok"], active)
                self.assertEqual((root / source.plugin_id / "health").read_text(), "checked\n")
                self.assertEqual((root / source.plugin_id / "jobs").read_text(), "started\n")
                self.assertEqual((await process.invoke(descriptor.id, {"value": 3}, context=InvocationContext("owner", "session", "web"))).value, 3)
            finally:
                await asyncio.to_thread(process.stop)

    async def test_missing_dependency_waits_then_activates_and_withdraws_when_provider_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            consumer = write_plugin(root, "example.consumer", requires=(("statistics", 1),))
            provider = write_plugin(root, "example.provider", service="statistics")
            selections = (PluginSelection(consumer.plugin_id, True), PluginSelection(provider.plugin_id, False))
            runtime, bridge, store, service = await self.start_runtime(root, (consumer, provider), selections)
            pending = None
            gate = root / "withdrawal"
            gate.mkdir()
            try:
                entry = next(item for item in service.public_snapshot()["plugins"] if item["plugin_id"] == consumer.plugin_id)
                self.assertEqual(entry["runtime_status"], "waiting_dependency", entry)
                self.assertEqual(entry["reason"], "service_dependency_missing", entry)
                self.assertTrue(entry["declared_only"])
                self.assertEqual(bridge.build_tool_handlers(), {})
                self.assertFalse((root / consumer.plugin_id / "health").exists())
                self.assertFalse((root / consumer.plugin_id / "jobs").exists())
                enabled = await service.set_enabled(plugin_id=provider.plugin_id, enabled=True)
                self.assertTrue(enabled["ok"], enabled)
                result = await runtime.invoke("example.consumer.run", {"value": 3}, context=InvocationContext("owner", "session", "web"))
                self.assertEqual(result.value, 3, result)
                self.assertEqual((root / consumer.plugin_id / "health").read_text(), "checked\n")
                self.assertEqual((root / consumer.plugin_id / "jobs").read_text(), "started\n")
                pending = asyncio.create_task(runtime.invoke("example.consumer.run", {"value": 3, "directory": str(gate)},
                    context=InvocationContext("owner", "session", "web")))
                async with asyncio.timeout(5):
                    while not (gate / "entered").exists():
                        if pending.done():
                            self.fail(str(pending.result()))
                        await asyncio.sleep(0.01)
                with bridge.service_scope():
                    self.assertEqual(bridge.list_services("statistics")[0]["status"], "available")
                    disabled = await service.set_enabled(plugin_id=provider.plugin_id, enabled=False)
                    self.assertEqual(bridge.list_services("statistics"), [])
                self.assertTrue(disabled["ok"], disabled)
                cancelled = await asyncio.wait_for(pending, 5)
                self.assertEqual(cancelled.status, "cancelled", cancelled)
                self.assertEqual(bridge.build_tool_handlers(), {})
                self.assertEqual((root / consumer.plugin_id / "jobs").read_text(), "started\n")
                self.assertEqual(service.public_snapshot()["plugins"][0]["runtime_status"], "waiting_dependency")
            finally:
                (gate / "release").touch()
                if pending is not None and not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                await runtime.stop()

    async def test_cycle_and_downstream_diagnostics_precede_health_and_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = (write_plugin(root, "example.a", service="a", requires=(("b", 1),)),
                       write_plugin(root, "example.b", service="b", requires=(("a", 1),)),
                       write_plugin(root, "example.consumer", requires=(("a", 1),)))
            selections = tuple(PluginSelection(item.plugin_id, True) for item in sources)
            runtime, bridge, _, service = await self.start_runtime(root, sources, selections)
            try:
                entries = {item["plugin_id"]: item for item in service.public_snapshot()["plugins"]}
                for plugin_id in ("example.a", "example.b"):
                    self.assertEqual(entries[plugin_id]["reason"], "service_dependency_cycle", entries)
                self.assertEqual(entries["example.consumer"]["reason"], "service_dependency_unavailable", entries)
                self.assertEqual(bridge.build_tool_handlers(), {})
                self.assertFalse(list(root.glob("*/health")))
                self.assertFalse(list(root.glob("*/jobs")))
            finally:
                await runtime.stop()

    async def test_configured_provider_switch_preserves_running_chain_and_next_call_uses_new_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = (write_plugin(root, "example.consumer", requires=(("report", 1),)),
                       write_plugin(root, "example.report", service="report", requires=(("statistics", 1),)),
                       write_plugin(root, "example.first", service="statistics"),
                       write_plugin(root, "example.second", service="statistics", offset=100))
            selections = tuple(PluginSelection(item.plugin_id, True) for item in sources)
            binding = {"service_id": "statistics", "version": 1, "plugin_id": "example.first"}
            runtime, bridge, store, service = await self.start_runtime(root, sources, selections, (binding,))
            gate = root / "gate"
            gate.mkdir()
            context = InvocationContext("owner", "session", "web")
            pending = None
            try:
                catalog, = bridge.list_services("statistics")
                self.assertEqual(catalog["configured_provider"], "example.first")
                self.assertEqual(catalog["selected_provider"], "example.first")
                self.assertEqual(len(catalog["providers"]), 2)
                with bridge.turn_scope():
                    pending = asyncio.create_task(runtime.invoke("example.consumer.run", {"value": 3, "directory": str(gate)}, context=context))
                    async with asyncio.timeout(5):
                        while not (gate / "entered").exists():
                            if pending.done():
                                self.fail(str(pending.result()))
                            await asyncio.sleep(0.01)
                    store.save(selections, service_bindings=({**binding, "plugin_id": "example.second"},))
                    # Enablement writes preserve the same deployment overlay.
                    store.save(selections)
                    reloaded = await service.restart()
                    self.assertTrue(reloaded["ok"], reloaded)
                    self.assertEqual(service.public_snapshot()["service_bindings"][0]["plugin_id"], "example.second")
                    with bridge.service_scope():
                        catalog, = bridge.list_services("statistics")
                        self.assertEqual(catalog["selected_provider"], "example.second")
                    fresh = await runtime.invoke("example.consumer.run", {"value": 3}, context=context)
                    self.assertEqual(fresh.value, 103, fresh)
                    (gate / "release").touch()
                    frozen = await asyncio.wait_for(pending, 10)
                    self.assertEqual(frozen.value, 3, frozen)
                await asyncio.wait_for(runtime._active.drain_retired(), 5)
                store.save(selections, service_bindings=())
                reloaded = await service.restart()
                self.assertTrue(reloaded["ok"], reloaded)
                entry = next(item for item in service.public_snapshot()["plugins"] if item["plugin_id"] == "example.report")
                self.assertEqual(entry["reason"], "service_provider_ambiguous", entry)
                self.assertEqual(bridge.list_services("statistics")[0]["reason"], "service_provider_ambiguous")
                self.assertEqual(bridge.build_tool_handlers(), {})
                store.save(selections, service_bindings=({**binding, "plugin_id": "example.missing"},))
                reloaded = await service.restart()
                self.assertTrue(reloaded["ok"], reloaded)
                entry = next(item for item in service.public_snapshot()["plugins"] if item["plugin_id"] == "example.report")
                self.assertEqual(entry["reason"], "service_provider_unavailable", entry)
                self.assertEqual(entry["dependency_errors"][0]["selected_provider"], "example.missing")
                catalog, = bridge.list_services("statistics")
                self.assertEqual(catalog["status"], "unavailable")
                self.assertEqual(catalog["configured_provider"], "example.missing")
                self.assertIsNone(catalog["selected_provider"])
                self.assertEqual(len(catalog["providers"]), 2)
                self.assertEqual(bridge.build_tool_handlers(), {})
            finally:
                (gate / "release").touch()
                if pending is not None and not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                await runtime.stop()
