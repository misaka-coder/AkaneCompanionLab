param(
    [switch]$SkipQuickRegression
)

$ErrorActionPreference = "Continue"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$script:Results = New-Object System.Collections.Generic.List[object]

function Add-Result {
    param(
        [string]$Name,
        [string]$Status,
        [string]$Detail
    )

    $script:Results.Add([pscustomobject]@{
        Check = $Name
        Status = $Status
        Detail = $Detail
    }) | Out-Null
}

function First-Line {
    param([object]$Value)

    $text = ($Value | Out-String).Trim()
    if (-not $text) {
        return ""
    }
    return (($text -split "`r?`n") | Select-Object -First 1)
}

function Get-PythonCommand {
    $venvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        return @($venvPython)
    }

    $venvPython = Join-Path $ProjectDir "venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        return @($venvPython)
    }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        return @($py.Source, "-3")
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return @($python.Source)
    }

    return @()
}

$script:PythonCommand = @(Get-PythonCommand)

function Invoke-Python {
    param([string[]]$Arguments)

    if (-not $script:PythonCommand -or $script:PythonCommand.Count -eq 0) {
        return [pscustomobject]@{ ExitCode = 127; Output = "Python not found" }
    }

    $exe = $script:PythonCommand[0]
    $prefix = @()
    if ($script:PythonCommand.Count -gt 1) {
        $prefix = $script:PythonCommand[1..($script:PythonCommand.Count - 1)]
    }

    $output = & $exe @prefix @Arguments 2>&1
    return [pscustomobject]@{
        ExitCode = $LASTEXITCODE
        Output = (($output | Out-String).Trim())
    }
}

function Test-CommandVersion {
    param(
        [string]$Name,
        [string]$Command,
        [string[]]$Arguments,
        [string]$MissingDetail
    )

    $resolved = Get-Command $Command -ErrorAction SilentlyContinue
    if (-not $resolved) {
        Add-Result $Name "WARN" $MissingDetail
        return
    }

    $path = $resolved.Path
    if (-not $path) {
        $path = $resolved.Source
    }
    $output = & $path @Arguments 2>&1
    $detail = First-Line $output
    if ($detail) {
        Add-Result $Name "OK" "$path | $detail"
    } else {
        Add-Result $Name "OK" $path
    }
}

function Test-PythonModule {
    param(
        [string]$Name,
        [string]$Code,
        [string]$MissingDetail
    )

    if (-not $script:PythonCommand -or $script:PythonCommand.Count -eq 0) {
        Add-Result $Name "WARN" "Python not found; cannot check module."
        return
    }

    $result = Invoke-Python @("-c", $Code)
    if ($result.ExitCode -eq 0) {
        $detail = First-Line $result.Output
        if (-not $detail) {
            $detail = "module import OK"
        }
        Add-Result $Name "OK" $detail
    } else {
        Add-Result $Name "WARN" $MissingDetail
    }
}

function Test-YtDlp {
    $resolved = Get-Command "yt-dlp" -ErrorAction SilentlyContinue
    if ($resolved) {
        $path = $resolved.Path
        if (-not $path) {
            $path = $resolved.Source
        }
        $output = & $path "--version" 2>&1
        Add-Result "yt-dlp" "OK" "$path | $(First-Line $output)"
        return
    }

    Test-PythonModule `
        -Name "yt-dlp" `
        -Code "from yt_dlp.version import __version__; print(__version__)" `
        -MissingDetail "yt-dlp not found; video page downloading may be unavailable."
}

function Test-Demucs {
    $resolved = Get-Command "demucs" -ErrorAction SilentlyContinue
    if ($resolved) {
        $path = $resolved.Path
        if (-not $path) {
            $path = $resolved.Source
        }
        Add-Result "demucs" "OK" "$path"
        return
    }

    Test-PythonModule `
        -Name "demucs" `
        -Code "import importlib.util, sys; spec = importlib.util.find_spec('demucs'); print('demucs module installed') if spec else sys.exit(1)" `
        -MissingDetail "demucs not found; vocal/instrumental separation will be unavailable."
}

function Test-DeepFilterNet {
    foreach ($candidate in @("deepFilter", "deep-filter")) {
        $resolved = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($resolved) {
            $path = $resolved.Path
            if (-not $path) {
                $path = $resolved.Source
            }
            Add-Result "deepfilternet" "OK" "$path"
            return
        }
    }

    Test-PythonModule `
        -Name "deepfilternet" `
        -Code "import importlib.util, sys; spec = importlib.util.find_spec('df'); print('df module installed') if spec else sys.exit(1)" `
        -MissingDetail "DeepFilterNet not found; AI voice cleaning will fall back or fail when quality=ai."
}

function Test-QuickRegression {
    if ($SkipQuickRegression) {
        Add-Result "quick regression" "SKIP" "Skipped by -SkipQuickRegression."
        return
    }

    if (-not $script:PythonCommand -or $script:PythonCommand.Count -eq 0) {
        Add-Result "quick regression" "FAIL" "Python not found; cannot run tests.quick_regression_suite."
        return
    }

    Push-Location $ProjectDir
    try {
        $result = Invoke-Python @("-m", "unittest", "tests.quick_regression_suite")
        if ($result.ExitCode -eq 0) {
            Add-Result "quick regression" "OK" "tests.quick_regression_suite passed."
        } else {
            $detail = First-Line $result.Output
            if (-not $detail) {
                $detail = "tests.quick_regression_suite failed."
            }
            Add-Result "quick regression" "FAIL" $detail
        }
    } finally {
        Pop-Location
    }
}

Write-Host "[INFO] Akane QQ workshop self-check"
Write-Host "[INFO] Project: $ProjectDir"

if ($script:PythonCommand.Count -gt 0) {
    $pythonVersion = Invoke-Python @("--version")
    Add-Result "python" "OK" "$($script:PythonCommand -join ' ') | $(First-Line $pythonVersion.Output)"
} else {
    Add-Result "python" "FAIL" "No usable Python interpreter was found."
}

Test-CommandVersion -Name "ffmpeg" -Command "ffmpeg" -Arguments @("-version") -MissingDetail "ffmpeg not found; media conversion/transcription prep will be unavailable."
Test-CommandVersion -Name "ffprobe" -Command "ffprobe" -Arguments @("-version") -MissingDetail "ffprobe not found; exact media inspection will be unavailable."
Test-YtDlp
Test-PythonModule -Name "faster-whisper" -Code "import faster_whisper; print(getattr(faster_whisper, '__version__', 'installed'))" -MissingDetail "faster-whisper not found; media transcription/subtitles will be unavailable."
Test-Demucs
Test-DeepFilterNet
Test-QuickRegression

Write-Host ""
$script:Results | Format-Table -AutoSize

$failures = @($script:Results | Where-Object { $_.Status -eq "FAIL" })
$warnings = @($script:Results | Where-Object { $_.Status -eq "WARN" })

if ($failures.Count -gt 0) {
    Write-Host "[FAIL] $($failures.Count) required check(s) failed." -ForegroundColor Red
    exit 1
}

if ($warnings.Count -gt 0) {
    Write-Host "[WARN] $($warnings.Count) optional/runtime dependency warning(s). See table above." -ForegroundColor Yellow
    exit 0
}

Write-Host "[OK] QQ workshop self-check passed." -ForegroundColor Green
exit 0
