# Claude Code 工具系统调研备忘录 v1

> 性质：**只读调研，不改任何代码。** 为 AkaneCompanionLab 的工具系统解耦（见 `docs/tool_system_decoupling_v1.md`）提供可借鉴设计。
> 源码位置：`F:/Akane/galgame/AkaneBrain/claude_code_annotated`（下文路径均相对此根）。
> 纪律：每条结论附 Claude Code 源码文件路径 + 函数/类名；行号为调研时所见，可能漂移，以函数名为准。

---

## 1. 多轮 agent loop：关键文件、函数、循环时序

- **入口**：`src/query.ts` 的 `query()`（async generator，~L219）→ `yield* queryLoop()`（~L230）。
- **主循环**：`src/query.ts` 的 `queryLoop()`（~L241），核心是 `while (true)`（~L307），不是递归——靠多个 `continue` 站点回到循环顶部，消息状态在迭代间累积。
- **每轮时序**（`queryLoop` 内）：
  1. 组装 `messagesForQuery`（~L365），先过 token 预算/压缩（`applyToolResultBudget`、microcompact，~L379–446）。
  2. 调模型流式产出 assistant 消息（含 `tool_use` block）。
  3. 收集 assistant 消息里的 `tool_use` block，调 `runTools(...)`（`src/query.ts` ~L1382，来自 `src/services/tools/toolOrchestration.ts`）。
  4. 把工具结果归一成 user 消息（`tool_result` block，按 `tool_use_id` 配对）压进 `toolResults`（~L1395）。
  5. 下一轮 query 的消息 = `[...messagesForQuery, ...assistantMessages, ...toolResults]`（~L1585 / ~L1716），`continue` 回顶。
- **顺序契约**：assistant(`tool_use`) 与 user(`tool_result`) **紧邻配对**；缺失的 tool_result 由 `yieldMissingToolResultBlocks()`（`src/query.ts` ~L123）补齐，防止 orphan tool_use。
  - 对 Akane 的意义：实锤了"当前用户消息必须在工具往返**之前**、tool_use→tool_result 必须紧邻"——旧 native 尝试把用户消息排到工具结果之后是 bug。

## 2. Tool 契约：字段/方法/默认策略

- **类型定义**：`src/Tool.ts` 的 `Tool<Input, Output, P>`（~L362）。
- **身份/Schema**：`name`、`aliases?`、`inputSchema`(Zod) / `inputJSONSchema?`(MCP 直供 JSON Schema)、`outputSchema?`、`searchHint?`。
- **模型可见说明**：`description(input, options)`、`prompt(options)`（工具自带提示词）。
- **校验**：`validateInput?(input, ctx) → ValidationResult`，`ValidationResult` 定义在 `src/Tool.ts`（~L95）：`{result:true}` 或 `{result:false, message, errorCode}`——`message` 面向模型。
- **权限**：`checkPermissions(input, ctx) → PermissionResult`（**仅在 validateInput 通过后调用**）。
- **执行**：`call(args, ctx, canUseTool, parentMessage, onProgress) → Promise<ToolResult<Output>>`；`ToolResult<T>`（`src/Tool.ts` ~L321）= `{ data, newMessages?, contextModifier?, mcpMeta? }`。
- **结果喂回塑形**：`mapToolResultToToolResultBlockParam(output, toolUseID) → ToolResultBlockParam`——工具自定义"结果怎么变成喂回模型的块"。
- **自描述风险/性质**：`isReadOnly(input)`、`isConcurrencySafe(input)`、`isDestructive?(input)`、`isEnabled()`、`isMcp?`、`getPath?`。
- **结果大小纪律**：`maxResultSizeChars`——超限落盘、只给模型预览+路径（注释见 `src/Tool.ts` `maxResultSizeChars` 字段）。
- **延迟加载**：`shouldDefer?`、`alwaysLoad?`（见第 5 节）。
- **默认策略（fail-closed）**：`src/Tool.ts` 的 `buildTool()`（~L783）+ `TOOL_DEFAULTS`（~L757）；`DefaultableToolKeys`（~L707）。默认：`isEnabled→true`、`isConcurrencySafe→false`（假设不安全）、`isReadOnly→false`（假设写）、`isDestructive→false`、`checkPermissions→allow`（交给通用权限系统）。所有工具经 `buildTool` 出厂，默认只在一处。

