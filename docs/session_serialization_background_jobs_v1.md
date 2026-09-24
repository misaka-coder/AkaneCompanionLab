# 会话串行与后台任务 V1

状态：实施中。QQ 与桌宠已接入同一持久化会话主链；实例级 Job 存储已完成幂等、租约、恢复、重试、取消和完成投递状态。内置媒体、插件 `execution_class=long_task`、长时 Shell run、控制中心工作流及一次性子代理均已接入统一 Job 权威；旧的 route-owned 内存工作流 Job 已删除。真实 QQ/桌宠纵向验收和可选的 Provider 原生异步仍待完成。

2026-09-08：完成事件在原 inbox 领取边界增加兼容聚合，QQ/桌宠共用投影，保留逐任务结果与发送状态；冻结批次、原子结算、FIFO 修复和已开始回合的未知结果保护见 [后台任务完成事件聚合](host_completion_aggregation_20260908.md)。

## 1. 目标

Akane 需要同时满足两件事：

- 同一会话中的用户消息、模型回复和 MemCore 时间线保持可解释的顺序；
- 生图、媒体处理、长命令和插件后台工作不能长时间占住会话。

本设计为 QQ、桌宠、插件和以后可能加入的渠道提供同一套宿主语义。插件可以隔离运行，但不能拥有第二套任务、角色回复或投递机制。

## 2. 已确定的原则

1. 同一会话同时只运行一个模型回合，不同会话可以并发。
2. 新消息先可靠进入宿主收件箱，再等待当前回合结束或进入安全的 steer 边界。
3. 长任务在可靠登记后立即让出模型回合，完成后通过宿主事件继续。
4. 内置工具与插件共用后台任务、产物、事件和 Agent 主链。
5. 工具调用始终有协议上的对应结果；用户是否需要再看到一句文字由真实效果决定。
6. 生图完成后默认唤醒完整角色 Agent，由模型决定如何发送图片和组织回复。
7. Shell 保留现有 `exec_run`、`exec_status`、`exec_cancel`，不新增一套同义工具。
8. OpenAI Responses 原生异步是最后接入的传输优化，不作为跨 Provider 正确性的前提。

## 3. 两种异步语义

### 3.1 跨回合后台任务

适用于生图、定时事件、媒体处理和可能持续较久的插件工作：

```text
模型调用工具
  -> 宿主持久化 Job
  -> 返回 accepted + job_id
  -> 模型可以简短回应并结束当前回合
  -> Job 后台执行
  -> 完成事件进入同一会话收件箱
  -> 按配置唤醒 Agent、直接投递或静默结算
```

这里的 `accepted` 是该次“启动任务”调用的完整工具结果。最终产物属于 Job 完成事件，不是几轮以后补回的迟到工具结果。

### 3.2 同一 Agent 任务内的异步调用

适用于编程 Agent 的慢测试、独立查询和子代理：

```text
模型发起异步调用
  -> 工作在后台继续
  -> 模型处理不依赖结果的步骤
  -> 结果完成后按原 call_id 回到同一 Provider 链
  -> 模型综合结果后交付
```

这种模式依赖 Provider 和模型真正支持异步工具协议。宿主必须保存 pending call 和 Provider continuation 状态，不能只看到 `protocol=responses` 就假定支持。

两种模式共用 Job、所有权、取消、产物和完成事实，但具有不同的模型协议。运行时不得把它们混成一种。

## 4. 会话顺序

每个会话拥有一个持久化 inbox。消息到达后先取得单调序号，再由单一领取者处理。

```text
arrived -> queued -> claimed -> committed
                    \-> failed/retryable
```

- 同一用户在活动模型回合中补充消息：先持久化；安全边界内可以 steer，否则成为下一回合。
- 群聊中其他用户的消息：进入同一群会话 FIFO，不抢占当前回合。
- 插件事件和 Job 完成事件：使用相同 inbox，不建立专用队列。
- 不同会话：各自领取，可以并发。
- `source_event_id` 保证一条输入最多形成一次模型可见时间线记录。

`TurnCoordinator` 继续管理活动回合和 steer 边界。持久化 inbox 管理可靠排队与恢复；两者不得形成两个顺序权威。

## 5. Job 权威

Job 使用宿主现有实例数据库，不新建独立数据库。最小状态为：

```text
queued -> running -> succeeded
                  -> failed
                  -> cancelled
```

每个 Job 至少记录：

