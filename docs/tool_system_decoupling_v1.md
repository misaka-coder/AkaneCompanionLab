# 工具系统解耦设计 v1：把"动作决策"从"角色表现"里拆出来

> 状态：**分阶段落地中**。本篇定方向、契约、管线与迁移路线，并记录每阶段实现状态。配套修订见 `engineering_invariants_v1.md` 的 INV-1 / INV-2。
>
> **⚠️ 当前权威方向见 §12「转向 native-first」（2026-06-23 定调）**——第 8 节的"逐个迁 + 默认关"是此前的保守路线，已被 §12 覆盖。
>
> 一句话目标：**工具拿出去，表达留下。** 让工具调用走独立、受 schema 校验的统一管线，最终角色表现 JSON 留在工具结果回来后的回复轮产出。

---

## 1. 背景与问题

现状：最终回复是一个大 JSON，`tool_call` 是其中一个字段（`persona_profiles.toml`）。这导致：

- **工具与表现共享同一次脆弱的 JSON 生成**：模型要在一口气里同时做"精确的工具调用"和"自由的角色表演"，两件相反的事互相拖累。
- **大 JSON 一崩就触发兜底**（`llm_runtime._extract_json → _repair_json → dict(fallback)`），兜底=丢掉模型真实输出换模板。工具越多，回合越多，大 JSON 被生成的次数越多，兜底概率累乘。
- 结论（已与作者达成共识）：**只要工具还是大 JSON 里的字段，工具稳定性就有结构性天花板。**

## 2. 目标 / 非目标

**目标**
- 工具调用成为**独立协议**：有契约、走统一管线、失败结构化。
- 角色表现保持为**另一层协议**：表情/音乐/人设/好感度不退化（守 INV-1）。
- **provider 无关**：OpenAI / Anthropic / 旧 JSON 都归一成内部结构，换模型不再炸 engine。
- 让**小模型也不容易踩进大 JSON 的坑**。

**非目标**
- ❌ 不是照抄 native function calling。
- ❌ 不是一把梭重写。分阶段、可回退、有指标。
- ❌ 不动表达层的字段契约（那是 INV-1）。

## 3. 对标 Claude Code：学"边界"，不是学"用了 native tools"

Claude Code 真正值得抄的是**它把工具做成了独立协议**。参考它的工具契约 `src/Tool.ts` 与执行管线 `src/services/tools/toolExecution.ts`，抽出对我们有用的承重边界：

| Claude Code 的边界 | 含义 | 我们要学的点 |
|---|---|---|
| `name` / `aliases` / `description()` / `inputSchema`(+`inputJSONSchema`) | 身份 + 模型可见的 schema | 工具自带 schema，不靠散文提示 |
| `validateInput() → ValidationResult` | `{result:true}` 或 `{result:false, message, errorCode}` | **失败要带可读 message 回模型**，不是静默 |
| `checkPermissions()`（**仅在 validateInput 通过后调用**） | 权限/风险闸门，与校验分离 | 解析→校验→权限→执行，顺序固定 |
| `call() → ToolResult<T>` | `{ data, newMessages?, contextModifier?, mcpMeta? }` | 结果是**信封**，不是裸串 |
| `isReadOnly / isConcurrencySafe / isDestructive / isMcp` | 工具自描述风险/性质 | 风险元数据驱动权限与并发 |
| `mapToolResultToToolResultBlockParam()` | 把结果**塑形成喂回模型的块** | 喂回格式由工具自己定 |
| `buildTool(def)` + `TOOL_DEFAULTS` | 一处填**fail-closed 默认**（默认当写操作、默认非并发安全） | 60+ 工具靠一个构造器保持一致 |

**关键证据**（`toolExecution.ts` 的 `checkPermissionsAndCallTool`）：校验失败时返回的是
```
{ type:'tool_result', is_error:true, content:`<tool_use_error>${message}</tool_use_error>` }
```
**喂回模型、不是 fallback**，并打结构化日志 `tengu_tool_use_error{toolName,error,errorCode}`。这正是我们 INV-3 的标准形。

> 注意：`Tool.ts` 里还有一大堆 `render*`（React UI）方法——那是它前端的事，**我们后端不抄**，渲染归桌宠/控制中心前端。

## 4. 工具契约（Akane 版）：现状 → 目标

现状 `BaseToolHandler`（`tool_runtime.py`）已有雏形：`build_prompt_instruction / normalize_call / execute`。目标是补齐成独立协议：

| 能力 | 现状 | 目标（新增/强化） |
|---|---|---|
| 名称 | `tool_type` | 保留 + 可选 `aliases`（改名兼容） |
| 模型可见描述 | `build_prompt_instruction()` | 保留（散文）+ 新增 `input_schema`（结构化 JSON Schema） |
| 解析/归一 | `normalize_call()`（失败 `None`） | 拆成 `parse()` + `validate() → ValidationResult{ok, message, code}`，**失败带 message** |
| 权限/风险 | 散落在 capability 配置 | 工具自描述 `risk` / `is_read_only` / `is_destructive`；`check_permission(ctx) → PermissionResult` |
| 执行 | `execute() → ToolExecutionResult` | 收敛成 `ToolResultEnvelope`（见下），状态语义统一 |
| 喂回塑形 | `followup_context` 字符串 | 明确 `to_model_feedback()`，区分 ok / error |
| 默认值 | 各 handler 自管 | 仿 `buildTool`：一处 fail-closed 默认（默认当写操作、默认要确认） |

