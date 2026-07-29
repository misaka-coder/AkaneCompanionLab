# Akane Voice Runtime V1

状态：方案与状态机已冻结；实时 ASR、durable Voice Host、MemCore、Thinking Agent、
TTS、桌宠播放 ACK、自动端点、连续通话、Slice C 播放期语义抢话及候选回复采用闭环已完成生产装配
日期：2026-07-29

本文档定义 Akane 面向低延迟语音对话的第一版运行时方案。目标不是单独增加
ASR 或 TTS 接口，而是让系统侧的实时音频处理、模型侧的语义判断，以及
MemCore 的事件时间线形成一个完整闭环。

## 1. 目标

V1 需要支持：

- 高准确率语音转文字；
- 用户说话时，Akane 可以先降低当前播放音量，而不是检测到人声就立即截断；
- 在用户尚未说完时，由模型根据语境判断是否应该继续、暂停、准备回复或接管；
- 模型可以提前生成候选回复、预取只读记忆或预合成语音，以降低首句延迟；
- 用户说完后，最终文本与前面的 partial/pulse 请求属于同一个语音轮次；
- 被打断的助手原文、实际送达状态、打断事件都进入统一事件时间线；
- “你刚才说什么，继续”这类追问可以依据原始未完成回复自然恢复；
- 后续可以接入持续双向实时音频模型，而不用重做 MemCore 和对话事件协议。

V1 先使用现有的文本级联链路：

```text
ASR → 文本模型 → 流式文本分段 → TTS → 有序播放
```

实时音频模型作为后续 provider，不作为 V1 的必要前提。

## 2. 设计原则

### 2.1 模型决定语义节奏，系统执行音频事实

模型可以根据当前对话和事件流判断：

- 用户是在附和、补充、纠正还是开启新问题；
- 当前助手语音是否应该继续；
- 是否可以并行准备下一段回答；
- 是否需要在当前语音单元结束后停止；
- 当前是否处于持续翻译、讲解或其他连续交互上下文。

系统负责：

- 回声消除、VAD、音量降低和播放队列；
- 执行模型提出的播放策略；
- 取消或保留可取消的生成任务；
- 保证语音单元按序播放；
- 记录真实送达状态。

模型不能直接操作声卡、设备或低级电机接口。它产生的是可审计的交互策略，
由 Voice Runtime 依据当前状态执行。

### 2.2 事件流统一，但不把所有高频噪声当成记忆

语音、工具调用、工具结果、播放状态和中断都属于同一条事件时间线。
不过，ASR 每个字符的临时修订、每个音频帧和每次 VAD 波动不应逐条进入普通
记忆渲染。

运行时可以保留高频内部状态；进入 MemCore 的是有语义意义的检查点和状态
转换。这样既保留“模型知道自己做过什么”的优势，也避免同一段稳定前缀重复
堆积。

### 2.3 原始回复与实际送达必须同时保存

助手生成的完整语音原文不能因为被打断而丢失。
但模型也必须知道其中哪些内容实际送达、哪些内容只是计划输出。

因此不能只保存 `delivered_text`，也不能把 `full_text` 渲染成用户已经听完。
两者必须带有明确的 delivery 状态。

### 2.4 不做字符级中断定位

V1 以完整的语义语音单元作为播放和记忆边界。
不要求保存“精确到第几个字符”的播放位置。

语音单元可以是一个自然短句或一个连续表达片段，具体长度由模型标点、
语义完整性、TTS 能力和当前播放队列共同决定，不设僵硬的固定字数规则。

### 2.5 三层职责

V1 在逻辑上分成三层，但不要求立刻拆成三个独立服务：

```text
Interaction Kernel  # AEC/VAD/ducking/播放缓冲/音频时钟
Turn Controller      # 附和、抢话、等待、继续和接管
Thinking Agent       # 正式回答、MemCore、检索和工具
```

Interaction Kernel 只报告和执行设备事实。Turn Controller 结合声学、转写和
当前活动做轻量语义决策。Thinking Agent 不承担毫秒级声卡控制。

### 2.6 单一正文与派生语音单元

模型回复只有一个正文权威：`speech`。模型不再同时填写
`speech_segments`，避免两个字段内容不一致、互相覆盖或恢复时选错正文。

宿主使用 MemCore 的公共分段器，从流式 `speech` 中按当前语言的自然句末
标点和换行派生语音单元。中文的 `。！？`、英文的 `.?!`、标点簇和省略号
都属于分段线索；它们不是另一份回复正文。

```text
model speech
  → StreamingSpeechParser
  → speech_unit / 临时客户端 speech_segments 投影
  → TTS 与有序播放
```

派生单元只服务于低延迟展示、TTS 和播放调度，不能单独写成第二份 MemCore
正文。完整 `speech` 连同原始标点仍用于最终回复、记忆和中断恢复。聊天气泡
可以在普通句末隐藏单个中文句号，但 `？/！/?/!`、单独标点和 `？！/!?`
等标点簇必须保留；TTS 与记忆不得使用这种展示裁剪文本。

## 3. Voice Transaction

每次用户开口都拥有一个稳定的 `voice_turn_id`。一次语音轮次包含多个阶段，
但最终只产生一个权威用户输入：

```text
voice.turn.open
  → voice.asr_checkpoint*
  → voice.semantic_pulse*
  → voice.final
  → voice.commit
```

`*` 表示可以有零个或多个检查点。检查点只在稳定前缀发生有意义的变化，或
需要请求模型做语义判断时产生，不按固定时间机械轮询。

### 3.1 ASR 状态

ASR 需要区分：

- `partial`：可能继续修订；
- `stable_checkpoint`：当前前缀在短时间内稳定，可交给模型预处理；
- `final`：本轮权威转写。

ASR 的最终准确率是 V1 的硬验收项。重点测试：

- 日常中文口语和省略表达；
- 人名、角色名、群昵称和项目名；
- 中英文混说；
- 工具名、文件名和 URL；
- 噪声、回声和用户距离变化。

流式 partial 可以不完美，但不能把 partial 直接当作正式用户消息或正式记忆
元数据目标。

### 3.2 语义脉冲

当 ASR 产生稳定检查点时，Voice Runtime 向当前模型会话追加一个短事件。
请求不只是“要不要打断”，还可以同时返回：

