# Akane 接入 memcore 实施方案 v1

## 目的

这篇文档用于在上下文压缩或换模型后继续接上 Akane → memcore 的迁移工作。它记录当前 Akane 记忆链路、memcore 对应能力、推荐切片顺序、要改的文件和验证口径。

结论先写前面：Akane 的对话记忆主路已经切到版本化 `memcore` 包。后续不要再把旧 retrieval/router/compaction 当成主线扩展；旧链路只保留为显式 `MEMORY_BACKEND=legacy|dual` 的兼容、迁移和对比工具，后续按切片清理。

## 当前状态

- Akane 工作区仍有一处 capcore handoff 文档改动: `docs/capability_adapter_v1_m1_handoff_prompt.md`。memcore 接入不要混入这条线。
- `requirements-packages.txt` 精确锁定 `memcore` 发行版。`MemcoreManager` 只做正常包导入；缺包时结构化报告 unavailable，不注入 sibling source path。
- Slice 0 已完成:配置项、settings catalog、可选 manager bootstrap、空工具/诊断模块、基础测试。
- Slice 1 已完成:同步/流式 turn 生命周期会在非 transient turn 下把 user raw、final assistant raw、user memory_metadata 写进 memcore；旧 Akane store 仍保留业务表和迁移来源。
- Slice 2 已完成:`MEMCORE_SHADOW_COMPARE=true` 时，legacy `retrieve_memory` 工具返回后会额外跑 memcore 影子检索，只把结构化对比写入 debug/state，不改变 followup_context 或用户可见回复。
- Akane 当前对话记忆主链路是 memcore:
  - `companion_v01/engine.py` 构造 `MemoryStore`、`VectorStore`、`RetrievalService`、`MemoryCompactionService`。
  - `MemoryStore` 仍承载附件、礼物、workspace、persona、旧数据回填等业务表；不要把它等同于“旧记忆主路”。
  - `process_turn()` / `process_turn_stream()` 仍先 `store.add_message(role="user")` 保持现有业务链路；非 transient 且 `index_in_vector=True` 的 user raw 会调用 `memcore_manager.record_user_turn()` 进入 memcore。
  - 最终回复后把 `final_output["memory_metadata"]` 回写到 user raw: `_apply_memory_metadata_to_user_record()` → `store.update_message_memory_metadata()` → `_upsert_raw_record()`；随后对 `index_in_vector=True` 的 user raw 同步调用 `memcore_manager.update_turn_metadata()`。
  - `index_in_vector=false` 的 user raw 会在 memcore 双写里结构化跳过，避免“记忆查询本身”污染后续 memcore 检索；metadata 回写也只对已双写 user raw 执行。
  - assistant 最终回复写 raw 后调 `_schedule_summary_cycle()`；`MEMORY_BACKEND=memcore` 下压缩由 memcore 接管。
  - `retrieve_memory` 工具在 `MEMORY_BACKEND=memcore` 下走 memcore read side，不再 fallback legacy retrieval。
  - `MEMCORE_SHADOW_COMPARE=true` 时，`execute_retrieve_memory_tool()` 会调用 `memcore_manager.shadow_retrieve_memory()`，并在 `state_updates["memory_retrieval"]["memcore_shadow"]` 写入 ok/status/reason、legacy/memcore snippet count、短 hash 和 overlap 计数；不写 memcore 片段全文。
  - `read_memory_timeline` 在 `MEMORY_BACKEND=memcore` 下走 memcore timeline，不再 fallback legacy timeline。
- memcore 已经具备目标能力:
  - `MemorySystem.record_user_turn()` / `record_assistant_turn()` / `update_turn_metadata()`。
  - `build_prompt_context()` / `render_prompt_context()`。
  - `retrieve_for_turn()`，会排除当前 prompt 已可见三层和本轮 user source_id。
  - `read_timeline()`，按日期/时间段精确读 raw。
  - `compact_due_background()` / `compact_due_sync()`。
  - metadata 前置过滤、五级放宽、可选 NumPy 内存索引加速。

## 关键映射

Akane → memcore namespace:

