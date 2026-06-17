# Capability Adapter v1 — M2 实施 Ticket（MCP 闭环）

Updated: 2026-06-16
Parent design: `docs/capability_adapter_v1.md`
Prerequisite: M1 骨架已审查通过
Scope: Milestone 2（MCP stdio adapter + prompt/tool 调用闭环）

## 目标

让已经配置并发现过 tools 的 MCP stdio server 可以通过 Capability Adapter v1 被 LLM 真正调用。M2 完成后：

- MCP discovered tools 以全局安全 ID `mcp.<server_id>.<tool_name>` 进入 tool prompt
- LLM 输出 `tool_call.type = "mcp.<server_id>.<tool_name>"` 时，tool orchestration 能路由到 adapter
- adapter 复用现有 `McpStdioToolCaller` 发 `tools/call`
- MCP 工具结果按 `CapabilityResult` / `ToolExecutionResult` 返回 followup，不伪造成功
- 高风险或需确认工具先返回 approval event，不直接执行
- 旧 `web_search` / AnySearch 专用路径不退步

**M2 不做**：UI 新页面、热重载、MCP SSE/HTTP、ComfyUI、TTS/ASR、工作区文件产物接入、approval 决策后的自动续跑。

## 现实基线（实施前必须承认）

代码现状已经不是设计草图里的“只能 list 不能 call”：

- `companion_v01/mcp_stdio_discoverer.py`
  - `McpStdioToolDiscoverer` 已实现 `initialize + tools/list`
  - `McpStdioToolCaller` 已实现单次 `initialize + tools/call`
- `companion_v01/tool_runtime.py`
  - `WebSearchToolHandler` 已直接复用 `McpStdioToolCaller` 调 AnySearch
- `companion_v01/local_capability_config.py`
  - `mcpServers` 已保存 stdio server config、discovered tools、sanitized input schema
  - `build_mcp_tool_config_entry()` 已使用 `mcp.<server_id>.<tool_name>` 作为 public id
- `companion_v01/routes/capabilities.py`
  - 已有 MCP config/discover routes
  - 已有 approval request store routes

因此 M2 的核心不是重写 JSON-RPC，而是把现有 caller 包进 Adapter v1 的统一注册、prompt 暴露、调用与审批路径。

## M2 关键决议

### 1. Profile manifest / config 方案

采用方案 C：

- engine 继续持有 M1 的 builtin adapter registry
- profile 相关 MCP server config 不写入 engine 全局状态
- 每次 prompt selection / tool execution 都用当前请求的 `profile_user_id` 懒加载 `capabilities.yaml` 里的 `mcpServers`
- 不新增 `engine.profile_user_id`，避免上一个 profile 的 manifest/tools 泄漏给下一个请求

M2 只消费现有 `capabilities/capabilities.yaml` 里的 `mcpServers`，不强制迁移到 profile `capability_manifests/`。真正的 manifest UI 写入和热重载留到 M5。

### 2. MCP tool ID 规则

- 内部/LLM tool type：`mcp.<server_id>.<tool_name>`
- MCP `tools/call.params.name`：仍传原始 `tool_name`
- 不允许裸 `tool_name` 进入全局 tool namespace
- `server_id` 与 `tool_name` 继续复用 `local_capability_config.py` 的 sanitizer

### 3. prompt_exposed 语义在 M2 的落点

现有 `mcpServers.tools` 没有 `prompt_exposed` 字段，catalog 里 `exposedToPrompt` 目前固定 false。M2 不改 UI，但要有安全开关：

- discovered tool 默认不进入 prompt
- 只有 config 中 tool 条目显式 `promptExposed: true` 或 `prompt_exposed: true` 才进入 prompt
- route/UI 还没有写这个字段时，可在测试 fixture/手写 config 中验证
- 若没有任何 prompt-exposed MCP tool，行为应与 M1 完全一致

### 4. Risk / approval

必须补 M1 审查指出的动态工具规则：

- 动态发现型 MCP tool 如果未在 server config 的 `lowRiskAllowlist` / `low_risk_allowlist` 中显式列出 tool name，不允许 `risk: low`，最低提升为 `medium`
- `risk: high` 强制 `confirm: always`
- `confirm: first_time` 在 M2 可先按 `ask_each_time` 处理，不实现 grant 持久化消费
- `confirm: always` / high risk 返回 `capability_approval_required` stream event，不调用 MCP
- `trusted_auto_allow` 策略可延用 `local_capability_config.capability_approval_mode()` 的判断，但不要绕过 high risk

中等风险是否允许 `confirm: never`：M2 先保持 M1 设计字面语义，不在本阶段改协议；只在 approval 映射函数中集中处理，方便后续调整。

### 5. 旧路径兼容

- `web_search` 仍保留专用 `WebSearchToolHandler`
- AnySearch 不在 M2 被强制迁移到 generic MCP adapter
- `_resolve_tool_handlers()` 必须继续返回旧工具 handler；MCP adapter tools 作为追加项

## 新建文件

