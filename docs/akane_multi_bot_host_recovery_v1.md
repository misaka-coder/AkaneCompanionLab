# Akane 单 Host 多 Bot 收敛探查与实施报告 v1

> 状态：产品与实施边界已冻结；Slice 0、Slice 1、Slice 2A、Slice 2B-core、Slice 2C、Slice 3A、Slice 3B、Slice 4A、Slice 4B 云端 Host 切换已完成；真实群聊与媒体表现验收待用户消息
>
> 建档日期：2026-07-19
>
> 目标：把当前“一个进程只能运行一个 QQ Bot、实例配置各自漂移、本地设备只绑定一个实例”的产品形态，收敛为“一个 Akane Host 同时运行多个 Bot；新增 Bot 只增加配置；所有 Bot 默认继承同一套 Akane Core 能力；金融、养成、模型等只作为可配置差异”。
>
> 本文是后续实现的权威续接文档。上下文压缩或更换执行会话后，应先读本文，再按切片继续，不需要重新从头调查 personal/finance 的核心链路。

---

## 0. 执行摘要

### 0.1 最终判断

项目不需要推倒重开。

调查确认：

1. personal 普通对话与启用金融插件的 Bot 普通对话，使用同一个 `AkaneMemoryEngine.process_turn()`、同一个 `PromptBuilder`、同一个 `LLMRuntime`、同一个 MemCore 集成与同一个工具循环。
2. 金融能力已经位于独立私有插件仓库，宿主侧旧金融行情实现已经删除，并有测试防止恢复。
3. `finance_mode` 进入 Engine 后会被丢弃；`DomainProfileRegistry` 当前只返回 `default`，没有一套仍在生效的“金融对话引擎”。
4. 金融主动推送使用独立的 `plugin_proactive` prompt/cache scope，但仍通过正常 Akane 引擎、正常工具、正常 MemCore 运行。它是事件类型差异，不是第二套 Bot 逻辑。
5. 当前 personal/finance 的能力差异主要来自运行配置漂移：模型、协议、上下文上限、压缩线、视觉配置、Satellite 配置和插件开关不同。
6. 当前真正错误的产品边界集中在启动装配、全局配置、QQ 单绑定、Satellite 单实例绑定和缺少 Bot 管理面，不在核心对话算法。

因此，实施目标不是重写 Akane，而是：

```text
把 app.py 的单例启动装配
        ↓
抽成可重复创建的 BotRuntime
        ↓
由一个 BotRegistry 在同一 Host 进程内管理多个 BotRuntime
```

每个 `BotRuntime` 使用同一个类和同一套代码，但保留独立的数据根、QQ 好友/群聊状态、MemCore、插件状态与少量配置覆盖。

### 0.2 冻结的产品决定

以下决定已经由用户明确，不应在实施中再次反复讨论或改回实例产品化路线：

1. **Bot 是配置记录，不是代码分支。**
2. **一个 Akane Host 默认同时运行多个 QQ Bot。**
3. **新增 Bot 的正常操作仅是注册 QQ 账号、填写凭据、选择配置并启动，不修改 Python/Rust/JavaScript 能力代码。**
4. **所有 Bot 默认继承完全相同的 Akane Core 能力。**
5. **金融是普通可挂载插件；任何 Bot 都能开启或关闭。**
6. **养成/Care 是每 Bot 可配置开关，不是产品类型。**
7. **模型/API Key 可以继承 Host 默认，也可以按 Bot 覆盖。**
8. **每个 Bot 有独立好友、群聊、短期会话、记忆根与插件数据。**
9. **一台本地电脑只运行一套 executor，并可服务 Host 下多个被授权 Bot。**
10. **桌宠不是新的 QQ Bot；它是默认 Akane Profile 的本地频道。**
11. **桌宠默认与默认 Bot 共享人格、养成和长期记忆，但使用独立短期 session。**
12. **现有 personal/finance 数据不做危险的原地合库；先原样挂载为两个 BotRuntime，以保证回滚。**

### 0.3 最终产品验收句

完成后必须满足：

> 在控制中心增加第三个 QQ Bot，只新增 Bot 配置与凭据；不修改任何能力实现代码。第三个 Bot 启动后默认拥有与其他 Bot 相同的看图、语音、文件、工具、桌面和插件宿主能力，仅其好友、群聊、记忆、角色和显式覆盖配置不同。

---

## 1. 当前代码事实

### 1.1 金融不是第二套 Akane

金融插件仓库的工程边界明确声明：

```text
This repository is the private finance plugin for Akane, not a second Akane product.
```

对应文件：

- `../private/AkaneFinancePlugin/AGENTS.md`
- `../private/AkaneFinancePlugin/src/akane_finance_plugin/plugin.py`
- `../private/AkaneFinancePlugin/src/akane_finance_plugin/analysis.py`

插件通过宿主公开端口注册：

- `PublicMarketCapabilityAdapter`
- 插件 QQ 指令
- 插件独立 storage
- 主动新闻 job
- `PluginReasoningPort`
- notification port
- managed artifact port

主动分析没有创建自己的模型客户端或第二个 Engine。`FinanceNewsAnalysisClient` 构造 `PluginReasoningRequest`，宿主的 `EnginePluginReasoningPort` 再调用正常 `engine.process_turn()`。

宿主侧验证：

- `tests/test_finance_plugin_absence.py::FinancePluginAbsenceTests`
  - 金融插件关闭时普通工具不变。
  - 金融插件缺失/启动失败时宿主结构化降级，不删除普通工具。
- `tests/test_finance_plugin_absence.py::CoreFinanceCutoverTests`
  - `companion_v01/finance/` 和旧 market data 权威实现已删除。
  - Engine 不再构造 legacy finance runtime。

### 1.2 personal 与 finance 普通对话使用同一主链

核心入口：

- `companion_v01/engine.py::AkaneMemoryEngine.process_turn`
- `companion_v01/engine.py::AkaneMemoryEngine.process_turn_stream`
- `companion_v01/engine_services/response_builder.py::prepare_context`
- `companion_v01/prompt_builder.py::PromptBuilder.build_final_generation_context`
- `companion_v01/llm_runtime.py::LLMRuntime`

当前行为：

- `payload.pop("finance_mode", None)`：旧 finance mode 不参与主链选择。
- `DomainProfileRegistry.get()` 无论输入什么都返回唯一 `default`。
- 普通对话 cache family 为 `chat:final`。
- 插件主动事件 cache family 为 `chat:plugin_proactive`。
- 两种 family 使用同一个 `_final_prompt_cache_key()` 算法。

### 1.3 金融主动推送不是旁路记忆

主动插件分析 payload 设置：

```text
turn_kind = plugin_proactive
client_mode = qq_text
```

它仍会：

- 进入 `engine.process_turn()`；
- 写正常 user/assistant turn；
- 使用同一 MemCore 可见 raw/episodic/semantic 层；
- 使用正常 Akane 人格、Care、关系、工作台、视觉和工具模块；
- 记录工具交换；
- 通过稳定 source id 做幂等。

对应测试：

- `tests/test_plugin_reasoning.py`
- `tests/test_memcore_integration.py::test_plugin_proactive_prompt_context_uses_normal_visible_memory_layers`
- `tests/test_memcore_integration.py::test_plugin_proactive_scope_keeps_normal_akane_modules_enabled`
- `tests/test_prompt_builder.py::test_plugin_proactive_scope_uses_stable_system_and_appends_memory_timeline_once`

保留 `plugin_proactive` scope 是合理的：主动外部事件需要一段稳定的金融研究原则，而且不应把主动推送与普通用户聊天误当成同一 prompt cache 路由。但这只是同一引擎内的事件 scope，不得再次演变成 finance Bot 产品分支。

