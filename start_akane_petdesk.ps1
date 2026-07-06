[CmdletBinding()]
param(
    [int]$BackendPort = 9999,
    [string]$BackendUrl = "",
    [string]$RuntimeDir = "",
    [switch]$SkipBackend,
    [switch]$ReuseBackend,
    [switch]$CheckOnly,
    [switch]$DryRun,
    [int]$HealthTimeoutSeconds = 45
)

$ErrorActionPreference = "Stop"
$scriptPath = Join-Path $PSScriptRoot "scripts\start_petdesk_runtime.ps1"
$parameters = @{
    BackendPort = $BackendPort
    HealthTimeoutSeconds = $HealthTimeoutSeconds
}
if ($BackendUrl) {
    $parameters.BackendUrl = $BackendUrl
}
if ($RuntimeDir) {
    $parameters.RuntimeDir = $RuntimeDir
}
if ($SkipBackend) {
    $parameters.SkipBackend = $true
}
if ($ReuseBackend) {
    $parameters.ReuseBackend = $true
}
if ($CheckOnly) {
    $parameters.CheckOnly = $true
}
if ($DryRun) {
    $parameters.DryRun = $true
}

& $scriptPath @parameters
exit $LASTEXITCODE
