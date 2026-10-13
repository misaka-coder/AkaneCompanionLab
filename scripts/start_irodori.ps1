param(
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$ConfigFile,
    [int]$WaitSeconds = 90
)
$ErrorActionPreference = 'Stop'
$iroConfigPath = (Resolve-Path -LiteralPath $ConfigFile).Path
$iroConfig = Get-Content -LiteralPath $iroConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$iroPort = [int]$iroConfig.port
$iroEndpoint = "http://127.0.0.1:$iroPort/akane/status"
function Get-IrodoriStatus {
    try { return Invoke-RestMethod -Uri $iroEndpoint -TimeoutSec 2 } catch { return $null }
}
$iroStatus = Get-IrodoriStatus
if ($null -ne $iroStatus -and $iroStatus.ready -eq $true) {
    Write-Output "Irodori 已就绪：$iroEndpoint"
    exit 0
}
if (Get-NetTCPConnection -LocalPort $iroPort -State Listen -ErrorAction SilentlyContinue) {
    throw '端口已被占用或服务正在准备，请检查已有服务状态。'
}
$iroLogDir = Split-Path -Parent $iroConfigPath
$iroStamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$iroOut = Join-Path $iroLogDir "server_$iroStamp.out.log"
$iroErr = Join-Path $iroLogDir "server_$iroStamp.err.log"
$iroScript = Join-Path $PSScriptRoot 'irodori_server.py'
$iroProcess = Start-Process -FilePath (Resolve-Path -LiteralPath $Python).Path `
    -ArgumentList @('-u', ('"' + $iroScript + '"'), '--config', ('"' + $iroConfigPath + '"')) `
    -WorkingDirectory $iroLogDir -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $iroOut -RedirectStandardError $iroErr
$iroStateFile = Join-Path $iroLogDir 'server_status.json'
@{ pid=$iroProcess.Id; config=$iroConfigPath; startedAt=(Get-Date).ToString('o'); stderr=$iroErr; stdout=$iroOut } |
    ConvertTo-Json | Set-Content -LiteralPath "$iroStateFile.tmp" -Encoding UTF8
Move-Item -LiteralPath "$iroStateFile.tmp" -Destination $iroStateFile -Force
$iroDeadline = (Get-Date).AddSeconds($WaitSeconds)
while ((Get-Date) -lt $iroDeadline) {
    $iroProcess.Refresh()
    if ($iroProcess.HasExited) { throw "服务启动失败，请查看 $iroErr" }
    $iroStatus = Get-IrodoriStatus
    if ($null -ne $iroStatus -and $iroStatus.ready -eq $true) {
        Write-Output "Irodori 已预热并就绪：$iroEndpoint"
        exit 0
    }
    Start-Sleep -Milliseconds 1000
}
Write-Output "Irodori 仍在准备，进程保留。状态：$iroEndpoint；日志：$iroErr"
exit 2
