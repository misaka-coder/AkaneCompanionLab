# Akane 主聊天提示词生命周期审查与无损收敛方案

状态：代码探查与真实链路审计完成；Slice 0~3 已在本地实现并通过回归，尚未部署

日期：2026-07-23

## 1. 目标与结论

这次工作的目标不是把 Akane 变成一个“提示词更短但不知道发生了什么”的 Bot，而是保住目前已经很好的对话体验，同时纠正信息的生命周期：

- 模型继续看得到用户消息、引用、附件、图片、工具调用与结果、任务、角色、演出和金融事件；
- 真正发生过的事情继续按时间顺序进入 MemCore；
- 只服务当前请求的检索证据和实时状态不再伪装成永久历史；
- 稳定规则只保留一份；
- 完整材料和工作区状态由事件告诉模型“发生了什么”，需要细节时再由工具读取；
- 优化同时看总输入、cache hit/miss、延迟和回答质量，不用隐藏 token 上限或能力降级换取漂亮比例。

核心判断：当前架构不用推倒，也不需要重构 MemCore。MemCore 的线性时间线、typed event、工具轨迹、provider projection、审计与压缩边界已经能支撑目标。主要问题在 Akane 宿主的 prompt assembly：它把不同生命周期的信息混在了同一条 provider user message 里。

当前 Bot 很聪明并不意外。Akane 现在给了模型大量真实且有用的信息，工具和追问也能沿时间线连续出现，这是正确方向。问题是同一信息经常以“规则、状态快照、工作区展开、历史冻结”多种形式反复出现。应保留信息覆盖，减少重复传输。

## 2. 审查范围与版本事实

本次审查覆盖：

- `companion_v01/engine_services/response_builder.py`
- `companion_v01/prompt_builder.py`
- `companion_v01/llm_runtime.py`
- `companion_v01/engine.py`
- `companion_v01/qq_gateway.py`
- `companion_v01/attachment_inbox.py`
- `companion_v01/task_workspace.py`
- `companion_v01/workspace_files.py`
- `companion_v01/generated_files_cards.py`
- `companion_v01/desktop_context_engine.py`
- `companion_v01/persona_system.py`
- `companion_v01/prompt_blocks.py`
- `companion_v01/prompt_profiles.py`
- `companion_v01/capability_registry.py`
- `companion_v01/engine_services/tool_rounds.py`
- `companion_v01/plugin_host.py`
- `companion_v01/plugin_reasoning.py`
- `companion_v01/memcore_integration/manager.py`
- `memcore` 的 projection、rendering、compaction 与 request audit
- 私有金融插件的稳定研究提示和 `event.finance` 链路

本地 Akane HEAD 为 `0a4987d`。云端运行目录为 `/opt/akane/releases/108bdc4`。`response_builder.py` 与 `qq_gateway.py` 文件哈希一致；`prompt_builder.py`、`engine.py`、`llm_runtime.py` 文件哈希不同。已抽查的 prompt assembly 与 observer 关键逻辑一致，但真实审计数据仍只代表当前云端版本，后续部署前必须重新比较本地构建产物，不能把本地结论直接冒充线上结果。

历史文档 `docs/llm_prompt_cache_optimization_notes.md` 中“70%~75% 已是天然健康上限”和旧金融隔离 prompt scope 的判断已被后续 Unified Timeline V2 与真实 DeepSeek/PinAI 测试超越。本方案不再沿用“为了主动推送另建轻量上下文”的旧路线。普通消息、主动事件、工具结果继续共享一条 MemCore 时间线。

## 3. 统一生命周期分类

不按 QQ、金融、附件、礼物等业务逐一硬编码，而按信息生命周期分四类：

| 类别 | 定义 | 典型内容 | 进入 MemCore | 每轮重发 |
|---|---|---|---|---|
| `stable_prefix` | 在配置、角色包或能力版本不变时逐字稳定的规则 | 身份、输出契约、通用工具原则、金融研究方法、客户端固定规则 | 否 | 是，但只保留一份并稳定在前缀 |
| `timeline_event` | 真正发生过、后续对话可能需要知道的事实或状态变化 | 用户消息、引用消息、附件上传、任务变化、换装、金融事件、工具调用与结果 | 是 | 由 MemCore 线性历史自然可见，不额外重复展开 |
| `live_state` | 只描述当前请求或当前瞬间，过后不应成为历史事实 | 当前窗口、播放进度、临时投递模式、实时饥饿值、本轮检索证据 | 否 | 需要时以短结构块放在时间线尾部 |
| `tool_storage` | 完整内容或可变资源存储，模型按需发现和读取 | 附件全文、文件内容、任务完整步骤、资源目录、能力详细 schema | 只保存引用、调用和结果，不复制底层存储 | 不展开；用工具读取后，调用与结果进入时间线 |

