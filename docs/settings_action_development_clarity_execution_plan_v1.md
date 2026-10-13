# 设置动作开发入口收敛执行单 v1

日期：2026-10-11。状态：已实施并复验，未提交、安装或重启。源码检查基准：`4a2790e5`。

本单可与 `docs/development_clarity_execution_plan_v1.md`、`docs/qq_command_development_clarity_execution_plan_v1.md` 并行执行。范围是桌宠控制中心的动作声明、主窗口命令回执及确认反馈，不涉及模型工具编排和 QQ 命令业务。

## 1. 要交付的效果

让开发者新增或修改设置动作时，明确知道：**按钮的 action ID → 使用的宿主入口 → 主窗口业务 → 本次结果如何反馈**。

完成后：

- 同一动作的命令名、是否需要运行确认、特殊等待时间在一处查到，不再手工维护多份同名名单。
- 主窗口负责补齐回执的关联字段，业务处理器返回真实结果即可；遗漏 `operationId` 不会把失败变成成功。
- 已停止的语音或回复，再收到本次成功停止回执时能够确认，不强制要求出现一次“运行到停止”的状态变化。
- 操作已发送、业务已完成、等待运行状态确认仍各有清楚的含义。失败、关闭窗口和切换对象的旧结果继续受现有归属规则处理。

整理重复维护和真实出口问题，不继续拆碎控制中心模块，也不新增通用 UI 框架、动作执行 DSL 或新的运行限制。

## 2. 已确认的实现和问题

### 实际入口

生产设置页是 `desktop_pet_next/control-center-lab.html`，加载 `src/control-center-v2/index.js`。通常的本机动作沿以下路径执行：

```text
组件/页面事件
  → app/actions.js：当前操作的按钮反馈与请求归属
  → bridge/actions.js：路由、operationId、确认与必要刷新
  → control-center/action-router.js
  → data-source/actions/source.js
  → data-source/tauri.js：Tauri invoke、窗口方法或主窗口事件
  → pet/settings.js：调用实际领域控制器，发布 settingsCommandResult
  → bridge/confirmation.js + view-model/action-results.js：本次确认与反馈
```

HTTP 业务、模型配置、设备操作、记忆设置和 QQ 接入各有实际专用入口。本单保留这些边界，不强制全部变成主窗口事件。

### 重复的动作说明

当前通过运行模块导出确认：

| 位置 | 数量/作用 |
| --- | --- |
| `control-center/action-router.js:CONTROL_CENTER_ACTIONS` | 91 个正式 action ID |
| 同文件 `CONTROL_CENTER_BRIDGED_ACTION_IDS` | 91 个 ID，与上面的集合完全相同，却单独手写维护 |
| `control-center/settings-command-contract.js:SETTINGS_COMMAND_BY_ACTION_ID` | 48 项主窗口命令映射 |
| `control-center-v2/bridge/confirmation.js:OBSERVED_ACTION_IDS` | 38 项要观察确认的动作 |
| 同文件 `confirmationTimeout()` | 另按动作名选择等待时间 |

目前 38 项都有命令映射，没有证据证明名单已经缺项。问题是新增一个动作需要理解这些重复登记的关联。屏幕观察已有 `SCREEN_OBSERVATION_COMMANDS` 权威定义，应继续复用，不能再抄一份。

48 项映射里有 10 项当前不走观察等待：`workspace.open`、三个 `advanced` 窗口动作，以及 `music.clear`、`music.setPlayMode`、`music.setVolumeNormalization`、`music.seek`、`music.selectQueueItem`、`music.playWorkspaceRecommendation`。不要为了统一表格将它们全部加进等待集合。

### 已复现的行为

