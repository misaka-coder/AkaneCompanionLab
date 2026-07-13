# Akane 免费公开行情 Provider 实施细案 V1

状态：F7d0-F7d4、public security master bootstrap 与瞬时网络重试已完成；公开源 live smoke 部分通过且确认上游波动会 fail closed；public_market 默认 disabled
更新时间：2026-07-11
适用仓库：AkaneCompanionLab
实施分支：`feature/qq-finance-assistant-emquant`
F7d0 起点提交：`93e0cef docs(finance): plan public market provider`

## 0. 文档目的

本文是免费公开行情接入的上下文恢复文档和施工单。它用于在对话上下文压缩、换模型、换协作者或暂停数日后，仍能从确定的代码边界继续实现，而不重新讨论一遍 Yahoo、AkShare、Choice 和 ETF 的关系。

目标是在现有 `MarketDataProvider` 契约上增加一个可选的 `public_market` Provider，使 Akane 在没有 Choice 审批和商业经费时，也能用真实公开数据完成第一版效果演示：

- 查询日经 225、标普 500、纳斯达克综合、恒生指数等全球指数的日线历史；
- 在上游确实提供有效时间戳时返回延迟行情快照；
- 查询境内跨境 ETF 的行情与历史数据；
- 继续复用已经完成的金融工具、确定性 K 线、报告、QQ 投递、memcore 证据和推送治理；
- 数据不可用时返回结构化失败，不生成 Mock，不把历史收盘伪装成实时价格；
- Choice 获批后仍可通过同一 Provider registry 切换，不修改上层分析链。

本文不是市场数据授权意见。正式公开展示、收费或大规模再分发前，仍需核对每个上游的服务条款、缓存范围和展示许可。

## 1. 上下文恢复顺序

上下文丢失后，按以下顺序阅读：

1. 根目录 `AGENTS.md`；
2. 本文件；
3. `docs/qq_finance_assistant_emquant_implementation_v1.md`，重点读 F7c、F9a、F9b 和“下一步”；
4. `services/market_data/provider.py`；
5. `services/market_data/factory.py`；
6. `services/market_data/types.py`；
7. `services/market_data/normalizers.py`；
8. `tests/test_market_data_provider.py`；
9. `tests/test_market_data_provider_registry.py`；
10. `companion_v01/finance/chart_provider.py` 和 `report_provider.py`，确认上层消费契约。

恢复后先执行：

~~~powershell
git status --short --branch
git log -6 --oneline
rg -n "MarketDataProvider|FINANCE_MARKET_PROVIDER|MarketSeries|MarketQuoteSnapshot" services companion_v01 config.py tests
~~~

预期分支为 `feature/qq-finance-assistant-emquant`。本文写入时仅有未跟踪的 `uv.lock`，它属于用户已有工作，不得暂存、修改或提交。

## 2. 当前已完成基础

不要重写下面这些能力：

- `services/market_data/provider.py` 已有 QQ 无关、人格无关的只读 Provider 契约；
- `services/market_data/factory.py` 已有显式 registry，生产默认只注册 `disabled` 和 `emquant`；
- `MockMarketDataProvider` 只用于测试，故意不进入生产 registry；
- 未知 Provider 会返回 `unsupported_provider`，不会静默回退 Mock；
- `MarketDataResponse` 已约束 `ok / empty / unavailable / permission_denied / rate_limited / invalid_arguments`；
- `MarketProviderHealth` 已约束 `ready / degraded / disconnected / permission_denied`；
- `MarketEventStore`、订阅、关注列表、聚类摘要、投递幂等和失败重试已经存在；
- `market_quote_snapshot`、`market_price_series`、确定性 PNG 图表和 MD/PDF/XLSX 报告已消费统一 Provider；
- QQ 和 memcore 上层不应知道数据来自 Yahoo、AkShare 还是 Choice；
- F9a 已避免模型临时兜底语污染记忆，F9b 限制的是主动消息发送节奏，不限制用户主动查询实时信息。

因此本切片只补行情数据底座，不重做 AI、QQ、图表、报告、事件编排或记忆系统。

## 3. 为什么 Choice 必须保持可选

本地下载到的 `EMQuantAPI_Python` 是 Choice QuantAPI SDK，不等于账户自动拥有免费数据权限。SDK 和手册已经显示权限、到期、LV2 和流量配额等错误状态，因此当前必须区分：

~~~text
SDK 可下载
!= API 已审批
!= 账户拥有目标市场权限
!= 允许对 QQ 群展示或再分发
!= 免费且无流量限制
~~~

锁定策略：

- `emquant` 保留为可选高级 Provider；
- 未获得权限前默认继续使用 `disabled`，开发时用离线 fixture；
- 免费演示主线使用新的 `public_market`；
- 不因 Choice 未批而改动图表、报告、QQ、memcore 或金融工具；
- Choice 获批后只做 Provider 切换和真实只读冒烟；
- 不把 Choice SDK、账号文件、登录记录或本地绝对路径提交进仓库。

## 4. 锁定范围与非目标

### 4.1 V1 必做

- 规范化证券/指标注册表；
- Yahoo 全球指数日线历史；
- Yahoo 延迟快照，前提是能获得可信观察时间；
- AkShare 境内 ETF 快照与日线历史；
- 进程内有界 TTL 缓存；
- 超时、有限重试和上游限流保护；
- `public_market` composite Provider 注册与配置；
- 离线 fixture 单测，以及显式启用后的网络 smoke；
- 在工具结果、图表和报告中保留来源、数据时间、复权和延迟提示。

### 4.2 V1 不做

- 不做交易、下单、组合或资金操作；
- 不把境内日经 ETF 当成日经 225 指数本体；
- 不声称免费源是交易所实时直连、零延迟或毫秒级；
- 不接 ComfyUI 绘制数值图表；
- 不让模型生成并执行绘图代码；
- 不依赖未文档化的财联社、金十或东方财富内部网页接口做首个切片；
- 不先建设大型时序数据库；
- 不把 pandas、yfinance、AkShare 强塞进基础安装；
- 不把过期缓存伪装成最新数据；
- 不从免费 Provider 静默回退到 Mock；
- 不在指数源失败时自动拿 ETF 价格冒充指数价格。

## 5. 开源项目评估与选择

| 项目 | 适合用途 | 优点 | 主要风险 | V1 决定 |
|---|---|---|---|---|
| yfinance | 全球指数和海外证券历史、延迟行情 | 接口简单，适合快速接 Provider | 非交易所官方数据接口；字段、频率、可用性和条款可能变化 | 采用，先做全球指数日线 |
| AkShare | A 股、基金、ETF 等公开数据聚合 | 国内品种覆盖广，便于快速验证 | 上游来源众多；接口和列名可能变化；依赖较重 | 采用，只做境内 ETF 小切片 |
| BaoStock | A 股历史数据 | 免费、历史查询清晰 | 全球指数和盘中 ETF 不是其优势 | 暂不采用，保留后备 |
| TuShare | 国内金融数据 | 数据结构较统一 | token、积分和权限不等于完全免费 | 不作为零预算主线 |
| OpenBB | 多数据源研究平台 | 生态完整、适配器多 | 体积和抽象层过重，与现有 Provider 重叠 | 仅参考，不引入 |
| vn.py / RQAlpha / backtrader | 交易、量化研究、回测 | 相关生态成熟 | 解决的是交易或回测，不是当前只读问答数据接入 | 不引入 |

选择标准按优先级排序：

1. 数据真实且能标出来源和时间；
2. 无需等待商业审批即可做内部演示；
3. 能映射进现有 `MarketDataProvider`，不污染上层；
4. 失败可检测，字段变化可通过 fixture 测出；
5. 依赖可选，不影响普通 Akane 安装；
6. 服务条款允许当前使用场景；
7. 性能足够问答和低频推送，不追求交易级延迟。