- `job_id`、能力来源和参数指纹；
- 会话、角色、用户与渠道引用；
- `turn_id`、原始 `tool_call_id` 和因果来源；
- 幂等键、状态、租约、尝试次数和时间；
- 结果摘要、artifact 引用和完成事件 ID。

Job 表只保存状态和引用。图片、音频、文件及大段日志继续由现有产物/执行资源边界管理。

任务必须先成功提交数据库，才能向模型返回 `accepted`。若提交失败，返回真实错误，不创建假 Job。

后台执行可先复用现有线程和执行器，但它们只是工作者；数据库中的 Job 才是恢复、查询和去重权威。

### 失联、取消与通知的真实语义

- 重启后，尚未开始的任务可以继续领取。已经运行但没有确认结果的任务由 Job 存储统一结算为 `failed`，原因是 `host_restart_outcome_unknown`；租约失效使用 `lease_expired_outcome_unknown`。这表示宿主失去结果确认，不表示外部操作一定没发生，也不表示外部进程已停止。不会自动重跑这些任务。
- 执行器明确确认可安全重试的失败仍可调用 `fail(retryable=True)`。幂等 Job ID 只能防重复登记，不能代替外部副作用的幂等保证。
- 排队任务可以直接取消。运行中的取消是请求，只有执行器确认停止才记为 `cancelled`；实际成功仍记为成功，失败或失联不会因为收到取消请求而伪装成取消成功。收到取消请求后不再自动重试。
- Shell 仍通过现有执行器终止进程，子代理 provider 合同支持协作取消。普通长工具目前没有通用的中途终止桥，不能向用户承诺任意生图或插件调用立即停止；后续接真实执行器取消能力，不加空回调占位。
- 完成投递使用独立的 `host-job-completions` lane，不与长工具竞争执行 worker。普通失败、执行异常和排队取消都形成同一条持久化完成事实。投递失败保留 pending，现有恢复入口可重试；不反向修改执行结果。
- `silent` acknowledgment 明确不自动通知；重复提交说明没有重复启动并返回已有状态。内部状态修复不新增模型工具、不动态修改 schema 或稳定提示前缀。

## 6. 工具注册语义

工具或插件能力可以声明默认行为：

```yaml
execution_class: long_task       # sync | long_task
completion_mode: agent           # agent | silent
memory_mode: timeline            # current_turn | timeline
```

- `execution_class` 决定是否脱离当前回合。
- `completion_mode` 决定完成时是否再次请求模型。
- `memory_mode` 决定完成事实只服务当前回合还是进入 MemCore 时间线。

当前只接受已经闭环的 `agent` 与 `silent`。`direct` 只有在宿主具备独立、可验证的渠道投递回执后才会开放；现阶段明确拒绝，不能静默退化为 Agent 回复。

三项彼此独立。单次调用可以在注册允许的范围内覆盖默认完成方式；宿主最终验证会话、角色、权限和真实投递结果。

插件可以声明这些语义，但不能自行启动简化模型、直接写 MemCore，或伪造宿主投递回执。

## 7. 生图的目标链路

生图默认：

```yaml
execution_class: long_task
completion_mode: agent
memory_mode: timeline
```

真实流程：

1. 模型调用生图能力。
2. 宿主保存 Job 并返回 `accepted + job_id`。
3. 当前 Agent 可以自然说明正在生成并结束回合。
4. 图片完成后登记为当前会话可用 artifact。
5. 宿主把带 artifact 引用的完成事件写入同一会话 inbox。
6. 完整角色 Agent 在最新 MemCore 上下文中被唤醒。
7. 模型调用发送图片能力并组织文字、表情或 TTS。

生成器本身不先发送图片，避免随后 Agent 再发送一次。发送失败时 artifact 仍可重试。

## 8. Shell 的目标链路

现有 Shell 设计保留：

- `exec_run` 在有限窗口内等待；
- 短命令直接返回终态；
- 仍在运行时返回 `running + run_id`；
- `exec_status` 增量读取输出；
- `exec_cancel` 终止运行。

本轮不新增 `execution_mode`，也不要求模型事先准确判断命令耗时。改造重点是：

- 让 `run_id` 接入可靠 Job 状态；
- 命令完成时发布一次完成通知；
- 模型收到通知前无需 `sleep` 或忙轮询；
- 只有真正依赖输出时才调用 `exec_status` 等待或读取。

长期服务和有限命令保持不同的结果语义。启动 ComfyUI 可以返回“服务进程运行中”，一次生图必须形成可终结的 Job。

