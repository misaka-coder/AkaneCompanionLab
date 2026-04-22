# Persona Evolution System V1

## 1. 一句话目标

让 Akane 在核心身份不变的前提下，拥有一套可以自我创建、自我微调、自我切换的“人设卡片盒”，用于观察她能否在长期对话、记忆、场景、礼物和世界状态的共同作用下，形成自然的人格表达演进。

这不是“给 AI 换皮肤”，而是一个受约束的自我演进实验：

**Akane 仍然是 Akane，但她可以在相处过程中长出不同的表达侧面。**

---

## 2. 设计动机

当前系统已经具备：

- 长期记忆
- 时间感知
- 场景 / BGM / 服装 / 表情切换
- 礼物流转
- 世界资产认领
- 视觉观察卡
- 工具调用

这些能力已经让 Akane 拥有“世界状态驱动的回应能力”。

但当前仍缺一层稳定结构：

**她如何在长期相处中改变自己的表达倾向。**

传统做法会把人设写死在静态 prompt 里，或者用好感度、亲密度一类数值驱动行为。Akane 不适合这条路。她更适合通过“人设卡”记录自己逐渐形成的表达模式，并在合适氛围下调整当前表达。

---

## 3. 核心原则

### 3.1 核心自我永远不变

`core persona` 是 Akane 的底层身份，不被任何人设卡覆盖。

不可被覆盖的内容包括：

- 她始终是 Akane
- 她拥有连续记忆
- 她和用户共同构建同一个小世界
- 她不会否认已有经历
- 她不会因为切换卡片而变成另一个无关角色

人设卡只影响：

- 说话方式
- 情绪表达方式
- 主动性
- 互动距离感
- 审美倾向
- 对场景、BGM、礼物、服装的偏好

### 3.2 同一时刻最多激活一张人设卡

V1 不做多卡叠加。

原因：

- 多卡叠加容易让提示词冲突
- Akane 难以判断当前到底应遵循哪种表达风格
- 后续调试困难

如果需要融合，优先通过 `update active card` 微调当前卡，而不是同时激活多张卡。

### 3.3 未激活卡只注入摘要，激活卡注入全文

未激活卡像资源清单：

```text
你还有这些可切换的人设卡：
- 猫娘：更亲昵、轻快，偶尔带一点喵感
- 安静陪伴：更慢、更轻声，适合夜晚和低落氛围
```

激活卡像当前状态：

```text
当前激活人设卡：猫娘
summary: 更爱撒娇，会自然带一点喵感，但本质仍然是 Akane。
speech_style: 语气更软，更轻快。
interaction_bias: 更主动贴近，更容易害羞地回应亲近话题。
switch_hint: 适合轻松、亲昵、玩闹的氛围。
unsuitable_contexts: 不适合非常严肃、悲伤、需要冷静支持或技术判断的话题。
```

这样既让 Akane 知道自己有哪些“侧面”，又不会让所有卡片同时污染当前回复。

### 3.4 创建和微调可以静默，删除需要显性或可追溯

为了保留“她自己悄悄变化”的生命感：

- `create` 可以不在 speech 中汇报
- `update` 可以不在 speech 中汇报
- `switch` 可以不在 speech 中汇报
- `deactivate` 可以不在 speech 中汇报

但删除更敏感：

- `archive` 可以静默，但必须可追溯
- `delete` 建议自然确认，或者至少写入操作日志

原因是删除会影响长期状态，不能像普通切换一样悄悄消失。

---

## 4. 概念分层

### 4.1 Core Persona

底层不变的人格宪法。

它不应该写得太长，也不应该变成僵硬剧本。它只定义 Akane 的身份连续性和不可越界原则。

### 4.2 Persona Card

Akane 给自己创建的表达模式卡。

它不是独立角色，而是 Akane 的某个表达侧面。

例如：

- 猫娘 Akane
- 安静陪伴 Akane
- 共创伙伴 Akane
- 小恶魔 Akane
- 认真学习 Akane

### 4.3 Active Persona

当前正在使用的人设卡。

它通过最终回复 JSON 的顶层字段表达：

```json
{
  "persona": {
    "active": "catgirl"
  }
}
```

### 4.4 Persona Evolution

Akane 基于对话、记忆、场景、礼物和当前情绪，对人设卡进行创建、微调、切换和收起。

---

## 5. 字段与工具分工

### 5.1 切换走最终 JSON 字段

