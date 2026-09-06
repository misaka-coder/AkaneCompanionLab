# 可选媒体转换插件

插件 `akane.media-convert`，工具 `akane.media-convert.run.v1`。它不是宿主内置转换器，也不导入宿主媒体业务实现。

支持普通独立音视频文件的音频转换/提取，输出 MP3、WAV、FLAC、M4A、AAC、OGG、Opus；保留裁剪、码率、采样率、单/双声道、响度标准化、音量增益、头尾静音裁切、淡入淡出和 0.25–4 倍速。播放列表、网络流与受保护缓存不作为输入。

## 安装与依赖

- Python 插件使用标准库；`capcore`、`companion_v01.plugin_api` 与 `companion_v01.plugin_subprocess` 是当前 Akane 提供的公共 SDK，不从网络安装一份私有宿主实现。更新后的 wheel 需要包含此子进程辅助模块的 Akane release。
- 运行宿主机器必须已有可执行的 FFmpeg 和 FFprobe，并包含所用音频编码器。两者需在宿主进程 PATH 中，或分别设置 `AKANE_MEDIA_FFMPEG`、`AKANE_MEDIA_FFPROBE` 为可执行文件。修改环境后重启宿主，使新进程继承配置。
- 可在宿主终端运行 `ffmpeg -version`、`ffprobe -version` 验证。插件激活检查真实可执行性；缺失时返回 `ffmpeg_not_found` / `ffprobe_not_found`，不发布假可用工具。编码器不支持或媒体损坏时，转换返回真实失败，不上传 stderr 或宿主路径。
- wheel 只包含插件 Python 源码，不捆绑 FFmpeg、不隐式 pip install、不做系统安装或复杂依赖求解。现有安装器的 `--no-deps --no-index` 行为保持不变。
- 市场发行与配置见 [静态市场说明](../../docs/plugin_market_v1.md)。控制中心“服务与扩展 → 可选插件市场 → 获取并检查”，再审查候选贡献/权限并安装；模型使用 `manage_extension(market)` → `stage_market` → `install`。开发阶段仍可 `test_source` → `stage_source` → `install`，这些入口最终使用同一个暂存安装器。

所需权限只有 `capability.prompt.invoke`、`resource.read`、`artifact.write`。插件无网络调用、持久数据库、额外后台服务或直接 QQ 发送权限。

## 运行语义

`source_id` 接收当前会话 `file_* / audio_* / video_* / gen_*` 或完整资源 ID；`output_format` 必填。开始/结束时间用秒数字符串、`MM:SS`、`HH:MM:SS` 或 `1分30秒`。采样率/声道为 0 或省略时不强制覆盖，由滤镜与编码器决定（例如响度标准化可能重采样），以返回的实际探测规格为准；增益范围 -24～24 dB，淡入/淡出 0～600 秒，参数越界明确拒绝，不静默截断。

调用声明为宿主长任务。插件收到调用取消后会终止并等待 FFmpeg/FFprobe，随后清理临时输入/输出。产物总预算明确声明为 1 GiB；大文件用文件交接，不装入 JSON 或整体内存。默认 `send_to_user=false`，只登记产物；返回真实音频规格以及生成文件事件，实际发送仍由原渠道处理，登记不等于发送成功。

## 本包验证

在 Akane 公共 SDK 可用的开发环境中：

```powershell
python -m unittest discover -s plugins/akane_media_convert/tests -v
```

测试通过临时路径装载本包源码，真实媒体测试需要 FFmpeg/FFprobe；隔离 wheel 安装、模型发现、Job/渠道与卸载验收由宿主测试覆盖。
