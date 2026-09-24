# P3 回合请求与旧入口迁移窗口

起始基线提交：`3dca2ce`（P3a，SDK 0.4.0）；本窗口在其后的 SDK 0.5.0
回合请求切片开启。目标删除版本为明确破坏性发布 **SDK 1.0.0**，不是无限兼容。
第一方迁移与对外迁移说明必须先于删除；新插件和新增功能不得扩展下列旧入口。

## 已经收敛的权威

`HostTurnRequests` 仅管理授权、观察依赖、合并及回执。执行仍由现有
`DurableSessionWorkQueue`、`TurnCoordinator`、桌宠/QQ 路由和 Engine 完成。
没有另一个模型循环、发送实现或任务数据库。

`plugin_agent_events.py` 已退为 V1 类型/来源适配：删除其中独立的任务集合、
幂等缓存、字段名黑名单和通用业务数据小上限。V1 `submit` 与新的 `request_turn`
共用同一请求权威；`current_turn` 在幂等准入后转换成观察和请求回合，冲突重试
不能先覆盖观察。`timeline` 仍保留原事件留存契约。可信宿主 Job 完成通知保持
其现有持久化恢复和批处理契约，不借插件的短期授权重放。

桌宠/QQ 真实入站消息已同时可由公开 `on` 订阅，来源为 `@host`，目标为宿主
解析出的会话/角色。JSON 数据不能自报授权。QQ 公开消息链保留顺序、引用、
提及与附件摘要；宿主内部媒体定位信息不进入数据。发布事件不自动请求模型。

QQ 戳一戳不再只走消息事件的 `trigger_reason`：宿主在应用自身照顾效果后，另外发布
`POKE_CONVERSATION_EVENT`（`conversation.poke.inbound`），数据为 `event_kind="poke"`、
渠道、会话、行为者、已解析的 `outcome_kind`/`status`/`reason`。事件本身不授予反戳
权限，也不请求模型。旧插件仍在订阅消息事件并匹配 `trigger_reason=qq_poke`；迁移到
新事件类型的清单见下表。

## 仍在窗口内的清单

| 旧入口/使用者 | 当前状态 | 退出条件 |
|---|---|---|
| `PluginAgentEventRequest` / `get_agent_event_port` / worker Agent-event RPC | thin adapter，共用新请求权威 | P5 公开服务驱动完成后迁移 timer/gentle-checkin，SDK 1.0 删除旧公开协议 |
| timer `TimerService` / gentle-checkin 后台服务 | 仍使用上一行适配器 | 保留计时、持久化与禁用语义，用公开服务上下文替换旧端口 |
| `add_event_handler` / `PluginEventEnvelope` / `PluginEventResult` | documented migration window | 迁移 poke-streak、gentle-checkin；删除旧订阅与旧 current_turn 返回语义 |
| desktop 对旧 handler 的 current_turn 消费 | thin adapter；原消息处理前将事实交给观察及同一入站回合回执 | 已删除 desktop 的 `render_current_turn_events`/extra_context 拼接；旧协议随 SDK 1.0 删除 |
| QQ 对旧 handler 的 current_turn 消费、被动消息唤醒 | thin adapter；观察及回执关联到原消息、排队或被实际采纳的 steer | 已删除 QQ 的提示词拼接和共用 `render_current_turn_events`；旧协议随 SDK 1.0 删除 |
| 旧 `record_timeline_events` 路由留存适配 | documented migration window | 迁移依赖显式历史留存的旧作者后删除旧事件形状 |
| poke-streak 订阅消息事件的 `trigger_reason=qq_poke` | 仍工作，但新事件类型已发布 | 改用 `POKE_CONVERSATION_EVENT`；随旧事件订阅一并退出 |

P3b 已交付公开请求回合、正常队列、V1 Agent-event 薄适配和公开渠道 feed；P3c/P3d
接通桌宠及 QQ 原消息的入站合并，旧渠道 current_turn 均通过同一观察/请求权威。
P4a 已统一同步/后台 followup，见 [结果迁移](plugin_followup_migration_v2.md)。
P4 继续修复冻结代次与撤权竞态；P5 再完成依赖公开服务的第一方后台迁移，窗口仍开放。
不得用“公开字段已存在”作为
删除窗口关闭或桌宠窗口体验通过的证据。

`tests/test_plugin_legacy_migration_window.py` 固定第一方旧 API 使用清单，新增使用
需要先解决迁移，不能仅更新允许列表。新棋盘样例使用公开 SDK，不含旧历史参数。

## 作者迁移

普通状态更新改用 `ctx.observe(key, data)`，不会主动回复。需要回复时显式
`ctx.request_turn(reason, data, observations={key: version}, stale="latest")`。
两者都由宿主提供目标范围，不填写用户、会话、角色或内部历史标识。

排队或合并不是执行完成。通过 `turn_status` 查询 `status`、`model_status`、
`delivery_status`；事件处理器返回 `TurnReceipt` 时，事件回执会等待实际终态。
解除绑定/停用/换代会撤销待执行请求，重启后丢失授权的旧队列项不能恢复成新任务。
运行中取消先返回 cancelling，不承诺已经发出的外部消息或工具效果可以撤回。

桌宠/QQ 旧 handler 的请求是正常用户输入的参与者：同步、流式和后台排队入口都只
处理原消息一次，保留原文、附件与普通历史语义。所有者/代次来自注册或真实 worker，
事实自报 source 不参与授权。多插件观察在同一次决策中冻结并分别记录实际版本。
参与者被撤销时不能停止或成为这条独立用户输入的授权父请求；已经开始的参与回执
以 `cancelled` / `model_status=detached` 终止，不假报模型已经停止。

同步的 `response_ready` 表示成功构造 HTTP 响应，流式的 `streamed` 表示正常输出
完该流，后台 `queued` 表示已有桌宠帧队列接收；均不证明窗口已显示或 TTS 播放完。
断流、等待期间取消和投递失败有独立终态。没有签名会话/回合权威时，旧 handler
的请求结构化降级，普通用户输入继续处理，不恢复第二条提示词拼接分支。

QQ 的 `steer_accepted` 只表示正常回合控制器接收，参与者须等 Engine 按实际
source ID 采纳后才进入 running；未采纳不能借原回合的成功结束。`sent` 表示网关
发送返回成功，不证明联系人已经阅读。模型成功但发送失败保留两个独立状态。
现有会话队列和图片后续任务接管同一入站参与者，旧 HTTP 清理不能提前结束它。
仅因插件请求而唤醒的被动消息在撤销或重启丢失授权后不能继续请求模型；直接面向
角色的独立用户输入仍可正常执行。控制命令不会因旧 handler 再额外请求一次回复。

详见 [公开 SDK](../akane_plugin/README.md) 和
[棋盘独立项目](../examples/plugins/akane_sdk_board/README.md)。
