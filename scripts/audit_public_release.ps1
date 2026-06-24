[CmdletBinding()]
param(
    [string]$ReleaseRoot = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $ReleaseRoot) {
    $ReleaseRoot = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) ".."
}
$root = [System.IO.Path]::GetFullPath($ReleaseRoot)
if (-not (Test-Path -LiteralPath $root -PathType Container)) {
    throw "release_root_missing"
}

$errors = [System.Collections.Generic.List[string]]::new()
$requiredFiles = @(
    "LICENSE",
    "NOTICE",
    "README.md",
    "README_EN.md",
    "ASSETS_LICENSE.md",
    "THIRD_PARTY_NOTICES.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CHANGELOG.md",
    "docs/productization_release_gate_v1.md",
    ".env.example",
    "requirements.txt",
    "start_akane.bat",
    "启动_Akane.bat",
    "scripts/bootstrap_akane_windows.ps1"
)
foreach ($relativePath in $requiredFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $root $relativePath) -PathType Leaf)) {
        $errors.Add("missing_required_file:$relativePath")
    }
}

function Read-ReleaseText {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    $path = Join-Path $root $RelativePath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        return ""
    }
    return [System.IO.File]::ReadAllText($path)
}

$legacyTauriSettingsFiles = @(
    "desktop_pet_next/src/settings.js",
    "desktop_pet_next/src/settings.css"
)
foreach ($relativePath in $legacyTauriSettingsFiles) {
    if (Test-Path -LiteralPath (Join-Path $root $relativePath) -PathType Leaf) {
        $errors.Add("legacy_tauri_settings_file:$relativePath")
    }
}