### 1.4 当前云端差异来自配置与进程版本漂移

2026-07-19 安全审计只读取非密钥配置，得到：

| 项目 | personal | finance |
|---|---|---|
| 部署代码文件 | 同一份部署目录，核心文件 hash 相同 | 同一份部署目录，核心文件 hash 相同 |
| 当前进程版本 | 最近重启 | 较早启动，可能仍保留旧内存代码 |
| Chat 模型 | `gpt-5.6-sol` | `gpt-5.6-luna` |
| 协议 | Responses | OpenAI Chat Completions |
| Context window | 已配置 | 未配置 |
| 自动压缩线 | 已配置 | 未配置 |
| Cache force | false | true |
| Cache namespace | personal 独立值 | finance 独立值 |
| Vision | 已配置 | 未配置 |
| Satellite | 已配置 | 未配置 |
| finance plugin | 关闭 | 开启 |

结论：

- 当前磁盘代码没有 personal/finance fork。
- finance 进程未同步重启会造成临时运行版本漂移，后续 Host 迁移必须统一生命周期。
- 两套配置差异会直接造成模型表现、缓存、工具可见性和能力差异。
- 配置不同本身不是问题；问题是没有“共享默认 + Bot 覆盖”，导致每个 Bot 都必须手工重复配置并容易漏项。

### 1.5 当前真实 cache 审计说明

近期安全 prompt audit 只记录长度/hash/usage，不记录正文。观察到：

- finance 的近期 native tool schema 基本稳定在 14 个工具。
- personal 的近期 native tool count 在 10、12、13、14 之间变化。
- personal 的变化与 Satellite 在线状态、provider readiness、模型 override 等有关。
- 工具 schema 变化会改变 `tool_prompt_context_hash`、native schema hash 和 cache key，降低真实缓存连续性。
- finance 与 personal 使用不同模型和协议，缓存比例不能直接横向比较。

多 Bot 收敛后应保证：

- 相同 Host、相同插件组合、相同设备在线状态下，各 Bot 的基础工具集合一致。
- finance 插件只增加 finance descriptor，不删除基础工具。
- Host 级设备上线/下线对所有被授权 Bot 一致生效，避免单 Bot 工具 schema 漂移。
- cache namespace 仍按 Bot/安全域隔离，但使用同一个生成算法和继承配置。

---

## 2. 当前单 Bot 绑定点代码地图

### 2.0 Slice 2C 已落地：语音能力读取 Bot 快照

Slice 2C 没有复制 personal/finance 语音代码，也没有切换全局 `config`。现在的链路是：

```text
BotRuntimeFactory
  └─ BotSettingsView（TTS / ASR / GPT-SoVITS / QQ voice）
       ├─ EdgeTTSClient
       ├─ /asr 与 /tts
       ├─ /pet/turn
       ├─ QQ voice delivery
       └─ capabilities provider tts-test / catalog
```

已纳入快照的配置包括：

- Edge TTS 音色、语速、音量、音高与流式开关；
- GPT-SoVITS 超时、语言、媒体类型、流式/并行/分桶/批大小、语速系数、片段间隔、切分方式；
- OpenAI-compatible ASR 超时/模型，以及 faster-whisper 模型、设备、计算类型、语言、VAD、缓存目录和上传上限；
- QQ TTS profile、最大合成文本长度、最大 segment 数。

路由仍保留 `config_module` 兼容回退，因此现有直接构造路由的测试/旧集成不需要改；真实 `app.py` 装配已经显式传入 `bot_runtime.settings`。双快照测试已验证 GPT-SoVITS 参数和 QQ 语音长度限制不会串线。

本切片尚未处理：控制中心 Bot 管理 UI、Host 级 QQ dispatcher、TTS/ASR 热更新和多 Bot 生命周期（Slice 3）。

### 2.0.1 Slice 3A 已落地：BotConfig 与 Host 生命周期基础

Slice 3A 新增 canonical `BotConfig` / `BotHostProfile`，配置只允许安全 id、显示名、继承 profile 引用、Care、QQ profile 引用和插件选择；API Key、token、绝对数据路径等未知字段会 fail-closed。数据根由 Host 根与 `memory_space_id` 计算，配置本身不保存路径。

现有 `InstanceManifest` 只通过 `bot_config_from_instance_context()` 进入兼容适配；canonical 路径则由 `instance_context_from_bot_config()` 投影给尚未迁移的 Engine/PluginHost 入口，避免出现两套并行产品配置。

`BotRegistry` 现在拥有：

- `registered → starting → online/degraded → stopping → stopped` 状态；
- 重复 Bot id 和重复数据根拒绝；
- `start_all()` 启动失败隔离；
- `stop_all()` 逐 Bot 有界关闭；
- 不包含路径、异常正文、token 的安全公开快照。

canonical `BotRuntimeFactory.create(bot_config=...)` 使用 per-Bot `RuntimeConfigView` 加载保存的 runtime overrides，不再写回进程全局 `config`；旧单实例兼容入口暂时保留原有 replay 行为。自动测试已用真实 factory 在同一进程构造、启动和关闭三个独立数据根的 `BotRuntime`，并验证三份 settings override 不串线。

`app.py` 的 startup/shutdown 已改由 `BotRegistry.start_all()/stop_all()` 统一拥有。

### 2.0.2 Slice 3B 已落地：bots.toml 进入真实 Host bootstrap

Host 启动现在按以下兼容顺序执行：

```text
<AKANE_DATA_ROOT>/bots.toml 存在
  → 校验 BotHostProfile
  → enabled Bot 按 memory_space_id 解析到 Host-owned data root
  → BotRuntimeFactory.create(bot_config=...)
  → BotRegistry 批量注册

bots.toml 不存在
  → 原 AKANE_INSTANCE_ID / 单 Bot 启动路径保持不变
```

一个非默认 Bot 构造失败会在 Registry 中保留为 `degraded/unavailable`，不会中止其他 Bot；默认 Bot 构造失败时 Host fail-closed，因为当前 Web/桌宠 routes 必须有明确默认绑定。构造失败状态只保留安全 reason，不返回异常正文或数据路径。

`app.py` 的 Web、桌宠、voice、capabilities、model-service 和现有兼容 QQ route 现在显式使用 `host_bot_bootstrap.default_runtime` 及其 `RuntimeConfigView`，不再从“最后构造的 Bot”或共享全局配置隐式选取。示例配置位于 `deploy/bots.example.toml`。

真实测试已覆盖 `bots.toml → Host bootstrap → 三个真实 BotRuntime → 三个独立 data root/settings override → Registry start_all/stop_all`。Slice 3B 只完成生命周期与默认频道绑定；非默认 QQ Bot 的 canonical webhook 路由、secret/self_id 分发仍属于 Slice 4，不能把“runtime online”误报为“QQ 已接收消息”。

### 2.0.3 Slice 4A 已落地：多 QQ 账号身份、路由和唤醒词隔离

Host 现在从 `<AKANE_DATA_ROOT>/secrets/qq_profiles.toml` 按 BotConfig 的安全 `profile_ref` 选择 QQ 账号、OneBot endpoint、webhook secret 与 access token。secret 文件不进入 Bot 配置、公开 snapshot、repr 或 prompt；同一 Host 中两个 profile 不能绑定同一个 QQ 号。

真实 webhook 路由为：

```text
/api/bots/{bot_id}/qq/napcat/event
  → 目标 Bot 的 webhook secret
  → 目标 Bot 的 event self_id
  → 目标 BotRuntime 的 Engine / Gateway / OneBot token / PluginHost
```

