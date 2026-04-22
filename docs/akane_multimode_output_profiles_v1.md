# Akane 多模式输出与桌面感知架构 V1

本文档用于约束 Akane 从 Web 场景实验形态走向桌宠、Live2D 场景、静态场景等多客户端形态时的提示词、输出字段、记忆边界与后端归一化方式。

核心目标不是让所有客户端共用一份臃肿 JSON，而是让每个模式只挂载自己需要的提示词、上下文和输出字段，同时保证 Akane 的记忆、礼物、人格与小世界连续性不分裂。

---

## 1. 总原则

### 原则 1：模式影响表达，不影响 Akane 的连续性

`client_mode` 可以影响：

- 本轮 Prompt 结构
- 本轮输出字段
- 本轮可用工具
- 本轮上下文来源
- 前端渲染方式

`client_mode` 不应该影响：

- Akane 的核心身份
- 长期记忆归属
- 礼物与资源资产归属
- 人设卡归属
- 用户身份与会话身份

换句话说，桌宠模式、静态场景模式、Live2D 场景模式都是同一个 Akane 的不同身体，不是三套 Akane。

### 原则 2：LLM Output Profile 可以按模式变化

不要强迫模型在所有模式下都输出同一份大 JSON。

桌宠模式不应该白白输出 `scene.background`、`scene.bgm` 等场景字段。静态场景模式也不应该强塞桌面窗口、空闲时间、前台应用等桌宠感知字段。

每个模式拥有自己的 `Output Profile`：

- `desktop_pet`
- `scene_static`
- `scene_live2d`

模型只看当前模式挂载的输出格式。后端负责把不同 profile 归一化到内部运行状态。

### 原则 3：后端运行状态统一，模型输出不统一

模型输出可以不同，但后端内部应维护统一的 `RuntimeState`。

典型规则：

- 字段缺失：本轮不更新该状态，沿用旧值。
- 字段存在且有效：通过对应 Adapter 更新内部状态。
- 字段显式要求清空：必须使用受控动作或明确状态字段，不能把空字符串误判为清空。

这样可以避免桌宠模式不输出 `scene` 时把当前场景错误清空。

### 原则 4：当前模式由客户端显式声明

不要让后端靠用户消息猜当前模式。

客户端请求中应显式携带：

```json
{
  "client_mode": "desktop_pet",
  "client_capabilities": ["speech_segments", "live2d", "desktop_context", "touch_event"]
}
```

后端根据 `client_mode` 选择：

- Prompt Profile
- Output Profile
- Tool Set
- Context Blocks
- Output Adapter

模型可以建议切换模式，但实际切换应由用户、客户端 UI 或后端策略确认。

### 原则 5：客户端感知默认是瞬时上下文，不是长期记忆

桌面活动、窗口标题、截图视觉描述、传感器数据等客户端上下文默认只用于当前轮 Prompt 或短期缓存。

只有经用户确认、对话吸收、重复形成习惯，或通过记忆晋升门判定为高价值事件时，才写入 `MemoryStore`。

这条原则用于避免桌面活动污染长期记忆。

---

## 2. 模式定义

### 2.1 `desktop_pet`

桌宠模式是 Akane 从小世界中探出头，贴近用户现实电脑环境的形态。

主要感知对象：

- 用户当前电脑活动
- 前台应用类别
- 空闲时间
- 双击、摸头、拖拽等桌宠交互
- 文件拖拽
- 可选音乐状态

主要渲染对象：

- 透明悬浮窗
- Live2D 或轻量立绘
- 小气泡
- 迷你菜单
- 托盘状态

不需要默认输出：

- 完整 `scene.major`
- 完整 `scene.minor`
- 背景图 ID
- BGM ID

### 2.2 `scene_static`

静态场景模式是当前 Web galgame 形态的延伸。

主要感知对象：

- 当前场景
- 当前背景图
- 当前 BGM
- 当前静态立绘 outfit / emotion
- 礼物系统
- 相册、曲库、场景集合
- 视觉观察卡

