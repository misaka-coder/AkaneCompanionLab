# Desktop Activity Runtime V0

本文件约束桌宠端的轻量 Activity Runtime。它只描述“Akane 当前正在做什么”的瞬时状态，不是记忆系统、资源归档系统或 QQ/Web 通用协议。

## 目标

让桌宠端拥有一个可被 Akane 理解和控制的当前活动，例如播放一首歌、未来进行一次 Akane 声线演唱。Activity 的作用是把“此刻发生了什么”投影进本轮提示词，让 Akane 能自然回应和决定下一步动作。

## 适用范围

- 仅在 `client_mode=desktop_pet` 下生效。
- V0 只允许一个 `currentActivity`。
- Activity 状态随桌宠 `/think` 请求发送给后端。
- 后端只把 Activity 渲染成本轮 prompt card。
- Activity 不写长期记忆，不进入资源工作区，不进入任务工作区。
- QQ 和 Web 场景模式不读取也不渲染桌宠 Activity。

## 跨端资源边界

Activity Runtime 必须遵守“现场状态按 session 隔离，永久世界按 profile 归属，提示词投影按模式裁剪”的三层原则。

### 1. Ownership：资源归属

- `profile` 级：长期属于 Akane 的资产，例如已收下的曲库、场景、服装、相册、人设卡定义。
- `session` 级：当前对话现场里的临时对象，例如 QQ 当前附件、桌宠刚拖入的音频、Web 当前 pending 礼物、当前 Focus、当前 Activity。

归属共享不等于全端可见。一个资产可以属于 Akane，但不代表每个客户端模式都要把它塞进 prompt。

### 2. Projection：提示词投影

当前 `client_mode` 决定哪些资源会被投影给 Akane。

- QQ 默认只投影文字聊天、当前附件、文件工坊、当前任务工作区。
- Web 场景默认投影场景、BGM、服装、礼物与小世界资源。
- 桌宠默认投影桌宠活动、可播放音频、当前立绘/表情、少量桌面瞬时上下文。

示例：

- 曲库可以是 profile 级永久资产，但 QQ 文本聊天默认不需要挂载曲库。
- Web 收下的场景属于 Akane，但桌宠默认不需要看到所有场景候选。
- 桌宠拖入的音频属于桌宠当前 session 的手边素材，QQ 当前工作台不应自动看到。

### 3. Action：操作边界

Akane 只能在当前模式拥有对应能力时操作相关资源。

- 桌宠可以播放、暂停、继续当前可播放音频。
- QQ 可以处理当前会话附件和生成文件。
- Web 可以处理礼物、场景、服装、曲库等小世界资产。

需要跨端共享时，必须有明确的提升动作，例如“收下为曲库”“归档进相册”“内化为 BGM”“另存为永久资源”。

简化规则：

```text
现场状态：session
永久世界：profile
提示词可见性：client_mode projection
```

## Activity 类型

### `audio_playback`

普通音频播放或试听。它表示桌宠正在播放一首歌、一个音频文件或一个生成音频。

规则：

- 用户发消息时，普通音频不默认暂停。
- Akane 可以看到当前歌曲、进度、状态。
- Akane 只有明确输出 activity action 时，系统才执行播放控制。
- 不要把普通播放描述成 Akane 正在唱歌。

Prompt 语义：

```text                              
【当前桌宠活动】
- 类型：普通音频播放
- 音频：xxx.mp3（file_012）
- 状态：正在播放
- 进度 00:37 / 03:42
- 普通音频不会因为本轮消息自动暂停；如果你想控制播放，请输出 activity action。
```

### `vocal_performance`

Akane 自己的演唱或未来 AI Cover 表演。它表示“Akane 正在表演”，而不是普通背景播放。

规则：

- 用户发消息时，如果表演正在进行，桌宠可以暂停音频并标记 `interrupted`。
- 后端提示 Akane：她刚唱到哪里被主人打断了。
- 用户说“继续”时，系统不能关键词硬触发；必须由 Akane 理解后输出 `resume`。
- Akane 不应在没有 activity action 的情况下假装已经继续播放。

Prompt 语义：

```text
【当前桌宠活动】
- 类型：Akane 表演/唱歌
- 音频：xxx.mp3（gen_012）
- 状态：因主人发来消息已暂停
- 进度 01:17 / 03:42
- 主人发消息时表演已暂停；如果你想继续表演，需要输出 activity action，而不是假装仍在继续。
```

## Activity Action

桌宠模式允许最终 JSON 输出可选字段：

```json
"activity": {
  "action": "play|pause|resume|stop",
  "target": "current",
  "source_id": "可选 file/audio/gen handle"
}
```

约束：

- 没有播放控制意图时输出 `null`。
- 代码只执行 Akane 输出的 activity action。
- 代码不得因为用户说了“继续”“停一下”等关键词直接控制播放。
- `speech` 可以自然承接，但不能声称动作已经完成，除非对应 action 已经输出。

## 资源关系

Activity 不替代附件区、生成区或礼物系统。

- 拖入音频先进入附件/工作区，得到 `file_xxx`。
- 生成音频进入生成区，得到 `gen_xxx`。
- Activity 只引用这些 handle，并记录播放状态。
- “收下为曲库”或“内化为资源”是资产流转动作，不属于 Activity V0 的职责。

## Music Timeline 预留

后续 Music Timeline 只作为桌宠 Activity 的辅助索引，不改变 QQ 工作台和生成文件原则。

- 时间轴索引属于当前桌宠 session。
- 时间轴不写长期记忆，不默认跨端共享。
- 完整转写稿可以作为 `gen_xxx` 存在，但默认仍跟随产生它的 session。
- 如果用户或 Akane 明确要求归档/收下，才提升为 profile 级资源。
- 桌宠 prompt 只注入当前播放进度、附近歌词、异步滚动摘要等轻量卡片。
- QQ 若需要处理完整转写稿，仍走文件工坊的全量文件读取能力，不复用桌宠 timeline prompt。

一句话：timeline 是桌宠“此刻听到哪里”的索引，不是 Akane 的长期音乐记忆。

## V0 不做

- 系统级音乐监听。
- 后台连续监控电脑声音。
- 歌词时间轴自动生成。
- RVC / AI Cover 全流程。
- 多 Activity 并发。
- QQ/Web 的 Activity Runtime。

## 验收边界

- 普通 `audio_playback` 播放时，用户发消息不会自动暂停。
- `vocal_performance` 运行时，用户发消息可以暂停并标记 `interrupted`。
- 后端只在桌宠模式、且具备 `audio_playback` capability 时渲染 Activity prompt。
- QQ/Web 不应出现桌宠 Activity prompt。
- Akane 控制播放必须通过 activity action，不通过关键词硬触发。