1. **空闲停止无法确认。** 用 `scripts/pet-runtime-harness.mjs` 加载真实 `createPetApplication` 和设置控制器，未启动实际桌宠或音频服务。空闲时发送带本次 `operationId` 的 `stopTts` / `stopReply`，主窗口真实返回 `ok: true, status: completed`，但 `isObservedActionConfirmation()` 返回 false。因为它还要求 `before.active` 曾经处于运行状态。在控制中心等待链中，这可能一直等到时限后被显示成“未确认”。
2. **业务失败被补成成功。** 用同一个 Vite harness 加载真实 `createSettingsController`，给 `systemMedia.controlActiveMusic` 一个临时业务替身，返回 `{ok: false, status: 'failed', reason: 'local_fixture_failure'}`。请求包含 `operationId`，业务结果没有它；实际外层返回变成 `{ok: true, status: 'completed'}`。`executeSettingsCommand()` 末尾通过“结果里的 operationId 是否匹配”决定是否新造成功回执，覆盖了原结果。

第二项是边界夹具复现，不能据此声称实际系统媒体服务已经发生过同样错误。它说明新业务处理器必须掌握一个不明显的回执字段要求，否则真实失败会被掩盖。

### 已运行的基线

在 `desktop_pet_next` 目录执行：

```powershell
node --test scripts/control-center-bridge.test.mjs scripts/control-center-bridge-runtime.test.mjs scripts/control-center-view-model.test.mjs
node scripts/control-center-action-bridge-smoke.mjs
```

68 项行为测试通过；动作 smoke 通过，覆盖 91 个生产动作，包含 28 个事件、9 个 invoke 和 14 条后端路由。现有通过结果没有覆盖上述两个问题。

## 3. 文件范围与并行约定

主修改范围：

| 文件 | 本单职责 |
| --- | --- |
| `desktop_pet_next/src/control-center/action-router.js` | 动作 ID 与可转发集合的重复维护退出；保留路由 API |
| `desktop_pet_next/src/control-center/settings-command-contract.js` | 主窗口动作的最小声明及现有映射投影 |
| `desktop_pet_next/src/control-center/data-source/tauri.js` | 读取统一动作说明，维持当前 payload 与传输选择 |
| `desktop_pet_next/src/control-center-v2/bridge/confirmation.js` | 读取观察集合与等待时间，保留等待/取消实现 |
| `desktop_pet_next/src/control-center-v2/bridge/actions.js` | 仅必要的动作说明衔接，不改绑定生命周期 |
| `desktop_pet_next/src/control-center-v2/view-model/action-results.js` | 本次回执、状态确认及空闲停止行为 |
| `desktop_pet_next/src/pet/settings.js` | 统一回执字段补齐，保留原业务结果 |

测试优先扩展现有 `scripts/control-center-bridge-runtime.test.mjs`、`control-center-view-model.test.mjs`、`control-center-action-bridge-smoke.mjs` 和 `pet-runtime-smoke.mjs`，必要时新增 `desktop_pet_next/scripts/settings-command-results.test.mjs`。按实际实现调整相关 control-center 模块检查，不删除行为断言来适应改动。

文档更新限定在 `docs/desktop_pet_control_center_v2/README.md`、`docs/control_center_bridge_split_v1.md` 及本单执行记录。清理本任务附近过期的“停止必须观察到状态变化”说明。

第一份任务保留 `src/pet/application.js`、`ui.js`、`panel.js`、五子棋代码和桌宠诊断文件的修改权。本单不编辑这些文件，不编辑 `package.json`、公共开发导航 `docs/DEVELOPMENT.md` / `docs/PROJECT_MAP.md`，也不修改前两份执行单。

所有 Python 后端、Rust 窗口代码、QQ 指令、工具编排、提示词、MemCore、运行配置和数据库均不在本单范围。`app/actions.js`、数据源业务处理器、`screen-observation.js`、`pet/voice.js`、`pet/chat.js`、`pet/system-media.js` 和 `pet-runtime-harness.mjs` 可读取，保留其业务职责。

与第一份任务并行 build 时，各次构建会使用当时完整源码。记录整合期间的状态，不把另一个任务尚未完成导致的失败算作本任务回归，也不要覆盖其工作。

## 4. A：动作说明集中，名单由它产生

### 实现步骤

