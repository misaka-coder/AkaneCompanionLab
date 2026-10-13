# QQ 模块职责整理执行单 v1

日期：2026-10-13。源码核对基准：`90d8e156`。

状态：已完成，代码和文档已合入主工作区，未推送或重启生产服务。本文记录范围、归属和验收；现行导航放在 `companion_v01/channels/qq/README.md`。本执行单保留过程与验证记录，不再维护第二份现行文件地图。

## 1. 目标与范围

从 QQ 开始建立一份容易使用的局部地图：收到什么、由谁处理、调用哪些共享能力、结果由谁发送，以及修改一个行为应该读哪些文件。以职责和真实调用链为依据整理现有代码，目录和文件名随后确定。

交付包括：

1. QQ 专属实现集中到 `companion_v01/channels/qq/`；现有网关内部模块一并归位。
2. 制作内容、编排业务与发送结果各有明确归属；共同使用的能力有自己的实现位置。
3. QQ README 逐文件说明职责，给出主要消息流程、共享服务边界、修改入口与验证方法。
4. 实际改动范围内的函数名、模块说明和必要注释表达真实职责，删除已过时的说明。

本轮整理 QQ 及与它直接相连的共享边界。其他客户端、模型循环、MemCore 内核、插件系统等全域重构另行处理。发现更大的混合职责时，写清调用方和建议后续范围；不能把它们一起搬进 QQ 包，也不能默默忽略。

## 2. 统一归属标准

**按模块提供的能力归类，按触发方式安排入口。** 有没有请求模型、谁调用它、文件名是否有 `qq_` 前缀，都不能单独决定归属。

对函数或类核对三个问题：

- 提供什么能力，输入是什么，输出交给谁？
- 哪些数据和规则确实属于 QQ，哪些由共享服务决定？
- 当前调用方与可变状态在哪里，移动后还有没有唯一的实现和状态 owner？

调用能力与实现能力分开。例如命令函数可以获取统计、调用图卡绘制、调用交付接口；它拥有指令流程，绘制和发送实现分别由对应模块负责。简单编排保留完整，不为了每个步骤多建一个文件、接口或结果封套。

| 位置 | 拥有的职责 | 与其他模块的交接 |
| --- | --- | --- |
| `routes/qq.py` | HTTP 请求、鉴权、账号校验、HTTP 响应 | 调用 QQ 事件入口；保留现有 URL、状态码与响应语义 |
| `onebot/` | Bot 绑定的协议请求、动作声明、Ark 适配 | 查询与发送都能调用它；复用 `channelcore_onebot`，不复制协议解析 |
| `inbound/` | 入站消息、消息指向、引用/转发、附件准备、事件分流 | 交给指令、QQ 回合、背景记录或插件；材料存储和识别调用共享服务 |
| `turns/` | QQ 的排队适配、回合驱动、主动观察、恢复及回复准备 | 使用共享队列和模型/工具引擎，不重写另一套 agent 循环 |
| `commands/` | 正式语法、指令分派、QQ 业务适配与编排 | 调用业务服务、制作能力和交付接口 |
| `cards/` | QQ 专属图卡内容组织、排版、绘制与图片缓存 | 接收准备好的数据，返回图片；不决定发送目标或送达状态 |
| `delivery/` | QQ 交付目标、发送格式、发送、回执、去重与失败处理 | 消费准备好的正文和制品，经 OneBot 发送 |
| 共享服务 | 模型/工具循环、记忆、账号关系、养成、素材、TTS 等通用能力 | QQ 只传必要输入、使用真实结果，不另存第二份权威状态 |

收到的小程序、音乐或链接卡片属于消息协议；自己绘制的状态图卡属于 `cards/`。二者不能因为都叫“卡片”而放进同一个制作模块。

QQ 所需的上传、消息分段、引用格式和传输格式转换属于交付适配；图卡绘制、语音合成、文件生成属于内容制作。语音合成复用共享 TTS，QQ 的声线选择和回复音频缓存可以保留在 QQ 回复准备层。

## 3. 目标目录

```text
companion_v01/
├── routes/
│   └── qq.py                  # HTTP 入口
└── channels/
    └── qq/
        ├── README.md          # 现行 QQ 局部地图
        ├── assembly.py        # 实例、依赖和回调装配
        ├── gateway.py         # 外部调用 QQ 的入口
        ├── context.py         # 消息/身份/会话上下文及必要视图
        ├── identity.py        # 身份、昵称与群成员信息
        ├── profiles.py        # Bot 连接档案
        ├── preferences.py     # QQ 会话偏好
        ├── onebot/
        ├── inbound/
        ├── turns/
        ├── delivery/
        ├── commands/
        └── cards/
```

