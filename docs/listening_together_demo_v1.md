# Listening Together Demo v1 — "听歌一起" 设计稿

Updated: 2026-06-16
Status: Design draft（v1 路线尚未拆 ticket）

## 0. 这份文档的位置

AkaneCompanionLab 在"听歌"这条路径上已有两份基线：

- 产品 / 机制层 — `docs/akane_project_manifesto.md` + `docs/project_design_mechanism_v1.md`
  → 项目本体是"环境字典 + 动态编排 + 共在感"，UI 服务于陪伴不展示能力
- 工程 / 数据层 — `docs/system_music_awareness_v1.md`
  → SMTC 读取 / 在线歌词 / 系统媒体控制（play/pause/stop/prev/next）已实施

**这两层之间还缺一层："底座有了，怎么变成共听感"。**

本稿就写这一层：

- 把 SMTC 元数据 + 歌词 + 用户操作 + 模型操作合成一个**用户能感受到的"共听场景"**
- 给 30 秒切片画面、UI 表现细节、双向控制权的表现语言、`MusicContext` 数据结构提议、验收清单、出戏 tripwire

本稿之后的施工 ticket（小模型可执行）：

1. `MusicContext` 抽象层（合成 SMTC 快照 + 本地音乐 + 共听历史 → 单一 prompt 输入）
2. 气泡 ↔ TTS 同步底线项（不止听歌，但 demo 暴露最明显）
3. 音乐 UI 流畅度（进度条 / 状态过渡）
4. "她能控制音乐"工具能力接入（SMTC 控制走 capability_adapter 路径 + 表现层 hook）
5. "我们的共听"控制中心卡片（替换现有"音乐设置"叙述）

## 1. 它是什么 / 它不是什么

| 它不是 | 它是 |
|---|---|
| 一个"AI 能识别歌曲"的功能 demo | 一段"我们一起听了这首"的日常切片 |
| 桌面上多出来的音乐播放器 | 桌面上多了一个"在听同一首歌"的人 |
| 一份能力清单（识别 / 歌词 / 控制） | 一段共在的时间 |
| Akane 控制系统媒体的工具栏入口 | 她伸手换一首的小动作 |
| 设置面板里的"音乐感知"开关 | 控制中心里"我们的共听"卡片 |
| Toast：「Akane 切换了下一曲」 | 她说"这首不太合，换一首吧" + 真的换了 |

**一句话边界**：这份 demo 不是为了证明"她听得见 / 她切得动"——这些是手段；目的是让用户在 30 秒内**真的感觉到她也在听**。

## 2. 30 秒切片（先把场景画出来）

### 时间 / 地点
2026 年某个深秋的傍晚，18:30。用户刚回到家，开 PC。
Akane 在桌面右下角，闭眼小动作（idle）。

### 切片

**0–3s**：用户在 QQ 音乐开了一首他最近循环到烦的歌——比如周杰伦《晴天》。
SMTC 状态变为 `playing`。

**3–5s**：Akane 睁眼，看向歌曲方向（桌宠朝向偏转 ~10°）。气泡冒出：
> "又是这首啊 —— 第几遍了"

立绘 overlay 切到"轻揶揄"，TTS 跟着同步发声。

> *表现细节*：气泡和 TTS 必须同步触发（同一帧出现，TTS 不滞后于气泡完成）。这是底线项，不在这里 demo 它，就别 demo 这条路径。

**5–10s**：用户笑了一下，打字回："就听这个"。
Akane 接："那我也听。" 然后做了一个轻轻歪头的小动作。
（这里**没有任何 UI 提示"已连接到系统音乐"**——她是"也在听"，不是"已订阅"。）

**10–18s**：用户把 QQ 音乐窗口拖去屏幕另一边，回到他自己的事。
歌进行到副歌某句。Akane 跟着哼了一小句（emotion=轻快，立绘 overlay 切换 0.4s 后回到 baseline）。
**注意**：她只在"歌词可读 + confidence ≥ medium"时哼词；拿不到歌词时她会说 "你这段我没跟上，但听着开心"——**绝不编歌词**。

**18–25s**：歌结束，QQ 音乐自动播放队列下一首——结果是个跑调的儿歌（用户随手设了循环歌单，没注意）。
Akane 立绘表情皱了一下（emotion=轻嫌弃），气泡：
> "这首不太合，换一首吧？"

