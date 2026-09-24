param(
    [string]$BotQQ = "3034389702",
    [string]$DataRoot = "",
    [int]$BackendPort = 12001,
    [int]$OneBotPort = 3003,
    [string]$EnvFile = "",
    [string]$PrivateEnvFile = "",
    [string]$NapCatRoot = "",
    [switch]$SkipPackageSync,
    [switch]$SkipDesktop,
    [switch]$OpenQQSetup,
    [switch]$ReuseBackend,
    [switch]$NoBuild,
    [switch]$Dev
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw "powershell_7_required: launch with pwsh"
}

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$instanceId = "local-qq-test"
$profileRef = "local-qq-test"

. (Join-Path $projectDir "scripts\akane_data_root.ps1")
. (Join-Path $projectDir "scripts\akane_instance_launcher.ps1")
. (Join-Path $projectDir "scripts\sync_akane_local_packages.ps1")

function New-AkaneLocalQQToken {
    $bytes = New-Object byte[] 32
    $random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $random.GetBytes($bytes)
    } finally {
        $random.Dispose()
    }
    return ([BitConverter]::ToString($bytes) -replace "-", "").ToLowerInvariant()
}

function Write-AkaneUtf8Atomic {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Content
    )

    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $temporary = "$Path.$([Guid]::NewGuid().ToString('N')).tmp"
    try {
        [System.IO.File]::WriteAllText($temporary, $Content, [System.Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $temporary -Destination $Path -Force
    } finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}

function Set-AkaneNapCatOneBotConfig {
    param(
        [Parameter(Mandatory = $true)][string]$ConfigPath,
        [Parameter(Mandatory = $true)][int]$ApiPort,
        [Parameter(Mandatory = $true)][int]$HostPort,
        [Parameter(Mandatory = $true)][string]$AccessToken,
        [Parameter(Mandatory = $true)][string]$WebhookSecret
    )

    if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
        throw "napcat_onebot_config_not_found"
    }
    $config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
    if ($null -eq $config.network) {
        throw "napcat_network_config_missing"
    }

    $httpServers = @($config.network.httpServers | Where-Object { $_.name -ne "akane-local-qq-api" })
    $httpServers += [pscustomobject]@{
        name = "akane-local-qq-api"
        enable = $true
        port = $ApiPort
        host = "127.0.0.1"
        enableCors = $false
        enableWebsocket = $false
        messagePostFormat = "array"
        token = $AccessToken
        debug = $false
    }
    $config.network.httpServers = $httpServers

    $httpClients = @($config.network.httpClients | Where-Object { $_.name -ne "akane-local-qq-webhook" })
    $httpClients += [pscustomobject]@{
        name = "akane-local-qq-webhook"
        enable = $true
        url = "http://127.0.0.1:$HostPort/api/qq/napcat/event"
        messagePostFormat = "array"
        reportSelfMessage = $false
        token = $WebhookSecret
        debug = $false
    }
    $config.network.httpClients = $httpClients

    # Ask the authenticated OneBot get_file/get_image APIs for bytes as well
    # as paths. QQ caches may live outside Akane (or behind fake-IP DNS);
    # receiving a QQ attachment must not require sharing those directories.
    $config | Add-Member -NotePropertyName enableLocalFile2Url -NotePropertyValue $true -Force

    Write-AkaneUtf8Atomic -Path $ConfigPath -Content ($config | ConvertTo-Json -Depth 16)
}

if ($BotQQ -notmatch "^[0-9]{5,20}$") {
    throw "invalid_local_qq_bot_id"
}
if ($BackendPort -lt 1 -or $BackendPort -gt 65535 -or $OneBotPort -lt 1 -or $OneBotPort -gt 65535) {
    throw "invalid_local_qq_port"
}
if ($BackendPort -eq $OneBotPort) {
    throw "local_qq_ports_must_differ"
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
    Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "Akane\local-qq-test-runtime"
} else {
    [System.IO.Path]::GetFullPath($DataRoot)
}

$resolvedPrivateEnvFile = if ([string]::IsNullOrWhiteSpace($PrivateEnvFile)) {
    Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "Akane\local-test-runtime\config\cloud-aligned.env"
} elseif ([System.IO.Path]::IsPathRooted($PrivateEnvFile)) {
    [System.IO.Path]::GetFullPath($PrivateEnvFile)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $projectDir $PrivateEnvFile))
}
if (-not (Test-Path -LiteralPath $resolvedPrivateEnvFile -PathType Leaf)) {
    throw "local_qq_cloud_aligned_provider_profile_missing"
}
$null = Import-AkaneEnvFile -Path $resolvedPrivateEnvFile

$secretEnvFile = Join-Path $resolvedDataRoot "config\local-qq-secrets.env"
if (-not (Test-Path -LiteralPath $secretEnvFile -PathType Leaf)) {
    $secretPayload = @(
        "AKANE_ADMIN_TOKEN=$(New-AkaneLocalQQToken)"
        "AKANE_DESKTOP_SATELLITE_TOKEN=$(New-AkaneLocalQQToken)"
        "QQ_WEBHOOK_SECRET=$(New-AkaneLocalQQToken)"
        "QQ_ONEBOT_ACCESS_TOKEN=$(New-AkaneLocalQQToken)"
    ) -join "`n"
    Write-AkaneUtf8Atomic -Path $secretEnvFile -Content ($secretPayload + "`n")
}
$null = Import-AkaneEnvFile -Path $secretEnvFile

