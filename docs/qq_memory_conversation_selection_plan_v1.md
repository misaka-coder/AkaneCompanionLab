# QQ 记忆按指定会话读取：执行单 V1

核查日期：2026-09-27。Akane 核查基线：`1d99a372`。

状态：**待实施，仅计划**。本轮只阅读代码与 MemCore 接入文档；没有修改运行代码、配置、记忆数据或依赖。本执行单供另一个模型实施，与群聊指令必须 @ 目标 Bot 的执行单分别处理。

## 1. 用户已经确定的产品规则

1. 主人 QQ 私聊与其他群聊的记忆允许双向共享，群与群之间也允许共享。
2. 非主人 QQ 私聊只能读取自己的当前会话，不能跨会话；也不作为上述共享池的读取目标。
3. 所有记忆工具默认仅读取当前会话。模型需要跨会话时，必须明确指定一个目标会话。
4. 不提供全库、全部群、所有会话聚合检索；不要在当前会话查空后自动扩大范围。
5. 语义检索、时间线、摘要目录、原文/来源读取遵循同一规则。
6. 提供跨会话能力开关。允许跨会话和默认搜索范围是两个概念：开关打开后，默认仍然只查当前会话。
7. MemCore 保持领域无关；QQ 主人、群号、Bot 实例、身份认证属于 Akane。

本单的实施边界：同一个 Bot 实例、当前角色记忆域内共享。不会因此互通两个 Bot 的数据库或跨角色记忆。不重写既有会话 ID，不合并当前历史，不改变队列、回复收件人、主动搭话或压缩调度。

群内任意发言人触发的模型回合都按“群会话”授权，可以读取主人私聊；不能再次加上“只有主人在群内提问才允许”的限制，这与用户明确要求的自由双向共享不一致。

## 2. 代码核查结果

| 位置 | 当前行为 | 实施含义 |
|---|---|---|
| `companion_v01/qq_gateway.py::resolve_identity` | 群的 session/profile 均为 `qq_group_shared_<群号>`；主人私聊均为 `master`；其他私聊 session 为 `qq_pri_<QQ号>`、profile 为 `qq_<QQ号>` | 群与私聊不只是 conversation 不同，还是 MemCore 的不同硬命名空间 |
| 同文件 `resolve_actor_profile_user_id` | 群发言人可映射到与私聊一致的个人身份 | 可保留人物归因，但 actor 不能替代记忆授权 |
| `companion_v01/memcore_integration/manager.py::_get_system` | 使用 profile/session/character 构建 `Namespace(user_id, conversation_id, domain_id)`，多个系统共用当前 engine 的 store/index/runtime | 宿主可授权后选择目标系统，不必迁移原始数据；不得跨 engine 取库 |
| 同文件 `_retrieve_memory` | 当前明确传 `cross_conversation=True` 给 `retrieve_for_turn_structured` | 必须改成明确单会话读取；不能只修改工具描述 |
| `companion_v01/memcore_integration/timeline.py` 的 `read/browse_memory/open_memory` | 有多处自动补 `cross_conversation=True` | 三条读取链全部收紧；不能只修语义检索 |
| `companion_v01/capability_registry.py::_memcore_tool_contract` | 从包生成原生记忆工具 schema，再映射 Akane 工具名称 | 在宿主投影中增加会话参数，去掉产品不允许的宽范围参数；其余参数继续由包维护 |
| `companion_v01/tool_handlers/memory.py` | retrieve 的 normalize 手工保留字段；其他三个工具转交参数字典 | 新字段必须贯穿 normalize、校验、执行，不能生成了 schema 却被 normalize 丢弃 |
| `companion_v01/retrieval_engine.py::execute_retrieve_memory_tool` | 当前消息和排除源来自发起回合，然后调用 manager | 查询目标不能覆盖发起回合身份、当前 stimulus、工具结果入库位置 |
| `companion_v01/tool_handlers/catalog.py` / `engine.py` | handler 从 engine 注入服务；`ToolExecutionContext` 持有当前身份 | 统一注入宿主读取策略，避免四个工具各写一套权限正则 |
| `companion_v01/bot_runtime.py` | 每个实例建立自己的 engine 与 QQ gateway，后者接收实例部署配置 | 使用实例绑定的主人与策略，不能读一个进程级主人号码替代多 Bot 配置 |
| `config.py`、`.env.example` | 现有 `MEMCORE_VISIBLE_SCOPE=user` 控制默认可见长期记忆，不是跨 QQ 会话授权开关 | 新开关必须独立；不能用此字段代替访问控制 |