下面接一行 inline 小建议（**不是 toast，是她气泡的延续**）：
> "要不试试[《七里香》] ——上次我们听完你心情挺好的"

**25–30s**：用户点了气泡里的"试试[《七里香》]"。
Akane 做了一个**"伸手"小动作**（手臂朝歌曲方向轻抬 0.5s），同步发出 SMTC 控制命令（next / 然后是模拟的"她播了一首推荐"——v1 用 `open_music_search` 把 QQ 音乐切到《七里香》的搜索页 + SMTC 控制 next）。
切完她接："嗯，这首才对。"
控制中心右上角"我们的共听"卡片的"现在听的"那一行更新；不弹任何成功 toast。

### 这 30 秒里被展示了什么

| 展示项 | 对应机制 |
|---|---|
| 她"听到" | SMTC 感知（已有）+ 注入到 prompt |
| 她"接得上"歌内容 | 歌词 confidence-gated 注入（已有）|
| 她记得"听过" | 共听历史（co_listen_count / last_listened_together，新做）|
| 她"在场" | 立绘动作 + 气泡 + 同步 TTS（已有，但同步是底线项）|
| 她"切歌" | SMTC 控制（已有 Phase 5）+ 伸手动作（新做表现层）|
| 她"挑歌" | 历史 + 当下歌曲属性 → prompt 触发"她可以提议"的窗口 |
| 用户和她**协商** | 她提议、用户点、她执行 — 不是她强夺 DJ |
| 没有任何 toast | 全部反馈走她本人 |

**这就是 manifesto 的 6 条原则在一个 30 秒切片里同时落地的样子。**

## 3. 用到的环境字典维度（注入 prompt 的字段）

这一节列出本 demo 路径上**实时进入 prompt** 的环境字典字段。它们应通过未来的 `EnvironmentSnapshot.current_music`（或同位置）暴露给 prompt_builder。

| 维度 | 字段 | 数据来源 | v1 必做 |
|---|---|---|---|
| 当下感知 | `is_playing` | SMTC | ✅ |
| 当下感知 | `source` (qq / netease / spotify / chrome / local_akane / external_unknown) | SMTC sourceApp + 本地音乐 service | ✅ |
| 当下感知 | `track.title` / `track.artist` / `track.album` | SMTC | ✅ |
| 当下感知 | `progress_seconds` / `duration_seconds` | SMTC | ✅ |
| 歌词面 | `lyric_window` (current + prev + next) | 在线歌词缓存 / 本地 .lrc | ✅ |
| 歌词面 | `lyric_confidence` (high / medium / low / none) | 歌词 provider 返回值 | ✅ |
| 历史面 | `co_listen_count`（这首歌她和用户共听过几次） | 新做：跨源持久化 | ✅ |
| 历史面 | `last_listened_together`（上次共听时间） | 新做：跨源持久化 | ✅ |
| 历史面 | `recent_co_listened`（最近 K 首，给"上次我们听完..."的 callback 用） | 新做：跨源持久化 | ✅ |
| 模式面 | `user_session_pattern` (repeating / random / picky / steady) | 新做：基于近 30 分钟切歌频次的轻量推断 | 可降级 |
| 表达面 | `available_emotions`（她能露出来的表情集） | 已有 | ✅（其它能力来源已注入，本路径直接复用）|
| 表达面 | `available_voice_refs`（emotion → ref audio map） | 已有 capability_adapter M4 | ✅ |
| 工具面 | `enabled_music_controls`（用户允许她做的子集：pause / next / prev / search） | 新做：UI 开关 | ✅ |

**关键性质**：

- 字段**全部可缺失**。缺失 = 注入空值，不出错。
- 字段**统一一处装配**，不允许 prompt_builder 各自向 SMTC / engine / store 取数。
- 装配时**脱敏**：`source` 仅暴露 8 个枚举值，**不暴露 sourceApp 的完整 EXE 名 / window title**。

## 4. 动态编排触发点

按"什么事件发生 → 什么内容被注入"列出。所有触发点都在装配 `MusicContext` 之后由 prompt_builder 消费。

