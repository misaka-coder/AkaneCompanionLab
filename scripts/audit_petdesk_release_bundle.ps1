[CmdletBinding()]
param(
    [string]$BundleRoot = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$errors = [System.Collections.Generic.List[string]]::new()

function Write-AkanePetdeskBundleAuditStep {
    param(
        [string]$Level,
        [string]$Message
    )

    Write-Host ("[{0}] {1}" -f $Level, $Message)
}

function ConvertTo-FullPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    return [System.IO.Path]::GetFullPath(
        [Environment]::ExpandEnvironmentVariables($Path)
    ).TrimEnd(
        [char[]]@(
            [System.IO.Path]::DirectorySeparatorChar,
            [System.IO.Path]::AltDirectorySeparatorChar
        )
    )
}

function Add-AuditError {
    param([Parameter(Mandatory = $true)][string]$Message)

    $script:errors.Add($Message)
}

function Test-IsRuntimeIgnoredPath {
    param(
        [Parameter(Mandatory = $true)][string]$RelativePath
    )

    $segments = @($RelativePath.Replace("\", "/").Split("/"))
    $ignoredNames = @(
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache"
    )
    foreach ($segment in $segments) {
        if ($ignoredNames -contains $segment.ToLowerInvariant()) {
            return $true
        }
    }
    return $false
}

function Read-BundleManifest {
    param([Parameter(Mandatory = $true)][string]$Path)

    try {
        return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    } catch {
        Add-AuditError ("manifest_invalid_json:{0}" -f $_.Exception.Message)
        return $null
    }
}

function Get-JsonProperty {
    param(
        [object]$Object,
        [string]$Name
    )

    if ($null -eq $Object) {
        return $null
    }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $null
    }
    return $property.Value
}

$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $scriptDir) {
    $scriptDir = (Get-Location).Path
}

if (-not $BundleRoot) {
    $BundleRoot = Join-Path $scriptDir ".."
}

$root = ConvertTo-FullPath $BundleRoot
Write-AkanePetdeskBundleAuditStep "INFO" ("Bundle root: {0}" -f $root)

if (-not (Test-Path -LiteralPath $root -PathType Container)) {
    Write-AkanePetdeskBundleAuditStep "FAIL" ("Bundle root missing: {0}" -f $root)
    Write-Host "AKANE_PETDESK_RELEASE_BUNDLE_AUDIT_FAILED"
    exit 1
}

$manifestPath = Join-Path $root "manifest.json"
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    Add-AuditError "missing_required_file:manifest.json"
}

$manifest = if (Test-Path -LiteralPath $manifestPath -PathType Leaf) {
    Read-BundleManifest -Path $manifestPath
} else {
    $null
}

if ($null -ne $manifest) {
    $schema = [string](Get-JsonProperty -Object $manifest -Name "schema")
    if ($schema -ne "akane.petdesk.releaseBundle.v1") {
        Add-AuditError ("manifest_schema_mismatch:{0}" -f $schema)
    }
    if ([string](Get-JsonProperty -Object $manifest -Name "runtimeExe") -ne "runtime/petdesk_runtime.exe") {
        Add-AuditError "manifest_runtime_exe_mismatch"
    }
    if ([string](Get-JsonProperty -Object $manifest -Name "startScript") -ne "scripts/start_petdesk_runtime_bundle.ps1") {
        Add-AuditError "manifest_start_script_mismatch"
    }
}

$requiredFiles = @(
    "README.md",
    "manifest.json",
    "runtime/petdesk_runtime.exe",
    "scripts/start_petdesk_runtime_bundle.ps1",
    "scripts/audit_petdesk_release_bundle.ps1",
    "docs/petdesk_operator_guide_m54.md",
    "docs/petdesk_release_doctor_m56.md",
    "docs/petdesk_release_acceptance_m57.md",
    "docs/petdesk_release_bundle_m58.md",
    "docs/petdesk_release_bundle_audit_m59.md"
)
foreach ($relativePath in $requiredFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $root $relativePath) -PathType Leaf)) {
        Add-AuditError ("missing_required_file:{0}" -f $relativePath)
    }
}

$runtimeExe = Join-Path $root "runtime/petdesk_runtime.exe"
if (Test-Path -LiteralPath $runtimeExe -PathType Leaf) {
    $runtimeExeItem = Get-Item -LiteralPath $runtimeExe
    if ($runtimeExeItem.Length -le 0) {
        Add-AuditError "runtime_exe_empty:runtime/petdesk_runtime.exe"
    }
}

