# 双文本配音第一版：实现与验收

2026-09-29。代码已接入，通用开关默认关闭；本机塞西莉亚已启用，Bot 后端已重启，常驻 GPT-SoVITS 已加载最终 GPT15 / SoVITS8 权重。

## 模型需要知道的事

```json
{
  "emotion": "normal",
  "utterances": [
    {"emotion": "happy", "style": "calm", "text": "（递来茶杯）回来啦。今天辛苦了。", "say": "おかえりなさい。今日もお疲れ様でした。"}
  ]
}
```

text 用中文显示，say 用配置的语言表达同一意思，只包含实际说出口的台词。按 emotion、style、text、say 的顺序写；emotion 和 style 可省略。相同表现可以包含多句话，换表情或语气再分组，不按句数强制增加 JSON 对象。自然句末使用。？！等标点。仅显示时 say:null，沉默时 utterances:[]。

2026-10-06：组内 emotion 选择角色包已有表情，与声音 style 独立。顶层 emotion 表示本轮整体表情，QQ 的表情投递仍只读顶层。桌宠第一组优先用组内 emotion，缺省用顶层；后续组缺省延续上一组。第一组预览时只应用一次实际选中的表情，后续在文字展示或语音开始播放时切换，完整回复到达不重新应用顶层。恢复历史展示时采用最后一个明确的组内表情，缺省用顶层。没有旧历史回填或 MemCore/VoiceCore 迁移，既有音频分句和长度上限保持不变。

实时语音通过宿主的 speech artifact 和可选播放头 emotion 携带相同表情线索；已有回合、交付和取消身份继续限定生效范围。分组回复不另外推送整轮顶层表情，避免它覆盖正在播放的分组表情。

宿主替换旧的 speech 格式提示，而不是追加互相矛盾的例外。模型不填写路径、声音档案 ID、单元编号，也不另写完整 speech。当前新协议不将缺失 say 自动解释成“朗读 text”。

## 已接入的链路

- 流式解析：text 增量送入现有 MemCore 分句器；say 同样按标点分句。前面的音频句子可以在 say 字符串结束前发出。缺少先行 text 的乱序字段在对象闭合后处理。
- 最终结果：utterances 投影成中文 speech/speech_segments，兼容现有展示和记忆入口。没有修改 MemCore 或 VoiceCore。
- 普通桌宠：配音事件携带 style，保留单个下一段预取、短片段 420ms 等待、原音频长度细分、取消和旧音频丢弃。不同 style 不合并。最终帧和重试前缀不重复入队。
- QQ：继续按中文标点发送文字；配音使用完整 say，逐句选择声音档案，合成 WAV 后拼成一条语音。双文本回复不套用旧的 QQ 语音句数上限；自动模式仍对完整展示正文检查长度，过长则改发文字。旧协议保留句数上限，中途失败不发送半成品。
- 实时语音：用通用文本 artifact 保存宿主的显示/配音结构，TTS 读取 say，播放消息携带中文，记忆最终正文仍为中文。播放观察和插话判断读取实际配音台词，不把结构化 JSON 当作已说的话。
- style 与 emotion 独立：style 映射到既有声音档案；该请求不再叠加 emotionVoiceMap。未知 style 使用声明的默认值，TTS HTTP 响应给出 fallback 标记。指定的声音档案或服务不可用时保留失败原因。

## 配置开关

只有客户端声明 speech_units_v1 且角色 speech_output.enabled 为 true 时才渲染新提示。普通桌宠、QQ 与实时语音已声明；小屋 scene_presentation_v1 保持原协议。

在角色 character.json 添加以下块（通过现有角色安全路径读取）：

```json
{
  "speech_output": {
    "enabled": true,
    "language": "日语",
    "default_style": "calm",
    "styles": {
      "calm": {
        "profile_id": "cecilia_calm",
        "description": "平静、柔和"
      }
    }
  }
}
```

