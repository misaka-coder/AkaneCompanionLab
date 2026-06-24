# Capability Adapter v1 — 统一能力扩展抽象

Updated: 2026-06-16
Status: M1-M5 implemented (uncommitted)

## 0. 背景与问题

项目已经有两份设计文档把"能力管理"的方向定下来：

- `docs/capability_registry_v1.md`：轻提示 / 重指令双层 prompt 模型
- `docs/local_capability_workflow_registry_v1.md`：声明式 provider config + 工作流脚手架（Phase 1 → Phase 6C 已落）

工程上也已经具备：

- `companion_v01/local_capability_config.py` 的 `capabilities.yaml`（per-profile）
- `companion_v01/local_workflow_runners/comfyui.py` 的 ComfyUI HTTP client
- `companion_v01/mcp_stdio_discoverer.py` 的 MCP 发现器
- `companion_v01/capability_approval.py` 的审批闸
- `tool_runtime.py` + `tool_orchestration_engine.py` 的工具调用层

**但是每加一个具体能力仍然要走一遍 Phase X 的工程**：

| 能力 | 工程现状 | 真正缺口 |
|---|---|---|
| MCP | discover 通 → 工具看得见 | **不能 call**，没有 invoke 路径接入 LLM 工具栏 |
| ComfyUI | portrait_cutout 一条 workflow 跑通（4A→4I）| 加第二条 workflow 等于重做 9 个 phase |
| GPT-SoVITS | provider 注册 + health check 通；M4 已接入 `/tts` adapter 路径 | 角色包语音编辑 UI / ASR 仍未接入 |
| ASR 自定义 | M5 已接入本地 OpenAI 兼容 ASR adapter；默认 faster-whisper 路径保留 | 前端配置入口 / 文件监听 UI 未接 |

**根因**：能力扩展是"工程化"的而不是"声明式"的。每类能力都从零做一次 provider class + runner + UI surface + prompt 注入。

**Adapter v1 的目标**：把"加能力 = 填一份 manifest + 复用已有 adapter type 或写一个薄 adapter 类"做实。

## 1. 设计目标

1. **加新能力的成本上限**：≤ 1 份 manifest YAML + 0 行业务代码（如果能复用已有 adapter type，如 `mcp_stdio` / `comfyui` / `openai_compat_tts`）；新 provider 协议时 ≤ 1 个 50~150 行的 adapter 类。
2. **不抛弃已有工程**：`local_capability_config.py` / `mcp_stdio_discoverer.py` / `comfyui.py` / `capability_approval.py` 全部继续用，adapter v1 是它们之上的统一壳。
3. **关闭 MCP list-but-not-call 缺口**：MCP 工具调用走 adapter 的统一 `invoke()`，跟 ComfyUI / SoVITS 同一道闸。
4. **对齐双层 prompt**：manifest 自动生成 capability_registry 的轻提示 + 触发条件 + 重指令。
5. **风险/审批统一**：所有 capability 都过 `capability_approval`，按 `risk` 字段走 `trusted_auto_allow` / `ask_each_time` / `disabled`。
6. **Surface 严格按 client mode**：manifest 的 `surfaces` 字段决定 web / desktop / qq / base 哪些客户端可见，跟 `client_protocol.ClientMode` 严丝合缝。

## 2. 三层模型

```
Provider (一个外部装置)
   └── Capability (Provider 暴露的一个动作)
        └── Surface (这个动作在哪些 client mode 可见)
```

- **Provider**：一个 ComfyUI 服务 / 一个 MCP server / 一个 GPT-SoVITS server / 一个 ASR provider / 一个本地 Python 插件。Provider 有 `endpoint` + `health` + 可选的 `tiers`（部署档位，借鉴 Sakura 的 RTX50/NVIDIA/CPU 三级）。
- **Capability**：Provider 上的一个具体动作。一个 Provider 可以暴露 N 个 capability。每个 capability 有 `inputs/outputs` schema、`risk`、`confirm` 策略。
- **Surface**：每个 capability 标注 `surfaces: [desktop, qq, web, base]`，决定它在哪些 client mode 出现在 prompt 工具栏 / UI 工具按钮 / 工作区。

**重要**：Surface 不是新概念，它就是 `client_protocol.ClientMode` + `capability_registry` 的 mode pack 的声明式投影。

## 3. Manifest schema

存放位置（双层，已决议）：

