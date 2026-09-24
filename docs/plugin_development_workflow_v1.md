# Akane 插件开发工作流 V1

## 目标

模型开发插件时只需要一条权威路径：独立代码项目、当前 release SDK、隔离暂存、精确权限确认、原子安装和真实运行验收。普通聊天不加载 SDK 手册；只有 `plugin-development` Skill 的一行路由描述常驻 Skill 目录。

## 唯一工作流

1. 用 `manage_project_workspace(create/open/select)` 建立当前代码目录。
2. 从只读 `alias:akane-sdk` 选择最接近需求的当前 release 样例，不搜索宿主物理 release。
3. 在项目内实现并编写普通 `unittest`，直接引用插件使用的真实公开 SDK，不伪造 CapCore、adapter 或 Plugin API。
4. `manage_extension(test_source, path)` 在独立子进程中使用当前 release 的真实 SDK 运行项目测试；它不暂存、不安装、不激活插件。
5. `manage_extension(stage_source, path)` 从源码构建 wheel，并在隔离进程中探测 manifest、权限和贡献项；暂存不会激活代码。
6. `manage_extension(install, stage_id, approved_permissions)` 只接受该 stage 返回的完整权限集合，并由宿主一次完成制品发布、selection 更新和 generation 激活。
7. 最后 `list` 确认 active；从尾部能力通知／`capability_list` 取得精确 ID，直接 `capability_load` 后携当前 `contract_ref` 经 `capability_invoke` 验证真实结果。新工具同会话立即可用，load 不扩充原生 tools；选择常驻后按既定 MemCore 压缩边界合并声明，再验证原生直接调用。模型不手工协调 publish、enable、restart 或 reconcile。

需要延迟唤醒当前角色时以 `examples/plugins/akane_timer` 为最小参考；只有还需要观察
会话活动或贡献 Skill 时才使用更综合的 `akane_gentle_checkin`。两者都保存宿主签发的
`conversation_ref` 并调用 Agent-event 端口，不保存裸收件人，也不把模型文本转交固定通知端口。

`stage_wheel` 为已有 wheel 提供同一条审计路径。`discard_stage`、`rollback` 和 `uninstall` 分别处理废弃候选、错误更新和明确卸载。它们不构成第二套安装器。

插件运行依赖由部署者显式供应：`AKANE_PLUGIN_DEPENDENCY_WHEELHOUSE` 指向受信离线
wheelhouse，或 `AKANE_PLUGIN_DEPENDENCY_INDEX_URL` 指向允许的包索引，二者只能配置一个。
staging 会先检查 wheel 的 `Requires-Dist`；宿主公开 SDK 依赖由宿主提供，第三方运行依赖
必须通过所选供应源进入插件自己的隔离 site。没有供应源时，带外部依赖的候选会返回
具体包名、版本约束和诊断编号，并清理候选，不会假装安装成功。

## 与参考项目的校准

- AstrBot 对外提供创建、安装、更新、移除和重载等完整意图；插件加载失败时展示错误并允许修复后重载。调用方不负责拼装内部插件管理状态。
- Alife 由统一 `PluginSystem` 协调市场、安装和运行环境同步；其源码也明确建议调用统一系统，而不是让上层分别操作内部组件。
- OpenCode 以项目/全局插件目录或配置中的包名表达安装意图，宿主负责发现、安装依赖和重载。

Akane 保留两步而不是一步，是因为“精确权限确认”需要一个稳定候选作为审批边界：
`stage_*` 只构建、审计和探测，`install` 则一次完成发布、selection 持久化与隔离代切换。
这比参考项目多出的复杂度只服务于权限审阅、进程隔离和 last-good；内部 catalog、
selection、generation 与 reconcile 不暴露给模型或外部开发者。

## 模型看到什么

- 稳定 Skill 目录：插件开发能力的一句描述。
- 加载 Skill 后：上述开发闭环和关键边界。
- 当前请求尾部：平台、Shell、当前 working directory。
- 工具结果：真实 stage id、贡献项、所需权限、安装/激活状态和失败原因。

插件 API、样例源码和权限解释不进入普通系统提示词。需要时由模型从 `alias:akane-sdk` 精确读取，避免把插件开发注意力成本施加给所有对话。

普通 `exec_run` 不继承 Akane 宿主私有模块。插件测试需要的真实公开契约只通过
`test_source` 注入：它从当前 release 动态取得 SDK，适用于 capability、命令、事件、后台服务等
所有插件形态，不绑定某个样例或业务。这样普通项目不会偶然依赖宿主 venv，插件测试也不会通过
`sys.modules` 假替身制造与生产不一致的成功。

## 公开子进程辅助模块

