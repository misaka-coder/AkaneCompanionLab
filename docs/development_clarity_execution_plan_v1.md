# 降低开发理解门槛：执行单 V1

日期：2026-10-11。调研基线：`4a2790e5`（`refactor(runtime): simplify public entries and settings assembly`）。

状态：**A～D 已完成。** 最终复验修复了 B 参数准备中的商店元数据泄漏，并重跑相关测试；当前规则以仓库 `AGENTS.md` 和用户当时的要求为准。

主模型复核已补正：电脑操作重复校验、原生适配器同名业务字段丢失、诊断对封装调用的误报；旧 schema 导出改为引用现有 ToolSpec，退出重复维护。详情见末尾复核记录。

## 1. 本轮要交付什么

让开发者修改一个工具、接一个插件服务或排查桌宠功能时，可以直接找到负责该行为的代码，并确认自己的修改已经进入实际调用链。

按 A → B → C → D 执行，每项验证后再进入下一项。A 是较小的定义整理，B 是本轮主要的调用链调整，C 改善开发反馈，D 把最终实现接回现有开发入口。四项都属于本轮范围。

验收看四件事：

1. 同一个参数结构、风险声明等语义只有一个维护位置。
2. 一次实际工具调用的业务参数由一个阶段准备，准备后的参数就是执行收到的参数。
3. 诊断能指出漏接的位置，也明确哪些运行状态还没有核实。
4. 常见功能修改不要求开发者理解无关的记忆、客户端身份映射和安装器内部状态。

代码行数、文件数可以记录，但不作为成功标准。保留有独立职责的模块；把重复决定、无效定义和不明确的转手清理掉。

## 2. 已检查的区域与取舍

| 区域 | 已确认的现状 | 本轮处理 |
|---|---|---|
| 内置工具定义 | `builtin_tables.py` 先维护一份 `ToolMetadata`，随后又用 `ToolSpec` 覆盖风险、参数结构和确认声明；同名表被赋值两次 | A：明确声明来源，删掉会被覆盖的重复字段 |
| 工具参数准备 | 收集、执行入口和校验都可能调用 `normalize_call`；字典与 `ToolInvocation` 在途中来回转换 | B：分清协议解析和业务参数准备，实际执行复用准备结果 |
| 商店工具 | `CharacterShopToolHandler.tool_metadata()` 传一个参数，辅助函数当前需要两个，读写工具均复现 `TypeError` | A：一并统一辅助函数与调用方，补真实处理器验收 |
| 桌宠接线 | 已有 `plugin-service.js`、`scope.js`、`docked.rs`；现有诊断检查页面、Vite、Rust 和窗口权限，尚不检查主窗口控制器的组合/注册 | C：补开发接线检查，继续使用已有助手 |
| 源码与运行版本 | 插件运行安装后的版本；前端 build、Tauri EXE 和实例运行副本各有步骤。当前插件目录检查只能证明状态/贡献项 | C、D：说清证据范围和更新步骤，避免把 active 或相同版本号当作最新源码证明 |
| 配置装配 | 上一轮已集中到 `_load_runtime_settings`；设置目录已有 current、desired、pendingRestart、managedIn、scope | D：用实际优先级解释修改入口，复用已有字段；这轮不重写配置装配 |

上一轮已完成的公共入口整理和保存配置读取合并继续使用。不要重新做一遍，也不要把独立的 MemCore、CapCore、OneBot 包并入宿主。

## 3. 开工与基线

先执行 `git status --short`，保留已有改动和未跟踪素材。用 `rg` 定位调用方，Windows 上用目录加 `-g '*.py'` 筛选，不把通配符写成不存在的目录路径。

使用仓库 `.venv/Scripts/python.exe` 做后端验证。文中的命令均从仓库根执行，注明 `desktop_pet_next` 的除外。

本次只读调研已跑过：

```powershell
.\.venv\Scripts\python.exe -B -m unittest tests.test_tool_orchestration_contract tests.test_tool_invocation tests.test_desktop_feature_diagnostics tests.test_bot_settings_assembly
.\.venv\Scripts\python.exe -B scripts/check_desktop_feature.py gomoku
```

