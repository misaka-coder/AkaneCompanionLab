# 子代理定位与独立模型配置调研

日期：2026-10-11。Akane 核对基线：`f938431d`。

本轮只读核对代码、既有审计及外部资料，新增这份调研。没有修改运行代码、生产模型配置或数据库，没有派发子任务，也没有重启。以下实施建议尚未执行。

## 结论

建议保留现有独立任务上下文、共享工具执行、Host Jobs 和结果回传，优先补独立模型配置与失败交接；随后将确实需要继续工作的子代理扩展为可接收后续任务的会话。

一次性子任务也是主流方案，并不是设计错误。我们的不足是目前只有这一种生命周期，开发任务失败或交付后，很难在原子会话上继续。仅补一次重新派发，仍会丢失原任务的工作上下文。

使用方式应符合用户偏好：主模型优先自己完成，只有明确独立、有预期收益的工作才委派。子代理数量不代表能力，工具等待和工具并发也不需要另开一个模型。

独立模型应能配置完整服务路线，包括服务商、协议、地址、凭据、模型和思考设置。单独改模型名不足以让 PinAI 上的主模型配合官方 DeepSeek 等另一服务的子模型。

## 外部对照

| 参考 | 核对到的做法 | 对 Akane 的启发 |
| --- | --- | --- |
| OpenAI 官方 Subagents 文档 | 独立工作上下文用于隔离过程和汇总结果；支持子模型和思考强度配置；并行委派有额外 token 成本 | 对独立、边界明确的工作使用子代理；同模型并行也有时间或上下文收益，但不能等同于省钱 |
| OpenAI Responses Multi-agent 文档 | 提供创建、消息、后续任务、等待、打断、列举等动作，打断不删除上下文 | 区分一个子代理会话与它的一次执行；后续工作可以继续使用原会话。这里核对的是 API 托管编排，不是 Akane 已有的接口 |
| Claude Code 官方文档 | 同时存在一次性探索代理和可恢复的一般代理；独立模型可配置；后续消息能继续原代理，API 错误会报告未完成及可用的部分输出 | 按任务需要决定是否保留继续工作的能力；失败交接不能只给一个错误码 |
| 本地 dsh 源码 | 区分 `one-shot` 和 `continuable`；子模型路线通过 `agentOptions` 配置；后者可以在工作边界接收消息、空闲时开始新轮 | 生命周期扩展应基于会话和现有调度，不另造一套文件工具或命令运行器 |
| OpenCode V2 官方文档与固定版本源码 | 子会话继承父会话的项目位置；支持独立模型、前台等待、后台立即返回、完成通知，以及按原 sessionID 接续 | 上下文隔离不等于工作区隔离；一次执行与可继续的子会话应分别管理 |
| Gemini CLI 官方文档与固定版本源码 | 自有模型、工具和历史循环；本次核对的本地调用入口等待报告，同时将活动更新用于界面显示；循环在工具回合间接收新提示与后台结果 | 界面进度与父模型输入是两条路径；不同产品不必使用相同的后台协议 |

在线来源，读取日期为 2026-10-11：

