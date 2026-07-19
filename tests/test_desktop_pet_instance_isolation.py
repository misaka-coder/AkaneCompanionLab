from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _ps_quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


class DesktopPetInstanceIsolationTests(unittest.TestCase):
    def test_bound_bot_urls_share_one_fail_closed_router(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is unavailable")
        result = subprocess.run(
            [node, str(ROOT / "desktop_pet_next" / "scripts" / "bot-routing-smoke.mjs")],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("bot routing smoke: ok", result.stdout)

    def test_instance_storage_keeps_equal_ids_in_separate_namespaces(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is unavailable")
        result = subprocess.run(
            [node, str(ROOT / "desktop_pet_next" / "scripts" / "instance-storage-smoke.mjs")],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("instance storage smoke: ok", result.stdout)

    def test_launcher_decision_rejects_another_instance_before_stop_or_reuse(self) -> None:
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if not powershell:
            self.skipTest("PowerShell is unavailable")
        helper = ROOT / "scripts" / "akane_instance_launcher.ps1"
        command = (
            f". {_ps_quote(helper)}; "
            "$health = [pscustomobject]@{ status='ok'; instance_id='instance-b'; root_binding='valid' }; "
            "$reuse = Get-AkaneBackendPortDecision -PortInUse $true -Health $health "
            "-ExpectedInstanceId 'instance-a' -ReuseBackend $true -ManagedProcess $true; "
            "$stop = Get-AkaneBackendPortDecision -PortInUse $true -Health $health "
            "-ExpectedInstanceId 'instance-a' -ReuseBackend $false -ManagedProcess $true; "
            "@{ reuse=$reuse; stop=$stop } | ConvertTo-Json -Compress"
        )
        result = subprocess.run(
            [powershell, "-NoProfile", "-Command", command],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(payload, {"reuse": "reject", "stop": "reject"})

    def test_named_data_root_never_copies_local_default_legacy_state(self) -> None:
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if not powershell:
            self.skipTest("PowerShell is unavailable")
        helper = ROOT / "scripts" / "akane_data_root.ps1"
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            project = temp / "project"
            (project / "users_data").mkdir(parents=True)
            (project / "desktop_pet_creator_kit" / "characters" / "legacy").mkdir(parents=True)
            (project / "users_data" / "legacy.txt").write_text("local", encoding="utf-8")
            (project / "desktop_pet_creator_kit" / "characters" / "legacy" / "character.json").write_text(
                "{}", encoding="utf-8"
            )
            named_root = temp / "named"
            local_root = temp / "local"
            command = (
                f". {_ps_quote(helper)}; "
                f"$named = Initialize-AkaneDataRoot -ProjectRoot {_ps_quote(project)} "
                f"-InstanceId 'instance-a' -DataRoot {_ps_quote(named_root)}; "
                f"$local = Initialize-AkaneDataRoot -ProjectRoot {_ps_quote(project)} "
                f"-InstanceId 'local-default' -DataRoot {_ps_quote(local_root)}; "
                "@{ namedCopied=$named.Copied; localCopied=$local.Copied } | ConvertTo-Json -Compress"
            )
            result = subprocess.run(
                [powershell, "-NoProfile", "-Command", command],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertEqual(payload["namedCopied"], 0)
            self.assertGreaterEqual(payload["localCopied"], 2)
            self.assertFalse((named_root / "users_data" / "legacy.txt").exists())
            self.assertFalse((named_root / "characters" / "legacy" / "character.json").exists())
            self.assertTrue((local_root / "users_data" / "legacy.txt").is_file())

    def test_tauri_mutable_artifacts_and_admin_secret_stay_instance_bound(self) -> None:
        tauri_source = (ROOT / "desktop_pet_next" / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
        control_center_source = (ROOT / "desktop_pet_next" / "src" / "control-center-lab.js").read_text(
            encoding="utf-8"
        )
        workshop_source = (ROOT / "desktop_pet_next" / "src" / "workshop.js").read_text(encoding="utf-8")

        self.assertNotIn("app_cache_dir()", tauri_source)
        self.assertIn("instance_id: String", tauri_source)
        self.assertIn("verify_backend_instance", tauri_source)
        self.assertIn('desktop_runtime_cache_dir("attachments/audio")', tauri_source)
        self.assertIn('desktop_runtime_cache_dir("character_import")', tauri_source)
        self.assertIn('desktop_runtime_cache_dir("export_staging")', tauri_source)
        self.assertIn('const ADMIN_TOKEN_ENV: &str = "AKANE_ADMIN_TOKEN"', tauri_source)
        self.assertIn("backend_admin_request", tauri_source)
        self.assertIn('invoke("backend_admin_request"', control_center_source)
        self.assertIn('invoke("backend_admin_request"', workshop_source)
        self.assertNotIn("AKANE_ADMIN_TOKEN", control_center_source)
        self.assertNotIn("AKANE_ADMIN_TOKEN", workshop_source)
        self.assertNotIn("adminToken", control_center_source)
        self.assertNotIn("adminToken", workshop_source)

    def test_launch_chain_exposes_and_forwards_instance_parameters(self) -> None:
        launcher = (ROOT / "start_akane_next.ps1").read_text(encoding="utf-8")
        bootstrap = (ROOT / "scripts" / "bootstrap_akane_windows.ps1").read_text(encoding="utf-8")
        direct = (ROOT / "desktop_pet_next" / "scripts" / "start-next.ps1").read_text(encoding="utf-8")
        for source in (launcher, bootstrap, direct):
            self.assertIn("$InstanceId", source)
            self.assertIn("$DataRoot", source)
            self.assertIn("$BackendPort", source)
            self.assertIn("$EnvFile", source)
        self.assertIn("Get-AkaneBackendPortDecision", launcher)
        self.assertIn("akane_backend.$safeInstanceId.log", launcher)
        self.assertIn(
            "named_instance_requires_data_root", (ROOT / "scripts" / "akane_data_root.ps1").read_text(encoding="utf-8")
        )


if __name__ == "__main__":
    unittest.main()
