# Attachment Focus Inbox V1

本文档记录 Akane 在 QQ、未来桌宠、以及 Web “只看看”场景中处理图片和文件附件的临时感知架构。

文件转换、生成文件、生成物二次修改与发回客户端的完整蓝图见 `docs/file_processing_generated_artifacts_v1.md`。本文只定义用户发来的临时附件如何进入当前对话工作台。

核心原则：

```text
礼物是资产。
场景是世界。
附件是临时感知 / 临时阅读。
对话才是记忆。
```

Attachment Focus Inbox 不是礼物系统，也不是长期资料库。它是 Akane 当前对话里的“手边材料区”：用户给她看图、读文件，她能围绕这些材料聊几轮；聊完后，材料可以被 Akane 或系统移除，不污染长期记忆。

---

## 1. 为什么需要 Attachment Inbox

QQ 图片 / 文件不应直接塞进用户消息，也不应默认进入礼物系统。

如果把视觉模型输出的大段 JSON 拼进用户消息，会出现几个问题：

- 用户真实说的话和系统视觉感知混在一起。
- raw 对话流水里出现大量 `entities`、`mood_tags` 等结构化字段。
- 摘要、检索、时间线渲染会被视觉 JSON 干扰。
- Akane 回忆时可能把“系统看见的东西”误当成“用户说过的话”。

更干净的分层：

```text
User Message
只保存用户真实说的话。

Attachment Observation
单独保存图片观察卡 / 文件阅读卡。

Prompt Injection
当前几轮把附件卡作为临时上下文注入。

Memory
只吸收用户和 Akane 围绕附件产生的真实对话。
```

---

## 2. 默认语义

### QQ 图片

```text
QQ 发图 = 给 Akane 看看
```

默认不代表：

- 送礼物
- 收进相册
- 变成场景
- 写入长期记忆

只有用户明确说“收下、记住、归档、当成你的资源、作为场景 / 形象候选”等，才桥接到 Gift / Artifact 系统。

### QQ 文件

```text
QQ 发文件 = 给 Akane 临时阅读 / 帮忙处理
```

默认不代表：

- 永久保存
- 加入资料库
- 写入长期记忆

只有用户明确说“以后记住这个、作为资料保存、放进学习档案”等，才晋升为长期资料或 artifact。

---

## 3. 异步感知模型

```text
对话主线：
用户消息 -> 附件登记 -> 短等待观察结果 -> Akane 回复

感知后台线：
附件下载 -> 视觉 / 文件解析 -> 观察卡写入 SQLite -> 后续轮次可用
```

QQ 端会加一层很短的防抖窗口：如果用户连续发多张图 / 多个文件，较早事件只负责把附件登记进 Inbox，不单独唤醒 Akane；最后一个事件会等待同一批附件的视觉卡 / 文件卡尽量 ready，再统一回复。

这不是为了省 Token，而是为了避免主模型抢跑：Akane 不应该在视觉模型还没看完时先尴尬地说“我还没看清”。

如果等待超时，Prompt 才告诉 Akane：

```text
主人刚发了一个图片附件，系统正在看图，还没有生成详细印象。
不要假装已经看清内容。
```

附件顺序只认 `sequence_no`。视觉模型后发先完成、文件解析乱序完成，都不会改变 Prompt 中的“第 1 张 / 第 2 张 / 第 N 个文件”的展示顺序。

---

## 4. 数据模型建议

建议新增 `attachment_inbox_items` 表。

字段：

```text
attachment_id: String
profile_user_id: String
session_id: String
source: qq | desktop | web
kind: image | file
attachment_handle: img_001 | file_001 | audio_001
sequence_no: Integer
status: pending_observation | ready | cleared | expired | failed
origin_name: String
mime_type: String
file_ext: String
file_size: Integer
storage_relpath: String
source_event_id: String
source_message_id: String
summary_title: String
short_hint: String
detail_json: JSON
error_message: String
created_at: Integer
updated_at: Integer
last_used_at: Integer
focus_rank: Integer
expires_at: Integer
```

索引建议：

```text
(profile_user_id, session_id, status, updated_at DESC)
(profile_user_id, session_id, kind, status, updated_at DESC)
```

### `detail_json` 示例：图片

```json
{
  "summary": "一张晚餐照片，桌上有米饭、汤和几道家常菜。",
  "entities": ["米饭", "汤", "家常菜", "餐桌"],
  "mood_tags": ["日常", "温暖", "生活感"],
  "suggested_focus": "晚餐里有哪些菜、想吃哪一道"
}
```

### `detail_json` 示例：文件

```json
{
  "summary": "一份关于高数、Python、英语的学习计划。",
  "sections": ["高数", "Python", "英语"],
  "key_points": [
    "每天晚上复习高数",
    "周末做一个小项目",
    "英语每天背 30 个单词"
  ],
  "excerpt": "文件前若干字符或摘要节选"
}
```

---

## 5. Prompt 注入策略