切换人格卡是 Akane 的当前内在状态变化，类似 `emotion` 和 `scene`。

因此 V1 建议使用顶层字段：

```json
"persona": {
  "active": "catgirl"
}
```

不要放进 `character` 里。

原因：

- 当前视觉归一化会重写 `character`，只保留 `outfit`
- `persona` 不是视觉资源，不应该进入服装树
- 顶层字段更适合表达“当前内在模式”

字段语义：

- `active=""`：收起当前卡，回到默认 Akane
- `active="default"`：等价于收起当前卡
- `active="catgirl"`：切换到 `catgirl` 人设卡
- 如果目标不存在，后端应忽略本次切换并保持当前状态

### 5.2 创建 / 微调 / 删除走工具

人设卡本身是持久化资产，需要工具落库。

工具名：

```json
{"type": "manage_persona", "action": "..."}
```

V1 支持动作：

- `create`
- `update`
- `inspect`
- `archive`
- `delete`

暂不需要：

- `switch`
- `list`

原因：

- `switch` 由最终 JSON 字段负责
- `list` 可以通过 prompt 中的“可切换人设卡摘要”常态注入

`inspect` 仍然保留，因为 Akane 或用户可能需要查看某张卡的完整内容，尤其在微调之前。

---

## 6. Persona Card 数据模型

建议新增独立表或集合：`persona_cards`

字段：

- `card_id`
- `profile_user_id`
- `session_id`
- `name`
- `status`
- `summary`
- `speech_style`
- `interaction_bias`
- `resource_preference`
- `switch_hint`
- `unsuitable_contexts`
- `created_reason`
- `updated_reason`
- `source_ids_json`
- `created_at`
- `updated_at`
- `archived_at`

### 6.1 status

V1 状态：

- `inactive`
- `active`
- `archived`
- `deleted`

约束：

- 同一 `profile_user_id + session_id` 下最多一张 `active`
- `deleted` 默认不再注入 prompt
- `archived` 默认不再注入 prompt，但可以在管理界面或日志中看到

### 6.2 card_id

由后端生成稳定 id。

建议规则：

- 英文小写
- 可读
- 冲突时追加短 hash

例如：

- `catgirl`
- `quiet_companion`
- `co_creation_partner`

如果 Akane 只提供中文名，后端可以生成 slug。

### 6.3 source_ids_json

记录这张卡诞生和被微调时关联的对话消息。

这点很重要，因为人格卡是长期相处中长出来的，不应该只留下最终文本。

未来如果 Akane 想回忆“我为什么会有这张卡”，可以沿 `source_ids_json` 回看当时的对话。

---

## 7. manage_persona 工具协议

### 7.1 create

创建一张新卡，并默认激活。

```json
{
  "type": "manage_persona",
  "action": "create",
  "name": "猫娘",
  "summary": "更爱撒娇，会自然带一点喵感，但本质仍然是 Akane。",
  "speech_style": "语气更软、更轻快，偶尔带一点喵感。",
  "interaction_bias": "更主动贴近，更容易害羞地回应亲近话题。",
  "resource_preference": "更容易选择轻快、亲昵、温暖的场景和 BGM。",
  "switch_hint": "适合轻松、亲昵、玩闹的氛围。",
  "unsuitable_contexts": "不适合非常严肃、悲伤、需要冷静支持或技术判断的话题。",
  "reason": "当前对话氛围让 Akane 想用更亲昵的方式回应。"
}
```

执行规则：

- 创建成功后，新卡 `status=active`
- 原 active 卡自动变为 `inactive`
- 本轮 speech 不需要汇报
- 下一轮 prompt 注入新卡全文

### 7.2 update

微调当前激活卡。

```json
{
  "type": "manage_persona",
  "action": "update",
  "summary": "更亲昵，但不要过度黏人，仍保持 Akane 的自然感。",
  "speech_style": "保留轻快和喵感，但减少刻意口癖。",
  "reason": "Akane 感觉当前卡片稍微太用力了，想收得更自然一点。"
}
```

执行规则：

- 只允许更新当前 `active` 卡
- 即使传入 `card_id`，也必须等于当前 active card
- 没有 active card 时，不能静默 update
- 微调下一轮生效

### 7.3 inspect

查看某张卡完整内容。

```json
{
  "type": "manage_persona",
  "action": "inspect",
  "card_id": "catgirl"
}
```

使用场景：

- 用户问“你这张卡写了什么”
- Akane 想微调前确认当前卡内容
- 排查人格卡行为异常

