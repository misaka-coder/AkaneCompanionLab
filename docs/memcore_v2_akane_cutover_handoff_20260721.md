# MemCore V2 → Akane 接管工作交接（2026-07-21）

## 最新 repair pass 状态（后续章节中的“尚未切读链”描述已过期）

截至本轮未提交工作区，Akane 已经切到 MemCore provider projection 读权威，并完成以下收口：

- transport 前 request observer 记录真实 Chat/Responses wire；observer 拒绝时不会调用 provider；
- 请求冻结按 projection message 生效，不再按整个 turn 锁死；已冻结旧消息不可改，后追加工具消息可分别首次冻结；
- 普通消息、`event.*`、单工具和并行工具使用同一线性 turn；Responses 不再合并相邻同 role 消息；
- final 保存真实 provider raw，缺少 LLM runtime 的轻量完成路径仍能原样提交 raw；
- 动态检索/runtime/persona/visual 上下文放在 append-only 历史和当前消息之后；空动态块不渲染；
- `tool/event/skill/material` 显式 kind 检索由宿主 allowlist 授权，模型只能缩小权限；
- MemCore projection 读取/冻结失败返回结构化记忆错误，不落入人格兜底，也不持久化失败回复；
- 工具新加载的图片不再回填到已冻结的原始 user message，而是在 tool result 后追加
  `material.model_input`；原图只走当前 provider request，持久化投影使用 media omission marker；
- MemCore 模式下最终生成重试复用完全相同的 user payload，不追加临时 retry note。

当前新增/重点测试位于：

```text
tests/test_memcore_integration.py
tests/test_provider_raw_result.py
tests/test_llm_client.py
memcore/tests/test_projection_cache.py
```

最新已单独通过：无 runtime 的 raw final 完成、工具产图冻结、重试 payload 一致、原生工具与 provider raw
聚焦测试。完整回归和真实云端 provider/QQ 验收仍需在本 repair pass 末尾执行；在此之前不要把本地状态写成
“线上已完成”。后续章节保留的是切换前历史，定位旧代码和理解迁移顺序仍有价值，但“当前阶段”以本节为准。

> 用途：把本文件全文复制给新的 Codex/AI 账号，即可继续当前主线。
> 当前不是重新设计阶段；MemCore V2 核心和 Akane V2 写链已经完成，下一步是补真实 provider raw output、切换读链、删除旧权威并做真实链路验收。

## 0. 给接手账号的第一句话

请继续 `MemCore Unified Timeline V2 → Akane` 回填，不要重新发明架构，也不要先部署云端。

先完整阅读：

1. `F:\Akane\AkaneCompanionLab\AGENTS.md`
2. `F:\Akane\memcore\AGENTS.md`
3. `F:\Akane\memcore\docs\unified_timeline_v2_design_v1.md`，重点 §22、§24
4. 本交接文件

然后先执行只读检查：

```powershell
cd F:\Akane\AkaneCompanionLab
git status --short
git log -5 --oneline

cd F:\Akane\memcore
git status --short
git log -8 --oneline
```

不要覆盖两个仓库中已有的用户改动；每轮只做一个完整、可验证的切片。

## 1. 用户真正要的架构

这条主线的目标不是“给金融 Bot 再写一套记忆”，也不是“为了缓存硬编码某几个工具或事件”。目标是：

- 普通消息、群聊事件、金融主动事件、工具调用/结果、最终回复，都进入同一条 append-only MemCore 时间线；
- 同一个真实模型轮使用同一个 `turn_id`；并行工具各有独立 `correlation_id`；
- 普通对话、个人 Bot、金融 Bot 使用同一套代码和同一套生命周期；差异只来自 profile、插件开关、Namespace 和配置；
- 给模型的旧前缀必须字节稳定，新增内容只追加在尾部；压缩、provider 切换和明确的媒体例外可以形成可解释断点；
- `memory_metadata` 标注的是本轮 stimulus，不得被中间工具轮污染；
- 有有效终态 metadata 的 message/event 可以按 policy 进入普通检索；
- `tool.*` / `material.*` 默认不进入普通检索，但模型显式请求相应 kind 时可以在前置过滤后检索；
- 未知的新 kind 必须能走 canonical renderer，不能靠穷举所有业务枚举；
- MemCore 只管理通用时间线、投影、压缩、检索和记忆工具契约；QQ 发送、工具执行、图片理解、文件本体、GPT-SoVITS、金融业务判断仍由 Akane 执行；
- 不新增第二块“金融记忆区”或第二套线性历史；
- 不为了某个点写死“必须调用两个工具”等规则；约束必须是通用、有价值的边界。

