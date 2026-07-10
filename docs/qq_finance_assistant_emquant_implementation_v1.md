# Akane QQ 金融助手与 EmQuant 接入实施细案 V1

状态：设计锁定；F0-F7c 与 F9a 已完成 Fake Bridge/Mock 验收；QQ subscription/watchlist、默认关闭的事件 worker、AI 分析重试、逐项 QQ 投递账本、确定性 PNG 图表、MD/PDF/XLSX 金融报告和行情 Provider 解耦已接通；真实 Choice 冒烟仍等待账户权限
更新时间：2026-07-10
适用仓库：AkaneCompanionLab
外部依赖：memcore、Choice EmQuantAPI Python SDK 2.7.2.x、NapCat / OneBot
实施分支：feature/qq-finance-assistant-emquant（从包含现有 QQ、memcore 和 Anthropic 工具链的 feature/monogatari-web 分出）

## 0. 文档目的

这份文档用于在上下文压缩、换模型、换协作者或暂停开发后，恢复 Akane QQ 金融助手主线。

目标不是重写 Akane，也不是另做一个没有人格的财经机器人。目标是在现有 QQ、工具调用、memcore、文件生成和后台任务能力之上，增加一个可按会话开启的金融领域档案，使当前角色能够：

- 接收 Choice 新闻、公告、行情和宏观数据；
- 主动调用多种只读工具核验事实；
- 结合群聊人物归因、关注标的、历史观点和风险偏好分析；
- 在 QQ 中主动推送文字、真实数据图表和报告文件；
- 保持当前角色人格，但在金融事实、证据和时间戳上更严格；
- 失败时结构化降级，不编造数据、不假装发送、不执行交易。

这份文档优先记录不可轻易推翻的边界、真实代码接点、数据契约、切片顺序和验收口径。后续实现前先读本文件，不要凭对话摘要重新猜架构。

## 1. 上下文恢复顺序

上下文丢失后，按以下顺序阅读：

1. 根目录 AGENTS.md
2. 本文件
3. docs/qq_napcat_integration_v1.md
4. docs/qq_workshop_capabilities_v1.md
5. docs/memcore_integration_plan_v1.md
6. docs/tool_system_decoupling_v1.md
7. docs/file_processing_generated_artifacts_v1.md
8. sibling memcore 仓库的 AGENTS.md、README.md 和 docs/model_prompt_playbook_v1.md
9. Choice SDK 自带的 EMQuantAPI_Python.pdf、python3/EmQuantAPI.py 和 python3/demo.py

恢复后先执行：

~~~powershell
git status --short
rg -n "finance|market_event|emquant|actor_stable_id" companion_v01 tests docs config.py
~~~

不要覆盖用户已有改动，不要把外部 SDK、userInfo、登录日志、API 密钥或数据库加入仓库。

## 2. 已确认的现有基础

以下能力已经存在，后续应复用，不要平行重造。

### 2.1 QQ / NapCat

- companion_v01/qq_gateway.py 已实现 OneBot HTTP 私聊和群聊收发。
- companion_v01/routes/qq.py 已实现事件入口、流式回复、附件处理、文件投递和错误状态。
- QQ 群会话使用 qq_group_shared_<group_id> 作为 session/profile 身份。
- QQ 已支持会话级角色、模型和回复媒介覆盖，并可持久化部分 gateway 状态。
- qq_gateway.send_reply、send_image、send_file 已是真实投递路径。
- 后台任务完成后主动通知 QQ 的路径已经存在于 companion_v01/app.py。

### 2.2 模型与工具调用

- services/llm_client.py 支持 OpenAI compatible、Anthropic 和 Ollama 协议。
- companion_v01/llm_runtime.py 支持 Anthropic tool_use/tool_result 与流式工具调用。
- companion_v01/engine.py 已有同轮多工具循环、重复调用拦截和结构化工具结果回填。
- companion_v01/tool_orchestration_engine.py 和 tool_runtime.py 已有工具元数据、风险和轮次预算。
- web_search、retrieve_memory、read_memory_timeline 等只读能力已经支持原生工具通道。

### 2.3 memcore

- memcore 已是 Akane 的对话记忆主路。
- MemcoreManager 已接 user/assistant raw、metadata 回写、可见三层、retrieve_for_turn、read_timeline 和后台压缩。
- memcore 支持 Actor、record_tool_exchange、材料轨迹、时间线、metadata 前置过滤和跨会话检索。
- Akane → memcore 已把 QQ 群发送者和附件上传者映射为结构化 Actor，并在 metadata 回写时使用同一 Actor owner。

### 2.4 文件与产物

- GeneratedFileService 已支持 txt、md、docx、xlsx、pdf、json、csv、html，并可安全登记固定渲染器生成的 PNG 与金融报告。
- QQ 能投递生成文件和普通图片。
- F7a 已加入面向金融数据的确定性 PNG 图表工具；当前固定支持日线 K 线、成交量与 MA5/10/20/60，不接受模型绘图代码或任意价格数组。
- F7b 已加入 MD/PDF/XLSX 金融报告工具；事实数据由程序重新读取并计算，模型文字只进入明确标注的分析、风险和观察章节。
- 当前 ComfyUI 接线主要服务角色工坊，不作为本主线依赖。

### 2.5 已验证测试基线

设计阶段曾验证以下测试全部通过：

- tests.test_memcore_integration：35
- tests.test_qq_gateway：68
- tests.test_llm_client：43
- tests.test_generated_files：50

实现期间仍应重新运行，不能把历史通过当作当前通过。

## 3. 锁定设计决定

以下决定在 V1 中视为架构约束。

### 3.1 金融模式不是新的客户端模式

不要新增 qq_finance ClientMode。

ClientMode 表示投递和渲染能力，金融是业务领域。QQ 仍使用：

~~~text
client_mode = qq_text
domain_profile = finance
finance_mode = off | qa | push
~~~

- off：普通 Akane，不加载金融提示词和金融工具。
- qa：只有用户主动询问时启用金融问答能力。
- push：包含 qa，并允许订阅事件主动触发分析与 QQ 推送。

### 3.2 人格保留，事实优先

金融模式不替换当前角色包，不创建独立的无人格分析机器人。

角色身份、称呼和表达风格继续来自当前 persona/character pack。金融领域提示词只增加：

- 工具使用纪律；
- 证据和时间戳纪律；
- 分析结构；
- 风险与不确定性表达；
- 产物选择策略。

人格不能替代行情、新闻、公告或历史证据。

### 3.3 Choice SDK 作为首个正式数据适配器

Choice EmQuantAPI 审批通过后，可以作为新闻、行情、历史序列、板块和宏观数据的优先主通道，但金融上层能力不能依赖 Choice 专有对象。Engine 只选择统一 `MarketDataProvider`，更换行情源不应改动 QQ、事件编排、图表、报告或 memcore 主链。

公开网页搜索继续保留，用于：

- 补充资讯正文；
- 交叉核验；
- 查询 Choice 未覆盖的公开来源；
- 在 Choice 权限或流量不可用时结构化降级。

### 3.4 Choice SDK 独立进程运行

不要把 ctypes DLL 和长连接订阅直接加载进 FastAPI 主进程。

新增独立 EmQuant Bridge 进程，负责：

- SDK 注册路径与 DLL 加载；
- 登录、心跳、断线状态；
- cfn/cnq/csq/csqsnapshot 等只读调用；
- 回调快速入队；
- 订阅恢复；
- 本地只读 IPC/HTTP 接口；
- 健康与流量状态。

Akane 主进程只消费标准化事件和只读结果。Bridge 崩溃或 SDK 断线不能拖垮聊天主路。

### 3.5 不把全量市场流灌进 memcore

数据职责固定为：

- MarketEventStore：原始新闻、公告、行情事件和投递状态的真相源。
- memcore：群聊人物、偏好、关注标的、历史观点、已推送事件证据和重要分析结论。
- GeneratedFileStore：图表、日报、PDF、Excel 等产物。

只对真正分析或推送的事件调用 record_tool_exchange 留证；不把每一条市场新闻当用户消息写入 raw。

### 3.6 图表确定性生成

K 线、收益曲线、成交量、资金变化和指标图必须从真实数据确定性渲染。

云端生图只用于：

- 日报封面；
- 装饰性信息图；
- 非数据承载的视觉包装。

禁止让图片生成模型虚构 K 线或数值图。

### 3.7 多轮工具自适应放宽，但保留保险丝

不把金融分析硬限制为三轮。

建议预算：

- 普通问答：6 轮；
- 金融研究：10 至 12 轮；
- 深度报告：最多 16 轮；
- 全局绝对上限：16 轮。

同时保留：

- 完全相同工具签名禁止重复；
- 连续两轮无新增证据时停止；
- 连续失败或空结果时收束；
- 单轮和整轮墙钟超时；
- 工具返回体积限制；
- 文件/图片必须真实生成后才能声称完成。

### 3.8 Choice 只暴露只读能力

允许：

- cfn、cnq、cfnquery
- csq、csqcancel、csqsnapshot
- csc、cmc、csd、css
- edb、edbquery
- sector、tradedates、getdate、tradedatesnum
- ctr、cfc、cec、cps、datastatistics

V1 禁止：

- pcreate
- porder
- pctransfer
- pdelete

不要向模型暴露通用的 call_emquant(function_name, arguments)。

### 3.9 V1 不执行真实交易

金融助手只提供数据查询、分析、提醒、图表和报告。

不接券商交易，不自动下单，不根据模型结论执行资金动作。未来若讨论交易执行，必须另立设计、授权、风控和确认边界。

### 3.10 保持可复用边界，但不提前抽包

这条主线不仅服务当前 Akane，也应为以后自己或其他宿主复用保留清晰边界。

以下模块必须保持人格无关、QQ 无关：

- MarketEvent、MarketQuoteSnapshot、MarketSeries 等数据契约；
- MarketDataProvider 接口；
- EmQuant Bridge；
- MarketEventStore；
- 去重、聚类、指标计算和图表数据准备；
- 只读金融工具的结构化返回契约。