1. **内置 manifest**：`companion_v01/builtin_capability_manifests/<provider_id>.yaml` — 项目自带，跟着代码走，只读
2. **Profile manifest**：`%LOCALAPPDATA%\Akane\users_data\<profile_user_id>\capability_manifests/<provider_id>.yaml` — 每个 profile 独立配置，UI 写入也落到这里

优先级：profile manifest 覆盖 builtin（同 provider_id 时）。`local_research/` 仅作研究素材目录，**不参与运行时扫描**。

完整 schema：

```yaml
schema: capability_adapter/v1
provider:
  id: comfyui                       # 全局唯一 slug
  type: comfyui                     # adapter type, see §4
  display_name: ComfyUI
  endpoint:
    url: http://127.0.0.1:8188      # 模板字符串支持 ${env.X}
    loopback_only: true             # public_guard 强制
  health:
    method: GET
    path: /system_stats
    timeout_seconds: 3
    expect_status: [200]
  tiers:                            # 可选；借鉴 Sakura 三级部署
    - id: nvidia_50
      label: "NVIDIA 50 系列"
      preset: { ... }
    - id: cpu
      label: "CPU 兜底"
      preset: { ... }
  secrets: []                       # 列出此 provider 需要的 secret key 名称，存到 model_service.json 同样的私有目录
capabilities:
  - id: portrait_cutout             # provider 内唯一
    display_name: "透明背景处理"
    short_hint: "把图片背景抠成透明"   # 轻提示文案，capability_registry 用（仅在 prompt_exposed=true 时生效）
    visible_in: [desktop, web]      # 在哪些 client mode 出现（UI 侧）
    prompt_exposed: true            # 默认 false；显式 true 才让 LLM 通过 prompt 知道这个能力
    risk: low                       # low | medium | high
    confirm: never                  # never | first_time | always
    effects: [media_generation]     # 副作用标签，影响 risk 自动提升，见 §6.3
    trigger:                        # prompt_exposed=true 时控制重指令何时注入
      kind: workspace_has_image     # 见 §6 触发器目录
    workflow_template: workflows/portrait_cutout.json   # 仅 comfyui type 需要
    inputs:
      - name: image
        kind: image_bytes
        required: true
        max_bytes: 8_388_608
    outputs:
      - name: cutout
        kind: image_bytes
        delivery: generated_file    # generated_file | workspace_attachment | inline_text | tts_audio
```

不同 adapter type 共享 `provider / capabilities / visible_in / prompt_exposed / risk / confirm / effects` 这套外壳，type 专属字段（如 ComfyUI 的 `workflow_template`、MCP 的 `command/args`、SoVITS 的 `tiers/refAudioPath`）在 type-specific 节点里。

### 3.1 prompt_exposed —— "试运行闸"语义

`prompt_exposed: false`（默认）：
- capability 已注册，UI 可见、可手动触发（按钮 / 显式 tool_call）
- **LLM prompt 里不会出现**，模型不知道这个能力存在
- 用户用这个状态"试跑"一个新装的 manifest

`prompt_exposed: true`：
- `short_hint` 进入对应 `visible_in` mode 的轻提示
- `trigger` 命中时重指令注入到 prompt
- LLM 可自主调用

这是"先 UI 验证、再交给 LLM 自主使用"的二阶段心智，避免不可控的能力静默接入 prompt。

### 3.2 effects —— 副作用标签

v1 effects 词汇表：

| tag | 含义 |
|---|---|
| `file_read` | 读本地文件 |
| `file_write` | 写本地文件 |
| `command_exec` | 执行 shell / 子进程 |
| `network_outbound` | 访问非 loopback 网络 |
| `browser_action` | 启动 / 控制浏览器 |
| `media_generation` | 生成新媒体文件 |
| `state_mutation` | 改用户态非平凡状态 |

`effects` 字段驱动 §6.3 的 risk 自动提升规则。

### 3.3 校验规则（v1 dict 校验，不引 jsonschema）

启动扫描时对每个 manifest 应用以下规则；任一规则失败，该 manifest 标 `invalid_config`，**不阻塞其它 manifest 加载**：

