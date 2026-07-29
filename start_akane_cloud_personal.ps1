param(
    [string]$SshHost = "akane-vps",
    [int]$LocalPort = 11001,
    [int]$RemotePort = 10001,
    [int]$GptSoVitsLocalPort = 9880,
    [int]$GptSoVitsRemotePort = 19880,
    [string]$GptSoVitsRoot = "",
    [int]$LocalMediaPort = 9879,
    [int]$LocalMediaRemotePort = 19879,
    [string]$InstanceId = "personal",
    [string]$DataRoot = "",
    [switch]$NoBuild,
    [switch]$Rebuild,
    [switch]$OpenSettings,
    [switch]$SkipDesktop,
    [switch]$SkipOfferCheck,
    [switch]$SkipLocalMedia,
    [switch]$SkipGptSoVits
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

function Get-AkaneGptSoVitsApi {
    param([int]$Port)
    try {
        $spec = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/openapi.json" -TimeoutSec 3
        $paths = @($spec.paths.PSObject.Properties.Name)
        if ($paths -contains "/tts") {
            return $spec
        }
    } catch {
        return $null
    }
    return $null
}

function Wait-AkaneGptSoVitsApi {
    param(
        [int]$Port,
        [int]$Attempts = 120
    )
    for ($attempt = 0; $attempt -lt $Attempts; $attempt += 1) {
        $spec = Get-AkaneGptSoVitsApi -Port $Port
        if ($null -ne $spec) {
            return $spec
        }
        Start-Sleep -Milliseconds 500
    }
    return $null
}

function Resolve-AkaneGptSoVitsRoot {
    param([string]$ExplicitRoot)
    $configured = ([string]$ExplicitRoot).Trim()
    if (-not $configured) {
        $configured = ([string][Environment]::GetEnvironmentVariable(
            "AKANE_GPT_SOVITS_ROOT",
            "Process"
        )).Trim()
    }
    if (-not $configured) {
        $configured = ([string][Environment]::GetEnvironmentVariable(
            "AKANE_GPT_SOVITS_ROOT",
            "User"
        )).Trim()
    }
    if (-not $configured) {
        throw "gpt_sovits_root_not_configured"
    }
    return [System.IO.Path]::GetFullPath($configured)
}

function Test-AkaneTrackedProcess {
    param(
        [string]$PidPath,
        [string]$ExpectedExecutable,
        [string]$ExpectedCommandFragment
    )
    if (-not (Test-Path -LiteralPath $PidPath -PathType Leaf)) {
        return $false
    }
    $storedProcessId = 0
    if (-not [int]::TryParse(
        [System.IO.File]::ReadAllText($PidPath).Trim(),
        [ref]$storedProcessId
    )) {
        return $false
    }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$storedProcessId" -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        return $false
    }
    $actualExecutable = [System.IO.Path]::GetFullPath([string]$process.ExecutablePath)
    $expectedPath = [System.IO.Path]::GetFullPath($ExpectedExecutable)
    return (
        $actualExecutable.Equals($expectedPath, [System.StringComparison]::OrdinalIgnoreCase) -and
        [string]$process.CommandLine -like "*$ExpectedCommandFragment*"
    )
}

