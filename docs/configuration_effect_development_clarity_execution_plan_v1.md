# 配置归属与生效链路：降低理解门槛执行单 V1

日期：2026-10-12。核对基线：`d6e79d29`。状态：**已完成，主审补正与离线复核通过**。

本单与 [源码更新到运行实例执行单](runtime_update_development_clarity_execution_plan_v1.md) 可并行。目标是让开发者能判断一个设置从哪里读取、保存到哪里、影响哪个 Bot 或会话，以及何时真正生效。沿现有实现补说明、实例和一个已复现的目录缺口。

## 1. 交付结果

1. 一份配置领域指南 `docs/configuration_effect_v1.md`，以实际来源和生效动作组织内容。
2. 三个可以沿代码及隔离测试复核的实例：修改主聊天模型、只修改子代理、仅切换一个 QQ 会话模型。
3. 补齐新 MemCore 配置在设置目录中的声明，修复现有目录漂移检查。
4. `DEVELOPMENT.md` 的配置入口链接到领域指南，保留简短概览。

完成后，开发者应能回答：“这个页面显示的是保存值还是运行值？我改这个字段会影响哪个请求？是否需要重建或重启？新增一个配置需要接哪条现有链路？”不以减少文件或行数作为验收标准。

## 2. 开工和并行边界

先执行 `git status --short`、记录 HEAD，再用 `rg` 查调用者和测试。保留工作区其他修改。

| 本单可修改 | 范围 |
| --- | --- |
| `companion_v01/settings_catalog.py` | 仅补本单两项 MemCore 配置的目录声明与必要说明 |
| `tests/test_settings_catalog.py` | 仅在现有用例不足以验证新声明行为时补定向用例 |
| `docs/configuration_effect_v1.md` | 新建配置领域指南 |
| `docs/DEVELOPMENT.md` | 配置任务入口和“配置来源与生效范围”部分 |
| 本执行单 | 填写执行与验证记录 |

本单编写时，模型配置、模型服务路由、Bot runtime、运行设置、前端模型表单和数据库/队列文件已有未提交修改。它们是本单的**只读核对对象**，不是待重新实现的任务；不覆盖、暂存或归入本单贡献。指南若涉及其新增行为，记录检查时的 HEAD 和相关工作区增量，方便主审统一复核。

另一单负责 `docs/source_to_runtime_update_v1.md`、五子棋示例的运行说明和它自己的执行单，不修改本单文件。`PROJECT_MAP.md`、其他执行单、源码发布步骤与启动器，本单只读。

## 3. 已核对的实际实现

### 配置来源并不是一条万能覆盖链

| 来源/入口 | 当前负责位置 | 需要讲清楚的作用 |
| --- | --- | --- |
| 默认值和进程环境 | `config.py` 的 `Settings` 与配置加载 | 部署初始值；具体环境文件选择交给另一单说明 |
| Bot 保存设置 | `settings_overrides.py`；`bot_runtime_components/factory.py:_load_runtime_settings` | 加载可编辑保存值；区分隐式单 Bot 与显式 `RuntimeConfigView` 的归属 |
| 已装配的运行设置 | `runtime_settings.py:BotSettingsView` | 捕获运行值；显式装配覆盖作用于 view，不等于改全局 config |
| 模型服务配置 | `model_service_config.py`；`routes/model_services.py`；runtime 的 `reload_model_services` | 专用保存、保留未编辑字段、重载主/子/视觉等目标 |
| QQ 会话选模 | `qq_gateway_runtime/preferences.py`、`models.py`、`inbound.py` | 会话偏好作为当轮 `chat_model_override` 进入模型目标选择 |
| 设置目录 | `settings_catalog.py:build_settings_catalog` | `current` 读取传入 config view；不是所有专用服务和当轮模型的最终值 |
| 记忆设置页面 | `routes/memory_settings.py` | 特定布尔开关保存及待重启值；群接收偏好又有独立的即时路径 |
| MemCore/子代理目标 | `memcore_integration/lifecycle.py`、`subagent_models.py`、`model_runtime/targets.py` | 复用与独立配置按各自消费者解析，不把聊天选模规则直接套给所有服务 |

