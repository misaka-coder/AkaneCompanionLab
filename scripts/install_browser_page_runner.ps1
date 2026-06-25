param(
    [string]$Python = "",
    [switch]$SkipBrowserInstall
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not $Python) {
    $VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path $VenvPython) {
        $Python = $VenvPython
    } else {
        $Python = "python"
    }
}

Write-Host "Using Python: $Python"
& $Python -m pip install playwright

if (-not $SkipBrowserInstall) {
    & $Python -m playwright install chromium
}

& $Python (Join-Path $RepoRoot "scripts\probe_browser_page.py") --require-ready