## 5. 内部统一结构（provider 无关）

```python
@dataclass
class ToolInvocation:
    id: str
    name: str
    arguments: dict
    source: str  # "native_openai" | "native_anthropic" | "legacy_json"

@dataclass
class ValidationResult:
    ok: bool
    message: str = ""     # ok=False 时，喂回模型的可读原因
    code: str = ""        # 机器可读错误码（unknown_tool / bad_args / not_available …）

@dataclass
class ToolResultEnvelope:
    invocation_id: str
    status: str           # "ok" | "error" | "needs_permission" | "needs_user"
    model_feedback: str   # 喂回模型的内容（error 时即 <tool_use_error>…）
    data: dict | None = None
    events: list = field(default_factory=list)   # 前台事件（产物登记、等待态等）
```

> ⚠️ 实现注意：可变默认值（list/dict）一律用 `field(default_factory=list)`，**不要**写 `events: list = None` 或 `= []`——前者实现时要么忘记初始化、要么退化成 `None` 引发空指针；后者是 Python 共享可变默认值陷阱。`ToolInvocation.arguments` 同理用 `default_factory=dict`。

OpenAI / Anthropic / 旧 JSON 三种来源，统一在 **adapter 层**转成 `ToolInvocation`；engine 之后只认 `ToolInvocation` / `ToolResultEnvelope`。

## 6. 统一管线（一条路，禁止旁路）

```
模型提出工具调用
  └─(adapter: native / legacy_json → ToolInvocation)
      └─ schema 校验 validate() → ValidationResult
          ├─ 失败 → ToolResultEnvelope(status="error", model_feedback="<tool_use_error>…")  ← 喂回模型，预算内重试；【绝不 fallback】
          └─ 通过 → 能力/权限检查 check_permission()
              ├─ 需要确认 → needs_permission 事件（交前台/授权策略）
              └─ 允许 → execute() → ToolResultEnvelope(status="ok") → 喂回模型
```

**硬规则**（呼应 INV-2 / INV-3 与 CLAUDE.md §4/§5）：
- 工具失败**只能**变成结构化 `tool_result` 喂回模型，**不准**退化成最终回复的 fallback。
- 模型**不准**在 `speech` 里声称"我已经调用了/已经搞定了"——结论以真实 `ToolResultEnvelope` 为准（防假成功）。
- 这条管线已有起点：`tool_orchestration_engine.classify_tool_call_rejection` + `engine._record_tool_call_rejection`（第三步已落地的结构化拒绝）。本设计是把它扩成完整契约。

### 6.1 多 tool call 规则（别留灰区）
INV-2 是"一轮一个工具"，但 native 通道一次响应**可能返回多个 tool calls**。adapter 会把它们解析成**多个 `ToolInvocation`**，管线必须有明确处置，不留灰区：

- **第二步 / 第三步（当前阶段）**：**每轮只接受一个**。收到多个时，**只执行第一个 `ToolInvocation`，其余结构化拒绝**：对被丢弃的那些发 `ToolResultEnvelope(status="error", model_feedback="本轮一次只能调用一个工具，已忽略其余 N 个：…；如需继续请下一轮再调用")`，并 `logger.warning` 记 reason。**不准静默吞掉多余调用。**
- 选"执行第一个 + 拒绝其余"而非"整批拒绝"，是为了不浪费模型已做对的那次决策；但二选一**必须在代码里写死、有日志**，不靠默认行为兜。
- **未来若要支持真并行多工具**，是独立提案（要先解掉 INV-2、`seen_tool_calls` 去重、回合预算如何记账等），**不在本设计范围**。

## 7. 表达层时序（"工具拿出去，表达留下"）

- **没工具的回合**：1 次调用，直接产出完整表现 JSON（不变贵）。
- **要工具的回合**：
  1. **瘦工具轮**：只产出工具调用（native tool_use 或精简结构），可附一句过场。
  2. **等待态事件**（非假成功，仅前台等待态）：
     ```json
     {"type":"assistant_working","speech":"我查一下哦","emotion":"thinking"}
     ```
  3. 工具结果回来 → **最终表现轮**产出完整表现 JSON（emotion/speech/persona/state_request/...，守 INV-1）。

> ⚠️ **等待态不是模型承诺**：`assistant_working` 是**系统/前端的状态事件**，不是模型在说"我搞定了"。文案只能是"我查一下 / 处理中 / 让我看看"这类**进行时**，**禁止**出现"已完成 / 已发送 / 已生成 / 找到了 X 条"这类**假进度、假完成、假结论**——真正的结论只能由工具结果回来后的最终表现轮给出（呼应 CLAUDE.md §5「不要 fake action / 不要假播放假进度」）。这条要在生成等待态文案的地方用约束兜住，不能任模型自由发挥。

> 成本注记：现状要工具时本来就是"大 JSON→工具→大 JSON…"多次；改后是"瘦工具轮×N + 一次表现轮"，**可能更省也更稳**，因为工具轮不再背整个表达层。

## 8. 迁移路线（分阶段、可回退）

**第一步（本篇）**：设计稿 + 修订 INV-1/INV-2。✅

**第二步**：引入内部 `ToolInvocation` / `ToolResultEnvelope` / `ValidationResult`。**先不接 native**，只把现有 `tool_call`（legacy_json）也走这套结构，跑通现有测试。零行为变化，纯打地基。