**值得 Akane 抄的**：`ValidationResult{result,message,errorCode}`、`checkPermissions` 与校验分离、`ToolResult` 信封、`maxResultSizeChars` 纪律、`buildTool`+`TOOL_DEFAULTS` 一处 fail-closed 默认。

## 3. WebSearchTool：门控 / 结果格式 / 错误 / 喂回

文件：`src/tools/WebSearchTool/WebSearchTool.ts`，导出 `WebSearchTool = buildTool({...})`（~L152）。

- **provider/model 门控**：`isEnabled()`（~L168）真的在判 provider 和 model——`firstParty` 开；`vertex` 仅 `claude-opus-4 / sonnet-4 / haiku-4` 开；`foundry` 开；其余返回 false。**工具自报"我在哪些 provider/model 上能用"。**
- **输入 schema**：`inputSchema`（~L25，Zod strictObject：`query`(min 2)、`allowed_domains?`、`blocked_domains?`）。
- **校验**：`validateInput()`（~L235）——空 query → `{result:false, message:'Error: Missing query', errorCode:1}`；同时给 allowed+blocked → `errorCode:2`。
- **结果格式**：`call()`（~L254）返回 `{data}`；`makeOutputFromSearchResponse()`（~L86）把 server 返回的 `server_tool_use` / `web_search_tool_result` / `text` block 序列整理成 `{query, results[], durationSeconds}`。
- **错误处理**：`web_search_tool_result` 的 `content` 非数组即错误，取 `error_code` 转成字符串塞进 `results` 并 `logError`（~L115–122）——错误**作为结果内容喂回**，不抛、不 fallback。
- **结果喂回**：`mapToolResultToToolResultBlockParam()`（~L401）把 results 格式化成文本，**末尾追加指令**：`"REMINDER: You MUST include the sources above ... using markdown hyperlinks"`（~L427）。即工具可在喂回里塞后置指令。
- **其它**：`maxResultSizeChars: 100_000`（~L155）、`shouldDefer: true`（~L156）、`isReadOnly()/isConcurrencySafe() → true`（~L200–205）、`checkPermissions` 返回 `behavior:'passthrough'`（~L209）。

## 4. MCPTool：MCP 如何被包装成统一 Tool

文件：`src/tools/MCPTool/MCPTool.ts`，导出 `MCPTool = buildTool({...})`（~L27）。

- 它就是**一个普通 Tool**，标志位 `isMcp: true`（~L28）。
- `call()`（~L51）把调用代理到 MCP 服务；`mapToolResultToToolResultBlockParam()`（~L70）把 MCP 返回塑形成 `tool_result`。
- MCP 工具的 schema 走 `Tool.inputJSONSchema`（`src/Tool.ts` 字段注释：MCP 直接给 JSON Schema，不从 Zod 转）。
- **结论**：在 Claude Code 里 **MCP 工具与内置工具走完全相同的契约与执行管线**（同 `validateInput→checkPermissions→call→mapToolResult`）。对 Akane：一旦有统一 `ToolInvocation` 管线，MCP 只是"另一个来源"，无需独立分支。

## 5. Skill：如何发现 / 何时加载 / 如何避免 prompt 爆炸

- **发现/定义**：`src/skills/loadSkillsDir.ts` 解析目录下带 **frontmatter** 的 skill（`frontmatterParser` 导入 ~L46）；frontmatter 字段含 `name / description / whenToUse / hooks / paths`。bundled skills 见 `src/skills/bundledSkills.ts`（`description` 字段 ~L17/78）。
- **何时加载正文（关键）**：`src/skills/loadSkillsDir.ts` 的 token 估算注释明确——**只按 frontmatter（name/description/whenToUse）估算，skill 正文仅在调用时加载**（~L97–104）。即"渐进式披露"：prompt 里只有元信息，正文调用 `SkillTool` 时才读。
- **避免 prompt 爆炸**：
  - skill 列表预算截断：`src/tools/SkillTool/prompt.ts`——bundled skill 永远给全描述，非 bundled 在预算紧时**截断描述乃至只剩名字**（~L78–168，埋点 `tengu_skill_descriptions_truncated`）。
  - 工具级延迟加载：`src/tools/ToolSearchTool/prompt.ts` 的延迟判定（`shouldDefer` 逻辑 ~L54–107）——`shouldDefer:true` 的工具 schema **不进初始 prompt**，模型需先调 `ToolSearch`（`src/tools/ToolSearchTool/constants.ts` `TOOL_SEARCH_TOOL_NAME`）来加载；`alwaysLoad:true`（MCP 经 `_meta['anthropic/alwaysLoad']` 设置）则永远直载（~L59–65）。
