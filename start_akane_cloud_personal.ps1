param(
    [string]$SshHost = "akane-vps",
    [int]$LocalPort = 11001,
    [int]$RemotePort = 10001,
    [int]$GptSoVitsLocalPort = 9880,
    [int]$GptSoVitsRemotePort = 19880,
    [int]$LocalMediaPort = 9879,
    [int]$LocalMediaRemotePort = 19879,
    [string]$InstanceId = "personal",
    [string]$DataRoot = "",
    [switch]$NoBuild,
    [switch]$Rebuild,
    [switch]$OpenSettings,
    [switch]$SkipDesktop,
    [switch]$SkipOfferCheck,
    [switch]$SkipLocalMedia
)

$ErrorActionPreference = "Stop"

function Test-AkaneSafeInstanceId {
    param([string]$Value)
    return [bool]($Value -match '^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')
}

function Get-AkaneCloudHealth {
    param([string]$BaseUrl)
    try {
        return Invoke-RestMethod -Uri ($BaseUrl.TrimEnd("/") + "/health") -TimeoutSec 3
    } catch {
        return $null
    }
}

function Test-AkaneCloudHealth {
    param(
        [object]$Health,
        [string]$ExpectedInstanceId
    )
    return (
        $null -ne $Health -and
        [string]$Health.status -eq "ok" -and
        [string]$Health.instance_id -eq $ExpectedInstanceId -and
        [string]$Health.root_binding -eq "valid"
    )
}

