param(
    [int]$BackendPort = 14321,
    [int]$FrontendPort = 1420
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$BackendHealthUrl = "http://127.0.0.1:$BackendPort/health"
$FrontendUrl = "http://localhost:$FrontendPort/scene.html?backend=http://127.0.0.1:$BackendPort&profile=scene-qa&session=scene-ui-live&character=akane_v1&bot=local-default"

Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "  正在启动 Akane 片刻小屋 (Scene UI)..." -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan

# 1. 检查后端服务
$backendReady = $false
try {
    $resp = Invoke-RestMethod -Uri $BackendHealthUrl -TimeoutSec 2 -ErrorAction SilentlyContinue
    if ($resp.status -eq "ok") { $backendReady = $true }
} catch {}

if ($backendReady) {
    Write-Host "[OK] 后端服务已在端口 $BackendPort 运行。" -ForegroundColor Green
} else {
    Write-Host "[INFO] 正在启动片刻小屋后端服务 (tests.scene_live_host，端口 $BackendPort)..." -ForegroundColor Yellow
    $pyCmd = "Set-Location -LiteralPath '$ProjectDir'; python -m tests.scene_live_host"
    Start-Process -FilePath "powershell.exe" -WorkingDirectory $ProjectDir -WindowStyle Minimized -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $pyCmd
    ) | Out-Null

    # 等待后端启动
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Seconds 1
        try {
            $resp = Invoke-RestMethod -Uri $BackendHealthUrl -TimeoutSec 2 -ErrorAction SilentlyContinue
            if ($resp.status -eq "ok") {
                $backendReady = $true
                Write-Host "[OK] 后端服务启动成功！" -ForegroundColor Green
                break
            }
        } catch {}
    }
    if (-not $backendReady) {
        Write-Host "[WARN] 后端启动可能需要更多时间，请稍候..." -ForegroundColor Yellow
    }
}

# 2. 检查前端 Vite 服务
$frontendReady = $false
try {
    $testConn = Get-NetTCPConnection -LocalPort $FrontendPort -State Listen -ErrorAction SilentlyContinue
    if ($testConn) { $frontendReady = $true }
} catch {}

if ($frontendReady) {
    Write-Host "[OK] 前端 Vite 服务已在端口 $FrontendPort 运行。" -ForegroundColor Green
} else {
    Write-Host "[INFO] 正在启动前端 Vite 服务 (端口 $FrontendPort)..." -ForegroundColor Yellow
    $viteCmd = "Set-Location -LiteralPath '$ProjectDir'; npm --prefix desktop_pet_next run dev"
    Start-Process -FilePath "powershell.exe" -WorkingDirectory $ProjectDir -WindowStyle Minimized -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $viteCmd
    ) | Out-Null

    for ($i = 0; $i -lt 10; $i++) {
        Start-Sleep -Seconds 1
        $testConn = Get-NetTCPConnection -LocalPort $FrontendPort -State Listen -ErrorAction SilentlyContinue
        if ($testConn) {
            $frontendReady = $true
            Write-Host "[OK] 前端服务就绪！" -ForegroundColor Green
            break
        }
    }
}

# 3. 打开浏览器
Write-Host "[INFO] 正在打开浏览器进入片刻小屋..." -ForegroundColor Cyan
Write-Host "地址: $FrontendUrl" -ForegroundColor Gray
Start-Process $FrontendUrl | Out-Null

Write-Host "`n启动完成！欢迎回到片刻小屋。" -ForegroundColor Green
