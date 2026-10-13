# 语音 Host 职责拆分

本片把 `companion_v01/voice_runtime/host.py` 从 908 行收为 35 行历史导入入口，实际实现放进 `host_runtime/`。调用链仍使用同一个 `AkaneVoiceRuntimeHost`；结果类、协议、方法签名及原入口中的历史名字保持可用。

## 职责导航

| 文件 | 职责 |
|---|---|
| `host.py` | 历史 Host、结果类型、协议及辅助导入的稳定入口 |
| `host_runtime/runtime.py` | 唯一会话 snapshot、恢复身份校验、事件归约及先落盘后推进 |
| `host_runtime/contracts.py` | 命令、投影、回执、推进结果及 journal/executor/projection 协议 |
| `host_runtime/commands.py` | 执行器结果校验、未知结果恢复及每次只推进一个命令层级 |
| `host_runtime/receipts.py` | 持久命令回执恢复、已保存 observation 的补偿和完成确认 |
| `host_runtime/projections.py` | 按原顺序投递 MemCore/宿主投影并确认，失败保留待处理项 |
| `host_runtime/ports.py` | 既有真实端口调用、结果类型校验及结构化异常边界 |

四个职责 mixin 使用同一个 Host 的 snapshot 和端口，没有另建会话状态或缓存。生产层 `production_runtime/assembly.py` 中原有的 `_SerializedVoiceRuntimeHost` 继续持有同一把 `RLock`；Host 不新增第二把锁。六个实现文件的纯迁移规模为 59–248 行。

事件及其投影仍原子落盘后才推进 snapshot，命令仍先保存 intent、执行外部操作，再整批保存 observation 并逐条归约。重启只补偿既有待确认回执和投影。无法确认的 TTS/播放结果仍保持未知，不重合成或自动重播；继续遵守 `plugin_tts_service_execution_plan_20260911.md` §4.4。持久化 schema、音频制品、客户端回执及主人私聊状态归属沿用原实现。

## 纯迁移验证

拆分前和纯迁移后的相同 182 项检查均完成：181 项通过，一项因当前 Windows 文件符号链接不可用而跳过。检查覆盖 Host、持久端口及故障边界、TTS、播放上下文和回执边界、生产装配及故障边界、持续通话路由和冷导入。

50 个历史方法 AST 完全一致；13 个历史类的方法签名、运行时类型注解解析、32 个历史模块绑定及 `__all__` 状态通过核对。编译、Ruff 正确性检查与 `git diff --check` 通过。

未做真实桌面、音频设备、外部 ASR/模型/TTS 验收，也未测量性能。未重启日常运行中的服务。本片只修改 Host 实现和职责导航，工作区其他改动保留。

## 失败状态和安全恢复

在保留上述顺序的前提下，本片修复以下可复现的边界：

- observation 已接受但命令完成回执写入硬失败时，`drive_once` 汇总为 `failed`，保留原原因和未完成回执，不再误报成功。
- observation 的 journal 追加返回可重试失败时，把该标记传递到回执恢复及外层推进结果，得到 `deferred`。snapshot 不推进，整批已保存的 observation 留待恢复；非重试故障仍得到 `failed`。
- 已接受的重复 observation 不再被算成命令执行失败，仍沿用原 event/projection 身份去重，后续完成回执失败也不会被重复状态遮蔽。
- observation 序列化与 journal 调用使用独立异常边界。端口在提交后抛出 `TypeError`/`ValueError` 时，返回 `command_observation_store_failed`，保留执行 intent 并从实际回执恢复；不再误报数据不可序列化。异常正文不进入返回值。
- 执行器同时声称 `succeeded`/`not_started` 和结果未知时，归为 `invalid_command_outcome_result`。保留执行回执，不释放后重新执行。正常、可确认的 `not_started` 仍可安全重试。
- 无效的 observation payload 返回结构化未知结果，不在校验阶段抛出未处理错误。

journal 追加、投影确认和投影投递的三段重复结果校验统一复用原有 helper，原类型/状态原因码保持一致。没有改变结果类、公开签名、schema 或新增恢复数据库。

## 最终验证

新增 `tests/test_voice_host_boundaries.py` 的 16 项检查使用实际临时 SQLite journal、完整合法语音事件、可控执行器和投影接收端。注入完成确认硬/软失败、observation 追加失败、提交后异常、矛盾执行结果和重复中间 observation，并通过 journal 重放创建新 Host，核对执行器调用次数、snapshot 和回执阶段。

对本轮开始时保存的 908 行原始代码执行同一组检查：九项失败，七个控制项通过。最终 16 项均通过。控制项还确认序列化失败不调用存储、真实未开始时允许一次执行，以及无效 journal/投影/确认结果仍保持原原因、不推进状态并保留投影身份。

| 检查组 | 项目数 | 结果 |
|---|---:|---|
| Host、持久端口及边界、TTS、播放、生产装配及边界、持续通话路由、冷导入和新增 Host 边界 | 198 | 197 通过，一项因 Windows 文件符号链接不可用跳过 |
| ASR 桥接、提供者、实时轮次、实时路由及边界、传输契约 | 38 | 全部通过 |
| 总计 | 236 | 235 通过，一项跳过 |

最终 50 个历史方法中，四个因修复变化，三个因统一结果校验变化，其余 43 个 AST 保持一致。13 个历史类、32 个历史绑定、方法签名、运行时类型注解解析及 `__all__` 状态通过核对；六个实现模块最大 248 行。

编译、Ruff 正确性检查、工作区及暂存区 `git diff --check` 通过。本轮开始时记录的 361 个已有修改/未跟踪文件内容保持一致。真实桌面、音频设备和外部服务验收限制仍如前述。