- 播放策略；
- 当前输入的临时意图；
- 是否继续听；
- 是否开始准备候选回复；
- 是否预取只读记忆；
- 是否预合成语音但暂不播放。

示例输入：

```text
[时间] event.voice.playback
response_id: r42
segment_id: 3
text: "如果把它接入桌面机器人，第一步应该先……"
status: playing
completed_segments: [1, 2]

[时间] event.voice.partial
voice_turn_id: v18
stable_text: "不对，我想说的是……"
speech_duration_ms: 1380
overlap_ms: 960
status: still_listening
```

示例模型输出：

```json
{
  "interaction_directive": {
    "playback": "finish_current_unit_then_stop",
    "input_handling": "take_over",
    "response_preparation": "speculative"
  },
  "speech": "",
  "candidate": {
    "candidate_id": "c17",
    "purpose": "prepare_reply",
    "playable": false
  }
}
```

`interaction_directive` 是低级执行原语，不是固定的“翻译模式”开关。模型可
根据当前会话上下文选择继续翻译、并行准备、暂停或接管。未知的可选提示字段
可以被运行时忽略，但核心播放执行必须有结构化且可审计的结果。

### 3.3 最终确认

用户说完后，Voice Runtime 追加同一 `voice_turn_id` 的 `voice.final`。
模型可以复用前一次候选结果，也可以放弃候选并重新生成。

候选结果必须绑定：

```text
voice_turn_id
response_id
source_turn_revision
conversation_generation
response_generation
candidate_id
```

final 到达后，宿主 verifier 再把候选绑定到当前 `final_turn_revision`。只接受
同一 turn lineage 且当前 generation 的候选。过期候选只能被记录为
`discarded`，不能覆盖新回答或写入正式记忆。

## 4. 播放和打断

### 4.1 本地即时反应

检测到疑似用户说话后，系统可以立即降低当前播放音量。这一步不等待模型，
用于改善用户抢话时的听感，但不代表已经确认打断。

确认过程应结合：

- 回声消除后的语音概率；
- 持续说话时间；
- ASR 稳定度；
- 是否出现自然停顿；
- 与当前播放的重叠时长；
- 声学特征和对话上下文。

不使用“检测到任意人声就停止”的硬编码规则。

### 4.2 播放动作

模型可要求：

- 保持当前单元继续播放；
- 播放完当前语义单元后停止；
- 立即停止并接管；
- 保持播放，同时准备下一轮；
- 将候选语音加入队列但暂不播放。

如果模型请求尚未返回，默认保持当前语义单元完成并继续监听，不直接把完整
回复丢掉。用户显式的本地停止动作可以绕过等待立即停止。

疑似打断还必须允许恢复：

```text
playing
→ suspected_interruption
→ ducked
→ confirmed_interruption | false_interruption
```

如果等待期内没有形成有效用户语音，恢复同一 response 尚未播放的缓冲，不创建
新的 assistant 回复。确认抢话后才清空队列并提交中断。

### 4.3 语音单元送达记录

助手输出应拆成带顺序号的语音单元：

```text
segment 1: completed
segment 2: completed
segment 3: interrupted
segment 4: not_started
```

若单元 3 在中间被打断，不要求字符级定位。下一轮以单元 1、2 为确定送达
内容，以单元 3 的完整原文和 `interrupted` 状态为上下文证据。

每个单元还应维护交付账本：

```text
generated → synthesized → queued → delivered
                                  ↘ interrupted / not_started
```

这些是状态，不要求复制四份相同文本。只有存在独立 speech plan 时才记录
`planned`。只有播放端可以确认 `delivered`。

## 5. MemCore 投影和渲染

### 5.1 需要进入统一时间线的事件

建议使用以下事件族：

```text
message.user.voice
event.voice.turn_open
event.voice.asr_checkpoint
event.voice.semantic_pulse
message.assistant.voice
event.voice.playback_unit
event.voice.interrupt
event.voice.candidate
event.voice.commit
```

事件应保留：

- 时间戳；
- `voice_turn_id`；
- `response_id` / `segment_id`；
- actor 和会话；
- 原始完整文本；
- 当前状态；
- 事件之间的 lineage / correlation；
- 是否为 provisional、speculative 或 committed。

工具请求、工具结果和语音请求处于同一打开回合时，继续使用 MemCore 的
typed action/observation 记录，不另建一套语音专用记忆。

### 5.2 检查点去重

所有检查点可以进入事件存储，但必须通过同一个 `voice_turn_id` 和
`revision_of` 关联。模型可见渲染层按语义检查点合并重复前缀：

```text
旧检查点："我想说"
新检查点："我想说的是这个方案"
```

不应把它们渲染成两条独立的用户消息。
原始事件仍保留，当前语音块显示最新稳定版本，并保留必要的修订关系。

### 5.3 被打断的回复渲染

当前轮必须能看到完整原文：

```text
[时间] assistant.voice
response_id: r42
full_text: "如果把它接入桌面机器人，第一步应该先……"
delivery_status: interrupted
delivered_segments: [1, 2]
interrupted_segment: 3
```

模型应理解：这是自己之前计划说出的内容，不是用户指令，也不代表用户已经
完整听到。

历史轮次不应无条件把所有完整语音原文堆进每一轮上下文；当前活动轮优先
完整渲染，历史内容通过 MemCore 时间线或语义检索按需恢复。这样不会牺牲
“刚才说什么”的证据，也不会让每个旧语音回复永久膨胀当前 prompt。

## 6. 推测式回复和工具

语义脉冲可以提前：

- 读取记忆；
- 读取时间线；
- 检查可用资料；
- 生成候选回答；
- 预合成候选语音。

这些操作必须带 `speculative=true`，只有最终语音轮次确认后，候选结果才
能转为正式回答。

有副作用的工具，例如发送、删除、写入、生成文件或改变外部状态，不应仅凭
尚未结束的 partial 执行。它们必须等待最终语音文本或明确确认。这个边界
由工具自身的 effects / cancellable 元数据决定，不通过关键词拦截。

如果 speculative 只读工具结果已经被最终回答采用，则把真实调用链正式追加
到该轮；如果最终文本改变，则保留为已取消或未采用的操作轨迹，不伪装成正式
记忆。

工具结果的执行状态与语音播报时机必须分开。播报调度至少支持：

```text
interrupt  # 立即中断当前语音并告知
when_idle  # 当前语音完成后告知
silent     # 写入上下文，不立即说出
```