这四类是默认判断，不是限制业务表达的死规则。一个对象可以同时具有不同投影：例如附件上传是 `timeline_event`，附件正文属于 `tool_storage`，本轮直接附给视觉模型的原图是当轮 provider 输入。

## 4. 当前真实 provider 请求顺序

### 4.1 代码顺序

主调用链：

```text
response_builder.prepare_context()
  -> PromptBuilder.build_final_generation_context()
  -> LLMRuntime._build_completion_kwargs()
  -> Engine request observer
  -> MemCore record_request_projection()
```

当前逻辑请求顺序为：

```text
system
  base system / mode output contract
  registered stable blocks
  plugin stable block（金融研究方法）
  domain profile
  resource context

history messages
  stable user intro + tool context
  persona runtime context
  relationship / client mode 等 stable extra
  semantic + episodic memory projection
  MemCore raw / event / tool timeline

current user tail
  当前时间线消息
  retrieval snippets（发生检索时）
  task workspace
  workspace files
  attachment focus
  generated files
  pending gifts / gift observation
  QQ / desktop / care / activity extra context
  current visual state
  current images

post-user turns
  当前轮后续 native/legacy tool use 与 tool result
```

native tool schema 是 provider 请求的独立字段。不同 provider 的缓存键顺序不同，但共同原则不变：位于时间线之前的动态 schema、persona 或资源块一旦变化，会让后面整段历史无法沿旧前缀命中。

### 4.2 当前轮冻结问题

`response_builder._build_memcore_provider_history()` 会从 MemCore projection 中分离当前 turn，只把更早历史传入 `history_turns`。当前用户消息随后由 `PromptBuilder` 和所有 volatile context 合并成一条 user message。

`Engine._build_memcore_request_observer()` 第一次观察真实 provider 请求时，从 `history_messages` 尾部取出当前 turn 对应条数，并把实际尾消息作为该 turn 的 provider projection 冻结。

因此当前冻结的不是“纯用户/事件消息”，而可能是：

```text
当前消息
+ 本轮检索片段
+ 当前任务工作区
+ 当前附件工作台
+ QQ/桌面说明
+ 当前演出快照
```

这解释了为何缓存账面比例可以很高、Bot 也一直知道上下文，但总输入仍持续膨胀：旧动态块已经变成了可缓存历史，新一轮又继续追加新的动态块。

`audit_history_messages` 已经可以保存完整真实 provider 请求用于审计；`history_messages` 也已经是单独参数。缺口不是 MemCore 没能力，而是宿主目前把两者传成了同一个语义边界。

## 5. 完整分类结果

### 5.1 系统与输出契约

| 来源 | 当前情况 | 目标分类与处理 |
|---|---|---|
| `prompt_blocks.py` | JSON、字段顺序、气泡、工具、记忆元数据、时间感、客户端规则 | `stable_prefix`，作为唯一权威规则 |
| `prompt_profiles.py` | 再次描述字段清单、顺序、`reply_medium` 和 JSON 示例 | 合并进稳定输出契约；保留模式差异，删除语义重复 |
| `qq_gateway.build_extra_context()` | 每轮再次说明 QQ 模式、语音、提前投递、媒体委派 | 固定规则迁入 `stable_prefix`；只留下极短投递 live flag |
| `engine._build_tool_prompt_context()` | 决策原则、能力状态、每个 legacy handler 说明 | 稳定原则与动态清单拆开；native schema 已有的信息不重复长篇描述 |
| native tool round instruction | 每轮列出全部 native 工具名并重复调用规则 | 保留简洁稳定规则；具体 schema 由 provider tools 字段负责 |
| 金融插件 `FINANCE_RESEARCH_SYSTEM_PROMPT` | 研究方法通过 restart-only plugin block 注册 | 正确的 `stable_prefix`，继续保留；不挪回每条新闻尾部 |
| `domain_profiles.py` | 当前 registry 永远返回 default，prompt 为空 | 当前无模型可见作用；不要为它再造第二套金融提示 |