以下能力属于 Akane 宿主：

- 当前角色 persona；
- 金融领域提示词的角色表达；
- QQ 命令、群权限和投递；
- memcore namespace/Actor 映射；
- Akane GeneratedFileStore 与任务工作区接线。

V1 先在 Akane 仓库内以独立目录实现并验证。只有出现第二个真实宿主，或抽包能删除 Akane 中一条重复权威实现时，再评估提取独立 package。不要仅为了“以后可能复用”提前制造双实现和版本同步负担。

### 3.11 金融对象分类与代码权威来源

模型、工具和存储层必须区分三类对象：

~~~text
可交易工具：
股票、债券、基金、ETF、REITs、期货、期权、外汇、商品等

市场指标：
指数、利率、汇率、波动率、信用利差等

分类与关系：
行业、板块、概念、主题、产业链、指数成分、ETF 跟踪关系等
~~~

固定语义：

- ETF 是基金的一种，同时具有交易所交易、跟踪标的、净值和溢折价等额外属性；分析 ETF 不能只看涨跌幅；
- 指数、利率、行业和板块本身通常不是与股票并列的可交易资产；应区分指标、分类以及对应的 ETF、期货或期权；
- 证券代码、交易所后缀、北交所或其他市场映射必须来自 provider 返回或证券主数据，不允许模型自行把数字代码拼成 `.SH / .SZ / .BJ`；
- Choice 接线以实际返回的东财代码和证券主数据为权威，用户别名、公司简称和自然语言实体只用于查找，不直接作为最终查询代码。

### 3.12 预期差与影响路径

事件分析不能停在“利好/利空”。至少连续判断：

~~~text
改变了什么？
改变了多少？
影响谁？
多久体现？
市场原本预期什么？
价格已经反映了多少？
还需要哪些证据验证？
~~~

预期差必须有证据来源，例如：

- 公司正式指引；
- 有时间戳和来源的一致预期；
- 政策草案、正式文件和此前公开口径的差异；
- 可比历史事件或可复现的程序基准。

没有预期数据时，模型必须写“当前无法可靠判断是否超预期”，不能从新闻语气或当日涨跌反推所谓市场一致预期。

影响路径使用以下工程解释框架：

1. 现金流：销量、价格、成本、税率、份额、资本开支等改变收入、利润和自由现金流；
2. 无风险利率与期限：利率和期限结构改变估值折现；
3. 风险溢价：监管、信用、流动性和不确定性改变投资者要求的风险补偿；
4. 交易结构：指数调仓、ETF 申赎、空头回补、流动性冲击、热点轮动和情绪扩散。

这四条路径是可重叠的解释框架，不是互斥分类。风险溢价本身也是折现率的一部分，同一事件可能同时影响现金流、折现和短期交易结构。

### 3.13 数量指标与表述纪律

程序侧负责可复现计算，模型侧负责解释：

- 涨跌额、涨跌幅、区间收益、同比/环比；
- 相对基准的超额收益；
- 相对成交量、换手率和成交额变化；
- 估值分位、同行比较和历史比较；
- 波动率、回撤、均线、突破和量价关系。

“相对成交量”必须写明公式、窗口和盘中时间对齐方式，例如：

~~~text
relative_volume = 当前累计成交量 / 近 20 个交易日同一时刻累计成交量均值
~~~

不要把任意相对成交量都简称为“量比”。若使用行情软件约定的“量比”，必须同时声明数据源定义和比较窗口。

成交量与资金流表述：

- 放量只说明参与度、换手或分歧发生变化，不能单独证明趋势更可靠；
- 必须结合价格位置、历史基准、换手率、持续时间和后续价格验证；
- “主力净流入”通常是数据源基于订单大小或主动买卖方向的统计口径，不代表系统看到了真实机构账户；
- 默认表述为“按该数据源的大单资金统计口径出现净流入”，除非另有龙虎榜、席位、基金持仓或正式披露支持。

估值表述：

- PE 必须区分静态、TTM 和预测口径；亏损或一次性收益显著时不能直接解释为便宜或昂贵；
- 周期股低 PE 可能对应盈利高点，低 PE 不自动等于低估；
- PB 对金融和重资产行业通常更有解释力，对轻资产、品牌和研发型企业需要谨慎；
- 估值结论至少结合自身历史、同行、盈利周期、利润质量和数据时间。

## 4. 目标架构

~~~text
Choice EmQuantAPI
  ├─ cfn / cnq 新闻公告
  ├─ csq / csqsnapshot 实时行情
  ├─ csc / cmc / csd 历史序列
  └─ css / edb / sector 基本面与宏观
          │
          ▼
EmQuant Bridge 独立进程
  ├─ 登录与权限
  ├─ 回调队列
  ├─ 断线/订阅状态
  ├─ 只读本地 API
  └─ 数据标准化
          │
          ▼
MarketEventStore
  ├─ 去重
  ├─ 聚类
  ├─ 订阅匹配
  ├─ 投递幂等
  └─ 事件留存
          │
          ▼
FinanceEventOrchestrator
  ├─ 重要性规则
  ├─ 成本与频率控制
  ├─ Akane transient market_event 回合
  ├─ Sonnet 多工具分析
  └─ 结果与证据记录
       │             │
       │             ├─ memcore：Actor、偏好、观点、证据与结论
       │             ├─ web_search：正文补充与交叉核验
       │             └─ GeneratedFileStore：图表与报告
       ▼
NapCat / OneBot
  ├─ QQ 文字
  ├─ PNG 图表
  └─ PDF / MD / XLSX 报告
~~~

## 5. 金融领域档案

### 5.1 建议数据结构

新增不可变领域档案：

~~~python
@dataclass(frozen=True)
class DomainProfile:
    id: str
    enabled: bool
    prompt_block_ids: tuple[str, ...]
    allowed_tool_names: tuple[str, ...]
    hidden_tool_names: tuple[str, ...]
    default_tool_round_budget: int
    hard_tool_round_limit: int
    proactive_delivery_enabled: bool
~~~

V1 至少提供：

~~~text
default
finance_v1
~~~

不要把 domain_profile 塞进 ClientCapability 枚举。ClientCapability 是端能力，金融是领域能力。

### 5.2 QQ 会话状态

在 NapCatQQGateway 持久化状态中增加：

~~~json
{
  "finance_mode_overrides": {
    "qq_group_shared_123": "push",
    "qq_pri_456": "qa"
  }
}
~~~

建议命令：

~~~text
开启金融模式
关闭金融模式
开启财经推送
关闭财经推送
当前金融模式
关注 600519.SH
取消关注 600519.SH
关注列表
~~~

权限建议：

- 私聊：当前用户可切自己的 qa；push 是否开放由产品配置控制。
- 群聊：仅 MASTER_QQ、群主、管理员或配置白名单可切 push。
- 普通群成员不能静默开启全群主动推送。

### 5.3 Payload

QQ turn payload 增加：

~~~json
{
  "domain_profile": "finance_v1",
  "finance_mode": "qa",
  "actor_stable_id": "qq:123456",
  "actor_display_name": "当前群昵称",
  "actor_platform": "qq"
}
~~~

finance_mode=off 时可以省略 domain_profile。

### 5.4 Prompt 组合

在 PromptModule 中新增 DOMAIN_PROFILE，或在现有稳定 system_extra_blocks 中加入领域块。

推荐顺序：

1. 固定输出协议；
2. 固定当前人格和安全边界；
3. 固定金融领域规则；
4. 固定金融工具说明；
5. 动态当前时间；
6. 动态 memcore 可见记忆；
7. 动态市场事件、工具结果和用户消息。

金融模式稳定后，system prompt 字节内容不要每轮随机变化，以保留前缀缓存。

### 5.5 金融提示词必须包含

- 你仍是当前角色，不要自称另一个金融机器人。
- 涉及当前、最新、实时、价格、涨跌、公告或宏观数据时主动使用工具。
- 一次结果不足时允许继续查询，直到证据足够或确认不可用。
- 明确区分来源事实、程序计算和分析推断。
- 实时数据必须写明 as_of 时间与时区。
- 新闻标题不足以支持深度结论时，应提取正文或降低置信度。
- 市场同时上涨或下跌不能自动证明新闻导致了行情；没有事件研究或更多证据时使用“市场可能将其解读为”。
- 判断超预期或低于预期时必须引用预期来源、口径和时间；没有预期证据时明确标记未知。
- 放量、资金流和低估值只能按数据口径解释，不能直接写成“主力进场”“趋势确认”或“明显低估”。
- 不能把过去观点当成当前事实；应说明新证据强化、削弱还是未改变旧判断。
- 不保证收益，不编造价格、公告、财务数据或来源。
- 用户未要求长文时优先短而有信息密度的 QQ 回复。
- 只有图表确实提升理解时才生成图表。
- 每条普通新闻不自动生成文件；日报、重大事件和对比分析可生成。

### 5.6 金融模式工具裁剪

默认显示：

- retrieve_memory
- read_memory_timeline
- web_search
- market_news_search
- market_quote_snapshot
- market_price_series
- market_macro_series
- render_market_chart
- compose_finance_report
- send_file

按条件显示：

- 附件存在时显示附件和文档读取工具；
- 已有生成物时显示生成文件管理工具；
- 用户要求长任务时显示 delegate_task 和 task workspace；
- 用户要求提醒时显示 reminder 工具。

在金融模式下隐藏与当前请求无关的世界、礼物、媒体加工和桌宠演出工具提示。人格模块仍保留。

## 6. QQ Actor 结构化接线

### 6.1 当前问题

QQMessageContext.to_turn_payload 当前主要把群发送者写成：

~~~text
【发送者昵称】正文
~~~

这对模型当轮理解有帮助，但 memcore raw 没有 Actor 结构，长期摘要和归因仍可能串人。

### 6.2 目标映射

~~~python
Actor(
    stable_id=f"qq:{user_id}",
    display_name=sender_label,
)
~~~

