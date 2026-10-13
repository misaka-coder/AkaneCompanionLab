# 从源码更新到运行实例：降低理解门槛执行单 V1

日期：2026-10-12。核对基线：`d6e79d29`。状态：**已完成，主审补正与离线复核通过**。

本单与 [配置归属与生效执行单](configuration_effect_development_clarity_execution_plan_v1.md) 可并行。目标是让开发者知道一次源码修改应更新哪个对象，并能核实当前运行对象已经加载它。沿现有启动器、诊断和插件安装链写清操作，不新造发布系统。

## 1. 交付结果

1. 新增领域指南 `docs/source_to_runtime_update_v1.md`，包含按修改对象选择更新步骤的表。
2. 核对四个对象：Python 后端、Tauri 桌宠、已安装插件、独立 Python 依赖包。
3. 用现有五子棋/函数插件和隔离用例演示“源码 → 产物/安装 → 运行核验”，明确每种证据能证明什么。
4. 修正五子棋示例中确有误导的运行说明；总导航的新增链接建议交主审整合。

指南应让开发者在改完代码后回答：“现在跑的是哪个实例和进程？更新的是哪份产物？下一步用什么证明它生效？”默认端口、文件存在、插件 active、构建成功都不能单独回答全部问题。

## 2. 开工和并行边界

先执行 `git status --short`，记录 HEAD 和已有改动；按当前源码核对实际参数、位置与调用顺序。

| 本单可修改 | 范围 |
| --- | --- |
| `docs/source_to_runtime_update_v1.md` | 新建领域指南 |
| `examples/desktop_features/gomoku/README.md` | 运行、更新和核验相关说明；复用指南，避免再复制完整发布教程 |
| `examples/plugins/akane_sdk_math/README.md` | 仅在实际核对发现安装/更新说明不准确时局部修正 |
| 本执行单 | 填写核验、执行和未实测记录 |

生产启动器、诊断实现、前后端业务和 SDK 默认只读。可修正文档中的错误命令；真实代码问题以触发和证据交主审，不为本说明任务重写启动器或合并其他工作区改动。

另一单拥有设置目录、目录测试、配置领域指南以及 `DEVELOPMENT.md` 的配置部分。本单不修改这些文件，也不修改 `PROJECT_MAP.md`；交付时给出应加入总导航的准确链接，由主审统一接入。

## 3. 已核对的实际链路

| 对象 | 实际入口/实现 | 必须核对的区别 |
| --- | --- | --- |
| 公共启动入口 | `scripts/bootstrap_akane_windows.ps1` → 根目录 `start_akane_next.ps1` | 公共入口读取保存的 active profile，参数是否显式传入会影响选择；默认 9999 不是每个实例的运行地址 |
| 进程与实例 | `scripts/akane_data_root.ps1`、`scripts/akane_instance_launcher.ps1`、根启动器 | 后端健康与实例身份、桌宠 UI ready、设备执行器 online 各有证据 |
| 根目录桌宠启动器 | `start_akane_next.ps1` | 通常复用已有 EXE/桌宠；源码较新时提示显式 `-Rebuild`，不自动等同发布更新 |
| 前端 release 入口 | `desktop_pet_next/package.json` 的 `start:release` → `desktop_pet_next/scripts/start-next.ps1` | 该入口会检查过期输入并决定构建，行为不同于根目录普通启动入口 |
| 桌宠构建及实例产物 | 根启动器的 `Copy-AkaneInstanceExecutable`；`desktop_pet_next/src-tauri/target/release/` | 仓库构建 EXE 与实例目录运行 EXE 分开；实例拷贝通过临时文件后替换 |
| 插件更新 | `companion_v01/extension_management_runtime/operations.py` 的 `stage_source`、同目录 `installation.py` 的 `install_stage`、已有插件目录/状态 | 当前源码目录不等于已经安装的代码；stage、review、install 和激活结果应关联同一产物 |
| 桌宠功能诊断 | `scripts/check_desktop_feature.py` | 源码接线、插件声明、构建 EXE、运行 EXE 分别检查；SKIP 不等于通过 |
| 独立依赖包 | `requirements-packages.txt`、`scripts/sync_akane_local_packages.ps1`、当前 Python 环境 | 修改其他源码仓库不等于已安装包更新；版本、导入路径与真实 wheel 内容各有用途 |

