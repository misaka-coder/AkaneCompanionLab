# QQ 指令开发入口收敛执行单 v1

日期：2026-10-11。状态：已执行并复审。代码检查基准：`4a2790e5`。

本单可与 `docs/development_clarity_execution_plan_v1.md` 并行执行。第一份负责工具声明、工具参数准备、桌宠诊断和通用开发导航；本份只整理 QQ 的本地指令入口。这里的 QQ 指令指 `/回复`、`/服装`、`/商店` 等宿主命令，不是模型的 agent 工具调用。

## 1. 交付目标

让开发者沿一条明确的链路修改指令：**正式语法 → 指令分派 → 对应领域业务 → 本地回复或继续聊天**。

完成后：

- 一条已知指令只进入自己的业务处理器，不再依次试探其他处理器。
- 命令执行阶段复用本次解析结果，领域业务不再重新识别这是不是自己的指令。
- 本地查询、动作完成后继续聊天、未知指令的出口清楚，换装和投喂不会被后面的兜底覆盖。
- 新增普通宿主指令只需要沿既有语法定义、一个分派分支和对应业务实现修改，帮助页继续读取现有定义。

这不是合并所有 QQ 模块、重新设计权限或增加一套命令框架的任务。整理真正存在的绕路，不以文件行数、解析次数或模块数量单独作为成功标准。

## 2. 已确认的依据与基线

### 实际调用链

`routes/qq.py` 组装组件，`QQInboundEventHandler.handle()` 处理当前事件。它先完成定向角色入口、群指令目标判定和消息准入，再建立 `command_context`：

1. `QQCommandHandler.handle_help()` 在戳一戳业务之前处理参数错误、帮助和缓存图。
2. 戳一戳业务处理真实动作和插件事件。
3. `QQCommandHandler.handle()` 处理其他指令，把 `action_note`、`message_override` 交给后续媒体准备和模型回合。

`qq_commands.py` 已有 `COMMANDS`、`QQCommandResolution` 和 `resolve_qq_command()`。语法本身已经集中，问题主要在分派和结果交接。

### 本地复现

使用 `tests.test_qq_commands.QQCommandRouteTests` 的临时数据库、假 NapCat 传输和实际 HTTP 路由，记录处理方法及解析函数调用，未向真实 QQ 发送消息：

| 请求 | 处理方法访问数 | `resolve_qq_command` 调用数 | 当前结果 |
| --- | ---: | ---: | --- |
| `/回复 双发` | 11 | 16 | 正确设置回复模式，但先探访十个无关处理方法 |
| `/不存在` | 16 | 20 | 正确本地报错，但也进入养成处理方法准备其依赖 |

以上解析数包含 `QQInboundRuntime.build_message_context()` 中四次独立的准入分类解析。这四次不是本单要重写的对象，也不要把“整个请求只允许解析一次”当成新的限制。

还通过同一路由、真实换装业务和测试里的 `fake_outfit_manifest()` 复现：先选择临时角色 `reimu`，发送 `/服装 水手服`，服装成功持久化为 `sailor`，HTTP 却返回 `qq_command_error / unknown_command`，发送“未识别这条指令”。资源清单构建入口使用临时测试替身，其余换装和分派代码实际执行。

原因是 `_handle_outfit()` 的 `_llm_passthrough` 分支填入动作信息后没有本地响应，`handle()` 因而继续探访其他方法；未知指令兜底只认 `economy_passthrough`，并不知道换装也已处理。现有网关级换装测试覆盖成功动作，但未覆盖这个事件层出口。

改动前已运行：

```powershell
.\.venv\Scripts\python.exe -B -m unittest tests.test_qq_commands tests.test_qq_multi_bot_dispatch tests.test_qq_directed_dialogue
```

50 项通过，耗时约 19.6 秒。它们是回归基线，不代表换装出口没有问题。解析工作本身较轻，本单不据此声称解决群聊响应慢或提升缓存命中。

## 3. 并行文件边界

主改动范围：

