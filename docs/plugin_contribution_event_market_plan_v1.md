# Akane 插件贡献、事件与市场执行计划 V1

> 2026-09-04：本文早期切片中复用旧主动推理端口的描述仅是实施历史，该端口现已删除。
> 需要角色回应的插件事件统一使用宿主 Agent-event 主链；当前权威语义见
> `docs/plugin_first_class_host_pipeline_v1.md`。

## 1. 文档目的

本计划在现有 `PluginHost`、CapCore、MemCore 和 channelcore-onebot 基础上补齐事件型
插件、组合贡献、真实热重载与市场分发。它不创建第二套插件宿主，也不把插件等同于
模型工具。

相关基线：

- `docs/plugin_ecosystem_v1.md`：扩展类型、生命周期和市场总原则；
- `docs/akane_capability_fabric_m66.md`：能力解析、执行与产物权威；
- `companion_v01/plugin_api.py`：当前公开插件契约；
- `companion_v01/plugin_host.py`：当前插件生命周期实现。

本计划参考 AstrBot 的消息事件、Agent/工具/发送钩子、配置与插件页面，也参考 Alife
的模块事件组合、开发模式和 AI 自修改插件体验。Akane 只吸收能降低宿主复杂度、改善
真实体验的部分，不开放任意宿主对象，不允许插件另造消息、记忆或执行权威。

## 2. 目标体验

插件是可安装的能力包，可以只贡献一种能力，也可以组合贡献：

- 模型可调用能力；
- QQ 指令；
- 消息或平台事件处理器；
- 执行链 Hook；
- 后台服务和主动事件；
- Skill 与稳定启动知识；
- 模型、语音、视觉等 Provider；
- 配置、状态页和静态资源；
- 宿主管理的产物。

一个不提供模型工具的事件插件必须能够正常工作。一个只提供工具的插件不需要注册
事件。插件没有真实贡献时不在模型提示、控制中心或市场详情中制造占位能力。

用户最终可以在同一个扩展中心中浏览、安装、启用、停用、更新和卸载 Skill、MCP 与
Plugin，但界面必须说明三者的真实生效方式：Skill 从下一模型轮可见，MCP 按需加载，
Plugin 经过宿主生命周期激活。

## 3. 不变的架构权威

| 事实或行为 | 唯一权威 | 插件获得什么 |
| --- | --- | --- |
| OneBot 有序消息链、引用、@、附件 | channelcore-onebot | 只读标准消息与材料句柄 |
| 对话、工具轨迹、压缩与召回 | MemCore | 结构化事件写入请求，不获得数据库或投影器 |
| 能力名称、Schema 与语义 | CapCore | 注册标准 Adapter/Offer |
| 调用、取消、状态和产物 | ExecutorBroker / ArtifactBroker | 标准调用与结果端口 |
| 插件发现、激活、代次和排空 | PluginHost | Registrar 与受控宿主端口 |
| 模型回合与回复决策 | Engine | 可选的正常推理请求，不获得 Engine 引用 |
| 市场目录和包下载 | Extension Catalog | 包与元数据，不参与运行 |

插件不得再实现一份消息文本解析、工具调用记录、模型历史或 QQ 发送器。需要的新能力
通过上述权威的公开契约表达。

## 4. 插件贡献快照

### 4.1 统一表示

PluginHost 在候选插件完成注册与健康检查后，根据真实注册结果生成不可变贡献快照：

```yaml
plugin_id: example.companion
generation: 7
contributes:
  capabilities: []
  commands: []
  event_handlers: []
  hooks: []
  background_services: []
  skills: []
  prompt_blocks: []
  providers: []
  ui_pages: []
```

该快照是运行事实，不要求插件作者再手写一份容易漂移的能力清单。Manifest 只声明
身份、版本、兼容范围、所需权限与包级元数据。

控制中心、管理 API、模型扩展目录和市场安装结果共用该快照。插件健康丢失后，其
动态能力 Offer 立即失效；历史工具轨迹仍由 MemCore 保留，但不会继续把离线能力
显示为当前可调用工具。

### 4.2 稳定前缀

- 常规操作说明优先放 Skill 或工具描述；
- 只有每个模型回合都必须知道的短小事实才使用稳定提示块；
- 稳定提示只在扩展代次变化时重新生成；
- 安装或热重载发生在回合中时，本回合继续使用起始快照，下一轮切换；
- 插件不得用事件 Hook 每轮改写 system prompt。

