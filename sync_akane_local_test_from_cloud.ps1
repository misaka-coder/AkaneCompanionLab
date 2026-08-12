param(
    [string]$SshHost = "akane-vps",
    [string]$ServiceName = "akane-host.service",
    [string]$DataRoot = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$resolvedDataRoot = if ([string]::IsNullOrWhiteSpace($DataRoot)) {
    $localAppData = [Environment]::GetFolderPath("LocalApplicationData")
    if ([string]::IsNullOrWhiteSpace($localAppData)) {
        throw "local_app_data_unavailable"
    }
    Join-Path $localAppData "Akane\local-test-runtime"
} else {
    [System.IO.Path]::GetFullPath($DataRoot)
}

$ssh = Get-Command ssh -ErrorAction SilentlyContinue
if ($null -eq $ssh) {
    throw "ssh_not_found"
}
if ($ServiceName -notmatch '^[A-Za-z0-9_.@-]+$') {
    throw "invalid_cloud_service_name"
}

$allowlist = @(
    "CHAT_API_KEY",
    "CHAT_API_PROTOCOL",
    "CHAT_BASE_URL",
    "CHAT_MODEL_NAME",
    "TEXT_API_KEY",
    "TEXT_API_PROTOCOL",
    "TEXT_BASE_URL",
    "TEXT_MODEL_NAME",
    "VISION_API_KEY",
    "VISION_API_PROTOCOL",
    "VISION_BASE_URL",
    "VISION_ENABLED",
    "VISION_MODEL_NAME",
    "CHAT_SUPPORTS_IMAGES",
    "EXECUTION_ENABLED",
    "MEMORY_BACKEND"
)
$allowlistJson = $allowlist | ConvertTo-Json -Compress
$remoteScript = @"
set -eu
pid=`$(systemctl show '$ServiceName' -p MainPID --value)
test -n "`$pid"
sudo -n python3 - "`$pid" '$allowlistJson' <<'PY'
import base64
import json
import pathlib
import sys

pid = sys.argv[1]
allowed = set(json.loads(sys.argv[2]))
raw = pathlib.Path(f"/proc/{pid}/environ").read_bytes()
payload = {}
for entry in raw.split(b"\0"):
    if b"=" not in entry:
        continue
    key_bytes, value = entry.split(b"=", 1)
    key = key_bytes.decode("ascii", "ignore")
    if key in allowed:
        payload[key] = value.decode("utf-8")
print(base64.b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii"))
PY
"@ -replace "`r", ""

$encodedScript = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($remoteScript))
$encodedPayload = @(
    & $ssh.Source `
        -o BatchMode=yes `
        -o ConnectTimeout=8 `
        $SshHost `
        "echo $encodedScript | base64 -d | bash" 2>$null
) | Select-Object -Last 1
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace([string]$encodedPayload)) {
    throw "cloud_runtime_profile_read_failed"
}

try {
    $json = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String(([string]$encodedPayload).Trim()))
    $payload = $json | ConvertFrom-Json
} catch {
    throw "cloud_runtime_profile_invalid"
}

$required = @(
    "CHAT_API_KEY", "CHAT_API_PROTOCOL", "CHAT_BASE_URL", "CHAT_MODEL_NAME",
    "TEXT_API_KEY", "TEXT_API_PROTOCOL", "TEXT_BASE_URL", "TEXT_MODEL_NAME",
    "VISION_API_KEY", "VISION_API_PROTOCOL", "VISION_BASE_URL", "VISION_ENABLED", "VISION_MODEL_NAME",
    "EXECUTION_ENABLED", "MEMORY_BACKEND"
)
foreach ($name in $required) {
    $property = $payload.PSObject.Properties[$name]
    if ($null -eq $property -or [string]::IsNullOrWhiteSpace([string]$property.Value)) {
        throw "cloud_runtime_profile_field_missing:$name"
    }
}

$lines = New-Object System.Collections.Generic.List[string]
foreach ($name in $allowlist) {
    $property = $payload.PSObject.Properties[$name]
    if ($null -eq $property) {
        continue
    }
    $value = [string]$property.Value
    if ($value.Contains("`r") -or $value.Contains("`n")) {
        throw "cloud_runtime_profile_multiline_value:$name"
    }
    $lines.Add("$name=$value")
}

$targetDir = Join-Path $resolvedDataRoot "config"
$target = Join-Path $targetDir "cloud-aligned.env"
New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
$temp = "$target.$([Guid]::NewGuid().ToString('N')).tmp"
try {
    [System.IO.File]::WriteAllText(
        $temp,
        (($lines -join "`n") + "`n"),
        [System.Text.UTF8Encoding]::new($false)
    )
    Move-Item -LiteralPath $temp -Destination $target -Force
} finally {
    Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue
}

# Best effort: the file lives under the user's local app-data already; remove
# inherited broad ACLs when icacls is available. Never print secret values.
$icacls = Get-Command icacls -ErrorAction SilentlyContinue
if ($null -ne $icacls -and -not [string]::IsNullOrWhiteSpace([string]$env:USERNAME)) {
    & $icacls.Source $target /inheritance:r /grant:r "$($env:USERNAME):(R,W)" *> $null
}

Write-Host "[OK] Cloud-aligned local-test provider profile saved."
Write-Host "[INFO] Chat model: $([string]$payload.CHAT_MODEL_NAME)"
Write-Host "[INFO] Vision model: $([string]$payload.VISION_MODEL_NAME)"
Write-Host "[INFO] Shell execution: $([string]$payload.EXECUTION_ENABLED)"
Write-Host "[INFO] Secrets, QQ credentials, paths, logs and memory were not printed or copied."