```text
Namespace(
  tenant_id="",                       # 先不引入多租户
  user_id=profile_user_id,            # Akane 真实用户隔离键
  domain_id=character_pack_id or "",  # Akane 当前角色包记忆隔离
  conversation_id=session_id,         # 当前会话窗口
  actor=Actor(...) or None            # QQ 群聊/多人场景第二阶段接
)
```

说明:

- Akane 当前实际隔离边界是 `profile_user_id + character_pack_id`，所以 memcore 的 `domain_id` 应承接 `character_pack_id`。
- `conversation_id=session_id` 只管 visible raw/episodic/semantic 窗口。memcore 检索仍能在同 hard key 下跨会话搜历史。
- 群聊发言人不要塞进 `user_id`。多人软标签应后续从 QQ payload 映射为 `Actor(stable_id=平台稳定ID, display_name=昵称)`。

Akane source_id:

- 双写时 memcore 必须复用 legacy `source_id`，不要另造 id。
- user raw: `record_user_turn(..., source_id=user_record["source_id"], timestamp=now_ts)`。
- assistant raw: `record_assistant_turn(..., source_id=assistant_record["source_id"], timestamp=assistant_ts)`。
- metadata 回写: `update_turn_metadata(user_record["source_id"], memory_metadata)`。

## 推荐目录

新增目录:

```text
companion_v01/memcore_integration/
  __init__.py
  adapters.py          # AkaneLLMClient / AkaneEmbeddingProvider / 可选 TokenCounter
  manager.py           # MemcoreManager: namespace 构造、MemorySystem 缓存、双写门面
  timeline.py          # read_memory_timeline / open_memory 的产品与 namespace 薄适配
  diagnostics.py       # 影子对比、状态上报，可后置
```

不要把这些逻辑塞进 `engine.py`。`engine.py` 只增加很薄的调用点。

## 适配器设计

### AkaneLLMClient

memcore 要求注入 `memcore.LLMClient`。Akane 现有 `LLMRuntime` 可包装:

- `TaskType.SUMMARY` / `SEMANTIC` / `REINFORCEMENT`: 调 `llm.call_aux_json(system_prompt, user_prompt, fallback, temperature, prompt_cache_key=...)`。
- MemCore 的 `LLMClient` 只承接 summary / semantic / reinforcement 三类 JSON 压缩任务；检索由包内确定性排序、硬过滤、关系完整性与 diagnostics 完成，不追加 verifier 模型调用。
- 任何异常都返回 `LLMResult(ok=False, data=request.fallback, error=...)`，不要抛裸异常。
- `LLMResult.attempts`、`latency_ms` 可以先粗略填，后面再精细化。

### AkaneEmbeddingProvider

Akane 的 `BaseEmbeddingProvider` 和 memcore 的 `EmbeddingProvider` 不是同一个 ABC，不能直接传。需要包装类继承 `memcore.EmbeddingProvider`:

- `name` / `version` / `dimension` 转发 Akane provider。
- `embed_text()` / `embed_texts()` 转发。
- 如果 Akane 当前 provider 是 hashed，要在状态里显式标记 degraded。第一切片可以允许继续跑，但不要在文档或日志里假装它是高质量语义检索。

### MemoryConfig 映射（当前实现；早期 count 方案已废弃）

MemCore 只使用 token/ratio planner：

```text
raw_token_trigger              <- MEMCORE_RAW_TOKEN_TRIGGER
raw_token_batch_ratio          <- MEMCORE_RAW_TOKEN_BATCH_RATIO
retrieval_result_token_budget  <- MEMCORE_RETRIEVAL_RESULT_TOKEN_BUDGET
native_timeline_page_token_budget <- MEMCORE_NATIVE_TIMELINE_PAGE_TOKEN_BUDGET
episodic_visible_max           <- EPISODIC_VISIBLE_MAX
episodic_compact_trigger_count <- EPISODIC_COMPACT_TRIGGER_COUNT
episodic_compact_batch_size    <- EPISODIC_COMPACT_BATCH_SIZE
semantic_visible_limit         <- SEMANTIC_VISIBLE_LIMIT
semantic_reinforcement_*       <- Akane 现有 SEMANTIC_REINFORCEMENT_*
visible_memory_scope           <- "user" for Akane 陪伴连续感，或配置项控制
enable_flavor                  <- true/false 配置项，Akane 桌宠可开，通用模式可关
```