当前虚拟环境实际导入的是 `.venv/Lib/site-packages/memcore`，不是直接导入相邻的 MemCore 源码仓库。若确实需要改包，必须完成源码、构建、宿主依赖接入与版本验证，不能只改旁边仓库就宣称本项目已生效；禁止直接修补 site-packages。

已核查包的公开能力：

- `MemorySystem.retrieve_for_turn_structured(..., cross_conversation=False)`：当前会话的 turn-aware 检索。
- `MemorySystem.retrieve_structured(..., cross_conversation=False, exclude_source_ids=...)`：显式指定系统的结构化检索，可由宿主负责实际可见内容排除。
- `browse_memory/open_memory/read_timeline`：均支持单会话读取和原有结构化分页。
- 当前包的 native retrieve dispatcher 内部仍固定 `cross_conversation=True`；不能把宿主检索改成直接调用它后，以为默认范围已经收紧。
- 原生分页游标会恢复自己的选择器，包括原来的跨会话范围；仅首次调用传 `False` 不足以构成完整的宿主权限边界。

## 3. 模型接口：只增加一个简单目标参数

四个现有工具统一增加可选字符串 `conversation`：

| 参数 | 意义 |
|---|---|
| 省略或 `"current"` | 发起调用的当前会话 |
| `"master"` | 当前 Bot 的主人 QQ 会话 |
| `"group:123456"` | 当前 Bot 的指定 QQ 群会话 |

不要求模型传内部 profile、namespace、tenant、character 或 Bot ID；不提供 `all`、`shared`、`*`、目标数组或通配符。空字符串、错误类型、无效群号等显式错误不能静默改成当前会话。

调用示例：

```text
retrieve_memory(query="周末的安排")
retrieve_memory(query="周末的安排", conversation="group:123456")
read_memory_timeline(date_from="2026-09-26", conversation="master")
browse_memory(keywords=["周末"], conversation="group:123456")
open_memory(memory_id="<上次返回的 ID>", view="content", conversation="group:123456")
```

查询目标替换本次读取范围，不是“当前会话再加上目标会话”。模型需要两个目标时分别调用；每次都明确、有限，不做后台扇出。

### 目标发现

- [ ] 增加轻量只读工具 `list_memory_conversations`，用于模型不知道目标群号时发现可读会话；它只列目录，不检索消息、生成摘要或请求 LLM。
- [ ] 目录仅返回已授权目标的 `conversation`、可用的显示名称和当前会话标记；支持有限页与续页，不把完整会话目录塞进每轮 prompt。
- [ ] 关闭开关或处于非主人私聊时，仅返回当前会话，不能泄露其他目标的存在和名称。
- [ ] 使用当前 Bot 的已知会话/历史登记信息。`store/sessions.py::list_sessions` 当前按 profile 列举，不能假定已经存在覆盖全部 QQ 群的完整目录 API。实施时先核对被动消息入库是否登记会话、重启后是否可恢复，再补最小的宿主目录读取能力。
- [ ] 目录必须兼容既有数据，不只列本次进程已打开的 `_systems`；若源不完整，如实标记，不把“没列出”解释为“没有历史”。不读取 MemCore 私有表来拼装第二套记忆系统。
- [ ] 目标群必须由当前实例的可信会话登记解析；不执行模型传入的内部存储键。已知且获准但无记录的目标可返回 empty，未登记目标返回明确不可用，不自动网络扫描所有群。

目录工具是发现手段，不是授权凭据。即使模型手里有合法格式的群号，执行每次读取时仍需重新授权。

## 4. 权限与配置

建议新增宿主开关 `MEMORY_CROSS_CONVERSATION_ENABLED`，按现有配置链加载并支持 Bot 实例有效配置；兼容默认值为 `false`。本需求部署使用时显式开启。不要在此次计划阶段修改真实 `.env`。