状态接口应报告每个插件的常驻提示字符数、常驻工具 Schema 数和估算 Token，方便
发现上下文膨胀，而不是等缓存命中率下降后再猜原因。

## 5. 通用事件契约

### 5.1 事件输入

QQ/OneBot 事件直接携带 channelcore-onebot 的标准只读消息链，不能退化为插件自己
拼接的纯文本。桌面、文件、计时器、游戏和外部服务使用小型结构化事件信封：

```yaml
event_id: stable-id
event_type: game.hp_changed
source: plugin.example
occurred_at: 2026-09-01T12:00:00+08:00
subject: optional-session-or-device
fields: {}
material_handles: []
```

事件信封不承担领域建模。游戏生命值、文件变化和桌面截图可以使用同一运输契约，
具体含义由插件的 Skill、工具和事件处理器解释。

### 5.2 默认行为

收到事件时默认行为是 `observe/pass`：

- 不自动唤醒模型；
- 不自动写入 MemCore；
- 不自动发送消息；
- 不阻断正常群聊链路。

只有事件处理器明确返回动作时宿主才执行。动作使用三个通用投递去向：

| 去向 | 用户体验 | 上下文行为 |
| --- | --- | --- |
| `internal` | 插件内部更新状态，无可见回复 | 不进入模型和 MemCore |
| `current_turn` | 当前回合需要模型感知 | 作为尾部临时事件，下一轮不再出现 |
| `timeline` | 值得成为共同经历或后续证据 | 通过 MemCore 公共事件接口写入，参与压缩和召回 |

同一种事件可以逐次选择不同去向。核心系统不规定“受伤必须入库”或“截图只能临时”，
避免为尚未出现的游戏形态预先写死规则。

第一版事件桥允许处理器选择投递去向，并请求一次正常 Agent 回合。请求模型不等于强制
回复；模型仍按当前输出协议决定回复或静默。消费宿主消息和直接贡献标准出站计划需要
与 M67-C 的出站 Hook、失败反馈和权限语义一起落地，第一版不提前开放半套拦截接口。

### 5.3 并发与重复

- 每个外部事件必须有稳定 `event_id` 或幂等键；
- 当前会话已有 Agent 回合时，事件进入该会话的统一事件/steer 仲裁，不新造并行回复链；
- 插件后台任务不能直接调用 Engine 或 QQ Gateway；
- 用户取消、关闭插件和代次切换必须能传递到尚未完成的插件操作；
- 重复投递返回已处理状态，不伪造第二次成功。

## 6. 第一版 Hook

第一版只开放已有真实消费场景的四个生命周期 Hook：

1. `before_tool_call`；
2. `after_tool_call`；
3. `before_outbound_plan`；
4. `after_delivery`。

入站消息不重复制造 `channel_event_received` Hook，而由 M67-B 已落地的
`conversation.direct.inbound` / `conversation.group.inbound` 事件表达。

Hook 接收不可变快照，返回类型化结果。它可以观察、追加诊断、贡献标准事件或修改
明确允许的出站装饰字段；不能取得 Engine、MemCore、具体 QQ Gateway 或任意 Prompt
列表的引用。

暂不开放通用 `on_llm_request(prompt)` 任意改写接口。需要给模型看的动态信息走
`current_turn`，稳定知识走 Skill/代次提示，工具反馈走既有工具结果链。这既保留插件
表达能力，也保护缓存前缀和 MemCore 投影一致性。

后续只有出现无法由上述契约表达的真实插件时，才增加新的 Hook 点。

## 7. 后台服务与主动唤醒

现有“每插件一个后台任务”改为按稳定 `service_id` 注册多个受监督后台服务。每个服务：

- 独立报告 starting/running/degraded/stopped/failed；
- 共享宿主关闭信号并支持幂等停止；
- 通过事件端口发出事实；
- 通过通知端口直接发送确定性通知；
- 只有需要模型判断时才使用 ReasoningPort；
- 不因为另一个同插件服务失败而伪造整体健康。

这样到点提醒、目录监听、游戏遥测和屏幕观察都能复用同一闭环，不再为每种主动能力
增加一条宿主专用定时器。

## 8. 隐形限制审计

开工前后逐项清点以下边界：