```text
companion_v01/capability_adapters/
└── mcp_stdio.py                 # McpStdioCapabilityAdapter

tests/
├── test_capability_adapter_mcp_stdio.py
└── test_capability_adapter_mcp_orchestration.py
```

可选但建议新增：

```text
docs/fixtures/capability_adapter_m2/
└── mcp_servers_config.json      # profile capabilities.yaml 片段样例
```

## 现有文件改动

### `companion_v01/capability_adapters/types.py`

补充 M2 需要的字段或 helper：

- `CapabilityDescriptor.raw` 已可透传 input schema
- `CapabilityResult` 若不够表达 MCP content，可加：
  - `metadata: Mapping[str, Any] | None = None`
  - 不破坏 M1 测试

### `companion_v01/capability_adapters/__init__.py`

导出 `McpStdioCapabilityAdapter`。

### `companion_v01/capability_adapters/mcp_stdio.py`

实现：

```python
class McpStdioCapabilityAdapter:
    type = "mcp_stdio"

    def __init__(
        self,
        *,
        provider_id: str,
        server_id: str,
        server_config: Mapping[str, Any],
        tool_configs: tuple[Mapping[str, Any], ...],
        caller: Any | None = None,
    ) -> None: ...

    async def health(self) -> HealthStatus: ...
    async def list_capabilities(self) -> tuple[CapabilityDescriptor, ...]: ...
    async def invoke(self, capability_id: str, args: Mapping[str, Any], ctx: InvocationContext) -> CapabilityResult: ...
    async def aclose(self) -> None: ...
```

行为要求：

- `list_capabilities()` 从 sanitized `tool_configs` 翻译出 descriptor
- `capability_id` 必须等于 `mcp.<server_id>.<tool_name>`
- 未知 capability → 抛 `CapabilityProtocolError("unknown_capability")`
- caller 抛 `McpStdioDiscoveryError` → 抛 `CapabilityProtocolError(reason)`
- MCP result 中 `isError: true` → 返回 `CapabilityResult(is_error=True, content=result)`
- 正常 result → 返回 `CapabilityResult(is_error=False, content=result)`
- 输出/异常不包含 env、dotenv secret、本地绝对路径

### `companion_v01/local_capability_config.py`

扩展 MCP server config sanitizer，保持向后兼容：

- server 级：
  - `lowRiskAllowlist` / `low_risk_allowlist`: list[str]
- tool 级：
  - `promptExposed` / `prompt_exposed`: bool
  - `confirm`: `never | first_time | always`
  - `risk`: 若已有 sanitizer 推导 risk，可继续保留；显式 risk 仍要过动态工具提升规则

注意：`_config_for_write()` 必须把这些字段写回，否则 UI 保存后会丢配置。

### `companion_v01/capability_registry.py`

M2 不重写默认 module 系统。新增最小旁路：

- 新增一个可选的 dynamic modules/tools 注入入口，或在 engine selection 后追加 adapter tools
- 推荐不让 `CapabilityRegistry` 直接读文件，以保持它纯选择器职责

建议新增轻量 dataclass：

```python
@dataclass(frozen=True)
class DynamicCapabilityPromptTool:
    tool_name: str
    light_hint: str
    instruction: str
    client_modes: tuple[ClientMode, ...]
```

如果实现过重，可以放到 engine helper 中，M2 只要求 selection 能返回 MCP tool names。

### `companion_v01/engine.py`

新增 helper，保持 per-request profile：

- `_build_mcp_adapter_tools(profile_user_id, client_context) -> dict[str, BaseToolHandler]`
- `_build_mcp_prompt_modules(profile_user_id, session_id, client_context) -> ...`

修改点：

- `_resolve_tool_handlers()`：在旧 handlers 基础上追加当前 profile 可见、prompt-exposed 的 MCP adapter tool handlers
- `_resolve_capability_selection()`：在 registry selection 后追加 MCP tool names/light hints
- `_build_tool_instruction_prompt()` 或等价 prompt 组装处：为 MCP tool handler 输出 instruction

不能做：

- 不把 MCP tools 写进 `self.tool_handlers` 全局 dict
- 不缓存跨 profile 的 MCP tool handler，除非 cache key 明确包含 profile_user_id + server_id + config updatedAt

### `companion_v01/tool_runtime.py`

新增一个通用 handler：

```python
class AdapterCapabilityToolHandler(BaseToolHandler):
    tool_type = "mcp.<server_id>.<tool_name>"
```

职责：

- `build_prompt_instruction()` 从 descriptor/input schema 生成短工具说明
- `normalize_call()` 接受 `{"type": capability_id, ...args}`，保留 schema 中允许的参数
- `execute()`：
  1. 计算 approval mode
  2. 如需审批，返回 `capability_approval_required` stream event 和 followup，不调用 adapter
  3. 否则同步桥接 async `adapter.invoke()`
  4. 将 MCP content 格式化为 followup context

不要让 `tool_orchestration_engine.py` 直接知道 MCP 细节；它继续只调 handler。

## Prompt 格式建议

