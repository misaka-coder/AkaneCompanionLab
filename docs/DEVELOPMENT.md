# 开发入口

这是当前源码的任务导航。先选要做的功能，再读对应实现；全量文件定位见 [PROJECT_MAP](PROJECT_MAP.md)。

## 按任务找入口

| 要做什么 | 先读 | 最小验证 |
|---|---|---|
| 改内置工具参数 | `capability_registry_runtime/*_contracts.py`；handler 的 `tool_spec()` 与 `normalize_call()` | `tests.test_native_tool_schema tests.test_tool_invocation tests.test_tool_orchestration_contract`，再跑该 handler 的测试 |
| 新增普通插件工具、接第三方 API | [SDK 入门](../akane_plugin/README.md)、[函数示例](../examples/plugins/akane_sdk_math/) | 插件自己的测试、stage/install 后检查 active 并实际调用 |
| 给桌宠增加交互窗口或小游戏 | [完整五子棋参考](../examples/desktop_features/gomoku/README.md) | 接线诊断、相关 Python/JS 测试、Tauri 构建与原生验收 |
| UI 与模型共用业务状态 | [五子棋共享状态链路](../examples/desktop_features/gomoku/README.md) | 插件服务与工具测试、`npm run test:gomoku` |
| 改了代码但运行没变化 | [源码更新到运行实例指南](source_to_runtime_update_v1.md) | 对应源码诊断、构建产物检查、运行实例核验 |
| 改桌宠聊天、气泡、声音 | `desktop_pet_next/src/pet/chat.js`、`bubble.js`、`voice.js` | 对应 `package.json` 中的 test/smoke、前端 build |
| 改设置页与动作 | [设置动作指南](desktop_pet_control_center_v2/README.md#settings-action-development-guide)、`src/control-center/data-source/tauri.js`、`src/pet/settings.js` | `node --test scripts/settings-command-results.test.mjs`、`node scripts/control-center-action-bridge-smoke.mjs` |
| 改 QQ 指令或事件 | [QQ 指令开发者指南](qq_commands_v1.md#开发者入口与新增指令指南)、`companion_v01/qq_commands.py`、`qq_command_handlers.py` | `tests.test_qq_command_dispatch`、`tests.test_qq_commands` |
| 改聊天处理或工具轮 | `companion_v01/engine_services/turn_preparation.py`、`turn_rounds.py`、`turn_completion.py` | 对应聊天、工具编排和上下文测试 |
| 改配置保存与生效 | [配置生效链路指南](configuration_effect_v1.md)、`companion_v01/settings_overrides.py`、`bot_runtime_components/factory.py` | `tests.test_settings_overrides`、`tests.test_bot_settings_assembly` |
| 做角色包 | [角色工坊](desktop_pet_character_workshop_v1/README.md) | 工坊导入、切换角色、资源检查 |

表中的 `src/` 指 `desktop_pet_next/src/`。先运行 `git status --short`，保留已有改动。

## 聊天请求从哪里走

QQ 群消息：`routes/qq.py` → `qq_inbound_events.py`，由 `qq_gateway_runtime/inbound.py` 解析发送人、群和消息 → `QQTurnRunner`（`qq_turn_runtime.py`）→ `qq_turn_streaming.py` → `engine.process_turn_stream`。
群会话是 `qq_group_shared_<group_id>`；发送人单独记录，群会话 ID 不是用户 ID。指令和只需记录的群消息会在入口分流。

桌宠消息：`src/pet/chat.js` → `backend.js` 生成当前 Bot 的 URL → `routes/think.py` → `routes/think_stream.py` 推进 `engine.process_turn_stream` 并返回流式事件。
主人记忆统一由 `owner_private_session.py` 映射存储与召回；界面请求、工具和插件仍使用当前客户端的业务身份。

两端进入同一个 `engine.py:_run_turn_core`：准备上下文（`turn_preparation`）→ 模型与工具轮（`turn_rounds`）→ 保存和输出（`turn_completion`）。改一处行为时先找负责它的阶段。

## 公共入口与实现位置

| 公共导入入口 | 实现所属目录 |
|---|---|
| `companion_v01/bot_runtime.py` | `bot_runtime_components/`：装配、生命周期、完成通知 |
| `companion_v01/capability_registry.py` | `capability_registry_runtime/`：工具定义、能力选择；执行由 `executor_broker.py` 负责 |
| `companion_v01/host_tool_jobs.py` | `host_tool_job_runtime/`：持久任务、结果和完成交付 |
| `companion_v01/extension_management.py` | `extension_management_runtime/`：插件选择、清单和安装管理 |

这四个入口只导出 `__all__` 声明的公共 API；修改或测试内部函数时直接定位实现模块。第三方插件使用 `akane_plugin` SDK。
`memcore`、`capcore`、`channelcore_onebot` 是当前 Python 环境中的独立依赖包，修改宿主桥接不等于修改依赖实现。

## 改工具参数或新增插件工具

内置工具的输入结构、风险和确认语义以 `CapabilityToolSpec` 为准。多数定义按领域放在
`companion_v01/capability_registry_runtime/*_contracts.py`；少数本地工具（例如商店工具）
把 spec 放在自己的 handler 模块。`companion_v01/tool_handlers/builtin_tables.py` 的
`TOOL_SPEC_BY_TYPE` 汇总内置定义，元信息从 spec 投影；`_BUILTIN_TOOL_CLASSIFICATION`
只保存 family、operation、预算和别名等补充分类。参数默认值或结构变更不要再补一份元信息 schema。

在 handler 的 `normalize_call()` 和执行方法里修改业务校验或行为。模型调用由
`engine_services/tool_protocol.py:_prepare_tool_round_decisions` 解析并保留调用信息；
`tool_orchestration/validation.py:prepare_tool_invocation` 绑定当前 handler、剥离宿主调度字段、
准备业务参数一次。校验与 `tool_orchestration/execution.py` 共用该准备结果。
原生适配器的业务值从保留的 wire 参数进入 `arguments` 封套，业务字段 `type`、`arguments`
不能与外层路由字段混淆。`computer_use` 保持参数不改写，仅在准入时给出一次结构化校验诊断。
新增参数至少核对：

```powershell
.\.venv\Scripts\python.exe -B -m unittest tests.test_native_tool_schema tests.test_tool_invocation tests.test_tool_orchestration_contract
```

再运行该工具自己的 handler/service 测试。实际工具调用与失败结果覆盖比只比较 schema 更重要。

普通插件工具从 [最小函数工具示例](../examples/plugins/akane_sdk_math/) 开始，用公开的
`akane_plugin.Plugin` 与 `@plugin.tool`；不要导入 `companion_v01` 私有实现。需要给桌宠 UI
调用的业务能力时，定义 versioned program service；模型工具与 service 可以复用同一业务模块。
程序服务本身不会唤醒模型，UI 要让角色参与时走 `src/pet/chat.js` 的正常 `chat.sendMessage`。
更新插件源码需经过 `stage_source` / review / `install` 并确认 active；只改当前工作目录不会更新
运行中的已安装插件。

## 配置来源与生效范围

详细分析、修改目标入口映射、三个端到端实例及新增配置步骤参见 [配置生效链路指南](configuration_effect_v1.md)。

从 `bot_runtime_components/factory.py:_load_runtime_settings` 看装配顺序。`config.py` 提供默认值并读取环境；
该 Bot 运行目录的 `settings_overrides.json` 覆盖可编辑项。隐式单 Bot 入口为兼容仍把保存值写入进程配置；
显式 `BotConfig` 使用独立 `RuntimeConfigView`，不会改其他 Bot 的进程默认值。`BotSettingsView.from_config()`
捕获当前 Bot 设置。

模型服务配置走另一层：`model_service.json` 读入后覆盖 `BotSettingsView` 中对应的模型服务字段；
工厂传入的显式 `settings_overrides` 再覆盖 view。QQ `/模型` 选择保存在 QQ 会话偏好里，作为当轮
`chat_model_override` 从 inbound context 传到 turn preparation 和模型目标选择；它只影响该会话请求，
不会改后端保存设置。`settings-catalog.current` 是当前 config view 的值，不代表所有专用服务或
QQ 当轮模型选择的最终结果。查看 `current`、`desired`、`pendingRestart`、`managedIn` 和 `scope`
来判断归属与生效动作；不要仅从字段名猜即时生效或重启要求。

## 桌宠交互的实际链路

```text
子窗口操作 → 主窗口功能控制器 → 插件程序服务 → 真实业务状态
                         ↓
                正常 chat.sendMessage
                         ↓
              模型工具 → 同一业务状态 → 子窗口刷新
```

主窗口持有聊天、连接和当前角色；子窗口负责显示与用户操作；插件负责业务状态。
程序服务供代码调用，模型工具供模型调用。需要模型参与时复用正常聊天入口，语音、表情、工具和 MemCore 随原链路处理。
任务执行结束、结果进入模型上下文、回复发送到客户端分别有自己的状态；判断操作是否成功要看实际执行结果。

当前可运行例子是 `plugins/akane_gomoku/src/akane_gomoku/__init__.py`：桌宠经
`src/pet/plugin-service.js` 调 `board.v1.control`，模型经 `@plugin.tool` 调同一棋局管理器；
主窗口用 scope 丢弃切换角色或会话后的旧响应。按此模式接入业务服务，不让前端绕过服务直接改插件状态。

窗口创建可复用 `src-tauri/src/pet_windows/docked.rs`，程序服务可复用 `src/pet/plugin-service.js`。
这两处助手减少重复代码，窗口的 HTML、命令和权限仍在正常配置中显式注册。

## 三种身份各负责什么

| 身份 | 用途 | 从哪里取 |
|---|---|---|
| 客户端业务身份 | 界面请求、工具和棋局等业务状态归属 | 前端当前 state；插件用 `ctx.invocation.session_id`、`profile_user_id`、`character_pack_id` |
| 记忆存储身份 | 决定对话写入与召回 | 后端既有记忆策略 |
| 操作人身份 | 决定谁有权执行 | 宿主解析；插件必要时用 `ctx.invocation.authorization_profile_user_id` |

主人桌宠与 QQ 私聊共用记忆，客户端业务身份仍保留各自的原值。插件业务状态使用客户端身份，记忆存储映射由后端处理；插件通常不需要自己实现主人共享记忆映射。
SDK 上下文示例：`ctx.invocation.session_id`、`ctx.invocation.character_pack_id`；不存在的字段应查真实类型与示例，不用猜。
异步界面操作复用 `src/pet/scope.js` 的 capture/isCurrent，切换会话后丢弃旧响应。

## 新增窗口需要接上的位置

以窗口 label `gomoku` 为例：

1. `gomoku.html` 和页面脚本；在 `vite.config.js` 的 input 加入 HTML。
2. `src-tauri/src/pet_windows/gomoku.rs` 创建窗口；`mod.rs` 引入实现。
3. `src-tauri/src/main.rs` 的 generate_handler 注册打开命令。
4. `src-tauri/capabilities/*.json` 给这个 label 授予实际需要的窗口与事件权限。
5. `src/pet/gomoku.js` 接程序服务与正常聊天；在 `src/pet/application.js` 组合控制器并注册事件。
6. 菜单或面板接上打开命令；有插件时在设置中源码安装并启用。

下面的命令只读检查接线与版本状态，不会发消息、下棋或重启进程：

```powershell
# 仓库根目录：源码注册与本机构建产物
.\.venv\Scripts\python.exe -B scripts/check_desktop_feature.py gomoku

# 加上目标后端和当前 Windows 桌宠进程
.\.venv\Scripts\python.exe -B scripts/check_desktop_feature.py gomoku --backend-url http://127.0.0.1:12001 --running
# 多 Bot 时追加 --bot-id 实际BotID
```

诊断按常规命名定位页面和命令，并核对 `src/pet/<label>.js` 在 `application.js` 的导入、组合、
桥接注册、主/子窗口请求和响应事件引用、UI/面板打开入口。非约定布局或无法从字面量确认的封装事件
显示 SKIP 并提示手工定位，不据此判定接线损坏。
同名插件位于 `plugins/akane_<label>` 时，`--backend-url` 只确认运行插件 active 与工具/程序服务贡献声明；
它不比较安装源码内容。相同版本号、generation 或工具名不能证明代码相同；安装响应的 digest 与激活结果是已有部署证据。
`--running` 只将当前桌宠 EXE 与本机构建 EXE 做 SHA-256 比较。未提供地址或 `--running` 时对应项为 SKIP，
不能据此宣称已核实运行版本或原生交互。

## 源码、构建与运行版本

按修改对象选择更新步骤、对齐实例环境及核验产物，见 [源码更新到运行实例指南](source_to_runtime_update_v1.md)。仅检查前端资源时，在 `desktop_pet_next/` 运行 `npm run build`；离线构建发布 EXE 用 `npm run tauri -- build --no-bundle`，Tauri 的 `beforeBuildCommand` 已自动运行前端构建，无需先重复构建。
单独 Cargo 编译用于 Rust 检查，不执行该前端钩子。构建成功还需要实例部署和进程更新。

`npm run tauri -- dev` 使用开发页面。`npm run start:release` 会重建过期的本机构建；根目录普通启动器复用已有产物，更新时使用其显式 `-Rebuild` 操作。
诊断若报告运行 EXE 与发布产物哈希不同，将发布 EXE 部署到桌宠实例使用的目录并重启桌宠；更新后用 `--running` 复核。
插件工具或服务缺失时，检查插件源码声明，重新 stage/install 并确认 active；诊断不会自动安装或重启插件。
Python 后端代码需更新后端进程，桌宠前端/原生代码需更新桌宠构建和实例；重启其中一个不代表另一个已更新。

## 验证范围

小改动运行相关测试、`git diff --check`；前端改动做 build，交互改动实际检查点击、失败、慢请求、重复操作、切换和重新打开。
需要整套验收时用 `scripts/verify_akane.ps1`。具体任务和 AGENTS.md 优先，日常功能改动按相关范围验证。
SDK 文档与示例版本检查：`python scripts/verify_plugin_sdk_docs.py`。

## 当前实现与历史设计

当前桌宠主线只有 `desktop_pet_next/`。旧 Electron、旧 Web 聊天和预览入口已移除；`web/assets/` 仍是共享图片目录。单独运行后端使用 `start_akane.bat -Mode Backend`。
设置主页面是 `control-center-lab.html`，另两个设置 HTML 是兼容跳转。
桌宠延续当前角色与 Bot 的会话，不提供“新对话”动作。前端清理范围和下一阶段入口整理见 [前端重做前置清理](frontend_cleanup_preparation_v1.md)。
`petdesk_runtime_architecture_v0.md` 是后续 runtime 路线设计，`petdesk_akane_bridge_m32.md` 记录另一宿主的适配协议；当前 Next 聊天从 `src/pet/chat.js` 看起。
里程碑、execution_plan 和 progress 文档用于理解背景；当前可调用接口以代码、SDK 类型和可运行示例为准。