| 发起位置 | 目标当前会话 | 目标主人私聊 | 目标其他群 | 目标非主人私聊 |
|---|---|---|---|---|
| 开关关闭，任意来源 | 允许 | 仅与当前相同才允许 | 仅与当前相同才允许 | 仅与当前相同才允许 |
| 开关开启，主人 QQ 私聊 | 允许 | 允许 | 允许 | 拒绝 |
| 开关开启，QQ群 | 允许 | 允许 | 允许 | 拒绝 |
| 开关开启，非主人 QQ 私聊 | 允许 | 拒绝 | 拒绝 | 仅当前私聊允许 |
| 桌面/Web/无可信 QQ 上下文 | 允许 | 本期不新增跨渠道授权 | 本期不新增跨渠道授权 | 拒绝 |

“仅当前”不是“同一个 profile 下全部会话”。未知来源、缺失 QQ 绑定、当前身份与渠道不一致时，不扩大授权。

- [ ] 在 Akane 建立单一的 `MemoryReadTarget`/策略服务：输入可信调用上下文和简单选择器，输出目标 session/profile 与公开来源描述，或结构化拒绝。
- [ ] 主人身份来源于当前实例的可信 QQ 配置和会话入口；不得从消息正文、群昵称、工具参数或模型生成的 `request_context` 字段认定主人。
- [ ] 保持当前 Bot 和当前角色域；不允许工具参数改变 engine、数据库、角色或 namespace。
- [ ] 策略在读取前执行，也覆盖继续分页、open 原文、within_memory_id、anchor、插件/子任务经既有能力入口调用的路径。目录过滤不能代替执行授权。
- [ ] 明确返回 `forbidden / cross_conversation_disabled / invalid_conversation / conversation_unavailable` 等状态；错误不假装成“没有找到记忆”。复用现有错误表示，不另造不兼容的结果协议。
- [ ] 旧 `cross_conversation=true` 参数不能绕过策略。产品 schema 移除它，执行端对过期/手写调用明确拒绝或要求改用 `conversation`；不要静默扩大或默默忽略。

需特别核查：现有 `master/master` 是否也被桌面/Web复用。若已经共用同一物理会话，`master` 会包含该会话实际存储的全部历史，不能仅凭工具描述声称只包含 QQ。报告真实边界；如需拆分渠道须另做明确迁移，不借本次功能悄悄改变写入身份。

## 5. 执行切片

### A. 单会话默认与共享策略

- [ ] 先补权限矩阵测试，再接入统一策略服务、配置加载和目标目录。
- [ ] 将 manager 语义检索固定的跨会话读取收紧；移除 timeline adapter 的自动扩展逻辑。
- [ ] 默认 prompt 可见记忆与工具搜索范围分开处理：本期不能因为开启共享，就自动注入其他群/私聊的近期历史或摘要。
- [ ] 评估 `MEMCORE_VISIBLE_SCOPE=user` 是否让非主人私聊看到同 profile 的其他 session 摘要；若有，QQ 严格隔离路径必须选择 conversation 可见范围，同时保留其他宿主场景的原有配置语义。不能在共享的 MemoryConfig 上按请求原地切换，避免并发串范围。

### B. 四个工具贯穿目标选择

- [ ] 在 `capability_registry.py` 的宿主 schema 投影中加入 `conversation` 与简短规则，移除宽范围参数，更新工具契约版本与相关快照。
- [ ] `RetrieveMemoryToolHandler.normalize_call` 保留并严格校验新字段，其余 handler 使用相同规则。
- [ ] 使用单独的读取目标对象/参数传到 service 和 manager。不要把全局 `ToolExecutionContext` 换成目标身份后继续执行原工具生命周期。
- [ ] 工具调用、返回证据、receipt 和最终回复仍写入发起会话的原开放 turn；读取不会在目标会话新增用户消息、工具观察或回复。
- [ ] 时间线、目录、open 都选择目标 `MemorySystem` 并限定该 conversation。现有日期、语义过滤、source layer、kind 授权、页大小与错误结果保持原含义。
- [ ] legacy/dual 路径同样不能绕过新范围。如尚不能提供精确单会话读取，应结构化返回不支持；不要回退到按 profile 全量读取。当前主线是 MemCore，无需顺手重建旧记忆后端。

### C. 跨目标语义检索的可见排除

