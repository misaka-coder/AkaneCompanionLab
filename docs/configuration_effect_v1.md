# 配置归属与生效链路指南 V1

本文档帮助开发者明确 Akane 系统中配置的完整生命周期：设置从哪里读取、保存到哪里、影响哪个 Bot 或会话，以及何时真正生效。

按 `d6e79d29` 与执行前已有的模型配置工作区增量核对；这些既有增量不属于本单新增实现。下面的 `<BotDataRoot>` 指目标 Bot 的 `runtime_layout.data_root`。模型配置和通用保存设置由 `runtime_layout.config_dir` 定位，当前为 `<BotDataRoot>/users_data/_local/`；QQ 偏好由 `runtime_layout.state_dir` 定位，当前为 `<BotDataRoot>/state/`。它们不是仓库中的 `data/` 文件。

---

## 1. 按修改目标找入口

系统中的配置并不是单一扁平的覆盖链，不同的业务目标对应不同的入口、持久化介质和生效机制：

| 修改目标 | 用户/调用入口 | 保存位置 | 生效动作 | 真正消费位置 |
| --- | --- | --- | --- | --- |
| **主聊天模型与思考强度** | 控制中心模型服务页（`POST /control-center/model-service`） | `users_data/_local/model_service.json` | 保存后调用 `reload_model_services`，后续请求使用重载后的设置 | `model_runtime/targets.py`、`request_policy.py`；聊天由 `engine_services/turn_rounds.py` 调度 |
| **子代理专用模型** | 控制中心模型服务页子代理表单 | 同一 `model_service.json` 的 `subagent` 节点 | 重载后，新派发使用新配置；已运行子代理持有自己的冻结副本 | `subagent_models.py:resolve_subagent_route`、`freeze_subagent_llm` |
| **单个 QQ 会话模型** | QQ 会话内 `/模型 <model_name>` 指令 | `state/qq_gateway_state.json` 的 `chat_model_overrides` | 后续消息准入时读取；偏好持续保留到切换或恢复默认 | `channels/qq/inbound/context_builder.py` → `turn_preparation` → 模型目标解析 |
| **通用运行开关** | 设置目录（`POST /control-center/settings-catalog/{key}`，正文为 `{"value": ...}`） | `users_data/_local/settings_overrides.json` | 可编辑 runtime 项更新 config view；现有可保存 restart 项等待后端重建 | 各设置的真实消费者，不能假定所有 `BotSettingsView` 自动更新 |
| **群记忆接收策略** | 记忆设置页群开关（`POST /control-center/memory-settings/groups/{group_id}`） | `state/qq_gateway_state.json` 的 `group_private_memory` | 保存成功后更新网关偏好，无需重启 | QQ 记忆镜像与分发策略 |
| **MemCore 核心架构参数** | 环境变量 / 部署配置文件（`.env`） | 部署环境配置 | `SCOPE_RESTART_CLIENT`（需重启后端重建 MemCore 实例） | MemCore 构建与压缩触发（`memcore_integration/lifecycle.py`） |

表中的相对保存路径都相对 `<BotDataRoot>`，后端模块路径相对 `companion_v01/`。配置不一定只影响一个服务：修改主提供商时，复用它的服务也会变化，独立服务按自己的配置解析。

装配顺序从 `bot_runtime_components/factory.py:_load_runtime_settings` 看起：先加载可编辑的保存 override，再建立 config/view，随后把保存的模型服务覆盖到 `BotSettingsView`，最后叠加工厂显式传入的 `settings_overrides`。隐式单 Bot 保留进程级 config 读者；显式 Bot 使用自己的 `RuntimeConfigView`。QQ 选模在请求阶段覆盖模型名，不改这条装配链。

设置目录的 `current` 来自传入的 config view，模型服务页来自专用配置解析，当轮最终目标又可能有会话覆盖。排查时沿真实消费者核对，不能用其中一个页面的字段代替所有运行值。

---

## 2. 核心实例剖析

### 实例一：修改主聊天模型与思考强度

修改主模型是开发和使用中最常见的操作，核心在于**区分测试连通性、持久化保存与运行时生效**。

1. **界面与调用入口**：
   - 前端在 `desktop_pet_next/src/control-center-v2/app/model.js` 与 `desktop_pet_next/src/control-center-v2/model-service.js`。
   - **测试连接**：点击“测试连接”时调用 `POST /control-center/model-service/test`，仅用前端传入的 candidate 数据测试 API 连通性，**不保存配置，不改变运行中的 Bot**。
   - **保存配置**：点击“保存”时调用 `POST /control-center/model-service`。