稳定 ID 必须来自 QQ 用户 ID，不能使用昵称。

### 6.3 修改接点

companion_v01/qq_gateway.py

- QQMessageContext.to_turn_payload 增加 actor_stable_id、actor_display_name、actor_platform。
- to_delivery_context 保留同样字段，供后台任务与主动推送恢复上下文。
- context_from_delivery_context 恢复 Actor 字段。

companion_v01/engine.py

- process_turn / process_turn_stream 解析 turn_actor。
- 调用 _record_memcore_user_turn 时传 Actor。
- record_passive_qq_message 接受 Actor。

companion_v01/memcore_integration/manager.py

- record_user_turn 增加 actor 参数或 actor_stable_id/display_name 参数。
- _record_turn 的 user 分支调用 system.record_user_turn(..., actor=Actor(...))。
- assistant 不传 Actor。
- record_material_reference 从附件 detail 中提取 QQ sender id/name，并传 Actor。

### 6.4 被动群消息策略

V1 不应默认把全部水群写入可检索长期记忆。

金融 push 群可配置：

~~~text
QQ_FINANCE_PASSIVE_MEMORY_MODE=off|selected|all
~~~

- off：保持当前行为。
- selected：只记录含证券代码、关注标的、持仓、风险偏好、明确观点、任务或承诺的消息。
- all：全部写 raw，但低重要度，并继续由 memcore 压缩。

默认 selected。

### 6.5 Actor 验收

- 两名不同 QQ 用户使用相同昵称，仍按 stable_id 区分。
- 同一用户改昵称后，历史归因仍属于同一 stable_id。
- 谁说了某个观点、谁关注某只股票可由 timeline/retrieve 找到明确证据。
- QQ 图片和文件保留上传者 Actor。
- 不把群成员观点归成主人私聊观点。

## 7. memcore 金融配置

### 7.1 Categories 使用全局稳定超集

MemoryConfig.categories 在 MemcoreManager 级别构造，不应每次切 finance_mode 都重建一套不兼容枚举。

建议在所有模式使用稳定超集：

~~~text
casual
preference
personal_profile
plan_goal
project_work
relationship
emotion_state
life_event
memory_query
system_meta
finance_question
watchlist
portfolio_context
risk_preference
investment_goal
market_thesis
alert_preference
market_event
market_analysis
tool_trace
material_trace
~~~

关闭金融模式时，模型不会被提示使用金融 category，但旧金融记忆仍合法存在。

### 7.2 domain_id

V1 不把 finance_mode 拼进 memcore domain_id。

继续使用 character_pack_id 作为 domain_id，原因：

- 金融模式切换不应让同一角色突然失忆；
- QQ 群本身已用 profile/session 隔离；
- categories 足以区分金融记忆。

若未来合规要求金融与陪伴记忆硬隔离，必须另立迁移方案，不能直接改 key 导致历史不可见。

### 7.3 外部事件记忆

外部新闻不是用户消息。

推荐：

~~~python
mem.record_tool_exchange(
    tool_name="market_feed",
    tool_call_id=event.event_id,
    tool_input={"subscription_id": event.subscription_id},
    result=event.evidence_payload(),
    timestamp=event.published_at,
    keywords=[event.code, event.content_type, event.source],
)

mem.record_assistant_turn(
    analysis_text,
    timestamp=analysis_ts,
    memory_metadata={
        "categories": ["market_analysis"],
        "keywords": [...],
        "subject_scopes": ["assistant"],
        "importance": ...,
        "confidence": ...,
    },
)
~~~

Akane 当前 transient turn 不会自动双写 assistant 到 memcore，因此 FinanceEventOrchestrator 必须显式记录推送分析。

不要修改 memcore 私有表；通过 MemorySystem 公共 API 或 MemcoreManager 门面调用。

## 8. EmQuant Bridge

### 8.1 外部 SDK 管理

SDK 放在仓库外，通过环境变量定位：

~~~text
EMQUANT_API_ROOT=
EMQUANT_ENABLED=false
~~~

不要把以下内容提交：

- DLL / so / dylib；
- userInfo；
- logininfo.log；
- ServerSelect.txt；
- 账号密码；
- 激活日志；
- Choice 数据缓存样本中的受限原文。

建议在 Akane 专用虚拟环境里运行官方 installEmQuantAPI.py，不要污染系统 Python。

### 8.2 进程边界

建议新增：

~~~text
services/market_data/
  __init__.py
  types.py
  provider.py
  emquant_bridge_client.py

services/emquant_bridge/
  __init__.py
  types.py
  error_codes.py
  main.py
  runtime.py
  sdk_loader.py
  fake_sdk.py
  normalizers.py
  subscription_manager.py
  local_api.py
~~~

Bridge 可先使用 loopback HTTP；若后续需要更低延迟，再考虑本地 socket。V1 不需要消息队列中间件。

### 8.3 生命周期

启动：

1. 校验 EMQUANT_API_ROOT；
2. 校验 SDK 版本、Python 位数和 DLL；
3. 调用 c.start；
4. 查询 datastatistics；
5. 恢复已启用订阅；
6. 暴露 ready 状态。

登录参数建议：

~~~text
ForceLogin=0
RecordLoginInfo=0
HTTPTimeout=15
~~~

不要默认 ForceLogin=1，避免踢掉 Choice 终端或其他 API 会话。

运行：

- cnq/csq 回调只做有界数据拷贝、轻量标准化和 `put_nowait` 入队；
- 不在 native callback 线程中调用 LLM、SQLite 长事务或 QQ HTTP；
- 回调队列满时不阻塞 native 线程，记录 dropped_callback_count 并把 health 降级；
- worker 消费队列并写 MarketEventStore；
- Bridge 维护每个 SerialID 的类型、参数、状态和最近事件时间。

关闭：

1. 停止接收新订阅；
2. 调 cnqcancel / csqcancel；
3. 刷新待写事件；
4. 调 c.stop；
5. 返回结构化关闭状态。

当前 F4 在 FastAPI lifespan 关闭时调用 runtime.stop；队列中的事件不会被静默删除，但真正的 worker 消费与落库属于 F6/F9。

### 8.4 错误状态

必须显式处理：

- 10001003：无 API 权限；
- 10001012：权限不足；
- 10001024：资讯订阅登录失败；
- 10001025：资讯流量验证失败；
- 10002013：资讯重连；
- 10002014：资讯连续重连失败；
- 10000016：请求频次过高；
- 10003013：订阅数或股票数达到上限；
- 10003015：订阅指标达到上限；
- 10003024：资讯数据量过大。

Bridge health 至少返回：

~~~json
{
  "ok": true,
  "status": "ready|degraded|disconnected|permission_denied",
  "logged_in": true,
  "news_subscription_count": 1,
  "quote_subscription_count": 2,
  "last_news_at": 0,
  "last_quote_at": 0,
  "last_error_code": 0,
  "last_error_reason": "",
  "quota_status": {},
  "capabilities": {},
  "queue_size": 0,
  "dropped_callback_count": 0
}
~~~

本地 API 固定为 loopback-only：CLI 强制绑定 `127.0.0.1 / localhost / ::1`，HTTP middleware 也拒绝非 loopback 客户端。只暴露 lifecycle、health、quota、cfn、csqsnapshot、订阅管理和事件出队，没有通用 SDK function endpoint。

### 8.5 审批等待期间

先实现 MockMarketDataProvider，并用与 Choice 输出字段一致的 fixture：

- datetime
- eitime
- code
- content
- title
- infoCode
- medianname
- url
- type
- label

所有上层逻辑必须在无真实 Choice 权限时可测试。

### 8.6 函数存在、账户权限与运行可用性分开

当前下载的 Python SDK 2.7.2.0 源码和 demo 均包含：

- `csq`；
- `csqsnapshot`；
- `cfn`；
- `cnq`。

因此 F4 不把 `csq` 视为不存在或已废弃，但也不能因为 Python 类上存在方法就声称实时行情已可用。Bridge 必须分别记录：

~~~text
present：当前 SDK 是否暴露函数
authorized：当前账户是否有权限
quota_available：当前流量或订阅额度是否可用
operational：最小调用或订阅是否真实成功
last_checked_at：最近检测时间
reason：结构化失败原因
~~~

启动时先做无副作用的函数存在性检测；权限开通后再按第 20 节运行最小冒烟。`sector` 历史成分、资讯类型、行情字段和回调能力同样以当前 SDK、账户套餐和真实返回为准，不把手册中的版本性限制永久硬编码为业务事实。

## 9. 标准数据契约

### 9.1 MarketEvent

~~~python
@dataclass(frozen=True)
class MarketEvent:
    provider: str
    event_id: str
    published_at: int
    produced_at: int | None
    received_at: int
    code: str
    content_type: str
    title: str
    source: str
    url: str
    sentiment: str
    labels: tuple[str, ...]
    sector_code: str
    raw_hash: str
~~~

event_id 优先使用：

~~~text
choice:<infoCode>
~~~

infoCode 缺失时：

~~~text
choice:sha256(code|content_type|title|published_at|source)
~~~

### 9.2 MarketQuoteSnapshot

~~~python
@dataclass(frozen=True)
class MarketQuoteSnapshot:
    provider: str
    code: str
    as_of: int
    timezone: str
    previous_close: float | None
    open: float | None
    high: float | None
    low: float | None
    last: float | None
    volume: float | None
    amount: float | None
    change: float | None
    change_pct: float | None
    status: str
~~~

change 和 change_pct 优先由程序根据 last/previous_close 计算，保留原始字段用于校验。

### 9.3 MarketSeries

~~~python
@dataclass(frozen=True)
class MarketSeries:
    provider: str
    code: str
    interval: str
    adjusted: str
    timezone: str
    points: tuple[MarketBar, ...]
    as_of: int
~~~

### 9.4 工具返回通用字段

所有金融只读工具返回：

