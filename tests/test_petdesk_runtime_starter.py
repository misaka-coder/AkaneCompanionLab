from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PetdeskRuntimeStarterTests(unittest.TestCase):
    def test_starter_fetches_health_and_whitelists_runtime_env(self) -> None:
        source = (ROOT / "scripts" / "start_petdesk_runtime.ps1").read_text(encoding="utf-8")

        self.assertIn("/pet/health", source)
        self.assertIn('"Cache-Control" = "no-store"', source)
        self.assertIn("ConvertTo-SafePetdeskRuntimeEnv", source)
        self.assertIn("MaxRuntimeEnvValueLength = 20000", source)
        self.assertIn("VITE_PETDESK_BACKEND_URL", source)
        self.assertIn("pnpm_not_found", source)
        self.assertIn("CheckOnly completed", source)
        self.assertIn("DryRun completed", source)
        self.assertIn("Invoke-PetdeskMvpSmoke", source)
        self.assertIn("run_petdesk_mvp_smoke.py", source)
        self.assertIn("SmokeOnly completed", source)
        self.assertIn("StartupSmokeOnly completed", source)
        self.assertIn("RunSmokeBeforeLaunch", source)
        self.assertIn("RunStartupSmokeBeforeLaunch", source)
        self.assertIn("SkipStartupSmoke", source)
        self.assertIn("runImplicitStartupSmoke", source)
        self.assertIn("-not $SkipStartupSmoke", source)
        self.assertIn("--startup-only", source)
        self.assertIn("Restore-ScopedEnv", source)
        self.assertIn("RuntimeMode", source)
        self.assertIn("RuntimeExe", source)
        self.assertIn("Resolve-PetdeskRuntimeExe", source)
        self.assertIn("Invoke-PetdeskRuntimeRelease", source)
        self.assertIn("Starting petdesk-runtime release window", source)
        self.assertIn("petdesk_runtime_release_exe_not_found", source)
        self.assertIn(".cargo\\config.toml", source)
        self.assertIn("release\\petdesk_runtime.exe", source)
        self.assertIn("Runtime mode: {0}", source)

        allowed_keys = {
            "VITE_PETDESK_INTERACTION_PROFILE",
            "VITE_PETDESK_INTERACTION_PROFILE_JSON",
            "VITE_PETDESK_LIVE2D_MODEL_LAYOUT_PROFILE",
            "VITE_PETDESK_LIVE2D_MODEL_LAYOUT_JSON",
            "VITE_PETDESK_LIVE2D_MOTION_MAP_JSON",
            "VITE_PETDESK_LIVE2D_EXPRESSION_MAP_JSON",
            "VITE_PETDESK_RESOURCE_MANIFEST_URL",
            "VITE_PETDESK_RESOURCE_MANIFEST_JSON",
        }
        for key in allowed_keys:
            self.assertIn(key, source)

        self.assertNotIn("Set-Item -Path Env:$name", source)
        self.assertNotIn("runtimeEnv[$name]", source)

    def test_starter_is_separate_from_public_desktop_launcher(self) -> None:
        bootstrap = (ROOT / "scripts" / "bootstrap_akane_windows.ps1").read_text(encoding="utf-8")
        root_launcher = (ROOT / "start_akane_next.ps1").read_text(encoding="utf-8")
        petdesk_script = (ROOT / "start_akane_petdesk.ps1").read_text(encoding="utf-8")
        petdesk_bat = (ROOT / "start_akane_petdesk.bat").read_text(encoding="utf-8")

        self.assertNotIn("start_petdesk_runtime.ps1", bootstrap)
        self.assertNotIn("start_petdesk_runtime.ps1", root_launcher)
        self.assertIn("scripts\\start_petdesk_runtime.ps1", petdesk_script)
        self.assertIn("SmokeOnly", petdesk_script)
        self.assertIn("StartupSmokeOnly", petdesk_script)
        self.assertIn("SkipStartupSmoke", petdesk_script)
        self.assertIn("RunSmokeBeforeLaunch", petdesk_script)
        self.assertIn("RunStartupSmokeBeforeLaunch", petdesk_script)
        self.assertIn("RuntimeMode", petdesk_script)
        self.assertIn("RuntimeExe", petdesk_script)
        self.assertIn("scripts\\start_petdesk_runtime.ps1", petdesk_bat)

    def test_m36_doc_records_transition_boundary(self) -> None:
        doc = (ROOT / "docs" / "petdesk_akane_starter_m36.md").read_text(encoding="utf-8")

        self.assertIn("does not replace the", doc)
        self.assertIn("public `desktop_pet_next` one-click launcher yet", doc)
        self.assertIn("GET /pet/health", doc)
        self.assertIn("whitelist runtimeEnv", doc)
        self.assertIn("fallback to runtime defaults", doc)

    def test_m41_doc_records_mvp_smoke_boundary(self) -> None:
        doc = (ROOT / "docs" / "petdesk_akane_mvp_closeout_m41.md").read_text(encoding="utf-8")

        self.assertIn("AKANE_PETDESK_MVP_SMOKE_OK", doc)
        self.assertIn("SmokeOnly", doc)
        self.assertIn("RunSmokeBeforeLaunch", doc)
        self.assertIn("/audio/petdesk/<token>", doc)
        self.assertIn("full MVP audio acceptance", doc)

    def test_m46_doc_records_startup_manifest_gate(self) -> None:
        doc = (ROOT / "docs" / "petdesk_startup_gate_m46.md").read_text(encoding="utf-8")

        self.assertIn("VITE_PETDESK_RESOURCE_MANIFEST_URL", doc)
        self.assertIn("/pet/resource-manifest", doc)
        self.assertIn("/pet/snapshot.visual.assetHandle", doc)
        self.assertIn("staticImages[assetHandle]", doc)
        self.assertIn("image/*", doc)

    def test_m47_doc_records_startup_only_smoke(self) -> None:
        doc = (ROOT / "docs" / "petdesk_startup_only_smoke_m47.md").read_text(encoding="utf-8")

        self.assertIn("--startup-only", doc)
        self.assertIn("StartupSmokeOnly", doc)
        self.assertIn("RunStartupSmokeBeforeLaunch", doc)
        self.assertIn("does not post", doc)
        self.assertIn("AKANE_PETDESK_STARTUP_SMOKE_OK", doc)

    def test_m48_doc_records_startup_preflight_policy(self) -> None:
        doc = (ROOT / "docs" / "petdesk_startup_preflight_m48.md").read_text(encoding="utf-8")

        self.assertIn("startup-only smoke before", doc)
        self.assertIn("SkipStartupSmoke", doc)
        self.assertIn("does not post `/pet/turn`", doc)
        self.assertIn("RunStartupSmokeBeforeLaunch", doc)
        self.assertIn("CheckOnly", doc)
        self.assertIn("DryRun", doc)

    def test_m50_doc_records_release_runtime_starter(self) -> None:
        doc = (ROOT / "docs" / "petdesk_release_runtime_starter_m50.md").read_text(encoding="utf-8")

        self.assertIn("RuntimeMode Release", doc)
        self.assertIn("RuntimeMode Dev", doc)
        self.assertIn("RuntimeExe", doc)
        self.assertIn("runtime_launch_env", doc)
        self.assertIn("does not replace", doc)
        self.assertIn("release mode is not", doc)
        self.assertIn("the default", doc)
        self.assertIn("petdesk_runtime.exe", doc)
        self.assertIn("startup smoke passes first", doc)

    def test_release_build_script_records_serial_warmup(self) -> None:
        source = (ROOT / "scripts" / "build_petdesk_runtime_release.ps1").read_text(encoding="utf-8")

        self.assertIn("CheckOnly completed. No release build was run.", source)
        self.assertIn("DryRun completed. No release build was run.", source)
        self.assertIn("SkipWarmup", source)
        self.assertIn("cargo", source)
        self.assertIn('"build", "--manifest-path", $cargoManifest, "--release", "-j", "1"', source)
        self.assertIn("pnpm", source)
        self.assertIn("tauri:build", source)
        self.assertIn(".cargo\\config.toml", source)
        self.assertIn("target-dir", source)
        self.assertIn("release\\petdesk_runtime.exe", source)
        self.assertIn("petdesk_runtime_release_exe_missing_after_build", source)
        self.assertIn("petdesk-runtime release build completed", source)
        self.assertNotIn("start_akane_next.ps1 @", source)
        self.assertNotIn("start_akane_petdesk.ps1", source)

    def test_release_wrapper_forces_release_mode_and_optional_build(self) -> None:
        source = (ROOT / "start_akane_petdesk_release.ps1").read_text(encoding="utf-8")
        batch = (ROOT / "start_akane_petdesk_release.bat").read_text(encoding="utf-8")

        self.assertIn("BuildFirst", source)
        self.assertIn("SkipBuildWarmup", source)
        self.assertIn("scripts\\build_petdesk_runtime_release.ps1", source)
        self.assertIn("scripts\\start_petdesk_runtime.ps1", source)
        self.assertIn('RuntimeMode = "Release"', source)
        self.assertNotIn('RuntimeMode = "Dev"', source)
        self.assertIn("RuntimeExe", source)
        self.assertIn("StartupSmokeOnly", source)
        self.assertIn("RunStartupSmokeBeforeLaunch", source)
        self.assertIn("SkipStartupSmoke", source)
        self.assertIn("start_akane_petdesk_release.ps1", batch)
        self.assertNotIn("scripts\\start_petdesk_runtime.ps1", batch)

    def test_m52_doc_records_release_build_script(self) -> None:
        doc = (ROOT / "docs" / "petdesk_release_build_script_m52.md").read_text(encoding="utf-8")

        self.assertIn("build_petdesk_runtime_release.ps1", doc)
        self.assertIn("cargo build --manifest-path src-tauri\\Cargo.toml --release -j 1", doc)
        self.assertIn("pnpm tauri:build", doc)
        self.assertIn("CheckOnly", doc)
        self.assertIn("DryRun", doc)
        self.assertIn("SkipWarmup", doc)
        self.assertIn("does not depend on memory", doc)

    def test_m53_doc_records_release_start_entry(self) -> None:
        doc = (ROOT / "docs" / "petdesk_release_start_entry_m53.md").read_text(encoding="utf-8")

        self.assertIn("start_akane_petdesk_release.ps1", doc)
        self.assertIn("start_akane_petdesk_release.bat", doc)
        self.assertIn("BuildFirst", doc)
        self.assertIn("SkipBuildWarmup", doc)
        self.assertIn("RuntimeMode = Release", doc)
        self.assertIn("do not replace", doc)
        self.assertIn("without changing the default", doc)
        self.assertIn("development launcher", doc)

    def test_m54_doc_records_operator_guide(self) -> None:
        doc = (ROOT / "docs" / "petdesk_operator_guide_m54.md").read_text(encoding="utf-8")

        self.assertIn("Petdesk Operator Guide M54", doc)
        self.assertIn("start_akane_petdesk_release.ps1", doc)
        self.assertIn("build_petdesk_runtime_release.ps1", doc)
        self.assertIn("stop_petdesk_runtime.ps1", doc)
        self.assertIn("start_akane_petdesk.ps1", doc)
        self.assertIn("StartupSmokeOnly", doc)
        self.assertIn("SmokeOnly", doc)
        self.assertIn("DryRun", doc)
        self.assertIn("AKANE_PETDESK_STARTUP_SMOKE_OK", doc)
        self.assertIn("petdesk_runtime_release_exe_not_found", doc)
        self.assertIn("link.exe", doc)
        self.assertIn("-Force", doc)
        self.assertIn("Do not stop the Akane backend or QQ bot", doc)
        self.assertIn("Window shows placeholder", doc)
        self.assertIn("No audio", doc)

    def test_stop_script_targets_only_petdesk_runtime(self) -> None:
        source = (ROOT / "scripts" / "stop_petdesk_runtime.ps1").read_text(encoding="utf-8")

        self.assertIn("Get-Process -Name petdesk_runtime", source)
        self.assertIn("Resolve-PetdeskRuntimeReleaseExePath", source)
        self.assertIn("Target release exe", source)
        self.assertIn("CheckOnly completed. No petdesk runtime process was stopped.", source)
        self.assertIn("CloseMainWindow", source)
        self.assertIn("Stop-Process -Id $process.Id -Force", source)
        self.assertIn("Re-run with -Force if needed", source)
        self.assertIn("Matching all petdesk_runtime.exe processes because -All was passed.", source)
        self.assertNotIn("python", source.lower())
        self.assertNotIn("qq", source.lower())
        self.assertNotIn("uvicorn", source.lower())

    def test_m55_doc_and_readme_record_stop_and_discovery(self) -> None:
        doc = (ROOT / "docs" / "petdesk_stop_and_discovery_m55.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("stop_petdesk_runtime.ps1", doc)
        self.assertIn("CheckOnly", doc)
        self.assertIn("-Force", doc)
        self.assertIn("-All", doc)
        self.assertIn("does not stop Akane backend, QQ bot", doc)
        self.assertIn("petdesk_operator_guide_m54.md", doc)
        self.assertIn("petdesk_operator_guide_m54.md", readme)
        self.assertIn("start_akane_petdesk_release.ps1", readme)
        self.assertIn("stop_petdesk_runtime.ps1", readme)

    def test_release_doctor_is_read_only_status_script(self) -> None:
        source = (ROOT / "scripts" / "check_petdesk_release.ps1").read_text(encoding="utf-8")

        self.assertIn("AKANE_PETDESK_RELEASE_DOCTOR_OK", source)
        self.assertIn("AKANE_PETDESK_RELEASE_DOCTOR_FAILED", source)
        self.assertIn("SkipBackendHttp", source)
        self.assertIn("/pet/health", source)
        self.assertIn("VITE_PETDESK_RESOURCE_MANIFEST_URL", source)
        self.assertIn("staticImages", source)
        self.assertIn("/pet/snapshot", source)
        self.assertIn("Get-Process -Name petdesk_runtime", source)
        self.assertIn("Resolve-PetdeskRuntimeReleaseExePath", source)
        self.assertIn(".cargo\\config.toml", source)
        self.assertIn("release\\petdesk_runtime.exe", source)
        self.assertIn("scripts\\build_petdesk_runtime_release.ps1", source)
        self.assertIn("scripts\\stop_petdesk_runtime.ps1", source)
        self.assertIn("scripts\\tools\\run_petdesk_mvp_smoke.py", source)
        self.assertNotIn("Stop-Process", source)
        self.assertNotIn("Start-Process", source)
        self.assertNotIn("start_akane_next.ps1", source)
        self.assertNotIn("uvicorn", source.lower())
        self.assertNotIn("qq", source.lower())

    def test_m56_doc_operator_guide_and_readme_record_release_doctor(self) -> None:
        doc = (ROOT / "docs" / "petdesk_release_doctor_m56.md").read_text(encoding="utf-8")
        guide = (ROOT / "docs" / "petdesk_operator_guide_m54.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("check_petdesk_release.ps1", doc)
        self.assertIn("SkipBackendHttp", doc)
        self.assertIn("/pet/health", doc)
        self.assertIn("runtimeEnv.VITE_PETDESK_RESOURCE_MANIFEST_URL", doc)
        self.assertIn("AKANE_PETDESK_RELEASE_DOCTOR_OK", doc)
        self.assertIn("does not launch the backend", doc)
        self.assertIn("does not launch", doc)
        self.assertIn("does not start the backend", guide)
        self.assertIn("AKANE_PETDESK_RELEASE_DOCTOR_FAILED", guide)
        self.assertIn("check_petdesk_release.ps1", readme)
        self.assertIn("doctor", readme)

    def test_release_acceptance_sequences_doctor_and_smoke_without_window(self) -> None:
        source = (ROOT / "scripts" / "accept_petdesk_release.ps1").read_text(encoding="utf-8")

        self.assertIn("AKANE_PETDESK_RELEASE_ACCEPTANCE_OK", source)
        self.assertIn("AKANE_PETDESK_RELEASE_ACCEPTANCE_FAILED", source)
        self.assertIn("AKANE_PETDESK_RELEASE_ACCEPTANCE_CHECK_OK", source)
        self.assertIn("check_petdesk_release.ps1", source)
        self.assertIn("start_akane_petdesk_release.ps1", source)
        self.assertIn("StartupSmokeOnly", source)
        self.assertIn("SmokeOnly", source)
        self.assertIn("Full", source)
        self.assertIn("SkipDoctor", source)
        self.assertIn("SkipSmoke", source)
        self.assertIn("StartBackend", source)
        self.assertIn("CheckOnly completed. No doctor, smoke, backend, or runtime process was launched.", source)
        self.assertIn("Backend start: disabled by default", source)
        self.assertNotIn("Stop-Process", source)
        self.assertNotIn("Start-Process", source)
        self.assertNotIn("Invoke-PetdeskRuntimeRelease", source)
        self.assertNotIn("petdesk_runtime.exe", source)
        self.assertNotIn("uvicorn", source.lower())
        self.assertNotIn("qq", source.lower())

    def test_m57_doc_operator_guide_and_readme_record_release_acceptance(self) -> None:
        doc = (ROOT / "docs" / "petdesk_release_acceptance_m57.md").read_text(encoding="utf-8")
        guide = (ROOT / "docs" / "petdesk_operator_guide_m54.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("accept_petdesk_release.ps1", doc)
        self.assertIn("doctor", doc)
        self.assertIn("startup-only smoke", doc)
        self.assertIn("Full", doc)
        self.assertIn("StartBackend", doc)
        self.assertIn("AKANE_PETDESK_RELEASE_ACCEPTANCE_OK", doc)
        self.assertIn("does not replace the release starter", doc)
        self.assertIn("does not open the runtime window", guide)
        self.assertIn("AKANE_PETDESK_RELEASE_ACCEPTANCE_CHECK_OK", guide)
        self.assertIn("accept_petdesk_release.ps1", readme)
        self.assertIn("acceptance", readme)

    def test_release_bundle_export_script_records_safe_bundle_shape(self) -> None:
        source = (ROOT / "scripts" / "export_petdesk_release_bundle.ps1").read_text(encoding="utf-8")

        self.assertIn("AKANE_PETDESK_RELEASE_BUNDLE_CHECK_OK", source)
        self.assertIn("AKANE_PETDESK_RELEASE_BUNDLE_DRY_RUN_OK", source)
        self.assertIn("AKANE_PETDESK_RELEASE_BUNDLE_EXPORT_OK", source)
        self.assertIn("reports\\petdesk-release-bundles", source)
        self.assertIn("runtime/petdesk_runtime.exe", source)
        self.assertIn("scripts/start_petdesk_runtime_bundle.ps1", source)
        self.assertIn("scripts/audit_petdesk_release_bundle.ps1", source)
        self.assertIn("manifest.json", source)
        self.assertIn("Get-FileHash -Algorithm SHA256", source)
        self.assertIn("petdesk_release_bundle_m58.md", source)
        self.assertIn("petdesk_release_bundle_audit_m59.md", source)
        self.assertIn("bundle_output_path_already_exists", source)
        self.assertIn("unsafe_bundle_output_path", source)
        self.assertIn("Copy-RequiredFile", source)
        self.assertNotIn("Stop-Process", source)
        self.assertNotIn("Start-Process", source)
        self.assertNotIn("start_akane_next.ps1", source)
        self.assertNotIn("uvicorn", source.lower())
        self.assertNotIn("qq", source.lower())

    def test_bundle_start_script_is_source_independent_runtime_launcher(self) -> None:
        source = (ROOT / "scripts" / "start_petdesk_runtime_bundle.ps1").read_text(encoding="utf-8")

        self.assertIn("runtime\\petdesk_runtime.exe", source)
        self.assertIn("VITE_PETDESK_BACKEND_URL", source)
        self.assertIn("/pet/health", source)
        self.assertIn("ConvertTo-SafePetdeskRuntimeEnv", source)
        self.assertIn("MaxRuntimeEnvValueLength = 20000", source)
        self.assertIn("CheckOnly completed. No runtime process was launched.", source)
        self.assertIn("DryRun completed. Runtime env keys", source)
        self.assertIn("Restore-ScopedEnv", source)
        self.assertNotIn("Find-AkaneProjectRoot", source)
        self.assertNotIn("start_akane_next.ps1", source)
        self.assertNotIn("Stop-Process", source)
        self.assertNotIn("pnpm", source)
        self.assertNotIn("cargo", source)
        self.assertNotIn("qq", source.lower())

    def test_m58_doc_operator_guide_and_readme_record_release_bundle(self) -> None:
        doc = (ROOT / "docs" / "petdesk_release_bundle_m58.md").read_text(encoding="utf-8")
        guide = (ROOT / "docs" / "petdesk_operator_guide_m54.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("export_petdesk_release_bundle.ps1", doc)
        self.assertIn("start_petdesk_runtime_bundle.ps1", doc)
        self.assertIn("reports/petdesk-release-bundles", doc)
        self.assertIn("runtime/petdesk_runtime.exe", doc)
        self.assertIn("manifest.json", doc)
        self.assertIn("not a full Akane installer", doc)
        self.assertIn("does not start the backend", doc)
        self.assertIn("Runtime Bundle", guide)
        self.assertIn("export_petdesk_release_bundle.ps1", guide)
        self.assertIn("start_petdesk_runtime_bundle.ps1", guide)
        self.assertIn("not a full Akane installer", guide)
        self.assertIn("export_petdesk_release_bundle.ps1", readme)
        self.assertIn("bundle", readme)

    def test_release_bundle_audit_script_verifies_manifest_and_boundaries(self) -> None:
        source = (ROOT / "scripts" / "audit_petdesk_release_bundle.ps1").read_text(encoding="utf-8")

        self.assertIn("AKANE_PETDESK_RELEASE_BUNDLE_AUDIT_OK", source)
        self.assertIn("AKANE_PETDESK_RELEASE_BUNDLE_AUDIT_FAILED", source)
        self.assertIn("akane.petdesk.releaseBundle.v1", source)
        self.assertIn("manifest.json", source)
        self.assertIn("runtime/petdesk_runtime.exe", source)
        self.assertIn("scripts/start_petdesk_runtime_bundle.ps1", source)
        self.assertIn("scripts/audit_petdesk_release_bundle.ps1", source)
        self.assertIn("Get-FileHash -Algorithm SHA256", source)
        self.assertIn("manifest_hash_mismatch", source)
        self.assertIn("manifest_size_mismatch", source)
        self.assertIn("file_not_in_manifest", source)
        self.assertIn("forbidden_top_level_directory", source)
        self.assertIn("forbidden_env_file", source)
        self.assertIn("unexpected_exe", source)
        self.assertNotIn("Stop-Process", source)
        self.assertNotIn("Start-Process", source)
        self.assertNotIn("Invoke-RestMethod", source)
        self.assertNotIn("start_akane_next.ps1", source)
        self.assertNotIn("uvicorn", source.lower())
        self.assertNotIn("qq", source.lower())

    def test_m59_doc_operator_guide_and_readme_record_bundle_audit(self) -> None:
        doc = (ROOT / "docs" / "petdesk_release_bundle_audit_m59.md").read_text(encoding="utf-8")
        guide = (ROOT / "docs" / "petdesk_operator_guide_m54.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("audit_petdesk_release_bundle.ps1", doc)
        self.assertIn("manifest hash/size", doc)
        self.assertIn("AKANE_PETDESK_RELEASE_BUNDLE_AUDIT_OK", doc)
        self.assertIn("read-only", doc)
        self.assertIn("does not start the runtime", doc)
        self.assertIn("audit_petdesk_release_bundle.ps1", guide)
        self.assertIn("from inside the bundle", guide)
        self.assertIn("audit_petdesk_release_bundle.ps1", readme)
        self.assertIn("bundle 导出/审计", readme)

    def test_release_pipeline_orchestrates_existing_scripts_and_summary(self) -> None:
        source = (ROOT / "scripts" / "release_petdesk_bundle.ps1").read_text(encoding="utf-8")

        self.assertIn("AKANE_PETDESK_RELEASE_PIPELINE_OK", source)
        self.assertIn("AKANE_PETDESK_RELEASE_PIPELINE_FAILED", source)
        self.assertIn("AKANE_PETDESK_RELEASE_PIPELINE_CHECK_OK", source)
        self.assertIn("AKANE_PETDESK_RELEASE_PIPELINE_DRY_RUN_OK", source)
        self.assertIn("build_petdesk_runtime_release.ps1", source)
        self.assertIn("accept_petdesk_release.ps1", source)
        self.assertIn("export_petdesk_release_bundle.ps1", source)
        self.assertIn("audit_petdesk_release_bundle.ps1", source)
        self.assertIn("release_summary.json", source)
        self.assertIn("akane.petdesk.releasePipelineSummary.v1", source)
        self.assertIn("Update-BundleManifestFileEntry", source)
        self.assertIn("FullAcceptance", source)
        self.assertIn("SkipAcceptance", source)
        self.assertIn("BuildFirst", source)
        self.assertIn("SkipBuildWarmup", source)
        self.assertIn("StartBackend", source)
        self.assertIn("pre_audit", source)
        self.assertIn("petdesk release bundle final audit", source)
        self.assertNotIn("Stop-Process", source)
        self.assertNotIn("Start-Process", source)
        self.assertNotIn("Invoke-PetdeskRuntimeRelease", source)
        self.assertNotIn("pnpm tauri:build", source)
        self.assertNotIn("uvicorn", source.lower())
        self.assertNotIn("qq", source.lower())

    def test_m60_doc_operator_guide_and_readme_record_release_pipeline(self) -> None:
        doc = (ROOT / "docs" / "petdesk_release_pipeline_m60.md").read_text(encoding="utf-8")
        guide = (ROOT / "docs" / "petdesk_operator_guide_m54.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("release_petdesk_bundle.ps1", doc)
        self.assertIn("release_summary.json", doc)
        self.assertIn("BuildFirst", doc)
        self.assertIn("FullAcceptance", doc)
        self.assertIn("SkipAcceptance", doc)
        self.assertIn("AKANE_PETDESK_RELEASE_PIPELINE_OK", doc)
        self.assertIn("not an installer", doc)
        self.assertIn("Release Pipeline", guide)
        self.assertIn("release_summary.json", guide)
        self.assertIn("AKANE_PETDESK_RELEASE_PIPELINE_DRY_RUN_OK", guide)
        self.assertIn("release_petdesk_bundle.ps1", readme)
        self.assertIn("release pipeline", readme)


if __name__ == "__main__":
    unittest.main()