- 每插件 Adapter、Capability、QQ 指令、提示块和后台任务数量；
- 提示、事件、工具结果和主动推理的字符/字节边界；
- 健康检查、调用、停止、排空和管理操作超时；
- 单组件错误对其他组件和整个 PluginHost 的影响；
- 事件队列积压、重复与取消策略。

处理标准：

- 启动探测和损坏防护可以有界；
- 正常有效任务默认不设置隐藏的次数或总时长上限；
- 必需的资源边界进入配置和状态接口，返回结构化原因；
- 不用截断、静默丢弃或假成功代替反馈；
- 旧限制没有现实依据时删除，而不是换一个更大的魔法数字。

## 9. 热重载与 last-good

当前进程内 `restart()` 继续诚实称为宿主重启，不宣称代码热更新。真正无感热重载使用
独立运行代次：

```text
package -> staging -> validate -> start candidate -> health check
        -> publish immutable snapshot -> new turns use new generation
        -> drain old generation -> stop old process
```

- 候选失败时旧代继续运行；
- 权限增加在切换前请求主人确认；
- 当前回合固定旧快照，不在半轮中混用两个 Schema；
- 旧代排空不以固定工具轮数打断正常调用；
- 进程间仅传输版本化 JSON 消息、调用、结果、事件和状态；
- 不使用 `importlib.reload` 伪装可靠热重载。

## 10. AI 自开发闭环

Akane 可以在开发模式下：

1. 创建或修改插件源码；
2. 构建本地包；
3. 安装到 staging；
4. 查看贡献快照、权限差异和健康结果；
5. 运行插件自测与宿主黑盒验收；
6. 验证通过后切换代次；
7. 失败时读取结构化反馈继续修改，当前 last-good 不受影响。

模型不直接覆盖正在运行的插件目录。新增敏感权限需要主人确认；无新增权限的可信本地
开发包是否自动切换由实例策略配置。安装、启用、更新和卸载统一走 ExtensionManagement，
不另设只供模型使用的后门。

## 11. 市场与控制中心

市场是索引和分发层，不参与插件运行。首版包含：

- 一个或多个静态市场源；
- 插件 ID、作者、版本、源码、兼容范围和包哈希；
- 权限、贡献类型、支持平台和上下文成本预览；
- staging 安装、更新、卸载；
- 更新失败保留 last-good；
- 本地包与市场包共用同一安装入口。

首版不做账号、评分、自动推荐、复杂依赖求解、陌生源码在线编译和无人确认的自动更新。

控制中心只显示真实状态：没有配置 Schema 不显示空配置页，没有 UI Page 不显示占位页，
插件离线时不把历史能力标成当前可用。

## 12. 分阶段执行与验收

### M67-A：贡献快照

状态：已完成第一版。

工作：统一现有 Capability、QQ 指令、后台服务和提示块的运行清单；只读 API 与控制中心
共用快照。

当前实现由 `PluginHost` 在一代插件完成激活时一次生成不可变
`PluginContributionSnapshot`，只列出现阶段真实可执行的 Capability、QQ 指令、事件
处理器、后台服务和提示块。状态 API 返回每个活动插件的
`contribution_snapshot`；停机后快照随能力一同清空。Hook 和 Skill 已在后续切片获得真实运行
契约并进入快照；Provider 和 UI Page 仍没有空占位字段。该变化不修改模型
工具投影或稳定提示内容。

验收：现有测试全通过；未注册的组件不出现；模型工具集合与改动前一致；稳定前缀不
发生无关变化。

### M67-B：事件桥

状态：已完成第一版。

工作：增加通用事件信封、channelcore 消息事件适配、事件处理结果和 internal/
current_turn/timeline 三种投递。

当前实现按会话语义暴露 `conversation.direct.inbound` 和
`conversation.group.inbound`，不按平台复制插件协议。QQ 私聊与桌宠 `/think` 走 direct，
QQ群走 group；后续微信私聊等一对一入口可复用 direct。通用事件信封的 `payload` 保留来源
适配器拥有的不可变事件对象，不依赖具体平台类型；QQ 适配器在其中直接携带
channelcore-onebot `InboundMessage`，引用、@、有序段、附件和转发引用不再被插件层重复
解析。桌宠的普通文本事实放在信封字段中，不改变原请求、会话 ID 或 MemCore 消息投影。
`/pet/turn` 仍是 M32 过渡桥，本阶段不在新旧桌宠入口各维护一份事件实现。PluginHost 并发隔离
事件观察器并发布真实 `event_handlers` 贡献快照：默认
`internal` 不改变主链；`current_turn` 只追加当轮尾部结构化事件并进入既有 Agent 仲裁；
`timeline` 通过 MemCore `append_standalone_event` 写入原生事件记录。事件处理器异常、超时
或时间线写入失败只产生结构化降级日志，不吞 QQ 消息，不伪造回复。

