# Akane 纯本地测试档

`start_akane_local_test.bat` 用于在同一份源码上验证本地后端与桌宠，不经过 Akane 云端宿主。

## 固定边界

- 后端固定监听 `127.0.0.1:11999`，不会暴露到局域网或公网。
- 实例固定为 `local-test`，数据默认写入 `%LOCALAPPDATA%\Akane\local-test-runtime`。
- `users_data`、工作区、缓存、日志与云端 `personal` 实例完全分离，不复制云端记忆。
- QQ channel 固定关闭，避免本地测试进程与线上 Bot 同时消费消息。
- 命名实例需要的管理凭据与 Satellite 凭据在每次启动时临时生成，只在本地后端与桌宠子进程间共享，不写入 `.env`、日志或实例清单。
- Prompt、角色包、Skill 和生产代码来自当前工作区；模型、视觉和 env 级 TTS 默认值复用项目根目录 `.env`。
- 若存在 `%LOCALAPPDATA%\Akane\local-test-runtime\config\cloud-aligned.env`，它会在项目 `.env` 之后加载，用于对齐云端当前 provider 与能力开关；该文件不在仓库内。
- 内部 Python 包按相邻源码仓库的 Git 提交指纹对齐；提交变化时会在本机缓存中重建 wheel 并更新 `.venv`，日常启动不会重复构建。
- 新建的纯本地测试档首次启动默认使用“完全访问”，避免桌宠主界面无法承接逐次审批；这只作用于回环地址上的隔离 `local-test` 实例，且不会跳过 URL、路径、密钥等硬安全校验。之后在“设置 → 能力 → 安全边界”做出的选择会被保留，启动器不会反复覆盖。
- 这是“Akane 宿主纯本地”，不是“模型离线运行”。若 `.env` 指向 DeepSeek、Gemini 等远程 provider，模型请求仍需要联网。

## 启动

双击：

```text
start_akane_local_test.bat
```

第一次使用或云端模型配置变化后，先运行一次：

```text
sync_akane_local_test_from_cloud.bat
```

同步脚本只复制 Chat/Text/Vision provider 配置、对应 API key、Shell 开关和 Memory backend。它不复制 QQ 凭据、管理 token、路径、日志、数据库或记忆；普通本地启动不会连接云服务器。

云端数据根中持久化的 TTS provider 选择、GPT-SoVITS profile、ASR/RVC 外部运行时状态不会复制。本地实例会使用自身可用的语音 provider 或 fallback；因此当前档用于严格对照聊天、视觉、Shell、Skill、MemCore 与桌宠 UI，不能把语音供应链视为已经一比一对齐。

首次启动可能需要编译桌宠。之后源码未变化时会直接复用已有 release executable。
首次对齐或内部包提交变化时还会重建本机 wheel，避免“云端 release 已更新、本地 `.venv` 仍是旧包”的假测试。

只启动本地后端、不打开桌宠：

```powershell
.\start_akane_local_test.ps1 -BackendOnly
```

仅准备并检查隔离档，不启动任何进程：

```powershell
.\start_akane_local_test.ps1 -PrepareOnly
```

## 验收

启动后执行：

```powershell
Invoke-RestMethod http://127.0.0.1:11999/health | Select-Object status, instance_id, root_binding
```

预期：

```text
status instance_id root_binding
------ ----------- ------------
ok     local-test  valid
```

然后在桌宠依次验证：

1. 普通文字回复与分气泡表现。
2. 图片轮是否路由视觉模型。
3. Shell、Skill、文件交付是否符合本机 `.env` 中的能力开关。
4. 重启后本地测试对话仍在，但云端 personal 历史不会出现。

若模型提示动作需要审批，打开桌宠“设置 → 能力 → 安全边界”，选择“完全访问”即可给本地实例统一授权；选择“请求批准”则会恢复逐次审批策略。权限档属于 `local-test`，不会改变云端 Bot 的授权策略。

切回线上模式时，重新运行桌面的“启动 Akane 本地能力”即可；两种模式可以保留各自后端和数据，但同一时刻只保留一个桌宠窗口。

## 配置对齐口径

启动器复用本地 `.env`，再按需加载仓库外的 cloud-aligned 私密覆盖档，最后只覆盖实例、数据根、监听地址和 QQ 开关。云端 provider 配置变化后重新运行同步脚本即可；不要复制云端数据库、运行日志或 `users_data`。
