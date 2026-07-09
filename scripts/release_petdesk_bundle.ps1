[CmdletBinding()]
param(
    [int]$BackendPort = 9999,
    [string]$BackendUrl = "",
    [string]$RuntimeDir = "",
    [string]$RuntimeExe = "",
    [string]$OutputPath = "",
    [switch]$BuildFirst,
    [switch]$SkipBuildWarmup,
    [switch]$FullAcceptance,
    [switch]$SkipAcceptance,
    [switch]$StartBackend,
    [switch]$ReuseBackend,
    [switch]$CheckOnly,
    [switch]$DryRun,
    [string]$SmokeText = "测试一下 petdesk MVP 语音链路。",
    [int]$DoctorTimeoutSeconds = 3,
    [int]$HealthTimeoutSeconds = 45
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-AkanePetdeskReleaseStep {
    param(
        [ValidateSet("OK", "WARN", "FAIL", "INFO")]
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

function Normalize-BackendUrl {
    param(
        [string]$RawUrl,
        [int]$Port
    )

    $value = ([string]$RawUrl).Trim()
    if (-not $value) {
        return "http://127.0.0.1:{0}" -f $Port
    }
    return $value.TrimEnd("/")
}

function Invoke-AkanePetdeskPipelineChildScript {
    param(
        [string]$Label,
        [string]$ScriptPath,
        [string[]]$Arguments
    )

    Write-AkanePetdeskReleaseStep "INFO" ("Running {0}..." -f $Label)
    & powershell -NoProfile -ExecutionPolicy Bypass -File $ScriptPath @Arguments
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        Write-AkanePetdeskReleaseStep "FAIL" ("{0} failed with exit code {1}." -f $Label, $exitCode)
        Write-Host "AKANE_PETDESK_RELEASE_PIPELINE_FAILED"
        exit $exitCode
    }
    Write-AkanePetdeskReleaseStep "OK" ("{0} passed." -f $Label)
}

function Get-SourceCommit {
    param([string]$ProjectRoot)

    try {
        $commit = [string](git -C $ProjectRoot rev-parse --short HEAD)
        if ($LASTEXITCODE -eq 0) {
            return $commit.Trim()
        }
    } catch {
        return ""
    }
    return ""
}

function Write-ReleaseSummary {
    param(
        [Parameter(Mandatory = $true)][string]$BundleRoot,
        [Parameter(Mandatory = $true)][string]$SourceCommit,
        [Parameter(Mandatory = $true)][string]$Backend,
        [Parameter(Mandatory = $true)][string]$Status
    )

    $summary = [ordered]@{
        schema = "akane.petdesk.releasePipelineSummary.v1"
        status = $Status
        createdAtUtc = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
        sourceCommit = $SourceCommit
        buildFirst = [bool]$BuildFirst
        skipBuildWarmup = [bool]$SkipBuildWarmup
        acceptance = [ordered]@{
            skipped = [bool]$SkipAcceptance
            mode = if ($FullAcceptance) { "full" } else { "startup" }
            startBackend = [bool]$StartBackend
            reuseBackend = [bool]$ReuseBackend
            backendUrl = $Backend
        }
        bundle = [ordered]@{
            manifest = "manifest.json"
            summary = "release_summary.json"
            runtimeExe = "runtime/petdesk_runtime.exe"
            startScript = "scripts/start_petdesk_runtime_bundle.ps1"
            auditScript = "scripts/audit_petdesk_release_bundle.ps1"
        }
        notes = @(
            "This summary intentionally avoids local absolute paths.",
            "The bundle audit is the authoritative integrity check."
        )
    }

    $summaryPath = Join-Path $BundleRoot "release_summary.json"
    $summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $summaryPath -Encoding UTF8
}

function Update-BundleManifestFileEntry {
    param(
        [Parameter(Mandatory = $true)][string]$BundleRoot,
        [Parameter(Mandatory = $true)][string]$RelativePath
    )

    $manifestPath = Join-Path $BundleRoot "manifest.json"
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "bundle_manifest_missing: $manifestPath"
    }
    $filePath = Join-Path $BundleRoot $RelativePath
    if (-not (Test-Path -LiteralPath $filePath -PathType Leaf)) {
        throw "bundle_summary_missing: $filePath"
    }

    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    $file = Get-Item -LiteralPath $filePath
    $entry = [pscustomobject][ordered]@{
        path = $RelativePath.Replace("\", "/")
        bytes = $file.Length
        sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $filePath).Hash
    }

    $existing = @(
        $manifest.files |
            Where-Object { [string]$_.path -ne $entry.path }
    )
    $files = @($existing + $entry | Sort-Object path)
    $updatedManifest = [ordered]@{
        schema = [string]$manifest.schema
        createdAtUtc = [string]$manifest.createdAtUtc
        sourceCommit = [string]$manifest.sourceCommit
        runtimeExe = [string]$manifest.runtimeExe
        startScript = [string]$manifest.startScript
        files = $files
    }
    $updatedManifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
}

$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $scriptDir) {
    $scriptDir = (Get-Location).Path
}