包的 `__init__.py` 保持轻量，不在导入时装配运行实例。无状态的 QQ 常量、校验和资源适配按实际调用方归位；需要共同使用时可以放在包根的具名模块，避免做一个无边界的 `utils.py`。

`assembly.py` 构造组件和连接既有回调；事件处理仍由具体模块执行。`gateway.py` 保留有真实调用者的 QQ 接口并委托到实现，不再新增业务到入口。内部模块不反向导入装配模块以取得“当前 Bot”。

先建立迁移表，再随一个可验证切片创建它需要的目录和文件；不把空目录、占位实现或提前生成的 README 当成交付。

## 4. 数据流与职责交接

逻辑链路如下。它表达所有权，不要求每种事件都同步串行经过所有阶段。

```text
NapCat 事件
  → HTTP 接入与事件准入
  → 既有协议包解析有序消息段
  → 确定发送者、会话、消息指向，取得必要引用证据
  → 分流
      指令：解析 → 业务处理 → 回复/制品准备 → 交付
      聊天：内容准备 → QQ 队列/回合协调 → 共享模型与工具 → 回复准备 → 交付
      背景：按策略准备内容 → 共享记忆记录 → 按设置评估主动搭话
      插件：发布真实 QQ 事件 → 既有插件处理；需要回合时进入 QQ 调度
```

保持已有 `MessageChain`、来源标识、素材句柄和回合载荷。各层只产生需要的视图，不重新解析和重复包装同一份 JSON。

| 内容 | 解析阶段保留 | 内容准备阶段负责 |
| --- | --- | --- |
| 文字与 @ | 顺序、文本、提及对象 | 判断说给谁，准备聊天及记忆需要的内容 |
| 图片、文件、语音、视频 | 附件引用、所属消息、发送者及类型 | QQ 来源获取；共享登记、存储、识别/读取；保留未完成和失败状态 |
| 引用回复 | 原消息引用 | 查询原消息，保留被回复者与原说话人；必要时影响唤醒判断 |
| 合并转发 | 转发引用或内联节点 | 查询/展开节点，保留节点说话人、时间和附件来源 |
| 小程序、音乐、链接分享 | 协议包提供的分享元信息与解析状态 | 作为分享内容交给后续处理，进一步读取按需要进行 |
| 戳一戳等通知 | 参与者、对象和事件类型 | 群友之间的观察与对角色的互动分流；养成效果由原有业务服务处理 |

附件可以后台准备；未准备完成时继续使用现有状态与后续恢复路径。停止等前置指令保持及时处理。模型中途回复和最终回复均使用交付能力；图卡命令、模型生成文件、插件制品不会各自实现另一套发送规则。

记忆写入由原有记忆链路负责，交付只登记交付结果。发送失败不等于消息没有进入记忆；背景消息没有立即调用模型也不等于没有处理。

## 5. 当前源码归位清单

以下路径均相对于 `companion_v01/`，QQ 目标相对于 `channels/qq/`。表中多目标表示需要按函数/状态拆清，而非复制整份实现。新名字是归位目标，执行时可根据实际边界作有记录的小幅调整。

### 5.1 入口、上下文和协议

| 当前实现 | 目标 | 处理要点 |
| --- | --- | --- |
| `routes/qq.py` | 原 HTTP 路由 + `assembly.py` | 抽出实例装配，清理仅为旧布局存在的内部重导出；真实 HTTP 行为保留 |
| `qq_gateway.py` | `gateway.py` | 保留实际使用的调用入口，逐项核对委托对象与实例状态 |
| `qq_channel_profiles.py` | `profiles.py` | 连接档案，不是群偏好；同步 Host/部署装配的导入 |
| `qq_gateway_runtime/context.py` | `context.py` | 保留消息链、模型载荷、发送上下文和转发来源对应关系 |
| `qq_gateway_runtime/identity.py` | `identity.py`；交付记录部分核对到 `delivery/` | 不复制昵称、身份或回执状态；原实例隔离保持 |
| `qq_gateway_runtime/preferences.py` | `preferences.py` | 保留配置字段、原子保存、共享锁与字典引用语义 |
| `qq_gateway_runtime/definitions.py` | `constants.py` 或对应 owner | QQ 常量各有用途，不扩大成通用常量仓库 |
| `qq_gateway_runtime/validation.py` | 对应上下文、偏好、命令或交付的校验；共同部分具名保留 | 依据输入归位，保持原校验语义，不增加限制 |
| `onebot_transport.py` | `onebot/transport.py` | HTTP 传输、Bot 绑定与传输文件准备；不装入业务流程 |
| `onebot_model_actions.py` | `onebot/model_actions.py` | 保留既有动作声明、参数/目标适配和权限结果；核对工具登记调用方 |
| `qq_miniapp.py` | `onebot/miniapp.py` | Ark 结果适配、参数校验与作用域句柄；不画图片 |

