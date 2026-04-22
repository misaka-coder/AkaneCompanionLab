# Akane 文件处理与生成物回流蓝图 V1

本文档记录 Akane 在 QQ、未来桌宠、Web 下载端中处理文件阅读、格式转换、内容重组、生成文件与二次修改的架构共识。

核心目标：让 Akane 能清楚地知道“用户发来的文件是什么”“自己刚生成的文件是什么”“当前要处理哪一个”，同时避免附件、长期记忆、生成产物混成一团。

---

## 1. 核心原则

```text
用户发来的文件，进入 Attachment Inbox。
Akane 生成的文件，进入 GeneratedFileStore。
当前要看的材料，通过 Workspace Projection 注入 Prompt。
对话才进入记忆；文件本体和解析卡默认不进入长期记忆。
```

这四句话是文件系统的底层边界。

- `Attachment Inbox`：用户发来的临时图片、文件、音频，handle 如 `img_001`、`file_002`。
- `GeneratedFileStore`：Akane 生成或转换出的文件，handle 如 `gen_001`、`gen_002`。
- `Workspace Projection`：把当前可查看、可修改的输入文件和生成文件投影给 Akane。
- `MemoryStore`：只存真实对话、摘要、长期事实，不默认存附件全文或生成文件全文。

---

## 2. 为什么不能只做“任意格式转换”

表面上用户会说：

```text
把这个 txt 转成 Word 发我。
把这两个表格合并一下。
把刚才那份 Word 第二段删掉，重新发我一份。
```

但工程上它们不是同一种能力。

### 2.1 Format Convert

格式转换，内容基本不变，只换文件容器。

示例：

- `txt -> docx`
- `md -> txt`
- `md -> docx`
- `csv -> xlsx`
- `xlsx -> csv`
- `json <-> yaml`
- `toml -> json`

特点：

- 后端白名单控制。
- 基本不用 LLM。
- 稳定、可测、低风险。

### 2.2 Transform Export

内容重组导出，内容会被整理、摘要、改写、结构化。

示例：

- 把 txt 整理成表格 docx。
- 把计划整理成 Excel。
- 把聊天记录整理成周报。
- 把多个文件合并成一个 Markdown。
- 把文章改成演讲稿并生成 docx。

特点：

- 需要 LLM 参与内容重组。
- LLM 只输出中间结构，不直接生成二进制文件。
- Python 后端负责生成 `.docx`、`.xlsx`、`.md` 等文件。

### 2.3 Freeform Artifact

自由生成文件，不一定依赖用户上传文件。

示例：

- 帮我做一个学习计划表。
- 帮我写一份 README。
- 帮我生成一个 CSV 模板。

特点：

- 输入可能来自对话、记忆、附件或用户直接要求。
- 输出仍进入 `GeneratedFileStore`。

---

## 3. 文件阅读 V1

文件阅读的 V1 不追求“模型总结全文”，而是先做本地结构化预览。

```text
QQ 文件进入 Attachment Inbox
  -> 下载 / 复制到本地
  -> 根据扩展名选择本地 parser
  -> 生成 File Observation Card
  -> 注入临时附件焦点
  -> Akane 需要更细时再 inspect / read section
```

### 3.1 当前已适合本地解析的文件

- `.txt`
- `.md`
- `.json`
- `.toml`
- `.yaml`
- `.csv`
- `.py`
- `.js`
- `.ts`
- `.html`
- `.css`
- `.xml`
- `.sql`
- 常见代码 / 配置 / 日志文本文件

文件卡示例：

```json
{
  "summary_title": "学习计划.md",
  "short_hint": "Markdown 文档，包含标题：目标、时间安排、每日任务。",
  "detail": {
    "file_kind": "markdown",
    "headings": ["目标", "时间安排", "每日任务"],
    "line_count": 42,
    "text_preview": "前几段文本..."
  }
}
```

### 3.2 Office / PDF 基础读取

