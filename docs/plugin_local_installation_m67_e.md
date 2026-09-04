# M67-E：插件本地安装与开发迭代

> 2026-09-04：本文保留安装与 generation 热切换的实施记录；其中旧主动推理回调已被
> Agent-event 主链取代，当前事件契约以 `docs/plugin_first_class_host_pipeline_v1.md` 为准。

状态：后端制品生命周期与 M67-F 无中断代码热切换已实现；控制中心安装页面留待 M67-G。

## 1. 用户最终会感受到什么

用户可以选择一个插件 wheel，或选择一个含 `pyproject.toml` 的本地插件工程：

1. Akane 在实例自己的 staging 目录安装候选包；
2. 独立短进程使用真实 `PluginHost` 完整启动、健康查询并排空一次插件；
3. 界面展示插件 ID、版本、贡献类型和它实际声明的权限；
4. 用户原样确认这组权限后，候选版本才成为当前版本；
5. 请求插件重载后，完整候选代次就绪才原子接替旧代；
6. 启动成功后该版本成为 last-good；启动失败时指针自动回到 last-good，旧代继续服务；
7. 卸载会先排空并停止插件贡献，再撤下实例选择和托管制品，不留下可见的幽灵插件。

安装、更新和卸载失败都返回 `status/reason`，不返回 pip 输出、异常堆栈、宿主路径或制品物理位置，也不伪装成功。

## 2. 单一权威链路

```text
本地源码 ──无网络构建 wheel──┐
                            ├─ staging ─ 静态制品审计 ─ 独立进程完整激活探针
本地 wheel ─────────────────┘
                                              │
                                      精确权限确认
                                              │
                                      原子 catalog 指针
                                              │
                                      候选插件代次
                                              │
                             PluginGenerationRuntime 原子发布
```

`ManagedPluginArtifactStore` 只负责托管制品和版本指针；`PluginSelectionStore` 只负责实例启停选择；`PluginGenerationRuntime` 是工具、事件、Hook、后台服务、命令、Skill 和稳定提示贡献的唯一 Bot 运行时权威。每个 worker 内仍复用原有 `PluginHost` 执行一个插件，但它不参与 Bot 侧路由选择。源码目录永远不会加入宿主 `sys.path`，因此开发模式没有形成第二条加载链。

每个实例的托管根位于它自己的 data root 下。公开 snapshot 只包含安全元数据和 SHA-256 摘要，不包含 URL、源码路径、wheel 路径、site-packages 路径或运行目录。

## 3. 当前管理接口

这些接口继续使用现有 loopback/admin 认证：

- `POST /admin/plugins/stages`：`{"wheel_path":"..."}`；
- `POST /admin/plugins/stages/source`：`{"source_path":"..."}`；
- `POST /admin/plugins/stages/{stage_id}/install`：`{"approved_permissions":[...]}`，一次完成发布和激活；
- `DELETE /admin/plugins/stages/{stage_id}`：丢弃未发布候选；
- `POST /admin/plugins/{plugin_id}/enabled`：启用或停用；
- `POST /admin/plugins/{plugin_id}/rollback`：把当前指针切回 last-good；
- `DELETE /admin/plugins/{plugin_id}`：卸载；
- `GET /admin/plugins/status`：查看运行时和托管制品状态。

安装权限必须与探针观察到的权限集合完全一致。少确认、多确认或候选在确认前发生变化，都不会安装。

本地源码构建使用当前 Akane Python 的 `pip wheel --no-deps --no-index --no-build-isolation`。它不会联网解析依赖；缺少构建后端或运行依赖时返回结构化失败，让开发者或模型修正工程后重新 staging。构建产物随后仍经过普通 wheel 的全部检查。

本地 wheel 不设人为文件大小门槛。安装和源码构建的单次存活检查为 10 分钟，完整激活探针为 45 秒；这些是管理操作的故障边界，不影响插件日常工具调用或后台任务时长。超时会删除本次 staging，并保留当前版本和 last-good。

## 4. 更新与 last-good

catalog 的一个插件指针只记录：

- `current`：下一候选代次应加载的制品摘要；
- `last_good`：最近一次由真实插件代次成功启动的制品摘要；
- `pending_activation`：当前 active generation 尚未验证 `current`。

发布只原子切换 catalog，不改正在执行的 active generation。请求 reload 后：

- 插件为 `active` 或按实例选择为 `disabled`：确认 `current` 为 last-good；
- 插件激活失败且存在旧 last-good：指针切回旧版本，返回 `rollback_scheduled`；
- 首次安装失败、没有 last-good：保留结构化失败，不制造一个不存在的可用版本。

未被 `current/last_good` 引用的托管版本会在对账后清理。删除只作用于已经验证属于该实例、该插件的精确目录。

## 5. 为什么现在不再要求重启 Bot 进程

生产 Bot 通过独立 worker 代次运行 Python PluginHost。`PluginGenerationRuntime.restart()` 会先
构造完整候选，成功后原子切换全部消费者，再排空旧进程；worker 内的 `PluginHost` 只负责单个
隔离代次，不是第二个宿主权威。

只要 catalog 标记 `pending_activation`：

- generation reload 会直接解析该不可变制品并启动候选；
- 候选成功后清除 pending 并记录 last-good；
- 候选失败时不切换，catalog 回退到 last-good，当前旧代继续服务。

M67-F 已复用本文件的 staging、权限和 catalog 完成该切换，没有另造安装器。