1. 保留 `CONTROL_CENTER_ACTIONS` 的现有 ID、对象键和公开导出。目前可转发集合就是全部正式 ID，`CONTROL_CENTER_BRIDGED_ACTION_IDS` 可直接由正式 ID 导出，不再维护第二份同名名单。
2. 在现有 `settings-command-contract.js` 中建立主窗口动作的最小声明，记录命令名、是否需要观察确认和确有差异的等待时间。默认等待时间只写一次；确实依赖 payload 的屏幕启用时间可保留简短选择函数。
3. 从这份声明产生 `SETTINGS_COMMAND_BY_ACTION_ID`、观察动作集合和等待时间查询。消费方读取它们，不再手写相同的成员名单。现有公开导入位置可通过直接导出保留，不留两套语义源。
4. `SCREEN_OBSERVATION_COMMANDS` 继续拥有屏幕命令和字段映射，动作说明从它产生相关条目；不要在新表里复制命令名与字段。
5. 运行侧状态确认函数保留在 `action-results.js`。不要把播放、附件、角色切换和 Bot 持久绑定这些领域判断塞进一个巨大的声明对象。

声明中不加入所有 HTTP endpoint、表单字段、UI 文案、权限、全部业务 handler 或缓存数据。非主窗口动作继续沿现有专用入口执行。当前 router 的显式注册能力与未知动作反馈保留，不把新表当成权限白名单。

保留各类动作当前的传输优先级。例如 `workspace.open` 同时有命令映射和直接 Tauri 打开入口，现有直接 invoke 优先；不能因为映射表里有命令名就改成发事件。

该切片以行为不变为目标：不增加观察集合，不重算全部超时，不改变事件名、窗口目标、action ID、command 名或 payload 字段，不把事件发送成功当作实际业务完成。

### 验收

- 91 个 action ID 和当前路由行为保持，未知/不可用动作仍如实返回。
- 初始 48 项映射和 38 项观察成员与原基线一致；如果后续为已证实的行为问题调整，应在 B 的记录中说明。
- 开发者在一处看到主窗口动作的命令、观察要求与超时，消费方没有重复名单。
- 模块依赖保持无环。尤其不能让基础声明反向导入 `bridge`、view-model 或桌宠控制器。

## 5. B：回执包装保留真实业务结果

在 `pet/settings.js` 的现有命令执行边界，统一补齐 `command`、请求的 `operationId` 和时间字段。业务处理器可以返回自己的 `ok`、`status`、`reason` 及确有用途的附加字段，身份关联由宿主负责。

处理规则：

- 返回了业务结果，就保留其成功/失败、排队/执行中状态和原因。缺少 `operationId` 是待补齐字段，不是新造成功回执的条件。
- `ok: false` 和明确的 `failed` 等状态不能被覆盖；`pending`、`queued`、`running` 等非终态不能被改成 `completed`。
- 未识别命令继续明确返回 `not-supported`，异常保留原安全错误转换。不能用泛化 catch 或默认空成功处理错误。
- 原来确实只执行同步设置、没有返回业务对象的分支，可以保留简洁的完成回执。不为了整理包装批量重写所有领域控制器；播放、角色切换等后续仍按原运行状态确认。
- 不要求每个业务处理器手工写同一套回执字段。运行侧已经存在的旧回执和其他请求的快照仍不得拿来确认当前请求。

保留 `handleSettingsCommand()` 已有的同命令请求代次、关闭状态及 `lastSettingsCommandResult` 写入判断。整理回执字段不能让慢旧请求覆盖新结果，也不能让一个动作重复执行。

### 空闲停止

对 `voice.stop` 和 `chat.stop`：本次命令与 `operationId` 匹配、回执成功且终态、当前实际快照已经停止时，可以确认；不要求操作前曾处于运行状态。

失败回执仍如实结束等待并呈现失败；其他操作、旧 operationId、缺失或正在运行的状态不能制造成功。没有 operationId 的既有调用保留原状态观察方式，不借这个切片重做全部旧入口。

Bot 切换仍验证本次回执及持久绑定；角色/服装切换、屏幕观察和音频试听保留各自确有必要的状态确认。该修复不等于“所有动作收到回执就直接成功”。

