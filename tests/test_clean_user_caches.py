import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "maintenance" / "clean-user-caches.ps1"
CMD = ROOT / "maintenance" / "run-clean-user-caches.cmd"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh.exe")


def _ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


@unittest.skipUnless(POWERSHELL, "PowerShell is required for maintenance script tests")
class CleanUserCachesTests(unittest.TestCase):
    def run_powershell(self, command):
        result = subprocess.run(
            [
                POWERSHELL,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            cwd=ROOT,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"PowerShell failed:\nstdout={result.stdout}\nstderr={result.stderr}",
        )
        return result.stdout.strip()

    def test_script_parses_without_errors(self):
        command = (
            "$tokens = $null; $errors = $null; "
            f"[System.Management.Automation.Language.Parser]::ParseFile({_ps_quote(SCRIPT)}, "
            "[ref]$tokens, [ref]$errors) | Out-Null; "
            "if ($errors.Count -gt 0) { "
            "$errors | ForEach-Object { Write-Error $_.Message }; exit 1 }"
        )
        self.run_powershell(command)

    def test_cmd_uses_script_relative_path_without_fixed_drive(self):
        content = CMD.read_text(encoding="utf-8")
        self.assertIn('"%~dp0clean-user-caches.ps1"', content)
        self.assertIsNone(re.search(r"(?i)\b[A-Z]:\\", content))

    def test_path_guard_rejects_dangerous_paths(self):
        command = f"""
. {_ps_quote(SCRIPT)}
$context = New-CleanupContext
$outside = Join-Path ([Environment]::GetFolderPath('System')) 'AkaneCleanupOutsideProbe'
$allowedProbe = Join-Path $context.Targets[0].Path '__akane_cleanup_probe__'
$checks = [ordered]@{{
    Empty = (Test-CleanupTargetPath -Path '' -AllowedRoots $context.AllowedRoots -UserHome $context.UserHome -RepositoryRoot $context.RepositoryRoot).Reason
    UserHome = (Test-CleanupTargetPath -Path $context.UserHome -AllowedRoots $context.AllowedRoots -UserHome $context.UserHome -RepositoryRoot $context.RepositoryRoot).Reason
    DriveRoot = (Test-CleanupTargetPath -Path ([IO.Path]::GetPathRoot($context.UserHome)) -AllowedRoots $context.AllowedRoots -UserHome $context.UserHome -RepositoryRoot $context.RepositoryRoot).Reason
    RepositoryRoot = (Test-CleanupTargetPath -Path $context.RepositoryRoot -AllowedRoots $context.AllowedRoots -UserHome $context.UserHome -RepositoryRoot $context.RepositoryRoot).Reason
    Outside = (Test-CleanupTargetPath -Path $outside -AllowedRoots $context.AllowedRoots -UserHome $context.UserHome -RepositoryRoot $context.RepositoryRoot).Reason
    Allowed = (Test-CleanupTargetPath -Path $allowedProbe -AllowedRoots $context.AllowedRoots -UserHome $context.UserHome -RepositoryRoot $context.RepositoryRoot).Reason
    AllowedCanDelete = (Test-CleanupTargetPath -Path $allowedProbe -AllowedRoots $context.AllowedRoots -UserHome $context.UserHome -RepositoryRoot $context.RepositoryRoot).CanDelete
}}
$checks | ConvertTo-Json -Compress
"""
        checks = json.loads(self.run_powershell(command))

        self.assertEqual(checks["Empty"], "empty_path")
        self.assertEqual(checks["UserHome"], "user_home")
        self.assertEqual(checks["DriveRoot"], "drive_root")
        self.assertEqual(checks["RepositoryRoot"], "repository_path")
        self.assertEqual(checks["Outside"], "outside_allowed_roots")
        self.assertEqual(checks["Allowed"], "allowed_cache_subpath")
        self.assertTrue(checks["AllowedCanDelete"])

    def test_dry_run_recognizes_allowed_temp_child_without_deleting(self):
        probe = Path(tempfile.mkdtemp(prefix="akane-cleanup-dry-run-"))
        marker = probe / "marker.txt"
        marker.write_text("dry-run", encoding="ascii")
        try:
            command = f"""
. {_ps_quote(SCRIPT)}
$context = New-CleanupContext
$result = Remove-ValidatedChildItems -Label 'test-temp' -Path {_ps_quote(probe)} -AllowedRoots $context.AllowedRoots -UserHome $context.UserHome -RepositoryRoot $context.RepositoryRoot -DryRun -Confirm:$false
[pscustomobject]@{{
    Status = $result.Status
    Reason = $result.Reason
    Planned = $result.Planned
    Removed = $result.Removed
    Exists = Test-Path -LiteralPath {_ps_quote(marker)}
}} | ConvertTo-Json -Compress
"""
            result = json.loads(self.run_powershell(command))
            self.assertEqual(result["Status"], "preview")
            self.assertEqual(result["Reason"], "dry_run")
            self.assertEqual(result["Planned"], 1)
            self.assertEqual(result["Removed"], 0)
            self.assertTrue(result["Exists"])
            self.assertTrue(marker.exists())
        finally:
            if marker.exists():
                marker.unlink()
            if probe.exists():
                probe.rmdir()


if __name__ == "__main__":
    unittest.main()
