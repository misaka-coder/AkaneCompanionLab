from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from companion_v01.plugin_installation import (
    ManagedPluginArtifactStore,
    PluginInstallationError,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_ROOT = PROJECT_ROOT / "examples" / "plugins" / "akane_gentle_checkin"
PLUGIN_ID = "akane.sample.gentle-checkin"


class ManagedPluginArtifactStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._build_temp = tempfile.TemporaryDirectory()
        cls.build_root = Path(cls._build_temp.name)
        cls.wheel_v1 = cls._build_wheel("0.1.0")
        cls.wheel_v2 = cls._build_wheel("0.2.0")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._build_temp.cleanup()

    @classmethod
    def _build_wheel(cls, version: str) -> Path:
        source = cls.build_root / f"source-{version}"
        wheelhouse = cls.build_root / f"wheelhouse-{version}"
        shutil.copytree(SAMPLE_ROOT, source)
        if version != "0.1.0":
            pyproject = source / "pyproject.toml"
            pyproject.write_text(
                pyproject.read_text(encoding="utf-8").replace(
                    'version = "0.1.0"',
                    f'version = "{version}"',
                ),
                encoding="utf-8",
            )
            plugin_source = source / "src" / "akane_gentle_checkin" / "__init__.py"
            plugin_source.write_text(
                plugin_source.read_text(encoding="utf-8").replace(
                    'PLUGIN_VERSION = "0.1.0"',
                    f'PLUGIN_VERSION = "{version}"',
                ),
                encoding="utf-8",
            )
        wheelhouse.mkdir()
        subprocess.run(
            [
                sys.executable,
                "-m",
                "build",
                "--wheel",
                "--outdir",
                str(wheelhouse),
                str(source),
            ],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        wheels = tuple(wheelhouse.glob("akane_gentle_checkin-*.whl"))
        if len(wheels) != 1:
            raise RuntimeError("test_plugin_wheel_not_built")
        return wheels[0]

    def setUp(self) -> None:
        self._runtime_temp = tempfile.TemporaryDirectory()
        self.root = Path(self._runtime_temp.name)
        self.store = ManagedPluginArtifactStore(
            self.root / "artifacts",
            instance_id="test-instance",
            project_root=PROJECT_ROOT,
        )

    def tearDown(self) -> None:
        self._runtime_temp.cleanup()

    def test_stage_probes_real_wheel_without_publishing_or_leaking_paths(self) -> None:
        staged = self.store.stage_wheel(self.wheel_v1)

        self.assertEqual(staged["status"], "staged")
        self.assertEqual(staged["plugin_id"], PLUGIN_ID)
        self.assertEqual(staged["version"], "0.1.0")
        self.assertIn("model.reasoning", staged["permissions"])
        self.assertIn("background_services", staged["contribution_snapshot"]["types"])
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot["plugin_count"], 0)
        self.assertEqual(snapshot["staged_count"], 1)
        self.assertNotIn(str(self.root), json.dumps(snapshot))
        self.assertEqual(self.store.entry_points(), ())

    def test_local_source_is_built_then_enters_the_same_wheel_probe(self) -> None:
        source = self.root / "source"
        shutil.copytree(SAMPLE_ROOT, source)

        staged = self.store.stage_source(source)

        self.assertEqual(staged["status"], "staged")
        self.assertEqual(staged["plugin_id"], PLUGIN_ID)
        self.assertEqual(staged["version"], "0.1.0")
        build_root = self.root / "artifacts" / "source-builds"
        self.assertFalse(build_root.exists() and tuple(build_root.iterdir()))

    def test_publish_requires_exact_permissions_and_selects_atomically(self) -> None:
        staged = self.store.stage_wheel(self.wheel_v1)

        rejected = self.store.publish_stage(
            staged["stage_id"],
            approved_permissions=(),
        )
        self.assertEqual(rejected["status"], "approval_required")
        self.assertEqual(self.store.snapshot()["plugin_count"], 0)

        before_sys_path = tuple(sys.path)
        published = self.store.publish_stage(
            staged["stage_id"],
            approved_permissions=staged["permissions"],
        )
        self.assertEqual(published["status"], "installed")
        self.assertTrue(published["restart_required"])
        entries = self.store.entry_points()
        self.assertEqual([entry.name for entry in entries], [PLUGIN_ID])
        self.assertEqual(tuple(sys.path), before_sys_path)
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot["plugin_count"], 1)
        self.assertTrue(snapshot["plugins"][0]["pending_process_restart"])
        self.assertEqual(self.store.pending_process_restart_plugin_ids(), (PLUGIN_ID,))
        self.assertFalse(tuple((self.root / "artifacts" / "staging").iterdir()))

    def test_successful_new_process_marks_current_release_last_good(self) -> None:
        staged = self.store.stage_wheel(self.wheel_v1)
        published = self.store.publish_stage(
            staged["stage_id"],
            approved_permissions=staged["permissions"],
        )

        result = self.store.reconcile_runtime(
            ({"plugin_id": PLUGIN_ID, "status": "active"},)
        )

        self.assertEqual(result["status"], "ready")
        item = self.store.snapshot()["plugins"][0]
        self.assertFalse(item["pending_process_restart"])
        self.assertEqual(item["last_good_digest"], published["digest"])
        self.assertEqual(self.store.pending_process_restart_plugin_ids(), ())

    def test_failed_update_points_catalog_back_to_last_good(self) -> None:
        first = self.store.stage_wheel(self.wheel_v1)
        published_v1 = self.store.publish_stage(
            first["stage_id"],
            approved_permissions=first["permissions"],
        )
        self.store.reconcile_runtime(({"plugin_id": PLUGIN_ID, "status": "active"},))

        second = self.store.stage_wheel(self.wheel_v2)
        published_v2 = self.store.publish_stage(
            second["stage_id"],
            approved_permissions=second["permissions"],
        )
        self.assertNotEqual(published_v1["digest"], published_v2["digest"])

        rollback = self.store.reconcile_runtime(
            ({"plugin_id": PLUGIN_ID, "status": "unavailable", "reason": "plugin_load_failed"},)
        )

        self.assertEqual(rollback["status"], "rollback_scheduled")
        self.assertTrue(rollback["restart_required"])
        item = self.store.snapshot()["plugins"][0]
        self.assertEqual(item["digest"], published_v1["digest"])
        self.assertTrue(item["pending_process_restart"])

    def test_failed_first_activation_remains_visible_without_fake_rollback(self) -> None:
        staged = self.store.stage_wheel(self.wheel_v1)
        self.store.publish_stage(
            staged["stage_id"],
            approved_permissions=staged["permissions"],
        )

        result = self.store.reconcile_runtime(
            ({"plugin_id": PLUGIN_ID, "status": "unavailable"},)
        )

        self.assertEqual(result["status"], "activation_failed")
        self.assertEqual(result["failed_plugin_ids"], [PLUGIN_ID])
        self.assertFalse(result["restart_required"])
        self.assertTrue(self.store.snapshot()["plugins"][0]["pending_process_restart"])

    def test_remove_withdraws_catalog_and_managed_entry_point(self) -> None:
        staged = self.store.stage_wheel(self.wheel_v1)
        self.store.publish_stage(
            staged["stage_id"],
            approved_permissions=staged["permissions"],
        )

        removed = self.store.remove_plugin(PLUGIN_ID)

        self.assertTrue(removed["ok"])
        self.assertEqual(removed["status"], "removed")
        self.assertEqual(self.store.snapshot()["plugin_count"], 0)
        self.assertEqual(self.store.entry_points(), ())

    def test_invalid_wheel_leaves_no_staged_candidate(self) -> None:
        invalid = self.root / "broken.whl"
        invalid.write_bytes(b"not-a-wheel")

        with self.assertRaises(PluginInstallationError) as raised:
            self.store.stage_wheel(invalid)

        self.assertEqual(raised.exception.reason, "plugin_wheel_install_failed")
        staging = self.root / "artifacts" / "staging"
        self.assertFalse(staging.exists() and tuple(staging.iterdir()))


if __name__ == "__main__":
    unittest.main()