主要渲染对象：

- 背景图
- 静态立绘
- BGM
- 对白框
- 功能面板
- 礼物托盘

### 2.3 `scene_live2d`

Live2D 场景模式是在场景舞台内用 Live2D 替代静态立绘。

主要感知对象：

- 当前场景
- 当前 BGM
- 当前 Live2D 模型
- 表情、动作、姿态、视线
- 礼物系统
- 视觉观察卡

主要渲染对象：

- 背景图
- Live2D 模型
- motion / expression
- BGM
- 对白框
- 功能面板

---

## 3. 请求结构建议

未来多端入口可以统一为 `ClientEvent`，但短期可以先在现有 `/think` 或新 `/client-event` 中逐步加入字段。

```json
{
  "profile_user_id": "master",
  "session_id": "main",
  "client_mode": "desktop_pet",
  "client_capabilities": [
    "speech_segments",
    "live2d",
    "desktop_context",
    "touch_event"
  ],
  "event": {
    "type": "chat_message",
    "text": "你看我现在在做什么"
  },
  "desktop_context": {
    "active_app": "VS Code",
    "activity_hint": "coding",
    "idle_seconds": 12,
    "privacy_level": "normal"
  },
  "current_visual": {},
  "client_state": {}
}
```

字段说明：

- `profile_user_id`：用户身份，决定长期资产和记忆归属。
- `session_id`：会话切片，决定当前连续对话上下文。
- `client_mode`：当前客户端模式，决定 Prompt 和 Output Profile。
- `client_capabilities`：客户端能力声明，决定哪些输出字段有意义。
- `event`：用户或客户端本轮触发的事件。
- `desktop_context`：桌面端瞬时感知信息。
- `current_visual`：场景端当前视觉状态。
- `client_state`：客户端自身状态，例如窗口位置、是否透明、当前 Live2D 模型等。

### 3.1 `client_capabilities` 的裁剪规则

`client_mode` 表示用户当前选择的模式，`client_capabilities` 表示当前客户端实际能承载什么。

后端应同时参考二者决定本轮 Prompt 和输出 schema。

基本规则：

- `client_mode` 决定默认 Profile。
- `client_capabilities` 决定 Profile 中哪些能力真实可用。
- 如果能力缺失，后端应裁剪相关 Prompt、资源清单和输出字段。
- 如果核心能力缺失，后端应回退到更低档 profile。

示例：

```json
{
  "client_mode": "scene_live2d",
  "client_capabilities": ["speech_segments", "background", "bgm"]
}
```

这种情况下，客户端声明自己处于 Live2D 场景模式，但没有 `live2d` 能力。后端不应要求 LLM 输出 `live2d` 对象，可回退到 `scene_static` profile，或输出 `scene_live2d` 的降级 schema。

建议能力枚举：

- `speech_segments`
- `background`
- `bgm`
- `static_sprite`
- `live2d`
- `desktop_context`
- `touch_event`
- `file_drop`
- `audio_playback`
- `tts`
- `choices`
- `tool_actions`

降级策略：

| 请求模式 | 缺失能力 | 推荐降级 |
| --- | --- | --- |
| `scene_live2d` | 无 `live2d` | 降级为 `scene_static` |
| `scene_static` | 无 `background` | 保留对话，省略场景切换 |
| `desktop_pet` | 无 `touch_event` | 保留桌宠气泡，不注入触摸事件 |
| 任意模式 | 无 `speech_segments` | 只要求输出 `speech` |

---

## 4. Prompt 组装方式

Prompt 不应再是一整块固定文本，而应模块化组装。

推荐模块：

- `core_identity_prompt`：Akane 的核心身份与输出底线。
- `memory_prompt`：检索到的长期记忆和近期上下文。
- `persona_prompt`：当前人设卡与可用表达侧面。
- `mode_context_prompt`：当前模式专属上下文。
- `resource_prompt`：当前模式需要的资源清单。
- `tool_prompt`：当前模式可用工具。
- `output_schema_prompt`：当前模式输出格式。

