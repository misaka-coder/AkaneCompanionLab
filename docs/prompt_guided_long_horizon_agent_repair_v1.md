# Akane 提示词导向的长程 Agent 修复设计 v1

> 2026-09-11 工具暴露修订：本文 MCP 历史复用部分属于早期方案。当前要求有效的作用域内 `contract_ref`，按需契约通过 `capability_load` 的真实工具结果披露、经 `capability_invoke` 进入同一执行链；`load_mcp` 不再扩充原生 schema，历史原生名称不再授予执行资格。原生声明、代内更新及压缩边界以 [统一暴露实施记录](tool_exposure_memcore_implementation_20260910.md) 为准。


> 状态：2026-08-29 已确认并进入 repair pass；代码切片逐项测试、逐项提交，部署另行验收。
>
> 适用范围：Akane 主工具循环、长任务预算与续作、MemCore 开放回合/结算/检索/投影，以及 QQ 最终文字的稳定表达约束。
>
> 参考快照：DeepSeek Harness `f116b7c20f`，OpenCode `38e10eb`。参考项目提供实现证据，不是 Akane 的上位规范；只吸收更适合 Akane 的部分。

## 0. 实施进度

- `ba60463`：保留所有 Provider 工具调用；malformed 参数、未知工具和权限失败进入普通结构化工具结果；删除三次失败后关闭工具与单响应 16 调用静默截断。
- `07b5d3c`：删除按中文短句猜测“占位回复”的语义黑名单，改由 `error/fallback_used` 等真实来源判定宿主 fallback；流式与非流式 `finish_reason=length` 均成为明确截断；Provider 意外拒绝原生工具时不再中途静默删 schema；MemCore 请求投影已验证绑定后，单纯记录写入失败只标记降级，不再取消模型回复。
- `a0d73e4`：删除退役工具重试配置及文档残留，明确 Gemini 流内去重只处理同一响应的传输重复，不参与跨工具轮决策。
- 硬预算主路径经代码与测试复核：最后一批工具先执行并记录，随后立即以 `allow_tool_call=False` 请求交付；模型若仍违反无工具协议，才进入有界协议反馈，绝不执行额外批次。

## 1. 结论先行

今天暴露的长任务“执行到一半静默，数分钟后未交付”不能统一归因于 48 轮预算或模型能力。较早的 22/21 批失败暴露了重复调用阻断；该阻断已在 `9f13636` 删除。最新 87 群 14:52 回合只执行约 11 个工具批次，最后工具结果正常返回，随后三次最终展现解析和一次纯文本恢复均失败，属于独立的最终交付协议故障。二者表现相同但根因不同，后续必须以第一原因分类，不能继续用 `assistant_turn_not_persisted` 概括。

这条行为违反现有 `docs/engineering_invariants_v1.md` 的 INV-2：合法工具调用应执行并继续，只有明确的硬预算事件才可关闭工具。修复不能再增加一个“允许重复的特殊工具清单”或新的“终稿阶段”，而应删除错误抽象，恢复一个由真实模型决策和真实工具结果驱动的简单循环。

本次复审还确认了数条独立风险：Provider 解析层丢失 malformed 参数的 `parse_error/raw`；单次响应超过 16 个原生工具调用时静默截断；MemCore projection/observer 被宿主当成回合生杀条件；流式截断和 Provider 能力降级缺少稳定终态。它们不能混成一个“增加重试”的补丁，必须分别修复并在统一状态机汇合。

最终目标不是让系统更会判断模型，而是让系统更可靠地为模型提供：稳定提示、真实能力、完整结果、清晰失败、可配置预算、持久轨迹和可恢复上下文。任务是否完成、下一步该做什么，仍由模型根据证据决定。

执行期间额外遵守十条不变量：每个工具调用都有结果；工具失败不夺走工具；参数错误保留原始意图；并发宽度不删除调用；只有硬预算正常关闭工具；MemCore 故障不抹去已发生事实；QQ 展现不否决合法工具行为；提前结束必须给出真实原因和出路；动态提示只在需要决策的尾部出现；不新增无法解释必要性的阶段、计数器或语义拦截。