每个 prompt-exposed MCP tool 生成一条 instruction：

```text
- mcp.<server_id>.<tool_name>：<description>。
  调用格式为 {"type":"mcp.<server_id>.<tool_name>", ...参数...}。
  参数 schema: <压缩后的 properties/required>。
  该能力来自本地 MCP server，失败时不要假装完成。
```

约束：

- description 最多 240 字符，沿用 sanitizer
- schema 最多 24 个 properties
- 不输出 command、cwd、env、dotenv、绝对路径
- prompt 里不出现 “裸 tool_name”

## Approval event 格式

复用现有 browser control 风格：

```python
{
    "type": "capability_approval_required",
    "capabilityId": "mcp.<server_id>.<tool_name>",
    "actionId": "mcp.<server_id>.<tool_name>",
    "title": "MCP 工具需要确认",
    "summary": "...",
    "risk": "medium|high",
    "approvalMode": "ask_each_time",
    "approvalReason": "requires_confirmation",
    "payloadPreview": {...},
    "client_mode": context.client_mode,
}
```

M2 不要求 approval decision 后自动继续执行；用户批准后下一轮模型可再次调用，或 M3/M5 再做 grant 消费。

## 测试要点

### `test_capability_adapter_mcp_stdio.py`

1. `list_capabilities()` 把 discovered tool 翻译为 `mcp.server.tool` descriptor
2. unknown capability 抛 `CapabilityProtocolError("unknown_capability")`
3. invoke 正常调用 fake MCP server，返回 `CapabilityResult(is_error=False)`
4. MCP `isError: true` 返回 `CapabilityResult(is_error=True)`，不抛
5. caller timeout/error 抛 `CapabilityProtocolError`
6. result/followup 不泄漏 dotenv secret / env key / cwd
7. dynamic tool 未在 `lowRiskAllowlist` 时，显式 low 被提升到 medium
8. allowlist 命中时，low 可以保留

### `test_capability_adapter_mcp_orchestration.py`

1. 无 prompt-exposed MCP tools 时，旧 `_resolve_tool_handlers()` 输出不变
2. prompt-exposed MCP tool 出现在当前 profile 的 handlers 中
3. 不同 profile 的 MCP tools 不互相可见
4. LLM `tool_call.type = mcp.demo.echo` 能路由到 `AdapterCapabilityToolHandler`
5. high risk MCP tool 返回 approval event，fake MCP server 未被调用
6. low risk / confirm never MCP tool 会真正调用 fake MCP server
7. `web_search` handler 仍存在且不被 generic MCP handler 覆盖
8. prompt instruction 不包含 command/cwd/env/secret/绝对路径

### 回归

- 保留 M1 测试：
  - `python -m unittest tests.test_capability_adapter_manifest_loader`
  - `python -m unittest tests.test_capability_adapter_registry`
- 保留现有 route regression：
  - `python -m unittest tests.quick_regression_suite`

## 验证命令

```powershell
python -m unittest tests.test_capability_adapter_mcp_stdio
python -m unittest tests.test_capability_adapter_mcp_orchestration
python -m unittest tests.test_capability_adapter_manifest_loader
python -m unittest tests.test_capability_adapter_registry
python -m unittest tests.quick_regression_suite
git diff --check
```

## M2 完成定义（DoD）

1. 通用 MCP stdio adapter 能调用 fake MCP server 的单个 tool
2. prompt-exposed MCP tool 能进入当前 profile 的 LLM tool handler 集合
3. `mcp.<server_id>.<tool_name>` 命名空间全链路一致
4. dynamic discovered tool 的 low-risk allowlist 规则有测试覆盖
5. high risk / confirm always 不直接执行，返回 approval event
6. AnySearch `web_search` 旧路径不退步
7. 所有新增测试和 quick regression 全绿
8. `git diff --check` 通过
9. 完成报告明确说明：M2 只打通 MCP stdio，不代表 ComfyUI/TTS/ASR 已接入 adapter

## 实施顺序

1. Step A：扩展 MCP config sanitizer（promptExposed / confirm / lowRiskAllowlist），补 local config 单测
2. Step B：实现 `McpStdioCapabilityAdapter`，补 adapter 单测
3. Step C：实现 `AdapterCapabilityToolHandler`，只接 fake adapter 单测
4. Step D：engine per-request profile 懒加载 MCP tools，补多 profile 隔离测试
5. Step E：prompt instruction 接入，补 secret/path 不泄漏测试
6. Step F：approval gating，补 high risk 不执行测试
7. Step G：quick regression + `git diff --check`

## 风险与刻意推迟

- Approval grant 消费暂不做：M2 只创建 approval-required event，不实现批准后自动续跑。
- Hot reload 暂不做：profile config 保存后下一请求懒加载即可，文件监听留 M5。
- MCP long-running session 暂不做：继续使用现有 one-shot process per call，降低状态泄漏风险。
- MCP resource/prompt/listChanged 暂不做：M2 只覆盖 tools/list 已沉淀的 tools/call。
