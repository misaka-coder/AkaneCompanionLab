# capcore 设计稿 v0

状态：M1/M1.2 implementation notes
来源项目：AkaneCompanionLab
目标：从 Akane 当前的 Capability Adapter v1 中抽出一个可复用的“能力接入内核”，后续再作为依赖反哺 Akane。

## 1. 一句话

`capcore` 是一个可复用的能力接入内核。

它回答这些问题：

```text
当前有哪些外部/本地能力？
这些能力在哪些客户端可见？
这些能力风险多高，是否需要确认？
这些能力如何声明、校验、发现、调用？
配置坏了、服务不可达、调用失败时，宿主如何结构化降级？
```

它不回答这些问题：

```text
角色是谁？
记忆系统怎么压缩和检索？
UI 怎么展示？
具体 ComfyUI 工作流、MCP server、TTS/ASR 服务内部怎么跑？
```

在 Akane 体系里，可以这样分工：

```text
memcore = 她怎么记得
capcore = 她能安全接入哪些能力
charcore = 她是谁，以及她的角色包/资源如何定义
AkaneCompanionLab = 产品壳、具体适配器、角色体验、客户端表现
```

## 1.1 当前实现补记

截至 2026-06-30，`capcore` 已从设计稿进入 M1/M1.2 回接状态：

- `capcore` 提供 manifest/registry/risk policy、tool projection、invocation validation、permission request/decision。
- `capcore.sanitize_permission_preview()` 统一处理 approval preview 的 secret、URL query secret 和本地路径脱敏。
- `capcore.project_mapping_fields()` 给 Akane 旧 catalog entry 投影 canonical `risk / confirm / requiresConfirmation / effects`。
- Akane 已用这些 helper 替换了 `capcore_runtime.py`、`capability_approval.py`、`local_capability_config.py`、`local_capability_catalog.py` 里的多处通用逻辑。

边界仍保持不变：

```text
capcore = reusable gates and projection helpers
Akane = persisted profile policy, approval queue, concrete adapters, routes, UI, and product statuses
```

## 2. 为什么值得抽

Akane 里已经有一条比较成熟的 Capability Adapter v1 主线：

- manifest 声明 provider 和 capability。
- `CapabilityAdapter` 协议定义 `health / list_capabilities / invoke / aclose`。
- builtin manifest 和 profile manifest 双层扫描。
- `visible_in` 控制能力在哪些客户端可见。
- `prompt_exposed` 控制能力是否暴露给模型自主调用。
- `risk / confirm / effects` 描述风险和确认策略。
- loopback endpoint、secret key name、tier 唯一性等边界校验。
- 已有 MCP stdio、ComfyUI、OpenAI-compatible TTS、OpenAI-compatible ASR 等具体 adapter。

真正值得复用的不是这些具体 adapter，而是这条边界：

```text
给 AI 增加一个能力，应该尽量变成“声明 + 薄 adapter”，
而不是每次都改 provider、runner、UI、prompt、权限、工具编排。
```

抽出来之后，迭代节奏会更像 `memcore`：

```text
母项目承受真实复杂度
  -> 抽成小内核沉淀边界
  -> 小内核用快速测试独立迭代
  -> 再回接母项目，减少母项目通用复杂度
```

## 3. Akane 中的来源模块

M0 主要从这些文件提炼：

- `companion_v01/capability_adapters/types.py`
- `companion_v01/capability_adapters/protocol.py`
- `companion_v01/capability_adapters/manifest_loader.py`
- `companion_v01/capability_adapters/registry.py`
- `docs/capability_adapter_v1.md`
- `docs/fixtures/capability_adapter_m1/`
- `tests/test_capability_adapter_manifest_loader.py`
- `tests/test_capability_adapter_registry.py`

M0 暂时不直接搬这些产品层文件：

- `companion_v01/routes/capabilities.py`
- `companion_v01/capability_registry.py`
- `companion_v01/tool_orchestration_engine.py`
- `companion_v01/capability_adapters/comfyui.py`
- `companion_v01/capability_adapters/mcp_stdio.py`
- `companion_v01/capability_adapters/openai_compat_tts.py`
- `companion_v01/capability_adapters/openai_compat_asr.py`
- 控制中心 abilities 页面。
- Tauri、QQ、桌宠、角色包相关路由。

