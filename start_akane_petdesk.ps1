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
$parameters.RuntimeMode = $RuntimeMode
if ($RuntimeExe) {
    $parameters.RuntimeExe = $RuntimeExe
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
if ($SmokeOnly) {
    $parameters.SmokeOnly = $true
}
if ($StartupSmokeOnly) {
    $parameters.StartupSmokeOnly = $true
}
if ($SkipStartupSmoke) {
    $parameters.SkipStartupSmoke = $true
}
if ($RunSmokeBeforeLaunch) {
    $parameters.RunSmokeBeforeLaunch = $true
}
if ($RunStartupSmokeBeforeLaunch) {
    $parameters.RunStartupSmokeBeforeLaunch = $true
}
if ($SmokeText) {
    $parameters.SmokeText = $SmokeText
}

& $scriptPath @parameters
exit $LASTEXITCODE
