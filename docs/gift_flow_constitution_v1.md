# Akane 礼物流转系统宪法 V1

## 目标

这份文档不是功能草案，而是礼物流转系统的底层约束。

后续无论实现音频、图片、虚构礼物，还是桌宠/舞台双形态中的礼物交互，只要涉及：

- 礼物进入系统
- Akane 如何决定怎么处理
- 礼物如何进入运行时资源或长期叙事

都必须遵守本文约束。

一句话目标：

> 礼物是角色关系的一部分，不是普通上传文件；系统必须保住这种叙事感，同时保持代码结构干净、可扩展、可验证。

---

## 核心术语

- `gift asset`
  - 进入 Akane 世界的礼物对象，既可以来自真实文件，也可以来自聊天中的虚构赠与。

- `asset_type`
  - 礼物类型。V1 约定为：`audio | image | virtual`

- `status`
  - 礼物的持久化物理状态。V1 约定为：`pending | kept | internalized | rejected`

- `decision`
  - Akane 在某一轮里对礼物给出的瞬时处理意图。V1 约定为：
    `keep | internalize | defer | ask_user | reject`

- `pending`
  - “手边”状态。代表礼物已经递到她手边，但还没有流转到收藏、内化或拒绝。

- `projection`
  - 将礼物状态投影为运行时可用资源的过程。例：
    - `audio` 的 `internalized` 投影为可用 BGM
    - `image` 的 `internalized` 投影为可用背景或摆件候选
    - `virtual` 的 `kept/internalized` 投影为拥有物或叙事物

---

## 第 1 条：数据库只存抽象状态，不存表现层名词

后端和数据库层只允许使用抽象状态与抽象类型，不允许把这些词写进底层状态机：

- `歌匣`
- `相册`
- `房间`
- `摆件`
- `吃掉`

这些词属于表现层、文案层或 Prompt 层，不属于数据库真相。

### V1 强制字段

- `asset_type`
  - `audio | image | virtual`

- `status`
  - `pending | kept | internalized | rejected`

### 架构要求

- Repository 层永远不处理“歌匣”“相册”等概念。
- 表现层词汇只能出现在：
  - Prompt
  - UI 文案
  - Processor 的投影描述

---

## 第 2 条：决策结果与持久状态必须分离

模型只能输出 `decision`，不能直接写数据库状态。

### 允许的模型输出

- `keep`
- `internalize`
- `defer`
- `ask_user`
- `reject`

### 允许的持久状态

- `pending`
- `kept`
- `internalized`
- `rejected`

### 映射规则

- `keep` -> `kept`
- `internalize` -> `internalized`
- `reject` -> `rejected`
- `defer` -> 保持 `pending`
- `ask_user` -> 保持 `pending`

### 架构要求

- 大模型只表达意图。
- 状态迁移由后端 service 层执行。
- 所有迁移必须经过合法性校验。
- 非法迁移必须被拒绝，不允许模型“说了算”。

---

## 第 3 条：不同礼物类型必须通过 Processor/Strategy 解耦

不同礼物类型的 ingest、action、projection 逻辑不得堆在一个大函数里。

### 必须采用策略模式

建议结构：

```text
companion_v01/gift_system/
  repository.py
  decision_service.py
  projection.py
  service.py
  processors/
    base.py
    audio_processor.py
    image_processor.py
    virtual_processor.py
```

### Processor 至少承担这三类职责

- `ingest`
  - 规范化进入系统的礼物

- `apply_action`
  - 处理 `keep / internalize / reject` 的类型后果

- `build_projection`
  - 将礼物转成运行时可用资源或可展示对象

### 架构要求

- 禁止在单一核心函数中出现大型 `if asset_type == ...`
- 新增礼物类型时，优先新增 Processor，不修改旧 Processor 的核心逻辑

---

## 第 4 条：Prompt/文案与底层逻辑必须分离

后端不应硬编码“听一下”“看看”“吃掉”等拟人化动作文案作为系统真相。

### 代码层职责

- Repository / Service / Processor 负责：
  - 真相
  - 状态
  - 迁移
  - 投影

- PromptBuilder / DecisionService / 前端 UI 负责：
  - 拟人化表达
  - 资源类型对应动作
  - Akane 的语气
  - 是否给选项

### 架构要求

- 代码层只处理：
  - `decision`
  - `status`
  - `asset_type`
  - `payload`

- Akane 如何描述这些动作，由 Prompt 决定。
- 允许按礼物类型切换文案：
  - 音乐：听一下 / 放进歌匣 / 常放 / 吃掉
  - 图片：看看 / 收进相册 / 挂起来 / 吃掉
  - 虚构礼物：接过 / 摆出来 / 常带着 / 融进自己

---