$env:AKANE_ENV_FILE = $resolvedEnvFile
$env:AKANE_INSTANCE_ID = $instanceId
$env:AKANE_DATA_ROOT = $resolvedDataRoot
$env:COMPANION_PORT = [string]$BackendPort
$env:COMPANION_HOST = "127.0.0.1"
$env:HOST = "127.0.0.1"
$env:QQ_BRIDGE_ENABLED = "true"
$env:EXECUTION_QQ_ENABLED = "true"
$env:BROWSER_PAGE_PRIVATE_NETWORK_ACCESS = "true"
$env:QQ_CHANNEL_PROFILE_REF = $profileRef
$env:QQ_BOT_QQ = $BotQQ
$env:QQ_ONEBOT_HTTP_URL = "http://127.0.0.1:$OneBotPort"
$env:QQ_CHARACTER_PACK_ID = "reimu"
Remove-Item Env:\AKANE_BACKEND_URL -ErrorAction SilentlyContinue

$dataStatus = Initialize-AkaneDataRoot `
    -ProjectRoot $projectDir `
    -InstanceId $instanceId `
    -DataRoot $resolvedDataRoot `
    -SeedBundledCharacters:$true
if ($dataStatus.Failed -gt 0) {
    throw "local_qq_character_seed_failed"
}

$manifestPath = Join-Path $resolvedDataRoot "instances\$instanceId\instance.toml"
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    $manifest = @"
schema_version = 1
instance_id = "$instanceId"
character_pack_id = "reimu"
plugins = []

[features]
care = true

[channels.qq]
enabled = true
profile_ref = "$profileRef"
"@
    Write-AkaneUtf8Atomic -Path $manifestPath -Content $manifest
} else {
    Write-Host "[INFO] Existing local QQ instance manifest preserved."
}

if (-not $SkipPackageSync) {
    $packageSync = Sync-AkaneLocalPackages -ProjectRoot $projectDir
    Write-Host "[INFO] Internal packages: $($packageSync.Status) ($($packageSync.Fingerprint.Substring(0, 12)))"
}

$localPython = Join-Path $projectDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $localPython -PathType Leaf)) {
    throw "local_qq_python_not_found"
}
if (-not (Test-AkaneLocalPackageContracts -PythonPath $localPython)) {
    throw "local_package_contract_validation_failed: restart without -SkipPackageSync"
}
if ($SkipPackageSync -and -not (Test-AkaneLocalPackagesCurrent -ProjectRoot $projectDir -PythonPath $localPython)) {
    throw "local_package_content_mismatch: restart without -SkipPackageSync"
}
$capabilitySeeder = Join-Path $projectDir "scripts\seed_akane_local_capabilities.py"
$sharedLocalUsersDataRoot = Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "Akane\users_data"
& $localPython $capabilitySeeder `
    --source-users-data-root $sharedLocalUsersDataRoot `
    --destination-users-data-root (Join-Path $resolvedDataRoot "users_data") `
    --profile-user-id "master" | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "local_qq_capability_seed_failed"
}
$policyInitializer = Join-Path $projectDir "scripts\initialize_akane_local_test_policy.py"
& $localPython $policyInitializer `
    --users-data-root (Join-Path $resolvedDataRoot "users_data") `
    --profile-user-id "master" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "local_qq_approval_policy_initialization_failed"
}

$resolvedNapCatRoot = if ([string]::IsNullOrWhiteSpace($NapCatRoot)) {
    $candidate28 = Join-Path $resolvedDataRoot "napcat\shell-v4.18.28"
    $candidate19 = Join-Path $resolvedDataRoot "napcat\shell-v4.18.19"
    if (Test-Path -LiteralPath $candidate28) {
        $candidate28
    } else {
        $candidate19
    }
} else {
    [System.IO.Path]::GetFullPath($NapCatRoot)
}
$oneBotConfig = Join-Path $resolvedNapCatRoot "config\onebot11_$BotQQ.json"
$env:AKANE_NAPCAT_ROOT = $resolvedNapCatRoot
if ($OpenQQSetup) {
    $env:AKANE_OPEN_SETTINGS_ON_START = "true"
    $env:AKANE_OPEN_QQ_SETUP = "true"
}
Set-AkaneNapCatOneBotConfig `
    -ConfigPath $oneBotConfig `
    -ApiPort $OneBotPort `
    -HostPort $BackendPort `
    -AccessToken ([string]$env:QQ_ONEBOT_ACCESS_TOKEN) `
    -WebhookSecret ([string]$env:QQ_WEBHOOK_SECRET)

Write-Host "[INFO] Akane Windows local QQ test profile"
Write-Host "[INFO] Instance: $instanceId"
Write-Host "[INFO] Bot QQ: $BotQQ"
Write-Host "[INFO] Backend: http://127.0.0.1:$BackendPort/"
Write-Host "[INFO] OneBot API: http://127.0.0.1:$OneBotPort/"
Write-Host "[INFO] Provider profile: cloud-aligned (secret values hidden)"
Write-Host "[INFO] Execution host: Windows local"
Write-Host "[INFO] Active QQ character library: $($dataStatus.Characters)"
Write-Host "[INFO] Creator-kit characters are source packs, not this instance's live library."
Write-Host "[INFO] New packs: import into this instance via Character Workshop; existing libraries are not auto-merged."
Write-Host "[INFO] NapCat must be restarted after its OneBot config changes."

$launcher = Join-Path $projectDir "start_akane_next.ps1"
$launcherArgs = @{
    InstanceId = $instanceId
    DataRoot = $resolvedDataRoot
    BackendPort = $BackendPort
}
if ($SkipDesktop) {
    $launcherArgs.SkipDesktop = $true
    $launcherArgs.DeviceOnly = $true
}
if ($ReuseBackend) { $launcherArgs.ReuseBackend = $true }
if ($NoBuild) { $launcherArgs.NoBuild = $true }
if ($Dev) { $launcherArgs.Dev = $true }
& $launcher @launcherArgs
exit $LASTEXITCODE
