# Akane Shell 资源闭环 v1

Status: capability guide
Date: 2026-08-10
Baseline: 通用执行 v1（`docs/akane_general_execution_v1.md`）
Scope: exec_run 资源暂存与输出登记、send_file 交付、MemCore 轨迹回看、QQ 主账号安全开放。

## 0. 文档定位

本文档说明 Akane **通用执行的资源闭环**：如何把材料索引中的 `doc_*` / `img_*` /
`aud_*` / `vid_*` / `arc_*` 与已有 `gen_*` 资源安全地传入 `exec_run`，如何声明命令输出并登记为新的
`gen_*`，如何用 `send_file` 交付到 QQ 或桌宠，以及如何在 MemCore 里保存轨迹、
压卡并按需回看。它建立在通用执行 v1 之上，不改变执行工具的职责边界。

本文档不是安全沙箱的承诺文档。**Shell 永远运行在承载当前执行 Provider 的机器上：
本机部署就操作本机，云端部署就操作云端；不做隐式跨机器控制。**

## 1. Shell 在哪台机器运行

| 形态 | exec_run 在哪运行 | input_resources / output_globs 作用在哪 |
|------|-------------------|----------------------------------------|
| 本机部署 | 后端进程所在主机 | 该主机的执行工作区 |
| 云端部署 | **云主机** | 云主机的执行工作区 |
| QQ（已开启） | **QQ Bot 后端所在机器** | QQ Bot 后端所在机器的执行工作区 |

QQ 调用的 Shell 运行在 QQ Bot 后端所在机器。Bot 部署于云端时，查询和处理的是云
服务器；**不会隐式访问用户个人电脑**。桌宠本机模式的 Shell 才是宿主机命令。

## 2. 资源如何传入 exec_run

`exec_run` 新增两个可选参数，与 `command` / `cwd` 一起进入批准指纹：

```json
{
  "type": "exec_run",
  "command": "python process.py inputs/source.wav outputs/result.wav",
  "input_resources": [
    {"handle": "aud_001", "as": "inputs/source.wav"}
  ],
  "output_globs": ["outputs/result.wav"]
}
```

### 2.1 input_resources 规则

- `handle` 必须是当前用户 / 会话材料索引中**实际显示**的精确句柄（常见为
  `doc_*`、`img_*`、`aud_*`、`vid_*`、`arc_*`、`gen_*`）。不接受 `latest`
  等会随时间变化的别名；解析逻辑与 `send_file` 使用相同 owner scope。
- `as` 必须是命令工作区内的**安全相对路径**。
  - 拒绝绝对路径、`..`、盘符（`C:`）、UNC（`\\`）、冒号段与 symlink 逃逸。
  - 同一目标名冲突时结构化拒绝，不静默覆盖。
- 输入会**复制**进本次运行的独立工作区，模型只使用自己声明的 `as` 相对路径，
  **不暴露原始存储路径**。
- 资源模式拥有独立 run 工作目录；使用 `input_resources` 或 `output_globs` 时必须
  省略 `cwd`，避免批准内容与实际执行目录不一致。
- 限制：最多 8 个输入；单文件与总大小受宿主限制。
- 每个 run 的暂存与输出相互隔离，后台任务之间不会因文件名冲突互相污染。

### 2.2 output_globs 规则

- 只声明本次命令**明确产出**的路径；不扫描整个工作区猜测产物。
- glob 相对命令工作区展开，全部匹配结果**展开、按规范化相对路径稳定排序、去重**，
  多个输出都会登记，不只取第一个。
- 只允许工作区内的普通文件；拒绝 symlink 逃逸、目录、设备文件与路径穿越。
- 限制：最多 32 个输出；单文件与总大小受宿主限制。

## 3. 输出如何登记为 gen_*

命令完成后，`exec_run` / 后续 `exec_status` 读到终态时，只对 `output_globs`
覆盖的路径调用现有 `GeneratedFileService` 登记，返回现有 `gen_*`，不新建另一套
文件记录。模型可见结果示例：

```json
{
  "status": "completed",
  "run_id": "execrun_...",
  "exit_code": 0,
  "stdout": "conversion finished",
  "generated_resources": [
    {"handle": "gen_001", "name": "result.wav", "media_type": "audio/wav", "size_bytes": 1820431}
  ],
  "artifact_status": "registered",
  "next_action": {"tool": "send_file", "targets": ["gen_001"]}
}
```

- `generated_resources` 只含 `handle` / `name` / `media_type` / `size_bytes`，
  **不含绝对路径**。
- `artifact_status` 与命令状态是**两个正交维度**：

| 命令状态 | artifact_status | 说明 |
|----------|-----------------|------|
| completed | `registered` | 输出已登记为 gen_* |
| completed | `registration_failed` | 命令成功但登记失败（如 `output_not_found` / 不支持的格式） |
| running / failed / timed_out / cancelled | `not_registered` | 未完成不登记不完整产物 |
| 未声明 output_globs | `not_requested` | 本次没有要求登记输出 |

- 登记失败不等于命令失败，也不等于交付成功：模型必须如实区分命令执行状态、
  产物登记状态与文件交付状态。
- 取消 / 超时 / 失败原则上不登记不完整产物；本版不隐式支持部分产物。

## 4. 长任务与幂等登记