事件处理器没有数量魔法上限；时延敏感的单处理器探测边界和事件字段资源边界公开在
PluginHost 状态契约中。事件不进入稳定 system prompt，因此未注册事件插件时模型前缀、
工具 Schema 和缓存命中路径均不变化。

验收：事件插件无需 Capability 即可激活；默认观察不抢答；临时事件不入库；timeline
事件能被压缩和召回；引用、@、附件不降级。

### M67-C：Hook 与多后台服务

状态：多后台服务、工具调用前后 Hook、出站计划与交付 Hook 已完成第一版。

工作：落地四个生命周期 Hook 和通用入站事件；后台任务改为按 ID 的服务集合；补齐取消、
幂等、状态和失败隔离。

多后台服务现由 `PluginRegistrar.add_background_service(service_id, service)` 注册。每个
`(plugin_id, service_id)` 拥有独立控制器、任务和状态；同插件一个服务异常退出只将该服务
标记为 `failed`，兄弟服务继续运行。宿主状态会诚实降级并指出具体服务，但不会停止或伪造
其他服务的状态。关闭时先同时广播关闭信号，再逐个调用幂等 `stop()`，超时或异常按服务
记录结构化原因。

所有后台服务都通过同一个 `add_background_service(service_id, service)` 入口注册并进入同一
服务表，不存在平行监督循环。服务数量不设魔法上限；稳定 ID 只接受公开的 64 字符
小写标识格式，状态接口同时公开该边界和停止探测时限。具名服务不进入模型提示、工具
Schema 或 MemCore 投影，因此未安装相关插件时缓存前缀不变。

工具 Hook 现由 `PluginRegistrar.add_hook_handler(hook_type, handler)` 注册，首个真实执行切片
只发布 `before_tool_call` 与 `after_tool_call`。`channel_event_received` 已由 M67-B 的
`conversation.direct.inbound` / `conversation.group.inbound` 事件桥权威表达，不再并行派发一份
同义 Hook。调用前快照保存 provider-neutral 工具名、调用 ID、来源、会话归属和完整公开参数；
参数使用 canonical JSON 固化，凭据键只标记为已配置，`_tool_*` provider sidecar 不暴露。
执行器仍接收原始参数，观察快照不会改变任务语义。调用后快照保存真实状态、
结构化原因、耗时、模型可见工具反馈和事件类型。快照不会进入 prompt 或 MemCore，也不能由
插件改写；没有 Hook 观察器时宿主跳过参数序列化与快照构造，普通工具调用路径没有新增等待。

Hook 处理器在 PluginHost 生命周期事件循环并发执行，Engine 与 QQ 发送工作线程通过明确桥接等待这次
观察完成。单处理器时限公开在 `contract.timeouts.hook_handler_seconds`；超时、异常和非法结果
只进入 `hook_runtime` 诊断计数，不改变工具成功/失败、不吞模型原有反馈，也不将插件异常文字
发给用户。QQ 文字、图片、语音、文件、转发及模型直接发起的用户可见 OneBot 动作统一经过
同一个出站边界；旧图片/语音运输回退不再绕过 Hook。`before_outbound_plan` 快照只暴露目标、
动作、有序段类型、可见文字和引用 ID，不暴露媒体 URL、本地路径或上传句柄。第一版唯一可变
字段是已有文字的前后装饰；它不能替换正文、目标、引用、媒体或动作，媒体消息也不会被装饰
强行变成图文消息。`after_delivery` 发布真实成功/失败、耗时和安全回执 message ID。插件异常、
超时或非法装饰时原始发送计划照常执行。

没有出站 Hook 观察器时不构造快照、不生成 delivery ID，也不等待 PluginHost；这些 Hook 不进入
prompt、工具 Schema 或 MemCore，因此不改变模型缓存前缀。Hook 处理器数量没有另设魔法上限，
注册类型只接受宿主当前真实可调用的 Hook。

