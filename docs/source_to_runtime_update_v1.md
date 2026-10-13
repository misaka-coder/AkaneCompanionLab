# 从源码更新到运行实例指南 V1

改完代码后，先确定修改对象，再更新对应产物或进程。本文按 `d6e79d29` 的启动器、构建配置和安装实现核对；配置保存与生效另见 [配置指南](configuration_effect_v1.md)。

## 1. 按修改对象选择操作

| 修改对象 | 常用更新路径 | 产物或加载位置 | 核验重点 |
| --- | --- | --- | --- |
| Python 后端：`companion_v01/` 等 | 重启目标后端；不需要构建桌宠 | 目标实例的 Python 进程 | 新 PID、正确实例的健康响应，再检查改动对应行为 |
| 桌宠页面、控制器、样式或 Rust：`desktop_pet_next/` | 根启动器 `-Rebuild`：构建、实例拷贝、更新桌宠进程 | 仓库 release EXE → 实例 `bin/` | 本次构建成功、EXE 哈希一致、进程已更新及原生交互 |
| 插件：`plugins/<name>/` | 源码 Stage → 检查权限 → Install；需要使用时启用 | `<DataRoot>/extensions/plugins/`，选择保存在 runtime 的 `config_dir/plugin-selections.json` | 同一次 Stage/Install 的 digest、激活结果、贡献项与实际调用 |
| 独立包：`../memcore`、`../capcore` 等 | 构建并安装真实 wheel，再重启使用它的后端 | 宿主 `.venv/Lib/site-packages/` | 版本、导入来源、所需接口与安装内容 |

只改源码、只编译或只看到进程存活，都不能独立证明功能已更新。以下命令在**仓库根目录**执行；更新操作用于后续部署，本指南的离线检查不执行生产重启或安装。

## 2. 先确认同一个实例与环境

### 公共入口读取保存的 profile

只读查看当前选择和进程：

```powershell
pwsh -NoLogo -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap_akane_windows.ps1 -Action Status
```

结果中的 `instance_id`、`data_root` 和各组件 `process_id` 用于定位目标；后端状态为 `healthy`，桌宠为 `ready`，设备执行器为 `online` 时分别表示对应检查通过。

Status 结果**没有后端端口字段**。端口从选中的 profile 的 `backend_port` 或明确的启动参数取得；默认 `9999` 只是回退值。本机 `local-qq-test / 12001` 是实例样例，不是所有环境的固定地址。

默认选择文件为 `%LOCALAPPDATA%\Akane\config\active-profile.json`；如设置了 `AKANE_ACTIVE_PROFILE_FILE`，沿 bootstrap 的选择逻辑定位。无需把完整 profile、环境文件或进程命令行贴入记录。

### 直接调用根启动器要携带目标和环境

`start_akane_next.ps1` 不读取公共入口的 active profile。直接调用时，要传同一个 InstanceId、DataRoot、BackendPort，并加载对应环境，避免只对齐端口却丢失模型或设备配置。

单个环境文件的模板如下。替换为已核实的目标值，端口例值也需按实际调整；后文复用同一 PowerShell 会话中的 `$updateTarget`：

```powershell
$updateTarget = @{
    InstanceId = "<实例ID>"
    DataRoot = "<数据根目录绝对路径>"
    BackendPort = 12001
    EnvFile = "<目标环境文件绝对路径>"
}
```

profile 有多个 `environment_files` 时，按其原顺序加载全部文件，而不是只把最后一份传给根启动器：

```powershell
. .\scripts\akane_instance_launcher.ps1
$updateEnvironmentFiles = @("<第一份环境文件绝对路径>", "<第二份环境文件绝对路径>")
foreach ($updateEnvPath in $updateEnvironmentFiles) {
    $null = Import-AkaneEnvFile -Path $updateEnvPath
}
$updateTarget.Remove("EnvFile")
```

此时后续启动从当前进程继承已加载的环境。公共 bootstrap 已处理保存的文件列表；优先使用它完成普通后端重启。不要输出加载后的环境变量值。

## 3. 四类更新的常用步骤

### Python 后端

使用保存的 active profile 重启后端：

```powershell
pwsh -NoLogo -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap_akane_windows.ps1 -Action Restart
```

此入口可复用或激活已匹配的桌宠，缺少桌宠时也可能启动它；它**不替换桌宠 EXE**。若明确只更新后端，在上节环境准备后使用：

```powershell
& .\start_akane_next.ps1 @updateTarget -Action Restart -SkipDesktop
```

按实际端口检查健康并对照新 PID：

```powershell
$updateHealth = Invoke-RestMethod -Uri "http://127.0.0.1:$($updateTarget.BackendPort)/health" -TimeoutSec 5
$updateHealth | Select-Object status, instance_id, root_binding
```