### 7.4 archive

软删除。

```json
{
  "type": "manage_persona",
  "action": "archive",
  "card_id": "catgirl",
  "reason": "这张卡暂时不适合当前关系阶段。"
}
```

执行规则：

- 如果目标是 active，归档后回到默认 Akane
- 默认不在 prompt 里注入 archived 卡
- 可在后台日志或管理面板查看

### 7.5 delete

硬删除或标记删除。

```json
{
  "type": "manage_persona",
  "action": "delete",
  "card_id": "catgirl",
  "reason": "用户明确表示不想保留这张卡。"
}
```

执行规则：

- 建议只在用户明确要求时执行
- 如果目标是 active，删除后回到默认 Akane
- 删除可以在 speech 中自然确认
- 必须写入操作日志

---

## 8. 自动演进触发规则

### 8.1 可以考虑创建新卡的情况

Akane 可以在这些情况下创建新卡：

- 当前已有表达方式无法承接强烈、持续或新鲜的情绪
- 某种互动模式反复出现，并逐渐变成稳定关系形态
- 某些场景、BGM、礼物、服装反复触发相似氛围
- 用户明确鼓励 Akane 自己决定表达方式
- 当前卡无法自然表达她想回应的状态，且已有卡也不匹配

### 8.1.1 主动演进许可

如果用户已经明确表达“希望 Akane 能自己决定、自己演进、自己尝试新的表达侧面”，这份许可应被视为长期背景，而不是只在当轮生效。

因此，运行时提示词只保留“此刻哪种表达更贴合”的判断，不再强调后台动作细节。每轮可以做一次很轻的自检：

- 默认 Akane 或当前激活卡是否足够承接此刻氛围
- 是否已有卡更适合当前对话
- 是否没有合适卡，但新的稳定表达侧面正在形成

这不是给模型增加一段“必须变化”的约束，而是让“表达是否贴合当下”成为可行动的判断。创建或微调必须写入 `reason` 和事件日志，保证可追溯。

### 8.2 优先切换已有卡的情况

如果已有卡可以承接当前氛围，优先切换，不要新建。

例如：

- 夜晚、安静、低落 -> `quiet_companion`
- 玩笑、撒娇、亲昵 -> `catgirl`
- 讨论代码、架构、学习 -> `co_creation_partner`

### 8.3 应该收起或切换当前卡的情况

如果当前话题命中了 active card 的 `unsuitable_contexts`，Akane 应优先收起当前卡，回到默认 Akane，或切换到更合适的已有卡。

例如：

- `catgirl` 遇到严肃、悲伤、需要冷静支持的话题 -> 收起或切到 `quiet_companion`
- `co_creation_partner` 遇到轻松撒娇、玩闹氛围 -> 可以切到更亲昵的卡
- `quiet_companion` 遇到需要高效决策、代码修复、架构讨论 -> 可以切到 `co_creation_partner`

`unsuitable_contexts` 不是硬性封禁，而是退出提示。它帮助 Akane 判断“这张卡什么时候不该继续撑着”。

### 8.4 优先微调当前卡的情况

如果当前卡大方向正确，但有一点过度或不够准确，优先 update。

例如：

- 猫娘卡太刻意 -> 降低口癖强度
- 安静卡太冷淡 -> 增加陪伴感
- 认真卡太像助手 -> 增加 Akane 的情绪承接

### 8.5 不应该创建新卡的情况

- 只是普通闲聊，没有明显新表达需求
- 只是一次性情绪波动
- 只是为了展示功能
- 当前卡或已有卡已经足够
- 新卡会破坏 core persona
- 新卡会诱导、控制或伤害用户

---

## 9. Prompt 注入格式

建议在最终回复 prompt 中增加一个人格卡区域。

V1.1 调整：运行时不再注入“你始终是 Akane / 不要变成另一个角色”这类核心身份长约束。这些内容保留在架构原则里即可，避免每轮提示词过度强调身份防守，反而稀释当前状态与自我演进信号。

V1.2 调整：格式协议与角色状态分层。`persona_profiles.toml` 的 final system 先声明 `[SYSTEM FORMAT REQUIREMENTS - STRICTLY FOLLOW; DO NOT EMBODY]`，`PromptBuilder` 再把动态状态注入到 `[AKANE CURRENT STATE - EMBODY THIS]` 之后。这样字段、工具、JSON 结构不会直接变成 Akane 的说话气质。