在 F7d0 锁依赖版本前，必须重新核对所选包的当前许可证、官方仓库、PyPI 包所有者和上游服务条款。不能只根据“无 Key”判断它免费、稳定或允许再分发。

### 5.1 F7d0 已确认事实

2026-07-11 在仓库外临时环境完成最小 spike，确认：

- yfinance 当前选定版本为 `1.5.1`，wheel 元数据写 Apache，项目说明同时明确其与 Yahoo 无隶属关系、主要用于研究教育，并提示 Yahoo 数据 API 面向个人使用；客户端开源许可证不授予行情再分发权；
- AkShare 当前选定版本为 `1.18.64`，wheel 元数据写 MIT，打包项目说明要求数据仅用于学术研究并提示数据风险；实际 ETF endpoint 标明上游为东方财富；
- 两者都会引入 pandas，AkShare 还带来 lxml、curl_cffi、mini-racer 等较重依赖，因此继续采用独立 `requirements-finance-public.txt`；
- yfinance 1.5.1 的 `download` 默认 `auto_adjust=True`、`multi_level_index=True`、`threads=True`、`progress=True`；适配器必须显式覆盖为未复权、单层列、单线程、无进度输出；
- 当前环境对 Yahoo chart host 连续超时；本轮只保存 yfinance 确定性函数/列契约，不保存或伪造 live Yahoo 行情；真实成功观察留给显式网络 smoke；
- spike 同时确认 yfinance 批量 `download()` 可能记录错误后返回空 DataFrame；生产默认 downloader 因此使用单标的 `Ticker.history(..., raise_errors=True)`，避免把网络失败误报为合法空数据；
- AkShare `fund_etf_spot_em()` 成功返回 37 列，包含 `数据日期` 和 timezone-aware 的 `更新时间`；`fund_etf_hist_em(..., period="daily", adjust="")` 成功返回 11 列未复权日线；
- 2026-07-11 抓取“实时行情”时，样本的 `数据日期/更新时间` 仍是 2026-07-10 收盘后；因此 `as_of` 必须来自数据字段，`fetched_at` 只能表示抓取时间，函数名中的“实时”不能直接变成产品承诺；
- AkShare ETF 的 `成交量` 与成交额/价格的数量级显示其源单位为“手”，统一适配时必须乘 100 转为份额；原值不能直接写入标准 `volume`；
- `513000` 与 `513520` 均由 AkShare 返回，且已通过上交所基本信息页和对应基金管理人页面交叉核验；它们分别映射为 `513000.SH`、`513520.SH`，跟踪对象为日经 225，但仍是人民币 ETF 代理而非指数本体；
- 513000 的基金管理人公开名称在 2025-09-01 后发生过变更，registry 应保留稳定代码和可更新 display name，不能把历史简称当永久身份。

F7d0 产物：

~~~text
requirements-finance-public.txt
tests/fixtures/public_market_yahoo_v1.json
tests/fixtures/public_market_akshare_etf_v1.json
tests/test_public_market_data_shapes.py
~~~

使用边界因此进一步锁定：这些免费客户端只进入开发、研究和内部效果验证；若 QQ 群属于公开、收费或商业再分发场景，必须先取得上游许可或更换有相应授权的数据 Provider。

## 6. Provider 架构

~~~text
PublicMarketProvider                     provider_id = public_market
├── PublicInstrumentRegistry             canonical code 和路由真相源
├── YahooFinanceAdapter                  全球指数日线与延迟观察
├── AkShareETFAdapter                    境内 ETF 快照与日线
├── TTLMarketDataCache                   短期复用与上游保护
└── PublicMarketHealth                   子适配器健康汇总
        │
        ▼
现有 MarketDataProvider / MarketDataResponse
        │
        ├── market_quote_snapshot
        ├── market_price_series
        ├── LocalChartProvider
        ├── FinanceReportProvider
        └── Sonnet / QQ / memcore
~~~

`PublicMarketProvider` 是 composite，而不是让 Engine 同时认识 Yahoo 和 AkShare。Engine 仍只根据 `FINANCE_MARKET_PROVIDER` 创建一个 Provider。

建议文件布局：

~~~text
services/market_data/
├── public_instruments.py
├── public_cache.py
├── public_yahoo.py
├── public_akshare.py
├── public_provider.py
├── provider.py
├── factory.py
└── types.py

tests/
├── fixtures/public_market_yahoo_v1.json
├── fixtures/public_market_akshare_etf_v1.json
├── test_public_market_instruments.py
├── test_public_market_yahoo.py
├── test_public_market_akshare.py
└── test_public_market_provider.py

requirements-finance-public.txt
~~~

不要把网络抓取逻辑写进 `companion_v01/finance/tool_handlers.py`、图表或报告模块。

## 7. 规范化标的注册表

### 7.1 数据结构

`PublicInstrumentRegistry` 是代码、供应商符号、市场时区、币种和路由的唯一真相源。建议结构：

~~~python
@dataclass(frozen=True)
class PublicInstrument:
    canonical_code: str
    display_name: str
    instrument_type: str
    market: str
    exchange_timezone: str
    currency: str
    route: str
    vendor_symbol: str
    quote_delay_kind: str
    active: bool = True
~~~

固定语义：

- `canonical_code` 是 Akane 上层、watchlist、图表和报告使用的稳定代码；
- `vendor_symbol` 只在适配器内部出现；
- `route` V1 只允许 `yahoo` 或 `akshare_etf`；
- `quote_delay_kind` 使用固定枚举，如 `delayed / end_of_day / unknown`；
- 模型不能自行把自然语言、Yahoo 符号或纯数字拼成最终证券代码；
- registry 查不到的代码返回 `invalid_arguments`，不能拿原字符串直接请求上游。

### 7.2 第一批标的

第一批建议种子：

| canonical_code | display_name | route | vendor_symbol | timezone | currency |
|---|---|---|---|---|---|
| `NIKKEI225.INDEX` | 日经 225 | yahoo | `^N225` | `Asia/Tokyo` | JPY |
| `SP500.INDEX` | 标普 500 | yahoo | `^GSPC` | `America/New_York` | USD |
| `NASDAQCOMPOSITE.INDEX` | 纳斯达克综合 | yahoo | `^IXIC` | `America/New_York` | USD |
| `HSI.INDEX` | 恒生指数 | yahoo | `^HSI` | `Asia/Hong_Kong` | HKD |
| `513000.SH` | 境内日经 225 ETF 候选 | akshare_etf | `513000` | `Asia/Shanghai` | CNY |
| `513520.SH` | 境内日经 225 ETF 候选 | akshare_etf | `513520` | `Asia/Shanghai` | CNY |

ETF 名称、基金状态、跟踪指数和代码必须在 F7d0 通过可信证券主数据或交易所公开信息复核后才能设为 `active=True`。文档中的两个代码是候选种子，不是对基金关系的永久硬编码保证。

### 7.3 别名

别名只用于确定性查找，例如：

~~~text
日经 / 日经225 / Nikkei 225 -> NIKKEI225.INDEX
标普500 / S&P 500 -> SP500.INDEX
纳指综合 / Nasdaq Composite -> NASDAQCOMPOSITE.INDEX
恒生 / 恒指 -> HSI.INDEX
~~~

别名命中多个标的时必须返回候选，不能猜测。指数与其 ETF 不共享同一个 canonical code。

## 8. 标准数据与 provenance 契约

现有 `MarketQuoteSnapshot`、`MarketSeries` 和 `MarketDataResponse` 可以承载基本行情，但免费源接入还必须保留下列信息：