provider 不支持非阻塞工具时，结构化降级为串行。调度不能依据工具名或关键词
硬编码。

模型可以生成两种短语音：

```text
backchannel     # 表示正在听，不取得完整发言权
latency_bridge  # 已接管本轮，正式回答仍在准备
```

它们必须带明确 purpose，不能与正式回答混成无法取消的普通文本。

## 7. 模型提示词要求

语音上下文应说明：

1. `partial` 可能修订，`final` 才是权威用户输入；
2. 当前播放内容的完整原文、已送达单元和被打断单元分别代表什么；
3. 被打断的助手原文不是用户指令；
4. 用户问“刚才说什么”时，应基于完整原文复述；
5. 用户说“继续”时，应从未完成的语义单元继续，而不是重写成无关答案；
6. 模型可以在用户未说完时准备候选结果，但不应把未确认候选当作已送达；
7. 连续翻译、讲解或其他交互方式由当前上下文决定，不用固定功能开关；
8. 语音输出使用自然、可独立朗读的语义片段和标点；
9. 只为最终正式回合输出一次 memory metadata。

模型应同时看到事件和最终自然语言，不把事件结构当成对用户说的话。

## 8. 缓存和性能

稳定的人设、工具 schema、MemCore 规则仍然位于固定前缀。
语音播放状态、partial、pulse 和最终文本都追加在动态尾部。

连续请求应复用同一对话的 provider cache family。
不能把当前毫秒、音频帧、request id 等动态数据塞入系统前缀。

必须分别记录：

```text
audio_capture_ms
asr_first_stable_ms
semantic_pulse_request_ms
model_first_control_ms
model_first_speech_ms
tts_first_audio_ms
playback_start_ms
interrupt_decision_ms
false_interruption_resume_ms
final_commit_ms
```

不能只看“整轮完成耗时”，否则无法知道延迟究竟来自 ASR、模型、TTS 还是
播放队列。

## 9. 实施阶段

### Slice A：文本流模拟

- 使用现有文本模型的单一 `speech` 正文，并由系统派生语音单元；
- 模拟播放时间和语音单元中断；
- 验证 candidate、generation、delivery 状态和 MemCore 渲染；
- 不接真实麦克风。

当前已落地的最小链路是：

```text
LLMRuntime / MemCore speech_segment
  → Akane VoiceResponseStreamBridge
  → voice.speech_unit.declared
  → VoiceCore start_tts / enqueue_playback
  → fake TTS / fake playback observation
```

桥不重新分句，也不把 `speech_chunk` 写入语音状态或记忆。每个完整
`speech_segment` 只持有一个宿主文本 artifact 引用；最终 `final.payload.speech`
才形成一次权威 `voice.response.generation_completed.full_text`。因此第一段可在
全文完成前进入 TTS，同时 MemCore 的 `message.assistant.voice` 仍只投影一次完整
正文。中断流不会把尚未闭合的残句刷新为完整语音单元。

Slice A 的 reducer 单元测试仍使用 fake playback；生产 Host 已改用需要客户端
ACK 的交付 channel。Akane 已提供
`SqliteVoiceRuntimeJournal`、`FileVoiceTextArtifactPort` 与
`FileVoiceAudioArtifactPort`；journal、MemCore projection 与 Thinking Agent 已用于
实时 ASR 生产接缝。生产 Host 已接入正式 `start_tts` executor，但在客户端播放
协议尚未协商前，Thinking Agent 仍不声明 speech unit；这样不会留下永远等待
`enqueue_playback` 的半完成 response：

- 宿主传入 instance 自己的 `state_dir`，端口按会话身份哈希建立私有存储桶；
- journal 使用单会话 SQLite 事务日志，事件行、权威 head 和该次 transition
  派生的 projection outbox 在同一事务提交，支持幂等追加、完整性校验和
  VoiceCore 确定性重放，避免高频语音事件制造大量小文件；
- 文本 artifact 不可变，只通过 `voice-text:<digest>` 引用，不把本地路径放入
  snapshot、command、日志或 prompt；
- TTS 输出先作为单文件原子 envelope 写成不可变 `voice-audio:<digest>`，元数据与
  音频字节不会因 sidecar 写入中断而分裂；`voice.tts.ready` 只在 artifact 可读后
  产生，不等于已经交给客户端，更不等于已经播放；
- TTS command 使用 VoiceCore `idempotency_key` 派生稳定 artifact 和 observation
  身份。重启恢复时只有同一 artifact 可完整读回才确认 ready；command intent 已
  落盘但 artifact 缺失时停在 `voice_tts_outcome_unconfirmed`，不盲目重复请求可能
  产生二次计费的 provider；
- TTS provider、文本 artifact 或音频落盘失败会归约为
  `voice.tts.failed(reason_code)`；异常正文、路径、Host 和 Key 不进入事件；
- journal 或 artifact 损坏、缺失、内容冲突时结构化失败，不跳过损坏记录继续
  伪造完整状态；
- projection 按 VoiceCore 的稳定 `projection_id` 顺序投递并在成功后确认；
  失败或崩溃时保留 pending，恢复后至少一次补偿，接收端必须按
  `projection_id` 幂等；
- pending projection 补齐前，Host 不执行后续模型、TTS 或播放 command，避免
  状态已推进但 MemCore/宿主仍缺少触发事实；
- 重放只重建权威 snapshot，不盲目重发全部历史 projection；只补偿 outbox 中
  尚未确认的记录。若投影已送达但确认前崩溃，接收端的幂等键会消除重复副作用。
- Host 在调用 command executor 前先持久化 command intent；executor 成功返回
  后，整批 observation 必须先作为 command receipt 落盘，再逐条交给 VoiceCore；
- 一个 command 可以返回任意数量的 observation，但只有批次最后一条携带
  `payload.command_id` 作为完成确认。中间 observation 使用原有 causation /
  correlation，不得提前清除 pending command；
- 若重启时 receipt 已有 observation 批次，Host 直接按原 `event_id` 补偿，不再
  重做 TTS、播放或模型请求；若只留下 `executing` intent，则 executor 必须按
  `idempotency_key` 查询真实结果。无法确认时结构化停在
  `command_recovery_unavailable`，不能盲目重试可见副作用；
- executor 返回 `deferred/failed` 但没有 observation 时，契约上表示外部副作用
  尚未提交，Host 才可释放 intent 供以后安全重试；结果不确定时必须返回
  unknown，而不能伪装成普通失败。

