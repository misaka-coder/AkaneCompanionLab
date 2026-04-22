# Akane 音频分离工具 V1

本文档定义 Akane 在 QQ / 桌面未来模式中处理“人声 / 伴奏分离”这类高阶音频任务的 V1 架构。

目标不是把 UVR 桌面程序本体塞进项目，而是让 Akane 拥有一项稳定、可调用、可清理、可复用的音频分离能力。

---

## 1. 结论先行

```text
普通音视频处理 -> 继续走 ffmpeg
高阶人声伴奏分离 -> 单独走 AI 音频分离工具层
```

也就是说：

- `convert_media_file`
  - 负责转码、抽音轨、裁剪、调速、响度、淡入淡出、去头尾静音
- `separate_audio_stems`
  - 负责人声 / 伴奏分离、未来更多 stems 分轨

这两个能力不能混在同一个工具里。

原因很简单：

- `ffmpeg` 是传统媒体处理
- `stem separation` 是模型推理
- 运行时成本、错误模式、依赖、耗时、并发策略都完全不同

---

## 2. 为什么不直接集成 UVR5 GUI

用户感知上常说“用 UVR5 分离”，但工程上更适合集成的是：

- `Demucs`
- `MDX / UVR 背后的模型推理脚本`
- 或者一层本地 CLI / Python 推理服务

不建议第一版直接把 UVR5 桌面 GUI 程序当依赖嵌进去。

### 2.1 不推荐把 GUI 本体塞进项目的原因

- GUI 程序与后端服务耦合重
- 自动化调用不优雅
- 错误处理困难
- 升级和部署不可控
- 后续不利于桌宠 / Web / QQ 统一工具层

### 2.2 推荐的做法

```text
Akane Tool
  -> AudioSeparationService
      -> 本地模型 CLI / 推理脚本
          -> 输出 vocals / instrumental
              -> GeneratedFileStore
```

这和现在的 `ffmpeg` 工具思路是一致的：主服务负责编排，重任务交给外部子进程或专门服务执行。

---

## 3. V1 模型路线

### 3.1 首选：Demucs

V1 建议优先走 `Demucs`。

原因：

- CLI 友好
- 社区成熟
- 文档和案例多
- 适合先跑通项目闭环
- 对“先做 usable，而不是先卷最强音质”这个阶段很合适

### 3.2 后续升级位

未来如需更高质量，可再考虑：

- `MDX-Net`
- `UVR/MDX 系列模型`
- `BS-RoFormer`
- 其他更适合中文流行歌曲 / 复杂混音的模型

V1 不把模型名暴露给 Akane 作为高频参数，先由后端默认选择一套稳定模型即可。

---

## 4. 工具边界

### 4.1 新工具名

建议新增：

```json
{
  "type": "separate_audio_stems"
}
```

而不是复用 `convert_media_file`。

### 4.2 为什么要独立

如果把“分离人声伴奏”也塞进 `convert_media_file`，会立刻出现这些混乱：

- 模型推理和 ffmpeg 变成一个工具的两种运行模式
- 提示词 instruction 膨胀
- 错误提示混杂
- 并发控制不清晰
- 以后扩成 4 stems / 6 stems 时语义更乱

独立工具更清楚：

- `convert_media_file` = 单文件媒体打磨
- `separate_audio_stems` = 模型分轨

---

## 5. V1 工具协议

建议 V1 参数尽量克制：

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

### 5.1 字段说明

- `source_id`
  - `audio_001 | file_001 | gen_001`
  - 支持原始附件和生成物
- `mode`
  - V1 只先支持 `vocals_instrumental`
- `output_format`
  - V1 建议支持：`wav | flac | mp3`
  - 其中默认推荐 `wav`
- `output_title`
  - 可选
- `send_to_user`
  - 是否自动发回当前客户端

### 5.2 V1 不做的内容

- 不要求 Akane 自己选模型名
- 不做多源混合输入
- 不做 4 stems / 6 stems 暴露
- 不做批量任务
- 不在一轮里拆很多个文件

---

## 6. 与现有暂存区 / 生成区的关系

这部分和项目现有架构是高度契合的。

### 6.1 输入

用户把音频或带音轨视频发给 Akane：

```text
Attachment Inbox
  - audio_001
  - file_002
```

Akane 已经能看到：

- 文件名
- 媒体基础规格
- 需要时可继续 inspect

### 6.2 处理

Akane 调用：

```json
{
  "type": "separate_audio_stems",
  "source_id": "audio_001",
  "mode": "vocals_instrumental",
  "output_format": "wav",
  "send_to_user": true
}
```

### 6.3 输出

后端生成两个文件进入 `GeneratedFileStore`：

- `gen_00x`：人声 `vocals.wav`
- `gen_00y`：伴奏 `instrumental.wav`

必要时还可生成第三个摘要卡：

- 分离来源
- 所用模式
- 输出格式
- 是否已发送给用户

### 6.4 后续流转

这两个生成物此后可以继续：

- 发回用户
- 再转码
- 再裁剪
- 做统一采样率 / 单声道
- 做语音训练前清洗
- 被清理工具删除

这就是它最适合接进当前项目的原因：

> 它不是一个旁门左道的新系统，只是又一种“生成文件”的能力。

---

## 7. 运行时建议

### 7.1 不要在主线程直接跑模型

建议新增：

```text
AudioSeparationService
```

由它负责：

- 校验来源文件
- 选择模型/脚本
- 调子进程
- 收集输出文件
- 写入 `GeneratedFileStore`

主引擎只负责工具编排，不直接在主线程里 import 模型推理库跑重任务。

