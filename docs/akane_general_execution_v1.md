# Akane 通用系统执行能力 v1

Status: capability guide
Date: 2026-08-09
Baseline: `b31f54f add guarded desktop system capabilities`
Scope: Phase 5 最终收口 —— 真实调用链验收、首次启用体验、MemCore/缓存闭环与接入说明。

## 0. 文档定位

本文档说明 Akane 的**通用系统执行能力**（general execution capability）：

- 能力如何定位、何时可用、何时不可用；
- `exec_run` / `exec_status` / `exec_cancel` 三个工具的职责与模型交互方式；
- cursor / output_ref 语义；
- 默认工作区与显式工作区的规则；
- alias 挂载与环境变量白名单；
- 本地部署、云端部署、Cloud + Satellite 的差异；
- 审批策略与高风险动作的一次性 grant；
- 常见 unavailable / error 状态与排查；
- MemCore settlement / 压卡行为；
- 为未来 Skill 复用这三个通用工具的预留方向（本阶段不实现 Skill）。

本文档不是安全沙箱的承诺文档。**`TrustedLocalExecutor` 不是 OS 沙箱。**

## 1. 能力定位

通用系统执行能力让模型可以在后端宿主主机上运行命令，用于查文件、处理数据、
跑脚本或做批量操作。它面向桌面宠物本机模式，只在宿主启用时出现在 schema 中。

边界：

- 能力默认关闭。`EXECUTION_ENABLED=false` 时，`exec_run` / `exec_status` /
  `exec_cancel` **完全不进入模型 schema**，普通文本请求保持原行为。
- 三个工具只在宿主配置启用执行时才注册。
- 运行时 readiness（工作区缺失、provider 不可用）只会变成结构化结果
  （`unavailable` / `workspace_missing`），**不会**增删已配置画像中的工具 schema。
- `TrustedLocalExecutor` 不是 OS 沙箱。命令以宿主用户权限运行，且命令文本本身
  仍可触及该用户有权访问的其它资源。工具契约约束的是 `cwd` 与继承的环境变量，
  不是命令文本里嵌的路径。

## 2. 三个执行工具

| 工具 | 职责 | 风险 | 审批 |
|------|------|------|------|
| `exec_run` | 在受信任执行工作区启动真实命令 | high | 按用户策略（`confirm=always`，可 allow / ask / deny） |
| `exec_status` | 按 run_id 查询运行状态，可在同一次调用内等待新输出，并用 cursor 增量读取输出 | low | 不询问 |
| `exec_cancel` | 请求停止指定 run_id 的命令；只有执行器确认进程组终止后才算成功 | medium | 不询问 |

`exec_run` 参数：

- `command`：要执行的命令或脚本（必填，最长 8192 字符）。
- `cwd`：工作区内相对路径或挂载别名（可选），默认工作区根。
- `timeout_seconds`：命令自身超时（1–600，默认 120）。
- `initial_wait_seconds`：本轮最多等待秒数（1–10，默认 8）；窗口内未结束的命令
  转为 `running` 并返回 run_id。

`exec_run` 不接受环境变量参数（`additionalProperties=false`），环境变量由宿主按
白名单注入。

`exec_status` 的 `wait_seconds` 可选范围为 0–30 秒，默认 0。等待型任务优先传 30：
调用会在出现新输出、进入终态或到达等待上限时立即返回，不需要模型连续发起多次
无结果轮询。它不是后台通知；如果一轮已经结束，Akane 不会在没有新事件时自行醒来。

### 2.1 短命令与长任务

短命令（initial wait 内结束）在本轮直接返回最终状态，模型**不需要**再调用
`exec_status`：

```text
exec_run(command="echo hi")
-> { "status": "completed", "exit_code": 0, "stdout": "hi", ... , "next_cursor": null }
```

长命令（超过 initial wait 仍存活）返回 `running` + run_id + 当前已有输出。模型
明确知道命令尚未完成，之后用 `exec_status` 读取增量、`exec_cancel` 停止：

```text
exec_run(command="python run_big_job.py", initial_wait_seconds=8)
-> { "status": "running", "run_id": "execrun_...", "stdout": "started\n", "next_cursor": "c1...." }

exec_status(run_id="execrun_...", cursor="c1....", wait_seconds=30)
-> { "status": "running", "tail": "progress 40%\n", "next_cursor": "c1...." }

exec_status(run_id="execrun_...", cursor="c1....", wait_seconds=30)
-> { "status": "completed", "exit_code": 0, "tail": "finished\n", "next_cursor": null }
```

