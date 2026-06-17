param(
    [int]$BackendPort = 9999,
    [switch]$SkipBackend,
    [switch]$ReuseBackend,
    [switch]$SkipDesktop,
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

function Test-AkaneBackendHealth {
    param([object]$Health)

    if ($null -eq $Health) {
        return $false
    }

    $status = [string]($Health.status)
    $pidValue = 0
    try {
        $pidValue = [int]($Health.pid)
    } catch {
        $pidValue = 0
    }

    $contracts = $Health.contracts
    $desktopPetContract = if ($null -ne $contracts) { $contracts.desktop_pet } else { $null }
    return ($status -eq "ok" -and $pidValue -gt 0 -and $null -ne $desktopPetContract)
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

    $nodeModules = Join-Path $DesktopDir "node_modules"
    if (Test-Path -LiteralPath $nodeModules) {
        return
    }

    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npm) {
        throw "npm was not found. Install Node.js before building Akane Next."
    }

    Write-Host "[INFO] desktop_pet_next/node_modules not found. Running npm install..."
    Push-Location -LiteralPath $DesktopDir
    try {
        & npm install
        if ($LASTEXITCODE -ne 0) {
            throw "npm install failed with exit code $LASTEXITCODE"
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
if ([string]$env:AKANE_DATA_ROOT_READY -eq "1" -and [string]$env:AKANE_DATA_ROOT) {
    $dataRoot = [System.IO.Path]::GetFullPath([string]$env:AKANE_DATA_ROOT)
} else {
    $dataStatus = Initialize-AkaneDataRoot -ProjectRoot $projectDir
    $dataRoot = $dataStatus.Root
    $env:AKANE_DATA_ROOT_READY = "1"
    if ($dataStatus.Failed -gt 0) {
        Write-Host "[WARN] User data root is ready, but $($dataStatus.Failed) legacy files could not be copied."
    } elseif ($dataStatus.Copied -gt 0) {
        Write-Host "[INFO] Migrated $($dataStatus.Copied) legacy files without overwriting existing data."
    }
}
$env:AKANE_DATA_ROOT = $dataRoot
$runtimeLogDir = Join-Path $dataRoot "logs"
$backendLog = Join-Path $runtimeLogDir "akane_backend.log"
$backendErrLog = Join-Path $runtimeLogDir "akane_backend.err.log"

New-Item -ItemType Directory -Force -Path $runtimeLogDir | Out-Null

Write-Host "[INFO] Akane Next one-click launcher"
Write-Host "[INFO] Project: $projectDir"
Write-Host "[INFO] Backend: http://127.0.0.1:$BackendPort/"

Write-Host "[INFO] Settings center: control-center-lab.html"

if ($OpenSettings) {
    $env:AKANE_OPEN_SETTINGS_ON_START = "1"
    $env:AKANE_OPEN_MODEL_SETTINGS = "1"
} else {
    Remove-Item Env:\AKANE_OPEN_SETTINGS_ON_START -ErrorAction SilentlyContinue
    Remove-Item Env:\AKANE_OPEN_MODEL_SETTINGS -ErrorAction SilentlyContinue
}

if (-not $SkipBackend) {
    if (Test-TcpPort -HostName "127.0.0.1" -Port $BackendPort) {
        if ($ReuseBackend) {
            Write-Host "[INFO] Backend already listening on port $BackendPort. Reusing it because -ReuseBackend was set."
        } else {
            $health = Get-BackendHealth -HostName "127.0.0.1" -Port $BackendPort
            $healthPid = 0
            try {
                $healthPid = [int]($health.pid)
            } catch {
                $healthPid = 0
            }

            if ((Test-AkaneBackendHealth -Health $health) -and (Test-AkaneBackendProcess -ProcessId $healthPid)) {
                Write-Host "[INFO] Backend already listening on port $BackendPort. Restarting Akane backend for fresh code."
                Stop-AkaneBackendProcess -ProcessId $healthPid -Port $BackendPort
            } else {
                Write-Host "[WARN] Port $BackendPort is already in use, but it was not recognized as a managed Akane backend."
                Write-Host "[WARN] Keeping the existing service. Use -SkipBackend or free the port if this is unexpected."
            }
        }
    }

    if (-not (Test-TcpPort -HostName "127.0.0.1" -Port $BackendPort)) {
        $python = Resolve-Python -ProjectDir $projectDir
        $env:COMPANION_PORT = "$BackendPort"
        Write-Host "[INFO] Starting backend with: $python"
        Write-Host "[INFO] Backend log: $backendLog"
        Start-Process `
            -FilePath $python `
            -ArgumentList @("launch_akane_memory_v01.py") `
            -WorkingDirectory $projectDir `
            -WindowStyle Hidden `
            -RedirectStandardOutput $backendLog `
            -RedirectStandardError $backendErrLog | Out-Null

        $ready = $false
        for ($i = 0; $i -lt 8; $i++) {
            Start-Sleep -Milliseconds 250
            if (Test-TcpPort -HostName "127.0.0.1" -Port $BackendPort) {
                $ready = $true
                break
            }
        }

        if ($ready) {
            Write-Host "[INFO] Backend is already accepting connections."
        } else {
            Write-Host "[INFO] Backend is still warming up; launching the desktop pet first."
            Write-Host "[INFO] 后端服务仍在启动 (首次启动可能需要几十秒加载配置)。"
            Write-Host "[INFO] 桌宠会先打开，后端就绪后会自动连接，无需手动操作；如果几分钟后仍未连接，再查看下方日志。"
            Write-Host "[INFO] Backend logs:"
            Write-Host "       $backendLog"
            Write-Host "       $backendErrLog"
        }
    }
}

if (-not $SkipDesktop) {
    if ($Dev) {
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

    $releaseExists = Test-Path -LiteralPath $releaseExe
    $inputWriteTime = Get-NewestInputWriteTime -DesktopDir $desktopDir
    $releaseWriteTime = if ($releaseExists) { (Get-Item -LiteralPath $releaseExe).LastWriteTime } else { $null }
    $releaseIsStale = $false
    if ($releaseExists -and $null -ne $inputWriteTime -and $releaseWriteTime -lt $inputWriteTime) {
        $releaseIsStale = $true
    }
    $shouldBuild = (-not $releaseExists) -or $releaseIsStale -or $Rebuild

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

    Stop-AkaneDesktopProcesses -ExePath $releaseExe
    Write-Host "[INFO] Starting Akane Next desktop app..."
    $desktopProcess = Start-Process -FilePath $releaseExe -WorkingDirectory $desktopDir -PassThru
    Write-Host "[INFO] Akane Next PID: $($desktopProcess.Id)"
    Write-Host "[INFO] Exe: $releaseExe"
}

Write-Host "[INFO] Done."
