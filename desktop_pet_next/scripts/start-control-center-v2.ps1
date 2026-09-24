$ErrorActionPreference = "Stop"

# Compatibility launcher: V2 is now the production control-center-lab entry.
$env:AKANE_OPEN_SETTINGS_ON_START = "1"

$tauriLauncher = Join-Path $PSScriptRoot "tauri-with-cargo-path.ps1"
& $tauriLauncher dev
exit $LASTEXITCODE