~~~json
{
  "ok": true,
  "status": "ok|empty|unavailable|permission_denied|rate_limited|invalid_arguments",
  "provider": "choice_emquant",
  "as_of": "2026-07-10T14:32:00+08:00",
  "source": "Choice",
  "reason": "",
  "data": {}
}
~~~

非法参数不能退化为宽泛查询。

### 9.5 后续标准证据对象

F5 前补齐或在 provider 返回中等价表达以下对象。

证券主数据：

~~~text
provider
code
canonical_id
name
asset_class
asset_subtype
exchange
currency
benchmark_code
underlying_or_index_code
as_of
~~~

预期证据：

~~~text
metric
actual_value
expected_value
expectation_source
expectation_as_of
comparison_basis
surprise_value
surprise_pct
confidence
~~~

派生指标：

~~~text
metric_name
value
unit
formula_id
window
benchmark_code
session_alignment
source_series_ids
as_of
~~~

这些对象的目标是让“超预期”“相对放量”“估值偏低”和“跑赢行业”等结论可复现、可审计。没有对应证据对象时，模型只能提出假设或待验证项。

## 10. MarketEventStore

建议新增独立 SQLite：

~~~text
market_events.sqlite3
~~~

不要把表塞进 memcore SQLite。

### 10.1 market_events

~~~text
event_id TEXT PRIMARY KEY
provider TEXT NOT NULL
published_at INTEGER NOT NULL
produced_at INTEGER
received_at INTEGER NOT NULL
code TEXT NOT NULL
content_type TEXT NOT NULL
title TEXT NOT NULL
source TEXT
url TEXT
sentiment TEXT
labels_json TEXT
sector_code TEXT
raw_hash TEXT NOT NULL
cluster_id TEXT NOT NULL
status TEXT NOT NULL
revision INTEGER NOT NULL
created_at INTEGER NOT NULL
updated_at INTEGER NOT NULL
~~~

索引：

- code, published_at
- content_type, published_at
- cluster_id
- raw_hash

### 10.2 finance_subscriptions

~~~text
subscription_id TEXT PRIMARY KEY
client TEXT NOT NULL
target_id TEXT NOT NULL
is_group INTEGER NOT NULL
session_id TEXT NOT NULL
profile_user_id TEXT NOT NULL
character_pack_id TEXT
finance_mode TEXT NOT NULL
enabled INTEGER NOT NULL
filters_json TEXT NOT NULL
delivery_policy_json TEXT NOT NULL
created_by_actor_id TEXT
created_at INTEGER NOT NULL
updated_at INTEGER NOT NULL
~~~

### 10.3 watchlist_items

~~~text
subscription_id TEXT NOT NULL
code TEXT NOT NULL
display_name TEXT
aliases_json TEXT
priority REAL NOT NULL
created_by_actor_id TEXT
created_at INTEGER NOT NULL
updated_at INTEGER NOT NULL
PRIMARY KEY(subscription_id, code)
~~~

### 10.4 market_event_deliveries

~~~text
event_id TEXT NOT NULL
subscription_id TEXT NOT NULL
status TEXT NOT NULL
analysis_id TEXT
attempt_count INTEGER NOT NULL
last_attempt_at INTEGER
delivered_at INTEGER
reason TEXT
created_at INTEGER NOT NULL
updated_at INTEGER NOT NULL
PRIMARY KEY(event_id, subscription_id)
~~~

这个表保证进程重启和回调重放时不重复推送。

投递状态固定为：

~~~text
pending → processing → delivered
                    ↘ failed → processing
pending / processing / failed → cancelled（订阅关闭）
~~~

`delivered` 和 `cancelled` 都不能被普通重放重新声明为待发送。`processing` 的超时租约回收属于 F9，F3 不假装已经实现跨进程 worker lease。

### 10.5 去重与聚类

第一层：event_id/infoCode 精确去重。
第二层：raw_hash 去重。
第三层：同代码、同类型、相近标题、短时间窗口聚类。

F3 的实际语义：

- 完全相同 event_id + raw_hash 返回 `duplicate_event_id`，不修改首次写入时间；
- 不同 event_id 但 raw_hash 相同返回 `duplicate_raw_hash`，并指向 canonical event；
- 相同 event_id 但 raw_hash 改变时更新原记录并递增 revision；
- 聚类只在代码、资讯类型和时间窗口一致时比较标准化标题，保守复用 cluster_id；
- 同一事件的后续版本是否构成“实质性更新”以及是否重置已投递状态，由 F6 的重要性策略决定，F3 不自动重复推送。

订阅过滤当前只允许：

~~~text
codes
content_types
sector_codes
labels_any
providers
~~~

未知过滤字段结构化拒绝。空 filters 且没有 watchlist 的订阅默认匹配零事件，不能退化为全市场广播。

## 11. 金融工具

### 11.1 market_news_search

用途：查询某标的、板块、类型或时间范围的历史事件。

输入：

~~~json
{
  "query": "英伟达 财报",
  "codes": ["NVDA.US"],
  "content_types": ["companynews", "report"],
  "date_from": "2026-07-01",
  "date_to": "2026-07-10",
  "limit": 20
}
~~~

先查 MarketEventStore，必要时由 Bridge 调 cfn 补历史。

### 11.2 market_quote_snapshot

用途：读取当前行情，不订阅长连接。

底层：csqsnapshot。

必须返回 as_of，不允许用缓存旧值假装实时。

### 11.3 market_price_series

用途：分钟线、日线、收益和量价分析。

底层：

- csc/cmc：分钟；
- csd：日/周/月序列。

程序侧可计算：

- 区间收益；
- 振幅；
- 均线；
- 成交量变化；
- 波动率；
- 突破/回撤；
- 相对指数表现。

计算结果和原始序列分字段返回，模型只负责解释。

### 11.4 market_macro_series

底层：edb/edbquery。

宏观数据必须保留发布日期，避免前视偏差。

### 11.5 render_market_chart

F7a 输入只接受显式可信证券代码和固定枚举参数：

~~~json
{
  "code": "600519.SH",
  "chart_type": "candlestick_volume",
  "interval": "1d",
  "adjusted": "forward",
  "lookback": 120,
  "moving_averages": [5, 20],
  "title": "贵州茅台日线量价",
  "send_to_user": true
}
~~~

工具内部通过 MarketDataToolService 重新读取受信 MarketSeries，不接受模型直接传价格数组、输出路径、绘图代码或任意样式。

LocalChartProvider 固定执行：

- 校验 code、interval、adjusted 与请求一致；
- 校验时间戳唯一且严格递增、OHLCV 为有限数、`low <= open/close <= high`；
- 程序计算均线，按固定 1280x720 模板绘制 K 线和成交量；
- 在 PNG 元数据和工具证据中写入 title、code、区间、provider/source、as_of、最新 OHLCV 与序列 SHA-256；
- 使用临时文件完成渲染后再原子替换目标，失败时不登记、不发送空文件。

输出写入 GeneratedFileStore，并返回 generated_id、mime_type、width、height、as_of。QQ 问答使用 `finance_tool_result` 授权，主动推送使用已再次校验的 `finance_subscription_push` 授权；普通对话的文件发送意图保护保持不变。

### 11.6 compose_finance_report

F7b 已实现专用的严格报告工具，复用 GeneratedFileService 的受管路径和 GeneratedFileStore 登记，不让模型直接提交事实数据或任意模板：

~~~json
{
  "report_type": "security_brief",
  "codes": ["600519.SH"],
  "output_format": "pdf",
  "interval": "1d",
  "adjusted": "forward",
  "lookback": 120,
  "chart_ids": ["gen_001"],
  "title": "贵州茅台证券简报",
  "analysis_summary": "明确标注为模型分析的解读",
  "risk_notes": ["待验证风险"],
  "watch_items": ["后续观察"],
  "send_to_user": true
}
~~~

支持：

- md：快速日报；
- pdf：正式简报并嵌入受信图表；
- xlsx：Summary、Metrics、Quotes、逐标的 OHLCV、Evidence、Notes 和 Charts 工作表。

工具会为每个代码重新读取可信日线序列和行情快照，程序计算收益、回撤、波动率、相对成交量和均线。任一请求代码缺少完整可信序列时不生成部分报告；行情快照不可用时可以在报告中结构化降级，但不得伪造。

`chart_ids` 只能精确引用当前会话由 `render_market_chart` 生成、仍存在于受管存储中的 PNG，并且 code、interval 和 adjusted 必须与报告一致。PDF/XLSX 嵌入图表，MD 使用受管相对引用；任意附件、普通生成图片、路径、模板和代码都不能进入嵌入链路。

模型提供的 `analysis_summary`、`risk_notes` 和 `watch_items` 只进入明确标注的模型分析、风险和观察章节。事实表、原始 OHLCV、provider/source、as_of、序列 SHA-256、报告 evidence SHA-256 与免责声明由程序固定生成。XLSX 会中和以 `= + - @` 开头的文本，避免公式注入；MD 会转义模型文本中的链接和图片语法。

输出写入 GeneratedFileStore，QQ 问答使用 `finance_tool_result` 授权发送文件，主动推送使用再次校验后的 `finance_subscription_push` 授权；普通对话的文件意图保护不变。HTML 暂不在 F7b 支持枚举中，避免出现未验收的第四套渲染口径。

## 12. 工具轮次与证据收敛

### 12.1 预算

在 ToolMetadata 增加 finance family：

~~~text
family=finance_read
operation=read
risk=low
default_round_budget=12
~~~

图表和报告：

~~~text
family=finance_artifact
operation=control
risk=low_or_medium
default_round_budget=12
~~~

### 12.2 No-progress guard

每轮记录 evidence fingerprint：

~~~text
tool_name
normalized_arguments
provider
as_of
result_hash
source_urls
event_ids
~~~

满足任一条件停止追加工具：

- 连续两轮 result_hash 无变化；
- 连续两轮没有新增 event_id/source_url/as_of；
- 连续两轮 empty/unavailable；
- 已达到用户问题所需证据最小集；
- 达到硬上限或墙钟超时。