当前最明显的规则重复包括：

- `tool_call` / native tool_calls 的调用与“不得假装完成”规则在 system block、tool context、native round instruction、QQ context 多次出现；
- QQ 不渲染场景、立绘、BGM 的事实在 prompt block、resource context、client mode 和 QQ extra context 多次出现；
- 字段顺序和 JSON 输出示例在 system blocks 与 mode prompt 中重复；
- 媒体处理应交后台任务的规则同时出现在能力提示、handler instruction 与 QQ extra context；
- 角色表情/服装资源同时出现在角色包 persona、resource context 和 current visual context。

收敛原则不是机械去重字符串，而是为每条规则指定唯一权威层。具体工具参数仍由 schema/handler 提供，不能为了短而删掉模型真正需要的调用说明。

### 5.2 Persona 与角色资源

角色包 context 当前混合了：

- 稳定身份、称呼、关系、`persona.md`；
- 角色资料库使用规则和目录；
- 当前服装与当前可用 emotion；
- 根据本轮用户文本自动加载的角色资料正文；
- 本地点击台词风格等参考材料。

整块目前位于 MemCore 历史之前。尤其 `build_automatic_context(character_pack_id, user_message)` 会随本轮问题变化，却作为 persona reference 放在整个时间线前面。

目标拆分：

```text
稳定身份 / persona / 表达原则       -> stable_prefix
角色资料库目录与加载方法             -> stable_prefix 或 tool_storage 目录
角色包/资源版本变化                  -> versioned stable block + timeline_event
真实换装 / 侧面切换                  -> timeline_event
本轮自动命中的角色资料正文           -> ephemeral evidence 或标准工具结果
当前可用 emotion / outfit 快照        -> 短版本化资源块，不重复完整 persona
```

不能删除自动角色资料能力；只改变它进入请求的位置，避免一个关键词让全部历史缓存失效。

### 5.3 MemCore 原始对话、事件、摘要与检索

以下设计应保留：

- user / assistant / `event.*` / tool use / tool result 线性追加；
- `event.finance` 使用中性事件名，不伪装成高权限 system event；
- 工具轨迹默认检索前置过滤，但显式 kind 查询仍可读取；
- material trace 默认显式可见，不污染普通语义检索；
- summary、semantic 与 raw 的 lineage、压缩和 namespace 边界；
- provider projection 的 append-only 与 request audit。

MemCore 当前 provider projection 顺序是：长期语义摘要、阶段摘要、未压缩时间线。摘要只在 compaction/强化时变化，变化时允许造成一次真实前缀变化；不能为追求缓存让摘要停止更新。

`retrieval snippets` 属于本轮证据，不属于新的历史事实。目前它们会与当前 user message 合并并被 observer 冻结。目标是：

- 保持检索片段紧邻当前问题，模型仍能理解为何检索；
- 片段只用于本轮 provider 请求，不写进当前消息 projection；
- 命中的原始条目本身仍在 MemCore，由 source id、时间线工具和检索工具管理；
- 完整 provider 请求继续进入安全 audit，不丢失诊断能力。

### 5.4 QQ 当前消息、引用与客户端状态

QQ 引用链路方向正确：`routes/qq.py` 已把引用正文、引用者、时间与当前追问渲染为一条 `qq.reply_reference` 当前消息，并能标记引用的 Bot 回复为 `assistant_self`。这条消息整体属于 `timeline_event`，不能把引用证据拆成孤立的临时块。

`qq_gateway.build_extra_context()` 当前每轮重复发送者 QQ、昵称、群号、群聊昵称说明、客户端说明、回复模式、语音规则、媒体委派和临时模型名。目标处理：

- 发送者归因由当前消息 Actor 和时间线渲染负责；
- 群号/会话归属由 namespace 负责，不需要反复教模型；
- QQ 固定表现规则进入稳定 system；
- 当前投递方式只保留如 `reply_delivery: auto|text|voice|both` 的短 live state；
- 临时模型名不进入模型提示；
- 投递失败、动作完成等真正发生的事用一次性事件或工具结果表达。

