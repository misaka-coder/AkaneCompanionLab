# Desktop Satellite 本地能力 V1

状态：**第一批真实短任务能力已部署到云端 personal，并通过真实模型 → broker → 本机执行器 smoke；finance 未配置或复用 personal Satellite。**

本文记录“云端 Akane 在用户电脑开着时调用本机能力”的当前真实边界，避免后续上下文压缩或接手时把协议、云端能力和本机执行器混为一谈。

## 1. 本轮完成范围

Desktop Satellite 现在可注册并执行四个真实工具：

| tool_id | 本机执行器 | 返回/效果 | 当前边界 |
|---|---|---|---|
| `open_browser` | 系统默认浏览器 | 真实打开经过公开 URL 安全校验的网页 | 不读取网页内容，不访问内网地址 |
| `desktop_context_snapshot` | Tauri 前台窗口采集 | 窗口标题、进程文件名、平台等有限摘要 | 不读取窗口正文、剪贴板或文件路径 |
| `system_media_snapshot` | Windows 系统媒体会话 | 当前歌曲、艺术家、播放状态和进度 | 没有活动播放器时返回真实不可用/空状态 |
| `system_media_control` | Windows 系统媒体控制 | 播放、暂停、停止、上一首、下一首 | 只接受固定动作，不操作文件或网页 |

这些工具不是“字段存在即完成”。只有本机 Satellite 在线、注册的 spec/schema 完全匹配并取得有效 lease 时，云端 resolver 才会把对应工具放进 desktop/QQ 本轮 schema。电脑离线或 lease 过期后，工具会从下一轮 schema 消失，不会假排队。

## 2. 真实调用链

```text
desktop/QQ 本轮请求
  -> CapabilityRegistry 根据 live offer 冻结 ToolSpec + ExecutionReceipt
  -> 模型产生稳定 tool_id 和参数
  -> tool_orchestration_engine 以冻结 handler 校验参数/receipt
  -> ExecutorBroker 复核 instance/offer/lease/spec/schema/idempotency
  -> DesktopSatelliteService 通过已鉴权 WSS 发送 invoke
  -> Tauri 再次校验 instance/lease/offer/tool/spec/schema/参数
  -> 调用真实 Windows/Tauri 执行器
  -> accepted/running/result 均携带同一个 tool_id
  -> 云端只接受与 pending invocation 完全匹配的结果
  -> 安全 data 进入 ToolResultEnvelope 和下一轮模型反馈
```

`engine.tool_handlers` 中的新 handler 只承担 canonical schema、提示和参数归一化，不允许直接执行本地动作。真实动作必须经过 `ExecutorBroker` 和在线 Satellite；不存在“云端执行失败后偷偷退回服务器本地函数”的路径。

## 3. 协议与幂等

- Tauri 注册四个 canonical offer，带 `tool_id`、`spec_version`、`schema_version`、`schema_hash`。
- 服务端只接受自身已知且 hash 完全匹配的 offer；未知工具不会进入连接能力集。
- 每次 `invoke` 固定 `instance_id`、`lease_epoch`、`offer_id`、`invocation_id` 和 `tool_id`。
- Tauri 的 terminal ledger 按 `invocation_id` 去重；同一 ID 重发同一工具返回已有结果，不重复产生副作用。
- 同一 ID 改成另一工具会返回 `invocation_id_tool_conflict`，不会复用上一工具结果。
- 断线前未 accepted 返回 `unavailable_before_dispatch`；accepted 后断线返回 `execution_unknown`，模型不得声称已完成。

## 4. 数据安全

- 本机结果通过 serde JSON 返回，不传绝对路径、密钥、provider endpoint 或原始异常。
- Prompt audit 只额外记录本轮实际发送的 canonical tool ID 列表，不记录工具描述、参数、返回数据或用户文本，用于区分“工具未进入 schema”和“模型自主未调用”。
- 云端结果清洗递归过滤 `path`、`absolutePath`、`localPath`、`token`、`secret`、`password`、`authorization` 及其常见复合键。
- 字符串、列表、对象大小和嵌套深度有界；未知对象不会原样进入 prompt。
- `desktop_context_snapshot` 只返回前台窗口有限元数据；Windows 进程查询得到的完整可执行路径会先裁成文件名。
- personal 与 finance 的 Satellite token、instance、lease 不共享。本轮只准备 personal 代码链，不自动把同一台电脑绑定给 finance。
- Windows launcher 会先读取当前进程凭据，再读取用户环境中的实例专用 `AKANE_DESKTOP_SATELLITE_TOKEN_<INSTANCE>`，最后才兼容通用 token；读取后只注入当前桌宠进程，不打印值。这样 personal/finance 可分别保存凭据，不要求把 token 写进仓库或启动命令。

