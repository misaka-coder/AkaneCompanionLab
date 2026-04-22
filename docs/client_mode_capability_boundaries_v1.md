# Akane 多客户端能力边界 V1

本文档记录 Akane 在 Web 场景端、QQ 端、未来桌宠端之间的能力分工、工具挂载原则与身份边界。

核心目标：同一个 Akane 共享长期记忆和核心世界，但不同客户端只暴露符合当前场景的能力。不要把所有端都做成同一个 Galgame，也不要把所有工具无差别塞给模型。

---

## 1. 核心共识

```text
同一个 Akane，共享长期记忆与核心世界；
不同客户端，只挂载符合该场景的提示词、工具和输出字段。
```

这意味着：

- `profile_user_id` 决定“这个 Akane 属于谁”。
- `session_id` 决定“当前对话现场在哪里”。
- `client_mode` 决定“这一轮 Akane 身处哪种客户端身体”。
- `prompt_profile` 决定“这一轮 Akane 看到哪些动态上下文”。
- `output_adapter` 决定“这一轮输出如何交给客户端渲染”。
- `tool_pack` 决定“这一轮 Akane 能调用哪些能力”。

---

## 2. 三端定位

### 2.1 Web / 场景端：玩法拓展

Web 场景端承担“共同搭建小世界”的体验。

适合承载：

- 场景切换
- BGM 切换
- 服装 / 表情 / 立绘资源
- 礼物流转
- 相册、曲库、场景集合
- 视觉观察卡
- 世界资产命名、归档、迁移、删除

默认语义：

```text
拖入图片/音乐 = 递给 Akane 的世界资产候选
```

所以 Web 端可以默认进入 pending / gift / artifact 流程。

### 2.2 QQ 端：功能拓展

QQ 端承担“现实聊天窗口里的轻量陪伴和功能助手”体验。

适合承载：

- 随手聊天
- 看图
- 读文件
- 摘要、解释、提取重点
- 提醒
- 轻量问答
- 群聊中的点名回复

默认语义：

```text
发图 = 给 Akane 看
发文件 = 给 Akane 读 / 帮忙处理
```

QQ 端不应该默认进入礼物系统。只有用户明确表达“收下、记住、当成资源、作为你的东西”时，才转入 artifact / gift 流程。

文件阅读、格式转换、生成文件与生成物回流的完整边界见 `docs/file_processing_generated_artifacts_v1.md`。简要原则是：用户发来的文件进入 Attachment Inbox，Akane 生成的文件进入 GeneratedFileStore，再通过工作台投影给她继续修改或发送。

### 2.3 桌宠端：权限拓展

桌宠端承担“陪在桌面上，知道用户正在经历什么”的体验。

适合承载：

- 前台窗口 / 应用类别感知
- 用户正在写代码、看视频、玩游戏、空闲等活动提示
- 触摸 / 双击 / 拖拽交互
- 轻量快捷操作
- 本地文件上下文
- 桌面级提醒与陪伴

默认语义：

```text
桌面感知 = 本轮瞬时上下文
```

桌面活动不应默认写入长期记忆。只有用户确认、对话吸收、重复形成习惯，或通过记忆晋升门判断为高价值事件时，才进入长期层。

---

## 3. 工具包分层

工具不应全局无差别挂载，应按 `client_mode` 动态组装。

### 3.1 `base_tools`

所有端都可能使用的基础能力。

候选工具：

- `set_reminder`
- `list_reminders`
- `cancel_reminder`
- `manage_persona`
- 未来的 `search_memory`

特点：

- 不依赖具体 UI。
- 不强绑定 Web 场景。
- 结果进入统一 Memory / Persona / Reminder 层。

### 3.2 `web_scene_tools`

只在 Web / 场景端挂载。

候选工具：

- `manage_gift`
- `check_inventory`
- `manage_artifact`
- 场景 / BGM / 服装资源相关管理工具

特点：

- 服务于小世界搭建。
- 可以操作相册、曲库、场景集合、立绘候选。
- 可以影响 Web 的视觉与音频投影。

### 3.3 `qq_tools`

只在 QQ 端挂载。

候选工具：

- `fetch_media_from_url`
- `inspect_attachment`
- `sync_attachment_workspace`
- `read_attachment_section`
- `retry_attachment`
- `clear_attachment_focus`
- `compose_file`
- `revise_generated_file`
- `apply_style_to_existing_file`
- `inspect_media_info`
- `separate_audio_stems`
- `clean_voice_track`
- `transcribe_media`
- `prepare_voice_dataset`
- `convert_media_file`
- `inspect_generated_file`
- `send_file`
- `manage_generated_file`

特点：