用户已经明确：当前 MemCore 只有他自己在用，不要求为了未知第三方长期保留旧实现。因此旧路径在新权威验收后应删除或降成真正的一行薄适配，不能长期双实现。

## 2. 仓库、分支与工作区状态

### 2.1 Akane 宿主仓库

```text
路径：F:\Akane\AkaneCompanionLab
分支：feature/qq-finance-assistant-emquant
本主线最新提交：9f5df7c feat: migrate Akane writes to MemCore V2 turns
```

`9f5df7c` 之后本交接文档是下一项改动。不要把下面未跟踪内容夹带进提交：

```text
.claude/
maintenance/_cache_bucket_replacement_probe_aa5bae7.py
maintenance/_cache_key_isolation_probe_2fb194c.py
maintenance/_cache_ttl_probe_2fb194c.py
maintenance/_compare_prompt_audit_aa5bae7.py
maintenance/_compare_responses_audit_2fb194c.py
maintenance/_deepseek_stream_cache_probe.py
maintenance/_partial_hit_cache_replacement_probe_2fb194c.py
maintenance/_personal_stream_cache_probe_aa5bae7.py
maintenance/_summarize_finance_outbox_2fb194c.py
maintenance/_summarize_llm_routes_aa5bae7.py
maintenance/_temporary_deepseek_cloud_switch.py
```

### 2.2 MemCore 包仓库

```text
路径：F:\Akane\memcore
分支：feature/actor-metadata-update
最新已提交主线：8e5ee7f feat: add timeline v2 host cutover primitives
```

当前用户已有未提交改动，接手时不得覆盖、格式化或夹带：

```text
README.md
docs/model_prompt_playbook_v1.md
docs/usage_flow_v1.md
memcore/chat_output/prompts.py
memcore/native_tools.py
memcore/prompts.py
tests/test_slice6_prompt_governance.py
tests/test_slice_native_tools.py
.claude/
```

这些改动涉及材料校验、提示词/原生工具等另一条线。除非新用户明确要求，当前 Akane read cutover 不应修改它们。

## 3. 权威文档与历史文档

### 3.1 当前权威

1. `F:\Akane\memcore\docs\unified_timeline_v2_design_v1.md`
   - §22：Akane 文件级回填与旧权威删除；
   - §22.3：原生多工具 history 移入 projection adapter；
   - §22.4：真实 provider raw output；
   - §22.5：`response_builder.py` 与 prompt envelope 删除窗口；
   - §22.6：切换顺序；
   - §24.3：真实 provider 缓存验收；
   - §24.4：可执行切片。
2. `F:\Akane\memcore\AGENTS.md`
   - 必须遵守公共 facade、Namespace/Actor、安全和验证边界；
   - 其中 `record_user_turn → update_turn_metadata → record_assistant_turn` 示例是 V1 兼容接入形态；本项目的 V2 轮次迁移以 `unified_timeline_v2_design_v1.md` 和当前 V2 tests 为更具体的权威。
3. `F:\Akane\AkaneCompanionLab\docs\package_reintegration_policy_m63.md`
   - 该政策明确写着 MemCore 的 public/private 决策不在 M63 范围内；
   - 但“包回填必须减少权威实现数量”仍是本次清理必须遵守的工程原则。
4. `F:\Akane\AkaneCompanionLab\docs\akane_multi_bot_host_recovery_v1.md`
   - 多 Bot 是同一 Host/Engine 能力的多个实例，不是金融/个人各写一套逻辑；
   - 各 Bot 的记忆、好友、群和少量配置通过实例数据隔离。
