# 金融主动推送线性记忆与原生工具修复 V1

状态：Slice A-D 已实施并验证；Slice E 已完成停服迁移、离线压缩、finance 启服与真实缓存验收；当前补齐 memcore raw/vector 边界并准备双服务部署

日期：2026-07-17

当前分支基线：`9f595b9 feat(finance): compact migrated history offline`

## 1. 文档用途与当前边界

这份文档固定当前代码事实、目标行为、最小改动点、实施顺序和验证方法。后续即使 Codex 上下文被压缩，也应先读本文再继续，不要重新根据旧金融文档猜设计。

本轮只写文档，不修改运行代码，不启动 finance 后端，不处理云端 pending delivery。

当前涉及三个仓库：

- Akane 宿主：`AkaneCompanionLab`
- 私有插件：`AkaneFinancePlugin`
- 记忆包：`memcore`

勘探时工作区状态：

- Akane 宿主只有用户原有的 `.claude/` 未跟踪目录。
- 私有金融插件工作区干净。
- memcore 有用户正在进行的材料解析、提示词治理和原生工具改动；后续实现不得覆盖或夹带这些改动，修改 memcore 前要重新核对 diff。

若本文与下列旧文档的金融主动推送结论冲突，以本文为本次 repair pass 的实现依据：

- `docs/llm_prompt_cache_optimization_notes.md` 中 2026-07-13 的 finance push 隔离方案
- `docs/qq_finance_assistant_emquant_implementation_v1.md` 中已退休的宿主内置金融实现
- `docs/public_market_provider_implementation_v1.md` 中旧 `finance_push` scope

这些文档仍可提供历史原因，但当前生产实现已经迁到私有插件，旧的 transient/独立历史方案不再是目标行为。

## 2. 用户意图：必须守住的产品语义

### 2.1 插件是加法，不是替代品

默认组合语义：

```text
本轮可用能力 = 当前 Akane 已启用能力 ∪ 当前已启用插件能力
```

能力只受以下普遍边界限制：

- 实例或用户明确配置关闭
- 当前客户端不能承载
- 权限、风险、确认和副作用规则
- provider 或工具真实不可用
- schema、幂等、超时和预算上限

`plugin_proactive` 不能成为删除普通 Akane 能力、记忆或模块的隐藏开关。

### 2.2 模型自主决定工具使用

原生工具通道必须允许模型返回 `0..N` 个调用：

- 不需要工具时调用 0 个。
- 只需要一个时调用 1 个。
- 模型自主选择多个独立调用时，宿主可在安全范围内并行执行。

测试证明通道支持这些形状，不得把“至少调用两个工具”写成真实运行配额，也不得要求固定来源数、固定分析框架或固定工具顺序。

### 2.3 单一线性记忆

财经事件、工具调用、工具结果和分析进入同一 finance 实例、同一收件人会话的 memcore 时间线：

```text
user                外部财经事件
assistant.tool_call 模型选择的工具调用
tool.<name>          对应工具结果
assistant            本轮分析或不推送判断
user                 下一条外部财经事件
...
```

`tool_trace` 标签保留，用于渲染、审计和默认检索排除；标签不能再让工具轨迹逃离正常压缩生命周期。

插件 outbox 只负责订阅、幂等、投递状态和重试，不再承担第二套模型记忆。

### 2.4 缓存靠稳定前缀与尾部追加

稳定 system、工具 schema 和已经发生的历史保持字面顺序。新事件、新工具结果和新分析只追加在后面。压缩发生时允许一次局部前缀变化；不能为了缓存数字删除真实能力，也不能每个工具轮次重写原始 user prompt。

缓存命中率是观测指标，不是控制模型行为的硬编码条件。

## 3. 当前真实调用链与问题位置

### 3.1 插件分析入口

私有插件 `src/akane_finance_plugin/analysis.py`：

- `FinanceNewsAnalysisClient.analyze()` 读取插件 `analysis_history` 最近 4 条。
- 每条事件最多执行 3 个 `PluginReasoningRequest`。
- 每次请求都把一大段研究步骤放进动态 `extra_context`。
- 程序要求至少 2 个 evidence authority，否则重新请求。
- `【不推送】`作为抑制 sentinel。

这条链路当前把模型的研究自由变成固定来源计数与固定外层重试，同时让同一事件可能启动多个独立 Engine turn。

### 3.2 宿主推理桥

宿主 `companion_v01/plugin_reasoning.py` 的 `EnginePluginReasoningPort.analyze()` 当前固定注入：

```python
"turn_kind": "plugin_proactive"
"transient_user_message": True
"transient_assistant_message": True
"pre_retrieval_enabled": False
```

后果：

- 财经事件不进入普通 raw/memcore user turn。
- 最终分析不进入普通 raw/memcore assistant turn。
- 正常 assistant 结束后的 `compact_due_background()` 不会运行。
- 当前工具 preface 和 `record_tool_exchange()` 仍从其它路径写入 memcore，形成不完整时间线。

### 3.3 `plugin_proactive` prompt 分支

宿主 `companion_v01/engine_services/response_builder.py` 当前在 `plugin_proactive` 下硬编码：

- 关闭 Care。
- 跳过 memcore raw / episodic / semantic。
- 跳过自动 retrieval snippets。
- 跳过关系、附件、工作区、礼物、视觉状态和 persona reference 等普通上下文。

`companion_v01/prompt_builder.py` 又把完整 legacy 工具说明放在动态 user prompt 前部，并把金融 `extra_context`、事件和时间重新拼成一个 user message。

这不是插件机制的必要成本，而是专项分支造成的能力减法和 prompt 重写。

### 3.4 原生工具实际退化原因

宿主已经具备原生工具能力：

- `response_builder.prepare_context()` 会构造 native tool plan。
- `LLMRuntime._extract_native_tool_calls()` 支持 provider 返回调用数组。
- `AkaneMemoryEngine._execute_and_record_tool_batch()` 会对可并行的只读调用做有界并行。
- `_append_native_openai_tool_history_batch()` 能生成 `assistant.tool_calls` 与 `role=tool` 的当前轮原生历史。

