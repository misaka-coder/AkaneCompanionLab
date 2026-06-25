[CmdletBinding()]
param(
    [string]$OutputPath = "",
    [switch]$InitializeGit
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

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

function Test-PathWithin {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root
    )

    if ($Path.Equals($Root, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }
    $prefix = $Root + [System.IO.Path]::DirectorySeparatorChar
    return $Path.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
}

function Test-PublicFileExcluded {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    $path = $RelativePath.Replace("\", "/")
    $lower = $path.ToLowerInvariant()

    $exactExclusions = @(
        ".env",
        "claude.md",
        "agents.md",
        "docs/mechanical_section_view_guide.md"
    )
    if ($exactExclusions -contains $lower) {
        return $true
    }

    # The akane_v1 demo character pack ships with real, ASSETS_LICENSE-documented
    # portraits. Allow its files (including .png) through the binary-exclusion gate,
    # but never its per-character private _local/ memory data.
    $allowedPrefixes = @(
        "desktop_pet_creator_kit/characters/akane_v1/",
        "web/assets/stickers/akane_v1/"
    )
    foreach ($allowed in $allowedPrefixes) {
        if ($lower.StartsWith($allowed.ToLowerInvariant()) -and $lower -notmatch "/_local/") {
            return $false
        }
    }

    # UI assets that ship as real images (not placeholders) in the public release.
    $allowedBinaryAssets = @(
        "desktop_pet_next/src/assets/control-center-lab/heroes/akane-sakura-wide.png",
        "desktop_pet_next/src/assets/control-center-lab/heroes/akane-sky-wide.png"
    )
    if ($allowedBinaryAssets -contains $lower) {
        return $false
    }

    $prefixExclusions = @(
        ".git/",
        ".venv/",
        ".claude/",
        ".codex/",
        ".pytest_cache/",
        "__pycache__/",
        "local_research/",
        "runtime_logs/",
        "users_data/",
        "node_modules/",
        "desktop_pet/node_modules/",
        "desktop_pet_next/node_modules/",
        "desktop_pet_next/dist/",
        "desktop_pet_next/src-tauri/target/",
        "desktop_pet_creator_kit/dist/",
        "desktop_pet_creator_kit/characters/reimu/",
        "web/assets/bgm/",
        "web/assets/characters/",
        "web/assets/live2d/",
        "web/assets/scenes/",
        "web/assets/备份/",
        "web/vendor/live2d/",
        "desktop_pet_next/src/assets/",
        "docs/magnetic_chaos_pendulum_video/",
        "docs/productization_work_session_",
        "docs/video_01_assets/",
        "docs/research_learning_agent_prompts/",
        "documents/projects/retrieval_eval_"
    )
    foreach ($prefix in $prefixExclusions) {
        if ($lower.StartsWith($prefix.ToLowerInvariant())) {
            return $true
        }
    }

    # Internal AI-process / handoff docs (milestone tickets, handoff prompts,
    # backlogs, agent prompts, personal learning notes, multi-agent collaboration
    # scaffolding, IP-source research) carry no open-source value and read as
    # machine-generated project process noise. Keep the design/architecture docs;
    # drop the process exhaust.
    $internalDocPatterns = @(
        "_ticket\.md$",
        "handoff",
        "_backlog\.md$",
        "recon_brief",
        "agent_prompt",
        "_notes\.md$",
        "collaboration_protocol",
        "multi_agent",
        "reimu_source_research",
        "claude_code_tool_system_research"
    )
    if ($lower.StartsWith("docs/")) {
        foreach ($docPattern in $internalDocPatterns) {
            if ($lower -match $docPattern) {
                return $true
            }
        }
    }

    $extension = [System.IO.Path]::GetExtension($lower)
    $binaryExclusions = @(
        ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".moc3",
        ".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus",
        ".webm", ".mp4", ".mov", ".mkv",
        ".pptx", ".pdf", ".docx", ".xlsx",
        ".zip", ".7z", ".rar", ".exe", ".dll", ".pdb",
        ".db", ".sqlite", ".sqlite3"
    )
    return $binaryExclusions -contains $extension
}

function Copy-PublicSourceFiles {
    param(
        [Parameter(Mandatory = $true)][string]$SourceRoot,
        [Parameter(Mandatory = $true)][string]$DestinationRoot
    )

    $relativePaths = @(
        git -c core.quotepath=false -C $SourceRoot ls-files --cached --others --exclude-standard
    )
    if ($LASTEXITCODE -ne 0) {
        throw "git_file_listing_failed"
    }

    $copied = 0
    $excluded = 0
    foreach ($relativePathValue in $relativePaths) {
        $relativePath = ([string]$relativePathValue).Trim()
        if (-not $relativePath) {
            continue
        }
        if (Test-PublicFileExcluded -RelativePath $relativePath) {
            $excluded += 1
            continue
        }

        $sourcePath = Join-Path $SourceRoot $relativePath
        if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
            continue
        }

        $destinationPath = Join-Path $DestinationRoot $relativePath
        $destinationDirectory = Split-Path -Parent $destinationPath
        if (-not (Test-Path -LiteralPath $destinationDirectory)) {
            $null = New-Item -ItemType Directory -Path $destinationDirectory
        }
        [System.IO.File]::Copy($sourcePath, $destinationPath, $false)
        $copied += 1
    }

    return [pscustomobject]@{
        Copied   = $copied
        Excluded = $excluded
    }
}

function New-PublicPlaceholderPng {
    param([Parameter(Mandatory = $true)][string]$Path)

    Add-Type -AssemblyName System.Drawing

    $directory = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $directory)) {
        $null = New-Item -ItemType Directory -Path $directory
    }

    $bitmap = [System.Drawing.Bitmap]::new(720, 720)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $backgroundBrush = [System.Drawing.Drawing2D.LinearGradientBrush]::new(
        [System.Drawing.Rectangle]::new(0, 0, 720, 720),
        ([System.Drawing.Color]::FromArgb(255, 238, 233, 250)),
        ([System.Drawing.Color]::FromArgb(255, 196, 210, 238)),
        45.0
    )
    $accentBrush = [System.Drawing.SolidBrush]::new(
        [System.Drawing.Color]::FromArgb(145, 114, 92, 168)
    )
    $textBrush = [System.Drawing.SolidBrush]::new(
        [System.Drawing.Color]::FromArgb(255, 52, 48, 70)
    )
    $font = [System.Drawing.Font]::new(
        "Segoe UI",
        30,
        [System.Drawing.FontStyle]::Bold,
        [System.Drawing.GraphicsUnit]::Pixel
    )
    $smallFont = [System.Drawing.Font]::new(
        "Segoe UI",
        18,
        [System.Drawing.FontStyle]::Regular,
        [System.Drawing.GraphicsUnit]::Pixel
    )
    $format = [System.Drawing.StringFormat]::new()
    $format.Alignment = [System.Drawing.StringAlignment]::Center
    $format.LineAlignment = [System.Drawing.StringAlignment]::Center

    try {
        $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
        $graphics.FillRectangle($backgroundBrush, 0, 0, 720, 720)
        $graphics.FillEllipse($accentBrush, 150, 115, 420, 420)
        $graphics.DrawString(
            "PUBLIC ALPHA",
            $font,
            $textBrush,
            ([System.Drawing.RectangleF]::new(80, 270, 560, 80)),
            $format
        )
        $graphics.DrawString(
            "replace with your own licensed asset",
            $smallFont,
            $textBrush,
            ([System.Drawing.RectangleF]::new(80, 345, 560, 60)),
            $format
        )
        $bitmap.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
    } finally {
        $format.Dispose()
        $smallFont.Dispose()
        $font.Dispose()
        $textBrush.Dispose()
        $accentBrush.Dispose()
        $backgroundBrush.Dispose()
        $graphics.Dispose()
        $bitmap.Dispose()
    }
}