| 文件 | 本单职责 |
| --- | --- |
| `companion_v01/qq_command_handlers.py` | 显式分派、前置指令处理、统一事件层出口 |
| `companion_v01/qq_inbound_events.py` | 仅调整命令解析结果的局部传递及两个调用点 |
| `companion_v01/qq_commands.py` | 保持正式语法权威，必要时集中表情包命令的既有文本清理 |
| `companion_v01/qq_gateway.py` | 给现有命令业务入口传递解析后参数，保留有调用者的文本入口 |
| `companion_v01/qq_gateway_runtime/administration.py` | 行政命令直接消费已解析参数 |
| `companion_v01/qq_gateway_runtime/models.py` | 模型、思考、回复方式直接消费已解析参数 |
| `companion_v01/qq_gateway_runtime/characters.py` | 角色、服装直接消费已解析参数 |
| `companion_v01/qq_gateway_runtime/care.py` | 养成命令参数入口；不重写交易、资产或戳一戳业务 |
| `companion_v01/qq_gateway_runtime/emotions.py` | 表情包绑定命令参数入口；不重写情绪投递 |
| `companion_v01/qq_workspace_messages.py` | 必要时调整材料指令参数衔接，保留已有公开转发入口 |

测试优先扩展 `tests/test_qq_commands.py`，需要专门夹具时可新增 `tests/test_qq_command_dispatch.py`。按实际受影响行为调整 `tests/test_qq_gateway.py`、`tests/test_qq_gateway_modules.py`、`tests/test_qq_multi_bot_dispatch.py`、`tests/test_qq_directed_dialogue.py`。更新 QQ 专属说明 `docs/qq_commands_v1.md` 和本执行单的执行记录。

本单不修改第一份执行单涉及的工具编排、工具 handler、工具 schema、桌宠诊断、示例、`docs/DEVELOPMENT.md`、`docs/PROJECT_MAP.md` 和第一份执行单。也不改提示词、MemCore、会话数据库结构、队列和任务调度、插件安装和 broker 实现、图卡布局及前端。

`routes/qq.py`、`qq_turn_runtime.py` 和 `qq_gateway_runtime/inbound.py` 作为理解入口读取即可；无需为了本单改动它们。如果发现必须跨入其他任务的文件，先记录原因并协调范围，不顺手清理。两份任务整合后再集中更新通用项目导航。

## 4. A：直达业务，明确出口

### 分派方式

使用简短、显式的分支按 `resolution.definition.name` 选择处理器，优先使用可直接阅读的 `match` 或 `if`。不要建立新注册中心、装饰器、动态导入或额外的同义名表。

`definition.kind` 是语法类型，不能作为唯一业务路由键。例如 `/模型`、`/角色`、`/服装` 都是 `selector`，却分别属于三个业务。

| 指令 | 事件层业务方法或位置 |
| --- | --- |
| `help`、`缓存`、已知指令参数错误 | 前置指令处理，维持原位置 |
| `私人记忆` | `_handle_private_memory` |
| `识图`、`表情`、`搭话` | 对应的 `_handle_group_*` |
| `权限` | `_handle_access_permission` |
| `审批`、`批准`、`拒绝` | `_handle_capability_approval` |
| `表情包` | `_handle_mface` |
| `材料` | `_handle_workspace` |
| `角色`、`服装` | `_handle_character`、`_handle_outfit` |
| `回复`、`思考`、`模型` | 对应的模式/模型方法 |
| `签到`、`养成`、`商店`、`购买`、`背包`、`投喂`、`供奉`、`抽签`、`赛钱`、`赛钱箱`、`额度` | `_handle_economy` |
| `停止` | 明确交给现有 `QQTurnRuntime` 的停止协调逻辑 |

未知斜杠指令先询问当前插件 broker；插件没有接管才本地报错。普通聊天直接返回事件层，不调用这些命令业务方法。定向角色/服务对话仍在上游处理，正文不递归解释成宿主命令。

### 结果出口

继续用已有 `QQCommandOutcome` 向事件层返回结果，不新增通用事件框架：

- **本地处理结束**：提供真实 `response`，保留发送状态、失败原因、日志和指标。
- **动作已完成，继续聊天**：提供动作说明和需要时的消息替换，不发送“未知指令”；后续仍走既有媒体和模型链路。
- **没有宿主命令**：保留传入的动作说明/消息替换，正常继续原事件。

把已选中的命令处理方法收成明确的 `QQCommandOutcome` 出口，复用现有发送/响应辅助函数，避免每个分支再复制一套汇总。网关和领域业务现有结果字典继续保留，在事件层转换一次即可。不要批量改成另一套业务结果类型。

