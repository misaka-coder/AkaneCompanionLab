# 子代理运行时 V1

状态：设计已确认，待按切片实施。旧 `delegate_task` / `TaskWorkerService` 不恢复。

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
- child 会话：子代理自己的消息与工具轨迹；不写进父对话历史。
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

## 4. Provider seam

运行时依赖一个窄 provider 契约：

```text
start(request) -> run handle
run.result() -> terminal result
run.cancel()
run.close()
```

第一版只注册 `in_process` provider。模型不选择 provider，也不看到 provider 名；以后接 Codex、Claude Code 或远端 ACP 时只替换 provider，不改变工具和 Job 语义。

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
- 已开始但进程内 child 消失的 Job 明确结算为 `host_restart_child_unavailable`；
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

1. 建立 provider/result 契约和 in-process child 驱动，不对模型暴露。
2. 接入 Host Job，验证持久化、取消、重启失败语义和恰好一次完成事件。
3. 让 child 使用正常 Resolver/Broker 与父工作区快照，删除任何直调 handler 的路径。
4. 增加 `spawn_subagent` ToolSpec/handler，并只在真实可用时暴露。
5. 以一个读代码并产出审计报告的任务完成父 → child → 父纵向验收。
6. 根据真实体验决定是否增加通用 Job 状态/取消入口。
7. 只有出现持续协作需求时，再设计 continuable child 和 follow-up。

## 11. 验收

- 父 Agent 收到 `job_id` 后可以继续处理新消息。
- child 的工具调用经过与父 Agent 相同的 ToolSpec、Resolver、审批和 Broker。
- child 看不到未授权工具，也无法通过名字直调隐藏 handler。
- 父子工作目录一致，但 child 不污染父聊天与伴侣记忆。
- child 结果只通过一次完成事件回到正确父会话和角色。
- 宿主重启不伪造成功、不重复投递、不盲目重跑外部副作用。
- child 失败、取消、token 上限和无可交付输出都不是成功。
- 普通 QQ/桌宠聊天的提示词和工具表不因空闲子代理 runtime 增长。