旧 `/api/qq/napcat/event` 只作为默认 Bot 的薄别名，两条路径共享同一个 Gateway duplicate ledger；同一事件同时投递到新旧地址时只产生一次 Engine turn 和一次出站回复。非默认 Bot 不注册旧地址。

每个启用 QQ 的 Bot 现在有独立 `wake_words`。Host 配置会拒绝按真实 QQ 匹配边界发生重叠的唤醒词，例如 `Akane` 与 `Akane Finance`；`Akane` 不会误匹配账号名或普通文本中的 `Akane218`。因此同群两个 Bot 可以分别使用 `Akane`、`金融助手`，角色切换等命令只进入被唤醒 Bot。

本切片只能阻止同一新 Host 内串线，不能跨进程去重。若用户看到“一次消息先收到正常模型回复，随后又收到‘我在认真听你说……’兜底”，而新 Host 的单 Gateway 已证明只处理一次，首要排查项是：

1. 旧 personal/finance systemd 进程仍在运行；
2. NapCat 同时保留旧 webhook 与新 Host webhook；
3. 旧进程仍读取另一套模型/API 配置并在解析失败后发送人设兜底。

云端迁移必须先保存回滚点，再停旧进程、删除旧 webhook、只配置 bot-scoped endpoint，最后用带 `bot_id` 的安全日志核对一条事件只进入一个 Runtime。仅修改本地代码或重启其中一个 Bot 不能完成这一步。

2026-07-19 对当前云端进行了不读取 secret/正文的实机复核：QQ `2184046306` 对应 personal；它的 Chat/Aux 均为同一 PinAI Responses 模型并使用同一 API key，且不存在保存的 model-service 覆盖。personal 与 finance 的 Chat key 不同，因此“personal 偷用两把 API”不成立。服务器上另有一个因 unit 名末尾混入回车而形成的 `personal\x0d` 幽灵服务；它没有 MainPID、绑定错误 env、累计自动重启 8832 次，已被精确停止，正常 personal/finance 未中断。

“正常回复后追加人设兜底”另有一条同进程可复现根因：流式 JSON 的 `speech` 字段完整时，QQ 会在 `assistant_stage_decision` 先发送正常文本；若 JSON 尾部随后损坏，`LLMRuntime` 原来会把最终对象替换成人设 fallback，QQ 又把这个不同文本当作新增尾句发送。修复后：

- `LLMRuntime` 在完整对象解析失败时先从已完成的顶层流式字段恢复 speech/emotion/reply medium；
- 已发送有效流式文本且最终帧为 transient failure 时，QQ transport 不再追加通用 fallback；
- 空的待发送消息列表不会再调用 OneBot send API；
- 正常恢复的 speech 成为最终帧与记忆内容，不再把兜底误存为实际回复。

同日已完成云端单 Host 切换：

- 版本化 release 为 `6cd1527`；流式兜底修复 `b09a1c9` 已先单独上线旧双实例并验证两条 QQ 通道 connected；
- 新 `akane-host.service` 在一个进程内加载 `personal` 与 `finance` 两个 BotRuntime；旧 `akane@personal` / `akane@finance` 已禁用并保持 inactive；
- 旧数据根没有复制、合并或移动。首次使用 symlink 时被 `resolve_bot_data_root()` 的越界护栏正确拒绝并自动回滚；最终改用两个 systemd bind mount，把旧根映射为 Host-owned `bots/personal` 与 `bots/finance`；
- nginx 保留原 NapCat 入站地址，personal 转发默认兼容入口，finance 转发 `/api/bots/finance/qq/napcat/event`；NapCat 主机无需改配置；
- 两枚 webhook secret 已在服务器内部轮换，旧值失效；OneBot access token 未输出或改动；
- finance 保存独立 PinAI `gpt-5.6-luna` model-service 配置并开启 vision；personal 继续使用 Host 默认 PinAI `gpt-5.6-sol`；
- 每个 BotRuntime 从同一 Host cache 默认派生独立 `:bot:{bot_id}` namespace，缓存策略相同但安全域不串；
- 受控 Host restart 后，默认 `/health` 为 `personal / valid`，personal/finance bot-scoped QQ self-check 均为 `connected`，Host 同时持有两个不同 root lock，`NRestarts=0`，当前 startup error/degraded 计数为 0；
- 新 Host、两个 bind mount 已 enable；旧正常双 unit 与 malformed `personal\x0d` unit 均 disabled/inactive。回滚备份保留在服务器部署备份目录。

### 2.1 `app.py` 曾是单例根因

文件：`companion_v01/app.py`

模块导入时直接创建：

1. 一个 `InstanceContext`；
2. 一个 `InstanceRuntimeLease`；
3. 一个 `ModelServiceConfigStore`；
4. 一个 `SettingsOverrideStore`；
5. 一个 `DesktopSatelliteService`；
6. 一个 `PluginHost`；
7. 一个 `AkaneMemoryEngine`；
8. 一个 `NapCatQQGateway`；
9. 一个 `RuntimeMetrics`；
10. 一组闭包绑定到上述单例的 routes。

关键位置：

- `instance_context = resolve_instance_context(...)`
- `instance_runtime = bind_instance_runtime(...)`
- `model_service_config_store = ...`
- `desktop_satellite_service = DesktopSatelliteService(...)`
- `plugin_host = PluginHost(...)`
- `engine = AkaneMemoryEngine(...)`
- `qq_gateway = NapCatQQGateway(...)`
- `build_qq_router(engine=engine, qq_gateway=qq_gateway, ...)`

Slice 1-4A 已把这些对象收进可重复创建的 `BotRuntime` 并由 Registry 持有；默认 Web/桌宠路由仍显式绑定默认 Bot，QQ 路由已经能绑定所有启用 QQ 的 Bot。

### 2.2 QQ Router 继续复用单 Bot handler，但由 Host 按 Bot 装配

文件：`companion_v01/routes/qq.py`

`build_qq_router(...)` 接收单个：

- `engine`
- `qq_gateway`
- `channel_config`
- `admin_auth`
- `tts_client`
- `config_module`

`/api/qq/napcat/event` 先用唯一 `channel_config` 校验 webhook，再校验事件 `self_id`，随后始终调用唯一 `qq_gateway` 与唯一 `engine`。

Slice 4A 没有复制 QQ 业务实现，也没有为 personal/finance 各写一套 handler。Host 使用同一个 `build_qq_router()` 工厂，为每个 Runtime 注册不同的 bot-scoped path：

```text
request path / webhook secret / event self_id
        ↓
目标 BotRuntime
        ↓
同一个 QQ turn handler 实现
```

这种装配保留了每 Bot 独立 Gateway 状态，又避免在请求期间切换共享全局对象。后续只有当 Bot 热增删要求动态路由时，才需要把静态 router 装配进一步收敛为 Registry dispatcher；不能为了形式再保留第二套 handler。

### 2.3 QQ 身份当前没有 Bot namespace

文件：`companion_v01/qq_gateway.py`

当前私聊 identity：

```text
session_id = qq_pri_<user_id>
profile_user_id = qq_<user_id>
```

当前群聊 identity：

```text
session_id = qq_group_shared_<group_id>
profile_user_id = qq_group_shared_<group_id>
```

在每 Bot 独立数据根时不会冲突。多 Bot Host 第一版仍保留每 Bot 独立 Engine/data root，因此无需立即修改 MemCore schema。

不要在第一版把多个 Bot 合并到同一个 Store 后再额外发明 `bot_id` 数据列。更安全的方案是：

```text
同一进程
├─ BotRuntime A → data root A → MemCore A
├─ BotRuntime B → data root B → MemCore B
└─ BotRuntime C → data root C → MemCore C
```

