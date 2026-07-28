[CmdletBinding()]
param(
    [ValidateSet("Auto", "Desktop", "Web")]
    [string]$Mode = "Auto",
    [string]$InstanceId = "",
    [string]$DataRoot = "",
    [int]$BackendPort = 9999,
    [string]$EnvFile = "",
    [switch]$CloudSatellite,
    [string]$BackendUrl = "",
    [switch]$PrepareOnly,
    [switch]$CheckOnly,
    [switch]$ForcePythonInstall,
    [string]$PackageWheelhouse = "",
    [string]$PackageIndexUrl = "",
    [switch]$KeepWindowOpen
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "akane_data_root.ps1")
. (Join-Path $PSScriptRoot "akane_instance_launcher.ps1")

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

    $oldErrorActionPreference = $ErrorActionPreference
    $oldNativeCommandPreference = $null
    $hasNativeCommandPreference = Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue
    try {
        $ErrorActionPreference = "Continue"
        if ($null -ne $hasNativeCommandPreference) {
            $oldNativeCommandPreference = $PSNativeCommandUseErrorActionPreference
            $PSNativeCommandUseErrorActionPreference = $false
        }
        & $PythonPath @PrefixArgs -c "import capcore, capcore_adapter_mcp, capcore_adapter_python, capcore_adapter_speech, capcore_adapter_comfyui, capcore_provider_native_tools, capcore_provider_openai, capcore_provider_anthropic, charpack_core, promptpack_core, memcore, voicecore, fastapi, uvicorn, chromadb, openai, requests, pydantic_settings, edge_tts" 2>$null
        $coreImportsReady = $LASTEXITCODE -eq 0
        & $PythonPath @PrefixArgs -c "import channelcore_onebot" 2>$null
        return $coreImportsReady -and $LASTEXITCODE -eq 0
    } finally {
        if ($null -ne $hasNativeCommandPreference) {
            $PSNativeCommandUseErrorActionPreference = $oldNativeCommandPreference
        }
        $ErrorActionPreference = $oldErrorActionPreference
    }
}

function Test-PackagedCoreDependencies {
    param(
        [string]$PythonPath,
        [string]$Root,
        [string[]]$PrefixArgs = @()
    )

    $checker = Join-Path $Root "scripts\check_packaged_dependencies.py"
    $oldErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & $PythonPath @PrefixArgs $checker 2>$null | Out-Null
        return $LASTEXITCODE -eq 0
    } finally {
        $ErrorActionPreference = $oldErrorActionPreference
    }
}

function Assert-PackageWheelhouse {
    param(
        [string]$Root,
        [string]$Wheelhouse
    )

    $manifestPath = Join-Path $Wheelhouse "akane-package-wheelhouse.json"
    try {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        throw "akane_package_wheelhouse_invalid: manifest_json_invalid"
    }
    if ($manifest.schema -ne "akane.package-wheelhouse.v1" -or $manifest.version -ne "0.1.0") {
        throw "akane_package_wheelhouse_invalid: manifest_contract_mismatch"
    }
    if ($manifest.runtime_dependencies_downloaded -ne $true) {
        throw "akane_package_wheelhouse_invalid: runtime_dependency_closure_missing"
    }

    $entries = @($manifest.internal_packages)
    $requirementsPath = Join-Path $Root "requirements-packages.txt"
    foreach ($rawLine in [System.IO.File]::ReadAllLines($requirementsPath)) {
        $line = $rawLine.Split("#", 2)[0].Trim()
        if (-not $line) {
            continue
        }
        if ($line -notmatch '^([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+-]+)$') {
            throw "akane_package_wheelhouse_invalid: internal_requirement_not_exact"
        }
        $packageName = $Matches[1].ToLowerInvariant()
        $packageVersion = $Matches[2]
        $entry = @($entries | Where-Object { ([string]$_.name).ToLowerInvariant() -eq $packageName })
        if ($entry.Count -ne 1 -or [string]$entry[0].version -ne $packageVersion) {
            throw "akane_package_wheelhouse_invalid: internal_package_manifest_mismatch"
        }
        $artifactName = [string]$entry[0].file
        if (-not $artifactName -or [System.IO.Path]::GetFileName($artifactName) -ne $artifactName) {
            throw "akane_package_wheelhouse_invalid: unsafe_internal_artifact_name"
        }
        $artifactPath = Join-Path $Wheelhouse $artifactName
        if (-not (Test-Path -LiteralPath $artifactPath -PathType Leaf)) {
            throw "akane_package_wheelhouse_invalid: internal_artifact_missing"
        }
        $expectedHash = ([string]$entry[0].sha256).ToLowerInvariant()
        $actualHash = (Get-FileSha256 -Path $artifactPath).ToLowerInvariant()
        if ($expectedHash -notmatch '^[0-9a-f]{64}$' -or $actualHash -ne $expectedHash) {
            throw "akane_package_wheelhouse_invalid: internal_artifact_hash_mismatch"
        }
    }
}