- 必须有 `schema == "capability_adapter/v1"`
- 必须有 `provider.id`，字符集 `[a-zA-Z0-9_.-]+`
- 必须有 `provider.type`，且 type 在 adapter type allowlist 里（见 §4）
- `visible_in` 每项必须 ∈ `{base, web, desktop, qq}`
- `risk` 必须 ∈ `{low, medium, high}`
- `prompt_exposed` 缺失时默认 `false`
- `endpoint.loopback_only == true` 时校验 url host ∈ LOOPBACK_HOSTS（沿用 `local_capability_config.py` 已有常量）
- `secrets` 中的明文值必须 reject（只允许引用 secret store 的 key 名）
- `tiers` 存在时，所有 tier id 必须唯一

## 4. Adapter 协议

```python
class CapabilityAdapter(Protocol):
    type: ClassVar[str]                      # "mcp_stdio" / "comfyui" / "openai_compat_tts" / ...
    provider_id: str

    async def health(self) -> HealthStatus: ...
    async def list_capabilities(self) -> list[CapabilityDescriptor]: ...
    async def invoke(
        self,
        capability_id: str,
        args: Mapping[str, Any],
        ctx: InvocationContext,
    ) -> CapabilityResult: ...
    async def aclose(self) -> None: ...
```

约定：

- `health()` 必须有 timeout，并且 loopback_only=true 时拒绝非 loopback host
- `list_capabilities()` 必须跟 manifest 声明的 capabilities 一致；如果 provider 是"动态发现型"（如 MCP stdio），manifest 里只声明 provider 不声明 capabilities，list_capabilities 在 startup/hot reload 时把发现的工具沉淀进 capability_registry
- `invoke()` 出错走两条路：协议错（unreachable / timeout / unknown_capability）抛 `CapabilityProtocolError`；业务错（工具内部失败）返回 `CapabilityResult(is_error=True, content=...)`——直接对齐 MCP `isError`
- `aclose()` 用于热重载和 shutdown，必须幂等

已规划 adapter type（v1 全部要做）：

| type | 包装的现有模块 | 主要场景 |
|---|---|---|
| `mcp_stdio` | `mcp_stdio_discoverer.py` 扩展 | MCP 本地 stdio server |
| `comfyui` | `local_workflow_runners/comfyui.py` | ComfyUI workflow |
| `openai_compat_tts` | `services/tts_client.py` 扩展 | GPT-SoVITS、Edge TTS、未来其他 TTS provider 共用此 type |
| `openai_compat_asr` | 新建 | 自定义 ASR (whisper.cpp / sensevoice / 云端) |
| `python_plugin` | 新建 | 借鉴 Sakura 的 auto-discovered local 插件 |

可后续追加（不在 v1 范围）：`mcp_sse`、`mcp_streamable_http`、`comfyui_workflow_bundle`。

## 5. 四类现有能力的接入草图

### 5.1 MCP（关键缺口：能 list 不能 call）

现状：`mcp_stdio_discoverer.py` 只做 initialize + tools/list。

接入：

1. 新建 `companion_v01/capability_adapters/mcp_stdio.py`，继承 `McpStdioToolDiscoverer` 加一个 `invoke()` 方法发 `tools/call`（按 MCP spec §Calling Tools）
2. Manifest 不声明 capabilities，由 discover 阶段动态填充
3. 注册时由 capability_registry 把 discovered tools 翻译成 prompt 工具描述，自动按 `surfaces` 字段（来自 manifest）注入到对应 client mode
4. risk 字段从 manifest 取，默认 `medium + confirm: first_time`（高风险预设）

**这一步做完，MCP 第一次"按得动"。**

### 5.2 ComfyUI（关键缺口：加新 workflow 是工程）

现状：portrait_cutout 单一硬编码 workflow。

接入：

1. 新建 `companion_v01/capability_adapters/comfyui.py`，把 `comfyui.py` runner 包装成 adapter
2. 每个 workflow 是 manifest 里的一个 capability，`workflow_template` 指向 JSON 路径
3. 第二条 workflow（例如 "image_upscale"）只需新增一份 manifest + 放一个 workflow.json，**零业务代码**

### 5.3 GPT-SoVITS

现状：provider config + health check 通；M4 已把 `/tts` 主流量接入 `openai_compat_tts` adapter，并支持 `emotionVoiceMap` 覆盖参考音频。

接入：