- 默认只生成本轮或短期上下文。
- 不默认写入礼物 pending。
- 不默认改变场景、BGM、立绘。
- `fetch_media_from_url` 可把公开可访问的视频/音频链接先下载进临时附件工作台，再沿用现有附件、媒体、生成物链路继续处理；它不直接总结、不直接转写，只负责把素材拿到手。
- `compose_file` 可把附件或当前内容整理成 `gen_001` 这类生成文件，并由当前客户端尝试发送。
- `revise_generated_file` 可基于 `gen_001` 生成 `gen_002` 修改版，不覆盖旧文件。
- `apply_style_to_existing_file` 可对已有 `docx/xlsx` 附件或生成文件做样式加工，不要求模型重吐全文。
- 普通音频/视频附件会尽量自动生成轻量媒体卡；`inspect_media_info` 用于复查媒体规格、查看生成物规格，或在精细转换前确认真实参数。
- `separate_audio_stems` 可把普通音频/带音轨视频拆成人声与伴奏，结果进入 `gen_001` 生成物体系。
- `clean_voice_track` 可对说话录音、分离后人声轨做降噪/去混响/人声聚焦，结果继续作为生成物供后续裁剪、转码或切片。
- `transcribe_media` 可把一个或多个音频/视频/生成媒体转成 `md/txt/srt/vtt/json` 文字稿或字幕；结果进入生成区，可继续总结、提炼重点、整理会议纪要或做字幕后处理。
- `prepare_voice_dataset` 可把一个或多个音频/人声生成物整理成语音训练素材 zip，内含 `manifest.json` 与 `slices/*.wav`，并在提示词里暴露过短、过长、音量偏低等片段文件名。
- `convert_media_file` 可把普通非加密音频/视频音轨转换成 `mp3/wav/flac/m4a/aac/ogg/opus`，也能做单文件截取、响度标准化、整体音量增减、淡入淡出和调速；结果进入 `gen_001` 生成物体系；不处理 `kgm/ncm/qmc` 这类平台加密或专有缓存格式。
- `inspect_generated_file` 可回看 `gen_001` 的正文、开头、结尾、摘要，也可查看训练集 zip 的文件列表、`manifest.json`、`README.md`；它只读不修改。
- `send_file` 可把已有临时附件 `file_001/img_001/audio_001` 或生成物 `gen_001/gen_002` 交给当前客户端发送，不重新生成、不修改。
- `manage_generated_file` 可归档、删除或彻底清理 `gen_001/gen_002` 这类生成物；它不处理用户原始附件。
- `compose_file` / `revise_generated_file` 可通过声明式 `formatting` 做常见 `docx/xlsx` 样式，例如表头加粗、列/行标色、关键词高亮。
- 可在用户明确要求时桥接到 `manage_artifact` 或长期记忆工具。

### 3.4 `desktop_tools`

只在桌宠端挂载。

候选工具：

- `fetch_media_from_url`
- `observe_desktop`
- `inspect_active_window`
- `read_selected_file`
- `inspect_attachment`
- `sync_attachment_workspace`
- `read_attachment_section`
- `compose_file`
- `revise_generated_file`
- `apply_style_to_existing_file`
- `inspect_media_info`
- `separate_audio_stems`
- `clean_voice_track`
- `transcribe_media`
- `prepare_voice_dataset`
- `convert_media_file`
- `inspect_generated_file`
- `send_file`
- `manage_generated_file`
- `open_shortcut`
- `desktop_idle_check`

特点：

- 权限敏感。
- 默认低权限、只读优先。
- 感知结果默认瞬时，不污染长期记忆。

---

## 4. QQ 附件原则

QQ 端不照搬 Web 礼物系统。

### 图片

默认流程：

```text
QQ 图片
  -> 下载临时文件
  -> 视觉模型生成观察卡
  -> 注入本轮 Prompt
  -> 本轮对话结束后可丢弃或短期缓存
```

只有当用户明确说：

- “这张图你收下”
- “把它记下来”
- “当成你的新场景”
- “归档到相册”
- “以后也能拿出来看”

才转入 artifact / gift 流程。

### 文件

默认流程：

```text
QQ 文件
  -> 下载临时文件
  -> 按类型读取文本 / 元数据 / 摘要
  -> 注入本轮 Prompt
  -> 默认不成为礼物
```

适合优先支持：

- `.txt`
- `.md`
- `.json`
- `.csv`
- `.pdf`
- 未来再考虑 `.docx`、代码压缩包等。

---

## 5. 身份与记忆边界

### 5.1 个人主创模式

本地使用时：

```text
Web -> profile_user_id = master
QQ 主人私聊 -> profile_user_id = master
```

这样 Web、QQ、手机浏览器都属于同一个 Akane。

### 5.2 云端测试模式

公开云端测试时，不应让所有 Web 用户都归属 `master`。

未来需要加入：

```env
WEB_IDENTITY_MODE=owner|browser|invite
WEB_OWNER_PROFILE_USER_ID=master
```

模式含义：

