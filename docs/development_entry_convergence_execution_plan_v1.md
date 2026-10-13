# 常见开发入口与示例收敛：执行单 V1

日期：2026-10-12。调研基线：`e6aae9ca`。状态：**复审通过，含补正；已提交并重启当前实例**。

本单可直接交给另一个模型执行。目标是接回已经完成的三份理解门槛任务，让开发者从当前导航进入真实实现，不再照着旧说明恢复已经删掉的重复登记。

## 1. 交付目标

围绕四类常见任务整理现有开发入口：模型工具、QQ 宿主指令、设置动作、桌宠交互窗口。

完成后，开发者应能沿文档回答：

1. 这个任务先读哪里，定义和实际执行分别归谁负责？
2. 新增或修改行为必须接上哪些位置，哪些内容已经自动派生？
3. 一个现有功能具体怎样走过这条链路，失败或旧请求怎样返回？
4. 该跑哪些验证，源码修改怎样进入运行中的插件、后端或桌宠？

复用现有文档、生产示例和测试。主要交付是准确的开发路径和可核验的现有例子；不要求为了完成本单开发一个新功能。代码行数、文件数、测试数量不作为理解门槛指标。

## 2. 已完成的基础与已确认的缺口

前三份执行单已经提交，不能重新做它们：

- `f12f1ece`：工具定义投影、一次参数准备、桌宠接线诊断与开发导航。
- `aa17fb07`：设置动作声明、回执补齐与空闲停止确认。
- `96e0be75`：QQ 命令按正式名称分派，事件层解析结果传给命令处理。

本次从当前实现确认的缺口如下，执行时重新核对，不仅依据行号修改：

| 现有说明 | 当前实现 | 需要处理 |
| --- | --- | --- |
| `PROJECT_MAP.md` 的新增设置命令步骤要求在 `data-sources.js` 修改 `settingsCommandByActionId` | `data-sources.js` 是公共导出入口；`data-source/tauri.js` 从 `settings-command-contract.js` 导入已派生的映射 | 指向声明来源和实际实现，移除手工编辑第二份映射的要求 |
| 同一段要求手动添加 `CONTROL_CENTER_BRIDGED_ACTION_IDS` | `action-router.js` 从 `CONTROL_CENTER_ACTIONS` 的值生成它 | 新动作按实际入口登记；派生列表不再手工维护 |
| `PROJECT_MAP.md` 的验证表仍把旧动作数量描述为当前状态 | 当前 action smoke 输出 91 个生产动作；这些数量还会随功能增加而变化 | 常驻导航描述检查职责；具体数量留在注明日期的验证记录中 |
| `DEVELOPMENT.md` 的 QQ 入口只让开发者从 router 继续查找 | `qq_commands_v1.md` 已有语法、分派、领域实现和出口说明 | 从总入口直接链接现有领域指南，避免另写一份分派教程 |
| 总导航对设置动作和页面数据路径仍较笼统 | `desktop_pet_control_center_v2/README.md` 已有动作指南；公共入口后的实现分布在 `data-source/`、`bridge/`、`view-model/` | 区分公共导入入口与实现位置，并核对常见任务步骤 |
| 五子棋和最小函数插件已有可运行参考 | `examples/desktop_features/gomoku/README.md` 指向生产代码，`examples/plugins/akane_sdk_math/` 使用公开 SDK | 以现有参考完成核验，修正发现的误导，不复制第二套业务实现 |

控制中心快照的字段也已超过导航中旧的“五个 provider”表述。核对 `routes/control_center.py` 当前聚合字段，更新本单涉及的当前说明，不为了保留旧数字改动实现。

历史执行单的“改前行为”和当时测试数量是历史记录，不是错误。保留它们的时间与基线；此次优先修正当前开发入口，不批量改写历史文档。

## 3. 开工与工作区边界

先执行 `git status --short`、记录 HEAD，再查当前调用者、实现和测试。Windows 搜索用目录加 `-g` 或明确文件，避免把 `engine*.py` 当成一个可展开的路径传给 `rg`。