1. 新建 `companion_v01/capability_adapters/openai_compat_tts.py`，type 同时支持 EdgeTTS / GPT-SoVITS / 通用 OpenAI 兼容 TTS
2. SoVITS 的 `refAudioPath` / `promptText` / `tiers`（RTX50/通用 NVIDIA/CPU）放到 manifest type-specific 节点
3. 借鉴 Sakura 的"emotion tag → reference audio"：manifest 声明 `emotion_voice_map: { joy: ref_audio_joy.wav, ... }`，invoke 时由 final_output_engine 的 emotion 标签自动选择参考音频
4. voice.py 的 `/tts` 改成调 adapter.invoke 而不是直接调 EdgeTTSClient

### 5.4 ASR 自定义

现状：M5 已把 `/asr` 接入 `openai_compat_asr` adapter；配置了本地 OpenAI 兼容 endpoint 时优先尝试，失败回落到既有 faster-whisper 路径。

接入：

1. 新建 `companion_v01/capability_adapters/openai_compat_asr.py`
2. 用户可在 abilities 页填一个本地 sensevoice / whisper.cpp endpoint，YAML 走 capability_manifests
3. 当 manifest 启用时，voice.py 的 `/asr` 走 adapter；未启用时走现有默认实现

## 6. 与现有系统的衔接

### 6.1 capability_registry

manifest 加载后自动生成两类 prompt 资源：

- 每个 capability 的 `short_hint` 进入对应 surface 的"轻提示"
- `trigger` 触发时把 capability 的 `display_name + inputs schema + description` 拼成"重指令"塞入 prompt

trigger 类型目录（v1 内置）：

- `always`：始终注入重指令
- `workspace_has_image` / `workspace_has_audio` / `workspace_has_text_attachment`
- `workspace_has_generated_file`
- `chat_mentions_topic: [...]`（关键词命中）

### 6.2 tool_orchestration_engine

LLM 发出 `tool_call` 时，orchestrator 按"capability_id → adapter.invoke"路由，不再硬编码 `call_npc` / `manage_gift` 这种 if-else 分支。

老工具（retrieve_memory、set_reminder 等"内置"工具）继续走现有 ToolHandler 路径；adapter 是新增旁路，不破坏旧通路。

### 6.3 capability_approval

#### 6.3.1 risk 自动提升规则（从 effects 推导）

manifest 加载后、注册到 registry 前，按以下顺序应用提升：

1. 若 `effects` 含 `command_exec` 或 `browser_action` 之一 → 强制 `risk: high`
2. 否则若 `effects` 含 `file_write` 或 `network_outbound` 之一 → 若声明的 risk < medium，提升到 `medium`
3. 否则若 `effects` 仅含 `file_read` / `media_generation` / 为空 → 保留作者声明的 risk
4. 动态发现型 capability（如 MCP discovered tools）：若 manifest 未在 `low_risk_allowlist` 中显式列出该 tool name，**不允许 `risk: low`**，最低提升到 `medium`

#### 6.3.2 risk → approval 映射

- `risk: low + confirm: never` → 自动放行
- `confirm: first_time` → 第一次走 `ask_each_time`，用户确认后转 `trusted_auto_allow`
- `confirm: always` → 每次 `ask_each_time`
- `risk: high` → 强制 `confirm: always`，**忽略 manifest 配置中较低的 confirm 设置**

### 6.4 public_guard

外部 adapter 默认禁止在 public RUN_MODE 下加载，除非 manifest 显式声明 `public_safe: true`（v1 全部不允许，留 v2 设计）。

### 6.5 task_workspace

capability output `delivery: generated_file` 或 `workspace_attachment` 自动接入现有 `GeneratedFileService` / `TaskWorkspaceService`，沿用已有的"工作区可见"约定。

## 7. Hot reload 与用户体验

- 启动时扫描两个 manifest 目录，构建 adapter 注册表
- 控制中心 abilities 页显示三类卡片：
  - **已启用**（绿）：health 通、capability 在 prompt 里
  - **已发现未启用**（灰）：manifest 在但 enabled=false
  - **缺配置**（黄）：manifest 在但 endpoint 不可达或 secret 缺失
- 用户在 UI 改 endpoint / enable 切换后，触发 `reload_adapter(provider_id)`，单 provider 热重载，不影响其他
- 新 manifest 文件落盘后，文件监听触发 `discover_new_manifest()`，控制中心冒泡"发现新能力 X，是否启用"

## 8. 实施分期

每个 milestone 都应该可以独立跑测试、不破坏旧能力。