- `owner`：个人主创模式，所有 Web 归属 `master`。
- `browser`：每个浏览器生成独立匿名 profile，适合临时试玩。
- `invite`：用户输入邀请码 / 测试码，映射到固定 profile，适合小范围闭测。

### 5.3 共享与隔离规则

```text
按 profile 共享：
- 长期摘要记忆
- 语义长期记忆
- 已归档 / 已内化资源
- 人设卡库

按 session 隔离：
- 原始聊天流水
- 当前对话现场
- pending 礼物焦点
- 当前激活人设卡
- 当前客户端运行状态
```

这条规则可以概括为：

```text
发生在哪，用 session_id；
属于谁，用 profile_user_id。
```

---

## 6. 新模式接入流程

新增客户端模式时，不应修改主流程到处加 `if client_mode == ...`。

推荐流程：

1. 定义 `client_mode`。
2. 定义该模式的 `client_capabilities`。
3. 新增或复用 `prompt_profile`。
4. 新增或复用 `output_schema`。
5. 实现 `output_adapter`。
6. 绑定该模式的 `tool_pack`。
7. 明确该模式的瞬时上下文与长期记忆边界。
8. 补契约测试。

理想状态下，新增模式像增加插件，而不是改造全项目。

---

## 7. 防打架护栏

### 护栏 1：工具按模式挂载

不要让 QQ 看到 Web 场景专用工具，也不要让 Web 默认看到桌面权限工具。

### 护栏 2：默认低权限

看图、读文件、桌面观察默认只进入本轮上下文，不自动写长期记忆或礼物库。

### 护栏 3：工具结果归一化

不同端的工具结果最终应落到统一层：

- `MemoryStore`
- `Gift / Artifact`
- `Persona`
- `Reminder`
- `RuntimeState`

### 护栏 4：输出字段按端裁剪

QQ 不输出场景和 BGM；桌宠不强制输出 Web 背景；Web 不强制携带桌面窗口上下文。

### 护栏 5：临时上下文不可污染长期记忆

桌面窗口标题、QQ 文件内容、图片观察卡都不应无条件写入长期记忆。

---

## 8. 建议实施顺序

### P0：身份模式开关

实现：

```env
WEB_IDENTITY_MODE=owner|browser|invite
WEB_OWNER_PROFILE_USER_ID=master
```

目的：

- 本地保持 `master` 统一体验。
- 云端测试时可以切换到隔离身份。

当前落地：

- `owner` 已可用：Web 统一归属 `WEB_OWNER_PROFILE_USER_ID`，默认 `master`。
- `browser` 已可用：每个浏览器生成独立匿名 profile。
- `invite` 已可用：访问 URL 带 `?invite=测试码` 时映射为 `invite_测试码`；无邀请码时退回 `browser`。
- `/app-config` 会下发 `web_identity_mode` 与 `web_owner_profile_user_id`。
- 前端会在生成身份前先读取 `/app-config`，避免云端模式下误生成 `master`。

### P1：工具包注册表

将工具挂载从“全局工具列表”升级为：

```text
base_tools + mode_specific_tools(client_mode)
```

目的：

- 避免不同客户端工具互相干扰。
- 降低模型选择噪音。

当前落地：

- `base` 工具包已包含提醒与人设卡工具。
- `web_scene` 工具包已包含 NPC、礼物、手边库存、相册 / 曲库 / 场景资产管理工具。
- `qq_text` 目前只挂载 `base`，不会看到或执行 Web 礼物类工具。
- `desktop_pet` 目前只挂载 `base`，桌面感知工具留待客户端出现后加入。
- Prompt 注入和后端工具执行都使用同一套模式过滤，避免模型幻觉调用未挂载工具。

### P2：QQ 附件感知

实现 QQ 图片 / 文件的轻量处理：

- 图片默认 observe。
- 文件默认 read / summarize。
- 明确表达“收下”时才桥接 artifact。

设计细节见 `docs/attachment_focus_inbox_v1.md`。

核心原则：

- 附件进入 Attachment Focus Inbox，不直接写入用户消息。
- 最近 1-2 个附件详细注入，其余附件只注入轻量索引。
- 视觉 / 文件解析可异步写 SQLite，不阻塞 Akane 回复。
- Akane 可通过工具展开较早附件或移除已聊完附件。

### P3：桌宠权限感知

在桌宠客户端出现后再实现：

- 桌面活动感知。
- 前台应用分类。
- 触摸 / 双击事件。
- 权限提示与隐私护栏。

---

## 9. 当前结论

Akane 不应该追求所有端功能完全一致，而应该追求：

```text
同一个角色核心，在不同客户端长出不同自然能力。
```

Web 端像家和小世界，QQ 端像随身聊天窗口，桌宠端像桌面上的陪伴存在。

这种分工能让 Akane 扩展新模式时不打架，也能让每个端都保留自己的体验优势。