### 12.3 最小证据集

实时价格问题：

- quote snapshot；
- as_of。

新闻影响问题：

- 新闻事件；
- 相关行情或明确说明行情不可用；
- 来源 URL/来源名；
- 必要时历史事件或公开正文。

趋势问题：

- 足够长度的价格序列；
- 计算指标；
- 数据截止时间；
- 不把新闻情绪直接等同趋势。

超预期问题：

- 实际值及口径；
- 预期值、来源和预期形成时间；
- 可比基准；
- 没有预期证据时返回 unknown，不退化为模型猜测。

放量或资金流问题：

- 原始成交量/成交额或资金流数据；
- 明确的数据源统计口径；
- 历史窗口和盘中时间对齐；
- 不把统计口径直接解释为真实机构账户行为。

估值问题：

- 指标口径，如 PE_TTM、PE_FORWARD 或 PB；
- 盈利是否为正、是否含一次性损益；
- 自身历史或同行比较；
- as_of 与财报期。

## 13. 主动事件分析

### 13.1 market_event 不是 user turn

扩展 transient turn 判断：

~~~text
turn_kind = market_event
transient_user_message = true
~~~

输入 prompt 中使用明确边界：

~~~text
【外部市场事件，不是用户发言】
事件 ID：
发布时间：
生产时间：
证券代码：
资讯类型：
标题：
来源：
URL：
舆情标签：
~~~

不得把外部事件 metadata 回写成某位群成员的偏好或陈述。

### 13.2 FinanceEventOrchestrator 流程

1. MarketEventStore 新事件入库；
2. 匹配 finance_subscriptions；
3. 创建 delivery pending；
4. 规则判断是否需要 AI；
5. 构造 market_event transient turn；
6. 模型按需调用行情、历史、记忆、网页和图表工具；
7. 解析最终输出；
8. 记录 market_feed tool trace；
9. 显式记录 assistant market_analysis 到 memcore；
10. 投递 QQ；
11. 更新 delivery 状态；
12. 失败按策略重试或进入 digest。

### 13.3 AI 调用前的确定性过滤

不要每条资讯都调用最贵模型。

先用规则：

- 是否命中 watchlist；
- 是否为 report/regularreport/tradeinfo；
- 是否包含重大、停牌、复牌、业绩、回购、增减持、处罚等关键词；
- 是否与短时间价格/成交量异常同时发生；
- 是否属于订阅板块；
- 是否已被同 cluster 推送。

规则不直接产出最终投资结论，只决定是否进入 AI 分析或摘要池。

## 14. QQ 主动投递

### 14.1 授权来源

主动推送依赖 finance_subscriptions 中的明确授权，不伪造“当前用户要求发送文件”的意图。

QQ_REQUIRE_FILE_DELIVERY_INTENT 仍保护普通对话。金融 push 使用独立 subscription delivery policy。

### 14.2 Delivery context

订阅保存：

- is_group
- target_id
- session_id
- profile_user_id
- character_pack_id
- finance_mode
- current actor/admin who enabled it

投递时通过结构化 context 调 qq_gateway.send_reply/send_image/send_file。

### 14.3 默认推送格式

~~~text
【市场快讯｜14:32】
已确认事实：
客观数据与时间：
分析推断：
尚待验证与风险：
与之前观点的关系：
接下来观察：
来源：
~~~

要求：

- QQ 首条尽量控制在可读长度；
- 长证据放后续气泡或报告；
- 图表只在明显有价值时附带；
- 标明数据时间；
- 不使用绝对化“必涨/必跌”；
- 重要推送必须区分事实、数据、推断和待验证项；普通短问答可以合并表达，不强制机械输出所有标题；
- 只观察到时间上的同时发生时，不把相关性写成确定因果；
- 同一事件不重复刷屏。

### 14.4 推送等级

- archive：只入库。
- digest：进入定时摘要。
- notify：发送文字。
- alert：文字 + 图表，必要时报告。

### 14.5 限流

建议每个群：

- 普通 notify 最小间隔可配置；
- 同代码短时间聚合；
- 每分钟和每日上限；
- 超限事件进入 digest；
- alert 可绕过普通间隔，但仍受硬上限。

## 15. 文件、图表与云端 Provider

### 15.1 不替换 GeneratedFileStore

新增 Provider 层，输出仍进入现有 GeneratedFileStore：

~~~python
class ArtifactProvider(Protocol):
    def generate(self, request: ArtifactRequest) -> ArtifactResult:
        ...
~~~

Provider 可包括：

- LocalDocumentProvider
- LocalChartProvider
- CloudDocumentProvider
- CloudImageGenerationProvider

### 15.2 Provider 路由

~~~text
金融数据图表 → LocalChartProvider 或可信图表 API
MD/CSV/XLSX → 现有本地实现
高保真 PDF/DOCX → 本地优先，按配置可切云端
装饰性封面/信息图 → 云端生图
~~~

### 15.3 云端边界

- API key 只在 provider 配置层使用；
- 不进入 prompt、日志、snapshot 或生成文件 metadata；
- 上传前移除本地绝对路径；
- 只上传任务必要内容；
- 云端失败时结构化回退本地或只发文字；
- 不把云端 URL 当成永久存储，下载后进入 GeneratedFileStore。

## 16. 配置建议

~~~text
# Finance domain
FINANCE_ASSISTANT_ENABLED=false
FINANCE_DEFAULT_MODE=off
FINANCE_TOOL_ROUND_BUDGET=12
FINANCE_TOOL_ROUND_HARD_LIMIT=16
FINANCE_ANALYSIS_MAX_ATTEMPTS=3
FINANCE_ANALYSIS_RETRY_BACKOFF_SECONDS=0.5
FINANCE_MARKET_PROVIDER=disabled
FINANCE_TURN_TIMEOUT_SECONDS=90
FINANCE_EVENT_DB_PATH=
FINANCE_PASSIVE_MEMORY_MODE=selected

# Choice bridge
EMQUANT_ENABLED=false
EMQUANT_API_ROOT=
EMQUANT_BRIDGE_URL=http://127.0.0.1:9910
EMQUANT_BRIDGE_HOST=127.0.0.1
EMQUANT_BRIDGE_PORT=9910
EMQUANT_SUBSCRIPTION_STATE_PATH=
EMQUANT_BRIDGE_TOKEN=
EMQUANT_LOGIN_FORCE=false
EMQUANT_RECORD_LOGIN_INFO=false
EMQUANT_HTTP_TIMEOUT_SECONDS=15
EMQUANT_CALLBACK_QUEUE_MAX=5000

# Akane 主进程事件 worker；四重开关与 subscription 全部满足时才消费
FINANCE_EVENT_INGESTION_ENABLED=false
FINANCE_EVENT_POLL_INTERVAL_SECONDS=2
FINANCE_EVENT_POLL_BATCH_SIZE=20
FINANCE_EVENT_RECOVERY_MAX_AGE_SECONDS=21600

# QQ finance
QQ_FINANCE_MODE_COMMANDS_ENABLED=true
QQ_FINANCE_PUSH_ENABLED=false
QQ_FINANCE_NOTIFY_MIN_INTERVAL_SECONDS=20
QQ_FINANCE_MAX_MESSAGES_PER_MINUTE=6
QQ_FINANCE_MAX_MESSAGES_PER_DAY=200
QQ_FINANCE_DEFAULT_DIGEST_TIME=15:10

# Artifact providers
FINANCE_CHART_PROVIDER=local
FINANCE_DOCUMENT_PROVIDER=local
FINANCE_IMAGE_PROVIDER=disabled
~~~

EMQUANT_BRIDGE_TOKEN 是本机进程间鉴权值，不得进入客户端 snapshot。

## 17. 建议代码地图

~~~text
companion_v01/
  domain_profiles.py
  finance/
    __init__.py
    types.py
    config.py
    event_store.py
    subscription_service.py
    event_orchestrator.py
    importance_policy.py
    prompt_block.py
    tool_handlers.py
    chart_service.py
    report_service.py
    memory_bridge.py

services/
  market_data/
    __init__.py
    provider.py
    types.py
    emquant_bridge_client.py
  emquant_bridge/
    __init__.py
    types.py
    error_codes.py
    main.py
    runtime.py
    sdk_loader.py
    fake_sdk.py
    normalizers.py
    subscription_manager.py
    local_api.py

tests/
  test_qq_actor_memcore.py
  test_finance_domain_profile.py
  test_finance_event_store.py
  test_finance_tools.py
  test_finance_event_orchestrator.py
  test_finance_qq_delivery.py
  test_emquant_bridge.py
  test_finance_artifacts.py
~~~

避免继续扩大 engine.py。engine.py 只增加薄的 service 装配和调用点。

## 18. 实施切片

### Slice F0：Actor repair

状态：已完成。

实际提交：

- memcore：ba4a67a feat: support actor-owned metadata updates
- Akane：94187b8 feat: preserve QQ actor attribution in memcore

目标：QQ 群 user 和 attachment 进入 memcore 时保留稳定 Actor。

改动：

- QQMessageContext payload/delivery context；
- engine turn_actor 解析；
- MemcoreManager user/material Actor 接线；
- 群聊归因提示与测试。

验收：

- 两人同昵称不串；
- 改名不丢历史；
- 当前 QQ 和 memcore timeline 中可见 id/name；
- 现有 QQ 和 memcore 测试保持通过。

### Slice F1：Finance domain profile

状态：已完成。

实际落地：