已确认：公共 bootstrap 没有 `-Rebuild` 参数，根目录 `start_akane_next.ps1` 和 `desktop_pet_next/scripts/start-next.ps1` 才有。指南不能把下层参数直接拼到不接受它的公共入口。

公共 `Restart` 会重启后端，已匹配的桌宠可被复用/激活；它不保证替换桌宠 EXE。运行配置如何解析由另一单负责，这里只写如何选择同一个实例和更新相应对象。

## 4. 实施步骤

### A. 先选对象，再列最小操作

指南开头用表说明：

- Python 后端业务变更：更新对应后端进程，核实实例与健康；不要求构建桌宠。
- 前端页面/控制器/样式或原生 Rust/权限变更：分清 Vite build、Tauri release、实例部署和桌宠进程更新；单独 Cargo 编译不保证嵌入最新页面。
- 插件源码变更：沿已有 stage/review/install/激活链；只重启宿主不代表源码已经重新安装。
- MemCore、CapCore 或其他独立包变更：真实构建并安装匹配 wheel，核实宿主 Python 的导入来源，再更新使用该包的进程；不通过临时 `PYTHONPATH` 冒充正常安装。

每类给一条常规路径及明确的结束证据。不要罗列所有历史启动器、云端发布流水线、迁移脚本和 TTS/ComfyUI 操作。

### B. 用当前实例展示选择与状态

可使用下面的只读入口观察保存的 profile 和状态：

```powershell
pwsh -NoLogo -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap_akane_windows.ps1 -Action Status
```

实例示例可写本单编写时使用的 `local-qq-test` / `12001`，标为本机样例；通用步骤从状态或调用者指定的实例参数取得地址。不要读取后直接粘贴整个 active profile、环境文件或带令牌的进程命令行。

涉及更新的命令模板要注明工作目录、参数归属和目标对象，先按脚本定义核对。特别说明：绕过公共 bootstrap 调用根启动器时，不能假定它会自动以同样方式选择保存的 active profile；应沿实际实现传递同一个 InstanceId、DataRoot、BackendPort 和必要 EnvFile。

命令模板用于后续获授权更新。本单不执行生产 Start、Stop、Restart、Rebuild、安装或配置迁移；不为了展示流程启动第二个占端口实例。

### C. 两个现有例子及证据边界

1. **五子棋窗口/控制器改动**：从示例 README 找到真实文件，核对前端构建入口、Rust 命令/权限、控制器组合；说明构建产物进入实例的路径及运行 EXE 核验。窗口拖动、关闭、模型参与落子属于实际原生验收，静态诊断和浏览器预览不能代替。
2. **最小函数插件源码改动**：沿 `example.math.add` 的真实 SDK 声明，核对 stage/install 的参数和返回值，说明如何关联 stage 与安装产物。复用已有隔离安装生命周期测试，必要时用临时实例/目录演示，不安装进生产库。

准确记录证据：

| 证据 | 能确认什么 |
| --- | --- |
| `/health` 的 status、instance_id | 服务可访问且属于目标实例；不独立证明某个源码提交已加载 |
| launcher Status 的 PID/ready/online | 对应进程和连接状态；不能据此声称所有客户端或物理设备验收通过 |
| Tauri build 成功 | 本次构建成功；不证明旧桌宠进程已经更新 |
| 运行 EXE 与构建 EXE 哈希一致 | 这两份二进制相同；没有新的构建证据时仍不能证明它们包含全部当前源码 |
| 插件 active、工具/service 贡献项存在 | 已激活对应声明；版本名相同不证明源码相同 |
| 同一次 stage/install 的 digest 与激活结果 | 已安装产物的身份及激活结果；不要把未经比较的当前源码目录当成同一产物 |
| Python 包版本、导入路径和 wheel 核验 | 当前解释器使用哪个安装包；依赖声明版本与源码目录各不能独立证明内容一致 |

不增加 Git hash 注入、全目录常驻扫描、二进制自动替换、重复版本注册表或新的后台监控。若现有证据只能证明部分，直接写清边界。

### D. 领域指南与示例分工

新指南保存公共更新路径；五子棋 README 保留自己的接线、功能测试和原生交互步骤，更新部分链接到指南。函数示例继续沿公开 SDK，不导入宿主私有模块让例子假通过。