生产 `native_tool_count=0` 的直接原因是 provider profile gate：

- PinAI 使用 OpenAI-compatible 协议。
- 当前 host/model 不在内置 `PROVIDER_TOOL_PROFILES`。
- 云端没有配置经过探针验证的 `NATIVE_TOOL_PROVIDER_ALLOWLIST`。
- `LLMRuntime._native_tool_profile()` 因此 fail closed，回到 legacy JSON `tool_call`。

现有探针 `scripts/tools/provider_tool_probe.py` 已能验证：

- tools without forced JSON
- tools + forced JSON
- tools + prompt-only JSON
- no-tools final JSON

并输出 runtime 真正能解析的 `host:model[:json]` 配置项。不需要新建 provider 探针框架。

### 3.5 工具结果为什么破坏当前轮前缀

当前 native 工具结果已经能通过 `post_user_turns` 追加；但 Engine 同时把以下文字重新合入下一次 `extra_user_context`：

- “第 N 次工具结果已通过结构化 tool_result 提供”
- 工具失败或拒绝说明
- 剩余预算/停止原因

因此即使 provider native tools 恢复，原始 user prompt 仍会在工具轮次中变化。原生结果已经存在时，这些重复文字没有信息价值。

工具预算耗尽时，`allow_tool_call=False` 还会让 native tools 数组和六千多 token 的 legacy 工具块一起消失，再次改变前缀。

### 3.6 memcore 压缩配置耦合

memcore `MemoryConfig.raw_compaction_excluded_categories` 当前默认包含：

```text
tool_trace
material_trace
```

count policy 只统计不在该集合里的消息。`tool_trace` 因此不产生触发压力，但仍会出现在可见 raw。

另一个关键耦合位于 `memcore/compaction.py`：

- `_trace_metadata_from_batch()` 把 `raw_compaction_excluded_categories` 同时当作“哪些 category 是 trace”的定义。

所以不能只从默认排除 tuple 删除 `tool_trace`；否则 summary 将不再确定性继承 `tool_trace` category/keywords。修复必须把“压缩触发排除”与“trace 身份”分开。

默认检索排除是另一项独立配置：

```text
retrieval_default_excluded_categories
```

它应继续包含 `tool_trace`，用户明确要求只改变压缩生命周期，不改变普通检索噪声控制。

### 3.7 插件第二套历史

私有插件 `subscriptions.py` 当前同时维护：

- durable `delivery_outbox`
- `recipient_delivery_state`
- `analysis_history`

成功 QQ 投递后，`mark_delivery_succeeded()` 把正文复制到 `analysis_history`；下一次分析再读取最近 4 条并拼入事件 prompt。

`analysis_history`没有清理上限，并且与 memcore 的连续性职责重叠。目标状态下它不再是模型上下文权威。

## 4. 可复用能力：不需要重构的依据

以下能力已经存在，repair pass 只需接回：

- PluginHost 的 `model.reasoning` 权限和有界 `PluginReasoningRequest`。
- CapabilityRegistry 的宿主能力 + 动态插件能力合并。
- provider native tool schema、tool-call 数组解析和 batch 执行。
- 当前轮 `post_user_turns` 原生 tool history。
- `MemcoreManager.record_user_turn/record_assistant_turn/record_tool_exchange`。
- memcore SQLite 同 namespace、同 `source_id` 的幂等写入。
- 正常 assistant 落库后的 `compact_due_background()`。
- 私有插件 outbox 的事件幂等键、发送正文保存和 QQ 投递重试。
- provider tool probe 与 cache usage/prompt audit。

不需要重写 Engine、PluginHost、CapabilityRegistry、memcore 数据库结构或 QQ 通道。

## 5. 目标端到端流程

```text
东方财富事件
  -> 插件过滤与订阅匹配
  -> durable outbox INSERT OR IGNORE
  -> 取最老 due delivery
  -> 构造一次 PluginReasoningRequest
       stable_system_context = 精简金融通用原则
       memory_idempotency_key = delivery 的稳定幂等键
       message = 当前外部财经事件
  -> Engine 正常持久 turn
       幂等记录 user 财经事件
       使用正常 Akane prompt/profile/capabilities
       模型自主返回 0..N 个原生工具调用
       原生 tool calls/results 追加到当前 provider history
       同时以 tool_trace 追加 memcore raw
       记录最终 assistant 分析或不推送判断
       调度 memcore background compaction
  -> 插件只做通用交付校验与可信字段投影
  -> should_send=false: outbox suppressed
  -> should_send=true: 保存完整投递正文到 outbox
  -> QQ 发送
  -> 成功标记 delivered；失败按 outbox 重试，不重新分析已有正文
```

技术失败且 outbox 之后重新分析时：

- 稳定 user event source ID 防止同一事件重复写入。
- 已实际产生的后续 assistant 修订可以按时间继续追加，不覆盖历史判断。
- 不伪造成功，不把坏 JSON 或空回复写成自然语言分析。

## 6. 文件级实施细节

### 6.1 宿主公开插件契约

文件：`companion_v01/plugin_api.py`

给 `PluginReasoningRequest` 增加两个有默认值的字段，保持现有插件源码兼容：

```python
stable_system_context: str = ""
memory_idempotency_key: str = ""
```

语义：

- `stable_system_context`：同一插件任务类型跨事件稳定的领域原则；进入稳定 system extra，不进入动态 user extra。
- `memory_idempotency_key`：宿主只用于生成不泄露原值的确定性 memory source ID；不得进入 prompt、日志或返回给插件。

`plugin_reasoning._validate_request()` 对两者做类型、NUL 和长度校验。建议上限：

- stable system：12,000 字符
- idempotency key：240 字符

这不新增插件权限。当前 `model.reasoning` 已经允许插件提供动态 `extra_context`；新字段只是把稳定和动态内容分到正确通道。