第一条共 53 项测试，出现 2 个子用例失败和 1 项错误：

- `LegacyAdmissionTests.test_malformed_wire_arguments_do_not_probe_tool_handlers`：demo、computer_use 两个子用例失败。已有解析错误的调用仍会先查找 handler，B 应让协议错误在解析阶段直接得到配对失败结果。
- `ExecutionAdmissionTests.test_legacy_execution_does_not_drop_handler_loss_feedback`：测试用查找次数模拟 handler 消失；实际路径没有按假定次数消失，进入未配置返回值的 Mock broker，随后报 `Mock is not JSON serializable`。先核实夹具，不把它当成已证实的生产序列化故障。

独立运行 `tests.test_tool_orchestration_contract` 也复现上述失败，因此并非仅在这组联合测试中出现。这些是改动前的结果。不要删测试或新增吞异常逻辑来掩盖它们；B 按真实可用性变化改造夹具，保留“未执行、同一个调用 ID、失败结果可见”的断言。

第二条的源码/本机构建检查通过。运行插件和运行 EXE 为 SKIP，因为没有传后端地址和 `--running`；不代表完成了原生交互或模型对弈验收。

另已用本地假工具核对：普通收集 → 执行路径的 handler 规范化发生 3 次；冻结展示契约 → 当前实现路径发生 2 次。这是代表性路径结果，不是所有工具的统一次数。

## 4. A：工具定义只有一处维护

### 入口与范围

- `companion_v01/tool_handlers/builtin_tables.py`
- `companion_v01/tool_handlers/core.py`：只调整受影响的内部引用/说明，保留公开 `ToolMetadata` 和 `BaseToolHandler` 接口
- `companion_v01/tool_handlers/character_shop.py`
- 相关测试：`tests/test_native_tool_schema.py`、`tests/test_tool_runtime.py`、`tests/test_service_character_shop.py`

### 目标实现

1. `TOOL_SPEC_BY_TYPE` 继续指向现有 `ToolSpec`；参数结构、risk、confirm 从这份定义派生。
2. 原来的手写元信息表只保留补充分类信息：family、operation、default_round_budget、background、aliases。给这个内部表一个与派生结果不同的名字，例如 `_BUILTIN_TOOL_CLASSIFICATION`。使用简单映射即可，无需增加一个注册框架。
3. `TOOL_METADATA_BY_TYPE` 只在一处生成，不再先手写同名表然后覆盖。补充表不重复存 input_schema、risk、requires_confirmation。
4. 统一内部辅助函数为 `_project_tool_metadata_from_spec(spec, *, classification=None)`。默认按 `spec.capability_id` 查内置补充分类；调用方有独立分类时显式传入。返回现有 `ToolMetadata`。
5. 更新辅助函数的全部调用方。商店读写处理器都应能返回真实元信息；读写分类按业务职责明确给出，参数/risk/confirm 仍取商店自己的 spec。

`default_round_budget` 当前属于描述性元信息，实际工具轮上限由 `TOOL_ROUND_HARD_LIMIT` 决定，默认 0。此次保留现有对外值，不能把分类表顺手改成一套新限额。

### 验收

- 用真实 `CharacterShopToolHandler` 分别检查 reader/writer 元信息，不再报 TypeError；读写分类准确，schema 与对应 spec 一致。
- 对代表性的只读、写入和执行工具核对：原生 schema、risk、确认语义和可见性没有被补充表改变。
- 修改一份 spec 后，元信息和原生 schema 都反映同一份定义，不需要改第二张重复表。
- 工具 ID、公开导出和既有参数默认值保持当前约定。

```powershell
.\.venv\Scripts\python.exe -B -m unittest tests.test_native_tool_schema tests.test_tool_runtime tests.test_service_character_shop
git diff --check
```

## 5. B：工具调用先解析，再准备一次业务参数

这是本轮主要切片。先走通一个普通内置工具，再覆盖冻结契约、适配器和桌面工具。各来源使用同一准备逻辑，不能给每种来源各造一条新链。