V1 可以通过本地库读取基础内容，不需要调用 API 做格式转换。

- `.pdf`：`pypdf` 或 `pdfplumber`，抽页数、文本层、前几页预览。
- `.docx`：`python-docx`，抽段落、标题、表格文字。
- `.xlsx`：`openpyxl`，抽 sheet 名、表头、前几行。
- `.pptx`：`python-pptx`，未来可抽每页文本。

如果 PDF 是扫描件、图片型文档或没有文本层，V1 应明确返回：

```text
这个 PDF 像扫描件，目前只能看到页数，暂时读不到文字。
```

不要让 Akane 假装已经读懂。

---

## 4. GeneratedFileStore

Akane 生成或转换出的文件不应回写为普通用户附件，而应进入生成文件区。

建议字段：

```text
generated_id: gen_001 / UUID
profile_user_id
session_id
source_ids: ["file_001", "gen_001"]
output_format: docx | xlsx | md | txt | json | yaml
display_name
storage_relpath
content_card_json
version_of: gen_001 | ""
created_by_tool: convert_attachment | compose_file | revise_generated_file
delivery_status: pending | sent | failed
created_at / updated_at
```

`content_card_json` 是给 Akane 二次修改用的结构化内容卡，不一定是完整二进制内容。

---

## 5. 生成物回流：Workspace Projection

采纳“产出物自动回流”的思想，但不把生成物伪装成普通 Inbox 附件。

正确做法：

```text
GeneratedFileStore
  -> Workspace Projection
  -> Prompt 注入
```

Prompt 中可以这样呈现：

```text
【你刚生成的文件】
- gen_001：学习计划.docx
  摘要：包含三段正文和一个任务表，刚刚已发送给用户。
  可继续修改、重发或另存为新版本。
```

这样用户说：

```text
大体不错，但帮我把第二段删掉，加一句总结，重新发我一份。
```

Akane 可以直接引用 `gen_001` 修改，而不是从原始 txt 重做。

---

## 6. 工具蓝图

### 6.1 已有工具

- `inspect_attachment`
  - 查看用户发来的临时附件。
- `sync_attachment_workspace`
  - 声明式整理当前附件工作台；新发来的附件会尽量自动进入 Focus，这个工具主要用于收起、切换或重排重点材料。
- `clear_attachment_focus`
  - 清理用户发来的临时附件。
- `retry_attachment`
  - 重试失败的临时附件。
- `convert_media_file`
  - 普通非加密媒体转码，输出进入生成物体系。

### 6.2 当前工具：`read_attachment_section`

大文件按需展开。

```json
{
  "type": "read_attachment_section",
  "target": "file_001",
  "section": "第3页"
}
```

当前实现状态：

- 已接入 `read_attachment_section`。
- 如果本地原始附件仍在，会优先按原文件重新展开：文本/代码按行号，Word 按段落或表格，Excel 按 sheet/行范围，PDF 按页范围。
- 如果本地原始附件不可用，则回退到当前已解析的附件卡，展开文本预览、行范围、表格预览、Excel sheet 预览。
- 如果附件没有文本层、是扫描 PDF、图片型文件或未支持格式，会返回清晰失败原因。
- 为避免把大文件一次性塞爆上下文，单次展开仍有安全上限；需要更多内容时应按页、行号、sheet 分批展开。

### 6.3 未来工具：`convert_attachment`

只做白名单格式转换。

```json
{
  "type": "convert_attachment",
  "target": "file_001",
  "output_format": "docx",
  "title": "整理后的笔记"
}
```

后端必须检查支持矩阵，不支持就拒绝。

### 6.4 当前工具：`compose_file`

做内容重组、整理和新文件生成。

```json
{
  "type": "compose_file",
  "sources": ["file_001", "file_002"],
  "task": "整理成学习计划表",
  "output_format": "xlsx",
  "title": "考研学习计划",
  "table_rows": [["任务", "状态"], ["数学", "重点"]],
  "formatting": {
    "header": {"bold": true},
    "columns": [{"match_header": "任务", "font_color": "red"}],
    "highlights": [{"text": "重点", "fill_color": "yellow"}]
  }
}
```

