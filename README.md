# AkaneCompanionLab

这是从主项目中独立出来的陪伴型 `Akane V0.1` 试验目录。

仓库按公开源码、工具、文档和本地私有材料分层：

- `companion_v01/`：FastAPI 后端和 Akane 运行时模块
- `services/`：共享服务客户端
- `web/`：后端直接 serve 的静态 Web 前端
- `desktop_pet/`：旧 Electron 桌宠 V0，冻结保留
- `desktop_pet_next/`：Tauri/WebView2 桌宠主线
- `desktop_pet_creator_kit/`：角色包创建工具
- `tests/`：测试套件
- `docs/` / `documents/`：工程文档、设计文档和项目资料
- `scripts/` / `maintenance/`：可共享开发工具和运维脚本
- `local_research/`：本地私有语料、抽取产物、临时研究资料，已被 Git 忽略

更详细的布局说明见 `docs/repository_layout.md`。

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

## 桌宠模式

桌宠是 Akane 的桌面陪伴客户端，不是新的大脑。它共享 `profile_user_id=master`，但使用独立 `session_id`，用于桌面上的轻量陪伴、输入、分段气泡和后台任务完成提醒。

启动时先开后端：

```powershell
python launch_akane_memory_v01.py
```

再开桌宠：

```powershell
powershell -ExecutionPolicy Bypass -File .\start_akane_desktop_pet.ps1
```

Windows 上也可以双击 `启动_Akane桌宠.bat`。更多说明见 `docs/desktop_pet_v01.md`。

## 本地访问

- 主界面：`http://127.0.0.1:9998/`
- 兼容入口：`http://127.0.0.1:9998/preview`
- 资源调试页：`http://127.0.0.1:9998/resource-preview`
- 健康检查：`http://127.0.0.1:9998/health`

## 测试

推荐每次大改前先跑这条快速回归命令：

```powershell
python -m unittest tests.quick_regression_suite
```

如果你在 Windows 上更习惯脚本入口，也可以直接跑：

```powershell
powershell -ExecutionPolicy Bypass -File .\run_quick_regression.ps1
```

QQ 工坊能力和本机依赖自检：

```powershell
powershell -ExecutionPolicy Bypass -File .\run_qq_workshop_self_check.ps1
```

能力路网说明见 `docs/qq_workshop_capabilities_v1.md`。

这组快速套件会优先检查：

- `tests/test_resource_visibility_contract.py`
  - 附件区 / 生成区 / 任务工作区边界
  - 图片视觉卡、媒体规格卡、端到端资源可见性链路
- 附件歧义确认
- 生成文件歧义确认
- 精确 handle 发送不会被歧义目标干扰

完整回归仍然建议再跑一轮：

```powershell
python -m unittest discover tests
```

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