- **2a 已完成**：新增 provider-agnostic 结构与 legacy adapter；对已归一化的 legacy tool_call 保持 `legacy → ToolInvocation → legacy` 往返恒等。
- **2b 已完成**：live legacy dispatch 在 `tool_orchestration_engine.normalize_tool_call` 中穿过 `ToolInvocation` 边界后再还原给现有 `execute` 路径；仍不接 native，不改变 handler 的 `normalize_call/execute` 语义。
- **2c 已完成**：新增 `validate_tool_invocation` / `validate_legacy_tool_call`，旧的 rejection 字符串由 `ValidationResult` 生成；新增 `execute_tool_invocation` 与 `ToolExecutionResult → ToolResultEnvelope` 转换。`execute_tool_call` 仍返回旧 `ToolExecutionResult`，所以外部行为保持不变，但内部已经有校验结果与结果信封边界。

**第三步**：给 1–2 个**读操作、低风险、易校验**的工具接 native adapter，窄路原型：
- `web_search`、`retrieve_memory`、`read_memory_timeline`（也许加 `load_character_context`）。
- **先别动**：写文件 `compose_file`、发 QQ、控制浏览器 `browser_page`、媒体处理等写/高风险工具。
- **legacy JSON 通道保留为回退**，不全量切。

- **3a 已接入但未达准入**：`web_search` 非流式 native 原型接入，默认关闭（`ENABLE_NATIVE_TOOL_DECISION=false`）。开启且当前 `(host, model)` 能力档案支持 native tools 时，`web_search` 走 native schema；同名工具会从 legacy prompt 清单移除，避免双通道冲突。native call 通过内部 `_native_tool_call` 载体进入 engine，再携带 `_tool_source/_tool_invocation_id` 归一到 `ToolInvocation(source="native_openai")`；公开最终 payload 不依赖这些内部字段。
- **3b 已完成**：新增 dry-run 评测闭环 `companion_v01/tool_decision_eval.py` + `scripts/tools/run_tool_decision_eval.py`。当前不烧真实模型额度、不调用 AnySearch MCP，用固定 8 条 web_search 决策样本对 legacy/native 两种通道跑 normalize / validate / dry-run execute / fallback 统计，后续真实模型评测复用同一结果结构。
- **3c 已完成**：`scripts/tools/run_tool_decision_eval.py --live-llm` 可用真实 chat LLM 跑同一套评测。live provider 使用最小评测 prompt，不复用 `_prepare_final_response_context`，避免把记忆/附件/workspace 拉进评测；legacy 模式保留 `web_search` 散文工具说明、不发 native tools；native 模式发送 `native_web_search_tool_schema()` 并从 prompt 中移除 legacy `web_search` 说明。结果会标记 `native_sent`、`native_extracted`、`native_degraded`、`provider_unsupported`、`comparison_eligible`，避免 native 退化成 legacy 时污染对比。
- **3d 已完成**：新增 `scripts/tools/run_native_web_search_acceptance.py` 作为 native `web_search` 准入门。默认跑 dry-run gate；加 `--live-llm --smoke --real-web-search` 时会同时验证 live LLM 决策、真实 engine 非流式/流式回合、真实 AnySearch MCP、`assistant_working` 等待态、最终表现 JSON fallback、工具不可用事件。该 gate 失败时返回非 0，并列出具体 failure reason。
- **3e 已完成**：新增 `NativeToolDecisionPlan` 收口 native 启用策略。engine 不再散落判断“开关/allowlist/handler/provider”四类条件，而是拿一个 plan：`enabled` 时发 native schema 并从 legacy prompt 排除同名工具；`unsupported` 时保留 legacy prompt 并记录 provider unsupported。当前阶段是**短期双入口、内部单管线、提示单通道**：已验证 native 的工具不再同时出现在 legacy 工具说明里；legacy `tool_call` 只作为未验证 provider/model 的兼容入口。

- **4a 已完成**：native `web_search` 不再把工具调用塞回最终表现 JSON 的公开 `tool_call` 字段。`llm_runtime` 对 provider `tool_calls` 的结果使用内部 `_native_tool_call` 载体；`engine._prepare_tool_round_decision` 会消费并移除该载体，执行路径仍保留 `_tool_source/_tool_invocation_id` 供去重和来源统计使用；`final_output_engine` 对公开 `tool_call` 剥离 `_tool_*` metadata，避免 UI payload 泄漏内部字段。legacy JSON `tool_call` 仍保留给未验证 provider/model 兼容。

- **4b 已完成**：native 工具轮的系统提示由 `engine._build_native_tool_round_instruction()` 统一生成。已通过 native schema 提供的工具（当前 `web_search`）必须走 provider `tool_calls`，不能再写进 JSON `tool_call`；仍在 legacy 工具清单里且未 native 化的工具可以继续短期走 JSON `tool_call`；如果不需要任何 legacy 工具，最终表现 JSON 的 `tool_call` 必须为 null。

- **5a 已完成**：`tool_runtime.ToolMetadata` 原本已经集中记录 `family/operation/risk/default_round_budget/background`；本阶段只补齐契约占位字段 `aliases/input_schema/requires_confirmation` 与派生只读属性 `is_read_only`。高风险内置工具只打 `requires_confirmation=True` 描述标签，**当前不接入 validate / permission / execute**，避免和现有 MCP approval 或 legacy 行为发生双门控。`tool_orchestration_engine.tool_metadata_dict()` 暂不暴露这些新字段，作为行为不变的保护线。

