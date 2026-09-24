# QQ Workshop Capabilities V1

这份文档给主创和调试者看，不直接塞进 Akane 的日常提示词。运行时提示词只保留 `capability_registry.py` 里的短路标，避免正常对话 token 压力变大。

## 一眼自检

推荐先跑轻量自检：

```powershell
powershell -ExecutionPolicy Bypass -File .\run_qq_workshop_self_check.ps1
```

它会检查：

- `ffmpeg` / `ffprobe`
- `yt-dlp`
- `faster-whisper`
- `demucs`
- `deepfilternet`
- `tests.quick_regression_suite`

缺少媒体/AI 依赖时会显示 `WARN`，方便定位是哪一段能力不可用；快速回归失败会显示 `FAIL`。

如果只想检查依赖，不跑快速回归：

```powershell
powershell -ExecutionPolicy Bypass -File .\run_qq_workshop_self_check.ps1 -SkipQuickRegression
```

## 能力路网

### 1. 文件处理

适合测试：

- 用户上传 `file_` / `img_` / `audio_` / `video_` 附件后，附件默认进入 Focus。
- 图片默认进入视觉观察卡，不退化成只有摘要。
- 文档在预算内展示原始内容；需要精读时用 `read_attachment_section` 读取原文件。
- `compose_file` 可从对话、附件或生成文件生成 `md/txt/docx/xlsx/pdf/json/csv/html`。
- `revise_generated_file` 生成新版，不覆盖旧 `gen_`。
- `apply_style_to_existing_file` 给现有文档做样式加工。

适合直接做：

- “把这个文件发我”
- “转成 md/docx”
- “把这段整理成文件”
- “看一下第二页/第 20-40 行”

不适合后台工坊：

- 一次简单发送、简单转换、简单读取。

### 2. 音视频处理

适合测试：

- `inspect_media_info` 查看精确规格。
- `convert_media_file` 转码、抽音轨、截取、调音量、去头尾静音、改采样率/声道。
- `separate_audio_stems` 拆人声和伴奏。
- `clean_voice_track` 降噪、去混响、去回声、聚焦人声。
- `transcribe_media` 转写音频/视频。
- `prepare_voice_dataset` 切片并打包训练素材。

关键路标：

- 用户只要原文件时，只发送原文件，不额外转写、提音频或净化。
- 用户要视频里的声音时，优先 `convert_media_file` 提音轨。
- 用户要人声/伴奏时，优先 `separate_audio_stems`。
- 用户要更干净的人声时，可对人声结果再用 `clean_voice_track`。
- 用户要训练素材时，可组合提音频、人声分离、降噪、切片打包。

### 3. 视频链接处理

适合测试：

- 公开视频链接下载到附件区：`fetch_media_from_url`。
- 下载后直接发回原视频：`send_file`。
- 下载后继续提音频、转写、总结、做字幕或准备训练素材。

边界：

- 不处理登录、付费、会员、DRM、私密链接。
- 不把整条播放列表/合集当成一个普通文件处理。
- 如果用户只是要“原视频/原音频/链接里的文件”，下载后直接发送，不多做处理。

### 4. 转写、字幕、总结

视频总结不是固定流程，但常见路线是：

1. `fetch_media_from_url` 或使用用户上传的视频。
2. `transcribe_media` 生成 `md/txt/json` 转写稿。
3. `compose_file` 基于转写稿生成总结、纪要或 Markdown 文件。
4. 用户要文件时用 `send_file` 精确发送目标 `gen_`。

字幕任务常见路线：

1. 使用视频/音频来源。
2. `transcribe_media` 输出 `srt` 或 `vtt`。
3. 用户要字幕文件时发送对应 `gen_`。

注意：

- `transcribe_media` 只负责转写，不负责总结。
- 总结、纪要、提纲、Markdown 应由 `compose_file` 完成。
- 有多个候选文件时必须确认，不静默猜。

### 5. 训练素材准备

适合后台或多步测试：

- 视频 -> 提音频 -> 分离人声 -> 降噪 -> 切片 -> 打包 zip。
- 多个音频/视频来源合并成训练批次。
- 对切片质量问题进行二次筛选。

关键产物：

- `slices/*.wav`
- `manifest.json`
- `README.md`
- 训练素材 zip 的 `gen_` 记录

环境依赖：

- `ffmpeg`
- `demucs`，用于人声分离
- `deepfilternet`，用于 AI 人声净化

### 6. 后台任务工坊

适合委派给后台：

- 多步任务，需要多个工具连续执行。
- 任务耗时较长，前台还要继续陪用户说话。
- 批量生成多个产物。
- 需要任务工作区记录步骤、产物、阻塞问题和交接状态。

适合直接工具执行：

- 发送一个已有附件或 `gen_`。
- 生成一个简单文件。
- 转换一个明确格式。
- 做一次简单转写。

前台/后台边界：

- 用户主对话对象始终是 Akane。
- worker 不直接对用户说话。
- worker 不绕过 Akane 发送最终结果。
- worker 的产物回流生成区/任务区，由 Akane 统一说明、确认、发送、清理。
- 任务工作区只是白板和交接记录，不替代附件区或生成文件区。

### 7. 清理与发送

发送：

- 统一优先使用 `send_file`。
- 精确 handle 永远优先，例如 `gen_033` 只能发送 `gen_033`。
- 模糊名称命中多个候选时必须确认。

清理：

- `clear_attachment_focus` 只收起临时附件 Focus，不删除原始记录。
- `manage_generated_file` 只管理生成文件，不处理用户原始附件。
- `manage_task_workspace cleanup` 只清理任务工作区，不替代附件区/生成区清理。

## 推荐验收脚本

日常快速检查：

```powershell
powershell -ExecutionPolicy Bypass -File .\run_qq_workshop_self_check.ps1
```

只跑资源可见性快速回归：

```powershell
python -m unittest tests.quick_regression_suite
```

完整回归：

```powershell
python -m unittest discover tests
```

## 手动测试清单

- 上传图片，确认 Akane 第一眼能看到视觉观察卡。
- 上传文档，询问某一段内容，再让 Akane 生成整理文件。
- 上传音频，询问时长、采样率、声道、码率。
- 视频链接 -> 下载 -> 直接发回原视频。
- 视频链接 -> 转写 -> 总结成 Markdown。
- 视频/音频 -> 生成 `srt/vtt` 字幕。
- 音频/视频 -> 分离人声/伴奏 -> 净化人声 -> 准备训练素材。
- 同时存在多个相似附件或 `gen_` 时，用模糊名称要求发送，确认 Akane 会问清楚。
- 委派长任务后，问“好了没/现在到哪了”，确认 Akane 基于任务工作区回答。