LLM 输出中间结构，后端生成文件。

当前实现状态：

- 已接入 `compose_file` 工具。
- 工具面向 Akane 暴露高层语义：`source_ids`、`task`、`output_format`、`output_title`、`content_markdown`、`table_rows`、`formatting`。
- 后端负责把内容渲染为 `md/txt/docx/xlsx/pdf/json/csv/html`，其中 `docx/xlsx/pdf` 依赖 `python-docx/openpyxl/reportlab`。
- 如果 Akane 没有传入 `content_markdown` 或 `table_rows`，后端会尽量从原始附件读取更大的安全片段作为来源材料，而不是只使用短预览。
- 如果用户只是要求忠实转换/导出原附件或已生成文件，例如 TXT 转 PDF/Word、把 `gen_001` 另存为 PDF，Akane 应留空 `content_markdown/table_rows`，让后端读取来源正文；后端不会把 `任务/来源摘录/用途` 这类工作台元信息写进最终正文。
- 后端已增加防呆：若忠实转换任务里 Akane 误把源文件开头预览当成最终正文传入，且能确认它只是原文件前缀，会自动改用更完整的原始附件材料。
- 这仍不是“无限全文塞入模型”：超长文件应先通过 `read_attachment_section` 分段展开、确认，再生成。
- `formatting` 是声明式样式层，当前白名单支持表头加粗、按表头定位列、按行/单元格定位、按文本命中高亮；未知字段会被忽略。
- `xlsx` 已支持表头、列、行、单元格、文字命中和简单条件行样式；`docx` 已支持段落/表格里的关键词、表头、列、行和单元格基础样式；`pdf` 暂不承诺精细样式。
- 生成物进入 `GeneratedFileStore`，获得 `gen_001` 这类 handle。
- QQ 端会根据 `generated_file_ready` 事件尝试上传生成文件；上传失败不会删除生成物。

### 6.5 当前工具：`revise_generated_file`

修改 Akane 已生成的文件。

```json
{
  "type": "revise_generated_file",
  "target": "gen_001",
  "instruction": "删掉第二段，加一句总结",
  "output_format": "docx",
  "formatting": {
    "highlights": [{"text": "总结", "fill_color": "yellow"}]
  }
}
```

原则：不要覆盖旧文件，默认生成新版本 `gen_002`。

当前实现状态：

- 已接入 `revise_generated_file`。
- 用户说“把刚才那份第二段删掉、加一句总结”时，Akane 应根据 `gen_001` 的工作台预览先整理出修改后的最终正文，再调用工具。
- 工具不会自己解释“删第二段”这种语义；它只负责版本化、渲染、存储和发送事件。
- 如果用户要求标红、高亮、加粗，Akane 应把明确的样式意图写入 `formatting`，不要让后端猜“哪里是重点”。
- 旧版本不会被覆盖。

### 6.6 当前工具：`send_file`

把已有本地文件交给当前客户端发送。它统一处理两类来源：

- 临时附件：`file_001` / `img_001` / `audio_001`
- 生成物：`gen_001` / `gen_002`

```json
{
  "type": "send_file",
  "targets": ["file_001", "gen_002"]
}
```

当前实现状态：

- 已接入 `send_file`。
- 用于“把刚才那个视频发我”“把原文件和总结稿都发我”“再发一次 gen_002”这类请求。
- 只发送已有文件，不重新生成、不修改、不转码、不清理。
- 支持批量混合发送临时附件和生成物。
- QQ 端会把 `file_ready` 事件走同一条上传文件通道。
- 如果来源是生成物，会标记 delivery 为 `pending`，发送后由客户端回写 sent/failed。

### 6.6b 兼容工具：`send_generated_file`

把生成文件交给当前客户端。