`BotRuntime` 现在按实例拥有一个 `AkaneVoiceRuntimeService`，并在
`/voice/realtime` 路由中注入真实 coordinator factory。服务只在 MemCore、
实时 ASR provider 和 voice turn resolver 都可用时开轮；否则返回结构化状态和
安全摘要。`start_response_generation` 已接到 Akane 现有 Thinking Agent；它复用
已提交的 `message.user.voice` source/turn，不把 ASR final 再送普通消息入口，工具
轨迹也继续挂在同一 MemCore turn。正式 TTS effect 和服务端播放回执边界已经
接入，但桌宠播放客户端尚未接入；现有文件式 `/asr` 能力没有被替换。

### Slice B：高准确率 ASR 接入

- 为 ASR provider 增加流式会话门面；
- 验证中文、专名、混合语言、噪声和回声；
- partial 只产生检查点，不直接成为正式记忆；
- final 负责正式回合提交。

当前已落地的 Slice B 最小门面是：

```text
capcore-adapter-speech provider session
  → partial / stable_checkpoint / final revision
  → Akane VoiceASRSessionBridge
  → voice.asr.partial / checkpoint / finalized
  → voice.turn.commit_requested
  → VoiceCore message.user.voice（仅一次）
```

- 语音包拥有 provider-neutral revision 规范与
  `open_session/feed_audio/finalize/cancel` 会话；
- 真正支持 streaming 的 client 通过 `open_streaming_session(...)` 接入；
- 现有批量 `transcribe(...)` 明确报告 `final_only`，feed 不伪造 partial；
- final 即使与最后 checkpoint 文本相同，也使用新的 revision；
- provider receipt 和 VoiceCore receipt 都支持重复投递去重；
- Akane 薄桥只做事件映射，正式 final 后提交一次用户消息，不调用模型、不保存
  音频帧，也不另建输入状态机；
- provider 失败会投影为结构化 `voice.turn.failed`，不会拿 partial 冒充 final。

2026-07-27 增补的首个真实 provider 切片：

- `capcore-adapter-speech` 已实现阿里云 `fun-asr-realtime` 官方 WebSocket
  `run-task → binary audio → result-generated → finish-task` 协议；
- 业务空间 Host 被固定转换为
  `wss://<workspace>.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference`，只接受
  阿里云官方地域域名，不允许模型或用户文本指定任意外发地址；
- provider 的 `sentence_end=false` 映射为 `partial`，
  `sentence_end=true` 映射为 `stable_checkpoint`，只有 Voice Runtime 主动
  `finalize()` 才形成一个权威 `final`；
- 多个 provider 句子在 final 时线性合并，不把一句话的中间修订重复提交成多条
  用户消息；
- `DASHSCOPE_API_KEY` 只存在于 Bot 私有运行时设置和 WebSocket 握手头，不进入
  descriptor、公开 snapshot、错误摘要、日志或 prompt；
- Akane 已有默认关闭的 `build_voice_asr_provider()` 装配门面，并结构化区分
  `disabled / missing_config / invalid_config / ready`。

QQ 麦克风采集端仍未激活，也没有使用真实用户录音做云端调用。浏览器
AudioWorklet 捕获和真实播放 ACK 已接入 `desktop_pet_next`；后续仍需增加
可重放 PCM 输入夹具，再做脱敏音频 A/B。现有 WebM 文件上传字节不会被标成
PCM 发送。

provider builder 已由 `BotRuntime` 的实时语音服务调用，桌宠也已具备新入口客户端；
旧 `/asr` 继续作为实时链路在 final 提交前失败时的明确降级通道。当前测试覆盖
协议夹具、人工 revision、production host 装配和客户端播放队列，不代表中文、
专名、噪声、回声等真实音频准确率已经验收。

2026-07-28 的真实专属业务空间 smoke 已验证：

- WebSocket 建连约 150–165ms，首个 partial 约 400ms；
- 4.7 秒固定中文测试语逐字识别正确；
- 持续补送静音 PCM 后，VAD 能在音频流仍打开时产生 stable checkpoint；
- `finish-task → task-finished` 的 provider 收尾确认约需 3 秒，不能让这一段
  纯传输生命周期阻塞所有模型准备工作。

Akane 因此增加 `VoiceASRRealtimeTurnCoordinator`，但不增加第二套权威状态机：

```text
stable checkpoint
  → VoiceASREarlyCandidate（只允许 non-playable speculative 工作）
  → provider finalization 在独立 task 中继续等待
  → normalized final
  → VoiceASRSessionBridge
  → VoiceCore final + commit
  → 单一 message.user.voice
```

协调器不会调用模型、播放候选、写正式 assistant 记忆或自行判断候选兼容性。
它只让宿主在等待 provider final 时拿到稳定文本和 source revision。候选采用
仍必须经过 VoiceCore 的 revision fence 与 `voice.candidate.validation_result`；
provider open/finalize 失败继续结构化写入同一语音轮，不能静默丢失。

实时音频入口与旧文件 `/asr` 有意分开：

- `capcore-adapter-speech.PCMStreamNormalizer` 接受带 `sequence` 和
  `audio_clock_ms` 的 `s16le`/`f32le` 帧；
- 输出固定为 16 kHz、单声道、little-endian `s16le`，48 kHz 等输入使用
  有状态 libsoxr，不能把 WebM/OGG 容器字节重命名为 PCM；
- 重复帧幂等丢弃，序号缺口、同序号内容冲突、时钟倒退和坏帧结构化失败；
- normalizer 不拥有麦克风权限、VAD、endpointing 或模型调用；这些仍由宿主
  输入层和 Voice Runtime 协调。

桌宠仍会为同一段录音保留 `MediaRecorder → 整段 WebM → /asr` 安全副本，但
WebM 只进入批量降级通道，绝不会改名或送进实时 PCM 入口。收到已提交的
`server.final` 后立即丢弃安全副本，避免同一用户语音重复写入 MemCore。

2026-07-28 增加了独立的 `/voice/realtime` WebSocket 传输契约。该入口不把
传输协议变成第二套语音状态机，只负责把有序 PCM 帧交给
`VoiceASRRealtimeTurnCoordinator`：