### 5.2 入站与内容准备

| 当前实现 | 目标 | 处理要点 |
| --- | --- | --- |
| `qq_inbound_events.py` | `inbound/router.py` | 指向判断与分流协调；保留前置命令和插件接入顺序 |
| `qq_gateway_runtime/inbound.py` | `inbound/context_builder.py`；防抖到 `inbound/attachments.py`；恢复到 `turns/`；发送上下文重建到对应上下文/交付入口 | 一个类混有多个阶段，要核对函数及状态，不能只搬整文件就结束 |
| `qq_gateway_runtime/evidence.py` | `inbound/references.py` | 引用和转发查找、来源绑定及内容准备；继续使用协议包 |
| `qq_inbound_content.py` | 引用投影并入 `inbound/references.py`，附件上下文到 `inbound/attachment_context.py` | 时间/文本辅助按实际用途归位；保持给模型和记忆的渲染结果 |
| `qq_inbound_media.py` | `inbound/prepare.py` + 引用/附件具体实现 | 编排内容准备；引用查找交给 references，素材与像素准备使用共享服务 |
| `qq_group_notices.py` | `inbound/notices.py` | 包括群友互戳的事实观察，不把所有通知解释成对角色的养成动作 |
| `qq_passive_messages.py` | `inbound/observations.py` | 背景记录策略、调度与结果；共享记忆写入不复制到 QQ |
| `qq_plugin_input.py` | `inbound/plugin_input.py` | 真实事件发布、入站租约与处理状态交接，复用既有 broker |
| `qq_route_helpers.py` | 附件等待/状态到 `inbound/attachments.py`；服装/清单适配到具名 QQ 资源模块 | 拆除不同职责混在一起的 helpers 文件，保留现有资源选择行为 |

### 5.3 QQ 调度与回复准备

| 当前实现 | 目标 | 处理要点 |
| --- | --- | --- |
| `qq_session_queue.py` | `turns/queue.py` | QQ 工单提交、批次和恢复适配；底层持久队列不迁入 |
| `qq_session_work.py` | `turns/work.py`；记忆记录标识按实际归属分离 | 特别核对引擎对 `_passive_record_source_id` 的引用，避免共享引擎依赖 QQ 队列 helper |
| `qq_turn_runtime.py` | `turns/runner.py` | 停止、主人追加、普通排队和回合运行；保留现有 processing fence |
| `qq_turn_streaming.py` | `turns/streaming.py` | 消费模型事件并调用已有准备/交付能力，不重写工具调度 |
| `qq_attention_runtime.py` | `turns/attention.py` | QQ 主动观察调度、代次和实例状态 |
| `qq_group_attention.py` | `turns/attention_state.py` | 群聊注意力状态与判定，与运行调度区分 |
| `qq_followups.py` | `turns/followups.py` | 审批恢复和媒体后续回合，不是固定延时补发机制 |
| `qq_plugin_turns.py` | `turns/plugin_turns.py` | 插件意图进入现有 QQ 回合和队列 |
| `qq_voice_delivery.py` | `turns/reply_audio.py` | QQ 回复音频准备和缓存，调用共享 TTS；不因旧名含 delivery 就作为发送实现 |

### 5.4 交付