`companion_v01.plugin_subprocess.PluginProcessRunner` 是当前 release 的公开 SDK 辅助模块，
只依赖 Python 标准库。插件可用 `await runner.run(argv, capture=False, timeout=1800)`
执行自己拥有的直接子进程，返回 `(returncode, stdout_bytes)`；非零退出由业务层转为
结构化错误，超时抛出 `asyncio.TimeoutError`，取消保留 `CancelledError`。
启动中的取消也等待子进程创建完成并终止回收，`await runner.aclose()` 关闭后拒绝新启动。
Windows 子进程无控制台窗口。它不提供任务队列、权限审批、媒体处理或任意后代进程树管理；
调用程序不得自行派生无人回收的工作进程。后台任务继续使用既有 Host Job。

转换插件已直接使用此模块，分轨包的 `process` 模块仅兼容重导出，不再拥有第二套实现。
独立 ML 子进程本身不依赖此 SDK；启动它的父进程需要当前 release 提供该公开模块。

同模块的 `run_completed(async_factory)` 仅供既有同步服务/兼容入口等待异步业务完成。
没有运行中 event loop 时直接运行；已有 loop 时在一个线程内运行并同步等到结束。
它不创建后台 Job、不提前返回、不提供同步调用方取消协议；新异步插件直接 await。

## 没有输入文件的产物工作目录

`await resource_port.work_directory()` 返回当前 invocation 私有临时目录，适用于
纯文本生图、创建文档等没有现成输入副本的任务。沿用 `resource.read` 端口授权；
文件登记仍须 `artifact.write`。目录不是稳定存储，不允许输出到 prompt 或 capability
content。用它创建 `ManagedArtifactDraft(path=...)` 后保持文件到本次调用完成；
宿主在产物交接完成、失败或取消实际排空后回收整个目录。不要后台继续写入。

这沿用已有 invocation 生命周期；worker 内直接创建本地工作目录，不增加另一条
文件登记协议或 Job。可交付超过内存 draft 16 MiB 上限的合法文件，仍受输出 slot 总预算限制。

## 当前调用内组合已安装能力

新插件使用 [公开 SDK](../akane_plugin/README.md) 的 `ctx.tools.call/call_result`；
本节以下端口是旧产物协议的兼容说明，不扩展其业务限制到新接口。
需要复用另一个插件的实际产物时，声明 `capability.invoke` 并在注册阶段捕获
`registrar.get_capability_port()`，执行中 `await port.invoke(capability_id, arguments)`。
返回真实 `CapabilityResult`：成功时 `content["artifacts"]` 为有序生成文件句柄，
需要读文件时继续用 resource port。只能调用当前启用、当前渠道可见的插件产物能力，
目标须声明布尔 `send_to_user`；内部调用强制为 false，不允许嵌套渠道投递。

宿主复用目标的普通权限 admission 和 ExecutorBroker，不自动批准、不新建 Host Job。
调用方不能指定身份；会话和调用链由宿主绑定，循环会拒绝。依赖深度和次数统一使用
部署资源策略，默认 32 层、256 次，可配置或解除；通过新 SDK 的 `ctx.tools.budget()`
查询当前链的限额和剩余量。后台服务/完成后调用无隐式会话权限。缺依赖/需要确认必须真实报告。
取消会等依赖真正结束并清理；依赖已完成的独立产物可能保留，但不能在取消确认后迟到登记。
依赖返回完成未确认等真实失败时必须向上传播，不能把它转成“已停止”。

## 命名连接与既有模型设置

需要现有生图连接时，显式声明 `connection.image_generation.read`，注册时捕获
`registrar.get_connection_port()`；只在正常 capability 调用中
`await port.resolve("image_generation")`。返回 `PluginConnectionResult` 是私有配置值，
不是可以放入 capability content 的结果。密钥/URL 不进 repr，且不得写日志、prompt、
descriptor、产物、命令参数或完成事件。宿主只投影这个连接所需字段，不提供任意配置查询。

当前生图连接继续由原模型服务设置拥有，保留专用 key 优先、原聊天 key 后备语义。
每次调用取得当前 Bot 的不可变设置，更新/禁用在下一次调用生效；不创建插件专用第二份
配置，也不返回其他聊天/视觉设置。`configured` 只表示配置齐备，不表示远端已成功。

注册、健康检查、源码测试/暂存没有 invocation，因此不能读取连接。插件的安装健康
只核验本地执行依赖，必须明确区分 runtime_ready 与 provider readiness；业务调用再
验证远端，认证失败/服务不可用必须结构化返回，不在暂存阶段发起付费请求。

托管制品的实际进程权限必须与安装时批准的集合一致；漂移会拒绝候选发布。无安装
审批记录的旧 process-installed 插件不能通过该端口读取连接，须走显式安装审批。
这些是可信插件的逻辑权限和数据边界，不是恶意 Python 代码的操作系统沙箱。

## 真实闭环验收

单个组件通过不代表插件可用。回归必须覆盖全新源码项目从 stage 到 install、active 和真实 capability/command/event/background 行为。运行时未尝试某个插件时不能将其报告为 activation_failed，目标插件没有进入候选 generation 时也不能报告安装成功。
