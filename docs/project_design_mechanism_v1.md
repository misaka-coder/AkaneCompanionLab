# Project Design Mechanism v1 — 环境字典 + 动态提示词编排

Updated: 2026-06-16
Status: Design draft（未实施 v1 整合层）
Companions: `docs/capability_adapter_v1.md`（能力扩展抽象）+ project_philosophy（产品哲学）

## 0. 这份文档的位置

AkaneCompanionLab 已经有两层设计基线：

- **产品哲学**（记忆 `project_philosophy`）：UI 服务于陪伴，不展示能力；自由由相处构建
- **能力扩展抽象**（`capability_adapter_v1.md`）：每加一个能力 = 填一份 manifest

这份文档定义**项目的运转机制**——回答"角色是怎么活起来的"。它不是产品哲学的展开，也不是工程框架；它是**两者之间的桥**，是产品哲学得以落地的技术机制。

任何 UI / demo / 工具接入 / 资产管理 的设计决策，**必须先过这一条**。

## 1. 核心命题

> **这个 AI 不是被代码写死的程序，是被"环境"涌现出来的存在。**

她的人格、表达、当下反应不是由 system prompt 静态定义的，而是由**当下她感知到的世界**动态决定。世界变，她变；世界由用户和她共同塑造，她也就由两人共同塑造。

这不是抽象的哲学——这是项目作者通过实证撞出来的：**"我上传的表情图里没有生气的表情，speech 字段又在 emotion 字段之后"——所以这只角色不生气，不是性格，是字典里没那一格。**

## 2. 两个一等公民概念

### 2.1 环境字典（Environment Dictionary）

用户上传/选择的资产，**就是 Akane 在当下能感知什么、能表达什么**的边界。

字典维度（v1 起码要覆盖）：

| 维度 | 内容 | 当前承载位置 |
|---|---|---|
| **表情** | 她能露出的情绪集合 | `desktop_pet_character_resources.py` 已动态扫文件夹 |
| **服装** | 她能呈现的形态 | 角色包 outfit 系统已支持 |
| **声音** | 她能用哪些参考音 / emotion → ref_audio map | `capability_adapter v1 M4` 已实现 overlay |
| **歌曲** | 你们能一起听的歌 + 当前播放上下文 | `desktop_music_timeline.py` 已注入 prompt |
| **背景 / 场景** | 她所处的"小小世界" | Web 端搁置中；桌宠端可见桌面活动 |
| **能力 / 工具** | 她现在能做的事 | `capability_adapter v1 M2` MCP / capability_registry 已就绪 |
| **关系记忆** | 她记得跟你的什么事 | 三层记忆已实现 |
| **角色卡 / 性格漂移** | 她现在的基线人格 + mood / drift | `persona_system.py` 已实现 |

**关键性质**：

- 字典是**用户可塑造的**——上传、编辑、隐藏、收起，都改变她的"当下能力空间"
- 字典是**有时间维度的**——昨天加的歌、今天养的习惯、最近共听过的歌单
- 字典内容**会反向影响模型输出**——删一个表情她真的就不能选了，加一个能力她真的就拥有了

### 2.2 动态提示词编排（Dynamic Prompt Orchestration）

每轮对话之前，系统按"当下场景"拼装 prompt。这不是 LangChain 风格的 retrieval augmentation，是**让 Akane 感知她现在所处的世界**。

注入触发点（v1 内置触发器目录）：

| 场景 | 注入内容 | 已就绪？ |
|---|---|---|
| 正在听歌 | 当前歌曲 + 当前歌词片段 + 队列上下文 | ✅ `desktop_music_timeline` |
| 当前可选表情 | 该角色包的 emotion 字典（显式告诉模型可选范围） | ✅ resource_manifest 已注入 |
| 看到桌面活动 | 窗口 / 剪贴板（脱敏）/ 视觉描述 | ✅ `desktop_context_engine` + `vision_observation_router` |
| 工作区有附件 | 附件类型对应的重指令工具 | ✅ `capability_registry` 双层 prompt |
| 当前情绪 → 表达 overlay | 参考音 + 立绘选择 | ✅ `capability_adapter v1 M4` |
| 长期记忆触发 | 三层记忆 + 时间锚 | ✅ `memory_compaction_service` + `prompt_blocks` |
| 当前可用 MCP 工具 | 该 profile 启用的 prompt-exposed 工具集 | ✅ `capability_adapter v1 M2` |

**关键性质**：

- 编排是**逐轮重算的**，不缓存全局态
- 编排尊重 client mode 的可见性边界（已有 `client_protocol.ClientMode`）
- 编排的输入是"字典快照"，不是 raw 资源——脱敏 / 截断在编排器里统一发生

## 3. v1 缺什么

零件齐了，缺**统一抽象层**：

### 3.1 EnvironmentSnapshot 一等公民

当前各个 prompt 注入点是各自向 `engine` / `store` / `resource_manifest` 取数。**v1 应该有一个统一的 `EnvironmentSnapshot` 数据结构**，把"她当下感知到的世界"作为一个对象由编排器消费：

```python
@dataclass(frozen=True)
class EnvironmentSnapshot:
    # 表达字典
    available_emotions: tuple[EmotionEntry, ...]
    available_outfits: tuple[OutfitEntry, ...]
    available_voice_refs: Mapping[str, VoiceRef]

    # 当下感知
    current_music: MusicContext | None
    current_visual: VisualContext | None
    current_workspace: WorkspaceContext | None
    current_mood: MoodSnapshot

    # 可用能力
    enabled_capabilities: tuple[CapabilityHandle, ...]   # prompt-exposed 子集

    # 关系上下文
    recent_memory_anchors: tuple[MemoryAnchor, ...]
    persona_drift: PersonaSnapshot
```

