# QQ 连续戳一戳感知插件

这是用公开 SDK 写的事件型插件样例：只订阅宿主发布的戳一戳事件，把"连续戳"变成一条当前
观察，不注册模型工具、不改 system prompt、不发消息、不写时间线。

## 用户能感受到什么

同一个人在同一个 QQ 私聊或群聊中，于 90 秒内连续戳 Akane 时，从第二次起，下一次模型
决策能看到：

```json
{"actor_id":"300","conversation_kind":"group","consecutive_count":2,"window_seconds":90}
```

这是"当前状态"（`ctx.observe`），**不会自动请求模型**，也不强制回复。是否回复仍由宿主
已有的 Agent 仲裁决定。首个戳、普通消息、非 QQ 来源和重复事件都保持安静。

## 权限与边界

- 插件 ID：`akane.sample.poke-streak`
- 依赖：`akane-plugin>=0.14,<0.15`
- 权限：`event.subscribe`、`context.observe`（由声明自动加入）
- 订阅：`conversation.poke.inbound`，`scope="conversation"`
- 状态隔离：按会话 `conversation_id` 与发送者 `actor_id` 隔离
- 连续窗口：公开常量 `POKE_STREAK_WINDOW_SECONDS = 90`
- 重复事件：按宿主签发的 `event.event_id` 幂等；仅保留最近 2048 个 ID，
  公开常量为 `RECENT_EVENT_ID_CAPACITY`

2048 是插件自身可见的内存资源边界，不是 Agent、消息或工具调用次数限制。超过后只淘汰
最旧的幂等记录，不拦截新事件。

事件里的 `event.occurred_at_ms` 由宿主记录，插件用它计算时间窗；业务状态版本不属于本
插件，本插件也不使用 `event.version` 做排序。

## 本地构建和启用

```powershell
python -m build --wheel examples/plugins/akane_poke_streak
python -m pip install --no-deps examples/plugins/akane_poke_streak/dist/akane_poke_streak-0.2.0-py3-none-any.whl
```

在目标实例中选择插件：

```toml
[[plugins]]
id = "akane.sample.poke-streak"
enabled = true
```

通过插件管理入口暂存并校验 wheel。候选代健康后原子接替当前代；候选失败时继续使用
last-good。启用、停用、更新和移除都不需要重启 Bot 进程。