2. **保存对象**：
   - 写入 `runtime_layout.config_dir / "model_service.json"`（由 `ModelServiceConfigStore` 负责）。
   - `_preserve_unedited_fields` 保留部分保存中未提交的主模型字段。同一提供商的留空密钥可沿用已保存值；切换提供商或明确要求清除时有不同处理，不能笼统认为所有留空都会保留。
3. **重载与联动机制**：
   - 保存成功后，调用 `bot_runtime.reload_model_services(settings)`。
   - 比较变更前后的服务商凭据三元组：`(protocol, base_url, api_key)`：
     - 若服务商身份**未变**（仅调整模型名或思考强度）：QQ 会话当前的个性化选模（`chat_model_overrides`）保持不变。
     - 若服务商身份**变更**：调用当前 Bot 的 `qq_gateway.clear_all_chat_model_overrides()`，清理其 QQ 会话选模；结果通过 `chat_model_overrides_status` 区分清除成功与持久化失败。
4. **真正消费位置**：
   - `ModelServiceSettings` 中的 `chat_model`、`chat_thinking_mode`、`chat_reasoning_effort` 对应运行 view 的 `chat_model_name`、`llm_thinking_mode`、`llm_chat_reasoning_effort`。
   - `runtime_settings.py:with_model_service` 做映射；`model_runtime/targets.py` 和 `request_policy.py` 解析后续请求目标与思考参数。已有请求捕获的上下文不应理解成被保存动作倒改。
5. **定向验证**：
   - `tests.test_model_service_config` 验证专用保存、字段保留与脱敏；重载后的 QQ 偏好清理见 `tests.test_bot_runtime` 中相关重载用例。

---

### 实例二：只修改子代理模型（Sub-agent）

子代理负责后台复杂工具调研或长期任务，其配置既可继承主模型，也可完全独立。

1. **界面与调用入口**：
   - 控制中心模型服务页的“子代理”表单（`subagent` 字段）。
2. **保存对象与模式含义**：
   - 保存至 `runtime_layout.config_dir / "model_service.json"` 的 `subagent` 节点（数据模型为 `SubagentModelSettings`）。
   - 包含三种模式：
     - `inherit`：使用父任务的实际执行目标，包括父会话当轮选模，并继承思考及输出设置。
     - `shared_service`：复用主聊天服务的凭据与协议，子任务有自己的模型、思考、输出预算和请求超时。
     - `independent`：使用子代理单独配置的提供商、模型及请求设置。
3. **安全脱敏与重载**：
   - `public_model_service_snapshot` 用 `hasApiKey`（子代理为 `subagent.hasApiKey`）表示是否保存密钥，不输出密钥正文。
   - 保存时通过 `existing_subagent` 保留独立模式下的已有密钥。
   - 保存后随 `reload_model_services` 更新到 `BotSettingsView.subagent`。
4. **真正消费位置**：
   - 派发子代理任务时，`subagent_models.py` / `model_runtime/targets.py` 根据 `subagent.mode` 解析目标客户端。
   - 只保存子代理字段时，现有部分保存链路保留主模型和独立视觉字段；不要把整个表单默认值重新覆盖回去。已运行子代理的设置副本由 `freeze_subagent_llm` 捕获。
5. **定向验证**：
   - `tests.test_model_service_config`。

---

### 实例三：仅切换一个 QQ 会话模型

用户在群聊或私聊中希望临时使用特定模型体验或测试，该操作属于**会话级覆盖**。

1. **界面与调用入口**：
   - 在 QQ 聊天框中发送 `/模型 <模型名>`，模型名取当前提供商列表，或发送 `/模型 默认` 清除覆盖。指令是宿主本地处理，不要求调用聊天模型。
2. **保存对象**：
   - 保存至 `runtime_layout.state_dir / "qq_gateway_state.json"` 的 `chat_model_overrides[session_id]`。保存失败在本地指令回执中带持久化警告。
   - 不修改 `model_service.json` 或其他会话的偏好；桌宠的默认聊天服务也不因此切换。已保存的选择可跨重启保留。
3. **入站上下文与覆盖传递**：
   - 消息到达 `channels/qq/inbound/context_builder.py` 时，网关解析出当前 `session_id` 的 `chat_model_override`。
   - 注入不可变的 `QQMessageContext`，经其 turn payload 进入 `engine_services/turn_preparation.py`。已准入消息保留捕获的值。
