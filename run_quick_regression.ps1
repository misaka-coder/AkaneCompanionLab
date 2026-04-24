param()

$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Invoke-QuickRegression {
    $venvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        & $venvPython -m unittest tests.quick_regression_suite
        return
    }

    $venvPython = Join-Path $ProjectDir "venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        & $venvPython -m unittest tests.quick_regression_suite
        return
    }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        & py -3 -m unittest tests.quick_regression_suite
        return
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        & python -m unittest tests.quick_regression_suite
        return
    }

    throw "No usable Python interpreter was found."
}

Push-Location $ProjectDir
try {
    Write-Host "[INFO] Running Akane quick regression suite..."
    Write-Host "[INFO] This suite covers resource visibility contracts and the highest-risk ambiguity checks."
    Invoke-QuickRegression
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