function Add-PublicPlaceholderAssets {
    param([Parameter(Mandatory = $true)][string]$DestinationRoot)

    $temporaryPlaceholder = Join-Path $DestinationRoot ".public-alpha-placeholder.png"
    New-PublicPlaceholderPng -Path $temporaryPlaceholder

    $emotionNames = @(
        "侧耳听", "偷吃被抓", "卖萌", "听歌中", "困困", "困惑", "开心",
        "得意", "思考中", "打哈欠", "无语", "正常", "气鼓鼓", "求摸摸",
        "病娇", "脸红", "被摸头"
    )
    $characterRoots = @(
        "web/assets/characters/猫娘",
        "desktop_pet_next/src/assets/characters/猫娘"
    )

    $destinations = [System.Collections.Generic.List[string]]::new()
    foreach ($root in $characterRoots) {
        foreach ($emotionName in $emotionNames) {
            $destinations.Add("$root/$emotionName.png")
        }
    }

    foreach ($backgroundName in @("morning", "afternoon", "evening", "night")) {
        $destinations.Add("web/assets/backgrounds/default/$backgroundName.png")
    }

    foreach ($assetPath in @(
        "desktop_pet_next/src/assets/control-center-lab/backgrounds/sky-city-balcony.png",
        "desktop_pet_next/src/assets/control-center-lab/covers/akane-night-window.png",
        "desktop_pet_next/src/assets/control-center-lab/covers/akane-sakura-close.png",
        "desktop_pet_next/src/assets/control-center-lab/covers/akane-sky-paper-plane.png",
        "desktop_pet_next/src/assets/control-center-lab/covers/cloud-letter.png",
        "desktop_pet_next/src/assets/control-center-lab/covers/moon-balcony.png",
        "desktop_pet_next/src/assets/control-center-lab/covers/starry-cloud-cat.png"
    )) {
        $destinations.Add($assetPath)
    }

    foreach ($relativePath in $destinations) {
        $destinationPath = Join-Path $DestinationRoot $relativePath
        $destinationDirectory = Split-Path -Parent $destinationPath
        if (-not (Test-Path -LiteralPath $destinationDirectory)) {
            $null = New-Item -ItemType Directory -Path $destinationDirectory
        }
        [System.IO.File]::Copy($temporaryPlaceholder, $destinationPath, $false)
    }

    $iconPath = Join-Path $DestinationRoot "desktop_pet_next/src-tauri/icons/icon.ico"
    $iconDirectory = Split-Path -Parent $iconPath
    if (-not (Test-Path -LiteralPath $iconDirectory)) {
        $null = New-Item -ItemType Directory -Path $iconDirectory
    }
    $sourceBitmap = [System.Drawing.Bitmap]::FromFile($temporaryPlaceholder)
    $iconBitmap = [System.Drawing.Bitmap]::new(256, 256)
    $iconGraphics = [System.Drawing.Graphics]::FromImage($iconBitmap)
    $pngStream = [System.IO.MemoryStream]::new()
    try {
        $iconGraphics.DrawImage($sourceBitmap, 0, 0, 256, 256)
        $iconBitmap.Save($pngStream, [System.Drawing.Imaging.ImageFormat]::Png)
        $pngBytes = $pngStream.ToArray()

        $iconStream = [System.IO.File]::Create($iconPath)
        $writer = [System.IO.BinaryWriter]::new($iconStream)
        try {
            $writer.Write([uint16]0)
            $writer.Write([uint16]1)
            $writer.Write([uint16]1)
            $writer.Write([byte]0)
            $writer.Write([byte]0)
            $writer.Write([byte]0)
            $writer.Write([byte]0)
            $writer.Write([uint16]1)
            $writer.Write([uint16]32)
            $writer.Write([uint32]$pngBytes.Length)
            $writer.Write([uint32]22)
            $writer.Write($pngBytes)
        } finally {
            $writer.Dispose()
        }
    } finally {
        $pngStream.Dispose()
        $iconGraphics.Dispose()
        $iconBitmap.Dispose()
        $sourceBitmap.Dispose()
    }

    Remove-Item -LiteralPath $temporaryPlaceholder
    return $destinations.Count + 1
}

