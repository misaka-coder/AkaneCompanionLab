# 一次性子代理云端部署验收（2026-09-05）

## 结果和范围

生产代码版本：`7b6ec17`。统一 Host 已切换至该版本，个人与金融 bot 的 QQ 自检均为 `connected`，健康检查为 `ok`。没有修改账号、权限、插件配置或稳定提示词配置，也没有恢复旧数据库覆盖新消息。

用户授权在群聊测试。验收通过带明确“Codex 部署自动验收、主人授权”标记的认证 QQ webhook 输入触发，不冒充手动 QQ 发言。后续使用真实云端模型、工具 Broker、子进程、MemCore、会话队列与 NapCat 群消息投递；不是模拟模型回执。

## 实测发现并修复的两处主链路缺陷

1. `1c55f6a`：延迟任务签发会话引用时误将 Engine 会话字符串当作 QQ 数字用户号。改从权威 `qq_delivery_context` 获取渠道目标；角色和宿主记录的因果发起人仍保留。子代理、后台工具与插件复用该修复。
2. `7b6ec17`：一次性完成事件没有用户 source，普通响应准备却要求当前 source，导致事件已入队但模型没能回复。改用不可见的宿主回合作为工具与最终回复归属，并显式允许瞬时输入读取历史。事件正文只参与当前请求，角色回复正常存储；不是将事件伪装成普通用户消息，也不复制另一套推理/发送链路。

第一轮真实派发失败；第二轮 child 成功但父回传失败。只有第三轮完成下面的全部验收，不能把前两轮后台终态算作用户交付成功。

## 最终群聊证据

- 群：`872732158`；父角色：`reimu`。
- 标记：`SUBAGENT_SMOKE_7B6EC17`。
- Job：`job_e8d33bdbb47e492eba66d195e1797bc2`。
- child：`subagent_cdc011bcd314b69913394ede26db4921`。
- exec：`execrun_9a1f22c3b63a43608047026e1c59655e`。

实际工具调用为一条只读命令：

```sh
python3 -c "import os; print('SUBAGENT_SMOKE_7B6EC17', 23*19); print(os.getcwd())"
```

工具回执中的 stdout 是 `SUBAGENT_SMOKE_7B6EC17 437` 和调用时继承的工作目录，退出码为 0；没有文件写入或安装操作。child 的真实调用及配对结果均保存在独立 MemCore 会话中，并非仅依据模型总结判定成功。

以下为服务端 UTC 时间（本地北京时间加 8 小时）：

| 时间 | 观测 |
| --- | --- |
| 10:32:43.582 | Job 登记 |
| 10:32:43.600 | child 启动 |
| 10:32:48 | 群里收到父角色“已派出”及等待结果说明 |
| 10:32:56.655 | child 成功结束，约 13 秒 |
| 10:33:06 | 群里收到父角色二次回复，包含标记、437、目录、退出码 |
| 10:33:28 | 完成事件的父回合结束，队列 committed，日志 sent=true |

父首次回复早于 child 完成。最终群历史确实出现第二次结果回复；不只检查 `completion_status=delivered`，该字段表示完成事件交接，不能独自证明 QQ 用户已经收到消息。

父首次与完成后的原始输出均包含 `emotion/reply_medium/speech`；群历史呈现正常文字而非原始 JSON。首次派发回合的表情图片发送回执成功。隐藏宿主事件记录为 `prompt_visible=0, semanticize=0`，最终角色回复正常可见。未进行 QQ 客户端截图或桌宠视觉/TTS 验收。

## 回归和回滚

Windows 与云端 Linux 均通过以下 367 项测试：

```sh
python -m unittest tests.test_memcore_integration tests.test_turn_mainline_contract tests.test_plugin_agent_events tests.test_subagent_engine tests.test_host_tool_jobs tests.test_host_execution_jobs tests.test_backend_route_modules tests.test_final_json_repair -q
```

本地语法检查和 `git diff --check` 通过。另修正一处 QQ 测试对部署机器 MASTER_QQ 环境的隐式依赖（`eddfcb9`），没有调整生产身份规则。

部署前对两个 bot 的主应用 SQLite 数据库做一致性备份并验证 `quick_check=ok`；保留原发布目录和服务启动配置。正常代码回滚不应恢复数据库，以免丢失上线后消息。用户已有角色包文档、测试和 `work/` 改动未纳入本轮提交。

## 未验收和后续维护

- 桌宠真实客户端、复杂长任务、多 child 压力测试及真实任务中重启/取消，不由这一次只读群 smoke 证明。
- 观察到停机时 `plugin_runtime_close_incomplete`、MemCore 活动任务延迟关闭告警；进程随后退出，新服务健康。本轮未修复关闭生命周期问题。
- 云盘剩余约 1.1 GB（使用率 98%），应另做有目标的空间清理；本轮保留备份与回滚版本，没有广泛删除旧数据。
- 并发群消息在完成事件期间正常入队并落库，但批量被动记录日志出现 `group_id=0 / not_group`，需后续核查是否丢失主动注意力评估的渠道信息；本轮未将它判定为已修复。
