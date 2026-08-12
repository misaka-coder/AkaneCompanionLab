from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from companion_v01.instance_profile import resolve_instance_context


ROOT = Path(__file__).resolve().parents[1]


class LocalTestLauncherTests(unittest.TestCase):
    def test_launcher_freezes_local_only_boundaries_after_env_import(self) -> None:
        source = (ROOT / "start_akane_local_test.ps1").read_text(encoding="utf-8")

        import_offset = source.index("Import-AkaneEnvFile")
        private_import_offset = source.index("cloud-aligned.env")
        overrides = (
            '$env:AKANE_INSTANCE_ID = $instanceId',
            '$env:COMPANION_HOST = "127.0.0.1"',
            '$env:HOST = "127.0.0.1"',
            '$env:QQ_BRIDGE_ENABLED = "false"',
            '$env:EXECUTION_QQ_ENABLED = "false"',
            "$env:AKANE_ADMIN_TOKEN = New-AkaneLocalTestSatelliteToken",
        )
        for fragment in overrides:
            self.assertGreater(source.index(fragment), import_offset)
            self.assertGreater(source.index(fragment), private_import_offset)
        self.assertIn('[int]$BackendPort = 11999', source)
        self.assertIn('-SeedBundledCharacters:$seedCharacters', source)
        self.assertIn("Sync-AkaneLocalPackages", source)
        self.assertNotIn("CloudSatellite =", source)

    def test_prepare_only_creates_isolated_named_instance_without_leaking_env_values(self) -> None:
        powershell = shutil.which("powershell")
        if not powershell:
            self.skipTest("PowerShell is unavailable")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_root = root / "local-data"
            env_file = root / "local.env"
            secret_marker = "must-not-appear-in-launcher-output"
            env_file.write_text(
                "\n".join(
                    (
                        f"CHAT_API_KEY={secret_marker}",
                        "AKANE_INSTANCE_ID=cloud-personal",
                        "AKANE_DATA_ROOT=Z:\\cloud-data",
                        "QQ_BRIDGE_ENABLED=true",
                        "HOST=0.0.0.0",
                    )
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                (
                    powershell,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(ROOT / "start_akane_local_test.ps1"),
                    "-DataRoot",
                    str(data_root),
                    "-EnvFile",
                    str(env_file),
                    "-PrepareOnly",
                    "-SkipCharacterSeed",
                ),
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            output = f"{completed.stdout}\n{completed.stderr}"
            self.assertEqual(completed.returncode, 0, output)
            self.assertNotIn(secret_marker, output)
            self.assertFalse((data_root / "users_data" / "memory.db").exists())

            context = resolve_instance_context(data_root=data_root, selected_instance_id="local-test")
            self.assertEqual(context.instance_id, "local-test")
            self.assertEqual(context.character_pack_id, "reimu")
            self.assertTrue(context.features.care)
            self.assertFalse(context.channels.qq.enabled)
            self.assertEqual(context.plugins, ())

    def test_batch_wrapper_targets_the_dedicated_profile(self) -> None:
        source = (ROOT / "start_akane_local_test.bat").read_text(encoding="utf-8")
        self.assertIn("start_akane_local_test.ps1", source)
        self.assertNotIn("start_akane_cloud_personal", source)

    def test_cloud_profile_sync_has_a_narrow_allowlist_and_external_target(self) -> None:
        source = (ROOT / "sync_akane_local_test_from_cloud.ps1").read_text(encoding="utf-8")
        self.assertIn('"CHAT_API_KEY"', source)
        self.assertIn('"VISION_MODEL_NAME"', source)
        self.assertIn('"EXECUTION_ENABLED"', source)
        self.assertIn('"MEMORY_BACKEND"', source)
        self.assertNotIn('"QQ_ONEBOT_ACCESS_TOKEN"', source)
        self.assertNotIn('"QQ_WEBHOOK_SECRET"', source)
        self.assertNotIn('"AKANE_ADMIN_TOKEN"', source)
        self.assertIn('"cloud-aligned.env"', source)
        self.assertIn("LocalApplicationData", source)
        self.assertIn("Secrets, QQ credentials, paths, logs and memory were not printed or copied", source)

    def test_package_sync_is_revision_bound_and_contract_checked(self) -> None:
        source = (ROOT / "scripts" / "sync_akane_local_packages.ps1").read_text(encoding="utf-8")
        self.assertIn("requirements-packages.txt", source)
        self.assertIn("git -C $packageRoot rev-parse HEAD", source)
        self.assertIn("--untracked-files=no", source)
        self.assertIn("build_extracted_package_wheelhouse.py", source)
        self.assertIn("--internal-only", source)
        self.assertIn("--force-reinstall", source)
        self.assertIn("browse_memory", source)
        self.assertIn("open_memory", source)


if __name__ == "__main__":
    unittest.main()