| 当前实现 | 目标 | 处理要点 |
| --- | --- | --- |
| `qq_gateway_runtime/outbound.py` | `delivery/actions.py` 或按实际职责具名拆分 | 模型动作目标、可见回执、Hook、近期 Bot 消息与诊断各有归属；网络请求交协议层 |
| `qq_gateway_runtime/messages.py` | `delivery/messages.py`；确有独立职责时拆出文件交付 | 文本、引用、音乐/贴纸、批量制品发送；格式适配不增加语义正文 |
| `qq_gateway_runtime/media.py` | `delivery/media.py` | 既有图片、音频和文件发送，保留路径投影、上传和失败回退 |
| `qq_gateway_runtime/emotions.py` | `delivery/emotions.py`；命令部分到 `commands/` | 保留表情选择、并发去重与失败后重试；绑定命令归命令处理 |
| `qq_reply_delivery.py` | `delivery/replies.py` + `turns/` 的回复准备调用 | 发声合成调用从发送实现中分离；保留文本/语音选择和失败回退顺序 |
| `qq_stream_delivery.py` | `delivery/stream.py` | 分段、重复片段过滤和失败发送补交付 |
| `qq_artifact_delivery.py` | `delivery/artifacts.py` | 插件制品读取、交付身份、真实回执判定 |
| `qq_tool_delivery.py` | `delivery/tool_port.py` | 工具调用的 QQ 发送/动作接口，不是中途汇报生成器 |

### 5.5 指令与图卡

| 当前实现 | 目标 | 处理要点 |
| --- | --- | --- |
| `qq_commands.py` | `commands/parser.py` | 正式语法、解析结果和帮助条目继续使用同一权威 |
| `qq_command_handlers.py` | `commands/handler.py` | 现有显式分派与业务出口；不退回逐个试探处理器 |
| `qq_gateway_runtime/characters.py` | `commands/characters.py` | QQ 角色与服装选择，资源和角色权威由既有服务提供 |
| `qq_gateway_runtime/models.py` | `commands/models.py` | QQ 会话模型与思考/回复方式选择，不实现模型 API |
| `qq_gateway_runtime/administration.py` | `commands/administration.py` | QQ 管理、权限与审批命令，不重写权限系统 |
| `qq_gateway_runtime/care.py` | `commands/care.py` | QQ 养成动作适配与戳角色处理，调用养成服务 |
| `qq_gateway_runtime/commerce.py` | `commands/commerce.py` | QQ 商店、背包、购买和投喂适配，交易权威保持原归属 |
| `qq_gateway_runtime/care_results.py` | `commands/care_results.py` | 当前请求的数据和 QQ 结果表达，公共养成规则留原服务 |
| `qq_poke_reactor.py` | `commands/poke.py` | QQ 戳一戳计划；通知中的群友互戳仍归入站观察 |
| `qq_workspace_messages.py` | `commands/workspace.py`；跨分支共用格式辅助具名归位 | QQ 材料指令和结果表达，素材检索仍走共享服务 |
| `qq_command_cards.py` | 业务准备到 `commands/`，生成到 `cards/`，实际交付助手到 `delivery/card_results.py` | 可以保留清楚的命令编排，不能把读取业务、绘制、发送继续混成一个“纯 cards”类 |
| `qq_help_cards.py` | `cards/help.py` | 帮助图卡制作 |
| `qq_care_cards.py` | `cards/care.py` | 状态图卡制作 |
| `qq_commerce_cards.py` | `cards/commerce.py` | 商店、背包和购买结果图卡制作 |
| `qq_cache_cards.py` | `cards/cache.py` | 日缓存命中统计图卡制作，统计数据由已有服务提供 |

旧 `qq_gateway_runtime/__init__.py` 随其实现归位后退出，不保留另一套同名实现目录。

### 5.6 共享能力及需要单独辨明的边界

| 当前实现或调用 | 归属与处理 |
| --- | --- |
| `text_cards.py`、`text_card_specs.py`、`tool_handlers/text_cards.py` | 桌宠/QQ 共用图文工具。渲染属于共享绘图，工具声明/执行属于共享工具链；不搬入 QQ 的 cards |
| `qq_card_artwork.py` | 字体、绘图、图片缓存被通用图卡使用。把共享绘图实现定位到共享渲染层；角色信纸是主题资源。必须核对 `__file__` 相对资源路径和渲染缓存标识 |
| `qq_music_audio.py` | 实际为公开音频 URL 预检，主体不依赖 QQ 会话。按通用媒体能力归位，QQ 交付调用它；保留真实网络、重定向和识别行为 |
| `attachment_ingest_runtime/qq_sources.py` | QQ 来源适配与通用素材落地目前同在一个 Mixin。QQ 来源查找归适配，通用存储留素材服务；不能把整个素材子系统搬到 QQ |
| `tool_handlers/qq_onebot.py` | 模型工具执行适配。协议实现归 QQ，handler 仍接共享工具链和已有 QQ port，不额外生成一套 schema |
| `engine_services/desktop_care_pet.py` 的 QQ 背景写入与 source ID 调用 | 按函数确认是 QQ 来源转换还是通用记忆写入。历史幂等标识保持一致；核心写入不得因搬文件而依赖 QQ 调度模块 |
| `durable_session_queue.py`、`session_inbox.py`、`turn_coordination.py` | 共享调度能力，保留原归属，QQ 只使用适配 |
| 模型/工具/子代理、MemCore、TTS、养成、账号关系、素材与生成文件服务 | 保留共享权威及真实调用入口，QQ README 仅说明输入、输出和定位链接 |