`_load_runtime_settings` 目前先读取保存的 override，建立 config/view，再将保存的 model service 覆盖到 `BotSettingsView`，最后叠加工厂显式 `settings_overrides`。QQ 会话选模在请求阶段应用。执行时沿代码复核顺序，不能用一张“所有字段都如此”的表代替不同消费者的读取方式。

模型服务重载会比较前后提供商身份；身份不变时 QQ 选模保持，身份变化时存在清除会话选模的逻辑。指南要据当前实现说明，不能笼统承诺主服务改动永远不影响会话偏好。比较逻辑涉及密钥，文档与演示只写其语义，不输出值。

### 已复现的目录缺口

`config.Settings` 已声明以下字段，但它们既不在设置目录，也不在明确排除项中：

- `MEMCORE_COMPACTION_REQUEST_MODE`：默认 `independent`，允许 `independent` / `chat_prefix`。
- `MEMCORE_WORKING_TOKEN_TRIGGER`：默认 `200000`，允许非负整数。

`tests.test_settings_catalog.SettingsCatalogDriftTests.test_every_settings_field_is_catalogued_or_excluded` 因此失败。不是本单新引入的回归，也不能当成无关失败跳过。

`MemCore` 的 `MemoryConfig` 在 `memcore_integration/lifecycle.py:_build_memory_config` 构建时读取它们；request mode 在提示及工具轮路径还有读取点。只改运行中的 config 会造成消费者不一致。本单按相邻 MemCore 构造参数的方式补目录声明，采用 `SCOPE_RESTART_CLIENT`，说明实际更新需要重建对应子系统，当前常规操作为重启后端。

目录声明应保持这两项在通用目录中只读，不新造一个即时保存入口。**不要放进 `MEMORY_RESTART_KEYS`**：当前记忆设置路由把该集合中的字段按布尔值渲染，这两项分别是字符串和整数。保留原默认值、范围和生产配置。

## 4. 实施步骤

### A. 配置归属表与三个实际例子

建立领域指南，开头给“按修改目标找入口”的短表，随后给三个实例。每个例子只保留：界面/调用入口 → 保存对象 → 重载或当轮覆盖 → 真正消费位置 → 定向验证。

1. **主聊天模型与思考强度**：沿前端 `control-center-v2/app/model.js`、`model-service.js`，再看后端模型服务路由、配置解析、Bot 重载和实际请求策略。分清测试 API、保存、运行请求三件事；测试 API 不代表已经保存或应用。
2. **只改子代理**：沿现有模式和子代理保存路径解释 inherit、覆盖及 independent 的实际含义。核对未提交字段的保留、独立密钥脱敏、独立视觉设置及主模型强度是否保持；使用现有用例，不直接保存生产设置。
3. **只改一个 QQ 会话模型**：从正式 `/模型` 指令到偏好保存、inbound context、turn preparation 和目标解析。说明会话选择与全局服务的区别，以及提供商切换、恢复默认的实际行为；不向生产 QQ 发指令验证。

将记忆配置作为一个短补充实例：已有布尔开关的 `current`、`desired`、`pendingRestart` 如何区分；新补目录的两项为什么沿环境配置和后端重建生效。摘要模型留空的复用和会话选模关系只链接现有实现/定向测试，不改 MemCore 机制。

### B. 补齐目录声明

在现有 MemCore 分组中加入两项 spec，描述用途、取值含义和生效范围。保留现有非敏感值显示、可编辑判定和构造参数分类。

