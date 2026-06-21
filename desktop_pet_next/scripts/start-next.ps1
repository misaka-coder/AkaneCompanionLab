param(
  [switch]$BuildIfMissing,
  [switch]$NoBuild,
  [switch]$Rebuild,
  [switch]$Doctor
)

$ErrorActionPreference = "Stop"

function Write-AkaneFirstBuildHints {
  param([string]$Reason)

  Write-Host ""
  Write-Host "[首次构建提示] 即将从源码构建桌宠 (Tauri release)。原因：$Reason"
  Write-Host "             首次启动或源码更新后通常需要几分钟，请耐心等待，不要关闭窗口。"
  Write-Host "             过程中看到 ``Compiling ...`` 与 Rust crate 名字是 cargo 在编译，属正常现象，不是错误。"
  Write-Host "             构建完成后桌宠会自动打开。"
  Write-Host ""
}

function Ensure-NpmInstall {
  param([string]$DesktopDir)

  $nodeModules = Join-Path $DesktopDir "node_modules"
  if (Test-Path -LiteralPath $nodeModules) {
    return
  }

  Write-Host "[INFO] node_modules not found. Running npm install..."
  npm install
  if ($LASTEXITCODE -ne 0) {
    throw "npm install failed with exit code $LASTEXITCODE"
  }
}

function Get-NewestInputWriteTime {
  param([string]$DesktopDir)

  $paths = @(
    (Join-Path $DesktopDir "src"),
    (Join-Path $DesktopDir "src-tauri\src"),
    (Join-Path $DesktopDir "src-tauri\capabilities"),
    (Join-Path $DesktopDir "src-tauri\icons"),
    (Join-Path $DesktopDir "src-tauri\build.rs"),
    (Join-Path $DesktopDir "src-tauri\Cargo.toml"),
    (Join-Path $DesktopDir "src-tauri\Cargo.lock"),
    (Join-Path $DesktopDir "src-tauri\tauri.conf.json"),
    (Join-Path $DesktopDir "package.json"),
    (Join-Path $DesktopDir "package-lock.json"),
    (Join-Path $DesktopDir "vite.config.js"),
    (Join-Path $DesktopDir "index.html"),
    (Join-Path $DesktopDir "panel.html"),
    (Join-Path $DesktopDir "settings.html"),
    (Join-Path $DesktopDir "shop.html"),
    (Join-Path $DesktopDir "workspace.html"),
    (Join-Path $DesktopDir "workshop.html"),
    (Join-Path $DesktopDir "control-center-lab.html")
  )

  $newest = $null
  foreach ($path in $paths) {
    if (-not (Test-Path -LiteralPath $path)) {
      continue
    }

    $item = Get-Item -LiteralPath $path
    $files = if ($item.PSIsContainer) {
      Get-ChildItem -LiteralPath $path -Recurse -File
    } else {
      @($item)
    }

    foreach ($file in $files) {
      if ($null -eq $newest -or $file.LastWriteTime -gt $newest) {
        $newest = $file.LastWriteTime
      }
    }
  }

  return $newest
}

function Stop-AkaneDesktopProcesses {
  param([string]$ExePath)

  $targetPath = [System.IO.Path]::GetFullPath($ExePath)
  $processes = @(Get-Process -Name "akane_desktop_pet_next" -ErrorAction SilentlyContinue)
  foreach ($process in $processes) {
    $processPath = ""
    try {
      $processPath = [System.IO.Path]::GetFullPath([string]$process.Path)
    } catch {
      continue
    }

    if ($processPath -ine $targetPath) {
      continue
    }

    Write-Host "[INFO] Stopping existing Akane Next desktop PID: $($process.Id)"
    try {
      Stop-Process -Id $process.Id -Force -ErrorAction Stop
      Wait-Process -Id $process.Id -Timeout 5 -ErrorAction SilentlyContinue
    } catch {
      Write-Host "[WARN] Failed to stop existing Akane Next desktop PID $($process.Id): $($_.Exception.Message)"
    }
  }
}

$Root = Split-Path -Parent $PSScriptRoot
$ExePath = Join-Path $Root "src-tauri\target\release\akane_desktop_pet_next.exe"

Set-Location $Root

if ($Doctor) {
  npm run doctor
  if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
  }
}

$releaseExists = Test-Path -LiteralPath $ExePath
$inputWriteTime = Get-NewestInputWriteTime -DesktopDir $Root
$releaseWriteTime = if ($releaseExists) { (Get-Item -LiteralPath $ExePath).LastWriteTime } else { $null }
$releaseIsStale = $false
if ($releaseExists -and $null -ne $inputWriteTime -and $releaseWriteTime -lt $inputWriteTime) {
  $releaseIsStale = $true
}
$shouldBuild = (-not $releaseExists) -or $releaseIsStale -or $Rebuild

if ($shouldBuild -and $NoBuild) {
  if (-not $releaseExists) {
    throw "Release exe not found: $ExePath. Run without -NoBuild to build it automatically."
  }
  if ($releaseIsStale) {
    Write-Host "[WARN] Release exe is older than source files, but -NoBuild was set. Launching existing exe."
  } elseif ($Rebuild) {
    Write-Host "[WARN] -Rebuild was requested, but -NoBuild was set. Launching existing exe."
  }
  $shouldBuild = $false
}

if ($shouldBuild) {
  $buildReason = if (-not $releaseExists) {
    "找不到现成的桌宠 exe (首次启动)"
  } elseif ($Rebuild) {
    "调用方指定了 -Rebuild"
  } else {
    "源码比已有 exe 新，需要重建"
  }
  Write-AkaneFirstBuildHints -Reason $buildReason

  Ensure-NpmInstall -DesktopDir $Root
  if (-not $releaseExists) {
    Write-Host "[INFO] Release exe not found. Building Akane Next..."
  } elseif ($Rebuild) {
    Write-Host "[INFO] Rebuilding Akane Next release exe..."
  } else {
    Write-Host "[INFO] Release exe is older than source files. Rebuilding Akane Next..."
  }
  npm run tauri -- build
  if ($LASTEXITCODE -ne 0) {
    throw "Tauri build failed with exit code $LASTEXITCODE"
  }
  Write-Host "[INFO] Tauri build finished. Launching the desktop pet next..."
} elseif ($BuildIfMissing) {
  Write-Host "[INFO] Release exe already exists and is up to date. -BuildIfMissing is no longer required."
}

Stop-AkaneDesktopProcesses -ExePath $ExePath
$process = Start-Process -FilePath $ExePath -WorkingDirectory $Root -PassThru
Write-Host "Akane Next 已启动。PID: $($process.Id)"
Write-Host $ExePath