- **5b 已完成**：`retrieve_memory` 与 `read_memory_timeline` 补齐 `input_schema`，`native_tool_schema` 优先使用 metadata schema，`NativeToolDecisionPlan` 从 hardcoded `web_search` 扩展为 allowlist ∩ handler 的通用 native schema 计划。默认 `NATIVE_TOOL_DECISION_ALLOWLIST` 仍为 `web_search`，所以默认线上行为不变；显式配置 `web_search,retrieve_memory,read_memory_timeline` 时，这两个只读记忆工具可进入 native schema，并会从 legacy prompt 同名工具说明中排除。

- **5c 已完成**：`tool_decision_eval` 的 live provider 已从 `web_search` 专用泛化为 `web_search` / `memory` / `all` 三种 toolset。`scripts/tools/run_tool_decision_eval.py --live-llm --toolset memory|all` 现在会发送对应 native schemas 和最小路由提示；评测逻辑同时识别公开 `tool_call` 与内部 `_native_tool_call` 载体，避免 4a 后 native 结果被误判成 no-call。最近一次实测：`--live-llm --toolset memory --mode both --limit 5` 为 native/legacy 双 1.0；`--live-llm --toolset all --mode native --limit 10` 为 1.0、fallback=0、native_degraded=0。生产默认 allowlist 仍未扩大。

- **5e 已完成（激活 = 扩默认 allowlist）**：默认 `NATIVE_TOOL_DECISION_ALLOWLIST` 由 `web_search` 扩为 `web_search,retrieve_memory,read_memory_timeline`，把已过 live acceptance gate 的低风险只读记忆工具纳入 native 允许清单。**总开关 `ENABLE_NATIVE_TOOL_DECISION` 默认仍为 `False`**：allowlist 只决定"允许哪些"，是否真的走 native 仍取决于总开关（通常由 env 控制）。总开关开启后，这三个工具走 provider native schema 并从 legacy prompt 同名说明中排除；未验证 provider/model 或总开关关闭时，全部回退 legacy JSON `tool_call`。写/控制类工具不在 allowlist 内。下一批工具（`inspect_attachment`/`list_workspace`/`load_character_context`）各有路径/附件/动态 schema 边界，按同一 5b→5d 流程逐个推进，不在本步。

- **5d 已完成**：真实 engine smoke / acceptance gate 从 `web_search` 泛化到 `memory`，验证全链路（native 决策 → `_native_tool_call` → `ToolInvocation(source=native)` → execute → 最终表现回复）。`run_native_web_search_smoke.py --toolset memory` 用确定性 fixture handler（`SmokeRetrieveMemoryHandler` / `SmokeReadMemoryTimelineHandler`，罐头记忆、不读真实记忆库、不写盘）跑真实 `AkaneMemoryEngine` 一个回合；`run_native_web_search_acceptance.py --toolset memory` 复用同一 gate（`native_tool_call_extracted>0`、`tool_event>0`、流式有 `assistant_working`、fallback=0、最终回复非空 speech）。`web_search` 默认行为不变。新增 `tests/test_native_tool_smoke.py` 覆盖 toolset 映射 / fixture 执行的确定性部分；live `--smoke` 需真实模型，由人触发。

### 8.1 Provider / Model 能力档案（3a 修正）

3c live eval 暴露了一个关键事实：`protocol="openai"` 不是足够细的能力判断。DeepSeek flash/pro 同属 `api.deepseek.com`、同走 OpenAI-compatible API，但 native tools 与强制 JSON 的组合行为不同：

| Provider / Model | A 只发 tools | B tools + `response_format=json_object` | C tools + prompt-only JSON | D 无工具 + prompt-only JSON | 来源 |
|---|---:|---:|---:|---:|---|
| `api.deepseek.com` / `deepseek-v4-flash` | ✅ | ⚠️ 不稳定（保守按不可共存处理） | ✅ | ✅ | 本项目实测 |
| `api.deepseek.com` / `deepseek-v4-pro` | ✅ | ✅ | ✅ | ✅ | 本项目实测 |
| OpenAI GPT 系 | ✅* | ✅* | ✅* | ✅* | 官方文档口径，未在本项目实测 |
| Anthropic Claude | ✅* | N/A（无 OpenAI `response_format`） | ✅* | ✅* | 官方文档口径，未在本项目实测 |
| Gemini 3 系 | ✅* | ✅* | ✅* | ✅* | 官方文档口径，未在本项目实测 |

`*` = 不能直接点亮生产 native；接入前必须先跑本项目探针并把结果登记成 `(host, model)` 档案。

硬结论：

- native 能力档案的 key 必须是 **`(host, model)`**，不是 provider，也不是 `protocol`。`deepseek-v4-flash != deepseek-v4-pro` 已实测证明。
- 未登记或未实测的模型默认 `supports_native_tools=False`，自动回退 legacy JSON，避免 OpenAI-compatible provider 静默忽略 tools。
- native 工具轮是否保留 `response_format=json_object` 由档案决定：`native_tools_coexist_with_forced_json=False` 时，本轮不发 `response_format`，改用 prompt-only JSON + `json_repair`；为 True 时才保留强制 JSON。
- native 工具轮的 prompt 必须避免“无条件必须输出最终表现 JSON”。正确写法是：**需要工具时走 provider `tool_calls` 且不输出正文；只有不需要工具时才输出最终表现 JSON**。3c live eval 已验证：把“必须返回 JSON”从 native 工具轮移除后，`deepseek-v4-flash` 的 `web_search` native 提取从 0/4 提升到 4/4。
- 这不是 DeepSeek 特判。DeepSeek 只是第一批登记的 `(host, model)` 档案。