验收：同插件两个服务可独立运行和停止；长任务中的新事件不产生重复 Agent 回合；Hook
错误得到结构化反馈且不吞主流程。

### M67-D：三个真实样例

1. 事件型：QQ 戳一戳/消息事件插件，不注册工具；已完成首个可安装样例
   `examples/plugins/akane_poke_streak`。同一发送者在同一会话 90 秒内连续戳一戳时，
   从第二次起仅向当轮追加结构化连续次数事实；普通消息、首个戳、非 QQ 来源与重复
   `event_id` 均静默放行。样例没有 Capability、稳定提示块或 MemCore 写入，wheel 与
   正式插件使用同一 entry point 和审计链；
2. 工具型：一个真实只读能力，经过 CapCore、Broker、MemCore 和产物链；已完成首个
   可安装样例 `examples/plugins/akane_hacker_news_report`。它只读取 Hacker News 官方
   固定公共 API，模型输入不含任意 URL、凭据或本地路径；结构化结果进入普通工具反馈，
   完整调用与结果由 MemCore 保存并可召回，Markdown 字节经宿主托管产物链登记和投递。
   单响应、请求时限、条目数和报告大小边界在样例常量、工具 Schema 与 README 中一致公开；
   部分条目失败时保留已取得结果并明确遗漏，全部失败时不生成假产物；
3. 混合型：已完成可安装样例 `examples/plugins/akane_gentle_checkin`。它观察 direct/group
   入站事件但不另开 Agent 回合，把配置和最近活动写入插件隔离目录；私聊可由模型通过一个
   `plugin_state` CapCore 工具配置，群聊由群主/管理员使用 `/checkin` 配置。随 wheel 发布的
   `gentle-checkin` Skill 只在工具真实可用时出现在按需目录，正文不常驻。后台服务在会话安静
   到期后向宿主提交原会话事件，由宿主复用普通 Agent 回合、角色/MemCore 上下文和统一表现层
   生成角色化问候；固定通知端口只用于已经确定文本的非模型通知。每段静默最多一次，新消息会
   重新计时，推理期间到达的新消息会使旧问候作废。插件停用
   同时撤下工具、事件、服务、命令与 Skill；配置损坏返回结构化错误，不伪装成空配置。

混合样例还验证了本地能力不必虚构网络访问：`trusted.stateful-plugin.v1` 由 CapCore 的
`risk/confirm/effects` 表达审批语义，宿主只交叉核对已知效果与权限；未来领域效果不会因为
不在今天的固定白名单里而被拒绝。插件 Skill 复用单一 `SkillRegistry` 的目录投影、按需读取、
last-good 和执行挂载，不复制到 managed 目录，也不增加第二套 Skill 生命周期。

验收覆盖群聊、私聊、切会话、慢请求、重复事件、取消、插件崩溃、禁用、卸载和重启。
记录缓存命中、首响应时间、常驻 Token 与事件尾部大小。

### M67-E：本地安装和开发模式

状态：后端制品生命周期已完成，详细契约见
`docs/plugin_local_installation_m67_e.md`。

已完成：本地目录/wheel 统一进入不可变 wheel staging；独立短进程通过真实 PluginHost
做完整激活探针；精确权限确认后原子发布；实例级启停、更新、卸载、last-good 对账和失败回退；
模型可以修改源码并依据结构化构建/探针错误继续修复。源码目录不成为第二套加载权威，路径和
子进程输出不进入公开状态。

当前边界：Python 插件已由独立 generation worker 承载，更新不再要求重启 Bot 进程；完整候选
通过后才切换，坏候选保留旧代。控制中心安装体验属于 M67-G。

验收：坏包不影响 last-good；无假热重载；模型能够依据结构化错误自行修复插件。

### M67-F：独立 PluginHost 代次

工作：冻结进程间协议，候选进程健康检查，原子发布快照，旧代排空。