- canonical code；
- vendor symbol；
- Provider 和实际子来源；
- `as_of` 与 `fetched_at`；
- exchange timezone；
- trading date/session；
- currency；
- adjustment mode；
- delay kind 或已知延迟秒数；
- data quality/confidence；
- 原始字段 schema 版本或 adapter 版本。

F7d0 先为现有标准类型增加向后兼容的明确 provenance 结构。建议：

~~~python
@dataclass(frozen=True)
class MarketDataProvenance:
    source: str
    vendor_symbol: str
    fetched_at: int
    exchange_timezone: str
    currency: str
    session: str
    delay_kind: str
    delay_seconds: int | None
    data_quality: str
    adapter_version: str
~~~

`MarketQuoteSnapshot` 和 `MarketSeries` 在末尾增加可选 `provenance` 字段，保证现有构造调用兼容。`to_public_dict()` 必须输出这些字段，工具、图表和报告才有机会向用户显示来源和延迟。

`MarketBar` 在末尾增加有默认值的 `trading_date` 和 `time_semantics`，避免破坏现有位置参数构造：

~~~python
@dataclass(frozen=True)
class MarketBar:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    amount: float | None = None
    trading_date: str = ""
    time_semantics: str = "instant"
~~~

日线必须设置 `trading_date=YYYY-MM-DD` 和 `time_semantics="trading_date"`。由于现有 `timestamp` 暂时不能改为可空：

- 优先保留上游真实日线时间戳；
- 上游只有日期时，按交易所时区生成“交易日排序锚点”；
- 该排序锚点仅用于现有排序、图表和区间过滤兼容；
- 该锚点不得被描述为盘中成交时刻或实时观察时间。

`provider` 对上层保持 `public_market`，`provenance.source` 写实际子来源，如 `Yahoo Finance` 或 `AkShare/<verified source>`。这样 watchlist 的 Provider 隔离仍稳定，同时用户能看到真实来源。

## 9. 时区和交易日规则

### 9.1 盘中观察

- 有明确时刻的行情使用 timezone-aware datetime 解析；
- 内部统一存 Unix epoch，语义等价于 UTC instant；
- 展示和跨市场比较时再转换到 `Asia/Shanghai`；
- 同时保留 `exchange_timezone`；
- 禁止在转换前直接 `.tz_localize(None)` 丢掉时区；
- naive datetime 只有在 registry 已知交易所时区时才能解释。

### 9.2 日线

- 日线的权威主键是 `trading_date`，不是“北京时间零点”；
- 东京、香港、上海、纽约交易日分别按各自交易所日历解释；
- 不把纽约前一交易日收盘错误对齐到中国同一自然日；
- 跨市场联动分析必须明确比较的是同一 instant、同一交易日标签，还是前后相邻 session；
- 日线复权口径必须来自请求和上游事实，不能由模型推断。

### 9.3 展示

QQ 回复建议同时显示：

~~~text
数据时间：2026-07-10 15:00 JST（北京时间 14:00）
来源：Yahoo Finance，经 public_market 适配
状态：延迟行情，非交易所实时直连
~~~

## 10. YahooFinanceAdapter

### 10.1 首个能力

F7d1 只实现：

- canonical code 查 registry；
- `interval=1d`；
- `adjusted=none`；
- 有界 `date_from / date_to / limit`；
- 输出规范化 OHLCV `MarketSeries`；
- 空结果和 schema 变化结构化返回；
- 网络完全由测试 double 替代，单测不访问 Yahoo。

第一版不承诺分钟线、复权、分红拆股、期权或新闻。

### 10.2 `auto_adjust`

首版显式使用未自动复权数据，保持 `adjusted=none`。若 yfinance API 使用 `auto_adjust` 参数，必须显式传 `False`，不能依赖包版本默认值。

调用方请求 `forward` 或 `backward` 时，若适配器没有经过验证的实现，返回：

~~~json
{
  "ok": false,
  "status": "invalid_arguments",
  "reason": "unsupported_adjustment:public_yahoo_v1"
}
~~~

### 10.3 字段处理

- 兼容 yfinance 单标的返回的普通列与 MultiIndex 列；
- 必须验证 `Open/High/Low/Close`；
- `Volume` 可空，但不能为负；
- 删除整行核心 OHLC 为空的记录；
- 不用 `0` 填补未知值；
- 结果按时间升序且时间唯一；
- `as_of` 是最新有效观察时间，不是本次抓取时间；
- `fetched_at` 单独记录；
- vendor symbol 不得泄漏为上层 canonical code。

### 10.4 延迟快照

F7d2 再实现 `quote_snapshot`。优先使用带真实观察时间的上游字段。若只能获得日线历史：

- 可以明确返回“最近收盘观察”；
- `status`、`delay_kind` 和 `as_of` 必须反映它不是盘中实时；
- 不能把 `fetched_at` 当 `as_of`；
- 无法确认时间时返回 `unavailable`，不猜。

### 10.5 2026-07-13 节点稳定性修订

- 默认日线下载从 `yfinance.Ticker.history()` 改为 Yahoo Chart HTTP，继续复用相同的规范化、provenance、缓存和结构化失败契约；请求带显式硬超时。
- `_default_yahoo_downloader()` 仍作为兼容和确定性测试路径保留；`yfinance.Search` 继续用于运行时证券发现，但不再阻塞已知 canonical code 的日线查询。
- 当前节点实测 `SP500.INDEX` 返回三条真实 Yahoo 日线；偶发 SSL/超时仍按 `unavailable` 返回并进入金融能力短时熔断，不会被误报为 `empty` 或成功。
- `PublicMarketProvider.health()` 的 Yahoo 本地依赖检查改为基础 `requests`；实际调用失败由结果熔断补充，不能再把“安装了 yfinance”当成上游可达证明。

## 11. AkShareETFAdapter

### 11.1 边界

本适配器只接 registry 中明确登记的境内 ETF，不开放任意函数名或任意 AkShare endpoint 给模型。

候选能力：

- ETF 当前快照；
- ETF 日线 OHLCV；
- 精确证券名称和状态核验。

F7d0 应实测当前 AkShare 版本的公开函数、列名、单位和上游来源，并保存脱敏的最小离线 fixture。生产代码不得依赖“第 3 列就是最新价”这类位置假设，必须按受测列名映射。

### 11.2 ETF 解释纪律

境内日经 ETF 只能作为“境内交易视角的代理信号”。分析时保留这些限制：

- CNY/JPY 汇率变化；
- ETF 溢价或折价；
- 跟踪误差和管理费；
- QDII 额度、申赎和流动性；
- 中日休市与交易时段错位；
- A 股市场情绪和涨跌停约束。

禁止表述：

- “ETF 就是日经指数”；
- “ETF 毫秒级、零延迟”；
- “境内 ETF 一定领先日本市场”；
- 仅凭 ETF 涨跌断言日经现货同步变化。

### 11.3 来源名称

AkShare 是聚合库，不应把所有数据笼统写成“AkShare 官方行情”。若 endpoint 能确认实际公开来源，provenance 应写：

~~~text
provider = public_market
source = AkShare/<actual upstream source>
adapter = public_akshare_etf_v1
~~~

无法确认上游时降低 `data_quality`，并在对外展示中写“公开聚合数据”。

## 12. Composite 路由和失败规则

### 12.1 路由

~~~text
NIKKEI225.INDEX             -> yahoo
SP500.INDEX                 -> yahoo
NASDAQCOMPOSITE.INDEX       -> yahoo
HSI.INDEX                   -> yahoo
513000.SH / 513520.SH       -> akshare_etf
unknown canonical code      -> invalid_arguments
~~~