5. `F:\Akane\AkaneCompanionLab\docs\finance_proactive_linear_memory_native_tools_repair_v1.md`
   - 用于核对金融主动推送的业务表现、线性记忆和人类可见输出格式；
   - 不作为 MemCore V2 底层生命周期的权威。

### 3.2 只能作为历史背景

`F:\Akane\AkaneCompanionLab\docs\memcore_integration_plan_v1.md` 记录的是早期 V1 接入和旧 `record_*` 主链，很多“当前状态”已经过期。不要照它把 V1 `record_user_turn/update_turn_metadata/record_assistant_turn` 重新变成权威。

MemCore 的 `README.md`、`docs/usage_flow_v1.md` 和 `AGENTS.md` 仍保留大量 V1 兼容示例；当它们与 Unified Timeline V2 的 §17–§24 冲突时，以 V2 设计、当前公共 API 和 V2 tests 为准。

## 4. MemCore 包已经完成什么

已提交切片：

```text
4b82e2d feat: add timeline v2 schema foundation
24f1c1b feat: add timeline v2 turn lifecycle
38727e1 feat: add timeline v2 projection ledger
41d55b4 feat: add timeline v2 atomic compaction
df09a50 feat: add retrieval v2 admission
3523c26 feat: add relation-aware retrieval expansion
8e5ee7f feat: add timeline v2 host cutover primitives
```

当前包能力包括：

- V2 schema/migration、turn/relation/projection tables；
- `begin_turn / append_entry / append_standalone_entry / complete_turn / abort_turn`；
- staged annotation，final 前不会提前获得普通检索准入；
- provider-neutral renderer registry；
- OpenAI/Anthropic/canonical projection；
- immutable projection ledger、request audit、strict-prefix checks；
- closed-turn、token-aware、lineage-aware 的原子 compaction；
- Retrieval V2 的 Namespace/kind/visibility/time/index generation 硬过滤；
- relation expansion：stimulus/final、并行 correlation branch、summary/semantic lineage closure；
- token budget 以原子关系组裁剪，不拆工具分支；
- typed standalone entry；
- 普通检索默认排除工具/材料 trace，显式 kind 查询可以打开相应前缀；
- Actor/Namespace 边界、循环/断裂 lineage、未闭合工具等测试。

此前 clean 状态验证记录为：292 tests passed、3 skipped；Ruff、format、`git diff --check`、wheel/sdist build 通过。当前包工作区已有上述用户脏改动，重新验证时要先保护它们，不能用格式化命令机械改写整仓库后夹带提交。

## 5. Akane 已经完成什么

提交：

```text
9f5df7c feat: migrate Akane writes to MemCore V2 turns
```

关键文件：

```text
F:\Akane\AkaneCompanionLab\companion_v01\memcore_integration\manager.py
F:\Akane\AkaneCompanionLab\companion_v01\engine.py
F:\Akane\AkaneCompanionLab\tests\test_memcore_integration.py
F:\Akane\AkaneCompanionLab\tests\test_native_web_search_tooling.py
F:\Akane\AkaneCompanionLab\tests\test_plugin_reasoning.py
```

### 5.1 当前真实写链

```text
message.user / event.* stimulus
  -> MemcoreManager.begin_input_turn()
  -> 返回稳定 turn_id

assistant 工具前话术
  -> append_turn_intermediate(turn_id)

同一工具批次
  -> 所有 action 先追加
  -> 所有 observation 后追加
  -> 每个 call 独立 correlation_id

final JSON memory_metadata
  -> stage_turn_metadata(stimulus source_id)
  -> 此时仍是 unannotated + explicit，不提前进入普通检索

最终 assistant
  -> complete_input_turn(turn_id)
  -> annotation + final + visibility + close 原子提交

不触发模型回复的记录
  -> append_standalone_entry()
```

### 5.2 已切换的 Akane 行为