### 实施顺序

1. 先添加缺少关联字段的失败结果回归，以及空闲停止的实际设置控制器回归。通过现有 Vite harness 加载生产模块；直接 Node import 依赖 `import.meta.glob` 的设置控制器不可用，不要为测试改生产模块。
2. 修复回执补齐，验证真实失败和非终态得到保留。
3. 修复空闲停止确认，验证成功和失败、当前与旧请求两种归属。
4. 再移除受影响分支附近多余的字段包装，仅整理本任务需要的部分，不把大型 switch 拆成几十个新文件。

使用 harness 时在 `finally` 中 dispose 应用并 `await runtime.close()`，避免 Vite 测试服务器使测试进程留在后台。

## 6. 行为验收

| 场景 | 要证明的结果 |
| --- | --- |
| 业务失败/执行中结果没有 operationId | 宿主补字段，保留失败原因或非终态；没有成功覆盖 |
| 业务抛异常、未知命令 | 真实错误或不可用状态，回执与当前请求关联 |
| 已空闲的语音和回复再次停止 | 本次成功回执加已停止状态可立即确认，不等完整超时 |
| 正在运行时停止、实际停止失败 | 原成功行为保持；失败原因可见，不伪造完成 |
| 旧回执、其他动作回执、状态字段缺失 | 不能确认当前动作；不能将缺失状态当作已经停止 |
| 同命令连续请求、关闭、A→B→A | 慢旧结果不覆盖新选择；等待及时取消，不发起过期刷新 |
| Bot 与角色切换 | 保留当前请求归属、持久绑定验证和同 ID 跨对象隔离 |
| 播放试听与附件操作 | 只发送成功不等于播放/导入成功；既有等待窗口和状态判断保留 |
| 非观察动作与直接 invoke | 原执行入口和反馈保持，不增加额外等待或重复操作 |
| 屏幕观察动作 | 命令与状态字段继续来自现有权威表，启用失败可见 |

测试需要调用生产路由、设置控制器及确认函数；原生服务可用隔离替身。不只断言“新表和它产生的名单相同”，也不靠行数断言证明理解门槛降低。

在 `desktop_pet_next` 中按切片运行受影响检查，完成后运行一次相关组合：

```powershell
node --test scripts/control-center-bridge.test.mjs scripts/control-center-bridge-runtime.test.mjs scripts/control-center-view-model.test.mjs scripts/control-center-app.test.mjs
node scripts/control-center-action-bridge-smoke.mjs
node scripts/control-center-bridge-modules.mjs
node scripts/control-center-view-model-modules.mjs
node scripts/pet-runtime-smoke.mjs
node scripts/control-center-browser-smoke.mjs
npm run build
```

新增测试文件时再运行 `node --test scripts/settings-command-results.test.mjs`；仓库根运行 `git diff --check`。浏览器检查在真实设置页上覆盖按钮反馈、失败重试、慢请求、切换和重新进入，扩展原隔离 fixture，不能向实际媒体设备或生产数据派发测试动作。

浏览器脚本需 Playwright/Edge；若本机依赖不可用，先查已有运行依赖配置并记录限制，不为本单临时重装大套工具。真实 Tauri/WebView2、声卡、麦克风及运行实例未联调时明确报告，不能以替身测试宣称实机验收完成。

## 7. C：新增设置动作的阅读路径

在 `docs/desktop_pet_control_center_v2/README.md` 添加简短开发路径，用现有音量动作说明：按钮如何发出 action ID、动作说明如何指定命令和确认、主窗口在哪调用领域方法、回执在哪统一补齐、用哪个实际测试验证。

再说明两类不走这条事件链的真实例子：直接窗口 invoke、后端业务动作。开发者先按业务归属选择入口，不需要把所有操作登记进主窗口。

调整 `docs/control_center_bridge_split_v1.md` 中动作名单和确认的现行说明，保留历史拆分记录。不要复制完整动作表，不另造开发手册，通用导航由三个任务整合后集中更新。