QQ profile 不启用 `CURRENT_VISUAL_STATE`，但当前代码仍生成“当前客户端模式不需要完整演出状态”的占位文本并包装进当前尾部。这个占位应直接为空。删除它不会降低任何表现。

### 5.5 附件、图片、文件与材料

附件已有正确基础：`MemcoreManager._record_material_event()` 会写入 `material.reference` / `material.cleanup`，包含安全的 file id、kind、filename、mime、文件状态、派生状态、Actor 与时间，不包含本地绝对路径或存储路径。

当前 `AttachmentInboxService.build_prompt_context()` 每轮仍展开最新图片、focused items、manifest、pending/failed、操作说明和内容预览。目标闭环：

```text
上传发生
  -> material.reference 追加一次
  -> 本轮若 provider 支持识图，原图直接附给当前请求
  -> 模型需要细节时调用 inspect/load 工具
  -> tool use / tool result 追加到 MemCore
  -> 过若干轮被压缩后，模型仍可通过材料工具重新加载
```

每轮最多保留短活动索引，例如最新/聚焦 handle 和状态；完整预览不再反复重发。

体验护栏：本轮刚上传的图片继续直接识图，不能为了结构纯洁强迫模型多调用一次工具；历史图片和文件则通过 handle/material trace 重新定位。

`generated_files_cards.py` 已按关键词条件注入，比附件工作台更克制。生成完成一般已有工具结果，后续应以生成事件、handle 和工具读取为主。

`workspace_files.py` 对桌宠可注入最近 20 个文件、focused 全文和修改时间，单文件上限很大。这是高风险动态块。固定路径与安全规则进稳定前缀；新增/移动/删除成为事件；当前 focus 只给短索引；正文只经工具读取。

### 5.6 Task workspace

当前 task workspace 每轮展开目标、后台状态、步骤、产物、handoff、pending question、最近事件和操作规则。任务工具调用与结果已进入 MemCore，但任务数据库状态变化本身还没有通用 MemCore 事件闭环，因此不能直接删除工作区提示。

先补事件：

```text
event.task.created
event.task.updated
event.task.waiting
event.task.completed
event.task.cleaned
```

事件字段保持通用、结构化，不把某个具体工作流硬编码到 MemCore。状态变化只追加一次。每轮只保留短活动任务卡；完整步骤、handoff 和产物列表由 `manage_task_workspace`/inspect 工具读取。

### 5.7 礼物、场景、服装与视觉观察

`build_pending_prompt_context()` 每轮列出最多三件礼物、当前聚焦礼物，并重复 `check_inventory`、`manage_gift`、`manage_artifact` 的操作说明。视觉服务还会把 scene/outfit/gift observation card 每轮拼进当前视觉尾部。

目标处理：

- 礼物收到、保留、使用、清理是 `timeline_event`；
- 当前聚焦礼物是短 live index；
- 完整库存和操作参数由工具提供；
- 礼物视觉观察第一次 ready 时追加一次 observation/event，后续不重复整卡；
- 场景/服装 observation 与资源指纹绑定，切换时成为事件；
- 当前正在渲染的表情、窗口状态等只作为短 live state；
- 工具规则只在稳定提示或 schema 保留一份。

这不是给“戳一戳”“换装”“礼物”逐一开后门。MemCore 已支持通用 typed event；哪些事件参与默认检索仍由通用 metadata、visibility 和前置过滤决定。

### 5.8 Care、桌面、音乐与实时演出

以下是 `live_state`：

- 前台窗口与当前桌面活动；
- 剪贴板；
- 当前饥饿、精力、好感数值；
- 当前音乐、播放进度、歌词位置；
- screen vision；
- 当前桌宠渲染快照。

固定解释（数值含义、activity 格式、不得假装播放成功、客户端渲染能力）进入稳定前缀。真实变化（关系 tier 跨越、开始/停止播放、换歌、换装）追加事件。高频进度只在本轮必要时提供，不进入摘要或长期记忆。

### 5.9 工具轮与按需能力加载

当前多工具主链的方向正确：

