# Public Alpha Cold-Start Smoke Test v1

每次跑 `scripts/export_public_alpha.ps1` 导出新一版公开包之后，照着这个文档
做一次冷启动人工测试。目的是验证：**从公开包里启动的用户，能不能在不查阅
源仓内部文档的情况下，第一次就把桌宠跑起来**。

未列出的能力（GPT-SoVITS、MCP、音乐感知、QQ 桥接等）走各自的产品化验收
表（`docs/productization_release_gate_v1.md`），不在本 smoke 范围内。

## 测试对象

- 一次新导出的目录，命名形如
  `AkaneCompanionLab-public-alpha-YYYYMMDD-HHmmss`
- 解压或 Get-Item 出来的版本号要跟 `README.md` 顶部的
  `v0.1.0-alpha.x` 一致
- **不要**在原始开发仓上跑这套流程；那样会被本地 `.venv`、
  `users_data\` 或 IDE 缓存伪装成"正常"

## 测试环境

| 项 | 建议值 | 备注 |
| --- | --- | --- |
| Windows | 10 22H2 / 11 23H2 及以上 | WebView2 已经随系统安装 |
| 用户账户 | 新建本地账户或纯净测试机 | 避免被 `%LOCALAPPDATA%\Akane\` 旧数据污染 |
| Python | 3.11.x（python.org 安装器，勾选 Add to PATH） | bootstrap 检测时要拿到 3.11+ |
| Node.js | 20 LTS 或更高 | 如需重新构建桌宠 exe 才需要 |
| Rust | rustup-init 安装的 stable | 同上；只要包内带了 exe 就可以跳过 |
| 网络 | 能正常出 PyPI、npm registry、crates.io | 首次依赖下载 |
| 磁盘 | 至少 6 GB 空闲 | `.venv` + node_modules + cargo target 占用 |

如果只想验证 Web 客户端，可以不装 Node/Rust，启动器会自动降级到
`-Mode Web` 路径。

## 步骤

### 1. 解压 / 进入公开包

```powershell
Set-Location 'F:\Temp\AkaneCompanionLab-public-alpha-YYYYMMDD-HHmmss'
Get-ChildItem .env.example, .env -ErrorAction SilentlyContinue
```

- 必须看到 `.env.example`
- **不应**看到 `.env`（如果看到说明导出脚本回归了，立刻中止并修复）

### 2. 运行 `启动_Akane.bat`

```powershell
.\启动_Akane.bat
```

- 终端首屏应出现 `AkaneCompanionLab Windows Bootstrap` 标题
- 接着出现 `User data root is ready.` 类似的 `[OK]` 行

### 3. 等待 Python 依赖安装

- 出现 `Creating .venv with Python …` 或
  `Installing Python dependencies. The first run can take several minutes...`
- pip 长时间没输出是因为在解析依赖图，不是卡死
- 完成后出现 `[OK] Python dependencies are ready.`

通过条件：终端没有 `error:` / `Traceback` 块；最终走到下一阶段。

### 4. 等待 Tauri 桌宠构建或复用已有 exe

如果导出包里没有现成 exe（默认就是没有）：

- 出现 `[首次构建提示]` 多行说明
- 接着进入 cargo `Compiling akane_desktop_pet_next…`、`Compiling tauri…`
- 完成后会出现 `[INFO] Tauri build finished. Launching the desktop pet next...`

如果当地缺 Node 或 Rust：

- Bootstrap 应自动降级到 `-Mode Web`
- 不应进入构建阶段，也不应静默失败

通过条件：要么 exe 构建成功，要么走到 Web fallback。中途有任何
`error[E…]:` / `npm ERR!` / `cargo:warning` 升级成 error 都算失败。

### 5. 桌宠或浏览器出现

- 桌面模式：屏幕上出现桌宠角色窗口，可以拖动
- Web 模式：默认浏览器自动打开
  `http://127.0.0.1:9999/?configure=model` 或 `/`

通过条件：能看到角色立绘 / 主页；窗口标题或页面不应是 "Not Found"
或空白错误页。

### 6. 打开控制中心

- 桌宠：右键 / 控制中心入口，应弹出 control-center-lab 窗口
- Web：浏览器里点击主页上的"控制中心"

通过条件：能看到至少"角色 / 模型 / 工具 / 能力"几个 tab，
能正常切换；首屏不应有 JS 报错弹窗。

### 7. 配置模型

- 进入"模型"页
- 选一个服务商（推荐 DeepSeek 或 OpenAI 兼容，或本机 Ollama）
- 填好地址 / API Key / 模型名
- 点 **测试连接** 应返回成功
- 点 **保存** 应返回 `已保存`

通过条件：
- `%LOCALAPPDATA%\Akane\users_data\_local\model_service.json` 出现
- 文件里的 `api_key` 不被读取接口回传到前端（只看到 `hasApiKey: true`）

### 8. 发送一条聊天