function Normalize-PublicTextFiles {
    param([Parameter(Mandatory = $true)][string]$DestinationRoot)

    $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
    $markdownFiles = Get-ChildItem -LiteralPath $DestinationRoot -File -Recurse -Filter "*.md"
    foreach ($file in $markdownFiles) {
        $content = [System.IO.File]::ReadAllText($file.FullName)
        $privateMarkdownPrefix = "](/f:/" + "Akane/AkaneCompanionLab/"
        $content = $content.Replace(
            $privateMarkdownPrefix,
            "](/"
        )
        [System.IO.File]::WriteAllText($file.FullName, $content, $utf8WithoutBom)
    }

    $htmlFiles = Get-ChildItem -LiteralPath $DestinationRoot -File -Recurse -Filter "*.html"
    foreach ($file in $htmlFiles) {
        $lines = [System.IO.File]::ReadAllLines($file.FullName)
        $filtered = @(
            $lines | Where-Object {
                $_ -notmatch "/vendor/live2d/" -and
                $_ -notmatch "/assets/live2d/"
            }
        )
        [System.IO.File]::WriteAllLines($file.FullName, $filtered, $utf8WithoutBom)
    }
}

$sourceRoot = ConvertTo-FullPath (Join-Path $PSScriptRoot "..")
if (-not $OutputPath) {
    $parent = Split-Path -Parent $sourceRoot
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $OutputPath = Join-Path $parent "AkaneCompanionLab-public-alpha-$timestamp"
}
$outputRoot = ConvertTo-FullPath $OutputPath
$sourceParent = ConvertTo-FullPath (Split-Path -Parent $sourceRoot)
$driveRoot = ConvertTo-FullPath ([System.IO.Path]::GetPathRoot($outputRoot))
$userHome = ConvertTo-FullPath ([Environment]::GetFolderPath("UserProfile"))