- [OpenAI 官方 Subagents 文档](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- [OpenAI Responses Multi-agent](https://developers.openai.com/api/docs/guides/responses-multi-agent)
- [Claude Code：Create custom subagents](https://code.claude.com/docs/en/sub-agents)

dsh 使用本地干净快照 `aa8262ec091698bae9a6b04773a6b5b06ad4aef2`，不是远端最新版本或性能实测：

- [委派工具与模型选择](https://github.com/deepseek-ai/DeepSeek-Harness/blob/aa8262ec091698bae9a6b04773a6b5b06ad4aef2/packages/subagent/tool-subagent/README.md)
- [模型路线及执行模式的实际接线](https://github.com/deepseek-ai/DeepSeek-Harness/blob/aa8262ec091698bae9a6b04773a6b5b06ad4aef2/packages/subagent/tool-subagent/src/index.ts#L475)
- [父子消息控制工具](https://github.com/deepseek-ai/DeepSeek-Harness/blob/aa8262ec091698bae9a6b04773a6b5b06ad4aef2/packages/subagent/tool-subagent-control/src/index.ts#L28)
- [独立上下文的进程内子代理](https://github.com/deepseek-ai/DeepSeek-Harness/blob/aa8262ec091698bae9a6b04773a6b5b06ad4aef2/packages/subagent/subagent-spawn-in-process/src/index.ts#L40)

## 当前实际调用链

```text
spawn_subagent(task, label)
  → 读取父任务模型、思考设置、已选项目和可用工具
  → 持久登记 Host Job，立即返回 job_id
  → 独立 child_session 执行模型与工具循环
  → 工具轨迹存入该 child 的 MemCore
  → 终态摘要、产物及失败原因通过现有完成事件交给父模型
```

| 位置 | 实际行为 | 判断 |
| --- | --- | --- |
| `tool_handlers/subagent.py:75,113` | 从父回合的模型目标取模型和思考强度 | 没有独立子模型配置入口 |
| `subagent_engine.py:81` | 在宿主当前聊天/视觉路线中重新解析，再核对准入指纹 | 仅给请求换一个模型名仍然没有独立客户端；配置变化发生在排队与执行之间时可能失败 |
| `subagent_engine.py:130,133` | 输入是任务、工具目录和环境；轨迹使用 child 专属 profile/session | 不复制伴侣聊天、关系状态和父会话记忆是合适的隔离 |
| `subagent_engine.py:225` | 使用公共 LLM 适配器，工具经公共批执行和宿主处理器 | 协议和工具基座可以继续复用，但子任务有自己的循环，不能假定主聊天的所有恢复与预算逻辑自动生效 |
| `subagent_engine.py:232` | LLM 适配器返回错误或 fallback 后立即结束 child；该分支没有传已累计 artifacts | 是已观察失败场景需要处理的接续和交接缺口。适配器另有特定 JSON 传输恢复，不能笼统声称所有路径都完全不重试 |
| `host_subagent_jobs.py:150,264` | 有活跃执行的暂停/恢复/停止；失败以 `retryable=False` 结算 | 活跃暂停后恢复不等于失败后恢复；没有为终态 child 追加新任务的入口 |
| `model_service_config.py:115`、控制中心模型页 | 有聊天、独立视觉等配置，没有子代理模型配置 | 设置、客户端组装、准入和实际请求必须一起接通 |

上述代码路径都在 `companion_v01/` 下；模型页为 `desktop_pet_next/src/control-center-v2/components/model.js`。

既有真实长任务审计记录了四个 child：一个成功、两个连接错误、一个请求超时，部分 child 已经写过代码。失败结果确实回到了父模型，但这不证明失败接续已经实现，也不能据此把所有连接问题归因于本地编排。详见 [轨迹复核与待处理问题](tool_runtime_trace_fixes_v1.md)。

## 建议的定位

主模型负责理解用户意图、关键决策、跨模块协调、验收与面向用户的交付。子代理负责已经说得清楚的独立工作，返回结论和实际证据。

| 工作 | 建议 |
| --- | --- |
| 聊天、简单改动、需要不断与用户讨论的设计 | 主模型处理 |
| 下载、长测试、长命令，需要等结果 | 使用已有后台工具；等待时间本身不是委派理由 |
| 阅读一批资料、排查独立目录、分析大量日志 | 适合用单独模型处理，主模型接收简短结论及来源 |
| 已定位问题后的一个实现切片、独立验证 | 可委派给足够可靠的便宜模型，主模型复核和整合 |
| 模糊问题或几个阶段高度依赖同一背景 | 主模型先厘清；便宜子模型不能自动替代这部分判断 |

不强制创建固定的“研究员、程序员、测试员”阵容。第一步只需要一个可独立配置的通用子模型，任务说明决定它这次做什么。以后确有不同模型表现差异，再增加具名配置。

同模型子代理仍可能通过独立上下文、并行和第二视角带来收益；但我们的主会话已有较高缓存命中，委派还要付出任务说明、子模型独立请求、重新读资料和父模型复核的成本，短任务未必划算。

总成本应按“父模型编排与验收 + 子模型执行 + 失败重做”评估。本轮没有对不同模型做费用或质量实测，不给出节省比例。

## 独立模型配置的落点

建议在控制中心现有“模型服务”页增加一个子代理配置区，复用服务商选择、模型检测、API 测试、保存与密钥处理。保留明确的“跟随主模型”选项，用户选择独立配置时以其为准。

| 配置 | 作用 |
| --- | --- |
| 服务商、协议、Base URL、API Key | 允许主模型和子模型使用不同接口；同服务可明确复用主服务凭据 |
| 模型 | 一个独立默认模型；第一步不让模型每次自行挑服务或传密钥 |
| 思考模式与强度 | 按子模型自己的协议和支持项解析，不能把父模型的 `max` 等设置机械带过去 |
| 输出和上下文预算 | 按子模型能力使用相应预算，复用现有配置/预算组件；不能认为所有子模型和主模型窗口一样 |

真实接线需要覆盖：

1. 配置存储、公开状态与前端表单，密钥不回显。
2. `ModelTargetsMixin` 组装独立子模型目标；`SpawnSubagentToolHandler` 判断的是子目标是否可执行，不能仍只检查父聊天客户端。
3. 准入选定目标、任务级配置副本、驱动实际请求和既有协议适配。复用同一工具执行链，不绕开宿主。
4. 已开始的执行固定自己的目标，后续配置修改用于新执行；排队/恢复的路线引用只保留必要的非敏感字段，不把密钥或客户端放进 Job、提示词或审计。
5. 不可用时保留原因和已有工作，由父模型决定接手。不要为了让子任务“成功”而无声换回昂贵主模型。

独立子模型的运行状态和选择不需要每轮注入主聊天系统提示；父模型只需知道子任务能做什么。子任务启动回执可以简短标明实际模型，详细使用量走已有指标和审计，不混进普通聊天命中率。

## 生命周期改进的范围

建议分两步讨论和实施：

**第一步：独立模型、可靠失败交接和请求恢复。** 在现有一次性 Host Job 上完成完整路由；失败报告保留最后有效输出、已核验产物及未完成内容。短暂传输失败的恢复只针对尚未取得有效响应的模型请求，不从头重跑整个 child，不重复执行已经落库的工具调用。具体错误类别和次数在实施时依据公共 LLM 恢复入口确定，不能承诺远端请求刚好执行一次。

**第二步：继续原子会话。** 子代理会话与某次 Host Job 执行分开看待，沿用 child 的 MemCore 和现有 Host Jobs。父模型可以在请求边界追加更正；已交付或失败后，可以使用原会话历史开始下一次执行。旧 Job 的终态和消费事实保留，新执行有自己的状态和结果。复用消息协调和完成事件，不另建一套任务调度权威。

第二步不需要立即加入兄弟代理互聊、递归派生、完整父会话复制或常驻团队面板。共享项目修改仍由父模型明确切片并验收；工作区隔离可以按任务选择，不能让新增协作层变成文件操作的重复审批和隐形限制。

新子会话中的工具协议、历史投影和预算应服从选中的子模型。继续同一 child 时保持自己的历史追加与缓存；父会话只收到必要报告，子代理生命周期变化不重写父会话稳定前缀。

## 后续验收重点

- 主子模型不同、服务不同、思考设置不同，实际请求和回执一致。
- 修改主模型不把已运行的子任务换走；独立配置缺失或失效有清楚原因。
- 子模型完成连续原生工具调用、并发工具和前台转后台等待，父模型只收到需要的结果。
- 连接错误发生在代码写入后，仍保留可接手的报告和产物；模型请求重试不重放已执行工具。
- 后续消息真正进入原 child 历史；停止、重复完成通知、父会话切换等边界沿用现有协调链路。
- 分开统计主模型与子任务的 token、命中、耗时、失败和交付质量，验证性价比。

本轮验证仅为源码/资料核对及文档格式检查，没有调用付费模型、运行新子代理、进行跨模型兼容测试或测量费用。长任务后的聊天延迟问题仍是另一条待处理任务，不能用本次子代理调研宣称已经解决。

## 补充核实：工作区、报告和接续

追加核对日期：2026-10-11。这里只记录官方资料与实际源码，不把尚未运行的代码当作生产体验测试。

### 工作区与修改方式

Claude Code 的常规子代理从主会话工作目录开始；`isolation: worktree` 才提供独立仓库副本。具备编辑能力时可以直接修改文件，并非默认只生成 diff 等待主模型应用。参见 [工作目录与隔离说明](https://code.claude.com/docs/en/sub-agents#frontmatter-reference)。

OpenCode V2 的创建路径明确取 `parent.location`，子代理工具没有额外的默认 worktree 创建或补丁合并阶段。源代码核对的是默认分支快照 `d21b0790a6c146815f28fea604e41e922f009590`，不要与 V1 的 Task 工具文档混用：

- [子会话继承父项目位置](https://github.com/anomalyco/opencode/blob/d21b0790a6c146815f28fea604e41e922f009590/packages/core/src/session.ts#L262)
- [子代理实际创建、模型选择与接续](https://github.com/anomalyco/opencode/blob/d21b0790a6c146815f28fea604e41e922f009590/packages/core/src/tool/plugin/subagent.ts#L153)
- [V2 代理配置文档](https://opencode.ai/v2/docs/agents)

因此 Akane 保留共享工作区符合常见做法。主模型负责核查真实文件差异、验证和整合；共享文件已经写入，不需要再自动应用一份补丁。以后按需加入工作区隔离即可，本轮不新增 worktree 管理系统。

### 前台与后台不是统一标准

OpenCode V2 的前台调用等待最终报告，后台调用立即返回 sessionID，再发自动完成通知；该 ID 还能继续原子会话。其失败通知保留可接续的 ID，但并不是所有失败出口都自动附完整的部分报告。参见 [调用分支](https://github.com/anomalyco/opencode/blob/d21b0790a6c146815f28fea604e41e922f009590/packages/core/src/tool/plugin/subagent.ts#L229) 和 [完成事件](https://github.com/anomalyco/opencode/blob/d21b0790a6c146815f28fea604e41e922f009590/packages/core/src/session/subagent-completion.ts#L21)。

Gemini CLI 使用源码快照 `9b6e0265d16bbd29ca51e33c9e0c01dc4cec5e83`。本次核对的本地入口等待 `getResult()`，活动更新交给 `updateOutput`，父模型最后收到结果与终止原因。它没有在该入口暴露“立即返回可接续 ID”的参数，不能据此把所有远程代理或其他后台机制一并断言为不支持。参见 [本地调用与报告](https://github.com/google-gemini/gemini-cli/blob/9b6e0265d16bbd29ca51e33c9e0c01dc4cec5e83/packages/core/src/agents/local-session-invocation.ts#L280) 和 [代理文档](https://geminicli.com/docs/core/subagents/)。

Akane 已有后台准入、`exec_status` 查询、请求边界汇集和空闲会话通知，可以继续使用。父模型只需任务报告、真实状态和必要产物，不自动复制子代理的全部工具历史。

### 恢复与循环的取舍

Claude Code 能继续原代理历史，并在 API 失败时保留可用的最后输出。其可配置模型回退链不是 Akane 本轮目标；我们不自动把便宜子模型升级为主模型。参见 [失败处理](https://code.claude.com/docs/en/sub-agents#api-errors-in-subagents) 和 [接续](https://code.claude.com/docs/en/sub-agents#resume-subagents)。

OpenCode V2 有公共的请求恢复策略，区分传输超时；其重试次数和延迟并不适合直接照搬到我们的伴侣交互。参见 [请求恢复源码](https://github.com/anomalyco/opencode/blob/d21b0790a6c146815f28fea604e41e922f009590/packages/core/src/session/runner/retry.ts#L42)。

Gemini CLI 本地循环也在请求之间追加提示与后台结果；它另要求 `complete_task` 交卷，并有轮数和时间上限及最后一次报告机会。可借鉴报告交接，保留 Akane 的原生工具优先和任务报告出口，无需新加必调完成工具或固定十分钟终止。参见 [回合间追加](https://github.com/google-gemini/gemini-cli/blob/9b6e0265d16bbd29ca51e33c9e0c01dc4cec5e83/packages/core/src/agents/local-executor.ts#L685) 和 [完成判定](https://github.com/google-gemini/gemini-cli/blob/9b6e0265d16bbd29ca51e33c9e0c01dc4cec5e83/packages/core/src/agents/local-executor.ts#L340)。

OpenAI Docs 的 Codex 子代理配置支持独立模型与思考设置；Responses 的托管 Multi-agent 文档则明确描述同请求的模型和工具共享。这是不同产品层的能力，不能将两者混写为“所有 OpenAI 子代理都可分别配置服务商”。参见 [Codex 模型配置](https://learn.chatgpt.com/docs/agent-configuration/subagents#choosing-models-and-reasoning) 和 [托管 API](https://developers.openai.com/api/docs/guides/responses-multi-agent#quickstart)。

实施草案见 [子代理模型、恢复与接续执行单](subagent_model_recovery_execution_plan_v1.md)。
