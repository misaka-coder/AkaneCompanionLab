# 原生多工具并行与 memcore 工具轨迹实现细节 v1

> 状态：核心链路已落地，待 live Sonnet 验收  
> 分支：`feature/qq-finance-assistant-emquant`  
> 建档日期：2026-07-12  
> 目标：即使对话上下文被压缩，也能继续完成“同轮多个原生工具调用、并行执行、结构化回填、memcore 持久化与后续压缩”这条主线。

## 1. 已确认的产品决定

1. 信任模型对同一轮工具任务的拆分。模型在同一轮发出的多个原生工具调用，默认可以并行执行。
2. 不维护“工具 A 只能和工具 B 并行”的组合白名单，也不因为组合未知向模型返回生硬的禁止并行错误。
3. 宿主只守重要边界：并发上限、超时、调用 ID 对应、单项失败隔离、明显共享状态冲突与副作用安全。
4. 如果少数工具必须互斥，应由工具自身的轻量元数据声明；宿主可在内部排队，不改变模型侧协议。
5. 同轮工具执行完成后，模型必须一次看到完整结果集，再决定继续调用工具还是生成最终回复。
6. 工具调用与工具结果应通过 `record_tool_exchange(...)` 进入 memcore raw，保留真实工具名、调用 ID、模型可见参数和模型可见结果。
7. `tool_trace` 在未压缩时属于可见 raw；普通 `retrieve` 默认排除，但显式 `categories=["tool_trace"]` 可以检索。
8. count 压缩时 `tool_trace` 不计入触发数量，但夹在普通消息压缩跨度内的轨迹一起进入摘要；token 压缩时轨迹 token 也参与容量压力。
9. 原生 `tool_use/tool_result` 是当前轮的权威结构化通道。memcore `tool_trace` 是跨轮连续性与后续压缩通道，两者都需要，但不得把同一结果在当前轮重复注入两份长文本。

## 2. 当前代码真实状态

### 2.1 Provider 解析会丢弃额外调用

`companion_v01/llm_runtime.py` 的：

- `_extract_native_tool_call(...)`
- `_stream_native_tool_call_from_parts(...)`

虽然能解析 provider 返回的多个调用，但只把 `invocations[0]` 转成内部 `_native_tool_call`。其余调用只增加 `native_tool_calls_extra` 指标，然后被丢弃。

### 2.2 Engine 每轮只处理一个调用

`companion_v01/engine.py` 当前：

- `_prepare_tool_round_decision(...)` 返回单个 `tool_call`；
- `_execute_and_record_tool_round(...)` 执行单个工具；
- `seen_tool_calls`、轮数预算和结果追加都以单调用为单位；
- 流式与非流式各有一套近似相同的循环。

### 2.3 Anthropic 工具历史是单调用交替消息

`_append_native_anthropic_tool_history_turns(...)` 当前每次追加：

```text
assistant: [tool_use]
user: [tool_result]
```

顺序调用时可用；并行调用应改成一个 assistant 消息包含同轮全部 `tool_use`，一个 user 消息包含对应全部 `tool_result`。

### 2.4 普通工具循环没有统一写 memcore

Akane 当前普通聊天工具执行完成后，主要把结果保存在：

- 当前 `process_turn()` 的 `tool_followups`；
- 当前 `process_turn()` 的 `native_tool_history_turns`；
- `ToolExecutionResult` 的 stream events / task workspace / NPC turns。

普通 `web_search`、行情、记忆读取等工具没有在统一执行点调用 `memcore_manager.record_tool_exchange(...)`，所以当前轮结束后，详细工具轨迹不会全部进入 memcore raw。

财经主动分析目前只在最终验证成功后写入一条 `market_feed` 工具证据；这不是分析期间每次真实工具调用的完整轨迹。

### 2.5 当前轮存在结果重复注入

原生 Anthropic 工具结果当前同时进入：

1. `extra_user_context` 的“本轮工具执行记录”长文本；
2. `post_user_turns` 的正式 `tool_use/tool_result` 块。

原生路径应以结构化块为权威；文本结果只保留给 legacy 工具或无法构造原生历史的兼容路径。

## 3. 目标协议

### 3.1 内部载体

新增兼容字段：

```python
NATIVE_TOOL_CALLS_FIELD = "_native_tool_calls"
```

约束：

- 值为非空 `list[dict]`；
- 每个元素保持现有单调用字典形状；
- 保留 `_tool_source`、`_tool_invocation_id`、必要时 `_tool_model_name`；
- 旧 `_native_tool_call` 在迁移期继续接受；
- 单调用也可统一放入列表，engine 内部不再区分一或多。

### 3.2 Provider 返回

OpenAI 与 Anthropic 的非流式、流式解析都返回全部合法调用，最大接收数量先定为 4。超过上限时：

- 保留前 4 个；
- 记录结构化指标和日志；
- 不向模型声称工具组合非法；
- 下一轮模型仍可继续调用剩余工具。

并发上限是资源治理，不是工具组合语义限制。

### 3.3 当前轮 Anthropic 历史