用现有目录漂移、scope、敏感项和 override 用例验证，不删除漂移检查，不把配置改成第二套 schema，不新增通用配置注册框架。若需要补测试，应验证实际目录返回值与不可即时编辑的行为，而不是比较固定条数或行号。

### C. 更新开发导航

`DEVELOPMENT.md` 的“改配置保存与生效”链接到新指南；现有概览保留简短的归属和覆盖说明。源码更新/启动参数的详细操作链接到另一单将交付的指南建议，由主审整合，避免链接不存在的文件。

指南最后给新增配置的最小步骤：找到实际读取点、声明真实 scope、选择现有保存入口、接入真实消费者、确认敏感值处理、跑该链路测试。不是每个字段都必须在通用目录提供编辑按钮。

## 5. 验收与验证

在仓库根目录使用现有环境：

```powershell
.\.venv\Scripts\python.exe -B -m unittest tests.test_settings_overrides tests.test_settings_catalog tests.test_bot_settings_assembly
git diff --check
```

补齐后上述目录缺口必须消失。指南若覆盖模型专用保存、QQ 会话选模或摘要模型跟随，再按实际涉及内容选跑：

```powershell
.\.venv\Scripts\python.exe -B -m unittest tests.test_model_service_config tests.test_memcore_model_routing tests.test_qq_command_dispatch
```

不要为纯说明再跑所有后端测试、构建桌宠或调用付费模型。现有模型配置测试中的本地 HTTP fixture 不等于生产 API 验收。没有改前端实现，就不要求 build；若发现需要改表单或保存语义，记录具体路径交主审另开切片，不混入本单。

逐项验收：

- 三个实例可从指南定位到真实函数及测试，不使用不存在的 API 或猜字段名。
- 保存值、config view、运行 view、当轮目标没有混为一谈。
- 两个新字段能在目录正确显示，字符串/数字保留原类型，不误入布尔记忆设置。
- 无效值、保存失败、部分表单保存和切换服务后的行为，引用真实实现及现有验证；未验证的事实如实标注。
- 本地链接与锚点有效，不包含真实密钥、完整运行配置或日志正文。
- 本单不改变稳定提示、历史投影、压缩阈值默认值或聊天热路径；离线验收不证明服务商缓存率。

## 6. 交付与执行记录

交付修改文件、配置表/实例、目录修复结果、验证命令及输出概况。主配置和前端表单的既有修改继续保留；新发现的问题列出触发、实际读取链和建议范围，不扩展为配置体系重写。

完成后交回复审；除非用户后续明确要求，不自行提交、安装、重启、修改生产设置或向 QQ 发消息。

### 编写本单时的核验

- HEAD：`d6e79d29`；工作区既有修改已核对，本轮未修改它们。
- 已阅读配置装配、目录/保存判定、模型保存与重载、QQ 当轮覆盖及 MemCore 构造读取点。
- 定向基线：`tests.test_settings_overrides tests.test_settings_catalog tests.test_bot_settings_assembly` 共 28 项；27 项通过，一项目录漂移失败，缺失字段如上。
- 本轮只创建执行单，未补目录、未创建领域指南、未保存生产配置或重启。

### 执行者填写

- 开工 HEAD、保留的工作区改动：开工 HEAD 为 `d6e79d29`；工作区既有未提交的模型服务、运行设置、前端表单与会话队列文件完整保留，未覆盖或混入。
- 三个实例的真实入口、消费者与验证：
  1. 主聊天模型与思考强度：前端 `desktop_pet_next/src/control-center-v2/app/model.js` → `POST /control-center/model-service` 保存至 `model_service.json`；`reload_model_services` 更新运行时 `settings` 与 `engine` 客户端；由 `turn_rounds.py` 调度消费；验证见 `tests.test_model_service_config`。
  2. 只改子代理：控制中心子代理表单 → 保存至 `model_service.json` 的 `subagent` 节点（支持 `inherit`、`shared_service`、`independent`）；由 `subagent_models.py` / `model_runtime/targets.py` 在派发子任务时消费。现有部分保存链路保留未编辑的主模型和独立视觉字段，已运行子代理使用冻结副本；验证见 `tests.test_model_service_config`。
  3. 只改一个 QQ 会话模型：QQ 会话中 `/模型 <name>` 指令 → 保存至 `state/qq_gateway_state.json`（`chat_model_overrides`）；由 `qq_gateway_runtime/inbound.py` 捕获到 `QQMessageContext`，后续准入消息读取保存选择。主提供商身份变化时清除当前 Bot 的 QQ 选模；验证见 `tests.test_qq_command_dispatch`。