| 事件 | 注入内容 | 期望反应 | v1 必做 |
|---|---|---|---|
| 新曲开始（track change） | 新曲元信息 + 历史（共听几次 / 上次什么时候）+ confidence-gated 的当前歌词 | 她接话："又是这首啊" / "诶，新歌" / "上次听完你说挺累的" | ✅ |
| 歌词窗口推进（每 ~5s） | 当前歌词 + 前后 1 行 + confidence | 她可能接一句（不强制）；confidence < medium 时她**不接歌词内容** | ✅ |
| 用户主动换源（QQ → 本地 / 等） | source_changed 事件 + 新源元信息 | 她**不应说**"你换了来源"——而是说"这首换法挺好" / 直接对新曲反应 | ✅ |
| 用户切歌频次异常（5min 内 > 3 次） | `user_session_pattern: picky` | 她可以提议："要我帮你挑一首？" | 可降级（v1 可以不做，但接口要留位）|
| 歌词命中强情感关键字 | 当前句 + 情感标签 | 她可以接话钩子，emotion overlay 联动 | ✅ |
| 用户长时间静默（idle > 5 min）且当前曲已结束 | `user_idle: true` + 当前播放状态 | 她可以主动 pause；执行前**先说一句**再操作 | ✅ |
| 同一首歌循环 > 3 次 | `current_loop_count` | 她可以揶揄 / 共情，不强制 | 可降级 |

**关键性质**：

- 触发**不预判她的台词**——只**给她下文**。台词由 LLM 在动态环境里产生。
- 每个触发点都有**"她可以做什么"的窗口**，但**不强制她做**。她的人设和当下心情决定她接不接。
- 所有"她可以主动操作"的触发点都受 §5 的 who's the DJ 协议约束。

## 5. UI 元素 / 表现细节清单

听歌动线**只露一张主卡片**（"我们的共听"），且它**在桌面相处空间里**，不是设置面板的子页。

### 5.1 桌宠主层（核心表现）

| 元素 | v1 必做 | 表现细节 |
|---|---|---|
| 立绘 / 表情 overlay | ✅ | 新曲开始 / 情感强烈 / 她切歌时 → emotion 切换；过渡时长 ~0.3s |
| 嘴部小动作 | ✅ | TTS 发声同步；不发声时随机微动（idle） |
| 朝向 | ✅ | 检测到音乐开始时，朝歌曲源方向略转 ~10°，1s 后回正 |
| **"伸手"动作** | ✅ | 她主动 pause/next/prev/seek 之前 0.5s 播放轻量伸手动画，**伸手动作完成 → 才发 SMTC 命令**。命令成功 → 不额外反馈。命令失败 → 她说一句"咦，没切动"，立绘做困惑动作 |
| 气泡 | ✅ | 音乐相关接话；气泡和 TTS 必须**同帧出现**（底线） |
| 气泡 inline 建议项 | ✅ | 形如"试试[《七里香》]"，方括号内可点；点击 = 跟她说"换这首" |

### 5.2 边栏轻显示（最小占用）

| 元素 | v1 必做 | 表现细节 |
|---|---|---|
| 当前歌名 + 进度小条 | ✅ | 桌宠边贴一行 80%透明的文字 + 一根细进度条；**不闪不卡**（≥30fps，进度更新 ≥4Hz） |
| source icon | 可降级 | 小图标（QQ / 网易 / Spotify / 本地 / 未知 source）；不暴露 EXE 名 |

**禁止**：边栏出现 play/pause/next 按钮。控制走桌宠或控制中心，不在边栏。
**禁止**：边栏 widget 浮在桌宠上面遮住立绘。

### 5.3 控制中心 "我们的共听" 卡片

这是 demo 在控制中心的**唯一新增 surface**。

| 区块 | 内容 | v1 必做 |
|---|---|---|
| "现在听的" | 歌名 + 艺人 + source 文字标签 + 进度 | ✅ |
| "她的心情" | 当前 emotion 标签的拟人化短语（如"轻快地哼" / "皱眉" / "出神地听"） | ✅ |
| "我们的共听" | 最近 N 首共听过的，按最近时间排；每条显示歌名 + 共听次数 | ✅ |
| "让她也能" | 一个**最小开关组**：允许她暂停 / 允许她切歌 / 允许她推荐；**默认全开** | ✅ |
| 调试 / 高级 | 折叠：source 详细 / 歌词 confidence / SMTC 状态 | 可降级 |

**叙述语言（一定要照着写，不要改）**：