```json
{
  "type": "send_generated_file",
  "target": "gen_002"
}
```

不同客户端使用不同 delivery adapter：

- QQ：OneBot `send_file` / 上传文件接口。
- Web：返回下载链接。
- Desktop：打开文件、复制路径或显示在本地文件夹。

当前实现状态：

- 已接入 `send_generated_file`。
- 保留为兼容旧调用；新提示词优先使用 `send_file`。
- 用于“再发一次”“把刚才那个文件发我”“发送 gen_002”这类请求。
- 只重发已有生成物，不重新生成、不修改内容。
- 如果本地文件本体已经丢失，会清晰失败；生成记录不会被删除。

### 6.6c 当前工具：`inspect_generated_file`

只读查看 Akane 已生成的文件，解决“生成后还想继续检查 / 修改 / 总结”的回看问题。

```json
{
  "type": "inspect_generated_file",
  "target": "gen_001",
  "section": "content",
  "max_chars": 12000
}
```

可用 `section`：

- `content` / `head` / `tail`：查看正文、开头或结尾。
- `summary`：查看生成物摘要、格式、大小、内容卡片。
- `file_list`：查看 zip 生成物内的文件列表。
- `manifest` / `file:manifest.json`：查看训练集 zip 的 manifest。
- `file:README.md`：查看 zip 内指定文本成员。

当前实现状态：

- 已接入 `inspect_generated_file`。
- 只读取生成物，不发送、不修改、不删除文件。
- `md/txt/json/csv/html/srt/vtt` 会直接从磁盘读取。
- `docx/xlsx/pdf` 会在安装了 `python-docx` / `openpyxl` / `pypdf` 时尽量抽取文本。
- `zip` 支持文件列表、manifest、README 和其他文本成员；二进制成员只返回大小说明。
- 适合 Akane 在修改 `gen_001` 前确认内容，也适合查看 `prepare_voice_dataset` 生成的 `manifest.json`。

### 6.7 当前工具：`apply_style_to_existing_file`

给已有 `docx/xlsx` 附件或生成文件套样式，不要求 Akane 重新输出全文。

```json
{
  "type": "apply_style_to_existing_file",
  "target": "file_001",
  "target_type": "attachment",
  "instruction": "低于60分整行标红，姓名列加粗",
  "output_title": "成绩表标注版",
  "formatting": {
    "columns": [{"match_header": "姓名", "bold": true}],
    "row_rules": [{"where": {"column": "分数", "lt": 60}, "font_color": "red"}],
    "highlights": [{"text": "重点", "fill_color": "yellow"}]
  },
  "send_to_user": true
}
```

当前实现状态：

- 已接入 `apply_style_to_existing_file`。
- 支持来源：临时附件 `file_001` / 已生成文件 `gen_001`。
- 支持输出：`xlsx` / `docx` 的样式加工版；原文件不会被覆盖。
- `xlsx` 复用声明式 `formatting`，可按表头定位列、按行号/条件行、按关键词和单元格套样式。
- `docx` 支持段落、表格、关键词、行列和单元格的基础样式。
- 不适合正文增删改；这类需求仍应走 `revise_generated_file` 或 `compose_file`。
- 生成物进入 `GeneratedFileStore`，获得新的 `gen_00x`，并通过 `generated_file_ready` 交给当前客户端发送。

### 6.8 当前工具：`inspect_media_info`

媒体规格读取工具，面向“这个视频多长”“有没有音轨”“采样率是多少”“分辨率和帧率是多少”这类轻量问题。

```json
{
  "type": "inspect_media_info",
  "source_id": "audio_001"
}
```

当前实现状态：

- 已接入 `inspect_media_info`。
- 底层依赖本机 `ffprobe`。
- 不生成文件，只返回媒体规格给 Akane 继续对话。
- 普通音频/视频附件在接收时会自动生成轻量媒体卡，包含时长、音轨、视频规格等低成本元数据；因此日常不必为了看基础信息多调用一次工具。
- 这个工具主要用于复查、读取 `gen_001` 这类生成物规格，或在精细转换前确认真实参数。