### 7.2 V1 采用子进程调用

推荐：

```text
subprocess -> demucs / separation script
```

优点：

- 错误隔离
- 模型依赖隔离
- GPU 占用更容易控
- 更容易和当前 `ffmpeg` / 生成区工作流并排管理

---

## 8. 本地环境要求

要让 `separate_audio_stems` 真正可用，仅有项目代码还不够，还需要本地存在一套可执行的 `Demucs + PyTorch` 环境。

### 8.1 最低要求

- `ffmpeg` 已可用
- `demucs` 已安装
- `torch` 可导入

### 8.2 推荐要求（NVIDIA / Windows）

- `nvidia-smi` 可正常看到显卡
- 当前 Python 环境安装的是 **CUDA 版 PyTorch**
- `python -m demucs.separate --help` 可以正常运行

### 8.3 为什么只装 demucs 还不够

如果当前环境里的 `torch` 是 CPU 版，即使机器本身有 NVIDIA 显卡，Demucs 依然会退回 CPU 推理，速度会明显变慢。

所以真正要检查的是：

```powershell
python -c "import torch; print(torch.__version__); print(torch.cuda.is_available())"
```

如果返回 `False`，说明要先把当前环境的 PyTorch 改成官方 CUDA 版本。

### 8.4 推荐安装流程

1. 卸掉现有 CPU 版 PyTorch
2. 按 PyTorch 官方安装矩阵安装 Windows + Pip + CUDA 对应版本
3. 安装 `demucs`

参考：

- PyTorch Start Locally: https://pytorch.org/get-started/locally/
- Demucs 官方仓库: https://github.com/facebookresearch/demucs
- Windows 环境出问题时更容易定位
- 与当前 `ffmpeg` 工具风格一致

### 7.3 并发必须限制

音频分离比普通转码重很多。

V1 必须做：

- 单独的 worker 数限制
- 至少 1 条分离任务队列
- 避免多个大文件同时吃 GPU

建议默认：

- `BACKGROUND_AUDIO_AI_WORKERS = 1`

先稳，再扩。

---

## 8. 输入类型边界

### 8.1 支持的输入

- 普通音频：
  - `mp3`
  - `wav`
  - `flac`
  - `m4a`
  - `aac`
  - `ogg`
  - `opus`
- 带音轨视频：
  - `mp4`
  - `mov`
  - `mkv`
  - `webm`

对于视频，先用现有 `ffmpeg` 抽音轨，再交分离模型。

### 8.2 不支持的输入

- `kgm`
- `ncm`
- `qmc`
- 其他平台加密 / 专有缓存格式

这些继续维持现有原则：

> 只识别与说明，不做解密或绕过保护。

---

## 9. 输出策略

### 9.1 默认输出格式

V1 默认推荐：

- `wav`

原因：

- 对后续训练更友好
- 少一层有损编码
- 适合后续再加工

### 9.2 面向聊天场景的压缩输出

如果用户只是想“拿回去听听”，可转：

- `mp3`

### 9.3 命名建议

源文件 `song.mp3` 分离后：

- `song_vocals.wav`
- `song_instrumental.wav`

或者由 `output_title` 控总标题：

- `副歌练习_人声.wav`
- `副歌练习_伴奏.wav`

---

## 10. 对话体验原则

Akane 应该把它理解为一种自然能力，而不是技术炫技。

### 10.1 适合的用户说法

- “把这首歌的人声和伴奏拆开”
- “把视频里的歌声单独提出来”
- “我想要干声”
- “帮我留人声，伴奏单独发我”

### 10.2 不要强迫用户说术语

用户不需要知道：

- Demucs
- UVR
- stems
- MDX

这些术语只存在后端实现层。

Akane 只需要理解用户意图并调用正确工具。

---

## 11. 与未来 Akane 专属语音的关系

这个工具非常适合给未来语音路线铺路。

### 11.1 可直接复用的地方

- 提取训练用干声
- 从视频里抽干净人声
- 把素材继续交给 `convert_media_file`
  - 统一采样率
  - 单声道
  - 裁头尾静音

### 11.2 这条路的价值

```text
用户发歌 / 发视频
  -> Akane 分离出人声
  -> Akane 再做清洗
  -> 输出更适合训练或转写的素材
```

所以它不是孤立功能，而是未来“Akane 本人语音训练”的前置铺路工具。

---

## 12. V1 失败兜底

失败场景主要有：

- 本机没装模型依赖
- GPU 不可用
- 显存不足
- 输入文件不完整
- 模型脚本执行失败

Akane 的 followup 应该自然地说：

- 这次没成功
- 原因是环境 / 文件 / 模型执行失败
- 如有必要建议换更短的文件、换普通格式，或稍后重试

不要把底层 traceback 直接念给用户。

---

## 13. V1 实施顺序

### P0

- 落文档
- 约定工具协议
- 约定生成区输出结构

### P1

- `AudioSeparationService`
- 本地 Demucs / 分离脚本子进程调用
- `separate_audio_stems` ToolHandler
- 生成物入库和发送

### P2

- 输出命名 polish
- 失败信息 polish
- 与 `convert_media_file` 联动的后处理流程

### P3

- 多 stems
- 模型选择
- 更细粒度输出控制

---

## 14. 一句话总结

Akane 的音频分离工具 V1，本质上不是“在项目里装一个 UVR5”。

它应该是：

> 在现有附件区与生成区架构之上，为 Akane 新增一项重型但清晰的 AI 音频工作能力。

这项能力的最佳定位不是替代 `ffmpeg`，而是站在 `ffmpeg` 之后，成为更高一层的“音频理解与拆解工具”。
