param(
    [int]$TempRetentionDays = 7
)

$ErrorActionPreference = 'SilentlyContinue'
$cutoff = (Get-Date).AddDays(-$TempRetentionDays)

function Get-ItemBytes {
    param(
        [Parameter(Mandatory = $true)]
        [System.IO.FileSystemInfo]$Item
    )

    if (-not $Item.Exists) {
        return 0
    }

    if (-not $Item.PSIsContainer) {
        return $Item.Length
    }

    $sum = Get-ChildItem -LiteralPath $Item.FullName -Force -Recurse -ErrorAction SilentlyContinue |
        Where-Object { -not $_.PSIsContainer } |
        Measure-Object -Property Length -Sum
    return $sum.Sum
}

function Remove-ChildItems {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [datetime]$OlderThan,
        [switch]$UseAgeFilter
    )

    $removed = 0
    $failed = 0
    $freedBytes = 0

    if (-not (Test-Path -LiteralPath $Path)) {
        return [pscustomobject]@{
            Path      = $Path
            Removed   = 0
            Failed    = 0
            FreedMB   = 0
            Status    = 'missing'
        }
    }

    Get-ChildItem -LiteralPath $Path -Force -ErrorAction SilentlyContinue | ForEach-Object {
        if ($UseAgeFilter -and $_.LastWriteTime -gt $OlderThan) {
            return
        }

        $bytes = Get-ItemBytes -Item $_
        try {
            Remove-Item -LiteralPath $_.FullName -Force -Recurse -ErrorAction Stop
            $removed += 1
            $freedBytes += $bytes
        } catch {
            $failed += 1
        }
    }

    return [pscustomobject]@{
        Path      = $Path
        Removed   = $removed
        Failed    = $failed
        FreedMB   = [math]::Round(($freedBytes / 1MB), 2)
        Status    = 'ok'
    }
}

$results = @()

$tempRoots = @(
    'C:\Users\Lenovo\AppData\Local\Temp',
    'F:\Temp'
)

foreach ($root in $tempRoots) {
    $results += Remove-ChildItems -Path $root -OlderThan $cutoff -UseAgeFilter
}

$codeRunning = @(Get-Process -Name 'Code' -ErrorAction SilentlyContinue).Count -gt 0
if (-not $codeRunning) {
    $codeCaches = @(
        'C:\Users\Lenovo\AppData\Roaming\Code\CachedExtensionVSIXs',
        'C:\Users\Lenovo\AppData\Roaming\Code\Cache',
        'C:\Users\Lenovo\AppData\Roaming\Code\CachedData',
        'C:\Users\Lenovo\AppData\Roaming\Code\Crashpad',
        'C:\Users\Lenovo\AppData\Roaming\Code\GPUCache'
    )

    foreach ($path in $codeCaches) {
        $results += Remove-ChildItems -Path $path
    }
} else {
    $results += [pscustomobject]@{
        Path    = 'C:\Users\Lenovo\AppData\Roaming\Code'
        Removed = 0
        Failed  = 0
        FreedMB = 0
        Status  = 'skipped-code-open'
    }
}

$edgeRunning = @(Get-Process -Name 'msedge', 'msedgewebview2' -ErrorAction SilentlyContinue).Count -gt 0
if (-not $edgeRunning) {
    $edgeCaches = @(
        'C:\Users\Lenovo\AppData\Local\Microsoft\Edge\User Data\Crashpad',
        'C:\Users\Lenovo\AppData\Local\Microsoft\Edge\User Data\GrShaderCache',
        'C:\Users\Lenovo\AppData\Local\Microsoft\Edge\User Data\ShaderCache',
        'C:\Users\Lenovo\AppData\Local\Microsoft\Edge\User Data\component_crx_cache',
        'C:\Users\Lenovo\AppData\Local\Microsoft\Edge\User Data\extensions_crx_cache',
        'C:\Users\Lenovo\AppData\Local\Microsoft\Edge\User Data\optimization_guide_model_store',
        'C:\Users\Lenovo\AppData\Local\Microsoft\Edge\User Data\BrowserMetrics'
    )

    foreach ($path in $edgeCaches) {
        $results += Remove-ChildItems -Path $path
    }
} else {
    $results += [pscustomobject]@{
        Path    = 'C:\Users\Lenovo\AppData\Local\Microsoft\Edge\User Data'
        Removed = 0
        Failed  = 0
        FreedMB = 0
        Status  = 'skipped-edge-open'
    }
}

$results | Sort-Object FreedMB -Descending | Format-Table -AutoSize