每次请求先查 registry，再按 route 分组。V1 对多代码快照采用 fail closed：任一代码失败时整次请求不返回混合真假难辨的部分成功；需要部分结果的上层应拆成单代码调用。以后若确实需要部分成功，再单独扩展标准状态，不能把部分成功伪装为完整 `ok`。

### 12.2 禁止的 fallback

- Yahoo 失败不能回退 Mock；
- 日经指数失败不能回退为 513000/513520 的价格；
- AkShare 失败不能从网页搜索文本中抽数字冒充行情；
- 未安装可选依赖不能回退合成数据；
- registry 未登记的 vendor symbol 不能直通上游。

可以返回代理建议，例如 `proxy_candidates=["513000.SH", "513520.SH"]`，但必须由用户或分析层明确选择后重新查询。

### 12.3 健康汇总

- Yahoo 和 AkShare 均可用：`ready`；
- 至少一个可用、至少一个失败：`degraded`；
- 所有已启用子适配器不可用：`disconnected`；
- 某个 route 未安装依赖时，该 route 返回 `unavailable`；
- Provider 的 capability 只声明实际可执行的能力。

## 13. 缓存、超时、重试和限流

V1 使用进程内、有界、按规范化请求键控的 TTL cache，不先建新 SQLite 时序库。

建议默认值：

| 数据 | TTL | 说明 |
|---|---:|---|
| Yahoo 日线序列 | 900 秒 | 日线无需每次问答重复下载 |
| Yahoo 延迟快照 | 60 秒 | 不宣称交易级实时 |
| AkShare ETF 日线 | 300 秒 | 降低公开上游压力 |
| AkShare ETF 快照 | 15 秒 | 兼顾问答体验和上游保护 |
| 失败负缓存 | 15 秒 | 防止模型工具循环连续打同一失败请求 |

缓存键至少包含：

~~~text
adapter / canonical_code / capability / interval / adjusted / date_from / date_to / limit
~~~

规则：

- cache 有最大条目数，建议默认 256；
- 命中缓存仍返回原始 `as_of`，只更新内部 cache hit 指标，不伪造数据时间；
- 过期值 V1 不作为新鲜结果返回；
- 超时默认 8 秒，可配置；
- 每个上游请求最多 2 次总尝试；
- 只对 timeout、连接重置和明确可重试的 5xx 重试；
- 对参数错误、未知代码、空数据、schema 变化不重试；
- 429 映射 `rate_limited`，尊重可用的 `Retry-After`；
- 重试采用短退避和少量 jitter；
- 工具循环的同签名重复保护继续由现有 Engine 承担；
- 日志只记录 provider、route、canonical code、状态和耗时，不记录响应全文或本地路径。

## 14. 可选依赖隔离

新增：

~~~text
requirements-finance-public.txt
~~~

该文件包含经过 F7d0 验证并锁定兼容范围的 yfinance、AkShare 及必要依赖。基础 `requirements.txt` 不加入这些包。

生产代码使用延迟导入：

- `FINANCE_MARKET_PROVIDER` 不是 `public_market` 时，不导入 yfinance/AkShare；
- 选择 `public_market` 但依赖缺失时，构造 Provider 仍应可返回结构化 health；
- 对应 route 调用返回 `unavailable / optional_dependency_missing:<package>`；
- 不提示用户安装来源不明的 wheel；
- CI 的基础回归不需要联网或安装公开行情 extra；
- 单独增加带可选依赖的测试 job 或本地命令。

建议安装方式：

~~~powershell
python -m pip install -r requirements-finance-public.txt
~~~

不要在文档或代码里写开发者本机 SDK 路径。

## 15. 配置字段

在 `config.py`、模块级导出和 `companion_v01/settings_catalog.py` 同步增加：

~~~dotenv
FINANCE_MARKET_PROVIDER=public_market
FINANCE_PUBLIC_MARKET_YAHOO_ENABLED=true
FINANCE_PUBLIC_MARKET_AKSHARE_ENABLED=true
FINANCE_PUBLIC_MARKET_TIMEOUT_SECONDS=8
FINANCE_PUBLIC_MARKET_MAX_ATTEMPTS=2
FINANCE_PUBLIC_MARKET_CACHE_MAX_ENTRIES=256
FINANCE_PUBLIC_MARKET_YAHOO_SERIES_TTL_SECONDS=900
FINANCE_PUBLIC_MARKET_YAHOO_QUOTE_TTL_SECONDS=60
FINANCE_PUBLIC_MARKET_AKSHARE_SERIES_TTL_SECONDS=300
FINANCE_PUBLIC_MARKET_AKSHARE_QUOTE_TTL_SECONDS=15
FINANCE_PUBLIC_MARKET_FAILURE_TTL_SECONDS=15
~~~

默认 `FINANCE_MARKET_PROVIDER` 仍为 `disabled`。安装依赖不能自动开启联网数据源，启动应用也不能自动发 QQ 消息。

`MarketDataProviderSettings` 增加上述字段，factory 注册：

~~~python
registry.register("public_market", build_public_market_provider)
~~~

生产 registry 最终预期为：

~~~text
disabled / emquant / public_market
~~~

Mock 仍不注册。

## 16. 结构化失败口径

沿用现有 status，不为上游异常制造假成功：

| 场景 | status | reason 示例 |
|---|---|---|
| canonical code 未登记 | `invalid_arguments` | `unknown_instrument:N225` |
| interval/复权不支持 | `invalid_arguments` | `unsupported_interval:1m` |
| 可选包未安装 | `unavailable` | `optional_dependency_missing:yfinance` |
| 网络超时 | `unavailable` | `upstream_timeout:yahoo` |
| 上游字段改变 | `unavailable` | `upstream_schema_changed:yahoo_v1` |
| 上游明确限流 | `rate_limited` | `upstream_rate_limited:akshare` |
| 合法请求但无数据 | `empty` | `no_observations:NIKKEI225.INDEX` |
| 无可信观察时间 | `unavailable` | `observation_time_unavailable` |
| Provider 全部 route 不可用 | health `disconnected` | `all_public_routes_unavailable` |
| 部分 route 不可用 | health `degraded` | `akshare_route_unavailable` |

`reason` 不包含 URL 查询参数、cookie、token、响应正文、本地路径或堆栈。内部异常可记录脱敏 exception class，不能直接回填给模型。

## 17. 新闻和事件源边界

公开行情 V1 不同时接入快讯轮询。原因：

- 未文档化网页接口可能随时改变；
- 无 Key 不代表允许高频抓取和再分发；
- 新闻正文、标题和转载链需要额外版权边界；
- 当前最先需要的是可验证指数/ETF 数据，让图表、报告和问答先工作。

后续新闻切片优先级：

1. 交易所、监管机构、上市公司官方公告；
2. 有明确文档和许可的 RSS/API；
3. 公开网页搜索用于核验和补充；
4. 未文档化 CLS/Jin10/东方财富网页接口必须单独评估，不进入默认主链。

新闻事件仍进入现有 `MarketEventStore`，不能把轮询流直接灌入 memcore。

## 18. 安全、授权和再分发

- yfinance/AkShare 是客户端库，不替代实际数据源的服务条款；
- 内部演示、私有群测试和公开商业服务不是同一授权场景；
- 对外回复保留 source、as_of、delay、currency 和 adjustment；
- 不缓存或分发超出上游条款允许范围的历史数据；
- 不提交 cookie、crumb、token、代理地址、响应原文缓存或用户查询日志；
- 不绕过登录、验证码、付费墙、频率限制或访问控制；
- 不提供自动下单或收益承诺；
- 数据异常时宁可返回不可用，也不使用模型补数字；
- “仅供参考”不能替代正式合规审查。

