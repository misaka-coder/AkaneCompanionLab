# MemCore 聊天作者边界与 QQ 消息引用 V6

状态：已实现，待部署迁移
日期：2026-08-31

## 目标

这次修改只解决一类问题：让模型看到的聊天历史忠实表达“谁在何时对谁说了什么”，同时不改写模型自己的历史输出。

系统继续遵循提示词导向：宿主保存和呈现真实事实，模型依据这些事实判断；宿主不猜语义、不伪造作者输出，也不增加新的回复状态机。

## 单一事实源

- OneBot 入站的有序消息链、消息 ID、引用、@、附件和转发由 `channelcore-onebot` 解析。
- Akane 只补充产品侧才能知道的事实，例如群昵称、当前会话、是否为静默观察。
- MemCore 保存原始条目和关系，并分别生成聊天模型视图与摘要模型视图。
- 原生工具调用和工具结果仍保持 Provider 原生边界，不改成聊天文本。

## 聊天模型视图

### 用户和环境消息

用户消息采用稀疏语义格式：只有真实存在的字段才出现。

普通群消息：

```yaml
time: 2026-08-31 14:20
message_id: "1847293012"
actor: Olivia (id=qq:123456)
text:
  今天天气不错
```

带多个 @ 和引用：

```yaml
time: 2026-08-31 14:20
message_id: "1847293012"
actor: Olivia (id=qq:123456)
mentions:
  - Akane (id=assistant)
  - 316 (id=qq:654321)
reply_to:
  message_id: "1847292800"
  actor: 千里朱音 (id=qq:111111)
  quoted_text:
    明天一起去吗
text:
  @Akane 帮我问问 @316 明天去不去
```

规则：

- `mentions` 保持消息链中的首次出现顺序；正文中的 @ 位置也保留。
- 不因为第一个 @ 就虚构唯一 `target`。
- 只有平台或真实引用关系能确定唯一对象时才写 `target`。
- 静默监听消息增加 `mode: observed`；它只是事实，不等于必须回复。
- 空字段不输出，普通消息不会被迫使用复杂模板。

### 助手消息

助手历史的唯一规则是作者保真：

```json
{"role":"assistant","content":"模型当时被宿主接受的原始输出"}
```

- 有 `provider_output_raw` 时逐字使用它。
- 没有 Provider 原文时使用宿主实际接受的 `semantic_text`，不猜测并补成 JSON。
- 不添加时间、`Assistant:`、emotion 标签、投影字段或宿主状态。
- 格式错误且未被接受的尝试只进入诊断审计，不冒充历史助手消息。
- 宿主兜底文本不得伪装成模型亲自输出。

这样模型看到用户消息中的时间和关系事实，但不会模仿宿主给自己添加的时间戳或字段标签。

## 摘要模型视图

摘要模型不复用聊天 Provider 投影，也不读取 `provider_output_raw` 协议外壳。

它读取独立的语义转录：

- 时间（含星期）、说话人稳定 ID 和显示名；
- 真实 target、mentions、reply、forward 和 observed 关系；
- 用户正文与助手 `semantic_text`；
- 真实存在且有记忆意义的媒介、投递状态和 emotion；
- 工具与事件继续使用各自已有的确定性渲染器。

摘要视图用于理解语义，不负责复刻下一轮 Provider 请求。因此它可以保留关系事实，但不会把最终输出 JSON 当成自然对话。

## QQ 消息 ID 与 `onebot_action`

不新增 QQ 工具。现有 `onebot_action` 增加可选 `message_selector`：

```json
{
  "action": "set_msg_emoji_like",
  "params": {"emoji_id": "66"},
  "message_selector": {"kind": "current_message"}
}
```

支持：

- `current_message`：触发当前模型回合的 QQ 消息；
- `replied_message`：当前 QQ 消息实际引用的消息；
- `recent_bot_message`：当前会话内最近第 N 条成功发送的 Bot 消息，`position=1` 表示最新。

解析规则：

- `params.message_id` 是显式事实，优先级最高；使用它时无需 selector。
- selector 由宿主解析成真实 `message_id`，选择器本身不会发送给 NapCat。
- 最近消息只记录 OneBot 成功回执中的真实 ID；失败发送不登记，无法解析时结构化返回原因。
- 权限仍由现有 OneBot 会话范围策略判断；selector 不扩大权限。

消息 ID 默认只出现在用户入站事实里。助手普通历史不附 ID，以免污染作者输出；需要操作 Bot 自己的近期消息时使用 selector，用户引用 Bot 消息时则使用引用中已有的真实 ID。

## 版本和迁移

- 新投影版本使用 V6，避免与曾回退的错误 V5 混淆。
- 原始 Timeline 条目不改写。
- 冻结的旧聊天投影通过显式、按命名空间原子迁移重建。
- 迁移不进入请求热路径；完成后只递增一次投影 generation。
- 无法找到完整原始条目的旧投影保持不动并报告，不伪造修复。
- 停机维护入口为 `scripts/migrate_chat_projections_v6.py`；默认 dry-run，只有 `--apply` 才写入。

## 性能与缓存

- 投影是确定性字符串，不包含请求时钟或倒计时。
- 同一原始条目永远生成同一投影。
- 请求热路径不做 OneBot 查询，不扫描聊天历史，不执行投影迁移。
- 最近 Bot 消息索引只维护有界的成功回执 ID。
- 迁移后稳定历史前缀可继续命中 Provider 缓存。

## 验收

1. 纯文本与 JSON 助手历史均保持原始作者输出，不出现时间戳或 `Assistant:`。
2. 普通、引用、多 @、静默观察、转发和媒体消息的用户事实清晰且无空字段。
3. 摘要模型能看见关系语义，但看不到助手输出协议外壳。
4. 原生工具调用、短结果原文、长结果可召回卡片均不退化。
5. `onebot_action` 可用显式 ID 和三种 selector；失败有结构化原因。
6. 投影迁移幂等，缓存 generation 只在实际变化时更新。
7. 聚焦测试、完整 MemCore 测试和 `git diff --check` 全部通过。