Akane 注入明确标为 `estimated` 的 TokenCounter。MemCore 的 token/ratio 与
episodic 差值约束会在构造时校验；配置不合法要启动时报结构化错误，不能吞掉。

### 存储路径

`MemorySystem(storage_dir=...)` 实际传给 `SQLiteMemoryStore(db_path)`，所以这里应传数据库文件路径，不是目录:

```text
base_dir / "memcore_v01.db"
```

如果未来要每用户分库再调整，但第一阶段单库最简单。

## 配置项

先加最小配置，不要一次铺太多:

```text
MEMORY_BACKEND=memcore      # memcore | legacy | dual
MEMCORE_STORAGE_PATH=       # 空则 base_dir / memcore_v01.db
MEMCORE_VISIBLE_SCOPE=user  # conversation | user
MEMCORE_ENABLE_FLAVOR=true
MEMCORE_SHADOW_COMPARE=false
MEMCORE_RAW_TOKEN_TRIGGER=48000
MEMCORE_RAW_TOKEN_BATCH_RATIO=0.67
MEMCORE_RETRIEVAL_RESULT_TOKEN_BUDGET=2000
```

含义:

- `memcore`: 主运行模式。读写、可见三层、检索工具、时间线和压缩都走 memcore。
- `legacy`: 显式旧系统兼容模式，用于排查或临时回退。
- `dual`: 迁移/对比模式。旧系统作为读侧，memcore 同步写入、压缩、可选影子检索。

`settings_catalog.py` 也要补这些字段，否则 drift guard 会红。

## 切片顺序

### Slice 0: 依赖与空接线

目标: Akane 能稳定 import memcore。

状态:已完成。

改动:

- 当时的源码依赖已在 M64 删除；当前由 `requirements-packages.txt` 和 wheelhouse/index 提供精确发行版。
- 新增 `companion_v01/memcore_integration/` 空模块和 adapter 骨架。
- 新增配置项与 settings catalog。
- `engine.__init__` 在 `MEMORY_BACKEND != "legacy"` 时构造 `self.memcore_manager`，失败要结构化记录；`memcore` 主路不能静默切回旧记忆 prompt/tools。

验证:

- `python -m py_compile companion_v01/memcore_integration/*.py config.py companion_v01/settings_catalog.py`
- `python -m unittest tests.test_settings_catalog -v`
- `git diff --check`

### Slice 1: 双写 raw，不切读侧

目标: legacy 行为不变，memcore 拿到同样 raw。

状态:已完成。

改动点:

- user 写入后、legacy `index_in_vector` 策略应用后调用 `memcore_manager.record_user_turn(...)`，复用 legacy source_id / timestamp / memory_metadata。
- `index_in_vector=false` 的 user raw 要返回结构化 `skipped`，不要写入 memcore 检索索引。
- assistant 最终回复写入后调用 `record_assistant_turn(...)`。
- 工具 preface / tool raw 是否双写先保守:第一切片可以只双写 user + final assistant。确认稳定后再扩展到 preface/tool raw。
- 最终回复 metadata 回写后调用 `memcore_manager.update_turn_metadata(source_id, memory_metadata)`。
- 每轮结束后 `compact_due_background()`，异常只记状态，不影响回复。

验证:

- 新增 `tests/test_memcore_dual_write.py`:
  - fake LLM + hashed embedding。
  - process 一轮后 legacy user/assistant 仍存在。
  - memcore SQLite 中有同 source_id raw。
  - metadata 回写后 memcore raw metadata 更新。
- 跑 `tests.test_backend_route_modules`，确保主路由不变。

### Slice 2: memcore 影子检索

目标: 不改变用户可见结果，但能比较 legacy 和 memcore 检索。

状态:已完成。

改动:

- `MEMCORE_SHADOW_COMPARE=true` 时，在 `retrieve_memory` legacy 工具执行后，额外调用 memcore `retrieve_for_turn()`。
- 只记录统计: query/filters 仍沿用现有 `memory_retrieval.tool_call`，shadow payload 只记录 legacy snippet count、memcore snippet count、短 snippet hash、overlap hash count、latency/status/reason。不要把 memcore 全文日志打出去。
- 影子失败不影响工具结果。

验证:

- 测试 shadow 开关下工具仍返回 legacy followup。
- memcore 异常时 state_updates 中有结构化 shadow error，不影响 `tool_type="retrieve_memory"`。

### Slice 3: 切 `retrieve_memory` 工具读侧

目标: 保留外部工具名和 schema，把底层改成 memcore。

状态:已完成。

改动:

- 在 `retrieval_engine.execute_retrieve_memory_tool()` 或新 wrapper 中按 `MEMORY_BACKEND` 分流。
- memcore 路径调用:
  - 当前轮 user record 从 manager 取或按 source_id 查 memcore。
  - `retrieve_for_turn_structured(current=..., query=..., entity_anchors=..., topic_terms=..., time_hint=..., source_layers=..., memory_facets=..., about_roles=...)`；模型不控制隐藏 limit，包默认最多返回 6 个命中组。
- followup 文案保持 Akane 当前文案，减少模型行为变化。
- state_updates 仍输出 `memory_retrieval`，字段名尽量兼容前端/debug。
- `MEMORY_BACKEND=memcore` 且 memcore read 成功时，`retrieve_memory` 的 followup 使用 memcore snippets，`retrieval_backend="memcore"`，不再调用 legacy retrieval。
- memcore read 失败/不可用时结构化记录 `memcore_read`，不写 snippets 全文，不 fallback 到 legacy retrieval。
- memcore read 成功但没有命中时保持现有 no-hit followup，不 fallback 到 legacy，避免 memcore 模式下读侧语义不清。
- `MEMORY_BACKEND=legacy|dual` 下仍以 legacy 为读侧；`MEMCORE_SHADOW_COMPARE=true` 时继续只记录 hash/stat shadow payload。
- 本切片不切 `read_memory_timeline`、最终 prompt 可见三层、压缩链路，也不删除旧 router/旧 retrieval 代码。

验证:

- `retrieve_memory` 工具 schema 不变。
- 可见三层和本轮 source_id 不重复返回。
- metadata filters 能前置缩候选: 用两类 categories 构造数据，传 category 只命中对应记忆。
- memcore read 成功时 legacy retrieval service 未被调用。
- memcore read 失败时不调用 legacy retrieval，且 `memcore_read` 不含 snippets。
- memcore no-hit 时使用既有 no-hit 文案。

### Slice 4: 切 `read_memory_timeline` 工具读侧

目标: 时间线精确读由 memcore 提供，但工具名、输入、结构化状态保持。

状态:已完成。

改动:

- 新增 `MemcoreTimelineToolService` 作为旧 `ReadMemoryTimelineToolHandler` 可直接使用的 timeline facade。
- 调 `MemorySystem.read_timeline(date_from, date_to, time_periods)`。
- 返回仍要匹配 Akane 当前工具 followup 习惯。
- `MEMORY_BACKEND=memcore` 时工具读侧走 memcore；memcore 不可用/失败时返回结构化空结果，不 fallback 到 legacy `MemoryTimelineService`。
- 为匹配 Akane legacy 行为，timeline 工具在 memcore 侧按 profile + character 跨 conversation 精确读 raw，并排除本轮 current source_id。
- 旧 timeline mirror、backfill、认识第 N 天提示仍暂时由 legacy `MemoryTimelineService` 提供，本切片不删除旧服务。

验证:

- 非法日期返回 structured invalid，不静默整天读取。
- 单日、多日、time_periods 均有测试。
- 单测确认 `ReadMemoryTimelineToolHandler` 在 memcore 模式下使用 memcore adapter，不调用 legacy read。

### Slice 5: 切最终 prompt 的可见三层

目标: 用 memcore 的 `build_prompt_context()` / `render_prompt_context()` 替代 legacy recent raw/summary/semantic 渲染。

