$ErrorActionPreference = "Stop"

# Keep the candidate selection scoped to this child process. Ordinary Tauri
# startup still opens the production control-center-lab entry.
$env:AKANE_CONTROL_CENTER_ENTRY = "v2"
$env:AKANE_OPEN_SETTINGS_ON_START = "1"

$tauriLauncher = Join-Path $PSScriptRoot "tauri-with-cargo-path.ps1"
& $tauriLauncher dev
exit $LASTEXITCODE