共享实现按已经存在的能力命名；必要的共享绘图或媒体叶子模块可以随切片归位，不扩展成全项目迁移。仅被多处调用不足以证明通用，仍需核对它是否含 QQ 业务规则。

## 6. 执行顺序

### A. 基线和最终迁移表

1. 查看工作区，核对本文与执行时源码的差异。
2. 对所有目标文件核对静态/延迟导入、测试补丁路径、`__file__` 资源、实例状态、正式导出及真实调用方。
3. 为混合文件列出函数/状态的具体去向，先解决共享边界与依赖关系。不能用批量替换文件名代替这项检查。
4. 跑首批相关基线，保存代表性上下文/回复的本地临时对照；不记录生产私人正文或凭据。

### B. 共享绘图边界与 QQ 图卡

先迁移绘图叶子和 QQ 专属图卡。通用图文工具继续支持桌宠和 QQ。修正资源定位，使同样的数据、字体和主题仍生成同样的图片；图卡生成不附带发送副作用。

### C. 协议、上下文与交付

归位 OneBot、连接档案、身份和偏好。迁移实际发送实现，拆清命令图卡发送与回复音频准备。不同来源使用现有交付能力，不新建一个强制所有消息经过的新任务系统。

同时核对 Host 装配、工具登记、TTS/素材绑定和真实对外调用。发送回执、重复过滤、Hook 和文件上传按原语义接回主链。

### D. 指令与内容准备

归位命令业务，接回交付入口。拆清引用/转发、附件准备、背景记录和事件分流。原有命令解析结果继续复用，保持直接回覆和继续模型回合的出口。

### E. 回合、队列与装配

归位 QQ 调度，保留普通聊天排队、主人干预、停止、插件租约和恢复路径。随后收敛 HTTP 路由中的装配到 `assembly.py`，核对所有组件使用同一 Bot 的正确实例。

此阶段不改变工具超时、并行、后台完成批次、MemCore 阈值或前缀冻结/压缩时机。需要更大调度改造时记录为独立事项。

### F. 局部地图与最终复核

补齐 QQ README、清理本轮范围内的过期名称和注释。仓库开发入口及总地图链接到 QQ README，详细实现表只维护这一份。检查旧路径的剩余引用并说明保留原因。

每个可独立验证的小切片按当前协作约定及时提交。内部导入同步迁移；已有明确外部消费者的接口单独核对，必要兼容有说明和单一实现，不给每个旧文件再套一个转发壳。部署、重启和推送按用户后续要求进行。

## 7. 行为与验证

整理成功的依据是：常见需求容易定位，职责与状态归属清楚，生产调用链保持有效。文件数、行数、目录层数不作为单独成功指标。

| 切片 | 优先复用的验证 |
| --- | --- |
| 图卡与共享绘图 | `test_qq_commands`、`test_qq_gateway`、`test_qq_care_cards`、`test_qq_commerce_cards`、`test_qq_cache_cards`、`test_text_cards`、`test_shop_artwork` 及现有卡片资源夹具 |
| 连接、协议与交付 | `test_onebot_transport`、`test_onebot_action_tool`、`test_qq_miniapp_share`、`test_qq_music_share`、`test_qq_delivery_identity_contract`、`test_qq_emotion_delivery`、`test_qq_voice_delivery`、`test_qq_stream_replay`、`test_qq_channel_profiles` |
| 指令与内容 | `test_qq_command_dispatch`、`test_qq_commands`、`test_qq_directed_dialogue`、`test_qq_rich_materials`、`test_qq_forward_source_binding`、`test_qq_group_notices`、`test_qq_poke_reactor` |
| 队列与恢复 | `test_qq_queue_processing_fence`、`test_qq_passive_memory_record`、`test_qq_passive_memory_recovery`、`test_qq_group_attention`、`test_qq_plugin_runtime_wiring`、`test_plugin_qq_inbound_turns`、`test_host_completion_batch`、`test_qq_multi_bot_dispatch` |
| 跨客户端与入口 | 现有主人身份/共享记忆、回复策略/语音恢复、TTS 选择、后端路由、三核心和工具注册检查；依据实际受影响调用方选取 |

