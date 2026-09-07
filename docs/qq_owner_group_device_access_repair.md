# QQ 主人群聊设备权限修复

2026-09-07：原 Satellite 渠道校验硬编码拒绝所有群聊，导致真实主人在群聊请求
`desktop_context_snapshot` 也收到 `device_action_requires_owner_private_chat`。

现在用 QQ 入口提供的 `qq_delivery_context.user_id` 与已配置 `MASTER_QQ` 校验，
不再把群聊存储身份当作设备权限身份。主人私聊和群聊均可继续进入执行器；
非主人、缺发言者、主人未配置均在派发前结构化拒绝，原因是
`device_action_requires_owner`。群昵称、消息正文和模型参数不授予身份。

高风险能力仍服从主人的 `ops` 设置；审批 grant 绑定真实请求者的权限身份，
同时保留群会话、具体参数和设备绑定，不能被另一群成员或另一 PID 复用。
桌面端仍是唯一执行器，没有新增后端桌面操作或离线假成功。

验证覆盖真实 `execute_tool_invocation` 派发入口（执行器替身）、主人与非主人、
读取与媒体动作、缺身份、ops 关闭/询问/允许、跨成员和跨 PID 审批复用拦截。
这不等于已在真实 Codex UI 中完成新对话发送；实际应用操作还取决于已有
执行能力、在线设备与当前权限，不能用通过渠道校验冒充任务成功。