### 6.9 当前工具：`convert_media_file`

普通媒体转换工具，面向“压缩音频、转常见格式、从视频提取音频”这类轻量需求。

```json
{
  "type": "convert_media_file",
  "source_id": "audio_001",
  "output_format": "mp3",
  "output_title": "压缩版",
  "start_time": "00:00:35",
  "end_time": "00:01:20",
  "normalize_volume": true,
  "volume_gain_db": 6,
  "trim_silence": true,
  "fade_in_seconds": 2,
  "fade_out_seconds": 3,
  "speed_ratio": 1.25,
  "bitrate": "192k",
  "sample_rate": 44100,
  "channels": 2,
  "send_to_user": true
}
```

当前实现状态：

- 已接入 `convert_media_file`。
- 底层依赖本机 `ffmpeg`。
- 支持输出：`mp3` / `wav` / `flac` / `m4a` / `aac` / `ogg` / `opus`。
- 支持普通音频转码、压缩体积、统一采样率/声道；`mp4` / `mov` / `mkv` / `webm` 等视频源如果 `ffmpeg` 能读取，也可提取音轨。
- 支持单文件精修：`start_time` / `end_time` 截取片段，`normalize_volume` 响度标准化，`volume_gain_db` 整体音量增减，`trim_silence` 自动去掉头尾静音，`fade_in_seconds` / `fade_out_seconds` 淡入淡出，`speed_ratio` 调速。
- `start_time` / `end_time` 可写 `120s`、`2:00`、`00:02:00`、`1分30秒` 这类常见说法，后端会标准化。
- `start_time` / `end_time` / `normalize_volume` / `volume_gain_db` / `trim_silence` / `fade_in_seconds` / `fade_out_seconds` / `speed_ratio` / `bitrate` / `sample_rate` / `channels` 都是可选项；用户没指定时 Akane 不需要硬填。
- `normalize_volume` 用于“声音忽大忽小、调正常、更舒服”的响度标准化；`volume_gain_db` 用于“放大一点/压低一点”的整体增益，正数变大、负数变小，后端限制在 `-24dB` 到 `+24dB`。
- `trim_silence=true` 用于“把前后空白切掉/去掉开头结尾静音”；当前只裁头尾静音，不会主动删除中间停顿。
- 如果用户要求语音识别、统一语音规格或更小体积，可用 `wav + sample_rate=16000 + channels=1` 或指定 `bitrate`；音乐文件通常保持原采样率和声道更自然。
- 这个工具只处理单个来源文件；多音频拼接以后单独设计 `concat_media_files`，不要塞进当前工具。
- 转换结果进入 `GeneratedFileStore`，获得新的 `gen_00x`，并通过 `generated_file_ready` 发送。
- `kgm` / `ncm` / `qmc` 等平台加密或专有缓存格式只做识别和说明，不做解密或绕过保护。

### 6.10 当前工具：`manage_generated_file`

管理 Akane 已生成的文件，不处理用户原始附件。

```json
{
  "type": "manage_generated_file",
  "action": "archive",
  "targets": ["gen_001", "gen_002"],
  "reason": "旧版本不用了"
}
```

### 6.11 当前工具：`separate_audio_stems`

高阶音频分离工具，面向“把歌曲或带音轨视频拆成人声和伴奏”这类 AI 音频工作流。

```json
{
  "type": "separate_audio_stems",
  "source_id": "audio_001",
  "mode": "vocals_instrumental",
  "output_format": "wav",
  "output_title": "副歌练习分轨",
  "send_to_user": true
}
```

当前实现状态：

- 已接入 `separate_audio_stems`。
- 底层优先调用本地 `demucs`；如果系统里找不到可调用的 `demucs`，会自然提示用户环境未配置完成。
- 当前只支持 `vocals_instrumental`，也就是输出两份结果：
  - `vocals`（人声）
  - `instrumental`（伴奏）