- 同步 `process_turn()` 和流式 `process_turn_stream()` 都保存真实 `memcore_turn_id`；
- 普通 user 与结构化 `event.*` 都走同一 V2 turn；
- 外部事件不会被伪装为高权限“系统事件”；金融使用中性 `event.finance`；
- 工具前话术是 `INTERMEDIATE`，不再伪装成 assistant final；
- 并行工具按同一轮批次记录，不通过物理相邻或 `seq_no ± N` 猜关系；
- error 结果也作为 terminal observation 闭合对应 branch；
- final metadata 对 user/event 都 stage，再由 complete 统一提交；
- 被动群消息是 standalone，不会留下永久 open turn；
- 附件 reference/cleanup 已改为 typed `material.reference/material.cleanup` standalone；
- 工具和材料 trace 使用 explicit retrieval policy，默认普通检索不可见，但显式 kind 检索仍可用；
- provider raw 缺失时传空字符串，不把 parsed dict 重新序列化冒充原始模型输出；
- 旧公开方法名只剩 V2 standalone/batch/stage 的薄适配，没有继续调用 MemCore V1 `system.record_*` 写算法。

关键入口（行号会随下一提交变化，先用 `rg`）：

```powershell
rg -n "def (begin_input_turn|append_turn_intermediate|record_tool_batch|stage_turn_metadata|complete_input_turn|abort_input_turn|_append_standalone_turn|_record_material_event)" companion_v01/memcore_integration/manager.py
rg -n "def (_record_memcore_input_turn|_append_memcore_turn_intermediate|_complete_memcore_input_turn|_abort_memcore_input_turn|_record_memcore_tool_batch)|memcore_turn_id =" companion_v01/engine.py
```

### 5.3 已有自动化证明

- V2 turn 的顺序为 stimulus → intermediate → actions → observations → final；
- 两个并行工具在 OpenAI projection 中仍是同一个 assistant tool-call batch；
- action/result 的 correlation id 不混；
- staged metadata 在 complete 前不会提前准入；
- 真实 raw 缺失时保持空，不伪造；
- `event.finance` 和 final 属于同一 turn；
- standalone 的 `turn_id` 为空；
- 材料 payload 不带 `storage_relpath` 或本地绝对路径；
- 同步/流式结构化事件都走 V2 input primitive；
- 工具 trace 的敏感 key、token 和绝对路径继续清洗。

最近验证：

```text
133 项 MemCore/工具/插件/可见上下文/时间线相关测试：通过
87 项 backend route 测试：通过
py_compile：通过
Ruff check：通过
git diff --check：通过
```

完整 Python suite 当时运行 1623 项，有 3 个与本轮无关的现有失败：

```text
tests.test_capability_fabric_m66...offer_controls...
tests.test_desktop_satellite_local_capabilities...open_browser...
tests.test_settings_catalog...catalog drift...
```

接手时应先重新跑当前 HEAD；如果这些失败已经被其他主线提交修复，不要继续引用旧结果。

## 6. 当前处于哪个阶段

按 `unified_timeline_v2_design_v1.md` §24.4：

```text
1 Schema foundation              完成
2 Turn lifecycle                 完成
3 Projection ledger              完成
4 Compaction V2                  完成
5 Retrieval admission            完成
6 Relation expansion             完成
7 Akane cutover primitives       完成
8 Native tools V2                尚无干净独立提交；MemCore 工作区脏改可能与此重叠，先保护
9 Akane V2 write cutover         已完成并提交 9f5df7c
10 Akane read cutover            下一主阶段，尚未完成
11 Real provider acceptance      尚未完成
12 Cleanup                       尚未完成
```

所以当前准确状态是：

```text
MemCore V2 核心完成
+ Akane V2 写链完成
+ Akane 仍在使用兼容 build_prompt_context / prompt envelope 读链
= 下一步必须切 provider projection 读链并删除旧读权威
```

本轮没有部署云端，不能把本地提交当成线上已生效。

## 7. 当前仍存在的旧权威/缺口

### 7.1 同步 provider raw output 缺失

`companion_v01/llm_runtime.py`：

- 流式 `ChatJSONStreamResult` 已有真实 `raw_text`；
- 同步 `call_chat_json()` 只返回 parsed dict；
- Akane 完成 V2 turn 时当前只能传 `provider_output_raw=""`；
- 绝对不能 `json.dumps(parsed)` 冒充 raw。