本单编写时，工作区还有队列/信箱、任务存储、私聊记忆镜像、模型配置/流式请求及设置页模型相关改动，以及私人素材和脚本。这些属于其他任务，保留原状；不纳入本单的修复、暂存或提交。已有改动的复审与收尾另行处理，不作为本单开工前必须执行的步骤。

本单主要可编辑范围：

- `docs/DEVELOPMENT.md`
- `docs/PROJECT_MAP.md`：只整理本单涉及的当前任务导航和相关模块/验证说明
- `docs/qq_commands_v1.md`：仅当领域指南确有缺口时补正
- `docs/desktop_pet_control_center_v2/README.md`
- `examples/desktop_features/gomoku/README.md`
- `examples/plugins/README.md`、`examples/plugins/akane_sdk_math/README.md`：仅修正相关入口或运行说明
- 本执行单的执行记录

`docs/control_center_bridge_split_v1.md`、`docs/control-center-lab-contract.md` 可以只读核对；只有当前用法与本单新指南直接冲突时，局部修正文案并说明原因。保留历史验收记录。

生产代码、SDK 实现、启动器和现有测试默认只读。若核验发现真实业务缺口，记录具体调用路径、证据和建议切片，交回复审；这份导航任务不扩展为队列、模型配置、记忆或工具机制重写。文档内的错误代码片段可以直接修正。

## 4. 文档分工

| 文档 | 保留的职责 |
| --- | --- |
| `DEVELOPMENT.md` | 中文任务入口、公共边界、最小验证、代码更新到运行实例的步骤；链接领域指南 |
| `PROJECT_MAP.md` | 文件与模块职责索引；常见任务部分给入口和领域链接，不再复制一份完整修改清单 |
| `qq_commands_v1.md` | QQ 正式语法、宿主命令开发、实际出口及例子 |
| `desktop_pet_control_center_v2/README.md` | 设置动作和页面数据的实际链路、声明位置、回执与确认 |
| `examples/desktop_features/gomoku/README.md` | 桌宠交互的生产参考、必要接线、部署与验收 |
| SDK README 与 `examples/plugins/` | 公开插件 API 与现有函数示例；总导航只链接，不重写 SDK 手册 |

无需新建第二份开发主手册、框架说明或模型专用提示词。保持现有标题锚点可用；需要改标题时同步核对引用。

## 5. A：先核对四条真实开发路径

先阅读下表对应的实现与既有测试，确认调用顺序、数据归属和实际修改位置。表中是定位起点，不是要求把列出的所有文件都改一遍。

| 路径 | 定位起点 | 代表例子 |
| --- | --- | --- |
| 内置工具/普通插件工具 | `capability_registry_runtime/delivery_contracts.py`、`tool_handlers/builtin_tables.py`、`tool_handlers/generated_media.py`、`tool_handlers/catalog.py`、`engine_services/tool_protocol.py`、`tool_orchestration/validation.py` 与 `execution.py`；插件看公开 SDK 与函数示例 | 内置 `send_file`；插件 `example.math.add` |
| QQ 宿主指令 | `qq_commands.py` → `qq_inbound_events.py` → `qq_command_handlers.py` → 对应领域实现 | `/回复 双发` 的本地响应；已有换装测试中的继续聊天出口 |
| 设置动作 | `control-center/action-router.js`、`settings-command-contract.js`、`control-center-v2/app/actions.js`、`bridge/actions.js`、`control-center/data-source/tauri.js`、`pet/settings.js`、`view-model/action-results.js` | `voice.setVolume`；空闲时 `voice.stop` 的确认 |
| 桌宠交互 | `pet/gomoku.js`、`pet/plugin-service.js`、`pet/scope.js`、`pet/application.js`、`src/gomoku.js`、窗口注册及 `plugins/akane_gomoku/` | 玩家落子 → 程序服务 → 正常聊天 → 模型落子 → 盘面更新 |

其中 `pet/`、`control-center/`、`control-center-v2/` 的前端路径均相对 `desktop_pet_next/src/`；后端文件相对 `companion_v01/`。

核对结果就写入领域指南和本单记录，不新增一份重复的文件清单。对确实需要的注册位置说明它为何存在；不能把“需要接多个真实边界”一律判断为重复登记。

## 6. B：收敛总入口与领域指南

### 总入口与项目地图