不得新造“自动修复”“一键更新全部组件”命令。源码过期、插件未激活、运行 EXE 不同、缺少构建工具、实例不匹配等情形，要让开发者知道检查哪个现有对象，保留实际失败或 SKIP。

## 5. 验收与验证

优先使用已有隔离用例核对文档引用的行为：

```powershell
.\.venv\Scripts\python.exe -B -m unittest tests.test_windows_bootstrap tests.test_public_launcher_aliases tests.test_local_test_launcher tests.test_desktop_feature_diagnostics
git diff --check
```

本组包括临时目录中的 wrapper/PrepareOnly 等隔离检查，不会重启生产实例。插件生命周期说明需要核对时，查看并选跑相关已有用例：

```powershell
.\.venv\Scripts\python.exe -B -m unittest tests.test_plugin_installation_lifecycle tests.test_plugin_installation
```

如需检查五子棋接线，在仓库根目录使用现有只读诊断：

```powershell
.\.venv\Scripts\python.exe -B scripts/check_desktop_feature.py gomoku
```

仅当需要实际运行版本核对且能取得目标地址时，追加 `--backend-url <目标地址> --running`；记录权限或设备不可用的原因，不为消除 SKIP 自动安装插件。没有修改前端代码，不要求再 build；不跑全仓测试或下载模型。

逐项验收：所有引用文件和命令存在、参数属于对应脚本、工作目录明确、四类更新步骤能到达真实运行核验、不同实例不被当成同一个对象、敏感信息未进入指南。

## 6. 交付与执行记录

交付更新对象表、两条实际例子的调用/产物路径、验证命令与结果、总导航链接建议和未实测内容。真实启动器行为若有独立问题，给出最小触发与证据，由主审处理，不把文档工作扩大成启动/安装机制改写。

完成后交回复审；除非用户后续明确要求，不自行提交、安装、部署、重启或向 QQ 发消息。工作区原有配置、前端、数据库及私人素材改动完整保留。

### 编写本单时的核验

- HEAD：`d6e79d29`；已核对公共 bootstrap、根启动器、release 入口、实例 EXE 拷贝与原有诊断边界。
- 已核对插件 stage/install 实现、五子棋和函数插件示例；没有更新生产安装产物。
- `tests.test_windows_bootstrap tests.test_public_launcher_aliases tests.test_local_test_launcher tests.test_desktop_feature_diagnostics`：37 项通过，2.942 秒。
- 本轮只创建执行单，未新建领域指南、构建/部署 EXE、重启或修改生产配置。

### 执行者填写

- 开工 HEAD、保留的工作区改动：HEAD 为 `d6e79d29`。保留了工作区已有未提交改动（包括 `companion_v01/bot_runtime_components/runtime.py`、`companion_v01/durable_session_queue.py`、`companion_v01/qq_session_queue.py`、`desktop_pet_next/src/control-center-v2/...` 等文件改动及各类 untracked 文件），无额外重置。
- 四类更新对象及实际入口：
  1. Python 后端（`companion_v01/...`）：常规入口为公共 bootstrap `-Action Restart`；直接调用根启动器时同时对齐实例和环境，需仅更新后端可用 `-Action Restart -SkipDesktop`。核验 `/health` 的 `status=ok`、实例 ID、root binding 和新 PID。
  2. 桌宠前端与原生 Rust（`desktop_pet_next/`）：使用根启动器 `-Rebuild` 构建并更新实例；Tauri 已自动执行 Vite，无需重复构建。实际 UI 产物为 `$DataRoot/bin/akane_desktop_pet_next.exe`，核验 EXE 哈希、新进程与 UI ready。
  3. 已安装插件源码（`plugins/<name>/`）：扩展管理 `stage_source` → 检查返回的 `permissions` → `install_stage`。比较同一次暂存/安装的 digest，并检查实际激活及贡献项；公共清单字段是 `runtime_status`。
  4. 独立 Python 依赖包（平级独立库与 `requirements-packages.txt`）：dot-source `scripts/sync_akane_local_packages.ps1` 后显式调用 `Sync-AkaneLocalPackages`；核验安装来源、版本、所需接口与内容。戳记只记录完整同步，不能代替安装内容比对。