执行前通过 `rg --files tests` 确认测试模块。已有结构测试更新为新实现的真实 owner 和导入路径，保留它们所验证的行为、隔离及单向依赖；不为迁移而删掉失败场景或放宽业务断言。新增测试只针对发现的实际行为缺口。

必须覆盖的场景：

- 私聊、显式 @、回复助手、未唤醒群消息、插件请求回合和未知消息类型。
- 图片来源、引用/转发内附件、下载/识别失败、尚未准备完成及恢复。
- 本地指令、继续聊天的动作、未知指令和前置停止。
- 普通轮忙时继续排队、工具轮主人追加、多 Bot/角色/会话隔离和已准入工单恢复。
- 流式中途回复、最终回复、重复片段、发送失败补交付、图卡文字回退、音频合成失败。
- 通用图文工具在桌宠/QQ 两端可用；生成与送达状态分开。

模型输入的渲染、工具声明、来源字段与缓存相关标识不因组织目录而改变。用代表性固定夹具检查迁移前后载荷与渲染一致；图卡比较图像或布局结果，音频合成比较服务输入和缓存身份，不为结构整理调用真实付费模型。

每个切片跑对应检查、必要 Python 编译与 `git diff --check`。纯后端整理无需前端 build。最终跑累计受影响的 QQ 与共享边界检查；已通过且未再变化的无关大型测试不重复运行。

本地延迟核对使用现有阶段计时和受控传输/模型替身，确认没有新增重复解析、全量扫描、同步附件等待或队列阻塞。模型供应商的响应时间和真实缓存率由后续实测观察，不用本地替身数字冒充端到端性能。生产 QQ 发消息、真实音频设备及外部服务未验证的部分明确记录。

## 8. QQ README 与命名要求

README 是 QQ 的当前使用地图，包含：

1. 一张 QQ 数据流图，包括指令、聊天、背景消息、插件和交付出口。
2. 每个实际文件的一两句职责说明；混合输入/输出的关键文件注明交接对象。
3. QQ 调用的共享能力表：能力、输入、输出、实现链接，不展开整个项目的所有内部文件。
4. 修改入口：唤醒、附件、转发、指令、图卡、语音、发送、排队、干预与恢复，各列最小阅读路径和验证。
5. 生效方式和排查依据：代码位置、运行版本、阶段耗时及失败状态；避免引用个人运行目录和凭据。

命名与注释以本轮实际修改范围为准：

- 模块名表达提供的能力；不把音频合成叫发送，不把协议分享叫绘图。
- 函数名尽量表达动作和对象。保留有真实消费者的公开签名，内部误导名称可更新全部调用方。
- 模块说明短而具体；复杂函数只补输入/输出、来源关系、状态生命周期或容易误解的边界。
- 注释解释原因，删除重复翻译代码的说明和旧机制描述；不把 README 逐行复制进源码。
- 不依靠大段注释解释反复转发、重复解析或隐式全局状态，先整理实际调用关系。

总地图保留按子系统定位的入口，QQ README 是这一部分的权威导航。后续其他模块可以沿用同样方法，各有自己的局部地图，通过共享能力边界相互连接。

## 9. 执行记录

方案准备阶段只核对了源码和现有文档。用户开启目标模式后，在隔离工作树 `codex/qq-module-organization` 开始实施；未调用真实模型、发送 QQ 消息或重启生产进程。

准备检查覆盖现有 54 个 QQ/OneBot 根模块及网关包文件（含包初始化文件），上述归位清单没有漏掉实现文件。执行单列出的测试模块已检查存在；这项文档检查不代表已执行运行时回归。