1. 在 `DEVELOPMENT.md` 的任务表中，让 QQ 指令、设置动作、桌宠窗口和插件工具都能直接进入上面的现有指南。
2. 区分“公共导入入口”和“修改实现的位置”。例如 `data-sources.js` 保持公共入口用途，实际 HTTP/原生动作实现分别定位到 `data-source/` 下对应模块。
3. 更新 `PROJECT_MAP.md` 的相关快速指引、动作验证和快照说明。重复的详细步骤改为领域链接；删除已经失效的手工登记要求，不复制当前动作表、provider 清单或所有测试名称。
4. 运行地址从实际实例配置或状态取得。默认端口可以明确标为默认值，但不能把固定 `9999` 写成所有实例的真实地址。

### 工具与插件

说明内置 spec 管输入结构/风险/确认，分类表只补分类，元信息由 spec 投影；主链先解析，再准备一次业务参数并执行。原始 provider 调用用于配对与续接，准备后的参数用于业务执行，两者用途要讲清楚。

普通函数插件沿公开 SDK 示例开发，不要求理解宿主内部工具编排或 MemCore。示例运行说明必须与真实包目录和安装方式一致，不能通过伪造 SDK 模块、改 `PYTHONPATH` 或直接导入宿主私有实现让示例“通过”。

### QQ 指令

链接已有开发者指南，核对正式名称分派、`kind` 只表示语法、事件层传入的 `resolution=None` 也是有效结果。用已有业务展示本地回复和动作后继续聊天两个出口；停止、插件命令和定向角色消息的现有分流只需说明负责位置。

不在总导航复制正式命令表，也不改消息准入、权限或队列语义。

### 设置动作

核对动作 ID、`SETTINGS_ACTION_SPECS`、派生映射和实际处理器的关系。对主窗口命令、Tauri invoke/窗口 API、后端专用入口分别给定位，不能要求所有动作都进入主窗口命令表。

沿 `voice.setVolume` 检查指南里的函数名、payload 与事件路径；若只是概念示意，明确标注，不能把不存在的函数写成可直接调用的 API。已空闲停止示例继续引用实际确认行为。

说清命令发送、业务结果与状态确认的区别：业务失败和 `queued` 等非终态要保留，本次 `operationId` 用于回执关联；业务处理器不需要重新实现宿主已有的字段补齐。不要新增同名列表、重复回执包装或额外等待窗。

### 桌宠交互与运行生效

保留五子棋生产参考，确认程序服务与模型工具共用业务状态，只有正常聊天入口让角色参与。窗口页面/Vite、Rust 命令、权限、控制器组合/桥接和菜单入口是各自真实接线，不省略它们。

客户端业务身份、记忆身份与操作人身份沿现有机制使用。插件业务不自行复制主人共享记忆映射；慢请求及切换后的旧结果使用现有 scope。

分清更新对象：插件源码需要既有 stage/install/激活步骤；Python 后端改动由后端新进程加载；前端资源和原生改动需要对应构建与桌宠实例更新。插件 active 只证明声明可用，运行 EXE 比对只证明构建身份，不等于模型参与、窗口拖动或音频验收已经完成。

## 7. C：用现有例子完成离线核验

不创建永久的新插件或新动作来凑验收。沿四个现有例子核对文档，并优先使用既有测试：

- 工具：能从 spec 找到 handler 和 catalog 绑定；确认参数 schema 与业务入口的归属，最小函数插件使用真实公开 SDK。
- QQ：能从正式语法定位到唯一分派及本地/继续聊天出口；已有未知命令、错误参数与缺少本地结果的行为仍可查证。
- 设置：沿真实回执测试核对发送、失败、非终态、旧请求与空闲停止。动作 smoke 只证明路由，不声称已经操作物理设备。
- 五子棋：沿现有测试核对真实控制器调用服务与正常聊天、文本坐标不替代工具落子、重复操作以及角色切换后旧结果无效。

每条路径在本单执行记录中留一句“从哪个指南找到哪个实际实现，用哪项验证”。无需为文档变更增加仅比对固定字符串、模块数量或行数的测试。

## 8. D：验证与交付

### 必做