- **对 Akane**：skill ≈ "带 frontmatter 的 markdown + 调用时加载正文 + 挂在一个 SkillTool 上"；治"40 个工具 schema 灌爆提示词"的解药是 `shouldDefer`+ToolSearch / 描述预算截断。

## 6. 并行工具：如何判断 concurrency safe / 如何分批执行

文件：`src/services/tools/toolOrchestration.ts`。

- **入口**：`runTools()`（~L19）接收一次 assistant 消息里的多个 `tool_use` block。
- **分批**：`partitionToolCalls()`（~L91）按每个工具的 `tool.isConcurrencySafe(parsedInput.data)`（~L98–101）把**连续的并发安全工具**合成一个并发批；不安全的各自成串行批；批的类型 `Batch = { isConcurrencySafe, blocks }`（~L84）。
- **执行**：并发安全批 → `runToolsConcurrently()`（~L152，上限 `getMaxToolUseConcurrency()` ~L8，默认 10）；不安全批 → `runToolsSerially()`（~L118，逐个，并应用 `contextModifier`）。
- **校验/权限/执行的单工具管线**：`src/services/tools/toolExecution.ts` 的 `runToolUse()`（~L337）→ `checkPermissionsAndCallTool()`（~L599）：`validateInput`（~L683）→ 失败即返回 `tool_result{ is_error:true, content:'<tool_use_error>…' }`（~L717–724，**喂回模型不 fallback**，并打 `tengu_tool_use_error`）→ 权限 → `tool.call()`（~L1207）→ `mapToolResultToToolResultBlockParam()`（~L1292）。错误分类 `classifyToolError()`（~L150）。
- **对 Akane**：这是将来松开 INV-2「一轮一个工具」时的现成蓝图——按 `isConcurrencySafe` 分批，只读类并发、写类串行。

## 7. Akane 可立即吸收的设计（小步、不重写）

1. **工具自报 provider/model 支持**：仿 `WebSearchTool.isEnabled()`（`src/tools/WebSearchTool/WebSearchTool.ts` ~L168），把"该工具在哪些模型上能 native"并入 `ProviderToolProfile` 思路（对应 `docs/tool_system_decoupling_v1.md` 第 5/10 节）。
2. **结果喂回由工具塑形 + 可带后置指令**：仿 `mapToolResultToToolResultBlockParam`（`WebSearchTool.ts` ~L401，末尾 REMINDER）。落到 Akane 的 `ToolResultEnvelope.model_feedback`。
3. **`maxResultSizeChars` 超限落盘纪律**：仿 `src/Tool.ts` `maxResultSizeChars` 字段——Akane 抓网页/搜索结果同样需要，避免爆上下文。
4. **校验失败 → `<tool_use_error>` 喂回，绝不 fallback**：仿 `src/services/tools/toolExecution.ts` `checkPermissionsAndCallTool()`（~L717–724）。Akane 已有起点 `tool_orchestration_engine.classify_tool_call_rejection` / `validate_tool_invocation`，方向一致。
5. **`ValidationResult{ok/message/code}` + fail-closed `buildTool` 默认**：仿 `src/Tool.ts` `ValidationResult`(~L95) / `buildTool`+`TOOL_DEFAULTS`(~L757–783)。Akane 已有 `ValidationResult`，可补 `code` 并仿"一处默认"。

## 8. Akane 暂不应吸收的设计

1. **所有 `render*` / UI 方法**：`src/Tool.ts` 的 `renderToolUseMessage` / `renderToolResultMessage` / `renderToolUseProgressMessage` 等——React/JSX/终端 UI，属前端，后端不抄。
2. **并行多工具**：`src/services/tools/toolOrchestration.ts` `runToolsConcurrently()`——与 Akane 当前 INV-2「一轮一个工具」冲突；先记蓝图，等单工具链路稳了再议。
3. **Skill 体系 / SkillTool**：`src/tools/SkillTool/*`、`src/skills/*`——是工具之上的独立层，3a 范围外，待工具契约稳定后单独设计。
4. **ToolSearch 延迟加载**：`src/tools/ToolSearchTool/*`——解决的是"几十上百工具"的 prompt 规模问题；Akane 当前工具规模未到该痛点，记为后续选项。
5. **子 agent / Team / 协调器**：`src/tools/AgentTool/*`、`src/tools/Team*`、`src/coordinator/*`——多 agent 编排，远超当前目标。