这样现有 personal/finance 数据可原样挂载，且回滚不需要拆分数据库。

### 2.4 Engine 已具备重复实例化基础

文件：`companion_v01/engine.py`

`AkaneMemoryEngine.__init__` 已接收：

- `base_dir`
- `instance_context`
- `runtime_layout`
- `plugin_capability_source`
- `qq_channel_config`
- `capability_offer_source`
- 角色资源

Engine 自己创建实例级：

- MemoryStore
- MemCore manager
- workspace
- attachment inbox
- generated files
- plugin tool bridge
- care state
- task worker

因此核心不需要改成一个巨型共享 Store。应通过 `BotRuntimeFactory` 重复创建同一个 Engine 类。

### 2.5 最大技术阻碍：模块级全局 `config`

当前 `LLMRuntime` 构造 chat/aux client 时直接读取：

- `config.CHAT_API_KEY`
- `config.CHAT_BASE_URL`
- `config.CHAT_MODEL_NAME`
- `config.CHAT_API_PROTOCOL`
- `config.AUX_*`

Vision、TTS、ASR、cache、context/compaction、QQ voice 等也有全局配置读取。

调查发现 `companion_v01/` 与 `services/` 中至少 21 个文件直接导入模块级 `config`，另有 Router 通过 `config_module` 接收并读取同一个全局配置对象。不能在多 Bot 进程中通过“切换全局 config 后调用 Engine”实现覆盖；并发请求会互相污染。

必须引入实例不可变的 settings view：

```python
bot_settings = HostDefaults.overlay(bot_override)
engine = AkaneMemoryEngine(..., settings=bot_settings)
llm = LLMRuntime(settings=bot_settings.model, ...)
```

实施时不要一次机械重写全部全局配置读取点。先迁移真正允许按 Bot 覆盖的运行键：

1. Chat/Aux/Text model；
2. Vision；
3. Prompt cache；
4. Context window / auto compact；
5. TTS/ASR provider；
6. QQ voice；
7. plugin/care 开关。

其他明确的 Host 级不可变默认值可以暂时继续由 `config` 提供，直到后续自然收敛。

### 2.6 Plugin 当前已经是配置，但只支持重启式 manifest

文件：

- `companion_v01/instance_profile.py`
- `companion_v01/plugin_host.py`
- `companion_v01/routes/plugins.py`

当前 manifest 已支持：

```toml
[[plugins]]
id = "akane.finance"
enabled = true
```

问题：

- 插件选择属于 `InstanceManifest`；
- 启动快照不可变；
- `/admin/plugins/status` 只能看状态，不能管理选择；
- 控制中心没有 Bot/插件配置页；
- QQ 没有通用 owner-only 插件管理命令。

多 Bot 后，插件选择必须进入 `BotConfig`，由 `BotRuntime` 创建自己的 `PluginHost`。第一版保存后允许只重启目标 BotRuntime，不要求整个 Host 重启。

### 2.7 Satellite 当前同时在服务端和客户端绑死单实例

服务端：`companion_v01/desktop_satellite.py`

- `DesktopSatelliteService` 文档和实现都是 instance-bound；
- 一个实例一个 token；
- 一个实例只允许一个活动 connection；
- receipt 固定包含 instance id。

客户端：`desktop_pet_next/src-tauri/src/main.rs`

- 从环境读取一个 `AKANE_INSTANCE_ID`；
- 读取一个 backend URL；
- 读取一个 Satellite token；
- `start_desktop_satellite()` 只运行一条 session；
- pet state 强制与 runtime instance id 完全相等。

这是“像一个 Bot 绑定一台电脑”的直接原因。

目标必须改为 Host 级 device/executor hub：

```text
Desktop device
  └─ one connection / one executor registry
          ├─ authorized bot A
          ├─ authorized bot B
          └─ authorized bot C
```

ToolSpec 与 executor 只实现一次。Bot 只参与授权、产物 namespace 和调用审计。

### 2.8 控制中心当前只认识一个实例

桌宠控制中心当前：

- 从 pet state 读取一个 instance id；
- 绑定一个 backend URL；
- 发现 instance mismatch 会失败；
- model settings、capabilities、QQ status 都默认是当前唯一实例。

关键文件：

- `desktop_pet_next/src/control-center-lab.js`
- `desktop_pet_next/src/control-center/data-sources.js`
- `companion_v01/routes/control_center.py`
- `companion_v01/routes/model_services.py`

多 Bot 后需要 Host 级 Bot 管理页，并把现有单实例设置页面变成“当前选中 Bot”的详情页。

### 2.9 第一轮动刀定位表

| 当前权威入口 | 当前职责/问题 | 第一目标状态 |
|---|---|---|
| `companion_v01/app.py` 模块级 bootstrap | 直接构造唯一 context/runtime/PluginHost/Engine/QQGateway/Satellite | 将 Bot 私有构造移入 `BotRuntimeFactory`；`app.py` 只装配 Host 与默认兼容 Bot |
| `companion_v01/engine.py::AkaneMemoryEngine.__init__` | 已能接收实例 context/layout/plugin/QQ/offer source，但内部仍有全局配置依赖 | 保持同一个 Engine 类；补 `BotSettingsView` 注入，不创建 finance/personal 子类 |
| `companion_v01/llm_runtime.py::reload_from_config`、`_build_aux_bundle`、`_build_chat_bundle` | 直接读取 `config.AUX_*` / `config.CHAT_*` | 改为读取 BotRuntime 持有的不可变模型设置快照 |
| `companion_v01/vision_service.py` client 创建路径 | 直接读取 `config.VISION_*` | 改为 Bot 级 vision settings；未配置时结构化 unavailable |
| `companion_v01/routes/qq.py::build_qq_router`、`qq_napcat_event` | 闭包捕获唯一 Engine/Gateway/config | 提取可复用的单 Bot event handler；外层 Host dispatcher 只负责鉴权与 runtime 解析 |
| `companion_v01/qq_gateway.py::NapCatQQGateway` | 拥有单 Bot 的账号、duplicate ledger、会话覆盖与发送能力 | 保持每 Bot 一份 Gateway，不复制其实现 |
| `companion_v01/instance_profile.py::InstanceManifest` | 当前实例产品配置权威 | 迁移期只做 `BotConfig` thin adapter，最终不再作为第二套可写权威 |
| `companion_v01/instance_runtime.py::bind_instance_runtime` | 绑定并锁定一个实例数据根 | 由 `BotRuntimeFactory` 对每个 Bot 调用一次，保留独立 root/lock |
| `companion_v01/plugin_host.py::PluginHost` | 已按 manifest 选择插件，但实例启动后不可管理 | 保持每 Bot 一份 PluginHost；插件选择进入 BotConfig，重启目标 Bot 生效 |
| `companion_v01/desktop_satellite.py::DesktopSatelliteService` | 服务端 instance-bound、单活动连接 | 收敛为 Host 级 `DeviceExecutorHub`，Bot 只参与授权、审计与 artifact namespace |
| `desktop_pet_next/src-tauri/src/main.rs::start_desktop_satellite` | 客户端读取单个 `AKANE_INSTANCE_ID` 并只建立一条实例 session | 改为一条 Host/device session，注册一次 offers，服务多个授权 Bot |
| `companion_v01/routes/control_center.py` 与 `routes/model_services.py` | 页面和设置路由只认识当前实例 | 增加 Bot 列表/选择；现有设置能力改为选中 Bot 的详情与覆盖来源 |

此表是后续实现的首轮搜索入口。进入某个 Slice 后仍需按 AGENTS 要求查看该入口的活跃调用者与测试，但不再重新审查 personal/finance 是否应该分成两套产品。

