[CmdletBinding()]
param(
    [int]$BackendPort = 9999,
    [string]$BackendUrl = "",
    [switch]$CheckOnly,
    [switch]$DryRun,
    [int]$HealthTimeoutSeconds = 30
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

function Write-AkanePetdeskBundleStep {
    param(
        [string]$Level,
        [string]$Message
    )

    Write-Host ("[{0}] {1}" -f $Level, $Message)
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

$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $scriptDir) {
    $scriptDir = (Get-Location).Path
}
$bundleRoot = [System.IO.Path]::GetFullPath((Join-Path $scriptDir ".."))
$runtimeExe = [System.IO.Path]::GetFullPath((Join-Path $bundleRoot "runtime\petdesk_runtime.exe"))
$resolvedBackendUrl = Normalize-BackendUrl -RawUrl $BackendUrl -Port $BackendPort

Write-AkanePetdeskBundleStep "INFO" ("Bundle root: {0}" -f $bundleRoot)
Write-AkanePetdeskBundleStep "INFO" ("Runtime exe: {0}" -f $runtimeExe)
Write-AkanePetdeskBundleStep "INFO" ("Backend: {0}" -f $resolvedBackendUrl)

if ($CheckOnly) {
    if (Test-Path -LiteralPath $runtimeExe -PathType Leaf) {
        Write-AkanePetdeskBundleStep "OK" "Bundled runtime exe exists."
    } else {
        Write-AkanePetdeskBundleStep "WARN" "Bundled runtime exe is not present in this source-tree template location."
    }
    Write-AkanePetdeskBundleStep "OK" "CheckOnly completed. No runtime process was launched."
    exit 0
}

if (-not (Test-Path -LiteralPath $runtimeExe -PathType Leaf)) {
    throw "petdesk_runtime_bundle_exe_not_found: $runtimeExe"
}

$runtimeEnv = [ordered]@{
    VITE_PETDESK_BACKEND_URL = $resolvedBackendUrl
}
$health = Get-PetdeskHealth -BaseUrl $resolvedBackendUrl -TimeoutSeconds $HealthTimeoutSeconds
if ($null -eq $health) {
    Write-AkanePetdeskBundleStep "WARN" "Could not fetch /pet/health; launching runtime with backend URL only."
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
        Write-AkanePetdeskBundleStep "INFO" ("Petdesk runtime env: {0}" -f (($safeEnv.Keys | Sort-Object) -join ","))
    } else {
        Write-AkanePetdeskBundleStep "INFO" "No whitelisted petdesk runtime env was returned by /pet/health."
    }
}

if ($DryRun) {
    $runtimeEnvKeys = (($runtimeEnv.Keys | Sort-Object) -join ",")
    Write-AkanePetdeskBundleStep "OK" ("DryRun completed. Runtime env keys: {0}" -f $runtimeEnvKeys)
    exit 0
}

$previousEnv = Set-ScopedEnv -Values $runtimeEnv
try {
    & $runtimeExe
    if ($null -eq $LASTEXITCODE) {
        exit 0
    }
    exit $LASTEXITCODE
} finally {
    Restore-ScopedEnv -Previous $previousEnv
}