## 9. 引用索引（文件 → 函数/类）

- `src/query.ts`：`query()`、`queryLoop()`、`yieldMissingToolResultBlocks()`、`runTools` 调用点。
- `src/services/tools/toolOrchestration.ts`：`runTools()`、`partitionToolCalls()`、`runToolsConcurrently()`、`runToolsSerially()`、`getMaxToolUseConcurrency()`。
- `src/services/tools/toolExecution.ts`：`runToolUse()`、`checkPermissionsAndCallTool()`、`classifyToolError()`、`buildSchemaNotSentHint()`。
- `src/Tool.ts`：`Tool`、`ValidationResult`、`ToolResult`、`ToolUseContext`、`buildTool()`、`TOOL_DEFAULTS`、`DefaultableToolKeys`。
- `src/tools/WebSearchTool/WebSearchTool.ts`：`WebSearchTool`、`isEnabled()`、`validateInput()`、`call()`、`makeOutputFromSearchResponse()`、`mapToolResultToToolResultBlockParam()`。
- `src/tools/MCPTool/MCPTool.ts`：`MCPTool`（`isMcp:true`）、`call()`、`mapToolResultToToolResultBlockParam()`。
- `src/skills/loadSkillsDir.ts`、`src/skills/bundledSkills.ts`、`src/tools/SkillTool/prompt.ts`、`src/tools/SkillTool/SkillTool.ts`。
- `src/tools/ToolSearchTool/prompt.ts`（`shouldDefer` 判定）、`src/tools/ToolSearchTool/constants.ts`（`TOOL_SEARCH_TOOL_NAME`）。

---

# 附录 A：三个深挖专题（只读调研，不改代码）

> 本附录回应"再挖三个方向"的要求，全部基于 `claude_code_annotated` 源码实读，每条结论带文件路径 + 函数/类名。

## A.1 ToolResult 超限处理：落盘而非截断

**核心机制：超限不截断，而是整体写文件 + 喂回"预览 + 文件路径"。** 实现在 `src/utils/toolResultStorage.ts`。

- **阈值解析** `getPersistenceThreshold(toolName, declaredMaxResultSizeChars)`（~L55）：
  - `maxResultSizeChars === Infinity`（如 Read）= 硬退出，永不落盘（~L62，注释："persisting its output to a file the model reads back with Read is circular"）。
  - 否则 `Math.min(declaredMaxResultSizeChars, DEFAULT_MAX_RESULT_SIZE_CHARS)`（~L77）——工具声明值与全局默认取小。GrowthBook 可按工具名覆盖。
- **落盘判定** `maybePersistLargeToolResult()`（~L272）：
  1. 空结果先拦截：注入 `(${toolName} completed with no output)`（~L287–295，注释 inc-4586：空 tool_result 尾部会让部分模型提前停。**这点 Akane 直接可抄——工具静默成功也要给模型一句话**）。
  2. 图片块跳过（~L302）。
  3. `size <= threshold` 直接原样返回（~L310）。
  4. 超限 → `persistToolResult()` 写盘 → 用 `buildLargeToolResultMessage()` 替换 content。
- **喂回模型的文本长什么样** `buildLargeToolResultMessage()`（~L189）：
  ```
  <persisted-output>
  Output too large (1.2 MB). Full output saved to: <filepath>

  Preview (first 2.0 KB):
  <前 2000 字节，按换行边界截断>
  ...
  </persisted-output>
  ```
  即：**模型看到的是"超限提示 + 真实文件绝对路径 + 2KB 预览"**，可用 Read 工具取回全文。路径是**暴露**的（这是设计：让模型能回读）。
