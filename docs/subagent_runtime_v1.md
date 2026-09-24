# 子代理运行时 V1

状态：一次性 child 的生产组装、模型入口与结果回传已实现；已通过进程内纵向测试、隔离的真实模型文件任务及云端 QQ 群真实模型/工具/投递验收。桌宠客户端尚未实测。旧 `delegate_task` / `TaskWorkerService` 不恢复。云端证据与验收边界见 [部署验收记录](subagent_cloud_acceptance_20260905.md)。

后续能力继承与公共批调度改造见 [共享执行基座 V2](subagent_shared_execution_v2.md)。V2 的本地测试与 V1 云端验收分开记录，不代表扩展后的工具集合已经部署实测。

## 1. 目标

子代理用于把可独立执行的工作从当前 Agent 回合中分离出去。它不是第二个面向用户的角色，也不是另一套工具系统。

第一条可交付链只提供一个真实的一次性后台 child：

```text
父 Agent 调用 spawn_subagent
  -> 宿主完成准入并持久化 Host Job
  -> 立即返回 job_id
  -> child 在独立上下文中执行
  -> 结果和 artifact 进入同一 Job
  -> 完成事实回到父会话的普通 Agent-event 主链
  -> 父 Agent 负责验证、整合和最终交付
```

## 2. 权威边界

- `HostJobStore`：任务状态、租约、恢复、幂等、取消和完成投递的唯一权威。
- child 会话：消息与工具轨迹使用 child 专属 MemCore profile/session，即使宿主启用跨会话记忆也不进入父记忆召回域。
- 正常 ToolSpec / Resolver / Broker：父子共用的能力发现、准入和执行边界。
- 父会话 Agent：唯一面向用户组织回复和发送产物的主体。

子代理 runtime 不维护第二份任务状态机，不直接调用 handler，不直接发 QQ/桌宠消息，也不直接写父 MemCore。

## 3. 上下文与权限

child 获得：

- 原始任务 brief；
- 父级当前项目的明确工作目录；
- 父级已选定的 provider、模型和思考强度；
- 本次声明允许的工具集合；
- 宿主生成的短执行说明。

child 不默认获得：

- 父会话完整聊天与伴侣记忆；
- QQ 群成员时间线；
- 人设关系状态和 Care 状态；
- 父 Agent 尚未提交的内部推理；
- 任意新权限。

工具筛选同时作用于 schema 可见性和执行准入。它只能缩小父级已有权限，不能扩大权限。工作目录继承是显式快照；child 的单次 `cwd` 覆盖不会改变父级坐标。

宿主通过 `TaskExecutionScope` 向普通文件和 Shell 处理器传入默认目录；显式 cwd/绝对路径仍优先，附件资源执行仍使用自己的临时目录。该对象不是权限凭证，也不是模型参数；JSON 中同名字段不能伪造宿主作用域。父会话之后切换项目不改变已启动任务的坐标。

执行授权、命令控制和资源归属沿用父会话及原发起者，方便父 Agent 后续检查和接管产物；Broker 幂等账本另外使用 task_id 隔离，避免父子或两个 child 的相同 provider call_id 相撞。独立的是任务上下文和轨迹，不是重新授予一套权限。V2 中 child 长工具经公共 Host Jobs 接收，Shell 保留公共 run_id；完成事件由活跃 child 消费，不另开面向角色的通知。唯一面向父会话的终态来自 child 的 Host Job。

模型客户端复用宿主 LLMRuntime；执行期间使用请求级配置副本，固定模型、思考模式和强度。持久化只保留公开路由指纹，不保存客户端或密钥。排队任务发现当前路由已变化时明确失败，不悄悄换模型。

## 4. Provider seam

运行时依赖一个窄 provider 契约：

```text
execute(request, cancelled) -> terminal result
```

第一版由 `BotRuntimeFactory` 注册 `in_process` provider，使用 `EngineSubagentDriver`。Host Job 在后台 lane 中调用同步 `execute`，通过 `cancelled()` 传递取消状态；父回合只等待持久化准入结果。模型不选择 provider，也不看到 provider 名；以后接其他 provider 时保持同一工具和 Job 语义。