- provider native tool calls 支持同轮并行；
- action 与 observation 有 correlation id；
- tool use / tool result 由 `record_tool_batch()` 写入当前 MemCore turn；
- 当前轮 `post_user_turns` 使用 MemCore provider projection 恢复真实结构；
- error/cancelled 结果也能闭合 branch；
- 普通检索默认排除工具 trace。

后续可以支持“少量常驻发现工具 + 按需加载具体工具描述”，但不能强制所有宿主采用这一模式。宿主工具少且稳定时，完整 schema 常驻仍合理。MemCore 只需要支持请求、结果和说明以结构化条目线性追加，不替宿主决定必须暴露几个工具。

当前工具集合会随附件、文件、本地能力在线状态和 provider 能力变化。应区分：

```text
稳定核心工具与通用调用原则  -> stable_prefix / provider schema
能力可用性变化              -> 短 live state 或 timeline event
延迟加载能力目录            -> 稳定发现工具
具体工具 schema             -> 常驻或按需加载，由宿主选择
调用与结果                  -> MemCore timeline
```

## 6. 真实云端审计结果

审计只读取 section 长度、估算 token、hash、消息指纹和 provider usage，没有读取或输出对话正文、密钥、路径或图片。

### 6.1 个人 Bot 主会话桶

2026-07-23 当前主桶：83 条 prompt、75 条 usage。

- 最新审计请求估算约 50,035 tokens；
- 最近 12 条 provider usage 的 cache hit ratio：最低 51.64%，中位 92.86%，最高 97.07%；
- 最近 12 条 reported input 中位约 36,873 tokens；
- `user.tool_context` 稳定约 6,486 tokens；
- `user.runtime_context` 稳定约 4,232 tokens；
- `user.persona_reference_context` 约 2,620 tokens；
- `user.volatile_extra_context` 约 2,810~2,975 tokens；
- 其中 task workspace 约 1,406 tokens；
- attachment focus 约 685 tokens；
- QQ/turn extra context 约 718~884 tokens。

最近相邻请求的 message common prefix 多数恰好覆盖上一请求的全部历史，并随新消息增长。这证明 MemCore 线性 provider history 已经工作；问题不是“所有提示每轮随机重排”，而是被追加进去的每轮内容过重。

### 6.2 金融 Bot 主会话桶

2026-07-23 当前主桶：395 条 prompt、347 条 usage。

- 最新审计请求估算约 49,483 tokens；
- 最近 12 条 cache hit ratio：最低 13.72%，中位 94.50%，最高 95.44%；
- 最近 12 条 reported input 中位约 43,596 tokens；
- `user.tool_context` 稳定约 6,899 tokens；
- `user.runtime_context` 约 3,511 tokens；
- `user.persona_reference_context` 约 2,546 tokens；
- `user.volatile_extra_context` 约 1,166~1,386 tokens；
- attachment focus 约 1,153 tokens；
- 当前消息约 16~138 tokens。

金融的稳定中位命中已经接近 95%，但低命中离群值仍存在，总输入也很大。不能因为命中比例高就停止治理。缓存命中的旧冗余仍会消耗上下文窗口、注意力、传输和 provider cached-input 成本。

### 6.3 数据解释

审计估算 token 与 provider reported input 使用不同 tokenizer 且采样请求不同，不能直接相减。这里可靠的结论是趋势：

1. 稳定前缀与线性历史大部分时间可复用；
2. 每轮动态尾部仍达到约 1K~3K tokens；
3. 这些动态尾部被冻进历史后，使总输入进入 3.7万~4.4万 reported-token 量级；
4. 高命中不等于低成本，也不等于上下文干净；
5. 偶发 7%~15% 级低命中仍需在改造后的 compaction、工具集合变化、角色/资源变化和服务重启场景分别定位，不能先验归因给 provider。

## 7. 目标 PromptAssembly

### 7.1 目标顺序

```text
provider tool schema（稳定集合或可复用版本）
stable system prefix
stable persona / output / domain rules
MemCore summary + raw/event/tool history
ephemeral evidence / live state（仅本轮）
current persistent user/event message
current images（仅本轮 provider media）
post-user tool turns（同一 open turn）
```

关键点是让当前持久消息成为可识别的独立 provider message，不能继续和 ephemeral evidence 拼成一条不可拆的 user 文本。对于引用消息，引用与当前追问本身仍是一条完整 persistent message。