- `companion_v01/domain_profiles.py` 提供 `default / finance_v1` 不可变领域档案、稳定金融 prompt、工具 allow/hidden 集和预算元数据；
- QQ gateway 按会话持久化 `off / qa / push`，payload 与 delivery context 带 `finance_mode / domain_profile`；
- QQ 命令已接“开启/关闭金融模式、开启/关闭财经推送、当前金融模式”，群 push 仅主人、群主或管理员可开，且默认受 `QQ_FINANCE_PUSH_ENABLED=false` 保护；
- `PromptModule.DOMAIN_PROFILE` 与 `system_extra_blocks` 已接稳定金融规则，不替换当前 persona，也不新增 client mode；
- CapabilitySelection、native schema、legacy tool prompt、校验与执行共用同一 domain tool filter，金融模式隐藏表情包、礼物、世界、媒体加工、浏览器等无关工具；
- F1 只提供领域模式和问答工具裁剪，不假接市场行情、新闻订阅或主动事件投递；这些仍属于 F2-F6。

目标：按 QQ 会话开启 off/qa/push，加载金融提示词并裁剪工具。

改动：

- domain_profiles.py；
- gateway finance override 持久化；
- QQ 命令；
- PromptModule.DOMAIN_PROFILE；
- CapabilityRegistry 金融选择；
- config/settings catalog。

验收：

- off 模式 prompt 不包含金融块；
- qa/push 模式包含稳定金融块；
- QQ 仍使用 qq_text 输出协议；
- 当前角色 persona 不被替换；
- 无关工具不进入金融 prompt。

### Slice F2：Market provider contract + Mock

状态：已完成。

实际落地：

- `services/market_data/types.py` 提供不可变 `MarketEvent / MarketQuoteSnapshot / MarketBar / MarketSeries`、统一 `MarketDataResponse`、provider health 和结构化校验错误；
- `services/market_data/provider.py` 提供人格无关、QQ 无关的只读 `MarketDataProvider` 接口，以及严格的新闻、快照和序列查询参数契约；
- `services/market_data/normalizers.py` 标准化 Choice 资讯字段与行情字段，程序侧计算 `change / change_pct`，拒绝坏时间、坏代码、非有限数值和不可能的 OHLC；
- `MockMarketDataProvider` 只接受显式 `synthetic=true` 且 schema 匹配的离线 fixture，health 明确标记 `mock_choice / Synthetic Choice Fixture / network=disabled`，不会声称已登录 Choice；
- `tests/fixtures/choice_market_data_synthetic_v1.json` 只含虚构代码 `000000.TEST` 和 `example.invalid` 来源，不包含真实 Choice 受限数据；
- 当前未创建市场事件数据库、订阅、QQ 投递、运行时工具 handler 或 EmQuant SDK 加载路径，这些仍属于 F3-F6。

目标：无 Choice 权限也能开发完整上层。

改动：

- services/market_data types/provider；
- MockMarketDataProvider；
- Choice 字段 fixture；
- provider health contract。

验收：

- mock 新闻/行情可标准化；
- 无效字段结构化失败；
- 不依赖外部网络。

### Slice F3：MarketEventStore + subscriptions

状态：已完成。

实际落地：

- `services/market_data/store.py` 创建独立 `market_events.sqlite3`，不复用或修改 memcore SQLite；F5 code resolver 将 schema 从 v1 可迁移升级为 v2，F6a 再升级为 v3 并补充订阅 `is_group`，所有迁移保留原事件与投递数据；
- event upsert 支持 `inserted / updated / duplicate_event_id / duplicate_raw_hash`，保存 canonical event、cluster_id 和 revision；
- 聚类限定为相同代码、相同资讯类型、六小时窗口和保守标题相似度，不把不同标的或不同类型强行聚合；
- subscription owner 字段创建后不可改绑，filters 使用固定枚举，空订阅 fail closed；
- watchlist 使用 provider code，支持显示名、别名、优先级和创建者 Actor；
- delivery 以 `event_id + subscription_id` 隔离，保存 pending/processing/delivered/failed/cancelled、尝试次数和失败原因；
- 关闭订阅会取消尚未完成的 delivery，重启后 delivered 不会重新进入可投递状态；
- 当前未接 Choice、LLM、QQ、后台 worker 或运行时配置，F3 只是持久化真相源和状态机。

目标：事件真相源、去重、关注列表和投递幂等。

改动：

- SQLite schema；
- event upsert；
- cluster/dedupe；
- subscription/watchlist；
- delivery state。

验收：

- 同 infoCode 重放不重复；
- 不同群投递状态隔离；
- 进程重启后不重复推送；
- 关闭订阅后不再匹配。

### Slice F4：EmQuant Bridge

状态：已完成 Fake SDK 验收；真实 Choice 最小冒烟等待账户权限。

实际落地：

- `sdk_loader.py` 只从显式 `EMQUANT_API_ROOT` 动态定位 Python SDK，缺配置返回 missing_config，模块或生命周期函数无效返回 invalid_sdk；导入阶段不主动调用 SDK 或登录；
- `runtime.py` 使用固定 `ForceLogin=0,RecordLoginInfo=0,HTTPTimeout=15`，依次执行 start、datastatistics、订阅恢复，并在 stop 前取消 cnq/csq；
- capability health 分开记录 present、authorized、quota_available、operational、last_checked_at 和 reason；
- Choice 已知权限、流量、重连和断线错误码映射为 permission_denied、rate_limited、degraded、disconnected 或 unavailable；
- `subscription_manager.py` 只允许 news/quote 两种订阅，拒绝账户敏感 options，可选原子 JSON 持久化，取消失败保留 serial_id 并返回 cancel_failed；
- callback 只进行有界拷贝、Choice 字段轻量展开和非阻塞入队，不调用 LLM、QQ、memcore 或 MarketEventStore；
- `local_api.py / main.py` 提供 loopback-only health/start/stop/quota/news/query/quotes/snapshot/subscriptions/events API；F5 在同一只读边界内补充 prices/series，不增加通用 SDK 调用入口；可选 Bearer/header token 二次保护且响应不回显 token，没有通用 `call_emquant` 或交易函数入口；
- `fake_sdk.py` 覆盖同步查询、订阅、回调、权限错误、流量错误、取消和断线；测试未加载真实 DLL、未登录真实账号、未调用网络；
- F4 本身不把 Bridge 接成 Akane 模型工具；该接线已在 F5 完成。

目标：独立进程读 Choice SDK，主进程不加载 DLL。

改动：

- SDK loader；
- start/stop/health；
- cfn/cnq/csq/csqsnapshot；
- callback queue；
- cancel/resubscribe；
- datastatistics。
- capability probe：区分 present、authorized、quota_available 和 operational。

验收：

- 无 SDK 时 missing_config；
- 无权限时 permission_denied；
- callback 不执行 LLM/QQ；
- Bridge 失败不影响 Akane；
- 只读 allowlist；
- 状态修改函数不可调用。
- Python 方法存在但账户无权限时必须返回 permission_denied，不得标记 ready；
- `csq / csqsnapshot / cfn / cnq / sector` 的实际可用性有独立检测结果和检测时间。

真实权限未开通前只跑 fake SDK 测试。

### Slice F5：金融只读工具

状态：核心与证券主数据/别名权威解析已完成；真实 Choice 最小冒烟、market_macro_series、基准超额收益、估值/预期差属于后续增强，当前不伪装已具备。

实际落地：

- `services/market_data/emquant_bridge_client.py` 实现 loopback-only `EmQuantBridgeMarketDataProvider`；只接受 `http://127.0.0.1 / localhost / ::1`，拒绝凭据、路径、query、fragment 和外部主机；支持可选 Bridge token、超时、断线、权限、限流和坏 JSON 的结构化降级，绝不静默切到 Mock；
- Bridge 增加固定 `csd` 历史序列方法和 `/prices/series` API；`extract_choice_series_records()` 将官方 SDK 的 `Data[code][indicator][date]` 展平，Fake SDK 提供合成 OHLCV 序列且按日期范围过滤；
- `MarketDataToolService` 将资讯优先与 `MarketEventStore` 合并，显式 codes + content_types 才会向 Choice 发 cfn；缺少条件时只查本地库，不广播全市场；
- `market_resolve_security / market_news_search / market_quote_snapshot / market_price_series` 四个 handler 使用 `family=finance_read / operation=read / risk=low / default_round_budget=12`，同时进入 legacy JSON 与 provider native schema；`additionalProperties=false`，非法代码、日期、interval、adjusted 和未知参数 fail closed；
- 四个工具只在启用的 finance_v1 档案中动态暴露，默认陪伴模式不出现；主进程只访问 Bridge HTTP，不导入 Choice SDK/DLL；
- 行情快照由程序计算 change、change_pct、跳空、日内振幅和区间位置；历史序列由程序计算区间收益、最新收益、总振幅、年化波动率、最大回撤、MA5/10/20/60、20 周期突破/跌破和 relative volume；relative volume 返回公式、实际窗口和“仅完整周期、未做盘中时间对齐”的口径说明；
- 所有工具结果保留 provider、source、as_of、reason；新闻保留 event_id/source URL，原始序列与程序指标分字段返回，模型只负责解释；
- 金融档案初始建议预算真正接为 12，硬上限 16；保留完全相同调用签名拦截，并增加连续金融结果 hash 不变或连续空/不可用的 no-progress guard；
- 预算耗尽、重复调用、no-progress、权限失败或工具不可用后，系统不会静默结束：最终 prompt 明确要求模型停止工具调用，基于已有证据立即产出完整可交付回答，说明 as_of、证据缺口和置信度，并禁止只回复“还在处理/没完成/需要继续查询”等占位语；同步与流式路径使用同一收尾契约；
- 配置补充 `FINANCE_MARKET_PROVIDER / FINANCE_EVENT_DB_PATH / EMQUANT_BRIDGE_URL / EMQUANT_BRIDGE_TOKEN / EMQUANT_HTTP_TIMEOUT_SECONDS`；`FINANCE_MARKET_PROVIDER` 默认 `disabled`，示例环境默认关闭真实 EmQuant，不包含账号或密钥；
- MarketEventStore schema v2 增加 `market_securities / market_security_aliases`：主数据记录 provider、code、名称、别名、市场、证券类型、来源和 as_of；升级会保留 v1 事件、订阅和投递表；
- `market_resolve_security` 只查询可信证券主数据与当前 profile/session 的启用 watchlist。唯一精确别名才返回 `resolved=true`；部分匹配返回 `needs_confirmation`，同名多代码返回 `ambiguous`，均不会自动选择；watchlist 别名不会跨群或跨会话泄漏；
- resolver 成功后只在相同 profile/session 内保存十分钟短期代码凭证。新闻、快照和序列工具执行前会校验代码来源：允许用户原文直接给出的完整 code、当前会话 watchlist code，或本轮 resolver 精确解析过的 code；仅仅“这个 code 存在于全局主数据”仍不够，防止模型把用户名称错配到另一个真实证券；
- 当前仍不允许模型自行拼交易所后缀；解析不到或候选不唯一时必须澄清。仓库不提交任何真实证券主数据快照，真实主数据只能由授权 provider/受控导入流程写入。

