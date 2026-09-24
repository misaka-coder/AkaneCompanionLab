param(
    [ValidateSet("Start", "Status", "Stop", "Restart", "Doctor")]
    [string]$Action = "Start",
    [string]$InstanceId = "",
    [string]$DataRoot = "",
    [int]$BackendPort = 9999,
    [string]$EnvFile = "",
    [switch]$CloudSatellite,
    [string]$BackendUrl = "",
    [switch]$SkipBackend,
    [switch]$ReuseBackend,
    [switch]$RestartBackend,
    [switch]$SkipDesktop,
    [switch]$DeviceOnly,
    [switch]$NoBuild,
    [switch]$Dev,
    [switch]$ControlCenterLab,
    [switch]$OpenSettings,
    [switch]$Rebuild
)

$ErrorActionPreference = "Stop"

function Find-ProjectRoot {
    param([string]$StartDir)

    $current = (Resolve-Path -LiteralPath $StartDir).Path
    while ($current) {
        if (
            (Test-Path -LiteralPath (Join-Path $current "launch_akane_memory_v01.py")) -and
            (Test-Path -LiteralPath (Join-Path $current "desktop_pet_next\package.json"))
        ) {
            return $current
        }

        $parent = Split-Path -Parent $current
        if (-not $parent -or $parent -eq $current) {
            break
        }
        $current = $parent
    }

    throw "Could not locate Akane project root from: $StartDir"
}