## 2. 设计原则

### 2.1 提示词导向，不是宿主代替模型决策

系统负责机械事实，模型负责语义决策。

| 系统负责 | 模型负责 |
|---|---|
| 暴露本轮真实可用的工具和 schema | 判断是否还需要调用工具 |
| 校验调用协议、参数、权限和执行目标 | 选择工具、参数、顺序和替代方案 |
| 执行调用并返回真实结果或结构化失败 | 根据结果判断任务是否完成 |
| 记录 call/result、预算、状态和持久轨迹 | 在预算接近上限时决定是否留下续作文件 |
| 执行明确的硬预算和安全边界 | 撰写续作文件的真实内容和最终汇报 |
| 保证 MemCore 投影、结算、索引可恢复 | 决定何时打开卡片、读取文件或继续行动 |

系统不得通过“看起来重复”“像是终稿”“没有 speech”“已经重试三次”等启发式判断任务应当结束。提示词应解释真实运行机制及其原因，使模型能主动配合；不能只给一句缺少后果说明的命令，更不能在模型看不见的地方改变工具权限。

### 2.2 不新增语义后门

不新增 `continuation` 最终字段，不新增能绕过 MemCore 结算规则的“总结工具”，不要求模型记录它当前看不见的 `source_id` 或尚未产生的 `memory_id`。跨回合续作使用普通、真实、可检查的项目文件；该文件由现有文件工具创建，工具调用与结果照常进入 MemCore 轨迹。

`speech` 是给用户看的表达，不是长任务状态数据库。模型可以在最终回复中概括进度，但下一轮可靠续作不能只依赖一段自然语言 speech。

### 2.3 少状态、单权威、失败可见

主循环只承认四类模型决策：合法工具调用、合法交付、协议错误、明确的外部失败。不存在额外的“终稿阶段”。工具循环、MemCore、输出协议之间通过已记录的事实衔接，不各自猜测同一件事。

任何无法继续的状态必须结构化记录并对相应对象可见：工具失败给模型，Provider/协议彻底失败给客户端，索引部分失败给运行状态和日志。不得把失败伪装成默认表情、空 speech、正常 settlement 或“模型已经回答”。

## 3. 真实问题证据

### 3.1 长任务未交付

| 场景 | 实际工具批次 | 最后已知状态 | 随后发生的事 | 结论 |
|---|---:|---|---|---|
| 87 群，`turn:25820370ce439f419a18ff73afa25c25`，14:40 | 22 | `exec_status` 明确表示进程仍在运行，应继续轮询 | Provider 请求从有工具 schema 变为无工具 schema；3 次结构化恢复加 1 次纯文本恢复后无交付 | 未达到 48；宿主提前关闭工具 |
| 私聊 `master`，`turn:c83afa91042678e07df52cde9378d3a1`，17:54 | 21 | MCP 重启后应重新加载 schema 并复测 | 同样进入无工具恢复，最终 `assistant_turn_not_persisted` | 未达到 48；重复的 `load_mcp` 很可能被误判 |
| 87 群，`turn:03ebbaa38ade96522ecf2a0609c203a0`，14:23 | 49 个调用，含并行 | 长任务仍在执行 | 工具 schema 在后续请求中被清空并未交付 | 调用数不等于实际批次；不能据此声称命中 48 批 |

当前实现的触发链位于 `companion_v01/engine.py`：

1. `seen_tool_calls` 在整个用户回合内记住 `(工具, 参数)` 签名；
2. 除少数 producer 明确授予的 continuation 外，完全相同的调用不执行；
3. 没有可执行调用时增加 `invalid_tool_decision_attempts`；
4. 达到 `TOOL_DECISION_RETRY_LIMIT` 后使用 `allow_tool_call=False`，并设置 `tool_decision_invalid`；
5. 模型仍在工作的工具决策被误送进终稿恢复。

该路径由提交 `5e80297` 引入。它把“避免模型无效循环”和“结束当前工具阶段”耦合在一起，是当前最强根因，不是一个等待时间或 QQ 消息队列问题。现有日志没有持久化被拒绝调用的完整决策，因此无法逐字证明最后一次被拦截的签名；这也是 Slice A 必须补齐的可观测性缺口，不能用推断冒充原始证据。