$forbiddenTopLevelDirectories = @(
    ".git",
    ".venv",
    ".codex",
    ".claude",
    "companion_v01",
    "desktop_pet_next",
    "node_modules",
    "runtime_logs",
    "target",
    "users_data"
)
foreach ($name in $forbiddenTopLevelDirectories) {
    if (Test-Path -LiteralPath (Join-Path $root $name) -PathType Container) {
        Add-AuditError ("forbidden_top_level_directory:{0}" -f $name)
    }
}

$allowedExe = "runtime/petdesk_runtime.exe"
$forbiddenExtensions = @(
    ".db", ".sqlite", ".sqlite3", ".sqlite-journal", ".sqlite-shm", ".sqlite-wal",
    ".log", ".pdb", ".map", ".zip", ".7z", ".rar"
)
$actualFiles = @(
    Get-ChildItem -LiteralPath $root -File -Recurse -Force |
        ForEach-Object {
            $_.FullName.Substring($root.Length).TrimStart("\", "/").Replace("\", "/")
        } |
        Where-Object { -not (Test-IsRuntimeIgnoredPath -RelativePath $_) } |
        Sort-Object
)
foreach ($relativePath in $actualFiles) {
    $lower = $relativePath.ToLowerInvariant()
    $extension = [System.IO.Path]::GetExtension($lower)
    if ([System.IO.Path]::GetFileName($lower) -eq ".env") {
        Add-AuditError ("forbidden_env_file:{0}" -f $relativePath)
    }
    if ($forbiddenExtensions -contains $extension) {
        Add-AuditError ("forbidden_file_extension:{0}" -f $relativePath)
    }
    if ($extension -eq ".exe" -and -not $lower.Equals($allowedExe, [System.StringComparison]::OrdinalIgnoreCase)) {
        Add-AuditError ("unexpected_exe:{0}" -f $relativePath)
    }
}

if ($null -ne $manifest) {
    $manifestFiles = @(Get-JsonProperty -Object $manifest -Name "files")
    if ($manifestFiles.Count -eq 0) {
        Add-AuditError "manifest_files_empty"
    }

    $manifestPaths = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($entry in $manifestFiles) {
        $relativePath = [string](Get-JsonProperty -Object $entry -Name "path")
        if (-not $relativePath) {
            Add-AuditError "manifest_file_path_missing"
            continue
        }
        if ($relativePath.Contains("\") -or $relativePath.StartsWith("/") -or $relativePath -match "^[A-Za-z]:") {
            Add-AuditError ("manifest_file_path_unsafe:{0}" -f $relativePath)
            continue
        }
        $segments = @($relativePath.Split("/"))
        if ($segments | Where-Object { -not $_ -or $_ -eq "." -or $_ -eq ".." }) {
            Add-AuditError ("manifest_file_path_unsafe:{0}" -f $relativePath)
            continue
        }
        $null = $manifestPaths.Add($relativePath)
        $filePath = Join-Path $root $relativePath
        if (-not (Test-Path -LiteralPath $filePath -PathType Leaf)) {
            Add-AuditError ("manifest_file_missing:{0}" -f $relativePath)
            continue
        }

        $expectedBytes = [int64](Get-JsonProperty -Object $entry -Name "bytes")
        $expectedHash = ([string](Get-JsonProperty -Object $entry -Name "sha256")).ToUpperInvariant()
        $file = Get-Item -LiteralPath $filePath
        if ($file.Length -ne $expectedBytes) {
            Add-AuditError ("manifest_size_mismatch:{0}" -f $relativePath)
        }
        $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $filePath).Hash.ToUpperInvariant()
        if ($actualHash -ne $expectedHash) {
            Add-AuditError ("manifest_hash_mismatch:{0}" -f $relativePath)
        }
    }

    foreach ($relativePath in $actualFiles) {
        if ($relativePath -eq "manifest.json") {
            continue
        }
        if (-not $manifestPaths.Contains($relativePath)) {
            Add-AuditError ("file_not_in_manifest:{0}" -f $relativePath)
        }
    }
}

if ($errors.Count -gt 0) {
    foreach ($errorItem in ($errors | Sort-Object -Unique)) {
        Write-AkanePetdeskBundleAuditStep "FAIL" $errorItem
    }
    Write-Host "AKANE_PETDESK_RELEASE_BUNDLE_AUDIT_FAILED"
    exit 1
}

Write-AkanePetdeskBundleAuditStep "OK" ("Bundle files verified: {0}" -f $actualFiles.Count)
Write-Host "AKANE_PETDESK_RELEASE_BUNDLE_AUDIT_OK"
exit 0