$internalSessionDocs = @(
    Get-ChildItem -LiteralPath (Join-Path $root "docs") -File -Filter "productization_work_session_*.md" -ErrorAction SilentlyContinue
)
foreach ($file in $internalSessionDocs) {
    $relative = $file.FullName.Substring($root.Length).TrimStart("\", "/").Replace("\", "/")
    $errors.Add("internal_productization_session_doc:$relative")
}

$settingsCompat = Read-ReleaseText -RelativePath "desktop_pet_next/settings.html"
if (-not $settingsCompat) {
    $errors.Add("missing_settings_compat_redirect:desktop_pet_next/settings.html")
} else {
    if ($settingsCompat -notmatch "control-center-lab\.html") {
        $errors.Add("settings_compat_does_not_redirect_to_control_center")
    }
    if ($settingsCompat -match "src/settings\.js|settings\.css") {
        $errors.Add("settings_compat_loads_legacy_settings_bundle")
    }
}

$tauriMain = Read-ReleaseText -RelativePath "desktop_pet_next/src-tauri/src/main.rs"
if ($tauriMain -match "AKANE_LEGACY_SETTINGS|LegacySettings") {
    $errors.Add("legacy_settings_runtime_gate:desktop_pet_next/src-tauri/src/main.rs")
}

$rootLauncher = Read-ReleaseText -RelativePath "start_akane_next.ps1"
$directLauncher = Read-ReleaseText -RelativePath "desktop_pet_next/scripts/start-next.ps1"
foreach ($entry in @(
    [pscustomobject]@{ Name = "start_akane_next.ps1"; Content = $rootLauncher },
    [pscustomobject]@{ Name = "desktop_pet_next/scripts/start-next.ps1"; Content = $directLauncher }
)) {
    if (-not $entry.Content) {
        $errors.Add("missing_launcher:$($entry.Name)")
        continue
    }
    if ($entry.Content -match "AKANE_LEGACY_SETTINGS|LegacySettings") {
        $errors.Add("launcher_uses_legacy_settings:$($entry.Name)")
    }
    if (
        $entry.Content -notmatch "Get-NewestInputWriteTime" -or
        $entry.Content -notmatch "releaseIsStale" -or
        $entry.Content -notmatch "Release exe is older than source files"
    ) {
        $errors.Add("launcher_missing_stale_release_guard:$($entry.Name)")
    }
}
if ($rootLauncher -and ($rootLauncher -notmatch "AKANE_DATA_ROOT" -or $rootLauncher -notmatch "akane_data_root\.ps1")) {
    $errors.Add("root_launcher_missing_user_data_root:start_akane_next.ps1")
}
if ($directLauncher -and ($directLauncher -notmatch '\[switch\]\$NoBuild' -or $directLauncher -notmatch '\[switch\]\$Rebuild')) {
    $errors.Add("direct_launcher_missing_build_control_flags:desktop_pet_next/scripts/start-next.ps1")
}

$assetLicense = Read-ReleaseText -RelativePath "ASSETS_LICENSE.md"
if ($assetLicense -and $assetLicense -notmatch "does \*\*not\*\*\s+automatically grant rights") {
    $errors.Add("asset_license_missing_code_asset_boundary")
}

$forbiddenDirectories = @(
    ".claude",
    ".codex",
    "local_research",
    "runtime_logs",
    "users_data",
    "reimu",
    "live2d"
)
$ignoredRuntimeDirectoryNames = @(
    ".git",
    ".venv",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "target",
    "dist"
)

function Test-IsIgnoredRuntimePath {
    param([Parameter(Mandatory = $true)][string]$FullName)

    $relative = $FullName.Substring($root.Length).TrimStart("\", "/")
    $segments = @($relative -split "[\\/]")
    foreach ($segment in $segments) {
        if ($ignoredRuntimeDirectoryNames -contains $segment.ToLowerInvariant()) {
            return $true
        }
    }
    return $false
}

foreach ($directory in Get-ChildItem -LiteralPath $root -Directory -Recurse -Force) {
    if (Test-IsIgnoredRuntimePath -FullName $directory.FullName) {
        continue
    }
    if ($forbiddenDirectories -contains $directory.Name.ToLowerInvariant()) {
        $relative = $directory.FullName.Substring($root.Length).TrimStart("\", "/")
        $errors.Add("forbidden_directory:$relative")
    }
}

$forbiddenExtensions = @(
    ".jpg", ".jpeg", ".webp", ".gif", ".moc3",
    ".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus",
    ".webm", ".mp4", ".mov", ".mkv",
    ".pptx", ".pdf", ".docx", ".xlsx",
    ".zip", ".7z", ".rar", ".exe", ".dll", ".pdb",
    ".db", ".sqlite", ".sqlite3"
)
$allowedPngPrefixes = @(
    "web/assets/characters/",
    "web/assets/backgrounds/",
    "web/assets/stickers/",
    "desktop_pet_next/src/assets/characters/",
    "desktop_pet_next/src/assets/control-center-lab/",
    "desktop_pet_creator_kit/characters/akane_v1/assets/"
)

# The akane_v1 demo character pack ships real, ASSETS_LICENSE-documented portraits.
# Every other png in the release must still be the single neutral placeholder.
$realArtPngPrefix = "desktop_pet_creator_kit/characters/akane_v1/"

$files = @(
    Get-ChildItem -LiteralPath $root -File -Recurse -Force |
        Where-Object { -not (Test-IsIgnoredRuntimePath -FullName $_.FullName) }
)
foreach ($file in $files) {
    $relative = $file.FullName.Substring($root.Length).TrimStart("\", "/").Replace("\", "/")
    $extension = $file.Extension.ToLowerInvariant()

    if ($file.Name -eq ".env") {
        $errors.Add("forbidden_env_file:$relative")
    }
    if ($forbiddenExtensions -contains $extension) {
        $errors.Add("forbidden_binary:$relative")
    }
    if ($extension -eq ".png") {
        $allowed = $false
        foreach ($prefix in $allowedPngPrefixes) {
            if ($relative.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
                $allowed = $true
                break
            }
        }
        if (-not $allowed) {
            $errors.Add("unexpected_png:$relative")
        }
    }
    if (
        $extension -eq ".ico" -and
        -not $relative.Equals(
            "desktop_pet_next/src-tauri/icons/icon.ico",
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        $errors.Add("unexpected_icon:$relative")
    }
    if ($file.Length -gt 5MB) {
        $errors.Add("oversized_file:$relative")
    }
}

$pngFiles = @($files | Where-Object { $_.Extension.ToLowerInvariant() -eq ".png" })
$placeholderPngFiles = @(
    $pngFiles | Where-Object {
        $rel = $_.FullName.Substring($root.Length).TrimStart("\", "/").Replace("\", "/")
        -not $rel.StartsWith($realArtPngPrefix, [System.StringComparison]::OrdinalIgnoreCase)
    }
)
$pngHashes = @(
    $placeholderPngFiles |
        ForEach-Object { (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash } |
        Sort-Object -Unique
)
if ($placeholderPngFiles.Count -eq 0) {
    $errors.Add("placeholder_pngs_missing")
} elseif ($pngHashes.Count -ne 1) {
    $errors.Add("non_placeholder_png_detected")
}

$textExtensions = @(
    ".py", ".js", ".mjs", ".ts", ".rs", ".toml", ".json", ".jsonl",
    ".md", ".txt", ".html", ".css", ".yml", ".yaml", ".ps1", ".bat",
    ".cmd", ".ini", ".example"
)
$forbiddenTextPatterns = [ordered]@{
    "private_username" = ("L" + "enovo")
    "private_repo_path_windows" = ("F:\\" + "Akane")
    "private_repo_path_markdown" = ("/f:/" + "Akane")
    "private_key" = "BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY"
    "openai_style_key" = "sk-[A-Za-z0-9_-]{20,}"
    "github_token" = "gh[pousr]_[A-Za-z0-9]{20,}"
    "aws_access_key" = "AKIA[0-9A-Z]{16}"
    "google_api_key" = "AIza[0-9A-Za-z_-]{30,}"
    "configured_master_qq" = "(?m)^MASTER_QQ=[0-9]+"
    "configured_bot_qq" = "(?m)^QQ_BOT_QQ=[0-9]+"
}

foreach ($file in $files) {
    $extension = $file.Extension.ToLowerInvariant()
    if (
        -not ($textExtensions -contains $extension) -and
        $file.Name -notin @("LICENSE", "NOTICE", ".gitignore", ".gitattributes")
    ) {
        continue
    }

    $content = [System.IO.File]::ReadAllText($file.FullName)
    $relative = $file.FullName.Substring($root.Length).TrimStart("\", "/").Replace("\", "/")
    foreach ($entry in $forbiddenTextPatterns.GetEnumerator()) {
        if ($content -match $entry.Value) {
            $errors.Add("$($entry.Key):$relative")
        }
    }
}

$gitDirectory = Join-Path $root ".git"
if (Test-Path -LiteralPath $gitDirectory -PathType Container) {
    $commitCountText = git -C $root rev-list --all --count
    if ($LASTEXITCODE -ne 0) {
        $errors.Add("git_history_check_failed")
    } elseif ([int]($commitCountText | Select-Object -First 1) -gt 0) {
        $errors.Add("git_history_is_not_empty")
    }
}

if ($errors.Count -gt 0) {
    $details = ($errors | Sort-Object -Unique) -join [Environment]::NewLine
    throw "public_release_audit_failed:$([Environment]::NewLine)$details"
}

$totalBytes = ($files | Measure-Object -Property Length -Sum).Sum
[pscustomobject]@{
    Status    = "ok"
    Root      = $root
    Files     = $files.Count
    SizeMB    = [math]::Round(($totalBytes / 1MB), 2)
    GitCommits = 0
} | Format-List
