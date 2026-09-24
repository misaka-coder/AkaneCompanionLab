# P4 后续模型处理迁移

起始基线提交：`5f66425`（P3d）；SDK **0.6.0** 引入结果级 `followup`。
新作者使用 `@plugin.tool(followup="required")` 和
`Result(value=data, followup="none")`。同步与后台结果共用
`tool_continuation.py` 的一个决策入口，程序调用、明确消费者、错误和撤销优先级相同。

| 旧入口 | 0.6 的归宿 | 删除目标 |
|---|---|---|
| `raw.model_followup=required/optional` | 薄适配为 `required/auto` | SDK 1.0.0 破坏性发布 |
| 插件输入的宿主 `finish_turn` 注入 | 已删除；新 schema 完全属于业务 | 无第二套注入实现 |
| 已记录的 V1 slot 调用顶层 `finish_turn` | 仅旧 optional、sync、无完整 schema 且无同名业务 slot 时解释 | SDK 1.0.0 |
| 插件 `raw.completion_mode=agent/silent` | 薄适配为 `required/none` | SDK 1.0.0；第一方八种后台插件已改用 `followup` |
| HostJob 的 `completion_mode` 存储列 | 旧数据库/非插件 Job 投递兼容；新模型 Job 始终保存完成事实，实际续答由结果决定 | 随宿主存储迁移单独处理，不是新的公开插件字段 |
| OneBot 原生动作的 `finish_turn` | 宿主动作适配为同一 `auto` 决策 | 不属于公开插件 SDK；没有独立续答布尔结果 |

`none` 不等于抛弃结果，不吞掉错误，不取消同批其他消费者，也不覆盖显式
`request_turn`。纯程序消费者总是取得值/错误，不因工具默认 required 而请求模型。
`auto` 只有在已有最终动作允许结束且宿主管理交付时结束；未知消费需求继续处理。
完整 schema（包括闭合对象和 `$ref`）与所有 followup 模式兼容，业务字段
`finish_turn` 不再与宿主控制字段冲突。P0b-2 的临时拒绝规则已删除。

后台结果同时保存规范 JSON 与最终 followup，摘要只是预览。结果与渠道回执分开：
执行 succeeded 不代表发送成功；队列受理不代表窗口显示、TTS 播放或联系人阅读。
`delivery_receipt.model_status` 与 `delivery_status` 保留不同结果；未知异常不假报
发送成功，也不自动重跑已完成动作。混合批次只把需要模型的结果放进模型输入，
none 的显式文件交付走同一个正常渠道。父回合停止、插件能力移除会持久撤销已接收
Job 的后续工作；历史规范结果不删除，重新启用不会恢复这些旧完成通知。

P4b 已修复停用/重启用后的迟到 handler 准入：handler 捕获宿主调用授权，同名能力
重新启用会得到新授权，旧 handler 永不复用它。后台作业在当前宿主存活期间保留
已准入的 handler，创建前后的撤销和执行前复核都会持久撤销该 Job；不因重新解析
工具而获得新授权。进行中调用等待真实清理，抑制取消并完成的结果仍保存，但不再
从该结果继续发送文件或请求模型。

P4c 将普通升级的版本保留接入实际同步/流式模型回合、独立子任务、后台作业和
跨 worker 子调用。当前回合继续使用已捕获的实现、提示块与 skill；独立新回合使用
新版本。同一回合首次安装的新插件可以加入，已经见过的插件不会在升级途中换代。
后台作业从准入到入库、排队和执行均保留原版本；父回合结束不提前释放作业的依赖。
显式停用、能力移除或所有者切换仍撤销旧授权，重新启用不复活旧调用。

发布成功与旧进程停止分别报告：`cleanup_status=draining` 表示已有消费者仍持有旧
进程，不表示执行已经停止。退役进程在真实调用排空且回合/作业释放后停止；关闭
被取消时也先完成实际清理。安装器按同一运行时记录保留旧 release 文件，避免延迟
导入失效；进程退役后的残留目录在下一次管理协调（包括下次启动协调）时回收。
卸载可以返回 `removed_cleanup_pending / plugin_workers_draining`，权限已撤销，
文件仍用于正在收尾的真实调用。物理路径只在宿主内部传递，不加入公开状态。

不承诺跨宿主重启继续运行原 worker；恢复作业沿用已有恢复规则。P4d1–P4d5 已将
完成批次等待、模型重试、实际工具派发与逐项交付的撤权检查接回现有权威；撤销后
保留真实结果与实际模型参与，禁止迟到发送。桌面文件 ACK 只表示入队。实现与验证
见[实施记录](plugin_system_v2_progress.md)，不以受控传输替代真实窗口验收；P5 按
优化方案中的 P2 前置继续推进。

管理查询：`GET /admin/plugins/jobs/{job_id}?profile_user_id=PROFILE&session_id=SESSION`，
使用原有管理认证，并按两个 owner 字段查询。它只读已有任务；参数不是执行授权。
响应不包括宿主 job payload、数据库位置、租约令牌或不透明投递凭据。
控制中心的统一结果 UI 属于 P9。

兼容回归保留旧调用的实际参数形状；第一方不得再新增旧声明，检查入口为
`tests/test_plugin_legacy_migration_window.py`。独立新项目见
[内容变化检查](../examples/plugins/akane_sdk_change_check/README.md)，接口说明见
[公开 SDK](../akane_plugin/README.md)。