目标：Sonnet 可主动查询新闻、行情、历史序列和宏观数据。

改动：

- tool handlers；
- native schema；
- NATIVE_TOOL_DECISION_ALLOWLIST；
- finance family budget；
- evidence/no-progress guard。
- 证券主数据与别名到 provider code 的权威映射；
- 程序侧 relative_volume、超额收益、估值比较和预期差计算。

验收：

- Anthropic 原生工具路径；
- 非法代码/日期不宽泛查询；
- 所有实时结果包含 as_of；
- 失败不编造；
- 深度问题允许超过三轮；
- 相同调用不会循环。
- 模型不能自行拼接交易所后缀；
- relative_volume 返回公式、窗口和盘中对齐方式；
- 超预期判断缺少 expectation evidence 时返回 unknown；
- 资金流结果保留数据源统计口径，不伪装成真实机构账户。

### Slice F6：Event → AI → QQ

状态：F6a/F6b 已完成 Fake Bridge 验收；事件 worker 默认关闭，未执行真实 Choice 登录或真实 QQ 推送。

实际落地：

- `FinanceEventImportancePolicy` 在调用昂贵模型前按资讯类型、重大/业绩/停复牌等关键词、可信标签和 watchlist priority 做确定性分级；`archive/digest` 不即时调用 AI，`notify/alert` 才进入主动分析；同一 subscription 已投递的 cluster 不再次推送；
- `FinanceAnalysisRequest` 构造 `turn_kind=market_event / client_turn_kind=proactive / transient_user_message=true / finance_mode=push / domain_profile=finance_v1`，并使用“外部市场事件，不是用户发言”的明确边界；事件完整 provider code、来源和发布时间进入临时 prompt，但不附带 Actor，不会伪装成群成员发言；
- `AkaneFinanceAnalysisClient` 复用现有最多 12/16 轮金融工具链。模型若没有按推送结构分层，程序会基于事件已知字段补齐“已确认事实 / 客观数据与时间 / 分析推断 / 尚待验证与风险 / 接下来观察 / 来源”，并拒绝把短小“处理中/未处理完”占位语当最终推送；
- memcore 门面新增公开 `record_tool_exchange(...)`，F6a 用确定性 source prefix 记录 `market_feed` 工具证据，并显式记录 assistant `market_analysis`；外部事件本身不走 `record_user_turn`；memcore 固定 category enum 增加金融类别后再写入，不修改 memcore 私有表；
- `FinanceEventOrchestrator` 实现 event upsert → subscription match → importance → authorization → delivery claim → AI → QQ → delivered/failed。QQ 或 AI 失败保留 failed，可由显式 `retry_pending()` 重试；delivered 重放不再次调用 AI/QQ；
- MarketEventStore schema v3 为 subscription 持久化 `is_group`，从 v2 升级时根据旧 QQ session id 回填；新增原子 `claim_delivery_attempt()`，同进程并发只有一个执行者能从 pending/failed 领取 processing；跨进程崩溃后的 processing 租约回收仍按原设计留到 F9；
- `QQFinanceDeliveryAdapter` 在调用 NapCat 前同时检查 `FINANCE_ASSISTANT_ENABLED / QQ_FINANCE_PUSH_ENABLED / QQ_BRIDGE_ENABLED`、subscription enabled、client=qq、finance_mode=push 和合法 target；默认配置下不会真实发送；
- `companion_v01.app` 只在现有 market store 与 QQ gateway 都已装配时创建 orchestrator 对象，不启动轮询、不消费 Bridge 队列，也不在应用启动时发送任何消息；
- Fake 端到端测试覆盖：外部事件不带 Actor、完整推送契约、同事件幂等、同 cluster 去重、开关关闭不花费 AI、QQ 失败重试、原子 claim、v2→v3 迁移以及群聊 delivery context。

F6b 实际落地：

- `FinanceSubscriptionService` 将群管理员/群主/主人或私聊用户的“开启财经推送”同步为 `finance_subscriptions`；切回 qa/off 时先禁用 subscription，再更新 QQ 会话模式，订阅落库失败则模式切换 fail closed；
- 新增“关注 证券代码或精确名称 / 取消关注 / 关注列表”固定命令。群聊增加和删除仍要求管理员权限，普通群成员可查看列表；名称只接受可信证券主数据的唯一 exact match，候选不唯一时要求完整 provider code，程序不会拼 `.SH/.SZ/.BJ`；
- watchlist 在关闭/重新开启推送后保留，subscription creator Actor 首次写入后不被后续管理员覆盖；空关注列表继续匹配零事件，不会退化成全市场广播；
- `EmQuantBridgeMarketDataProvider.poll_market_events()` 只访问 loopback Bridge 的固定 `/events`，只规范化成功的 news callback；quote/system/error callback 只计入 ignored，不冒充市场新闻；Bridge 失败不生成 Mock 事件；
- `FinanceEventWorker` 是串行 daemon worker，不占用普通聊天请求线程。没有 enabled push subscription 时不会 drain Bridge；拿到 callback batch 后先把整批事件写入 MarketEventStore，再逐条调用 orchestrator，降低中途退出导致整批丢失的风险；
- worker 首轮会在可配置时间窗内扫描已落库事件并恢复未完成分析，同时先处理 pending/failed delivery；同事件、同 cluster 和 delivered 状态仍由 F6a 幂等约束保护；
- 应用 startup/shutdown 只负责 start/stop worker。worker 的有效启动同时要求 `FINANCE_EVENT_INGESTION_ENABLED / FINANCE_ASSISTANT_ENABLED / QQ_FINANCE_PUSH_ENABLED / QQ_BRIDGE_ENABLED`，且默认第一个开关为 false；启动不会调用 Bridge `/start`，主进程仍不加载 Choice DLL；
- `/api/qq/napcat/status` 可附带 worker 运行状态；worker cycle 只记录结构化计数和安全原因，不记录 token、SDK 路径、原始正文或本地数据库绝对路径；
- Fake 测试覆盖管理员命令真实落 subscription、可信名称进 watchlist、未授权群成员修改被拒绝、无订阅不 drain、整批先落库再分析、近期事件恢复、worker start/stop、Bridge callback 规范化和 source failure fail closed。

目标：新事件触发人格化金融分析并主动投递。

改动：

- importance policy；
- FinanceEventOrchestrator；
- market_event transient turn；
- memcore tool trace + assistant analysis；
- QQ subscription delivery。

验收：

- 外部事件不存成 user；
- Actor 偏好检索可参与分析；
- 每次推送有来源与时间；
- 重要推送区分事实、数据、推断和待验证项；
- 同时发生的事件与行情不自动写成确定因果；
- 同事件不重复；
- QQ 失败保留 pending/retry；
- 普通聊天不被阻塞。

### Slice F7：图表和报告

目标：真实数据生成 PNG、MD、PDF、XLSX 并投递 QQ。

状态：F7a 确定性 PNG 图表与 F7b compose_finance_report 均已完成 Fake Bridge/Mock 验收；真实 Choice 和真实 QQ 仍保持未调用。

改动：

- LocalChartProvider；
- render_market_chart；
- compose_finance_report；
- GeneratedFileStore 接线；
- QQ send_image/send_file。

F7a 实际落地：

- `companion_v01/finance/chart_provider.py` 新增严格 `ChartRequest`、`LocalChartProvider` 与固定 PNG 元数据；
- `render_market_chart` 只允许 `candlestick_volume + 1d + bounded lookback + fixed MA enum`，并沿用可信证券代码 provenance；
- 图表通过 GeneratedFileService 的受管路径分配与非空产物登记进入 GeneratedFileStore，PNG 成为可追踪的 `gen_XXX`；
- QQ 新增专用市场图表图片投递入口：只有真实工具事件、金融模式和明确的问答/订阅授权同时成立时才调用 send_image；
- 主动推送先完成文字投递，再尝试图表；图片失败会结构化记录并发送降级提示，不把整条已发送文字重新伪装成未投递；
- Pillow 成为显式运行依赖；ComfyUI 和云端生图仍不参与真实数值图表。

F7b 实际落地：

- `companion_v01/finance/report_provider.py` 新增严格 `FinanceReportRequest`、可信行情证据结构和固定 MD/PDF/XLSX 渲染器；
- `compose_finance_report` 重新读取每个请求代码的可信日线和行情快照，任一代码序列缺失时 fail closed，不生成不完整报告；
- PDF 嵌入可信图表，XLSX 包含原始 OHLCV 与证据工作表，MD 使用受管相对图表引用；
- 普通生成图片和任意本地路径不能作为金融报告图表；代码、周期和复权口径必须匹配；
- 模型分析与程序事实字段分区，报告固定携带 provider/source、as_of、序列指纹、总证据指纹和免责声明；
- QQ 新增专用金融报告文件投递入口，问答与 subscription 授权不依赖当前文本文件意图，普通文件保护仍有效；
- 主动推送的文字一旦成功不会因图片/报告部分发送失败而重复整条推送，产物失败以结构化降级和提示处理。

验收：

- 图表数值与输入序列一致；
- 标题、代码、区间和 as_of 正确；
- 生成失败不发送空文件；
- 不需要当前用户文本文件意图，使用 subscription 授权；
- 普通对话文件保护仍有效。