启动前验证 provider 是否支持请求需要的能力。缺能力时明确失败，不能接受后静默忽略。

## 5. 一次性 child 生命周期

```text
queued -> running -> succeeded
                  -> failed
                  -> cancelled
```

一次性 child 使用 Host Job，不新增平行 Task 表。Job payload 保存 brief、父会话引用、工作区引用、工具策略和 child 会话 ID；长输出留在 child 会话，Job 只保存有界摘要和 artifact 引用。

宿主重启时：

- 未开始的 Job 可重新领取；
- 已开始但进程内 child 消失的 Job 由 `HostJobStore` 统一结算为 `host_restart_outcome_unknown`，不另设 child 专用重跑拦截；
- 不从头悄悄重跑可能带外部副作用的 child；
- 已完成但未投递的结果继续走现有完成事件重试。

## 6. 模型表面

第一版只新增一个模型工具：

```yaml
spawn_subagent:
  task: 清晰、独立、可交付的任务说明
  label: 可选短名称
```

成功结果只确认 `job_id` 已可靠登记。完成结果由现有 Agent-event 回到父会话。等待、取消和状态读取优先复用通用 Job 接口；没有通用接口前不新增三套子代理专用同义工具。

`spawn_subagent` 通过现有 execution 能力组暴露，需要真实 provider、Host Job、MemCore 和原生工具协议。已选项目是调用前置条件：没有项目时返回 `workspace_not_selected`，提示调用现有工作区工具；不根据聊天关键词或项目切换动态改写 schema。准入及 child 调用继续经过普通权限检查。运行状态不追加常驻人设提示。

child 从父级实际可用能力继承，再集中过滤伴侣记忆、向用户投递、角色上下文及父会话状态控制，不递归派发。文件、Shell、搜索、浏览器、产物和扩展管理沿用普通工具与授权；MCP 延迟目标只保存可调用名称，不常驻全量 schema。子任务启动后的工具上限是快照，新安装能力在运行中扩展可见性的生命周期仍需单独验证。按现有协议得到的工具错误仍配对回传，child 可以据此纠正调用，不因一次工具名错误直接终止。

## 7. child 输出

child 的终态结果包含：

- `status`：`succeeded | failed | cancelled`；
- 有界 `summary`；
- `artifact_handles`；
- `child_session_id`，用于宿主审计；
- 失败时的结构化 `reason`。

父 Agent 只收到终态摘要和产物引用，不接收整段子代理工具轨迹。需要进一步检查时由宿主提供有界结果读取，不把 child transcript 全量塞回父上下文。

失败报告和已经产生的部分产物同样保留在 Job 中，不只返回错误码。产物句柄取自真实工具回执，忽略模型自行填写的句柄；工具产生的图片继续通过现有媒体记录与投影传给 child。完成事件附上任务启动时的目录，不把它误称为父会话当前目录。

## 8. 并发、顺序与缓存

- 多个独立 child 可以在受限 worker lane 中并发。
- 同一 child 一次只运行一个回合。
- 父子使用同一批调度器：连续的已声明只读调用可并行，写入与未知类型保持顺序屏障。此规则不代替多个独立任务之间的共享文件协调。
- child 不占住父会话；父会话继续按原 FIFO 串行。
- child 完成事件与用户新消息共用父会话 inbox，不抢写 MemCore。
- 稳定 ToolSpec 只在版本发布时变化一次；运行状态只作为工具结果或尾部事件追加。
- 普通聊天的 system prefix 不因 child 状态变化。

## 9. 与 DSH/OpenCode 的取舍

采用：

- provider 与模型工具分离；
- child 上下文独立；
- 后台启动立即返回稳定 ID；
- 中间轨迹留在 child；
- 完成后由父 Agent 整合；
- 工作区继承与权限缩减显式化。

暂不采用：

- 多 provider 同时暴露多个委派工具；
- 可继续 child 的驻留 activation 图；
- child 再派 child；
- report/send/interrupt/list 四套控制工具；
- 动态 persona 与任意 JSON Schema 输出。

这些能力只有在一次性链路真实使用暴露需求后再补。接口设计不得阻止后续扩展，但运行时不预先实现未被使用的复杂度。