同一轮模型调用三个工具时：

```json
{
  "role": "assistant",
  "content": [
    {"type": "tool_use", "id": "toolu_1", "name": "web_search", "input": {}},
    {"type": "tool_use", "id": "toolu_2", "name": "market_price_quote", "input": {}},
    {"type": "tool_use", "id": "toolu_3", "name": "market_news_search", "input": {}}
  ]
}
```

工具完成后按原始调用顺序回填：

```json
{
  "role": "user",
  "content": [
    {"type": "tool_result", "tool_use_id": "toolu_1", "content": "..."},
    {"type": "tool_result", "tool_use_id": "toolu_2", "content": "..."},
    {"type": "tool_result", "tool_use_id": "toolu_3", "content": "..."}
  ]
}
```

实际完成顺序可以不同，但模型历史、日志与 memcore 落库顺序必须按模型原始调用顺序保持确定性。

## 4. 并行执行原则

### 4.1 默认并行

同一 provider 回合返回的调用默认提交到有界执行器：

```text
max_workers = min(4, 本轮调用数)
```

每个调用独立：

- normalize；
- validate；
- execute；
- timeout / exception 捕获；
- 生成独立结果信封。

单项失败不取消同批其他调用。

### 4.2 不建立组合白名单

禁止实现：

```python
ALLOWED_PARALLEL_PAIRS = {("web_search", "market_price_quote"), ...}
```

新增工具不应要求修改中央组合表。

### 4.3 轻量冲突元数据

工具默认行为：

```python
concurrency_mode = "parallel"
concurrency_key = ""
```

特殊工具才声明：

```python
concurrency_mode = "exclusive"
```

或在运行时生成共享状态键：

```python
concurrency_key = "qq:group:123"
concurrency_key = "file:gen_123"
```

同 key 的调用由宿主内部顺序执行；不同 key 和无 key 调用继续并行。第一版如果元数据尚未覆盖全部 handler，不因缺少元数据拒绝调用。

### 4.4 执行与提交分离

只并行执行 handler 本体。以下操作回到主线程按原调用顺序提交：

- `tool_results` / `tool_events` 累加；
- task workspace 产物登记；
- NPC/raw 消息记录；
- Anthropic tool history 组装；
- memcore `record_tool_exchange(...)`；
- 最终流事件顺序输出。

这样可以避免 SQLite、会话状态、memcore manager 和前端事件出现竞态。

## 5. memcore 落库契约

### 5.1 写入时机

工具执行完成并形成模型可见结果后、下一次调用模型之前写入。每个真实调用写一次：

```python
manager.record_tool_exchange(
    tool_name=tool_name,
    tool_call_id=provider_call_id,
    tool_input=model_visible_arguments,
    result=model_visible_result,
    source=tool_source,
    timestamp=completed_at,
    source_id_prefix=stable_source_prefix,
    ...namespace,
)
```

### 5.2 写什么

写入“模型实际看到的内容”，而不是未经控制的内部对象：

- 参数移除 `_tool_*` 内部字段；
- 不写 API key、token、绝对路径、本地数据库路径和 provider 私密配置；
- 结果优先使用已经整形的 `shaped_followup`；
- 对超长结果使用已有工具结果截断策略；
- 错误结果保留结构化错误状态与可读原因；
- 调用 ID 优先使用 provider 原始 ID，没有时使用 `ToolInvocation.id`。

### 5.3 幂等

`source_id_prefix` 必须稳定，建议由当前用户 source_id + tool call id 派生。模型重试或宿主重复提交同一调用时，不应生成多份不同轨迹。

需要核对 memcore manager / store 对相同 `source_id` 的行为；如果不是幂等 upsert，则在 Akane 当前轮维护 `recorded_tool_call_ids`，保证一次 `process_turn()` 只写一次。

### 5.4 可见与压缩

- 未压缩：两条 raw 以 `assistant.tool_call` / `tool.<name>` 事件块出现在可见原始上下文。
- count policy：轨迹不计入触发数量，但压缩跨度内的轨迹一起总结。
- token policy：轨迹 token 参与容量压力，在完整 assistant 边界压缩。
- 压缩摘要继承 `tool_trace` 类别、关键词、subject scope 和较高 confidence。
- 普通 retrieve 默认排除 `tool_trace`；当前可见 raw 不排除；显式工具轨迹检索可以回找。

## 6. 工具轮预算与去重

1. 一批并行调用算一个模型工具轮。
2. 总调用数另设硬上限，首版建议 `max_tool_calls_per_turn = max_tool_rounds * 4`。
3. `seen_tool_calls` 对批内每个调用分别计算签名。
4. 批内重复调用去重时，不拒绝整批；重复项返回已有结果或结构化重复说明，其余项照常执行。
5. 后续调用依赖前序结果时，模型自然在下一轮提出；宿主不推测依赖图。

## 7. 流式表现

收到一批工具调用后，前端只需要一个稳定等待态，可附工具数量：

```json
{
  "type": "assistant_working",
  "status": "running",
  "phase": "tool_batch",
  "tool_count": 3,
  "message": "我一起查一下。"
}
```