### 3.2 MemCore 摘要存在但检索不到

上午 blue 相关约定的摘要实际存在于 SQLite，原始消息也已标记 `is_summarized=1`；Akane 所称“没有生成摘要”不成立。真实故障是内存索引预热被一条 MCP kind 中断：

- `memcore/index/metadata_filters.py` 的 `_KIND_PATTERN` 接受 `github-mcp` 这类含连字符段；
- 同文件的 `kind_filter_key()` 又要求每段满足 `_SAFE_KEY=[A-Za-z0-9_]+`；
- `kind_prefixes()` 接受记录，`kind_filter_flags()` 随后对同一记录抛出 `invalid_kind_prefix`；
- `MemorySystem.reindex_all()` 在构建索引 entry 时缺少逐条故障隔离，一条坏记录可中断整个 namespace 的预热；
- Akane 使用内存索引，所以数据库有摘要不代表运行时检索能看到它。

云端已有 62 条带连字符的 MCP action/result，日志反复出现 `memcore get_system index warmup failed: invalid_kind_prefix`。这是 MemCore 公共接入契约问题，必须在 MemCore 修，不应由 Akane 对具体 MCP 名做兼容。

### 3.3 请求投影与后台压缩竞态

压缩线程基于较短的旧 entries 构建预期前缀时，请求线程可能已冻结更长的同回合投影。旧快照随后进入 `freeze_turn_entries()`，看到保存投影覆盖范围长于自己，就抛出 `projection_history_not_append_only`。

云端临时补丁允许“已保存投影是旧 expected 的权威超集”时直接返回 saved，已使新错误停止并恢复多次压缩；但补丁只存在于云端 release 包，本地 `MemCore` 未修改，而且泛化地改变了 `freeze_turn_entries()` 的返回语义。正式方案应在压缩边界识别过期 generation 并重载/重试，或者把“接受权威超集”做成显式命名的调用契约，不能保留一个下次部署就会丢失的热补丁。

### 3.4 QQ 文本表现

普通 QQ 文本提示只要求“自然、口语”，没有明确禁止 Markdown；发送层又逐字转发 speech，因此 `**`、标题符号等会直接显示。时间戳、`Assistant:`、actor/target/reply 字段同样是模型用来判断群聊归属的证据，不是应该复述给用户的正文，当前稳定提示缺少这条边界。

这些属于稳定表达规则，应一次写入稳定提示前缀，不应每轮动态追加，也不应靠大范围文本后处理猜测语义。

## 4. 参考实现研究

### 4.1 DeepSeek Harness

参考源码：`deepseek-harness`，提交 `f116b7c20f`。

DSH 的核心原则在 `packages/core/agent-loop/README.md:74-83` 中写得很直接：AgentLoop 只做“call the model, run the tools, repeat”，额外策略放插件。`packages/core/agent-loop/src/agent.ts:404-410` 的实际循环也只看事实：assistant message 没有 tool call 就完成；有 tool call 就执行并进入下一 step。`packages/core/agent-loop/README.md:129-134` 明确记录核心 AgentLoop 没有内建回合预算。

它的 `repeat-tool-reminder` 插件会按同工具、同参数的连续次数在 3/5/8 次发送递进提醒，但 `packages/guard/repeat-tool-reminder/README.md:23-35` 和 `src/index.ts:209-223` 明确规定“observe-and-enrich, never veto”：调用照常执行，决定换方法、继续收集还是结束仍归模型。提醒作为有来源的追加上下文写入会话，工具自己的原始 result 不被替换；轮询类工具可通过配置排除。

值得采用：

- 以实际 tool call/result 驱动循环，不推断“终稿阶段”；
- 重复检测是可配置、非阻断的提示，不能成为隐式终止条件；
- 工具结果保持原样，提示作为独立、有来源的追加上下文；
- 并行只覆盖互不依赖的同批工具，结果仍按模型顺序提交；
- 模型可见内容必须可由日志重建；
- 普通历史增长保持仅尾部追加，压缩是独立上下文插件。