Prompt 注入分三层：当前工作台、旁边文件筐、正在处理。

### 5.1 当前工作台 Focus

优先挂载 `focus_rank > 0` 的附件详细内容。

新进入且处理完成的一批附件会自动进入 Focus，避免用户发完文件后 Akane 只能先说“我打开看看”的迟钝感。默认语义是：用户发多少材料，系统就先尽力把这一批材料放到 Akane 眼前；后续如果 Akane 调用 `sync_attachment_workspace`，则以工具提交的清单替换当前 Focus。

Focus 不使用固定 3 个的硬上限，而使用较宽松的上下文预算：多张图片、小文本文件、小型代码片段可以一起展开；超大 PDF / Excel / 长文本如果放不下，会保留在 Manifest，并提示 Akane 用 `read_attachment_section` 指定页、行或 sheet 继续展开。

```text
【当前工作台 Focus】
1. [img_001]（第1张图） 图片《晚餐图》
视觉印象：桌上有米饭、汤和几道家常菜，氛围温暖日常。

2. [file_001]（第1个文件） 文件《学习计划.md》
内容：
（小文件可直接挂载全文；长文件按预算挂载，并提示继续展开。）
```

### 5.2 轻量索引区

未进入 Focus 的 `ready` 附件只给 handle、绝对序号、临时标题和结构化识别字段。

Manifest 不展示半截正文，也不展示长篇 summary；它只负责让 Akane 知道“旁边有什么”。真正的视觉描述、文本正文、表格内容应在 Focus 或 `read_attachment_section` 中出现。

```text
【旁边的文件筐 Manifest】
- [img_002]（第2张图） 图片《作业截图》：要素=窗口, 代码, 按钮；标签=截图, 报错
- [file_002]（第2个文件） 文件《error.log》：格式=txt；大小=3.1KB；行数=86
- [file_003]（第3个文件） 文件《成绩表.xlsx》：格式=xlsx；工作表=Sheet1, Sheet2

如果需要收起暂时不分析的附件、重新指定重点材料，或切换对比对象，可以使用 sync_attachment_workspace 工具整理当前工作台。
如果附件处理失败，可以使用 retry_attachment 工具重试。
如果附件已经聊完，可以使用 clear_attachment_focus 工具移除。
```

### 5.3 Pending 提示

对 `pending_observation` 附件，只给状态提示，不允许模型假装看清。

```text
【正在处理的附件】
主人刚发来 2 张图片，系统正在生成视觉印象。现在你只知道它们是图片，还没有详细内容。
```

---

## 6. 连续发图 / 文件

连续附件不应互相覆盖。

规则：

- 所有附件进入 inbox。
- 附件获得 session 内按 kind 绝对递增的 handle，例如 `img_001`, `img_002`, `file_001`。
- 删除、清理、过期都不回收编号。
- 有显式 Focus 时，只详细挂载 `focus_rank > 0` 的附件。
- 新附件处理完成时，后端会自动把最近一批附件设为 Focus，替换旧 Focus。
- 其它附件进入 Manifest 轻量索引区。
- 附件过多时，旧附件转为 background / expired。

建议限制：

```text
每个 session 最多保留 active/background 附件 8-10 个。
Prompt 详细挂载最多 2 个。
Prompt 轻量索引最多 6 个。
单文件读取最多 N 字符。
单批图片最多并发处理 N 张。
```

混合场景：

```text
[页面截图]
[错误日志.txt]
你看看哪里有问题
```

应作为同一个临时工作上下文处理。V1 可以先不做复杂 batch 推理，只要让 Prompt 同时看到最近图和最近文件即可。

---

## 7. 工具设计

Attachment Inbox 对 Akane 暴露的工具应属于 `qq_tools` / `desktop_tools`，不是 `web_scene_tools`。

### 7.1 `sync_attachment_workspace`

用途：声明式整理当前附件工作台。它不是“第一次打开附件”的工具；新发来的图片 / 文件会尽量自动进入 Focus，让 Akane 当轮就能基于内容回应。

当材料太多、话题变了、或用户只想分析其中几份时，Akane 不需要逐个打开 / 关闭附件，只提交整理后的最终工作台清单。

参数建议：

```json
{
  "type": "sync_attachment_workspace",
  "focus_targets": ["img_001", "第2张图", "菜单照片"],
  "kind": "image | file | document | audio | any",
  "reason": "用户要求对比这些附件"
}
```

效果：

- 后端解析 handle、绝对序号、标题、原文件名等线索。
- 后端自动把目标设置为 Focus，并把其它附件退回 Manifest。
- Focus 不再使用固定 3 个硬上限；工具可提交多张图片或多个文件进行对比。
- 后端按上下文预算尽量展开 Focus 内容；如果大文件放不下，会通过 followup_context 告知 Akane 继续用 `read_attachment_section` 精确展开。
- 工具返回当前 Focus 的详细卡片，Akane 当轮即可继续对比。

### 7.2 `inspect_attachment`