| Milestone | 范围 | 验收 |
|---|---|---|
| **M1: 骨架** | `CapabilityAdapter` Protocol + manifest loader + registry + builtin 目录扫描 | Completed: 2026-06-16 (uncommitted). manifest 能加载、可列出 provider，但还没人调它 |
| **M2: MCP 闭环** | `mcp_stdio` adapter 的 `invoke()`、capability_registry 接入、tool_orchestration 路由 | Completed: 2026-06-16 (uncommitted). prompt-exposed MCP stdio tools 可按 profile 进入 tool handler，并走 adapter/approval 路径 |
| **M3: ComfyUI 复用** | `comfyui` adapter 替换现有 phase 4 硬编码，加第二条 workflow 验证 | Completed: 2026-06-16 (uncommitted). 现有 portrait cutout runner 通过 ComfyUI adapter 执行，第二条 workflow 可由 manifest + json 构建 adapter |
| **M4: TTS / SoVITS** | `openai_compat_tts` adapter，emotion → ref audio 映射，voice.py 切流 | ✅ 已完成：EdgeTTS / SoVITS 走 adapter；emotion 标签可覆盖参考音频 |
| **M5: ASR + Hot reload UX** | `openai_compat_asr` adapter，控制中心三类卡片，文件监听 | ✅ 已完成后端骨架：ASR adapter + reload endpoint；前端文件监听 UI 留作 hardening |

非阻塞建议：

- M1-M2 是核心，**先做这两个**就能解锁 MCP 这条最痛的线
- M3-M5 可以并行，做哪条先取决于"3 分钟 demo 故事"先用到哪条

## 9. 参考来源

| 来源 | 借鉴的点 |
|---|---|
| MCP spec (modelcontextprotocol.io) | `tools/list` + `tools/call` 协议；`isError` vs 协议错的双错误模型；`annotations` 概念；`listChanged` 通知支撑 hot reload |
| Continue.dev MCP 配置 | "丢一份 YAML 文件到目录 = 加一个能力" 的极简心智；`type: stdio/sse/streamable-http` 同一外壳不同 transport |
| Sakura（Rvosy/sakura）| `.char` 单文件角色包；GPT-SoVITS 三级部署 tier 模式；emotion tag → 参考音频映射；两套并行插件系统（MCP + 本地 Python）；permission-gated tools |
| 自家 `capability_registry_v1` | 轻提示 / 重指令双层 prompt 模型直接对齐 |
| 自家 `local_capability_workflow_registry_v1` | manifest 字段名延用（`workflowPath`、`slotMapping`、`enabled` 等），降低迁移成本 |

## 10. 非目标（v1 不做）

明确划在 v1 之外，避免范围蔓延：

- `mcp_sse` / `mcp_streamable_http` transport（v1 仅 stdio）
- ComfyUI workflow JSON 在 UI 里可视化编辑（v1 仅声明引用）
- 公网 / 多用户场景下的 adapter（v1 全部 loopback_only + non-public）
- 角色包 `.char` 单文件打包（独立设计，与 adapter 并行推进）
- 自动从公网 marketplace 拉 manifest（v1 不联网拉）
- adapter 沙箱化（v1 信任源码 + manifest 校验）

## 11. 决议（2026-06-16）

| 议题 | 决议 |
|---|---|
| 存储位置 | 双层：`companion_v01/builtin_capability_manifests/`（内置只读）+ `%LOCALAPPDATA%\Akane\users_data\<profile>\capability_manifests/`（profile 可写，覆盖 builtin）。`local_research/` 不参与运行时扫描。 |
| Manifest 校验 | 不引 jsonschema，dict 校验。规则见 §3.3。单 manifest 失败标 `invalid_config`，不阻塞其它。 |
| MCP 命名空间 | 内部 ID 用 `mcp.<server_id>.<tool_name>`，UI 显示用 manifest 提供的 display_name。**不允许裸 tool_name 进入全局工具空间**。 |
| risk 默认值 | 缺省 `medium + confirm: first_time`。`effects` 字段驱动自动提升（§6.3.1）。外部动态发现工具不允许默认 `low`，必须 allowlist 才能声明 low。 |
| 字段名 | `surfaces` → `visible_in`（更直观）。新增 `prompt_exposed`（§3.1）+ `effects`（§3.2）。 |

---

**下一步**：按 `docs/capability_adapter_v1_m2_ticket.md` 执行 M2（MCP 闭环），优先复用现有 `McpStdioToolCaller`，把通用 MCP discovered tools 接入 Adapter v1 的 prompt/tool/approval 路径。