普通输出在本次结果中足量返回（首轮预算约 50 KiB / 2000 行）。只有超长或持续
增长的输出才通过 `next_cursor` 按需续读；续读增量预算约 32 KiB / 1000 行。

## 3. cursor 与 output_ref 语义

### 3.1 cursor

- `next_cursor` 是不透明字符串，绑定到**一个** run_id 的绝对字节偏移。
- 每次 `exec_status` 返回自 cursor 之后的新输出片段与新的 `next_cursor`。
- 任务终止后仍可逐页读完剩余输出，**全部读完后 `next_cursor` 才为 null**。
- 普通短命令没有 `next_cursor`，不需要机械翻页。
- 模型不能猜测 cursor 的含义或解码它；只用它做续读。

### 3.2 output_ref

- `output_ref` 只在完整输出**真实保存**时返回，形如 `runlog:<run_id>`。
- 它是**不透明引用**，模型绝不能把它当成本地路径去猜。
- 完整输出可通过 run store 恢复；模型无需知道磁盘路径。

### 3.3 状态诚实

- `running` 表示命令仍在执行，模型不得声称完成。
- `started_at` / `finished_at` 是执行器记录的开始与完成时间，`observed_at` 是本次
  状态查询时间；稍后查询一个已完成任务时，不得把 `observed_at` 说成完成时间。
- `failed` / `timed_out` / `cancelled` / `execution_unknown` 必须如实说明。
- `exec_cancel` 只是取消请求；只有执行器确认进程组终止后才返回 `cancelled`。
  kill 重试耗尽后返回 `termination_unconfirmed`（即 `execution_unknown`），
  后续 `exec_status` 仍能正确解释这一状态。
- 超时只有在确认进程已终止后才会返回 `timed_out`；无法确认终止时返回
  `execution_unknown`，不伪装成 timeout success。

## 4. 默认工作区与显式工作区

`TrustedLocalExecutor` 的 `cwd` 根目录只来自宿主配置，模型永远不能选择目录。

### 4.1 EXECUTION_ENABLED=false

- 不创建任何目录。
- 三个 exec 工具不进入 schema。
- 请求体与未启用执行时逐字节一致，不注入空占位提示或 future-only 状态。

### 4.2 EXECUTION_ENABLED=true，未显式设置 EXECUTION_WORKSPACE_ROOT

- 自动在 Akane 数据目录内创建默认工作区：`DATA_ROOT/execution_workspace`。
- 这是宿主拥有的默认位置，首次启用即可用，不需要手动建目录。
- 默认工作区的绝对路径**不展示给模型**。

### 4.3 EXECUTION_ENABLED=true，显式设置自定义目录

- **不自动创建**。
- 路径不存在时 provider 返回结构化 `workspace_missing`，fail-closed。
- 防止配置拼写错误导致 Akane 在任意位置创建目录。
- 用户主目录、桌面、磁盘根目录不会被自动挂载为工作区。

### 4.4 EXECUTION_RUN_LOG_DIR

- 默认受控目录可自动创建（`STATE_DIR/execution_runlogs`）。
- 显式自定义路径继续按现有安全规则处理。

## 5. alias 挂载

- `cwd` 只支持工作区相对路径或挂载别名。
- 别名格式：`alias:<name>`，`name` 必须是宿主显式配置的挂载。
- 未配置的别名返回结构化失败（`unknown_mount_alias`）。
- `..`、绝对 cwd、symlink 逃逸均被拒绝（`path_traversal_not_allowed` /
  `absolute_path_not_allowed` / `path_escapes_workspace`）。
- 相对 cwd 解析后必须仍落在工作区根内，否则拒绝。

## 6. 环境变量白名单

- 模型**不能**传 `env`。
- 子进程只继承白名单中的宿主环境变量名（默认：PATH、SystemRoot、COMSPEC、
  TEMP、TMP、USERPROFILE、HOME；宿主可用 `EXECUTION_ALLOWED_ENV_NAMES`
  覆盖）。
- 宿主完整 env、API key、token 不得进入模型结果。
- 私有 run output store 对合法 UTF-8 保留完整文本；真正非法的字节序列会替换为
  U+FFFD。模型可见的 Prompt、MemCore、
  stream event、审计摘要统一使用脱敏投影（secret/path 会被替换）。
- run log 的真实磁盘路径绝不返回给模型。

### 6.1 Python / pip 持久环境

- 本地执行器为每个执行工作区维护宿主私有的 `.runtime/python_userbase` 与
  `.runtime/pip_cache`；普通 `python -m pip install ...` 默认安装到这里，并在后续
  命令与重启后继续可用。