状态：十五个后端切片已完成，生产 Bot 已使用独立代次。现有安装探针已经改为可存活的单候选 PluginHost 进程，使用
`akane.plugin-generation.v1` 换行 JSON 控制协议完成 ready、health 和 stop；坏候选不会留下
进程，正常候选会经过真实 `PluginHost.stop()` 排空。第二切片冻结了 Capability 描述、
`InvocationContext`、参数和 `CapabilityResult` 的 JSON 投影，并让同一代次支持按
`request_id` 关联的并发调用、并发健康查询和取消传播。Capability 调用与旧代排空默认不设
统一总时长上限；部署方需要资源保险丝时可以显式配置排空上限。第三切片接通了受管产物
回交：worker 只在代次私有 outbox 暂存经过同一契约校验的 bytes，控制协议只返回随机 opaque
handle，不传路径、不做 Base64；父进程消费后仍通过唯一的 `ManagedArtifactSink` 登记为
GeneratedFile。无宿主 sink、损坏交接、超限或写入失败均返回结构化失败并清理暂存文件，stop
会等待父进程物化完成后再回收代次。既有受管产物回调超时仍是显式宿主资源边界，不成为
Capability 的统一总时长上限。第四切片接通通知反向回调：worker 保留插件级权限、宿主状态
与 idempotency ledger，父进程只接收经过 JSON 投影的 `NotificationIntent` 并调用唯一的真实
通知端口，再将 `NotificationResult` 回传。同一控制通道支持并发关联和取消传播，不新增网络
监听；绑定时有运行事件循环就直接复用，否则仅在首次真实回调时懒启动一个代次回调循环，
stop 会取消并回收它。通知回调不新增统一隐式超时；未绑定端口和非法结果均返回结构化结果，
不伪造已投递。第五切片在同一反向回调通道接通现有 `PluginReasoningPort`：请求中的身份、
会话、角色包、稳定上下文、幂等键与结构化外部事件逐字段投影，结果保留文字、状态和安全证据
事件；父进程只调用已绑定的唯一真实推理端口。并发推理按 callback ID 关联，Capability 取消会
传到宿主推理协程，stop 会等待已接收调用完成。进程层不修改 system prompt，不新增模型轮数、
文本长度或统一超时；既有推理端口的校验、幂等复用、超时和 MemCore 语义保持权威。未绑定端口
返回 `not_configured`，不会假装完成推理。

第六切片接通宿主到插件代次的事件分发。代次 ready 快照公开真实订阅事件类型，父进程提供与
现有 broker 一致的 `registered_event_types`、`observes()` 和异步 `dispatch()`；worker 仍调用
唯一的 `PluginEventBroker`，所以权限、处理器探测超时、失败隔离以及 `internal / current_turn /
timeline` 语义没有第二份实现。通用 JSON、list 和 tuple payload 可逆传输；QQ 的
channelcore-onebot `InboundMessage` 使用明确允许的不可变类型编码，引用、@、有序段、附件、
转发引用及可信宿主定位信息不会退化为摘要或纯文本。定位信息只存在于父子进程事件调用，不
进入 ready/status、日志、模型提示或事件结果。未知对象返回 `event_payload_unsupported`，不会
静默丢掉 payload 后伪装成已观察。聚合结果仍只表达临时事件、时间线事件和是否请求普通 Agent
仲裁，写 MemCore、追加当轮尾部和发送回复继续由频道宿主决定。并发事件按 request ID 关联，
取消会传到处理器，stop 会排空已接收事件；进程层不新增事件次数、payload 大小或统一超时。

第七切片接通四个生命周期 Hook。代次 ready 快照公开真实注册的 Hook 类型；父进程同时实现
现有 Hook 消费者需要的 `registered_hook_types`、`observes()`、异步 `dispatch()` 和同步
`dispatch_from_consumer()` 边界。worker 只把不可变 Hook 快照交给唯一的 `PluginHookBroker`，
工具调用前后、出站计划与真实交付的字段语义、权限、单处理器探测时限和失败隔离没有第二份
实现。诊断、失败和文字前后装饰均可逆回传；装饰仍不能改写正文、目标、引用、媒体或动作。
并发 Hook 按 request ID 关联，取消会传到插件处理器，stop 会排空已接收 Hook。没有观察器时
现有消费端仍可快速跳过快照构造；Hook 不进入 prompt、工具 Schema 或 MemCore，也没有新增
总时长、处理器数量或装饰长度限制。

第八切片冻结后台服务的跨代次生命周期，而没有复制监督器。候选 worker 启动的仍是完整
`PluginHost`，因此每个具名服务继续使用原控制器、独立任务、失败隔离、通知/推理端口和停止
排空语义。父进程从 ready 贡献快照公开不可变 `registered_background_service_ids`；候选就绪
现在同时要求插件激活和整个宿主运行状态为 `active`，启动即崩或提前退出的服务会以
`plugin_runtime_failed` 拒绝候选，不能伪装成可切换。就绪后单服务失败仍通过现有 health
快照显示为 `degraded`，兄弟服务继续运行；stop 返回服务真实终态。该切片没有新增轮询、第二
套服务表、服务数量上限或运行总时长，既有显式停止探测时限继续是唯一资源保险丝。