这些先留在 Akane 里，当作 `capcore` 的宿主适配层。

## 4. 核心概念

### Provider

Provider 是一个外部服务、本地进程，或宿主提供的能力后端。

例子：

- 一个 ComfyUI 服务。
- 一个 MCP stdio server。
- 一个 GPT-SoVITS / OpenAI-compatible TTS endpoint。
- 一个 ASR endpoint。
- 一个宿主内置 Python adapter。

### Capability

Capability 是 provider 提供的一个具体动作。

例子：

- `portrait_cutout`
- `image_upscale`
- `tts_synthesize`
- `asr_transcribe`
- `mcp.anysearch.search`

### Surface

Surface 表示能力可以出现在哪些客户端表面。

M0 内置 surface：

```text
base
web
desktop
qq
```

宿主可以把它映射到更细的产品模式：

```text
web -> scene_static / scene_live2d
desktop -> desktop_pet
qq -> qq_text
base -> 当前宿主允许的通用模式
```

### Prompt Exposure

“客户端可见”和“模型可见”要分开。

```text
visible_in
  这个能力在某些客户端/模式中存在，可以被 UI 或宿主手动触发。

prompt_exposed
  这个能力可以进入模型提示词或 native tool schema，让模型自主选择调用。
```

这保留了一个很重要的两阶段接入心智：

```text
先让 UI/开发者手动试跑
再决定是否交给模型自主使用
```

## 5. 公共 API 形状

M0 应该提供很小的公共 API：

```python
from capcore import (
    CapabilityAdapter,
    CapabilityDescriptor,
    CapabilityManifest,
    CapabilityRegistry,
    CapabilityResult,
    EndpointConfig,
    HealthStatus,
    InvalidManifest,
    InvocationContext,
    load_manifest,
)
```

可选门面：

```python
class CapabilitySystem:
    def scan(self) -> None: ...
    def list_manifests(self) -> tuple[CapabilityManifest, ...]: ...
    def list_invalid(self) -> tuple[InvalidManifest, ...]: ...
    def get_manifest(self, provider_id: str) -> CapabilityManifest | None: ...
    def list_capabilities(
        self,
        *,
        surface: str | None = None,
        prompt_exposed: bool | None = None,
    ) -> tuple[CapabilityDescriptor, ...]: ...
```

M0 可以先只保留 `CapabilityRegistry`。等 Akane 回接后，如果发现宿主每次都要重复组装同样流程，再补 `CapabilitySystem` 门面。

## 6. Manifest Schema v0

第一版 schema 尽量贴近 Akane 当前 `capability_adapter/v1`。

```yaml
schema: capability_adapter/v1
provider:
  id: comfyui
  type: comfyui
  display_name: ComfyUI
  endpoint:
    url: http://127.0.0.1:8188
    loopback_only: true
  health:
    method: GET
    path: /system_stats
    timeout_seconds: 3
    expect_status: [200]
  tiers:
    - id: nvidia_50
      label: NVIDIA 50 series
      preset: {}
  secrets: []
capabilities:
  - id: portrait_cutout
    display_name: Transparent background cutout
    short_hint: Remove image background and return a transparent image.
    visible_in: [desktop, web]
    prompt_exposed: true
    risk: low
    confirm: never
    effects: [media_generation]
    trigger:
      kind: workspace_has_image
    inputs:
      - name: image
        kind: image_bytes
        required: true
        max_bytes: 8388608
    outputs:
      - name: cutout
        kind: image_bytes
        delivery: generated_file
```

M0 继续使用 dict 校验，不强行引入 JSON Schema。

原因：

- Akane 当前实现已经是这个方向。
- dict 校验更容易输出面向用户/开发者的结构化错误。
- 未来需要 UI 表单时，可以再导出 JSON Schema，而不是一开始就把运行时绑死。

## 7. 校验规则

M0 校验策略应默认 fail-closed。

必需规则：