## 19. 测试矩阵

### 19.1 注册表

- canonical code 唯一；
- vendor symbol 不为空但不暴露为 canonical code；
- timezone 可由 `ZoneInfo` 加载；
- currency、route 和 delay enum 合法；
- alias 唯一命中和歧义返回；
- 未登记代码 fail closed；
- 指数与 ETF 不会解析成同一标的。

### 19.2 Yahoo adapter

- 普通列和 MultiIndex fixture；
- OHLCV 正常规范化；
- `auto_adjust=False` 显式传入；
- 缺列、空行、重复日期、负成交量；
- Tokyo/New York 时区；
- date range 和 limit；
- unsupported interval/adjustment；
- timeout、429、5xx、schema changed；
- 观察时间与抓取时间分离；
- 单测零网络。

### 19.3 AkShare ETF adapter

- 精确代码路由；
- 列名和数值单位映射；
- 名称、状态和交易日；
- 空值、停牌、零成交、负数异常；
- 上游列名改变时 fail closed；
- ETF 不冒充指数；
- 单测零网络。

### 19.4 Composite 和 cache

- 指数路由 Yahoo、ETF 路由 AkShare；
- 未安装单个依赖时 health degraded；
- 全部缺失时 disconnected；
- 相同请求 TTL 内只调用上游一次；
- cache key 包含日期、interval 和 adjusted；
- 过期后刷新；
- 失败负缓存；
- 最大条目淘汰；
- 不回退 Mock；
- 多代码任一失败时 fail closed。

### 19.5 上层集成

- `FINANCE_MARKET_PROVIDER=public_market` 能由 Engine 构造；
- production registry 是 `disabled/emquant/public_market` 且无 Mock；
- `market_price_series` 能生成确定性 PNG；
- report 保留 public source/provenance；
- 日经指数和 ETF 报告分别标注对象类型；
- Provider 不可用时普通聊天仍可用；
- 默认配置不联网、不发送 QQ。

### 19.6 网络 smoke

网络 smoke 必须显式启用，不进入普通单测。只查询少量固定标的：

1. `NIKKEI225.INDEX` 最近 10 个交易日日线；
2. `SP500.INDEX` 最近 10 个交易日日线；
3. 一只已复核的境内日经 ETF 快照；
4. 一只已复核的境内日经 ETF 最近 10 日日线；
5. 打印结构化状态、行数、as_of、source 和 delay，不打印整份响应。

## 20. 实施切片和提交边界

每个切片独立验证、独立提交，不把所有依赖、Provider、配置和上层改动塞进一个 commit。

### F7d0：依赖、许可证和数据形状 spike

状态：已完成；Yahoo live 成功观察因当前网络不可达保留为显式 smoke 项，不阻塞离线 adapter 实现。

目标：在写生产 adapter 前锁定真实包版本、字段、单位、时间语义和使用边界。

交付：

- 核对 yfinance/AkShare 当前许可证与上游条款；
- 在临时环境做最小只读查询；
- 保存小型脱敏 fixture，不保存 cookie/响应头；
- 确认 ETF 候选代码、名称、状态和跟踪关系；
- 为标准类型设计向后兼容 provenance；
- 创建 `requirements-finance-public.txt`，但不改基础 requirements。

建议提交：

~~~text
test(finance): capture public market data shapes
~~~

### F7d1a：规范化标的注册表

状态：已完成；纯离线实现，不导入公开行情依赖，不修改生产 Provider factory。

目标：先建立稳定 canonical code 和路由真相源，不访问网络。

交付：

- `public_instruments.py`；
- 第一批指数和已复核 ETF；
- alias 查找；
- registry 单测。

实际实现额外锁定：

- 公开序列化不输出 vendor symbol 和内部 route；
- 不接受 `^N225` 或纯数字 ETF vendor code 直接解析；
- alias 只做规范化后的 exact match，不做模糊猜测；
- 同名 alias 返回 `ambiguous` 和候选；
- ETF 的 tracking target 必须指向 registry 中真实存在的 index；
- 未知代码由 `require()` 返回 `invalid_arguments / unknown_instrument`。

第一个生产实现提交必须保持小：

~~~text
feat(finance): add public market instrument registry
~~~

### F7d1b：Yahoo 全球指数日线

状态：已完成；离线 adapter 契约已接通，尚未注册进生产 factory，真实 Yahoo 成功查询仍等待网络 smoke。

目标：让 `market_price_series` 对全球指数返回真实日线，并能直接复用现有图表和报告。

本切片同时加入 provenance 标准类型的最小向后兼容扩展；不把这一类型改动塞回已经完成的 registry commit。

实际落地：

- `MarketDataProvenance` 保存 source、vendor symbol、fetched_at、exchange timezone、currency、session、delay 和 data quality；
- `MarketBar` 向后兼容增加 `trading_date/time_semantics`，日线使用交易所本地日期排序锚点并明确标记为 `trading_date`；
- `YahooFinanceAdapter` 只接受 registry 中 route 为 Yahoo 的 canonical code；
- 默认 downloader 走带硬超时的 Yahoo Chart HTTP；兼容 downloader 仍延迟导入 yfinance，模块导入和基础测试不要求公开行情依赖；
- 兼容 yfinance downloader 使用能抛出上游错误的单标的 history 路径；批量 download 吞错返回的空表不能作为生产失败判断依据；
- 兼容 yfinance 调用参数显式锁定 `auto_adjust=False / multi_level_index=False / threads=False / progress=False`；
- 只支持 `1d + adjusted=none`，请求区间按交易所本地 trading date 解释；
- 空数据返回 `empty`，缺依赖、超时、限流和 schema 变化返回结构化失败；
- adapter 兼容上游意外返回 MultiIndex 列，但不把 vendor symbol 当业务 code；
- 本切片没有启用 QQ 推送；只有用户主动查询或显式 readiness smoke 才访问公开行情。
- 2026-07-13 的真实 smoke 已由 Yahoo Chart HTTP 成功返回 SP500 三条日线；失败场景仍返回结构化 `unavailable`，没有产生 fixture/Mock 数据。

建议提交：

~~~text
feat(finance): add yahoo public index series adapter
~~~

### F7d2：Yahoo 延迟快照与 TTL cache

状态：已完成；当前快照明确是最近已完成日线观察，不冒充盘中实时行情；尚未注册进生产 factory。

目标：在不冒充实时数据的前提下补 quote snapshot，并保护公开上游。

实际落地：

- `TTLMarketDataCache` 是线程安全、有界 LRU/TTL cache，支持成功 TTL、失败负缓存、过期清理、显式 delete/clear 和 deterministic clock；
- series cache key 包含 adapter、canonical code、capability、interval、adjusted、日期区间和 limit；
- cache 命中直接返回原不可变 response，保留原始 `as_of/fetched_at`，不制造新时间；
- Yahoo series 默认缓存 900 秒、quote 60 秒、失败/空数据 15 秒，均可在 adapter 构造时覆盖；
- `MarketQuoteSnapshot` 向后兼容增加 `trading_date/time_semantics`；
- Yahoo quote 使用最近“严格早于抓取本地日期”的已完成日线，主动排除同日可能仍在形成的 partial bar；
- quote 固定 `status=end_of_day`、`delay_kind=end_of_day`、`reason=latest_completed_daily_bar`；
- 没有可信已完成交易日时返回 `unavailable / observation_time_unavailable`；
- 多代码请求任一失败时整次 fail closed，不返回部分结果；
- 当前未实现 Yahoo 盘中 quote，不因切片名中的“延迟快照”声称交易级或盘中实时能力；
- 本切片没有修改 factory/config，没有启用默认联网或 QQ 推送。

