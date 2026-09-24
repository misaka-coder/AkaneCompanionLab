# Restart the local-qq-test instance (backend + desktop) without depending on the caller's process tree.
# Launched by a one-shot scheduled task so it survives the backend it is about to replace.
param(
    [int]$DelaySeconds = 40,
    [int]$HealthWaitSeconds = 120
)

$ErrorActionPreference = 'Continue'
$project = Split-Path -Parent $PSScriptRoot
$localAppData = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HOME 'AppData\Local' }
$root = Join-Path $localAppData 'Akane\local-qq-test-runtime'
$instanceId = 'local-qq-test'
$port = 12001
$taskName = 'AkaneLocalQQTestRestart'
$log = Join-Path $root 'run\restart-local-qq-test.log'

function Write-RestartLog {
    param([string]$Message)
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Message" | Add-Content -LiteralPath $log -Encoding UTF8
}

function Import-EnvFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return }
    foreach ($line in (Get-Content -LiteralPath $Path -Encoding UTF8)) {
        $text = $line.Trim()
        if (-not $text -or $text.StartsWith('#')) { continue }
        $index = $text.IndexOf('=')
        if ($index -lt 1) { continue }
        $key = $text.Substring(0, $index).Trim()
        $value = $text.Substring($index + 1).Trim()
        if ($value.Length -ge 2) {
            $first = $value[0]
            if (($first -eq '"' -and $value[-1] -eq '"') -or ($first -eq "'" -and $value[-1] -eq "'")) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }
        Set-Item -Path ("Env:" + $key) -Value $value
    }
}

function Get-BackendHealth {
    try {
        return Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 3
    } catch {
        return $null
    }
}

function Wait-BackendHealth {
    param([int]$Seconds)
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        $health = Get-BackendHealth
        if ($null -ne $health -and [string]$health.instance_id -eq $instanceId) { return $health }
        Start-Sleep -Seconds 2
    }
    return $null
}

function Stop-ProcessesUnderRoot {
    param([string[]]$Names)
    foreach ($name in $Names) {
        foreach ($process in @(Get-Process -Name $name -ErrorAction SilentlyContinue)) {
            try {
                $path = [System.IO.Path]::GetFullPath([string]$process.Path)
            } catch {
                continue
            }
            if ($path.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
                Write-RestartLog "stopping $name pid=$($process.Id)"
                Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
                Wait-Process -Id $process.Id -Timeout 8 -ErrorAction SilentlyContinue
            }
        }
    }
}

function Stop-BackendListener {
    foreach ($connection in @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) {
        $listenerId = [int]$connection.OwningProcess
        if ($listenerId -gt 0) {
            Write-RestartLog "stopping backend listener pid=$listenerId"
            Stop-Process -Id $listenerId -Force -ErrorAction SilentlyContinue
            Wait-Process -Id $listenerId -Timeout 10 -ErrorAction SilentlyContinue
        }
    }
}