function Test-AkaneTcpPort {
    param(
        [string]$HostName,
        [int]$Port
    )
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $connect = $client.ConnectAsync($HostName, $Port)
        if (-not $connect.Wait(500)) {
            return $false
        }
        return $client.Connected
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Import-AkanePersonalSatelliteToken {
    param([string]$ExpectedInstanceId)
    $suffix = ($ExpectedInstanceId.ToUpperInvariant() -replace '[^A-Z0-9_]', '_')
    $specificName = "AKANE_DESKTOP_SATELLITE_TOKEN_$suffix"
    foreach ($candidate in @(
        @{ Name = $specificName; Scope = "Process" },
        @{ Name = $specificName; Scope = "User" },
        @{ Name = "AKANE_DESKTOP_SATELLITE_TOKEN"; Scope = "Process" },
        @{ Name = "AKANE_DESKTOP_SATELLITE_TOKEN"; Scope = "User" }
    )) {
        $stored = [Environment]::GetEnvironmentVariable($candidate.Name, $candidate.Scope)
        if (-not [string]::IsNullOrWhiteSpace([string]$stored)) {
            $env:AKANE_DESKTOP_SATELLITE_TOKEN = ([string]$stored).Trim()
            return "$($candidate.Name) [$($candidate.Scope)]"
        }
    }
    throw "cloud_satellite_token_required: set the instance-specific user environment token first"
}

function Start-AkaneSshTunnel {
    param(
        [string]$Target,
        [int]$BindPort,
        [int]$TargetPort,
        [string]$LogDirectory
    )
    $ssh = Get-Command ssh -ErrorAction SilentlyContinue
    if ($null -eq $ssh) {
        throw "ssh_not_found"
    }
    New-Item -ItemType Directory -Force -Path $LogDirectory | Out-Null
    $stdoutLog = Join-Path $LogDirectory "cloud_satellite_tunnel.log"
    $stderrLog = Join-Path $LogDirectory "cloud_satellite_tunnel.err.log"
    $forward = "127.0.0.1:{0}:127.0.0.1:{1}" -f $BindPort, $TargetPort
    return Start-Process `
        -FilePath $ssh.Source `
        -ArgumentList @(
            "-N",
            "-T",
            "-o", "BatchMode=yes",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
            "-L", $forward,
            $Target
        ) `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog `
        -PassThru
}

function Start-AkaneGptSoVitsReverseTunnel {
    param(
        [string]$Target,
        [int]$LocalProviderPort,
        [int]$RemoteProviderPort,
        [string]$LogDirectory
    )
    $ssh = Get-Command ssh -ErrorAction SilentlyContinue
    if ($null -eq $ssh) {
        throw "ssh_not_found"
    }
    New-Item -ItemType Directory -Force -Path $LogDirectory | Out-Null
    $stdoutLog = Join-Path $LogDirectory "gpt_sovits_reverse_tunnel.log"
    $stderrLog = Join-Path $LogDirectory "gpt_sovits_reverse_tunnel.err.log"
    $reverseForward = "127.0.0.1:{0}:127.0.0.1:{1}" -f $RemoteProviderPort, $LocalProviderPort
    return Start-Process `
        -FilePath $ssh.Source `
        -ArgumentList @(
            "-N",
            "-T",
            "-o", "BatchMode=yes",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
            "-R", $reverseForward,
            $Target
        ) `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog `
        -PassThru
}

function Wait-AkaneCloudHealth {
    param(
        [string]$BaseUrl,
        [string]$ExpectedInstanceId,
        [int]$Attempts = 24
    )
    for ($attempt = 0; $attempt -lt $Attempts; $attempt += 1) {
        $health = Get-AkaneCloudHealth -BaseUrl $BaseUrl
        if (Test-AkaneCloudHealth -Health $health -ExpectedInstanceId $ExpectedInstanceId) {
            return $health
        }
        Start-Sleep -Milliseconds 250
    }
    return $null
}

function Wait-AkaneSatelliteOffers {
    param(
        [string]$BaseUrl,
        [bool]$IncludeLocalMedia = $true,
        [int]$Attempts = 40
    )
    $expected = @(
        "tool.open_browser",
        "tool.desktop_context_snapshot",
        "tool.system_media_snapshot",
        "tool.system_media_control"
    )
    if ($IncludeLocalMedia) {
        $expected += @(
            "provider.asr.local_media_executor",
            "provider.voice_conversion.rvc.local_executor",
            "prompt_module.cover_song"
        )
    }
    for ($attempt = 0; $attempt -lt $Attempts; $attempt += 1) {
        try {
            $catalog = Invoke-RestMethod -Uri ($BaseUrl.TrimEnd("/") + "/capabilities") -TimeoutSec 3
            $states = @{}
            foreach ($capability in @($catalog.capabilities)) {
                $states[[string]$capability.id] = [string]$capability.status
            }
            $ready = @($expected | Where-Object { $states[$_] -eq "ready" })
            if ($ready.Count -eq $expected.Count) {
                return $true
            }
        } catch {
            # The desktop process and its WSS registration can still be warming up.
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

$projectRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$mainLauncher = Join-Path $projectRoot "start_akane_next.ps1"
if (-not (Test-Path -LiteralPath $mainLauncher -PathType Leaf)) {
    throw "main_launcher_missing"
}
if (-not (Test-AkaneSafeInstanceId -Value $InstanceId)) {
    throw "invalid_instance_id"
}
if (
    $LocalPort -lt 1 -or $LocalPort -gt 65535 -or
    $RemotePort -lt 1 -or $RemotePort -gt 65535 -or
    $GptSoVitsLocalPort -lt 1 -or $GptSoVitsLocalPort -gt 65535 -or
    $GptSoVitsRemotePort -lt 1 -or $GptSoVitsRemotePort -gt 65535 -or
    $LocalMediaPort -lt 1 -or $LocalMediaPort -gt 65535 -or
    $LocalMediaRemotePort -lt 1 -or $LocalMediaRemotePort -gt 65535
) {
    throw "invalid_tunnel_port"
}

$resolvedDataRoot = if ($DataRoot.Trim()) {
    [System.IO.Path]::GetFullPath($DataRoot.Trim())
} else {
    $localBase = [string]$env:LOCALAPPDATA
    if (-not $localBase.Trim()) {
        throw "local_app_data_unavailable"
    }
    [System.IO.Path]::GetFullPath((Join-Path $localBase ("Akane\satellite\" + $InstanceId)))
}
$logDirectory = Join-Path $resolvedDataRoot "logs"
$runDirectory = Join-Path $resolvedDataRoot "run"
New-Item -ItemType Directory -Force -Path $logDirectory, $runDirectory | Out-Null

if (-not $SkipLocalMedia) {
    $localMediaLauncher = Join-Path $projectRoot "scripts\start_akane_local_media.ps1"
    if (-not (Test-Path -LiteralPath $localMediaLauncher -PathType Leaf)) {
        throw "local_media_launcher_missing"
    }
    & $localMediaLauncher `
        -SshHost $SshHost `
        -LocalCapabilityPort $LocalMediaPort `
        -RemoteCapabilityPort $LocalMediaRemotePort `
        -DataRoot (Join-Path $resolvedDataRoot "local-media")
}

$gptSoVitsTunnelPidPath = Join-Path $runDirectory "gpt_sovits_reverse_tunnel.pid"
if (Test-AkaneTcpPort -HostName "127.0.0.1" -Port $GptSoVitsLocalPort) {
    $expectedReverseForward = "127.0.0.1:{0}:127.0.0.1:{1}" -f $GptSoVitsRemotePort, $GptSoVitsLocalPort
    if (Test-Path -LiteralPath $gptSoVitsTunnelPidPath -PathType Leaf) {
        $storedProcessId = 0
        if ([int]::TryParse(
            [System.IO.File]::ReadAllText($gptSoVitsTunnelPidPath).Trim(),
            [ref]$storedProcessId
        )) {
            $storedProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$storedProcessId" -ErrorAction SilentlyContinue
            $isTrackedReverseTunnel = (
                $null -ne $storedProcess -and
                [System.IO.Path]::GetFileNameWithoutExtension([string]$storedProcess.ExecutablePath) -eq "ssh" -and
                [string]$storedProcess.CommandLine -like "*-R*$expectedReverseForward*"
            )
            if ($isTrackedReverseTunnel) {
                Stop-Process -Id $storedProcessId -Force
                Start-Sleep -Milliseconds 200
            }
        }
    }
    Write-Host "[INFO] Publishing local GPT-SoVITS to the cloud over SSH loopback..."
    $gptSoVitsTunnelProcess = Start-AkaneGptSoVitsReverseTunnel `
        -Target $SshHost `
        -LocalProviderPort $GptSoVitsLocalPort `
        -RemoteProviderPort $GptSoVitsRemotePort `
        -LogDirectory $logDirectory
    Start-Sleep -Milliseconds 400
    if ($gptSoVitsTunnelProcess.HasExited) {
        throw "gpt_sovits_reverse_tunnel_failed: see $logDirectory"
    }
    [System.IO.File]::WriteAllText($gptSoVitsTunnelPidPath, [string]$gptSoVitsTunnelProcess.Id)
} else {
    Write-Host "[INFO] GPT-SoVITS is not listening on 127.0.0.1:$GptSoVitsLocalPort; provider tunnel not started."
}

$tokenSource = Import-AkanePersonalSatelliteToken -ExpectedInstanceId $InstanceId
$backendUrl = "http://127.0.0.1:$LocalPort"
$health = Get-AkaneCloudHealth -BaseUrl $backendUrl
$tunnelProcess = $null

if (Test-AkaneCloudHealth -Health $health -ExpectedInstanceId $InstanceId) {
    Write-Host "[INFO] Reusing the verified cloud tunnel at $backendUrl."
} else {
    if (Test-AkaneTcpPort -HostName "127.0.0.1" -Port $LocalPort) {
        throw "cloud_tunnel_port_in_use_by_another_service"
    }
    Write-Host "[INFO] Starting the encrypted SSH tunnel to '$SshHost'..."
    $tunnelProcess = Start-AkaneSshTunnel `
        -Target $SshHost `
        -BindPort $LocalPort `
        -TargetPort $RemotePort `
        -LogDirectory $logDirectory
    [System.IO.File]::WriteAllText(
        (Join-Path $runDirectory "cloud_satellite_tunnel.pid"),
        [string]$tunnelProcess.Id
    )
    $health = Wait-AkaneCloudHealth -BaseUrl $backendUrl -ExpectedInstanceId $InstanceId
    if ($null -eq $health) {
        if ($null -ne $tunnelProcess -and -not $tunnelProcess.HasExited) {
            Stop-Process -Id $tunnelProcess.Id -Force -ErrorAction SilentlyContinue
        }
        throw "cloud_tunnel_health_verification_failed: see $logDirectory"
    }
}

Write-Host "[INFO] Cloud instance verified: $InstanceId"
Write-Host "[INFO] Satellite credential source: $tokenSource"

$launchArgs = @{
    CloudSatellite = $true
    BackendUrl = $backendUrl
    InstanceId = $InstanceId
    DataRoot = $resolvedDataRoot
}
if ($NoBuild) { $launchArgs.NoBuild = $true }
if ($Rebuild) { $launchArgs.Rebuild = $true }
if ($OpenSettings) { $launchArgs.OpenSettings = $true }
if ($SkipDesktop) { $launchArgs.SkipDesktop = $true }

& $mainLauncher @launchArgs

if (-not $SkipDesktop -and -not $SkipOfferCheck) {
    Write-Host "[INFO] Waiting for the Desktop Satellite to publish four local offers..."
    if (-not (Wait-AkaneSatelliteOffers -BaseUrl $backendUrl -IncludeLocalMedia (-not $SkipLocalMedia))) {
        throw "cloud_satellite_offer_verification_failed: reviewed local capabilities did not become ready"
    }
    Write-Host "[OK] Cloud Akane is connected to all reviewed local capabilities."
}

Write-Host "[OK] Akane cloud personal startup completed."
