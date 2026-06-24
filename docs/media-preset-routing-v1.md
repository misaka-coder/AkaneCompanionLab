# 媒体任务预设路由 V1

## 概述

媒体任务预设路由是 Akane Companion Lab 在 V1 阶段对媒体处理能力的结构化映射：**把 LLM 能理解的用户意图（"生成字幕""提取音频"）直接对接到底层工具 + 参数模板，让模型不需要猜测该用什么工具、填什么参数。**

本路由不是独立工具，而是运行时注入的一段提示文本。它仅在当前会话确实有媒体上下文（附件/生成文件）并激活 `media_workbench` 模块时出现在 agent prompt 中。

---

## 1. 媒体工具清单

| 工具 | 职责 |
|---|---|
| `fetch_media_from_url` | 从公开 URL 下载音频/视频到工作台 |
| `inspect_media_info` | 读取媒体时长、编码、采样率、码率、分辨率等规格 |
| `convert_media_file` | 格式转换、提取音频、压缩、截取、音量调整、变速、淡入淡出 |
| `transcribe_media` | 语音转文字：字幕（srt/vtt）或转写稿（md/txt） |
| `clean_voice_track` | 人声净化：降噪、去混响、去回声 |
| `separate_audio_stems` | 人声伴奏分离（vocals / instrumental） |
| `prepare_voice_dataset` | GPT-SoVITS / RVC 训练素材切片打包 |
| `send_file` | 交付已有文件到客户端（不做任何转换） |

### 1.1 关键参数参考

**convert_media_file**
| 参数 | 类型 | 用途 |
|---|---|---|
| `source_id` | str | 工作台材料或生成文件 handle |
| `output_format` | str | mp3 / wav / flac / m4a / aac / ogg / opus |
| `output_title` | str | 输出文件名（不含扩展名） |
| `start_time` / `end_time` | str | 截取区间，格式 `"00:00:35"` |
| `normalize_volume` | bool | 音量标准化（忽大忽小 → 统一响度） |
| `volume_gain_db` | float | 音量增减：正数放大，负数压低 |
| `trim_silence` | bool | 去掉头尾静音 |
| `fade_in_seconds` / `fade_out_seconds` | float | 淡入淡出 |
| `speed_ratio` | float | 播放速度（如 1.25） |
| `bitrate` | str | 目标码率（如 `"192k"`） |
| `sample_rate` | int | 目标采样率 |
| `channels` | int | 声道数（1=单声道，2=立体声） |

用户没指定的参数不要硬填。

**transcribe_media**
| 参数 | 类型 | 用途 |
|---|---|---|
| `source_ids` | list[str] | 支持批量来源 |
| `output_format` | str | md / txt / srt / vtt / json |
| `language` | str | zh / en / auto |
| `with_timestamps` | bool | 保留时间戳（默认 true） |
| `merge_outputs` | bool | 批量时是否合并为一份（默认 true） |
| `model_size` | str | small / medium / large-v3 |
| `vad_filter` | bool | 语音活动检测过滤静音段（默认 true） |

转写完成后如需总结/纪要，应基于转写稿调用 `compose_file`，不在转写步骤做总结。

**separate_audio_stems**
| 参数 | 类型 | 用途 |
|---|---|---|
| `source_id` | str | 工作台材料或生成文件 handle |
| `mode` | str | 当前仅支持 `vocals_instrumental` |
| `output_format` | str | wav / flac / mp3 |

分离后如需精修，再调用 `convert_media_file`。

**clean_voice_track**
| 参数 | 类型 | 用途 |
|---|---|---|
| `source_id` | str | 工作台材料或生成文件 handle |
| `mode` | str | denoise / dereverb / deecho / voice_focus |
| `quality` | str | auto（优先 AI）/ ai / basic（ffmpeg 轻净化） |
| `post_filter` | bool | 仅 AI 模式有意义，杂音重时启用 |

**prepare_voice_dataset**
| 参数 | 类型 | 用途 |
|---|---|---|
| `source_ids` | list[str] | 支持批量来源 |
| `profile` | str | gpt_sovits / rvc / archive |
| `target_sr` | int | 目标采样率 |
| `min_clip_seconds` / `max_clip_seconds` | float | 切片长度限制 |
| `silence_threshold_db` | float | 静音判定阈值 |
| `clean_first` | bool | 切片前做轻量降噪 |
| `normalize_volume` | bool | 切片前做音量标准化 |