### 入口与范围

- `companion_v01/engine_services/tool_protocol.py`：`_prepare_tool_round_decisions`
- `companion_v01/tool_orchestration/normalization.py`
- `companion_v01/tool_orchestration/validation.py`
- `companion_v01/tool_orchestration/execution.py`
- `companion_v01/tool_orchestration_engine.py`：必要的入口导出
- `companion_v01/tool_invocation.py`、`capability_contracts.py`、`tool_scheduling.py`：只有实际数据传递需要时小幅调整
- `tool_handlers/adapters.py` 与 `execution_runtime/run.py`：核对参数入口和执行准入，避免重复准备；不重写业务处理器

### 各阶段负责什么

| 阶段 | 职责 | 传给下一步的内容 |
|---|---|---|
| 模型调用解析 | 确认调用载体、工具名、ID、来源与 JSON 解析状态；保留 provider 原始调用信息 | 可配对的原始调用 |
| 执行准备 | 绑定本次当前实现，按实际 spec 剥离宿主调度字段，规范化业务参数并产生校验诊断 | 当前 handler、准备后的 invocation 或明确失败 |
| 权限/任务准入 | 使用当前操作人、目标、权限与取消状态；决定前台或后台执行 | 已准入的调用及必要的权限结果 |
| 执行与结果记录 | 使用同一个 handler 和已准备参数，记录真实结果及原调用 ID | 配对结果、现有任务/历史/交付状态 |

目标数据流：

```text
模型原始调用
  → 解析与保留调用 ID
  → 绑定当前实现
  → 剥离宿主调度字段、准备业务参数一次
  → 准入
  → 执行准备后的参数
  → 返回原调用 ID 对应的结果
```

### 实现步骤

1. 给主链路一个明确的协议解析入口，例如 `parse_tool_call`。它不调用业务 handler 的 `normalize_call`。`_prepare_tool_round_decisions` 使用这个入口，保留现有批处理、Hook、记录所需的字典格式。
2. 已有 parse_error 时先形成诊断，不能为了规范化无效 JSON 而查找/绑定业务 handler。未知工具名仍保留为一次真实调用，随后返回失败结果。
3. 在执行准备阶段集中绑定当前 handler；冻结快照负责展示，当前实现负责执行。把这个 handler 和准备后的 invocation 作为局部结果传下去，避免后续重新选择另一份实现。
4. 业务 `normalize_call` 成功后，把返回参数放进用于执行的 `ToolInvocation.arguments`。失败时保存本次诊断需要的信息，校验不要再调用一遍 normalize；`normalized_candidate` 不能只用于判断真假后被丢弃。
5. 准备后的调用继续进入现有权限、Host Job、Broker 和 dispatch。适配器的 `admit_execution` 仍负责其真实 CapCore 准入，host 准备与 CapCore 权限/契约处理分清职责，不以“只处理一次”为由删掉它。
6. 独立调用 `validate_tool_invocation` / `validate_legacy_tool_call` 时，复用同一准备逻辑并只返回诊断。实际执行直接消费准备结果，不再先做一遍独立校验、然后再准备一遍。
7. 现有 `normalize_tool_call` 的显式兼容用途可保留，继续满足需要返回规范化字典的调用方；主链路从其重复调用中退出。注释说明用途，不为兼容入口扩展新逻辑。
8. 只有有实际协议差异的边界才转换字典与 `ToolInvocation`。不要求重写整个批处理、Hook 或历史模块来追求统一类型。

可以在现有编排模块内用少量函数表达准备结果。不要增加跨轮“已校验”缓存、持久标记或从模型参数读取的跳过校验开关；本次局部准备结果足够。

### 需要保留的具体行为