当前已登记档案：

```python
("api.deepseek.com", "deepseek-v4-flash"):
    supports_native_tools=True
    native_tools_coexist_with_forced_json=False  # 保守值：探针结果曾冲突，prompt-only live eval 已达标
    verified=True

("api.deepseek.com", "deepseek-v4-pro"):
    supports_native_tools=True
    native_tools_coexist_with_forced_json=True
    verified=True
```

新增探针脚本：

```bash
python scripts/tools/provider_tool_probe.py --models deepseek-v4-flash,deepseek-v4-pro
```

它会跑 A/B/C/D 四个最小探针并输出建议的 `ProviderToolProfile`，但**不会自动改代码**。接入任何新 provider/model 前，先跑探针，再登记档案，再允许 native。

**第四步**：原型指标达标后，逐步迁更多工具；最后才考虑给大 JSON 瘦身。

## 9. 验证指标（用数据决定，不靠感觉）

每阶段对比改前/改后：
1. **工具调用成功率**（提出 → 成功执行的比例；失败按 code 分桶：unknown_tool / bad_args / …）。
2. **最终 JSON fallback 触发率**（`dict(fallback)` 命中率）。
3. （可选）平均每轮 LLM 调用次数、首字延迟。

指标不达标就停在该阶段修，不往前叠（呼应 CLAUDE.md「不确定先降级」）。

当前 3b dry-run 命令：

```bash
python scripts/tools/run_tool_decision_eval.py --mode both
```

输出包括 `expectation_match_rate`、`normalize_success_rate`、`validation_success_rate`、`execution_success_rate`、`fallback_hit_count`、`native_source_call_count` / `legacy_source_call_count` 与 legacy/native delta。注意：这是**评测口径与管线 smoke**，不是线上模型质量结论；真实模型评测需要替换 `response_provider` 后复用同一套统计。

当前 3c live LLM 探察命令（会真实调用配置里的 chat 模型，但不会真实联网执行 AnySearch）：

```bash
python scripts/tools/run_tool_decision_eval.py --live-llm --mode both --limit 5 --summary-path reports/tool_decision_eval_summary.json --details-path reports/tool_decision_eval_details.jsonl
```

3d 准入门槛先写死：

- native `provider_unsupported_count == 0`
- native `native_degraded_count == 0`
- native `eligible_expectation_match_rate >= legacy eligible_expectation_match_rate`
- native `fallback_hit_count <= legacy fallback_hit_count`
- native `validation_success_rate >= 0.95`

任一不过，先停在 3c 调 schema/prompt/provider，不继续叠流式等待态。

当前 3d 准入命令：

```bash
python scripts/tools/run_native_web_search_acceptance.py --live-llm --smoke --real-web-search --limit 5 --summary-path reports/native_web_search_acceptance_summary.json
```

最近一次 fixture 准入结果（2026-06-23）：`status=pass`；dry-run gate、live LLM gate、非流式 engine smoke、流式 engine smoke 全部通过；`chat_json_fallbacks_delta=0`，`native_tool_provider_unsupported_delta=0`，流式 smoke 出现 `assistant_working`，工具事件为 `web_search_completed status=ok`。真实 AnySearch MCP 受外部服务可用性影响；若出现 `mcp_tool_call_timeout`，gate 会失败并保留结构化 `tool_unavailable` 结果，不允许模型反复重试或假成功。

3e 收口规则：

- native 工具启用由 `NativeToolDecisionPlan` 决定，状态必须可解释：全局关闭、allowlist 未包含、handler 缺失、provider/profile 未验证、已启用。
- `plan.enabled == true` 时，模型只看到该工具的 native schema；同名 legacy 工具说明必须从 prompt 移除。
- `plan.status == "unsupported"` 时，不发送 native schema，不排除 legacy prompt；这是一条明确降级，不是静默失败。
- 所有来源最终仍归一成 `ToolInvocation`，走 validate → permission/execute → envelope/followup；禁止任何 native/MCP/skill 旁路执行。

4a 当前边界：

- native 已验证的 `web_search` 已离开公开表现 JSON 的 `tool_call` 载体；`tool_call` 保留为兼容解析层，只服务 legacy provider/model。
- 最终 payload 不应泄漏 `_native_tool_call`、`_tool_source`、`_tool_invocation_id` 等内部字段；执行路径需要 metadata 时，只能在 engine/tool orchestration 内部使用。
- 4a 不扩大工具范围，不动写操作；只处理 `web_search` 的表现 JSON 去工具字段迁移。

4b 当前边界：

- 收紧 native 工具轮覆盖提示，移除“用 JSON `tool_call` 调已验证 native 工具”的误导。
- 保留 legacy prompt 文案给未验证 provider/model，以及尚未 native 化的工具，避免一次性切断兼容入口。

## 10. 风险与待决问题