function Test-TcpPort {
    param(
        [string]$HostName,
        [int]$Port
    )

    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect($HostName, $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(500, $false)) {
            return $false
        }
        $client.EndConnect($async)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Get-BackendHealth {
    param(
        [string]$HostName,
        [int]$Port
    )

    $healthUrl = "http://{0}:{1}/health" -f $HostName, $Port
    try {
        return Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
    } catch {
        return $null
    }
}

function Wait-AkaneBackendReady {
    param(
        [int]$Port,
        [string]$ExpectedInstanceId,
        [int]$ProcessId = 0,
        [int]$TimeoutSeconds = 120
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $url = "http://127.0.0.1:{0}/health" -f $Port
    while ([DateTime]::UtcNow -lt $deadline) {
        if ($ProcessId -gt 0) {
            try {
                $null = Get-Process -Id $ProcessId -ErrorAction Stop
            } catch {
                throw "Backend process exited before reporting a healthy instance."
            }
        }
        try {
            $health = Invoke-RestMethod -Uri $url -TimeoutSec 2
            if (Test-AkaneInstanceHealth -Health $health -ExpectedInstanceId $ExpectedInstanceId) {
                return $true
            }
        } catch {
            # The backend may still be importing configuration; keep polling.
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Get-BackendHealthUrl {
    param([string]$BackendUrl)

    try {
        $healthUrl = ([System.Uri]::new($BackendUrl.TrimEnd('/') + "/health")).AbsoluteUri
        return Invoke-RestMethod -Uri $healthUrl -TimeoutSec 6
    } catch {
        return $null
    }
}

function Resolve-AkaneSatelliteBackendUrl {
    param([string]$Value)

    $candidate = $Value.Trim().TrimEnd('/')
    if (-not $candidate) { throw "cloud_satellite_backend_url_required" }
    try { $uri = [System.Uri]::new($candidate) } catch { throw "invalid_cloud_satellite_backend_url" }
    if (
        -not $uri.IsAbsoluteUri -or
        $uri.UserInfo -or
        $uri.AbsolutePath -ne "/" -or
        $uri.Query -or
        $uri.Fragment
    ) { throw "invalid_cloud_satellite_backend_url" }
    $loopback = $uri.IsLoopback -or $uri.Host -eq "localhost"
    if ($uri.Scheme -ne "https" -and -not ($uri.Scheme -eq "http" -and $loopback)) {
        throw "cloud_satellite_requires_https"
    }
    return $candidate
}

function New-AkaneSatelliteToken {
    $bytes = New-Object byte[] 32
    $random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $random.GetBytes($bytes) } finally { $random.Dispose() }
    return [Convert]::ToBase64String($bytes)
}

function Get-AkanePersistedSatelliteToken {
    param([string]$DataRoot)

    $tokenPath = Join-Path $DataRoot "config\desktop-satellite-token"
    if (-not (Test-Path -LiteralPath $tokenPath -PathType Leaf)) {
        return ""
    }
    try {
        $token = ([System.IO.File]::ReadAllText($tokenPath)).Trim()
    } catch {
        return ""
    }
    if ($token.Length -lt 32 -or $token.Length -gt 256 -or $token -match "\s") {
        return ""
    }
    return $token
}

function Save-AkanePersistedSatelliteToken {
    param(
        [string]$DataRoot,
        [string]$Token
    )

    $configDir = Join-Path $DataRoot "config"
    New-Item -ItemType Directory -Force -Path $configDir | Out-Null
    $tokenPath = Join-Path $configDir "desktop-satellite-token"
    if (Test-Path -LiteralPath $tokenPath -PathType Leaf) {
        return
    }
    $temporaryPath = "$tokenPath.$([Guid]::NewGuid().ToString('N')).tmp"
    try {
        [System.IO.File]::WriteAllText($temporaryPath, "$Token`n", [System.Text.UTF8Encoding]::new($false))
        try {
            [System.IO.File]::Move($temporaryPath, $tokenPath)
        } catch [System.IO.IOException] {
            if (-not (Test-Path -LiteralPath $tokenPath -PathType Leaf)) {
                throw
            }
        }
    } finally {
        if (Test-Path -LiteralPath $temporaryPath) {
            try { [System.IO.File]::Delete($temporaryPath) } catch { }
        }
    }
}

function Import-AkaneSatelliteTokenForInstance {
    param([string]$InstanceId)

    if (-not [string]::IsNullOrWhiteSpace([string]$env:AKANE_DESKTOP_SATELLITE_TOKEN)) {
        return
    }
    $suffix = ($InstanceId.ToUpperInvariant() -replace '[^A-Z0-9_]', '_')
    foreach ($name in @("AKANE_DESKTOP_SATELLITE_TOKEN_$suffix", "AKANE_DESKTOP_SATELLITE_TOKEN")) {
        $stored = [System.Environment]::GetEnvironmentVariable($name, "User")
        if (-not [string]::IsNullOrWhiteSpace([string]$stored)) {
            [System.Environment]::SetEnvironmentVariable(
                "AKANE_DESKTOP_SATELLITE_TOKEN",
                ([string]$stored).Trim(),
                "Process"
            )
            return
        }
    }
}

function Test-AkaneBackendHealth {
    param(
        [object]$Health,
        [string]$ExpectedInstanceId
    )

    if ($null -eq $Health) {
        return $false
    }

    $status = [string]($Health.status)
    $instanceId = [string]($Health.instance_id)
    $rootBinding = [string]($Health.root_binding)
    return (
        $status -eq "ok" -and
        $rootBinding -eq "valid" -and
        $instanceId -eq $ExpectedInstanceId
    )
}

function Get-BackendListeningProcessId {
    param([int]$Port)

    try {
        $connections = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop)
        foreach ($connection in $connections) {
            $owningProcess = [int]($connection.OwningProcess)
            if ($owningProcess -gt 0) {
                return $owningProcess
            }
        }
    } catch {
        return 0
    }
    return 0
}

function Test-AkaneBackendProcess {
    param([int]$ProcessId)

    if ($ProcessId -le 0) {
        return $false
    }

    try {
        $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId"
    } catch {
        return $false
    }

    if ($null -eq $processInfo) {
        return $false
    }

    $commandLine = [string]($processInfo.CommandLine)
    return [bool]($commandLine -match "launch_akane_memory_v01\.py|companion_v01\.app:app")
}

function Stop-AkaneBackendProcess {
    param(
        [int]$ProcessId,
        [int]$Port
    )

    Write-Host "[INFO] Stopping existing Akane backend PID: $ProcessId"
    Stop-Process -Id $ProcessId -Force -ErrorAction Stop

    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 250
        if (-not (Test-TcpPort -HostName "127.0.0.1" -Port $Port)) {
            Write-Host "[INFO] Previous backend stopped."
            return
        }
    }

    throw "Backend process $ProcessId was stopped, but port $Port is still busy."
}

function Stop-AkaneDesktopProcesses {
    param([string]$ExePath)

    $targetPath = [System.IO.Path]::GetFullPath($ExePath)
    $processes = @(Get-Process -Name "akane_desktop_pet_next" -ErrorAction SilentlyContinue)
    $targetProcessName = [System.IO.Path]::GetFileNameWithoutExtension($targetPath)
    if ($targetProcessName -and $targetProcessName -ine "akane_desktop_pet_next") {
        $processes += @(Get-Process -Name $targetProcessName -ErrorAction SilentlyContinue)
    }
    foreach ($process in $processes) {
        $processPath = ""
        try {
            $processPath = [System.IO.Path]::GetFullPath([string]$process.Path)
        } catch {
            continue
        }

        if ($processPath -ine $targetPath) {
            continue
        }

        Write-Host "[INFO] Stopping existing Akane Next desktop PID: $($process.Id)"
        try {
            Stop-Process -Id $process.Id -Force -ErrorAction Stop
            Wait-Process -Id $process.Id -Timeout 5 -ErrorAction SilentlyContinue
        } catch {
            Write-Host "[WARN] Failed to stop existing Akane Next desktop PID $($process.Id): $($_.Exception.Message)"
        }
    }
}

function Get-AkaneDesktopProcess {
    param([string]$ExePath)

    $targetPath = [System.IO.Path]::GetFullPath($ExePath)
    $processNames = @("akane_desktop_pet_next")
    $targetProcessName = [System.IO.Path]::GetFileNameWithoutExtension($targetPath)
    if ($targetProcessName -and $targetProcessName -ine "akane_desktop_pet_next") {
        $processNames += $targetProcessName
    }
    foreach ($processName in $processNames) {
        foreach ($process in @(Get-Process -Name $processName -ErrorAction SilentlyContinue)) {
        try {
            $processPath = [System.IO.Path]::GetFullPath([string]$process.Path)
        } catch {
            continue
        }
        if ($processPath -ieq $targetPath) {
            return $process
        }
        }
    }
    return $null
}

function Signal-AkaneDesktopActivation {
    param([string]$InstanceId)

    $safeId = ($InstanceId -replace '[^A-Za-z0-9_.-]', '_')
    $eventName = "Local\Akane.Desktop.Activate.$safeId"
    try {
        $event = [System.Threading.EventWaitHandle]::OpenExisting($eventName)
        try {
            return $event.Set()
        } finally {
            $event.Dispose()
        }
    } catch [System.Threading.WaitHandleCannotBeOpenedException] {
        return $false
    } catch {
        Write-Host "[WARN] Existing desktop could not be activated: $($_.Exception.Message)"
        return $false
    }
}

function Wait-AkaneDesktopReady {
    param(
        [string]$DataRoot,
        [string]$ExpectedInstanceId,
        [int]$ProcessId,
        [int]$TimeoutSeconds = 120
    )

    $statePath = Join-Path $DataRoot "run\desktop-ui-state.json"
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        $processAlive = $false
        try {
            $processAlive = $null -ne (Get-Process -Id $ProcessId -ErrorAction Stop)
        } catch {
            $processAlive = $false
        }

        if (Test-Path -LiteralPath $statePath -PathType Leaf) {
            try {
                $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
                $sameInstance = [string]$state.instance_id -eq $ExpectedInstanceId
                $sameProcess = [int]$state.process_id -eq $ProcessId
                if ($sameInstance -and $sameProcess -and [string]$state.status -eq "ready") {
                    Write-Host "[INFO] Desktop UI is ready for instance '$ExpectedInstanceId'."
                    return $true
                }
                if ($sameInstance -and $sameProcess -and [string]$state.status -eq "failed") {
                    $phase = [string]$state.phase
                    $reason = [string]$state.reason
                    $reasonSuffix = if ([string]::IsNullOrWhiteSpace($reason)) { "" } else { ": $reason" }
                    throw ("Desktop UI initialization failed at {0}{1}" -f $phase, $reasonSuffix)
                }
            } catch {
                if ($_.Exception.Message -like "Desktop UI initialization failed*") {
                    throw $_
                }
            }
        }

        if (-not $processAlive) {
            throw "Desktop process exited before reporting ui_ready."
        }
        Start-Sleep -Milliseconds 500
    }
    throw "Desktop UI did not report ui_ready within $TimeoutSeconds seconds. Check the instance log and WebView2 runtime."
}

function Wait-AkaneDeviceReady {
    param(
        [string]$DataRoot,
        [string]$ExpectedInstanceId,
        [int]$ProcessId,
        [int]$TimeoutSeconds = 30
    )

    $statePath = Join-Path $DataRoot "run\desktop-device-state.json"
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        $processAlive = $false
        try {
            $processAlive = $null -ne (Get-Process -Id $ProcessId -ErrorAction Stop)
        } catch {
            $processAlive = $false
        }

        if (Test-Path -LiteralPath $statePath -PathType Leaf) {
            try {
                $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
                $sameInstance = [string]$state.instance_id -eq $ExpectedInstanceId
                $sameProcess = [int]$state.process_id -eq $ProcessId
                if ($sameInstance -and $sameProcess -and [string]$state.status -eq "online") {
                    Write-Host "[INFO] Desktop device executor is online for instance '$ExpectedInstanceId'."
                    return $true
                }
            } catch {
                # The native process replaces the marker atomically; a partial
                # read simply means the next poll should try again.
            }
        }

        if (-not $processAlive) {
            throw "Desktop device executor exited before reporting online."
        }
        Start-Sleep -Milliseconds 500
    }
    throw "Desktop device executor did not report online within $TimeoutSeconds seconds. Check the instance log and backend health."
}

