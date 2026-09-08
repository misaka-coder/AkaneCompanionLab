# QQ 持久队列停机修复（2026-09-08）

状态：本地验证完成，尚未部署。

## 修复边界

`BotRuntime.stop()` 原先直接通知引擎关停、取消 QQ follow-up 并关闭插件和引擎，未等待独立的 `DurableSessionWorkQueue`。`claimed=0` 也不能排除正在执行数据库 claim 的线程。

- 队列新增单向停机栅栏：拒绝新入队/主动 claim，不恢复或启动新 worker；既有已入库但未处理的消息原样保留。
- 延迟队头通过停机事件唤醒，不必睡到原定时间；与停机竞争的 claim 完成后归还队列，不执行 handler。
- 既有 handler 自然完成并结算，停机不会取消仍在 `to_thread` 中执行的模型工作。
- 跟踪实际数据库线程；调用方取消并不意味着线程已退出。关闭时也核对当前 worker 交给直接回合的 steer claims。
- 只有队列真正停止，运行时才继续关闭依赖；超时/异常返回结构化 degraded，不关闭在途回合仍需使用的引擎或插件，并允许随后重试 stop。

## 验证

78 项测试通过：`test_session_queue_shutdown`、`test_session_inbox`、`test_session_completion_batches`、`test_host_completion_batch`、`test_bot_runtime`、`test_instance_writer_shutdown`。

新增 7 项测试覆盖真实 SQLite、线程执行中超时、已接受消息保留、延迟队头、claim 竞争、直接回合 steer、被取消的入队等待者、运行时关闭顺序与生命周期取消。`py_compile`、`git diff --check` 通过。

## 限制与发布要求

- 不保证 SIGKILL/主机掉电后的外部动作可安全重放；保留既有“不确定结果不自动重试”的边界。
- 栅栏是单向的，停止后恢复服务应建立新的 runtime，不在原队列上重新开放。
- 本次首次发布仍须处理旧进程不含此修复的事实；不能仅检查一瞬间 `claimed=0` 就宣称已安全排空。
- 不修改正式聊天历史、财经维护暂停、桌宠 UI/TTS 或 MemCore 包。