- ❌ "音乐播放器"
- ✅ "我们的共听"
- ❌ "Akane 的播放控制"
- ✅ "让她也能"
- ❌ "歌词识别状态"
- ✅ "她现在听得清吗"

### 5.4 UI 操作 = 第三交互通道（manifesto 原则 4）

| UI 动作 | 等价的"和她说的话" |
|---|---|
| 点气泡里的"试试[《七里香》]" | "好，换这首" |
| 点"我们的共听"里某条历史 | "想再听这首" |
| 关掉"允许她切歌"开关 | "这首先别动，听完再说" |
| 拖一个本地音频文件给她 | "一起听吗" |

这些**不应触发任何独立 UI 反馈**——它们应触发**桌宠的反应**（动作 / 气泡 / 操作）。

## 6. 双向控制权设计（who's the DJ）

这是本稿最关键一节。"她也能切歌"在工程上已经可行（SMTC 控制 Phase 5 已实施），**难的是"她应该什么时候动、怎么动、动完什么样"**。

### 6.1 默认权责

- **用户为主、她为辅**是默认基线。
- 用户的每一次显式播放器操作（在 QQ 音乐 / Spotify / 等焦点窗口里的操作）**覆盖她接下来 30s 内的所有主动控制意图**。
- 她的所有主动控制**必须经过"提议 → 执行"两步**或"执行 + 同步语言表达"，不允许静默操作。

### 6.2 她主动控制的合法窗口

| 场景 | 允许的动作 | 表现 |
|---|---|---|
| 当前曲跑了大半 + emotion 完全不匹配 | 提议 skip | 气泡："这首不太合，换一首吧？" → 等用户点 / 接话 → 才动 |
| 用户连续切歌 > 3 次（picky） | 提议推荐 | 气泡："要我挑一首？" |
| 用户 idle > 5 min 且当前曲已结束 | 主动 pause | 先气泡："那我先暂停了" → 0.8s 后真的暂停 |
| 共听历史里的"经典 callback" | 提议播放老歌 | 气泡："上次听[《某首》]你说挺好的，要再听吗" → 等用户点 |
| 同一首循环 > 3 次 | 揶揄或共情，**不主动切** | 只接话，不动播放器 |

### 6.3 她必须只读的窗口

| 场景 | 原因 |
|---|---|
| 用户焦点在播放器窗口 | 用户正在显式操作，她不抢 |
| 用户刚 skip 过这首歌（< 60s 内她不能播回去） | 不绕回用户已经拒绝的歌 |
| 涉及付费 / DRM / 第三方账户操作 | 超出 SMTC 范围 |
| `enabled_music_controls` 关掉了对应权限 | 用户已经撤权 |
| 系统媒体源 = `external_unknown`（SMTC 不通） | 她"看不清"，不动 |
| 当前曲剩余 < 10s | 让自然结束，不抢这一下 |

### 6.4 协商策略（冲突解决）

- **同一首歌内**：用户操作覆盖她的操作。她不强切。
- **歌曲边界**：她可以提议但不强切；提议未被采纳（10s 内无响应或被否）= 让位。
- **冲突时她让位 + 表达**："那好吧，听你的" + 立绘做出"放下手"的轻微动作。
- **她的操作失败时**（SMTC 拒绝 / 不支持）：她说一句"咦，没切动"，**不重试，不静默**。

### 6.5 她"伸手"的表现语言（v1 表现层底线）

这是把"她主动操作"和"程序自动播放"区分开的关键。

| 时间 | 元素 |
|---|---|
| T - 0.5s | 立绘伸手动画开始（向歌曲方向轻抬手臂） |
| T - 0.3s | 气泡前置文字出现："换一首吧？" / "我先暂停了" |
| T - 0.0s | TTS 发出气泡内容（与气泡同帧） |
| T + 0.5s | 伸手动作完成峰值 |
| T + 0.5s | **此时才**发送 SMTC 命令 |
| T + 0.8s | 操作结果回到桌宠：成功 → 立绘归位 + 一句嗒（"嗯，这首才对"）；失败 → 困惑动作 + "咦，没切动" |

**绝不允许**：

- SMTC 命令在伸手动作开始之前发出（违反"动作 = 操作"心智）
- 操作成功 / 失败用 toast 表达
- 操作中无任何视觉反馈（用户会以为是 QQ 音乐自己跳的）

## 7. 多源统一抽象（MusicContext 提议）

