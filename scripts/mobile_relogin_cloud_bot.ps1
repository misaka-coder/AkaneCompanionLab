[CmdletBinding()]
param(
    [ValidateSet("status", "personal", "finance", "both")]
    [string]$Bot = "status",
    [Parameter(Mandatory = $true)]
    [string]$HostName,
    [string]$UserName = "akane-recovery",
    [string]$KeyPath = "",
    [string]$KnownHostsPath = "",
    [string]$OutputDirectory = "",
    [switch]$NoOpen
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$global:OutputEncoding = New-Object System.Text.UTF8Encoding($false)

function Resolve-NativeCommand {
    param([Parameter(Mandatory = $true)][string]$Name)
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "missing_command:$Name"
    }
    foreach ($candidate in @($command.Source, $command.Path, $command.Definition)) {
        if ($candidate) {
            return [string]$candidate
        }
    }
    throw "missing_command:$Name"
}

$ssh = Resolve-NativeCommand -Name "ssh"
$sftp = Resolve-NativeCommand -Name "sftp"
$packageRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$resolvedKey = if ($KeyPath.Trim()) {
    [System.IO.Path]::GetFullPath($KeyPath.Trim())
} else {
    Join-Path $packageRoot "akane-mobile-recovery"
}
$resolvedKnownHosts = if ($KnownHostsPath.Trim()) {
    [System.IO.Path]::GetFullPath($KnownHostsPath.Trim())
} else {
    Join-Path $packageRoot "known_hosts"
}
$resolvedOutput = if ($OutputDirectory.Trim()) {
    [System.IO.Path]::GetFullPath($OutputDirectory.Trim())
} else {
    Join-Path $packageRoot "二维码"
}

foreach ($requiredFile in @($resolvedKey, $resolvedKnownHosts)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "recovery_package_file_missing:$requiredFile"
    }
}
New-Item -ItemType Directory -Path $resolvedOutput -Force | Out-Null

$commonOptions = @(
    "-i", $resolvedKey,
    "-o", "IdentitiesOnly=yes",
    "-o", "BatchMode=yes",
    "-o", "PasswordAuthentication=no",
    "-o", "StrictHostKeyChecking=yes",
    "-o", "UserKnownHostsFile=$resolvedKnownHosts",
    "-o", "ConnectTimeout=10"
)
$target = "$UserName@$HostName"

Write-Host "Requesting Akane Bot recovery action: $Bot"
$output = @(& $ssh @commonOptions $target $Bot 2>&1 | ForEach-Object { [string]$_ })
$exitCode = $LASTEXITCODE
$output | ForEach-Object { Write-Host $_ }

$files = @(
    $output |
        Where-Object { $_.StartsWith("AKANE_FILE=") } |
        ForEach-Object { $_.Substring("AKANE_FILE=".Length).Trim() } |
        Where-Object { $_ -match '^(personal|finance)\.png$' } |
        Select-Object -Unique
)

foreach ($fileName in $files) {
    $localPath = Join-Path $resolvedOutput $fileName
    $batchPath = Join-Path ([System.IO.Path]::GetTempPath()) ("akane-sftp-" + [guid]::NewGuid().ToString("N") + ".txt")
    try {
        $sftpLocalPath = $localPath.Replace("\", "/")
        [System.IO.File]::WriteAllText(
            $batchPath,
            "get $fileName `"$sftpLocalPath`"`n",
            (New-Object System.Text.UTF8Encoding($false))
        )
        & $sftp @commonOptions "-b" $batchPath $target
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $localPath -PathType Leaf)) {
            throw "qr_download_failed:$fileName"
        }
    } finally {
        Remove-Item -LiteralPath $batchPath -Force -ErrorAction SilentlyContinue
    }
    Write-Host "Fresh QR downloaded: $localPath"
    if (-not $NoOpen) {
        Start-Process -FilePath $localPath
    }
}

if ($exitCode -ne 0) {
    throw "recovery_action_failed:$Bot"
}
if ($files.Count -eq 0 -and $Bot -ne "status") {
    Write-Host "No QR was needed; the selected Bot already restored its saved login."
}
if ($files.Count -gt 0) {
    Write-Host "Scan within 10 minutes. On one phone, save the image and use QQ Scan > Album."
}