第九切片接通 QQ 插件指令。ready 贡献快照中的真实命令 token 成为父进程不可变清单；父进程
组合 Broker 只负责维持现有“宿主内置指令优先”规则，插件指令则跨代次交给 worker 内唯一的
`PluginQQCommandBroker`。因此精确匹配、参数与身份归一化、群成员角色、幂等键、处理器时限、
异常隔离、回复截断和静默回复仍只有一份实现。指令参数和 `PluginQQCommandResult` 逐字段 JSON
投影，不把原始事件、插件对象或处理器带出进程；并发调用按 request ID 关联，取消传到处理器。
stop 现在先排空所有已接收的 Capability、事件、Hook 和指令请求，再停止 PluginHost，避免插件
端口或后台服务先关闭而命令仍在执行。该边界不进入 prompt、工具 Schema 或 MemCore，也没有
新增命令轮数、回复长度或统一总时长限制。

第十切片接通稳定提示贡献。worker 继续由唯一的 `PluginHost` 完成权限校验、文本规范化和
`plugin_id / block_id` 稳定排序，父进程只在 ready 时接收一次不可变正文快照，并提供与现有
宿主相同的 `stable_system_prompt_blocks()` 读取接口。提示正文不会通过 health 轮询动态变化，
也不进入事件尾部或 MemCore；因此缓存前缀只会在整个插件代次原子切换时变化。协议不重新解释
提示内容，也没有新增字符门槛或第二份排序规则。

第十一切片接通插件 Skill 挂载。worker 不把安装目录或宿主路径写入协议，而是把已经通过现有
Skill 包校验的内容冻结复制到该 generation 的私有挂载树；ready 只发布 Skill 名与代际唯一的
opaque alias。父进程从自己已知的私有根恢复路径并再次使用同一个 Skill 校验器核验名称、入口
和资源，再通过现有 `ContributedSkillRoot` / `SkillRegistry` 目录投影按需披露。提示目录仍只含
名称和描述，完整说明与资源仍由 `load_skill` 打开；Shell 继续使用返回的 `execution_cwd`，看不
到物理路径。源安装目录后续变化不会改写当前代快照，stop 后挂载随代际回收。没有新增 Skill
数量、包大小、正文长度或工具权限规则；复制继续服从既有包校验与 generation 启动故障边界。

第十二切片收敛插件管理门面。`ExtensionManagementService` 现在面向一个最小的
`PluginManagementRuntime` 契约持有选择、状态、重启、重配、调用和所属事件循环；管理 HTTP
路由不再额外持有或直调 `PluginHost`，诊断 Capability 也通过同一服务进入同一个运行时。因此
后续代次切换只需要替换这一个权威对象，不会出现启停落在新代、调用却仍落在旧宿主的双轨。
当前 `PluginHost` 原样满足该契约，端点、权限、状态码、调用参数和结果投影均不改变；这一步
没有增加轮询、后台任务、缓存前缀或运行限制。

第十三切片建立完整插件集合的原子 active-generation 槽位。一个候选快照必须覆盖选择中全部
启用插件，并在发布前统一拒绝跨插件 Capability、QQ 指令和 Skill 名冲突；事件与 Hook 可以由
多个插件共同观察，不被误当成冲突。发布只切换一个内存指针，新调用立即租用新快照；已经
租用旧快照的调用继续完成，租约归零后才并行停止旧进程。候选未 ready、启动后提前退出或
集合不完整时，当前快照完全不动。旧代停止失败会明确出现在管理状态中，但不会把已经工作的
新代倒退回旧代码。管理切换本身串行化，调用和排空仍没有统一总时长或轮数上限，也没有健康
轮询；即使切换请求被取消，已经退役的旧代也会完成排空和回收。代次客户端同时冻结一份
path-free ready 状态，管理读取无需重新请求子进程。