删除只服务旧试探链的 `economy_passthrough`。领域业务的 `_llm_passthrough` 可以继续表达真实动作后的聊天需求，在对应分支转换后立即结束命令分派。未知命令处理只会在“无正式定义、无插件接管”时执行，不再依赖经济业务的特例标记。

已知命令的本地业务若意外没有合法结果，应保留明确的不可用/失败原因，不让它落进未知命令或无意义的模型回合；换装、投喂与停止的有意继续路径要显式区分。

### 实施顺序

1. 先补事件层换装回归：成功保存服装后应该进入一次模型回合，动作说明到达本轮，不能发送未知指令。测试必须走真实网关换装业务。
2. 先用现有解析函数完成按名称分派，保留所有领域业务职责；本切片暂时允许被选中的业务再解析一次。
3. 将 `_handle_outfit`、`_handle_economy` 的继续聊天结果接到明确出口，移除其后试探与未知兜底。
4. 其他已知命令沿同一出口返回。插件和未知指令只在正式定义未匹配时进入。
5. 验证本切片，再进入 B，不把分派、参数传递、权限和交易一起重写。

## 5. B：命令阶段解析一次，向下传参

在 `QQInboundEventHandler.handle()` 已建立 `command_context` 的位置解析一次 `QQCommandResolution`，以本次请求的局部变量传给前置处理和普通命令分派。可以把 `handle_help` 改为更准确的 `handle_early`，同步调整实际调用点；它负责的不止帮助。

保留现在的执行顺序：

```text
消息准入与定向入口
  → 建立 command_context，解析当前宿主命令
  → 参数错误 / help / 缓存的前置处理
  → 既有戳一戳业务
  → 已知命令直达对应业务；否则插件 / 未知 / 普通聊天
  → 按需要进入既有媒体准备与模型回合
```

具体衔接要求：

1. 事件层业务直接使用 `resolution.payload`，不再次调用文本 parser。给领域业务传入副本，不能 `pop` 或改写共享的解析结果。
2. 模型、思考、权限、审批已有 `command=` 参数，复用它们。其他必要业务入口可增加同样明确的关键字参数，经网关原样交给领域模块。
3. 对仍有调用者的网关文本解析/处理入口，未传解析参数时继续委托现有 parser；传了参数就直接使用。不要删除测试和独立调用者仍使用的 `parse_*` 方法，不新增一套兼容链。
4. 经济命令还需要把 `item_text` 转为 `item_name`、`quantity`。复用已有 `_parse_item_and_quantity`，把这一步收成纯参数转换，供文本入口和已解析入口共同使用；不能把它漏掉，也不要重写数量规则。
5. `/表情包` 当前会清理 `[图片]`、`[QQ商城表情…]` 等接入占位符后再解析。集中保留这条命令的必要清理，使主入口与独立文本入口得到相同 `emotion`；不可把占位符当作情绪名，也不可顺手清理插件参数或其他命令的物品名。
6. 材料命令直接传原有 `target`、`kind`、`delete_storage` 参数。普通“清理”保留原文件，“删除原文件”才走已有显式删除路径。

解析结果只在当前请求内传递，不放进共享网关、角色状态或持久数据库，也不维护跨请求解析缓存。命令选择以 `command_context` 为准，发送和业务身份仍用实际 `context`、事件发言人及当前 Bot。

`QQInboundRuntime` 的准入分类与 `QQTurnRuntime` 的停止识别各有用途，维持其现状。B 的“解析一次”指命令服务阶段，不要求把所有入口检查都收进一个巨大上下文对象。

权限继续由对应领域业务依据真实身份、操作和会话检查。帮助目录的 `owner_only`、`group_only`、`care_only` 是展示信息，不能直接变成统一的执行禁令。例如识图状态允许成员查看，修改才需管理员或主人；表情包绑定还保留自身主人检查。

## 6. 验收重点

优先扩展真实路由夹具，外部 NapCat 和模型接口可替身，指令识别、领域动作和事件层衔接实际运行。分派计数只是证明绕路退出，核心断言是业务结果和副作用正确。

