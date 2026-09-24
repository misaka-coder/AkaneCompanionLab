from __future__ import annotations

import shutil
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from companion_v01.instance_profile import resolve_instance_context
from companion_v01.local_capability_config import get_approval_policy_config, save_capability_approval_modes
from scripts.initialize_akane_local_test_policy import initialize_local_test_policy
from scripts.seed_akane_local_capabilities import seed_local_capability_profile


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
            '$env:BROWSER_PAGE_PRIVATE_NETWORK_ACCESS = "true"',
            "Ensure-AkaneLocalTestSecrets -DataRoot $resolvedDataRoot",
        )
        for fragment in overrides:
            self.assertGreater(source.index(fragment), import_offset)
            self.assertGreater(source.index(fragment), private_import_offset)
        self.assertIn('[int]$BackendPort = 11999', source)
        self.assertIn('-SeedBundledCharacters:$seedCharacters', source)
        self.assertIn("Sync-AkaneLocalPackages", source)
        self.assertIn("initialize_akane_local_test_policy.py", source)
        self.assertIn("Settings -> Abilities -> Safety Boundary", source)
        self.assertNotIn("CloudSatellite =", source)
        self.assertIn("local-test-secrets.env", source)
        self.assertIn("Existing local QQ instance manifest preserved.", (ROOT / "start_akane_local_qq_test.ps1").read_text(encoding="utf-8"))

    def test_prepare_only_creates_isolated_named_instance_without_leaking_env_values(self) -> None:
        powershell = shutil.which("pwsh")
        if not powershell:
            self.skipTest("PowerShell 7 is unavailable")

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
        self.assertIn("where pwsh.exe", source)
        self.assertIn("pwsh.exe -NoLogo -NoProfile", source)
        self.assertNotIn("\npowershell -NoProfile", source)

    def test_local_profiles_fail_early_outside_powershell_7(self) -> None:
        for launcher_name in ("start_akane_local_test.ps1", "start_akane_local_qq_test.ps1"):
            source = (ROOT / launcher_name).read_text(encoding="utf-8")
            self.assertIn("$PSVersionTable.PSVersion.Major -lt 7", source, launcher_name)
            self.assertIn("powershell_7_required: launch with pwsh", source, launcher_name)
        self.assertNotIn("start_akane_cloud_personal", source)

    def test_cloud_profile_sync_has_a_narrow_allowlist_and_external_target(self) -> None:
        source = (ROOT / "sync_akane_local_test_from_cloud.ps1").read_text(encoding="utf-8")
        self.assertIn('GetEnvironmentVariable("CHAT_API_KEY", "Process")', source)
        self.assertIn('GetEnvironmentVariable("IMAGE_GENERATION_API_KEY", "Process")', source)
        self.assertIn('$requestedChatModel = "gpt-5.6-luna"', source)
        self.assertIn('$localChatProtocol = "openai"', source)
        self.assertIn('$lines.Add("MEMCORE_SUMMARY_MODEL_NAME=$localChatModel")', source)
        self.assertIn('$lines.Add("EMBEDDING_PROVIDER=hashed")', source)
        self.assertIn('$lines.Add("ENABLE_NATIVE_TOOL_DECISION=true")', source)
        self.assertNotIn('$lines.Add("ENABLE_NATIVE_TOOL_DECISION=false")', source)
        self.assertIn("/models", source)
        self.assertIn("$localChatModel", source)
        self.assertIn("local_pinai_api_key_missing", source)
        self.assertIn('Import-AkaneEnvFile -Path $projectEnvFile', source)
        self.assertIn('foreach ($name in @("CHAT", "TEXT", "AUX"))', source)
        self.assertIn('"VISION_MODEL_NAME"', source)
        self.assertIn('"EXECUTION_ENABLED"', source)
        self.assertIn('"MEMORY_BACKEND"', source)
        self.assertIn("MEMCORE_OPERATION_PROJECTION_POLICY=compact_after_terminal", source)
        remote_allowlist = source[source.index("$allowlist = @(") : source.index(")\n$allowlistJson")]
        self.assertNotIn('"CHAT_API_KEY"', remote_allowlist)
        self.assertNotIn('"TEXT_API_KEY"', remote_allowlist)
        self.assertNotIn('"QQ_ONEBOT_ACCESS_TOKEN"', source)
        self.assertNotIn('"QQ_WEBHOOK_SECRET"', source)
        self.assertNotIn('"AKANE_ADMIN_TOKEN"', source)
        self.assertIn('"cloud-aligned.env"', source)
        self.assertIn("LocalApplicationData", source)
        self.assertIn("Provider secrets, QQ credentials, paths, logs and memory were not printed or copied", source)

    def test_package_sync_is_content_bound_and_contract_checked(self) -> None:
        source = (ROOT / "scripts" / "sync_akane_local_packages.ps1").read_text(encoding="utf-8")
        self.assertIn("requirements-packages.txt", source)
        self.assertIn("Get-FileHash", source)
        self.assertIn("GetRelativePath", source)
        self.assertNotIn("local_package_source_dirty", source)
        self.assertIn("build_extracted_package_wheelhouse.py", source)
        self.assertIn("--internal-only", source)
        self.assertIn("--force-reinstall", source)
        self.assertGreaterEqual(source.count("| Out-Host"), 2)
        self.assertIn("browse_memory", source)
        self.assertIn("open_memory", source)
        self.assertIn("render_text_with_mentions", source)
        self.assertIn("QuotedMessage, 'mentions'", source)
        self.assertIn("Test-AkaneLocalPackagesCurrent", source)
        self.assertIn("check_memcore_runtime_contract.py", source)

    def test_skip_package_sync_still_validates_runtime_contracts(self) -> None:
        for launcher_name in ("start_akane_local_test.ps1", "start_akane_local_qq_test.ps1"):
            source = (ROOT / launcher_name).read_text(encoding="utf-8")
            sync_end = source.index("\n}\n", source.index("if (-not $SkipPackageSync)"))
            contract_check = source.index("Test-AkaneLocalPackageContracts", sync_end)
            self.assertGreater(contract_check, sync_end, launcher_name)
            self.assertIn("local_package_contract_validation_failed", source[contract_check:])
            self.assertIn("local_package_content_mismatch", source[contract_check:])

    def test_local_qq_launcher_attaches_desktop_by_default(self) -> None:
        source = (ROOT / "start_akane_local_qq_test.ps1").read_text(encoding="utf-8")

        self.assertIn("[switch]$SkipDesktop", source)
        self.assertIn("$launcherArgs.SkipDesktop = $true", source)
        self.assertIn("$launcherArgs.DeviceOnly = $true", source)
        self.assertIn("[switch]$ReuseBackend", source)
        self.assertIn("if ($ReuseBackend) { $launcherArgs.ReuseBackend = $true }", source)
        self.assertNotIn("-SkipDesktop `\n", source)

    def test_local_qq_launcher_seeds_missing_shared_capabilities(self) -> None:
        source = (ROOT / "start_akane_local_qq_test.ps1").read_text(encoding="utf-8")

        self.assertIn("seed_akane_local_capabilities.py", source)
        self.assertIn("--source-users-data-root", source)
        self.assertIn("--destination-users-data-root", source)
        self.assertLess(source.index("seed_akane_local_capabilities.py"), source.index("initialize_akane_local_test_policy.py"))

    def test_local_qq_setup_enables_attachment_bytes_without_shared_directories(self) -> None:
        powershell = shutil.which("pwsh")
        if not powershell:
            self.skipTest("PowerShell 7 is unavailable")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "onebot.json"
            config_path.write_text(json.dumps({
                "network": {"httpServers": [], "httpClients": []},
                "enableLocalFile2Url": False, "parseMultMsg": True,
            }), encoding="utf-8")
            runner = root / "configure.ps1"
            runner.write_text('''param($Launcher, $ConfigPath)
$ErrorActionPreference = "Stop"
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Launcher, [ref]$null, [ref]$null)
$ast.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -in @("Write-AkaneUtf8Atomic", "Set-AkaneNapCatOneBotConfig")}, $false) | ForEach-Object { Invoke-Expression $_.Extent.Text }
Set-AkaneNapCatOneBotConfig -ConfigPath $ConfigPath -ApiPort 3003 -HostPort 12001 -AccessToken "fixture-api-token" -WebhookSecret "fixture-webhook-token"
''', encoding="utf-8")
            completed = subprocess.run(
                [powershell, "-NoProfile", "-File", str(runner), str(ROOT / "start_akane_local_qq_test.ps1"), str(config_path)],
                capture_output=True, text=True, timeout=20, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            configured = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertTrue(configured["enableLocalFile2Url"])
            self.assertTrue(configured["parseMultMsg"])
            self.assertEqual(configured["network"]["httpServers"][0]["host"], "127.0.0.1")
            self.assertEqual(configured["network"]["httpServers"][0]["token"], "fixture-api-token")
            self.assertNotIn("fixture-api-token", completed.stdout)

    def test_capability_seed_adds_missing_entries_without_overwriting_instance_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_root = root / "shared"
            destination_root = root / "isolated"
            source_path = source_root / "master" / "capabilities" / "capabilities.yaml"
            destination_path = destination_root / "master" / "capabilities" / "capabilities.yaml"
            source_path.parent.mkdir(parents=True)
            destination_path.parent.mkdir(parents=True)
            source_path.write_text(
                "schemaVersion: 2\nproviders:\n  tts:\n    endpoint: http://127.0.0.1:9880\nvoiceProfiles:\n  dania:\n    providerId: tts\n",
                encoding="utf-8",
            )
            destination_path.write_text(
                "schemaVersion: 1\napprovalPolicy:\n  capabilityModes:\n    ops: trusted_auto_allow\n    extensions: trusted_auto_allow\nproviders:\n  local:\n    endpoint: http://127.0.0.1:9999\n",
                encoding="utf-8",
            )

            status, count = seed_local_capability_profile(
                source_users_data_root=source_root,
                destination_users_data_root=destination_root,
            )
            self.assertEqual((status, count), ("seeded", 2))
            payload = destination_path.read_text(encoding="utf-8")
            self.assertIn("ops: trusted_auto_allow", payload)
            self.assertIn("local:", payload)
            self.assertIn("dania:", payload)
            self.assertIn("tts:", payload)

            status, count = seed_local_capability_profile(
                source_users_data_root=source_root,
                destination_users_data_root=destination_root,
            )
            self.assertEqual((status, count), ("unchanged", 0))

    def test_new_local_profile_defaults_to_full_access_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            users_data_root = Path(temp_dir) / "users_data"

            status, mode = initialize_local_test_policy(users_data_root=users_data_root)

            self.assertEqual((status, mode), ("initialized", "trusted_auto_allow"))
            policy = get_approval_policy_config(
                base_dir=users_data_root,
                profile_user_id="master",
            )["approvalPolicy"]
            self.assertEqual(
                {item["id"]: item["mode"] for item in policy["families"]},
                {"ops": "trusted_auto_allow", "extensions": "trusted_auto_allow"},
            )

    def test_existing_local_approval_choice_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            users_data_root = Path(temp_dir) / "users_data"
            saved = save_capability_approval_modes(
                base_dir=users_data_root,
                profile_user_id="master",
                modes={"ops": "ask_each_time", "extensions": "ask_each_time"},
            )
            self.assertTrue(saved["ok"])

            status, mode = initialize_local_test_policy(users_data_root=users_data_root)

            self.assertEqual((status, mode), ("preserved", "ask_each_time"))
            policy = get_approval_policy_config(
                base_dir=users_data_root,
                profile_user_id="master",
            )["approvalPolicy"]
            self.assertEqual(
                {item["id"]: item["mode"] for item in policy["families"]},
                {"ops": "ask_each_time", "extensions": "ask_each_time"},
            )

    def test_policy_initializer_runs_as_a_script_outside_the_repo_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            users_data_root = root / "users_data"
            completed = subprocess.run(
                (
                    str(ROOT / ".venv" / "Scripts" / "python.exe"),
                    str(ROOT / "scripts" / "initialize_akane_local_test_policy.py"),
                    "--users-data-root",
                    str(users_data_root),
                ),
                cwd=root,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout.strip(), "initialized:trusted_auto_allow")


if __name__ == "__main__":
    unittest.main()
