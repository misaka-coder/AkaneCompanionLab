# Akane Voice Runtime V1

状态：方案与状态机已冻结；Slice A 正在以 fake 端口逐步验收，尚未激活生产语音入口
日期：2026-07-27

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

该链路的 TTS、playback 和 projection 仍只使用 fake 端口。Akane 已提供默认
未装配的 `SqliteVoiceRuntimeJournal` 与 `FileVoiceTextArtifactPort`：

- 宿主传入 instance 自己的 `state_dir`，端口按会话身份哈希建立私有存储桶；
- journal 使用单会话 SQLite 事务日志，事件行、权威 head 和该次 transition
  派生的 projection outbox 在同一事务提交，支持幂等追加、完整性校验和
  VoiceCore 确定性重放，避免高频语音事件制造大量小文件；
- 文本 artifact 不可变，只通过 `voice-text:<digest>` 引用，不把本地路径放入
  snapshot、command、日志或 prompt；
- journal 或 artifact 损坏、缺失、内容冲突时结构化失败，不跳过损坏记录继续
  伪造完整状态；
- projection 按 VoiceCore 的稳定 `projection_id` 顺序投递并在成功后确认；
  失败或崩溃时保留 pending，恢复后至少一次补偿，接收端必须按
  `projection_id` 幂等；
- pending projection 补齐前，Host 不执行后续模型、TTS 或播放 command，避免
  状态已推进但 MemCore/宿主仍缺少触发事实；
- 重放只重建权威 snapshot，不盲目重发全部历史 projection；只补偿 outbox 中
  尚未确认的记录。若投影已送达但确认前崩溃，接收端的幂等键会消除重复副作用。

这些端口目前没有在 BotRuntime 或路由中构造。它们没有接入真实 `/asr`、
`/tts`、QQ、桌宠或生产模型路由，也没有替换现有文件式语音能力。

### Slice B：高准确率 ASR 接入

- 为 ASR provider 增加流式会话门面；
- 验证中文、专名、混合语言、噪声和回声；
- partial 只产生检查点，不直接成为正式记忆；
- final 负责正式回合提交。

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