- **Provider / model 行为差异**：`tools` 与强制 JSON 是否共存是 `(host, model)` 级事实，不是 provider 级事实。DeepSeek flash/pro 已实测不同；OpenAI / Anthropic / Gemini 只按官方文档记录，未在本项目实测前不点亮生产 native。adapter 要吸收这些差异，engine 不感知。→ 第三步原型重点验证。
- **工具轮模型可能不吐文本**：没关系，走心回复在最终表现轮；但要确保等待态事件覆盖，用户不"断片"。
- **能力门控/权限 与新权限模型的关系**：现有 `capability_registry` + `promptExposed` 如何映射到工具自描述的 `risk/permission`，第二步要定清楚。
- **prefix cache 对齐**：工具轮与表现轮拆开后，注意别破坏现有的 prompt 缓存分层（INV-4）。
- **Skill 需要上下文注入通道**：Claude Code 的 `SkillTool` 用 `newMessages` 把 skill 正文注入下一轮，而不是塞进 `tool_result`；Akane 的 `ToolResultEnvelope` 需要预留 `new_context_messages` 一类字段，且要和能力门控/模型覆盖按轮生效配套。
- **工具结果落盘不能泄漏绝对路径**：Claude Code 超限结果会落盘并把真实路径喂回模型；Akane 可以吸收“超限落盘 + 预览”纪律，但喂给模型的引用必须是 workspace 相对/抽象句柄，不能暴露本机绝对路径或缓存路径。
- **MCP 规模化前要按需展开**：当前 `promptExposed` 是 opt-in 直进 prompt；工具数量变大前，需要设计“只露名 + 按需展开 schema”的 ToolSearch/SkillSearch 类机制，避免 MCP/schema 把系统提示撑爆。

## 10.1 从 Claude Code 调研立即吸收的小任务

调研备忘录见 `docs/claude_code_tool_system_research_v1.md`。以下只列能小步落地、且不要求重写的吸收项：

- **工具自定义 model feedback**：把 `ToolExecutionResult.followup_context` 明确升级为工具自塑形的 `ToolResultEnvelope.model_feedback` 来源，允许工具在结果末尾追加安全的后置指令（例如 web_search 要求引用来源），但不得写假完成。
- **结果大小纪律**：给搜索/网页提取类工具补 `max_result_size_chars` 约束；超限时落盘或截断预览，模型只拿安全摘要，避免工具结果挤爆最终表现轮上下文。
- **空结果占位句**：工具执行成功但没有正文输出时，生成稳定占位句（例如“web_search completed with no output”），避免空 `tool_result` 让模型误判回合结束。
- **工具自报可用性**：把 `ProviderToolProfile` 与工具级 `is_enabled(profile/model/context)` 对齐。默认未登记/未知能力为不可用，不能只靠全局开关。
- **失败信封标准化**：校验/权限/执行失败统一落到 `ToolResultEnvelope(status="error", model_feedback="<tool_use_error>…")`，继续禁止转最终回复 fallback。
- **MCP 不开旁路**：MCP adapter 输出也必须归一成 `ToolInvocation`，走同一条 validate → permission → execute → envelope 管线；`promptExposed` 继续默认 opt-in。

## 11. 参考

**Claude Code 源码**（`F:/Akane/galgame/AkaneBrain/claude_code_annotated`）
- 调研备忘录：`docs/claude_code_tool_system_research_v1.md`
- 工具契约：`src/Tool.ts`（`Tool` 接口、`ValidationResult`、`ToolResult`、`buildTool` + `TOOL_DEFAULTS` fail-closed 默认）
- 执行管线：`src/services/tools/toolExecution.ts`（`runToolUse` → `checkPermissionsAndCallTool`：validate→permission→call→`mapToolResultToToolResultBlockParam`；校验失败 → `<tool_use_error>` 喂回）

**Akane 现状**
- 工具实现：`companion_v01/tool_runtime.py`（`BaseToolHandler` 三件套，40 个 handler）
- 工具编排：`companion_v01/tool_orchestration_engine.py`（`normalize_tool_call` / `classify_tool_call_rejection`）
- 回合循环：`companion_v01/engine.py`（`process_turn` / `process_turn_stream` + 共享回合辅助）
- 不变量：`docs/engineering_invariants_v1.md`（INV-1 表达层不可丢 / INV-2 工具管线 / INV-3 结构化失败）

---

## 12. 方向定调（当前权威方向）：转向 native-first，tool_call 降为回退

> 决策时间：2026-06-23。此前（2a–6b）是"legacy 为主、native 为辅、逐个迁、开关默认关"的保守路线。经与作者确认 + 对照行业（Claude/OpenAI/Anthropic 全是原生）与 Sakura（native-first 桌宠，工具走原生、表达单独一轮），**正式转向 native-first**。本节是当前的迁移路线，覆盖第 8 节里"逐个迁 + 默认关"的旧节奏。

**目标终点**：工具决策走 provider 原生通道（行业标准）；人设表达**单独一轮**产出（守 INV-1，不与工具焊接）；`tool_call` 字段**降级为兼容回退**（未验证 provider / 工具未覆盖 native 时才用），最终可选删除。

**为什么更快**：不再维护"哪些工具迁了、哪些没迁"的双轨进度；不再每个工具走一遍完整 live 仪式。装上门 → 翻转 → 收尾，三段式。

**关键复用（吃旧架构）**：`capability_registry.CapabilitySelection.tool_names_for_mode(mode)`（`capability_registry.py:223`）已经做"按客户端/场景只给需要的工具"。native 直接把**这个子集**喂进 native tools 列表——这正是避免"工具太多 native 路由稀释"的解法（对标 Sakura 的 `active_groups`）。**不是重写，是接上。**

**安全顺序（不能反，反了工具会断）**：

