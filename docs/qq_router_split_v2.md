# QQ 路由职责拆分 v2

## 切片与边界

本轮从约 5,083 行的 `companion_v01/routes/qq.py` 继续拆分。入口行数是观察指标，边界、调用链与原有行为优先。

| 切片 | 模块 | 职责 |
| --- | --- | --- |
| 投递 | `qq_reply_delivery.py`、`qq_voice_delivery.py`、`qq_stream_delivery.py` | 上轮已验证的文字/语音发送、音频缓存、分段与防重 |
| 回合流 | `qq_turn_streaming.py`、`qq_artifact_delivery.py` | 模型流消费、工具与文件投递、可见回执、完成状态记录 |
| 内容 | `qq_inbound_content.py`、`qq_workspace_messages.py` | 引用与合并转发证据、附件提示、工作台材料摘要 |
| 命令 | `qq_command_handlers.py`、`qq_command_cards.py` | 按原顺序处理内置/插件命令、状态卡片和文字兜底 |
| 会话调度 | `qq_session_work.py`、`qq_session_queue.py`、`qq_turn_runtime.py` | 队列载荷、身份恢复、准入、停止/追加输入与回合执行 |
| 主动观察 | `qq_attention_runtime.py` | 观察代次、参与者、图片证据、冷却与过期回合取消 |
| 插件与后续 | `qq_plugin_input.py`、`qq_plugin_turns.py`、`qq_followups.py` | 入站授权租约、事件发布、插件回合接入、授权与视觉后续处理 |
| 入站事件 | `qq_inbound_events.py`、`qq_passive_messages.py`、`qq_inbound_media.py` | 当前事件协调、被动历史记录、媒体证据解析与本轮像素准备 |

无状态模块不反向导入路由，不保存当前 Bot、角色或用户；调用者传入实际网关、引擎和当前上下文。原有私有辅助名称通过路由中的直接导入保留，测试与已有调用者可继续使用。

第二个切片使入口降至 3,918 行。25 个迁移函数的参数、返回注解和主体 AST 与切片前一致；相对导入按新的模块位置调整，原有说明性注释保留。

投递切片提交为 `eb916597`，280 项回归通过。内容与流式切片的 40 项定向测试和 209 项路由、语音、注意力、插件及完成通知回归全部通过。

内容切片提交为 `fc604578`。命令切片使入口降至 2,983 行；每类命令拥有独立处理方法，帮助仍在戳一戳处理前执行，普通命令仍在其后执行。换装与经济动作使用每次请求独立的结果对象，将真实处理的动作说明继续交给模型回合。八处重复发送、指标、日志和 HTTP 结果汇总统一到一个辅助方法，各命令原有成功/失败判定保持一致。卡片测试补丁迁至真实实现模块。最终 155 项命令、商店卡片、定向对话和后端路由回归全部通过。

命令切片提交为 `c9913c03`。调度切片使入口降至 1,067 行；每次路由组装创建独立的注意力、插件租约、队列适配和回合运行对象。队列与注意力用两个回合回调和一个队列检查回调连接同一个运行器，不相互导入实现模块，不创建全局 Bot 或用户上下文。共享的持久队列仍通过原注册方式接入；两个 HTTP 别名继续使用原有插件收据所有者。运行器的模型流函数通过显式回调注入，保留原来的可替换测试入口。

注意力对象单独拥有最新快照、待绑定图片和控制票据；票据绑定/释放通过方法执行，旧任务不能清除新代次快照。新增同会话标识的两个 Bot 图片隔离测试。调度、群注意力、插件入站回合、插件事件、完成通知、多 Bot 和后端路由的 168 项回归通过；模块边界及插件别名检查单独运行。

调度切片提交为 `0842bef2`。最后的入站切片使入口收敛到 **303 行**，主要保留组件组装、诊断路由、Webhook 鉴权、开关状态、JSON 对象校验与 Bot 身份校验。被动记录和媒体准备分别拥有独立处理器；入站事件处理器按既有顺序协调它们，媒体准备结果与命令动作结果只存在于本次请求，不覆盖共享服务。连续请求、定向角色与服务对话、同成员追加输入的 18 项定向回归通过。

异常响应和事件日志保留原因类型，不回传未预期异常的原始文本；新增哨兵测试验证凭据和内部路径片段不会进入公开响应或错误日志。非法 JSON 和非对象载荷在调用会话处理器前被拦截。原有 55 个辅助函数/类与路由入口保持可调用，参数和返回注解核对通过；明确删除的两个无调用者相似度函数除外。

## 最终入口与实际变化

模块之间通过明确的对象或回调交接引擎、网关和当前会话，不通过路由反向导入或修改模块全局变量转发调用。原有命令文字、QQ 消息分段、语音选择、发送失败兜底、角色/调用者权限、队列去重和完成通知规则保留。处理异常时，用户收到明确的通用失败信息，原因类型用于诊断。

303 行表示入口文件的行数，包含旧辅助名称的兼容导入；其余实现迁到对应职责模块。总业务代码继续保存在职责模块中，确认没有调用者的代码和重复结果汇总已清理。

## 验证与限制

每个切片运行对应行为测试、编译与 `git diff --check`，通过后单独提交。测试覆盖发送失败、空回复、重复片段、停止、排队、身份和角色隔离、引用来源和多 Bot。

最终队列与调用链组合回归 254 项全部通过，覆盖后端路由、QQ 插件真实组装、插件入站授权、完成批次、多 Bot、命令、会话数据库、队列关闭、任务撤回和模型取消。

最终投递与入站组合回归 167 项全部通过，覆盖模块边界、公开错误脱敏、语音与缓存、防重、投递身份、群注意力、消息准入、引用来源、TTS 选择、MemCore 投递结果、插件静默、文字卡片、商店卡片和定向对话。两组共 **421 项测试执行全部通过**，没有将各切片中的重复执行再累计到这个数。

两组完整复现命令：

```bash
python -m unittest tests.test_qq_delivery_modules tests.test_qq_inbound_runtime tests.test_qq_voice_delivery tests.test_qq_stream_replay tests.test_qq_delivery_identity_contract tests.test_qq_group_identity tests.test_qq_group_attention tests.test_qq_channelcore_admission tests.test_qq_forward_source_binding tests.test_tts_provider_selection tests.test_memcore_delivery_outcome tests.test_plugin_event_silence tests.test_text_cards tests.test_qq_commerce_cards tests.test_qq_directed_dialogue
python -m unittest tests.test_backend_route_modules tests.test_qq_plugin_runtime_wiring tests.test_plugin_qq_inbound_turns tests.test_host_completion_batch tests.test_qq_multi_bot_dispatch tests.test_qq_channelcore_integration tests.test_qq_commands tests.test_plugin_agent_events tests.test_turn_coordination tests.test_session_inbox tests.test_session_completion_batches tests.test_session_queue_shutdown tests.test_plugin_completion_revocation tests.test_completion_model_cancellation
```

所有新模块和入口编译通过，暂存变更的 `git diff --check` 通过。前端没有本轮改动。

真实 NapCat/QQ、桌面音频和外部 TTS 尚未联调；本轮不宣称性能提升。商店资源接入等工作区已有改动保持独立。

商店资源接入原来位于路由内的卡片函数中，拆分后保留在 `qq_command_cards.py` 的工作区改动中；该资源参数没有并入本轮提交。其他商店、前端、技能和工具改动保持在原工作区。