function Copy-AkaneInstanceExecutable {
    param(
        [string]$SourcePath,
        [string]$DestinationPath
    )

    $parent = Split-Path -Parent $DestinationPath
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $pendingPath = "$DestinationPath.pending.$([Guid]::NewGuid().ToString('N')).exe"
    try {
        Copy-Item -LiteralPath $SourcePath -Destination $pendingPath -Force
        Move-Item -LiteralPath $pendingPath -Destination $DestinationPath -Force
    } finally {
        Remove-Item -LiteralPath $pendingPath -Force -ErrorAction SilentlyContinue
    }
}

function Start-AkaneDeviceExecutor {
    param(
        [string]$SourceExe,
        [string]$DeviceExe,
        [string]$DesktopDir,
        [string]$DataRoot,
        [string]$ExpectedInstanceId,
        [int]$ReadyTimeoutSeconds = 30,
        [switch]$BestEffort
    )

    $existing = Get-AkaneDesktopProcess -ExePath $DeviceExe
    if ($null -ne $existing) {
        try {
            Wait-AkaneDeviceReady -DataRoot $DataRoot -ExpectedInstanceId $ExpectedInstanceId -ProcessId $existing.Id -TimeoutSeconds $ReadyTimeoutSeconds
        } catch {
            if (-not $BestEffort) { throw }
            Write-Host "[WARN] Desktop device executor is still connecting; the UI will remain available."
        }
        return $existing
    }

    Remove-Item -LiteralPath (Join-Path $DataRoot "run\desktop-device-state.json") -Force -ErrorAction SilentlyContinue
    Copy-AkaneInstanceExecutable -SourcePath $SourceExe -DestinationPath $DeviceExe
    $deviceStart = @{
        FilePath = $DeviceExe
        WorkingDirectory = $DesktopDir
        ArgumentList = @("--device-only")
        WindowStyle = "Hidden"
        PassThru = $true
    }
    $process = Start-Process @deviceStart
    Write-Host "[INFO] Desktop device executor PID: $($process.Id)"
    try {
        Wait-AkaneDeviceReady -DataRoot $DataRoot -ExpectedInstanceId $ExpectedInstanceId -ProcessId $process.Id -TimeoutSeconds $ReadyTimeoutSeconds
    } catch {
        if (-not $BestEffort) { throw }
        Write-Host "[WARN] Desktop device executor is still connecting; the UI will remain available."
    }
    return $process
}

