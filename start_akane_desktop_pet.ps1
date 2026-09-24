$ErrorActionPreference = "Stop"

function Find-ProjectRoot {
    param(
        [string]$StartDir
    )

    $current = (Resolve-Path -LiteralPath $StartDir).Path
    while ($current) {
        $packageJson = Join-Path $current "desktop_pet\package.json"
        if (Test-Path -LiteralPath $packageJson) {
            return $current
        }

        $parent = Split-Path -Parent $current
        if (-not $parent -or $parent -eq $current) {
            break
        }
        $current = $parent
    }

    throw "Could not locate project root. Expected desktop_pet\package.json under or above: $StartDir"
}

try {
    $scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
    if (-not $scriptDir) {
        $scriptDir = (Get-Location).Path
    }

    $projectDir = Find-ProjectRoot -StartDir $scriptDir
    $desktopPetDir = Join-Path $projectDir "desktop_pet"
    $packageJson = Join-Path $desktopPetDir "package.json"
    $nodeModulesDir = Join-Path $desktopPetDir "node_modules"

    if (-not (Test-Path -LiteralPath $packageJson)) {
        throw "desktop_pet/package.json not found: $packageJson"
    }

    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npm) {
        throw "npm was not found. Please install Node.js, then rerun this launcher."
    }

    Write-Host "[INFO] Akane desktop pet launcher"
    Write-Host "[INFO] Project dir: $projectDir"
    Write-Host "[INFO] This launcher does not start the Python backend."
    Write-Host "[INFO] Please start the backend first in another terminal:"
    Write-Host "       python launch_akane_memory_v01.py"
    Write-Host ""

    if (-not (Test-Path -LiteralPath $nodeModulesDir)) {
        Write-Host "[INFO] desktop_pet/node_modules not found. Running npm install..."
        Push-Location -LiteralPath $desktopPetDir
        try {
            & npm install
            if ($LASTEXITCODE -ne 0) {
                throw "npm install failed with exit code $LASTEXITCODE"
            }
        } finally {
            Pop-Location
        }
    }

    Write-Host "[INFO] Starting Akane desktop pet..."
    Push-Location -LiteralPath $desktopPetDir
    try {
        & npm start
        $exitCode = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 }
        exit $exitCode
    } finally {
        Pop-Location
    }
} catch {
    Write-Host "[ERROR] $($_.Exception.Message)"
    exit 1
}