- **文件落点**：`getToolResultsDir()` = `projectDir/sessionId/tool-results/`，文件名 `<tool_use_id>.{json|txt}`（~L104–117）。写用 `flag:'wx'`（已存在即跳过，~L162，避免每轮重写、保持 prompt cache 前缀稳定）。
- **消息级聚合预算** `enforceToolResultBudget()`（~L769）：单条 user 消息里多个 tool_result 总和超 `getPerMessageBudgetLimit()` 时，挑**最大的若干个**落盘。关键纪律：**一旦某结果被"看见"，它的命运冻结**（`seenIds`/`replacements`，~L390）——已替换的每轮重放同一预览串（零 IO、字节一致），已发原文的永不事后替换。全为 prompt cache 前缀稳定服务。

**对 Akane**：抓网页/搜索结果是天然超限源。当前 Akane 把结果塞进 `followup_context` 进下一轮 user_prompt，没有落盘阈值。可借鉴：(1) 给 `ToolResultEnvelope` 加一个"超限就落盘 + 喂回预览+路径"的塑形步骤；(2) **空结果注入占位句**这条几乎零成本，建议先抄。但注意 CLAUDE.md §3：路径不进 snapshot/prompt 的红线——Akane 若落盘，喂回的路径要么是工作区相对路径、要么走 workspace 抽象，**不能像 Claude Code 那样直接喂绝对路径**。

## A.2 SkillTool 调用全过程：skill 正文如何进入下一轮上下文

**核心：SkillTool 的 `tool_result` 本身只是一句"Launching skill: X"，真正的 skill 正文是通过 `newMessages` 作为独立 user 消息注入对话的。** 实现在 `src/tools/SkillTool/SkillTool.ts`。

调用链（`call()` ~L580）分三种执行形态：

1. **inline（默认）**：
   - `processPromptSlashCommand(commandName, args, …)`（~L638）把 skill 展开成完整 prompt 消息（做 `!command` 替换、`$ARGUMENTS` 插值）。
   - 产出的 user/attachment/system 消息经 `tagMessagesWithToolUseID(…, toolUseID)`（~L735）打上 `sourceToolUseID`——**在本工具 resolve 前保持 transient**。
   - 返回 `{ data:{...}, newMessages, contextModifier }`（~L767）。
   - **`mapToolResultToToolResultBlockParam()`（~L843）：inline 时 tool_result content 只是 `Launching skill: ${commandName}`**——真正正文不在 tool_result 里，而在 newMessages 里。
   - `contextModifier(ctx)`（~L775）：把 skill 声明的 `allowedTools` 并进 `alwaysAllowRules`、套用 `model`/`effort` 覆盖。**这是 skill 携带能力门控/模型覆盖进入后续轮次的通道。**

2. **forked（`command.context === 'fork'`）** `executeForkedSkill()`（~L122）：在隔离子 agent（独立 token 预算）里 `runAgent()` 跑 skill，只把 `extractResultText()` 的结果字符串回填到 tool_result（~L843 的 forked 分支：`Skill "X" completed (forked execution).\n\nResult:\n…`）。正文不进主上下文。

3. **remote canonical（实验、ant-only）** `executeRemoteSkill()`（~L969）：从 GCS/AKI 拉 SKILL.md → `parseFrontmatter` 剥 frontmatter → 注入"Base directory: …"头 + 替换 `${CLAUDE_SKILL_DIR}`/`${CLAUDE_SESSION_ID}` → 作为 `createUserMessage({content, isMeta:true})` 注入（~L1101）。

**newMessages 怎么落地** `src/services/tools/toolExecution.ts` ~L1565：`if (result.newMessages?.length) { for (…) resultingMessages.push({message}) }`——工具返回的 newMessages 被原样追加进对话消息流，**下一轮 query 时就是普通上下文**。

**survive compaction**：inline/remote 都调 `addInvokedSkill(commandName, path, finalContent, agentId)`（~L1088）登记，压缩后可恢复 skill 正文。

**对 Akane**：这就是"skill 正文如何进入下一轮"的权威答案——**不是塞进 tool_result，而是工具产出 `newMessages` 注入对话，tool_result 只留一句确认**。Akane 将来做 skill，对应物是：工具执行后除了 `model_feedback`，还要能产出"追加到下一轮上下文的消息"。当前 `ToolResultEnvelope` 只有 `model_feedback`/`data`/`events`，**缺一个 `new_context_messages` 之类的通道**——这是做 skill 前要补的结构位。另外 inline 的 `contextModifier`（带 allowedTools/model 进后续轮）对应 Akane 的"能力门控按轮"（INV-5），方向一致。

