param([string]$BackendUrl = 'http://127.0.0.1:14321')
$ErrorActionPreference = 'Stop'
$sceneRepo = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$sceneRoot = Join-Path $sceneRepo 'work/scene-native'
$sceneExe = Join-Path $sceneRepo 'desktop_pet_next/src-tauri/target/release/akane_desktop_pet_next.exe'
if (-not (Test-Path -LiteralPath $sceneExe -PathType Leaf)) { throw '请先在 desktop_pet_next 执行 npm run tauri -- build --no-bundle。' }
$sceneHealth = Invoke-RestMethod -Uri ($BackendUrl.TrimEnd('/') + '/health') -TimeoutSec 10
if ($sceneHealth.instance_id -ne 'local-default' -or $sceneHealth.root_binding -ne 'valid') {
    throw '此入口仅用于独立的 local-default 验收宿主，请先按运行说明启动 tests.scene_live_host。'
}
$sceneStateDir = Join-Path $sceneRoot 'state'
New-Item -ItemType Directory -Force -Path $sceneStateDir | Out-Null
$sceneStatePath = Join-Path $sceneStateDir 'pet_state.json'
if (-not (Test-Path -LiteralPath $sceneStatePath)) {
    $sceneState = @{
        instanceId = 'local-default'; hostId = 'local-default'; boundBotId = 'local-default'
        backendUrl = $BackendUrl; profileUserId = 'master'; characterPackId = 'akane_v1'
        sessionId = 'scene-native-qa'; outfit = 'default'; voiceEnabled = $false; proactiveWakeEnabled = $false
    } | ConvertTo-Json
    [IO.File]::WriteAllText($sceneStatePath, $sceneState, [Text.UTF8Encoding]::new($false))
}
$scenePreviousEnv = @{}
$sceneEnv = @{
    AKANE_DATA_ROOT = $sceneRoot; AKANE_BACKEND_URL = $BackendUrl
    AKANE_INSTANCE_ID = 'local-default'; AKANE_BOUND_BOT_ID = 'local-default'
}
try {
    foreach ($sceneKey in $sceneEnv.Keys) {
        $scenePreviousEnv[$sceneKey] = [Environment]::GetEnvironmentVariable($sceneKey, 'Process')
        [Environment]::SetEnvironmentVariable($sceneKey, $sceneEnv[$sceneKey], 'Process')
    }
    $sceneProcess = Start-Process -FilePath $sceneExe -WindowStyle Normal -PassThru
    Write-Host "独立验收桌宠已启动，PID $($sceneProcess.Id)。右键桌宠，点击“小屋”进入。"
    Write-Host "本次状态保存在 $sceneRoot，不复用现有桌宠状态。"
} finally {
    foreach ($sceneKey in $sceneEnv.Keys) {
        [Environment]::SetEnvironmentVariable($sceneKey, $scenePreviousEnv[$sceneKey], 'Process')
    }
}
