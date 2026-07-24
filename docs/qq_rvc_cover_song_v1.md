# Akane RVC 自动翻唱 V1

本文档是 `cover_song` 的实现与续作基线。上下文压缩后，继续工作前先阅读本文档、`docs/audio_separation_tool_v1.md`、`companion_v01/generated_files.py`、`companion_v01/generated_files_media.py` 和 `companion_v01/tool_runtime.py`。

## 1. V1 目标

用户在 QQ、桌面或 Web 会话中提供普通音频/视频后，Akane 可以通过原生工具完成：

```text
工作台音频/视频
  -> 主唱与伴奏分离
  -> 固定 RVC 模型转换主唱音色
  -> FFmpeg 对齐、混音、限幅和编码
  -> GeneratedFileStore
  -> QQ 语音 / 文件 / 两者
```

同一源音频、目标模型与参数再次请求时，应命中持久缓存，不重复占用 GPU。so-vits-svc 后续作为第二个 Voice Conversion Provider 接入，不改变工具协议、缓存和交付层。

## 2. 对参考设计的修正

参考设计方向正确，但在 Akane 中不另建一套附件、生成物或 jobs 权威实现：

- 输入继续使用 `AttachmentInboxService` 和 `GeneratedFileService` 的 `audio_*/file_*/gen_*`。
- 输出继续进入 `GeneratedFileStore`，获得 `gen_*`。
- 临时分离轨、转换轨只存在于受管工作目录，不进入 prompt、memcore 或普通日志。
- 持久缓存是生成流程的加速层，不替代 SQLite 生成文件记录。
- V1 不强制主唱/和声三轨。先稳定完成 vocals/instrumental 两轨，未来再添加高级 Separation Provider。
- V1 不安装新的 GUI。调用本机 RVC WebUI 暴露的本地 Gradio API；服务端全局模型切换由进程内锁串行保护。

## 3. 当前本机执行器

```text
RVC WebUI: http://127.0.0.1:7899
RVC root:  F:\MyTablePet\RVC
FFmpeg:    PATH 中的 ffmpeg/ffprobe
```

已验证的 RVC API：

- `infer_change_voice`：Gradio fn index 5
- `infer_convert`：Gradio fn index 2
- `uvr_convert`：Gradio fn index 6

不要长期依赖固定 fn index。Provider 启动时读取 `/config`，按 `api_name` 解析实际 index 和组件顺序；若协议不匹配，应结构化失败。

## 4. 原生工具协议

工具名：`cover_song`

建议参数：

```json
{
  "type": "cover_song",
  "source_id": "audio_001|file_001|gen_001",
  "song_title": "可选歌曲名，也用于缓存检索",
  "artist": "可选原唱",
  "voice_model": "auto 或 RVC 模型名",
  "pitch_shift": 0,
  "index_rate": 0.6,
  "filter_radius": 3,
  "rms_mix_rate": 0.25,
  "protect": 0.33,
  "vocal_gain_db": 0.0,
  "instrumental_gain_db": -1.0,
  "output_format": "mp3|flac|wav",
  "delivery": "auto|voice|file|both|none",
  "force_rebuild": false
}
```

规则：

- `source_id` 存在时先按内容指纹查缓存，未命中才推理。
- `source_id` 缺失但有 `song_title` 时，只查询已经完成的翻唱缓存；不能凭歌名伪造源音频。
- `voice_model=auto` 使用宿主配置的默认音色。
- 升降调由模型结合用户意图决定；没有证据时保持 0，不按性别强制升降八度。
- `delivery=auto`：QQ 默认语音，其他客户端保留普通生成文件交付；过长或语音失败时仍保留可发送文件。
- 技术参数都有安全范围，但不硬编码“模型只能怎样组合”。

## 5. Provider 边界

```text
CoverSongService
  -> SeparationProvider
  -> VoiceConversionProvider
  -> AudioMixProvider (FFmpeg)
```

V1 实现：

- `RvcWebUiProvider.separate_vocals()`：使用 RVC 内置 UVR5，默认 `HP5_only_main_vocal`。
- `RvcWebUiProvider.convert_voice()`：使用 RVC v2 + RMVPE。
- `CoverSongService._mix_tracks()`：FFmpeg `amix normalize=0` + limiter。

云端 Bot 使用 `LOCAL_MEDIA_EXECUTOR_BASE_URL` 时，Provider 切换为
`LocalRvcExecutorProvider`：输入音频通过受限 multipart 上传到本机 loopback 媒体宿主，
分轨和转换结果以 ZIP/WAV 字节返回。`CoverSongService`、缓存、混音、生成文件记录和 QQ
交付仍在云端，不传递或假定两端共享绝对路径。

当前 RVC 的单次 `infer_convert` 已在内部处理长音频切段：超过窗口阈值后，它会在约 60 秒目标点附近寻找局部最低能量位置，在该位置切段推理并按顺序拼回。宿主不要再把每个静音段拆成多次 `infer_convert` 请求；当前 RVC 会在每次请求中重新读取 FAISS index，外层重复切段会放大 index I/O、请求开销和音轨累计对齐误差。若未来要升级为严格的静音区间切点，应在 RVC 单次推理内部完成，并保持 padding、总时长和顺序拼接语义。

本机 RVC 运行时带有两项热路径优化：