建议提交：

~~~text
feat(finance): cache delayed public market quotes
~~~

### F7d3：AkShare 境内 ETF adapter

状态：已完成 adapter 与离线契约；显式 live smoke 中 513000 快照成功，history 上游本次 ConnectionError 并正确降级；尚未注册进生产 factory。

目标：增加境内 ETF 快照和日线，保留代理信号限制。

实际落地：

- `AkShareETFAdapter` 只接受 registry 中 route 为 `akshare_etf` 的 canonical code；
- 默认 loader 延迟导入 AkShare，不影响基础安装和普通 Akane 启动；
- history 固定 `fund_etf_hist_em / daily / adjust=""`，按受测中文列名规范化；
- spot 固定 `fund_etf_spot_em`，只按精确 vendor code 取唯一行；
- spot `as_of` 来自 timezone-aware `更新时间`，`fetched_at` 仅记录抓取时间；
- spot/history 的成交量均从“手”乘 100 转为份额，成交额保持 CNY；
- provenance 明确写 `AkShare/Eastmoney public web data`、`data_quality=aggregated`；
- quote 的 delay 保持 `unknown`，不因函数名包含“实时”宣称零延迟；
- 多代码任一缺失时整次返回 empty，不返回不完整成功；
- schema、重复代码、非法 OHLC、负成交量/成交额、超时和限流均结构化失败；
- 复用共享 TTL cache 和失败负缓存；
- live smoke：513000 快照成功，返回 2026-07-10 的真实观察时间与 114,530,800 份规范化成交量；同轮 history 遇到上游 `ConnectionError`，返回 `unavailable`，没有使用 fixture 或 Mock 顶替。

建议提交：

~~~text
feat(finance): add public etf market adapter
~~~

### F7d4：`public_market` composite 注册与配置

状态：已完成；生产 registry 已注册，默认配置仍为 `disabled`，不会因安装依赖自动联网。

目标：完成 Engine、config、settings catalog 和生产 registry 接线，默认仍关闭。

实际落地：

- `PublicMarketProvider` 完整实现 `MarketDataProvider`，声明 `quote_snapshot / price_series / security_master`；
- `news_search` 明确返回 `unavailable`，不伪造新闻能力；
- series 按 canonical instrument route 分发到 Yahoo 或 AkShare；
- mixed quote 按 route 分组并按原请求顺序重组，任一路失败时不返回部分数据；
- route 被配置关闭时返回 `unavailable / public_route_disabled:<route>`；
- health 只检查可选包是否存在，不发起网络请求；全部可用为 ready，部分为 degraded，全部缺失为 disconnected；
- Yahoo/AkShare 共享同一 instrument registry 和有界 TTL cache；
- `MarketDataProviderSettings`、`config.py`、settings catalog 和 Engine 已接全部公开行情开关/TTL；
- production registry 固定为 `disabled / emquant / public_market`，仍不包含 Mock；
- Engine 构造 public_market 时不会立即导入 yfinance/AkShare，也不会请求网络；
- public instrument registry 会在 Engine 构造工具服务时幂等写入现有 `MarketEventStore` security master；
- `market_resolve_security` 现在可把“日经225”“标普500”“日经ETF华夏”等精确别名解析为可信 canonical code；
- seed 不写 vendor symbol 别名，`^N225` 和纯 vendor code 仍不能绕过 canonical code 边界；
- Engine 使用通用可选 `seed_security_master` 钩子，没有新增 public_market 类型特判；seed 失败会记录 provider/reason 类型但不拖垮普通聊天；
- Yahoo 与 AkShare loader 对 timeout、连接重置/中断、SSL/certificate/curl transport 错误和明确 HTTP 5xx 最多执行 3 次总尝试，并使用有界短退避；
- 缺依赖、限流、HTTP 4xx、参数错误、空数据和 schema 错误不重试；最终失败仍进入原有短 TTL 负缓存；
- 默认 `FINANCE_MARKET_PROVIDER=disabled`，默认启动行为保持不变。

建议提交：

~~~text
feat(finance): register public market provider
~~~

### F7d5：指数、ETF、FX 联合分析

只有找到可靠且时间语义清楚的 FX 数据源后才开始。该切片用于解释指数、人民币 ETF 和汇率之间的差异，不属于 F7d0-F7d4 完成条件。

新闻/公告 polling 另立切片，不与 F7d5 混做。

## 21. 完成定义

F7d0-F7d4 完成必须同时满足：

1. 默认安装和默认配置不联网；
2. 安装公开行情可选依赖并选择 `public_market` 后，日经、标普、纳指综合、恒生至少能查询日线；
3. 至少一只已复核境内日经 ETF 能查询快照和日线；
4. 返回 canonical code，不把 `^N225` 暴露为业务代码；
5. 每个结果有实际 source、as_of、fetched_at、timezone、currency、delay 和 adjustment；
6. 日线按 trading date 解释，不制造盘中时间；
7. 日经指数和 ETF 在类型、价格、来源和解释上严格分离；
8. 现有确定性图表和金融报告可直接消费结果；
9. 网络、限流、缺依赖、空数据和 schema 变化均结构化失败；
10. production registry 不含 Mock，也没有任何静默 Mock fallback；
11. 单元测试完全离线；
12. 网络 smoke 显式启用且只做小流量只读查询；
13. 基础 Akane 回归不因可选依赖缺失而失败；
14. 文档、日志和 commit 不含密钥、cookie、数据库、SDK、本地路径或原始大响应。

达到这些条件，免费行情地基才算可用于给群主演示。它提供的是可核验的市场数据和分析能力，不是交易级实时行情承诺。

### 21.1 2026-07-11 真实公开源 smoke 记录

本地按 `requirements-finance-public.txt` 安装锁定版本后，在事件 worker、QQ push 和默认 provider 开关均不改变的前提下执行只读查询：

- provider health 为 `ready`，Yahoo 与 AkShare 两个可选依赖均可导入；
- Yahoo `NIKKEI225.INDEX` 曾成功返回 20 个日线观察，最后交易日为 `2026-07-10`，source 为 `Yahoo Finance`，时区为 `Asia/Tokyo`，币种为 `JPY`，delay 为 `end_of_day`；
- AkShare `513000.SH` 快照成功，最后价 `2.403`，交易日为 `2026-07-10`，source 为 `AkShare/Eastmoney public web data`；
- AkShare `513000.SH` history 曾被远端断开：异常链为 `ConnectionError -> ProtocolError -> RemoteDisconnected`；当前 adapter 对同类瞬态传输错误最多执行 3 次总尝试，随后仍会返回结构化 `unavailable`，不会伪造行情；
- Yahoo 在后续重复 smoke 中出现 `upstream_timeout:yahoo`，即使只为本次 smoke 把单次 timeout 提高到 30 秒仍可能失败，说明当前免费上游可达性确有波动；
- 行情失败时 `render_market_chart` 返回结构化 `unavailable`，没有登记空 PNG；报告链也不会在缺少可信图表/series 时伪造成功；
- security master 的“日经225”解析成功，得到 `NIKKEI225.INDEX`；ETF partial candidates 不会覆盖唯一 exact index match。

因此当前已经验证“真实成功路径的行情规范化”和“真实失败路径的重试、负缓存、结构化降级”。尚未完成的是同一次稳定网络窗口中的真实 series → PNG → 报告 → QQ 发送验收；不能把上游超时写成产品成功。

### 21.2 2026-07-11 QQ 私聊/群聊一致性加固

