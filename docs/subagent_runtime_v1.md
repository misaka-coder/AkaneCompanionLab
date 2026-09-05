# 子代理运行时 V1

状态：实施中。provider/result、Host Job、工具范围、工作区继承和隔离 child 驱动已实现并通过进程内纵向测试；模型入口和生产绑定尚未发布。旧 `delegate_task` / `TaskWorkerService` 不恢复。

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

执行授权、命令控制和资源归属沿用父会话及原发起者，方便父 Agent 后续检查和接管产物；Broker 幂等账本另外使用 task_id 隔离，避免父子或两个 child 的相同 provider call_id 相撞。独立的是任务上下文和轨迹，不是重新授予一套权限。child 内长工具在 child worker 执行，不再开启另一个面向角色的完成通知；唯一面向父会话的终态来自 child 的 Host Job。

模型客户端复用宿主 LLMRuntime；执行期间使用请求级配置副本，固定模型、思考模式和强度。持久化只保留公开路由指纹，不保存客户端或密钥。排队任务发现当前路由已变化时明确失败，不悄悄换模型。

## 4. Provider seam

运行时依赖一个窄 provider 契约：

```text
execute(request, cancelled) -> terminal result
```

第一版计划由宿主注册 `in_process` provider（真实驱动验收前不启用）。Host Job 在后台 lane 中调用同步 `execute`，通过 `cancelled()` 传递取消状态；父回合只等待持久化准入结果。模型不选择 provider，也不看到 provider 名；以后接其他 provider 时保持同一工具和 Job 语义。

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

`spawn_subagent` 仅在具备真实 provider、Host Job、工作区和执行权限时暴露。普通陪伴聊天不增加常驻子代理提示；编程 Skill 只在工具可用时加入一句使用建议。

## 7. child 输出

child 的终态结果包含：

- `status`：`succeeded | failed | cancelled`；
- 有界 `summary`；
- `artifact_handles`；
- `child_session_id`，用于宿主审计；
- 失败时的结构化 `reason`。

父 Agent 只收到终态摘要和产物引用，不接收整段子代理工具轨迹。需要进一步检查时由宿主提供有界结果读取，不把 child transcript 全量塞回父上下文。

## 8. 并发、顺序与缓存

- 多个独立 child 可以在受限 worker lane 中并发。
- 同一 child 一次只运行一个回合。
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
3. 让 child 使用正常 Resolver/Broker 与父工作区快照。（驱动、真实文件读写、原生工具配对、独立 MemCore 及 Job 回传的进程内验收已完成；外部模型 smoke 待执行）
4. 增加 `spawn_subagent` ToolSpec/handler，并只在真实可用时暴露。
5. 以一个读代码并产出审计报告的任务完成父 → child → 父纵向验收。
6. 根据真实体验决定是否增加通用 Job 状态/取消入口。
7. 只有出现持续协作需求时，再设计 continuable child 和 follow-up。

## 11. 验收

2026-09-05 repair pass：`CapabilitySelection` 增加宿主内部的 `execution_allowlist`。`None` 沿用普通会话行为，空集合明确不允许执行工具。筛选同步移除无关 schema、handler、原生别名、执行凭据及提示；再次筛选只能缩小范围。历史 MCP 原生别名和 `invoke_mcp` 的延迟解析继续保留这一上限，最终调用由普通 validator/Broker 校验。允许的 MCP 仍可按需调用，不需要常驻全量 schema。该状态不进入模型工具 schema 或稳定提示词。

中断前尝试的 `turn_kind=subagent` 接线已撤下：仅设置独立 session 和短系统提示尚不足以证明关系/Care 隔离、权限主体继承、默认 cwd、取消和产物归属完整。后续需在接入真实驱动时逐项验证；不能把测试用 provider runner 当成已可供模型调用的子代理。

验证入口：`python -m unittest tests.test_restricted_capability_selection tests.test_subagent_runtime tests.test_host_subagent_jobs -q`。新增覆盖空集合、重复缩小、父子选择互不修改、历史别名、路由目标拒绝、允许的延迟 MCP、最终执行校验及真实 Broker 幂等。

- 父 Agent 收到 `job_id` 后可以继续处理新消息。
- child 的工具调用经过与父 Agent 相同的 ToolSpec、Resolver、审批和 Broker。
- child 看不到未授权工具，也无法通过名字直调隐藏 handler。
- 父子工作目录一致，但 child 不污染父聊天与伴侣记忆。
- child 结果只通过一次完成事件回到正确父会话和角色。
- 宿主重启不伪造成功、不重复投递、不盲目重跑外部副作用。
- child 失败、取消、token 上限和无可交付输出都不是成功。
- 普通 QQ/桌宠聊天的提示词和工具表不因空闲子代理 runtime 增长。