function Resolve-Python {
    param([string]$ProjectDir)

    $venvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        return $venvPython
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return $python.Source
    }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        return $py.Source
    }

    throw "Python was not found. Install Python or create .venv first."
}

function Write-AkaneFirstBuildHints {
    param([string]$Reason)

    Write-Host ""
    Write-Host "[首次构建提示] 即将从源码构建桌宠 (Tauri release)。原因：$Reason"
    Write-Host "             首次启动或源码更新后通常需要几分钟，请耐心等待，不要关闭窗口。"
    Write-Host "             过程中看到 ``Compiling ...`` 与 Rust crate 名字是 cargo 在编译，属正常现象，不是错误。"
    Write-Host "             构建完成后桌宠会自动打开。"
    Write-Host ""
}

function Ensure-NpmInstall {
    param([string]$DesktopDir)

    # Check for the actual Tauri CLI binary, not just the node_modules directory.
    # A previous failed/interrupted npm install can leave a partial node_modules that
    # passes the directory check but is missing key binaries like tauri.cmd.
    $tauriCmd = Join-Path $DesktopDir "node_modules\.bin\tauri.cmd"
    if (Test-Path -LiteralPath $tauriCmd) {
        return
    }

    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npm) {
        throw "npm was not found. Install Node.js before building Akane Next."
    }

    Write-Host "[INFO] desktop_pet_next/node_modules not found or incomplete. Running npm install..."
    Push-Location -LiteralPath $DesktopDir
    try {
        # Redirect the global npm prefix to a user-writable directory so npm install
        # does not attempt to write to C:\Program Files\nodejs\ (EPERM on non-admin).
        $savedPrefix = $env:npm_config_prefix
        if (-not $env:npm_config_prefix) {
            $env:npm_config_prefix = Join-Path $env:APPDATA "npm"
        }
        try {
            & npm install
            if ($LASTEXITCODE -ne 0) {
                throw "npm install failed with exit code $LASTEXITCODE"
            }
        } finally {
            $env:npm_config_prefix = $savedPrefix
        }
    } finally {
        Pop-Location
    }
}

function Get-NewestInputWriteTime {
    param([string]$DesktopDir)

    $paths = @(
        (Join-Path $DesktopDir "src"),
        (Join-Path $DesktopDir "src-tauri\src"),
        (Join-Path $DesktopDir "src-tauri\capabilities"),
        (Join-Path $DesktopDir "src-tauri\icons"),
        (Join-Path $DesktopDir "src-tauri\build.rs"),
        (Join-Path $DesktopDir "src-tauri\Cargo.toml"),
        (Join-Path $DesktopDir "src-tauri\Cargo.lock"),
        (Join-Path $DesktopDir "src-tauri\tauri.conf.json"),
        (Join-Path $DesktopDir "package.json"),
        (Join-Path $DesktopDir "package-lock.json"),
        (Join-Path $DesktopDir "vite.config.js"),
        (Join-Path $DesktopDir "index.html"),
        (Join-Path $DesktopDir "settings.html"),
        (Join-Path $DesktopDir "workspace.html"),
        (Join-Path $DesktopDir "workshop.html"),
        (Join-Path $DesktopDir "control-center-lab.html")
    )

    $newest = $null
    foreach ($path in $paths) {
        if (-not (Test-Path -LiteralPath $path)) {
            continue
        }

        $item = Get-Item -LiteralPath $path
        $files = if ($item.PSIsContainer) {
            Get-ChildItem -LiteralPath $path -Recurse -File
        } else {
            @($item)
        }

        foreach ($file in $files) {
            if ($null -eq $newest -or $file.LastWriteTime -gt $newest) {
                $newest = $file.LastWriteTime
            }
        }
    }

    return $newest
}

$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $scriptDir) {
    $scriptDir = (Get-Location).Path
}

$projectDir = Find-ProjectRoot -StartDir $scriptDir
$desktopDir = Join-Path $projectDir "desktop_pet_next"
$releaseExe = Join-Path $desktopDir "src-tauri\target\release\akane_desktop_pet_next.exe"
. (Join-Path $projectDir "scripts\akane_data_root.ps1")
. (Join-Path $projectDir "scripts\akane_instance_launcher.ps1")

$instanceIdWasBound = $PSBoundParameters.ContainsKey("InstanceId")
$dataRootWasBound = $PSBoundParameters.ContainsKey("DataRoot")
$backendPortWasBound = $PSBoundParameters.ContainsKey("BackendPort")
$envFileWasBound = $PSBoundParameters.ContainsKey("EnvFile")