```text
client.open
  → server.ready
client.audio(sequence, audio_clock_ms)
  → 下一帧 binary PCM
  → server.partial / server.checkpoint / server.candidate_ready
client.endpoint
  → server.finalizing
  → server.final
client.cancel
  → server.cancelled
任一阶段失败
  → server.failed(status/reason/retryable/terminal)
```

- `client.open` 明确携带 profile、conversation、session、可选 character pack、
  输入格式、采样率和声道；`voice_turn_id` / `audio_stream_id` 可由服务端生成，
  `voice_session_id` 由服务端创建并在 ready 返回；
- 每个 `client.audio` JSON 帧头后只能跟一个 binary PCM 帧。音频不做 base64，
  也不写入日志、prompt 或 MemCore；
- endpoint 只启动后台 finalization。约 3 秒的 provider 收尾期间，WebSocket
  仍能接收重复 endpoint、cancel 和断线；
- partial/checkpoint 只返回明确字段，不回传 provider 原始响应。candidate
  始终标记 `speculative=true, playable=false`；
- 只有 normalized final 经 VoiceASRSessionBridge 成功提交后才返回
  `server.final`；重复 endpoint 不产生第二个 final 或第二条
  `message.user.voice`；
- 帧缺口、同序号内容冲突、坏 PCM、provider 失败和未装配状态都会返回结构化
  `server.failed`，不会静默，也不会用 partial 冒充结果；
- 客户端取消或断线会取消同一 provider session，不留下 capturing 的幽灵回合；
- 传输日志只记录耗时、帧数、字节数和 provider id，不记录转写正文、音频、
  Key、Host 或本地路径。

2026-07-28 增加了可选的 `binary_audio_ack_v1` 输出协商。旧客户端不提供
`client.open.output` 时仍使用 `text_only`，收到 `server.final` 后按原契约关闭；
声明输出能力的连接则保持为双向通道：

```text
client.open(output.mode=binary_audio_ack_v1)
  → server.ready(output.acknowledgements_required=true)
模型产生首个完整 speech_segment
  → TTS → immutable voice-audio artifact
  → server.speech(JSON header, binary_follows=true)
  → 下一帧 binary audio
client.playback.enqueued
  → voice.playback.enqueued
client.playback.started
  → voice.playback.started
client.playback.completed / interrupted / failed
  → 对应 VoiceCore terminal observation
VoiceCore duck_playback / resume_playback / stop_playback
  → server.playback.control
  → 桌宠实际降音 / 恢复同一音频 / 停止目标单元
  → client.playback.control_ack
  → voice.playback.control_acknowledged
全部语音单元终态且模型 final 已提交
  → server.response.completed / failed
```

- JSON header 只携带 delivery/response/unit 身份、序号、文本、media type 和字节数；
  不携带 artifact ref、本地路径或 provider 私有信息；
- 服务端写出 binary frame 仍不算入队。只有客户端明确发送
  `client.playback.enqueued` 后才清除 `enqueue_playback` command；
- `completed` 必须晚于 `enqueued` 和 `started`，乱序 ACK 会结构化拒绝且不推进
  VoiceCore；同一 ACK 可幂等重复，内容冲突会失败；
- 多个 speech unit 即使已经并行/提前合成，也只在前一单元 terminal 后按 ordinal
  交付下一单元，不允许音频队列越序；
- 连接在 queued/playing 阶段断开时记为 interrupted，在尚未入队阶段断开时记为
  failed；两者都不能伪造成 delivered；
- 模型完整正文与用户实际听到的单元继续分开记录，只有所有单元 completed 才产生
  `delivery_status=delivered` 的 `message.assistant.voice`；
- TTS、播放控制与 ACK 只追加到 Voice Runtime 动态尾部，不改写稳定系统提示词，
  高频播放状态也不进入普通 prompt，因此不会为了语音交付破坏既有前缀缓存。
- `server.ready.output.playback_controls` 明确协商 `duck/resume/stop`；控制请求带稳定
  `control_id` / VoiceCore `command_id`，同一回执可以幂等重放，身份或结果冲突会
  结构化失败；
- `server.playback.control` 写入 WebSocket 不代表动作生效。只有桌宠返回
  `client.playback.control_ack(status=applied)` 后，VoiceCore 才确认命令完成；
  `stop` 的 applied 回执才会把目标单元记为 `interrupted`；
- `duck` 只降低当前音频的实际音量，不暂停、不从头重播；`resume` 恢复同一 audio
  element 和用户音量；`stop` 回传目标单元实际 `played_ms`。目标已结束或不匹配时
  返回 failed，不伪装成已执行；
- 播放控制失败会作为 VoiceCore 事实清除对应 pending command，并保留最后一个已
  确认的播放状态；晚到的 stop 与自然播放完成相撞时，不会把已 delivered 的单元
  回滚为 interrupted。

路由继续使用注入式 coordinator factory，真实 `BotRuntime` 已装配：

```text
BotRuntime
  → AkaneVoiceRuntimeService（实例私有）
  → SqliteVoiceRuntimeJournal replay / projection outbox 补偿
  → VoiceASRRealtimeTurnCoordinator
  → VoiceCore message.user.voice（一次）
  → MemcoreManager typed V2 turn
```

- 会话存储桶只使用实例、用户、会话、角色等身份的摘要，不把原始 namespace、
  本地路径或密钥写入 snapshot、prompt 或日志；
- provisional `event.voice.asr_checkpoint` 会持久化，但
  `prompt_visible=false`、不可检索，不会让相同前缀反复进入模型；
- final `message.user.voice` 打开一个稳定派生的 MemCore turn，后续
  `message.assistant.voice` 完成同一 turn；前端不得再把 final 转写走普通文本
  入口重复提交；
- typed assistant final 在 provider projection 中保留 kind、全文和交付状态；
  普通 `message.assistant` 的既有纯文本投影不变；
- projection outbox 重放继续依赖 VoiceCore `projection_id` 和 MemCore source id
  双重幂等，重启不会重复写用户语音或助手语音；
- MemCore 不可用、journal replay 失败、pending projection 无法补齐或 provider
  不可用时，均在打开音频会话前结构化失败。

`start_response_generation` 的默认生产链路现在是：

```text
VoiceCore durable command
  → voice.response.created
  → voice.response.generation_started
  → 后台复用 Akane process_turn_stream（检索 / 工具 / 流式 JSON）
  → voice.response.generation_completed
  → message.assistant.voice（text_only）
```