### 6.2 推理桥恢复正常持久生命周期

文件：`companion_v01/plugin_reasoning.py`

修改 `EnginePluginReasoningPort.analyze()`：

- 不再固定发送 `transient_user_message=True`。
- 不再固定发送 `transient_assistant_message=True`。
- 不再固定关闭 `pre_retrieval_enabled`；使用正常 Akane 默认行为。
- 继续设置 `turn_kind=plugin_proactive`，仅用于 audit/cache family，不再用于能力减法。
- 传递稳定 system context 和 memory idempotency key。
- 若 Engine frame 标记 `_transient_final_failure` 或没有有效 speech，返回结构化失败，不让插件靠字符串猜“处理中”。

注意：不要在此直接访问 memcore、Engine store 或插件数据库；桥只翻译公开请求。

### 6.3 宿主 user turn 幂等写入

文件：

- `companion_v01/store/core.py`
- `companion_v01/engine.py`

原因：一条 outbox 事件在技术失败后可能再次进入 reasoning。取消 transient 之前必须避免同一财经事件反复写成多个 user turn。

`MemoryStore.add_message()` 增加可选 `source_id` 参数：

```python
source_id: str = ""
```

行为：

1. 未传时保持 UUID 现状。
2. 已存在同 source ID、同 profile/session/character、同 role、同 content 时返回既有记录，视为幂等命中。
3. source ID 已存在但 owner、role 或 content 不一致时结构化报 collision，绝不覆盖。
4. 不改 SQLite schema；`chat_messages.source_id` 已是主键。

Engine 根据以下材料生成哈希 source ID：

```text
profile_user_id | session_id | character_pack_id | memory_idempotency_key | role=user
```

只保存哈希，不保存原始幂等键。memcore 已支持同 source ID 幂等，现有 `MemcoreManager._record_turn()` 会沿用 legacy record 的 source ID。

assistant 不使用同一个固定 ID：

- 成功生成并保存到 outbox 后，QQ 失败只重发正文，不重新推理。
- 若技术失败后模型后来给出新分析，它是新的真实时间线事件，应追加而不是静默覆盖旧输出。

### 6.4 稳定金融 system 通道

文件：

- `companion_v01/engine.py`
- `companion_v01/engine_services/response_builder.py`
- `companion_v01/prompt_builder.py`
- `companion_v01/llm_runtime.py`

数据流：

```text
PluginReasoningRequest.stable_system_context
-> Engine payload.plugin_stable_system_context
-> response_builder.prepare_context(...)
-> PromptBuilder.build_final_generation_context(...)
-> system_extra_blocks
-> provider system
```

要求：

- block 位于动态时间、记忆、事件和工具结果之前。
- OpenAI-compatible 路径继续由 LLMRuntime 合并进 system 文本。
- Anthropic 路径继续作为 system block。
- `prompt_cache_key` 的稳定 payload 加入 system extra 的稳定 hash，避免不同插件稳定规则共享错误观测 key。
- prompt audit 只记录长度/hash，不记录金融正文。

不要恢复已退休的宿主 `finance_v1` DomainProfile，也不要在宿主硬编码私有插件 ID。领域文本由有 `model.reasoning` 权限的插件经公开字段提供。

### 6.5 精简金融稳定原则

文件：`AkaneFinancePlugin/src/akane_finance_plugin/analysis.py`

当前长 `_analysis_instruction()` 不应原样搬入 system。目标文本只保留普遍且长期稳定的原则，例如：

```text
你正以当前 Akane 身份研究一个由可信插件接收的外部财经事件。
区分事件原文、工具事实、推断与不确定性；不要伪造来源、数据或工具结果。
当前信息不足时，自主选择可用工具；不需要工具时直接分析，不按次数凑工具或来源。
结合已有连续记忆判断本事件是印证、修正、反驳还是没有实质增量。
若没有值得送达的增量，可以返回【不推送】。
不给目标价、仓位、确定性买卖指令或收益承诺。
最终正文自然说明事件及其可能影响；可信时间和原文链接由程序补齐。
```

不保留以下固定流程要求：

- 必须两种解释
- 必须七个问题
- 必须四条机制
- 必须一个或两个以上独立来源
- 必须调用某个工具
- 固定栏目或固定推理顺序

字符上限属于 QQ 交付事实，可以保留一句简短说明；程序仍是最终长度权威。

### 6.6 prompt 动态区改为追加式

文件：

- `companion_v01/engine_services/response_builder.py`
- `companion_v01/prompt_builder.py`

移除 `plugin_proactive` 对 Care、memcore、关系、附件、工作区、礼物、视觉和 persona reference 的整组硬编码排除。是否出现由现有 QQ prompt profile、配置和真实可用状态决定。

保留专项 layout 的唯一理由是缓存顺序，而不是删能力。目标顺序：

```text
system:
  Akane/QQ 稳定规则
  精简金融稳定原则
  稳定输出契约

provider tools:
  当前真实可用的原生工具 schema，顺序确定

dynamic user:
  正常 QQ 模块产生的真实动态上下文（仅在存在时）
  episodic / semantic（仅压缩后变化）
  memcore 未压缩线性 raw（放在动态区尾部）
```

财经事件已经先 `record_user_turn()`，因此如果 memcore raw 已含当前 source ID：

- 不再额外输出“用户原始消息”副本。
- 不再在 raw 后输出单独当前时间；raw 自带时间锚点，事件正文也有发布时间。

如果 memcore 不可用或 current source ID 不在可见 raw：

- 结构化降级为一次 current message block，不能静默丢事件。

删除模型可见的：

```text
debug_enabled=false
```

宿主仍根据 debug flag 选择 fast/debug system contract；模型无需再看重复布尔值。

不要在本切片顺手重写全部 QQ 输出模板。只删除有明确重复权威的字段，并用 prompt audit 记录前后 token。

### 6.7 当前轮 native tool history 只走结构化追加