if ($envFileWasBound -and -not [string]::IsNullOrWhiteSpace($EnvFile)) {
    $envFilePath = if ([System.IO.Path]::IsPathRooted($EnvFile)) {
        [System.IO.Path]::GetFullPath($EnvFile)
    } else {
        [System.IO.Path]::GetFullPath((Join-Path $projectDir $EnvFile))
    }
    $null = Import-AkaneEnvFile -Path $envFilePath
    $env:AKANE_ENV_FILE = $envFilePath
}

$expectedInstanceId = if ($instanceIdWasBound -and -not [string]::IsNullOrWhiteSpace($InstanceId)) {
    $InstanceId.Trim()
} elseif (-not [string]::IsNullOrWhiteSpace([string]$env:AKANE_INSTANCE_ID)) {
    ([string]$env:AKANE_INSTANCE_ID).Trim()
} else {
    "local-default"
}
if (-not (Test-AkaneSafeInstanceId -InstanceId $expectedInstanceId)) {
    throw "invalid_instance_id"
}

if (-not $backendPortWasBound -and -not [string]::IsNullOrWhiteSpace([string]$env:COMPANION_PORT)) {
    $configuredPort = 0
    if (-not [int]::TryParse(([string]$env:COMPANION_PORT).Trim(), [ref]$configuredPort) -or $configuredPort -lt 1 -or $configuredPort -gt 65535) {
        throw "invalid_backend_port"
    }
    $BackendPort = $configuredPort
}

$requestedDataRoot = if ($dataRootWasBound -and -not [string]::IsNullOrWhiteSpace($DataRoot)) {
    $DataRoot.Trim()
} else {
    ([string]$env:AKANE_DATA_ROOT).Trim()
}
$dataStatus = Initialize-AkaneDataRoot `
    -ProjectRoot $projectDir `
    -InstanceId $expectedInstanceId `
    -DataRoot $requestedDataRoot `
    -ReadOnly:($Action -in @("Status", "Stop", "Doctor"))
$dataRoot = $dataStatus.Root

