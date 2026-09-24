# 语音 Runtime 前沿方案调研（2026-07）

状态：资料调研与 V1 设计校准，不包含运行时代码改动
日期：2026-07-27

这份文档回答一个问题：现在成熟的实时语音系统，究竟怎样处理回合、打断、
流式转写、工具和低延迟输出；哪些思路适合 Akane，哪些暂时不应照搬。

## 1. 先给结论

目前能看到的方案大致分成两类：

1. **级联式实时链路**：本地/边缘 VAD → 流式 ASR → 回合检测 → 文本模型 →
   流式 TTS。
   这条路线可观察、可替换、方便接入工具和记忆，代价是需要自己处理 partial
   修订、打断和音频播放状态。

2. **原生实时语音模型**：客户端持续发送音频，模型直接输出音频和转写，并在
   provider 内部完成回合判断、取消和打断。
   延迟更低、语气和重叠语音更自然，但会把一部分会话状态交给 provider，工具、
   记忆和“实际播放了多少”必须依赖适配器重新投影。

前沿产品的共同点不是选择其中一层，而是把三种判断叠加起来：

```text
声学层：现在有没有人声、是否需要立刻降音量
语义层：这句话是否说完、是附和还是抢话、是否应该接管
执行层：当前音频/模型/工具哪些可以取消，哪些必须保留
```

因此，Akane 现有的“系统侧快速反应 + 模型侧语义判断 + MemCore 事件流”
方向是成立的。需要补充的是：把“已生成内容”和“已实际播放内容”明确分成
两个投影，而不是只选择其中一个。

## 2. 资料对照

### 2.1 OpenAI Realtime：provider 负责 VAD、取消与音频截断

官方实时 API 支持 `server_vad` 和 `semantic_vad`：

- `server_vad` 依据静音分段，支持 `threshold`、前置音频和静音时长；
- `semantic_vad` 依据语义判断用户是否说完，并用 `eagerness` 调整等待时间；
- VAD 可以控制是否自动创建响应、是否自动打断当前响应。

官方还明确区分了 WebRTC/SIP 与 WebSocket：

- WebRTC/SIP 由服务器持有输出缓冲，用户打断时服务器自动取消响应并截断未播放
  的音频；
- WebSocket 由客户端播放音频，客户端必须停止播放、测出已经播放的时长，再
  发送 `conversation.item.truncate`；
- 截断会移除未播放部分的音频和对应转写，但不承诺字符级的音频/文字对齐。

这直接支持我们“不做字符级记忆边界”的判断，同时说明了“实际送达进度”不能
凭模型生成结束事件推断，必须由播放端上报。

来源：