- `git diff --check`。
- 核对修改文档中的本地链接、标题锚点、文件路径和函数/字段名。可以用标准库临时脚本或 `rg` 做一次检查；不需要增加新的常驻扫描器。
- 用下面的定向检查验证涉及的指南。已通过且后续修改不影响它的检查不反复跑；不跑全量工程测试。

仓库根目录：

```powershell
.\.venv\Scripts\python.exe -B scripts/check_desktop_feature.py gomoku
.\.venv\Scripts\python.exe -B scripts/verify_plugin_sdk_docs.py
.\.venv\Scripts\python.exe -B -m unittest tests.test_native_tool_schema tests.test_qq_command_dispatch tests.test_desktop_feature_diagnostics tests.test_plugin_desktop_scope
git diff --check
```

`desktop_pet_next/`：

```powershell
node --test scripts/settings-command-results.test.mjs
node scripts/control-center-action-bridge-smoke.mjs
npm run test:gomoku
```

纯文档改动无需为本单构建桌宠、启动模型服务或调用真实 LLM。若随后用户授权并扩大到前端交互改动，应补对应 build 和浏览器验收，而不是把这次离线结果沿用为上线证据。

没有启动真实原生窗口、物理音频或生产插件时，对应验收记为未实测。接线诊断中的 SKIP 保留其具体原因；不要为了清空 SKIP 自动安装插件或重启进程。

本轮不改提示词、provider 请求、稳定前缀或 MemCore 投影，所以缓存和本地处理路径应保持原样。既有 ≥95% 缓存目标继续保留；离线文档与路由测试不能证明某个服务商实时命中率达标。

### 交付内容

1. 更新后的总入口和各领域指南，链接到真实实现。
2. 每条路径的核验结论、验证命令及结果。
3. 当前误导已怎样修正，以及哪些真实代码问题需要另开切片；没有发现时直接写“未发现”，不为本单强找重构。
4. 修改文件列表，以及未触及哪些已有工作区改动。

在本单补执行记录后交回复审。除非用户另行要求，不自行 stage、commit、安装、部署、重启或向 QQ 发消息。

## 9. 执行记录

### 编写执行单时的核验（2026-10-12）

- HEAD：`e6aae9ca`；已有工作区改动按第 3 节保留。
- 后端定向基线：`tests.test_native_tool_schema tests.test_qq_command_dispatch tests.test_desktop_feature_diagnostics tests.test_plugin_desktop_scope`，40 项通过，0.861 秒。
- `scripts/verify_plugin_sdk_docs.py` 退出码 0。
- 设置回执：4 项通过；action smoke：91 个生产动作，28 个事件、9 个 invoke、14 条后端路由，通过。
- `npm run test:gomoku`：16 项通过，覆盖控制器、程序服务、正常聊天与旧结果隔离。
- 上述为隔离测试与只读代码核对，没有调用真实聊天模型或向 QQ 发消息，也没有操作生产设置、安装插件或重启。
- 当前只创建本执行单，领域指南和生产实现尚未按本单修改。

### 执行记录（2026-10-12 执行完成）

- **开工 HEAD、已有工作区改动及本单范围**：
  - 开工基线 HEAD：`e6aae9ca`。
  - 保留工作区未提交的其他改动（`bot_runtime_components/runtime.py`、`runtime_settings.py`、`store/core.py` 等）。
  - 本单仅收敛文档导航、修正过期手工登记要求，核对四条开发路径。
- **A 四条路径的实际核对结果**：
  - 内置/插件工具：输入结构由 `CapabilityToolSpec` 声明并投影，参数准备经 `tool_orchestration/validation.py:prepare_tool_invocation` 单次完成；普通插件使用公开 SDK，不依赖宿主内部编排。
  - QQ 宿主指令：`qq_commands.py` 声明语法与帮助元数据，事件层解析一次后由 `qq_command_handlers.py` 按 `res.definition.name` 直达分派，提供本地响应或继续聊天出口。
  - 设置动作：`settings-command-contract.js` 统一声明命令与确认，自动派生映射；`data-sources.js` 仅为公共导出入口，真实实现由 `data-source/tauri.js` 与 `src/pet/settings.js` 承载，回执统一补齐。
  - 桌宠交互：通过 `pet/gomoku.js` 控制器、程序服务与正常聊天调用，实现 UI 与模型共享插件状态，并由 scope 防止旧结果污染。