- `schema` 必须等于 `capability_adapter/v1`。
- `provider.id` 必填，并且匹配 `[A-Za-z0-9_.-]+`。
- `provider.type` 必填，并且必须在允许的 adapter type 集合中。
- `capability.id` 必填，匹配 `[A-Za-z0-9_.-]+`，并且同一 provider 内唯一。
- `visible_in` 每一项必须属于 `{base, web, desktop, qq}`。
- `risk` 必须属于 `{low, medium, high}`。
- `confirm` 必须属于 `{never, first_time, always}`。
- 缺省 `prompt_exposed` 时默认为 `false`。
- 安全相关布尔字段写错时必须 invalid，不能静默按默认值处理；尤其是 `endpoint.loopback_only`。
- `endpoint.loopback_only: true` 时拒绝非 loopback host。
- `secrets` 只能是 secret key name，不能是明文 secret value。
- `tiers` 中的 tier id 必须唯一。
- `inputs` / `outputs` 若存在必须是 list；slot 必须有合法 `name / kind`，`required / max_bytes` 写错必须 invalid。
- 未知 `effects` 必须判 invalid。

坏 manifest 不阻塞其它 manifest 加载。
如果 profile 层的坏 manifest 能从 `provider.id` 或安全文件名推导出 provider id，它必须遮蔽同 provider 的 builtin manifest，避免 profile 显式覆盖失败后继续暴露旧 builtin 能力。

这里的 `provider.secrets` 不是普通配置值列表，只能写 secret store 的 key name，例如 `openai_api_key`。
真实 secret、`Bearer ...`、`sk-...`、`ghp_...`、`key=value` 都不能写在这里。
普通 endpoint/config 字段不复用 secret key-name 校验，避免把 URL 参数里的 `=` / `:` 误判成 secret。

`capcore` 默认 adapter type / surface 是 fail-closed 默认值，不是框架上限。宿主可以通过 `ValidationPolicy` 显式扩展 `allowed_adapter_types` 和 `visible_in_values`；风险提升规则仍不应放松。

返回：

```python
InvalidManifest(
    source_path=...,
    source_layer="builtin" | "profile",
    reason="endpoint_not_loopback",
    detail="host=example.com",
    provider_id="comfyui",
)
```

## 8. 风险策略

M0 风险策略保持简单、确定、可测试。

effects 词表：

```text
file_read
file_write
command_exec
network_outbound
browser_action
media_generation
state_mutation
```

风险提升规则：

```text
effects 包含 command_exec 或 browser_action
  -> risk = high, confirm = always

effects 包含 file_write 或 network_outbound
  -> 最低 risk = medium
  -> 如果 confirm 原本是 never，则提升为 first_time

risk = high
  -> confirm = always

只包含 file_read / media_generation / 空 effects
  -> 保留声明值，除非被其它规则提升
```

宿主可以加更严格的策略，但 `capcore` 不应该静默降低风险。

## 9. Adapter Protocol

M0 protocol：

```python
class CapabilityAdapter(Protocol):
    type: ClassVar[str]
    provider_id: str

    async def health(self) -> HealthStatus: ...

    async def list_capabilities(self) -> tuple[CapabilityDescriptor, ...]: ...

    async def invoke(
        self,
        capability_id: str,
        args: Mapping[str, Any],
        ctx: InvocationContext,
    ) -> CapabilityResult: ...

    async def aclose(self) -> None: ...
```

错误模型：

```text
协议级失败
  抛 CapabilityProtocolError。
  例子：provider 不可达、timeout、transport 错、unknown capability。

能力业务失败
  返回 CapabilityResult(is_error=True, status=..., reason=..., content=...)。
  例子：provider 接受了请求，但内部 workflow/tool 执行失败。
```

这保留 MCP 里很重要的一条边界：

```text
协议坏了
和
工具跑了但返回业务错误
不是同一类失败。
```

## 10. Invocation Context

M0 context 保持轻量：

```python
@dataclass(frozen=True)
class InvocationContext:
    profile_user_id: str = ""
    session_id: str = ""
    client_mode: str = ""
```

它只放最小运行上下文。

不要把这些内容塞进 context：

- API key。
- 本地绝对路径。
- 截图内容。
- prompt 全文。
- 聊天历史。
- 用户文件正文。

