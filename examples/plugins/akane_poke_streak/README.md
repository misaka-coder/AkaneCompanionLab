# QQ 连续戳一戳感知插件

这是 Akane 的第一个真实事件型插件样例。它使用正式 wheel、entry point、
`PluginHost` 和事件桥，不注册模型工具，也不修改 system prompt。

## 用户能感受到什么

同一个人在同一个 QQ 私聊或群聊中，于 90 秒内连续戳 Akane 时，从第二次起，模型当轮会看到：

```json
{"event_type":"interaction.qq_poke_streak","fields":{"actor_id":"...","consecutive_count":"2","conversation_kind":"group","window_seconds":"90"},"source":"akane.sample.poke-streak"}
```

这是当轮事实，不是强制回复指令。Akane 仍沿用宿主已有的 Agent 仲裁决定是否回复以及如何回复。
首个戳、普通消息和非 QQ 来源不会产生额外上下文；插件不会自行发消息，也不会写入 MemCore。

## 权限与边界

- 插件 ID：`akane.sample.poke-streak`
- 权限：仅 `event.subscribe`
- 订阅：`conversation.direct.inbound`、`conversation.group.inbound`
- 状态隔离：按宿主插件实例、会话 subject 和发送者 actor ID 隔离
- 连续窗口：公开常量 `POKE_STREAK_WINDOW_SECONDS = 90`
- 重复事件：按 OneBot `event_id` 幂等；仅保留最近 2048 个 ID，公开常量为
  `RECENT_EVENT_ID_CAPACITY`

2048 是插件自身可见的内存资源边界，不是 Agent、消息或工具调用次数限制。超过后只淘汰最旧的
幂等记录，不拦截新事件。

## 本地构建和启用

```powershell
python -m build --wheel examples/plugins/akane_poke_streak
python -m pip install --no-deps examples/plugins/akane_poke_streak/dist/akane_poke_streak-0.1.0-py3-none-any.whl
```

在目标实例的 TOML 中选择插件，然后按当前 restart-only 生命周期重启插件宿主：

```toml
[[plugins]]
id = "akane.sample.poke-streak"
enabled = true
```

停用时将 `enabled` 改为 `false` 并重启插件宿主。当前阶段尚未把安装和卸载伪装成热重载；
统一的 staging 安装与 last-good 切换属于 M67-E。