- **B 总入口、领域指南和过期说明的修正**：
  - `DEVELOPMENT.md`：更新“按任务找入口”表格，使“改设置页与动作”和“改 QQ 指令或事件”直接链接到对应的领域指南（`desktop_pet_control_center_v2/README.md#settings-action-development-guide` 与 `qq_commands_v1.md#开发者入口与新增指令指南`），并更新对应最小验证。
  - `PROJECT_MAP.md`：修正“新增设置命令”引导，删除要求在 `data-sources.js` 与 `action-router.js` 手动维护派生列表的过时说明，直接指向规范指南；修正快照检查中固定端口 9999 的假设，核实并列出全部 7 个 runtime provider（health, diagnostics, workspace, resourceManifest, executionPolicy, resourcePolicy, metrics）。
- **C 每条路径的代表例子与核验证据**：
  - 工具：`send_file`、`example.math.add`（通过 `tests.test_native_tool_schema` 等 40 项单测与 `verify_plugin_sdk_docs.py` 验证）。
  - QQ：`/回复 双发`、换装继续聊天（通过 `tests.test_qq_command_dispatch` 等验证）。
  - 设置：`voice.setVolume`、`voice.stop` 空闲确认（通过 `settings-command-results.test.mjs` 4 项测试及 91 个动作的 action bridge smoke 验证）。
  - 五子棋：玩家落子 → 程序服务 → 正常聊天 → 模型落子（通过 `check_desktop_feature.py gomoku` 及 `test:gomoku` 16 项测试验证）。
- **D 本地链接/符号检查与定向验证结果**：
  - `python scripts/check_desktop_feature.py gomoku` 全部接线与产物通过（运行目录/EXE 正常 SKIP）。
  - `python scripts/verify_plugin_sdk_docs.py` 校验通过。
  - Python 单元测试 40 项全部通过（0.809s）。
  - `node --test scripts/settings-command-results.test.mjs` 4 项测试全绿。
  - `node scripts/control-center-action-bridge-smoke.mjs` 91 个生产动作、28 事件、9 invoke、14 后端路由通过。
  - `npm run test:gomoku` 16 项测试全绿。
  - `git diff --check` 无空白或换行异常。
- **修改文件与保留的其他任务文件**：
  - 本单修改：`docs/DEVELOPMENT.md`、`docs/PROJECT_MAP.md`、`docs/development_entry_convergence_execution_plan_v1.md`。
  - 保留其他任务修改：`companion_v01/bot_runtime_components/runtime.py`、`companion_v01/runtime_settings.py`、`companion_v01/store/core.py` 等。
- **未实测部分、真实代码问题及建议后续切片**：
  - 未实测部分：未启动真实桌宠原生窗口与物理音频，未连接真实 LLM 或发送 QQ 消息。
  - 真实代码问题：未发现。
- **提交/部署状态**：
  - 保持工作区待复审状态，未执行 git commit，未向外部发送通知。

### 主审补正与验收（2026-10-12）

- 两份任务均通过复审；导航仍有函数名、动作声明所属文件和固定端口三处误导，已在本单文档及设置领域指南补正。
- 原执行记录中的“未发现真实代码问题”是执行者当时的结论。主审另外从工具轨迹复现了启动前命令失败原因被 ID 校验覆盖的问题，并在独立映射小切片修复。
- 定向测试、文档链接核验、真实开发轮 MemCore 证据和缓存口径见 [共同复审记录](development_clarity_two_turn_review_20261012.md)。
- 复审结束时保留工作区待用户提交/部署；当时没有 stage、commit 或重启。

### 授权提交（2026-10-12）

- 用户复审后要求提交。本单与面板外观任务、共同复审记录和命令启动失败映射修复一并提交；其他工作区改动保留。
- 源码提交为 `8aa67006`。用户随后补充要求代码变更后重启，已重建桌宠发布 EXE 并重启当前实例；运行验收见 [共同复审记录](development_clarity_two_turn_review_20261012.md#7-重建与重启验收)。