更复杂的运行依赖由宿主 adapter 构造器注入。

## 11. Registry 语义

M0 registry 扫描两层 manifest：

```text
builtin manifests
profile manifests
```

同一个 `provider.id` 下，profile manifest 覆盖 builtin manifest。

Registry 输出：

```python
list_manifests() -> 有效 manifest
list_invalid() -> 无效 manifest 和原因
get(provider_id) -> 当前选中的 manifest 或 None
reload(provider_id) -> M0 可以先全量 rescan
```

M0 registry 不负责创建具体 adapter。

原因：

```text
manifest 加载和校验是内核逻辑。
具体 adapter 如何实例化是宿主/产品逻辑。
```

未来可以加 adapter factory，但 M0 先保持边界清楚。

## 12. 多端投影

`capcore` 应该帮助宿主回答：

```text
当前 client mode 下，哪些 capability 可见？
当前 prompt 策略下，哪些可见 capability 能进入模型 prompt/tool schema？
```

M0 可以先提供纯函数 helper：

```python
def filter_capabilities(
    capabilities: Iterable[CapabilityDescriptor],
    *,
    surface: str | None = None,
    prompt_exposed: bool | None = None,
    risk_max: str | None = None,
) -> tuple[CapabilityDescriptor, ...]:
    ...
```

Akane 仍然保留更复杂的 `ClientMode` 和 `CapabilityRegistry`。

关键边界：

```text
capcore 理解通用 surface。
Akane 负责把具体产品 client_mode 映射到 surface。
```

这会直接改善多端适配：

```text
QQ 不应该看到 Web 场景/礼物工具。
Web 不应该默认看到桌面权限工具。
桌宠可以看到桌面活动/本地文件/播放相关能力，但这些不该污染 QQ。
```

## 13. 结构化失败边界

`capcore` 不提供假成功。

典型失败：

```text
YAML 坏了
  -> InvalidManifest(reason="yaml_parse_error")

schema 不匹配
  -> InvalidManifest(reason="schema_mismatch")

loopback_only=true 但 endpoint 是公网 host
  -> InvalidManifest(reason="endpoint_not_loopback")

secrets 里像是明文 key
  -> InvalidManifest(reason="secrets_must_be_key_names")

adapter health timeout
  -> HealthStatus(ok=False, status="timeout", reason="...")

调用时 transport 坏了
  -> CapabilityProtocolError

provider 内部业务失败
  -> CapabilityResult(is_error=True, status="provider_error", reason="...")
```

宿主决定这些失败如何显示到 UI，或如何喂回 agent/tool loop。

## 14. Akane 回接计划

M0 抽出后，Akane 应把内核能力声明/校验逻辑改为依赖 `capcore`。

替换：

```text
companion_v01/capability_adapters/types.py
companion_v01/capability_adapters/protocol.py
companion_v01/capability_adapters/manifest_loader.py
companion_v01/capability_adapters/registry.py
```

变成：

```python
from capcore import ...
```

保留在 Akane：

```text
companion_v01/capability_adapters/comfyui.py
companion_v01/capability_adapters/mcp_stdio.py
companion_v01/capability_adapters/openai_compat_tts.py
companion_v01/capability_adapters/openai_compat_asr.py
companion_v01/routes/capabilities.py
companion_v01/capability_registry.py
control center abilities UI
```

当前需要处理的一处耦合：

```text
manifest_loader.py 依赖 local_capability_config.py 中的
LOOPBACK_HOSTS / MCP_SAFE_TYPE_RE / MCP_SECRET_MARKERS。
```

`capcore` 中建议改为本地默认策略或可配置策略：

```python
ValidationPolicy(
    loopback_hosts=frozenset({"localhost", "127.0.0.1", "::1"}),
    allowed_adapter_types=...,
    secret_markers=...,
)
```

M0 可以先使用默认值，后续再开放 override。

## 15. M0 包结构

建议结构：