- command observation 先写 journal receipt，再启动后台模型请求；模型耗时不会
  阻塞 ASR `server.final`；
- 当前语音尾部直接复用 MemCore provider projection 中该 source 的完整 typed
  payload；下一轮它转入历史时字节结构保持一致，不用普通 user 文本重新包装，
  因而不会为了标注语音模态牺牲前缀缓存；
- `server.final.response` 会明确返回 `started / failed`、response id、retryable 和
  安全摘要，不能只给出 ASR 成功却悄悄不启动回复；
- 模型异常、缺 final 或不可交付 transient final 会进入
  `voice.response.failed → event.voice.failure`，不写假的 assistant final；
- 最终答复需要修复重试时，正式 user 消息和 MemCore provider projection 保持
  原样；修复说明只作为请求级临时尾部追加。这样模型能看见 `speech` 不能为空、
  不能只给占位话的具体修复要求，同时不污染时间线，也不改写已经缓存的前缀；
- 重启时先补偿 command receipt，再从 VoiceCore 中仍处于 generating 的 response
  恢复后台任务；同一进程按 response id 去重；
- 未协商 playback 时，流式 `speech_segment` 不声明为 VoiceCore speech unit，最终
  完整 `speech` 仍以 `text_only` 完成；协商 `binary_audio_ack_v1` 后才启用
  `speech_segment → TTS → playback ACK`。缺少客户端 ACK 时，
  `enqueue_playback` 保持 pending，不会伪造 queued/started/completed。
- 连续通话的 TTS 不再直接使用 Bot 的全局 Edge client。Host 会按本轮
  `profile_user_id + character_pack_id` 复用普通桌宠 TTS 的角色 provider/profile
  解析，并把实际 `provider_id` 写进 `voice.tts.started/ready/failed`。角色明确
  请求 GPT-SoVITS 时，provider 不可用会结构化失败，不允许无提示地换成另一个
  Edge 声线；未配置角色声线且默认请求 Edge 的角色仍可正常使用 Edge。

2026-07-28，`desktop_pet_next` 已在本地接入该入口：AudioWorklet 以约 20ms
的 `f32le` 单声道帧发送，WebSocket 依次发送帧头和 binary PCM；服务端音频由
同一个 `voice-player` 串行播放，并且只在真实入队、`play()` 成功、`ended` 或
播放失败/中断后发送对应 ACK。MediaRecorder 同步保留安全副本，但只有
`server.final` 之前失败才回退旧 `/asr`，不会重复提交已经进入 VoiceCore/MemCore
的语音轮。语音输出关闭或 WebView 不支持 AudioWorklet 时仍保持旧听写体验。

客户端库现已增加通话级资源所有权：`RealtimeVoiceCallResources` 持有一场通话
唯一的麦克风流和播放器，不同语音轮各自保留 WebSocket/ACK 通道，但音频交付由
通话级仲裁器串行取得播放器所有权。结束单轮只注销该轮播放队列，不停止麦克风
轨道；只有结束整场通话才停止轨道并清空播放器。主入口现已用这份所有权实现
连续多轮通话，按钮不再逐轮创建和销毁麦克风。

通话资源路径也已持有唯一 AudioContext/AudioWorklet。每个
`RealtimeVoiceSession` 只取得当前 Input Turn 的 PCM sink 租约：开始前先用
`reset` 回执丢弃无人监听期间不足一帧的残留，结束时用 `flush` 把最后一帧交给
原轮次后释放；异步释放尚未完成时，紧接着的新轮次等待同一交接任务，不瞬时
报“麦克风忙”。单轮结束不关闭 AudioContext，整场 `close` 才停止 worklet 和
媒体轨道；`reset/flush` 未收到 worklet 回执时会结构化失败，不能把可能串轮的
音频当成成功。该路径已有连续多轮、残帧隔离、立即换轮与回执超时测试，桌宠
主入口也已复用它；每轮安全录音仍独立收口，不能跨轮拼接。

客户端还增加了独立的 `RealtimeVoiceEndpointDetector`。它不按关键词或固定回复
判断语义，而是把自适应噪声底、短时 RMS/迟滞、有效发声时长与 ASR
`partial/checkpoint` 组合起来：稳定 checkpoint 后允许较短静音收尾，只有 partial
时保留更长等待；单个爆音、持续背景噪声和句中短停顿不提交。检测到有效人声但
始终没有 ASR 文本时形成 `discard/speech_without_transcript`，不请求 Thinking
Agent，也不设置固定的单句最长时限。检测器现已由主入口为每个 Input Turn
实例化，自动 endpoint 只结束当前发言，不结束整场通话。

端点现在分成“候选”和“确认”两步：达到 checkpoint/partial 对应的静音条件时
先进入可撤销的 `endpoint_pending`，继续保持采集；只有静音再稳定保持一个短确认
窗口才真正提交。确认前一旦检测到用户继续说话，候选立即撤销并回到采集中。
这把 VoiceCore 规格中的 `endpoint_pending → activity_resumed` 落到真实声学入口，
避免把思考停顿直接当成不可逆的句末；确认窗口只处理声学迟滞，不按关键词猜测
用户语义，也不设置整句最长时限。

这一客户端切片已部署云端并完成真实 Tauri/WebView2 麦克风、Fun-ASR、TTS
基础链路验证，但连续抢话的人体时序验收尚未通过。实时 final 有界等待；超时或实时链路失败时，客户端会保留的
MediaRecorder 音频改走普通 ASR，并把成功转写自动提交给 Thinking Agent，不能
停在输入框或静默等待。服务端同时提供 finalize/input inactivity 超时与结构化
失败原因。

Slice C 的首个显式接管子步也已落地：用户主动按下麦克风时，可以停止当前
生成/播放并立即开始新一轮录音；播放器会按真实 `interrupted` ACK 记录已播放
部分，不能把整段回复伪装成已送达。该动作代表明确的本地接管，不等同于自动
VAD 抢话。自动 duck、语义脉冲与误打断恢复由下述统一状态机处理；独立
backchannel 回复仍未启用。

Slice C 的播放控制闭环也已落地：VoiceCore 的 `duck_playback`、
`resume_playback`、`stop_playback` 由同一个 Akane playback executor 路由到拥有
目标 speech unit 的活动 delivery channel，桌宠执行后用统一控制 ACK 回传。
VoiceCore 仍是唯一状态机，Akane 没有复制打断转移。