$instanceBin = Join-Path $dataRoot "bin"
$instanceExe = Join-Path $instanceBin "akane_desktop_pet_next.exe"
$deviceExe = Join-Path $instanceBin "akane_desktop_pet_next_device.exe"
if ($Action -in @("Status", "Stop", "Doctor")) {
    $portInUse = Test-TcpPort -HostName "127.0.0.1" -Port $BackendPort
    $health = Get-BackendHealth -HostName "127.0.0.1" -Port $BackendPort
    $healthPid = Get-BackendListeningProcessId -Port $BackendPort
    $uiStatePath = Join-Path $dataRoot "run\desktop-ui-state.json"
    $deviceStatePath = Join-Path $dataRoot "run\desktop-device-state.json"
    $readMarker = {
        param([string]$Path)
        if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
        try { return (Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json) } catch { return $null }
    }
    $uiState = & $readMarker $uiStatePath
    $deviceState = & $readMarker $deviceStatePath
    $desktopProcess = Get-AkaneDesktopProcess -ExePath $instanceExe
    $deviceProcess = Get-AkaneDesktopProcess -ExePath $deviceExe
    $deviceOnline = $null -ne $deviceProcess -and $deviceState -and [string]$deviceState.status -eq "online" -and [string]$deviceState.instance_id -eq $expectedInstanceId
    $desktopReady = $null -ne $desktopProcess -and $uiState -and [string]$uiState.status -eq "ready" -and [string]$uiState.instance_id -eq $expectedInstanceId
    if ($Action -eq "Status" -or $Action -eq "Doctor") {
        $payload = [ordered]@{
            action = $Action.ToLowerInvariant()
            instance_id = $expectedInstanceId
            data_root = $dataRoot
            backend = [ordered]@{
                status = if (Test-AkaneInstanceHealth -Health $health -ExpectedInstanceId $expectedInstanceId) { "healthy" } else { "offline_or_mismatch" }
                process_id = $healthPid
                managed = Test-AkaneBackendProcess -ProcessId $healthPid
            }
            device = [ordered]@{
                status = if ($deviceOnline) { "online" } else { "offline" }
                process_id = if ($deviceProcess) { [int]$deviceProcess.Id } else { 0 }
            }
            desktop = [ordered]@{
                status = if ($desktopReady) { "ready" } else { "offline" }
                process_id = if ($desktopProcess) { [int]$desktopProcess.Id } else { 0 }
            }
        }
        if ($Action -eq "Doctor") {
            $payload.doctor = [ordered]@{
                release_exe = Test-Path -LiteralPath $releaseExe -PathType Leaf
                manifest = Test-Path -LiteralPath (Join-Path $dataRoot ("instances\{0}\instance.toml" -f $expectedInstanceId)) -PathType Leaf
                ui_marker = Test-Path -LiteralPath $uiStatePath -PathType Leaf
                device_marker = Test-Path -LiteralPath $deviceStatePath -PathType Leaf
            }
        }
        $payload | ConvertTo-Json -Depth 8 -Compress
        exit 0
    }

    if ($desktopProcess) { Stop-AkaneDesktopProcesses -ExePath $instanceExe }
    if ($deviceProcess) { Stop-AkaneDesktopProcesses -ExePath $deviceExe }
    foreach ($markerPath in @($uiStatePath, $deviceStatePath)) {
        if (Test-Path -LiteralPath $markerPath -PathType Leaf) {
            Remove-Item -LiteralPath $markerPath -Force -ErrorAction SilentlyContinue
        }
    }
    if (-not $SkipBackend -and (Test-AkaneInstanceHealth -Health $health -ExpectedInstanceId $expectedInstanceId)) {
        if ($healthPid -le 0 -or -not (Test-AkaneBackendProcess -ProcessId $healthPid)) {
            throw "backend_process_identity_unavailable"
        }
        Stop-AkaneBackendProcess -ProcessId $healthPid -Port $BackendPort
        Write-Host "[INFO] Stopped managed backend for instance '$expectedInstanceId'."
    } elseif (-not $SkipBackend -and $portInUse) {
        throw "refusing_to_stop_unmatched_backend"
    }
    Write-Host "[INFO] Stopped managed desktop resources for instance '$expectedInstanceId'."
    exit 0
}
if ($Action -eq "Restart") { $RestartBackend = $true }
$env:AKANE_DATA_ROOT_READY = "1"
if ($dataStatus.Failed -gt 0) {
    Write-Host "[WARN] User data root is ready, but $($dataStatus.Failed) legacy files could not be copied."
} elseif ($dataStatus.Copied -gt 0) {
    Write-Host "[INFO] Migrated $($dataStatus.Copied) legacy files without overwriting existing data."
}
$env:AKANE_DATA_ROOT = $dataRoot
$env:AKANE_INSTANCE_ID = $expectedInstanceId
$env:COMPANION_PORT = "$BackendPort"
$env:AKANE_MANAGED_LAUNCHER = Join-Path $projectDir "start_akane_next.ps1"
$env:AKANE_MANAGED_STOP_BACKEND = if ($CloudSatellite -or $SkipBackend) { "0" } else { "1" }
$generatedSatelliteToken = $false
if ($CloudSatellite) {
    if ($expectedInstanceId -eq "local-default") { throw "cloud_satellite_requires_named_instance" }
    if (-not $PSBoundParameters.ContainsKey("BackendUrl")) { throw "cloud_satellite_backend_url_required" }
    $resolvedBackendUrl = Resolve-AkaneSatelliteBackendUrl -Value $BackendUrl
    Import-AkaneSatelliteTokenForInstance -InstanceId $expectedInstanceId
    if ([string]::IsNullOrWhiteSpace([string]$env:AKANE_DESKTOP_SATELLITE_TOKEN)) {
        throw "cloud_satellite_token_required"
    }
    $remoteHealth = Get-BackendHealthUrl -BackendUrl $resolvedBackendUrl
    if (-not (Test-AkaneBackendHealth -Health $remoteHealth -ExpectedInstanceId $expectedInstanceId)) {
        throw "cloud_satellite_instance_verification_failed"
    }
    $env:AKANE_BACKEND_URL = $resolvedBackendUrl
} else {
    $resolvedBackendUrl = "http://127.0.0.1:$BackendPort"
    $env:AKANE_BACKEND_URL = $resolvedBackendUrl
    if (
        $expectedInstanceId -eq "local-default" -and
        -not $SkipBackend -and
        [string]::IsNullOrWhiteSpace([string]$env:AKANE_DESKTOP_SATELLITE_TOKEN)
    ) {
        $env:AKANE_DESKTOP_SATELLITE_TOKEN = Get-AkanePersistedSatelliteToken -DataRoot $dataRoot
        if ([string]::IsNullOrWhiteSpace([string]$env:AKANE_DESKTOP_SATELLITE_TOKEN)) {
            $env:AKANE_DESKTOP_SATELLITE_TOKEN = New-AkaneSatelliteToken
            Save-AkanePersistedSatelliteToken -DataRoot $dataRoot -Token $env:AKANE_DESKTOP_SATELLITE_TOKEN
            $generatedSatelliteToken = $true
        }
    }
}
$safeInstanceId = Get-AkaneSafeInstanceLogId -InstanceId $expectedInstanceId
$runtimeLogDir = Join-Path $dataRoot "logs"
$backendLog = Join-Path $runtimeLogDir "akane_backend.$safeInstanceId.log"
$backendErrLog = Join-Path $runtimeLogDir "akane_backend.$safeInstanceId.err.log"

New-Item -ItemType Directory -Force -Path $runtimeLogDir | Out-Null

Write-Host "[INFO] Akane Next one-click launcher"
Write-Host "[INFO] Project: $projectDir"
Write-Host "[INFO] Backend: $resolvedBackendUrl/"

Write-Host "[INFO] Settings center: control-center-lab.html"

if ($OpenSettings) {
    $env:AKANE_OPEN_SETTINGS_ON_START = "1"
    $env:AKANE_OPEN_MODEL_SETTINGS = "1"
} else {
    Remove-Item Env:\AKANE_OPEN_SETTINGS_ON_START -ErrorAction SilentlyContinue
    Remove-Item Env:\AKANE_OPEN_MODEL_SETTINGS -ErrorAction SilentlyContinue
}