try {
    $projectRoot = Find-AkaneProjectRoot -StartDir $scriptDir
} catch {
    Write-AkanePetdeskReleaseStep "FAIL" $_.Exception.Message
    Write-Host "AKANE_PETDESK_RELEASE_PIPELINE_FAILED"
    exit 1
}

$projectRootFull = ConvertTo-FullPath $projectRoot
$resolvedBackendUrl = Normalize-BackendUrl -RawUrl $BackendUrl -Port $BackendPort
if (-not $OutputPath) {
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $OutputPath = Join-Path $projectRootFull ("reports\petdesk-release-bundles\petdesk-release-{0}" -f $timestamp)
}
$resolvedOutputPath = ConvertTo-FullPath $OutputPath

$buildScript = Join-Path $projectRootFull "scripts\build_petdesk_runtime_release.ps1"
$acceptanceScript = Join-Path $projectRootFull "scripts\accept_petdesk_release.ps1"
$exportScript = Join-Path $projectRootFull "scripts\export_petdesk_release_bundle.ps1"
$auditScript = Join-Path $projectRootFull "scripts\audit_petdesk_release_bundle.ps1"

$requiredScripts = @($exportScript, $auditScript)
if ($BuildFirst) {
    $requiredScripts += $buildScript
}
if (-not $SkipAcceptance) {
    $requiredScripts += $acceptanceScript
}
foreach ($scriptPath in $requiredScripts) {
    if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
        Write-AkanePetdeskReleaseStep "FAIL" ("required script missing: {0}" -f $scriptPath)
        Write-Host "AKANE_PETDESK_RELEASE_PIPELINE_FAILED"
        exit 1
    }
}

Write-AkanePetdeskReleaseStep "INFO" ("Akane project: {0}" -f $projectRootFull)
Write-AkanePetdeskReleaseStep "INFO" ("Backend: {0}" -f $resolvedBackendUrl)
Write-AkanePetdeskReleaseStep "INFO" ("Output: {0}" -f $resolvedOutputPath)
Write-AkanePetdeskReleaseStep "INFO" ("BuildFirst: {0}" -f [bool]$BuildFirst)
Write-AkanePetdeskReleaseStep "INFO" ("Acceptance: {0}" -f $(if ($SkipAcceptance) { "skipped" } elseif ($FullAcceptance) { "full" } else { "startup" }))

if ($CheckOnly) {
    Write-AkanePetdeskReleaseStep "OK" "CheckOnly completed. No child scripts were run."
    Write-Host "AKANE_PETDESK_RELEASE_PIPELINE_CHECK_OK"
    exit 0
}