## 9. 可见动作与模型续推

戳一戳、贴表情、撤回等操作仍产生内部工具结果，以保持协议、日志和错误处理完整。

同步动作的续推契约见 `tool_result_continuation_v1.md`。默认继续；注册允许省略续推，且模型在本次调用明确选择结束、宿主确认成功时，才省去下一次请求：

```yaml
model_followup: optional   # 插件 descriptor.raw 的注册策略
finish_turn: true          # 本次模型调用选择；不是插件业务参数
```

宿主结果中的 `finish_turn` 是执行后事实，不是模型传参的照抄。OneBot 以真实传输成功回执为准；插件以已校验的成功结果为准，不把它解释成消息已投递。同批每项都满足才结束，失败、待交付产物、查询或新的用户插话都不能被截掉。子代理仍需完成自己的最终报告。

后台 `silent + timeline` 仍通过同一个 Job 完成队列写入 MemCore，不请求模型、不发送消息。失败保留 pending，宿主恢复时重试同一个完成事件；不另建通知执行器。`silent + current_turn` 只保留 Job 终态及调用方已有轨迹。Shell 即时完成与子代理内部任务使用后者，避免重复完成事件。

## 10. 子代理的后续接入

子代理不是本轮首个实现切片，但复用同一基础设施：

- 子代理拥有独立上下文和工作任务；
- 创建成功后立即返回 agent/job id；
- 父 Agent 继续处理不依赖子代理结果的工作；
- 需要结果时再等待；
- 子代理完成后把选定结果提交给父 Agent；
- 父 Agent 负责整合、验证和最终交付。

子代理内部轨迹不整段灌入父上下文。普通聊天角色不常驻子代理控制说明；只有相关编程 Skill/模式暴露简短指导和必要工具。

当前权威方案见 `docs/subagent_runtime_v1.md`。`spawn_subagent` 已接入生产组装，继承父工作区快照、模型设置和工具上限，经过正常 Resolver/Broker，在独立 MemCore 域执行；结果经现有 Host Job 完成事件回父会话。已通过隔离真实模型文件任务，尚未部署验证真实 QQ/桌宠投递。

## 11. Responses 原生异步

在通用 Job 与 inbox 稳定后，最后增加可选 Provider 适配：

1. Provider/模型发布经过验证的 `async_tool_calls` 能力。
2. 只有能力稳定时，Responses 工具 schema 才加入 `async: true`。
3. 宿主持久化 `response_id + call_id + job_id` 的 pending call。
4. 同一会话的 Provider continuation 仍串行提交。
5. 工具完成后以原 `call_id` 提交 `function_call_output`。
6. 不支持时自动使用宿主后台 Job，不降低正确性。

该能力只改变 Provider 适配，不改变插件或工具的业务注册协议。

参考：

- OpenAI Async tool calling: <https://developers.openai.com/api/docs/guides/async-tool-calling>
- OpenAI Multi-agent: <https://developers.openai.com/api/docs/guides/responses-multi-agent>
- DSH background jobs: `packages/jobs/tool-jobs/README.md`
- DSH subagents: `packages/subagent/tool-subagent/README.md`

## 12. 模型体验与缓存

模型只需要知道：

- 后台任务已经开始；
- 完成时会收到通知；
- 等待期间继续做独立工作；
- 真正被结果阻塞时再读取；
- 最终交付前收集仍相关的结果，取消不再需要的工作。

租约、恢复、幂等、数据库状态和路由选择不进入提示词。

工具注册默认值属于稳定 schema；运行中的 Job 状态只作为尾部事实出现。普通工具轮不改 system prefix。Provider 原生异步能力在会话模型路由确定时冻结，不能逐轮抖动 schema。

## 13. 实施切片

1. 写不变量测试，锁定当前会话串行、工具配对和 MemCore 顺序。
2. 建立持久化会话 inbox，并让 `TurnCoordinator` 从中领取。
3. 建立唯一 Job 存储、租约、恢复、幂等和取消。
4. 让现有 `execution_class=long_task` 真正返回可靠 Job acknowledgment。（已完成）
5. 将 Job 完成事件接入普通 Agent 主链。（已完成，真实渠道验收归入下一项）
6. 以生图完成第一条 QQ + 桌宠纵向验收。（进程内主链已通过；真实客户端 smoke 待部署）
7. 将现有 Shell run 状态接入 Job 权威和完成通知。（已完成，真实渠道验收归入下一轮 smoke）
8. 迁移 ComfyUI、媒体处理和插件后台工作。（已完成；真实渠道 smoke 仍待执行）
9. 删除旧的 route-owned/in-memory Job 权威与重复通知路径。（已完成）
10. 再设计并接入子代理。（见 `subagent_runtime_v1.md`：一次性 child 驱动及模型入口已完成；通用 Job 查询/取消的模型入口和持续协作仍待后续需求验证）
11. 最后实现经过能力门控的 Responses 原生异步。

