[CmdletBinding()]
param(
    [int]$BackendPort = 9999,
    [string]$BackendUrl = "",
    [string]$RuntimeDir = "",
    [string]$RuntimeExe = "",
    [switch]$BuildFirst,
    [switch]$SkipBuildWarmup,
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

if ($BuildFirst) {
    $buildScript = Join-Path $PSScriptRoot "scripts\build_petdesk_runtime_release.ps1"
    $buildArguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $buildScript)
    if ($RuntimeDir) {
        $buildArguments += @("-RuntimeDir", $RuntimeDir)
    }
    if ($CheckOnly) {
        $buildArguments += "-CheckOnly"
    }
    if ($DryRun) {
        $buildArguments += "-DryRun"
    }
    if ($SkipBuildWarmup) {
        $buildArguments += "-SkipWarmup"
    }

    & powershell @buildArguments
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

$startScript = Join-Path $PSScriptRoot "scripts\start_petdesk_runtime.ps1"
$parameters = @{
    BackendPort = $BackendPort
    HealthTimeoutSeconds = $HealthTimeoutSeconds
    RuntimeMode = "Release"
}
if ($BackendUrl) {
    $parameters.BackendUrl = $BackendUrl
}
if ($RuntimeDir) {
    $parameters.RuntimeDir = $RuntimeDir
}
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

& $startScript @parameters
exit $LASTEXITCODE
