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
    "VISION_API_KEY",
    "VISION_API_PROTOCOL",
    "VISION_BASE_URL",
    "VISION_ENABLED",
    "VISION_MODEL_NAME",
    "CHAT_SUPPORTS_IMAGES",
    "EXECUTION_ENABLED",
    "MEMORY_BACKEND",
    "MEMCORE_OPERATION_PROJECTION_POLICY"
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
    "VISION_API_KEY", "VISION_API_PROTOCOL", "VISION_BASE_URL", "VISION_ENABLED", "VISION_MODEL_NAME",
    "EXECUTION_ENABLED", "MEMORY_BACKEND"
)
foreach ($name in $required) {
    $property = $payload.PSObject.Properties[$name]
    if ($null -eq $property -or [string]::IsNullOrWhiteSpace([string]$property.Value)) {
        throw "cloud_runtime_profile_field_missing:$name"
    }
}

$deepSeekApiKey = [Environment]::GetEnvironmentVariable("DEEPSEEK_API_KEY", "Process")
if ([string]::IsNullOrWhiteSpace($deepSeekApiKey)) {
    $deepSeekApiKey = [Environment]::GetEnvironmentVariable("DEEPSEEK_API_KEY", "User")
}
if ([string]::IsNullOrWhiteSpace($deepSeekApiKey)) {
    throw "local_deepseek_api_key_missing"
}

$lines = New-Object System.Collections.Generic.List[string]
foreach ($name in @("CHAT", "TEXT", "AUX")) {
    $lines.Add("${name}_API_KEY=$deepSeekApiKey")
    $lines.Add("${name}_API_PROTOCOL=openai")
    $lines.Add("${name}_BASE_URL=https://api.deepseek.com/v1")
    $lines.Add("${name}_MODEL_NAME=deepseek-v4-flash")
}
$lines.Add("CHAT_SUPPORTS_IMAGES=false")

foreach ($name in $allowlist) {
    if ($name -eq "CHAT_SUPPORTS_IMAGES" -or $name -eq "MEMCORE_OPERATION_PROJECTION_POLICY") {
        continue
    }
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
$lines.Add("MEMCORE_OPERATION_PROJECTION_POLICY=compact_after_terminal")

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
Write-Host "[INFO] Chat/text/aux model: deepseek-v4-flash"
Write-Host "[INFO] Vision model: $([string]$payload.VISION_MODEL_NAME)"
Write-Host "[INFO] Shell execution: $([string]$payload.EXECUTION_ENABLED)"
Write-Host "[INFO] MemCore tool-result settlement: compact_after_terminal"
Write-Host "[INFO] Provider secrets, QQ credentials, paths, logs and memory were not printed or copied."