若 provider 对连续同 role 消息有特殊合并行为，adapter 必须按真实 wire shape 记录并验证。不能假设 Chat Completions、Responses 与 Anthropic Messages 的行为完全相同。

### 7.2 projection 与 audit 的新边界

建议宿主内部明确三份数据：

```python
persistent_turn_messages   # 本轮以后仍应出现在时间线里的消息
ephemeral_request_messages # 检索证据和 live state，只服务当前请求
audit_history_messages     # 实际发送给 provider 的完整安全审计形状
```

request observer 不再用“取实际请求最后 N 条”猜持久消息，而由 PromptAssembly 提供明确的 provider message span/slot；observer 校验这些 slot 与实际 wire request 一致后，只把 persistent turn 写入 `history_messages`，完整请求仍写入 `audit_history_messages`。

必须继续保留：

- source id 覆盖校验；
- provider role/schema 校验；
- append-only 与 projection immutable 校验；
- Responses/Chat/Anthropic 的真实 wire audit；
- 图片/base64/path/key 的审计清洗；
- tool branch 的原子关系。

不能通过放松 MemCore projection 校验来绕过迁移错误。

### 7.3 通用事件与短状态格式

事件沿用 MemCore typed event，不要求穷举所有业务：

```text
[2026-07-23 21:34] event.task.updated
actor: ...
ref: task:...
status: waiting
summary: 等待用户选择输出格式
```

本轮短状态保持机器清晰、人也容易读：

```text
context.live
client: qq
reply_delivery: auto
focused_material: attachment:img_103
active_task: task:abc (running)
```

这里不使用“系统事件”这种容易越权的叫法，也不把所有事件叫“插件事件”。字段允许不同领域扩展；MemCore 只要求安全 kind、payload 与 renderer contract。

## 8. 实施切片

每轮只做一个可验证切片。先修边界，再缩内容；这样即使中途停止，也不会先丢体验。

### Slice 0：锁定基线

- 为 PromptBuilder、request observer 与真实 provider shape 增加无正文 fingerprint fixture；
- 记录 personal private/group、finance group/proactive 的 section hash、input、hit/miss、message common prefix；
- 建立体验样本：普通追问、引用自己、引用别人、图片、语音、单工具、并行工具、工具后追问、金融事件。

不改 prompt 行为。

### Slice 1：修正 persistent / ephemeral projection 边界

主要文件：

- `companion_v01/prompt_builder.py`
- `companion_v01/engine_services/response_builder.py`
- `companion_v01/engine.py`
- `companion_v01/llm_runtime.py`
- `tests/test_prompt_builder.py`
- `tests/test_memcore_integration.py`
- `tests/test_llm_client.py`

动作：

- 当前 user/event 使用 MemCore renderer 的独立 provider message；
- retrieval、volatile context 与 live visual 成为独立 ephemeral message；
- observer 使用显式 slot，不再用尾部猜测；
- `history_messages` 只记录 persistent turn；
- `audit_history_messages` 保留完整真实请求；
- post-user tool history 继续按当前 MemCore projection；
- 同步与流式路径一起改。

这是最高收益、也最不应改变模型可见信息的第一刀。

### Slice 2：删除纯占位并统一 QQ 固定规则

- QQ 未启用 visual 时返回空，不发送“当前客户端不需要完整演出状态”；
- QQ 固定规则移入唯一 system block；
- 当前 reply delivery 保留短 live state；
- 删除临时模型名、重复 sender/group 说明；
- 保留 Actor、时间戳、群成员归因和引用一体化。

### Slice 3：附件事件闭环与短活动索引

- 复用已有 `material.reference/cleanup`；
- 当前上传原图继续直接给视觉 provider；
- attachment focus 缩为 handle/status 索引；
- 完整详情通过现有 inspect/load 工具；
- 工具调用与结果继续进入 MemCore；
- 验证旧附件被压缩后仍能重新定位。

### Slice 4：任务状态事件化

- 给 TaskWorkspaceService 的 create/update/waiting/complete/cleaned 写通用 MemCore event adapter；
- 每次状态变化只写一次；
- prompt 只保留短 active task card；
- 完整步骤、handoff、pending question 与产物由工具 inspect；
- 事件写失败必须返回 status/reason，不能静默假成功。