这是必须单独验证的点：`retrieve_for_turn_structured` 会根据它所属的系统排除“该系统近期可见的记忆”。当在 A 的回合里查询 B，B 的近期历史通常没有呈给 A 的模型；直接在 B 上调用 turn-aware 方法可能把最需要的近期消息全部排除。

- [ ] 当前会话继续使用 `retrieve_for_turn_structured(..., cross_conversation=False)`，保留本轮与可见历史去重。
- [ ] 外部目标推荐使用已有公开 `retrieve_structured(..., cross_conversation=False, exclude_source_ids=...)`，宿主只排除发起回合确实已呈现、且适用于目标的来源；不使用 B 的默认可见历史作为排除集，不伪造 B 的 current stimulus。
- [ ] 保留 host-authorized kind 过滤、显式轨迹权限、lineage 限制及既有结果预算。不能为了复用 native dispatcher 而重新开启所有 conversation。
- [ ] 如现有公开 API 确实无法传递所需的可见集合，才为 MemCore 提议通用的“查询目标与实际可见上下文分离”接口；不得加入 QQ 或主人分支。

### D. 来源、阅读接力与游标

- [ ] 结果统一带 `conversation` 和可读来源标签，保留原时间、actor、source/memory ID；不要把群员归并成主人。
- [ ] 后续 `open_memory`、段内检索和 anchor 扩窗必须继续携同一 `conversation`。跨会话结果给出可直接照抄的最小下一步参数。
- [ ] 没传目标时不得为了打开某个 ID 而扫描全部会话自动找归属；ID 指向另一个目标时返回明确失败。
- [ ] 分页采用宿主封装的不可伪造 opaque continuation，将原生 cursor 与发起会话、目标、Bot、角色、工具种类绑定，复用项目已有 token/引用基础设施（如有）。返回给模型的下一页只需传 cursor，不要求重复一串身份参数。
- [ ] 每页重新检查共享开关及权限；关闭开关后旧跨会话 cursor 不再有效。拒绝篡改、跨用户转用、跨 Bot/角色转用、与显式目标冲突的 cursor。
- [ ] 不信任模型直接提交的旧原生宽范围 cursor，不在 Akane 复制 MemCore 私有 cursor 解码实现。宿主只解自己的封装，授权后原样交回包继续分页。
- [ ] 封装只负责访问控制与路由，不另做分页算法或内容缓存。重启后若不能恢复 continuation，明确要求重查，不退化为全库查找。

### E. 帮助、提示与交付

- [ ] 稳定工具说明保持简短：“默认当前会话；需要其他场合的记忆时先发现/指定 conversation；空结果不代表所有会话都没有记录”。
- [ ] 保留模型主动求证指引，但不增加每轮 router LLM、全量目标目录注入或自动轮询其他会话。
- [ ] 配置示例与文档区分“允许跨会话”与“默认单会话”。本期配置开关可走既有宿主配置链，不要求新增设置 UI；若增加 UI 则另补 build/交互验收。
- [ ] 说明关闭开关只阻止后续跨会话读取，不会抹掉此前合法读取后已写入发起会话的工具证据；不做破坏性回溯清理。

## 6. MemCore 的通用性与预计文件范围

优先仅修改 Akane 的接入层，复用公开 `MemorySystem`，维持 MemCore 硬命名空间隔离。宿主根据明确共享授权选择另一个系统，是宿主路由；不是把包内跨用户检查关掉。

预计 Akane 文件：

- 新增宿主记忆目标/授权服务及只读会话目录适配，文件名由实施者按现有模块结构确定。
- `companion_v01/capability_registry.py`、`tool_handlers/memory.py`、`tool_handlers/catalog.py`。
- `companion_v01/retrieval_engine.py`、`memcore_integration/manager.py`、`memcore_integration/timeline.py`。
- `companion_v01/engine.py`、`bot_runtime.py` 的必要依赖注入；QQ 入口仅补缺失的可信上下文，不重构路由。
- `config.py`、`.env.example` 与实例有效配置所需的映射；必要时宿主会话登记/目录接口。
- 对应测试和文档。

不要求改 MemCore schema、迁移历史、拷贝消息、重建所有 embedding、增加第二套摘要或长期记忆库。若必须修改 MemCore，先在其仓库按 AGENTS 完整读取接入文档，添加与平台无关的 API 和测试，再走正式包接入；不得给 MemorySystem 增加 `master_qq`、群聊权限等参数。