文件：`companion_v01/engine.py`

修改原则：

- 原生调用与结果只通过 `native_tool_history_turns/post_user_turns` 回给 provider。
- memcore `record_tool_exchange()`继续作为跨轮时间线记录。
- 当前轮 trace source IDs 继续加入 `prompt_exclude_source_ids`，避免同一结果同时以 raw 文本和原生 tool result 注入。
- 原生路径不再把“结果已通过结构化 tool_result 提供”写回 `extra_user_context`。
- 原生失败、拒绝或预算状态若必须告诉模型，追加为新的 post-user control turn，不得重写初始 user prompt。
- legacy fallback 继续使用清晰的文本 followup；它是兼容路径，不冒充 native。

工具 schema 暴露与“是否还能继续调用”分开：

- `native_tools`保持本轮可用能力集合稳定。
- 正常轮使用 `tool_choice=auto`。
- 达到宿主通用预算上限时使用 `tool_choice=none`，不删除整个 tools schema。
- legacy `tool_call`仍由宿主预算校验，不能越过上限。

预算是通用上限，不是工具配额；模型在任何允许轮次都可选择 0 个调用并完成回复。

### 6.8 PinAI 原生工具启用

文件通常不需要修改；使用：

- `scripts/tools/provider_tool_probe.py`
- `config.NATIVE_TOOL_PROVIDER_ALLOWLIST`

实施时先在不启动 finance job 的情况下，用云端当前 PinAI base URL、协议、密钥引用和模型运行探针。密钥只来自环境，不写命令历史、文档或日志。

根据探针输出：

- 支持 tools、不支持 forced JSON 共存：配置 `host:model`，工具轮使用 prompt-only JSON。
- 支持 tools 与 forced JSON 共存：配置 `host:model:json`。
- 不支持：保持 fail closed，并把 legacy 状态明确报告为外部能力限制；不能伪造 native 成功。

不要直接把未经探针的 PinAI host/model 硬编码进 `PROVIDER_TOOL_PROFILES`。

### 6.9 私有插件取消固定外层研究循环

文件：`AkaneFinancePlugin/src/akane_finance_plugin/analysis.py`

目标：

- 一个 due delivery 发起一个 `PluginReasoningRequest`。
- Engine 自己的通用工具循环允许模型按需继续研究。
- 删除 `MAX_ANALYSIS_ATTEMPTS` 和 `minimum_evidence_count`。
- 删除固定“门禁失败后要求再找一个来源”的 feedback prompt。
- 不再读取 `analysis_history`。
- `_render_event_message()`只输出当前可信事件字段。
- evidence authority 计数可留作 diagnostics，不再作为固定发送门禁。

程序保留的交付边界：

- reasoning port 结构化成功
- 有非空最终正文，或明确 `【不推送】`
- 正文与完整消息不超过渠道长度
- 不使用模型提供的时间、来源标题或链接覆盖插件可信字段
- Engine 标记 incomplete/fallback 时结构化失败

不再用一组中文进度短语猜 Engine 是否完成；完成状态应由 reasoning port 传递。

技术失败交给 durable outbox 的已有 backoff/attempt 上限，不在一次 delivery 内再套模型循环。

### 6.10 停用 `analysis_history` 作为第二权威

文件：`AkaneFinancePlugin/src/akane_finance_plugin/subscriptions.py`

首个 repair slice：

- `mark_delivery_succeeded()`不再写 `analysis_history`。
- 删除运行时 `list_recent_analyses()`调用和公开 dataclass 使用。
- 现有历史表只作为一个版本的 documented migration window 保留，不读、不新增、不进入 prompt。
- 本切片不 DROP 表，避免破坏回滚和扩大数据库迁移范围。

后续独立 schema maintenance 才决定是否从新建 schema 和旧库中移除该表；不能让它与 memcore 长期并行维护。

outbox 保留，因为它解决的是投递事务，不是记忆：

- 未生成正文时可重试分析。
- 已保存正文后 QQ 失败只重试发送。
- 幂等键防重复投递。
- delivered/dead 已有 8192 条保留上限。

### 6.11 `tool_trace`进入 count 压缩触发

memcore 文件：

- `memcore/schema.py`
- `memcore/config.py`
- `memcore/compaction.py`
- 相关 tests/docs

目标默认值：

```python
raw_compaction_excluded_categories = ("material_trace",)
retrieval_default_excluded_categories = ("tool_trace", "material_trace")
```

必须同时解耦 trace 身份：

- 新增稳定的 trace category 集合，例如 `TRACE_CATEGORIES=("tool_trace", "material_trace")`。
- `_trace_metadata_from_batch()`使用 `TRACE_CATEGORIES`，不能再读取 `raw_compaction_excluded_categories`来判断 trace。
- summary 继续确定性继承跨度内 `tool_trace/material_trace` category、keywords、scope。
- 显式 `categories=["tool_trace"]`仍可检索；普通 retrieve 仍默认排除。

这样 count policy 下，纯工具轨迹也会达到触发阈值并进入摘要，不再等待普通聊天凑够 30 条。

本切片不同时启用 token policy。memcore 已提供 token policy，但 Akane manager 尚未注入生产 tokenizer；不要用字符估算伪装精确 token。先按用户要求恢复正常 count 生命周期，再根据真实长工具结果指标决定是否单独接 TokenCounter。

### 6.12 压缩调度

取消 transient 后，正常 Engine 尾部已经会：

```text
record_assistant_turn
-> _schedule_memcore_compaction
-> compact_due_background
```

因此不要再给金融插件发明第二套定时压缩器。

边界情况：若进程在工具结果写入后、最终 assistant 前崩溃，trace 会留到下一次正常 assistant 或维护压缩。这是可接受的可恢复状态，不需要为单次崩溃增加常驻任务。

## 7. 实施切片与顺序

每个切片都应形成一个可验证 repair checkpoint；不要同时大改三个仓库后才测试。

