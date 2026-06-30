# Akane 接入 memcore 实施方案 v1

## 目的

这篇文档用于在上下文压缩或换模型后继续接上 Akane → memcore 的迁移工作。它记录当前 Akane 记忆链路、memcore 对应能力、推荐切片顺序、要改的文件和验证口径。

结论先写前面:先不要继续拆 `companion_v01/store/core.py` 里的 messages / summaries / semantic 记忆区块。下一步应该以适配层方式接入 sibling repo `../memcore`，先双写和影子验证，再逐步切读侧、可见三层、压缩链路。

## 当前状态

- Akane 工作区仍有一处 capcore handoff 文档改动: `docs/capability_adapter_v1_m1_handoff_prompt.md`。memcore 接入不要混入这条线。
- `requirements.txt` 已加入 `-e ../memcore`。`MemcoreManager` 也有 sibling path fallback，方便本地未安装 editable 时仍可在 `MEMORY_BACKEND=dual|memcore` 下加载。
- Slice 0 已完成:配置项、settings catalog、可选 manager bootstrap、空工具/诊断模块、基础测试。
- Slice 1 已完成:同步/流式 turn 生命周期会在非 transient turn 下把 user raw、final assistant raw、user memory_metadata 双写进 memcore；旧 Akane 仍是读侧和用户可见行为来源。
- Slice 2 已完成:`MEMCORE_SHADOW_COMPARE=true` 时，legacy `retrieve_memory` 工具返回后会额外跑 memcore 影子检索，只把结构化对比写入 debug/state，不改变 followup_context 或用户可见回复。
- Akane 当前记忆主链路仍是旧系统:
  - `companion_v01/engine.py` 构造 `MemoryStore`、`VectorStore`、`RetrievalService`、`MemoryCompactionService`。
  - `process_turn()` / `process_turn_stream()` 先 `store.add_message(role="user")`，再取 recent raw / episodic / semantic，跑旧 pre-retrieval；旧 user vector policy 落定后，非 transient 且 `index_in_vector=True` 的 user raw 同步调用 `memcore_manager.record_user_turn()`。
  - 最终回复后把 `final_output["memory_metadata"]` 回写到 user raw: `_apply_memory_metadata_to_user_record()` → `store.update_message_memory_metadata()` → `_upsert_raw_record()`；随后对 `index_in_vector=True` 的 user raw 同步调用 `memcore_manager.update_turn_metadata()`。
  - `index_in_vector=false` 的 user raw 会在 memcore 双写里结构化跳过，避免“记忆查询本身”污染后续 memcore 检索；metadata 回写也只对已双写 user raw 执行。
  - assistant 最终回复、工具 preface、部分工具结果也会写 raw，并调 `_schedule_summary_cycle()`；Slice 1 只双写 final assistant raw，不双写工具 preface/tool raw。
  - `retrieve_memory` 工具在 `retrieval_engine.execute_retrieve_memory_tool()` 内重新收集可见三层 source_id，合并 `_memory_retrieval_exclude_source_ids`，再走 `RetrievalService.run_explicit()`。
  - `MEMCORE_SHADOW_COMPARE=true` 时，`execute_retrieve_memory_tool()` 会调用 `memcore_manager.shadow_retrieve_memory()`，并在 `state_updates["memory_retrieval"]["memcore_shadow"]` 写入 ok/status/reason、legacy/memcore snippet count、短 hash 和 overlap 计数；不写 memcore 片段全文。
  - `read_memory_timeline` 当前走 `MemoryTimelineService.read()`，读旧 `MemoryStore` raw 并返回结构化时间线。
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
  tools.py             # retrieve_memory / read_memory_timeline 的 memcore 实现包装
  diagnostics.py       # 影子对比、状态上报，可后置