- 支持普通音频附件，也支持带音轨视频；视频来源会先用 `ffmpeg` 抽取音轨，再交给分离模型。
- 输出结果进入 `GeneratedFileStore`，通常会一次生成两份新的 `gen_00x` 文件，并通过 `generated_file_ready` 发送。
- `output_format` 当前建议使用 `wav`，也支持 `flac` / `mp3`；若不是 `wav`，会再交给 `ffmpeg` 做结果转码。
- 这个工具只负责“拆轨”，不负责后续精修；如果分离后还要裁剪、统一采样率、去头尾静音、调音量或转格式，应继续对生成结果调用 `convert_media_file`。
- `kgm` / `ncm` / `qmc` 等平台加密或专有缓存格式只做识别和说明，不做解密或绕过保护。

### 6.12 当前工具：`clean_voice_track`

高阶语音净化工具，面向“把说话声 / 干声再弄干净一点”这类 AI 语音增强工作流。

```json
{
  "type": "clean_voice_track",
  "source_id": "gen_001",
  "mode": "denoise",
  "quality": "auto",
  "output_format": "wav",
  "output_title": "主播口播净化版",
  "post_filter": false,
  "send_to_user": true
}
```

当前实现状态：

- 已接入 `clean_voice_track`。
- 适用于说话录音、播客人声、直播片段、分离后的人声轨，不用于普通媒体转码。
- `mode` 当前支持：
  - `denoise`
  - `dereverb`
  - `deecho`
  - `voice_focus`
- `quality=auto` 时，会优先尝试本地 `DeepFilterNet`；如果环境未配置完成，则退回 `ffmpeg` 轻净化。
- `quality=ai` 表示只接受 AI 净化；若本地没有可用 `DeepFilterNet`，会明确失败并提示补环境。
- `quality=basic` 表示直接走 `ffmpeg` 轻净化。
- 输出结果进入 `GeneratedFileStore`，获得新的 `gen_00x` 文件，并通过 `generated_file_ready` 发送。
- 这个工具只负责“净化”，不负责后续裁剪、去头尾静音、统一采样率、调音量或转格式；这些仍应继续交给 `convert_media_file`。

### 6.13 当前工具：`transcribe_media`

音频/视频转写工具，面向“给音频配文字稿 / 生成字幕 / 先转写再总结内容”等工作流。

```json
{
  "type": "transcribe_media",
  "source_ids": ["audio_001", "file_002", "gen_003"],
  "output_format": "md",
  "output_title": "视频内容转写稿",
  "language": "zh",
  "with_timestamps": true,
  "merge_outputs": true,
  "model_size": "small",
  "vad_filter": true,
  "send_to_user": true
}
```

当前实现状态：

- 已接入 `transcribe_media`。
- 支持多个来源一起转写，来源可以是音频附件、带音轨视频附件，或 `gen_001` 这类生成媒体。
- 底层用 `ffmpeg` 抽取/标准化音轨，再交给本地 `faster-whisper`。
- 输出格式支持：
  - `md`
  - `txt`
  - `srt`
  - `vtt`
  - `json`
- `merge_outputs=true` 时，多个来源合并成一份总转写稿。
- `merge_outputs=false` 时，每个来源各生成一份转写稿。
- 如果生成 `srt/vtt`，可用于字幕工作流；如果后续要总结、提炼重点、生成会议纪要，推荐使用 `md` 并保留时间戳。
- 这个工具只负责“转写”，不负责总结。转写完成后，如果用户要总结视频/音频内容，应基于转写稿继续调用 `compose_file`。
- 若音频很吵、歌曲伴奏重或人声不清晰，推荐先使用 `separate_audio_stems` / `clean_voice_track`，再对结果调用本工具。

### 6.14 当前工具：`prepare_voice_dataset`