## 10. 实施切片

1. 建立 provider/result 契约。（已完成；in-process child 驱动在第 3 项接入）
2. 接入 Host Job，验证持久化、取消、重启失败语义和恰好一次完成事件。（已完成）
3. 让 child 使用正常 Resolver/Broker 与父工作区快照。（已完成；进程内验收与外部模型 smoke 均已通过）
4. 增加 `spawn_subagent` ToolSpec/handler，并只在真实可用时暴露。（已完成生产组装与原生父工具调用验收）
5. 以一个读代码并产出审计报告的任务完成父 → child → 父纵向验收。（真实模型文件任务已通过；云端 QQ 已用只读命令验证真实派发、子任务执行和父角色二次群回复；桌宠客户端待 smoke）
6. 根据真实体验决定是否增加通用 Job 状态/取消入口。
7. 只有出现持续协作需求时，再设计 continuable child 和 follow-up。

## 11. 验收

2026-09-05 repair pass：`CapabilitySelection` 增加宿主内部的 `execution_allowlist`。`None` 沿用普通会话行为，空集合明确不允许执行工具。筛选同步移除无关 schema、handler、原生别名、执行凭据及提示；再次筛选只能缩小范围。历史 MCP 原生别名和 `invoke_mcp` 的延迟解析继续保留这一上限，最终调用由普通 validator/Broker 校验。允许的 MCP 仍可按需调用，不需要常驻全量 schema。该状态不进入模型工具 schema 或稳定提示词。

中断前尝试的 `turn_kind=subagent` 接线已撤下。当前驱动不进入伴侣的关系/Care/检索回合，只复用真实 LLM 客户端、普通工具协议/Broker 和独立 MemCore 工具轨迹。权限主体继承、目录快照、Broker 幂等域及产物归属分别有回归测试，不靠换一个 session 名推断隔离已经成立。

真实模型 smoke：`python -m tests.live_subagent_smoke --live`。只向当前配置的模型发送临时样例，使用真实文件工具、Broker、MemCore 投影及 Job；身份/投递采用测试夹具，embedding 是本地 stub，不覆盖记忆检索。2026-09-05 两次完成耗时约 29 秒、83 秒；第二次观测到 `project_inspect(source.py) → workspace_write(audit.md) → project_inspect(audit.md)`，报告文件存在，Job 成功且完成接收端收到一次事件。启动登记分别约 0 秒、0.015 秒。耗时不是性能保证，也不等于 QQ/桌宠实际投递验收。

另以真实 LLM 客户端和模拟 HTTP 响应覆盖多轮原生工具配对及 DeepSeek 思考内容回传；普通工具轮的工具表和系统消息保持一致。该测试不属于外部模型实测。扩大回归：171 项通过（子代理、Job、工作区、工具 schema、BotRuntime 和实例隔离）。

剩余边界：通用 Job 查询/取消的模型入口尚未提供；当前提供后端协作取消，在模型/工具步骤之间生效，不承诺强杀正在进行的任意同步调用。尚不提供继续 child 的 follow-up 或递归派发。QQ 原会话和角色的结果投递已验收，桌宠表现层仍待客户端实测。

验证入口：`python -m unittest tests.test_restricted_capability_selection tests.test_subagent_runtime tests.test_host_subagent_jobs -q`。新增覆盖空集合、重复缩小、父子选择互不修改、历史别名、路由目标拒绝、允许的延迟 MCP、最终执行校验及真实 Broker 幂等。

- 父 Agent 收到 `job_id` 后可以继续处理新消息。
- child 的工具调用经过与父 Agent 相同的 ToolSpec、Resolver、审批和 Broker。
- child 看不到未授权工具，也无法通过名字直调隐藏 handler。
- 父子工作目录一致，但 child 不污染父聊天与伴侣记忆。
- child 结果只通过一次完成事件回到正确父会话和角色。
- 宿主重启不伪造成功、不重复投递、不盲目重跑外部副作用。
- child 失败、取消、token 上限和无可交付输出都不是成功。
- 普通 QQ/桌宠聊天的提示词和工具表不因空闲子代理 runtime 增长。