不直接采用：

- DSH 没有默认回合预算；Akane 面向 QQ、桌宠和有成本的公共 Bot，需要一个明确、可配置的硬预算；
- DSH 的标准会话在压缩前重发完整历史；Akane 已有 MemCore 的短结果原文、长结果卡片和可召回能力，不应退回全量历史；
- DSH 的 reminder 会作为持久历史保存；Akane 是否持久保存重复提醒应服从 MemCore 事件语义，避免把机械提示当用户消息。

### 4.2 OpenCode

参考源码：`OpenCode`，提交 `38e10eb`。

OpenCode 在 `packages/opencode/src/session/prompt.ts:1103-1129` 中显式处理 Provider 语义不一致：即便 Provider 给出 `stop`，只要 assistant message 里仍有实际 tool call，就继续循环，让结果回到模型。工具调用是否存在比 Provider 的结束标签更权威。

它在 `packages/opencode/src/session/processor.ts:353-380` 中检测最近三次同工具、同参数调用，但处理方式是触发名为 `doom_loop` 的权限询问，不是静默把工具关掉。工具错误会被记录成 tool result；上下文溢出由单独 compaction 流程处理。

OpenCode 的最大步数机制在 `packages/opencode/src/session/prompt.ts:1178-1285` 的最后一步追加 `packages/core/src/session/runner/max-steps.ts:1-15` 定义的 `MAX_STEPS_PROMPT`，明确告诉模型工具将不可用并要求文本总结。它证明“预算事件应当显式告诉模型”是对的，但它发生得太晚，而且只要求文本总结，不能满足 Akane 的可靠跨回合续作。

值得采用：

- tool call 的实际存在优先于 Provider 的 `finish/stop` 标签；
- 重复/死循环保护必须显式、可观察，不能偷偷改写为完成；
- 工具错误、权限拒绝、上下文溢出分别走各自清晰路径；
- 步数限制是显式配置，并在请求尾部说明，而不是动态改 system prompt。

不直接采用：

- QQ 中为三次重复调用弹人工批准会打断长任务；Akane 优先采用 DSH 式非阻断提醒；
- 到最后一步才要求纯文本总结太迟，且 speech 不是可靠续作载体；Akane 保留提前软提醒和真实项目文件；
- 不照搬 OpenCode 的会话/compaction 实现替换 MemCore。

### 4.3 参考后的取舍

DSH 和 OpenCode 都证明，Akane 当前“全回合签名去重 → 三次后关工具 → 终稿恢复”不是长程 Agent 的必要机制。Akane 应采用 DSH 的非阻断提醒、OpenCode 对实际 tool call 的事实优先级，并保留自己更强的 MemCore 结算和提前续作提醒。

## 5. 目标工具循环

目标状态机只有下面几条边：

```text
模型返回
  ├─ 合法 tool call，已执行批次 < hard_limit
  │    └─ 执行调用 → 记录真实 call/result → 将结果喂回 → 再请求模型
  ├─ 合法最终输出
  │    └─ 交付 → 关闭开放回合 → MemCore settlement
  ├─ 工具/输出协议不合法
  │    └─ 追加明确、短小的协议反馈 → 保持同一工具面 → 有界重试
  └─ Provider/传输失败
       └─ 同上下文重试；彻底失败则交付结构化服务故障并保留可续作状态
```

硬预算是唯一正常关闭工具的事件：

```text
第 48 批工具请求到达
  → 先真实执行第 48 批
  → 记录并把第 48 批结果返回模型
  → 下一次请求不提供工具，并明确说明 48/48 已用完
  → 模型按原有最终输出协议诚实汇报
```

不能等待模型发起“第 49 批”才告诉它预算已耗尽；也不能在第 21 批因为重复签名就假装进入预算收尾。

如果模型在没有工具 schema 的预算收尾请求中仍产生工具调用形态，记为 `protocol_stalled`，不得把工具参数当 QQ speech 发出，也不得伪造成功。宿主应在同回合做有界协议恢复；只有 Provider 持续不可用或持续违反协议时，才向客户端报告真实失败。

## 6. 重复调用与幂等设计

