[CmdletBinding()]
param(
    [ValidateSet("Auto", "Desktop", "Web")]
    [string]$Mode = "Auto",
    [int]$BackendPort = 9999,
    [switch]$PrepareOnly,
    [switch]$CheckOnly,
    [switch]$ForcePythonInstall,
    [switch]$KeepWindowOpen
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "akane_data_root.ps1")

function Write-AkaneStep {
    param(
        [string]$Level,
        [string]$Message
    )

    $color = switch ($Level) {
        "OK" { "Green" }
        "WARN" { "Yellow" }
        "FAIL" { "Red" }
        default { "Cyan" }
    }
    Write-Host ("[{0}] {1}" -f $Level, $Message) -ForegroundColor $color
}

function Get-ProjectRoot {
    $root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
    if (
        -not (Test-Path -LiteralPath (Join-Path $root "launch_akane_memory_v01.py") -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $root "start_akane_next.ps1") -PathType Leaf)
    ) {
        throw "project_root_not_found"
    }
    return $root
}

function Get-SystemPython {
    $candidates = @()
    $py = Get-Command "py" -ErrorAction SilentlyContinue
    if ($py) {
        $candidates += [pscustomobject]@{
            Command = $py.Source
            Args = @("-3.11")
            Label = "Python 3.11 via py"
        }
    }
    $python = Get-Command "python" -ErrorAction SilentlyContinue
    if ($python) {
        $candidates += [pscustomobject]@{
            Command = $python.Source
            Args = @()
            Label = "Python"
        }
    }

    foreach ($candidate in $candidates) {
        try {
            $versionText = & $candidate.Command @($candidate.Args) -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" 2>$null
            if ($LASTEXITCODE -ne 0) {
                continue
            }
            $version = [version]([string]$versionText).Trim()
            if ($version.Major -eq 3 -and $version.Minor -ge 11) {
                return [pscustomobject]@{
                    Command = $candidate.Command
                    Args = $candidate.Args
                    Label = $candidate.Label
                    Version = $version
                }
            }
        } catch {
            continue
        }
    }
    return $null
}

function Test-PythonImports {
    param(
        [string]$PythonPath,
        [string[]]$PrefixArgs = @()
    )

    $previousNativeErrorPreference = $null
    $hasNativeErrorPreference = [bool](Get-Variable -Name PSNativeCommandUseErrorActionPreference -Scope Local -ErrorAction SilentlyContinue)
    if ($hasNativeErrorPreference) {
        $previousNativeErrorPreference = $PSNativeCommandUseErrorActionPreference
    }

    try {
        if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
            $PSNativeCommandUseErrorActionPreference = $false
        }
        & $PythonPath @PrefixArgs -c "import fastapi, uvicorn, chromadb, openai, requests, pydantic_settings, edge_tts" *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    } finally {
        if ($hasNativeErrorPreference) {
            $PSNativeCommandUseErrorActionPreference = $previousNativeErrorPreference
        }
    }
}

function Get-FileSha256 {
    param([string]$Path)

    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        $bytes = $sha256.ComputeHash($stream)
        return ([System.BitConverter]::ToString($bytes)).Replace("-", "")
    } finally {
        $stream.Dispose()
        $sha256.Dispose()
    }
}

function Get-PipIndexArgs {
    $indexUrl = [string]$env:AKANE_PIP_INDEX_URL
    if ([string]::IsNullOrWhiteSpace($indexUrl)) {
        $indexUrl = [string]$env:PIP_INDEX_URL
    }
    if ([string]::IsNullOrWhiteSpace($indexUrl)) {
        return @()
    }
    return @("--index-url", $indexUrl.Trim())
}

function Format-PipNetworkHint {
    return (
        "If pip is slow or blocked by your network, set a PyPI mirror before launching, " +
        "for example: `$env:AKANE_PIP_INDEX_URL='https://pypi.tuna.tsinghua.edu.cn/simple'; .\启动_Akane.bat"
    )
}

function Format-HuggingFaceModelHint {
    return (
        "HuggingFace embedding downloads can be slow on some networks. " +
        "Keep EMBEDDING_LOCAL_FILES_ONLY=true for no startup download, or set " +
        "HF_ENDPOINT=https://hf-mirror.com in .env before enabling online model downloads."
    )
}