- **N1 — 全工具装 native 门（确定性，零额度）**：给每个 handler 产出 native schema（通用生成器已支持，精度处补 `input_schema`；描述已扫过基本无 tool_call 污染，仅 `compose_file` 一句待清）。纯新增，开关仍默认关 → 零行为变化。验收：每个工具都能产出合法 native spec + 单测。
- **N2 — 接 per-client 分发（确定性，零额度）**：native tools 列表由 `CapabilitySelection` 决定，每个客户端/场景只发其工具子集。验收：native 子集 == legacy capability 子集，按 mode 对齐。
- **N3 — 翻转优先级：native 为主（需 live，烧额度的一步）**：总开关默认开、allowlist 放开到全部、native 按 capability 子集下发；`tool_call` 字段保留但降为回退。验收：跑 acceptance gate 全工具集——INV-1 表达层完好、fallback 率低、按客户端补路由提示（仿 5c）让选工具质量达标。**这是"commit"那一刻,行为真正改变,要真机验证。**
- **N4 — 收尾（最后,且只在 N3 稳定后）**：二选一——①保留一层薄 `tool_call` 当**文档化回退**（换模型也稳，行业常见）；②彻底从表达 JSON 删除 `tool_call`（Sakura 式纯原生，锁定需支持 native 的 provider）。作者倾向最终走 ②"不维护 tool_call"，但**必须是最后一步**，N1–N3 全绿后再动。

**不变的边界**：写/控制/媒体工具的**执行与权限确认逻辑完全不变**——native 只改"模型怎么表达调用"，不改 execute、不绕过确认。表达层（emotion/persona/scene/segments…）是 Akane 自己的产品域，**没有行业标准、也不需要**，继续按角色需要演化；唯一被行业标准约束的只有"工具调用"这一件，N1–N4 就是把它掰回标准。

---

# 附录 A：Claude Code 三专题深挖（源码级 → Akane 结构决定）

> 这是把调研备忘录 `docs/claude_code_tool_system_research_v1.md` 附录 A 的源码结论，落成本设计要做的**结构决定**。每条带 Claude Code 文件路径 + 函数/类名（行号为调研时所见，以函数名为准）。上面第 10/10.1 节是摘要，这里是可据以实现的细节。

## A.1 ToolResult 超限：落盘而非截断（`src/utils/toolResultStorage.ts`）

**机制（源码实读）**：
- 阈值 `getPersistenceThreshold(toolName, declaredMaxResultSizeChars)`（~L55）= `Math.min(工具声明值, 全局默认 DEFAULT_MAX_RESULT_SIZE_CHARS)`；`maxResultSizeChars === Infinity`（如 Read）是硬退出，**永不落盘**（~L62，理由：落盘再让模型 Read 回读是循环）。
- 落盘判定 `maybePersistLargeToolResult()`（~L272）：① 空结果先拦截注入占位句（~L287）；② 图片块跳过（~L302）；③ `size <= threshold` 原样返回；④ 超限 → `persistToolResult()` 写盘 → `buildLargeToolResultMessage()` 替换 content。
- **喂回模型的形态**（`buildLargeToolResultMessage` ~L189）：
  ```
  <persisted-output>
  Output too large (1.2 MB). Full output saved to: <filepath>

  Preview (first 2.0 KB):
  <前 2000 字节，按换行边界截断>
  ...
  </persisted-output>
  ```
- 写盘细节：`projectDir/sessionId/tool-results/<tool_use_id>.{json|txt}`，`flag:'wx'`（已存在即跳过，避免每轮重写、保 prompt cache 前缀稳定，~L162）。
- 消息级聚合预算 `enforceToolResultBudget()`（~L769）：单条 user 消息内多个 tool_result 总和超 `getPerMessageBudgetLimit()` 时挑最大的若干落盘；**一旦"看见"命运冻结**（`ContentReplacementState{seenIds, replacements}` ~L390）——已替换的每轮重放同一预览串（零 IO、字节一致），已发原文的永不事后替换。全为 prompt cache 服务。

**Akane 结构决定**：
- `ToolResultEnvelope` 增一个**超限塑形步**：工具声明 `max_result_size_chars`，超限时落盘 + 喂回"预览 + 句柄"。
- **红线（CLAUDE.md §3）**：Claude Code 喂回的是本机绝对路径；Akane **不能**这么做。喂回的引用必须是 **workspace 相对路径 / 抽象句柄**（走现有 workspace 抽象），绝对路径/缓存路径不进 model_feedback、不进 prompt。
- **空结果占位句**（A.1 的 ①，源码 ~L287，注释 inc-4586：空 tool_result 尾部会让部分模型提前停止整轮）——几乎零成本，**建议最先抄**：工具成功但无正文时，`model_feedback` 给稳定占位句而非空串。
- 冻结/重放纪律（seenIds）与我们的 INV-4（缓存块只放稳定内容）同源——落盘改造时要保证同一结果每轮喂回字节一致，否则破 prefix cache。

## A.2 SkillTool：正文走 `newMessages`，不走 `tool_result`（`src/tools/SkillTool/SkillTool.ts`）

**机制（源码实读）**——`call()`（~L580）三种执行形态：
1. **inline（默认）**：`processPromptSlashCommand()`（~L638）把 skill 展开成完整 prompt 消息（`!command` 替换、`$ARGUMENTS` 插值）→ 经 `tagMessagesWithToolUseID()`（~L735）→ 作为 `newMessages` 返回。**`mapToolResultToToolResultBlockParam()`（~L843）此时 tool_result 只有一句 `Launching skill: X`**；真正正文在 `newMessages` 里。
2. **forked**（`command.context === 'fork'`）：`executeForkedSkill()`（~L122）在隔离子 agent（独立 token 预算）`runAgent()` 跑，只回填结果字符串。
3. **remote**（实验）：`executeRemoteSkill()`（~L969）拉 SKILL.md → 剥 frontmatter → 注入 `createUserMessage({isMeta:true})`。