- 调用 ID 与结果配对；未知名、解析错误、参数错误均返回可见失败，不能被当成没有调用工具。
- provider 原始名字/参数、别名映射及当前工具轮的 reasoning 等重放信息按现有边界保留。原始 wire 调用和准备后的业务调用各有用途；不能为了减少表示把原始参数覆盖成规范化参数。
- 尤其覆盖适配器业务参数名为 `type` 或 `arguments` 的情况：运输层字段和原始业务字段不能互相吞掉。
- 宿主 background 参数不进入普通工具业务参数；工具本身有 background 字段时，使用现有 host_background 避让规则。
- `DIRECT_TASK_TOOLS` 继续使用自己的生命周期；exec_run 等不能再被套成第二个 Host Job。
- 绑定后撤销、插件不可用、worker 重启和取消仍在真正 dispatch 前得到处理。必要的可用性检查与重复参数准备是两回事。
- 参数准备的 handler 与实际执行的 handler 一致；保留现有 invocation retain/release 和权限操作人归属。
- 普通与流式、QQ 与桌宠使用同一核心链；工具完成补入、查询消费和最终交付行为延续现状。
- stable tools、系统前缀、能力快照和 MemCore 历史接口的语义保持现状，不追加本轮无关的模型提示词。

### 必须用行为证明的场景

| 场景 | 验收重点 |
|---|---|
| 原生/兼容的普通假工具 | 一次实际调用只做一次业务规范化；带默认值/修整的参数就是 execute 收到的参数 |
| 冻结展示契约后实现更新 | 采用当前实现准备并执行，调用 ID 与 provider 重放信息保留 |
| 缺失参数、非法 JSON、未知工具 | 无副作用，有完整配对失败结果和可继续处理的诊断 |
| 适配器含同名业务字段 | type、arguments、background 等业务键完整，宿主字段不泄漏 |
| 前台/后台与长任务 | 仍只启动一次任务，返回/查询使用同一个 ID；没有第二层任务包装 |
| 绑定后失效/取消 | 未执行且理由可见，不把旧 handler 的规范化结果交给新 handler |
| 并行批次与工具完成边界 | 结果配对、消费及不重复补入行为延续现状 |
| OpenAI/Anthropic、普通/流式请求 | 工具声明与稳定前缀不因准备方式调整而变化 |

优先扩展既有测试。计数用于证明重复处理已退出，参数到达执行、失败未产生副作用及结果配对才是核心断言。handler 消失的测试用真实可用性开关或当前绑定对象模拟，不再依赖“第 N 次查询正好消失”。

```powershell
.\.venv\Scripts\python.exe -B -m unittest tests.test_tool_invocation tests.test_tool_orchestration_contract tests.test_tool_call_rejection tests.test_native_tool_schema
.\.venv\Scripts\python.exe -B -m unittest tests.test_tool_foreground_execution tests.test_tool_batch tests.test_tool_completion_boundary tests.test_tool_continuation
.\.venv\Scripts\python.exe -B -m unittest tests.test_tool_exposure_wire tests.test_tool_exposure_mcp tests.test_tool_execution_modules
git diff --check
```

先运行正在修改的相关用例，再在 B 完成时运行这组验证一次。失败后针对原因重跑，不要求每次编辑都跑整组。

## 6. C：让开发接线诊断指出具体缺口

### 入口与范围

- `scripts/check_desktop_feature.py`
- `tests/test_desktop_feature_diagnostics.py`
- 生产参照：`desktop_pet_next/src/pet/application.js`、`src/pet/gomoku.js`、`src/pet/ui.js`、`src/pet/panel.js`
- 生命周期参照：`companion_v01/extension_management_runtime/inventory.py`、`installation.py`；本轮不改安装器

### 实现步骤

1. 现有脚本继续负责只读诊断。保留页面、Vite、Rust、窗口权限、本机构建、运行 EXE 和插件目录检查。
2. 在现有约定布局下，补主窗口控制器引用和组合入口检查：`src/pet/<label>.js` 是否存在、在 application 中是否导入/组合、是否注册桥接；提示缺哪个文件/调用。
3. 对有交互窗口的约定实例检查请求与响应事件名是否在主/子窗口两侧存在；再检查已有 UI/面板中有打开窗口命令的引用。检查名写成“源码引用/接线”，不能把静态引用当作事件已经执行。
4. 用 gomoku 验证，保留其他页面不一定有同名控制器的事实。非约定布局给出明确 SKIP 和手工定位提示，不新增“所有窗口必须按这种结构开发”的限制，也不需要一份新功能注册清单。
5. `catalog_checks` 输出只证明插件 active、对应工具/程序服务已声明。明确提示“源码内容是否与已安装版本一致尚未核实”；版本号、generation、工具名相同都不足以证明代码相同。
6. 运行 EXE 的哈希比对保留；缺少 running/backend 检查时继续 SKIP。更新指引按缺口区分：前端 build、Tauri 构建、运行副本更新、插件源码 stage/install，不能全部归为重启后端。

