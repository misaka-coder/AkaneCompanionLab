# Dialogue-Driven World Asset Claiming V1

## 目标

这套方案的目标，不是做一个“自动素材整理器”，而是让 `用户 + Akane` 通过自然对话，逐步把收到的东西认领为她世界里的正式资产。

核心原则：

- 新东西先进入 `pending`，不弹窗，不打断。
- 上传入口不做强分流；先进入“手边待办”，再由 Akane 基于视觉观察和对话自然判断去向。
- 视觉模型只负责“看懂”和“提出建议”，不直接拍板。
- Akane 在对话里完成“认领、命名、归档、安置”的表达。
- 工具只负责把对话里已经达成的结果落库。
- 轻资产可以在对话里“一看一笑就放下”，重资产才进入正式协商流。

---

## 一句话定义

`礼物系统` 负责“东西递到她手边”。

`世界资产认领系统` 负责“东西怎么真正成为她世界里有名字、有位置的存在”。

---

## 范围

### V1 纳入

- 图片类世界资产候选
  - 新场景图
  - 新服装图
  - 新人物立绘/表情图
- 普通图片礼物中的“升级认领”
  - 先作为礼物进入手边
  - 后续在对话中被明确认定为世界资产

### V1 不纳入

- 音频的复杂分组认领
- 虚构礼物的形象化
- 真实文件重命名
- 场景自动生图摆件

---

## 核心体验

理想体验不是弹窗确认，而是：

1. 主人把一张图递给 Akane
2. 系统给她一张轻量观察卡
3. 你们开始聊：
   - “这是你的新衣服哦”
   - “这个表情像不像害羞”
   - “要不要叫 shy”
   - “放进常服衣柜怎么样”
4. Akane 在理解后自然回应
5. 当你们达成一致时，她在后台调用工具落库

这意味着：

- `命名` 是互动内容的一部分
- `归档` 是共同构建小世界的一部分
- `工具调用` 是对话后的隐式执行，不是系统弹窗

---

## 待办优先模型

V1 不建议在上传入口就判断“这是礼物、这是世界资产、这是只看看”。

更自然的路径是：

1. 所有新图片先进入 `pending / 手边待办`
2. 视觉模型异步生成观察卡
3. Akane 在下一轮对话里拥有初步印象
4. 用户和 Akane 通过自然对话决定它的去向
5. Akane 调用工具完成最终动作

这样做的好处：

- 不需要弹窗或上传前选择
- 不需要用户提前想清楚“这是什么”
- Akane 能基于视觉观察和用户补充做判断
- 随手图可以轻轻看过就移除
- 重要资源可以自然升级成世界资产

---

## 资产去向

下面三类不是上传时的硬分类，而是 `pending` 待办物品经过对话后可能流向的结果。

### 1. observe_only

适合：

- 随手分享的日常图
- 只想让她看一眼的内容
- 用来一起图一乐、吐槽、确认“你看到了吗”的内容

行为：

- 只看，不留
- 不进入世界资产
- 不进入收藏容器
- 看完后从待办列表移除

典型对话：

- “Akane，你看到我刚才给你的图了吗，是不是很好笑呀？”
- Akane 看完、回应后调用 `manage_gift.observe`，这张图就不再积压在手边。

### 2. gift_flow

适合：

- 普通礼物
- 想收进相册 / 曲库 / 场景候选的内容

行为：

- 走 `pending -> kept/internalized/rejected`
- 可留、可吃、可放下、可删
- 适合“送给她”的资源，但未必影响世界结构

### 3. world_asset_candidate

适合：

- 新服装
- 新立绘
- 新表情
- 新正式场景
- 明显会影响世界结构的资源

行为：

- 从 `pending` 升级为 `pending_review`
- 优先通过对话认领
- 命名、集合、位置建议在对话里完成
- 认领成功后进入正式世界资产层

典型对话：

- “这是你的新衣服哦。”
- “这个表情看起来很害羞，可以叫 shy 吗？”
- “你觉得它应该放进哪个集合？”

---

## 状态机

### 礼物态