- `exec_run` 返回 `running` 时，输入映射与输出声明绑定到 `run_id`。
- 后续 `exec_status` 读到终态（completed）时**只登记一次**产物。
- 重复调用 `exec_status` 幂等：返回相同的 `gen_*`，不会重复生成多个 handle。
- 并发 `exec_status` 也只完成一次登记（host 侧串行化登记临界区并做 owner 校验）；
  不同 run 也不会竞争同一会话的下一个 `gen_*`。
- `run_id` 过期不影响已经登记的 `gen_*`（gen_* 已存在于 GeneratedFileService）。

## 5. 如何用 send_file 交付

`exec_run` 只负责执行与登记产物，**不会自动替模型发送**。登记成功后，模型按
`next_action` 建议调用现有 `send_file`：

```json
{"type": "send_file", "targets": ["gen_001"]}
```

- 多个 `gen_*` 可一次批量交付（`targets` 数组）。
- QQ：`file_ready` 结构化事件 → `qq_gateway.send_generated_files` → OneBot 真实
  上传（`upload_private_file` / `upload_group_file`）。
- 桌宠：`file_ready` 事件携带 delivery_action，工作台真实打开 / 定位 / 保存。
- 只有模型调用 `send_file` 才会发送；没有调用就不会发送。

## 6. 职责区别速查

| 标识 | 职责 | 谁生成 | 是否模型可见 |
|------|------|--------|--------------|
| `doc_*` / `img_*` / `aud_*` / `vid_*` / `arc_*` | 用户上传 / 工作台已有的原始材料 | 附件入库 | 是（handle） |
| `gen_*` | Akane 工具生成的产物 | GeneratedFileService | 是（handle） |
| `run_id` | 一次 exec_run 的运行身份 | 执行 provider | 是（运行期内） |
| `call_id` | 一次工具调用身份（MemCore correlation） | 工具调用链 | 是（卡片内） |
| `source_id` | MemCore 工具结果存储 / reload 键 | MemCore | 是（卡片内） |
| `output_ref` / cursor | 完整命令输出续读 | 执行 provider | 是（不透明引用） |

## 7. MemCore 轨迹、压卡与回看

生命周期：`tool.exec_run.call → tool.exec_run.result → assistant.final → settled
compact card → reload by source_id/call_id`。

- 当前工具轮模型看到实际命令状态、足量 stdout/stderr、`run_id` 与 cursor（需要
  时）、生成的 `gen_*`、以及明确的 `send_file` 建议；**不自动替模型发送**。
- MemCore 只在 assistant final 后 settlement；当前工具轮不提前压卡。
- 压卡后紧凑卡片保留：tool name、真实状态、call_id/source_id 与 reload 提示
  （`open_memory(memory_id=...)`）；卡片不重复内嵌长结果。
- 回看：按卡片里的 `source_id` 重新打开完整历史工具结果，其中保留 run_id 与 gen_*，
  之后仍可再次 `send_file`。
- 不保存：绝对路径、运行日志物理路径、输入资源真实存储路径、环境变量与密钥。
- `full` 模式保持当前完整工具结果语义。

## 8. QQ 安全开放（默认关闭）

```env
EXECUTION_QQ_ENABLED=false
```

开启条件（缺一不可）：

```text
EXECUTION_ENABLED=true
AND EXECUTION_QQ_ENABLED=true
AND 当前身份通过宿主既有执行策略
```

首版安全范围：

- **master 私聊**：允许进入执行批准链（仍 `confirm=always`，逐次批准）。
- **普通私聊用户**：默认禁用。
- **群聊**：默认禁用。
- 不凭 QQ 昵称或展示名判断 master，只认 `MASTER_QQ` 数值身份。
- 不绕过现有 approval redemption；高风险命令仍逐次批准，不复用旧批准。

Schema 稳定性：

- Provider 临时不可用时保留 schema，调用返回结构化 `unavailable`。
- 不因 readiness 探针变化增删工具。
- `EXECUTION_QQ_ENABLED=false` 时完全不向 QQ 注入工具或占位提示。

## 9. 失败场景与下一步

| 场景 | 模型应做什么 |
|------|--------------|
| handle 不存在 / 越权 | 检查句柄、确认用户可见的材料；调整后重试 |
| 不安全 `as` | 改成工作区内安全相对路径后重试 |
| 输出 glob 越界 | 收紧输出声明，只声明工作区内的相对路径 |
| 命令成功但没有输出文件 | artifact_status=`registration_failed`，告知用户交付失败 |
| 命令失败 / 超时 / 取消 | 如实说明未完成，不要声称成功；视情况重试命令 |
| 文件登记失败 | 区分命令成功与登记失败；调整输出格式或声明 |
| QQ 上传失败 | 说明交付失败，不声称文件已发送 |
| Provider 不可用 | 结构化 unavailable，不假装已执行 |

## 10. 本轮明确不做

- 不让云端自动控制用户电脑。
- 不做跨机器分布式 Shell。
- 不把 MemCore 改造成文件数据库。
- 不让 Shell 直接调用 QQ SDK。
- 不自动扫描并登记整个目录。
- 不退役现有媒体 / 文档 / 播放器等专用工具。
- 不设计 Skill 系统。
- 不为每种命令编写专用产物逻辑。