本轮不增加源码 watcher、自动重装插件、自动重启、新的指纹持久化机制或功能生成器。代码内容的部署证据使用现有安装返回的 digest、激活状态以及 EXE 比对记录；这份只读诊断不负责执行部署。

### 验收

- gomoku 的正常引用检查通过。
- 临时夹具分别删掉主窗口导入/组合、桥接注册、事件一侧或打开入口，诊断指出对应缺口；把文字放在注释中不能作为已接线证明。
- 非约定布局不被误报成禁止开发；结论区分 FAIL、WARN、SKIP。
- 后端不可达、插件 inactive、旧贡献项、旧 EXE 的原因保留；active 且相同版本号也不会被宣称与源码内容一致。
- 脚本运行不发模型请求、不下棋、不安装、不修改运行目录。源码扫描只发生在显式运行诊断时，不进入聊天请求热路径。

```powershell
.\.venv\Scripts\python.exe -B -m unittest tests.test_desktop_feature_diagnostics
.\.venv\Scripts\python.exe -B scripts/check_desktop_feature.py gomoku
git diff --check
```

如修改了前端生产接线，再执行 `npm run test:gomoku` 与 `npm run build`（在 `desktop_pet_next`）。需要原生窗口验收时构建 Tauri 并记录实测；仅改诊断脚本不要求重建 EXE。

## 7. D：把最终实现接回开发入口

更新现有 `docs/DEVELOPMENT.md`、`docs/PROJECT_MAP.md`，按需要更新 `examples/desktop_features/gomoku/README.md` 与 `akane_plugin/README.md`。具体文件以真实改动为准，不复制一份新 SDK 手册。

让入口能够回答以下任务，每个给出负责的文件/函数、最小修改范围、最小验证和生效动作：

| 开发任务 | 文档需要说清楚 |
|---|---|
| 改内置工具参数 | spec 声明与 handler 业务规则各改什么；分类投影自动更新；B 的参数准备位置 |
| 写一个普通插件工具 | 从已有函数工具示例开始；只需公开 SDK，不导入宿主私有实现 |
| UI 与模型共用业务状态 | 页面调用程序服务，模型调用工具，复用同一业务实现；程序服务自身不会唤醒模型 |
| 给桌宠加交互 | 页面/控制器/插件各负责什么；使用已有服务客户端和正常 chat.sendMessage；Tauri/Vite 注册及权限仍按实际需要接入 |
| 改了代码但运行没变化 | 插件源码 → stage/install → active；前端源码 → Vite/Tauri 构建 → 实例运行产物；后台重启与桌宠更新分别处理 |
| 改某项配置 | 默认/启动配置、Bot 保存值、模型服务配置、工厂显式覆盖和 QQ 当轮模型选择的归属及优先级 |

配置说明以 `_load_runtime_settings`、`BotSettingsView` 和 QQ 当轮 `chat_model_override` 的真实链路为准。`settings-catalog.current` 是当前 config view 的值，不能把它写成所有专用服务和当轮模型选择的最终值。复用 current/desired/pendingRestart/managedIn/scope，不另造配置来源追踪系统；无法区分环境与默认值时写“启动配置”，不要猜。

保留三种身份的简短说明：客户端业务身份、记忆存储身份、操作人身份。插件业务状态使用已有 `ctx.invocation`，前端复用 backend/service/scope；正常插件开发不用自行实现主人共享记忆映射。

移除本次改动涉及的过期说明和纯内部绕路引用。对外稳定入口继续使用；静态 Mixin 组合、隔离安装、权限边界等有职责的部分不因名字带“兼容”就删除。