| 切片 | 状态 | 实际范围、验证与提交 |
| --- | --- | --- |
| A 基线与最终迁移表 | 已完成 | 未改代码的基线运行 460 项 QQ 检查，459 项通过。唯一失败为 `test_gateway_poke_path_uses_same_package_normalizer` 的补丁指向普通消息模块，实际通知解析已归 `qq_group_notices`；迁移时已更新真实 owner，保留调用次数与解析结果断言。隔离树的图卡/通用图文 39 项基线通过。全部目标文件及拆出的函数归属已复核。 |
| B 绘图与图卡 | 已提交 `aad7e048` | 共享绘图和文字排版进入 `rendering/`，四个 QQ 图卡模块进入 `channels/qq/cards/`；更新真实调用方和资源夹具。76 项图卡、图文工具及指令检查、19 项角色商店检查通过，Ruff 和 diff 检查通过。六个渲染模块的主体 AST（排除导入、文档与调整后的资源定位）与基线一致，布局、参数和缓存常量保持；主题路径仍锚定 `companion_v01/assets`。 |
| C 协议、上下文与交付 | 已提交 `14dc4257`、`b32af297`、`3ba3b37a` | QQ 实现与实际调用方使用唯一的 `channels/qq/` 路径；公开音频预检进入共享 `media_audio_urls.py`。路径切片的 22 个关键模块主体 AST 与原基线一致。附件防抖、交付上下文、一次性反馈和已准入工单恢复各归自己的 owner；各实例状态仍保持唯一。未保留旧目录转发壳。 |
| D 指令与内容准备 | 已提交 `b32af297`、`3ba3b37a` | 图卡指令只准备和绘制文档，图片发送、文字回退和真实回执进入 `delivery/card_results.py`。引用/转发查询、准备与投影归 `inbound/references.py`，附件标签、句柄和视觉说明归 `attachment_context.py`，发送者权限标签归 `identity.py`；原 `content.py` 退出。附件准备顺序保持，结果类型为 `QQPreparedContent`。表情包绑定指令归 `commands/emotions.py`；文字和时间显示归 `formatting.py`。 |
| E 调度与装配 | 已提交 `b32af297` | HTTP 路由只处理 HTTP，`assembly.py` 装配原队列、回合、插件和发送回调。共享队列、模型循环、工单和来源字段保持。QQ 背景来源 ID 归入 `inbound/record_ids.py`，共享入库复用它，不再从队列辅助文件取 ID。原路由私有函数的真实调用方和补丁位置直接迁移到 owner。 |
| F 局部地图与复核 | 文档已提交 `cdf7fc0f`，已合入主工作区 | QQ README 覆盖 60 个实际实现文件与包初始化职责，含数据流、共享服务、修改路径、验证与生效说明；全部本地链接通过。开发入口和项目总地图改为链接局部地图；历史拆分文档保留过程并指向现行地图。 |

扩大检查时另确认三项基线失败，均在主工作区的未修改源码上复现：`test_agents_records_package_reintegration_guardrail` 仍断言旧版 AGENTS 的固定文字；回复 Ledger 的结构测试仍只读取已拆分的网关文件；戳一戳 HTTP 测试仍要求不存在已长期提供的 actor 字段。后两项已更新真实 owner/实际身份断言，未改生产载荷。第一项与本轮 QQ 整理无关，保留并记录，不改用户协作规则以迎合测试。

最终代码复核：完成全部引用投影和音频准备边界后，460 项 QQ 检查全部通过（188.88 秒）。混合职责切片曾有 6 处插件测试仍替换旧 HTTP 别名，改为实际装配位置后，33 项插件/完成通知/模块检查通过；没有恢复无实际调用者的路由别名。最后的插件真实回合、语音恢复、桌面多 Bot 路由和生产函数/SDK 引用检查共 26 项通过。`prepare_qq_reply_audio()` 在回复准备层选择待说内容并调用合成；交付层只消费返回结果，双模式仍先发文字。

累计共享边界检查运行 393 项，390 项通过；3 处 TTS 检查在 Windows 清理临时环境中的已加载 `.pyd` 时失败。未整理 QQ 的主工作区运行同组 5 项，也有 4 项在同样的 DLL 清理阶段失败，没有业务断言失败，确认是已有环境问题。本轮不扩大为 TTS 运行环境重构。退出时的工单协程未等待警告已通过 tracemalloc 定位到 `test_qq_shell_permission` 的假监督器，在未修改 QQ 的主工作区也复现；补齐捕获协程的 cleanup 后定向 3 项通过，最终 QQ 整组退出无警告。