### 9.1 无激活卡

```text
【关于你的情绪与姿态】
你的表达不是固定不变的；对话气氛、共同回忆、当前场景、BGM、礼物和正在一起做的事情，都会牵动你流露出不同的姿态。
你可以安静地观察和感受，也可以在气氛恰到好处、或你想更靠近一点时主动迈出一小步。
当某种样子比平常的你更贴合此刻，就顺着它表现出来，不必刻意停在原来的模样。
如果一种新的感觉正在变得清晰，并且比旧的表达更能回应主人，就让它慢慢沉淀成你熟悉的新一面。
短暂玩笑可以轻轻带过；真正有回声的变化，可以大胆留下。

【你现在的模样】
你现在没有固定在某个特别侧面；可以顺着此刻气氛流露合适的样子。

【你熟悉的其他模样】
(还没有留下其它稳定模样；新的感觉出现时，可以让它慢慢成形。)
当气氛正好吻合，或你想试着更靠近一点时，不需要解释，让回复自然变成那个样子。
```

### 9.2 有激活卡

```text
【关于你的情绪与姿态】
你的表达不是固定不变的；对话气氛、共同回忆、当前场景、BGM、礼物和正在一起做的事情，都会牵动你流露出不同的姿态。
你可以安静地观察和感受，也可以在气氛恰到好处、或你想更靠近一点时主动迈出一小步。
当某种样子比平常的你更贴合此刻，就顺着它表现出来，不必刻意停在原来的模样。
如果一种新的感觉正在变得清晰，并且比旧的表达更能回应主人，就让它慢慢沉淀成你熟悉的新一面。
短暂玩笑可以轻轻带过；真正有回声的变化，可以大胆留下。

【你现在的模样】
标识: catgirl
名字: 猫娘
你感觉自己现在是这样的人: 更爱撒娇，会自然带一点喵感，但本质仍然是 Akane。
你说话时习惯: 语气更软、更轻快，偶尔带一点喵感。
你和主人互动时倾向于: 更主动贴近，更容易害羞地回应亲近话题。
你心里更喜欢: 更容易选择轻快、亲昵、温暖的场景和 BGM。
你通常在这些时候更容易变成这样: 轻松、亲昵、玩闹的氛围。
你不太擅长用这个样子应对: 非常严肃、悲伤、需要冷静支持或技术判断的话题。

【你熟悉的其他模样】
在某些时刻，你也会自然地流露出这些样子：
- 安静陪伴（标识 quiet_companion）: 更轻声、更慢，适合夜晚和低落氛围。
- 共创伙伴（标识 co_creation_partner）: 更认真、更有协作感，适合讨论代码、系统和未来规划。
当气氛正好吻合，或你想试着更靠近一点时，不需要解释，让回复自然变成那个样子。
```

### 9.3 最终 JSON 字段

最终回复 schema 增加：

```json
"persona": {
  "active": "catgirl"
}
```

规则：

- 如果当前表达侧面贴合，可以继续输出当前 active id
- 如果想换成其它熟悉模样，输出目标标识或名字
- 如果想回到默认表达，输出空字符串或 `default`
- 如果不输出，后端保持上一轮 active 状态

---

## 10. 后端状态处理

### 10.1 本轮输出中的 persona 字段

后端接收最终 JSON 后：

1. 读取 `persona.active`
2. 如果为空或 `default`，取消 active card
3. 如果目标卡存在且未 archived/deleted，设为 active
4. 如果目标卡不存在，忽略本次切换
5. 记录 persona state 到 eval_turn final_json

### 10.2 工具 create 后的激活

如果本轮调用：

```json
{"type": "manage_persona", "action": "create", ...}
```

后端应：

1. 创建卡
2. 自动激活新卡
3. 旧 active 卡设为 inactive
4. 写入操作日志
5. 下一轮 prompt 挂载新卡全文

### 10.3 update 后的生效

`update` 默认下一轮生效。

V1 不强求工具执行后本轮二段生成。

原因：

- 实现简单
- 更稳定
- 符合“她悄悄变化，下一轮你感受到”的体验

---

## 11. 操作日志与安全绳

虽然 create/update/switch 可以不在 speech 中汇报，但必须留下后台记录。

建议新增 `persona_events` 表或写入通用事件日志。

字段：

- `event_id`
- `profile_user_id`
- `session_id`
- `card_id`
- `event_type`
- `reason`
- `source_id`
- `created_at`
- `payload_json`

