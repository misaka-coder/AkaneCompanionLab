[CmdletBinding()]
param(
    [int]$BackendPort = 9999,
    [string]$BackendUrl = "",
    [string]$RuntimeDir = "",
    [string]$RuntimeExe = "",
    [switch]$All,
    [switch]$SkipBackendHttp,
    [int]$TimeoutSeconds = 3
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:AkanePetdeskDoctorFailCount = 0
$script:AkanePetdeskDoctorWarnCount = 0

function Write-AkanePetdeskDoctorResult {
    param(
        [ValidateSet("OK", "WARN", "FAIL", "INFO")]
        [string]$Level,
        [string]$Name,
        [string]$Message
    )

    if ($Level -eq "FAIL") {
        $script:AkanePetdeskDoctorFailCount += 1
    } elseif ($Level -eq "WARN") {
        $script:AkanePetdeskDoctorWarnCount += 1
    }

    Write-Host ("[{0}] {1}: {2}" -f $Level, $Name, $Message)
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

    $candidate = if ($RequestedRuntimeDir) {
        if ([System.IO.Path]::IsPathRooted($RequestedRuntimeDir)) {
            $RequestedRuntimeDir
        } else {
            Join-Path $ProjectRoot $RequestedRuntimeDir
        }
    } else {
        Join-Path $ProjectRoot "..\petdesk-runtime"
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

function Join-PetdeskBackendUrl {
    param(
        [string]$BaseUrl,
        [string]$Reference
    )

    $value = ([string]$Reference).Trim()
    if (-not $value) {
        return ""
    }
    if ($value -match "^https?://") {
        return $value
    }
    if (-not $value.StartsWith("/")) {
        return ""
    }
    return "{0}{1}" -f $BaseUrl.TrimEnd("/"), $value
}

function Get-ObjectPropertyValue {
    param(
        [object]$Object,
        [string]$Name
    )

    if ($null -eq $Object) {
        return $null
    }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $null
    }
    return $property.Value
}

function Get-PetdeskJson {
    param(
        [string]$Url,
        [string]$Name,
        [int]$Timeout
    )

    try {
        return Invoke-RestMethod `
            -Uri $Url `
            -TimeoutSec ([Math]::Max(1, $Timeout)) `
            -Headers @{ "Accept" = "application/json"; "Cache-Control" = "no-store" }
    } catch {
        Write-AkanePetdeskDoctorResult "FAIL" $Name ("request failed: {0}" -f $_.Exception.Message)
        return $null
    }
}

function Get-ProcessPathSafe {
    param([System.Diagnostics.Process]$Process)

    try {
        return $Process.Path
    } catch {
        return ""
    }
}

function Test-RequiredFile {
    param(
        [string]$Name,
        [string]$Path
    )

    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        Write-AkanePetdeskDoctorResult "OK" $Name $Path
        return $true
    }

    Write-AkanePetdeskDoctorResult "FAIL" $Name ("missing: {0}" -f $Path)
    return $false
}

function Test-OptionalCommand {
    param(
        [string]$Name,
        [string]$Reason
    )

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) {
        Write-AkanePetdeskDoctorResult "OK" ("command {0}" -f $Name) $command.Source
    } else {
        Write-AkanePetdeskDoctorResult "WARN" ("command {0}" -f $Name) ("not found; {0}" -f $Reason)
    }
}

$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $scriptDir) {
    $scriptDir = (Get-Location).Path
}

try {
    $projectRoot = Find-AkaneProjectRoot -StartDir $scriptDir
    Write-AkanePetdeskDoctorResult "OK" "Akane project" $projectRoot
} catch {
    Write-AkanePetdeskDoctorResult "FAIL" "Akane project" $_.Exception.Message
    Write-Host "AKANE_PETDESK_RELEASE_DOCTOR_FAILED"
    exit 1
}

$resolvedRuntimeDir = Resolve-PetdeskRuntimeDir -ProjectRoot $projectRoot -RequestedRuntimeDir $RuntimeDir
if (Test-Path -LiteralPath $resolvedRuntimeDir -PathType Container) {
    Write-AkanePetdeskDoctorResult "OK" "petdesk-runtime" $resolvedRuntimeDir
} else {
    Write-AkanePetdeskDoctorResult "FAIL" "petdesk-runtime" ("missing: {0}" -f $resolvedRuntimeDir)
}

$packageJson = Join-Path $resolvedRuntimeDir "package.json"
$cargoManifest = Join-Path $resolvedRuntimeDir "src-tauri\Cargo.toml"
$releaseExe = Resolve-TargetRuntimeExe `
    -ProjectRoot $projectRoot `
    -ResolvedRuntimeDir $resolvedRuntimeDir `
    -RequestedRuntimeExe $RuntimeExe

$null = Test-RequiredFile -Name "petdesk-runtime package.json" -Path $packageJson
$null = Test-RequiredFile -Name "petdesk-runtime Cargo.toml" -Path $cargoManifest

if (Test-Path -LiteralPath $releaseExe -PathType Leaf) {
    $exe = Get-Item -LiteralPath $releaseExe
    if ($exe.Length -gt 0) {
        Write-AkanePetdeskDoctorResult "OK" "release exe" (
            "{0} ({1} bytes, {2})" -f $exe.FullName, $exe.Length, $exe.LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss")
        )
    } else {
        Write-AkanePetdeskDoctorResult "FAIL" "release exe" ("empty file: {0}" -f $releaseExe)
    }
} else {
    Write-AkanePetdeskDoctorResult "FAIL" "release exe" (
        "missing: {0}; run scripts\build_petdesk_runtime_release.ps1" -f $releaseExe
    )
}

$null = Test-RequiredFile -Name "release starter" -Path (Join-Path $projectRoot "start_akane_petdesk_release.ps1")
$null = Test-RequiredFile -Name "release build helper" -Path (Join-Path $projectRoot "scripts\build_petdesk_runtime_release.ps1")
$null = Test-RequiredFile -Name "release stop helper" -Path (Join-Path $projectRoot "scripts\stop_petdesk_runtime.ps1")
$null = Test-RequiredFile -Name "petdesk smoke script" -Path (Join-Path $projectRoot "scripts\tools\run_petdesk_mvp_smoke.py")

Test-OptionalCommand -Name "python" -Reason "needed for startup and MVP smoke scripts"
Test-OptionalCommand -Name "cargo" -Reason "needed only when rebuilding petdesk-runtime release"
Test-OptionalCommand -Name "pnpm" -Reason "needed only when rebuilding petdesk-runtime release"

$allProcesses = @(Get-Process -Name petdesk_runtime -ErrorAction SilentlyContinue)
if ($allProcesses.Count -eq 0) {
    Write-AkanePetdeskDoctorResult "OK" "runtime process" "no petdesk_runtime.exe process is running"
} else {
    $matchedProcesses = @()
    $unmatchedProcesses = @()
    foreach ($process in $allProcesses) {
        $path = Get-ProcessPathSafe -Process $process
        if ($All -or ([string]::Equals($path, $releaseExe, [System.StringComparison]::OrdinalIgnoreCase))) {
            $matchedProcesses += $process
        } else {
            $unmatchedProcesses += $process
        }
    }

    if ($All) {
        Write-AkanePetdeskDoctorResult "INFO" "runtime process" "reporting all petdesk_runtime.exe processes because -All was passed"
    }
    foreach ($process in $matchedProcesses) {
        $path = Get-ProcessPathSafe -Process $process
        Write-AkanePetdeskDoctorResult "OK" "runtime process" ("PID {0}: {1}" -f $process.Id, $path)
    }
    foreach ($process in $unmatchedProcesses) {
        $path = Get-ProcessPathSafe -Process $process
        Write-AkanePetdeskDoctorResult "WARN" "runtime process" (
            "PID {0} does not match target release exe: {1}" -f $process.Id, $path
        )
    }
}

$resolvedBackendUrl = Normalize-BackendUrl -RawUrl $BackendUrl -Port $BackendPort
Write-AkanePetdeskDoctorResult "INFO" "backend" $resolvedBackendUrl

if ($SkipBackendHttp) {
    Write-AkanePetdeskDoctorResult "WARN" "backend HTTP" "skipped because -SkipBackendHttp was passed"
} else {
    $healthUrl = Join-PetdeskBackendUrl -BaseUrl $resolvedBackendUrl -Reference "/pet/health"
    $health = Get-PetdeskJson -Url $healthUrl -Name "/pet/health" -Timeout $TimeoutSeconds
    if ($null -ne $health) {
        $ok = Get-ObjectPropertyValue -Object $health -Name "ok"
        $status = [string](Get-ObjectPropertyValue -Object $health -Name "status")
        if ($ok -eq $true -and $status -eq "ready") {
            Write-AkanePetdeskDoctorResult "OK" "/pet/health" "ok=true status=ready"
        } else {
            Write-AkanePetdeskDoctorResult "FAIL" "/pet/health" ("expected ok=true status=ready; got ok={0} status={1}" -f $ok, $status)
        }

        $snapshotRef = [string](Get-ObjectPropertyValue -Object $health -Name "snapshot")
        $turnRef = [string](Get-ObjectPropertyValue -Object $health -Name "turn")
        if ($snapshotRef -eq "/pet/snapshot" -and $turnRef -eq "/pet/turn") {
            Write-AkanePetdeskDoctorResult "OK" "pet bridge endpoints" "snapshot=/pet/snapshot turn=/pet/turn"
        } else {
            Write-AkanePetdeskDoctorResult "FAIL" "pet bridge endpoints" ("snapshot={0} turn={1}" -f $snapshotRef, $turnRef)
        }

        $runtimeEnv = Get-ObjectPropertyValue -Object $health -Name "runtimeEnv"
        $manifestRef = [string](Get-ObjectPropertyValue -Object $runtimeEnv -Name "VITE_PETDESK_RESOURCE_MANIFEST_URL")
        if ($manifestRef) {
            Write-AkanePetdeskDoctorResult "OK" "resource manifest env" $manifestRef
            $manifestUrl = Join-PetdeskBackendUrl -BaseUrl $resolvedBackendUrl -Reference $manifestRef
            if ($manifestUrl) {
                $manifest = Get-PetdeskJson -Url $manifestUrl -Name "resource manifest" -Timeout $TimeoutSeconds
                if ($null -ne $manifest) {
                    $staticImages = Get-ObjectPropertyValue -Object $manifest -Name "staticImages"
                    $staticImageCount = if ($null -ne $staticImages) { @($staticImages.PSObject.Properties).Count } else { 0 }
                    if ($staticImageCount -gt 0) {
                        Write-AkanePetdeskDoctorResult "OK" "resource manifest" ("staticImages={0}" -f $staticImageCount)
                    } else {
                        Write-AkanePetdeskDoctorResult "FAIL" "resource manifest" "missing staticImages"
                    }
                }
            } else {
                Write-AkanePetdeskDoctorResult "FAIL" "resource manifest env" ("unsupported reference: {0}" -f $manifestRef)
            }
        } else {
            Write-AkanePetdeskDoctorResult "FAIL" "resource manifest env" "missing VITE_PETDESK_RESOURCE_MANIFEST_URL"
        }

        $resolvedSnapshotRef = if ($snapshotRef) { $snapshotRef } else { "/pet/snapshot" }
        $snapshotUrl = Join-PetdeskBackendUrl -BaseUrl $resolvedBackendUrl -Reference $resolvedSnapshotRef
        if ($snapshotUrl) {
            $snapshot = Get-PetdeskJson -Url $snapshotUrl -Name "/pet/snapshot" -Timeout $TimeoutSeconds
            if ($null -ne $snapshot) {
                $visual = Get-ObjectPropertyValue -Object $snapshot -Name "visual"
                $renderer = [string](Get-ObjectPropertyValue -Object $visual -Name "renderer")
                $assetHandle = [string](Get-ObjectPropertyValue -Object $visual -Name "assetHandle")
                if ($assetHandle) {
                    Write-AkanePetdeskDoctorResult "OK" "/pet/snapshot" (
                        "renderer={0} assetHandle={1}" -f $renderer, $assetHandle
                    )
                } else {
                    Write-AkanePetdeskDoctorResult "FAIL" "/pet/snapshot" "missing visual.assetHandle"
                }
            }
        } else {
            Write-AkanePetdeskDoctorResult "FAIL" "/pet/snapshot" ("unsupported reference: {0}" -f $resolvedSnapshotRef)
        }
    }
}

if ($script:AkanePetdeskDoctorFailCount -gt 0) {
    Write-Host "AKANE_PETDESK_RELEASE_DOCTOR_FAILED"
    exit 1
}

Write-Host (
    "AKANE_PETDESK_RELEASE_DOCTOR_OK warnings={0}" -f $script:AkanePetdeskDoctorWarnCount
)
exit 0