### Slice 5：Persona 与资源版本拆分

- 稳定身份/persona 与动态自动资料分开；
- automatic character context 移到时间线尾部或标准工具结果；
- 当前 outfit/emotion 通过版本化资源块与真实换装事件表达；
- 不删除角色资料、表情映射或 persona side；
- 切角色、换装、QQ 引用 Bot 自己时做体验验收。

### Slice 6：桌面/care/music/gift/scene 的 state/event 拆分

- 固定说明去重到 system；
- 高频状态只保留短 live values；
- 开始播放、换歌、换装、礼物处理、关系 tier 变化追加事件；
- observation card ready 后只追加一次；
- 验证文字气泡、表情、动作、TTS、音乐不互相打架。

### Slice 7：工具提示治理与可选按需加载

- 统计 native schema 与 legacy instruction 的重复；
- 稳定核心工具保持常驻；
- 状态易变或低频工具可由稳定发现工具按需加载；
- 加载结果、具体调用和结果追加到 MemCore；
- 不固定工具数量，不强制并行数量，不为某个金融例子硬路由；
- 工具少且稳定的宿主允许继续完整常驻。

### Slice 8：真实部署验收

本地自动化通过后再部署。分别测试：

1. 个人 Bot 私聊连续多轮；
2. 个人 Bot 白名单群连续多轮；
3. 引用 Bot 自己的分段回复继续追问；
4. 引用其他群成员；
5. 当前图片直接识图；
6. 历史图片/文件重新加载；
7. QQ 文字/语音/双发与 GPT-SoVITS；
8. 单工具与并行多工具；
9. 工具结果后继续追问；
10. active task 更新和恢复；
11. 连续至少三条模拟 `event.finance` 主动事件；
12. 金融群普通聊天与主动推送交替；
13. compaction 前后；
14. 服务重启、工具在线状态变化、角色/资源版本变化。

## 9. 验收指标

缓存命中不是唯一目标，也不预设一个隐藏 token 限制。每个切片同时记录：

- provider reported input tokens；
- cached/hit 与 miss/creation tokens；
- cache hit ratio；
- prompt section tokens/hash；
- 相邻请求 message common prefix；
- dynamic tail 大小；
- 首 token 与最终完成延迟；
- 工具选择、参数、并行与追问正确性；
- 引用、附件、图片、文件和任务恢复正确性；
- 角色语气、气泡、表情、TTS、音乐与文件交付的真实表现；
- compaction 前后的信息覆盖与一次性 cache disruption。

成功标准是：模型看到的信息不减少，但相同状态不再每轮重复；ephemeral evidence 不进入下一轮 persistent history；总输入和 miss 绝对量下降；稳定普通对话与主动事件继续共享同一线性前缀。

不把单轮 95% 当作完成证据，也不把一次 compaction/restart 后的低命中当作永久失败。至少看同一真实入口的连续稳态样本，并记录造成前缀变化的明确原因。

## 10. 明确禁止

- 不为个人/金融/新 QQ Bot 复制 prompt builder 或 Engine；
- 不给金融主动推送另建 memory bucket 或第二历史区；
- 不把 event 伪装成高权限 system instruction；
- 不删除当前上传图片的直接视觉输入；
- 不让引用正文与当前追问分离；
- 不把 live state 送入摘要或长期记忆；
- 不把 retrieval snippets 冻结成用户历史；
- 不用低 token 上限裁掉上下文来伪装优化；
- 不固定工具数量、来源数量、并行数量或金融分析栏目；
- 不全局放松 MemCore append-only、namespace、visibility 或安全前置过滤；
- 不保留新旧两套长期并行的 prompt 权威；
- 不把 fake action、未接通能力或占位状态写进 prompt；
- 不只看字段存在，必须检查用户真的能看到、听到、收到和继续追问。

## 11. 下一步建议

先实施 Slice 0 + Slice 1，不先动附件、任务、Persona 具体内容。第一刀只把“模型本轮能看到的完整请求”和“以后应永久留在 MemCore 的消息”分开，理论上不减少任何信息，却能阻止新冗余继续冻结增长。

Slice 1 稳定后，再按 QQ 占位、附件、任务、Persona 的顺序逐层缩短。这样每一步都能用真实对话体验验证，不需要一次大改，也不会把问题归咎于 MemCore 或 provider。