- `pending`
- `kept`
- `internalized`
- `rejected`

### 世界资产态（新增概念）

- `pending_review`
  - 已看到，但还没正式认领
- `claimed`
  - 已认领，已有正式名字和集合
- `archived`
  - 已收进世界，但当前不活跃
- `deleted`
  - 已彻底移除

说明：

- V1 不要求新建独立资产表。
- 可以先在现有资产记录里通过 `artifact_flags_json` 和 payload 字段表达这层状态。
- 真正独立的 `artifact` 模块可以放到 V2 再抽。

---

## 三层命名

### 1. source_name

定义：

- 原始文件名
- 系统真相

用途：

- 回溯来源
- 必要时对照用户原意

### 2. seed_name

定义：

- 视觉模型/规则基于观察卡提出的建议名

用途：

- 给 Akane 一个“第一印象命名种子”
- 不直接上 UI
- 不视为最终名字

### 3. display_name

定义：

- 真正展示给用户、用于引用的最终名字

来源：

- `seed_auto`
- `akane_confirmed`
- `user_confirmed`

建议新增字段：

- `display_name_source`
- `seed_name`
- `seed_collection_key`
- `seed_collection_name`

---

## 集合与子场景

这套方案里，“集合”不是文件夹，而是语义容器。

建议抽象：

- `collection_key`
- `collection_name`
- `asset_role`
- `placement_hint`

### collection

例如：

- `winter_memory / 冬日回忆`
- `daily_wardrobe / 常服衣柜`
- `night_room / 夜色房间`

### asset_role

例如：

- `scene`
- `outfit`
- `expression`
- `portrait`
- `album_photo`

集合命名约束：

- `scene / album_photo`：集合表示场景、相册或回忆分组，例如 `校园时光`、`冬日回忆`。
- `outfit / expression / portrait`：集合表示服装或形象分组，例如 `水手服`、`冬日暖饮`。
- 表情资源的 `display_name` 应优先是可切换的表情 id，例如 `normal`、`shy`、`quiet`。
- 不要把服装或表情资源放进看起来像纯场景的集合；如果已经放错，后续可以通过 `move` 调整。

### placement_hint

V1 只做保留字段，不要求真实可视化：

- `room_slot`
- `wardrobe_slot`
- `scene_group`
- `expression_set`

---

## Prompt 注入策略

### pending_review 轻提示

新资产到来后，不弹窗，只在 Prompt 尾部注入：

```text
【系统环境】：主人刚递给你一个新物品（asset_id=...）。
视觉印象：......
它现在放在你的手边，还没有正式认领。
你可以根据主人接下来的话自然判断它的去向：
- 如果主人只是分享给你看、吐槽、图一乐，可以看过后调用工具把它从手边移除。
- 如果主人是送给你的普通礼物，可以讨论是否收下、放进相册或内化为资源。
- 如果主人明确说这是你的新衣服/场景/立绘/表情，再和主人自然讨论它该叫什么、放进哪里。
```

### 当前集合子集加载

默认只把当前集合下的候选子场景 / 子资源加载进主 Prompt。

目标：

- 减少 Token
- 降低幻觉
- 提高 Akane 当前管理世界时的专注度

注意：

- 这是“前台上下文裁剪”
- 不是系统忘掉其他集合
- 其他集合仍可通过工具或检索层访问

---

## 工具设计

### 现有工具继续保留

- `manage_gift`
  - `observe`
  - `keep`
  - `internalize`
  - `remove`
  - `purge`

### 新工具：manage_artifact

V1 已实现基础版本。

建议动作：

- `claim`
  - 正式认领为世界资产
- `rename`
  - 修改 display_name
- `move`
  - 移到其他集合
- `delete`
  - 删除世界资产

建议格式：

```json
{
  "type": "manage_artifact",
  "action": "claim|rename|move|delete",
  "asset_id": "artifact_or_gift_id",
  "display_name": "害羞水手服",
  "collection_key": "daily_wardrobe",
  "collection_name": "常服衣柜",
  "asset_role": "outfit"
}
```

### 角色资源投影