SDK 示例使用当前真实类型。文档内不加入新的系统提示词或开发时才需要的普通聊天注入。

验收：按“修改工具参数”和“接 UI 程序服务”各从入口走一遍，能找到当前生产实现与验证命令，不需要到历史里程碑文档猜最新做法。

```powershell
# 修改 SDK 文档/示例时
.\.venv\Scripts\python.exe -B scripts/verify_plugin_sdk_docs.py
git diff --check
```

## 8. 完成交付

最终报告至少包含：

- A～D 各完成了什么、开发者少维护了哪份信息或少追踪了哪个阶段。
- 代表工具的准备次数、execute 实际收到的参数、失败/失效场景的结果。
- 已有 53 项基线测试中的问题如何处理，修改后的相关测试结果。
- 稳定工具声明/前缀、普通/流式链和既有任务行为的验证结果。缓存实测可后续由用户进行，不能只凭本地 wire 测试宣称线上达到 98% 或 99%。
- 诊断能核实什么，哪些源代码内容、运行版本、原生交互尚未实测。
- 变更文件、必要的构建/安装动作，以及是否已应用到运行环境。

保持相关范围的测试和 `git diff --check` 通过。不要把本轮扩展成 MemCore 重构、提示词清理、全项目命名迁移或新的开发框架；查到其他问题可以记录为后续事项。

发现代码与执行单不同，先核对调用链并在本文件执行记录中说明调整理由。保留目标和行为验收，允许合理的局部实现调整；不用为了照抄文件名单而制造冗余代码。

这份执行单的默认交付是实现、相关验证和变更报告。commit、运行部署和重启按用户届时的要求进行。

### 执行记录（接续模型更新）

| 切片 | 状态 | 实际变更与验证 |
|---|---|---|
| A 工具定义 | 已完成 | `builtin_tables.py` 改为单一 spec 派生元信息，分类表只保留补充分类；商店读写元信息显式分类。新增 spec 投影和真实商店处理器验收。`tests.test_native_tool_schema tests.test_tool_runtime tests.test_service_character_shop`：105 项通过；`git diff --check` 通过。 |
| B 参数准备 | 已完成 | 增加 parse-only `parse_tool_call`，模型调用解析不再触发 handler 归一化；执行准备绑定当前 handler、准备一次参数并把同一 `PreparedToolInvocation` 交给校验与 dispatch。保留显式兼容 `normalize_tool_call`、CapCore admission 和 provider 原始 wire 信息。补主链路调用 ID/参数到达 execute 断言；更新冻结 selection 与调度 schema 测试。原 53 项基线中的非法 JSON 两个子用例已通过；handler 消失测试改为真实绑定撤销夹具并保留配对失败结果。B 验收四组：58、61、38 项通过，能力适配器/MCP/桌宠能力 44 项通过，native web/tool schema 与 capability fabric 79 项通过。最终复验发现 `CharacterShopToolHandler.normalize_call()` 收到 `_tool_*` 传输元数据会拒绝商店调用；准备阶段现保留原始调用侧车，仅将剥离 `_tool_*` 的业务字典交给 handler 与失败诊断。修复后主链路和商店目标用例通过，A 组 105 项及 B 四组 58/61/38 项全部通过；另复验适配器/桌宠 33 项、native web/capability fabric 79 项通过。`test_final_json_repair` 两项失败在未修改的基线提交 `4a2790e5` 上同样复现（39 项中的 2 项），不是本轮新回归。 |
| C 开发诊断 | 已完成 | `check_desktop_feature.py` 增加 conventional controller 导入/组合/注册、两侧请求/响应事件以及 UI/面板打开命令检查；扫描前去除 JS 注释。非同名结构明确 SKIP 并给手工定位提示。active 插件目录输出 active/贡献声明与源码身份的核验边界；未加载项指向 stage/install/激活，EXE 哈希差异指向部署本机产物并重启桌宠。测试覆盖缺导入、组合、注册、事件断线、打开入口、注释误判和非约定布局。`tests.test_desktop_feature_diagnostics`：13 项通过；gomoku 源码/构建诊断通过，插件目录和运行 EXE 按缺少地址/`--running` 如实 SKIP；workshop 同名控制器检查 SKIP。原生拖动/关闭未做运行验收。 |
| D 开发入口 | 已完成 | 更新 `DEVELOPMENT.md` 与 `PROJECT_MAP.md`：内置工具 spec/handler/准备链、普通 SDK 插件、UI 与模型共享程序服务、插件/前端/Tauri/运行 EXE 更新路径、配置覆盖来源与 QQ 当轮模型选择都有直接入口和最小验证。补 Gomoku 参考的诊断范围与 SKIP/运行版本边界，并改用仓库 `.venv` 命令。`scripts/verify_plugin_sdk_docs.py` 通过；最终 `git diff --check` 通过（Git 对既有 CRLF 文件给出换行规范化警告）。未提交、未安装/部署、未重启。 |

