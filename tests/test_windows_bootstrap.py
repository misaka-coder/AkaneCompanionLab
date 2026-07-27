from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WindowsBootstrapContractTests(unittest.TestCase):
    def test_public_launchers_share_one_bootstrap_entry(self) -> None:
        ascii_launcher = (ROOT / "start_akane.bat").read_text(encoding="utf-8")
        chinese_launcher = (ROOT / "启动_Akane.bat").read_text(encoding="utf-8")

        self.assertIn("scripts\\bootstrap_akane_windows.ps1", ascii_launcher)
        self.assertIn('call "%~dp0start_akane.bat"', chinese_launcher)

    def test_bootstrap_prepares_core_and_degrades_auto_mode_to_web(self) -> None:
        source = (ROOT / "scripts" / "bootstrap_akane_windows.ps1").read_text(encoding="utf-8")

        self.assertIn('[ValidateSet("Auto", "Desktop", "Web")]', source)
        self.assertIn("python_dependency_install_failed", source)
        self.assertIn("Copy-Item -LiteralPath $examplePath -Destination $envPath", source)
        self.assertIn("No model service is configured yet", source)
        self.assertIn("Initialize-AkaneDataRoot", source)
        self.assertIn("AKANE_DATA_ROOT_READY", source)
        self.assertIn("without overwriting existing data", source)
        self.assertNotIn("Assert-CoreSourceDependencies", source)
        self.assertNotIn("source checkout was not found", source)
        self.assertIn("Test-PackagedCoreDependencies", source)
        self.assertIn("check_packaged_dependencies.py", source)
        self.assertIn("akane-package-wheelhouse.json", source)
        self.assertIn("AKANE_PACKAGE_WHEELHOUSE", source)
        self.assertIn("AKANE_PACKAGE_INDEX_URL", source)
        self.assertIn("--no-index", source)
        self.assertIn("--find-links", source)
        self.assertIn("--force-reinstall --no-deps", source)
        self.assertIn("packaged_core_install_failed", source)
        self.assertIn("akane_package_wheelhouse_invalid", source)
        self.assertIn("internal_artifact_hash_mismatch", source)
        self.assertIn("runtime_dependency_closure_missing", source)
        self.assertIn("akane_package_artifacts_unavailable", source)
        self.assertIn("packaged_dependency_validation_failed", source)
        dependency_checker = (ROOT / "scripts" / "check_packaged_dependencies.py").read_text(encoding="utf-8")
        self.assertIn("runtime_contract_missing", dependency_checker)
        self.assertIn("memory_metadata_has_signal", dependency_checker)
        self.assertIn("validate_onebot_identity", dependency_checker)
        self.assertIn("voice_event_to_dict", dependency_checker)
        self.assertIn("import capcore", source)
        self.assertIn(
            "import capcore, capcore_adapter_mcp, capcore_adapter_python, capcore_adapter_speech, capcore_adapter_comfyui, capcore_provider_native_tools, capcore_provider_openai, capcore_provider_anthropic, charpack_core, promptpack_core, memcore, voicecore",
            source,
        )
        self.assertIn("AKANE_OPEN_SETTINGS_ON_START", (ROOT / "start_akane_next.ps1").read_text(encoding="utf-8"))
        self.assertNotIn("notepad.exe", source.lower())
        self.assertIn('return "Web"', source)
        self.assertIn("Start-WebMode", source)
        self.assertIn("Start-DesktopMode", source)
        self.assertIn("-SkipDesktop", source)
        self.assertIn("Wait-BackendReady", source)
        self.assertNotIn("TEXT_API_KEY=", source)
        self.assertNotIn("Get-FileHash", source)
        self.assertIn("[System.Security.Cryptography.SHA256]::Create()", source)

    def test_desktop_launcher_matches_minimal_instance_health(self) -> None:
        source = (ROOT / "start_akane_next.ps1").read_text(encoding="utf-8")

        self.assertIn("$Health.instance_id", source)
        self.assertIn("$Health.root_binding", source)
        self.assertIn("Get-BackendListeningProcessId", source)
        self.assertIn("ExpectedInstanceId", source)
        self.assertNotIn("$health.pid", source)

    def test_shared_data_root_script_is_copy_only(self) -> None:
        source = (ROOT / "scripts" / "akane_data_root.ps1").read_text(encoding="utf-8")

        self.assertIn('Join-Path $base "Akane"', source)
        self.assertIn('Join-Path $root "characters"', source)
        self.assertIn('Join-Path $root "users_data"', source)
        self.assertIn("Copy-AkaneMissingTree", source)
        self.assertIn("if (Test-Path -LiteralPath $target)", source)
        self.assertNotIn("Remove-Item", source)

    def test_desktop_launcher_has_no_legacy_settings_switch(self) -> None:
        root_launcher = (ROOT / "start_akane_next.ps1").read_text(encoding="utf-8")
        direct_launcher = (ROOT / "desktop_pet_next" / "scripts" / "start-next.ps1").read_text(encoding="utf-8")

        self.assertNotIn("LegacySettings", root_launcher)
        self.assertNotIn("AKANE_LEGACY_SETTINGS", root_launcher)
        self.assertNotIn("LegacySettings", direct_launcher)
        self.assertNotIn("AKANE_LEGACY_SETTINGS", direct_launcher)

    def test_desktop_release_launchers_rebuild_stale_exe(self) -> None:
        root_launcher = (ROOT / "start_akane_next.ps1").read_text(encoding="utf-8")
        direct_launcher = (ROOT / "desktop_pet_next" / "scripts" / "start-next.ps1").read_text(encoding="utf-8")

        for source in (root_launcher, direct_launcher):
            self.assertIn("Get-NewestInputWriteTime", source)
            self.assertIn("releaseIsStale", source)
            self.assertIn("Release exe is older than source files", source)
            self.assertIn("src-tauri\\Cargo.toml", source)
            self.assertIn("src-tauri\\Cargo.lock", source)
            self.assertIn("src-tauri\\build.rs", source)
            self.assertIn("settings.html", source)
            self.assertIn("control-center-lab.html", source)
            self.assertIn("Stop-AkaneDesktopProcesses", source)
            self.assertIn('Get-Process -Name "akane_desktop_pet_next"', source)
            self.assertIn("Stopping existing Akane Next desktop PID", source)

        self.assertIn("[switch]$NoBuild", direct_launcher)
        self.assertIn("[switch]$Rebuild", direct_launcher)
        self.assertIn("$shouldBuild = (-not $releaseExists) -or $releaseIsStale -or $Rebuild", direct_launcher)
        self.assertIn("Release exe not found. Building Akane Next", direct_launcher)

    def test_release_audit_requires_the_bootstrap_files(self) -> None:
        audit = (ROOT / "scripts" / "audit_public_release.ps1").read_text(encoding="utf-8")
        exporter = (ROOT / "scripts" / "export_public_alpha.ps1").read_text(encoding="utf-8")

        self.assertIn('"start_akane.bat"', audit)
        self.assertIn('"启动_Akane.bat"', audit)
        self.assertIn('"scripts/bootstrap_akane_windows.ps1"', audit)
        self.assertIn('"requirements-packages.txt"', audit)
        self.assertIn('"requirements-runtime.txt"', audit)
        self.assertIn('"scripts/build_extracted_package_wheelhouse.py"', audit)
        self.assertIn('"scripts/check_packaged_dependencies.py"', audit)
        self.assertIn('"scripts/verify_extracted_package_independence.py"', audit)
        self.assertIn('"docs/productization_release_gate_v1.md"', audit)
        self.assertIn("internal_productization_session_doc", audit)
        self.assertIn('"docs/productization_work_session_"', exporter)
        self.assertIn("legacy_tauri_settings_file", audit)
        self.assertIn("settings_compat_loads_legacy_settings_bundle", audit)
        self.assertIn("launcher_missing_stale_release_guard", audit)
        self.assertIn("root_launcher_missing_user_data_root", audit)
        self.assertIn("direct_launcher_missing_build_control_flags", audit)
        self.assertIn('"web/assets/scenes/家/白天客厅.png"', audit)


if __name__ == "__main__":
    unittest.main()