状态:5a 已完成。为了先让 Akane 体验 memcore 记忆，本切片早于 Slice 4 落地；timeline 工具仍留到 Slice 4 单独切。

建议做法:

- 不要在第一步改 `PromptBuilder` 大结构。
- 先在 `response_builder.prepare_context()` 做分流，尽量复用现有 raw/episodic/semantic prompt 插槽。
- `MEMORY_BACKEND=memcore` 时 legacy `recent_*` 可以逐步置空或只保留 raw 兼容，避免重复给模型两套记忆。
- 确认 prompt cache: 固定系统说明仍放前面，memcore 动态记忆放后面。

5a 实际改动:

- `MemcoreManager.build_prompt_context()` 调 memcore `MemorySystem.build_prompt_context()`，并用 memcore renderer 分层输出 `raw_text / episodic_text / semantic_text`。
- `response_builder.prepare_context()` 在 `MEMORY_BACKEND=memcore` 时用 memcore 三层文本替换 legacy raw/episodic/semantic 渲染。
- memcore context 失败或不可用时返回空 memcore context，不 fallback 到 legacy prompt 记忆，避免两条记忆链路同时进入模型。
- 本切片不删除 legacy store、router、timeline、compaction，也不迁旧数据。

验证:

- prompt audit 中能看到 memcore 可见三层，但没有重复 legacy 三层。
- raw 渲染带日期/星期，跨天分组正常。
- 已可见三层不会被后续 retrieve 重复检索。
- 单测确认 final prompt 构建在 memcore 模式下使用 memcore 三层，不夹带 legacy 三层文本。

### Slice 6: 切压缩链路

目标: memcore 接管 raw → summary → semantic。

状态:6a 已完成。memcore 可用且 `MEMORY_BACKEND=memcore` 时，旧 `MemoryCompactionService` 不再接收 summary queue 调度；完整一轮 assistant 写入后仍由 `_schedule_memcore_compaction()` 触发 memcore 后台压缩。同步 `_run_summary_cycle()` 在 memcore 模式下转调 `compact_due_sync()`，方便测试/维护入口继续可用。

改动:

- `MemoryCompactionService` 在 `MEMORY_BACKEND=memcore` 时不再调旧 compaction，或变成兼容空壳。
- 保留 legacy store 的非记忆业务表。不要删除 `MemoryStore`，附件、礼物、文件、persona、任务仍大量依赖它。
- 后台关闭时 `engine.close()` 调 `memcore_manager.close()`。
- 如果 `MEMORY_BACKEND=memcore` 但 memcore manager 不可用，旧 compaction 不应作为静默主路 fallback；返回结构化状态，避免恢复双链路。

验证:

- raw 到达阈值后 memcore 创建 summary，且 raw 可见窗口缩小。
- summary 到达阈值后创建/强化 semantic。
- LLM 失败不提交空摘要。
- 单测确认 memcore 可用时 `_schedule_summary_cycle()` 不再排旧队列，`_run_summary_cycle()` 转调 memcore sync compaction。

### Slice 7: 旧数据迁移/回填

目标: 把 legacy SQLite 里的现有 raw / summaries / semantic_summaries 导入 memcore。

状态:7a 已完成 raw-only 手动回填入口。

这一步不要提前做。等新写入稳定后再做一次性 migration:

- 遍历 legacy messages，按 `profile_user_id/session_id/character_pack_id/source_id/timestamp/memory_metadata` 写入 memcore。
- 遍历 legacy summaries / semantic_summaries 写入 memcore store，或先只迁 raw 让 memcore 后台重新压缩。
- 迁移必须幂等，同 source_id 重复运行不能复制多条。
- 迁移后 `reindex_all()`。

7a 实际改动:

- `MemcoreManager.import_legacy_raw_messages(...)` 从 legacy `iter_messages_for_vector_reindex()` 批量读取旧 raw，只导入 `role=user|assistant` 且符合 profile/character 过滤的记录。
- `AkaneMemoryEngine.backfill_memcore_from_legacy_raw(...)` 提供手动维护入口，不在启动或聊天链路自动执行。
- 回填复用旧 `source_id`，依赖 memcore store 的同 namespace 幂等写入；重复执行不会复制同一条记忆。
- 暂不搬旧 `memory_summaries` / `memory_semantic_summaries`。需要旧历史长期记忆时，先 raw 回填，再由 memcore 压缩链路重新生成摘要和语义层。

## 不要做的事

- 不要直接一次性删除 `RetrievalService` / `MemoryCompactionService` / `VectorStore`。memcore 主路稳定后按切片清理，避免误删附件、workspace、评测等仍依赖的非记忆能力。
- 不要继续大拆 `store/core.py` 的记忆表方法。它们可能会被 memcore 替换，继续拆会制造无效工作。
- 不要把附件、礼物、生成文件、workspace、persona 这些业务表迁到 memcore。memcore 只接长期对话记忆。
- 不要让 memcore JSON 输出契约替换 Akane 当前 final output JSON。Akane 已有更大的桌宠输出 schema，只需要把 `memory_metadata` 对齐即可。
- 不要第一切片启用 token compaction。先 count policy 上车，token counter 以后单独切。

## 风险点

- Akane 当前 embedding 在 HuggingFace 加载失败时会退 hashed；memcore 原则是不静默退化。适配期要显式上报 degraded，后续再决定是否 fail closed。
- Akane 旧 pre-retrieval router 在 `MEMORY_BACKEND=memcore` 下已绕过。memcore 设计是不内置 router，后续不要把旧 router 当成新主路扩展。
- `MemorySystem(storage_dir)` 是 SQLite db path，不是目录。
- memcore `LLMClient` 要返回结构化失败，不能让 LLMRuntime 异常穿透到聊天主流程。
- 角色包隔离必须映射到 `domain_id`，否则不同角色可能共享同一用户长期记忆。
- QQ 群聊 actor 必须用稳定 ID，不要用昵称当 actor id。

## 最小验收标准

第一阶段完成后，至少满足:

- 默认 `MEMORY_BACKEND=memcore` 时，模型 prompt、retrieve_memory、read_memory_timeline、
  open_memory、压缩都不走旧记忆主链路。
- `MEMORY_BACKEND=legacy` 时旧兼容模式仍可显式运行。
- `MEMORY_BACKEND=dual` 时一轮对话会在 legacy 和 memcore 中写入相同 source_id 的 user / assistant raw。
- final output 的 `memory_metadata` 能回写到 memcore user raw，并更新索引状态或 pending 状态。
- `retrieve_memory` 的既有 schema 保持兼容；`read_memory_timeline` 在原有日期/anchor 模式之外增加
  精确 `time_range`、完整逻辑单元分页、`coverage/next_cursor` 与 projection；
  `open_memory` 用记忆工具返回的通用 `memory_id` 展开 card/content/sources 证据。
- `retrieve_memory` 的 raw 命中携带 `source_id/turn_id/timestamp` 并默认按真实关系扩成完整问答组；只有上下文仍不足时才扩窗。raw 锚点只能读取当前会话，跨会话命中改用片段时间做精确 `time_range`。
- `read_memory_timeline` 的模型工具调用始终使用 MemCore 配置的有限完整逻辑单元页；省略或传 0
  不代表无限读取，正数只能缩小而不能突破宿主上限。`status=partial` 时模型根据 selected / returned /
  remaining 体量决定只传 `next_cursor` 无损继续，或等待 `browse_memory` 目录能力获取概览；紧凑
  operation/material 证据用 `open_memory` 展开。
- MemCore 已按逻辑单元和 token 预算完成分页时，宿主不得再按字符数二次截断；普通未声明边界的
  工具结果仍保留通用安全整形。所有结果照常执行密钥和本地路径脱敏。
- 当前工具轮仍把完整页正文与结构化状态交给模型；写回 MemCore 的 operation observation 只保存包生成的
  receipt（selector、IDs、coverage、cursor、hash），不得重复持久化正文。
- memcore 失败返回结构化空/失败状态，不把旧记忆静默塞回 prompt/tools。
- `git diff --check` 通过。
