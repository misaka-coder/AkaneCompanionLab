# 工具结果与模型续推

日期：2026-09-05。代码和本地回归已接入；真实 QQ/桌宠部署验收另行进行。

## 三个独立问题

- 工具结果始终和调用配对保存，不因少请求一次模型而丢失。
- 是否继续模型由宿主回合调度决定，不等于是否对用户发文字。
- 后台任务的 `completion_mode` 控制唤醒，`memory_mode` 控制完成事实是否进入时间线；Job 本身始终持久化。

## 同步工具

默认需要模型继续。插件可在 `CapabilityDescriptor.raw` 声明
`{"model_followup":"optional"}`，允许模型对最终动作传 `finish_turn:true`。
不传仍继续；宿主增加并消费该参数，插件业务函数不接收它。
该参数仅用于同步工具；长任务继续使用 `completion_mode`，不暴露无效的 `finish_turn` 参数。
此策略保存在已有描述符中，沿用隔离 worker 的描述符传输与发布，不新增注册表或工具。
插件自有输入不得与这个可选宿主参数同名；未启用该策略的插件不受影响。

OneBot 的混合动作工具也提供这一参数，但只有真实成功的可见动作可结束。
读取消息、查资料不因此结束。插件调用成功不等于已投递，不从插件内容伪造可见交付回执。

宿主先执行并记录完整批次，然后判断：每项都明确允许结束、没有失败、模型图片输入或待交付产物，才进入既有静默收尾路径。
模型声明“这是最后一步”，宿主检查执行事实；宿主不从用户文字猜测任务是否完成。
收尾仍经过用户插话/停止的正常安全边界。子代理复用工具执行，但必须提交任务报告；这个开关不会代替子代理报告。

MemCore 使用公开 `complete_turn(append_final=False)` 原子完成标注和关闭回合，仍检查所有 action/result 已配对。不新增助手正文、伪造 JSON 或隐藏占位消息；普通模型静默 JSON 仍保存其真实输出。终态投影收紧与 raw 压缩继续通过同一生命周期执行。

内置工具使用同一 `optional_followup_schema` / `FINISH_TURN_PARAMETER` 定义参数，执行器只在实际成功时设置内部 `ToolExecutionResult.finish_turn`。
没有必要为所有工具增加字段；查询、Shell、文件编辑等默认保持原样。

## 后台工具

| completion_mode | memory_mode | 终态处理 |
|---|---|---|
| agent | timeline | 普通 Agent 回合并记录事件 |
| agent | current_turn | 完成事件供此次 Agent 回合使用 |
| silent | timeline | 通过宿主已有 MemCore 事件入口记录，不唤醒/投递 |
| silent | current_turn | 只保留 Job 终态，由原调用方读取 |

完成队列的 delivered 表示完成策略已处理；不等于 QQ 消息发送成功。
timeline 写入失败保留 pending 和错误，现有恢复入口重试；同一个完成事件 ID 防止重复入库。
不增加定时扫描器或第二套完成状态机。
Shell 在同步观察阶段使用 silent/current_turn，实际返回 running 后由已有 arm 操作原子升级为 agent/timeline。
子代理内部长任务使用 silent/current_turn，由 TaskWork 写入自己的续跑上下文，不能误写父会话。

## 清理与验证边界

删除旧文档中未实现的 `visible_effect_delivered/needs_model_followup` 双字段示意；删除插件结果尾部强制再说一遍的提示；删除 Job Store 中无真实消费者的 direct 模式。
不添加动态系统前缀，只有本次版本的可选工具参数发生变化，普通后续轮次保持 schema 稳定。
本地测试覆盖普通/流式主回合、结果配对、失败续推、混合批次、插件参数隔离、后台事件持久化与幂等、Shell 和子代理回归。
不将模拟传输当作真实平台已验收。

## 本地验收与发布依赖

- Akane 聚焦回归：300 项通过，覆盖本文件中的主回合、插件、Job、子代理、MemCore 和 JSON 收尾链路。
- MemCore 全量回归：运行 606 项，无失败、5 项跳过；新增 4 项覆盖无回复关闭、幂等、未配对阻止关闭、跨 namespace 拒绝、settlement 与 raw 压缩。
- MemCore 构建通过；本轮修改文件的 Ruff 检查/格式检查通过。仓库全局仍有其他文件的既有 lint/格式问题，没有顺手修改。
- 两个仓库 `git diff --check` 通过。QQ/桌宠真实传输仍待部署实测，不把 mock 端口作为线上成功证据。
- 发布时必须同时包含 MemCore 的公开 `append_final` 契约与 V6 助手作者保真投影。`42658a3` 只有前者，不能单独作为合格发布包；修复版本为 `bd001af` / `0.1.0+chatv6.1`。使用实际服务解释器运行 `scripts/check_memcore_runtime_contract.py`，同时验证接口、投影版本以及纯文本/JSON 助手历史不被加时间戳；不可只替换 Akane 宿主代码或只核对提交号。
