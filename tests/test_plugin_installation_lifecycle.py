from __future__ import annotations

import asyncio
import shutil
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import Mock

from capcore import InvocationContext

from companion_v01.extension_management import ExtensionManagementService, PluginSelectionStore
from companion_v01.plugin_generation_candidate import PluginGenerationCandidateBuilder
from companion_v01.plugin_generation_runtime import PluginGenerationRuntime
from companion_v01.plugin_installation import ManagedPluginArtifactStore
from companion_v01.plugin_tool_bridge import PluginCapabilityToolBridge


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_ROOT = PROJECT_ROOT / "examples" / "plugins" / "akane_gentle_checkin"
DIAGNOSTIC_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "akane_diagnostic_plugin"
PLUGIN_ID = "akane.sample.gentle-checkin"
CAPABILITY_ID = f"{PLUGIN_ID}.configure.v1"


class PluginInstallationLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_install_upgrade_and_uninstall_preserve_files_owned_by_live_invocations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            package = source / "src" / "freeze_install"
            package.mkdir(parents=True)
            manifest = textwrap.dedent('''
                [build-system]
                requires = ["setuptools>=77"]
                build-backend = "setuptools.build_meta"
                [project]
                name = "freeze-install-fixture"
                version = "VERSION"
                requires-python = ">=3.11"
                dependencies = ["akane-plugin>=0.6,<0.9"]
                [project.entry-points."akane.plugins.v1"]
                "example.freeze-install" = "freeze_install:create_plugin"
                [tool.setuptools.packages.find]
                where = ["src"]
            ''')
            (package / "__init__.py").write_text(textwrap.dedent('''
                import asyncio
                from pathlib import Path
                from akane_plugin import Plugin
                plugin = Plugin("example.freeze-install", version="PLUGIN_VERSION")
                @plugin.tool
                def read() -> str:
                    from .version import VERSION
                    return VERSION
                @plugin.tool
                async def slow(directory: str) -> str:
                    root = Path(directory)
                    (root / "entered").touch()
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        while not (root / "release").exists():
                            await asyncio.sleep(0.01)
                        from .version import VERSION
                        return VERSION
                def create_plugin(): return plugin
            '''), encoding="utf-8")
            artifacts = ManagedPluginArtifactStore(root / "artifacts", instance_id="freeze", project_root=PROJECT_ROOT)
            selections = PluginSelectionStore(root / "selections.json", defaults=(), instance_id="freeze")
            runtime = PluginGenerationRuntime((), candidate_builder=PluginGenerationCandidateBuilder(
                source_resolver=artifacts, project_root=PROJECT_ROOT, work_root=root / "generations"))
            service = ExtensionManagementService(plugin_runtime=runtime, selection_store=selections, artifact_store=artifacts)
            bridge = PluginCapabilityToolBridge(runtime, config_base_dir=root)
            context = InvocationContext("owner", "session", "qq_text")
            plugin_source = (package / "__init__.py").read_text(encoding="utf-8")
            async def install(version):
                (source / "pyproject.toml").write_text(manifest.replace("VERSION", version), encoding="utf-8")
                (package / "__init__.py").write_text(plugin_source.replace("PLUGIN_VERSION", version), encoding="utf-8")
                (package / "version.py").write_text(f"VERSION = {version!r}\n", encoding="utf-8")
                staged = await service.stage_source(source_path=str(source))
                self.assertTrue(staged["ok"], staged)
                result = await asyncio.wait_for(service.install_stage(stage_id=staged["stage_id"],
                    approved_permissions=staged["permissions"]), 10)
                self.assertTrue(result["ok"], result)
            await runtime.start()
            pending = None
            try:
                # This is the same scope as a model installing its first plugin.
                with bridge.turn_scope():
                    await install("0.1.0")
                    old_site = artifacts.resolve_generation_source("example.freeze-install").site_dir
                    handler = bridge.build_tool_handlers()["example.freeze-install.read"]
                    await install("0.2.0")
                    self.assertEqual(runtime.status_snapshot()["cleanup_status"], "draining")
                    self.assertTrue((old_site / "freeze_install" / "version.py").exists())
                    value = await handler.adapter.invoke(handler.tool_type, {}, context)
                    self.assertEqual(value.value, "0.1.0", value)
                await asyncio.wait_for(runtime._active.drain_retired(), 5)
                service.reconcile_runtime(runtime.status_snapshot())
                self.assertFalse(old_site.exists())
                current_site = artifacts.resolve_generation_source("example.freeze-install").site_dir
                pending = asyncio.create_task(runtime.invoke("example.freeze-install.slow", {"directory": str(root)}, context=context))
                async with asyncio.timeout(5):
                    while not (root / "entered").exists():
                        self.assertFalse(pending.done(), pending.result() if pending.done() else "")
                        await asyncio.sleep(0.01)
                removed = await asyncio.wait_for(service.uninstall(plugin_id="example.freeze-install"), 5)
                self.assertTrue(removed["ok"], removed)
                self.assertEqual(removed["status"], "removed_cleanup_pending")
                self.assertFalse(pending.done())
                self.assertTrue(current_site.exists())
                (root / "release").touch()
                value = await pending
                self.assertEqual(value.value, "0.2.0", value)
                await asyncio.wait_for(runtime._active.drain_retired(), 5)
                service.reconcile_runtime(runtime.status_snapshot())
                self.assertFalse(current_site.exists())
            finally:
                (root / "release").touch()
                if pending is not None:
                    await pending
                await runtime.stop()

    async def test_stage_reports_specific_plugin_rejection_instead_of_aggregate_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            shutil.copytree(DIAGNOSTIC_ROOT, source)
            artifact_store = ManagedPluginArtifactStore(
                root / "artifacts",
                instance_id="test-instance",
                project_root=PROJECT_ROOT,
            )
            service = ExtensionManagementService(
                plugin_runtime=Mock(),
                selection_store=Mock(),
                artifact_store=artifact_store,
            )

            staged = await service.stage_source(source_path=str(source))

            self.assertFalse(staged["ok"])
            self.assertEqual(staged["status"], "failed")
            self.assertEqual(staged["reason"], "contribution_policy_rejected")

    async def test_fresh_source_stage_installs_activates_and_invokes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            shutil.copytree(SAMPLE_ROOT, source)
            artifact_store = ManagedPluginArtifactStore(
                root / "artifacts",
                instance_id="test-instance",
                project_root=PROJECT_ROOT,
            )
            selection_store = PluginSelectionStore(
                root / "plugin-selections.json",
                defaults=(),
                instance_id="test-instance",
            )
            runtime = PluginGenerationRuntime(
                (),
                candidate_builder=PluginGenerationCandidateBuilder(
                    source_resolver=artifact_store,
                    project_root=PROJECT_ROOT,
                    work_root=root / "generation-work",
                    plugin_storage_data_root=root / "instance-data",
                    plugin_storage_instance_id="test-instance",
                ),
            )
            service = ExtensionManagementService(
                plugin_runtime=runtime,
                selection_store=selection_store,
                artifact_store=artifact_store,
            )
            await runtime.start()
            try:
                staged = await service.stage_source(source_path=str(source))
                installed = await service.install_stage(
                    stage_id=staged["stage_id"],
                    approved_permissions=staged["permissions"],
                )
                invoked = await service.invoke_capability(
                    CAPABILITY_ID,
                    {"action": "status"},
                    context=InvocationContext(
                        profile_user_id="master",
                        session_id="test-session",
                        client_mode="qq_text",
                    ),
                )

                self.assertEqual(staged["status"], "staged")
                self.assertTrue(installed["ok"])
                self.assertEqual(installed["status"], "active")
                self.assertEqual(installed["plugin_id"], PLUGIN_ID)
                self.assertFalse(invoked.is_error)
                self.assertEqual(invoked.status, "ok")
                self.assertEqual(selection_store.load()[0].plugin_id, PLUGIN_ID)
                self.assertTrue(selection_store.load()[0].enabled)
                artifact = artifact_store.snapshot()["plugins"][0]
                self.assertFalse(artifact["pending_activation"])
                self.assertEqual(artifact["last_good_digest"], artifact["digest"])
            finally:
                await runtime.stop()


if __name__ == "__main__":
    unittest.main()
