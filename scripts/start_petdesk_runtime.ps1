[CmdletBinding()]
param(
    [int]$BackendPort = 9999,
    [string]$BackendUrl = "",
    [string]$RuntimeDir = "",
    [ValidateSet("Dev", "Release")]
    [string]$RuntimeMode = "Dev",
    [string]$RuntimeExe = "",
    [switch]$SkipBackend,
    [switch]$ReuseBackend,
    [switch]$CheckOnly,
    [switch]$DryRun,
    [switch]$SmokeOnly,
    [switch]$StartupSmokeOnly,
    [switch]$SkipStartupSmoke,
    [switch]$RunSmokeBeforeLaunch,
    [switch]$RunStartupSmokeBeforeLaunch,
    [string]$SmokeText = "测试一下 petdesk MVP 语音链路。",
    [int]$HealthTimeoutSeconds = 45
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$PetdeskRuntimeEnvKeys = @(
    "VITE_PETDESK_INTERACTION_PROFILE",
    "VITE_PETDESK_INTERACTION_PROFILE_JSON",
    "VITE_PETDESK_LIVE2D_MODEL_LAYOUT_PROFILE",
    "VITE_PETDESK_LIVE2D_MODEL_LAYOUT_JSON",
    "VITE_PETDESK_LIVE2D_MOTION_MAP_JSON",
    "VITE_PETDESK_LIVE2D_EXPRESSION_MAP_JSON",
    "VITE_PETDESK_RESOURCE_MANIFEST_URL",
    "VITE_PETDESK_RESOURCE_MANIFEST_JSON"
)
$MaxRuntimeEnvValueLength = 20000
$script:PetdeskMvpSmokeExitCode = 0

function Write-AkanePetdeskStep {
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

    $candidate = if ($RequestedRuntimeDir) {
        if ([System.IO.Path]::IsPathRooted($RequestedRuntimeDir)) {
            $RequestedRuntimeDir
        } else {
            Join-Path $ProjectRoot $RequestedRuntimeDir
        }
    } else {
        Join-Path $ProjectRoot "..\petdesk-runtime"
    }

    $resolved = [System.IO.Path]::GetFullPath($candidate)
    $packageJson = Join-Path $resolved "package.json"
    if (-not (Test-Path -LiteralPath $packageJson -PathType Leaf)) {
        throw "petdesk_runtime_not_found: $resolved"
    }
    return $resolved
}

function Resolve-PetdeskRuntimeExe {
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
        $resolved = [System.IO.Path]::GetFullPath($candidate)
        if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
            throw "petdesk_runtime_release_exe_not_found: $resolved"
        }
        return $resolved
    }

    $candidates = New-Object System.Collections.Generic.List[string]
    $cargoConfig = Join-Path $ResolvedRuntimeDir ".cargo\config.toml"
    if (Test-Path -LiteralPath $cargoConfig -PathType Leaf) {
        $configText = Get-Content -LiteralPath $cargoConfig -Raw
        if ($configText -match '(?m)^\s*target-dir\s*=\s*"([^"]+)"') {
            $targetDir = $Matches[1]
            if (-not [System.IO.Path]::IsPathRooted($targetDir)) {
                $targetDir = Join-Path $ResolvedRuntimeDir $targetDir
            }
            $candidates.Add((Join-Path $targetDir "release\petdesk_runtime.exe"))
        }
    }

    $candidates.Add((Join-Path $ResolvedRuntimeDir "src-tauri\target\release\petdesk_runtime.exe"))
    $candidates.Add((Join-Path $ResolvedRuntimeDir "target\release\petdesk_runtime.exe"))

    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        $resolved = [System.IO.Path]::GetFullPath($candidate)
        if (Test-Path -LiteralPath $resolved -PathType Leaf) {
            return $resolved
        }
    }

    throw "petdesk_runtime_release_exe_not_found: pass -RuntimeExe or build petdesk-runtime release first"
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

