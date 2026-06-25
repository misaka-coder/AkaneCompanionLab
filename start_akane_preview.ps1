param(
    [int]$Port = 9998,
    [string]$BindHost = "0.0.0.0",
    [string]$LocalHostAddr = "127.0.0.1"
)

$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppScript = Join-Path $ProjectDir "launch_akane_memory_v01.py"
$HealthUrl = "http://$LocalHostAddr`:$Port/health"
$MainUrl = "http://$LocalHostAddr`:$Port/"
$ResourcePreviewUrl = "http://$LocalHostAddr`:$Port/resource-preview"

function Test-ServerReady {
    param(
        [string]$HealthUrl
    )

    try {
        $resp = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 2
        return $resp.status -eq "ok"
    } catch {
        return $false
    }
}

function Get-ListeningConnection {
    param(
        [int]$Port
    )

    return Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
        Where-Object { $_.State -eq "Listen" } |
        Select-Object -First 1
}

function Get-PythonLaunchCommand {
    param(
        [string]$ProjectDir,
        [string]$AppScript,
        [string]$BindHost,
        [int]$Port
    )

    $venvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        return "`$env:COMPANION_HOST='$BindHost'; `$env:COMPANION_PORT='$Port'; & '$venvPython' '$AppScript'"
    }

    $venvPython = Join-Path $ProjectDir "venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        return "`$env:COMPANION_HOST='$BindHost'; `$env:COMPANION_PORT='$Port'; & '$venvPython' '$AppScript'"
    }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        return "`$env:COMPANION_HOST='$BindHost'; `$env:COMPANION_PORT='$Port'; & py -3 '$AppScript'"
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return "`$env:COMPANION_HOST='$BindHost'; `$env:COMPANION_PORT='$Port'; & python '$AppScript'"
    }

    throw "No usable Python interpreter was found."
}

function Get-LanPreviewUrls {
    param(
        [int]$Port
    )

    $items = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object {
            $_.IPAddress -and
            $_.IPAddress -notlike "127.*" -and
            $_.IPAddress -notlike "169.254*" -and
            $_.IPAddress -notlike "198.18*"
        } |
        Select-Object -Property InterfaceAlias, IPAddress -Unique

    return @($items | ForEach-Object {
        [PSCustomObject]@{
            InterfaceAlias = $_.InterfaceAlias
            MainUrl = "http://$($_.IPAddress):$Port/"
            ResourcePreviewUrl = "http://$($_.IPAddress):$Port/resource-preview"
        }
    })
}

try {
    if (-not (Test-Path -LiteralPath $AppScript)) {
        throw "App script not found: $AppScript"
    }

    if (Test-ServerReady -HealthUrl $HealthUrl) {
        Write-Host "[INFO] AkaneCompanionLab server is already running."
        Write-Host "[INFO] Local main UI: $MainUrl"
        Write-Host "[INFO] Local resource preview: $ResourcePreviewUrl"
        $lanUrls = Get-LanPreviewUrls -Port $Port
        foreach ($item in $lanUrls) {
            Write-Host "[INFO] LAN main UI ($($item.InterfaceAlias)): $($item.MainUrl)"
            Write-Host "[INFO] LAN resource preview ($($item.InterfaceAlias)): $($item.ResourcePreviewUrl)"
        }
        Start-Process $MainUrl | Out-Null
        Write-Host "[INFO] Main UI opened: $MainUrl"
        exit 0
    }

    $listener = Get-ListeningConnection -Port $Port
    if ($listener) {
        $processId = $listener.OwningProcess
        $proc = $null
        try {
            $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $processId"
        } catch {
        }

        if ($proc -and $proc.CommandLine -like "*$AppScript*") {
            Write-Host "[WARN] Found stale AkaneCompanionLab listener on port $Port. Restarting it..."
            Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 1
        } else {
            $procLabel = if ($proc) { "$($proc.Name) ($processId)" } else { "PID $processId" }
            throw "Port $Port is already in use by $procLabel. Please close that process first."
        }
    }

    $launchCommand = Get-PythonLaunchCommand -ProjectDir $ProjectDir -AppScript $AppScript -BindHost $BindHost -Port $Port

    Write-Host "[INFO] Starting AkaneCompanionLab VN server on port $Port..."
    Write-Host "[INFO] Local main UI: $MainUrl"
    Write-Host "[INFO] Local resource preview: $ResourcePreviewUrl"
    $lanUrls = Get-LanPreviewUrls -Port $Port
    foreach ($item in $lanUrls) {
        Write-Host "[INFO] LAN main UI ($($item.InterfaceAlias)): $($item.MainUrl)"
        Write-Host "[INFO] LAN resource preview ($($item.InterfaceAlias)): $($item.ResourcePreviewUrl)"
    }
    Start-Process -FilePath "powershell.exe" -WorkingDirectory $ProjectDir -ArgumentList @(
        "-NoExit",
        "-ExecutionPolicy", "Bypass",
        "-Command", "Set-Location -LiteralPath '$ProjectDir'; $launchCommand"
    ) | Out-Null

    $isReady = $false
    for ($i = 0; $i -lt 20; $i++) {
        if (Test-ServerReady -HealthUrl $HealthUrl) {
            $isReady = $true
            break
        }
        Start-Sleep -Seconds 1
    }

    if (-not $isReady) {
        throw "Server window started, but /health did not return ok within 20 seconds."
    }

    Start-Process $MainUrl | Out-Null
    Write-Host "[INFO] Main UI opened: $MainUrl"
    exit 0
} catch {
    Write-Host "[ERROR] $($_.Exception.Message)"
    Write-Host "[INFO] Project dir: $ProjectDir"
    exit 1
}