健康接口的预期字段为 `status="ok"`、目标 `instance_id`、`root_binding="valid"`，与 Launcher Status 的 `backend.status="healthy"` 不同。健康通过之后，再验证本次修改对应的路由或行为；不能仅凭 PID 宣称某个 commit 已加载。

### 桌宠页面与原生 Rust

后端已健康、目标和环境已按上节准备时，根启动器一并完成构建、实例拷贝与桌宠更新：

```powershell
& .\start_akane_next.ps1 @updateTarget -SkipBackend -Rebuild
```

`-Rebuild` 属于根启动器和 `desktop_pet_next/scripts/start-next.ps1`，**不属于公共 bootstrap**。根启动器会停止目标实例匹配的桌宠进程，构建后替换实例 EXE 并重新启动；启用独立设备执行器时也沿对应路径更新。

Tauri 配置 `desktop_pet_next/src-tauri/tauri.conf.json` 已声明 `beforeBuildCommand: "npm run build"`。上述构建会自动运行 Vite，不需要先手动 `npm run build` 再构建一遍。若只是离线构建核验，替代使用：

```powershell
Push-Location .\desktop_pet_next
try {
    npm run tauri -- build --no-bundle
} finally {
    Pop-Location
}
```

这条离线命令自动构建前端，但**只生成仓库产物，不部署实例、不更新旧进程**。`--no-bundle` 不会跳过前端钩子；单独 `cargo build` 则不会执行该钩子。仅检查页面时，可独立运行 `npm run build`。

实际 EXE 文件名均使用下划线：

- 构建：`desktop_pet_next/src-tauri/target/release/akane_desktop_pet_next.exe`。
- 实例 UI：`<DataRoot>/bin/akane_desktop_pet_next.exe`。
- 实例设备执行器：`<DataRoot>/bin/akane_desktop_pet_next_device.exe`。

更新后比较实例 UI 与本次构建产物：

```powershell
$updateBuildHash = (Get-FileHash -LiteralPath .\desktop_pet_next\src-tauri\target\release\akane_desktop_pet_next.exe).Hash
$updateInstanceExe = Join-Path $updateTarget.DataRoot "bin\akane_desktop_pet_next.exe"
$updateInstanceHash = (Get-FileHash -LiteralPath $updateInstanceExe).Hash
$updateBuildHash -eq $updateInstanceHash
```

随后确认新进程及 `ready`，再操作本次修改的原生窗口。缺少 Rust/Node 工具或构建失败时保留真实错误，不把旧 EXE 启动成功当成更新成功。

普通根启动器可复用已有桌宠或产物，源码变新时提示显式 `-Rebuild`；`desktop_pet_next/` 的 `npm run start:release` 会检查过期输入并决定重建，两者行为不同。

### 已安装插件源码

宿主加载的是已安装产物，修改源码目录或只重启宿主不会重装插件。常用入口是控制中心插件管理中的本地源码安装，检查返回权限后安装该候选。

代码和管理 API 的对应关系：

| 步骤 | 已有服务方法 / 路由 | 参数与关键返回值 |
| --- | --- | --- |
| 暂存源码 | `await management.stage_source(source_path=...)`；`POST /admin/plugins/stages/source` | 正文 `{"source_path":"<绝对路径>"}`；成功返回 `stage_id`、`digest`、`permissions` |
| 安装候选 | `await management.install_stage(stage_id=..., approved_permissions=...)`；`POST /admin/plugins/stages/{stage_id}/install` | 正文 `{"approved_permissions":[...]}`，内容来自已检查的 Stage 权限；成功响应包含 `digest`、`status`、`runtime` |

管理 API 使用已有管理认证；服务方法属于运行中的扩展管理服务，不能当成独立脚本直接调用。实现见 `companion_v01/routes/plugins.py`、`extension_management_runtime/operations.py` 与 `installation.py`。

核验同一次 Stage 与 Install 响应的 `digest` 一致。安装保留已有启停选择，所以 `ok=true` 并不总表示可调用：`disabled` 需要按使用意图启用，`waiting_dependency` 需要排查依赖；预期运行时应确认 `active`。

公共插件清单用 `runtime_status` 显示运行状态和贡献声明，不含产物 digest。不要在公共清单里查不存在的字段；安装回执提供产物身份，激活状态与实际调用提供运行证据。核对工具/service 贡献项及输入输出，版本号相同不能证明源码相同。

### 独立 Python 依赖包

宿主使用虚拟环境中已安装的包。独立仓库修改后应构建并安装真实 wheel，不通过临时 `PYTHONPATH` 冒充安装。

本机具备 `requirements-packages.txt` 所需的全部平级源码仓库时，使用现有同步函数：