补充集成检查发现插件测试依赖个人 `.env` 提供角色名，隔离环境中签名目标为空；改为夹具显式绑定内置 Akane 后，保留原来的完整交付断言并通过。生产角色选择没有改变。前端生产函数检查复用主工作区已有 Node 依赖，未改前端源码。修改后的 Python 文件 Ruff 与编译检查通过，diff 检查仅有既有 CRLF 规范化提示。与本轮无关的旧 AGENTS 固定文字断言仍按基线记录保留。

输入与开销核对：私聊、群 @ 与媒体、群背景、转发/JSON 卡片、戳助手及群友互戳共 6 类完整回合输入/交付目标逐项一致；固定事件时间后最终源码再次对比通过。18 个首轮拆出函数/HTTP endpoint 与 11 个最后归位的投影函数保持原 AST。另对比 7 种完整文字/语音交付场景，合成输入、文字先行的顺序、真实成功/失败回执与原实现一致。受控 OneBot 替身、固定配置、预热后的 1,500 次私聊测量：上下文建立中位数 0.0665→0.0665 ms，载荷准备 0.0044→0.0045 ms；对应 P95 为 0.1071→0.1057 ms、0.0054→0.0054 ms。这只说明被测本地阶段没有新增开销，不代表整轮请求、真实网络或供应商缓存命中率。基线主工作区相对初始基准新增安装/CI 提交，没有改 QQ 实现；整合保留这些独立提交。

最终逐项审计：QQ 地图的 85 个本地链接全部有效，60 个实现文件均有定位说明；旧 QQ/OneBot 根文件及网关包实现均已退出，后端 AST 导入检查未发现旧路径引用，也没有 QQ 内部反向导入 HTTP 路由或装配模块。真实装配、图卡准备/发送、引用投影、语音准备和实例状态归属已结合源码复核，较大共享边界按下节明确保留，没有把共享引擎或存储搬进 QQ。

## 10. 后续共享边界

本轮 QQ 职责和真实调用方已归位。下列较大范围留作独立任务，在 QQ README 中同时给出当前入口，避免目录整理变成全域重构：

| 现状 | 实际归属与后续范围 |
| --- | --- |
| `engine_services/desktop_care_pet.py` 同时承接养成、跨客户端附件、消息来源/寻址与背景记录 | Store/MemCore 写入和压缩属于共享引擎；QQ 特有 ID 已独立复用。后续按材料接入、消息持久化、养成拆共享入口，不能整份移入 QQ。 |
| `attachment_ingest_runtime/qq_sources.py` 在共享材料服务中 | 它适配 QQ 的下载来源，但材料落盘、来源凭据和生命周期由该服务统一拥有；不为了文件名含 QQ 将下载实现从服务拆走。后续整理整个材料服务时一并核对。 |
| QQ 语音交付调用共享合成 | `turns/reply_audio.py` 是 QQ 声线和缓存适配，合成实现仍在共享 TTS。文字先交付再准备声音，不能为目录纯化而提前阻塞文字。 |

## 11. 主工作区整合与完成验收

2026-10-13，以合并提交 `0c2ee720` 将 `codex/qq-module-organization` 合入主工作区，保留截至 `317319e9` 的安装、CI 与 Linux 发布提交；无合并冲突。本轮 168 个改动路径在整合后与已验证的任务分支完全一致。再次扫描 Git 跟踪的 Python 文件，没有旧 QQ 模块的静态导入。

整合时正在编辑的 `.github/workflows/ci.yml`、`scripts/verify_akane.ps1` 和未跟踪的 `requirements-dev.txt` 完整保留，合并前后文件哈希一致，没有纳入本轮提交。主工作区的原始未跟踪执行单与现行计划范围相同，移至本地临时目录保留备份后，再接入已提交的执行记录。

主工作区追加运行 `tests.test_backend_route_modules`、`tests.test_qq_gateway_modules`，103 项全部通过。`scripts/smoke_core_backend.py` 的隔离实例启动、健康检查、两轮真实 HTTP 聊天链路、记忆连续性和本地命令执行均通过；模型传输只连接临时回环替身，未调用生产模型、发送真实 QQ 消息或启动桌面/TTS。`git diff --check` 通过，只有并行工作文件的既有 CRLF 规范化提示。

已完成的 QQ 与共享边界检查详见第 9 节；现有 TTS 临时 DLL 清理和旧 AGENTS 文字断言基线保留。真实 QQ/外部服务、音频设备及供应商缓存率未在本轮重测，不能据此宣称这些线上指标已通过。新源码需要后续重启后端才能由运行实例加载；本轮不推送、不重启。