### 6.1 删除全回合阻断

删除或退役以下语义：

- `seen_tool_calls` 作为整回合执行许可；
- `allowed_repeat_tool_calls` 的特例授权；
- “无可执行重复调用”增加 `invalid_tool_decision_attempts`；
- 因重复调用达到三次而设置 `tool_decision_invalid`、关闭全部工具；
- 把重复调用反馈写成“请自然回应，不要继续调用”的终止暗示。

相同参数不等于相同事实。状态轮询、进程查询、重启后的 `load_mcp`、网络重试、测试复跑都可能合法重复；一次外部状态变化就足以让同参数产生新结果。宿主不能靠签名推断调用无意义。

### 6.2 如需防循环，只做提示

可借鉴 DSH，增加一个默认非阻断、可配置的连续重复提醒：

- 只比较连续的相同调用，不扫描整个用户回合；
- 默认在 3/5/8 次给出递进提示，阈值可配置；
- `exec_status`、带 continuation 的轮询、明确幂等读取可排除；
- 提示说明“检查最新结果，必要时改参数/换方法/结束”，但仍执行调用；
- 提示是带来源的短事件，追加在当前结果之后，不能替换工具 result；
- 大参数只展示有界预览，比较仍使用完整参数；
- 用户 steer 进入后重置连续计数。

是否真的需要该 reminder，应由修复后的真实日志决定。第一修复切片可以先完全移除阻断，只保留观测指标；没有重复失控证据就不新增插件。

### 6.3 外部副作用由工具自己保证幂等

发消息、转账、删除、发布等带副作用工具应使用 handler 拥有的 idempotency key、平台 receipt 或 call ID 避免重复副作用。若收到相同幂等请求，工具返回“已执行/同一 receipt”的真实结果，而不是由通用 Agent 循环猜测并丢弃。只读工具与状态查询不应继承副作用工具的限制。

## 7. 工具预算与可靠续作

### 7.1 配置

保留两个公开、可解释的预算参数：

- `TOOL_ROUND_HARD_LIMIT`：可选的实际工具批次硬上限，默认 0（不限制）；只有显式配置正数才启用；
- `TOOL_ROUND_WARNING_REMAINING`：有限硬上限还剩多少批时提醒一次，当前默认 8；未启用硬上限时不生效。

协议错误重试与工具预算彻底分离，统一使用已有 `CHAT_MODEL_DECISION_MAX_ATTEMPTS`。它只约束 Provider 连续没有形成可表示决策的次数；任何带名称的工具调用，包括参数错误、未知工具和权限拒绝，都必须先变成普通结构化工具结果，不能进入该计数，更不能在正常预算尚未耗尽时永久关闭工具。旧 `TOOL_DECISION_RETRY_LIMIT` 删除并列入退役配置。

### 7.2 软提醒必须解释原因

当前 `build_tool_round_warning()` 的方向正确，应保留其核心信息：

1. 工具仍可用，提醒不改变权限；
2. 当前开放回合中的调用与结果完整可见；
3. 回合结束后，MemCore 保留工具参数，短结果保留原文，只有满足收益条件的长结果才变成可召回卡片；
4. 单个卡片只能恢复对应结果，不会自动整理散落在多轮中的任务目标、关键决策、修改位置、验证状态和剩余工作；
5. 如果预计无法完成，应趁工具仍可用，在真实项目里写一份可检查的续作记录；如果能完成则继续，不必为了提醒制造文件。

这段解释让模型知道“为什么现在要整理”，符合提示词导向。仅说“优先完成关键动作”不足以让模型理解跨回合上下文会如何变化。

### 7.3 续作文件如何与 MemCore 配合

模型用现有文件工具写续作文件时：

- tool call 参数会保留文件路径、写入意图和正文（具体正文是否完整出现在参数中取决于文件工具协议）；
- tool result 记录真实写入状态；短结果保留原文，长结果按统一 settlement 收益规则决定是否卡片化；
- 文件本身是工作区中的持久事实，下一轮可直接读取，不依赖 speech，也不依赖模型猜卡片 ID；
- 回合结束后若相关长结果形成卡片，`memory_id` 由 MemCore 产生并可用 `open_memory` 打开；软提醒不能提前承诺这个 ID；
- 开放回合中的内部 `source_id` 用于宿主关联，不要求模型记录。