## 12. Slice 0/1 本地实现记录

2026-07-23 已完成第一条运行时边界：

- `PromptBuilder.user_prompt` 只保留当前 user/event 的结构化文本；当前上传图片仍附在这条消息上；
- retrieval snippets、volatile host context、current visual state 和必要的 fallback current time 进入独立 `ephemeral_turns`；
- provider 顺序固定为 `system -> history -> current user/images -> ephemeral -> tool continuations`；
- `LLMRuntime` 显式提供 `persistent_turn_messages`，内容是当前 user 加本轮已形成的 tool continuation，不再让 Engine 从完整请求尾部猜测；
- MemCore `history_messages` 只接收上述持久消息，`audit_history_messages` 继续接收完整真实 Chat/Responses wire；
- observer 缺失显式持久槽、数量不一致、role 非法或 source id 缺失时结构化拒绝，不回退到旧 suffix 推断；
- prompt token 估算与安全审计均计入 ephemeral 内容，但审计只保留长度/hash，不记录正文、图片 base64、路径或密钥。

本地验证覆盖普通/主动事件一致布局、检索与演出仍可见、当前图片位置、legacy/native/并行工具、工具加载图片、Responses wire、observer retry/rejection，以及下一轮 provider projection 不含 ephemeral 状态。此记录只代表本地代码；真实缓存、QQ 投递和视觉/TTS 表现仍需后续部署切片验收。

## 13. Slice 2 本地实现记录

QQ 每轮上下文已从一整段固定说明收敛为单行 live state：`qq.reply_delivery: auto|text|voice|both`。发送者 QQ、群号、客户端类型、临时模型名、语音写法、提前投递和媒体委派规则不再随每条消息重复传输：

- 发送者与群成员归属继续由当前消息的 `【昵称】`、Actor 元数据、时间戳和引用一体化结构表达；
- 戳一戳等有实质意义的本轮事件仍保留明确事件归属，不依赖被删除的通用 QQ 号快照；
- reply delivery、语音友好写法、提前投递和条件式 `delegate_task` 使用规则进入唯一的稳定 QQ system block；
- 临时模型 override 仍由宿主真实路由执行，但不再告诉模型“自己正在使用哪个模型”；
- QQ profile 未启用 current visual 时直接返回空，不再发送纯占位句；
- QQ 角色资源块只描述真实的 emotion 清单状态，不再重复“不渲染立绘”的固定规则。

这一切片不改变消息准入、群聊唤醒、引用正文、识图开关、reply mode 后端强制、模型切换命令、TTS 或文件交付实现。真实 QQ/TTS 表现仍待部署验收。

## 14. Slice 3 本地实现记录

附件完整工作台与主聊天活动索引已拆成两个明确消费者接口：

- `build_prompt_context()` 继续保留图片观察卡、媒体规格、文件正文/预览和 Manifest，后台 task worker 仍使用这个完整接口；
- 新增 `build_activity_prompt_context()`，主聊天只获得 `attachment.workspace` 下的 handle、kind、status、focus 和安全短名称；
- 活动索引不读取或渲染视觉描述、文件正文、short hint、media detail、`storage_relpath` 或本地绝对路径；
- 活动索引读取当前会话全部 active entries，不用固定条目数静默隐藏较早 handle；
- pending 与 failed 状态仍明确可见，失败原因只经过现有安全、可读错误映射，不把底层异常路径交给模型；
- `material.reference/cleanup`、`inspect_attachment`、`load_material`、`read_attachment_section`、workspace focus 与清理逻辑均未另建第二实现；
- 当前 QQ 图片仍由 `build_native_image_inputs()` 直接附给本轮视觉 provider，没有被迫先调用工具；
- 后台任务继续通过 `_build_task_worker_attachment_context()` 获得完整材料，没有因主聊天缩短而退化。

本地验证覆盖 65 个以上活跃材料不被短索引截断、ready/pending/failed 状态、安全路径与正文不泄露、完整资源可见性契约、附件工具、后台 task worker，以及 QQ 当前/引用图片直接进入原生多模态请求。真实 QQ 识图、历史材料重新加载、input token 与 cache miss 变化仍待统一部署后验收。