- 公开 market Provider、security master、native 金融工具清单在 QQ 私聊和群聊使用同一实例/同一基础能力；群聊仍保留 Actor、记忆、watchlist、附件和生成物的必要会话隔离。
- `market_resolve_security` 支持从自然任务句中识别唯一登记别名；例如“画一张日经225最近三个月K线”解析为 `NIKKEI225.INDEX`。
- 如果模型错误地把用户原文改写为 `^N225` 等未登记 vendor symbol，解析工具会回看当前用户消息；仅当消息中存在唯一可信别名时恢复，多个标的仍返回歧义，不共享其他会话的临时代码信任。
- `not_found` 只表示本次查询词未匹配，不能再被解释成 Provider 不支持；真正的能力缺失必须由 `provider_capabilities` 或结构化 `unavailable` 证明。
- `invalid_arguments` 不再立即终止全部工具轮；模型可以在同一轮解析代码、修正参数并继续报告/图表任务，避免口头承诺“下一条消息开始”后停住。
- public instrument registry 新增 `CSI300.INDEX`，Yahoo 路由为 `000300.SS`，支持“沪深300 / CSI300 / 000300”等可信别名，用于明确标注代理口径的 A 股大盘对比。
- `513000` 与 `000300` 现在可分别规范化为 `513000.SH` 与 `CSI300.INDEX`；这只解决代码与数据路由，不允许模型在用户没有明确市场/基准时静默替用户决定报告对象。
- QQ 图表/报告产物轮会抑制普通情绪立绘，避免角色图片与真实行情图混淆。

仍可能发生但不属于私聊/群聊能力漂移的是免费上游瞬时超时：同一 Provider 在不同时间请求可能一成一败。系统必须准确报告 `upstream_timeout`，不得写成“群聊不支持”或“security master 没有”。

### 21.3 2026-07-11 免费行情主动推送质量门禁

- 新增独立 `FinancePublicQuoteEventSource`，不把 `PublicMarketProvider` 假装成通用新闻 callback Provider；它只读取已启用 push subscription 的 `public_market` watchlist。
- Yahoo 路由只生成 `daily_close`，并明确称为“最近完成交易日收盘”；不会生成或宣称盘中实时事件。
- AkShare ETF 路由只在 `time_semantics=instant`、来源 provenance 完整、观察时间与当前时间同属一个 A 股交易日且都处于 `09:30-11:30 / 13:00-15:00` 连续交易时段、数据时间未过期时生成 `quote_move` 候选。
- 每条候选必须通过 provider/code 精确匹配、时区、as_of、fetched_at、正价格、非负成交量/额、OHLC 内部一致、程序重算 change/change_pct 一致、无时间回退和异常涨跌上限检查。
- 第一次观察只建立候选基线；至少两个不同 `fetched_at` 的一致观察后才确认。新收盘日线或新盘中涨跌档位同样需要二次确认，宁可漏推，不用单次脏数据触发群消息。
- 基线、候选、确认次数、最近抓取时间和上次事件档位写入 SQLite；重启后继续确认，不会因进程重启把旧数据当新事件。
- 被拒绝的数据只保存 reason、stage 和 payload SHA-256，五分钟内相同拒绝会合并，保留 30 天诊断窗口，不把完整上游响应写入事件或模型上下文。
- `quote_move` 使用 1/2/3/5/8/10% 确定性档位去重；相同交易日、方向和档位不会重复生成事件。3%/5% 标签只影响确定性重要度，不由模型自由判断。
- 行情事件标题由程序生成，包含 canonical code、价格、昨收、涨跌幅和数据时间。模型只解释影响、风险与观察项；最终 QQ 文本始终重新注入该权威事实，新闻冲突时必须放弃新闻推断，不能覆盖行情事实。
- `market_price_series`、图表和报告的公开 schema 已收紧为当前 Provider 真正支持的 `1d + none`；不再向模型承诺尚未实现的周/月聚合或前后复权。
- `finance_tool_completed` 事件现在携带结构化 `reason`；Yahoo/AkShare 对 SSL/certificate/curl 瞬态错误最多尝试 3 次，便于区分网络波动与参数/数据质量错误。
- 生产开关仍保持关闭：`FINANCE_EVENT_INGESTION_ENABLED=false`、`QQ_FINANCE_PUSH_ENABLED=false`。只有离线测试、真实只读干跑和单群受控验收全部通过后才允许打开。
- 本轮真实只读干跑中，`513000.SH` 快照与 `NIKKEI225.INDEX` 五日线均成功；随后把休市后的真实 `513000.SH` 快照送入最终事件源，结果为 `events=0 / rejection_reason=instant_not_current_trading_date`，证明“工具能查到旧快照”不会被误转成盘中主动推送。

### 21.4 2026-07-11 东方财富 7×24 免费新闻主动推送

状态：代码、离线测试与真实只读 smoke 已完成；生产消费和 QQ 推送总开关仍关闭，等待单群受控验收。

数据入口与边界：

- `EastmoneyFastNewsAdapter` 只读访问东方财富 7×24 页面使用的公开列表接口 `https://np-weblist.eastmoney.com/comm/web/getFastNewsList`，来源页固定为 `https://kuaixun.eastmoney.com/7_24.html`；
- adapter 负责超时、有界瞬时重试、熔断、schema 校验、时间解析、正文链接构造和规范化 `PublicNewsItem`，不直接知道 QQ、订阅或模型；
- 默认每 15 秒允许一次源级抓取。首次运行只把当前最多 100 条建立为持久化 baseline，绝不把启动前历史快讯一次性刷进群；seen ID、latest published time 和 last poll time 写入 SQLite，重启后继续去重；
- 新闻发布时间必须不晚于当前时间容差且不早于最大新鲜度窗口；旧闻、未来时间、schema 变化和上游失败都不会伪造成新事件；
- 标题命中 security master/watchlist 唯一可信别名时生成 `security_matched` 事件，否则进入 `GLOBAL.MARKET / market_wide`，供允许市场级快讯的订阅消费；
- 同一规范化事件未来可被 `market_news_search` 从 `MarketEventStore` 查询。当前没有把抓取器直接暴露为任意模型网页工具，但保留了后续 model-facing news adapter 的接口边界。

转发审核采用两层 fail-closed 门禁：

1. 本地确定性策略先拦截 `习近平 / 总书记 / 中共中央 / 党中央 / 中央政治局 / 政治局常委 / 中央军委` 等明确国内敏感主体；裸 `中央` 默认同样拦截，但 `中央银行 / 欧洲中央银行 / 中央气象台 / 中央结算 / 中央国债登记结算` 等明确非政治短语可通过；
2. 通过本地门禁后，再由结构化 LLM 审核器批量输出逐条 `allow|block`。缺失决定、非法 JSON、拒绝、异常、隐晦指代或不确定都按 block 处理；特朗普、高市早苗等外国政治人物新闻本身允许，但同条同时涉及被拦截的国内主体仍 block。

审核基础设施故障与内容判定分开处理：结构化审核单轮最多调用两次；若仍为超时、上游异常、JSON fallback、缺失决定等 retryable failure，本轮继续 fail closed，不发送消息，但不会把新闻写入 `seen_item_ids`。SQLite 会保存 `moderation_attempts`，下一次轮询重新审核；明确 `block`、成功 `allow` 或跨轮重试耗尽后才终结。错误审计只记录脱敏的异常类型和尝试次数，不把密钥或完整响应写入事件库。

新闻标题与摘要可能远长于证券主数据查询上限。security alias 解析只使用有界标题查询，watchlist 嵌入匹配仍可查看完整规范化文本，避免长摘要触发 `MarketDataValidationError` 并拖掉整批新闻。Composite source 的部分失败原因会保留脱敏的来源类名与异常类型，不再只留下无法定位的 `partial_event_source_failure`。

