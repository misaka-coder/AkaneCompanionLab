# 会话串行与后台任务 V1

状态：实施中。QQ 与桌宠已接入同一持久化会话主链；实例级 Job 存储已完成幂等、租约、恢复、重试、取消和完成投递状态。内置 `execution_class=long_task` 与长时 Shell run 均已接入统一 Job 和 Agent-event 主链；生图与 Shell 的真实渠道纵向验收、插件后台工作迁移仍待完成。

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

## 6. 工具注册语义

工具或插件能力可以声明默认行为：

```yaml
execution_class: long_task       # sync | long_task
completion_mode: agent           # agent | direct | silent
memory_mode: timeline            # current_turn | timeline
```

- `execution_class` 决定是否脱离当前回合。
- `completion_mode` 决定完成时是否再次请求模型。
- `memory_mode` 决定完成事实只服务当前回合还是进入 MemCore 时间线。

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

是否继续请求模型由宿主真实回执决定：

```yaml
visible_effect_delivered: true
needs_model_followup: false
```

这些事实由宿主交付层生成，插件和模型不能自行声明成功。成功且无需补充时，不再启动一次模型说“操作成功”；失败则允许 Agent 解释、重试或降级。

## 10. 子代理的后续接入

子代理不是本轮首个实现切片，但复用同一基础设施：

- 子代理拥有独立上下文和工作任务；
- 创建成功后立即返回 agent/job id；
- 父 Agent 继续处理不依赖子代理结果的工作；
- 需要结果时再等待；
- 子代理完成后把选定结果提交给父 Agent；
- 父 Agent 负责整合、验证和最终交付。

子代理内部轨迹不整段灌入父上下文。普通聊天角色不常驻子代理控制说明；只有相关编程 Skill/模式暴露简短指导和必要工具。

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
- DSH background jobs: `F:\Akane\_refs\deepseek-harness\packages\jobs\tool-jobs\README.md`
- DSH subagents: `F:\Akane\_refs\deepseek-harness\packages\subagent\tool-subagent\README.md`

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
6. 以生图完成第一条 QQ + 桌宠纵向验收。
7. 将现有 Shell run 状态接入 Job 权威和完成通知。（已完成，真实渠道验收归入下一轮 smoke）
8. 迁移 ComfyUI、媒体处理和插件后台工作。
9. 删除旧的 route-owned/in-memory Job 权威与重复通知路径。
10. 再设计并接入子代理。
11. 最后实现经过能力门控的 Responses 原生异步。

每一项单独验证和提交，不在同一切片同时重写 UI、MemCore 与 Provider 适配。

当前第 7 项已完成代码接线：`exec_run` 在进程启动前登记并领取 Job；只有首个等待窗口结束后仍为 `running` 才武装一次 Agent 完成事件，短命令保持原有单回合结果。执行器终态是唯一触发点，`exec_status`/`exec_cancel` 和输出游标协议没有变化。群聊延迟事件通过宿主签名的会话引用恢复原发起成员的授权与工作区身份，不把身份选择交给插件或模型。宿主重启后无法安全接管的旧进程会明确结算为 `host_restart_process_unavailable`，不会伪造成功或重复执行命令。

## 14. 删除条件

替代链路通过故障与真实渠道验收后，删除：

- route 内独立维护的工作流 Job 字典；
- `SessionWorkQueue` 对消息内容的唯一内存所有权；
- 声明 `long_task` 却同步占住模型回合的路径；
- 插件自建的角色推理或完成投递路径；
- 重复的状态、轮询和假进度逻辑。

在替代链路真正接通前，旧路径只允许作为明确的迁移边界存在，不能与新实现长期共同成为权威。

## 15. 核心验收

- 同一私聊连续输入不会并行启动两个模型回合或产生重复回答。
- 群聊多用户输入按一个会话的顺序处理且身份不串。
- 不同会话保持并发。
- 排队消息和已登记 Job 在宿主重启后可恢复。
- 生图迅速返回 Job acknowledgment，生成期间仍可继续聊天。
- 生图完成后由正确角色、正确 MemCore 和正常表现层处理。
- Job 重试不重复创建任务或重复投递完成事件。
- 工具调用与结果在 OpenAI、Anthropic 及兼容 Provider 上保持合法配对。
- 可见动作成功时不产生机械的二次回复，失败时不静默。
- 插件与内置能力走同一 Job 和 Agent-event 主链。
- QQ、桌宠与控制中心只展示真实状态，无假进度。
- 普通轮次的系统前缀和工具 schema hash 保持稳定。
