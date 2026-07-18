# Desktop Satellite 本地能力 V1

状态：**第一批真实短任务能力已完成本地代码与自动化测试；尚未部署到云端 personal 实例。**

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
- 云端结果清洗递归过滤 `path`、`absolutePath`、`localPath`、`token`、`secret`、`password`、`authorization` 及其常见复合键。
- 字符串、列表、对象大小和嵌套深度有界；未知对象不会原样进入 prompt。
- `desktop_context_snapshot` 只返回前台窗口有限元数据；Windows 进程查询得到的完整可执行路径会先裁成文件名。
- personal 与 finance 的 Satellite token、instance、lease 不共享。本轮只准备 personal 代码链，不自动把同一台电脑绑定给 finance。

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

## 6. 尚未完成，不能对外宣称可用

下面仍属于 M66-E 后续真实切片：

- 云端 personal token 配置、同一 token 的本机安全注入，以及 personal 真实 WSS smoke。
- 托管可见浏览器 `browser_page` 在用户电脑执行；当前云端 runner 不能冒充用户电脑浏览器。
- 本地视觉模型/图片识别。现有 `/desktop-pet/vision/clip` 是截图上传后由后端视觉模型识别，不是本地视觉 executor；云端缺少 vision provider 配置时它仍不可用。
- 本地 Whisper/ASR、GPT-SoVITS、RVC。
- FFmpeg、Demucs、DeepFilterNet、ComfyUI 和其他长任务。
- 通过 ArtifactBroker/ticket 传输这些任务的输入输出字节。
- 长任务 progress/cancel、断线中止、重连 reconcile 和“产物已产生但回执丢失”的恢复。
- finance 的独立 Satellite enrollment；除非明确需要，不复用 personal 设备凭据。

这些能力必须逐个用真实 dependency readiness、真实执行结果、真实失败状态和断线测试验收。协议占位、空 executor、假成功或仅模型可见提示都不算完成。