语音训练素材准备工具，面向 GPT-SoVITS / RVC / 归档整理等工作流。

```json
{
  "type": "prepare_voice_dataset",
  "source_ids": ["gen_001", "audio_002"],
  "profile": "gpt_sovits",
  "output_title": "Akane_voice_dataset_001",
  "target_sr": 44100,
  "min_clip_seconds": 3,
  "max_clip_seconds": 12,
  "silence_threshold_db": -40,
  "min_silence_ms": 300,
  "max_silence_kept_ms": 300,
  "clean_first": false,
  "normalize_volume": false,
  "send_to_user": true
}
```

当前实现状态：

- 已接入 `prepare_voice_dataset`。
- 支持多个来源一起处理，来源可以是临时音频/视频附件，也可以是 `gen_001` 这类生成音频。
- 底层用 `ffmpeg` 统一成训练用 PCM wav，再用内置 RMS/静音切片逻辑生成片段。
- 最终生成一个 zip 批次，包含：
  - `slices/*.wav`
  - `manifest.json`
  - `README.md`
- `manifest.json` 会记录每段的文件名、来源、开始/结束时间、时长、采样率、RMS/峰值、问题标签。
- 问题标签会暴露给 Akane，方便二次协作：
  - `too_short`
  - `too_long`
  - `low_volume`
  - `clipping`
  - `empty_or_failed`
- 这个工具不负责高阶人声分离和 AI 净化；需要更干净素材时，先使用 `separate_audio_stems` / `clean_voice_track`，再对结果调用本工具。

### 6.15 当前工具：`manage_generated_file`

管理 Akane 已生成的文件，不处理用户原始附件。

- `archive`：从生成文件工作台隐藏，磁盘文件保留。
- `delete`：从生成文件工作台隐藏，并删除本地生成文件。
- `purge`：删除本地生成文件，同时清空生成物内容卡片，只保留一条很薄的记录。
- 支持批量 `targets`，也支持 `latest` / `all`。
- 不会删除用户原始附件；临时附件清理仍使用 `clear_attachment_focus`。
- 成功后返回 `generated_files_managed` 事件，方便未来客户端同步 UI。

---

## 7. 支持转换矩阵

不要承诺“任意格式转换”。V1 应使用白名单。

建议初始支持：

```text
txt  -> md, docx
md   -> txt, docx
csv  -> xlsx
xlsx -> csv
json -> yaml
yaml -> json
toml -> json
```

未来谨慎支持：

```text
docx -> md
docx -> txt
pptx -> txt / md
pdf  -> txt / md
```

不建议 V1 承诺：

```text
扫描 PDF -> 完美 Word
复杂 PDF -> 保留排版 Word
图片 -> 可编辑表格
PPT -> 高还原 Word
```

---

## 8. 极端情况与预案

### 8.1 同名文件

用户可能连续发两个 `计划.docx`。

解决：

```text
file_001（第1个文件）计划.docx
file_002（第2个文件）计划.docx
```

工具调用优先使用 handle，不只靠文件名。

### 8.2 文件版本

用户说“刚才那个错了，我重新发一版”。

解决：

- 新文件生成新 handle。
- 不自动覆盖旧文件。
- Akane 可询问是否清理旧版。

### 8.3 多文件对比 / 合并

用户说“对比这两份计划”“把三个表格合并”。

解决：

- 用 `sync_attachment_workspace` 选中 sources。
- 未来 `compose_file` 接收多个 source id。

### 8.4 大文件读不完

默认只读结构卡和前几段。

解决：

- V1：提示“只看到了前几段”。
- V2：`read_attachment_section` 按页、章节、sheet 展开。

### 8.5 扫描 PDF / 图片型文档

本地 parser 读不到文本层。

解决：

- 返回清晰失败原因。
- 未来可接 OCR 或视觉模型。

### 8.6 敏感信息

文件可能包含身份证、手机号、账号、隐私表格。

解决：

