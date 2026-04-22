# AkaneCompanionLab

这是从主项目中独立出来的陪伴型 `Akane V0.1` 试验目录。

当前只保留这几部分：

- `companion_v01/`
  - 新记忆引擎实现
- `launch_akane_memory_v01.py`
  - 本地启动入口
- `start_akane_preview.ps1`
  - 本地预览启动脚本
- `启动_Akane预览.bat`
  - Windows 双击启动入口
- `config.py`
  - 最小配置入口
- `services/llm_client.py`
  - LLM 客户端
- `documents/`
  - 当前框架和项目文档

## 启动

推荐直接双击：

- `启动_Akane预览.bat`

也可以命令行启动：

```powershell
powershell -ExecutionPolicy Bypass -File .\start_akane_preview.ps1
```

如果想临时换本地端口：

```powershell
powershell -ExecutionPolicy Bypass -File .\start_akane_preview.ps1 -Port 10098
```

如果只想直接跑后端：

```powershell
python launch_akane_memory_v01.py
```

## 本地访问

- 主界面：`http://127.0.0.1:9998/`
- 兼容入口：`http://127.0.0.1:9998/preview`
- 资源调试页：`http://127.0.0.1:9998/resource-preview`
- 健康检查：`http://127.0.0.1:9998/health`

## 说明

- 当前主界面已经切到通用 gal 壳，前端不再绑定某个固定人物。
- `web/assets/` 保持项目自有资源，不与其它仓库互相覆盖。
- 这个目录后续用于网页前端版本，不再和旧 `AkaneBrain` 主工程混改。

## 可选：音频分离环境

如果你希望 Akane 使用 `separate_audio_stems` 做人声 / 伴奏分离，或使用 `clean_voice_track` 做 AI 人声净化，需要额外准备本地音频模型环境。

当前项目默认只依赖：

- `ffmpeg`
- `torch`
- `demucs`
- `deepfilternet`

但要真正吃到 NVIDIA GPU，需要确保：

1. 你的机器能正常看到显卡
   - `nvidia-smi`
2. 当前 Python 环境安装的是 **CUDA 版 PyTorch**，而不是 CPU 版
3. `demucs` 命令或 `python -m demucs.separate` 可以执行

### Windows / NVIDIA 推荐流程

先卸掉当前环境里的 CPU 版 PyTorch：

```powershell
python -m pip uninstall -y torch torchvision torchaudio
```

然后按 PyTorch 官方安装矩阵选择 **Windows + Pip + Python + CUDA** 对应命令安装。

官方入口：

- PyTorch Start Locally: https://pytorch.org/get-started/locally/

对于支持 CUDA 12.8 的 Windows / NVIDIA 机器，一般会是类似下面这种形式：

```powershell
python -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
```

再安装 Demucs：

```powershell
python -m pip install -U demucs
```

### 验证

```powershell
python -c "import torch; print(torch.__version__); print(torch.cuda.is_available()); print(torch.cuda.device_count())"
python -m demucs.separate --help
```

只要 `torch.cuda.is_available()` 返回 `True`，并且 `demucs` 能正常显示帮助信息，Akane 的音频分离工具环境就算准备好了。