if ($DryRun) {
    if ($BuildFirst) {
        $buildDryRunArguments = @("-DryRun")
        if ($RuntimeDir) {
            $buildDryRunArguments += @("-RuntimeDir", $RuntimeDir)
        }
        if ($SkipBuildWarmup) {
            $buildDryRunArguments += "-SkipWarmup"
        }
        Invoke-AkanePetdeskPipelineChildScript `
            -Label "petdesk release build dry-run" `
            -ScriptPath $buildScript `
            -Arguments $buildDryRunArguments
    }

    if (-not $SkipAcceptance) {
        Invoke-AkanePetdeskPipelineChildScript `
            -Label "petdesk release acceptance check" `
            -ScriptPath $acceptanceScript `
            -Arguments @("-CheckOnly")
    }

    $exportDryRunArguments = @("-DryRun", "-OutputPath", $resolvedOutputPath)
    if ($RuntimeDir) {
        $exportDryRunArguments += @("-RuntimeDir", $RuntimeDir)
    }
    if ($RuntimeExe) {
        $exportDryRunArguments += @("-RuntimeExe", $RuntimeExe)
    }
    Invoke-AkanePetdeskPipelineChildScript `
        -Label "petdesk release bundle export dry-run" `
        -ScriptPath $exportScript `
        -Arguments $exportDryRunArguments

    Write-Host "AKANE_PETDESK_RELEASE_PIPELINE_DRY_RUN_OK"
    exit 0
}

if ($BuildFirst) {
    $buildArguments = @()
    if ($RuntimeDir) {
        $buildArguments += @("-RuntimeDir", $RuntimeDir)
    }
    if ($SkipBuildWarmup) {
        $buildArguments += "-SkipWarmup"
    }
    Invoke-AkanePetdeskPipelineChildScript `
        -Label "petdesk release build" `
        -ScriptPath $buildScript `
        -Arguments $buildArguments
}

if (-not $SkipAcceptance) {
    $acceptanceArguments = @(
        "-BackendUrl", $resolvedBackendUrl,
        "-DoctorTimeoutSeconds", ([string]$DoctorTimeoutSeconds),
        "-HealthTimeoutSeconds", ([string]$HealthTimeoutSeconds)
    )
    if ($RuntimeDir) {
        $acceptanceArguments += @("-RuntimeDir", $RuntimeDir)
    }
    if ($RuntimeExe) {
        $acceptanceArguments += @("-RuntimeExe", $RuntimeExe)
    }
    if ($FullAcceptance) {
        $acceptanceArguments += "-Full"
    }
    if ($StartBackend) {
        $acceptanceArguments += "-StartBackend"
    }
    if ($ReuseBackend) {
        $acceptanceArguments += "-ReuseBackend"
    }
    if ($SmokeText) {
        $acceptanceArguments += @("-SmokeText", $SmokeText)
    }
    Invoke-AkanePetdeskPipelineChildScript `
        -Label "petdesk release acceptance" `
        -ScriptPath $acceptanceScript `
        -Arguments $acceptanceArguments
} else {
    Write-AkanePetdeskReleaseStep "WARN" "Skipping release acceptance."
}

$exportArguments = @("-OutputPath", $resolvedOutputPath)
if ($RuntimeDir) {
    $exportArguments += @("-RuntimeDir", $RuntimeDir)
}
if ($RuntimeExe) {
    $exportArguments += @("-RuntimeExe", $RuntimeExe)
}
Invoke-AkanePetdeskPipelineChildScript `
    -Label "petdesk release bundle export" `
    -ScriptPath $exportScript `
    -Arguments $exportArguments

$sourceCommit = Get-SourceCommit -ProjectRoot $projectRootFull
Write-ReleaseSummary `
    -BundleRoot $resolvedOutputPath `
    -SourceCommit $sourceCommit `
    -Backend $resolvedBackendUrl `
    -Status "pre_audit"
Update-BundleManifestFileEntry -BundleRoot $resolvedOutputPath -RelativePath "release_summary.json"

Invoke-AkanePetdeskPipelineChildScript `
    -Label "petdesk release bundle audit" `
    -ScriptPath $auditScript `
    -Arguments @("-BundleRoot", $resolvedOutputPath)

Write-ReleaseSummary `
    -BundleRoot $resolvedOutputPath `
    -SourceCommit $sourceCommit `
    -Backend $resolvedBackendUrl `
    -Status "ok"
Update-BundleManifestFileEntry -BundleRoot $resolvedOutputPath -RelativePath "release_summary.json"

Invoke-AkanePetdeskPipelineChildScript `
    -Label "petdesk release bundle final audit" `
    -ScriptPath $auditScript `
    -Arguments @("-BundleRoot", $resolvedOutputPath)

Write-AkanePetdeskReleaseStep "OK" ("Release bundle ready: {0}" -f $resolvedOutputPath)
Write-Host "AKANE_PETDESK_RELEASE_PIPELINE_OK"
exit 0