---

## 3. 目标运行模型

### 3.1 类型与所有权

推荐最小类型：

```text
AkaneHostRuntime
├─ HostDefaults
├─ BotRegistry
├─ DeviceExecutorHub
├─ shared static resources / plugin distribution catalog
└─ host diagnostics / management auth

BotRegistry
└─ bot_id → BotRuntime

BotRuntime
├─ BotConfig
├─ BotSettingsView
├─ InstanceRuntimeLayout（兼容复用现有独立数据根）
├─ AkaneMemoryEngine
├─ PluginHost
├─ NapCatQQGateway
├─ AsyncTaskSupervisor
├─ per-bot metrics/log identity
└─ lifecycle state
```

不要把 `BotRuntime` 变成另一套 Engine；它只是把当前 `app.py` 的一组单例包装成可重复创建的对象。

### 3.2 BotConfig

BotConfig 是 Bot 产品配置唯一权威。建议字段：

```toml
schema_version = 1
bot_id = "bot-a"
enabled = true
display_name = "Akane A"
character_pack_id = "akane_v1"
memory_space_id = "bot-a"
model_profile_ref = "default"
capability_profile_ref = "default"
care_enabled = true

[channels.qq]
enabled = true
profile_ref = "qq.bot-a"

[[plugins]]
id = "akane.finance"
enabled = false
```

约束：

- 配置不含 API Key、QQ token、Satellite token、绝对路径。
- secrets 由 Host secret store / 部署环境按 `profile_ref` 解析。
- `memory_space_id` 只是安全 id，不是路径。
- 新增 Bot 不新增 schema 字段或 Python 类。
- 未配置的能力、模型与 cache 字段全部继承 HostDefaults。

### 3.3 HostDefaults 与 Bot override

配置解析顺序冻结为：

```text
代码安全默认值
    ↓
HostDefaults
    ↓
Model/Capability Profile
    ↓
BotConfig override
    ↓
允许的会话临时 override（例如 chat model）
```

必须能区分：

- `inherited`：从 Host 默认继承；
- `overridden`：Bot 显式覆盖；
- `disabled`：Bot 显式关闭；
- `unavailable`：配置允许但真实 provider/device 不在线。

控制中心不能把 unavailable 显示为 enabled，也不能把 inherited 复制成 Bot 本地冗余字段。

### 3.4 一个进程，多份独立 Bot 数据根

第一版不合并 personal/finance 数据库。

Host 进程启动时：

```text
for bot_config in enabled_bots:
    layout = bind bot data root
    runtime = BotRuntimeFactory.create(bot_config, layout, shared_services)
    registry.add(runtime)
```

现有 `InstanceRuntimeLayout`、root binding、lock 和路径安全检查可以复用。需要修改的是它们的调用方从“进程唯一”变成“BotRuntime 唯一”。

### 3.5 QQ 多 Bot 分发

推荐新 canonical endpoint：

```text
POST /api/bots/{bot_id}/qq/napcat/event
```

处理顺序：

1. 校验 `bot_id` 是安全 id；
2. 从 BotRegistry 解析 runtime；
3. 使用该 Bot 的 webhook secret 校验请求；
4. 解析 JSON；
5. 校验事件 `self_id` 与该 Bot 的 QQ id 一致；
6. 调用现有 QQ 单 Bot 处理函数；
7. 使用该 Bot 的 Gateway 回发。

兼容窗口：

- 旧 `/api/qq/napcat/event` 仅转发到 `default_bot_id`；
- 控制中心与部署迁移完成后删除旧 endpoint；
- 不允许新旧 endpoint 长期成为两个权威实现。

### 3.6 桌宠与默认 Bot 的记忆关系

冻结行为：

- 桌宠在 Host 配置中保存 `bound_bot_id`，默认指向 `default_bot_id`；
- 桌宠请求由 Host dispatcher 转给对应 `BotRuntime.engine`；
- 桌宠使用与默认 Bot owner 相同的 `profile_user_id`；
- 桌宠使用独立 `session_id`，例如 `desktop:<device-safe-id>`；
- QQ 私聊/群聊继续使用各自 session；
- MemCore 长期层通过同一 Bot data root/profile identity 共享；
- 原始短期时间线不强行合并。

体验要求：

- QQ 告诉默认 Akane 的重要信息，桌宠之后能够通过长期记忆记得；
- 桌宠当前聊天不会被 QQ 群原始消息直接淹没；
- 其他 Bot 的好友/群聊记忆不会进入默认桌宠；
- 切换桌宠绑定 Bot 只是改配置，不复制记忆。

### 3.7 DeviceExecutorHub

Host 级设备服务拥有：

- device connection；
- live offers；
- executor readiness；
- invocation ledger；
- artifact transfer ticket；
- bot authorization policy。

Bot Engine 在每轮解析能力时向 Host hub 请求：

```text
resolve_offers(bot_id, tool_spec, policy)
```

receipt 仍需包含：

- bot id / memory space；
- device/offer/lease；
- tool/spec/schema hash；
- expiry；
- policy digest。

但 executor 实现与连接不能按 Bot 复制。

### 3.8 Cache 继承与隔离

所有 Bot 使用同一算法，但 cache namespace 默认按 Bot 安全隔离：

```text
<host namespace>:<bot id>
```

原因：

- Bot 可能使用不同用户、不同 API Key、不同模型；
- provider cache key 不应成为跨 Bot 数据混用入口；
- 公平指的是同一算法和默认配置，不是让不同 Bot 共用用户 prompt cache。

必须保留：

- `prompt_cache_scope_hash` 按 profile/session/character 分区；
- `plugin_proactive` 与普通 final 分 scope；
- stable system prefix；
- append-only MemCore raw timeline；
- native tool schema hash 审计。

应改进：

- 相同基础能力对所有 Bot 默认一致；
- Host 级设备上线/下线同时影响所有授权 Bot；
- 插件差异只增加插件工具；
- Bot 设置页显示 cache 配置来源及真实 usage，不显示虚假命中目标。

---

## 4. 实施切片

每个切片必须独立可验收、可回滚。不要同时推进 UI、数据库迁移、Satellite 和 TTS。

### Slice 0：契约测试与文档冻结

目的：先用测试锁死产品边界，防止实施中再次走回“finance 特殊 Bot”。

新增/调整测试建议：

- `tests/test_multi_bot_product_contract.py`
- `tests/test_finance_plugin_absence.py`
- `tests/test_instance_profile.py`

必须断言：

1. finance plugin off/on 不改变普通 handler 集合，只增加插件 handler；
2. `finance_mode` 与 finance domain profile 不再改变核心 prompt；
3. 新增第三个 BotConfig 不需要注册新 handler 类；
4. Care 是 BotConfig boolean；
5. Bot 配置不允许 secret/path；
6. default Bot 与 desktop binding 的 memory identity 关系固定。

完成门：只新增契约测试与本文档，不改运行代码。

### Slice 1：抽取单 BotRuntime，不改变现有行为

目的：把 `app.py` 当前单例装配移入可测试 factory。

建议文件：

- 新增 `companion_v01/bot_runtime.py`
- 新增 `companion_v01/bot_registry.py` 的最小单 Bot 版本
- 修改 `companion_v01/app.py`
- 修改 startup/shutdown 测试

`BotRuntime` 至少拥有：

- context/layout/lease；
- engine；
- plugin host；
- QQ gateway；
- notification port；
- follow-up supervisor；
- model/capability stores；
- start/stop/status。

`app.py` 变为：

```text
create HostRuntime
create default BotRuntime through factory
register Host routes
```

完成门：