### 7.2 Akane prompt 仍走兼容读链

`companion_v01/engine_services/response_builder.py` 当前仍有：

```text
_build_memcore_prompt_context()
_build_structured_history_turns()
_attach_message_prompt_envelopes()
_sync_current_message_prompt_envelope()
```

`MemcoreManager.build_prompt_context()` 仍调用 V1-compatible `MemorySystem.build_prompt_context()`，然后 Akane 自己拼 raw/episodic/semantic/history。

### 7.3 Akane store 仍写 prompt envelope

`companion_v01/store/core.py` 当前仍有：

```text
prompt_envelope_text column
upsert_message_prompt_envelope()
get_message_prompt_envelopes()
delete_message_prompt_envelope()
prune_message_prompt_envelopes()
```

列本身可以暂留；新权威切换后 writer/reader/pruner 必须停止并删除，不能长期和 MemCore projection 双写。

### 7.4 当前轮原生工具 history 仍由 Akane 私有函数拼

`companion_v01/engine.py` 仍有：

```text
_append_native_tool_history_batch()
_append_native_anthropic_tool_history_batch()
_append_native_openai_tool_history_batch()
```

当前轮调用仍可用，但跨轮历史必须切到 MemCore provider projection；验收后这些函数应删除或变成 MemCore adapter 的薄调用。

### 7.5 Manager 尚未完成进程级 Runtime 收敛

`MemcoreManager` 仍持有独立 index warmup executor，并缓存多个 `MemorySystem`。V2 最终设计要求同进程通过共享 `MemCoreRuntime` 管理 executor/lock/ownership。这个切片应在 read cutover 稳定后做，不要和 provider raw output 同轮大改。

## 8. 接下来严格按什么顺序做

### Slice A：补真实 Chat JSON raw-result（下一个立即执行的切片）

目标：同步和流式都能把真实模型原文交给 `complete_turn()`，但不改变用户可见回复。

主要文件：

```text
companion_v01/llm_runtime.py
companion_v01/engine.py
相关 LLM/final response tests
```

建议实现：

1. 新增内部结果类型，例如：

   ```text
   ChatJSONResult
   ├── parsed
   ├── raw_text
   ├── provider_profile / route（只放安全标识）
   ├── usage/cache fields（安全结构）
   └── error/status
   ```

2. 新增 `call_chat_json_result()`；旧 `call_chat_json()` 暂时只做 `.parsed` 薄适配。
3. 流式路径复用现有 `ChatJSONStreamResult.raw_text`，不要重新序列化。
4. Engine 内部传递 semantic final 和 raw output 两种表示；不要把 raw 塞进 QQ/UI 返回 JSON、日志或 prompt audit 全文。
5. `_complete_memcore_input_turn()` 传真实 raw；确实没有 raw 时仍显式传空并返回可诊断状态。
6. parse fallback、重试和最终失败都要测试：失败 raw 不能作为自然语言 final 落库。

验收：

- 同步结果保存的 raw 与 fake provider 原文完全相同；
- 流式结果保存的 raw 与 stream 拼接文本完全相同；
- parsed key 顺序变化不能影响保存的 raw；
- 没有任何 `json.dumps(parsed)` 冒充 raw；
- raw 不出现在用户响应、普通日志或 debug payload；
- 现有 QQ 文本、图片、TTS/表情流程不受影响。

完成后做一个聚焦 commit，不顺手切读链。

### Slice B：给 Akane manager 增加 Projection/Ledger 薄门面

目标：只暴露 MemCore 公共 API，不在 Akane 复制 renderer/ledger。

主要文件：

```text
companion_v01/memcore_integration/manager.py
tests/test_memcore_integration.py
```

增加：

```text
build_context_projection(provider_profile, namespace...)
record_request_projection(provider_profile, actual safe messages/hash...)
```

要求：

- provider profile 统一映射为 MemCore 的 `openai_chat / anthropic_messages / canonical_user_assistant`；
- profile 由实际 LLM route 决定，不由“个人/金融”决定；
- 返回结构化 status/reason/hash/source coverage；
- 不在 manager 自己重写 OpenAI/Anthropic message renderer；
- 不记录密钥、base64、绝对路径或完整敏感 audit 正文。