4. **真正消费位置**：
   - 在当轮执行 `turn_rounds.py` 准备模型请求时，若存在非空的 `chat_model_override`，则覆盖主配置的默认聊天模型；其他参数（Base URL、API Key）仍沿用当前服务商。
5. **定向验证**：
   - `tests.test_qq_command_dispatch`。

---

## 3. 记忆系统配置短补充

记忆相关配置涉及三种状态字段和不同的生命周期：

- **布尔开关的三态区分（`control-center/memory-settings`）**：
  - `current`：来自目标 runtime 的 `config_module`，可能是 Bot 独立 view。
  - `desired`：持久化在 `settings_overrides.json` 中的用户期望值。
  - `pendingRestart`：当 `desired != current` 时为 `true`，明确提示该项需要重启后端才能应用（例如 `OWNER_PRIVATE_SESSION_SHARED`）。
- **群记忆镜像与接收策略**：
  - 群开关通过 `POST /control-center/memory-settings/groups/{group_id}` 写入 `state/qq_gateway_state.json`，更新该 Bot 的网关偏好，无需重启。
- **MemCore 核心架构参数（如本次补齐的两项）**：
  - `MEMCORE_COMPACTION_REQUEST_MODE`（默认 `independent`）：压缩请求上下文模式（`independent` 或 `chat_prefix`）。
  - `MEMCORE_WORKING_TOKEN_TRIGGER`（默认 `200000`）：尚未终局的工具执行轮 token 压缩触发线，`0` 停用这类工具轮压缩；不是文件工作目录的大小限制。
  - 这两项在 `settings_catalog.py` 中标记为 `SCOPE_RESTART_CLIENT`，在通用目录中展示为只读。因为它们在 `memcore_integration/lifecycle.py:_build_memory_config` 构建 `MemoryConfig` 实例时被消费，热修改可能导致运行期组件状态不一致，常规更新操作为修改环境配置并重启后端。
- **摘要模型的复用**：`MEMCORE_SUMMARY_MODEL_NAME` 留空时不建立专用摘要 bundle，按会话聊天选模解析；只指定模型名时复用聊天接口，指定独立接口时 key/base 必须一起提供。实际构造见 `model_runtime/targets.py:_build_memcore_summary_bundle`，会话选择与旧任务结果的隔离见 `tests.test_memcore_model_routing`。主人桌宠与私聊共享记忆时，摘要模型选择还受共享记忆 namespace 的会话提示管理，不应把聊天偏好和摘要目标当成两个完全无关的对象。

---

## 4. 新增一个配置的最小步骤

当需要向系统引入新配置项时，遵循以下规范化步骤，避免配置漂移或假生效：

1. **在 `config.py` 中声明字段**：
   在 `Settings` 模型中声明字段名称、类型、默认值及校验约束（如 `Field(default=..., ge=...)`）。
2. **确定生效作用域（Scope）**：
   - `SCOPE_RUNTIME`：每轮请求或每次调用时实时从配置读取，改动可即时生效。
   - `SCOPE_RESTART`：仅在服务启动时读取一次，改动需重启服务。
   - `SCOPE_RESTART_CLIENT`：在特定子系统/客户端初始化时读取，需重建对应子系统（当前常规操作为重启）。
3. **在 `companion_v01/settings_catalog.py` 声明元信息**：
   - 登记分类、Scope、中文说明及敏感标记（`sensitive=True`）。
   - 若由专用表单管理（如模型服务），设置 `managed_in=MANAGED_MODEL_SERVICE`。
   - 部署项也可只读展示在目录中；仅当有意不进入目录时放入 `EXCLUDED_KEYS` 并说明理由。只读不等于必须排除。
4. **选择持久化入口与消费链路**：
   - 通用可编辑配置：由 `settings_overrides.py` 验证和持久化。`SCOPE_RESTART` 本身不等于已允许保存，当前只有明确的 `MEMORY_RESTART_KEYS` 走待重启保存路径。
   - 专用配置：由专用模块（如 `model_service_config.py`）管理。
   - 部署只读项：更新其环境来源，再重建对应消费者。修改 `config` 不会自动改写已捕获的 `BotSettingsView` 或客户端；新增功能要核对真实消费者的更新链。
5. **执行定向测试核验**：
   ```powershell
   .\.venv\Scripts\python.exe -B -m unittest tests.test_settings_overrides tests.test_settings_catalog tests.test_bot_settings_assembly
   ```
   确保无目录漂移，且敏感值与只读判定完全符合预期。