每一项单独验证和提交，不在同一切片同时重写 UI、MemCore 与 Provider 适配。

当前第 7 项已完成代码接线：`exec_run` 在进程启动前登记并领取 Job；只有首个等待窗口结束后仍为 `running` 才武装一次 Agent 完成事件，短命令保持原有单回合结果。执行器终态是唯一触发点，`exec_status`/`exec_cancel` 和输出游标协议没有变化。群聊延迟事件通过宿主签名的会话引用恢复原发起成员的授权与工作区身份，不把身份选择交给插件或模型。宿主重启后已运行且无法确认结果的任务使用第 5 节统一失联结算；仅登记尚未关联进程的残留 Shell Job 由执行桥结算为 `host_restart_process_unavailable`，不会伪造成功或重复执行命令。

当前第 8 项的能力调用链已完成：内置生图和媒体工具沿用稳定 `ToolSpec.execution_class`；插件能力可在 `CapabilityDescriptor.raw` 中声明 `execution_class`、`completion_mode` 与 `memory_mode`，无需新工具或提示词段。所有长任务先复用处理器自身的参数归一化、权限和一次性审批，再持久化已准入调用；后台执行不会二次消费审批，也不会在审批前返回假 `accepted`。目前支持 `agent` 和 `silent` 完成方式；`direct` 尚无独立渠道交付契约，因此会在插件审查阶段明确拒绝，不静默映射为 Agent。后台完成只登记 artifact，完成事件明确标记尚未确认发送，由正常 Agent/渠道发送能力负责交付。

当前第 9 项已完成：控制中心工作流不再由路由内字典保存状态和图片字节，而是使用实例级 `HostJobStore`。工作流输入、输出放在受管资产目录，数据库只保存状态、句柄和公开元数据；重启后可以恢复排队任务并继续读取已完成产物。控制中心通过轮询消费结果，因此该类 Job 使用 `silent` 结算，不为 UI 操作错误唤醒角色 Agent。路由只负责身份校验和 HTTP 投影，不再成为第二套 Job 权威。

第 6 项的进程内纵向主链也已锁定：BotRuntime 启动后会把待投递 Job 完成事实送入实例自己的 `HostAgentEventRouter`，保留会话、角色、MemCore 投影方式和 artifact 句柄；QQ 与桌宠适配测试分别验证该事件继续走普通回复渲染/发送和桌宠 frame 合同。外部 NapCat 与真实桌宠卫星的 smoke 必须在部署环境执行，不能由单元测试伪装成已完成。

## 14. 删除条件

route 内独立维护的工作流 Job 字典已经由第 9 项删除。其余替代链路通过故障与真实渠道验收后，再删除：

- `SessionWorkQueue` 对消息内容的唯一内存所有权；
- 声明 `long_task` 却同步占住模型回合的路径；
- 插件自建的角色推理或完成投递路径；
- 重复的状态、轮询和假进度逻辑。

在替代链路真正接通前，旧路径只允许作为明确的迁移边界存在，不能与新实现长期共同成为权威。

## 15. 核心验收

- 同一私聊连续输入不会并行启动两个模型回合或产生重复回答。
- 群聊多用户输入按一个会话的顺序处理且身份不串。
- 不同会话保持并发。
- 排队消息与尚未开始的 Job 在宿主重启后可恢复；已运行未确认的 Job 不重跑，已完成结果可继续投递。
- 生图迅速返回 Job acknowledgment，生成期间仍可继续聊天。
- 生图完成后由正确角色、正确 MemCore 和正常表现层处理。
- Job 重试不重复创建任务或重复投递完成事件。
- 工具调用与结果在 OpenAI、Anthropic 及兼容 Provider 上保持合法配对。
- 可见动作成功时不产生机械的二次回复，失败时不静默。
- 插件与内置能力走同一 Job 和 Agent-event 主链。
- QQ、桌宠与控制中心只展示真实状态，无假进度。
- 普通轮次的系统前缀和工具 schema hash 保持稳定。