## 7. 验收矩阵

使用隔离临时数据库、真实 MemCore 公共 API、可控测试 embedding/LLM，至少准备：同 Bot 的主人会话、群 A、群 B、非主人私聊 U、同 profile 的另一个 session，以及另一 Bot/另一角色的同名目标。每个位置写唯一标记，并同时覆盖 raw 与压缩后的摘要/语义层；不能只 mock 掉整个 manager 后测试返回值。

| 场景 | 验收结果 |
|---|---|
| 四工具均省略 conversation | 只命中当前 session；同 profile 的其他 session 也不命中 |
| 主人 → 群 A、群 A → 主人、群 A → 群 B | 开启后可双向查到目标；不混入第三个会话 |
| 普通群员在群 A 触发查询主人 | 允许，符合用户要求；actor 归因仍准确 |
| 非主人私聊 U 指定 master/群/他人私聊 | 统一拒绝，manager 不读取目标；目录只显示 current |
| 主人/群指定非主人私聊 | 拒绝，不扩大共享池 |
| 开关关闭后重试、使用旧 cursor | 明确拒绝跨会话；current 仍正常 |
| 同样的群号，在两个 Bot 或不同角色 | 各自隔离，不跨 engine/角色读取 |
| current 无命中、目标无命中 | empty，不自动搜索其他位置，不把异常伪装成 empty |
| 目标最近一条消息尚未压缩 | 语义检索可命中；不被目标的 turn-aware 可见排除误删 |
| browse → open content/sources → 段内检索 → 时间线扩窗 | 目标和权限全程一致，来源可追溯 |
| ID 属于别的会话但未指定它 | 拒绝/不可用，不自动找库；批量 ID 也不能扩大目标 |
| 时间线/目录/来源分页 | 返回完整页和 continuation；次页保持目标，拒绝篡改/转用/混目标 |
| 旧 cross_conversation=true、all、数组、无效类型、伪造身份 | 明确失败，不能静默变为宽范围检索 |
| 工具结果写入、慢请求、并行读两个目标 | 写入原发起 turn，无目标消息写入，无共享可变 scope 串线 |
| 重启、历史会话目录、空目录 | 已有可读目标仍可发现；不完整/不可用如实呈现 |
| embedding 故障、数据库错误、包不支持、目标不可用 | 保留失败原因，无全库或 legacy 宽范围兜底 |
| 非主人同 profile 有其他 session 摘要 | 默认 prompt 与工具都不泄露其他 session 内容 |

重点修订 `tests/test_memcore_integration.py` 中现有“默认 cross_conversation=True”的断言，增加行为证据，不能只把 True 改成 False。同时核对工具 schema、normalize、handler 注册、能力暴露与 request context 真实调用链。

建议验证命令（实施后执行，本计划阶段未运行这些回归）：

```powershell
git diff --check
.venv/Scripts/python.exe -m unittest tests.test_memcore_integration tests.test_memcore_keyword_catalog tests.test_memory_timeline tests.test_tool_runtime tests.test_memcore_runtime_contract_guard tests.test_qq_group_identity tests.test_qq_multi_bot_dispatch
```

另运行新增目标策略、目录、真实多 namespace 检索测试，对修改的 Python 文件执行 py_compile。若改 MemCore，按其 AGENTS 跑库测试、lint、format、build，并验证宿主导入的是该构建版本。没有前端修改无需前端 build。

## 8. 完成条件与实施报告

- [ ] 在真实宿主工具链中可调用，不只有孤立 helper 或说明文档。
- [ ] 主人/群双向共享、非主人私聊隔离、默认单会话、单目标检索四条同时通过。
- [ ] 模型只需可选 conversation；目录与分页不暴露存储路径、硬 namespace 或秘密。
- [ ] 每次查询仅路由一个目标，没有全库检索、自动范围放大或额外 LLM 判断。
- [ ] 运行时有效开关、开启方式、来源标签、失败行为有明确说明；真实部署参数不在无授权情况下改写。
- [ ] 汇报实际修改文件、测试结果、是否改了 MemCore 包、是否需要重启，以及未完成的真实 QQ 验收。
- [ ] 本执行单不授权提交或部署；后续按用户要求处理 commit。保留现有无关工作区内容。
