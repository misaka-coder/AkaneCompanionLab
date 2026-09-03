from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from capcore import InvocationContext

from companion_v01.extension_management import ExtensionManagementService, PluginSelectionStore
from companion_v01.plugin_generation_candidate import PluginGenerationCandidateBuilder
from companion_v01.plugin_generation_runtime import PluginGenerationRuntime
from companion_v01.plugin_installation import ManagedPluginArtifactStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_ROOT = PROJECT_ROOT / "examples" / "plugins" / "akane_gentle_checkin"
DIAGNOSTIC_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "akane_diagnostic_plugin"
PLUGIN_ID = "akane.sample.gentle-checkin"
CAPABILITY_ID = f"{PLUGIN_ID}.configure.v1"


class PluginInstallationLifecycleTests(unittest.IsolatedAsyncioTestCase):
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
