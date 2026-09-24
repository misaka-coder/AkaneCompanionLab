[CmdletBinding()]
param(
    [string]$RuntimeDir = "",
    [string]$RuntimeExe = "",
    [switch]$All,
    [switch]$Force,
    [switch]$CheckOnly,
    [int]$TimeoutSeconds = 5
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-AkanePetdeskStopStep {
    param(
        [string]$Level,
        [string]$Message
    )

    Write-Host ("[{0}] {1}" -f $Level, $Message)
}

function Find-AkaneProjectRoot {
    param([string]$StartDir)

    $current = (Resolve-Path -LiteralPath $StartDir).Path
    while ($current) {
        if (
            (Test-Path -LiteralPath (Join-Path $current "launch_akane_memory_v01.py") -PathType Leaf) -and
            (Test-Path -LiteralPath (Join-Path $current "start_akane_next.ps1") -PathType Leaf)
        ) {
            return $current
        }

        $parent = Split-Path -Parent $current
        if (-not $parent -or $parent -eq $current) {
            break
        }
        $current = $parent
    }

    throw "akane_project_root_not_found"
}

function Resolve-PetdeskRuntimeDir {
    param(
        [string]$ProjectRoot,
        [string]$RequestedRuntimeDir
    )

    $configuredRuntimeDir = if ($RequestedRuntimeDir) {
        $RequestedRuntimeDir
    } elseif ($env:PETDESK_RUNTIME_ROOT) {
        $env:PETDESK_RUNTIME_ROOT
    } else {
        throw "petdesk_runtime_dir_required: pass -RuntimeDir or set PETDESK_RUNTIME_ROOT"
    }

    $candidate = if ([System.IO.Path]::IsPathRooted($configuredRuntimeDir)) {
            $configuredRuntimeDir
        } else {
            Join-Path $ProjectRoot $configuredRuntimeDir
        }

    return [System.IO.Path]::GetFullPath($candidate)
}

function Resolve-PetdeskRuntimeReleaseExePath {
    param([string]$ResolvedRuntimeDir)

    $cargoConfig = Join-Path $ResolvedRuntimeDir ".cargo\config.toml"
    if (Test-Path -LiteralPath $cargoConfig -PathType Leaf) {
        $configText = Get-Content -LiteralPath $cargoConfig -Raw
        if ($configText -match '(?m)^\s*target-dir\s*=\s*"([^"]+)"') {
            $targetDir = $Matches[1]
            if (-not [System.IO.Path]::IsPathRooted($targetDir)) {
                $targetDir = Join-Path $ResolvedRuntimeDir $targetDir
            }
            return [System.IO.Path]::GetFullPath((Join-Path $targetDir "release\petdesk_runtime.exe"))
        }
    }

    return [System.IO.Path]::GetFullPath((Join-Path $ResolvedRuntimeDir "src-tauri\target\release\petdesk_runtime.exe"))
}

function Resolve-TargetRuntimeExe {
    param(
        [string]$ProjectRoot,
        [string]$ResolvedRuntimeDir,
        [string]$RequestedRuntimeExe
    )

    if ($RequestedRuntimeExe) {
        $candidate = if ([System.IO.Path]::IsPathRooted($RequestedRuntimeExe)) {
            $RequestedRuntimeExe
        } else {
            Join-Path $ProjectRoot $RequestedRuntimeExe
        }
        return [System.IO.Path]::GetFullPath($candidate)
    }

    return Resolve-PetdeskRuntimeReleaseExePath -ResolvedRuntimeDir $ResolvedRuntimeDir
}

function Get-ProcessPathSafe {
    param([System.Diagnostics.Process]$Process)

    try {
        return $Process.Path
    } catch {
        return ""
    }
}

$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $scriptDir) {
    $scriptDir = (Get-Location).Path
}

$projectRoot = Find-AkaneProjectRoot -StartDir $scriptDir
$resolvedRuntimeDir = Resolve-PetdeskRuntimeDir -ProjectRoot $projectRoot -RequestedRuntimeDir $RuntimeDir
$targetRuntimeExe = Resolve-TargetRuntimeExe `
    -ProjectRoot $projectRoot `
    -ResolvedRuntimeDir $resolvedRuntimeDir `
    -RequestedRuntimeExe $RuntimeExe

Write-AkanePetdeskStopStep "INFO" ("Akane project: {0}" -f $projectRoot)
Write-AkanePetdeskStopStep "INFO" ("petdesk-runtime: {0}" -f $resolvedRuntimeDir)
if (-not $All) {
    Write-AkanePetdeskStopStep "INFO" ("Target release exe: {0}" -f $targetRuntimeExe)
} else {
    Write-AkanePetdeskStopStep "WARN" "Matching all petdesk_runtime.exe processes because -All was passed."
}

$allProcesses = @(Get-Process -Name petdesk_runtime -ErrorAction SilentlyContinue)
$matchedProcesses = @()
foreach ($process in $allProcesses) {
    $path = Get-ProcessPathSafe -Process $process
    if ($All -or ([string]::Equals($path, $targetRuntimeExe, [System.StringComparison]::OrdinalIgnoreCase))) {
        $matchedProcesses += $process
    }
}

if ($matchedProcesses.Count -eq 0) {
    Write-AkanePetdeskStopStep "OK" "No matching petdesk runtime process is running."
    exit 0
}

foreach ($process in $matchedProcesses) {
    $path = Get-ProcessPathSafe -Process $process
    Write-AkanePetdeskStopStep "INFO" ("Matched petdesk runtime PID {0}: {1}" -f $process.Id, $path)
}

if ($CheckOnly) {
    Write-AkanePetdeskStopStep "OK" "CheckOnly completed. No petdesk runtime process was stopped."
    exit 0
}

$deadlineMs = [Math]::Max(0, $TimeoutSeconds) * 1000
foreach ($process in $matchedProcesses) {
    if ($process.HasExited) {
        continue
    }

    $closed = $false
    if ($process.MainWindowHandle -ne [IntPtr]::Zero) {
        $closed = $process.CloseMainWindow()
    }

    if ($closed) {
        Write-AkanePetdeskStopStep "INFO" ("Sent close request to petdesk runtime PID {0}." -f $process.Id)
        $null = $process.WaitForExit($deadlineMs)
    } else {
        Write-AkanePetdeskStopStep "WARN" ("Petdesk runtime PID {0} has no closable main window." -f $process.Id)
    }

    if (-not $process.HasExited) {
        if ($Force) {
            Stop-Process -Id $process.Id -Force
            Write-AkanePetdeskStopStep "OK" ("Force-stopped petdesk runtime PID {0}." -f $process.Id)
        } else {
            Write-AkanePetdeskStopStep "WARN" ("Petdesk runtime PID {0} is still running. Re-run with -Force if needed." -f $process.Id)
        }
    } else {
        Write-AkanePetdeskStopStep "OK" ("Stopped petdesk runtime PID {0}." -f $process.Id)
    }
}
