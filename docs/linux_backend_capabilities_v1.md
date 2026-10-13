# Linux 后端与 Agent 能力

Linux 可运行后端回合引擎。Agent 工具循环、MemCore、文件工作区和本机命令
执行没有依赖 Windows 桌面。两种系统的命令执行路径分别向模型声明
PowerShell 或 `/bin/sh`，不会把 Windows 命令语言当作 Linux 命令语言。

| 能力 | Linux 边界 | 所需条件 |
| --- | --- | --- |
| 文字聊天、结构化输出、流式回复 | 后端主调用链支持 | 配置外部模型 API |
| MemCore 原始历史、压缩、记忆浏览与时间线 | 跨平台；SQLite 持久化 | 默认 navigation 模式不需要本地 embedding 模型 |
| 原生工具调用与多步 Agent 循环 | 跨平台 | 模型支持原生工具；具体工具按实际权限与可用性暴露 |
| 文件读取、检索、修改、项目工作区 | 跨平台 | 显式授权工作区和文件范围 |
| Shell、后台任务、状态查询、取消 | 有 POSIX 实现 | `/bin/sh` 与需要的命令行工具；执行宿主权限仍由宿主控制 |
| Skills、插件、MCP | 跨平台的宿主与契约 | 插件或 MCP 服务自己的依赖、进程命令必须适配 Linux |
| 网页读取与受控浏览器 | 可选；源码有 Linux 浏览器路径 | Playwright、Chromium、系统库与合适的 headless 配置；不随轻量后端安装 |
| QQ / OneBot | 后端协议接入跨平台 | 另行部署并配置兼容的 NapCat/OneBot 服务 |
| 文档、媒体处理 | 可选 | 完整依赖及相关程序；例如媒体处理需要 ffmpeg |
| TTS / ASR / 本地声学模型 | 可选的独立服务 | 相关 provider、模型或 API；轻量后端不承诺本地模型可用 |
| Windows 电脑操作、剪贴板、系统音频感知 | 没有 Linux 原生桌面执行器 | 可由已连接的 Windows 设备执行器提供远程能力 |
| Tauri 桌宠、立绘窗口与桌面交互 | 当前主要验证 Windows | 不把 Linux 后端启动通过当作 Linux 桌面端验收 |

`backend` 安装配置适合先验证小型 CPU 服务器的聊天、记忆和通用 Agent
基础；2 核/2GB 的具体容量仍取决于同时开启的服务与对话数量。浏览器和
本地机器学习能力另行安装、配置与验收，不应成为文字聊天的启动前提。

目前的自动验收覆盖空环境安装、后端健康检查、两轮真实 HTTP 聊天调用、
历史回带，以及本机 Shell 命令执行。模型响应由隔离的本机测试接口提供。
CI 在 Ubuntu 22.04 / Python 3.11 与 Windows 上执行同一入口；具体外部
模型、NapCat、Chromium、音频设备和 Linux 图形桌面需要各自的真实验收。

代码入口：

- 回合与工具循环：`companion_v01/engine_services/`、`companion_v01/tool_orchestration/`
- 本机执行器：`companion_v01/local_execution_runtime/`
- 工作区：`companion_v01/project_workspace_runtime/`、`companion_v01/workspace_files_runtime/`
- 浏览器：`companion_v01/managed_browser/`
- MemCore 适配：`companion_v01/memcore_integration/`

安装步骤见 [后端安装说明](backend_installation_v1.md)。