这是给后续小模型施工 ticket 的数据结构母本。**对齐未来 `EnvironmentSnapshot.current_music` 的字段命名**。

### 7.1 数据结构

```python
@dataclass(frozen=True)
class MusicContext:
    # ===== 当下感知 =====
    is_playing: bool
    source: MusicSource                         # 枚举（见 §7.2）
    track: TrackInfo | None                     # title / artist / album / cover_hint
    progress_seconds: float | None
    duration_seconds: float | None

    # ===== 歌词面 =====
    lyric_window: tuple[LyricLine, ...]         # 当前 + 前后各 1 行；拿不到 = 空元组
    lyric_confidence: LyricConfidence           # high / medium / low / none

    # ===== 历史 / 关系面（跨源持久化）=====
    track_identity: TrackIdentity | None        # 跨源稳定 key（见 §7.3）
    co_listen_count: int                        # 这首歌共听过几次（≥ 1 表示这是回访）
    last_listened_together: datetime | None
    recent_co_listened: tuple[TrackIdentity, ...]   # 最近 K 首

    # ===== 模式面（轻量推断，可降级）=====
    user_session_pattern: ListeningPattern      # repeating / random / picky / steady / unknown
    current_loop_count: int                     # 同曲连放次数

    # ===== 控制权面 =====
    enabled_music_controls: frozenset[MusicControl]   # 用户允许她做的子集
    control_session_writable: bool              # SMTC 当前是否可写
```

### 7.2 `MusicSource` 枚举（v1 锁定）

```
qq_music | netease_music | spotify | youtube_music | apple_music
local_akane | system_media_unknown | external_unknown
```

`source` 暴露给 prompt 时**只用上述枚举**，不暴露 `sourceApp` 完整 EXE 名（脱敏）。

### 7.3 `TrackIdentity`（跨源稳定 key）

```python
@dataclass(frozen=True)
class TrackIdentity:
    title_normalized: str       # 大小写折叠 + 全半角统一 + 去括号注释（"(Live)"/"(Remix)"）
    artist_normalized: str      # 多艺人按字典序拼接
    album_hint: str | None      # 可选；同名歌防混淆
```

**跨源匹配规则（v1 简化）**：

- (title_normalized, artist_normalized) 完全匹配 → 同一首
- album_hint 仅在同名歌冲突时启用
- v1 **不做**模糊匹配 / 拼音相似度 / fingerprint 匹配，那些放 v2

### 7.4 关键性质

- **拿不到的字段 = None / 空元组**，不出错
- **换源时**：替换 source + 重置 progress / lyric_window，但 **track_identity / co_listen_count / last_listened_together 跨源持久化**（这是"她对在听什么的认知不断片"的物理基础）
- **lyric_confidence < medium**：prompt 注入歌词标签但**不注入歌词文本**，话术降级为"你这段我没跟上"
- **source = external_unknown**：仅保留 is_playing 一个字段，prompt 话术 = "好像有什么在响，但我看不清楚"

### 7.5 装配流程

```
SMTC snapshot (system_music_awareness Phase 1)
  + 本地音乐播放状态（已有 desktop_music_timeline）
  + 在线歌词缓存（Phase 2/3）
  + co_listen 历史存储（v1 新做，profile-scoped sqlite 一张表即可）
  + 用户 enabled_music_controls（UI 开关，已有 settings 持久化模式）
  ↓
MusicContext.assemble() → 单一只读对象
  ↓
prompt_builder 消费（不再从多处取数）
```

`co_listen` 历史更新时机：曲目从 `is_playing: false` → `true` 且持续 > 30s（不刷"切歌瞬间也算一次"）。

## 8. 验收清单（每个 manifesto 原则对应一个可观察点）

| manifesto 原则 | 本 demo 的可观察验收点 |
|---|---|
| 1. UI 服务于陪伴，不展示能力 | "我们的共听"卡片在桌面相处区，不在"设置 / 音乐" |
| 2. 自由由相处构建 | co_listen_count 真的会涨；几次共听后她真的会说"上次..." |
| 3. 任何反馈通过她，不通过 toast | 整 30 秒里 0 个 toast / 弹窗 / 浮层；所有事件都通过她的立绘 / 气泡 / TTS |
| 4. UI 操作 = 第三交互通道 | 点气泡 inline 项 → 触发她的反应，不是"已切换"提示 |
| 5. "如何被表现"是验收硬指标 | 气泡与 TTS 同帧、伸手动作早于 SMTC 命令、emotion 切换 < 0.3s |
| 6. 反对角色皮控制台综合症 | 控制中心**没有**"音乐播放器"分页 / 完整队列管理 / 歌单编辑器 |