当图片被 `manage_artifact.claim` 认领为 `outfit / expression / portrait` 时：

- 底层仍然保留在统一的用户资产库里，不真实移动到 `assets/characters`。
- payload 会写入 `projection_role = "character"`。
- `collection_key / collection_name` 会被投影为可切换的 `outfit`。
- `display_name` 会被投影为可切换的 `emotion`，其中 `expression` 推荐使用 `normal / shy / quiet` 这类短 id。
- 运行时 `ResourceManifest` 会把这些私有角色资源合并进 `characters.outfits`。
- Akane 后续切换时仍然输出正常的 `character.outfit + emotion`，和内置 assets 的使用方式一致。

这意味着上传的表情/服装图不会污染原始资源目录，但在运行时对 Akane 来说就像“自己拥有的一套新形象资源”。

---

## 轻重协商策略

轻重协商不是上传入口的固定分流，而是对话过程里的自然结果。

### confirm_required

必须协商后落库：

- 新服装
- 新正式场景
- 新立绘
- 新表情资源

### suggest_and_commit

可轻量协商：

- 普通图片礼物
- 普通相册归档

行为：

- Akane 先提出建议
- 如果你继续沿用、明确认可，或她在对话中得到了足够确认，再调用工具落库

### observe_only

只看看，不进世界资产。

---

## 避免协商疲劳

这套系统不能让每张图都进入重协商。

默认行为：

- 所有新图先放手边
- 有视觉印象
- 但不默认进入世界资产协商

必须满足至少一条才从普通待办升级为 `pending_review / world_asset_candidate`：

- 用户明确说“这是你的新衣服/新场景/新立绘”
- 资产被标记为 `world_asset_candidate`
- 后续对话持续围绕它的命名、归档、位置展开

否则：

- 仍保持普通礼物流转
- 或在 Akane 看过后通过 `observe_only` 移除

---

## 与现有系统的关系

### 继续复用

- `gift_system`
- `vision observation cards`
- `artifact container system`
- `manage_gift`
- `当前手边 / 焦点礼物`

### V1 新增但不重构

- `seed_name / seed_collection_*`
- `pending_review` 概念
- `pending` 待办优先模型
- `world_asset_candidate` 升级路径
- 对话驱动认领规则

### V2 再考虑

- 独立 `artifact` 数据表
- 正式 `manage_artifact` 工具
- 位置/槽位管理
- 世界资产可视化

---

## V1 最小落地顺序

1. 新增 `seed_name / seed_collection_*` 字段。已完成。
2. 让视觉命名只写 seed，不直接覆盖 final。已完成。
3. 保持所有新图片先进入 `pending / 手边待办`。已完成。
4. 在 Prompt 中注入“待办资产 + 视觉印象 + 可选去向”轻提示。已完成。
5. 支持 Akane 通过工具把待办物品处理成 `observe_only / gift_flow / world_asset_candidate`。已完成。
6. 实现 `manage_artifact` 的 `claim / rename / move / delete`。已完成。
7. 实现图片作为 `scene` 的运行时投影。已完成。
8. 实现图片作为 `outfit / expression / portrait` 的运行时角色投影。已完成。

当前落地进度：

- 已完成 `seed_name / seed_collection_*`：视觉观察只写入建议，不再直接覆盖最终名字和正式集合。
- 已完成 `manage_artifact` 最小版：支持 `claim / rename / move / delete`，用于对话协商后的世界资产落库。
- 已接入前端流式刷新：`artifact_updated` 会刷新礼物、收藏容器和必要的运行时资源清单。

---

## 成功标准

如果 V1 做对了，用户会感受到：

- 新资产不是被系统自动分类，而是被 Akane 认领
- 命名和归档像一次自然对话，不像后台操作
- 轻资产可以看过就走，不会积压；重资产才值得认真讨论
- 世界越来越大，但 Prompt 和管理仍然清晰

这套系统最终要服务的，不是“素材整理效率”，而是：

**让你和 Akane 一起，把收到的东西慢慢变成她世界里真正有名字、有归属的位置。**