function Stop-AkaneTrackedProcess {
    param(
        [string]$PidPath,
        [string]$ExpectedExecutable,
        [string]$ExpectedCommandFragment
    )
    if (-not (Test-AkaneTrackedProcess `
        -PidPath $PidPath `
        -ExpectedExecutable $ExpectedExecutable `
        -ExpectedCommandFragment $ExpectedCommandFragment
    )) {
        return
    }
    $storedProcessId = [int][System.IO.File]::ReadAllText($PidPath).Trim()
    Stop-Process -Id $storedProcessId -Force
    Start-Sleep -Milliseconds 250
}

function Start-AkaneGptSoVitsApi {
    param(
        [string]$Root,
        [int]$Port,
        [string]$LogDirectory
    )
    $python = Join-Path $Root "runtime\python.exe"
    $entry = Join-Path $Root "api_v2.py"
    $config = Join-Path $Root "GPT_SoVITS\configs\tts_infer.yaml"
    foreach ($requiredPath in @($python, $entry, $config)) {
        if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
            throw "gpt_sovits_dependency_missing"
        }
    }
    return Start-Process `
        -FilePath $python `
        -ArgumentList @(
            "-I",
            "api_v2.py",
            "-a", "127.0.0.1",
            "-p", [string]$Port,
            "-c", "GPT_SoVITS/configs/tts_infer.yaml"
        ) `
        -WorkingDirectory $Root `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDirectory "gpt_sovits_api.log") `
        -RedirectStandardError (Join-Path $LogDirectory "gpt_sovits_api.err.log") `
        -PassThru
}

function Get-AkaneRemoteGptSoVitsApi {
    param(
        [string]$Target,
        [int]$Port
    )
    $ssh = Get-Command ssh -ErrorAction SilentlyContinue
    if ($null -eq $ssh) {
        throw "ssh_not_found"
    }
    try {
        $remoteCommand = "curl -fsS --max-time 4 http://127.0.0.1:$Port/openapi.json"
        $response = & $ssh.Source `
            "-T" `
            "-o" "BatchMode=yes" `
            "-o" "ConnectTimeout=4" `
            $Target `
            $remoteCommand 2>$null
        if ($LASTEXITCODE -ne 0) {
            return $null
        }
        $spec = (($response -join "`n") | ConvertFrom-Json)
        $paths = @($spec.paths.PSObject.Properties.Name)
        if ($paths -contains "/tts") {
            return $spec
        }
    } catch {
        return $null
    }
    return $null
}

function Wait-AkaneRemoteGptSoVitsApi {
    param(
        [string]$Target,
        [int]$Port,
        [int]$Attempts = 10
    )
    for ($attempt = 0; $attempt -lt $Attempts; $attempt += 1) {
        $spec = Get-AkaneRemoteGptSoVitsApi -Target $Target -Port $Port
        if ($null -ne $spec) {
            return $spec
        }
        Start-Sleep -Milliseconds 500
    }
    return $null
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

function Invoke-AkaneCloudGptSoVitsHealthCheck {
    param([string]$BaseUrl)
    try {
        $result = Invoke-RestMethod `
            -Method Post `
            -Uri (
                $BaseUrl.TrimEnd("/") +
                "/capabilities/providers/provider.tts.gpt_sovits.local/health-check" +
                "?user_id=desktop&real_user_id=master"
            ) `
            -ContentType "application/json" `
            -Body "{}" `
            -TimeoutSec 10
        if ([bool]$result.ok -and [string]$result.status -eq "ready") {
            return $result
        }
    } catch {
        return $null
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

$gptSoVitsApiPidPath = Join-Path $runDirectory "gpt_sovits_api.pid"
$gptSoVitsTunnelPidPath = Join-Path $runDirectory "gpt_sovits_reverse_tunnel.pid"
if (-not $SkipGptSoVits) {
    $resolvedGptSoVitsRoot = Resolve-AkaneGptSoVitsRoot -ExplicitRoot $GptSoVitsRoot
    $gptSoVitsPython = Join-Path $resolvedGptSoVitsRoot "runtime\python.exe"
    $gptSoVitsApi = Get-AkaneGptSoVitsApi -Port $GptSoVitsLocalPort
    if ($null -eq $gptSoVitsApi) {
        Stop-AkaneTrackedProcess `
            -PidPath $gptSoVitsApiPidPath `
            -ExpectedExecutable $gptSoVitsPython `
            -ExpectedCommandFragment "api_v2.py"
        if (Test-AkaneTcpPort -HostName "127.0.0.1" -Port $GptSoVitsLocalPort) {
            throw "gpt_sovits_port_in_use_by_another_service"
        }
        Write-Host "[INFO] Starting the configured character voice runtime..."
        $gptSoVitsProcess = Start-AkaneGptSoVitsApi `
            -Root $resolvedGptSoVitsRoot `
            -Port $GptSoVitsLocalPort `
            -LogDirectory $logDirectory
        [System.IO.File]::WriteAllText($gptSoVitsApiPidPath, [string]$gptSoVitsProcess.Id)
        $gptSoVitsApi = Wait-AkaneGptSoVitsApi -Port $GptSoVitsLocalPort
        if ($null -eq $gptSoVitsApi) {
            if (-not $gptSoVitsProcess.HasExited) {
                Stop-Process -Id $gptSoVitsProcess.Id -Force -ErrorAction SilentlyContinue
            }
            throw "gpt_sovits_start_timeout: see $logDirectory"
        }
    } else {
        Write-Host "[INFO] Reusing the verified character voice runtime."
    }

    $expectedReverseForward = "127.0.0.1:{0}:127.0.0.1:{1}" -f $GptSoVitsRemotePort, $GptSoVitsLocalPort
    $ssh = Get-Command ssh -ErrorAction SilentlyContinue
    if ($null -eq $ssh) {
        throw "ssh_not_found"
    }
    Stop-AkaneTrackedProcess `
        -PidPath $gptSoVitsTunnelPidPath `
        -ExpectedExecutable $ssh.Source `
        -ExpectedCommandFragment $expectedReverseForward
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
    if ($null -eq (Wait-AkaneRemoteGptSoVitsApi -Target $SshHost -Port $GptSoVitsRemotePort)) {
        Stop-Process -Id $gptSoVitsTunnelProcess.Id -Force -ErrorAction SilentlyContinue
        throw "gpt_sovits_reverse_tunnel_health_failed: see $logDirectory"
    }
    Write-Host "[OK] Character voice runtime is reachable from the cloud."
} else {
    Write-Host "[INFO] Character voice runtime startup was explicitly skipped."
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
if (-not $SkipGptSoVits) {
    $providerHealth = Invoke-AkaneCloudGptSoVitsHealthCheck -BaseUrl $backendUrl
    if ($null -eq $providerHealth) {
        throw "cloud_gpt_sovits_health_refresh_failed"
    }
    Write-Host "[OK] Cloud character voice provider state is ready."
}

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