## 8. 交付与整合

开工先看 `git status --short`，只修改本单范围，保留其他任务的工作。确认当前任务 diff、更新本单执行记录；除非用户另行要求，不自行 stage、commit、重启或向生产实例发动作。

最终报告：

- 现在新增一条本机设置动作需要维护哪些位置，哪些重复登记已经移除。
- 回执字段由谁负责，失败与非终态如何保留。
- 空闲停止的本地回归结果，以及旧请求/切换保护验证。
- 68 项基线、91 动作 smoke 与新增行为测试结果，浏览器/build 和实机验证的实际范围。
- 修改文件列表，确认未编辑前两份任务保留的文件。

建议先完成这三份切片，再集中检查一次整合后的调用链和开发导航；不继续并行铺开聊天、记忆或基础配置的重构。

### 执行记录（接续模型更新）

- **开工 HEAD 与工作区状态**：基线 HEAD `4a2790e5`（`refactor(runtime): simplify public entries and settings assembly`）。工作区包含前序任务并行的后端工具链路、五子棋及聊天取消修改，本单仅修改设置动作相关前端文件，未触碰保留范围。
- **A 动作说明与重复登记处理**：
  - `desktop_pet_next/src/control-center/action-router.js`：移除 91 行手写重复的 `CONTROL_CENTER_BRIDGED_ACTION_IDS`，直接基于 `CONTROL_CENTER_ACTIONS` 冻结值导出。
  - `desktop_pet_next/src/control-center/settings-command-contract.js`：建立单一权威声明 `SETTINGS_ACTION_SPECS`，包含 48 项主窗口动作（28 项显式观察动作 + 10 项屏幕观察动作从 `SCREEN_OBSERVATION_COMMANDS` 派生 + 10 项非观察动作），统一派生 `SETTINGS_COMMAND_BY_ACTION_ID`、`OBSERVED_SETTINGS_ACTION_IDS` 与 `getSettingsActionTimeout()`。
  - `desktop_pet_next/src/control-center-v2/bridge/confirmation.js`：直接接入统一动作声明派生的观察动作集合与超时函数，移除冗余的手工 Set 与超时分支。
- **B 回执和停止行为修复**：
  - `desktop_pet_next/src/pet/settings.js:executeSettingsCommand`：在命令退出边界统一为回执补齐 `command`、`operationId` 与 `at`；业务处理器返回的失败状态（如 `local_fixture_failure`）和非终态（如 `queued`、`running`）得到完整保留，不再因遗漏 `operationId` 被覆盖为虚假成功；未知命令统一返回带 `operationId` 的 `not-supported` 回执。
  - `desktop_pet_next/src/control-center-v2/view-model/action-results.js:isObservedActionConfirmation`：修复 `voice.stop` 与 `chat.stop` 空闲停止确认行为。在携带本次 `operationId` 且收到匹配的成功完成回执时，当前快照处于已停止状态（`speaking === false` 或 `!sending && !replyDisplayActive`）即可立即确认，不再强制要求 `before` 曾处于运行态；同时确保状态字段存在且非运行态，避免因状态缺失造成误判；不带 `operationId` 的历史调用保留原状态跃迁观察。
- **C 阅读路径和最终文件列表**：
  - `docs/desktop_pet_control_center_v2/README.md`：新增设置动作开发路径指南，以 `voice.setVolume` 梳理完整链路，并说明直接 invoke 与后端业务两类不走主窗口事件的专用入口；清理过期的“停止必须观察到状态变化”描述。
  - `docs/control_center_bridge_split_v1.md`：更新模块职责中对统一动作声明的引用及现行空闲停止确认规则。
  - 最终修改文件：
    - `desktop_pet_next/src/control-center/action-router.js`
    - `desktop_pet_next/src/control-center/settings-command-contract.js`
    - `desktop_pet_next/src/control-center-v2/bridge/confirmation.js`
    - `desktop_pet_next/src/control-center-v2/view-model/action-results.js`
    - `desktop_pet_next/src/pet/settings.js`
    - `desktop_pet_next/scripts/settings-command-results.test.mjs`（新增回归测试）
    - `docs/desktop_pet_control_center_v2/README.md`
    - `docs/control_center_bridge_split_v1.md`
    - `docs/settings_action_development_clarity_execution_plan_v1.md`
  - 确认未修改第一份任务保留的文件（`application.js`、`ui.js`、`panel.js`、五子棋、桌宠诊断、`package.json`、`DEVELOPMENT.md`、`PROJECT_MAP.md` 及前两份执行单）。