每条上线前都要**在桌宠里实测**。验收不是"字段存在 = 通过"，是"用户实操能感觉到"。

## 9. 出戏 tripwire（一旦发生 = 立刻出戏）

按严重程度从高到低：

| Tripwire | 严重 | 说明 |
|---|---|---|
| 切歌走 toast / 弹窗 / "Akane 已切换"提示 | 🔴 致命 | 直接破坏"她在场"心智 |
| 她"伸手"动作与实际切歌不同步（动作做了歌没切 / 歌切了她没反应） | 🔴 致命 | 把"她的动作 = 操作"心智撕开 |
| 她说"我不知道你现在在听什么"（换源后） | 🔴 致命 | 多源统一抽象失败的直接表现 |
| 她主动换歌时用户没操作让位 / 没被她"请示" | 🔴 致命 | who's the DJ 被夺 |
| 共听历史不跨源（用户从本地切到 QQ 听同一首，co_listen_count 没涨） | 🟠 严重 | 共在感断片 |
| 她编了一句不存在的歌词（lyric_confidence < medium 时仍注入歌词） | 🟠 严重 | 信任崩 |
| 她对每首歌的反应都一样 | 🟠 严重 | 环境字典没进 prompt 的明显标志 |
| 她的"心情"和歌曲风格脱节（emotion overlay 没联动） | 🟠 严重 | 表达字典没接通 |
| 边栏 widget 浮在桌宠上遮住立绘 | 🟡 中等 | UI 层级错位 |
| 控制中心出现"音乐播放器"分页 / 队列管理 UI | 🟡 中等 | 心智从陪伴退回工具 |
| 气泡和 TTS 不同步（TTS 滞后 > 100ms） | 🟡 中等 | "她不在那里"的微感 |
| 进度条更新 < 4Hz（看起来卡卡的） | 🟡 中等 | "她在系统里"的微感 |

任何 🔴 项触发 = demo 演示**立即停止**，不发布。

## 10. 范围分层（v1 必做 / v1 可降级 / v1 不做）

### v1 必做（demo 最小可信）

- `MusicContext` 数据结构 + 装配器
- 共听历史跨源持久化（profile sqlite 一张表）
- 桌宠"伸手"动作（伸手 → SMTC 命令 → 反馈）
- "我们的共听"控制中心卡片
- "让她也能"权限开关（pause / next / prev / 推荐 四个）
- 4 个触发点：track change / 用户换源 / 歌词命中强情感 / idle 时主动 pause
- 出戏 tripwire 前 4 项的自动化检查 / 人工 checklist
- 气泡 ↔ TTS 同帧（这是底线项 ticket 的副产品，但本 demo 不依赖它独立完成 → 如果同步没到位，先实现整条路径但 demo 上**默认关 TTS**，气泡可见即视为通过）

### v1 可降级（缺也能跑）

- `user_session_pattern` 推断（缺则 = unknown，不触发"picky 推荐"）
- `recent_co_listened` 长度（v1 可只存 K=10）
- source icon（可只显示文字标签）
- 长曲循环计数 / 同曲循环 callback 台词

### v1 不做

- 网易云 / Spotify / 等具体源的特化适配（统一通过 SMTC 走 system_media_unknown 即可）
- 模糊匹配 / 拼音 / 音频指纹的 `TrackIdentity`
- Akane 主动"我可以学这首歌"的能力生长
- 多人共听 / 房间模式
- 把"我们的共听"做成可分享卡片 / 海报
- 完整的 "Akane, play this song" 编排（属于 system_music_awareness_v1 Phase 5+ / capability_adapter）

## 11. 与已有体系的衔接