不同模式挂载不同模块。

| 模式 | 应挂载 | 不应默认挂载 |
| --- | --- | --- |
| `desktop_pet` | 桌面事件、桌面短期缓存、触摸状态、迷你动作表 | 全量场景资源、全量 BGM、复杂相册管理 |
| `scene_static` | 场景资源、静态立绘、BGM、礼物与资源投影 | 前台应用、窗口标题、截图感知 |
| `scene_live2d` | 场景资源、Live2D 模型、motion/expression、BGM | 纯桌宠物理拖拽状态 |

### 4.1 当前代码落点

V1 已经把 Prompt 组装拆出 `PromptProfile` 注册表：

```text
client_mode / client_capabilities
  -> ModeProfileRegistry
  -> ClientProtocolContext.effective_mode
  -> PromptProfileRegistry
  -> 本轮启用的 PromptModule 列表
```

当前 `scene_static` profile 保持全量模块开启，以保证 Web 端行为不变。

已定义的 PromptModule：

- `client_mode`：客户端模式说明。
- `extra_context`：工具 follow-up 或额外上下文。
- `current_visual_state`：当前场景 / 立绘 / BGM 基准状态。
- `scene_observation`：当前场景视觉观察卡。
- `outfit_observation`：当前服装视觉观察卡。
- `resource_manifest`：可用场景、BGM、服装资源清单。
- `pending_gifts`：手边待处理礼物。
- `focused_gift_observation`：当前聚焦礼物观察卡。
- `persona`：当前人设卡与可用表达侧面。
- `tools`：本轮可用工具说明。

未实现的模式暂时会在 `ModeProfileRegistry` 降级到 `scene_static`，因此不会影响当前 Web 端。未来启用桌宠或 QQ 文本时，只需要把对应 `ModeProfile.implemented` 打开，并完善对应 OutputAdapter / PromptProfile。

---

## 5. Output Profiles

### 5.1 Common Minimal Fields

所有模式都建议保留一组极小公共字段：

```json
{
  "speech": "",
  "speech_segments": [],
  "emotion": "normal",
  "status": "final",
  "memory_tags": "",
  "tool_call": null,
  "choices": [],
  "persona": {
    "active": ""
  }
}
```

说明：

- `speech_segments` 是展示层。
- `speech` 是兼容与记忆聚合层，由后端兜底生成。
- `emotion` 是跨模式的高层情绪，不直接等于某个客户端的具体动画文件。
- `tool_call` 仍然走统一工具运行时，但可见工具由模式决定。

### 5.1.1 `emotion` 与具体表现字段的关系

顶层 `emotion` 是跨模式的高层情绪标签，用于：

- 记忆与总结
- 语义检索
- 多端统一状态
- 客户端不支持细粒度表现时的兜底

具体表现字段用于当前模式的渲染：

- `pet.expression`
- `live2d.expression`
- `character.outfit` 下的静态表情资源

合并策略：

- 如果具体表现字段存在，Renderer 优先使用具体字段。
- 如果具体表现字段缺失，Renderer 使用顶层 `emotion` 兜底。
- Adapter 应尽量保持顶层 `emotion` 与具体表现字段语义相近。
- 允许轻微不同，但不应冲突。例如 `emotion=shy`、`pet.expression=look_away` 是合理的；`emotion=sad`、`pet.expression=smug` 通常应被纠正或降级。

### 5.2 `desktop_pet` Output Profile

桌宠模式应尽量轻量。

```json
{
  "speech": "",
  "speech_segments": [
    "主人，又在写代码呀？"
  ],
  "emotion": "curious",
  "pet": {
    "expression": "curious",
    "motion": "lean_in",
    "attention": "screen",
    "bubble_priority": "normal"
  },
  "status": "final",
  "memory_tags": "",
  "tool_call": null,
  "choices": [],
  "persona": {
    "active": ""
  }
}
```

字段说明：

