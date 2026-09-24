# Shared local voice runtime lifecycle. Dot-sourcing does not start services.

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
        [int]$Attempts = 120,
        [object]$Process = $null
    )
    for ($attempt = 0; $attempt -lt $Attempts; $attempt += 1) {
        if ($null -ne $Process -and $Process.HasExited) { return $null }
        $spec = Get-AkaneGptSoVitsApi -Port $Port
        if ($null -ne $spec) {
            return $spec
        }
        Start-Sleep -Milliseconds 500
    }
    return $null
}

function Resolve-AkaneGptSoVitsRoot {
    param([string]$ExplicitRoot, [switch]$Optional)
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
        if ($Optional) { return "" }
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
    $actualExecutableValue = ([string]$process.ExecutablePath).Trim()
    $expectedExecutableValue = ([string]$ExpectedExecutable).Trim()
    if (-not $actualExecutableValue -or -not $expectedExecutableValue) {
        return $false
    }
    try {
        $actualExecutable = [System.IO.Path]::GetFullPath($actualExecutableValue)
        $expectedPath = [System.IO.Path]::GetFullPath($expectedExecutableValue)
    } catch {
        return $false
    }
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
    $hostEntry = Join-Path $PSScriptRoot "launch_gpt_sovits_api.py"
    $config = Join-Path $Root "GPT_SoVITS\configs\tts_infer.yaml"
    foreach ($requiredPath in @($python, $entry, $hostEntry, $config)) {
        if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
            throw "gpt_sovits_dependency_missing"
        }
    }
    return Start-Process `
        -FilePath $python `
        -ArgumentList @(
            "-I",
            ('"' + $hostEntry + '"'),
            "--upstream-api", "api_v2.py",
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

function Ensure-AkaneGptSoVitsApi {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [int]$Port = 9880,
        [Parameter(Mandatory = $true)][string]$LogDirectory,
        [Parameter(Mandatory = $true)][string]$PidPath
    )
    if ($Port -lt 1 -or $Port -gt 65535) { throw "invalid_gpt_sovits_port" }
    $mutex = [System.Threading.Mutex]::new($false, "Local\Akane.GptSoVits.$Port")
    $ownsMutex = $false
    try {
        try { $ownsMutex = $mutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $ownsMutex = $true }
        if (-not $ownsMutex) {
            if ($null -ne (Wait-AkaneGptSoVitsApi -Port $Port)) {
                return [pscustomobject]@{ Status = "ready"; Started = $false }
            }
            throw "gpt_sovits_start_in_progress"
        }
        if ($null -ne (Get-AkaneGptSoVitsApi -Port $Port)) {
            return [pscustomobject]@{ Status = "ready"; Started = $false }
        }
        if (Test-AkaneTcpPort -HostName "127.0.0.1" -Port $Port) {
            throw "gpt_sovits_port_in_use_by_another_service"
        }
        # Only retire a stale process whose executable and entrypoint match our receipt.
        Stop-AkaneTrackedProcess -PidPath $PidPath -ExpectedExecutable (Join-Path $Root "runtime\python.exe") -ExpectedCommandFragment "api_v2.py"
        New-Item -ItemType Directory -Force -Path $LogDirectory, (Split-Path -Parent $PidPath) | Out-Null
        $voiceProcess = Start-AkaneGptSoVitsApi -Root $Root -Port $Port -LogDirectory $LogDirectory
        try {
            [System.IO.File]::WriteAllText($PidPath, [string]$voiceProcess.Id)
            if ($null -eq (Wait-AkaneGptSoVitsApi -Port $Port -Process $voiceProcess)) {
                throw "gpt_sovits_start_timeout_or_exit"
            }
        } catch {
            if (-not $voiceProcess.HasExited) {
                Stop-Process -Id $voiceProcess.Id -Force -ErrorAction SilentlyContinue
            }
            throw
        }
        return [pscustomobject]@{ Status = "ready"; Started = $true }
    } finally {
        if ($ownsMutex) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}