- 这避免模型安装的 numpy、matplotlib 等依赖污染运行 Akane 后端的宿主用户
  site-packages。它仍然不是容器或 OS 沙箱；命令显式指定其它路径时，仍受宿主用户
  权限支配。
- 这些物理路径只进入子进程环境，不进入 Prompt、stream event 或 MemCore。

## 7. 部署形态差异

| 形态 | exec_run 在哪运行 |
|------|-------------------|
| 本地部署 | 后端进程所在的主机（宿主机） |
| 云端部署（启用 EXECUTION_ENABLED） | **云主机**上执行命令，不是用户电脑 |
| Cloud + Desktop Satellite | 云端的 exec_run 仍是云主机命令；用户电脑的进程/音量等能力**必须**经过 Desktop Satellite |

提醒：云端启用 `EXECUTION_ENABLED` 执行的是云主机命令，不是用户电脑命令。
用户电脑上的进程、音量、媒体等能力只能通过 Desktop Satellite 提供。

## 8. 审批策略

`exec_run` 是 high-risk、`confirm=always`。审批策略：

| 策略 | 行为 |
|------|------|
| `disabled` | 拒绝执行，返回结构化 `blocked` |
| `ask_each_time` | 每次创建审批请求，模型返回 `approval_required`，等用户决定 |
| `trusted_auto_allow` | 直接执行，但高风险绑定仍按能力策略校验 |

### 8.1 QQ 会话级 Shell 开关

QQ Shell 还受 `EXECUTION_QQ_ENABLED` 宿主总闸和当前会话自己的 `exec_run`
策略约束。会话权限默认关闭，`MASTER_QQ` 对应的主人账号可以发送：

- `/shell on`：只把当前私聊或当前群的 `exec_run` 设为
  `trusted_auto_allow`，后续命令直接执行。
- `/shell ask`：只把当前会话设为 `ask_each_time`。
- `/shell off`：关闭当前会话的 Shell，下一轮不再向模型暴露三个执行工具。
- `/shell status`（或 `/shell`）：查看当前会话状态；该只读命令群成员也可使用。

群聊按 `qq_group_shared_<group_id>` 独立保存；在一个群开启不会影响其他群或主人私聊。
群主、管理员和普通成员都不能修改该开关，除非其 QQ 号同时是 `MASTER_QQ`。主人在群里
发出的显式 `/shell` 控制命令无需 @ Bot；该命令由路由控制面处理，不进入模型，也不依赖
提示词。开启群 Shell 意味着群成员提出的任务可能由模型在 **Bot 所在机器**执行，部署者应
只在可信群开启。

### 8.2 高风险动作的精确绑定与一次性 grant

- 审批 grant 绑定 capability/action、resource（cwd）、device（provider）与
  请求参数的 fingerprint。
- 一个命令的审批**不会**静默覆盖另一个不同命令。
- 一次性 grant 只能用于完全相同的参数组合；会话 / provider 也参与绑定。
- `exec_status` / `exec_cancel` 是 owner-scoped：只能查询 / 停止自己 profile /
  session / provider 启动的 run，不询问。

## 9. 常见 unavailable / error 状态与排查

| 状态 / reason | 含义 | 排查 |
|---------------|------|------|
| `workspace_missing` | 工作区目录不存在 | 显式配置路径拼写错误；或默认目录创建失败 |
| `execution_provider_unconfigured` | 宿主未配置执行 provider | 检查 `EXECUTION_ENABLED` |
| `availability_check_failed` | provider readiness 探测异常 | 看后端日志 |
| `execution_run_capacity_reached` | 运行中的 run 超过容量 | 等待终态 run 回收后再启动 |
| `run_not_found` | run_id 不存在或已过期 | 重新执行；终态结果保留约 10 分钟 |
| `termination_unconfirmed` | kill 重试耗尽，无法确认终止 | 看后端日志；按 `execution_unknown` 处理 |
| `execution_timeout` | 超时且已确认进程终止 | 调大 `timeout_seconds` 或拆分子任务 |
| `cancel_failed` | 取消请求未获确认 | 稍后 `exec_status` 再确认状态 |

## 10. 安全边界与明确不保证事项

不保证、不做：

- `TrustedLocalExecutor` 不是 OS 沙箱。不要用“沙箱”描述它。
- 不自动挂载用户主目录、桌面或磁盘根目录。
- 不为“开箱即用”放宽 cwd 的相对路径、alias、symlink 约束。
- 不执行用户未要求的高风险系统动作。
- 不向模型暴露绝对路径、密钥、宿主环境变量名。
- 不为演示写假成功、假播放、假进度、假状态。