### Slice C：普通私聊/群聊 projection shadow

目标：先比较，不改变模型实际 prompt。

主要文件：

```text
companion_v01/engine_services/response_builder.py
companion_v01/llm_runtime.py
tests/test_memcore_integration.py
```

记录安全结构：

```text
provider_profile
source ids
projection hash
actual conversation hash
strict-prefix status
first divergence index/reason
```

不能：

- shadow 双发模型请求；
- shadow 双执行工具；
- 把 shadow projection 注入 prompt；
- 在日志保存完整对话、图片、路径或 key。

验收普通私聊和群聊：同一 provider/profile 下，上一轮 conversation + 真实 raw final 必须是下一轮 history 的字节前缀；Actor 归因不能丢。

### Slice D：切普通对话读权威

目标：`response_builder.py` 使用 `MemorySystem.build_context_projection()` 的 provider messages，停止 Akane 自己从 raw records 拼历史。

要求：

- 先切无工具普通私聊/群聊；
- 当前消息只能出现一次；
- summary/semantic projection 和 raw projection 顺序稳定；
- stable system prefix/persona/tool schema 仍由 Akane 管理，MemCore 只提供历史 messages；
- 同一切片停止新的 `prompt_envelope_text` 写入；
- 不长期保留“envelope 非空优先，否则 projection”的双权威；
- 旧数据库列可以保留，writer/reader/pruner 在 cleanup 删除。

用户当前只有自己使用 MemCore，不必为了第三方维护复杂 envelope migration。旧条目无法安全归属时使用 canonical fallback；不要把含路径/base64/key 的旧 envelope 搬入新 ledger。

### Slice E：切跨轮原生多工具 projection

目标：下一轮从 MemCore 恢复完全相同的 tool id/name/arguments/result 结构。

验收：

- 单工具；
- 两个以上并行工具；
- 乱序执行结果仍按 correlation 正确；
- OpenAI 和 Anthropic profile 分别 round-trip；
- error/cancelled branch 也闭合；
- 工具轮之后普通对话仍保持严格前缀；
- 验收后删除/变薄 `_append_native_*` 第二权威。

不要把“两个以上工具”写成模型必须遵守的业务硬规则；这里只验证通用并行能力不降级。

### Slice F：切 event/finance 读链

普通消息已稳定后，再让 `event.*` 和金融主动推送使用同一 projection 读权威。

要求：

- `event.finance` 只是中性 typed event，不出现“插件”字样，也不是越权 system instruction；
- 金融研究方法仍由稳定领域 system block/profile 提供，不在每条事件尾部忽隐忽现；
- 给用户看的推送格式保持现状，例如 `【财经快讯｜时间】...原文链接...`；
- 事件、普通消息、工具结果都只追加到底部，不建立第二缓存桶或第二历史区；
- 至少连续注入三条虚拟金融事件后再判断稳态缓存；
- 私聊推送正常不代表金融群聊对话正常，两条真实入口都要验收。

### Slice G：Runtime/compaction/旧权威 cleanup

读写都切稳后：

- manager 注入共享 `MemCoreRuntime`；
- 移除独立 index warmup executor/重复 lock ownership；
- compaction 只选择 closed-turn 前缀；
- 删除 Akane 私有 prompt renderer、prompt envelope writer/reader/pruner；
- 删除不再使用的分段 `record_*` helper；
- legacy import 若用户确认不需要，可删除；若保留只能是一次性维护 adapter；
- 不删除整个 Akane `MemoryStore`：附件、workspace、persona、任务等非记忆业务仍依赖它。

### Slice H：真实 provider 与云端验收

只有本地自动化通过后，再经用户确认部署云端。

必须分别走真实入口：

1. 个人 Bot 私聊连续三轮；
2. 个人 Bot 群聊连续三轮；
3. 金融 Bot 私聊主动推送至少三条；
4. 金融 Bot 群聊普通对话；
5. 一轮单工具；
6. 一轮并行多工具；
7. 工具后普通对话；
8. 图片轮前后；
9. 一次 compaction 前后；
10. TTS、表情资源、QQ 最终投递没有因主回复变化而丢失。

