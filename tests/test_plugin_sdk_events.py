"""Stage/install two independent SDK projects and run their public event chain."""

from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from capcore import InvocationContext
from companion_v01.extension_management import ExtensionManagementService, PluginSelectionStore
from companion_v01.plugin_capability_calls import EnginePluginCapabilityProvider
from companion_v01.plugin_generation_candidate import PluginGenerationCandidateBuilder
from companion_v01.plugin_generation_runtime import PluginGenerationRuntime
from companion_v01.plugin_installation import ManagedPluginArtifactStore
from companion_v01.plugin_tool_bridge import PluginCapabilityToolBridge
from tests.test_plugin_engine_bridge import EngineFacade
from tests.test_plugin_resources import services


ROOT = Path(__file__).resolve().parents[1]
SOURCE, CALCULATOR = "example.event-source", "example.event-calculator"


class InstalledSdkEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_installed_events_calculate_without_model_and_survive_reconfiguration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifacts = ManagedPluginArtifactStore(root / "artifacts", instance_id="sdk-events", project_root=ROOT)
            selections = PluginSelectionStore(root / "selections.json", defaults=(), instance_id="sdk-events")
            runtime = PluginGenerationRuntime((), candidate_builder=PluginGenerationCandidateBuilder(
                source_resolver=artifacts, project_root=ROOT, work_root=root / "generations",
            ))
            engine = EngineFacade(PluginCapabilityToolBridge(runtime, config_base_dir=root))
            engine.store, _, _ = services(root)
            engine.llm = Mock(side_effect=AssertionError("event chain must not invoke a model"))
            runtime.bind_capability_provider(EnginePluginCapabilityProvider(engine))
            service = ExtensionManagementService(plugin_runtime=runtime, selection_store=selections, artifact_store=artifacts)
            await runtime.start()
            context = InvocationContext("owner", "session", "web")

            async def publish(a, b):
                result = await runtime.invoke(SOURCE + ".publish_numbers", {"a": a, "b": b}, context=context)
                self.assertFalse(result.is_error, result)
                return result.value

            async def terminal(dispatch_id):
                async with asyncio.timeout(10):
                    while True:
                        result = await runtime.invoke(SOURCE + ".dispatch_status", {"dispatch_id": dispatch_id}, context=context)
                        self.assertFalse(result.is_error, result)
                        if result.value["complete"]:
                            return result.value
                        await asyncio.sleep(0.01)

            try:
                for project in ("akane_sdk_event_source", "akane_sdk_event_calculator"):
                    staged = await service.stage_source(source_path=str(ROOT / "examples/plugins" / project))
                    self.assertTrue(staged["ok"], staged)
                    self.assertIn("event.emit", staged["permissions"])
                    self.assertIn("event.subscribe", staged["permissions"])
                    self.assertTrue(staged["contribution_snapshot"]["event_subscriptions"])
                    installed = await service.install_stage(stage_id=staged["stage_id"], approved_permissions=staged["permissions"])
                    self.assertTrue(installed["ok"], installed)
                observer = root / "observer"
                observer.mkdir()
                (observer / "pyproject.toml").write_text('''[build-system]
requires = ["setuptools>=77"]
build-backend = "setuptools.build_meta"
[project]
name = "akane-sdk-observer-test"
version = "0.1.0"
dependencies = ["akane-plugin>=0.6,<0.9"]
[project.entry-points."akane.plugins.v1"]
"example.zz-observer" = "observer:create_plugin"
[tool.setuptools]
py-modules = ["observer"]
''', encoding="utf-8")
                (observer / "observer.py").write_text('''from akane_plugin import Plugin
plugin = Plugin("example.zz-observer")
@plugin.on("example.sum-ready", sources=("example.event-calculator",))
async def audit(event, ctx):
    return {"observed_sum": event.data["sum"]}
def create_plugin():
    return plugin
''', encoding="utf-8")
                staged = await service.stage_source(source_path=str(observer))
                self.assertTrue(staged["ok"], staged)
                self.assertEqual(staged["permissions"], ["event.subscribe"])
                self.assertEqual(staged["contribution_snapshot"]["capabilities"], [])
                installed = await service.install_stage(stage_id=staged["stage_id"], approved_permissions=["event.subscribe"])
                self.assertTrue(installed["ok"], installed)
                self.assertFalse(any(key.startswith("example.zz-observer.") for key in engine._resolve_tool_handlers()))
                with patch("companion_v01.plugin_tool_bridge.project_model_result", side_effect=AssertionError("no model consumer")):
                    accepted = await publish(2, 40)
                    self.assertEqual(accepted["status"], "accepted")
                    self.assertFalse(accepted["complete"])
                    completed = await terminal(accepted["dispatch_id"])
                    self.assertEqual(completed["status"], "completed", completed)
                    delivery, = completed["deliveries"]
                    self.assertTrue(delivery["linked_dispatches"])
                    received = delivery["value"]["deliveries"][0]["value"]
                    self.assertEqual(received, {"sum": 42, "input_event_id": accepted["event_id"]})
                    observed = [item["value"] for item in delivery["value"]["deliveries"]
                                if item.get("subscription_id") == "example.zz-observer.audit"]
                    self.assertEqual(observed, [{"observed_sum": 42}])
                    denied = await runtime.invoke(SOURCE + ".dispatch_status", {"dispatch_id": accepted["dispatch_id"]},
                                                  context=InvocationContext("other", "session", "web"))
                    self.assertTrue(denied.is_error)
                    self.assertEqual(denied.reason, "event_dispatch_access_denied")
                    self.assertTrue((await service.set_enabled(plugin_id=CALCULATOR, enabled=False))["ok"])
                    self.assertEqual((await publish(10, 20))["status"], "unobserved")
                    self.assertEqual((await terminal(accepted["dispatch_id"]))["status"], "completed")
                    self.assertTrue((await service.set_enabled(plugin_id=CALCULATOR, enabled=True))["ok"])
                    resumed = await terminal((await publish(10, 20))["dispatch_id"])
                    self.assertEqual(resumed["deliveries"][0]["value"]["deliveries"][0]["value"]["sum"], 30)
                engine.llm.assert_not_called()
                self.assertEqual(engine.store.list_generated_files(profile_user_id="owner", session_id="session", limit=10), [])
            finally:
                await runtime.stop()


if __name__ == "__main__":
    unittest.main()