## 11. MemCore settlement / 压卡

- `exec_run` 的 action/result 以 `tool.exec_run.call` / `tool.exec_run.result`
  进入同一个 MemCore turn。
- 当前工具轮里模型能看到 producer-bounded 的真实结果。
- MemCore 只在 assistant final 后做 settlement；当前工具轮不提前压卡。
- assistant final 后，长结果按现有策略（`MEMCORE_OPERATION_PROJECTION_POLICY`
  ，默认 `full_until_raw_compaction`；启用 `compact_after_terminal` 时）压成可
  回读卡片。卡片保留：
  - tool name（`tool: exec_run`）
  - status（真实终态）
  - call_id / source_id 等可恢复引用
  - reload 提示（`open_memory(memory_id=...)`）
- `running` 不会被投影成错误；`failed` / `timed_out` / `cancelled` /
  `execution_unknown` 保留真实状态，不会把超时或未确认的命令记成成功。
- 压卡后不泄漏原始绝对路径或密钥（MemCore observation 走脱敏投影）。
- 不复制第二套 settlement 规则；exec 复用与其它工具相同的 MemCore 闭环。

## 12. 未来 Skill 复用预留（本阶段不实现）

- 这三个通用工具（`exec_run` / `exec_status` / `exec_cancel`）是 Skill 可以复用
  的基础原语：Skill 可以通过 `exec_run` 启动真实脚本、用 `exec_status` 轮询
  进度、用 `exec_cancel` 停止。
- 本阶段**不实现** Skill 系统，不加入尚未实现的 Skill、自动安装或任意系统
  管理承诺。预留方向只写在文档里，不塞进用户可见 / 模型可见链路。

## 13. 配置字段核对

与 `config.py`、`settings_catalog.py` 逐项核对：

| 字段 | 默认 | 说明 |
|------|------|------|
| `EXECUTION_ENABLED` | `false` | 宿主冻结的启用开关；不在 control-center 暴露 |
| `EXECUTION_QQ_ENABLED` | `false` | QQ Shell 宿主总闸；打开后仍需主人按私聊/群聊用 `/shell` 显式授权 |
| `EXECUTION_WORKSPACE_ROOT` | `""` | 空 = `DATA_ROOT/execution_workspace`（自动创建） |
| `EXECUTION_RUN_LOG_DIR` | `""` | 空 = `STATE_DIR/execution_runlogs`（自动创建） |
| `EXECUTION_ALLOWED_ENV_NAMES` | `""` | 空 = 保守默认集；逗号分隔覆盖 |

`settings_catalog.py` 将 `EXECUTION_ENABLED` / `EXECUTION_QQ_ENABLED` /
`EXECUTION_WORKSPACE_ROOT` / `EXECUTION_RUN_LOG_DIR` / `EXECUTION_ALLOWED_ENV_NAMES`
列为 `EXCLUDED_KEYS`：它们是 host-startup 安全边界，只能由部署配置提供，绝不作为
control-center 设置或 live runtime override 暴露。

## 13.1 资源闭环与 QQ 开放

`exec_run` 支持 `input_resources`（按材料索引实际显示的 `doc_*` / `img_*` / `aud_*` /
`vid_*` / `arc_*` / `gen_*` 精确句柄暂存到运行工作区）与 `output_globs`（命令完成后把明确声明的输出登记为
`gen_*`，再经 `send_file` 交付）；QQ 侧需同时开启 `EXECUTION_QQ_ENABLED` 总闸，并由
主人在目标私聊或群聊用 `/shell on` 授权。详见 `docs/akane_execution_resource_loop_v1.md`。

## 14. 相关代码入口

- `companion_v01/execution_specs.py`：三个 ToolSpec 的唯一契约来源
- `companion_v01/execution_run.py`：run store、cursor/output_ref、结果映射
- `companion_v01/execution_local.py`：`TrustedLocalExecutor` 真实进程执行
- `companion_v01/execution_resources.py`：`ExecutionResourceBridge`（暂存/登记）
- `companion_v01/tool_handlers/execution.py`：exec 三个工具 handler 与审批
- `companion_v01/tool_orchestration_engine.py`：invocation 校验、envelope、broker
- `companion_v01/capability_registry.py`：execution capability module 与 schema 选择
- `companion_v01/engine.py`：`_build_execution_provider`（默认工作区自动创建）
- `companion_v01/memcore_integration/manager.py`：`record_tool_batch` 与 MemCore 闭环
- `desktop_pet_next/src-tauri/src/main.rs`：Desktop Satellite 能力（进程/音量/媒体）