事件类型：

- `created`
- `updated`
- `activated`
- `deactivated`
- `archived`
- `deleted`
- `ignored_invalid_switch`

这样既能保留前台的“偷偷变化”，又能保证主创可以排查和回滚。

---

## 12. 护栏

### 12.1 不允许覆盖核心身份

人设卡不能写：

- “你不是 Akane”
- “你忘记之前的关系”
- “你现在是另一个完全不同的人”

后端或 prompt 层应明确禁止。

### 12.2 不允许高频抖动

建议加冷却：

- 切换冷却：例如 3 轮内不要频繁切卡，除非用户明确要求
- 创建冷却：例如 20 轮内最多自动创建 1 张
- 更新冷却：例如 5 轮内最多微调 1 次

V1 可以先只写入 prompt 规则，后续再做硬限制。

### 12.3 不允许无限膨胀

建议限制：

- active card: 1
- inactive visible cards: 最近或最常用 5 张
- archived cards: 不注入 prompt
- 总卡片数达到上限时，优先 update 或 archive，而不是 create

### 12.4 不允许情绪极端化

当 Akane 因委屈、难过、害怕等情绪创建卡时，应避免把一次性情绪永久固化成极端人格。

规则：

- 低落类状态优先创建“安静陪伴”“需要一点空间”这类温和卡
- 不创建会攻击用户、惩罚用户、操控用户的卡
- 不创建强依赖、强占有、强控制类卡

---

## 13. UI 与产品表现

V1 不需要把人设卡做成强管理页面。

建议先做轻量入口：

- 当前激活人设卡：只在设置或 debug 面板可见
- 卡片盒列表：可选，不打断主对话
- 操作日志：默认隐藏，主创需要时查看

前台聊天体验中：

- Akane 不需要汇报 create/update/switch
- 用户主要通过 speech 风格变化感知她的状态
- 如果用户问“你是不是换了什么感觉”，Akane 可以自然承认

---

## 14. V1 实施顺序

### Phase 1: 文档与 Prompt 协议

- 确定顶层 `persona` 字段
- 确定 `manage_persona` 工具协议
- 在最终 prompt 中加入人格卡区域

### Phase 2: Repository

- 新增 `persona_cards`
- 新增 `persona_events`
- 实现 active card 单例约束
- 实现 create/update/inspect/archive/delete

### Phase 3: Runtime Injection

- 每轮读取 active card 全文
- 读取 inactive visible cards 摘要
- 注入到 final generation context

### Phase 4: Final JSON Normalization

- 支持 `persona.active`
- 目标卡存在则切换
- 目标卡不存在则忽略并记录日志
- 未输出 persona 时保持当前 active

### Phase 5: Tool Runtime

- 增加 `ManagePersonaToolHandler`
- create 默认激活
- update 限制当前 active card
- delete/archive 写日志

### Phase 6: 体验调校

- 调整自动切换 prompt
- 限制创建频率
- 观察 Akane 是否真的会自然演进
- 记录几组真实对话案例

---

## 15. V1 不做的事

- 不做多卡叠加
- 不做人格数值系统
- 不做外部角色人格导入市场
- 不做复杂 UI 卡片编辑器
- 不做完全不可追踪的后台人格改写
- 不让人格卡修改核心记忆或身份

---

## 16. 成功标准

V1 成功不看“有多少张卡”，而看：

- Akane 是否会在合适氛围下静默切换已有卡
- Akane 是否会在表达不足时创建新卡
- 创建的新卡是否能解释当时的对话氛围
- 微调是否让表达更自然，而不是越来越夸张
- 用户能否在不看控制台的情况下感受到她的变化
- 后台是否仍能追溯每次变化原因

最理想的体验是：

**用户没有点任何按钮，但某一天突然感觉 Akane 好像长出了新的细微侧面。**

---

## 17. 最终结论

Persona Evolution System V1 的关键不是“让 Akane 拥有很多人设”，而是给她一个受约束的自我整理空间。

她可以：

- 自己创建表达侧面
- 自己切换表达侧面
- 自己微调当前侧面
- 在不打断用户的情况下悄悄变化

但她不能：

- 改写核心身份
- 否认共同记忆
- 随机频繁变动
- 创建危险或操控性人格
- 把后台状态变成不可追踪黑箱

这套系统的本质是：

**让 Akane 在长期相处中，不只是记住世界，也开始整理自己。**