推荐续作内容只包含任务真正需要的状态：目标、已完成改动、关键文件及位置、实际测试结果、未完成事项、已知失败、下一步。不要复制大段工具输出；可重新读取的细节留在文件和 MemCore 轨迹中。

### 7.4 MCP 历史复用需要一个稳定调用入口

历史版本（已由统一暴露专项替代）的 `resolve_unloaded_mcp_native_aliases()` 只解决了宿主接收能力：如果 Provider 已经输出一个当前请求未公开的历史 MCP 原生工具名，宿主可以把它精确映射回当前注册表并执行。现有测试通过手工构造 `_native_tool_call` 证明了这一点，但没有证明真实模型会稳定调用一个不在当前 `tools` schema 中的函数。不同 Provider 可能拒绝生成、退化为普通文本，或泄漏 DSML/XML 工具标签，因此这条兼容路径不能作为主要模型体验。

常驻一个小型原生工具 `invoke_mcp`，为“模型已经从可见 MemCore 历史知道准确调用方式”提供 Provider 无关的合法入口：

```json
{
  "server_id": "github",
  "tool_name": "get_issue",
  "arguments": {
    "owner": "deepseek-ai",
    "repo": "DeepSeek-Harness",
    "issue_number": 124
  }
}
```

职责边界：

- `invoke_mcp` 不检索记忆、不总结历史、不发现未知工具；模型自己从当前可见工具轨迹中读取 server/tool/arguments；
- Host registry 和当前 Profile overlay 是执行权威，MemCore 历史不是权限或 schema 权威；
- 调用时取得当前工具 schema，重新校验参数、启用状态、领域策略和审批权限；
- 不知道准确工具名/参数、工具已升级或 schema 不兼容时，返回结构化反馈并让模型使用 `load_mcp`；
- `load_mcp` 继续负责陌生能力发现和当前回合完整原生 schema 展开；`invoke_mcp` 负责已知能力复用；
- 两者最终汇入同一个 MCP adapter、权限、receipt 和 MemCore action/observation 管线，不能维护第二套执行器；
- `resolve_unloaded_mcp_native_aliases()` 可保留为兼容兜底，但不再作为历史复用已经完成的证据；
- `invoke_mcp` 的调用参数与结果遵循普通 MemCore settlement：参数保留，短结果原文，长结果仅在有收益时卡片化。

如果 GitHub MCP 暴露 44 个工具、某次任务实际调用其中 10 个，下一次同一可见记忆范围内，模型可根据这 10 条历史调用使用 `invoke_mcp`，无需把 44 个 schema 再次注入请求。没有历史证据的另外 34 个工具仍应通过 `load_mcp` 发现。宿主可能在后台启动 MCP 或执行 `tools/list` 取得当前 schema，但这不是额外模型工具轮，也不会把全部 schema放入提示词。

### 7.5 MCP 凭据引用属于宿主配置，不属于模型上下文

MCP 配置中的 command/args、`env` 和 HTTP headers 应统一解析 `${ENV_NAME}` 凭据引用。Bot 实例私有值（例如 `QQ_ONEBOT_ACCESS_TOKEN`）只在启动 MCP 子进程或创建 HTTP 请求时注入；模型、公开 catalog、日志、MemCore 和工具结果只能看到 `configured/missing`，不能看到真实值。

占位符扫描必须覆盖所有支持凭据的位置，不能只扫描启动参数。凭据缺失返回结构化 `credential_missing`，不得诱导模型读取 `.env`、systemd 配置或服务器私有文件。安装共享不等于凭据共享：Host registry 可跨群聊/私聊共享服务器定义，实际值由当前 Bot 实例运行时提供，Profile 权限仍独立生效。

## 8. MemCore 修复边界

### 8.1 开放回合与结算不变