允许后的 QQ 内容按分析开关选择一种主形态：

- 开启分析时，只发送简短时间头、Akane 的自然语言事实转述/补充核验/分析推断，以及文末原文链接；不再前置粘贴整段东方财富原文/摘要，也不附来源名称、ISO 发布时间、五段式标签、通用免责声明或“接下来观察”等无信息量模板。模型可调用只读工具；若链接漏写、重复或只在中间写出，后处理会清理并在结尾只保留一次；
- 新闻分析采用引导式证据纪律而非固定模板：模型按内容自主选择确认程度、传导机制、直接受影响资产、成立条件和反向情形；遇到“据悉/商讨/拟议”优先核验官方或第二独立来源。具体比例、价格、行情状态、开收市时间与官方确认状态必须由事件字段或本轮工具结果支持，否则省略精确数字或改为条件性表述；不强行把每条全球新闻映射到 A 股；
- `FINANCE_PUBLIC_NEWS_MODEL_ANALYSIS_ENABLED=false` 时完全不调用模型，立即只生成原文转发；
- 模型异常、空回复、进度占位或拒绝最多按现有分析策略重试，全部失败后状态为 `relayed_without_analysis`，回退为程序生成的原文/摘要转发，固定含来源、发布时间、原文 URL 和“来源发布不等于事项已获官方确认”的说明，不发送拒绝话术；
- 模型分析若重新引入本地禁止的国内敏感主体，整段分析作废并回退原文，不通过同义改写规避内容审核；
- 原文进入事件前已经通过两层审核。审核失败的内容不会因为“模型可以改写”而获得转发资格。

新增配置：

~~~dotenv
FINANCE_PUBLIC_NEWS_ENABLED=true
FINANCE_PUBLIC_NEWS_POLL_INTERVAL_SECONDS=15
FINANCE_PUBLIC_NEWS_TIMEOUT_SECONDS=6
FINANCE_PUBLIC_NEWS_REQUIRE_LLM_MODERATION=true
FINANCE_PUBLIC_NEWS_MODEL_ANALYSIS_ENABLED=true
~~~

这些局部能力开关不等于授权发送。真正开始轮询并向 QQ 发消息仍同时要求：

~~~dotenv
FINANCE_ASSISTANT_ENABLED=true
FINANCE_MARKET_PROVIDER=public_market
FINANCE_EVENT_INGESTION_ENABLED=true
QQ_BRIDGE_ENABLED=true
QQ_FINANCE_PUSH_ENABLED=true
~~~

真实只读 smoke：

- 东方财富 adapter 成功返回 5 条最新快讯，最新项带真实发布时间和 `finance.eastmoney.com/a/...html` 原文链接；
- 首次事件源抓取读取 100 条并得到 `events=0 / ignored=100 / baseline_seeded=true`，证明不会启动洪水；
- 当前配置的 LLM 审核对只涉及特朗普的样本返回 allow，对涉及中共中央政治局的样本返回 block；
- 两个全局推送开关继续保持 false，尚未向真实群发送上述 smoke 数据。

### 21.5 2026-07-13 金融推送 prompt/cache 专项修复

真实运行中发现，新闻分析虽然标记为 transient turn，但仍通过普通聊天完整 prompt 构建器读取同一 QQ 群的 memcore 可见层。连续主动推送会把历史快讯再次注入下一条推送，单次 prompt 最终增长到约 13.5 万 tokens；当 JSON/上游失败时，内外两层重试还会重复提交该大 prompt，最后才回退原文。

现改为独立 `finance_push` prompt scope：

- `FinanceAnalysisRequest.to_turn_payload()` 固定携带 `prompt_scope=finance_push` 和 `pre_retrieval_enabled=false`；
- 稳定财经规则进入可缓存 system extra，单条事件与本次 importance 参数留在动态尾部；
- 主动推送不注入普通聊天 raw/summary/semantic、桌宠养成、关系、视觉、礼物、附件、生成文件和任务工作区；
- 不删除历史记忆；需要旧观点、风险偏好或时间线时，模型仍可自主调用只读记忆工具；
- 保留 finance domain 原生工具选择与并行调用，不新增按关键词硬路由；
- finance push 内层 JSON 修复只运行一次，外层证据校验与重试最多三次；
- 模型尝试耗尽仍按既有产品约定发送原文，但 delivery part 会保存脱敏的分析状态、尝试次数和原因，便于定位；
- 专用 prompt audit key 为 `chat:finance_push`。运行指标新增 provider 上报的 input/output token 累计值，可与 cache read/create 一起核算真实成本。

离线最终构建验收：主 system 约 1292 tokens，稳定财经/domain system extra 约 2497 tokens，动态 user 约 541 tokens；raw、summary、semantic 和 retrieval 均未进入 prompt。finance push 在原生 tools 可用时不再重复渲染 legacy 工具说明。该改动只改变金融主动推送的提示词投影，不改变普通 QQ 金融问答和陪伴聊天的记忆可见性。

真实 PinAI 验证：连续四次 finance push 的 main system 与 system extra hash 完全一致，均在 5 分钟内，但 provider usage 仍为 cache creation、`cache_read=0`。因此 Akane 侧不再继续扩大 TTL 或填充 prompt；后续若要改善代理缓存，需要确认 PinAI 是否能为同一 key 提供稳定 upstream account/workspace 路由。

## 22. 上下文恢复后的精确下一步

若接手者看到本文，F7d0-F7d4、名称解析 bootstrap、三次瞬时网络重试、免费行情质量门禁和东方财富 7×24 新闻源已经完成。下一步做显式本地验收，不要直接打开生产推送：

1. `git status --short --branch`，确认不碰用户的 `uv.lock`；
2. 安装 `requirements-finance-public.txt`，但只在本地测试环境设置 `FINANCE_MARKET_PROVIDER=public_market`；
3. 保持 `FINANCE_ASSISTANT_ENABLED=true`、主动事件消费和 QQ push 关闭，先走用户主动查询；
4. 查询 provider health，并先用“日经225”“日经ETF华夏”验证 security master exact resolution；
5. 先复测 `NIKKEI225.INDEX` 日线和 `513000.SH` 日线；513000 快照已真实成功，不必反复高频拉取；
6. Yahoo 或 AkShare 网络失败时保留结构化 status/reason，不继续提高三次重试上限、不改成 Mock；
7. 在任一真实 series 成功的同一 provider/cache 生命周期内立即执行确定性 PNG 和一份最小金融报告 smoke，避免第二次网络抖动；
8. 再走真实 QQ 主动查询“日经225最近走势”，验证文字、图片/报告投递、source 和 as_of；
9. 用隔离测试数据库给 `513000.SH` 建立关注项，手动连续运行 public quote source，确认第一次只建基线、第二次确认、脏数据进入 rejection、相同档位不重复出事件；此时仍不发送 QQ；
10. 使用隔离数据库先运行新闻源首轮 baseline，再注入一条允许的外国财经快讯、一条国内敏感快讯和一条审核器故障样本；只允许第一条形成事件；
11. 分别测试“仅转述分析+文末链接”“关闭分析只发原文”“分析拒绝回退原文”和“分析漏 URL 自动补到结尾”；任何 blocked 新闻都不得进入分析客户端；
12. 检查生成事件标题、source、published_at、原文 URL、importance 和 delivery reservation 后，只对测试群开启一次受控 QQ 推送；若任一字段不可信立即恢复两个 false 开关；
13. 验证完成后恢复默认 disabled，再按产品优先级选择 F7d5、F9c，或“全球指数/财经快讯第二公开源”。

Yahoo live smoke 失败不得改成假成功；后续网络恢复时再补成功观察。Choice 继续保持可选，现有金融主链不受影响。