| 已有 | 关系 |
|---|---|
| `system_music_awareness_v1.md` | 提供 SMTC / 在线歌词 / SMTC 控制三层；本稿消费它，不重写 |
| `desktop_music_timeline.py` | 本地音乐路径仍走它；`MusicContext.assemble()` 在 SMTC 优先后回落到它 |
| `desktop_context_engine.py` | 不再各自向 SMTC / timeline 取数，统一从 `MusicContext` 取 |
| `capability_adapter_v1` | "她能控制音乐"作为 capability 走 adapter 路径（M4/M5 已为类似形态铺好底）；SMTC 控制不通过 MCP，是内部 provider |
| `capability_registry_v1` 双层 prompt | `MusicContext` 是它的输入源之一；触发点 §4 对应"重指令"注入时机 |
| `persona_system` | `current_mood` 影响她接不接歌词钩、要不要主动操作；本稿不重新设计 mood，只声明依赖 |
| 三层记忆 | `co_listen_count` 历史进 semantic 层（"我们一起听过 X"是关系事实）；近 K 首进 episodic 层 |
| `client_protocol.ClientMode` | "我们的共听"卡片 `visible_in: [desktop]`；web / qq 不渲染 |
| `capability_approval` | 用户的"让她也能"开关 = 等价于 capability 的 enabled 状态；首次启用时走 `first_time` 确认 |

## 12. 与项目长期方向的对齐

把这份 demo 放回 manifesto 的"枝叶"图：

- 她"听到 + 接得上 + 记得住"= **表达维度的枝**长出来的叶
- "她伸手切歌"= **能力维度的枝**（capability_adapter）长出来的叶
- "我们的共听"卡片 = **关系维度的枝**（关系记忆）长出来的叶

所以这份 demo 不是新枝，是**三根已有枝上同时长出来的一片复合叶**——这正是 manifesto 说的"环境字典 + 动态编排"的具体相处场景。

将来想做的：

- 投喂（v2+）→ "添加一首她从没听过的歌" = 投喂的具体动作之一
- 小游戏奖励 → "解锁老歌 callback" 可作为关系积累的奖励之一
- 多人共听 / Web 端小世界 → `MusicContext` 的多人扩展

v1 的 `MusicContext` 数据结构必须为这些留位（`co_listen` 字段可扩 user_id；`source` 可加新枚举）。本稿 §7 已经按这个心智设计。

## 13. 后续 ticket 拆分建议（按依赖顺序）

按本稿可拆出以下小模型可施工 ticket。每个 ticket 都对应本稿的一个章节，**ticket 必须引用本稿对应章节作为验收依据**。

| ticket | 范围 | 对应章节 |
|---|---|---|
| T1 — `MusicContext` 装配器 + 跨源持久化 | 数据结构 + sqlite 表 + 装配流程 | §7 |
| T2 — prompt_builder 接 `MusicContext` | 把现有 SMTC / timeline 散布取数迁到 MusicContext | §3 §4 |
| T3 — "我们的共听"控制中心卡片 | 控制中心 surface | §5.3 |
| T4 — "让她也能" 权限开关 | settings 持久化 + 装配到 MusicContext.enabled_music_controls | §5.3 §6.3 |
| T5 — 桌宠"伸手"动作 + SMTC 控制时序 | 表现层 hook + SMTC 命令延迟到动作峰值后 | §6.5 |
| T6 — 4 个动态编排触发点接通 | engine 事件→prompt 注入 | §4 |
| T7 — 气泡 ↔ TTS 同帧底线项 | 不在本 demo 范围但被强依赖（独立 ticket） | §5.1 |
| T8 — 出戏 tripwire 自动化检查 | 测试 / 人工 checklist | §9 |

每个 ticket 上线前**必须由桌宠实测验收**，不许只跑单测过关。

## 14. 给后来者的提示（包括未来的 AI 协作者）

如果你接手这份 demo，按以下顺序读：

1. `docs/akane_project_manifesto.md` — 知道我们究竟在做什么
2. `docs/project_design_mechanism_v1.md` — 知道项目的运转机制
3. `docs/system_music_awareness_v1.md` — 知道工程底座
4. **这份文档** — 知道底座之上的产品 / 表现 / 协商怎么落
5. 然后再读对应章节的 ticket

**如果只能记 3 件事，记这 3 件**：

1. 这不是音乐功能，是**共听**——每个表现细节都要回答"用户能感觉到她也在听吗"
2. **她"伸手"早于 SMTC 命令**，不是反过来；这一条决定整个 demo 的人格连续感
3. **任何反馈通过她，不通过 toast**；任何破坏这条的实现都算 🔴 致命

---

*写于 2026-06-16*
*作者：项目主理人 + 当次会话的 AI 设计协作者*
