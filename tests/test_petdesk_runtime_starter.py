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

        allowed_keys = {
            "VITE_PETDESK_INTERACTION_PROFILE",
            "VITE_PETDESK_INTERACTION_PROFILE_JSON",
            "VITE_PETDESK_LIVE2D_MODEL_LAYOUT_PROFILE",
            "VITE_PETDESK_LIVE2D_MODEL_LAYOUT_JSON",
            "VITE_PETDESK_LIVE2D_MOTION_MAP_JSON",
            "VITE_PETDESK_LIVE2D_EXPRESSION_MAP_JSON",
            "VITE_PETDESK_RESOURCE_MANIFEST_URL",
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


if __name__ == "__main__":
    unittest.main()