function Ensure-PythonEnvironment {
    param(
        [string]$Root,
        [switch]$ReadOnly
    )

    $venvPython = Join-Path $Root ".venv\Scripts\python.exe"
    $requirementsPath = Join-Path $Root "requirements.txt"
    $stampPath = Join-Path $Root ".venv\.akane-requirements.sha256"
    $requirementsHash = Get-FileSha256 -Path $requirementsPath

    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        $systemPython = Get-SystemPython
        if ($null -eq $systemPython) {
            throw "Python 3.11 or newer was not found. Install Python 3.11 from python.org, enable 'Add Python to PATH', then run this launcher again."
        }
        if ($ReadOnly) {
            if (-not (Test-PythonImports -PythonPath $systemPython.Command -PrefixArgs $systemPython.Args)) {
                throw "Python is available, but Akane dependencies are not installed. Run 启动_Akane.bat once without -CheckOnly."
            }
            Write-AkaneStep "OK" ("{0} {1} is available; project .venv has not been created yet." -f $systemPython.Label, $systemPython.Version)
            return $systemPython.Command
        }

        Write-AkaneStep "INFO" ("Creating .venv with {0} {1}..." -f $systemPython.Label, $systemPython.Version)
        & $systemPython.Command @($systemPython.Args) -m venv (Join-Path $Root ".venv")
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
            throw "python_venv_creation_failed"
        }
    }

    $installedHash = ""
    if (Test-Path -LiteralPath $stampPath -PathType Leaf) {
        $installedHash = ([System.IO.File]::ReadAllText($stampPath)).Trim()
    }
    $importsReady = Test-PythonImports -PythonPath $venvPython
    $requiresInstall = $ForcePythonInstall -or -not $importsReady -or $installedHash -ne $requirementsHash

    if ($ReadOnly) {
        if (-not $importsReady) {
            throw "Akane Python dependencies are incomplete. Run 启动_Akane.bat once without -CheckOnly."
        }
        Write-AkaneStep "OK" "Python environment is ready."
        if ($installedHash -ne $requirementsHash) {
            Write-AkaneStep "WARN" "requirements.txt changed after the last bootstrap; the next normal launch will update dependencies."
        }
        return $venvPython
    }

    if ($requiresInstall) {
        Write-AkaneStep "INFO" "Installing Python dependencies. The first run can take several minutes..."
        $pipIndexArgs = @(Get-PipIndexArgs)
        if ($pipIndexArgs.Count -gt 0) {
            Write-AkaneStep "INFO" "Using configured Python package index from AKANE_PIP_INDEX_URL/PIP_INDEX_URL."
        }
        & $venvPython -m pip install --disable-pip-version-check --retries 5 --timeout 60 @pipIndexArgs --upgrade pip
        if ($LASTEXITCODE -ne 0) {
            throw ("pip_upgrade_failed. {0}" -f (Format-PipNetworkHint))
        }
        & $venvPython -m pip install --disable-pip-version-check --retries 5 --timeout 60 @pipIndexArgs -r $requirementsPath
        if ($LASTEXITCODE -ne 0) {
            throw ("python_dependency_install_failed. {0}" -f (Format-PipNetworkHint))
        }
        [System.IO.File]::WriteAllText($stampPath, $requirementsHash)
        Write-AkaneStep "OK" "Python dependencies are ready."
    } else {
        Write-AkaneStep "OK" "Python dependencies are already up to date."
    }
    return $venvPython
}

function Get-EnvValue {
    param(
        [string]$Path,
        [string]$Name
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return ""
    }
    foreach ($line in [System.IO.File]::ReadAllLines($Path)) {
        if ($line -match ("^\s*{0}\s*=(.*)$" -f [regex]::Escape($Name))) {
            return ([string]$Matches[1]).Trim().Trim('"').Trim("'")
        }
    }
    return ""
}

function Test-ModelServiceConfigured {
    param([string]$DataRoot)

    $configPath = Join-Path $DataRoot "users_data\_local\model_service.json"
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
        return $false
    }
    try {
        $payload = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $protocol = [string]($payload.protocol)
        $baseUrl = [string]($payload.base_url)
        $model = [string]($payload.chat_model)
        $apiKey = [string]($payload.api_key)
        return [bool](
            $baseUrl.Trim() -and
            $model.Trim() -and
            ($protocol.Trim().ToLowerInvariant() -eq "ollama" -or $apiKey.Trim())
        )
    } catch {
        Write-AkaneStep "WARN" "The saved model service config is invalid; open the control center to repair it."
        return $false
    }
}

