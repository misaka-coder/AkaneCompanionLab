[CmdletBinding()]
param()

$verificationEntry = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "scripts\verify_akane.ps1"
& $verificationEntry -Tier Quick
exit $LASTEXITCODE