- 单 Bot 所有现有测试与真实 personal 行为不变；
- `app.py` 不再直接拥有 Engine/PluginHost/QQGateway 构造细节；
- 没有同时保留新旧两套 bootstrap 权威。

### Slice 2：BotSettingsView 与全局配置去耦

目的：允许同一进程内 Bot A/B 使用不同 API Key/模型/cache/vision，而不修改全局 `config`。

建议文件：

- 新增 `companion_v01/runtime_settings.py`
- 修改 `companion_v01/model_service_config.py`
- 修改 `companion_v01/llm_runtime.py`
- 修改 `companion_v01/engine.py`
- 修改 `companion_v01/vision_service.py`
- 修改 `companion_v01/routes/model_services.py`
- 修改 `companion_v01/routes/voice.py`
- 修改 `companion_v01/routes/qq.py` 中 model/TTS 读取

原则：

- `config.py` 只负责进程 boot defaults；
- 每个 BotRuntime 持有不可变/线程安全 settings snapshot；
- 保存模型设置只 reload 目标 Bot 的 LLM/Vision；
- 不得通过临时修改模块全局变量来模拟 per-Bot 配置；
- API key 不出现在 snapshot/log/prompt。

完成门：

- 同一进程两个 Bot 使用两个 fake provider client，调用互不串线；
- 修改 Bot A 模型配置不改变 Bot B；
- shared default 改变后，未覆盖 Bot 继承；已覆盖 Bot 保持覆盖；
- cache namespace/retention/context/compact 按解析结果生效。

### Slice 3：BotConfig / BotRegistry 多 Bot 生命周期

目的：一个 Host 同时启动多个 BotRuntime。

建议文件：

- 新增 `companion_v01/bot_profile.py`
- 扩展 `companion_v01/bot_registry.py`
- 将 `companion_v01/instance_profile.py` 变成兼容 adapter，不能成为第二配置权威
- 修改 `companion_v01/instance_runtime.py` 调用方式
- 修改 Host startup/shutdown

生命周期：

```text
registered → starting → online/degraded → stopping → stopped
```

要求：

- 一个 Bot 启动失败不阻止其他 Bot；
- 一个 Bot stop 等待其 writer/plugin job/follow-up task；
- Host stop 逐个有界关闭；
- 同一 bot id/data root 不允许重复绑定；
- Bot 状态只返回安全字段。

完成门：同一测试进程同时运行至少三个 BotRuntime，各自写入不同临时数据根。

### Slice 4：QQ Host Dispatcher

目的：真实多个 QQ 账号同时在线。

建议文件：

- 修改 `companion_v01/routes/qq.py`
- 修改 `companion_v01/qq_gateway.py`
- 修改 `companion_v01/deployment_security.py`
- 新增 `companion_v01/qq_bot_dispatcher.py`（如能明显减少 route 重复）
- 扩展 QQ route tests

要求：

- canonical bot-scoped webhook；
- path bot id、secret、event self_id 三者一致；
- 每个 Bot 独立 duplicate ledger、好友、群聊、角色 override、回复模式和模型 override；
- 出站始终使用正确 Bot 的 Gateway/token；
- plugin QQ commands 使用正确 Bot 的 PluginHost；
- 被动群消息写入正确 Bot MemCore；
- 附件、语音、文件发送和后台完成通知不串 Bot。

完成门：两个 fake NapCat 账号并发发消息，分别落入正确 Engine/Memory/Gateway。

### Slice 5：Host 级 DeviceExecutorHub 与桌宠绑定

目的：一台电脑服务多个 Bot，并让桌宠绑定默认 Bot 记忆空间。

建议文件：

- 重构 `companion_v01/desktop_satellite.py`
- 修改 `companion_v01/capability_registry.py`
- 修改 `companion_v01/tool_orchestration_engine.py`
- 修改 `companion_v01/routes/satellite.py`
- 修改 `desktop_pet_next/src-tauri/src/main.rs`
- 修改 `desktop_pet_next/src/main.js`
- 修改 pet state / launch binding tests

服务端目标：

- Host 级 device enrollment；
- 一个设备 connection 发布一次 offers；
- 多 Bot 授权映射；
- per-Bot receipt/namespace；
- finance/personal 不再各自要求一套 executor/token。

桌宠目标：

- pet state 从 `instance_id` 主绑定迁移为 `host_id + bound_bot_id`；
- 一个 Host backend URL；
- 一个 device token；
- 默认 Bot selector；
- 桌宠请求 dispatch 到 bound BotRuntime；
- 桌宠 session 独立、长期记忆共享。

完成门：

- 同一 desktop connection 下 Bot A/B 均能真实调用同一个 `desktop_context_snapshot` executor；
- invocation/result 按 Bot 审计，不串 artifact；
- PC 离线时所有授权 Bot 同时结构化 unavailable；
- finance plugin 开关不影响基础 desktop offers。

### Slice 6：控制中心 Bot 管理与安全 QQ 指令

目的：用户无需编辑 manifest/env/systemd。

后端建议路由：

```text
GET    /control-center/bots
POST   /control-center/bots
GET    /control-center/bots/{bot_id}
PATCH  /control-center/bots/{bot_id}
POST   /control-center/bots/{bot_id}/start
POST   /control-center/bots/{bot_id}/stop
GET    /control-center/bots/{bot_id}/model-service
POST   /control-center/bots/{bot_id}/model-service
GET    /control-center/bots/{bot_id}/capabilities
```

前端 Bot 页至少显示：

- Bot 名称和安全 id；
- QQ 在线状态；
- 当前角色；
- 模型配置来源：继承/覆盖；
- finance plugin 开关；
- Care 开关；
- 视觉/TTS/文件/设备真实状态；
- 当前好友/群聊数量只显示计数，不泄露身份；
- start/stop/restart 状态；
- 错误 reason。

QQ owner-only 指令可提供：

```text
/Akane 状态
/Akane 能力
/Akane 插件
/Akane 金融 开启
/Akane 金融 关闭
/Akane 养成 开启
/Akane 养成 关闭
```

限制：

- QQ 不配置 API Key/token/endpoint；
- 群聊普通成员不能修改 Bot；
- 指令修改后只重启目标 BotRuntime；
- 未实现热重载时明确显示“保存后重启目标 Bot”，不得假装立即生效。

### Slice 7：现有 personal/finance 迁移与旧部署删除

目的：将现有两个云端实例迁为一个 Host 下两个 BotConfig。

迁移策略：

1. 不复制/合并现有数据根；
2. 生成 Bot A/B 配置引用现有根；
3. 保存旧 systemd/env/manifest 回滚点；
4. 停止旧 personal/finance 进程，释放 root locks；
5. 启动新 Host 并同时绑定两根；
6. 更新两套 NapCat webhook 到 bot-scoped endpoint；
7. 验证两 Bot QQ 登录、自检、回复、记忆、插件和 push；
8. 验证桌宠默认绑定；
9. 稳定窗口后删除旧两套常驻 unit，而不是永久双跑。

回滚：

- 停止新 Host；
- 释放所有 Bot root locks；
- 恢复旧 webhook；
- 启动旧 personal/finance units；
- 数据根未迁移，因此无需反向数据库转换。

### Slice 8：在共享能力边界上恢复看图、GPT-SoVITS 与文件处理

此切片必须在多 Bot/共享 device 边界稳定后进行，避免再次为 personal/finance 各接一次。

顺序：

1. PinAI native vision：Host 默认 model profile 开启，Bot 可覆盖；真实 QQ 图片验收所有 Bot。
2. GPT-SoVITS：一套本地 provider/executor，所有授权 Bot 复用；音色可按 Bot/Profile 配置。
3. ArtifactBroker 双向字节链：输入、输出、hash、size、mime、namespace、ticket。
4. 文件/FFmpeg/Whisper/RVC/ComfyUI 逐个真实纵向切片。