cecilia_calm 是既有 voiceProfiles 的档案 ID，参考音频路径和参考台词仍放在原声音配置里；不进入提示词。使用 GPT-SoVITS 时 textLang、promptLang 设为 all_ja，mediaType 为 wav。QQ 仍按既有 QQ TTS 用户配置范围读取声音档案。

本机已配置以下四种风格。calm 沿用用户认可的参考；另外三种根据台词语境初选，完成两句完整合成及 ASR 对照，情绪效果仍待用户试听评价。ASR 不用于证明情绪或声线质量。

| style | 模型看到的简短描述 | 原参考 | 声音档案 |
|---|---|---|---|
| calm | 平静柔和，日常交流和安慰 | ep12_017.wav | cecilia_calm |
| happy | 轻快期待，开心和分享好消息 | ep12_012.wav | cecilia_happy |
| shy | 害羞迟疑，亲近时有些不好意思 | ep01_023.wav | cecilia_shy |
| earnest | 认真温柔，解释和鼓励 | ep01_004.wav | cecilia_earnest |

角色的 speech_output.styles 配置这四个映射，default_style 仍为 calm。每次准备模型提示和解析 TTS 档案都会重新读取 character.json；原子替换配置后，下一轮可见，无需重启。已有回合的提示不追溯更新，变更宜在空闲时进行。声音档案也走既有配置读取。此配置暂不提供专用的工坊编辑界面。

切换风格只是切参考档案，同一 GPT-SoVITS 服务必须已加载适用的权重，style 不负责加载或切换权重。没有增加跨风格特征缓存或额外翻译模型请求。

关闭 speech_output.enabled 即恢复原提示协议。角色根 voice、旧小屋、点击台词等旧入口未自动迁移；不要直接把日语档案绑定给仍然传中文的旧入口。

## 验证记录

自动验证覆盖分块边界、Unicode、嵌套工具 JSON、不完整流、缺失配音、长 say 分句、style 边界、重复事件、最终补发、语音 artifact 恢复、QQ WAV 拼接及失败。

最终一次组合回归 214 项通过，提示构建另跑 32 项通过；git diff --check 通过。以下分项包含重复覆盖，不相加计数。

- 旧流式解析、新单元、VoiceCore 宿主/执行器与播放上下文：通过。
- QQ 投递与播放上下文：40 项通过。
- 客户端协议、提示构建、后端路由：146 项通过。
- 实时语音生产适配相关测试：通过；未进行真实麦克风打断验收。
- 前端 build、speech-delivery、speech-units、tts-presentation、voice-call、voice-interruption smoke：通过。修补原 TTS smoke 中缺少的场景播放环境桩，并增加双文本真实生产队列用例。
- 浏览器打开开发版页面，离线状态正常，未见控制台警告或错误；没有向真实群聊发送测试消息。
- 额外运行 test_tts_provider_selection 时，两个用例在 Windows 清理临时插件目录阶段因已加载 .pyd 文件被占用报错；没有将这套测试记为全通过，未修改无关清理逻辑。

真实模型试答通过 scripts/probe_speech_units.py 运行，直接读取现有 model_service.json，不创建 Bot 回合或写入聊天历史，报告不保存凭据。

本机 gemini-3.8-flash-high 两次试答：短回复一组含两句配音；长回复一组含八句配音。说明音频句数与 say 对象数量独立。首个配音事件分别约 6.59s、6.06s，完整模型响应约 6.71s、6.25s；这些时间包含模型首 token 等待，不能当作纯解析耗时。

最终 GPT15/SoVITS8 + calm 参考的十个句子实测：首次冷合成约 4.50s，其余约 0.65～1.43s，生成音频约 1.74～5.46s。热身后通常具备预取余量，但不能承诺冷启动或换参考完全无等待。

音频与报告保存在训练项目的 speech_units_integration/。长样本末句在固定 seed=42 时被 Whisper 校验发现漏读后半句；同文本换 seed=17/123/7 的复验均识别完整。这是合成采样的偶发问题，尚未以在线 ASR/重试掩盖，也不认为某个种子能保证所有文本不漏字。