---

## 2. 用户意图 → 工具映射

```
生成字幕                    → transcribe_media output_format=srt/vtt
转写文字稿/会议纪要前置       → transcribe_media output_format=md/txt
提取视频音频                → convert_media_file output_format=mp3/wav
压缩音频/减小体积            → convert_media_file bitrate（如 128k/192k）
截取片段                    → convert_media_file start_time/end_time
声音忽大忽小/调正常/更舒服   → convert_media_file normalize_volume=true
声音太小/放大一点            → convert_media_file volume_gain_db（正数，如 3 或 6）
声音太大/太吵/压低一点       → convert_media_file volume_gain_db（负数，如 -3 或 -6）
人声降噪/去混响/去回声       → clean_voice_track
人声伴奏分离                → separate_audio_stems
训练素材切片打包             → prepare_voice_dataset
只要原文件不处理             → send_file，不要转写/转码/净化
```

### 2.1 常见组合链路

- **视频总结**：`fetch_media_from_url`（如需要）→ `transcribe_media` → `compose_file`
- **字幕任务**：`fetch_media_from_url`（如需要）→ `transcribe_media output_format=srt/vtt`
- **训练素材**：`convert_media_file` 提音频 → `separate_audio_stems` 分离人声 → `clean_voice_track` 降噪 → `prepare_voice_dataset` 切片打包
- **纯交付**：`send_file`（原文件），不做任何额外处理

---

## 3. 客户端边界

| 客户端 | media_workbench | media_preset_routing | 说明 |
|---|---|---|---|
| QQ | ✓ | ✓ | 完整媒体工作台 + 路由提示 |
| 桌宠 | ✓ | ✓ | 完整媒体工作台 + 路由提示；`send_file` 可携带 `delivery_action`（open / reveal / save_desktop / copy_path） |
| Web scene | ✗ | ✗ | 不挂载任何媒体工具，即使有文件状态也不显示路由 |
| 其他模式 | ✗ | ✗ | 模块不适用 |

### 3.1 路由注入条件

- 当前模式为 `QQ_TEXT` 或 `DESKTOP_PET`
- `CapabilitySnapshot` 中 `has_media_attachment` 或 `has_media_generated_file` 为 true（触发 `_has_media_context`）
- `media_workbench` 出现在 `CapabilitySelection.module_names` 中

路由块在 prompt 中的位置：在「可用能力概览」和「桌宠文件交付」（如有）之后，「当前可调用工具」之前。

---

## 4. 设计原则

1. **不新增底层工具。** 所有媒体功能都已存在；路由只是提示层面的意图映射，不会引入新的可调用工具。

2. **用户没指定参数时不要硬填。** 路由给出的是 "如果用户说 X → 用工具 Y 加参数 Z" 的提示，但 `convert_media_file` 的 `start_time`、`normalize_volume`、`volume_gain_db`、`trim_silence`、`bitrate`、`sample_rate`、`channels`、`fade_*`、`speed_ratio` 等全是可选参数，用户没有明确说要做什么就不要填。

3. **只处理已有的工作台材料或生成文件。** 所有媒体工具（除 `fetch_media_from_url`）都通过 `source_id` / `source_ids` 引用已就绪的工作台附件或生成文件；不会自己去找外部文件。

4. **工具生成结果进入生成文件工作台。** `convert_media_file`、`transcribe_media`、`separate_audio_stems`、`clean_voice_track`、`prepare_voice_dataset` 的输出自动注册为 `gen_xxx` 格式的生成文件，可通过 `inspect_generated_file` / `manage_generated_file` 管理，或通过 `send_file` 交付。

---

## 5. 测试

```bash
# 快速回归（覆盖多端媒体路由）
python -m tests.quick_regression_suite

# 媒体路由专项测试
python -m unittest tests.test_vn_extensions.EngineExtensionTests.test_media_preset_routing_appears_in_prompt_for_chat_clients
python -m unittest tests.test_vn_extensions.EngineExtensionTests.test_web_scene_prompt_excludes_media_preset_routing

# 全部 VN 扩展测试
python -m unittest tests.test_vn_extensions
```

---

## 6. 变更历史

| 日期 | 变更 |
|---|---|
| 2026-05-01 | V1 初始文档。路由内容从 light_hint 迁出为独立 prompt 块；拆分简短能力概览与详细预设路由两层。 |
