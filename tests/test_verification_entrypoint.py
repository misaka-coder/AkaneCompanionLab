from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
VERIFY_SCRIPT = ROOT / "scripts" / "verify_akane.ps1"


def _powershell_executable() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


def _run_config_probe(*, cwd: Path, env: dict[str, str], code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )


class VerificationEntrypointTests(unittest.TestCase):
    def test_quick_regression_manifest_has_no_stale_test_names(self) -> None:
        from tests.quick_regression_suite import QUICK_TESTS

        loader = unittest.TestLoader()
        for test_name in QUICK_TESTS:
            suite = loader.loadTestsFromName(test_name)
            self.assertGreater(suite.countTestCases(), 0, test_name)
        self.assertEqual(loader.errors, [])

    def test_quick_check_only_returns_structured_bounded_plan(self) -> None:
        powershell = _powershell_executable()
        if not powershell:
            self.skipTest("PowerShell is unavailable")

        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(VERIFY_SCRIPT),
                "-Tier",
                "Quick",
                "-CheckOnly",
                "-Json",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout.strip())
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["tier"], "Quick")
        self.assertTrue(payload["check_only"])
        stages = payload["stages"]
        self.assertEqual(
            [stage["name"] for stage in stages],
            ["python-lint", "python-quick-regression", "diff-hygiene"],
        )
        self.assertTrue(all(stage["status"] == "planned" for stage in stages))
        self.assertTrue(all(stage["timeout_seconds"] > 0 for stage in stages))

    def test_preflight_failure_is_structured_and_does_not_run_stages(self) -> None:
        powershell = _powershell_executable()
        if not powershell:
            self.skipTest("PowerShell is unavailable")

        env = dict(os.environ)
        env["PATH"] = ""
        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(VERIFY_SCRIPT),
                "-Tier",
                "Quick",
                "-CheckOnly",
                "-Json",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stdout.strip())
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["reason"], "preflight_failed")
        self.assertEqual(payload["stages"], [])
        self.assertTrue(any(item.startswith("git:") for item in payload["missing"]))

    def test_explicit_env_file_is_used_on_initial_load_and_reload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            cwd_env = temp_root / ".env"
            first_env = temp_root / "first.env"
            second_env = temp_root / "second.env"
            cwd_env.write_text("RUN_MODE=CWD_FILE\n", encoding="utf-8")
            first_env.write_text("RUN_MODE=FIRST_BOUND_FILE\n", encoding="utf-8")
            second_env.write_text("RUN_MODE=SECOND_BOUND_FILE\n", encoding="utf-8")

            env = dict(os.environ)
            env["PYTHONPATH"] = str(ROOT)
            env["AKANE_DATA_ROOT"] = str(temp_root / "data")
            env["AKANE_ENV_FILE"] = str(first_env)
            env["SECOND_AKANE_ENV_FILE"] = str(second_env)
            result = _run_config_probe(
                cwd=temp_root,
                env=env,
                code=(
                    "import os; import config; "
                    "print(config.settings.RUN_MODE); "
                    "os.environ['AKANE_ENV_FILE'] = os.environ['SECOND_AKANE_ENV_FILE']; "
                    "config.reload_settings(); print(config.settings.RUN_MODE)"
                ),
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["FIRST_BOUND_FILE", "SECOND_BOUND_FILE"])

    def test_local_default_still_reads_working_directory_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            (temp_root / ".env").write_text("RUN_MODE=LOCAL_DEFAULT_FILE\n", encoding="utf-8")
            env = dict(os.environ)
            env.pop("AKANE_ENV_FILE", None)
            env["PYTHONPATH"] = str(ROOT)
            env["AKANE_DATA_ROOT"] = str(temp_root / "data")
            result = _run_config_probe(
                cwd=temp_root,
                env=env,
                code="import config; print(config.settings.RUN_MODE)",
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "LOCAL_DEFAULT_FILE")

    def test_ci_and_legacy_quick_entry_delegate_to_canonical_script(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        quick_entry = (ROOT / "run_quick_regression.ps1").read_text(encoding="utf-8")
        verification = VERIFY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("scripts/verify_akane.ps1 -Tier Full", workflow)
        self.assertIn("scripts\\verify_akane.ps1", quick_entry)
        self.assertIn("-Tier Quick", quick_entry)
        self.assertIn('ValidateSet("Quick", "Full", "Acceptance")', verification)
        self.assertIn('Name "m65-e5-two-instance"', verification)


if __name__ == "__main__":
    unittest.main()