if (-not $CloudSatellite -and -not $SkipBackend) {
    if (Test-TcpPort -HostName "127.0.0.1" -Port $BackendPort) {
        $health = Get-BackendHealth -HostName "127.0.0.1" -Port $BackendPort
        $healthPid = Get-BackendListeningProcessId -Port $BackendPort
        $managedProcess = Test-AkaneBackendProcess -ProcessId $healthPid
        $decision = Get-AkaneBackendPortDecision `
            -PortInUse $true `
            -Health $health `
            -ExpectedInstanceId $expectedInstanceId `
            -ReuseBackend ([bool]$ReuseBackend -and -not $generatedSatelliteToken) `
            -ManagedProcess $managedProcess `
            -RestartBackend ([bool]$RestartBackend)
        if ($decision -eq "reuse") {
            Write-Host "[INFO] Matching Akane instance '$expectedInstanceId' is already listening on port $BackendPort. Reusing it."
        } elseif ($decision -eq "stop") {
            Write-Host "[INFO] Backend instance '$expectedInstanceId' is listening on port $BackendPort. Explicit restart requested."
            Stop-AkaneBackendProcess -ProcessId $healthPid -Port $BackendPort
        } else {
            throw "Port $BackendPort is not a stoppable or reusable backend for instance '$expectedInstanceId'."
        }
    }

    if (-not (Test-TcpPort -HostName "127.0.0.1" -Port $BackendPort)) {
        $python = Resolve-Python -ProjectDir $projectDir
        Write-Host "[INFO] Starting backend with: $python"
        Write-Host "[INFO] Backend log: $backendLog"
        $backendProcess = Start-Process `
            -FilePath $python `
            -ArgumentList @("launch_akane_memory_v01.py") `
            -WorkingDirectory $projectDir `
            -WindowStyle Hidden `
            -RedirectStandardOutput $backendLog `
            -RedirectStandardError $backendErrLog `
            -PassThru

        Write-Host "[INFO] Waiting for backend health and instance binding..."
        if (-not (Wait-AkaneBackendReady -Port $BackendPort -ExpectedInstanceId $expectedInstanceId -ProcessId $backendProcess.Id)) {
            Stop-Process -Id $backendProcess.Id -Force -ErrorAction SilentlyContinue
            Write-Host "[INFO] Backend logs:"
            Write-Host "       $backendLog"
            Write-Host "       $backendErrLog"
            throw "Backend did not become healthy for instance '$expectedInstanceId' within 120 seconds."
        }
        Write-Host "[INFO] Backend is healthy and bound to instance '$expectedInstanceId'."
    }
}

if (-not $SkipDesktop -or $DeviceOnly) {
    if ($Dev -and -not $DeviceOnly) {
        Ensure-NpmInstall -DesktopDir $desktopDir
        Push-Location -LiteralPath $desktopDir
        try {
            Write-Host "[INFO] Starting Akane Next in Tauri dev mode..."
            & npm run tauri -- dev
            exit $LASTEXITCODE
        } finally {
            Pop-Location
        }
    }

    New-Item -ItemType Directory -Force -Path $instanceBin | Out-Null
    $existingDesktop = Get-AkaneDesktopProcess -ExePath $instanceExe
    $existingDevice = Get-AkaneDesktopProcess -ExePath $deviceExe
    $externalDeviceConfigured = -not [string]::IsNullOrWhiteSpace([string]$env:AKANE_DESKTOP_SATELLITE_TOKEN)

    if ($null -ne $existingDesktop -and -not $Rebuild -and -not $DeviceOnly) {
        # A desktop started by this launcher owns a separate headless executor.
        # If its process disappeared, the next activation repairs that executor;
        # an older UI without the state marker keeps its original in-process
        # satellite instead of accidentally creating a competing lease.
        $deviceStatePath = Join-Path $dataRoot "run\desktop-device-state.json"
        $deviceStateHasMarker = Test-Path -LiteralPath $deviceStatePath -PathType Leaf
        if ($externalDeviceConfigured -and ($deviceStateHasMarker -or $null -ne $existingDevice)) {
            $deviceSource = if (Test-Path -LiteralPath $releaseExe -PathType Leaf) { $releaseExe } else { $instanceExe }
            Start-AkaneDeviceExecutor `
                -SourceExe $deviceSource `
                -DeviceExe $deviceExe `
                -DesktopDir $desktopDir `
                -DataRoot $dataRoot `
                -ExpectedInstanceId $expectedInstanceId `
                -ReadyTimeoutSeconds 5 `
                -BestEffort | Out-Null
        }
        if (Signal-AkaneDesktopActivation -InstanceId $expectedInstanceId) {
            Write-Host "[INFO] Existing Akane desktop activated for instance '$expectedInstanceId' (PID $($existingDesktop.Id))."
        } else {
            Write-Host "[INFO] Existing Akane desktop is already running for instance '$expectedInstanceId' (PID $($existingDesktop.Id)); no second window will be started."
        }
        Write-Host "[INFO] Done."
        return
    }

    if ($null -ne $existingDevice -and -not $Rebuild -and $DeviceOnly) {
        Wait-AkaneDeviceReady -DataRoot $dataRoot -ExpectedInstanceId $expectedInstanceId -ProcessId $existingDevice.Id
        Write-Host "[INFO] Device executor for instance '$expectedInstanceId' is already running (PID $($existingDevice.Id)). Reusing it."
        Write-Host "[INFO] Done."
        return
    }

    $releaseExists = Test-Path -LiteralPath $releaseExe
    $inputWriteTime = Get-NewestInputWriteTime -DesktopDir $desktopDir
    $releaseWriteTime = if ($releaseExists) { (Get-Item -LiteralPath $releaseExe).LastWriteTime } else { $null }
    $releaseIsStale = $false
    if ($releaseExists -and $null -ne $inputWriteTime -and $releaseWriteTime -lt $inputWriteTime) {
        $releaseIsStale = $true
    }
    # Source timestamps are useful diagnostics, but a normal launch must not
    # turn into a development build. Build on first use or by explicit request.
    $shouldBuild = (-not $releaseExists) -or $Rebuild
    if ($releaseIsStale -and -not $Rebuild) {
        Write-Host "[WARN] Release exe is older than source files; use -Rebuild explicitly when updating the desktop client."
    }

    if ($shouldBuild) {
        if ($NoBuild) {
            if (-not $releaseExists) {
                throw "Release exe not found: $releaseExe. Run without -NoBuild to build it automatically."
            }
            Write-Host "[WARN] Release exe is older than source files, but -NoBuild was set. Launching existing exe."
            $shouldBuild = $false
        }
    }

    if ($shouldBuild) {
        if ($null -ne $existingDesktop -or $null -ne $existingDevice) {
            Write-Host "[INFO] Stopping this instance's desktop processes before rebuilding..."
            Stop-AkaneDesktopProcesses -ExePath $instanceExe
            Stop-AkaneDesktopProcesses -ExePath $deviceExe
        }
        $buildReason = if (-not $releaseExists) {
            "找不到现成的桌宠 exe (首次启动)"
        } elseif ($Rebuild) {
            "调用方指定了 -Rebuild"
        } else {
            "源码比已有 exe 新，需要重建"
        }
        Write-AkaneFirstBuildHints -Reason $buildReason

        Ensure-NpmInstall -DesktopDir $desktopDir
        Push-Location -LiteralPath $desktopDir
        try {
            if (-not $releaseExists) {
                Write-Host "[INFO] Release exe not found. Building Akane Next..."
            } elseif ($Rebuild) {
                Write-Host "[INFO] Rebuilding Akane Next release exe..."
            } else {
                Write-Host "[INFO] Release exe is older than source files. Rebuilding Akane Next..."
            }
            & npm run tauri -- build
            if ($LASTEXITCODE -ne 0) {
                throw "Tauri build failed with exit code $LASTEXITCODE"
            }
            Write-Host "[INFO] Tauri build finished. Launching the desktop pet next..."
        } finally {
            Pop-Location
        }
    }

    # Each instance owns executable copies. The headless device executor is
    # started before the UI and the UI is told not to claim a second lease.
    if ($DeviceOnly) {
        if (-not $externalDeviceConfigured) {
            throw "device_executor_token_required"
        }
        Start-AkaneDeviceExecutor `
            -SourceExe $releaseExe `
            -DeviceExe $deviceExe `
            -DesktopDir $desktopDir `
            -DataRoot $dataRoot `
            -ExpectedInstanceId $expectedInstanceId | Out-Null
        Write-Host "[INFO] Device-only executor is running for instance '$expectedInstanceId'."
    } else {
        if ($externalDeviceConfigured) {
            Start-AkaneDeviceExecutor `
                -SourceExe $releaseExe `
                -DeviceExe $deviceExe `
                -DesktopDir $desktopDir `
                -DataRoot $dataRoot `
                -ExpectedInstanceId $expectedInstanceId `
                -ReadyTimeoutSeconds 5 `
                -BestEffort | Out-Null
            $env:AKANE_DESKTOP_EXECUTOR_EXTERNAL = "1"
        } else {
            Remove-Item Env:\AKANE_DESKTOP_EXECUTOR_EXTERNAL -ErrorAction SilentlyContinue
        }

        Stop-AkaneDesktopProcesses -ExePath $instanceExe
        $uiStatePath = Join-Path $dataRoot "run\desktop-ui-state.json"
        Remove-Item -LiteralPath $uiStatePath -Force -ErrorAction SilentlyContinue
        Copy-AkaneInstanceExecutable -SourcePath $releaseExe -DestinationPath $instanceExe
        Write-Host "[INFO] Starting Akane Next for instance '$expectedInstanceId'..."
        $desktopStart = @{ FilePath = $instanceExe; WorkingDirectory = $desktopDir; PassThru = $true }
        $desktopProcess = Start-Process @desktopStart
        Write-Host "[INFO] Akane Next PID: $($desktopProcess.Id)"
        Write-Host "[INFO] Exe: $instanceExe"
        Wait-AkaneDesktopReady -DataRoot $dataRoot -ExpectedInstanceId $expectedInstanceId -ProcessId $desktopProcess.Id
    }
}

Write-Host "[INFO] Done."