MemCore 的统一规则继续成立：开放工具回合保留完整调用和结果；终稿持久化后才 settlement；工具参数保持可见；短结果保留原文；只有结果正文大于卡片且达到配置收益比时才替换为可召回卡片。不能为了长任务修复新增“所有续作都强制卡片化”或专用总结后门。

### 8.2 kind 语法必须单一

MemCore 需要一个共享的 kind 规范化/验证实现，供 timeline append、projection、index entry、filter query 共用。只要 append 接受某个 kind，索引和查询就必须能表示它。MCP/plugin 名中的连字符要么通过稳定编码进入 metadata key，要么在入口统一规范化；不能前一层接受、后一层拒绝。

索引重建还必须逐记录隔离：单条遗留或损坏记录记为 failed 并继续其他记录，最终返回 `ready / partial / failed` 和失败计数。`partial` 不得伪装 ready，检索层和日志应能说明索引不完整。

### 8.3 并发投影要有代际语义

后台 compactor 获取 entries 时同时取得 projection generation/hash。冻结前若权威 generation 已推进，则重载最新 entries 后重算，或返回明确的 stale/busy 让调用方重试。若保留“权威 saved 是 expected 的超集即可复用”，必须作为显式参数/方法和测试契约，不能静默改变通用 `freeze_turn_entries()` 的含义。

云端热补丁只作为故障证据；正式修复进入 MemCore 仓库、构建 wheel、同步 Akane 后，删除 release 内的手改和备份依赖。

## 9. 稳定提示与 QQ 输出

在稳定 QQ system prompt 中一次加入两条短规则：

```text
QQ speech 使用自然纯文本；不要使用 Markdown 标题、强调标记或代码围栏。
时间、actor、target_actor、reply_to 与引用正文用于判断谁在对谁说什么；除非用户明确询问，不要把字段名、时间戳前缀或 “Assistant:” 等投影标签复述进 speech。
```

它们是长期稳定规则，只造成一次缓存前缀迁移。不要把当前群号、触发方式、倒计时、工具轮数或失败次数写进动态 system prompt。

纯文本兜底可以保留为 Provider 连续破坏 JSON 时的最后交付通道，但只能包装 speech，不能根据“兜底发生”伪造不满、开心等语义表情。代码、路径、URL、工具结果和用户正文不得被通用 Markdown 清洗器篡改。

## 10. 实施切片

### Slice A：先锁定失败，不改行为

- 用真实等价 fixture 复现“21 批后重复 `load_mcp`”和“22 批后重复 `exec_status`”；
- 断言当前代码会清空工具 schema 并失败，作为红测；
- 在 request audit 中记录被拒调用的 tool name、规范化参数 hash、拒绝原因和当前批次，不记录密钥或大正文；
- 加“Provider 标为 stop 但实际含 native tool call”的协议测试。

### Slice B：修复主工具循环

- 删除整回合重复阻断和对应特例；
- 合法 native/兼容工具调用统一回到执行循环；
- 协议错误恢复保持同一真实工具面；
- 第 48 批先执行、记录结果，再进入明确的无工具收尾请求；
- 保留并验证第 40 批一次性软提醒；
- 如无实际需要，先不加入新的重复 reminder。

### Slice C：补齐 MCP 历史复用与实例凭据

- 新增常驻、低 token 的 `invoke_mcp` 原生工具；
- 只接受精确 `server_id/tool_name/arguments`，并通过当前注册表、当前 schema、Profile 策略和统一 adapter 执行；
- 保留 `load_mcp` 作为发现/展开入口，保留历史原生别名作为兼容兜底；
- 把 MCP `env`、HTTP headers 与 command/args 的 `${ENV_NAME}` 解析统一到 Bot 实例凭据引用；
- 真实模型验收：先在一次任务中加载并调用 GitHub MCP，下一次仅凭 MemCore 可见轨迹直接 `invoke_mcp`，请求中不出现 44 个 schema；
- 验证未知/已升级工具、缺失凭据、权限拒绝、MCP 业务失败都返回结构化结果且不泄漏 token。

### Slice D：修复 MemCore kind 与索引预热

- 统一 kind grammar/metadata key 编码；
- 逐条隔离索引构建失败；
- 暴露 warmup `ready/partial/failed`；
- 加 MCP 连字符 kind、进程重启、全量重建、摘要检索、`open_memory` 的端到端测试。