### Slice A：PinAI 能力事实与宿主契约

1. 运行现有 provider probe，保存脱敏结论，不保存响应正文或密钥。
2. 给 `PluginReasoningRequest`增加稳定 system 与幂等字段。
3. 给 legacy MemoryStore 增加安全的可选 source ID 幂等写。
4. 修改 reasoning bridge 为持久 turn，并补结构化 incomplete 状态。
5. 只跑宿主单测，不启动 finance job。

2026-07-17 脱敏实测与实施结果：

- 使用云端 finance 实例现有环境单独运行 A/B/C/D provider probe；finance systemd 实例全程保持 inactive。
- PinAI 支持 OpenAI native tools，并支持 native tools 与 forced JSON 共存。
- 四组探针无错误，prompt-only JSON 的无工具终态也通过。
- 建议 allowlist 形态为 `host:model:json`；host、model、base URL、密钥和响应正文不写入本文。
- `PluginReasoningRequest` 已增加稳定 system 与 memory 幂等字段；reasoning bridge 不再把插件主动轮固定为 transient，也不再固定关闭 pre-retrieval。
- legacy `MemoryStore` 已支持 caller source ID 幂等：同 owner/角色/正文返回原记录，不同不可变身份结构化 collision。
- Engine 同步与流式路径已使用同一脱敏确定性 user source ID，并在进入后续 turn 上下文前消费原始幂等键。
- 本切片没有修改 prompt layout、金融插件研究循环、`analysis_history` 或 memcore 压缩策略，也没有处理 outbox pending delivery。

### Slice B：追加式 prompt 与 native 当前轮

1. 接稳定 plugin system block。
2. 移除 `plugin_proactive` 能力/记忆减法。
3. 当前事件只出现一次，raw 位于动态尾部。
4. 原生工具结果不再改写初始 user prompt。
5. tools schema 与 tool-choice 状态解耦。
6. 删除模型可见 `debug_enabled=false`。

2026-07-17 实施结果：

- `plugin_stable_system_context` 已进入 provider system extra，并以内容 hash 参与 prompt cache key；prompt audit 仍只记录长度和 hash。
- `plugin_proactive` 不再关闭 Care，也不再跳过关系、附件、工作区、礼物、视觉、persona reference、自动 retrieval 或 memcore 三层。
- memcore raw 已含当前 source ID 时不再重复输出 current message/time；raw 不可用或当前 source 缺失时保留一次 current message 降级块。
- 插件主动轮的 semantic、episodic 与未压缩 raw 按动态区尾部顺序输出；新增事件只在尾部追加。
- native tool call/result 只经 `post_user_turns` 回给 provider；不再向原始 user prompt 追加“已通过结构化 tool_result 提供”的重复说明。
- 工具预算结束后保留同一 native tools schema，使用 `tool_choice=none`，并在结构化 tool history 尾部追加简短宿主控制消息。
- 最终回复 prompt 不再输出 `debug_enabled=false/true`；fast/debug 仍由宿主选择对应输出契约。
- Slice B 聚焦与宽回归共 401 项通过。完整宿主 discover 运行 1471 项时另有 2 个可独立复现的既有失败：`test_llm_runtime_stream` 的旧 mock 不接受 `prompt_cache_key`，以及 `test_settings_catalog` 尚未登记 7 个既有 Settings 字段；本切片未修改对应运行文件或设置目录。
- 本切片没有修改私有插件固定研究循环、authority 门禁、`analysis_history`、memcore `tool_trace` 压缩配置或云端服务状态。

### Slice C：私有插件简化

1. 精简金融稳定原则。
2. 移除最近 4 条 `analysis_history`拼装。
3. 移除固定三次分析与固定来源数门禁。
4. 停止新写 `analysis_history`。
5. 保留 outbox 和可信字段投影。
6. 构建 wheel 并做 source-blind installed-artifact smoke。

2026-07-17 实施结果：

- 私有插件提交 `af5ada1`（`akane-finance-plugin` 0.7.11）完成本切片。
- 每个 due outbox delivery 只构造一个 `PluginReasoningRequest`；Engine 通用循环继续允许模型自主选择 0..N 个工具，插件不再套固定三次研究循环。
- 精简金融原则进入 `stable_system_context`，不同事件保持完全相同；动态 message 只含当前可信事件字段，`extra_context`只保留 QQ 正文长度事实。
- delivery outbox 的持久 `idempotency_key`原样进入 `memory_idempotency_key`；分析技术失败后的后续 outbox 重试复用同一键，不在 prompt 中暴露该键。
- 删除固定来源数量门禁和中文进度短语猜测；evidence authority 数量仅保留为 diagnostics。模型可直接分析、按需使用任意数量工具或返回 `【不推送】`。
- 插件不再读取最近 4 条 `analysis_history`，成功投递也不再新增该表；旧表仅保留一个 documented migration window，本切片未 DROP、未改写历史数据。
- outbox 排序、失败退避、已生成正文的 QQ 重试、可信发布时间/原文链接投影以及正文/整条消息长度门禁保持不变。
- 私有插件 Ruff、`git diff --check`、0.7.11 wheel 构建均通过；完整 47 项测试通过，其中 source-blind installed-artifact smoke 使用真实 Akane PluginHost 从隔离安装目录发现 wheel，并跑通主动推送闭环。
- 本切片没有修改 memcore 压缩配置、宿主运行代码、云端数据或服务状态；finance 服务仍保持停用，pending outbox 未处理。

### Slice D：memcore trace 生命周期

1. 先重新检查 memcore 用户未提交改动。
2. 解耦 trace category 与 compaction exclusion。
3. `tool_trace`参与 count trigger，检索默认排除不变。
4. 更新 memcore README、AGENTS 和 trace compaction 文档的旧结论。
5. 跑 memcore 全套测试、ruff、format check 和 build。

2026-07-17 实施结果：