function Ensure-EnvironmentFile {
    param(
        [string]$Root,
        [string]$DataRoot,
        [switch]$ReadOnly
    )

    $envPath = Join-Path $Root ".env"
    $examplePath = Join-Path $Root ".env.example"
    $created = $false
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
        if ($ReadOnly) {
            Write-AkaneStep "WARN" ".env does not exist yet; a normal launch will create it from .env.example."
            return [pscustomobject]@{ Path = $envPath; Created = $false; LlmConfigured = $false }
        }
        Copy-Item -LiteralPath $examplePath -Destination $envPath
        $created = $true
        Write-AkaneStep "OK" "Created local .env configuration."
    }

    $textKey = Get-EnvValue -Path $envPath -Name "TEXT_API_KEY"
    $chatKey = Get-EnvValue -Path $envPath -Name "CHAT_API_KEY"
    $textProtocol = (Get-EnvValue -Path $envPath -Name "TEXT_API_PROTOCOL").ToLowerInvariant()
    $chatProtocol = (Get-EnvValue -Path $envPath -Name "CHAT_API_PROTOCOL").ToLowerInvariant()
    $textBaseUrl = (Get-EnvValue -Path $envPath -Name "TEXT_BASE_URL").ToLowerInvariant()
    $chatBaseUrl = (Get-EnvValue -Path $envPath -Name "CHAT_BASE_URL").ToLowerInvariant()
    $embeddingProvider = (Get-EnvValue -Path $envPath -Name "EMBEDDING_PROVIDER").ToLowerInvariant()
    $embeddingLocalFilesOnly = (Get-EnvValue -Path $envPath -Name "EMBEDDING_LOCAL_FILES_ONLY").ToLowerInvariant()
    $hfEndpoint = Get-EnvValue -Path $envPath -Name "HF_ENDPOINT"
    if (-not $embeddingProvider) {
        $embeddingProvider = "auto"
    }
    $downloadsHuggingFaceModel = (
        $embeddingProvider -in @("auto", "huggingface", "hf", "sentence-transformer", "sentence-transformers") -and
        $embeddingLocalFilesOnly -in @("false", "0", "no", "off") -and
        -not $hfEndpoint -and
        -not $env:HF_ENDPOINT
    )
    if ($downloadsHuggingFaceModel) {
        Write-AkaneStep "WARN" (Format-HuggingFaceModelHint)
    }
    $ollamaConfigured = (
        $textProtocol -eq "ollama" -or
        $chatProtocol -eq "ollama" -or
        $textBaseUrl.Contains("11434") -or
        $chatBaseUrl.Contains("11434")
    )
    $configured = [bool](
        (Test-ModelServiceConfigured -DataRoot $DataRoot) -or
        $textKey -or
        $chatKey -or
        $ollamaConfigured
    )
    if ($configured) {
        Write-AkaneStep "OK" "LLM connection appears configured."
    } else {
        Write-AkaneStep "WARN" "No model service is configured yet. Akane will open the visible model settings after launch."
    }
    return [pscustomobject]@{ Path = $envPath; Created = $created; LlmConfigured = $configured }
}

function Test-DesktopToolchain {
    $missing = [System.Collections.Generic.List[string]]::new()
    foreach ($command in @("node", "npm", "cargo", "rustc")) {
        if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
            $missing.Add($command)
        }
    }
    return [pscustomobject]@{
        Ready = $missing.Count -eq 0
        Missing = @($missing)
    }
}

function Format-DesktopToolchainHint {
    param([string[]]$Missing)

    $missingText = if ($Missing -and $Missing.Count -gt 0) {
        $Missing -join ", "
    } else {
        "unknown desktop build tools"
    }
    return (
        "Missing: {0}. To use Desktop mode, install Node.js LTS from https://nodejs.org/ " +
        "(includes npm) and Rust from https://rustup.rs/. Windows winget alternative: " +
        "winget install OpenJS.NodeJS.LTS Rustlang.Rustup. After installing, reopen PowerShell " +
        "or VS Code so PATH refreshes, then run 启动_Akane.bat again."
    ) -f $missingText
}

function Resolve-LaunchMode {
    param(
        [string]$RequestedMode,
        [string]$Root
    )

    if ($RequestedMode -ne "Auto") {
        return $RequestedMode
    }
    $releaseExe = Join-Path $Root "desktop_pet_next\src-tauri\target\release\akane_desktop_pet_next.exe"
    if (Test-Path -LiteralPath $releaseExe -PathType Leaf) {
        return "Desktop"
    }
    $toolchain = Test-DesktopToolchain
    if ($toolchain.Ready) {
        return "Desktop"
    }
    Write-AkaneStep "WARN" ("Desktop build tools are incomplete; using the Web client for this launch. {0}" -f (Format-DesktopToolchainHint -Missing $toolchain.Missing))
    return "Web"
}