## A.3 MCP 工具的 prompt 暴露与权限

**A.3.1 何时进 prompt vs 延迟加载** — `src/tools/ToolSearchTool/prompt.ts` `isDeferredTool(tool)`（~L62）是唯一裁决点，优先级自上而下：

1. `tool.alwaysLoad === true` → **永不延迟**，turn-1 带完整 schema 进 prompt（~L65）。MCP 工具通过 `_meta['anthropic/alwaysLoad'] === true` 设此位——见 `src/services/mcp/client.ts` ~L1785 `alwaysLoad: tool._meta?.['anthropic/alwaysLoad'] === true`。**即：MCP 服务端可声明"我这个工具必须常驻 prompt"。**
2. `tool.isMcp === true` → **默认延迟**（~L68，注释"workflow-specific"）。即未显式 alwaysLoad 的 MCP 工具，默认不进初始 prompt，靠 ToolSearch 按需拉 schema。
3. ToolSearch 自己永不延迟（~L71）；FORK_SUBAGENT 下的 Agent、KAIROS 下的 Brief/SendUserFile 等通信通道工具强制不延迟（~L76–105）。
4. 其余看 `tool.shouldDefer === true`（~L107）。

延迟工具只在 prompt 里露**名字**（`formatDeferredToolLine` 仅返回 `tool.name`，~L115；searchHint 经 A/B 验证无收益已不渲染）。模型用 ToolSearch 的 `select:Name` / 关键词 / `+prefix` 三种查询形态取回完整 JSONSchema（~L48–51）。delta 开关决定延迟工具名是经 `<system-reminder>` 还是 `<available-deferred-tools>` 宣告（`getToolLocationHint` ~L35）。

**A.3.2 权限/确认** — MCP 工具走与原生工具完全相同的 `checkPermissionsAndCallTool` 管线，但 `MCPTool.checkPermissions()`（`src/tools/MCPTool/MCPTool.ts` ~L56）返回 `{ behavior:'passthrough' }`——即**不在工具内自决，交回上层权限系统**（用户的 allow/deny 规则、确认弹窗）。对照 SkillTool 的 `checkPermissions`（SkillTool.ts ~L432）：先查 deny 规则 → 安全属性白名单自动放行（`skillHasOnlySafeProperties` ~L910，未知属性默认需确认，新属性默认收紧）→ 否则 `behavior:'ask'` 弹确认并给"加 allow 规则"建议。

**对 Akane**：Akane 现在的 `promptExposed`（每轮把工具清单塞进系统提示）对应 Claude Code 的"`alwaysLoad` 类工具"。三点直接可对照：
- **MCP 默认延迟**这条说明 Claude Code 不把所有 MCP 工具常驻 prompt，而是 turn-1 只露名、按需拉 schema——Akane 若将来接很多 MCP 工具，`promptExposed` 全量铺开会撑爆系统提示，应学这套"露名 + 按需展开"。
- **`passthrough` 权限**：MCP 工具不该自己决定能不能跑，交回 Akane 的能力门控/确认层——对应当前后端的能力配置（`local_capability_config`）。
- **安全属性白名单 + 未知默认收紧**（SkillTool.ts ~L875 `SAFE_SKILL_PROPERTIES`）：fail-closed 思路，新增字段默认需确认。Akane 做 skill/MCP 权限时值得照搬这条默认收紧纪律。

## A.4 三专题落到 Akane 的结构缺口（仅记录，不改）

1. `ToolResultEnvelope` 缺 **`new_context_messages` 通道**（A.2）——skill/复杂工具要往下一轮注入正文，当前只有 `model_feedback` 不够。
2. `ToolResultEnvelope` 缺 **超限落盘塑形步**（A.1）——抓网页/搜索是天然超限源；落盘喂回时**路径必须走 workspace 相对/抽象，不能像 Claude Code 喂绝对路径**（CLAUDE.md §3 红线）。
3. **空结果占位句**（A.1）几乎零成本，可最先抄。
4. MCP 规模化前需要 **"露名 + 按需展开"** 的工具暴露策略（A.3），替代 `promptExposed` 全量铺开。

> 以上均为只读调研结论，未改任何代码。配套：`docs/tool_system_decoupling_v1.md`（设计）、`docs/engineering_invariants_v1.md`（不变量）。
