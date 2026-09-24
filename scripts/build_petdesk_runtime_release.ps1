[CmdletBinding()]
param(
    [string]$RuntimeDir = "",
    [switch]$CheckOnly,
    [switch]$DryRun,
    [switch]$SkipWarmup
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-AkanePetdeskBuildStep {
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

    $resolved = [System.IO.Path]::GetFullPath($candidate)
    $packageJson = Join-Path $resolved "package.json"
    $cargoManifest = Join-Path $resolved "src-tauri\Cargo.toml"
    if (-not (Test-Path -LiteralPath $packageJson -PathType Leaf)) {
        throw "petdesk_runtime_package_not_found: $packageJson"
    }
    if (-not (Test-Path -LiteralPath $cargoManifest -PathType Leaf)) {
        throw "petdesk_runtime_cargo_manifest_not_found: $cargoManifest"
    }
    return $resolved
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

function Resolve-RequiredCommand {
    param([string]$Name)

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $command) {
        throw ("{0}_not_found" -f $Name)
    }
    return $command.Source
}

function Invoke-StepCommand {
    param(
        [string]$Label,
        [string]$CommandPath,
        [string[]]$Arguments
    )

    Write-AkanePetdeskBuildStep "INFO" ("Running {0}..." -f $Label)
    & $CommandPath @Arguments
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw ("{0}_failed: exit {1}" -f $Label, $exitCode)
    }
}

$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $scriptDir) {
    $scriptDir = (Get-Location).Path
}

$projectRoot = Find-AkaneProjectRoot -StartDir $scriptDir
$resolvedRuntimeDir = Resolve-PetdeskRuntimeDir -ProjectRoot $projectRoot -RequestedRuntimeDir $RuntimeDir
$cargoManifest = Join-Path $resolvedRuntimeDir "src-tauri\Cargo.toml"
$releaseExe = Resolve-PetdeskRuntimeReleaseExePath -ResolvedRuntimeDir $resolvedRuntimeDir
$cargo = Resolve-RequiredCommand -Name "cargo"
$pnpm = Resolve-RequiredCommand -Name "pnpm"

Write-AkanePetdeskBuildStep "INFO" ("Akane project: {0}" -f $projectRoot)
Write-AkanePetdeskBuildStep "INFO" ("petdesk-runtime: {0}" -f $resolvedRuntimeDir)
Write-AkanePetdeskBuildStep "INFO" ("Cargo manifest: {0}" -f $cargoManifest)
Write-AkanePetdeskBuildStep "INFO" ("Expected release exe: {0}" -f $releaseExe)

if ($CheckOnly) {
    Write-AkanePetdeskBuildStep "OK" "CheckOnly completed. No release build was run."
    exit 0
}

$warmupArguments = @("build", "--manifest-path", $cargoManifest, "--release", "-j", "1")
$tauriBuildArguments = @("tauri:build")

if ($DryRun) {
    if (-not $SkipWarmup) {
        Write-AkanePetdeskBuildStep "INFO" ("Would run: {0} {1}" -f $cargo, ($warmupArguments -join " "))
    } else {
        Write-AkanePetdeskBuildStep "INFO" "Would skip serial Cargo warm-up."
    }
    Write-AkanePetdeskBuildStep "INFO" ("Would run in {0}: {1} {2}" -f $resolvedRuntimeDir, $pnpm, ($tauriBuildArguments -join " "))
    Write-AkanePetdeskBuildStep "OK" "DryRun completed. No release build was run."
    exit 0
}

if (-not $SkipWarmup) {
    Invoke-StepCommand `
        -Label "petdesk-runtime serial cargo release warm-up" `
        -CommandPath $cargo `
        -Arguments $warmupArguments
} else {
    Write-AkanePetdeskBuildStep "WARN" "Skipping serial Cargo warm-up."
}

Push-Location -LiteralPath $resolvedRuntimeDir
try {
    Invoke-StepCommand `
        -Label "petdesk-runtime Tauri release build" `
        -CommandPath $pnpm `
        -Arguments $tauriBuildArguments
} finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath $releaseExe -PathType Leaf)) {
    throw "petdesk_runtime_release_exe_missing_after_build: $releaseExe"
}

$exe = Get-Item -LiteralPath $releaseExe
Write-AkanePetdeskBuildStep "OK" (
    "petdesk-runtime release build completed: {0} ({1} bytes, {2})" -f
    $exe.FullName,
    $exe.Length,
    $exe.LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss")
)