- [Voice activity detection](https://developers.openai.com/api/docs/guides/realtime-vad)
- [Realtime conversations — Interruption and Truncation](https://developers.openai.com/api/docs/guides/realtime-conversations)

### 2.2 Gemini Live：持续双向音频、主动音频和非阻塞工具

Gemini Live 使用有状态 WebSocket，持续接收 PCM 音频并返回音频块，同时可选
输入/输出转写。它提供：

- 自动或手动 activity start/end；
- `start_of_speech_sensitivity`、`end_of_speech_sensitivity`、前置音频和静音
  时长；
- 发生打断时取消未完成生成，并发送 `interrupted` 事件；
- 音频输出、输入输出转写、工具调用都在同一实时会话中流动；
- Gemini 2.5 Flash Live 支持 `NON_BLOCKING` 函数调用，并用
  `INTERRUPT`、`WHEN_IDLE`、`SILENT` 指定工具结果如何影响当前回答。

这里有一个重要的版本差异：Gemini 3.1 Flash Live 目前不支持非阻塞函数调用，
而 2.5 Flash Live 支持。这说明“模型能否边说边等工具”是 provider 能力，不应
写死成 MemCore 或宿主的通用假设。

Gemini 文档还特别提醒，`send_realtime_input` 为响应速度优化，但牺牲了确定的
内容排序；需要精确建立历史时，应使用有序的内容更新接口。这个取舍和 Akane
的缓存原则一致：稳定前缀走固定上下文，实时事件走追加尾部。

来源：

- [Gemini Live API 概览](https://ai.google.dev/gemini-api/docs/live-api?hl=zh-cn)
- [Live API 功能指南](https://ai.google.dev/gemini-api/docs/live-api/capabilities?hl=zh-cn)

### 2.3 LiveKit：VAD、语义回合检测和自适应抢话三层组合

LiveKit 将回合处理拆成可组合的检测模式：

- VAD：延迟最低，只判断有无语音；
- STT endpointing：使用 STT 自己的结束判断；
- turn detector：结合语音内容和声学属性判断用户是否说完；
- realtime provider：使用 OpenAI/Gemini 自带的服务端判断；
- manual：推送通话或明确的按键控制。

无论选择哪种模式，LiveKit 都把 endpointing delay、噪声消除和 interruption
handling 作为独立调节项。它的 adaptive interruption 模型在 VAD 触发后继续
分析声学信号，把“嗯嗯、好的、对”这类 backchannel 和真正抢话区分开来，
不必等待完整转写，因此反应更快。

它还设置了 turn boundary cooldown：助手刚开始说话和即将说完时，打断判定
容易误判，需要单独保护边界。这是一个很实用的经验，说明打断阈值不应是全局
单一常量。

自适应打断依赖 aligned transcript（词或片段带开始/结束时间），并为每次
打断记录延迟和事件指标。

来源：

- [Turns overview](https://docs.livekit.io/agents/logic/turns/)
- [Adaptive interruption handling](https://docs.livekit.io/agents/logic/turns/adaptive-interruption-handling/)

### 2.4 Pipecat：中断沿流水线广播，语境只提交已说出口内容

Pipecat 的中断不是某个 LLM 工具的特例，而是一个穿过整条流水线的
`InterruptionFrame`：

- 各处理器立即取消当前可取消任务并清空排队帧；
- LLM 停止生成；
- TTS 清理聚合缓冲和词时间戳；
- 输出传输层丢弃尚未播放的音频；
- 不可中断的函数结果和结束帧继续处理。

它的上下文聚合器只提交实际播放过的 assistant 文本；没有播放的文本不会被
伪装成已经说过。这种“对话语境投影”非常可靠，但它没有否定保存完整生成
原文的价值：完整原文仍可以进入审计/事件存储，用于之后解释“刚才原本准备说
什么”。

Pipecat 还采用“VAD 判断开始、Smart Turn 模型判断结束”的组合，并建议尽量
使用本地 VAD 降低 150–200ms 的网络延迟。

来源：

- [Interruptions](https://docs.pipecat.ai/pipecat/fundamentals/interruptions.md)
- [Speech Input & Turn Detection](https://docs.pipecat.ai/pipecat/learn/speech-input.md)

### 2.5 全双工研究：Moshi 与原生 speech-to-speech

Moshi 是全双工语音对话研究的代表：它同时建模用户和助手两条音频流，不依赖
显式 speaker turn，并用“Inner Monologue”先预测与音频对齐的文字 token，
再生成音频 token。论文报告理论延迟约 160ms、实践约 200ms。

这类模型可以自然处理重叠语音、插话、语气和非语言声音，但需要专门训练的
speech-to-speech 模型与音频 codec。它不能直接证明普通“ASR→文本模型→TTS”
链路应该删掉，也不适合作为 Akane V1 的前置依赖。

来源：

- [Moshi: a speech-text foundation model for real-time dialogue](https://arxiv.org/abs/2410.00037)

### 2.6 同声传译：稳定前缀，而不是等待整句

SeamlessStreaming 使用 Efficient Monotonic Multihead Attention，在源语音
尚未结束时就生成目标语言，属于“稳定增量前缀”策略，而不是把每次 partial
都当成一条新消息。

这验证了我们的两个判断：

- 同声传译只是连续交互的一个表现，不必做成固定模式开关；
- 增量输出必须有 revision/稳定前缀语义，否则会重复播放和污染记忆。

来源：

- [Seamless: Multilingual Expressive and Streaming Speech Translation](https://arxiv.org/abs/2312.05187)

### 2.7 在线 ASR：local agreement 与自适应延迟

Whisper-Streaming 的核心不是简单地“每 1 秒重新识别一次”，而是使用
local agreement：连续窗口对同一前缀达成一致后，才提交稳定文字；同时根据
语音内容动态调整延迟。

这与 Akane 的 `partial → stable_checkpoint → final` 三段式一致。稳定检查点
可以触发低风险的预处理，但只有 final 才能作为权威用户消息和 memory metadata
的目标。

来源：

- [Turning Whisper into Real-Time Transcription System](https://arxiv.org/abs/2307.14743)

### 2.8 Half-cascade：原生音频理解与独立 TTS

LiveKit 目前把 realtime model 加独立 TTS 作为正式支持的 half-cascade
架构：实时模型负责理解输入音频和生成文字，独立 TTS 负责精确、可控地播放。

它解决了原生 speech-to-speech 的几个现实问题：

- realtime provider 的输入转写可能在回答之后才到；
- provider 对给定脚本的逐字朗读没有保证；
- 长历史通常仍然只能以文本重新加载；
- 角色音色、演出标签和播放队列仍需要宿主管理。

因此 Akane 的 provider 边界不应只有“传统级联”和“完全原生”两个极端，
还应允许：

```text
native audio understanding → text/semantic plan → Akane TTS
```

来源：

- [Realtime models overview](https://docs.livekit.io/agents/models/realtime/)

### 2.9 快速交互层与慢速思考层

DuplexOmni 将能力明确拆成异步协作的 interaction layer 和 thinking layer：
前者持续处理音频/视频并实时互动，后者以可插拔模块完成复杂推理和工具调用。

RelayS2S 则让全双工快路径先推测一个很短的回复前缀，同时让高质量
ASR→LLM 慢路径生成正式继续内容，再由 verifier 决定是否采用快路径前缀。
这与 Akane 的 candidate、generation fencing 和 speculative TTS 很接近。

不过两者仍是研究方案，不能把论文结果直接当成生产 SLA。对 V1 的价值是确认
接口边界，而不是要求立刻实现第二套模型：

```text
Interaction Kernel  # 硬实时事实
Turn Controller      # 轻量交互决策
Thinking Agent       # 正式回答、记忆和工具
```

来源：

- [DuplexOmni](https://arxiv.org/abs/2606.09186)
- [RelayS2S](https://arxiv.org/abs/2603.23346)

### 2.10 语音、语言和动作共用时钟

DuplexSLA 在同一约 160ms 时间轴上联合处理用户音频、助手音频和结构化动作，
使暂停、附和、规划与工具调用不必全部等待自然语言轮次结束。

Akane 不需要复制它的模型结构，但应借鉴“共享 correlation 和时钟”的协议：
语音、Live2D 动作、工具和字幕可以异步完成，却必须能追溯到同一个
`voice_turn_id`、`response_id` 和 generation。

来源：

- [DuplexSLA](https://arxiv.org/abs/2605.20755)

### 2.11 误打断恢复与版本围栏

LiveKit 对 VAD 触发后却没有有效转写的情况定义了 false interruption。默认可在
超时后从中断位置继续播放，并提供 `resume_false_interruption` 和
`false_interruption_timeout`。

ElevenLabs Speech Engine 则要求每个用户转写携带递增 `event_id`；如果用户再次
开口，宿主应取消旧 LLM 请求，带旧 `event_id` 的后续回答会被静默丢弃。

两者分别验证了：

- 中断必须允许 `suspected → false_positive → resumed`，不能只有停止；
- `voice_turn_id + generation + revision` 不是多余字段，而是所有异步结果的
  一致性围栏。

来源：

- [LiveKit Turns overview — False interruptions](https://docs.livekit.io/agents/logic/turns/)
- [ElevenLabs Speech Engine upstream](https://elevenlabs.io/docs/api-reference/speech-engine/speech-engine-upstream)

### 2.12 工具播报调度、延迟桥和情绪证据

Gemini Live 把非阻塞工具结果分为三种调度：

```text
INTERRUPT  # 立即打断并告知
WHEN_IDLE  # 当前语音完成后再告知
SILENT     # 只进入上下文，暂不播报
```

OpenAI GPT-Realtime-2 提供 preamble，让模型在复杂推理或工具前先发出短提示；
ElevenLabs soft timeout 则只在主回答超时后播放一次短反馈。它们说明
“我查一下”可以是正式的延迟管理行为，而不只是提示词废话。

需要区分：

```text
backchannel     # 表示正在听，不取得完整发言权
latency_bridge  # 已接管本轮，正式结果仍在准备
content         # 正式回答内容
```

情绪和韵律也可以参与回合判断，但只能作为带来源与置信度的观察。SpeechEQ
显示当前端到端语音模型仍存在文本捷径、跨轮情绪遗忘和语境误判，因此不应把
`affect_estimate` 写成确定的用户事实。

来源：

- [Gemini Live tool use](https://ai.google.dev/gemini-api/docs/live-api/tools?hl=en)
- [OpenAI: Advancing voice intelligence](https://openai.com/index/advancing-voice-intelligence-with-new-models-in-the-api/)
- [ElevenLabs Conversation flow](https://elevenlabs.io/docs/eleven-agents/customization/conversation-flow)
- [SpeechEQ](https://arxiv.org/abs/2606.25990)

## 3. 对 Akane V1 的具体校准

现有 V1 方案大方向不需要推翻，但应补上以下约束。

### 3.1 回合检测不是一个状态，而是三种信号

Voice Runtime 应分别记录：

```text
acoustic_activity   # VAD：有人声，立刻降音量/开始缓存
barge_in_intent     # 自适应声学判断：是否真抢话
semantic_endpoint   # 语义回合判断：这句话是否说完
```

三者可以异步到达，不能互相覆盖。模型是否接管是决策层结果，不是 VAD 的
直接副作用。

### 3.2 中断采用“双投影”

同一个 assistant response 需要同时保留：

```text
canonical_generation:
  full_text
  all speech units
  generation status

conversation_projection:
  spoken_text / delivered_units
  unplayed_units
  interrupted_at_unit
```

模型看到的当前活动轮可以同时看到两者，并有明确标签：
“完整生成原文”与“用户实际听到的内容”不是同一件事。历史轮次默认只投影
已送达文本，用户追问“你刚才本来要说什么”时再按 response_id 恢复完整原文。

这样兼顾了 Pipecat 的真实会话语境和 Akane 需要保留原始证据的记忆原则。

### 3.3 partial 只更新同一条语音轮次

所有 partial、稳定检查点和最终转写都必须绑定：

```text
voice_turn_id
revision
stable_prefix
```

渲染层显示最新稳定前缀，事件存储保留修订 lineage；不能把每次修订都渲染成
新的 user 消息。最终提交只发生一次。

### 3.4 推测通道和正式通道分开

只读检索、时间线读取、候选回答、候选 TTS 可以进入 speculative 通道；有副作用
的工具要等 final 或明确确认。若 provider 支持 out-of-band response 或
non-blocking function call，可以复用，但结果必须带 generation/correlation，
不能覆盖新一轮。

### 3.5 播放端是事实来源

模型的 `response.done`、TTS 完成和音频已生成，都不等于用户已经听到。播放端
需要报告 `started/completed/interrupted/not_started`，以及至少片段级进度。
字符级截断只作为 provider 适配字段，不作为 MemCore 的核心语义。

### 3.6 传输层做适配，不让 MemCore 认识 provider

以后可增加：

```text
voice.provider.cascade
voice.provider.openai_realtime
voice.provider.gemini_live
voice.provider.full_duplex
```

这些 provider 只负责把各自的 VAD、转写、音频和取消事件映射为统一
`voice.*` 事件。MemCore 只看到稳定的事件语义、时间线和 lineage，不认识
WebRTC、WebSocket 或某一家 API 的字段。

### 3.7 Interaction Kernel 是责任边界，不一定是新包

Interaction Kernel 负责 AEC、降噪、VAD、重叠语音、ducking、播放缓冲、
音频时钟和 generation fencing。它只执行可以从设备和当前播放状态确认的
事实，不做长上下文推理。

这个名字用于划清状态机责任，不要求 V1 立刻新建独立进程或 package。现有
运行时只要保持接口清楚，也可以先作为宿主内的模块实现。

### 3.8 误打断必须可恢复

播放流程至少需要：

```text
playing
→ suspected_interruption
→ ducked
→ confirmed_interruption | false_interruption
```

如果没有形成有效用户语音，恢复尚未播放的同一音频缓冲；如果已经确认抢话，
才清空队列并提交中断事实。恢复点使用音频/语义单元边界，不要求字符级定位。

### 3.9 交付账本记录阶段，不复制四份文本

一个语音单元需要区分：

```text
generated
synthesized
queued
delivered / interrupted / not_started
```

若系统确实存在 speech plan，可以额外记录 `planned`；否则不为了字段完整而
复制 `planned_text`、`generated_text` 等相同内容。文本和音频使用 artifact
引用，阶段状态记录真实发生过什么。

### 3.10 工具结果与语音播报分离

工具的执行状态和播报时机是两个维度：

```text
effect_status: pending | completed | failed | cancelled
delivery_schedule: interrupt | when_idle | silent
```

调度由工具 effects、当前活动和模型指令共同确定，不通过工具名、关键词或
正则硬编码。provider 不支持非阻塞工具时，应结构化降级为串行。

### 3.11 Provider 使用能力协商

Voice Runtime 应依据能力而不是供应商名称决定路径，至少描述：

```text
native_audio_input / native_audio_output
server_vad / semantic_vad / manual_activity
audio_truncation
input_transcript / output_transcript / aligned_transcript
exact_script
non_blocking_tools / parallel_tools
affect_observation
```

未知能力默认为不支持；不做 fake action。half-cascade 也通过同一能力协商
产生，而不是单独写 provider 分支。

### 3.12 可重放事件测试

真实语音验收应保存可控的测试夹具和事件记录：

```text
输入音频
VAD/turn 信号
ASR revision
模型 directive/candidate
工具结果
TTS/播放回执
最终 MemCore 投影
```

同一份音频可以更换 VAD、ASR 或 Turn Controller 离线重放，比较状态转移和
延迟。测试夹具与运行日志必须分开，不能把真实用户音频或敏感路径提交进仓库。

## 4. 暂时不采用的做法

- 只用 VAD，检测到任意声音就停止播放；
- 每个 audio frame 或每个 partial 都请求一次文本模型；
- 用固定字数、固定毫秒数代替语义边界；
- 把未播放的完整回答当成已说出口的对话；
- 为了追求全双工而立即替换现有级联链路；
- 让语音模式、同声传译模式成为互相独立的硬开关；
- 用关键词/正则决定是否打断或是否执行副作用工具。

## 5. 进入状态机设计前必须冻结的接口

在正式写状态机前，至少冻结以下事件字段：

```text
voice_turn_id
response_id
conversation_generation
turn_revision
response_generation
speech_unit_id
event_kind
provisional / speculative / committed
delivery_status
correlation_id
audio_clock
artifact_ref
delivery_schedule
```

并明确五条不变量：

1. 同一 `voice_turn_id` 只有一个权威 `final` 和一个 `commit`；
2. 旧 conversation/response generation 的候选、工具结果和音频不能覆盖当前
   generation；
3. 事件日志可以比模型可见上下文更完整，但模型可见上下文不能把“计划输出”
   渲染成“已送达事实”；
4. 只有播放端可以确认 `delivered`，TTS 完成不能代替播放回执；
5. false interruption 恢复的是同一 response 的未播放缓冲，不创建一条伪造的
   新 assistant 回复。

这些接口冻结后，再设计状态机才不会因为换 ASR、TTS 或原生实时 provider
而返工 MemCore。