用途：展开某个较早附件的详细观察卡 / 文件卡。

参数建议：

```json
{
  "type": "inspect_attachment",
  "target": "current | latest | attachment_id | name_hint",
  "kind": "image | file | any"
}
```

返回：

- 指定附件详细卡片
- 若目标不明确，返回可选列表，让 Akane 自然询问用户

### 7.3 `clear_attachment_focus`

用途：移除已经聊完的附件。

参数建议：

```json
{
  "type": "clear_attachment_focus",
  "target": "current | all | attachment_id | name_hint",
  "targets": ["img_001", "第2张图", "计划.md"],
  "kind": "image | file | document | audio | any",
  "reason": "话题已经结束"
}
```

效果：

- 将目标附件状态改为 `cleared`
- 后续 Prompt 不再注入
- `target` 适合清理当前、全部或单个附件
- `targets` 适合任意批量清理，后端会按 handle、序号、标题、文件名提示解析
- `target="all"` 可配合 `kind="image"` / `kind="document"` 只清理某类附件

### 7.4 `retry_attachment`

用途：重试失败的临时附件。

参数建议：

```json
{
  "type": "retry_attachment",
  "target": "latest | img_006 | attachment_id | name_hint",
  "kind": "image | file | document | audio | any",
  "reason": "用户让我再试一次"
}
```

效果：

- 不创建新的附件编号，沿用原来的 `img_006` / `file_001`
- 将附件状态改回 `pending_observation`
- 重新走下载 / NapCat 本地缓存 / 文件解析 / 视觉观察管线
- 成功后回到临时附件焦点，失败后写入更可读的失败原因

### 7.5 未来：`promote_attachment`

V1 暂不建议实现。

未来可用于：

- 把图片桥接到 artifact / gift
- 把文件桥接到长期资料库
- 把图片作为场景 / 服装 / 相册候选

---

## 8. 记忆边界

Attachment 本身不进入长期记忆。

进入长期记忆的是围绕附件发生的自然对话。

示例：

```text
用户消息：你看看这个晚餐，你想吃哪个？
附件卡：图片里有米饭、汤、煎鱼。
Akane：我可能会先选那碗汤，看起来好暖。
```

长期记忆可总结为：

```text
主人曾给 Akane 看过一张晚餐图，两人聊起她想吃哪道菜。
```

而不是：

```json
{"entities":["米饭","汤","煎鱼"],"mood_tags":["温暖"]}
```

---

## 9. 生命周期

状态机：

```text
pending_observation
  -> ready
  -> cleared
  -> expired
  -> failed
```

清理策略：

- Akane 可通过工具清理。
- 用户也可显式说“刚才那张不用看了”触发清理。
- 系统可按 TTL 自动过期。
- 超过 session 上限时，最旧附件转为 expired。

建议 V1 TTL：

```text
ready 附件默认保留 30-60 分钟或 8-12 轮。
pending 失败重试 1-2 次。
failed 附件只保留简短错误提示，不反复注入。
```

---

## 10. 与现有系统关系

### 与 Gift / Artifact

```text
Gift / Artifact = 被 Akane 收下、归档、成为世界的一部分。
Attachment Inbox = 当前聊天里给 Akane 看/读的临时材料。
```

默认不互通。

只有明确保存意图时才桥接。

### 与 Vision Observation

图片附件复用视觉模型能力，但观察结果写入 Attachment Inbox。

如果同一图片重复发送，可以按文件 hash 复用观察卡，避免重复 API 调用。

### 与 Memory

Attachment Inbox 不直接写入 raw message，也不直接写入 summary。

对话仍然正常进入 MemoryStore。

---

## 11. 实施顺序建议

### P2.1 数据层

- 新增 `attachment_inbox_items`
- 新增 `AttachmentInboxService`
- 支持 add / mark_ready / list_active / clear / expire

### P2.2 Prompt 注入

- `build_prompt_context()`
- 详细挂载最近 1-2 个
- 轻量索引其它 active 附件
- pending 状态只提示正在处理

### P2.3 QQ 图片 ingest

- NapCat 图片下载到临时附件目录
- 写入 pending
- 异步调用视觉模型
- 生成 detail_json 后写 ready

### P2.4 QQ 工具

- `inspect_attachment`
- `retry_attachment`
- `clear_attachment_focus`
- 挂到 `qq_tools`

### P2.5 文本文件读取

优先支持：

- `.txt`
- `.md`
- `.json`
- `.csv`
- `.log`
- 常见源码文本

后续再考虑：

- `.pdf`
- `.docx`
- 压缩包

---

## 12. 当前结论

Attachment Focus Inbox 是 Akane 的临时工作记忆层。

它让 Akane 能看图、读文件、围绕材料连续聊几轮，又不会让视觉 JSON 或文件正文污染长期记忆。

这套层级应作为 QQ 附件、桌宠拖拽文件、桌面截图感知、Web “只看看”能力的共同底座。