- memcore 提交 `2cb929e`完成本切片；修改前后均保留用户已有的 8 个防串图/关闭任务相关未提交文件和 `.claude/`，未把它们夹入提交。
- `raw_compaction_excluded_categories`默认值从 `("tool_trace", "material_trace")`改为 `("material_trace",)`；`tool_trace`现在参与 count trigger，纯工具时间线无需等待普通聊天也能进入摘要生命周期。
- 新增稳定 `TRACE_CATEGORIES=("tool_trace", "material_trace")`；summary metadata 的 trace category/keywords/subject scopes 继承不再借用压缩排除配置判断。
- `material_trace`仍默认不计入 count trigger，但位于 eligible batch 跨度内时继续进入 transcript、source IDs 和 summary metadata。
- `retrieval_default_excluded_categories`保持 `("tool_trace", "material_trace")`；普通 retrieve 仍搜不到工具轨迹，显式 `categories=["tool_trace"]`仍可检索。
- 未启用 token policy，未引入第二套压缩任务；正常 assistant turn 后的既有 `compact_due_background()`调度保持唯一生产路径。
- memcore 完整 202 项测试通过，3 项可选 NumPy/Chroma 依赖测试跳过；Ruff lint、59 文件 format check、`git diff --check`、sdist 和 wheel 构建全部通过。
- 本切片没有修改宿主运行代码、云端数据或服务状态；finance 服务仍保持停用，pending outbox 未处理。

### Slice E：停服数据维护与真实链路

1. 备份 finance 数据库；不删除历史记录。
2. 使用 memcore 公共 facade 对现有 finance namespaces 做一次有界同步 compaction。
3. 任一 summary/semantic LLM 失败就保留 raw 并结构化停止，不手工改 SQL 标记。
4. 安装已验证的宿主与插件版本。
5. 配置探针建议的 native provider allowlist。
6. 只启动 finance 后端，personal 不随本切片改动。
7. 用真实推送链路观察工具通道、memcore 顺序、QQ 投递和 cache usage。

## 8. 测试与验收设计

### 8.1 宿主单测

更新或新增：

- `tests/test_plugin_reasoning.py`
  - request 不再 transient。
  - stable system/idempotency 字段安全传递。
  - incomplete frame 返回结构化失败。
- store tests
  - 同 owner/role/content/source ID 重复写只有一条。
  - 跨 owner 或不同 content 冲突拒绝。
- `tests/test_memcore_integration.py`
  - plugin proactive 能看到正常可见三层。
  - 当前事件只出现一次。
  - assistant 保存后调度 compaction。
- `tests/test_prompt_builder.py`
  - 两个不同财经事件的 stable system hash 相同。
  - prompt 不含 `debug_enabled=false`。
  - raw 之后没有重复 current message/time footer。
- `tests/test_native_web_search_tooling.py` / plugin engine bridge tests
  - 模型返回 0 个调用时直接完成。
  - 返回 1 个时正常执行。
  - 返回多个独立只读调用时 runtime 能消费数组并安全执行；这是通道形状测试，不是生产调用配额。
  - native result 只出现在 post-user structured history，不重复进入 initial user prompt。
- `tests/test_llm_client.py`
  - provider allowlist profile会真正发送 native tools。
  - forced-JSON 不共存时仍能走 prompt-only native tool round。
  - cache key 纳入 stable system extra hash。

### 8.2 私有插件测试

更新：

- `tests/test_analysis.py`
  - 一条 event 只请求 reasoning port 一次。
  - 不要求固定工具数或 authority 数。
  - 模型可直接分析、可自主使用工具、也可 `【不推送】`。
  - 时间和原文链接仍由程序可信字段控制。
  - 长度、空正文和结构化 incomplete 仍失败。
- `tests/test_stateful_runtime.py`
  - 发送成功不再写新 `analysis_history`。
  - 已保存正文的 QQ 重试不再调用模型。
  - 同 outbox delivery 的 reasoning retry 使用同一 memory idempotency key。

按插件 AGENTS.md 跑：

```text
unit tests
wheel build
source-blind installed-artifact smoke
Ruff
git diff --check
```

### 8.3 memcore 测试

必须覆盖：

- 默认 `raw_compaction_excluded_categories`不含 `tool_trace`。
- 默认 retrieval exclusion 仍含 `tool_trace`。
- 只有 tool exchanges、没有普通 user/assistant 时，达到 count trigger 也会创建 summary。
- summary source IDs 包含 tool use/result。
- summary metadata 继续继承 `tool_trace`。
- 普通 retrieve 搜不到 tool trace。
- 显式 `categories=["tool_trace"]`可检索。
- material trace 的既有行为不回归。

### 8.4 真实链路验收

不强制模型调用固定数量工具。真实验收观察：

- provider audit 中 native tools schema 确实发送，不再静默为 0。
- 模型选择 0、1 或多个工具时，宿主忠实处理。
- provider payload 中当前轮工具调用/结果保持原生 role 和 call ID。
- memcore 顺序为 event -> 可选 tools -> analysis。
- 下一条事件只在底部追加；压缩前 history prefix 不重排。
- 压缩后 old raw 被摘要替代一次，之后继续追加。
- QQ 只看到最终投递正文，不看到内部 tool preface/result。
- `【不推送】`不会投递，但其判断作为 assistant 时间线事实保留。
- 失败不假成功，outbox status/reason 可解释。

缓存只报告以下真实指标：

- reported input/output tokens
- cached/read tokens
- cache creation/miss tokens
- stable system hash
- tool schema hash/count
- initial user prompt hash
- post-user turn count
- 是否刚发生 compaction

预期是连续事件中 cached tokens 随追加历史增长；不把固定 80%/90%写成运行时硬门禁。如果精确前缀已稳定但 provider 仍不命中，应转查 PinAI upstream sticky routing/TTL，而不是继续删能力或填充无用文本。

## 9. 停服数据与部署注意

2026-07-17 只读快照：