if (
    (Test-PathWithin -Path $outputRoot -Root $sourceRoot) -or
    (Test-PathWithin -Path $sourceRoot -Root $outputRoot) -or
    $outputRoot.Equals($sourceParent, [System.StringComparison]::OrdinalIgnoreCase) -or
    $outputRoot.Equals($driveRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
    $outputRoot.Equals($userHome, [System.StringComparison]::OrdinalIgnoreCase)
) {
    throw "unsafe_output_path"
}
if (Test-Path -LiteralPath $outputRoot) {
    throw "output_path_already_exists"
}

$null = New-Item -ItemType Directory -Path $outputRoot
$copyResult = Copy-PublicSourceFiles -SourceRoot $sourceRoot -DestinationRoot $outputRoot
$placeholderCount = Add-PublicPlaceholderAssets -DestinationRoot $outputRoot
Normalize-PublicTextFiles -DestinationRoot $outputRoot

$auditScript = Join-Path $outputRoot "scripts/audit_public_release.ps1"
if (-not (Test-Path -LiteralPath $auditScript -PathType Leaf)) {
    throw "public_audit_script_missing"
}
& $auditScript -ReleaseRoot $outputRoot

if ($InitializeGit) {
    git -C $outputRoot init --initial-branch main
    if ($LASTEXITCODE -ne 0) {
        throw "git_init_failed"
    }
}

[pscustomobject]@{
    Status            = "ok"
    OutputPath        = $outputRoot
    CopiedFiles       = $copyResult.Copied
    ExcludedFiles     = $copyResult.Excluded
    PlaceholderImages = $placeholderCount
    GitInitialized    = [bool]$InitializeGit
    GitHistoryCopied  = $false
} | Format-List