工具完成事件按原调用顺序回放，避免并发完成顺序导致 UI 抖动。不得提前把某个工具的完成误报为整批完成。

## 8. 分阶段实施

### P1：文档与协议载体

状态：✅ 已完成。

- 新增本文件。
- 新增 `NATIVE_TOOL_CALLS_FIELD`。
- `llm_runtime` 非流式/流式保留全部调用。
- `final_output_engine` 保留内部批量载体但不泄漏到公开输出。
- 保持旧单调用字段兼容。

### P2：Engine 批量归一化

状态：✅ 已完成。

- `_prepare_tool_round_decision` 返回调用列表与逐项 rejection。
- legacy JSON 仍形成单元素列表。
- 批量签名、预算、重复检测。

### P3：有界并行执行

状态：✅ 已完成。当前同轮最多 4 个调用；handler 本体并行，结果提交按原调用顺序。

- 抽出单调用“纯执行阶段”。
- 最多 4 个并发。
- 单项异常转结构化工具错误，不中断整批。
- 主线程按原调用顺序提交副作用。

### P4：Anthropic 成组历史与去重文本

状态：✅ 已完成。Anthropic 原生结果使用成组结构化历史，文本区只保留短状态提示。

- 同轮多个 `tool_use` 合并为一个 assistant content list。
- 多个 `tool_result` 合并为一个 user content list。
- 原生路径不再把完整结果重复塞入 `extra_user_context`；保留简短轮次状态即可。
- legacy 路径继续使用文本 followup。

### P5：memcore 工具轨迹持久化

状态：✅ 已完成。统一提交点按真实调用 ID 写入，当前 turn 内去重，并剥离内部字段、凭证和本地绝对路径。

- 在统一工具提交点调用 manager facade。
- 参数/结果安全整形。
- 稳定 source ID 与本轮幂等集合。
- 验证下一用户轮可见 raw 包含工具调用和结果。
- 验证压缩后摘要继承 `tool_trace`。

### P6：验收与上线

状态：⏳ 自动化回归已完成；live Sonnet 同轮多调用、QQ 实际表现与重启观察待执行。

- 单元测试、流式测试、Anthropic payload 测试。
- live Sonnet：同轮提出多个只读工具调用。
- 确认模型收到整组结果并汇总。
- 确认 memcore raw 可见、后续压缩正常。
- 检查无重复 QQ 发送、无重复文件写入、无内部路径或密钥落库。
- 安全重启后端并观察状态与日志。

自动化验收基线（2026-07-12）：

- 原生工具、Anthropic 历史、memcore 集成定向测试：125 项通过；
- `ruff check`、`ruff format --check`、`py_compile`、`git diff --check` 通过；
- 全量测试共 1371 项，1364 项通过；其余 4 个 failure、3 个 error 位于礼物焦点、旧提示词断言和前端默认场景测试，涉及文件均不在本功能 diff 内，作为仓库既有问题隔离，不在本切片顺手修改。

## 9. 必须覆盖的测试

1. OpenAI 非流式两个 tool calls 全部保留。
2. Anthropic 非流式两个 tool_use 全部保留。
3. OpenAI 流式不同 index 的调用全部组装。
4. Anthropic 流式不同 content block index 全部组装。
5. 单调用旧测试继续通过。
6. 一批三个慢工具的总耗时接近最慢项，而非三项之和。
7. 一个工具失败、两个成功时，三项结果全部回给模型。
8. 并发完成顺序不同，最终历史仍按原调用顺序。
9. 一轮多个 Anthropic tool_use 使用一个 assistant 消息；结果使用一个 user 消息。
10. memcore raw 下一轮可见 `assistant.tool_call` 与 `tool.<name>`。
11. tool trace 不单独触发 count 压缩，但进入普通消息形成的压缩跨度。
12. token policy 下工具长结果参与 token 压力。
13. 工具参数中的内部字段、绝对路径和敏感配置不进入 memcore。
14. 重复调用 ID 不重复落库。
15. 流式与非流式最终行为一致。

## 10. 当前运行与提交基线

建档时最近相关提交：

```text
215cdc5 fix(finance): block cadre news before moderation
be9b651 fix(finance): recover interrupted push deliveries
005a4d5 perf(finance): decouple news polling from analysis delivery
```

工作区仅有未跟踪用户文件 `uv.lock`，不得修改或提交。

## 11. 完成定义

只有同时满足以下条件才算完成：

- provider 返回的同轮多个调用不再丢失；
- 独立调用实际并行执行；
- 模型收到按调用 ID 对应的整组结构化结果；
- 单项失败不拖垮整组；
- 普通新增工具默认可并行，不需要中央组合白名单；
- 明显共享状态冲突由宿主内部安全串行；
- 每次真实工具交换进入 memcore raw，下一轮可见；
- 后续压缩保留可接续摘要，不永久堆积完整长结果；
- 流式、非流式、QQ 金融主动分析均不退化；
- 全部相关测试、ruff、format、`git diff --check` 通过；
- 后端重启后 live 状态正常。