function Start-BackendDirect {
    $python = Join-Path $project '.venv\Scripts\python.exe'
    $outLog = Join-Path $root 'logs\akane_backend.local-qq-test.log'
    $errLog = Join-Path $root 'logs\akane_backend.local-qq-test.err.log'
    $process = Start-Process -FilePath $python -ArgumentList @('launch_akane_memory_v01.py') `
        -WorkingDirectory $project -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog
    Write-RestartLog "direct backend launch pid=$($process.Id)"
    return $process
}

Write-RestartLog '=== restart requested ==='
schtasks /Delete /TN $taskName /F 2>$null | Out-Null

Start-Sleep -Seconds $DelaySeconds

foreach ($envFile in @(
    (Join-Path $project '.env'),
    (Join-Path $root 'config\canonical-launch.env'),
    (Join-Path $root 'config\local-qq-secrets.env'),
    (Join-Path $localAppData 'Akane\local-test-runtime\config\cloud-aligned.env')
)) {
    Import-EnvFile -Path $envFile
    Write-RestartLog "env imported: $envFile"
}

# Environment the launcher normally guarantees. Children inherit it either way.
$env:AKANE_ENV_FILE = Join-Path $project '.env'
$env:AKANE_INSTANCE_ID = $instanceId
$env:AKANE_DATA_ROOT = $root
$env:COMPANION_PORT = "$port"
$env:COMPANION_HOST = '127.0.0.1'
$env:HOST = '127.0.0.1'
$env:QQ_BRIDGE_ENABLED = 'true'
$env:EXECUTION_QQ_ENABLED = 'true'
$env:BROWSER_PAGE_PRIVATE_NETWORK_ACCESS = 'true'
$env:QQ_CHANNEL_PROFILE_REF = $instanceId
$env:QQ_CHARACTER_PACK_ID = 'reimu'
$env:AKANE_MANAGED_STOP_BACKEND = '1'
$env:AKANE_MANAGED_LAUNCHER = Join-Path $project 'start_akane_next.ps1'
Remove-Item Env:\AKANE_BACKEND_URL -ErrorAction SilentlyContinue

# The desktop must be gone first: the running exe holds a file lock, and the
# launcher would otherwise just re-activate the old window instead of swapping it.
Stop-ProcessesUnderRoot -Names @('akane_desktop_pet_next', 'akane_desktop_pet_next_device')
Start-Sleep -Seconds 2

# Ship the freshly built desktop client before the launcher starts anything, so the
# window the launcher opens is already the new build.
$releaseExe = Join-Path $project 'desktop_pet_next\src-tauri\target\release\akane_desktop_pet_next.exe'
$binExe = Join-Path $root 'bin\akane_desktop_pet_next.exe'
if ((Test-Path -LiteralPath $releaseExe) -and (Test-Path -LiteralPath $binExe)) {
    $releaseHash = (Get-FileHash -LiteralPath $releaseExe -Algorithm SHA256).Hash
    $binHash = (Get-FileHash -LiteralPath $binExe -Algorithm SHA256).Hash
    if ($releaseHash -ne $binHash) {
        Copy-Item -LiteralPath $binExe -Destination "$binExe.prev" -Force
        Copy-Item -LiteralPath $releaseExe -Destination $binExe -Force
        Write-RestartLog "desktop exe updated: $releaseHash"
    } else {
        Write-RestartLog 'desktop exe already current'
    }
}

$launcherLog = Join-Path $root 'run\restart-launcher.out.log'
$launcherErrLog = Join-Path $root 'run\restart-launcher.err.log'
$launcherExit = $null
try {
    $pwshPath = (Get-Command pwsh -ErrorAction Stop).Source
    $launcher = Start-Process -FilePath $pwshPath -ArgumentList @(
        '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', (Join-Path $project 'start_akane_next.ps1'),
        '-Action', 'Restart', '-InstanceId', $instanceId,
        '-DataRoot', $root, '-BackendPort', "$port"
    ) -WorkingDirectory $project -PassThru `
        -RedirectStandardOutput $launcherLog -RedirectStandardError $launcherErrLog
    $launcher | Wait-Process -Timeout 420 -ErrorAction SilentlyContinue
    $launcherExit = $launcher.ExitCode
    Write-RestartLog "launcher finished: exit=$launcherExit"
} catch {
    Write-RestartLog "launcher could not start: $($_.Exception.Message)"
}

$health = Wait-BackendHealth -Seconds $HealthWaitSeconds

if ($null -eq $health) {
    Write-RestartLog 'backend unhealthy after launcher; falling back to a direct host launch'
    Stop-BackendListener
    Start-Sleep -Seconds 2
    $null = Start-BackendDirect
    $health = Wait-BackendHealth -Seconds $HealthWaitSeconds
}

if ($null -ne $health) {
    Write-RestartLog "backend healthy: instance=$($health.instance_id)"
} else {
    Write-RestartLog 'FATAL: backend did not become healthy'
}

# Make sure the desktop window is up with the freshly copied exe.
if (-not (Get-Process -Name 'akane_desktop_pet_next' -ErrorAction SilentlyContinue)) {
    $binExe = Join-Path $root 'bin\akane_desktop_pet_next.exe'
    $releaseExe = Join-Path $project 'desktop_pet_next\src-tauri\target\release\akane_desktop_pet_next.exe'
    if ((Test-Path -LiteralPath $releaseExe) -and (Test-Path -LiteralPath $binExe)) {
        if ((Get-FileHash -LiteralPath $releaseExe).Hash -ne (Get-FileHash -LiteralPath $binExe).Hash) {
            Copy-Item -LiteralPath $releaseExe -Destination $binExe -Force
            Write-RestartLog 'desktop exe refreshed from release build'
        }
    }
    Start-Process -FilePath $binExe -WorkingDirectory (Split-Path -Parent $binExe) | Out-Null
    Write-RestartLog 'desktop window started'
}

$uiState = Join-Path $root 'run\desktop-ui-state.json'
if (Test-Path -LiteralPath $uiState) {
    Write-RestartLog ("desktop ui state: " + ((Get-Content -LiteralPath $uiState -Raw -Encoding UTF8) -replace '\s+', ' '))
}
Write-RestartLog '=== restart finished ==='
schtasks /Delete /TN $taskName /F 2>$null | Out-Null