```text
capcore/
  pyproject.toml
  README.md
  AGENTS.md
  capcore/
    __init__.py
    types.py
    protocol.py
    manifest_loader.py
    registry.py
    risk_policy.py
    validation_policy.py
    errors.py
  docs/
    capcore_design_v0.md
  examples/
    minimal_manifest_scan.py
    dummy_adapter.py
  tests/
    test_manifest_loader.py
    test_registry.py
    test_risk_policy.py
    test_protocol_contract.py
```

M0 保持依赖很轻：

```text
runtime: PyYAML
dev: ruff / build / unittest 或 pytest
```

Akane 当前 manifest 使用 YAML，所以 M0 保留 PyYAML 更自然。

## 16. M0 测试清单

最低测试：

- 有效 manifest 能加载。
- 坏 YAML 返回 `InvalidManifest`。
- schema 错误返回 `InvalidManifest`。
- 缺 provider id 返回 `InvalidManifest`。
- provider id 字符非法返回 `InvalidManifest`。
- 未知 provider type 返回 `InvalidManifest`。
- `loopback_only` 下非 loopback endpoint 被拒绝。
- secret-looking value 被拒绝。
- 重复 tier id 被拒绝。
- 未知 effect 被拒绝。
- `command_exec` 把风险提升为 high，并强制 `confirm=always`。
- `file_write` 把 low 风险提升到 medium。
- 缺省 `prompt_exposed` 默认为 false。
- profile manifest 覆盖 builtin manifest。
- 一个坏 manifest 不阻塞其它有效 manifest。

验证命令按 `memcore` 风格：

```bash
uv run --extra dev python -m unittest discover -s tests -v
uv run --extra dev ruff check .
uv run --extra dev ruff format --check .
git diff --check
uv run --extra dev python -m build
```

## 17. M0 不做什么

M0 不实现：

- 真实 MCP `tools/call`。
- 真实 ComfyUI workflow 执行。
- 真实 GPT-SoVITS / TTS 调用。
- 真实 ASR 调用。
- 控制中心 UI。
- 文件监听和 hot reload UX。
- 审批请求持久化。
- native tool orchestration。
- chat model tool result envelope。
- skill search 或延迟展开工具 schema。
- marketplace 或远程拉 manifest。

这些是宿主层或后续版本的范围。

## 18. Claude Code 校准结论

校准时间：2026-06-30

已对本地 Claude Code 源码做了一次窄范围校准。结论是：`capcore` 当前 M0 方向不需要大改，但要明确吸收几条边界纪律。

只读范围：

- `src/Tool.ts`
- `src/services/tools/toolExecution.ts`
- `src/tools/ToolSearchTool/prompt.ts`
- `src/tools/MCPTool/MCPTool.ts`
- `src/utils/toolResultStorage.ts`
- `src/tools/SkillTool/SkillTool.ts`

不照搬结构，只回答这些问题：

1. 工具/能力定义的基础接口怎么组织？
2. validate 和 permission 是否分层？
3. 结构化错误如何返回给模型或上层？
4. 默认值如何 fail-closed？
5. 工具很多时，是全量进 prompt，还是延迟展开？

### 18.1 已确认的边界

1. 工具定义是大接口，但可复用核心很小。

   Claude Code 的 `Tool` 接口包含 UI 渲染、进度、权限、schema、结果映射、搜索等大量职责。`capcore` M0 不应照搬大接口，只吸收这些内核字段：

   - `name / aliases`
   - `input_schema` 或 JSON schema。
   - `validateInput` 风格的结构化校验。
   - `checkPermissions` 风格的权限闸，但只定义协议，不实现宿主审批。
   - `isReadOnly / isDestructive / isMcp / alwaysLoad / shouldDefer` 这类风险和投影元数据。
   - `mapToolResultTo...` 对应的“结果信封”思想，但 M0 暂不做完整 agent loop envelope。

2. 执行顺序必须固定：schema parse -> validate -> permission -> call -> result。

   Claude Code 在 `toolExecution.ts` 中先做 schema safeParse，再调工具自己的 `validateInput`，通过后才进入权限和执行。失败会变成结构化 `tool_result`，内容包在 `<tool_use_error>` 中喂回上层，而不是转成假成功。

   `capcore` M0 对应决议：

   ```text
   manifest/config validation 属于加载期。
   invoke 参数 validation 属于调用期。
   permission/approval 必须在 validation 之后。
   validation 失败必须结构化返回，不进入 adapter.invoke。
   ```