- V1 不做复杂合规引擎，但不主动传播敏感信息。
- 产品化后加入安全提示与发送确认。

### 8.7 生成文件版本管理

用户让 Akane 修改刚生成的文件。

解决：

- 默认生成新版本。
- `gen_001 -> gen_002`
- 不覆盖旧文件，除非用户明确要求。

### 8.8 QQ 发送失败

文件生成成功，不等于发送成功。

解决：

```text
generated_status: ready
delivery_status: sent | failed
```

Akane 可以说：“文件我做好了，但 QQ 发送失败了，要不要换个方式给你？”

### 8.9 长任务

大 PDF、多个文件合并、复杂导出可能耗时。

解决：

- 后台 job。
- Akane 先确认“我开始整理了”。
- 完成后发送，或下轮用户询问时报告状态。

### 8.10 修改原文件

用户说“把原文件第二段删掉”。

解决：

- 不原地改用户上传原件。
- 生成 `generated_修改版`。
- 保护原文件不可变。

---

## 9. 记忆边界

默认不进入长期记忆：

- 附件全文
- 文件解析卡
- 生成文件全文
- 格式转换产物

可以进入长期记忆：

- 用户围绕文件表达的重要偏好。
- 用户明确要求记住的事实。
- Akane 与用户围绕某个产物形成的真实对话。

示例：

```text
用户：以后我的学习计划都按这种表格格式来。
```

这可以进入长期偏好。

```text
用户：把这个文件转成 Word。
```

这只是任务，不默认长期保存。

---

## 10. 实施路线

### P1：文件阅读增强

- 增强文本 / Markdown / JSON / CSV / 代码文件卡。
- 加 PDF / DOCX / XLSX 基础读取。
- 对扫描 PDF 给出明确不可读提示。

### P2：GeneratedFileStore

- 生成文件表。
- `gen_001` handle。
- 生成物工作台投影。

状态：已完成第一版。

### P3：Format Convert

- 支持白名单转换矩阵。
- 生成结果进入 GeneratedFileStore。

### P4：Client Delivery

- QQ 发回文件。
- Web 下载链接。
- Desktop 打开 / 展示本地路径。

状态：QQ 已有第一版上传尝试；Web / Desktop 仍待接入。

### P5：Transform Export

- `compose_file`。
- LLM 输出结构化中间层。
- 后端生成 docx / xlsx / md。

状态：已完成第一版 `compose_file`；已能从原始附件读取更大的安全材料片段。超大文件的分段规划、复杂结构化导出仍待加强。

### P5.5：Declarative Formatting

- `formatting` 字段。
- 由 Akane 把“姓名列标红”“重点高亮”“第 2 行黄底”翻译为明确规则。
- Python 后端负责在 `xlsx/docx` 中执行样式。

状态：已完成第一版。已覆盖中小型生成文件的常见样式需求；并已新增 `apply_style_to_existing_file`，用于对已有 `docx/xlsx` 文件只套样式、不重吐全文。

### P6：Revise Generated File

- 基于 `gen_001` 二次修改。
- 默认版本化输出。
- 不覆盖旧文件。

状态：已完成第一版。复杂差异对比、从完整生成物全文恢复编辑、以及局部补丁式修改仍待后续加强。

---

## 11. 最终闭环

```text
用户发文件
  -> Attachment Inbox: file_001
  -> File Observation Card
  -> Akane 读取 / 对话 / 选择处理目标
  -> convert_attachment 或 compose_file
  -> GeneratedFileStore: gen_001
  -> ClientDeliveryAdapter 发回 QQ / Web / Desktop
  -> Workspace Projection 投影 gen_001
  -> 用户要求二次修改
  -> revise_generated_file
  -> GeneratedFileStore: gen_002
  -> 用户要求再发一次
  -> send_file
```

这条链路让 Akane 既能处理用户给的文件，也能继续处理自己刚生成的成果，同时保持附件、生成物、长期记忆三者边界清晰。