- `pet.expression`：桌宠表情意图。
- `pet.motion`：桌宠动作意图，例如 `pat_head`、`lean_in`、`look_screen`、`idle`。
- `pet.attention`：注意力方向，例如 `user`、`screen`、`away`。
- `pet.bubble_priority`：气泡优先级，用于避免低价值气泡打扰用户。

`pet.expression` 与顶层 `emotion` 的推荐关系：

- `emotion` 表示 Akane 本轮情绪。
- `pet.expression` 表示桌宠前端具体表情或动画入口。
- 如果前端没有独立 `pet.expression` 系统，可以直接忽略它，只用 `emotion`。
- 如果两者都存在，`pet.expression` 应是 `emotion` 的具体化，而不是反向冲突。

### 5.3 `scene_static` Output Profile

静态场景模式可以沿用当前 Web 输出结构。

```json
{
  "speech": "",
  "speech_segments": [],
  "emotion": "shy",
  "character": {
    "outfit": "水手服"
  },
  "scene": {
    "major": "home",
    "minor": "bedroom",
    "background": "night_room",
    "bgm": "quiet_night"
  },
  "status": "final",
  "memory_tags": "",
  "tool_call": null,
  "choices": [],
  "persona": {
    "active": ""
  }
}
```

### 5.4 `scene_live2d` Output Profile

Live2D 场景模式不应强行复用静态立绘字段，而应明确表达 Live2D 意图。

```json
{
  "speech": "",
  "speech_segments": [],
  "emotion": "shy",
  "live2d": {
    "model": "akane_default",
    "expression": "shy",
    "motion": "look_away",
    "attention": "user"
  },
  "scene": {
    "background": "night_room",
    "bgm": "quiet_night"
  },
  "status": "final",
  "memory_tags": "",
  "tool_call": null,
  "choices": [],
  "persona": {
    "active": ""
  }
}
```

字段说明：

- `live2d.model`：当前模型或模型组。
- `live2d.expression`：Live2D 表情。
- `live2d.motion`：Live2D 动作意图。
- `live2d.attention`：视线或注意力方向。

`live2d.expression` 与顶层 `emotion` 的推荐关系：

- 如果 LLM 输出 `live2d.expression`，Adapter 可将其同步为本轮具体表现。
- 如果 LLM 只输出 `emotion`，Adapter 应用 `emotion` 兜底填充 `live2d.expression`。
- 如果二者冲突，优先保留顶层 `emotion` 的语义，并将 `live2d.expression` 降级到最接近的可用表达。
- 不要求现在就锁死所有 Live2D 字段；V1 只需要 `model`、`expression`、`motion`、`attention` 四个高层意图即可。

---

## 6. Output Adapter 与 RuntimeState

每个 Output Profile 都应通过 Adapter 合并进内部运行状态。

```text
DesktopPetOutputAdapter
SceneStaticOutputAdapter
SceneLive2DOutputAdapter
```

统一流程：

```text
LLM raw output
  -> profile-specific normalizer
  -> output adapter
  -> RuntimeState merge
  -> renderer payload
  -> MemoryStore write speech aggregate
```

`RuntimeState` 可以包含：

```json
{
  "last_scene": {
    "major": "home",
    "minor": "bedroom",
    "background_id": "night_room",
    "bgm_id": "quiet_night",
    "atmosphere_tags": ["夜晚", "安静"]
  },
  "last_character": {
    "outfit_id": "水手服",
    "expression_id": "normal",
    "sprite_id": "水手服/normal"
  },
  "last_live2d": {
    "model_id": "akane_default",
    "expression_id": "normal",
    "motion_id": "idle",
    "attention": "user"
  },
  "last_pet": {
    "expression_id": "normal",
    "motion_id": "idle",
    "attention": "user",
    "bubble_priority": "normal"
  },
  "last_client_mode": "desktop_pet",
  "last_emotion": "normal"
}
```

合并原则：