3. MCP 权限应 passthrough 给宿主。

   Claude Code 的 `MCPTool.checkPermissions()` 返回 `behavior: "passthrough"`，也就是 MCP 工具不在工具内部自决权限，而交给上层权限系统。

   `capcore` M0 对应决议：

   ```text
   CapabilityAdapter 不应绕过宿主审批。
   capcore 只描述 risk/confirm/effects。
   具体是否允许执行，由宿主 permission/approval policy 决定。
   ```

4. 大量工具不应全量塞进 prompt。

   Claude Code 的 ToolSearch 机制里，MCP 工具默认 deferred；只有 `alwaysLoad` 的工具才在初始 prompt 暴露完整 schema。延迟工具先只露名字，需要时再取完整 schema。

   `capcore` M0 暂不实现 ToolSearch，但 schema 里保留未来方向：

   ```text
   prompt_exposed = 是否允许模型知道该能力。
   always_load / should_defer = future 字段，等工具规模变大再加入。
   ```

5. 空结果和大结果都要有稳定信封。

   Claude Code 对空 tool result 注入稳定占位句，对超大结果做持久化和预览，并且保持跨轮结果替换稳定，避免 prompt cache 被破坏。

   `capcore` M0 暂不做结果持久化，但保留后续方向：

   ```text
   CapabilityResult 不应允许“空成功”制造歧义。
   大结果处理属于宿主或后续 envelope helper。
   Akane 场景里不能把本机绝对路径暴露给模型；只能暴露 workspace handle 或抽象引用。
   ```

6. fail-closed 默认值值得保留。

   Claude Code 的 `buildTool` 给工具补默认值：默认非并发安全、默认非只读。Skill 的安全属性用 allowlist，新字段默认需要重新审查。

   `capcore` M0 对应决议：

   ```text
   未知 adapter type -> invalid。
   未知 effect -> invalid。
   高风险 effects 自动提升 risk/confirm。
   未识别能力不自动低风险。
   后续新增 manifest 字段默认不改变安全行为。
   ```

### 18.2 对 M0 的影响

本次校准后，M0 仍保持原范围：

- 继续只抽 types / protocol / manifest_loader / registry / risk policy。
- 不实现真实 ToolSearch。
- 不实现完整 tool result envelope。
- 不实现宿主 approval store。
- 不搬 Claude Code 的 UI/render/progress 接口。

但 M0 文档和实现要守住三条硬线：

```text
validation before permission
adapter never bypasses host approval
structured failure instead of fake success
```

如果 Claude Code 里有值得吸收的新字段，默认先放到 future，不进入 M0，除非它明显是 M0 必需边界。

## 19. 后续版本预留

v0.1 可以考虑：

- Adapter factory registry。
- Adapter lifecycle manager。
- health 聚合。
- hot reload hooks。
- permission policy interface。
- tool schema projection helper。

v0.2 可以考虑：

- MCP 动态发现 helper。
- 大量工具的延迟 schema 展开。
- agent loop result envelope helper。
- public-safe manifest mode。
- 给 UI 表单导出的 JSON Schema。

v1 可以考虑：

- 稳定 public API。
- Akane 完整使用 `capcore` 的 kernel modules。
- 具体 adapter 留在 Akane，或拆成可选 `capcore-adapters-*` 包。

## 20. 成功标准

M0 成功的标准：

- `capcore` 能加载并校验 Akane 现有 M1 fixture manifests。
- 原 manifest loader / registry 测试迁移后通过。
- Akane 能把内核 types / loader / registry import 替换成 `capcore`。
- Akane 回接后产品行为不变。
- 坏 manifest 仍然结构化降级，不阻塞其它 provider。
- 风险提升逻辑仍然确定、可测。
- 包可以用 `uv`、`ruff`、build 命令独立验证。

第一轮抽取要追求和 `memcore` 类似的感觉：

```text
小 public API
清楚的宿主责任
结构化失败
快速测试
不夹带具体人格、UI、客户端产品逻辑
```