function Wait-BackendReady {
    param(
        [int]$Port,
        [int]$TimeoutSeconds = 60
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $url = "http://127.0.0.1:{0}/health" -f $Port
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $health = Invoke-RestMethod -Uri $url -TimeoutSec 2
            if ([string]$health.status -eq "ok") {
                return $true
            }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    return $false
}

function Start-WebMode {
    param(
        [string]$Root,
        [int]$Port,
        [switch]$OpenModelSettings
    )

    & (Join-Path $Root "start_akane_next.ps1") -BackendPort $Port -SkipDesktop
    if (-not (Wait-BackendReady -Port $Port)) {
        throw "Backend did not become healthy within 60 seconds. Check the Akane user-data logs folder."
    }
    $url = if ($OpenModelSettings) {
        "http://127.0.0.1:{0}/?configure=model" -f $Port
    } else {
        "http://127.0.0.1:{0}/" -f $Port
    }
    Start-Process $url
    Write-AkaneStep "OK" ("Web client opened: {0}" -f $url)
}

function Start-DesktopMode {
    param(
        [string]$Root,
        [int]$Port,
        [switch]$OpenModelSettings
    )

    $toolchain = Test-DesktopToolchain
    $releaseExe = Join-Path $Root "desktop_pet_next\src-tauri\target\release\akane_desktop_pet_next.exe"
    if (-not (Test-Path -LiteralPath $releaseExe -PathType Leaf) -and -not $toolchain.Ready) {
        throw ("Desktop mode needs a prebuilt release or Node.js + Rust. {0}" -f (Format-DesktopToolchainHint -Missing $toolchain.Missing))
    }
    & (Join-Path $Root "start_akane_next.ps1") -BackendPort $Port -OpenSettings:$OpenModelSettings
    Write-AkaneStep "OK" "Desktop pet launch requested."
}

$projectRoot = Get-ProjectRoot
Write-Host ""
Write-Host "AkaneCompanionLab Windows Bootstrap" -ForegroundColor Magenta
Write-Host ("Project: {0}" -f $projectRoot)
Write-Host ""

$exitCode = 0
try {
    $dataStatus = Initialize-AkaneDataRoot -ProjectRoot $projectRoot -ReadOnly:$CheckOnly
    $env:AKANE_DATA_ROOT = $dataStatus.Root
    $env:AKANE_DATA_ROOT_READY = "1"
    if (-not $CheckOnly) {
        if ($dataStatus.Failed -gt 0) {
            Write-AkaneStep "WARN" ("User data root is ready, but {0} legacy files could not be copied." -f $dataStatus.Failed)
        } elseif ($dataStatus.Copied -gt 0) {
            Write-AkaneStep "OK" ("User data root is ready; migrated {0} legacy files without overwriting existing data." -f $dataStatus.Copied)
        } else {
            Write-AkaneStep "OK" "User data root is ready."
        }
    }
    $null = Ensure-PythonEnvironment -Root $projectRoot -ReadOnly:$CheckOnly
    $envStatus = Ensure-EnvironmentFile -Root $projectRoot -DataRoot $dataStatus.Root -ReadOnly:$CheckOnly
    $launchMode = Resolve-LaunchMode -RequestedMode $Mode -Root $projectRoot
    Write-AkaneStep "INFO" ("Selected client: {0}" -f $launchMode)

    if ($CheckOnly) {
        if ($launchMode -eq "Desktop") {
            $toolchain = Test-DesktopToolchain
            $releaseExe = Join-Path $projectRoot "desktop_pet_next\src-tauri\target\release\akane_desktop_pet_next.exe"
            if ($toolchain.Ready -or (Test-Path -LiteralPath $releaseExe -PathType Leaf)) {
                Write-AkaneStep "OK" "Desktop runtime is buildable or already built."
            } else {
                throw ("Desktop runtime is not ready. {0}" -f (Format-DesktopToolchainHint -Missing $toolchain.Missing))
            }
        }
        Write-AkaneStep "OK" "Bootstrap check completed without changing local files."
    } elseif ($PrepareOnly) {
        Write-AkaneStep "OK" "Preparation completed. Nothing was launched."
    } elseif ($launchMode -eq "Desktop") {
        try {
            Start-DesktopMode -Root $projectRoot -Port $BackendPort -OpenModelSettings:(-not $envStatus.LlmConfigured)
        } catch {
            if ($Mode -ne "Auto") {
                throw
            }
            Write-AkaneStep "WARN" ("Desktop launch failed; falling back to Web. {0}" -f $_.Exception.Message)
            Start-WebMode -Root $projectRoot -Port $BackendPort -OpenModelSettings:(-not $envStatus.LlmConfigured)
        }
    } else {
        Start-WebMode -Root $projectRoot -Port $BackendPort -OpenModelSettings:(-not $envStatus.LlmConfigured)
    }

} catch {
    $exitCode = 1
    Write-AkaneStep "FAIL" $_.Exception.Message
    Write-Host ""
    Write-Host "See README.md -> Windows one-click start for supported prerequisites." -ForegroundColor Yellow
}

if ($KeepWindowOpen -or $exitCode -ne 0) {
    Write-Host ""
    Read-Host "Press Enter to close"
}
exit $exitCode