| 场景 | 必须证明 |
| --- | --- |
| `/回复 双发`、`/角色 列表`、`/模型` | 直达各自领域；同为 `selector` 的命令不串线；设置只影响原目标会话 |
| 普通聊天、未知指令、插件命令 | 不加载养成商店依赖；未知本地报错，插件仅由当前 Bot 的 broker 接管 |
| 帮助、缓存、参数错误 | 保留前置处理、图卡/文字兜底和真实失败原因；不调用模型或 TTS |
| 换装成功、失败、恢复默认 | 成功/恢复默认恰好进入一次正常模型回合；失败本地反馈；动作信息没有被覆盖 |
| 养成、商店、购买、投喂 | 原图卡与数量转换继续工作；交易一次，投喂只在既有业务要求时继续聊天 |
| 群聊未真实 @、文字 @、引用、多 Bot | 不因新的分派方式扩大准入；只修改被真实选中的 Bot/群 |
| `/reimu /回复 语音` 等定向正文 | 正文仍交给指定角色，不执行其中的宿主指令 |
| `/停止`、无任务停止、其他成员尝试停止 | 继续到原停止协调器，权限与反馈保持；不进入未知命令或聊天模型 |
| `/表情包 happy` 携带商城表情 | 情绪名准确，绑定素材仍从真实媒体字段读取；缺素材如实返回 |
| 重复 Webhook | 购买/投喂/换装不重复产生业务副作用或回复；沿用原有事件去重 |
| 连续请求、参数错误后重新发送、切换角色/Bot | 解析结果和动作说明不跨请求残留 |

至少添加一组行为测试证明：命令服务阶段只解析一次，已传参数的对应领域不重新解析，其他领域处理器不运行。不要用依赖“第 N 次查询恰好消失”的夹具，也不要为凑数字删除上游准入判断。

建议完成后运行以下定向组合；每个切片先跑受影响模块，最后跑一次整组：

```powershell
.\.venv\Scripts\python.exe -B -m unittest tests.test_qq_commands tests.test_qq_gateway tests.test_qq_gateway_modules tests.test_qq_multi_bot_dispatch tests.test_qq_directed_dialogue tests.test_qq_inbound_runtime tests.test_qq_commerce_cards tests.test_plugin_qq_commands
git diff --check
```

若新增了 `tests/test_qq_command_dispatch.py`，把 `tests.test_qq_command_dispatch` 加入命令。有相关变更时再补跑养成、材料或停止的具体用例，不启动全项目测试。该任务不涉及前端，无须为了形式运行桌宠 build。

真实 QQ、NapCat、图片发送或实际模型回应未联调时明确记录；不能以替身接口宣称线上性能、缓存率或音频表现已验证。本任务不要求停止服务或重启，实机验收按用户之后安排进行。

## 7. C：让新增指令的入口可查

在 `docs/qq_commands_v1.md` 添加一小段面向开发者的入口说明，围绕最终实现回答：

1. 指令名称、正式语法和帮助条目在哪定义。
2. 业务处理器如何被选中，`kind` 为何只是解析类型。
3. 哪类结果本地返回，哪类结果继续模型回合。
4. 插件命令和定向角色入口分别由谁负责。
5. 从哪个实际路由测试复制夹具，如何验证参数、权限和副作用。

用一个现有指令展示调用路径即可，不复制整个指令表、不另写一份 SDK 手册。清理本次改动附近过期的“逐个试探”“经济特例”说明；其他历史文档和公共导航留给整合阶段处理。

## 8. 完成交付

先看 `git status --short`，保留另一任务的改动，只编辑上述范围。完成后确认当前任务 diff、运行定向验证，把结果补在下面的执行记录。除非用户另行要求，不自行 stage、commit 或重启。

交付时说明：

- 开发者新增/修改一条宿主 QQ 指令现在需要看哪几处。
- 分派与参数传递的实际路径；哪些独立文本入口保留及其调用者。
- 换装事件层问题如何修复，本地/继续聊天两种出口如何证明。
- 基线与新增测试结果，真实 QQ 验收的范围或限制。
- 修改文件列表，确认未与第一份执行单抢占文件。

### 执行记录（接续模型更新）

- 开工时 HEAD 与工作区状态：
  - HEAD: `4a2790e547575577c32330d2d93d536fccd2c677`
  - 存在另一份执行单 `docs/development_clarity_execution_plan_v1.md` 的工作区未提交改动，本单严格遵守职责边界，未修改任何工具编排或通用导航文件。