```

不要把这些逻辑塞进 `engine.py`。`engine.py` 只增加很薄的调用点。

## 适配器设计

### AkaneLLMClient

memcore 要求注入 `memcore.LLMClient`。Akane 现有 `LLMRuntime` 可包装:

- `TaskType.SUMMARY` / `SEMANTIC` / `REINFORCEMENT`: 调 `llm.call_aux_json(system_prompt, user_prompt, fallback, temperature, prompt_cache_key=...)`。
- `TaskType.VERIFIER` + `ResponseFormat.NDJSON`: 调 `llm.call_aux_ndjson(...)`，把 `NDJSONCallResult.events` 填进 `LLMResult.data`。
- 任何异常都返回 `LLMResult(ok=False, data=request.fallback, error=...)`，不要抛裸异常。
- `LLMResult.attempts`、`latency_ms` 可以先粗略填，后面再精细化。

### AkaneEmbeddingProvider

Akane 的 `BaseEmbeddingProvider` 和 memcore 的 `EmbeddingProvider` 不是同一个 ABC，不能直接传。需要包装类继承 `memcore.EmbeddingProvider`:

- `name` / `version` / `dimension` 转发 Akane provider。
- `embed_text()` / `embed_texts()` 转发。
- 如果 Akane 当前 provider 是 hashed，要在状态里显式标记 degraded。第一切片可以允许继续跑，但不要在文档或日志里假装它是高质量语义检索。

### MemoryConfig 映射

第一阶段用 count policy，不上 token policy:

```text
raw_trigger_count              <- SUMMARY_TRIGGER_COUNT
summary_batch_size             <- SUMMARY_BATCH_SIZE
episodic_visible_max           <- EPISODIC_VISIBLE_MAX
episodic_compact_trigger_count <- EPISODIC_COMPACT_TRIGGER_COUNT
episodic_compact_batch_size    <- EPISODIC_COMPACT_BATCH_SIZE
semantic_visible_limit         <- SEMANTIC_VISIBLE_LIMIT
semantic_reinforcement_*       <- Akane 现有 SEMANTIC_REINFORCEMENT_*
visible_memory_scope           <- "user" for Akane 陪伴连续感，或配置项控制
enable_flavor                  <- true/false 配置项，Akane 桌宠可开，通用模式可关
```

注意 memcore 的差值约束会在构造时校验，Akane 配置如果不满足要启动时报结构化错误，不能吞掉。

### 存储路径

`MemorySystem(storage_dir=...)` 实际传给 `SQLiteMemoryStore(db_path)`，所以这里应传数据库文件路径，不是目录:

```text
base_dir / "memcore_v01.db"
```

如果未来要每用户分库再调整，但第一阶段单库最简单。

## 配置项

先加最小配置，不要一次铺太多:

```text
MEMORY_BACKEND=legacy       # legacy | dual | memcore
MEMCORE_STORAGE_PATH=       # 空则 base_dir / memcore_v01.db
MEMCORE_VISIBLE_SCOPE=user  # conversation | user
MEMCORE_ENABLE_FLAVOR=true
MEMCORE_SHADOW_COMPARE=false
```

含义:

- `legacy`: 完全旧系统。
- `dual`: 旧系统仍作为读侧和用户可见结果，memcore 同步写入、压缩、可选影子检索。
- `memcore`: 读写都走 memcore。不要第一切片直接默认它。

`settings_catalog.py` 也要补这些字段，否则 drift guard 会红。

## 切片顺序

### Slice 0: 依赖与空接线

目标: Akane 能稳定 import memcore，但不改变行为。

状态:已完成。

改动:

- `requirements.txt` 加 `-e ../memcore`，注释说明和 capcore 一样是 sibling package。
- 新增 `companion_v01/memcore_integration/` 空模块和 adapter 骨架。
- 新增配置项与 settings catalog。
- `engine.__init__` 在 `MEMORY_BACKEND != "legacy"` 时构造 `self.memcore_manager`，失败要结构化记录并回退 legacy，不能让聊天主流程启动失败。

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
  - `retrieve_for_turn(current=current_record, query=query, keywords=..., time_hint=..., source_layers=..., subject_scopes=..., categories=..., importance_min=..., limit=...)`
- followup 文案保持 Akane 当前文案，减少模型行为变化。
- state_updates 仍输出 `memory_retrieval`，字段名尽量兼容前端/debug。
- `MEMORY_BACKEND=memcore` 且 memcore read 成功时，`retrieve_memory` 的 followup 使用 memcore snippets，`retrieval_backend="memcore"`，不再调用 legacy retrieval。
- memcore read 失败/不可用时结构化记录 `memcore_read`，不写 snippets 全文，然后 fallback 到 legacy retrieval。
- memcore read 成功但没有命中时保持现有 no-hit followup，不 fallback 到 legacy，避免 memcore 模式下读侧语义不清。
- `MEMORY_BACKEND=legacy|dual` 下仍以 legacy 为读侧；`MEMCORE_SHADOW_COMPARE=true` 时继续只记录 hash/stat shadow payload。
- 本切片不切 `read_memory_timeline`、最终 prompt 可见三层、压缩链路，也不删除旧 router/旧 retrieval 代码。

验证:

- `retrieve_memory` 工具 schema 不变。
- 可见三层和本轮 source_id 不重复返回。
- metadata filters 能前置缩候选: 用两类 categories 构造数据，传 category 只命中对应记忆。
- memcore read 成功时 legacy retrieval service 未被调用。
- memcore read 失败时 fallback legacy，且 `memcore_read` 不含 snippets。
- memcore no-hit 时使用既有 no-hit 文案。

### Slice 4: 切 `read_memory_timeline` 工具读侧

目标: 时间线精确读由 memcore 提供，但工具名、输入、结构化状态保持。

状态:已完成。

改动:

- 新增 `MemcoreTimelineToolService` 作为旧 `ReadMemoryTimelineToolHandler` 可直接使用的 timeline facade。
- 调 `MemorySystem.read_timeline(date_from, date_to, time_periods)`。
- 返回仍要匹配 Akane 当前工具 followup 习惯。
- `MEMORY_BACKEND=memcore` 且 memcore 可用时，工具读侧走 memcore；memcore 不可用/失败时 fallback 到 legacy `MemoryTimelineService`。
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
- `response_builder.prepare_context()` 在 `MEMORY_BACKEND=memcore` 且 memcore 可用时，用 memcore 三层文本替换 legacy raw/episodic/semantic 渲染。
- memcore context 失败或不可用时保留 legacy fallback，避免聊天主流程因为 memcore 临时不可用而中断。
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
- 如果 `MEMORY_BACKEND=memcore` 但 memcore manager 不可用，旧 compaction 暂时保留 fallback，避免聊天在降级 legacy prompt 时彻底失去摘要退路。

验证:

- raw 到达阈值后 memcore 创建 summary，且 raw 可见窗口缩小。
- summary 到达阈值后创建/强化 semantic。
- LLM 失败不提交空摘要。
- 单测确认 memcore 可用时 `_schedule_summary_cycle()` 不再排旧队列，`_run_summary_cycle()` 转调 memcore sync compaction。

### Slice 7: 旧数据迁移/回填

目标: 把 legacy SQLite 里的现有 raw / summaries / semantic_summaries 导入 memcore。

这一步不要提前做。等新写入稳定后再做一次性 migration:

- 遍历 legacy messages，按 `profile_user_id/session_id/character_pack_id/source_id/timestamp/memory_metadata` 写入 memcore。
- 遍历 legacy summaries / semantic_summaries 写入 memcore store，或先只迁 raw 让 memcore 后台重新压缩。
- 迁移必须幂等，同 source_id 重复运行不能复制多条。
- 迁移后 `reindex_all()`。

## 不要做的事

- 不要直接删除 `RetrievalService` / `MemoryCompactionService` / `VectorStore`。先分流，等 memcore 模式稳定后再清理。
- 不要继续大拆 `store/core.py` 的记忆表方法。它们可能会被 memcore 替换，继续拆会制造无效工作。
- 不要把附件、礼物、生成文件、workspace、persona 这些业务表迁到 memcore。memcore 只接长期对话记忆。
- 不要让 memcore JSON 输出契约替换 Akane 当前 final output JSON。Akane 已有更大的桌宠输出 schema，只需要把 `memory_metadata` 对齐即可。
- 不要第一切片启用 token compaction。先 count policy 上车，token counter 以后单独切。

## 风险点

- Akane 当前 embedding 在 HuggingFace 加载失败时会退 hashed；memcore 原则是不静默退化。适配期要显式上报 degraded，后续再决定是否 fail closed。
- Akane 旧 pre-retrieval router 还在。memcore 设计是不内置 router。切 memcore 读侧时应优先关闭/绕过 pre-retrieval，只保留聊天模型主动工具调用。
- `MemorySystem(storage_dir)` 是 SQLite db path，不是目录。
- memcore `LLMClient` 要返回结构化失败，不能让 LLMRuntime 异常穿透到聊天主流程。
- 角色包隔离必须映射到 `domain_id`，否则不同角色可能共享同一用户长期记忆。
- QQ 群聊 actor 必须用稳定 ID，不要用昵称当 actor id。

## 最小验收标准

第一阶段完成后，至少满足:

- `MEMORY_BACKEND=legacy` 时所有现有测试行为不变。
- `MEMORY_BACKEND=dual` 时一轮对话会在 legacy 和 memcore 中写入相同 source_id 的 user / assistant raw。
- final output 的 `memory_metadata` 能回写到 memcore user raw，并更新索引状态或 pending 状态。
- `retrieve_memory` 和 `read_memory_timeline` 外部 schema 不变。
- memcore 失败只降级 legacy，不影响用户看到回复。
- `git diff --check` 通过。
