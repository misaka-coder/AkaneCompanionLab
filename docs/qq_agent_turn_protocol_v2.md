# QQ Agent 回合协议 V2

## 目标

QQ 事件只决定是否立即请求模型，不替模型决定必须输出文字。模型可以：

- 只输出 `speech`；
- 只执行用户可见的 QQ 动作；
- 执行动作并补充 `speech`；
- 明确保持静默。

宿主只验证协议、执行工具并记录真实回执，不因为艾特、触发词、引用或戳一戳而强制生成文字。
群聊 follow-up、idle observation 与 quoted-reply review 事件只提供一次是否自然参与的判断机会，
也不引入另一套输出格式。

## Provider 侧输出契约

原生工具模式下，QQ 普通回复的最小输出为：

```json
{"emotion":"normal","reply_medium":"text","speech":"当然可以。"}
```

无需继续发送文字时输出：

```json
{"speech":""}
```

`speech` 是唯一文字权威。普通回复按 `emotion`、`reply_medium`、`speech` 的顺序输出，以便宿主在 `speech` 完整句子形成时立即流式投递。`memory_metadata`、`persona`、`state_request` 只在有真实非空信号时追加。

原生工具调用使用 Provider 的 `tools/tool_calls` 协议，不进入最终 JSON。只有 Provider 明确不支持原生工具、且本轮实际提供了“兼容 JSON 工具”清单时，才允许临时 legacy profile 输出非空 `tool_call`。

模型不再输出：

- `attention: silent`；
- 固定的 `status: final`；
- QQ 无消费者的 `choices: []`；
- 原生工具模式下的 `tool_call: null`；
- 空 `persona`、空 `state_request`、没有记忆信号的整套空 metadata。

宿主可为客户端兼容在规范化后的内部 frame 中派生 `status` 等字段，但不得把派生字段重新描述成模型决策。

## 终态与工具循环

```text
合法原生工具调用 -> 执行 -> 记录真实结果 -> 继续请求模型
合法兼容工具调用 -> 执行 -> 记录真实结果 -> 继续请求模型
speech 非空       -> 正常交付
speech 明确为空   -> 合法静默或 action-only 完成
缺失 speech/类型错误/损坏 JSON -> 同回合协议恢复
```

工具调用本身不要求有 `speech`。QQ 可见动作成功后，模型可以用 `{"speech":""}` 表示无需追加文字。内部读取、搜索、Shell、文件修改等工具不构成 QQ 可见交付。

## 用户可见交付账本

OneBot 权威能力表中的成功 `write` 动作构成可见交付，例如发送消息、引用、戳一戳、表情回应、点赞、撤回和转发。`read`、`capabilities` 及失败动作不构成可见交付。

最终回合分类：

| speech | 成功可见动作 | 分类 |
|---|---:|---|
| 非空 | 否 | speech-only |
| 空 | 是 | action-only |
| 非空 | 是 | action-and-speech |
| 空 | 否 | deliberate-silent |

分类用于投递、注意力窗口和诊断；不生成额外 QQ 文本。

## MemCore 契约

- 最终 `memory_metadata` 只标注本轮显式 annotation target，通常是触发本轮的用户消息或事件；不标注助手回复或工具结果。
- 工具调用与结果通过 `kind + trace_metadata + call_id/source_id` 独立保存。
- 只有宿主接受的终态输出可以提交 annotation；中间工具轮和修复轮不能覆盖目标。
- `speech` 非空时，`semantic_text` 保存真实正文。
- `speech` 为空时，不向用户可读时间线写入空白气泡；MemCore 可以保存不可见的终态 Provider 原文，用于闭合回合和精确历史重放。
- 没有 `memory_metadata` 时标记为 `missing`，不能把宿主补出的空对象冒充 `accepted_model`。
- 多个独立 annotation target 必须逐 target 提交 annotations，不能把一份 metadata 静默复制给所有输入。

## 提示词与缓存

触发类型、说话人、目标对象、艾特、引用、附件和转发关系来自 ChannelCore/MemCore 时间线。系统提示只稳定解释一次字段和行为，不按事件动态重写。

原生/legacy 输出 profile 只随 Provider 能力配置变化，同一 Provider 的正常回合保持稳定。协议迁移造成一次性缓存前缀变化，之后不得因直接回复、主动观察或静默选择产生新的 system prompt 变体。

## 删除边界

实现完成后删除旧的 optional-silent 解析、scope 门控、专属恢复、专属持久化标记和 QQ `choices` 能力。Legacy JSON 工具只能存在于明确的兼容 profile，不得与原生工具在同一请求中重复暴露同一个工具。