Slice C 的语义判断子步也已落地：`voice.interruption.suspected` 只立即发出
`duck_playback`；直到同一 Input Turn 出现 `control_significant=true` 的稳定
checkpoint 或 final，VoiceCore 才发出带 `turn_revision` 围栏的
`request_semantic_pulse`。Akane 使用独立的只读模型请求执行该命令：

- 复用 MemCore 的 provider-native 历史投影，并在动态尾部补充完整生成正文、已
  送达单元、当前播放单元及 stable/unstable 转写；
- 不暴露 native tools，不执行外部动作，不写新的正式 user/assistant 消息；只有
  真正形成 backchannel/takeover 的 directive 才按 VoiceCore 投影语义事件；
- 使用独立稳定的 `voice-semantic-<identity hash>` cache family，不改普通聊天的
  system prompt 或 cache key；当前播放/转写事实只在该请求尾部变化；
- 新 ASR revision 到达后，旧模型结果以 `stale_turn_revision`/skipped 收束，不能
  覆盖较新的判断；
- 进程重启时不重放只属于旧实时播放通道的 semantic pulse；旧 command 以
  `semantic_runtime_restarted` 结构化跳过，服务完成 durable recovery 后才接受
  新 pulse，不能生成没有播放器可以执行的 resume/stop；
- 模型失败、非法 JSON 或上下文不可用会形成 `voice.semantic_pulse.failed`，并请求
  恢复同一播放单元，不能让声音永久保持低音量或静默消失。

VoiceCore 现在还会把 final commit 与同 revision semantic pulse 做一次权威握手：
final 先到时只记录提交意图，不先生成普通回复；`treat_as_interaction` 最终只落
交互事件，`take_over/commit_when_final` 才落正式语音消息。checkpoint 与 final
正文相同时复用已有判断，正文继续增长时才重新请求；语义请求失败则退回宿主
原始 disposition，保证用户 final 不丢失且只提交一次。Akane 的 ASR coordinator
也只在 VoiceCore 已真实提交为 `message` 后启动 Thinking Agent，不能依据入口
默认值越过这道状态边界。

Slice C 的推测式候选回复也已接通。semantic pulse 只有在
`input_action=take_over` 且稳定转写已经足够明确时，才能返回
`response_action=prepare_candidate`。该候选：

- 使用临时 ASR revision 生成，禁止 native tools、外部动作、提醒消费及人物/关怀
  状态写入；不形成正式 user/assistant MemCore 消息；
- 可以提前完成文字生成和 TTS，但 `commitment=speculative`、
  `playable=false`，final 到达前不会产生 `enqueue_playback`；
- final 提交后由独立、只读的候选校验请求比较临时转写、最终转写和完整候选正文，
  并用 `voice-candidate-validator-<identity hash>` cache family 隔离动态尾部；
- 校验通过时采用同一个 `response_id`、文字和音频 artifact，不再请求第二次正式
  回复；校验拒绝或模型失败时丢弃候选，只启动一次绑定 final revision 的正式回复；
- 只有采用后的真实播放终态才完成 `message.assistant.voice`，因此未确认候选不会
  污染正式对话记忆；进程重启时旧实时通道上的未确认候选直接结构化丢弃，不在新
  通话里重播或补生成。

候选启动不依赖轮询。Voice Host 每次完成 durable command drive 后，只尝试接手
已经进入 `generating` 的 speculative job；即使前面的 `stop_playback` 曾等待桌宠
ACK，ACK 解锁命令队列后也会自然启动候选。普通 committed 回复继续由 ASR
coordinator 的正式 response starter 启动，两条路径不会互相抢跑。

桌宠主入口现已接入连续通话控制器。用户点一次麦克风按钮开始通话，按钮在整场
通话中显示“挂”，再次点击、按 Escape、关闭语音能力或退出窗口才结束通话。
`RealtimeVoiceCallResources` 在整场通话中持有同一麦克风、AudioContext 和播放
器；每个 Input Turn 单独创建实时 session、endpoint detector 与安全录音。
检测器只在稳定转写或可确认的语音停顿后提交，无法确认的声学活动会 discard，
不创建用户消息；服务端完成当前回复和真实播放回执后，控制器自动申请下一轮
监听。重复 endpoint、已结束 turn 和挂断后排队的监听请求都由 call flow 拒绝，
不会产生双提交或挂断后偷偷重开。

实时链路在正式提交前失败时，客户端先读取端点检测器的结构化证据。若没有起声
也没有任何转写，网络/协议类可重试故障只关闭当前监听 turn，并按
400/800/1600/3200/4000ms 有界退避重建监听；不会结束整场通话，也不会把有字节
但实际静音的安全录音送给普通 ASR。连续重连耗尽或本地采集能力不可用时明确结束
并报错。只有已经检测到起声或转写、但尚未正式提交的输入才允许用当前 turn 的
安全录音结束通话并转入普通 ASR，避免吞掉用户确实说过的话。正式提交后的回复
失败会明确提示，并继续下一轮。安全录音只属于当前 turn，不作为每轮提示词或
MemCore 上下文，因此不会改变模型前缀或缓存命中。

当前连续通话已经从纯轮流说话推进到回复生成期监听。服务端 `server.final` 确认
本轮输入已经提交且回复状态为 `started` 后，通话控制器便释放 Input Turn 槽并
建立下一轮 ASR，不再等待首个 TTS 单元开始播放。旧回复的 WebSocket、模型生成、
播放队列和 ACK 通道继续存活，因此监听不会伪装成旧回复已经结束。客户端声学
检测器只有确认连续人声后才发送 `client.interruption.suspected`；单纯打开麦克风、
背景静音和短爆音都不是打断证据。

新的监听轮即使听到声音，也不会立即取消旧回复。只有该轮形成可提交端点时，
客户端才先停止并冲洗自己的采集，再发送 `client.cancel`；服务端必须把它归约为
VoiceCore 的 `voice.response.cancel_requested`，真实执行
`cancel_response_generation` 并返回 `server.cancelled`。客户端收到这份取消回执
后才提交新的 `client.endpoint`。取消超时或执行失败会进入结构化失败/安全降级，
不能冒险并发启动两个正式模型回复。这样新用户句子可以在旧回复生成或播放期间
自然接管，而噪音、附和和没有形成端点的半句不会误杀旧回复。