## 5. 已完成验证

Python 测试覆盖：

- 离线时三个新增工具不进入 selection。
- 在线且 schema 匹配时，desktop/QQ 均取得冻结 ToolSpec 和 receipt。
- 非法媒体动作在 broker dispatch 前失败。
- 错误 `tool_id` 的 result 被忽略。
- 敏感结果字段被移除。
- 本机真实 data 进入最终 `ToolResultEnvelope`。
- 原有 `open_browser` 断线、lease、实例隔离和幂等测试继续通过。

Rust 测试覆盖：

- 四个工具的 spec/version/hash 和参数校验。
- 未知工具、非法媒体动作、实例/lease/schema 不匹配拒绝。
- 浏览器调用与桌面上下文读取的 invocation 幂等。
- 桌面上下文真实采集结果序列化。
- 系统媒体不可用结果序列化且不带内部 detail/本地路径。

本地验证命令：

```powershell
python -m unittest tests.test_capability_fabric_m66 tests.test_desktop_satellite_local_capabilities
cargo test --manifest-path desktop_pet_next/src-tauri/Cargo.toml
cargo check --manifest-path desktop_pet_next/src-tauri/Cargo.toml
git diff --check
```

2026-07-18 personal 真实部署验收：

- personal `/health` 精确为 `status=ok / instance_id=personal / root_binding=valid`，finance 全程保持原 PID。
- 本机通过加密 SSH tunnel 暴露 loopback backend URL；应用层仍执行原有“远端 HTTPS、loopback 可用 HTTP/WS”的默认规则，没有新增私网明文例外。
- personal 使用已通过 A/B/C/D provider probe 的 PinAI Responses 协议；prompt audit 从 `native_tool_count=0` 恢复为实际发送 canonical native schemas。
- Satellite diagnostics 为 `online / connected=true / activeOfferCount=4`。
- 有效 desktop-pet 协议请求的 native schema 实际包含 `open_browser`、`desktop_context_snapshot`、`system_media_snapshot`、`system_media_control`。
- `desktop_context_snapshot` 真实事件为 `capability_execution_result / succeeded`。
- `system_media_snapshot` 真实事件为 `capability_execution_result / succeeded`。
- 没有自动执行 `system_media_control`，避免在验收时改变用户正在播放的媒体；其参数、协议和 Tauri executor 由自动化测试覆盖。

## 6. personal 一键启动

Windows 本机使用根目录入口：

```text
start_akane_cloud_personal.bat
```

该入口只启动桌面端与 Desktop Satellite，不启动第二个 Akane 后端。它会：

1. 优先读取用户环境中的 `AKANE_DESKTOP_SATELLITE_TOKEN_PERSONAL`；
2. 建立或复用 `akane-vps` 到云端 personal 后端的加密 SSH 隧道；
3. 精确核对 `/health` 的 `personal / ok / valid`；
4. 以 `-CloudSatellite` 调用正式桌宠启动器；
5. 等待 `open_browser`、`desktop_context_snapshot`、`system_media_snapshot`、`system_media_control` 四项能力在云端目录中变为 `ready`。

token 不写进仓库、命令行、prompt 或普通日志。隧道 PID 和无敏感信息的 SSH 日志位于本机 personal Satellite 数据根的 `run/`、`logs/` 下。已验证但并非该入口创建的 loopback 隧道也可以安全复用。

## 7. 尚未完成，不能对外宣称可用

下面仍属于 M66-E 后续真实切片：

- SSH tunnel 由 personal 一键入口自动建立，但当前仍依赖本机 SSH 配置中的 `akane-vps`；尚未做独立设备注册 UI 或系统登录自启动。
- 托管可见浏览器 `browser_page` 在用户电脑执行；当前云端 runner 不能冒充用户电脑浏览器。
- 本地视觉模型/图片识别。现有 `/desktop-pet/vision/clip` 是截图上传后由后端视觉模型识别，不是本地视觉 executor；云端缺少 vision provider 配置时它仍不可用。
- 本地 Whisper/ASR、GPT-SoVITS、RVC。
- FFmpeg、Demucs、DeepFilterNet、ComfyUI 和其他长任务。
- 通过 ArtifactBroker/ticket 传输这些任务的输入输出字节。
- 长任务 progress/cancel、断线中止、重连 reconcile 和“产物已产生但回执丢失”的恢复。
- finance 的独立 Satellite enrollment；除非明确需要，不复用 personal 设备凭据。

这些能力必须逐个用真实 dependency readiness、真实执行结果、真实失败状态和断线测试验收。协议占位、空 executor、假成功或仅模型可见提示都不算完成。
