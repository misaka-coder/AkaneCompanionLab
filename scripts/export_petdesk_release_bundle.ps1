[CmdletBinding()]
param(
    [string]$OutputPath = "",
    [string]$RuntimeDir = "",
    [string]$RuntimeExe = "",
    [switch]$CheckOnly,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-AkanePetdeskBundleExportStep {
    param(
        [string]$Level,
        [string]$Message
    )

    Write-Host ("[{0}] {1}" -f $Level, $Message)
}

function ConvertTo-FullPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    return [System.IO.Path]::GetFullPath(
        [Environment]::ExpandEnvironmentVariables($Path)
    ).TrimEnd(
        [char[]]@(
            [System.IO.Path]::DirectorySeparatorChar,
            [System.IO.Path]::AltDirectorySeparatorChar
        )
    )
}

function Test-PathWithin {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root
    )

    if ($Path.Equals($Root, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }
    $prefix = $Root + [System.IO.Path]::DirectorySeparatorChar
    return $Path.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
}

function Find-AkaneProjectRoot {
    param([string]$StartDir)

    $current = (Resolve-Path -LiteralPath $StartDir).Path
    while ($current) {
        if (
            (Test-Path -LiteralPath (Join-Path $current "launch_akane_memory_v01.py") -PathType Leaf) -and
            (Test-Path -LiteralPath (Join-Path $current "companion_v01") -PathType Container)
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

function Copy-RequiredFile {
    param(
        [Parameter(Mandatory = $true)][string]$SourcePath,
        [Parameter(Mandatory = $true)][string]$DestinationPath
    )

    if (-not (Test-Path -LiteralPath $SourcePath -PathType Leaf)) {
        throw "bundle_source_file_missing: $SourcePath"
    }
    $destinationDirectory = Split-Path -Parent $DestinationPath
    if (-not (Test-Path -LiteralPath $destinationDirectory -PathType Container)) {
        $null = New-Item -ItemType Directory -Path $destinationDirectory
    }
    Copy-Item -LiteralPath $SourcePath -Destination $DestinationPath
}

function New-BundleReadmeText {
    return @'
# Akane Petdesk Runtime Bundle

This bundle contains the built `petdesk_runtime.exe` and a source-independent
starter script for acceptance work.

It is not a full Akane installer. Start the Akane backend separately, then run:

```powershell
.\scripts\start_petdesk_runtime_bundle.ps1 -BackendUrl http://127.0.0.1:9999
```

Useful checks:

```powershell
.\scripts\start_petdesk_runtime_bundle.ps1 -CheckOnly
.\scripts\start_petdesk_runtime_bundle.ps1 -DryRun -BackendUrl http://127.0.0.1:9999
.\scripts\audit_petdesk_release_bundle.ps1
```

The starter fetches `/pet/health`, whitelists petdesk runtime environment
values, and starts `runtime/petdesk_runtime.exe`. It does not start the backend,
stop processes, build source code, or touch external chat adapters.

See `docs/petdesk_operator_guide_m54.md` for the source-tree operator guide.
'@
}

function New-BundleManifest {
    param(
        [Parameter(Mandatory = $true)][string]$BundleRoot,
        [Parameter(Mandatory = $true)][string]$SourceCommit
    )

    $files = @(
        Get-ChildItem -LiteralPath $BundleRoot -File -Recurse |
            Sort-Object FullName |
            ForEach-Object {
                $relative = $_.FullName.Substring($BundleRoot.Length).TrimStart("\", "/").Replace("\", "/")
                [ordered]@{
                    path   = $relative
                    bytes  = $_.Length
                    sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash
                }
            }
    )

    return [ordered]@{
        schema        = "akane.petdesk.releaseBundle.v1"
        createdAtUtc  = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
        sourceCommit  = $SourceCommit
        runtimeExe    = "runtime/petdesk_runtime.exe"
        startScript   = "scripts/start_petdesk_runtime_bundle.ps1"
        files         = $files
    }
}

$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $scriptDir) {
    $scriptDir = (Get-Location).Path
}

$projectRoot = Find-AkaneProjectRoot -StartDir $scriptDir
$resolvedRuntimeDir = Resolve-PetdeskRuntimeDir -ProjectRoot $projectRoot -RequestedRuntimeDir $RuntimeDir
$releaseExe = Resolve-TargetRuntimeExe `
    -ProjectRoot $projectRoot `
    -ResolvedRuntimeDir $resolvedRuntimeDir `
    -RequestedRuntimeExe $RuntimeExe

if (-not $OutputPath) {
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $OutputPath = Join-Path $projectRoot ("reports\petdesk-release-bundles\petdesk-release-{0}" -f $timestamp)
}

$outputRoot = ConvertTo-FullPath $OutputPath
$projectRootFull = ConvertTo-FullPath $projectRoot
$driveRoot = ConvertTo-FullPath ([System.IO.Path]::GetPathRoot($outputRoot))
$userHome = ConvertTo-FullPath ([Environment]::GetFolderPath("UserProfile"))

if (
    $outputRoot.Equals($projectRootFull, [System.StringComparison]::OrdinalIgnoreCase) -or
    (Test-PathWithin -Path $projectRootFull -Root $outputRoot) -or
    $outputRoot.Equals($driveRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
    $outputRoot.Equals($userHome, [System.StringComparison]::OrdinalIgnoreCase)
) {
    throw "unsafe_bundle_output_path"
}
if (Test-Path -LiteralPath $outputRoot) {
    throw "bundle_output_path_already_exists: $outputRoot"
}

Write-AkanePetdeskBundleExportStep "INFO" ("Akane project: {0}" -f $projectRootFull)
Write-AkanePetdeskBundleExportStep "INFO" ("petdesk-runtime: {0}" -f $resolvedRuntimeDir)
Write-AkanePetdeskBundleExportStep "INFO" ("Release exe: {0}" -f $releaseExe)
Write-AkanePetdeskBundleExportStep "INFO" ("Output: {0}" -f $outputRoot)

$requiredSourceFiles = [ordered]@{
    "runtime/petdesk_runtime.exe" = $releaseExe
    "scripts/start_petdesk_runtime_bundle.ps1" = (Join-Path $projectRoot "scripts\start_petdesk_runtime_bundle.ps1")
    "docs/petdesk_operator_guide_m54.md" = (Join-Path $projectRoot "docs\petdesk_operator_guide_m54.md")
    "docs/petdesk_release_doctor_m56.md" = (Join-Path $projectRoot "docs\petdesk_release_doctor_m56.md")
    "docs/petdesk_release_acceptance_m57.md" = (Join-Path $projectRoot "docs\petdesk_release_acceptance_m57.md")
    "docs/petdesk_release_bundle_m58.md" = (Join-Path $projectRoot "docs\petdesk_release_bundle_m58.md")
    "docs/petdesk_release_bundle_audit_m59.md" = (Join-Path $projectRoot "docs\petdesk_release_bundle_audit_m59.md")
    "scripts/audit_petdesk_release_bundle.ps1" = (Join-Path $projectRoot "scripts\audit_petdesk_release_bundle.ps1")
}

foreach ($entry in $requiredSourceFiles.GetEnumerator()) {
    if (-not (Test-Path -LiteralPath $entry.Value -PathType Leaf)) {
        throw "bundle_source_file_missing: $($entry.Value)"
    }
}

$releaseExeItem = Get-Item -LiteralPath $releaseExe
if ($releaseExeItem.Length -le 0) {
    throw "petdesk_runtime_release_exe_empty: $releaseExe"
}

if ($CheckOnly) {
    foreach ($entry in $requiredSourceFiles.GetEnumerator()) {
        Write-AkanePetdeskBundleExportStep "OK" ("Would include {0}" -f $entry.Key)
    }
    Write-Host "AKANE_PETDESK_RELEASE_BUNDLE_CHECK_OK"
    exit 0
}

if ($DryRun) {
    Write-AkanePetdeskBundleExportStep "OK" "DryRun completed. No bundle files were written."
    Write-Host "AKANE_PETDESK_RELEASE_BUNDLE_DRY_RUN_OK"
    exit 0
}

$null = New-Item -ItemType Directory -Path $outputRoot
foreach ($entry in $requiredSourceFiles.GetEnumerator()) {
    $destination = Join-Path $outputRoot $entry.Key
    Copy-RequiredFile -SourcePath $entry.Value -DestinationPath $destination
}

$readmePath = Join-Path $outputRoot "README.md"
Set-Content -LiteralPath $readmePath -Value (New-BundleReadmeText) -Encoding UTF8

$sourceCommit = ""
try {
    $sourceCommit = [string](git -C $projectRoot rev-parse --short HEAD)
    if ($LASTEXITCODE -ne 0) {
        $sourceCommit = ""
    }
} catch {
    $sourceCommit = ""
}

$manifest = New-BundleManifest -BundleRoot $outputRoot -SourceCommit $sourceCommit
$manifestPath = Join-Path $outputRoot "manifest.json"
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

Write-Host "AKANE_PETDESK_RELEASE_BUNDLE_EXPORT_OK"
[pscustomobject]@{
    Status      = "ok"
    OutputPath  = $outputRoot
    RuntimeExe  = "runtime/petdesk_runtime.exe"
    StartScript = "scripts/start_petdesk_runtime_bundle.ps1"
    Files       = $manifest.files.Count
} | Format-List
