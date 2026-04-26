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