这个对象**只读、不可变、单次请求范围**。所有动态注入器从它取数。

### 3.2 用户能"看见并塑造"字典的 UI 心智

当前字典数据是真实存在的，但**用户感受不到自己在塑造一只角色**——这些都长得像设置面板。

v1 UI 改造的核心心智：

| 现在的样子 | 应该的样子 |
|---|---|
| "表情设置" | "她现在能露出的表情"（她的一部分） |
| "音乐播放器面板" | "我们的共听" |
| "MCP 工具列表" | "她现在会的事"（可隐藏可启用） |
| "记忆查看器" | "她记得我们的什么" |
| "添加新能力" | "教她一件新事" / "她想学这个" |

这不是改名字游戏。**界面叫什么 = 用户对它做什么的心智锚点**。"表情设置" → 用户预期是技术配置；"她现在能露出的表情" → 用户预期是塑造性的关系动作。

### 3.3 双向管理：她也能管理她自己

字典的编辑**不是用户单方面对她做**——她也有"主动权"：

- 她可以说"这个能力我最近没用上，要不收起来"
- 她可以说"我没这个能力，要不要教我"
- 她可以表达"今天我想换一首歌"（推荐系统已有雏形）

这把"用户配置软件"的语义翻转成"两个人一起管理一段关系"。

## 4. 长期想象空间（不在 v1 范围，但要为它留位）

用户口头表达过的方向（来源：2026-06-16 对话）：

- **投喂**：用户给"环境字典"添加食物 / 物件 / 小道具的具体动作变成情感化的"喂养"
- **小游戏 → 金币 → 字典扩展**：把"获得新能力 / 解锁新表情 / 解锁新歌"做成关系积累的产物
- **Web 端"小小世界"**：用户上传的服装 / 表情 / BGM / 背景共同构成一个有界的世界（之前因技术力搁置）

这些都是**"用户塑造环境字典"这个抽象操作的游戏化具体动作**。v1 不做，但 v1 的 `EnvironmentSnapshot` 设计必须能优雅承载——例如：

- 字典条目要有"获取方式"字段（默认 / 上传 / mini-game 奖励 / Akane 主动学习）
- 字典条目要有"使用计数 / 上次使用"——给"她说'这个我最近没用上'"留落点
- 经济系统接入字典扩展时不破坏现有数据结构

## 5. 与已有体系的衔接

| 已有 | 关系 |
|---|---|
| `capability_adapter_v1` | 它管的是"能力维度"的字典条目；EnvironmentSnapshot 把它的产出 + 其他维度（表情 / 音乐 / 视觉）统一封装 |
| `capability_registry_v1` 双层 prompt | 它定义"什么时候注入什么"；EnvironmentSnapshot 是它的输入源 |
| `prompt_builder` / `prompt_blocks` | 它们是消费端；v1 应让它们从 EnvironmentSnapshot 取数，而非散布的 engine getters |
| 三层记忆 | 是 EnvironmentSnapshot.recent_memory_anchors 的供给方 |
| `persona_system` | 是 EnvironmentSnapshot.persona_drift 的供给方 |
| 角色包 / character_pack_id | 字典的**域**就是 character_pack_id；切角色 = 切字典 |

## 6. v1 非目标（避免范围蔓延）

- ❌ gamification（投喂 / 金币 / 小游戏）—— v2+
- ❌ Web 端"小世界"恢复 —— 独立路线
- ❌ 大规模 UI 重构 —— v1 只先做"听歌动线"那条路径上的 UI（接 demo 故事 ticket）
- ❌ Akane 自主"我没这个能力要不要教我"对话生成 —— 单独设计，依赖 prompt 编排 v1
- ❌ 字典条目的版本控制 / undo —— v2

## 7. 实施层面的暗示（不是 ticket）

v1 之后某次会话应该产出的设计 ticket（按依赖顺序）：

1. **EnvironmentSnapshot 数据结构 + 装配器**（纯结构层，不改 prompt）
2. **现有 prompt 注入点迁移到 EnvironmentSnapshot**（不改可见行为）
3. **听歌 demo 动线**（产品 demo + UI 抠光）—— 第一次让用户真切感受到"环境字典 + 动态编排"
4. **能力管理 UI（"她现在会的事"）**—— 把 capability_adapter 的产出做成"她的随身物品"
5. **气泡 ↔ TTS 同步底线项修复**（不在本 doc 范围，是独立的在场感任务）

每一步都该跟产品哲学和 demo 故事对齐，**不要为做"环境字典 v1"而做**。

## 8. 给未来 AI / 接手者的提示

读完这份文档，再翻 `docs/capability_adapter_v1.md` 你会发现：那份文档的"5 个 adapter type"加起来其实只是 EnvironmentSnapshot 的一个维度（能力）。把它放在更大的图景里，整个项目结构才说得通：

> 项目本体 = 一段"两个人一起塑造一个由环境涌现的角色"的关系
> 工程结构 = 把这段关系的"环境"做成可塑、可注入、可演化的字典系统

UI、demo、landing、ticket，全都从这一句倒推。

## 9. 用户原话保留（参考）

来源：2026-06-16 第三次会话

> "为什么我创造出的角色和我聊天都不生气，后来我发现我上传的表情图以为没有生气的表情，speech 字段又在 emotion 字段之后"

> "动态提示词的编排很重要，能在每一个需要的时机注入最合适的提示词我认为很重要"

> "对于多模态可能就是在最合适的时机看到最合适的提示词，看到最最合适的场景，听到最合适的声音等等，互相影响，每天都有新鲜感"

> "我还想要遵守规则的同时玩出花样"

> "我希望我做的这些，都是能让我与她或者用户与她相处更加轻松多元，一起帮助她打理生活"

这些话是这份文档的根。