同时记录安全 usage/hash：

```text
prompt_cache_hit_tokens
prompt_cache_miss_tokens
命中率
projection/full-prefix hash
断点原因
```

先用 hash 证明结构正确，再看 provider cache usage；不能把“缓存尽力而为”当成结构不稳定的借口，也不能用本地直接调用 MemCore 的高命中代替真实 QQ 链路。

## 9. 禁止事项

- 不重开项目，不推倒当前 V2；
- 不按个人/金融/Bot QQ 号复制 Engine 或 MemCore integration；
- 不新建第二套金融 memory store；
- 不用 `seq_no ± N` 猜 stimulus/final/tool 关系；
- 不把中间工具 JSON 当最终 memory annotation；
- 不从 parsed dict 伪造 provider raw；
- 不让运行时健康状态反复增删同一个工具 schema；
- 不把 request id、当前时间、debug flag、动态检索片段放进稳定前缀；
- 不为了某个例子硬编码“必须两个工具”“必须几个来源”；
- 不吞异常或假成功；必须返回 status/reason；
- 不修改/提交 `.env`、数据库、runtime logs、users_data、缓存、构建产物；
- 不覆盖两个仓库现有脏改动；
- 不未经用户授权改云端、QQ 登录、NapCat token、API key 或 Bot profile；
- 不只测后端字段；真实表现要看 QQ 回复、图片、TTS、表情、推送交付是否成立。

## 10. 验证命令

### Akane 每个宿主切片至少运行

```powershell
cd F:\Akane\AkaneCompanionLab

python -m py_compile companion_v01\engine.py companion_v01\memcore_integration\manager.py
python -m ruff check companion_v01\engine.py companion_v01\memcore_integration\manager.py tests\test_memcore_integration.py tests\test_native_web_search_tooling.py tests\test_plugin_reasoning.py
python -m unittest tests.test_memcore_integration tests.test_native_web_search_tooling tests.test_plugin_reasoning tests.test_plugin_engine_bridge tests.test_engine_visible_context tests.test_memory_timeline
python -m unittest tests.test_backend_route_modules
git diff --check
```

高风险 read cutover 完成后再运行：

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

### 修改 MemCore 包时

优先使用包仓库规定的环境：

```powershell
cd F:\Akane\memcore

uv run --extra dev python -m unittest discover -s tests -v
uv run --extra dev ruff check .
uv run --extra dev ruff format --check .
git diff --check
uv run --extra dev python -m build
```

注意：包仓库当前有用户脏改，不要对整仓执行会机械重写这些文件的 formatter 后一起提交。只 stage 本轮文件。

## 11. 提交前复核

```powershell
git status --short
git diff --stat
git diff --cached --name-only
git diff --check
```

只 stage 本轮相关文件。一个边界清晰、测试通过的切片做一个聚焦 commit；不要把多个切片揉成一次大改。

## 12. 新账号可以直接从这里开始

建议把下面这段连同本文件一起交给新账号：

```text
伙伴，接手 MemCore Unified Timeline V2 → Akane 主线。

先完整阅读：
- F:\Akane\AkaneCompanionLab\AGENTS.md
- F:\Akane\memcore\AGENTS.md
- F:\Akane\memcore\docs\unified_timeline_v2_design_v1.md 的 §22、§24
- F:\Akane\AkaneCompanionLab\docs\memcore_v2_akane_cutover_handoff_20260721.md

当前 MemCore 核心到 8e5ee7f，Akane V2 写链到 9f5df7c；不要重做已完成部分。下一刀先做 handoff 文档 §8 的 Slice A：给同步/流式 Chat JSON 补真实 raw-result 内部传递，让 complete_turn 保存真实 provider raw，禁止 json.dumps(parsed) 冒充原文。先查代码和测试，再小步完整实现、验证、聚焦提交。不要部署云端，不要覆盖两个仓库现有脏改。
```
