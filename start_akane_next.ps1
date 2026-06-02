param(
    [int]$BackendPort = 9999,
    [switch]$SkipBackend,
    [switch]$SkipDesktop,
    [switch]$NoBuild,
    [switch]$Dev,
    [switch]$ControlCenterLab,
    [switch]$LegacySettings,
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
        (Join-Path $DesktopDir "src-tauri\tauri.conf.json"),
        (Join-Path $DesktopDir "package.json"),
        (Join-Path $DesktopDir "package-lock.json"),
        (Join-Path $DesktopDir "vite.config.js"),
        (Join-Path $DesktopDir "index.html"),
        (Join-Path $DesktopDir "settings.html"),
        (Join-Path $DesktopDir "workspace.html"),
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
$runtimeLogDir = Join-Path $projectDir "runtime_logs"
$backendLog = Join-Path $runtimeLogDir "akane_backend.log"
$backendErrLog = Join-Path $runtimeLogDir "akane_backend.err.log"

New-Item -ItemType Directory -Force -Path $runtimeLogDir | Out-Null

Write-Host "[INFO] Akane Next one-click launcher"
Write-Host "[INFO] Project: $projectDir"
Write-Host "[INFO] Backend: http://127.0.0.1:$BackendPort/"

if (-not $LegacySettings) {
    $env:AKANE_CONTROL_CENTER_LAB = "1"
    Write-Host "[INFO] Settings center: control-center-lab.html"
} else {
    Remove-Item Env:\AKANE_CONTROL_CENTER_LAB -ErrorAction SilentlyContinue
    Write-Host "[INFO] Settings center: legacy settings.html"
}

if (-not $SkipBackend) {
    if (Test-TcpPort -HostName "127.0.0.1" -Port $BackendPort) {
        Write-Host "[INFO] Backend already listening on port $BackendPort. Reusing it."
    } else {
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
        for ($i = 0; $i -lt 180; $i++) {
            Start-Sleep -Milliseconds 500
            if (Test-TcpPort -HostName "127.0.0.1" -Port $BackendPort) {
                $ready = $true
                break
            }
        }

        if ($ready) {
            Write-Host "[INFO] Backend is ready."
        } else {
            Write-Host "[WARN] Backend did not respond within 90 seconds. Check logs:"
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
            if ($ControlCenterLab -or -not $LegacySettings) {
                $env:AKANE_CONTROL_CENTER_LAB = "1"
            }
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
        } finally {
            Pop-Location
        }
    }

    Write-Host "[INFO] Starting Akane Next desktop app..."
    $desktopProcess = Start-Process -FilePath $releaseExe -WorkingDirectory $desktopDir -PassThru
    Write-Host "[INFO] Akane Next PID: $($desktopProcess.Id)"
    Write-Host "[INFO] Exe: $releaseExe"
}

Write-Host "[INFO] Done."
