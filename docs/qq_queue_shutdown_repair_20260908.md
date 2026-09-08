# QQ 持久队列停机修复（2026-09-08）

状态：本地及云端候选验证完成，2026-09-08 已部署云端个人 / 财经共用 Host。

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

云端候选相关套件 327 项通过。引用群图片路由的测试 fixture 显式配置 QQ 网关，避免测试依赖开发机 `.env`。

## 实际发布

- 从实际线上 d2b88ef 基底仅应用群身份 02d42b3 与队列停机 cbdd0c3 的补丁，以及上述测试 fixture；未顺带发布其他本地提交。
- 临时封闭两条 NapCat webhook 入口、暂停个人 timer 插件，等待旧 Nginx 请求退出，以及会话队头、claimed、Host jobs 持续空闲 30 秒。延迟队头之后的 queued 项不误算为可执行队头。
- 两套主库 SQLite backup / quick_check / gzip 回读摘要一致后，旧进程正常退出（systemd Result=success，Application shutdown complete），切换新进程并确认 health=ok。
- 消息入口及个人 timer 已恢复；财经插件仍 disabled，5 条 queued 项的状态、可用时间和正文摘要与切换前一致，无重放、删除或历史清理。
- 在线队列、运行时、收件箱和 QQ 网关四个核心文件的 SHA-256 与本地已验证代码一致。
- 本次观察到的是旧版本退出、新版本启动；未再对新版本进行第二次生产重启，也未执行真实 QQ 发信或真实模型语义验收。

后续用户要求恢复财经 Bot 时，已对 cbdd0c3 版本进行一次有序生产重启：先封闭 webhook、暂停个人 timer、等待在途任务空闲，退出结果 success，再恢复财经维护清单中的两条未开始 queued 项以及插件启用配置后启动。新队列停止路径在该次空闲/延迟队头场景实际执行；未宣称生产中的模型长任务强制中断已通过验收。

## 限制与发布要求

- 不保证 SIGKILL/主机掉电后的外部动作可安全重放；保留既有“不确定结果不自动重试”的边界。
- 栅栏是单向的，停止后恢复服务应建立新的 runtime，不在原队列上重新开放。
- 本次首次发布仍须处理旧进程不含此修复的事实；不能仅检查一瞬间 `claimed=0` 就宣称已安全排空。
- 不修改正式聊天历史、财经维护暂停、桌宠 UI/TTS 或 MemCore 包。