```powershell
. .\scripts\sync_akane_local_packages.ps1
Sync-AkaneLocalPackages -ProjectRoot (Get-Location).Path
```

该文件是**函数库**，单独 `pwsh -File ... -ProjectRoot .` 不会调用同步。函数从项目父目录定位规范源码仓库（还包括构建器所需的 `capcore-host-utils`），计算指纹，在变化或所需接口检查失败时构建 wheelhouse 并重装，再写完成戳记。

只有单个包发布、未具备完整邻库时，按该包的构建说明生成匹配 wheel，再正常安装：

```powershell
.\.venv\Scripts\python.exe -m pip install --force-reinstall --no-deps "<实际构建的wheel绝对路径>"
```

同步版本声明、宿主依赖 pin 和对应验证；`--no-deps` 不会补齐缺失依赖。核验真实导入及所需接口：

```powershell
.\.venv\Scripts\python.exe -c "import memcore; from importlib.metadata import version; print(version('memcore-kernel')); print(memcore.__file__)"
. .\scripts\sync_akane_local_packages.ps1
Test-AkaneLocalPackageContracts -PythonPath (Join-Path (Get-Location).Path ".venv\Scripts\python.exe")
```

导入应来自该宿主 `site-packages`。上述函数检查既有包接口和 MemCore 运行契约；`tests.test_windows_bootstrap` 检查启动器，不能替代已安装包验证。更新包之后还需重启使用它的进程。

`.venv/.akane-local-packages.sha256` 是完整同步的完成记录，不是逐文件安装内容证明。单包手动安装不会更新它；若要核对规范同步状态，具备全部邻库时调用 `Test-AkaneLocalPackagesCurrent`。需要证明源码与安装内容一致时，另对照本次 wheel 的实际安装文件。

## 4. 两个现有例子

### 五子棋

从 [完整参考](../examples/desktop_features/gomoku/README.md) 定位前端、Rust 和插件：

- 改窗口/控制器：运行 `npm run test:gomoku`，再按桌宠路径构建并更新实例。
- 改 `plugins/akane_gomoku/`：运行 `tests.test_plugin_gomoku`，再 Stage/Install；窗口代码没有变化时不必重建桌宠。
- 改双方：分别完成两条更新链，再检查真实落子、窗口拖动/关闭、续局、慢响应重复操作和切换对象。

只读诊断在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -B scripts/check_desktop_feature.py gomoku
# 核对目标运行状态时，填实际地址
.\.venv\Scripts\python.exe -B scripts/check_desktop_feature.py gomoku --backend-url http://127.0.0.1:12001 --running
```

默认未查运行插件和进程的项显示 SKIP。`--backend-url` 检查插件激活及贡献声明，不比对源码内容；`--running` 比较运行 EXE 和构建 EXE。静态 OK 或浏览器布局预览不能代替原生交互、模型参与和插件部署验收。

### 最小 SDK 函数插件

[akane_sdk_math](../examples/plugins/akane_sdk_math/README.md) 声明 `example.math.add`，权限是 `capability.prompt.invoke`。先按其 README 在干净虚拟环境安装匹配 SDK/CapCore wheel 和插件项目，再运行插件测试；仅进入源码目录直接使用未安装项目的 Python，不足以运行 src 布局测试。

已有隔离用例可检查宿主安装生命周期：

```powershell
.\.venv\Scripts\python.exe -B -m unittest tests.test_plugin_installation_lifecycle tests.test_plugin_installation
```

这些用例不安装到当前生产实例。真实加载该例子时仍需 Stage/Install，比较同一候选的 digest、确认 active 和贡献项，再实际调用。

## 5. 汇报证据的边界

| 证据 | 可确认 | 仍需验证 |
| --- | --- | --- |
| 健康接口 `ok`、实例 ID、root binding | 服务可访问且属于目标实例 | 本次源码改动的具体行为 |
| Launcher PID / ready / online | 对应进程及组件状态 | 客户端、网络、物理设备和每个功能 |
| 标准 Tauri 构建成功 | 前端钩子与编译流程成功、仓库产物生成 | 实例拷贝和旧进程更新 |
| 两份 EXE 哈希一致 | 实例文件与所比对的构建文件相同 | 该构建是否对应本次源码，以及当前进程是否已更新 |
| Stage/Install digest 相同、插件 active | 安装了同一候选并激活 | 候选是否含本次修改、真实工具/service 行为 |
| 包版本、site-packages 导入、接口检查 | 当前解释器所用发行包及所需接口 | 源码、wheel 与安装文件内容一致性 |

遇到源码已改但运行没变化，先按第 1 节选对象：核对目标实例、实际加载位置和相应更新回执。无需新增版本注册表或常驻扫描来替代这些已有证据。