### 主模型复核记录（2026-10-11）

发现并修复三个实际问题：

- `computer_use` 在准备阶段调用 `normalize_arguments`，准入阶段又调用同一校验器；已有内部错误测试复现调用两次。准备阶段恢复不改写参数，准入给出一次结构化诊断；合法参数与内部异常均确认只校验一次。
- 清理 `_tool_*` 时，原生 Adapter/MCP/插件调用失去用于区分业务字段的原始参数。业务字段为 `type`、`arguments` 时，调用会被错误拆解。新增真实适配器执行测试先复现四个成功场景失败，再修复为将剥离宿主调度字段后的原始业务值放入既有 `arguments` 封套。覆盖普通/冻结绑定、MCP/插件、合法/未知字段；调用 ID、原始重放参数和业务 `background` 均保留，宿主 `host_background` 不进入业务执行。
- 新诊断把当前 `panel` 的 `registerPanelBridge`、UI 调控制器打开方法和变量发送事件误报为断线。增加已有形式的识别；无法从字面量确认的事件明确 SKIP 并提示沿助手核对。非约定布局不再仅因有页面打开入口就被强制套入同名控制器结构。

同时将 `builtin_tables.py` 中八份旧 schema 导出改为现有 ToolSpec 的别名，保持导出名称，并删除第二份手写参数结构；开发入口补充原生适配器封套和电脑操作单次诊断的边界。

复核验证：

| 范围 | 结果 |
|---|---|
| 工具 schema/运行时/商店/调用/拒绝/前台后台/公开通道/MCP/适配器/桌面本地能力/电脑操作诊断 | 238 项通过；删除旧 schema 定义后另复验 schema/运行时/商店/电脑诊断 115 项通过 |
| 原生网页调用、能力选择、桌面凭据续期、电脑操作与权限/投影/工作流/批次 | 107 项通过 |
| 批次、工具完成边界、连续工具轮、公开通道与执行模块 | 99 项通过（其中前台/公开通道已在补正后再次覆盖） |
| 桌面功能诊断 | 15 项通过；实际 `gomoku` 接线通过，`panel` 封装事件明确 SKIP |
| SDK 文档与差异检查 | SDK 文档检查通过，`git diff --check` 通过 |
| 既有桌宠长任务中断修复 | Python 26 项与 JS 8 项通过；独立提交 `343ebac9` |

只读运行核验确认 `akane.gomoku` active，工具及 `board.v1.control` 贡献已声明；运行桌宠 EXE 与本机发布产物哈希一致。并行任务仍修改桌宠源码，因此发布产物时效检查为 WARN。未比对插件安装源码内容，未重做原生拖动/关闭或真实模型对弈。执行模型记录的 `test_final_json_repair` 两项基线失败仍作为独立事项保留。

本次复核不部署或重启。此前桌宠长任务中断修复已经构建、部署并重启；本次新增补正由后端下次重启加载。其他正在执行的 QQ 指令、设置动作工作与个人素材不纳入本次提交。

下一步先验收现有两张并行执行单，再用真实开发任务检验是否能沿当前入口独立完成接线；目前不扩展第四张泛化整理单。