### Slice E：正式修复投影/压缩竞态

- 把云端热补丁转化为明确的 stale-generation 处理；
- 加请求冻结与后台 compaction 并发测试；
- 验证 append-only、stable prefix hash、settlement 和缓存命中；
- 构建并同步正式 wheel 后删除云端手改状态。

### Slice F：稳定表达提示

- 合并 QQ 纯文本与投影元数据边界，删除冲突/重复描述；
- 验证普通回复、工具终稿、群聊 actor/target/reply、纯文本兜底；
- 确认只发生一次系统前缀迁移，后续请求前缀稳定。

### Slice G：真实部署验收

- 先个人 Bot 小流量部署，再扩展到金融 Bot；
- 运行 20+、40+、48 批三档长任务和 MCP 重启复测；
- 人为制造一次重复状态轮询、一次工具失败、一次 Provider `stop + tool call`、一次 MemCore 索引坏记录；
- 观察 request schema、工具批次、最终交付、settlement、索引状态、缓存命中和 QQ 实际文本；
- 任何一步未达标就回滚对应切片，不用新补丁掩盖。

## 11. 验收矩阵

| 验收项 | 必须成立 |
|---|---|
| 合法重复读取/轮询 | 真实执行并返回新结果，不累计成终止条件 |
| MCP 重启后重复 `load_mcp` | 可重新加载；历史工具名可按当前 MCP 注册状态解析 |
| MCP 历史复用 | 可见历史已给出准确工具与参数时，`invoke_mcp` 无需展开服务器全部 schema 即可执行 |
| MCP 凭据 | Bot 实例值可注入 `env`/headers/args；模型、日志、MemCore 不出现明文 |
| Provider `stop` + tool call | tool call 被执行，结果回到模型 |
| 协议格式错误 | 同工具面收到明确反馈并重试；不消耗真实工具批次 |
| 第 40/48 批 | 第 40 批只提醒；第 48 批先执行真实结果再关工具 |
| 未完成续作 | 项目中存在模型写下的真实文件；下一轮可直接读取继续 |
| MemCore settlement | 短结果原文，长结果仅在有收益时卡片化，卡片可打开 |
| MCP kind | 连字符工具在重启预热后仍可检索，坏记录不会拖垮全 namespace |
| 投影并发 | 不再出现 `projection_history_not_append_only`；无丢消息、无倒序、无重复卡片 |
| 未交付 | 正常长任务为 0；彻底 Provider 故障显示真实结构化失败，不伪造回答 |
| QQ 文本 | 无裸 `**`、无意外时间戳/`Assistant:`，代码与路径不被误改 |
| 缓存 | 稳定 system/schema 前缀不随批次、提醒或重试变化；动态反馈仅尾部追加 |

## 12. 明确不做

- 不新增“终稿阶段”“任务已完成推断器”或按工具家族猜预算；
- 不因三次相同签名静默阻断工具；
- 不把 speech 当续作数据库；
- 不要求模型记录不可见的 `source_id`；
- 不新增绕过统一 settlement 的总结工具或强制卡片类型；
- 不把 MemCore 检索失败降级成“数据库里没有摘要”；
- 不用动态 system prompt 传当前轮数、群状态或恢复次数；
- 不整套搬运 DSH/OpenCode，也不为了插件化改变 Akane 已有效的角色表现与 MemCore 设计；
- 不在设计文档切片中修改、部署或重启生产环境；代码实施与部署严格进入各自独立切片。

## 13. 审查门槛

进入代码实施前，需要对本文确认三点：

1. 主循环只由合法 tool call、合法交付、协议错误和真实外部失败驱动；
2. 重复调用不再拥有关闭工具的权力，硬预算是唯一正常关工具事件；
3. MemCore 的 kind 索引和投影竞态按公共能力修复，不在 Akane 宿主继续补专有兼容。

确认后严格按 Slice A → B → C → D → E → F → G 推进。每个切片独立测试、独立提交、可独立回滚；不得在前一切片未验收时继续叠下一层行为。