- `get_vc` 记录当前实际加载的模型。重复选择同一模型时直接复用权重、HubERT、Pipeline 和已预热的 RMVPE；真正切换到其他模型时仍执行完整加载并更新当前模型状态。
- 当前 Pipeline 按 index 的规范路径、文件大小和纳秒修改时间缓存一个 FAISS index；文件发生变化或模型切换后自动失效。`IndexIVFFlat` 使用 direct map 仅重建本次搜索命中的向量，不再为 240 MB index 常驻一份完整 `reconstruct_n` 副本。

这两项属于外部 RVC runtime 的本机补丁，不是 Akane Provider 私自假定全局模型状态。重新安装或整体覆盖 RVC runtime 后需要重新核对；宿主即使没有该补丁仍保持正确，只是会恢复为每次模型选择和 index 重载的慢路径。

V2 可替换：

- Separation：audio-separator / MelBand-RoFormer / BS-RoFormer / Demucs。
- Voice conversion：so-vits-svc / Seed-VC。

## 6. 缓存

缓存键至少包含：

```text
源文件 SHA-256
+ voice provider id/version
+ 模型名及本地模型 size/mtime 指纹
+ index 路径及 size/mtime 指纹
+ 分离模型
+ pitch/index/filter/rms/protect
+ vocal/instrumental gain
+ 输出格式
+ pipeline version
```

缓存位于 Akane 受管工作区，不暴露绝对路径。缓存索引使用原子写入。生成文件被用户清理时不能删除共享缓存本体；命中缓存后复制或硬链接到当前会话 Outputs，再建立新的 `gen_*` 记录。

缓存分两层：

- 分轨缓存：仅由源文件内容指纹、Separation Provider 和分离模型决定，保存 `vocals.wav` / `instrumental.wav`。同一来源更换 RVC 音色、升降调、检索比例、混音增益或最终格式时复用分轨，不重复跑 UVR。
- 成品缓存：继续包含音色模型指纹、RVC 参数、混音参数和输出格式；完全相同的请求直接复用成品。

`force_rebuild=true` 同时绕过成品缓存和分轨缓存。缓存文件优先使用同卷硬链接写入，无法硬链接时退回原子复制。

非缓存推理会返回安全的 `processing` 摘要，包括宿主侧 source hash、模型指纹、解码、分离、RVC、混音和缓存写入耗时，以及 RVC 返回的 feature extraction、pitch extraction、voice synthesis 耗时。该摘要只保留布尔值和秒数，不保留 RVC index 路径、工作目录或服务器临时路径。

## 7. 安全与正确性

- 只允许 localhost RVC endpoint，除非宿主未来显式配置可信远端。
- 不处理 `kgm/ncm/qmc` 等加密缓存格式。
- 限制输入大小、歌曲时长、单次输出数量和并发。
- RVC 返回的服务器临时路径只在宿主内部读取，不进入 prompt、memcore、tool trace 或公开结果。
- 任何一步失败都返回明确 stage/reason，不假成功，不发送半成品。
- 缓存命中前验证文件存在、大小非零，必要时用 ffprobe 验证音频。
- RVC WebUI 使用全局当前模型状态，选择模型到推理完成之间必须持有同一把锁。

## 8. QQ 交付

生成事件携带内部字段：

```json
{
  "type": "generated_file_ready",
  "send_to_user": true,
  "delivery_mode": "voice|file|both",
  "delivery_scope": "cover_song"
}
```

QQ Gateway：

- `voice` 调用 OneBot `record`。
- `file` 调用群文件/私聊文件上传。
- `both` 依次尝试语音和文件，任一失败都保留生成文件。
- 只有 `cover_song` 产物能通过该 scope 使用专用音频交付，普通生成音频保持现有文件行为。

## 9. 后台任务

V1 工具本身是确定性重任务，并标记为 background capability。它可以被前台直接调用，也应加入 `media_agent`/`speech_agent` 的受限工具集合，以便通过现有 `delegate_task` 后台执行。

注意：当前 `TaskWorkerService` 的完成回调尚未接 QQ 主动交付。V1 先确保直接原生工具闭环可靠；后续单独补齐“后台完成后主动推送”切片，不能在本切片里伪装成已经异步推送。

## 10. 验收

- 能列出并解析本机 RVC 模型。
- 能从普通音频得到 vocals/instrumental。
- 能选择 RVC 模型并用 RMVPE 转换人声。
- 能混音并生成有效 WAV/FLAC/MP3。
- 结果进入 GeneratedFileStore，prompt 只看到安全摘要。
- 第二次相同请求命中缓存，不调用分离/转换。
- QQ `voice/file/both` 路由正确，失败保留产物。
- 无绝对路径、RVC 临时路径或模型路径泄漏到模型可见文本。
- 相关单元测试、ruff、`git diff --check` 通过。

## 11. 当前实施顺序

1. `RvcWebUiProvider` 与健康检查。
2. `CoverSongService`、缓存与 FFmpeg 混音。
3. `cover_song` 原生 ToolHandler 和动态 schema。
4. Engine、能力目录、后台 worker allowlist。
5. QQ 专用音频交付。
6. 配置、测试、本机短音频 smoke。
7. 后续独立切片：后台完成主动推送、RoFormer Provider、so-vits-svc Provider。