- finance 后端 inactive，personal 后端 active。
- finance outbox 有少量 pending delivery。
- finance memcore 尚有大量未压缩 tool trace，最大 namespace 的未压缩字符几乎全部来自工具结果。
- episodic 已有 trace 摘要，但 semantic 尚未成功形成。

在修复前不要启动 finance job继续制造样本。

部署顺序必须保证：

1. 新 compaction 语义与持久 turn 代码先通过离线测试。
2. 插件 wheel 与宿主 API 版本匹配。
3. 数据库先备份，再用公开 facade 做维护压缩。
4. provider probe 成功后才设置 allowlist。
5. 只重启 finance，并检查 health、plugin activation、native audit、outbox 和真实 QQ 投递。

不要：

- 手工删除 memcore raw 或 analysis_history。
- 用 SQL 直接伪造 summarized/semanticized 状态。
- 清空 pending outbox来让状态看起来正常。
- 同时改 personal 实例模型和 finance repair，避免两条故障线混在一起。

## 10. 非目标

本 repair pass 不做：

- Engine 重写或再次抽包
- 新的金融记忆数据库/向量库
- UI、桌宠或设置窗口改造
- 固定工具数量、固定来源数量、固定分析栏目
- 为缓存命中率硬编码模型行为
- token compaction 接入
- 立即 DROP 旧 `analysis_history`表
- personal 云端模型迁移
- 金融订阅规则、新闻源或 QQ 命令重构

## 11. 实现完成后的用户感受

用户应感受到：

- 金融 Akane 保留正常 Akane 能力，并额外拥有金融插件工具。
- 模型可按事件复杂度自主决定直接分析、调用一个工具或调用多个工具。
- 连续推送能自然承接前面已经分析过的观点，不依赖插件硬塞最近 4 条。
- QQ 不出现工具过程、处理中占位或假成功。
- 长时间运行后 prompt 不再因 tool trace 无限增长到十几万 token。
- 缓存随线性历史自然增长，压缩时只出现可解释的一次波动。

仍需明确的外部风险只有一个：PinAI 当前 host/model 对 native tools 与 forced JSON 的真实兼容性。仓库已有探针，实施时必须以探针和真实 audit 为准，不能靠猜。

## 12. Slice E 实测基线与实施记录（2026-07-17）

### 12.1 云端旧版本真实缓存基线

本轮没有用模拟消息推算命中率，而是读取两个实例重启后的 `/metrics` 累计 usage，
并将连续真实请求与 `llm_prompt_audit` 的 section hash 对齐。Bot 中途掉线重启造成的空窗不计入连续样本。

- personal 连续两次真实最终回复合计 reported input `55,240`、cached input `10,752`，命中率 `19.46%`；与用户在 provider 面板看到的约 20%一致。
- finance 一次真实主动分析 reported input `15,239`、cached input `3,840`，命中率 `25.20%`。这是尚未部署 Slice A-D 的旧云端链路，不能作为修复后验收结果。
- personal 当时累计为 cached `95,488` / input `723,494`，约 `13.20%`；说明跨轮平均值甚至低于最近两轮。
- finance 当时 plugin proactive 累计为 cached `499,200` / input `1,960,389`，约 `25.46%`。

真实 hash 序列确认了三个结构问题：

1. `_final_prompt_cache_key()`没有用户/会话作用域；同一 key 的 31 次 personal 请求出现 6 套记忆前缀、2 套工具前缀和 29 套 raw，多个会话会争用一个 upstream cache bucket。
2. personal 的工具说明约占 `8.7k`估算 tokens，通常稳定却位于 append-only raw 后；raw 每次追加后，这个大块必然重新计算，因此结构上无法达到高命中。
3. 动态 persona state 位于第一条 system message；persona 内容变化时会在 raw 历史之前截断 provider 前缀。finance 旧链路还把 raw 保持为空，同一事件反复请求 2~3 次，并让 extra/tool context 每轮变化。

### 12.2 缓存布局修复

- cache key 加入不可逆的 `profile/session/character`作用域 hash，防止跨会话缓存桶互相覆盖；原始身份不进入 key、日志或 prompt。
- cache key 加入 legacy tool prompt hash；工具形状变化时进入独立 bucket，不破坏同一工具形状的历史前缀。
- 最终 prompt 顺序统一为：稳定 system/system extra -> 稳定用户说明和工具契约 -> 低频 semantic/episodic -> 线性 raw -> retrieval/extra/visual/persona/current message 等本轮动态尾部。
- persona state 保留完整能力与内容，只从第一条 system 的早期动态位置移到 raw 后的“宿主可信上下文”，不关闭人设、视觉、检索或工具。
- prompt audit 新增实际发送的 native tool schema hash/count；usage audit 为每次 final/plugin proactive 调用记录 reported/cached tokens 与命中率，不记录 prompt 或消息正文。
- `/metrics`新增 final 与 plugin proactive 各自的 token/call 计数，避免 auxiliary 请求污染最终回复命中率。

### 12.3 旧分析迁移准备

云端只读 dry-run 时，旧 `analysis_history`已有 155 条；155/155 都能与 delivered outbox、完全一致的投递正文、可信 event payload 和完整 memcore namespace 一一对应。memcore 当时没有 `plugin-event:*` 或本迁移 source id，旧分析确实尚未进入唯一时间线。

新增离线迁移工具 `scripts/migrate_finance_analysis_history.py`：

- 默认只读 dry-run；`--apply`必须提供 backup 目录，并先用 SQLite backup API 备份 finance 与 memcore 两个数据库并跑 integrity check。
- 事件复用线上 `plugin-event`幂等 source ID；历史分析使用稳定 `finance-history-analysis` source ID。
- 只写 `event(user) -> 历史已投递分析(assistant)`，保留 published/delivered 时间戳；assistant 明确标注“仅代表当时判断，不是当前事实更新”。
- 通过 `MemcoreManager.record_user_turn()` / `record_assistant_turn()`写入，不手改 memcore SQL；新增脱敏 `inspect_turn_source()`区分 missing、已有和跨 namespace 冲突。
- 任一 inspect/write 失败即结构化停止；旧表不删除、不更新，重复执行只报告 already complete。