### Slice F7c：行情 Provider 解耦与可信代码来源隔离

状态：已完成；不依赖真实 Choice 权限。

目标：Choice 审核失败或后续更换数据源时，F0-F7 的上层能力继续保留。

已落地：

- `FINANCE_MARKET_PROVIDER=disabled|emquant` 显式选择行情供应商，默认 `disabled`；金融领域开关与数据源选择互相独立；
- `MarketDataProviderRegistry / MarketDataProviderSettings` 从 Engine 抽出供应商构造。Engine 不再直接实例化 `EmQuantBridgeMarketDataProvider`；未来适配器注册 builder 即可，不需要改图表、报告、QQ 或事件编排；
- 默认生产 registry 只注册 `disabled / emquant`，绝不注册 Mock。未知 provider 返回 `unsupported_provider` 并关闭行情工具服务，不会静默回退到合成数据；
- `DisabledMarketDataProvider` 保留统一工具契约，对新闻、快照和序列返回明确 `unavailable`，本地事件库和普通金融问答仍可独立工作；
- 每个适配器声明 `news_search / event_poll / quote_snapshot / price_series / macro_series / streaming / security_master` 能力。当前 EmQuant 只声明已经实现的前四项（含 `event_poll`），Mock 只声明离线新闻、快照和序列；未实现的宏观、流式和证券主数据抓取不做假成功；
- MarketEventStore schema v4 为 watchlist 增加 provider provenance。证券主数据、当前会话 watchlist 解析、可信代码校验和事件匹配均按 provider 隔离；旧 v3 关注项按此前唯一正式通道无损回填为 `choice_emquant`，不会在切换供应商后被自动冒充为新 provider 代码；
- 共享 provider 契约测试覆盖 Disabled、Mock、EmQuant 及未来 registry 扩展入口。

因此 Choice 不给权限时，损失的是 `choice_emquant` 这个数据适配器，不是订阅、事件状态机、AI 分析、图表、报告、memcore 或 QQ 投递能力。可以继续接入另一家 provider，或先使用公开检索与 `disabled` 的结构化降级。

### Slice F8：云端产物 Provider

目标：可配置云端文档或生图，但不是主链依赖。

验收：

- provider 可切换；
- 云端失败有本地/文字降级；
- 密钥和路径不泄漏；
- 生成图片不承担真实行情数值表达。

### Slice F9：可靠性与运营

目标：长期运行。

#### F9a：逐项投递账本与分析兜底重试

状态：已完成；不依赖 Choice 权限。

已落地：

- MarketEventStore schema v5 新增 `market_event_delivery_parts`。每条文字分段、每张图表和每份报告都有独立 `part_key / part_type / pending|processing|delivered|failed / attempt_count / reason`；
- 首次有效分析会把实际待发送部件持久化。后续 retry 直接读取部件账本，只 claim `pending/failed` 项；已经 delivered 的文字、图表或报告不会重复发送；
- 某张图或报告失败时，父 delivery 保持 failed 以进入 worker retry，但已成功文字保持 delivered。附件补发成功后父 delivery 才变为 delivered；
- 分析完成后才创建投递部件，因此 QQ 故障不会再次调用模型，也不会重复消耗行情工具与 memcore 写入；
- `AkaneFinanceAnalysisClient` 对异常、非对象、空回复、短进度占位，以及人设兜底句“我在认真听你说……”执行最多 `FINANCE_ANALYSIS_MAX_ATTEMPTS` 次进程内重试，并使用可配置退避；
- 进程内尝试全部耗尽后，父 delivery 记录为 `analysis_exhausted`，不会被每两秒 worker 循环再次调用模型；它保留为显式失败，等待后续人工 replay 能力处理；
- 兜底句只被当作 transient retry sentinel。所有尝试耗尽前不调用金融 `_record_memory`；FinanceAnalysisRequest 同时标记 `transient_assistant_message=true`，Engine 不把这些临时 assistant 尝试写进旧聊天库、raw 索引、memcore 或 eval 库；只有验收通过的正式市场分析由金融门面写入 `market_analysis`；
- 共享测试确认：兜底两次后成功时 memcore 只出现最终分析；三次均兜底时没有任何 assistant memory；图表首次失败后仅重试图表，文字 attempt_count 保持 1，分析客户端只调用一次。

F9a 仍不包含跨进程 processing 租约回收；进程在 claim 后崩溃的 watchdog/lease 属于 F9c。

其余 F9 包括：

- Bridge watchdog；
- 订阅自动恢复；
- quota/流量告警；
- digest；
- 速率限制；
- retention；
- metrics；
- 手动 replay；
- Choice 授权范围记录。

## 19. 测试矩阵

### 单元测试

- Actor 稳定 ID 与改名；
- finance mode 命令解析与持久化；
- domain profile prompt 选择；
- capability 裁剪；
- Choice 字段 normalizer；
- 证券别名/代码映射不由模型拼接后缀；
- event id/hash/cluster；
- quote 和技术指标计算；
- relative_volume 的窗口与盘中对齐；
- expectation evidence 缺失时返回 unknown；
- 资金流和估值口径保留；
- SDK present 与 permission_denied 状态分离；
- no-progress guard；
- chart 数据一致性；
- delivery 幂等。

### 集成测试

- Mock provider → MarketEventStore → Orchestrator → Fake QQ；
- Anthropic native tool_use → market tool → tool_result → final；
- memcore retrieve 历史观点；
- 两个群不同 watchlist；
- Bridge unavailable 时聊天仍正常；
- 生成图表后真实调用 QQ image/file 路径。

### 必跑回归

~~~powershell
python -m unittest tests.test_memcore_integration
python -m unittest tests.test_qq_gateway
python -m unittest tests.test_llm_client
python -m unittest tests.test_generated_files
python -m unittest tests.test_prompt_builder
python -m unittest tests.test_native_tool_schema
python -m unittest tests.quick_regression_suite
git diff --check
~~~

修改 memcore 自身时，再按 memcore/AGENTS.md 跑完整验证。

## 20. 真实 API 最小冒烟

Choice 权限开通后，不运行官方完整 demo。

只做：

1. c.start，ForceLogin=0、RecordLoginInfo=0；
2. datastatistics 查询权限和流量；
3. cfn 查询一个代码最近 1 至 3 条资讯；
4. csqsnapshot 查询一个代码；
5. cnq 订阅一个代码或板块短时间；
6. cnqcancel；
7. c.stop。

不得在冒烟中调用组合创建、组合订单、资金调配或删除。

## 21. 安全与授权

- Choice API 权限不自动等于允许向第三方群重新分发全部数据。
- 正式商用前向 Choice 确认 QQ 推送、缓存期限、行情展示和新闻链接的授权范围。
- 若授权只允许内部使用，Demo 群必须限制为授权成员。
- API key、账号、userInfo、token、数据库、登录日志和本地绝对路径不能进入 prompt、日志、snapshot、文档或 commit。
- 不记录完整受限正文，除非合同允许。
- 每条分析应保留 source/as_of/event_id，方便审计。
- “仅供参考，不构成投资建议”不能替代真实业务合规判断；若面向公众、收费或持续提供具体标的买卖、目标价、仓位、止损等建议，上线前必须单独进行证券投资咨询合规审查。
- V1 产品定位保持为资讯整理、原文核验、数据查询、事件影响路径解释、风险提示和历史证据对照，不提供下单指令或收益承诺。

## 22. 非目标

V1 不做：

- 真实交易和券商下单；
- 全市场每条 tick 都调用 LLM；
- 把所有新闻写入 memcore；
- 用生图模型画行情图；
- 在主 FastAPI 进程加载 Choice DLL；
- 重写 GeneratedFileStore；
- 重写 memcore；
- 新建 qq_finance ClientMode；
- 为效果伪造数据、文件、发送状态或 API 成功。

## 23. 完成定义

V1 完成时，下面场景必须真实成立：

1. 群管理员发送“开启财经推送”。
2. 该群关注一个证券代码。
3. Mock 或 Choice 推送一条命中关注列表的新资讯。
4. 系统通过 event_id 去重并读取当前行情。
5. Sonnet 主动补查必要证据，能超过三轮但不会死循环。
6. 回复保留当前角色人格，明确事实、推断、来源和数据时间。
7. 重大事件可生成真实 PNG 图表并发到 QQ。
8. memcore 记录事件证据、分析结论和正确 Actor。
9. 同一资讯重放不重复推送。
10. Choice/图表/QQ 任一环节失败时结构化降级，普通聊天仍可用。

达到以上结果，才算“有人格的金融问答与主动分析助手”主链完成。

## 24. 下一步

F6、F7、F7c 与 F9a 已完成 Fake Bridge/Mock 主链验收，真实主动推送仍因默认开关和 Choice 权限保持关闭。上下文恢复后按以下顺序继续：

1. Choice 权限开通后按第 20 节执行最小只读冒烟，确认 cfn/cnq/csqsnapshot/csd 的实际权限、callback 字段、证券主数据来源和 AdjustFlag 口径；未确认前保持 `FINANCE_MARKET_PROVIDER=disabled` 或仅使用 Fake SDK 测试；
2. 如果 Choice 未授权，按统一能力契约新增另一家只读 provider，优先补 `quote_snapshot / price_series / news_search`，不改上层主链；
3. 进入 F8：仅为装饰性封面、非事实插图或可选高保真文档接云端 Provider；真实行情图和报告事实表继续由本地确定性程序生成；
4. 视真实数据源权限补 `market_macro_series`，并为发布日期/修订时间防前视偏差；
5. 继续 F9b：事件限频、同类聚合、安静时段与 digest；随后 F9c 补 processing 跨进程租约、Bridge watchdog 与订阅恢复，F9d 补 metrics 和人工 replay。

推荐下一个独立提交边界：`finance notification rate limits, quiet hours and digest`；F8 云端产物仍为可选增强，不能让 Choice 或云端能力成为金融主链依赖。