任何能力完成标准都包含用户实际看到/听到/收到的表现，不以字段存在为完成。

---

## 5. 旧路径删除与兼容策略

根据 `docs/package_reintegration_policy_m63.md` 的单权威要求，迁移不能长期保留新旧两套。

### 5.1 允许的兼容窗口

| 旧路径 | 迁移期状态 | 最终状态 |
|---|---|---|
| `InstanceManifest` 作为产品 Bot 配置 | thin adapter | 由 BotConfig 替代或仅保留高级隔离部署 adapter |
| module-level `app.py` 单例装配 | thin adapter | `create_app(HostRuntime)` |
| `/api/qq/napcat/event` | default Bot adapter | 删除或只保留明确版本化兼容期 |
| per-instance Satellite | migration adapter | Host DeviceExecutorHub |
| per-instance model settings route | bot-scoped adapter | Bot settings authority |
| personal/finance systemd 双 unit | rollback-only | 单 Host unit |
| finance-specific tool round budget 遗留 | repair/delete | 删除无消费者分支 |

### 5.2 不允许

- 新旧 QQ router 长期各自处理完整业务；
- 同时存在 `InstanceConfig` 与 `BotConfig` 两个可写权威；
- 为 finance/personal 分别复制 Satellite executor；
- 通过改全局 `config` 在请求之间切换 Bot；
- 让桌宠同时维持 personal/finance 两套进程连接；
- 为演示增加 fake online、fake playback、fake file result。

---

## 6. 关键风险与修复护栏

### 6.1 全局配置串线

风险最高。任何 per-Bot 模型、视觉、TTS 配置都必须通过 runtime settings 注入，不能共享可变模块全局。

测试必须并发调用两个 Bot，验证 endpoint/model/key client 身份完全隔离。

### 6.2 一个 Bot 崩溃影响 Host

Bot start/stop/plugin job 必须结构化隔离。普通 Bot 失败不能关闭全局 app。

不得让一个 PluginHost 的异常冒泡中止其他 Bot。

### 6.3 多 Bot 后资源占用

第一版每 Bot 独立 Engine 会重复创建 embedding/vector/background worker。当前 Bot 数量少时可接受，优先保证隔离与迁移安全。

后续只有在真实 profiling 证明必要时，才把无状态 provider client、模型 metadata、静态资源等提升为共享服务。不要预先进行大规模对象池重构。

### 6.4 QQ webhook 误路由

必须同时校验 path bot id、webhook secret 和 event self_id。不能只信 `self_id`。

### 6.5 记忆串 Bot

第一版通过独立 data root 根治。桌宠只绑定一个 BotRuntime。跨 Bot 共享记忆必须是未来显式功能，默认禁止。

### 6.6 设备产物串 Bot

所有 artifact handle/ticket/result 必须绑定 bot id、memory space、invocation id、offer lease 和 hash。设备路径不进入云端 prompt/log。

### 6.7 缓存不公平

同一默认配置下，所有 Bot 的 cache hints、retention、context、compact 设置必须来自同一个 Host profile。Bot 覆盖要在 UI 明确显示。

prompt audit 应按 bot id 分组，但只记录 hash/长度/usage。

### 6.8 插件改变基础能力

新增 contract test：启用任意插件后，基础 ToolSpec 集合只能保持或增加，不能删除，除非 BotConfig 有显式 capability disable 且 UI 显示。

---

## 7. 验证矩阵

### 7.1 单元/集成

- BotConfig 校验、未知字段、secret/path 拒绝；
- HostDefaults + override 合并；
- BotRegistry 三 Bot 生命周期；
- per-Bot LLM/Vision/TTS client 隔离；
- finance plugin on/off；
- Care on/off；
- QQ self_id/secret/path dispatch；
- passive group memory；
- plugin QQ commands；
- background notification 正确 Bot 投递；
- desktop default Bot memory mapping；
- shared device offers；
- artifact namespace；
- shutdown writer/lock 释放；
- compatibility endpoint 只转发 default Bot。

### 7.2 真实 smoke

至少创建：

- Bot A：默认配置、finance off、Care on；
- Bot B：继承默认模型、finance on、Care 可配置；
- Bot C：不同模型 profile、finance off。

真实验证：

1. 三个 QQ 账号同时在线；
2. 同一用户分别私聊三个 Bot，记忆不串；
3. Bot A/B 在相同设备在线时看到相同基础本地能力；
4. Bot B 多出 finance tools，Bot A/C 不出现；
5. personal/default Bot 与桌宠共享长期记忆；
6. 桌宠不读取 Bot B/C 记忆；
7. Bot A 修改模型不影响 B/C；
8. 关闭设备后所有授权 Bot 同时结构化 unavailable；
9. 重启 Host 后三 Bot 自动恢复；
10. 添加第四个 Bot 只改配置。

### 7.3 回归

至少运行：

```bash
python -m unittest tests.test_backend_route_modules
python -m unittest tests.test_qq_gateway
python -m unittest tests.test_memcore_integration
python -m unittest tests.test_plugin_host
python -m unittest tests.test_plugin_reasoning
python -m unittest tests.test_finance_plugin_absence
python -m unittest tests.test_instance_runtime
python -m unittest tests.test_instance_profile
python -m unittest tests.test_capability_fabric_m66
python -m unittest tests.test_desktop_satellite_local_capabilities
```

金融插件仓库按其 AGENTS 要求运行完整测试、wheel/source-blind smoke、Ruff 和 `git diff --check`。

Rust/Tauri 至少运行：

```bash
cargo test
cargo check
npm run build
```

每个切片都必须运行 `git diff --check`。

---

## 8. 本轮实施与验证记录

本轮完成 Slice 4A：`bots.toml` 下的多个 Bot 已可分别绑定 QQ deployment profile、bot-scoped webhook、唤醒词、Engine/Gateway 与插件命令 broker；默认 Web/桌宠 routes 仍明确绑定 Registry 默认 Bot。Slice 2B/2C 的模型、视觉、cache 和语音快照继续保持在同一 Runtime 边界内。

已完成：

