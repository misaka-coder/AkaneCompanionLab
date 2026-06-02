param(
  [switch]$BuildIfMissing,
  [switch]$Doctor,
  [switch]$LegacySettings
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$ExePath = Join-Path $Root "src-tauri\target\release\akane_desktop_pet_next.exe"

Set-Location $Root

if (-not $LegacySettings) {
  $env:AKANE_CONTROL_CENTER_LAB = "1"
}

if ($Doctor) {
  npm run doctor
  if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
  }
}

if (-not (Test-Path -LiteralPath $ExePath)) {
  if (-not $BuildIfMissing) {
    Write-Host "未找到 release exe：$ExePath"
    Write-Host "请先运行：npm run tauri -- build"
    Write-Host "也可以运行：powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-next.ps1 -BuildIfMissing"
    exit 1
  }

  npm run tauri -- build
  if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
  }
}

$process = Start-Process -FilePath $ExePath -WorkingDirectory $Root -PassThru
Write-Host "Akane Next 已启动。PID: $($process.Id)"
Write-Host $ExePath