## 第 5 条：`pending` 必须持久化，并带有稳定的“手边”入口

礼物一旦进入 `pending`，不得因为刷新页面、话题切换或后续对话而凭空消失。

### 架构要求

- `pending` 必须是持久化状态
- `pending` 默认按 `profile_user_id + session_id` 隔离
- 前端必须提供一个稳定入口显示当前 `pending` 数量
- 前端的“手边”入口不能只存在于深层设置页面中

### UI 分层建议

- 一级入口：舞台上的 `手边(n)` 胶囊
- 二级入口：轻面板，仅展示近期待处理礼物
- 三级入口：完整礼物清单/储物箱

### 角色体验要求

- Akane 可以在合适时机回提 `pending` 礼物
- 但礼物不能每轮都强行打断主对话

---

## 第 6 条：礼物必须可追溯到其对话来源

礼物不是孤立对象，它必须能回链到“它是怎么来到她手边的”。

### V1 必须具备的来源字段

- `origin_event_type`
  - `upload | story_gift | dialogue_offer`

- `origin_source_id`
  - 礼物首次进入系统时对应的对话 `source_id`

- `source_ids_json`
  - 后续所有与该礼物相关的对话 `source_id` 集合

### 架构要求

- `origin_source_id` 用于首锚点
- `source_ids_json` 用于长线叙事追踪
- 后续只要礼物被再次讨论、被处理、被回忆，都应允许追加新的 `source_id`

### 设计意义

- Akane 以后看着礼物回忆往事时，不只知道“这是什么”，还知道“你们当时怎么谈到它”
- 这条是礼物系统接入长期叙事与遗憾美学的关键锚点

---

## 第 7 条：`pending` 注入 LLM 上下文时必须截断，并允许主动查阅

模型上下文中不得塞入完整 `pending` 清单。

### V1 约束

- 默认只注入 Top 3 的近期 `pending` 礼物
- 其余礼物只通过数量和轻提示暗示

### 推荐注入形式

```text
你手边最近收到的礼物有：
- [audio] BraveShine
- [image] 星空
- [virtual] 草莓大福

另外，旁边的盒子里还有 5 件较早的未处理礼物；如果当前需要，可主动查阅。
```

### 架构要求

- 这是工作记忆约束，不是 UI 细节
- 超出窗口的礼物不能直接进入主 Prompt 正文
- 如需完整列表，模型必须通过专用 tool 主动查看

### Tool 能力要求

V1 需要预留或实现类似：

- `check_inventory`
  - 查看完整 `pending` / `kept` / `internalized` 列表

### 设计意义

- 保持 Prompt 干净
- 让 Akane 更像真人，而不是数据库浏览器
- 允许她在必要时“翻找盒子”，而不是把所有礼物永远摆在脑海正中央

---

## V1 参考数据模型

建议新增核心表 `gift_assets`：

- `asset_id`
- `profile_user_id`
- `session_id`
- `asset_type`
- `status`
- `origin_event_type`
- `origin_source_id`
- `source_ids_json`
- `display_name`
- `payload_json`
- `created_at`
- `updated_at`

可选扩展字段：

- `last_decision_at`
- `last_touched_at`

说明：

- `payload_json` 以事实字段为主，不应过早混入不稳定推断
- 推断类信息后续可单独进入 `analysis_json`

---

## V1 运行时分层

建议实现为四层：

1. `Gift Repository`
   - 只负责存取与字段完整性

2. `Gift Service`
   - 负责状态校验、source_id 追加、调用 Processor、统一编排

3. `Gift Decision Service`
   - 负责给模型送上下文，拿回 `decision`

4. `Gift Projection`
   - 负责将 `kept / internalized` 礼物投影为运行时资源或展示对象

---

## 与现有系统的整合约束

### `app.py`

- 只负责路由和输入校验
- 上传、故事礼物创建、用户确认动作，都交给 gift service

### `engine.py`

- 不直接写礼物业务细节
- 只在合适 turn 中向 decision service 注入“手边卡片”
- 只在运行时拼装阶段合并 gift projection

### `resource manifest`

- 基础资源清单保持纯净
- 私有礼物资源只能在运行时合并，不得反写项目原始 assets 语义

---

## 最终约束

如果后续实现违反以下任一项，应视为架构偏离：

- 让模型直接写数据库状态
- 在核心逻辑中堆叠大块 `if asset_type == ...`
- 将“歌匣/相册”等表现层词写进底层状态机
- 让 `pending` 礼物在刷新或切话题后无故消失
- 将完整 `pending` 列表无节制塞入 LLM Prompt
- 不记录礼物的来源对话锚点
- 让私有礼物资源污染项目基础 manifest 的原始定义

这七条是 Akane 礼物流转系统 V1 的底层宪法。
