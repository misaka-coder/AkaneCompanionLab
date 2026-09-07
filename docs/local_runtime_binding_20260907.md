# 本地运行环境绑定与依赖准备

## 开源可移植性边界

- 管理根和解释器均为用户参数，公共代码不要求 F 盘或本机安装路径。
- 不调用该可选绑定函数时，保持既有启动器和用户数据目录默认规则；仅加载脚本也不会修改环境。
- 明确指定的目录或解释器不可用时启动配置失败，不静默换盘、安装 Python 或创建 venv。
- 下文具体盘符和环境表是本机验收记录，不是其他用户的默认配置。Linux/macOS 继续使用既有环境配置入口，本 PowerShell 辅助脚本只服务 Windows 启动。

## 唯一配置入口

`scripts/akane_local_runtime.ps1` 提供 `Set-AkaneLocalRuntime -Root <目录> -Python <已有解释器>`。
实例环境文件加载完后、启动后端前调用；只设置当前启动进程及其子进程的既有环境变量。
不写用户/系统 PATH，不新建 venv，不搬迁数据库、QQ 数据、旧环境或已有项目。
当前本机启动入口 `work/restart-screen-observation-local.ps1` 调用该函数；该本机脚本不提交。

已选择管理根 `F:\AkaneRuntime`；默认任务目录为其 `workspace` 子目录，临时目录和 pip/npm/pnpm/corepack 缓存也位于该根下。
显式选中的项目仍使用其原目录；已有下载不会自动搬迁。
通用命令和文档插件使用 `F:\Akane\AkaneCompanionLab\.venv\Scripts\python.exe`；宿主启动器原本也使用该解释器。
其他启动入口必须在启动前调用绑定函数才能继承配置，本方案不修改系统全局环境。

## 已核对的环境（2026-09-07）

| 用途 / 解释器 | Python | 核验结果与决策 |
| --- | --- | --- |
| Akane 项目 `.venv\Scripts\python.exe` | 3.11.4 | docx 1.2.0、openpyxl 3.1.5、reportlab 4.4.10、pypdf 6.10.2；文档插件真实 DOCX/XLSX/PDF 健康检查通过。复用。 |
| `C:\Program Files\Python311\python.exe` | 3.11.4 | 文档依赖存在；torch 2.12.0 / torchaudio 2.11.0 不配套，不选作音频环境，不自动修复。 |
| 用户 Python310 安装 | 3.10.11 | 缺 pypdf，低于文档插件 Python 3.11 要求；不作为默认。 |
| `F:\PythonEnvs\torch-cu128\Scripts\python.exe` | 3.11.4 | torch/torchaudio 2.11.0+cu128；无文档依赖，保留专用。 |
| `F:\Akane\optional-runtimes\voice-clean\venv\Scripts\python.exe` | 3.11.4 | torch/torchaudio 2.6.0+CPU；已准备的语音清理专用环境，不迁移或激活。 |
| `F:\ComfyUI-aki-v2\python\python.exe` | 3.12.10 | torch/torchaudio 2.8.0+cu129，已有活跃进程；不改动。 |
| seethrough-local 项目 `.venv` | 3.12.13 | torch 2.8.0+cu128；保持项目专用。 |
| `F:\msst\workenv\python.exe` | 未确认 | 可执行文件存在，但探测无输出；不当作兼容可用。 |

Node、FFmpeg、winget 已可发现；LibreOffice 尚未确认安装。环境表是盘点记录，不是自动发现的完整机器清单，也不代表允许删除这些环境。

## 依赖准备流程

1. 从命令实际输出核对 `Get-Command python`、`python -c 'import sys; print(sys.executable)'`；目录切换不代表环境切换。
2. 先在候选解释器检查所需包及版本，已有兼容环境可复用；专用环境冲突不原地升级。
3. 确需安装时，明确目标解释器/系统程序、来源、版本约束、空间与影响，再取得安装许可。
4. 始终用目标解释器完整路径执行 `-m pip`，健康检查与实际插件任务必须使用同一解释器。
5. 下载先确定目标目录，报告真实文件字节或日志；静默运行不等于卡死。取消后可能留下部分文件。
6. 退出码 0 只说明命令成功结束；核验目标文件完整性以及程序实际可用后才能宣布安装完成。

本次未安装软件、未迁移旧环境、未删除本地下载残留。云端部署不继承本机路径绑定。

本机加载验收：用户确认后，2026-09-07 23:02 仅重启本地后端。新进程健康正常；其实际环境中的所选解释器、工作区和临时目录均与配置一致。以该进程环境重跑文档 worker 健康检查及真实执行器烟测通过。桌宠窗口未重启。

23:05–23:06 本地 QQ 对话验收：用户要求只检查环境、不安装软件；模型真实执行命令，回执与最终回复均指向项目 venv 的 Python 3.11.4，并准确区分已安装文档依赖与未找到的 LibreOffice。