- 新增 `companion_v01/bot_runtime.py`：统一拥有 context、root lease、deployment security、PluginHost、Engine、QQ Gateway、Satellite、TTS、metrics、public guard 和关闭顺序。
- 新增 `companion_v01/bot_registry.py`：当前注册默认 Bot，拒绝不安全/重复 Bot id，为后续多 Bot 生命周期提供唯一注册入口。
- `companion_v01/app.py` 改为通过唯一 `BotRuntimeFactory` 创建运行时；保留旧模块级名称作为兼容引用，不再直接构造 Engine/PluginHost/QQGateway/Satellite。
- QQ 后台完成通知、插件 notification port、managed artifact sink、reasoning port 和 storage port 随 BotRuntime 一起装配。
- `tests/test_bot_runtime.py` 新增 5 项生命周期/注册契约。
- `tests/test_instance_channel_security.py` 的装配顺序断言迁移到新的唯一权威 `bot_runtime.py`。
- 新增 `companion_v01/runtime_settings.py`：不可变 `BotSettingsView`，带安全 public snapshot 和显式 overlay 校验。
- `LLMRuntime` 的 Chat/Aux client 构造改为读取本地 settings snapshot；直接独立构造时才从旧 `config` 生成兼容快照。
- `AkaneMemoryEngine` 与 `BotRuntimeFactory` 已注入同一份 Bot settings；后续 Bot 可在 factory 入口传入覆盖值，不需要切换全局 config。
- `tests/test_runtime_settings.py` 新增双 runtime provider 隔离测试。
- `VisionObservationService`、Engine native vision status、prompt cache hints/namespace、context/auto-compact token limits 均读取 Bot settings snapshot。
- 模型服务保存通过目标 `BotRuntime.reload_model_services()` 更新 snapshot；不会再把 saved model settings 写回共享模块级 config。
- QQ 模型查询使用当前 Engine settings，避免保存后仍显示旧全局模型。
- `BotRuntimeFactory` 使用 Bot 快照构造 Edge TTS；Web voice、petdesk、QQ delivery 和 capabilities provider tts-test 均优先读取同一快照。
- GPT-SoVITS、OpenAI-compatible ASR、faster-whisper、QQ TTS profile/长度/segment 参数已纳入快照；路由保留旧 `config_module` 回退。
- `tests/test_runtime_settings.py` 新增双 GPT-SoVITS client 参数隔离测试；`tests/test_qq_voice_delivery.py` 新增 Bot 快照覆盖 QQ 语音限制测试。
- 新增 `companion_v01/bot_profile.py`：`BotConfig` / `BotHostProfile` 安全解析、唯一 default、Bot/memory space 去重、配置字段 fail-closed 和 Host-owned data root 解析。
- `instance_profile.py` 降为兼容读适配；canonical BotConfig 可投影到现有 InstanceContext，不复制 Engine/PluginHost 实现。
- `BotRegistry` 增加线程安全生命周期状态、批量启停、失败隔离、超时和安全公开快照；`app.py` startup/shutdown 改由 Registry 统一拥有。
- canonical factory 路径使用 `RuntimeConfigView`，三份保存的 Bot runtime overrides 不再写回或污染进程全局 config。
- `tests/test_bot_profile.py` 覆盖配置校验/未知 secret/path/重复 root/default；`tests/test_bot_runtime.py` 使用真实 factory 构造并运行三个独立 BotRuntime。
- 新增 `companion_v01/host_bot_bootstrap.py`：有 `bots.toml` 时批量构造 enabled Bot，无文件时保留原 `AKANE_INSTANCE_ID` 单实例路径；非默认构造失败隔离，默认构造失败 fail-closed 并清理已构造 sibling。
- `app.py` 从 Host bootstrap 取得 Registry/default runtime，所有现有默认频道 route 使用该 runtime 的 `RuntimeConfigView`；不会因构造顺序误绑其他 Bot。
- 新增 `deploy/bots.example.toml`，示例不包含密钥/路径，并提供互不重叠的 Bot 唤醒词配置。
- 新增 `companion_v01/qq_channel_profiles.py` 与 `deploy/qq_profiles.example.toml`：QQ endpoint/账号/token 从 Host secret 文件按安全 profile ref 选择，公开状态与 repr 不泄漏凭据。
- `app.py` 为每个启用 QQ 的 Runtime 注册 `/api/bots/{bot_id}/qq/napcat/event`；旧 `/api/qq/napcat/event` 只绑定默认 Bot。
- `routes/qq.py` 支持安全 route base 与 per-Runtime plugin command broker provider；请求不会偷用默认 Bot 的金融插件 broker。
- `qq_gateway.py` 使用每 Bot `wake_words`；账号名 `Akane218` 不会误触发 `Akane`，同群 Bot 的重叠唤醒词在配置加载时 fail-closed。
- canonical BotRuntime 的 `DATA_DIR/DATA_ROOT` 固定为自己的 runtime root，QQ profile、模型配置、记忆、Gateway 状态和插件存储不会回落到另一个 Bot 的共享路径。
- `LLMRuntime` 对流式 JSON 尾部损坏执行完成字段恢复；QQ transport 对“已发送正常流式文本 + transient final failure”执行第二道兜底抑制，不再出现正常回复后追加“我在认真听你说”。
- canonical factory 基于同一 Host 默认 namespace 为每个 Bot 派生独立 prompt-cache scope，避免多 Bot 共用 cache 安全域或统计串线。

验证通过：

- Slice 3A `test_bot_profile + test_bot_runtime + test_settings_overrides`：26 项，包含真实三 BotRuntime factory/override/root/lifecycle 验证。
- Slice 3B `test_host_bot_bootstrap + test_bot_runtime + test_bot_profile`：23 项，包含 bots.toml 真实三 Bot 链、legacy fallback、默认/非默认构造失败边界。
- `tests.test_instance_channel_security`：22 项，包含命名实例真实启动、QQ 安全和失败前置检查。
- 路由、桌宠后端、插件 Host/通知/推理、writer shutdown 组合回归：142 项。
- BotRuntime、instance runtime/profile、finance absence、plugin engine bridge 组合回归：39 项。
- LLM client 回归：63 项；插件/金融/native web 工具组合回归：92 项。
- Vision、model-service、runtime-settings 组合回归：125 项；真实实例/QQ/路由/桌宠回归：117 项；插件/金融/实例组合回归：65 项。
- Slice 3A 与 multi-Bot product/finance absence/instance/route 组合回归：164 项。
- Slice 3B 与 instance security/backend routes/product/finance/settings 组合回归：149 项。
- Slice 4A QQ profile/双 Bot 分发聚焦测试：8 项；覆盖 secret/self_id/Engine/OneBot token、唤醒词、默认别名去重和插件 broker 隔离。
- Slice 4A 与 backend routes/QQ Gateway/instance security/Host bootstrap/BotRuntime/product contract/finance absence 组合回归：223 项。
- 流式字段恢复与 QQ 兜底抑制聚焦回归：73 项；LLM/native tool/QQ/route/plugin reasoning 组合回归：289 项。
- Ruff check、Ruff format check、py_compile、`git diff --check` 均通过。
- 云端旧双进程与旧 finance webhook upstream 已下线，新单 Host 与两条 bot-scoped QQ runtime 已通过健康、鉴权、self_id、出站 self-check、restart 和 root-lock smoke。
- 尚未完成真实群聊双唤醒词、QQ 附件/语音/文件/后台通知的双 Bot 用户表现验收、控制中心 Bot 管理 UI 和 DeviceExecutorHub；不能用合成群消息污染真实记忆来假装表现验收。
- 未合并/复制 MemCore 数据，未修改 NapCat 登录、OneBot access token 或桌宠前端；用户原有 `.claude/` 未触碰。

---

## 9. 后续执行起点

后续执行不应直接继续为 personal 单独接 GPT-SoVITS 或为 finance 单独复制视觉/Satellite 配置。

Slice 0、Slice 1、Slice 2A、Slice 2B-core、Slice 2C、Slice 3A、Slice 3B、Slice 4A 与 Slice 4B 云端切换已完成。下一步进入 **真实表现验收与 Slice 5**：

1. 在两个 Bot 同在的真实群发送 `Akane ...`，确认只有 personal 回复；发送 `金融助手 ...`，确认只有 finance 回复；
2. 各自连续普通对话，确认 personal 不再出现“正常回复 + 我在认真听你说”双尾句，finance 插件命令只由 finance 处理；
3. 继续补齐图片、语音、文件和后台通知的双 Bot 真实链验收；
4. 进入 Host 级 DeviceExecutorHub，让同一台本地电脑的完整能力按授权服务所有 Bot，而不是恢复 per-Bot executor；
5. 增加控制中心 Bot 管理面，后续新增 Bot 只注册 QQ/profile/config，不再写代码或 systemd unit。

当上下文被压缩时，恢复顺序：

1. 读本文；
2. 看 `git status --short`；
3. 看当前切片测试；
4. 不重新发明多 Bot 产品模型；
5. 不跨切片提前接新能力；
6. 每个完成切片做聚焦 commit。