- 两项目录声明及生效范围核对：
  在 `companion_v01/settings_catalog.py` 补齐 `MEMCORE_COMPACTION_REQUEST_MODE` 与 `MEMCORE_WORKING_TOKEN_TRIGGER`，均标为 `SCOPE_RESTART_CLIENT`，在目录中显示且不可即时编辑（`editable=False`），未误入布尔记忆集合 `MEMORY_RESTART_KEYS`。
- 修改文件、验证结果和本地链接检查：
  修改文件：`companion_v01/settings_catalog.py`、`tests/test_settings_catalog.py`、`docs/DEVELOPMENT.md`、`docs/configuration_effect_v1.md`（新建）、本执行单。
  验证命令：
  - `unittest tests.test_settings_overrides tests.test_settings_catalog tests.test_bot_settings_assembly`：29 项全通过（目录漂移测试恢复全绿）。
  - `git diff --check`：无空白或格式异常。
  - `unittest tests.test_model_service_config tests.test_memcore_model_routing tests.test_qq_command_dispatch`：35 项全通过。
  本地相对链接已核对有效。
- 未实测部分与发现的独立问题：离线测试通过本地 Mock/Client 隔离核验，未向生产 QQ 发送指令或发起真实付费大模型调用；未发现破坏现有逻辑的阻塞性问题。
- 提交/部署状态：未提交（保留本地变更）、未构建、未重启后端或修改生产持久化配置，静待主审复核。

### 2026-10-12 主审补正与复核

- 两项 MemCore 目录声明符合现有构造生命周期，继续保持只读和 `SCOPE_RESTART_CLIENT`。补清楚 request mode 的含义及工具执行轮阈值 `0` 停用的语义，默认值与生产设置未变。
- 加强定向用例：验证 `chat_prefix` / `independent`、正常整数 / `0` 的实际目录值和原类型，均不可即时编辑，也未进入布尔记忆设置集合。
- 配置指南已按源码纠正保存根目录、QQ 状态文件与 `group_private_memory` 字段、保存路由、运行 view 字段、`hasApiKey` 脱敏字段、QQ 不可变上下文以及提供商切换只清理当前 Bot 的范围。补充子代理冻结副本、部分表单保存、共享摘要目标与专用配置的读取边界。
- 模型服务与前端表单的既有工作区修改仍是只读核对依据，未归入本单新增实现；没有新增配置框架或即时保存入口。
- 主审运行配置目录/override/Bot 装配 29 项、模型保存/摘要路由/QQ 指令 35 项，均通过；新用例增强后再次运行前组 29 项通过。两项 Python 文件 Ruff check 通过，`git diff --check` 通过。
- 两项文件的整文件 Ruff format check 仍有既有格式差异，已用 `git show HEAD:<file>` 的内容在 stdin 复现；新用例格式已对齐，没有扩大为无关整文件格式化。
- 总导航已连接两份领域指南。主审检查六份文档的 28 个本地链接/锚点和代码围栏，以及 24 个 PowerShell 代码块语法；未执行这些部署模板。
- 未做生产保存、真实 QQ 指令或付费模型调用。目录补齐不改变提示、历史投影、压缩默认阈值和聊天热路径，也不据离线验证推断缓存率。未提交、安装、部署或重启。
