param(
    [string]$SshHost = "akane-vps",
    [int]$LocalCapabilityPort = 9879,
    [int]$RemoteCapabilityPort = 19879,
    [int]$RvcPort = 7899,
    [string]$RvcRoot = "",
    [string]$WhisperCacheDir = "",
    [string]$WhisperModel = "",
    [string]$AsrDevice = "",
    [string]$AsrComputeType = "",
    [string]$DataRoot = ""
)

$ErrorActionPreference = "Stop"

function Test-AkaneTcpPort {
    param([string]$HostName, [int]$Port)
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

function Get-AkaneEnvValue {
    param([string]$Path, [string]$Name)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return ""
    }
    foreach ($rawLine in [System.IO.File]::ReadAllLines($Path)) {
        $line = ([string]$rawLine).Trim()
        if (-not $line -or $line.StartsWith("#")) {
            continue
        }
        $separator = $line.IndexOf("=")
        if ($separator -le 0 -or $line.Substring(0, $separator).Trim() -ne $Name) {
            continue
        }
        $value = $line.Substring($separator + 1).Trim()
        if (
            $value.Length -ge 2 -and
            (($value.StartsWith('"') -and $value.EndsWith('"')) -or
             ($value.StartsWith("'") -and $value.EndsWith("'")))
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        return $value
    }
    return ""
}

function Get-AkaneLocalMediaHealth {
    param([int]$Port)
    try {
        return Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 3
    } catch {
        return $null
    }
}

function Test-AkaneLocalMediaHealth {
    param([object]$Health)
    return (
        $null -ne $Health -and
        [string]$Health.service -eq "akane_local_media_capabilities" -and
        [int]$Health.protocol_version -eq 1
    )
}

function Wait-AkaneCondition {
    param(
        [scriptblock]$Probe,
        [int]$Attempts,
        [int]$DelayMilliseconds
    )
    for ($attempt = 0; $attempt -lt $Attempts; $attempt += 1) {
        $value = & $Probe
        if ($value) {
            return $value
        }
        Start-Sleep -Milliseconds $DelayMilliseconds
    }
    return $null
}

function Stop-AkaneTrackedProcess {
    param(
        [string]$PidPath,
        [string]$ExpectedProcessName,
        [string]$ExpectedCommandFragment
    )
    if (-not (Test-Path -LiteralPath $PidPath -PathType Leaf)) {
        return
    }
    $storedProcessId = 0
    if (-not [int]::TryParse([System.IO.File]::ReadAllText($PidPath).Trim(), [ref]$storedProcessId)) {
        return
    }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$storedProcessId" -ErrorAction SilentlyContinue
    if (
        $null -ne $process -and
        [System.IO.Path]::GetFileNameWithoutExtension([string]$process.ExecutablePath) -eq $ExpectedProcessName -and
        [string]$process.CommandLine -like "*$ExpectedCommandFragment*"
    ) {
        Stop-Process -Id $storedProcessId -Force
        Start-Sleep -Milliseconds 250
    }
}

function Start-AkaneReverseTunnel {
    param(
        [string]$Target,
        [int]$LocalPort,
        [int]$RemotePort,
        [string]$LogDirectory
    )
    $ssh = Get-Command ssh -ErrorAction SilentlyContinue
    if ($null -eq $ssh) {
        throw "ssh_not_found"
    }
    $forward = "127.0.0.1:{0}:127.0.0.1:{1}" -f $RemotePort, $LocalPort
    return Start-Process `
        -FilePath $ssh.Source `
        -ArgumentList @(
            "-N",
            "-T",
            "-o", "BatchMode=yes",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
            "-R", $forward,
            $Target
        ) `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDirectory "local_media_reverse_tunnel.log") `
        -RedirectStandardError (Join-Path $LogDirectory "local_media_reverse_tunnel.err.log") `
        -PassThru
}

foreach ($port in @($LocalCapabilityPort, $RemoteCapabilityPort, $RvcPort)) {
    if ($port -lt 1 -or $port -gt 65535) {
        throw "invalid_local_media_port"
    }
}

$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$envPath = Join-Path $projectRoot ".env"
$resolvedRvcRoot = if ($RvcRoot.Trim()) {
    [System.IO.Path]::GetFullPath($RvcRoot.Trim())
} else {
    $configured = Get-AkaneEnvValue -Path $envPath -Name "RVC_ROOT_DIR"
    if (-not $configured.Trim()) {
        throw "rvc_root_not_configured"
    }
    [System.IO.Path]::GetFullPath($configured.Trim())
}
$resolvedWhisperCache = if ($WhisperCacheDir.Trim()) {
    [System.IO.Path]::GetFullPath($WhisperCacheDir.Trim())
} else {
    $configured = Get-AkaneEnvValue -Path $envPath -Name "WHISPER_CACHE_DIR"
    if ($configured.Trim()) { [System.IO.Path]::GetFullPath($configured.Trim()) } else { "" }
}
$resolvedWhisperModel = if ($WhisperModel.Trim()) {
    $WhisperModel.Trim().ToLowerInvariant()
} else {
    $configured = Get-AkaneEnvValue -Path $envPath -Name "AKANE_LOCAL_WHISPER_MODEL"
    if ($configured.Trim()) { $configured.Trim().ToLowerInvariant() } else { "small" }
}
$resolvedAsrDevice = if ($AsrDevice.Trim()) {
    $AsrDevice.Trim().ToLowerInvariant()
} else {
    $configured = Get-AkaneEnvValue -Path $envPath -Name "AKANE_LOCAL_ASR_DEVICE"
    if ($configured.Trim()) { $configured.Trim().ToLowerInvariant() } else { "cpu" }
}
$resolvedAsrComputeType = if ($AsrComputeType.Trim()) {
    $AsrComputeType.Trim().ToLowerInvariant()
} else {
    $configured = Get-AkaneEnvValue -Path $envPath -Name "AKANE_LOCAL_ASR_COMPUTE_TYPE"
    if ($configured.Trim()) { $configured.Trim().ToLowerInvariant() } else { "int8" }
}
if ($resolvedWhisperModel -notin @("tiny", "base", "small", "medium", "large-v2", "large-v3")) {
    throw "invalid_local_whisper_model"
}
if ($resolvedAsrDevice -notin @("auto", "cpu", "cuda")) {
    throw "invalid_local_asr_device"
}
if ($resolvedAsrComputeType -notin @("auto", "float16", "float32", "int8", "int8_float16")) {
    throw "invalid_local_asr_compute_type"
}
$resolvedDataRoot = if ($DataRoot.Trim()) {
    [System.IO.Path]::GetFullPath($DataRoot.Trim())
} else {
    if (-not ([string]$env:LOCALAPPDATA).Trim()) {
        throw "local_app_data_unavailable"
    }
    [System.IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA "Akane\local-media"))
}
$logDirectory = Join-Path $resolvedDataRoot "logs"
$runDirectory = Join-Path $resolvedDataRoot "run"
New-Item -ItemType Directory -Force -Path $logDirectory, $runDirectory | Out-Null

$rvcPython = Join-Path $resolvedRvcRoot "runtime\python.exe"
$rvcEntry = Join-Path $resolvedRvcRoot "infer-web.py"
$ffmpegPath = Join-Path $resolvedRvcRoot "ffmpeg.exe"
$demucsPackageRoot = Join-Path $resolvedDataRoot "demucs-runtime"
foreach ($requiredPath in @($rvcPython, $rvcEntry, $ffmpegPath)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "local_media_dependency_missing"
    }
}

if (-not (Test-AkaneTcpPort -HostName "127.0.0.1" -Port $RvcPort)) {
    Write-Host "[INFO] Starting local RVC..."
    $rvcProcess = Start-Process `
        -FilePath $rvcPython `
        -ArgumentList @("infer-web.py", "--pycmd", "runtime\python.exe", "--port", [string]$RvcPort) `
        -WorkingDirectory $resolvedRvcRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDirectory "rvc.log") `
        -RedirectStandardError (Join-Path $logDirectory "rvc.err.log") `
        -PassThru
    [System.IO.File]::WriteAllText((Join-Path $runDirectory "rvc.pid"), [string]$rvcProcess.Id)
    $rvcReady = Wait-AkaneCondition `
        -Attempts 180 `
        -DelayMilliseconds 1000 `
        -Probe { Test-AkaneTcpPort -HostName "127.0.0.1" -Port $RvcPort }
    if (-not $rvcReady) {
        throw "rvc_start_timeout"
    }
} else {
    Write-Host "[INFO] Reusing local RVC."
}

$health = Get-AkaneLocalMediaHealth -Port $LocalCapabilityPort
if (
    (Test-AkaneLocalMediaHealth -Health $health) -and
    (
        [string]$health.asr.model -ne $resolvedWhisperModel -or
        [string]$health.asr.device -ne $resolvedAsrDevice -or
        [string]$health.asr.compute_type -ne $resolvedAsrComputeType -or
        -not [bool]$health.separation.ready -or
        [string]$health.separation.executor -notlike "isolated_*"
    )
) {
    Write-Host "[INFO] Restarting local media host to apply capability configuration."
    Stop-AkaneTrackedProcess `
        -PidPath (Join-Path $runDirectory "local_media_host.pid") `
        -ExpectedProcessName "python" `
        -ExpectedCommandFragment "akane_local_capability_host.py"
    $health = $null
}
if (-not (Test-AkaneLocalMediaHealth -Health $health)) {
    if (Test-AkaneTcpPort -HostName "127.0.0.1" -Port $LocalCapabilityPort) {
        throw "local_media_port_in_use_by_another_service"
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $python) {
        throw "python_not_found"
    }
    $env:AKANE_LOCAL_RVC_ROOT = $resolvedRvcRoot
    $env:AKANE_LOCAL_RVC_BASE_URL = "http://127.0.0.1:$RvcPort"
    $env:AKANE_LOCAL_FFMPEG_PATH = $ffmpegPath
    $env:AKANE_LOCAL_WHISPER_CACHE_DIR = $resolvedWhisperCache
    $env:AKANE_LOCAL_WHISPER_MODEL = $resolvedWhisperModel
    $env:AKANE_LOCAL_ASR_DEVICE = $resolvedAsrDevice
    $env:AKANE_LOCAL_ASR_COMPUTE_TYPE = $resolvedAsrComputeType
    $env:AKANE_LOCAL_DEMUCS_PYTHON = $rvcPython
    $env:AKANE_LOCAL_DEMUCS_PACKAGE_ROOT = $demucsPackageRoot
    if ($resolvedAsrDevice -eq "cuda") {
        $cudaRuntimeDirectory = Join-Path $resolvedRvcRoot "runtime\Lib\site-packages\torch\lib"
        $cublasPath = Join-Path $cudaRuntimeDirectory "cublas64_12.dll"
        $cudnnPath = Join-Path $cudaRuntimeDirectory "cudnn64_9.dll"
        if (
            -not (Test-Path -LiteralPath $cublasPath -PathType Leaf) -or
            -not (Test-Path -LiteralPath $cudnnPath -PathType Leaf)
        ) {
            throw "local_asr_cuda_runtime_missing"
        }
        $env:PATH = "$cudaRuntimeDirectory;$env:PATH"
    }
    Write-Host "[INFO] Starting Akane local media capability host..."
    $hostProcess = Start-Process `
        -FilePath $python.Source `
        -ArgumentList @("scripts\akane_local_capability_host.py", "--port", [string]$LocalCapabilityPort) `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDirectory "local_media_host.log") `
        -RedirectStandardError (Join-Path $logDirectory "local_media_host.err.log") `
        -PassThru
    [System.IO.File]::WriteAllText((Join-Path $runDirectory "local_media_host.pid"), [string]$hostProcess.Id)
    $health = Wait-AkaneCondition `
        -Attempts 90 `
        -DelayMilliseconds 1000 `
        -Probe {
            $candidate = Get-AkaneLocalMediaHealth -Port $LocalCapabilityPort
            if (Test-AkaneLocalMediaHealth -Health $candidate) { $candidate } else { $null }
        }
    if ($null -eq $health) {
        throw "local_media_host_start_timeout"
    }
} else {
    Write-Host "[INFO] Reusing Akane local media capability host."
}

if (-not [bool]$health.asr.ready) {
    throw "local_asr_not_ready"
}
if (-not [bool]$health.rvc.ready) {
    throw "local_rvc_not_ready"
}

$tunnelPidPath = Join-Path $runDirectory "local_media_reverse_tunnel.pid"
$expectedForward = "127.0.0.1:{0}:127.0.0.1:{1}" -f $RemoteCapabilityPort, $LocalCapabilityPort
Stop-AkaneTrackedProcess `
    -PidPath $tunnelPidPath `
    -ExpectedProcessName "ssh" `
    -ExpectedCommandFragment $expectedForward

Write-Host "[INFO] Publishing local ASR and RVC through encrypted SSH loopback..."
$tunnelProcess = Start-AkaneReverseTunnel `
    -Target $SshHost `
    -LocalPort $LocalCapabilityPort `
    -RemotePort $RemoteCapabilityPort `
    -LogDirectory $logDirectory
Start-Sleep -Milliseconds 500
if ($tunnelProcess.HasExited) {
    throw "local_media_reverse_tunnel_failed"
}
[System.IO.File]::WriteAllText($tunnelPidPath, [string]$tunnelProcess.Id)

Write-Host "[OK] Local ASR and RVC are ready."