- **定向检查、浏览器/build、实机验收限制**：
  - 新增测试 `node --test scripts/settings-command-results.test.mjs`：3 项测试通过（覆盖合约派生、超时匹配、空闲停止即时确认、缺失状态阻断、旧请求隔离、业务失败保留及非终态保留）。
  - 行为基线：`node --test scripts/control-center-bridge.test.mjs scripts/control-center-bridge-runtime.test.mjs scripts/control-center-view-model.test.mjs scripts/control-center-app.test.mjs`（103 项测试全部通过）。
  - 动作 Smoke：`node scripts/control-center-action-bridge-smoke.mjs`（91 个生产动作、28 事件、9 invoke、14 后端路由全部通过）。
  - 模块边界：`node scripts/control-center-bridge-modules.mjs` 与 `node scripts/control-center-view-model-modules.mjs` 全部通过（无循环依赖，无未绑定标识符）。
  - 运行时 Smoke：`node scripts/pet-runtime-smoke.mjs` 全部通过。
  - 浏览器 Smoke：`node scripts/control-center-browser-smoke.mjs` 全部通过（7 页、离线/重连、草稿竞态、0 错误）。
  - 构建：`npm run build` 成功。
  - 仓库 diff 检查：`git diff --check` 通过。
  - 实机验收限制：上述验证均在无头与隔离 harness / fixture 环境完成；真实 Tauri 进程、物理声卡、麦克风设备与外部生产服务未联调，需后续在真实桌面环境中整体验收。

### 整合复验（2026-10-11）

- 复验 HEAD：`f12f1ece`。此时前序工具准备与桌宠诊断任务已提交；QQ 命令任务和个人素材仍在工作区，本次未编辑它们。
- 核对真实链路：`bridge/actions.js` 生成本次 `operationId`，`data-source/tauri.js` 按实际入口调用 invoke 或发布主窗口事件，`pet/settings.js` 调用领域控制器，观察层按本次回执及实际状态确认。失败、排队状态与旧请求隔离保持有效。
- 修复一个回执关联漏点：`previousMusic`、`nextMusic`、`stopMusic` 调用真实播放器时，领域结果的 `command: "controlActiveMusic"` 会覆盖宿主收到的命令名。通过生产控制器 harness 先复现失败，再把宿主 `command` 放在领域结果展开之后，统一保留请求命令名；业务 `ok`、`status`、`reason`、媒体动作与目标保持原值。
- 删除没有调用者的新增超时别名与测试中的未使用导入，修正文档顶部的待执行状态。
- 回归补充：真实播放器命令名归属、声明派生一致性、两类停止的旧回执/非终态/缺失必要状态字段。新增文件现有 4 项测试。
- 最终行为检查：`node --test scripts/settings-command-results.test.mjs scripts/control-center-bridge.test.mjs scripts/control-center-bridge-runtime.test.mjs scripts/control-center-view-model.test.mjs scripts/control-center-app.test.mjs`，107 项通过，0 失败。
- 其余复验：91 个生产动作 smoke、bridge 和 view-model 模块边界检查、`pet-runtime-smoke.mjs`、`control-center-browser-smoke.mjs`（7 页、0 页面错误）、`npm run build`（1244 个模块）均通过。
- `git diff --check` 通过，仅提示部分既有 CRLF 文件将规范化为 LF；暂存区为空。
- 未向生产实例发送动作，未安装新桌宠或重启服务。浏览器与控制器检查使用隔离 fixture，不能替代真实 Tauri、物理声卡/麦克风和外部服务联调。