## 四种语气与 QQ 上线验收

- 新增配置热加载回归；更新后相关组合 131 项通过。新增 QQ 双文本投递集成测试通过真实 TTS 插件工作进程，验证中文发文字、日语按 style 合成、最终仅一条完整 WAV，画面 emotion 不覆盖 style。
- 前端 build、speech-units 和 tts-presentation smoke 再次通过。
- 四组完整双句试音与原参考保存在训练项目 style_review_v1/；listen.html 可逐个对照，four_styles_full.wav 顺序为 calm、happy、shy、earnest。8 句 ASR 未发现整句漏读或重复；earnest 末句「あなたが」识别为「あなたの」，保留该差异，不宣称逐字准确。
- 真实 QQ 模式模型试答使用运行时角色配置，选择 happy、shy 两组，共 5 个配音句子；没有增加翻译请求。
- 后端 /tts 用 QQ 群会话身份实际请求四种 style，全部返回 200 / audio/wav，style 匹配、无 fallback。首次 4.39s，其余 1.12～1.26s，生成 3.62～4.18s 音频。测量是本机样本，不代表所有文本或并发情况。
- 本机 QQ / NapCat 在线；未向真实群聊发送测试消息，QQ 客户端最终收听由用户验收。

私有运行时保留角色和声音配置备份。声音服务使用 config/cecilia-voice/inference_config.yaml、serve.py 与 start.ps1，引用最终权重并保存独立参考副本，不覆盖上游默认权重配置；PID 与日志保存在既有 run/、logs/。服务退出后可用该 start.ps1 重新启动，未新增开机自启任务。

QQ 的参考档案继续放在主人声音配置范围。当前使用塞西莉亚的会话只增加 akane.tts.service.tts.v1.synthesize 的允许配置，解决原本首次合成需要确认的问题，未放开其它操作。新增会话如果仍要求确认，应通过现有权限配置单独授权 TTS。

运行配置、声音、权重、日志和带凭据的配置不进 Git；仓库提交实现、回归与部署说明。

用户实际私聊验收发现，旧的 QQ_VOICE_MAX_SEGMENTS=3 会把新协议 9 个日语句子静默截成 3 句（已发音频 4.82s），即使中文完整显示。已修复新协议投递入口：保留按标点逐句合成，全部完成后拼接；回归用一个 say 内含四句、下一组另有一句，验证上限配置仍为 3 时实际合成全部五句。QQ 投递套件 37 项通过。

主动搭话验收又发现尾部观察提示仍举旧的 `{"speech":""}` 示例，使模型回到旧正文格式；角色未配置旧 voice 时，旧正文会选默认 Edge TTS。现在观察提示只要求按当前格式发言或沉默，不再重复定义字段。共用 QQ 合成入口以角色 speech_output 为依据检查配音结构：启用双文本的角色如果缺少 utterances，返回 speech_units_required 并保留文字，不调用旧 TTS。未启用的角色保留原语音配置。

主动观察、QQ 投递与双文本回归覆盖了正常发言、静默、缺字段、纯语音转文字以及双发的语音失败状态；双发语音失败仍如实标为部分失败，不因文字已送达而制造语音成功。真实模型加上生产观察尾部提示后，接话样本输出 happy + 日语三句，静默样本输出 utterances:[]。

## 当前体验边界

普通桌宠的中文分句展示与日语播放是两条流，不保证跨语种逐句字幕同步。实时语音每个音频块携带所属组的中文，长组的字幕粒度仍较粗；不通过算法猜逐字翻译对齐。模型可以按完整意思分成适度长度的组来改善对应体验。

本次已切换运行中的塞西莉亚配置并启动常驻声音服务；未部署新的桌宠可执行文件。真实桌宠连贯播放、语音通话打断和三种新增参考的人工试听仍需验收。
