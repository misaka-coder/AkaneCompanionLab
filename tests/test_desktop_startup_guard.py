from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

RUNNER = r'''
param([string]$Launcher, [string]$DataRoot, [string]$Scenario)
$ErrorActionPreference = "Stop"
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Launcher, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw "launcher_parse_failed" }
$ast.FindAll({ param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -in @("Wait-AkaneDesktopReady", "Wait-AkaneDesktopReadyState")
}, $false) | ForEach-Object { Invoke-Expression $_.Extent.Text }
$childArgs = @("-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "Start-Sleep -Seconds 30")
$child = Start-Process -FilePath (Join-Path $PSHOME "pwsh.exe") -ArgumentList $childArgs -WindowStyle Hidden -PassThru
$peer = Start-Process -FilePath (Join-Path $PSHOME "pwsh.exe") -ArgumentList $childArgs -WindowStyle Hidden -PassThru
try {
    $runDir = Join-Path $DataRoot "run"
    New-Item -ItemType Directory -Path $runDir -Force | Out-Null
    $marker = Join-Path $runDir "desktop-ui-state.json"
    $state = @{ status = "ready"; instance_id = "test-instance"; process_id = $child.Id; phase = "ui_ready"; reason = "" }
    switch ($Scenario) {
        "failed" { $state.status = "failed"; $state.phase = "ui_initialization_failed"; $state.reason = "synthetic renderer failure" }
        "wrong-instance" { $state.instance_id = "other-instance" }
        "wrong-process" { $state.process_id = $peer.Id }
        "exited" { Stop-Process -Id $child.Id -Force; $child.WaitForExit() }
    }
    if ($Scenario -eq "invalid-json") {
        Set-Content -LiteralPath $marker -Value "{" -Encoding utf8
    } elseif ($Scenario -notin @("missing", "exited")) {
        $state | ConvertTo-Json | Set-Content -LiteralPath $marker -Encoding utf8
    }
    $ready = $false
    $reason = ""
    try {
        $ready = Wait-AkaneDesktopReady -DataRoot $DataRoot -ExpectedInstanceId "test-instance" -ProcessId $child.Id -TimeoutSeconds 1
    } catch { $reason = $_.Exception.Message }
    $child.Refresh()
    if ($Scenario -ne "ready") { $child.WaitForExit(5000) | Out-Null }
    $peer.Refresh()
    @{ ready = $ready; reason = $reason; child_alive = -not $child.HasExited; peer_alive = -not $peer.HasExited } | ConvertTo-Json -Compress
} finally {
    foreach ($owned in @($child, $peer)) {
        $owned.Refresh()
        if (-not $owned.HasExited) { Stop-Process -Id $owned.Id -Force -ErrorAction SilentlyContinue }
        $owned.Dispose()
    }
}
'''


@unittest.skipUnless(shutil.which("pwsh"), "PowerShell 7 is unavailable")
class DesktopStartupGuardTests(unittest.TestCase):
    def check_scenario(self, scenario: str) -> dict:
        with tempfile.TemporaryDirectory(prefix="akane-ui-guard-") as temporary:
            folder = Path(temporary)
            runner = folder / "run.ps1"
            runner.write_text(RUNNER, encoding="utf-8")
            result = subprocess.run(
                [shutil.which("pwsh"), "-NoLogo", "-NoProfile", "-File", str(runner),
                 "-Launcher", str(ROOT / "start_akane_next.ps1"),
                 "-DataRoot", str(folder), "-Scenario", scenario],
                capture_output=True, text=True, encoding="utf-8", timeout=20, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout.splitlines()[-1])
            self.assertTrue(report["peer_alive"], "Startup cleanup stopped an unrelated process")
            return report

    def test_ready_window_stays_alive(self) -> None:
        report = self.check_scenario("ready")
        self.assertTrue(report["ready"])
        self.assertTrue(report["child_alive"])
        self.assertEqual(report["reason"], "")

    def test_failed_window_stops_and_retains_reason(self) -> None:
        report = self.check_scenario("failed")
        self.assertFalse(report["ready"])
        self.assertFalse(report["child_alive"])
        self.assertIn("ui_initialization_failed: synthetic renderer failure", report["reason"])

    def test_unready_or_stale_markers_do_not_leave_a_process(self) -> None:
        for scenario in ("missing", "invalid-json", "wrong-instance", "wrong-process"):
            with self.subTest(scenario=scenario):
                report = self.check_scenario(scenario)
                self.assertFalse(report["ready"])
                self.assertFalse(report["child_alive"])
                self.assertIn("did not report ui_ready", report["reason"])

    def test_already_exited_window_preserves_exit_error(self) -> None:
        report = self.check_scenario("exited")
        self.assertFalse(report["ready"])
        self.assertFalse(report["child_alive"])
        self.assertIn("process exited", report["reason"])


if __name__ == "__main__":
    unittest.main()
