[CmdletBinding()]
param(
    [int]$BackendPort = 9999,
    [string]$BackendUrl = "",
    [string]$RuntimeDir = "",
    [string]$RuntimeExe = "",
    [switch]$Full,
    [switch]$SkipDoctor,
    [switch]$SkipSmoke,
    [switch]$StartBackend,
    [switch]$ReuseBackend,
    [switch]$CheckOnly,
    [string]$SmokeText = "测试一下 petdesk MVP 语音链路。",
    [int]$DoctorTimeoutSeconds = 3,
    [int]$HealthTimeoutSeconds = 45
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-AkanePetdeskAcceptanceStep {
    param(
        [ValidateSet("OK", "WARN", "FAIL", "INFO")]
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

function Invoke-AkanePetdeskChildScript {
    param(
        [string]$Label,
        [string]$ScriptPath,
        [string[]]$Arguments
    )

    Write-AkanePetdeskAcceptanceStep "INFO" ("Running {0}..." -f $Label)
    & powershell -NoProfile -ExecutionPolicy Bypass -File $ScriptPath @Arguments
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        Write-AkanePetdeskAcceptanceStep "FAIL" ("{0} failed with exit code {1}." -f $Label, $exitCode)
        Write-Host "AKANE_PETDESK_RELEASE_ACCEPTANCE_FAILED"
        exit $exitCode
    }
    Write-AkanePetdeskAcceptanceStep "OK" ("{0} passed." -f $Label)
}

$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $scriptDir) {
    $scriptDir = (Get-Location).Path
}

try {
    $projectRoot = Find-AkaneProjectRoot -StartDir $scriptDir
} catch {
    Write-AkanePetdeskAcceptanceStep "FAIL" $_.Exception.Message
    Write-Host "AKANE_PETDESK_RELEASE_ACCEPTANCE_FAILED"
    exit 1
}

$resolvedBackendUrl = Normalize-BackendUrl -RawUrl $BackendUrl -Port $BackendPort
$doctorScript = Join-Path $projectRoot "scripts\check_petdesk_release.ps1"
$releaseStarter = Join-Path $projectRoot "start_akane_petdesk_release.ps1"

Write-AkanePetdeskAcceptanceStep "INFO" ("Akane project: {0}" -f $projectRoot)
Write-AkanePetdeskAcceptanceStep "INFO" ("Backend: {0}" -f $resolvedBackendUrl)
if ($Full) {
    Write-AkanePetdeskAcceptanceStep "INFO" "Mode: full MVP smoke."
} else {
    Write-AkanePetdeskAcceptanceStep "INFO" "Mode: quick startup smoke."
}
if ($StartBackend) {
    Write-AkanePetdeskAcceptanceStep "WARN" "StartBackend was passed; the release wrapper may start Akane backend."
} else {
    Write-AkanePetdeskAcceptanceStep "INFO" "Backend start: disabled by default; expecting an existing backend."
}

if (-not (Test-Path -LiteralPath $doctorScript -PathType Leaf)) {
    Write-AkanePetdeskAcceptanceStep "FAIL" ("release doctor not found: {0}" -f $doctorScript)
    Write-Host "AKANE_PETDESK_RELEASE_ACCEPTANCE_FAILED"
    exit 1
}
if (-not (Test-Path -LiteralPath $releaseStarter -PathType Leaf)) {
    Write-AkanePetdeskAcceptanceStep "FAIL" ("release starter not found: {0}" -f $releaseStarter)
    Write-Host "AKANE_PETDESK_RELEASE_ACCEPTANCE_FAILED"
    exit 1
}
if ($SkipDoctor -and $SkipSmoke) {
    Write-AkanePetdeskAcceptanceStep "FAIL" "SkipDoctor and SkipSmoke cannot both be used."
    Write-Host "AKANE_PETDESK_RELEASE_ACCEPTANCE_FAILED"
    exit 1
}

if ($CheckOnly) {
    Write-AkanePetdeskAcceptanceStep "OK" "CheckOnly completed. No doctor, smoke, backend, or runtime process was launched."
    Write-Host "AKANE_PETDESK_RELEASE_ACCEPTANCE_CHECK_OK"
    exit 0
}

if (-not $SkipDoctor) {
    $doctorArguments = @(
        "-BackendUrl", $resolvedBackendUrl,
        "-TimeoutSeconds", ([string]$DoctorTimeoutSeconds)
    )
    if ($RuntimeDir) {
        $doctorArguments += @("-RuntimeDir", $RuntimeDir)
    }
    if ($RuntimeExe) {
        $doctorArguments += @("-RuntimeExe", $RuntimeExe)
    }
    Invoke-AkanePetdeskChildScript `
        -Label "petdesk release doctor" `
        -ScriptPath $doctorScript `
        -Arguments $doctorArguments
} else {
    Write-AkanePetdeskAcceptanceStep "WARN" "Skipping release doctor."
}

if (-not $SkipSmoke) {
    $smokeArguments = @(
        "-BackendUrl", $resolvedBackendUrl,
        "-HealthTimeoutSeconds", ([string]$HealthTimeoutSeconds)
    )
    if ($Full) {
        $smokeArguments += "-SmokeOnly"
    } else {
        $smokeArguments += "-StartupSmokeOnly"
    }
    if (-not $StartBackend) {
        $smokeArguments += "-SkipBackend"
    }
    if ($ReuseBackend) {
        $smokeArguments += "-ReuseBackend"
    }
    if ($RuntimeDir) {
        $smokeArguments += @("-RuntimeDir", $RuntimeDir)
    }
    if ($RuntimeExe) {
        $smokeArguments += @("-RuntimeExe", $RuntimeExe)
    }
    if ($SmokeText) {
        $smokeArguments += @("-SmokeText", $SmokeText)
    }

    $smokeLabel = if ($Full) { "petdesk full MVP smoke" } else { "petdesk startup smoke" }
    Invoke-AkanePetdeskChildScript `
        -Label $smokeLabel `
        -ScriptPath $releaseStarter `
        -Arguments $smokeArguments
} else {
    Write-AkanePetdeskAcceptanceStep "WARN" "Skipping smoke."
}

Write-Host "AKANE_PETDESK_RELEASE_ACCEPTANCE_OK"
exit 0