共享 AudioWorklet 在模型生成和 TTS 准备期间也不再丢弃无人认领的麦克风帧：
空档时滚动保留最近 800ms 作为起声预卷；本地声学检测确认起声后冻结并继续收集
这一候选输入，直到下一条唯一的正式 ASR turn 接管，再按原顺序把预卷和后续音频
送入同一个端点检测器和实时 provider。这样用户在“她正在想”时已经开始说话，
句首不会因为下一监听轮尚未建立而消失，也不会为了提前收音并发提交第二条正式
用户消息。每个正式 turn 同时保留同源 Float32 PCM 安全副本；实时链路在提交前
失败时可编码为 WAV 交给普通 ASR，避免 MediaRecorder 只录到接管后的后半句。
空档候选与 PCM 安全副本只在客户端内存中存在，不写 MemCore、不进入模型提示词，
也不改变缓存前缀。

服务端把这份声学证据绑定到 VoiceCore 中唯一处于 `playing` 的真实语音单元，
并立即驱动 `duck_playback`。后续稳定 checkpoint/final 仍走已有 semantic pulse，
由模型在完整播放原文、实际送达单元和当前转写证据上决定 resume、当前单元后停
或立即接管。若声学活动最终没有形成任何 ASR 文本，取消输入轮时会追加
`voice.interruption.false_positive` 并驱动 resume，不能把回复永久留在低音量。
没有真实播放单元时，协议返回 `server.interruption.skipped`，不会 fake duck。

真实人体时序验收曾暴露一项必须修复的失败边界：WebSocket 在回复交付中途终止
时，即使传输层已经进入 terminal，也必须关闭尚未终态的 delivery channel，并
把未完成的播放命令归约成失败/中断事件。否则旧 `enqueue_playback` 会永久占据
Host 的 pending command 队列，使后续正常输入被误报为
`voice_command_drive_deferred`，并诱发多个回复回合并发。传输异常日志只记录
阶段、异常类型和计数，不记录转写、音频或密钥。

恢复时还要区分两种状态：上次只完成输入提交、尚未真正进入模型生成的命令可以
从 durable command 继续；上个进程已经处于 `generating` 的模型请求没有可验证的
连续流和原客户端，必须追加 `voice.response.failed` /
`voice_thinking_runtime_restarted` 后收口，不能在下一场通话里重新生成一段用户
从未听到的旧回复并写成正常助手记忆。

VoiceCore 发出的 `cancel_response_generation` 现在由 Thinking Agent 执行器真实
接管：先把取消意图加入线程安全围栏，再用带 `command_id` 的
`voice.response.generation_cancelled` 作为 durable observation 收口命令。仍在
运行的模型流在下一流事件边界停止消费并关闭 iterator；即使取消与 final 同时
到达，取消围栏也阻止旧 final 被投影成正常助手记忆。该命令纳入恢复白名单，
不能再以未路由的 deferred 命令阻塞后续语音轮。

这仍不是 native speech-to-speech provider 意义上的完全全双工：
`prepare_candidate` 已在级联架构中启用，`prepare_backchannel` 仍未启用。当前
完成的是可恢复、可审计的自动抢话和候选采用闭环，不以本地音量阈值直接停止
模型回复，也不把附和强行生成为一条独立语音。

### Slice C：播放和语义打断

- 加入回声消除、VAD、降音量和语义脉冲；
- 支持当前语音单元完成后停止；
- 支持候选回复和候选 TTS；
- 验证用户打断、附和、纠正、误打断恢复和追问。

### Slice D：低延迟实时 provider

- 接入支持持续双向音频会话的 provider；
- 将 provider 的转写、音频取消、音频截断和工具事件映射到同一 Voice Runtime；
- 使用 provider 能力协商支持 cascade、half-cascade 和 native realtime；
- 不修改 MemCore 的事件语义。

### Slice E：表现端同步

- 将语音单元开始、结束、中断和情绪投影到 Live2D/桌宠；
- 口型、表情、动作和播放队列使用同一个 `response_id`；
- 验证文字气泡、TTS、表情和动作不互相打架。

## 10. V1 验收场景

至少通过以下真实流程：

1. 普通语音提问，ASR 正确，第一段回答先于整轮完成开始播放；
2. Akane 播放时用户附和，不误停；
3. 用户纠正 Akane，音量先降低，模型确认后在语义单元边界接管；
4. 用户问“你刚才说什么”，模型依据完整被打断原文回答；
5. 用户说“继续”，Akane 从未完成单元自然继续；
6. 用户未说完时，候选回复可提前准备，但错误候选不会播放或进入正式记忆；
7. 语音轮中发生只读检索，工具轨迹和结果进入统一时间线；
8. 语音轮中发生不可取消的副作用工具，系统向模型报告真实状态；
9. ASR、模型或 TTS 超时，系统结构化降级，不静默、不伪造成功；
10. 中断、工具调用、最终回复和 `memory_metadata` 不重复污染同一语音轮次；
11. 疑似打断没有形成有效语音时，继续原 response，不重复朗读、不创建伪回复；
12. 工具结果按 `interrupt/when_idle/silent` 调度，执行成功不等于已经播报；
13. 同一份测试音频可离线重放 VAD、ASR revision、directive 和播放事件。

## 11. 前沿方案对照

前沿资料与 Akane V1 的对应关系见：

[`docs/voice_runtime_frontier_review_2026-07.md`](voice_runtime_frontier_review_2026-07.md)

本轮调研没有推翻 V1 的级联路线，反而确认了三点需要在状态机中显式表达：

1. VAD、抢话意图和语义回合结束是三种不同信号；
2. 完整生成原文与用户实际听到的内容需要双投影；
3. provider 的原生实时能力只能通过适配器进入统一 `voice.*` 事件流。

这三点已经冻结，并由 VoiceCore reducer 与 Akane 薄 Host 开始分切片实现；
真实音频入口仍需等待对应 Slice 的独立验收。

## 12. 状态机规格

VoiceCore V1 的权威状态机、事件 envelope、幂等规则、恢复策略和 Slice A
实现门槛见：

[`docs/voicecore_state_machine_v1.md`](voicecore_state_machine_v1.md)

状态机规格将早期简写的 `generation` 拆成
`conversation_generation / turn_revision / response_generation`，实现时不得
重新合并为含义不清的单字段。
