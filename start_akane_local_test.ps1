param(
    [string]$DataRoot = "",
    [int]$BackendPort = 11999,
    [string]$EnvFile = "",
    [string]$PrivateEnvFile = "",
    [switch]$NoBuild,
    [switch]$Rebuild,
    [switch]$Dev,
    [switch]$OpenSettings,
    [switch]$BackendOnly,
    [switch]$PrepareOnly,
    [switch]$SkipCharacterSeed,
    [switch]$SkipPackageSync
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$instanceId = "local-test"

. (Join-Path $projectDir "scripts\akane_data_root.ps1")
. (Join-Path $projectDir "scripts\akane_instance_launcher.ps1")
. (Join-Path $projectDir "scripts\sync_akane_local_packages.ps1")

function New-AkaneLocalTestSatelliteToken {
    $bytes = New-Object byte[] 32
    $random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $random.GetBytes($bytes)
    } finally {
        $random.Dispose()
    }
    return ([BitConverter]::ToString($bytes) -replace "-", "").ToLowerInvariant()
}

if ($BackendPort -lt 1 -or $BackendPort -gt 65535) {
    throw "invalid_local_test_backend_port"
}

$resolvedEnvFile = if ([string]::IsNullOrWhiteSpace($EnvFile)) {
    Join-Path $projectDir ".env"
} elseif ([System.IO.Path]::IsPathRooted($EnvFile)) {
    [System.IO.Path]::GetFullPath($EnvFile)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $projectDir $EnvFile))
}
$resolvedEnvFile = Import-AkaneEnvFile -Path $resolvedEnvFile

$resolvedDataRoot = if ([string]::IsNullOrWhiteSpace($DataRoot)) {
    $localAppData = [Environment]::GetFolderPath("LocalApplicationData")
    if ([string]::IsNullOrWhiteSpace($localAppData)) {
        throw "local_app_data_unavailable"
    }
    Join-Path $localAppData "Akane\local-test-runtime"
} else {
    [System.IO.Path]::GetFullPath($DataRoot)
}

$resolvedPrivateEnvFile = if ([string]::IsNullOrWhiteSpace($PrivateEnvFile)) {
    Join-Path $resolvedDataRoot "config\cloud-aligned.env"
} elseif ([System.IO.Path]::IsPathRooted($PrivateEnvFile)) {
    [System.IO.Path]::GetFullPath($PrivateEnvFile)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $projectDir $PrivateEnvFile))
}
if (Test-Path -LiteralPath $resolvedPrivateEnvFile -PathType Leaf) {
    $null = Import-AkaneEnvFile -Path $resolvedPrivateEnvFile
    Write-Host "[INFO] Cloud-aligned provider profile: loaded from the private local runtime."
} elseif (-not [string]::IsNullOrWhiteSpace($PrivateEnvFile)) {
    throw "private_env_file_not_found"
} else {
    Write-Host "[WARN] Cloud-aligned provider profile is absent; using the project .env as-is."
}

# Import the ordinary local .env first, then freeze the fields which make this
# a local-only profile. Process environment wins over dotenv when the backend
# loads settings, so a cloud instance selector cannot leak into this launch.
$env:AKANE_ENV_FILE = $resolvedEnvFile
$env:AKANE_INSTANCE_ID = $instanceId
$env:AKANE_DATA_ROOT = $resolvedDataRoot
$env:COMPANION_PORT = [string]$BackendPort
$env:COMPANION_HOST = "127.0.0.1"
$env:HOST = "127.0.0.1"
$env:QQ_BRIDGE_ENABLED = "false"
$env:EXECUTION_QQ_ENABLED = "false"
$env:AKANE_ADMIN_TOKEN = New-AkaneLocalTestSatelliteToken
$env:AKANE_DESKTOP_SATELLITE_TOKEN = New-AkaneLocalTestSatelliteToken
Remove-Item Env:\AKANE_BACKEND_URL -ErrorAction SilentlyContinue

$seedCharacters = -not [bool]$SkipCharacterSeed
$dataStatus = Initialize-AkaneDataRoot `
    -ProjectRoot $projectDir `
    -InstanceId $instanceId `
    -DataRoot $resolvedDataRoot `
    -SeedBundledCharacters:$seedCharacters
if ($dataStatus.Failed -gt 0) {
    throw "local_test_character_seed_failed"
}

$bundledCharacter = Join-Path $projectDir "desktop_pet_creator_kit\characters\reimu\character.json"
if (-not $SkipCharacterSeed -and -not (Test-Path -LiteralPath $bundledCharacter -PathType Leaf)) {
    throw "local_test_reimu_pack_missing"
}

$manifestDir = Join-Path $resolvedDataRoot "instances\$instanceId"
$manifestPath = Join-Path $manifestDir "instance.toml"
New-Item -ItemType Directory -Force -Path $manifestDir | Out-Null
$manifest = @"
schema_version = 1
instance_id = "local-test"
character_pack_id = "reimu"
plugins = []

[features]
care = true

[channels.qq]
enabled = false
profile_ref = ""
"@
$manifestTemp = "$manifestPath.$([Guid]::NewGuid().ToString('N')).tmp"
try {
    [System.IO.File]::WriteAllText(
        $manifestTemp,
        $manifest,
        [System.Text.UTF8Encoding]::new($false)
    )
    Move-Item -LiteralPath $manifestTemp -Destination $manifestPath -Force
} finally {
    Remove-Item -LiteralPath $manifestTemp -Force -ErrorAction SilentlyContinue
}

Write-Host "[INFO] Akane pure-local test profile"
Write-Host "[INFO] Instance: local-test (isolated local memory)"
Write-Host "[INFO] Backend: http://127.0.0.1:$BackendPort/"
Write-Host "[INFO] QQ channel: disabled"
Write-Host "[INFO] Model/vision and env-level TTS settings: inherited locally (secret values hidden)"
Write-Host "[INFO] This bypasses the Akane cloud host; remote model providers still require Internet access."

if ($PrepareOnly) {
    Write-Host "[INFO] Local-test profile prepared; backend and desktop were not started."
    exit 0
}

if (-not $SkipPackageSync) {
    $packageSync = Sync-AkaneLocalPackages -ProjectRoot $projectDir
    Write-Host "[INFO] Internal packages: $($packageSync.Status) ($($packageSync.Fingerprint.Substring(0, 12)))"
}

$launcher = Join-Path $projectDir "start_akane_next.ps1"
$launcherArguments = @{
    InstanceId = $instanceId
    DataRoot = $resolvedDataRoot
    BackendPort = $BackendPort
}
if ($NoBuild) { $launcherArguments.NoBuild = $true }
if ($Rebuild) { $launcherArguments.Rebuild = $true }
if ($Dev) { $launcherArguments.Dev = $true }
if ($OpenSettings) { $launcherArguments.OpenSettings = $true }
if ($BackendOnly) { $launcherArguments.SkipDesktop = $true }

& $launcher @launcherArguments
if (-not $?) {
    exit 1
}
exit 0