- 主聊天框输入 "你好" 或 "hello"
- 应收到模型实时回复
- 中文输入应正确显示，不应乱码

通过条件：
- 回复非空，且不是 `not_implemented` / `provider_unreachable`
- 终端后端日志 `akane_backend.log` 出现对应请求

### 9. 检查能力页产品化状态

- 控制中心 → "能力"页
- 翻一遍 GPT-SoVITS、MCP、音乐、QQ 等能力的开关 / 状态卡

通过条件：
- 未配置的能力显示 `unconfigured` / `disabled` / `unavailable`
- **不应**出现把未配置写成 "已就绪" 的伪成功
- 配置入口都指向控制中心，**不应**要求用户手改 `.env`

## 整体通过标准

| 项 | 要求 |
| --- | --- |
| 公开包内容 | 没有 `.env`、`users_data/`、`runtime_logs/`、`.db`、`node_modules`、`dist`、`target` |
| Bootstrap 输出 | 至少一条 `[OK] Python dependencies are ready.`；没有 `Traceback (most recent call last):` |
| 桌宠或 Web | 任一客户端能成功打开 |
| 控制中心 | 能加载，所有 tab 可切换 |
| 模型保存 | 写入 `%LOCALAPPDATA%\Akane\users_data\_local\model_service.json` |
| 一条聊天 | 收到模型真实回复 |
| 未配置能力 | 显式 `unconfigured` 状态，没有伪成功 |
| 关闭体验 | 关掉窗口后没有残留 `python.exe` / `akane_desktop_pet_next.exe`（必要时手动确认 Task Manager） |

任一项失败都算 smoke 不通过；不应发新版公开包。

## 常见失败排查

### Python 不存在

- 现象：bootstrap 报
  `Python 3.11 or newer was not found. Install Python 3.11 from python.org…`
- 处理：让测试机装 python.org 的 3.11 安装器，勾选 Add to PATH，重启
  PowerShell 再跑

### Node / Rust 不存在，无法构建桌宠

- 现象：bootstrap 报
  `Desktop build tools are incomplete (node, npm, cargo, rustc); using the Web client for this launch.`
- 处理：这是预期降级。要测试桌面端就装 Node 20 + rustup stable，重跑

### 9999 端口被占用

- 现象：`Port 9999 is already in use, but it was not recognized as a managed Akane backend.`
- 处理：用 `Get-NetTCPConnection -LocalPort 9999` 找到占用进程；要么关掉，
  要么用 `.\start_akane.bat -BackendPort 9998` 改端口重试

### LLM 未配置

- 现象：桌宠开了但聊天回复 `model_not_configured` 或控制中心
  弹出"模型"页
- 处理：这是预期的首次配置入口；按步骤 7 走一遍。不应在这里
  让用户去翻 `.env`

### pip 依赖安装失败

- 现象：bootstrap 在 `Installing Python dependencies…` 之后抛
  `python_dependency_install_failed`
- 处理：
  - 看 `%LOCALAPPDATA%\Akane\logs\akane_backend.log` 是否有
    `ConnectionError` / `SSLError` → 网络问题
  - 国内网络可在当前 PowerShell 临时指定镜像后重跑：
    `$env:AKANE_PIP_INDEX_URL = "https://pypi.tuna.tsinghua.edu.cn/simple"`
  - 看是否撞上 `requirements.txt` 里某个版本 wheel 在当前 Python
    没编译产物 → 把详细输出贴回 bug
  - 不要在公开包里手动 `pip install --force-reinstall`，那样会污染测试

### HuggingFace 语义模型下载慢

- 默认配置 `EMBEDDING_LOCAL_FILES_ONLY=true` 不会在启动时下载模型；
  如果本地没有缓存，会回退到 hashed embedding，启动不应被阻塞。
- 如果测试真实语义模型，把 `.env` 改成
  `EMBEDDING_LOCAL_FILES_ONLY=false` 后，国内网络可同时设置
  `HF_ENDPOINT=https://hf-mirror.com`。
- 如果下载失败但后端仍能启动，并且日志说明回退到 hashed embedding，
  公开包可以继续测试基础聊天；把模型下载问题记录为网络/镜像配置问题。

### WebView2 缺失

- 现象：桌宠 exe 启动后窗口白屏，或直接退出，事件查看器里
  `WebView2 runtime not installed`
- 处理：去微软官网下载 WebView2 Evergreen Standalone Installer 装上。
  Windows 11 自带 WebView2，所以这种失败一般出现在老 Windows 10 镜像

## 通过后

- 在 PR 或发布说明里贴一行 "已照 docs/public_alpha_smoke_test_v1.md
  跑通"，附测试日期 + 公开包目录名
- 把这次发现的新失败模式补回这份文档"常见失败"小节
- 真要打包安装器，再另外起一份 installer smoke 文档；这份只覆盖
  "源码型公开包"
