param(
    [switch]$NoBuild,
    [switch]$Rebuild,
    [switch]$OpenSettings,
    [switch]$SkipDesktop
)

$ErrorActionPreference = "Stop"
$launcher = Join-Path $PSScriptRoot "start_akane_cloud_personal.ps1"
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
    throw "cloud_personal_launcher_missing"
}

$arguments = @{}
if ($NoBuild) { $arguments.NoBuild = $true }
if ($Rebuild) { $arguments.Rebuild = $true }
if ($OpenSettings) { $arguments.OpenSettings = $true }
if ($SkipDesktop) { $arguments.SkipDesktop = $true }

& $launcher @arguments