function Resolve-PackageInstallSource {
    param([string]$Root)

    $configuredWheelhouse = ([string]$PackageWheelhouse).Trim()
    if (-not $configuredWheelhouse) {
        $configuredWheelhouse = ([string]$env:AKANE_PACKAGE_WHEELHOUSE).Trim()
    }
    if (-not $configuredWheelhouse) {
        $configuredWheelhouse = Join-Path $Root "package_wheels"
    }
    if (Test-Path -LiteralPath (Join-Path $configuredWheelhouse "akane-package-wheelhouse.json") -PathType Leaf) {
        Assert-PackageWheelhouse -Root $Root -Wheelhouse $configuredWheelhouse
        return [pscustomobject]@{ Kind = "wheelhouse"; Value = $configuredWheelhouse }
    }

    $configuredIndex = ([string]$PackageIndexUrl).Trim()
    if (-not $configuredIndex) {
        $configuredIndex = ([string]$env:AKANE_PACKAGE_INDEX_URL).Trim()
    }
    if ($configuredIndex) {
        return [pscustomobject]@{ Kind = "index"; Value = $configuredIndex }
    }
    return $null
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

function Ensure-PythonEnvironment {
    param(
        [string]$Root,
        [switch]$ReadOnly
    )

    $venvPython = Join-Path $Root ".venv\Scripts\python.exe"
    $requirementsPath = Join-Path $Root "requirements.txt"
    $packageRequirementsPath = Join-Path $Root "requirements-packages.txt"
    $runtimeRequirementsPath = Join-Path $Root "requirements-runtime.txt"
    $stampPath = Join-Path $Root ".venv\.akane-requirements.sha256"
    $requirementsHash = @(
        Get-FileSha256 -Path $requirementsPath
        Get-FileSha256 -Path $packageRequirementsPath
        Get-FileSha256 -Path $runtimeRequirementsPath
    ) -join ":"

    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        $systemPython = Get-SystemPython
        if ($null -eq $systemPython) {
            throw "Python 3.11 or newer was not found. Install Python 3.11 from python.org, enable 'Add Python to PATH', then run this launcher again."
        }
        if ($ReadOnly) {
            if (
                -not (Test-PythonImports -PythonPath $systemPython.Command -PrefixArgs $systemPython.Args) -or
                -not (Test-PackagedCoreDependencies -PythonPath $systemPython.Command -Root $Root -PrefixArgs $systemPython.Args)
            ) {
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
    $importsReady = (
        (Test-PythonImports -PythonPath $venvPython) -and
        (Test-PackagedCoreDependencies -PythonPath $venvPython -Root $Root)
    )
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
        $packageSource = Resolve-PackageInstallSource -Root $Root
        if ($null -eq $packageSource) {
            throw "akane_package_artifacts_unavailable: provide a complete package_wheels bundle or set AKANE_PACKAGE_INDEX_URL"
        }
        if ($packageSource.Kind -eq "wheelhouse") {
            & $venvPython -m pip install --disable-pip-version-check --force-reinstall --no-deps --no-index --find-links $packageSource.Value -r $packageRequirementsPath
            if ($LASTEXITCODE -ne 0) {
                throw "packaged_core_install_failed"
            }
            & $venvPython -m pip install --disable-pip-version-check --no-index --find-links $packageSource.Value -r $requirementsPath
        } else {
            & $venvPython -m pip install --disable-pip-version-check --force-reinstall --no-deps --index-url $packageSource.Value -r $packageRequirementsPath
            if ($LASTEXITCODE -ne 0) {
                throw "packaged_core_install_failed"
            }
            & $venvPython -m pip install --disable-pip-version-check --index-url $packageSource.Value -r $requirementsPath
        }
        if ($LASTEXITCODE -ne 0) {
            throw "python_dependency_install_failed"
        }
        if (-not (Test-PackagedCoreDependencies -PythonPath $venvPython -Root $Root)) {
            throw "packaged_dependency_validation_failed"
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
        [string]$EnvironmentPath = "",
        [switch]$AllowCreate,
        [switch]$ReadOnly
    )

    $envPath = if ($EnvironmentPath.Trim()) {
        [System.IO.Path]::GetFullPath($EnvironmentPath.Trim())
    } else {
        Join-Path $Root ".env"
    }
    $examplePath = Join-Path $Root ".env.example"
    $created = $false
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
        if ($ReadOnly -or -not $AllowCreate) {
            Write-AkaneStep "WARN" "No instance environment file is available; using process environment and saved model settings."
            return [pscustomobject]@{ Path = $envPath; Created = $false; LlmConfigured = $false }
        }
        Copy-Item -LiteralPath $examplePath -Destination $envPath
        $created = $true
        Write-AkaneStep "OK" "Created local .env configuration."
    }

    $textKey = Get-EnvValue -Path $envPath -Name "TEXT_API_KEY"
    if (-not $textKey) { $textKey = [string]$env:TEXT_API_KEY }
    $chatKey = Get-EnvValue -Path $envPath -Name "CHAT_API_KEY"
    if (-not $chatKey) { $chatKey = [string]$env:CHAT_API_KEY }
    $textProtocol = Get-EnvValue -Path $envPath -Name "TEXT_API_PROTOCOL"
    if (-not $textProtocol) { $textProtocol = [string]$env:TEXT_API_PROTOCOL }
    $textProtocol = ([string]$textProtocol).ToLowerInvariant()
    $chatProtocol = Get-EnvValue -Path $envPath -Name "CHAT_API_PROTOCOL"
    if (-not $chatProtocol) { $chatProtocol = [string]$env:CHAT_API_PROTOCOL }
    $chatProtocol = ([string]$chatProtocol).ToLowerInvariant()
    $textBaseUrl = Get-EnvValue -Path $envPath -Name "TEXT_BASE_URL"
    if (-not $textBaseUrl) { $textBaseUrl = [string]$env:TEXT_BASE_URL }
    $textBaseUrl = ([string]$textBaseUrl).ToLowerInvariant()
    $chatBaseUrl = Get-EnvValue -Path $envPath -Name "CHAT_BASE_URL"
    if (-not $chatBaseUrl) { $chatBaseUrl = [string]$env:CHAT_BASE_URL }
    $chatBaseUrl = ([string]$chatBaseUrl).ToLowerInvariant()
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
    Write-AkaneStep "WARN" ("Desktop build tools are incomplete ({0}); using the Web client for this launch." -f ($toolchain.Missing -join ", "))
    return "Web"
}

function Wait-BackendReady {
    param(
        [int]$Port,
        [string]$ExpectedInstanceId,
        [int]$TimeoutSeconds = 120
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $url = "http://127.0.0.1:{0}/health" -f $Port
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $health = Invoke-RestMethod -Uri $url -TimeoutSec 2
            if (Test-AkaneInstanceHealth -Health $health -ExpectedInstanceId $ExpectedInstanceId) {
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
        [string]$InstanceId,
        [string]$DataRoot,
        [string]$EnvFile,
        [switch]$OpenModelSettings
    )

    & (Join-Path $Root "start_akane_next.ps1") -InstanceId $InstanceId -DataRoot $DataRoot -BackendPort $Port -EnvFile $EnvFile -SkipDesktop
    if (-not (Wait-BackendReady -Port $Port -ExpectedInstanceId $InstanceId)) {
        $dataRoot = if ($env:AKANE_DATA_ROOT) { $env:AKANE_DATA_ROOT } else { Join-Path $env:LOCALAPPDATA "Akane" }
        $safeInstanceId = Get-AkaneSafeInstanceLogId -InstanceId $InstanceId
        $errLog = Join-Path $dataRoot "logs\akane_backend.$safeInstanceId.err.log"
        Write-Host ""
        Write-Host "[FAIL] Backend did not become healthy within 120 seconds." -ForegroundColor Red
        if (Test-Path -LiteralPath $errLog) {
            Write-Host "[INFO] Last lines of akane_backend.err.log:" -ForegroundColor Yellow
            Get-Content -LiteralPath $errLog -Tail 30 | ForEach-Object { Write-Host "       $_" }
        } else {
            Write-Host "[INFO] No backend error log found at: $errLog"
        }
        throw "Backend did not become healthy. See error output above or check: $errLog"
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
        [string]$InstanceId,
        [string]$DataRoot,
        [string]$EnvFile,
        [switch]$CloudSatellite,
        [string]$BackendUrl,
        [switch]$OpenModelSettings
    )

    $toolchain = Test-DesktopToolchain
    $releaseExe = Join-Path $Root "desktop_pet_next\src-tauri\target\release\akane_desktop_pet_next.exe"
    if (-not (Test-Path -LiteralPath $releaseExe -PathType Leaf) -and -not $toolchain.Ready) {
        throw ("Desktop mode needs a prebuilt release or Node.js + Rust. Missing: {0}" -f ($toolchain.Missing -join ", "))
    }
    $launchParameters = @{
        InstanceId = $InstanceId
        DataRoot = $DataRoot
        BackendPort = $Port
        EnvFile = $EnvFile
        OpenSettings = [bool]$OpenModelSettings
    }
    if ($CloudSatellite) {
        $launchParameters.CloudSatellite = $true
        $launchParameters.BackendUrl = $BackendUrl
    }
    & (Join-Path $Root "start_akane_next.ps1") @launchParameters
    Write-AkaneStep "OK" "Desktop pet launch requested."
}

$projectRoot = Get-ProjectRoot
$instanceIdWasBound = $PSBoundParameters.ContainsKey("InstanceId")
$dataRootWasBound = $PSBoundParameters.ContainsKey("DataRoot")
$backendPortWasBound = $PSBoundParameters.ContainsKey("BackendPort")
$envFileWasBound = $PSBoundParameters.ContainsKey("EnvFile")
$resolvedEnvFile = ""
if ($envFileWasBound -and -not [string]::IsNullOrWhiteSpace($EnvFile)) {
    $resolvedEnvFile = if ([System.IO.Path]::IsPathRooted($EnvFile)) {
        [System.IO.Path]::GetFullPath($EnvFile)
    } else {
        [System.IO.Path]::GetFullPath((Join-Path $projectRoot $EnvFile))
    }
    $null = Import-AkaneEnvFile -Path $resolvedEnvFile
    $env:AKANE_ENV_FILE = $resolvedEnvFile
}
$resolvedInstanceId = if ($instanceIdWasBound -and -not [string]::IsNullOrWhiteSpace($InstanceId)) {
    $InstanceId.Trim()
} elseif (-not [string]::IsNullOrWhiteSpace([string]$env:AKANE_INSTANCE_ID)) {
    ([string]$env:AKANE_INSTANCE_ID).Trim()
} else {
    "local-default"
}
if (-not (Test-AkaneSafeInstanceId -InstanceId $resolvedInstanceId)) {
    throw "invalid_instance_id"
}
if (-not $backendPortWasBound -and -not [string]::IsNullOrWhiteSpace([string]$env:COMPANION_PORT)) {
    $configuredPort = 0
    if (-not [int]::TryParse(([string]$env:COMPANION_PORT).Trim(), [ref]$configuredPort) -or $configuredPort -lt 1 -or $configuredPort -gt 65535) {
        throw "invalid_backend_port"
    }
    $BackendPort = $configuredPort
}
$resolvedDataRoot = if ($dataRootWasBound -and -not [string]::IsNullOrWhiteSpace($DataRoot)) {
    $DataRoot.Trim()
} else {
    ([string]$env:AKANE_DATA_ROOT).Trim()
}
if ($CloudSatellite) {
    if ($resolvedInstanceId -eq "local-default") { throw "cloud_satellite_requires_named_instance" }
    if (-not $PSBoundParameters.ContainsKey("BackendUrl") -or [string]::IsNullOrWhiteSpace($BackendUrl)) {
        throw "cloud_satellite_backend_url_required"
    }
    if ([string]::IsNullOrWhiteSpace([string]$env:AKANE_DESKTOP_SATELLITE_TOKEN)) {
        throw "cloud_satellite_token_required"
    }
    if ($Mode -eq "Web") { throw "cloud_satellite_requires_desktop_mode" }
}
Write-Host ""
Write-Host "AkaneCompanionLab Windows Bootstrap" -ForegroundColor Magenta
Write-Host ("Project: {0}" -f $projectRoot)
Write-Host ""

$exitCode = 0
try {
    $dataStatus = Initialize-AkaneDataRoot `
        -ProjectRoot $projectRoot `
        -InstanceId $resolvedInstanceId `
        -DataRoot $resolvedDataRoot `
        -SeedBundledCharacters:$CloudSatellite `
        -ReadOnly:$CheckOnly
    $env:AKANE_DATA_ROOT = $dataStatus.Root
    $env:AKANE_DATA_ROOT_READY = "1"
    $env:AKANE_INSTANCE_ID = $resolvedInstanceId
    $env:COMPANION_PORT = "$BackendPort"
    $env:AKANE_BACKEND_URL = if ($CloudSatellite) { $BackendUrl.Trim().TrimEnd('/') } else { "http://127.0.0.1:$BackendPort" }
    if (-not $CheckOnly) {
        if ($dataStatus.Failed -gt 0) {
            Write-AkaneStep "WARN" ("User data root is ready, but {0} legacy files could not be copied." -f $dataStatus.Failed)
        } elseif ($dataStatus.Copied -gt 0) {
            Write-AkaneStep "OK" ("User data root is ready; migrated {0} legacy files without overwriting existing data." -f $dataStatus.Copied)
        } else {
            Write-AkaneStep "OK" "User data root is ready."
        }
    }
    if ($CloudSatellite) {
        $envStatus = [pscustomobject]@{ Path = $resolvedEnvFile; Created = $false; LlmConfigured = $true }
        Write-AkaneStep "OK" "Cloud Satellite mode uses the verified remote backend; local Python setup is not required."
    } else {
        $null = Ensure-PythonEnvironment -Root $projectRoot -ReadOnly:$CheckOnly
        $envStatus = Ensure-EnvironmentFile `
            -Root $projectRoot `
            -DataRoot $dataStatus.Root `
            -EnvironmentPath $resolvedEnvFile `
            -AllowCreate:($resolvedInstanceId -eq "local-default" -and -not $resolvedEnvFile) `
            -ReadOnly:$CheckOnly
    }
    $launchMode = Resolve-LaunchMode -RequestedMode $Mode -Root $projectRoot
    if ($CloudSatellite -and $launchMode -ne "Desktop") {
        throw "cloud_satellite_requires_desktop_mode"
    }
    Write-AkaneStep "INFO" ("Selected client: {0}" -f $launchMode)

    if ($CheckOnly) {
        if ($launchMode -eq "Desktop") {
            $toolchain = Test-DesktopToolchain
            $releaseExe = Join-Path $projectRoot "desktop_pet_next\src-tauri\target\release\akane_desktop_pet_next.exe"
            if ($toolchain.Ready -or (Test-Path -LiteralPath $releaseExe -PathType Leaf)) {
                Write-AkaneStep "OK" "Desktop runtime is buildable or already built."
            } else {
                throw ("Desktop runtime is not ready. Missing: {0}" -f ($toolchain.Missing -join ", "))
            }
        }
        Write-AkaneStep "OK" "Bootstrap check completed without changing local files."
    } elseif ($PrepareOnly) {
        Write-AkaneStep "OK" "Preparation completed. Nothing was launched."
    } elseif ($launchMode -eq "Desktop") {
        try {
            Start-DesktopMode -Root $projectRoot -Port $BackendPort -InstanceId $resolvedInstanceId -DataRoot $dataStatus.Root -EnvFile $resolvedEnvFile -CloudSatellite:$CloudSatellite -BackendUrl $BackendUrl -OpenModelSettings:(-not $envStatus.LlmConfigured)
        } catch {
            if ($Mode -ne "Auto" -or $CloudSatellite) {
                throw
            }
            Write-AkaneStep "WARN" ("Desktop launch failed; falling back to Web. {0}" -f $_.Exception.Message)
            Start-WebMode -Root $projectRoot -Port $BackendPort -InstanceId $resolvedInstanceId -DataRoot $dataStatus.Root -EnvFile $resolvedEnvFile -OpenModelSettings:(-not $envStatus.LlmConfigured)
        }
    } else {
        Start-WebMode -Root $projectRoot -Port $BackendPort -InstanceId $resolvedInstanceId -DataRoot $dataStatus.Root -EnvFile $resolvedEnvFile -OpenModelSettings:(-not $envStatus.LlmConfigured)
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
