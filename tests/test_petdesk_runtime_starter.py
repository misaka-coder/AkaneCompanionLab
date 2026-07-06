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
        self.assertIn("Restore-ScopedEnv", source)

        allowed_keys = {
            "VITE_PETDESK_INTERACTION_PROFILE",
            "VITE_PETDESK_INTERACTION_PROFILE_JSON",
            "VITE_PETDESK_LIVE2D_MODEL_LAYOUT_PROFILE",
            "VITE_PETDESK_LIVE2D_MODEL_LAYOUT_JSON",
            "VITE_PETDESK_LIVE2D_MOTION_MAP_JSON",
            "VITE_PETDESK_LIVE2D_EXPRESSION_MAP_JSON",
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
        self.assertIn("scripts\\start_petdesk_runtime.ps1", petdesk_bat)

    def test_m36_doc_records_transition_boundary(self) -> None:
        doc = (ROOT / "docs" / "petdesk_akane_starter_m36.md").read_text(encoding="utf-8")

        self.assertIn("does not replace the", doc)
        self.assertIn("public `desktop_pet_next` one-click launcher yet", doc)
        self.assertIn("GET /pet/health", doc)
        self.assertIn("whitelist runtimeEnv", doc)
        self.assertIn("fallback to runtime defaults", doc)


if __name__ == "__main__":
    unittest.main()
