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


if __name__ == "__main__":
    unittest.main()