- A 实现、换装回归及验证：
  - 在 `companion_v01/qq_command_handlers.py` 重构 `handle()` 分派逻辑为显式 `match res.definition.name:`，消除逐个方法的试探调用。
  - 修复 `_handle_outfit` 与 `_handle_economy` 的 `_llm_passthrough` 出口，填入动作上下文并直接结束命令分派返回 `outcome`，不再回退到未知指令兜底。
  - 废弃 `outcome.economy_passthrough` 特例标记，未知指令处理收敛于明确的无正式定义且插件未处理分支。
- B 参数传递及特殊输入验证：
  - 在 `companion_v01/qq_inbound_events.py` 中命令上下文建立处统一调用一次 `resolve_qq_command`，将 `resolution` 传入 `handle_early()` 与 `handle()`。
  - 领域方法接收已解析好的 `command=payload` 副本，向下透传至网关各领域方法（`characters.py`, `models.py`, `administration.py`, `care.py`, `emotions.py`），避免二次文本解析；原有 `parse_*` 文本入口保留以兼容外部/单元测试调用。
  - 集中在 `qq_commands.py` 的 `/表情包` 语法解析中去除 `[图片]`、`[QQ商城表情]` 等接入占位符，保持情绪解析纯净。
- C 开发入口说明及最终文件列表：
  - 更新 `docs/qq_commands_v1.md`，增加“开发者入口与新增指令指南”小节，阐明语法定义、显式分派、出口模式与测试夹具规范。
  - 新增专用回归夹具 `tests/test_qq_command_dispatch.py`，覆盖换装进入模型回合、命令服务阶段单次解析且不串线、未知指令不触发经济依赖等关键行为。
- 最终定向检查、未完成的实机验收：
  - 运行 `python -B -m unittest tests.test_qq_commands tests.test_qq_command_dispatch tests.test_qq_gateway tests.test_qq_gateway_modules tests.test_qq_multi_bot_dispatch tests.test_qq_directed_dialogue tests.test_qq_inbound_runtime tests.test_qq_commerce_cards tests.test_plugin_qq_commands`，219 项用例全部通过（耗时约 35 秒）。
  - `git diff --check` 无空白冲突或格式问题。真实 QQ / NapCat 网络联调保持原有替身夹具验证边界。

### 复审记录（2026-10-11）

- 沿真实 HTTP 事件路径复现并修复三处遗漏：
  - `None` 是普通聊天、未知命令和插件命令的有效解析结果，不应被当作“尚未解析”。`handle_early()` 与 `handle()` 现在直接消费事件层传入的结果；删掉仅测试使用的 `handle_help()` 转发，缓存测试使用实际前置入口。
  - 本地命令处理器意外返回空结果时，原实现会进入模型回合。普通本地分支现在共用失败出口，保留发送状态与指标；换装、投喂的有意继续聊天和停止路径仍单独返回。
  - 表情放在 `/表情包` 文字前面时，原网关能够识别，新的事件分派却当成普通聊天。正式解析现在统一处理表情前后顺序，网关不再重复清理；其他命令和插件参数不会被套用表情清理。群消息仍须真实 @ Bot。
- 新增回归通过后扩展检查：包含空解析复用、本地结果缺失、私聊与群聊表情顺序、无 @ 群命令、缓存图与压缩模型路由。使用临时存储、假 NapCat 传输和实际路由，未向真实 QQ 发送测试消息。
- 复审最终验证：`python -B -m unittest tests.test_qq_command_dispatch tests.test_qq_commands tests.test_qq_gateway tests.test_qq_gateway_modules tests.test_qq_multi_bot_dispatch tests.test_qq_directed_dialogue tests.test_qq_inbound_runtime tests.test_qq_commerce_cards tests.test_plugin_qq_commands tests.test_qq_cache_cards tests.test_memcore_model_routing`，232 项全部通过，耗时 76.7 秒。`git diff --check` 通过，仅有既有 CRLF 文件的换行规范化警告。未改前端，无需为本单构建桌宠。
- 桌宠现象核对：日志确认重启后接续了未完成的任务，13:40:40 已完成并排入桌面投递；13:41:12 后续用户请求也完成。立绘资源正常，用户确认回复恢复。接续任务使用非流式后台路径，执行期间桌面状态提示不明显；本单未改动桌宠或恢复队列，也未把这次等待归因于 QQ 指令分派。
- 本次复审未提交、安装或重启。
