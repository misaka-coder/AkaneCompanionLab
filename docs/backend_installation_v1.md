# 后端安装与公开依赖供应

基础环境为 Python 3.11。源码中的 `package_wheels/` 是维护者本地产物，
不会出现在普通 Git 克隆里；安装器从
[官方依赖发行物](https://github.com/misaka-coder/AkaneCompanionLab/releases/tag/deps-2026.10.13)
下载内部包，校验仓库中固定的 SHA-256，再安装普通 wheel。
第三方依赖使用当前 pip 配置的公共索引。

## Linux 纯文字后端

先准备 Python 3.11 和其 venv 支持。Ubuntu 22.04 的默认 `python3` 可能是
3.10，下面明确使用 3.11：

```bash
git clone https://github.com/misaka-coder/AkaneCompanionLab.git
cd AkaneCompanionLab
python3.11 -m venv .venv
./.venv/bin/python scripts/install_dependencies.py --profile backend
cp .env.backend.example .env
```

编辑 `.env`，填入 `CHAT_API_KEY`、`CHAT_BASE_URL`、`CHAT_MODEL_NAME` 和
对应协议；也可使用现有的 `.env.example` 配置其他模型分工。
保留已有配置时不要覆盖原 `.env`。

```bash
./.venv/bin/python launch_akane_memory_v01.py
```

默认访问 `http://127.0.0.1:9999/health`，API 说明在 `/docs`。
配置好模型后可以通过真实聊天入口检查回复：

```bash
curl -X POST http://127.0.0.1:9999/think_once \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"my-user","message":"你好"}'
```

`backend` 配置包含聊天、MemCore 与通用工具契约，不安装 Chroma、
Playwright、文档/媒体处理包或本地机器学习环境。部分内部适配器的轻量
PCM 依赖仍会由包元数据安装；不需要 CUDA、Node、Rust 或桌宠。
它没有 Web 聊天客户端，Linux 服务器使用 API 或接入自己的客户端。
平台与可选能力边界见 [Linux 能力表](linux_backend_capabilities_v1.md)。

## Windows

普通桌宠用户继续使用 `启动_Akane.bat`。缺少本地 wheelhouse 时，启动器
自动调用同一个官方依赖安装器。仅启动后端用：

```powershell
.\start_akane.bat -Mode Backend
```

手动准备轻量后端也可以使用：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe scripts\install_dependencies.py --profile backend
```

需要文档、媒体、浏览器、Chroma 或 Edge TTS 等可选服务时，安装完整宿主
依赖；本地语音模型与 GPU 环境仍单独配置：

```bash
python scripts/install_dependencies.py --profile full
```

## 离线供应与源码重建

Ubuntu 22.04 / Linux x86_64 / Python 3.11 用户可以直接从
[依赖发行页](https://github.com/misaka-coder/AkaneCompanionLab/releases/tag/deps-2026.10.13)
下载 `akane-backend-linux-x86_64-py311.zip` 和同名 `.zip.sha256` 文件。
这是经过 Linux CI 空环境安装和后端行为验收的完整依赖闭包，包括第三方包；
不需要 GitHub 登录，不能用于 Windows 或其他 Python 版本。

```bash
sha256sum --check akane-backend-linux-x86_64-py311.zip.sha256
unzip akane-backend-linux-x86_64-py311.zip -d backend-wheelhouse
./.venv/bin/python scripts/install_dependencies.py \
  --profile backend --wheelhouse ./backend-wheelhouse
```

同名 `.json` 文件记录来源代码提交、CI 运行和压缩包 SHA-256。
下载依赖包后仍需准备项目源码、Python 3.11、venv 与模型 API 配置。

官方发行物提供 14 个内部包的 wheel 与对应源码，包括当前锁定的
`memcore-kernel`，不使用 PyPI 上无关的 `memcore`，也不随意解除版本锁定。
`akane-package-sources.json` 记录各包版本及源码提交。
源码归档包含运行时代码、构建元数据和许可证，不包含本地数据或未提交改动。

维护者或用户从公开源码构建当前平台的完整离线后端 wheelhouse：

```bash
python -m pip install 'build>=1.2'
python scripts/build_public_backend_wheelhouse.py --output-dir dist/backend-wheelhouse
python scripts/verify_extracted_package_independence.py \
  --profile backend --wheelhouse dist/backend-wheelhouse
```

复制 wheelhouse 到同平台、同 Python 版本的新机器，安装时明确指定目录：

```bash
python scripts/install_dependencies.py --profile backend --wheelhouse /path/to/backend-wheelhouse
```

此模式完全离线。仅内部包的压缩包不是完整离线供应；缺少第三方闭包会
明确报错。Linux CI 也会从公开源码重建依赖闭包、在空环境离线安装验证，
然后提供 `akane-backend-linux-x86_64-py311` 构建产物。
维护者可以手动运行 `Publish verified Linux backend bundle` 工作流，将通过
Ubuntu 验收的主分支 CI 产物再次离线验收后附到已有 Release；该操作记录来源，
并拒绝覆盖已有同名发行物。

需要旧安装器兼容的供应方式时，`AKANE_PACKAGE_WHEELHOUSE` 和
`AKANE_PACKAGE_INDEX_URL` 继续有效；显式无效目录不会被悄悄替换成在线安装。
发行供应锁位于 `deploy/public-package-supply.json`。更新内部包版本时必须
一起更新发行物和锁文件；版本、来源或校验值不匹配会停止安装。

## 不调用外部模型的本地验收

```bash
python scripts/smoke_core_backend.py
```

验收使用临时数据目录和本机测试模型接口，检查 HTTP 聊天、MemCore 的历史
连续性与真实 Shell 子进程。它不使用真实密钥，不测试模型质量，也不启动
桌宠、QQ 或音频服务。