- `desktop_pet` 输出 `pet` 时，只更新 `last_pet`。
- `scene_static` 输出 `scene` 和 `character` 时，只更新场景与静态立绘状态。
- `scene_live2d` 输出 `live2d` 和 `scene` 时，只更新 Live2D 与场景状态。
- 某模式未输出的字段不覆盖旧状态。

---

## 7. 记忆与模式的关系

### 7.1 长期记忆共享

无论用户在什么模式下与 Akane 互动，都应写入同一个 `MemoryStore`。

否则会出现：

```text
桌宠里聊过的事，切到场景里不记得。
```

这会破坏 Akane 的连续性。

推荐记录 metadata：

```json
{
  "role": "assistant",
  "content": "主人刚才摸了摸我的头，我有点不好意思。",
  "metadata": {
    "client_mode": "desktop_pet",
    "event_type": "touch_head"
  }
}
```

检索默认跨模式，只有特殊分析或调试时才按 `client_mode` 过滤。

### 7.2 桌面感知默认不入长期记忆

桌面感知信息分三层处理：

```text
Desktop Context
  -> 本轮 Prompt 注入
  -> DesktopObservationBuffer
  -> MemoryPromotionGate
  -> MemoryStore
```

### 7.3 第一层：瞬时感知

只用于当前轮 Prompt，不写入长期记忆。

示例：

- 当前前台应用是 VS Code。
- 当前活动像是在写代码。
- 用户刚刚双击了桌宠。
- 当前空闲 12 秒。
- 当前正在播放音乐。

### 7.4 第二层：DesktopObservationBuffer

短期桌面观察缓存，用于保留近期连续性。

建议策略：

- TTL：1 到 6 小时。
- 数量：最近 10 到 30 条。
- 默认只在桌宠模式注入。
- 不进入长期 RAG 检索。
- 可用于回答“我刚才在干嘛”这类近期问题。

示例：

```json
{
  "event_type": "desktop_activity",
  "activity_hint": "coding",
  "active_app": "VS Code",
  "observed_at": 1710000000,
  "privacy_level": "normal",
  "memory_policy": "ephemeral"
}
```

### 7.4.1 `activity_hint` 推荐枚举

`activity_hint` 应尽量使用有限集合，避免把窗口标题原文直接喂给模型。

推荐 V1 枚举：

- `coding`
- `writing`
- `browsing`
- `gaming`
- `video`
- `music`
- `chatting`
- `reading`
- `designing`
- `idle`
- `private`
- `unknown`

前台窗口标题、进程名、应用名应先映射成 `activity_hint`，再进入 Prompt。

示例：

| 原始信号 | activity_hint | 可注入描述 |
| --- | --- | --- |
| VS Code / IDEA | `coding` | 主人似乎正在写代码 |
| Word / Markdown 编辑器 | `writing` | 主人似乎正在写文档 |
| Chrome / Edge 普通页面 | `browsing` | 主人正在浏览网页 |
| Steam / 游戏进程 | `gaming` | 主人正在玩游戏 |
| Bilibili / YouTube | `video` | 主人正在看视频 |
| 音乐播放器 | `music` | 主人正在听歌 |
| QQ / WeChat | `chatting` | 主人可能在聊天 |
| 隐私黑名单 | `private` | 隐私保护模式 |
| 无法判断 | `unknown` | 主人正在使用电脑 |

### 7.5 第三层：MemoryPromotionGate

只有通过晋升门的桌面感知，才写入长期记忆。

可晋升条件：

- 用户明确说“记住这个”。
- 用户和 Akane 围绕该事件聊了多轮。
- 该事件被 Akane 的回复吸收成共同经历。
- 该行为反复出现，形成稳定习惯。
- 系统判断该事件对长期陪伴很有价值。

不应晋升：

- 普通窗口切换流水账。
- 精确窗口标题。
- 敏感应用或隐私内容。
- OCR 原文。
- 截图原图。
- 文件完整路径。
- 账号、支付、聊天隐私等内容。

### 7.6 桌面感知存储策略表