第十四切片接通完整候选的制品解析与多插件消费者组合。安装目录中已发布的 `current` 指针和
当前 Python 环境里已安装的插件现在由同一个内部解析器映射为精确 generation source；一旦目录
已有受管指针，该指针就是权威，损坏或缺失时不会偷偷退回同名旧发行版。候选构造先解析全部
启用选择，再并发启动每插件一个进程；任一来源缺失、启动失败或跨插件 Capability、QQ 指令、
Skill 冲突都会回收整批候选，不发布半代。物理 site、工作目录和实例存储根只交给进程启动器，
不进入管理状态、协议快照或模型提示。正式候选可绑定现有产物、通知、推理端口，并把插件存储
继续指向原实例命名空间，更新代次不会把插件配置误变成一次性数据。

事件、Hook 和 QQ 指令同时获得 active-generation 的稳定组合门面。一次分发只租用一个完整
快照：同一事件的多个观察者并发执行并按稳定插件顺序聚合；Hook 诊断和文字装饰保持原契约；
QQ 仍由宿主内置指令优先，再精确路由到唯一插件。发布新代后新请求立即进入新快照，已接收的
事件、Hook、指令和 Capability 都计入同一排空租约，因此不会出现工具已进新代而群聊事件仍在
旧代的双轨。单插件 worker 内已有的校验、处理器时限和失败隔离仍是唯一规则，组合层不再添加
次数、文本长度或总时长限制。

为避免后续端口继续堆进同一个实现文件，现有行为已按职责拆为稳定协议帧、父/子反向回调、
worker 和父进程客户端；公开入口、协议版本、安装探针和运行语义不变。这只是实现边界整理，
没有增加第二套宿主、额外服务、提示词内容或运行时分支。

第十五切片完成生产组合根替换。每个 Bot 只持有一个 `PluginGenerationRuntime`；它组合既有候选
构造器与唯一 active-generation 槽位，并同时向 Engine、管理服务、事件、Hook、QQ 指令、稳定
提示和 Skill 提供动态门面。启动、启停、同版本重载和新制品加载都先构造完整候选，成功才原子
发布；失败结果带明确插件与原因，旧代继续服务，不再为代码更新要求重启整个 Bot。Bot 进程
启动时若待发布版本失败，会把 catalog 回到 last-good 并只再尝试一次真实候选，不循环重试。
管理页现在公开 `atomic_generation_switch`，发布制品只标记需要 generation reload，不再误报进程
重启。Engine 的插件工具映射原本就按工具回合动态读取描述快照，因而发布后下一轮自然使用新
schema；稳定提示只在代次切换时变化，正常请求没有额外轮询或缓存前缀抖动。宿主进程内的
`PluginHost` 加载路径已经删除；它只作为每个隔离 worker 内部的单插件运行实现，因此
没有两条生产权威。

验收：升级过程中当前 Agent 回合不中断；下一轮切换新能力；升级失败继续使用旧版本；
无线程、端口、子进程和能力残留。

### M67-G：市场与插件页面

工作：静态市场源、安装更新 UI、贡献/权限/Token 预览、可选插件配置页和状态页。

验收：从浏览到安装再到模型使用形成真实闭环；卸载后 UI、工具、事件处理器、后台服务
和稳定提示全部消失；没有 future-only 占位。

## 13. 开工顺序与停止条件

实施顺序为 M67-A -> M67-B -> M67-C -> M67-D。前三个样例和真实缓存/体验验收通过后，
再进入 M67-E/F/G。

如果任一阶段出现以下情况，先停止扩展并进入 repair pass：

- 新旧两套消息、事件、工具或安装权威长期并存；
- 插件贡献导致每轮 system prompt 动态变化；
- 事件型插件必须虚构模型工具才能工作；
- 插件失败让 Agent 主流程静默或未交付；
- 控制中心显示无法真实调用的能力；
- 缓存命中、首响应时间或 MemCore 投影出现明显回归。

## 14. 当前决策与尚不提前决定的事项

已确定：

- 插件不是工具的同义词；
- 事件默认观察并放行；
- 插件逐事件决定临时或持久投递；
- 稳定前缀只随扩展代次变化；
- 市场不参与运行；
- 热重载以独立代次和 last-good 为目标。

暂不提前决定：

- 某个具体游戏事件是否必须入库；
- 游戏 AI 是否与聊天 Agent 分离；
- UI Page 的前端框架与隔离技术；
- 第三方插件是否默认运行在容器或系统沙箱；
- 何时将某个 Python 模块替换为其他语言。

这些问题应由真实插件和性能数据推动，不能为了未来可能出现的需求预先增加运行时分支。