function Test-LoopbackBackendUrl {
    param([string]$Url)

    return [bool]($Url -match "^http://(127\.0\.0\.1|localhost):\d+/?$")
}

function Start-AkaneBackendForPetdesk {
    param(
        [string]$ProjectRoot,
        [int]$Port,
        [switch]$Reuse
    )

    $parameters = @{
        BackendPort = $Port
        SkipDesktop = $true
    }
    if ($Reuse) {
        $parameters.ReuseBackend = $true
    }
    & (Join-Path $ProjectRoot "start_akane_next.ps1") @parameters
    if (-not $?) {
        throw "akane_backend_start_failed"
    }
}

function Get-PetdeskHealth {
    param(
        [string]$BaseUrl,
        [int]$TimeoutSeconds
    )

    $healthUrl = "{0}/pet/health" -f $BaseUrl.TrimEnd("/")
    $deadline = [DateTime]::UtcNow.AddSeconds([Math]::Max(0, $TimeoutSeconds))
    do {
        try {
            return Invoke-RestMethod `
                -Uri $healthUrl `
                -TimeoutSec 3 `
                -Headers @{ "Cache-Control" = "no-store" }
        } catch {
            if ([DateTime]::UtcNow -ge $deadline) {
                return $null
            }
            Start-Sleep -Milliseconds 500
        }
    } while ([DateTime]::UtcNow -lt $deadline)

    return $null
}

function ConvertTo-SafePetdeskRuntimeEnv {
    param(
        [object]$RuntimeEnv,
        [string]$BackendUrl
    )

    $safe = [ordered]@{}
    if ($null -eq $RuntimeEnv) {
        return $safe
    }

    $properties = @($RuntimeEnv.PSObject.Properties)
    foreach ($property in $properties) {
        $name = [string]$property.Name
        if ($PetdeskRuntimeEnvKeys -notcontains $name) {
            continue
        }
        if ($property.Value -isnot [string]) {
            continue
        }

        $value = ([string]$property.Value).Trim()
        if (-not $value -or $value.Length -gt $MaxRuntimeEnvValueLength) {
            continue
        }
        if ($name -eq "VITE_PETDESK_RESOURCE_MANIFEST_URL" -and $value.StartsWith("/")) {
            if ($value.StartsWith("//") -or -not $BackendUrl) {
                continue
            }
            $value = "{0}{1}" -f $BackendUrl.TrimEnd("/"), $value
            if ($value.Length -gt $MaxRuntimeEnvValueLength) {
                continue
            }
        }
        $safe[$name] = $value
    }

    return $safe
}

function Set-ScopedEnv {
    param([System.Collections.IDictionary]$Values)

    $previous = @{}
    foreach ($key in $Values.Keys) {
        $envPath = "Env:{0}" -f $key
        $oldValue = [Environment]::GetEnvironmentVariable($key, "Process")
        $previous[$key] = $oldValue
        Set-Item -Path $envPath -Value ([string]$Values[$key])
    }
    return $previous
}

function Restore-ScopedEnv {
    param([System.Collections.IDictionary]$Previous)

    foreach ($key in $Previous.Keys) {
        $envPath = "Env:{0}" -f $key
        $oldValue = $Previous[$key]
        if ($null -eq $oldValue) {
            Remove-Item -Path $envPath -ErrorAction SilentlyContinue
        } else {
            Set-Item -Path $envPath -Value ([string]$oldValue)
        }
    }
}

function Invoke-PetdeskRuntimeDev {
    param(
        [string]$ResolvedRuntimeDir,
        [System.Collections.IDictionary]$RuntimeEnv
    )

    $pnpm = Get-Command pnpm -ErrorAction SilentlyContinue
    if (-not $pnpm) {
        throw "pnpm_not_found"
    }

    Push-Location -LiteralPath $ResolvedRuntimeDir
    $previousEnv = Set-ScopedEnv -Values $RuntimeEnv
    try {
        & $pnpm.Source tauri:dev
        return $LASTEXITCODE
    } finally {
        Restore-ScopedEnv -Previous $previousEnv
        Pop-Location
    }
}

function Invoke-PetdeskRuntimeRelease {
    param(
        [string]$ResolvedRuntimeExe,
        [System.Collections.IDictionary]$RuntimeEnv
    )

    $previousEnv = Set-ScopedEnv -Values $RuntimeEnv
    try {
        & $ResolvedRuntimeExe
        if ($null -eq $LASTEXITCODE) {
            return 0
        }
        return $LASTEXITCODE
    } finally {
        Restore-ScopedEnv -Previous $previousEnv
    }
}

function Invoke-PetdeskMvpSmoke {
    param(
        [string]$ProjectRoot,
        [string]$BaseUrl,
        [string]$Text,
        [switch]$StartupOnly
    )

    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        throw "python_not_found"
    }

    $smokeScript = Join-Path $ProjectRoot "scripts\tools\run_petdesk_mvp_smoke.py"
    if (-not (Test-Path -LiteralPath $smokeScript -PathType Leaf)) {
        throw "petdesk_mvp_smoke_not_found"
    }

    $arguments = @($smokeScript, "--base-url", $BaseUrl, "--text", $Text)
    if ($StartupOnly) {
        $arguments += "--startup-only"
    }

    & $python.Source @arguments
    $script:PetdeskMvpSmokeExitCode = $LASTEXITCODE
}

$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $scriptDir) {
    $scriptDir = (Get-Location).Path
}

$projectRoot = Find-AkaneProjectRoot -StartDir $scriptDir
$resolvedRuntimeDir = Resolve-PetdeskRuntimeDir -ProjectRoot $projectRoot -RequestedRuntimeDir $RuntimeDir
$resolvedBackendUrl = Normalize-BackendUrl -RawUrl $BackendUrl -Port $BackendPort
$resolvedRuntimeExe = $null
if ($RuntimeMode -eq "Release") {
    $resolvedRuntimeExe = Resolve-PetdeskRuntimeExe `
        -ProjectRoot $projectRoot `
        -ResolvedRuntimeDir $resolvedRuntimeDir `
        -RequestedRuntimeExe $RuntimeExe
}

Write-AkanePetdeskStep "INFO" ("Akane project: {0}" -f $projectRoot)
Write-AkanePetdeskStep "INFO" ("petdesk-runtime: {0}" -f $resolvedRuntimeDir)
Write-AkanePetdeskStep "INFO" ("Runtime mode: {0}" -f $RuntimeMode)
if ($resolvedRuntimeExe) {
    Write-AkanePetdeskStep "INFO" ("petdesk-runtime exe: {0}" -f $resolvedRuntimeExe)
}
Write-AkanePetdeskStep "INFO" ("Backend: {0}" -f $resolvedBackendUrl)

if ($CheckOnly) {
    Write-AkanePetdeskStep "OK" "CheckOnly completed. No backend or runtime process was launched."
    exit 0
}

if (-not $SkipBackend) {
    if (Test-LoopbackBackendUrl -Url $resolvedBackendUrl) {
        Start-AkaneBackendForPetdesk -ProjectRoot $projectRoot -Port $BackendPort -Reuse:$ReuseBackend
    } else {
        Write-AkanePetdeskStep "WARN" "Custom backend URL is not a simple loopback port; skipping backend start."
    }
}

$health = Get-PetdeskHealth -BaseUrl $resolvedBackendUrl -TimeoutSeconds $HealthTimeoutSeconds
$runtimeEnv = [ordered]@{
    VITE_PETDESK_BACKEND_URL = $resolvedBackendUrl
}

if ($null -eq $health) {
    Write-AkanePetdeskStep "WARN" "Could not fetch /pet/health; launching runtime with backend URL only."
} else {
    $healthRuntimeEnv = if ($health.PSObject.Properties.Name -contains "runtimeEnv") {
        $health.runtimeEnv
    } else {
        $null
    }
    $safeEnv = ConvertTo-SafePetdeskRuntimeEnv -RuntimeEnv $healthRuntimeEnv -BackendUrl $resolvedBackendUrl
    foreach ($key in $safeEnv.Keys) {
        $runtimeEnv[$key] = $safeEnv[$key]
    }
    if ($safeEnv.Count -gt 0) {
        Write-AkanePetdeskStep "INFO" ("Petdesk runtime env: {0}" -f (($safeEnv.Keys | Sort-Object) -join ","))
    } else {
        Write-AkanePetdeskStep "INFO" "No whitelisted petdesk runtime env was returned by /pet/health."
    }
}

if ($DryRun) {
    $runtimeEnvKeys = (($runtimeEnv.Keys | Sort-Object) -join ",")
    Write-AkanePetdeskStep "OK" ("DryRun completed. Runtime mode: {0}. Runtime env keys: {1}" -f $RuntimeMode, $runtimeEnvKeys)
    exit 0
}

if ($SmokeOnly -and $StartupSmokeOnly) {
    throw "choose either SmokeOnly or StartupSmokeOnly"
}
if ($RunSmokeBeforeLaunch -and $RunStartupSmokeBeforeLaunch) {
    throw "choose either RunSmokeBeforeLaunch or RunStartupSmokeBeforeLaunch"
}
if (($SmokeOnly -or $StartupSmokeOnly) -and ($RunSmokeBeforeLaunch -or $RunStartupSmokeBeforeLaunch)) {
    throw "choose either a smoke-only mode or a smoke-before-launch mode"
}

$runImplicitStartupSmoke = -not $SkipStartupSmoke `
    -and -not $SmokeOnly `
    -and -not $StartupSmokeOnly `
    -and -not $RunSmokeBeforeLaunch `
    -and -not $RunStartupSmokeBeforeLaunch

if ($SmokeOnly -or $StartupSmokeOnly -or $RunSmokeBeforeLaunch -or $RunStartupSmokeBeforeLaunch -or $runImplicitStartupSmoke) {
    $startupOnlySmoke = $StartupSmokeOnly -or $RunStartupSmokeBeforeLaunch -or $runImplicitStartupSmoke
    $smokeName = if ($startupOnlySmoke) { "petdesk startup smoke" } else { "petdesk MVP smoke" }
    Write-AkanePetdeskStep "INFO" ("Running {0}..." -f $smokeName)
    Invoke-PetdeskMvpSmoke `
        -ProjectRoot $projectRoot `
        -BaseUrl $resolvedBackendUrl `
        -Text $SmokeText `
        -StartupOnly:$startupOnlySmoke
    $smokeExitCode = $script:PetdeskMvpSmokeExitCode
    if ($smokeExitCode -ne 0) {
        exit $smokeExitCode
    }
    if ($SmokeOnly -or $StartupSmokeOnly) {
        if ($StartupSmokeOnly) {
            Write-AkanePetdeskStep "OK" "StartupSmokeOnly completed. No runtime process was launched."
        } else {
            Write-AkanePetdeskStep "OK" "SmokeOnly completed. No runtime process was launched."
        }
        exit 0
    }
}

if ($RuntimeMode -eq "Release") {
    Write-AkanePetdeskStep "INFO" "Starting petdesk-runtime release window..."
    $exitCode = Invoke-PetdeskRuntimeRelease -ResolvedRuntimeExe $resolvedRuntimeExe -RuntimeEnv $runtimeEnv
} else {
    Write-AkanePetdeskStep "INFO" "Starting petdesk-runtime Tauri dev window..."
    $exitCode = Invoke-PetdeskRuntimeDev -ResolvedRuntimeDir $resolvedRuntimeDir -RuntimeEnv $runtimeEnv
}
exit $exitCode