| 输入类型 | 默认处理 | 可否晋升长期记忆 |
| --- | --- | --- |
| 用户普通聊天 | 写入 MemoryStore | 是 |
| 桌宠触摸事件 | 作为本轮事件进入对话 | 视对话价值而定 |
| 前台应用类别 | 注入本轮 Prompt / DesktopBuffer | 反复形成习惯时可摘要 |
| 精确窗口标题 | 默认不长期存 | 用户确认后才可摘要 |
| 截图视觉描述 | 短期 Observation Card | 用户确认后才可摘要 |
| OCR 文本 | 默认不存 | 原则上不自动存 |
| 隐私窗口 | 不注入或打码注入 | 不自动存 |
| 用户说“记住” | 写入 MemoryStore | 是 |

---

## 8. 隐私等级

桌面感知应带 `privacy_level`。

建议等级：

- `public`：低风险，例如天气、时间、普通应用类别。
- `normal`：普通工作状态，例如 VS Code、浏览器、播放器。
- `sensitive`：可能包含个人信息，例如聊天窗口、文档标题。
- `private`：支付、银行、密码、账号、无痕窗口等。

处理规则：

- `public`：可注入本轮 Prompt。
- `normal`：可注入本轮 Prompt，可进入短期缓存。
- `sensitive`：只注入高层描述，不保留精确标题。
- `private`：默认不注入，或只注入“隐私保护模式”。

---

## 9. 工具挂载规则

工具也应按模式挂载，避免 Token 膨胀和能力混乱。

### `desktop_pet` 推荐工具

- 查看短期桌面观察
- 响应触摸事件
- 处理拖拽文件
- 管理迷你气泡或提醒
- 可选音乐状态读取

### `scene_static` 推荐工具

- 管理礼物
- 管理相册集合
- 管理曲库
- 管理场景候选
- 切换静态立绘资源

### `scene_live2d` 推荐工具

- 切换 Live2D 模型
- 触发 Live2D motion
- 切换 Live2D expression
- 管理场景候选
- 管理礼物与资源

---

## 10. 实施路径

### Phase 1：文档与请求字段

- 固化本文档。
- 在请求中引入 `client_mode`。
- 当前 Web 默认 `scene_static`。
- 后端不做复杂推断。

### Phase 2：Prompt Profile Registry

新增模式注册表：

```text
ModeProfileRegistry
  desktop_pet
  scene_static
  scene_live2d
```

每个 profile 定义：

- Prompt 模块
- 输出 schema
- 可见工具
- 上下文来源
- Output Adapter

### Phase 3：Output Adapter

为不同模式建立 adapter。

短期可以先做：

- `SceneStaticOutputAdapter`：适配现有 Web 输出。
- `DesktopPetOutputAdapter`：为未来桌宠预留。

### Phase 4：Desktop Event 入口

新增或扩展 API：

```text
POST /client-event
```

支持事件：

- `chat_message`
- `desktop_touch`
- `desktop_activity`
- `file_drop`
- `idle_tick`

### Phase 5：DesktopObservationBuffer 与 MemoryPromotionGate

实现桌面感知短期缓存和记忆晋升门。

优先规则：

- 桌面活动默认只短期保存。
- 用户确认才长期保存。
- 敏感信息永不自动长期保存。

### Phase 6：Electron / Tauri 原型

先验证最小闭环：

- 透明窗口
- Live2D 或 PNG 渲染
- 小气泡
- 双击触摸事件
- 调用 Akane Core
- 根据 `emotion` / `pet.motion` 改变表现

---

## 11. 一句话结论

Akane 的多模式架构不应该追求“一套大 JSON 走天下”。

更稳的做法是：

```text
多套 LLM Output Profile
+ 单套 RuntimeState
+ 单套 MemoryStore
+ 单套 Gift / Persona / Tool Core
+ 多套 Renderer Adapter
```

这样既能省 Token，又能避免提示词互相污染；既能让桌宠拥有电脑环境感知，也不会把长期记忆污染成桌面监控日志。