M67-F 的第一步已经把原先一次性结果文件探针收敛成
`akane.plugin-generation.v1` 版本化进程协议。安装候选与未来运行代次复用同一个
`PluginGenerationProcess` 启停边界。第二步已经开放公开 Capability 描述和调用，支持并发
请求关联、健康查询、取消传播与已接收调用排空；调用和排空默认没有统一总时长上限。第三步
已经接通受管产物回交：产物 bytes 只进入代次私有 outbox，换行 JSON 协议只携带无路径的
随机 handle，父进程消费后复用既有 `ManagedArtifactSink` 和 GeneratedFile 权威；失败会结构化
返回并清理暂存，stop 也会等待父进程物化完成。既有 sink 回调超时仍是显式资源边界，不是
Capability 总超时。第四步已经接通通知反向回调：worker 继续拥有权限、宿主可用性和
idempotency ledger，父进程通过同一控制通道调用唯一真实通知端口；并发按 callback ID 关联，
取消会传播，未绑定端口或非法结果不会伪造成投递成功。已有运行事件循环会直接复用，只有同步
绑定且首次收到真实回调时才懒启动代次回调循环，stop 后不留线程；通知回调不新增统一隐式
超时。第五步复用同一反向回调通道接通现有 `PluginReasoningPort`：完整公开请求和安全结果按
callback ID 无损关联，取消与旧代排空保持闭环；父进程仅调用绑定的唯一真实推理端口。进程层
不新增 prompt、模型轮数、长度或统一超时，既有推理端口继续负责校验、幂等、超时与 MemCore
语义；未绑定时返回结构化 `not_configured`。第六步接通入站事件：ready 快照发布真实事件订阅，
父进程可直接作为事件 broker 使用；worker 继续运行现有 `PluginEventBroker`，完整返回
internal/current_turn/timeline 与普通 Agent 仲裁意图。通用结构化 payload 可逆传输，QQ 的
channelcore-onebot 消息权威通过明确类型编码保留引用、@、有序段、附件和转发，不压成摘要；
未知对象结构化失败，不静默删数据。事件调用支持并发、取消与排空，不新增次数、大小或总时长
限制，也不直接写 MemCore 或修改 prompt。插件普通 stdout/stderr 与协议通道隔离。
第七至第九步又依次接通生命周期 Hook、后台服务就绪与排空、QQ 插件指令；第十步把已经由
PluginHost 校验和稳定排序的提示块正文作为 ready 时的一次性不可变快照交给父进程。它不经
health 动态刷新，不进入 MemCore 或当轮尾部，缓存前缀只在插件代次切换时变化。第十一步又将
已验证插件 Skill 冻结到代际私有挂载树，协议只发布名称和 opaque alias；父进程通过已知私有根
二次校验并复用原 `SkillRegistry`，不暴露安装路径，也不改变渐进披露。运行切换前审计发现的
管理视图已经收敛：状态、选择、启停、重配与诊断调用统一通过 `ExtensionManagementService`
面向同一个运行时契约，管理路由不再旁路直调 `PluginHost`。完整插件集合的原子槽位现已完成：
候选不完整或冲突时不切换，新请求切到新代后旧代只排空
已有租约，停止失败也会明确报告。制品解析和事件/Hook/QQ 组合已经接通同一份代次租约，
实例插件存储也能跨代保持；Bot 最终组合根已经替换，Engine、管理、事件、Hook、QQ、稳定提示
和 Skill 通过同一个 generation facade 读取当前快照。进程实现已拆为
协议帧、父/子回调、worker 与父进程客户端四个职责文件；仍只有同一个
`akane.plugin-generation.v1` 协议和同一个公开 `PluginGenerationProcess`，不形成兼容双轨。

## 6. AI 自修改的边界

模型可以通过正常 Agent 文件与 Shell 能力创建或修改插件工程，并依据 `plugin_source_build_failed`、`plugin_probe_failed` 等反馈修复代码。模型不能因为“代码由自己写”而绕过安装审批：发布仍需要宿主或用户确认探针得出的确切权限。

当前 `manage_extension` 模型工具复用同一服务，暴露查询、源码或 wheel 暂存、精确权限安装、启停、回滚和卸载。安装由宿主一次协调制品指针、实例选择和原子 generation，不再要求模型手工编排发布与重载。插件开发说明由 `plugin-development` Skill 按需加载；普通提示词只保留一句能力路由。它仍不搜索市场或下载代码，也不允许模型绕过 `extensions` 权限与逐次审批。

## 7. 不属于本阶段的内容

- 不声称恶意 Python 插件已被安全沙箱隔离；当前仍是显式批准的可信本地代码；
- 不自动联网下载构建依赖；
- 不为一个 wheel 同时发布多个 Akane 插件，当前契约是一个制品对应一个插件 ID；
- 不在每轮 prompt 动态加入安装状态；插件贡献仍按扩展代次进入稳定目录或当轮事件；
- 不实现市场浏览、评分、更新提醒和配置 UI；这些属于 M67-G。

## 8. 验收

`tests/test_plugin_installation.py` 使用真实样例源码和真实 wheel 覆盖：

- 本地源码构建后进入同一探针；
- 候选在发布前不会被运行时发现；
- 权限未精确确认时不发布；
- 发布使用 SHA-256 制品指针且不泄露路径；
- 新 generation 成功后写入 last-good；
- 坏更新自动安排回退；
- 卸载撤下 catalog 和托管制品；
- 非 wheel 与坏 wheel 不留下 staging 残骸。

扩展管理测试另覆盖：待更新状态通过完整候选代次切换，动态插件卸载后会从持久选择和当前 generation snapshot 一同消失。