- **newMessages 落地点**：`src/services/tools/toolExecution.ts` ~L1566 `if (result.newMessages?.length) { for (…) resultingMessages.push({message}) }`——工具产出的消息直接追加进对话流，**下一轮 query 即普通上下文**。
- **`contextModifier(ctx)`（~L775）**：把 skill 声明的 `allowedTools` 并进 `alwaysAllowRules`、套用 `model`/`effort` 覆盖——**skill 携带能力门控/模型覆盖进入后续轮次的通道**。
- survive compaction：`addInvokedSkill()`（~L1088）登记，压缩后可恢复正文。

**Akane 结构决定**：
- 这是"skill 正文如何进入下一轮"的权威答案：**不塞 tool_result，而是工具产出"注入下一轮的消息" + tool_result 留一句确认**。
- `ToolResultEnvelope` 当前只有 `model_feedback`/`data`/`events`，**缺 `new_context_messages`（注入下一轮上下文的消息列表）**——这是做 skill **前置必备**的结构位，本设计先预留字段，不实现 skill。
- inline 的 `contextModifier`（带 allowedTools/model 进后续轮）对应 Akane 的"能力门控按轮"（INV-5）——做 skill 时要让 envelope 能带"本次调用临时放行的能力 + 模型覆盖"，且只在后续轮生效。

## A.3 MCP 暴露与权限（`src/tools/ToolSearchTool/prompt.ts` + `src/tools/MCPTool/MCPTool.ts`）

**何时进 prompt vs 延迟加载** —— 唯一裁决点 `isDeferredTool(tool)`（`ToolSearchTool/prompt.ts` ~L62），优先级自上而下：
1. `tool.alwaysLoad === true` → **永不延迟**，turn-1 带完整 schema 进 prompt（~L65）。MCP 工具经 `_meta['anthropic/alwaysLoad'] === true` 设此位（`src/services/mcp/client.ts` ~L1785）——**MCP 服务端可声明"我必须常驻"**。
2. `tool.isMcp === true` → **默认延迟**（~L68，"workflow-specific"）。未显式 alwaysLoad 的 MCP 工具不进初始 prompt，靠 ToolSearch 按需拉 schema。
3. ToolSearch 自己永不延迟（~L71）；通信通道类工具（Agent/Brief/SendUserFile，特性开关下）强制不延迟。
4. 其余看 `tool.shouldDefer === true`（~L107）。
- 延迟工具只在 prompt 露**名字**（`formatDeferredToolLine` 仅返回 `tool.name` ~L115）；模型用 ToolSearch `select:Name` / 关键词 / `+prefix` 取回完整 schema。

**权限** —— `MCPTool.checkPermissions()`（`MCPTool.ts` ~L56）返回 **`{behavior:'passthrough'}`**：MCP 工具不在工具内自决，交回上层权限系统。对照 SkillTool 的 `checkPermissions`（`SkillTool.ts` ~L432）：deny 规则 → 安全属性白名单自动放行（`skillHasOnlySafeProperties` ~L910，未知属性默认需确认）→ 否则 `behavior:'ask'`。安全属性白名单 `SAFE_SKILL_PROPERTIES`（~L875）是 **fail-closed**：新增字段默认收紧，需显式审查后入列。

**Akane 结构决定**：
- Akane 现在的 `promptExposed`（每轮把工具清单塞系统提示）≈ Claude Code 的 `alwaysLoad` 类。**MCP 默认延迟**这条说明工具多了不能全量铺 prompt——MCP 规模化前要做"露名 + 按需展开 schema"，否则撑爆系统提示。
- MCP 工具的权限应当 **`passthrough` 到 Akane 的能力门控层**（`local_capability_config`），不让工具自决。
- **fail-closed 默认收紧**（未知属性/未登记能力默认需确认）是贯穿性纪律——与第 5 节 `buildTool` 风格"一处默认"、A.3 的安全属性白名单、第 8.1 的"未登记 model 默认 `supports_native_tools=False`"是同一条原则，实现各层都照此默认。

## A.4 三专题汇总：`ToolResultEnvelope` 的待补结构位（仅设计预留，不在 3a 实现）

| 缺口 | 来源 | 设计动作 |
|---|---|---|
| `new_context_messages`（注入下一轮上下文） | A.2 SkillTool `newMessages` | 做 skill 前置；本设计预留字段，3a 不用 |
| 超限落盘塑形 + **workspace 相对句柄**（禁绝对路径） | A.1 `toolResultStorage` + CLAUDE.md §3 | 抓网页/搜索类工具补 `max_result_size_chars`；落盘引用走 workspace 抽象 |
| 空结果占位句 | A.1 `maybePersistLargeToolResult` ~L287 | `model_feedback` 成功无正文时给稳定占位句；可最先落地 |
| 能力门控随调用按轮放行（allowedTools/model 覆盖） | A.2 `contextModifier` | envelope/事件带"本次临时放行能力 + 模型覆盖"，仅后续轮生效 |
| MCP/工具规模化的"露名 + 按需展开" | A.3 `isDeferredTool` | 替代 `promptExposed` 全量铺开；MCP 默认延迟、可 `alwaysLoad` opt-out |

> 以上为只读调研落到设计的结构决定，未改任何代码。3a 范围仍只做 `web_search` 窄路 native，本附录的结构位除"空结果占位句/超限纪律"外均为后续阶段。