- 两个例子的源码、产物/安装与运行证据：
  1. 五子棋功能：README 链接领域指南，区分离线构建与实例部署、插件源码与安装产物。主审纠正了执行者误加的 Vite 前置步骤：标准 Tauri CLI 已运行该钩子。静态诊断与原生拖动、关闭及模型落子仍分别验收。
  2. 最小函数插件（`akane_sdk_math`）：核对了独立公开 SDK 规范、`example.math.add` 工具及 `capability.prompt.invoke` 权限声明；通过已有隔离安装测试验证了 Stage/Install 生命周期。
- 修改文件、命令/链接核对和验证结果：
  1. 新建 `docs/source_to_runtime_update_v1.md`；
  2. 修正 `examples/desktop_features/gomoku/README.md` 中的构建部署说明并增加指南链接；
  3. 填写本执行单 `docs/runtime_update_development_clarity_execution_plan_v1.md`；
  4. 验证通过：
     - `.\.venv\Scripts\python.exe -B -m unittest tests.test_windows_bootstrap tests.test_public_launcher_aliases tests.test_local_test_launcher tests.test_desktop_feature_diagnostics`（37 项全部通过）；
     - `.\.venv\Scripts\python.exe -B -m unittest tests.test_plugin_installation_lifecycle tests.test_plugin_installation`（22 项全部通过）；
     - `.\.venv\Scripts\python.exe -B scripts/check_desktop_feature.py gomoku`（主审复核为 21 项 `[OK]`，未给运行地址与 `--running` 的两项为 `[SKIP]`）；
     - `pwsh -File .\scripts\bootstrap_akane_windows.ps1 -Action Status`（成功读取 active profile 及运行组件）；
     - `git diff --check` 无格式异常。
- 总导航链接建议、未实测内容及独立问题：
  - 导航链接建议：交由主审在 `docs/DEVELOPMENT.md` 相应小节中补充指向 `docs/source_to_runtime_update_v1.md` 的链接（从源码更新到运行实例指南）。
  - 未实测内容：按边界约束，未执行生产实例的强制 Restart/Stop、未重新构建生产桌宠发布 EXE、未向生产数据目录安装测试插件。
  - 独立问题：无。
- 提交/部署状态：未执行 git commit，未修改生产配置或重启生产实例，工作区修改就绪，交由主审查验。

### 2026-10-12 主审补正与复核

- 文档有多处操作性错误，已纠正：实际 EXE 文件名用下划线；健康接口为 `status=ok` 和 `root_binding=valid`；Status 不返回端口；根启动器需要对齐实例及环境，多个环境文件按保存顺序加载。
- 标准 Tauri CLI 已通过 `beforeBuildCommand` 自动运行 Vite，移除必须手动预构建以及随后再 `-Rebuild` 的重复步骤。区分离线构建、实例文件同步、进程更新与原生功能证据。
- 插件产物位于 `extensions/plugins`；Stage 返回 `permissions`，公共清单使用 `runtime_status`。比较同一次 Stage/Install 的 digest，并保留 disabled / waiting_dependency 等实际状态，不把安装成功当成已可调用。
- 包同步文件是函数库，改为 dot-source 后调用 `Sync-AkaneLocalPackages`；说明完整邻库条件、单包 wheel 正常安装，以及同步戳记和安装内容核验的不同用途。启动器测试不能代替已安装包接口检查。
- 最小 SDK 示例的测试先沿原 README 安装干净环境中的依赖和插件项目；已有隔离生命周期测试不等于生产实例已安装示例。五子棋 README 与 `DEVELOPMENT.md` 导航已统一接入指南。
- 主审运行启动器/别名/本地实例/桌宠诊断及插件安装两组共 59 项测试，全部通过；与另一单的 64 项合计 123 项相关测试通过。五子棋只读诊断为 21 OK、2 SKIP，未检查生产运行插件/EXE。
- 六份文档的 28 个本地链接/锚点和代码围栏、24 个 PowerShell 模板语法检查通过；命令参数与实际脚本/函数声明对照，`git diff --check` 通过。
- 该单没有改启动器、诊断、安装实现或前后端业务。未实际构建和部署 EXE、更新生产插件或依赖、验收原生拖动/关闭、重启或提交；现有无关工作区修改保留。