当前本地缓存/迁移聚焦回归为 128 项通过，相关宿主宽回归为 308 项通过；云端 apply、部署后 cache usage 和最终条数仍需在停服备份后填写。

### 12.4 停服迁移、离线压缩与部署结果

2026-07-17 的 Slice E 维护窗口内，finance 与 personal 共享宿主实例均先保持停止，避免在宿主文件和共享 venv 更新期间出现版本撕裂。部署前备份 ID 为
`slice-e-8a9a15f-9f595b9`；备份包含原宿主运行文件、原插件与 memcore 包、finance 环境文件校验值，以及 finance/memcore 两库通过 SQLite backup API 生成并完成 integrity check 的副本。

- 宿主运行文件已部署到提交 `9f595b9`，finance 插件为 `0.7.11`，memcore 为 `0.1.0`。
- memcore wheel 从干净提交 `2cb929e` 的 archive 构建；没有夹带 memcore 工作区内用户未提交的其它改动。
- PinAI 原生工具 allowlist 使用探针验证过的 `host:model:json` 精确项；本文不记录 host、model、密钥或环境文件正文。
- 停服最终 dry-run 为 eligible/scanned `156/156`，没有 skipped 或 rejection。
- apply 写入 `312` 个 turn，即 `156` 个 event(user) 与 `156` 个历史已投递分析(assistant)；`pairs_completed=156`，没有失败。
- 同步 compaction 完成 `2/2` 个 namespace，新建 `27` 个 summary、`4` 个 semantic summary，并完成 `1` 次 reinforcement；没有 retry pending。
- 第二次不带 compaction 的 apply 幂等复跑报告 `already_complete=156`、`turns_written=0`。
- 维护后只读核对：`plugin-event:* = 156`、`finance-history-analysis:* = 156`、旧 `analysis_history = 156`；finance 与 memcore 两库 `PRAGMA integrity_check` 均为 `ok`。
- 未摘要 raw 总数从迁移后压缩前的 `318` 降为 `138`；旧表、失败 outbox 和投递状态均未删除或伪造。

finance 随后单独启动并通过启动层验收：

- `/health` 返回 `status=ok`、`instance_id=finance`、`root_binding=valid`，10002 仅在 loopback 正常监听。
- PluginHost 为 active，`akane.finance` 0.7.11 active；6 个 capability 与 1 个后台 job 已发布，job 为 running。
- `/metrics` 已出现 final 与 plugin proactive 各自的 cache/read/input/output/call 指标；刚启动且尚无新模型调用时各项为 0，符合冷启动事实。
- 启动时间窗没有 plugin activation、Traceback 或结构化启动失败。

### 12.5 真实事件验收中的既有 outbox 顺序风险

finance 启动后，真实新闻轮询和订阅匹配持续产生新候选，证明 job、新闻源和 durable outbox 已接通；旧 `analysis_history` 仍为 156，没有恢复写入。

第一批新候选尚未进入模型调用，因为现有 outbox 的严格收件人顺序规则出现 head-of-line blocking：一条更早、已有正文的历史通知失败仍在退避期，`list_due_deliveries()` 的 earlier-row 门禁不考虑 earlier row 是否已到 `next_attempt_at`，因此它在不可重试期间仍阻塞同收件人的后续空正文候选。此时 PluginHost job 仍为 active，但精确查询没有可返回的 due delivery，所以 plugin proactive usage 保持 0。

本维护窗口不通过清空失败项、提前修改 `next_attempt_at`、伪造 delivered 或直接调用 pending delivery 来制造验收成功。先观察状态机的自然重试；若要修复这项可用性风险，应作为独立的小切片设计并测试“通知退避、收件人顺序、15 分钟未分析过期”三者的语义，不能为了缓存实测直接放松顺序门禁。

### 12.6 真实结构化缓存验收与 raw/vector 缺口（2026-07-18）

部署 `9044a07 fix(cache): preserve provider message boundaries` 后，使用真实 finance proactive 链路而不是合成请求复测：

- 一次 compaction/high-water pass 将可见 history 从 30 条收敛到 16 条；该次前缀变化造成一次可解释的短暂失配。
- 随后的稳定 native-tool follow-up reported input `31,854`，cached input `29,184`，真实命中率 `91.62%`。
- 该事件的 native schema 发送数为 `6`，实际提取调用数为 `3`，无工具决策 `2`，provider unsupported `0`；模型确实可以在同一通道自主选择 0..N 个工具。
- proactive 聚合值为 cached `30,720` / input `95,264`（约 `32.25%`），因为包含冷启动轮和 compaction 轮；它不能覆盖稳定后续轮的 `91.62%`，也不应被当作单轮命中率。

同一真实快照还发现一个与缓存无关但必须先修的 memcore 边界缺口：

- legacy `plugin-event:*` 已有 `33` 条新事件，但 memcore 仍只有迁移时的 `156` 条；最新 legacy 事件缺失于 memcore，说明推送链路把 `index_in_vector=false` 误当成“不要写 raw”。
- 正确语义是：`index_in_vector=false` 只禁止向量 upsert；SQLite raw 仍必须落库，状态标记为 `skipped`，不进入 pending outbox；metadata 回写、`reindex_pending()` 和 `reindex_all()` 都保留该 opt-out。
- 修复已落在 memcore commit `99a1fa0`，宿主 `MemcoreManager` 不再提前返回 `legacy_index_disabled`，而是把路由标志传给公共 `MemorySystem.record_user_turn()`。相关 memcore 204 项、宿主 memcore 集成 46 项测试通过。

这 33 条已经缺失的历史 user event 不在本切片直接用 SQL 重排或伪造回填；若要补回，必须另做“按真实发生顺序追加、工具/分析配对和当前 prompt 可见性影响”评估，避免为了补数量破坏唯一线性时间线。
