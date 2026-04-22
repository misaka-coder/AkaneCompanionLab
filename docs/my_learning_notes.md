# My Learning Notes

这个文件只记录已经真正过了一遍的内容。每次学习不求长，留下三到五行也算完成。

## 2026-04-17 - Akane 对话请求主链路

今天目标：体验一次“项目驱动学习”的完整闭环，追踪用户发一句话后 Akane 内部发生什么。

### 一句话理解

前端把用户输入发到 `/think`，后端用 `engine.process_turn_stream` 处理这一轮：先保存用户消息，再判断是否需要记忆检索，然后拼最终 prompt，流式调用 LLM，最后把结果和调试信息流式返回给前端。

### 关键链路

1. `web/app.js`
   - `sendMessage` 附近会 `fetch("/think")`。
   - 请求体里带 `user_id`、`real_user_id`、`message`、`current_visual`。
   - 前端用 `consumeNdjsonStream` 一行行消费后端返回的事件。

2. `companion_v01/app.py`
   - `/think` 接口读取请求 JSON。
   - 用 `StreamingResponse` 返回 NDJSON 流。
   - 每个事件来自 `engine.process_turn_stream(payload)`。

3. `companion_v01/engine.py`
   - `process_turn_stream` 是一轮对话的主流程。
   - 先把用户消息写入 store。
   - 调用 `RetrievalService.run` 判断和执行记忆检索。
   - 调用 `_stream_final_response` 生成最终回复。
   - 如果模型输出 `tool_call`，会执行工具，再生成一次后续回复。
   - 最后保存 assistant 消息，写 eval 记录，返回 `final_ui` 和 `final`。

4. `companion_v01/retrieval_service.py`
   - `run` 先让 router 判断 `need_retrieval`。
   - 如果需要检索，就跑 retrieval chain。
   - 最后返回 `confirmed_snippets`，给最终 prompt 使用。

5. `companion_v01/prompt_builder.py`
   - `build_final_generation_context` 把当前问题、原始上下文、阶段摘要、语义记忆、检索片段、视觉状态、工具说明拼成最终 prompt。

6. `companion_v01/llm_runtime.py`
   - `stream_chat_json` 调用聊天模型。
   - `_stream_chat_json` 从模型流里提取 `ui`、`speech_chunk` 等事件。

### 今天留下的问题

- `RetrievalService._run_retrieval_chain` 里面具体怎么融合 raw / episodic / semantic 命中，还没细看。
- `memory_tags` 如何影响后续记忆检索，还没细看。
- `tool_call` 的参数校验和各个工具 handler 后续可以单独过一遍。

### 我自己的复述

Akane 的一轮对话不是简单调用一次 LLM，而是一个小流水线：前端提交消息，后端保存当前轮消息，router 决定是否需要记忆，检索服务找相关记忆并确认，prompt builder 拼出最终上下文，LLM 流式生成结构化回复，前端边收边显示，最后后端再把回复存回记忆系统。

## 2026-04-17 - 最终回复 Prompt 是怎么拼的

今天目标：理解 Akane 在最终回复前，给主聊天模型准备了哪些材料。

### 一句话理解

最终 prompt 不是一整段写死的提示词，而是由 `engine.py` 收集上下文材料，再交给 `prompt_builder.py` 排版成 `system_prompt` 和 `user_prompt`。

### 分工

1. `persona_profiles.toml`
   - 存角色和输出规则。
   - `final.system` 规定只能输出 JSON、字段有哪些、`speech` 怎么用、`tool_call` 怎么用、视觉资源不能编造等。
   - `final.fast_mode` / `final.debug_mode` 决定是否允许输出 `thought`。

2. `engine.py`
   - `_prepare_final_response_context` 收集材料。
   - 材料包括当前消息、未总结原始消息、阶段摘要、长期语义记忆、检索确认片段、当前视觉状态、可用资源、礼物/待办上下文、工具说明。

3. `prompt_builder.py`
   - `build_final_generation_context` 把材料拼成两个 prompt。
   - `system_prompt` 主要放身份、输出格式、工具规则。
   - `user_prompt` 主要放本轮具体上下文和可用资料。

4. `llm_runtime.py`
   - `stream_chat_json` 把 `system_prompt` 和 `user_prompt` 发给聊天模型。
   - 模型返回结构化 JSON，流式过程中会尽量先吐出 `emotion` 和 `speech`，让前端能边生成边显示。

### 最终 user_prompt 的材料顺序

1. `debug_enabled`
2. 当前时间
3. 用户原始问题
4. 当前会话中所有未总结的原始消息
5. 最近可见阶段摘要
6. 较长期语义记忆
7. 可用回忆片段，也就是检索确认后的 `confirmed_snippets`
8. 当前演出状态
9. 可用视觉资源
10. 额外上下文，比如手边礼物、聚焦礼物观察、工具后的 followup context
11. 最后的输出提醒：以 Akane 身份自然回复，只输出 JSON

### 我自己的复述

Akane 的 prompt 拼装像给演员递一份本场戏资料：system prompt 讲“你是谁、必须怎么演、输出什么格式、能不能用工具”，user prompt 讲“现在几点、主人刚说什么、最近聊过什么、长期记得什么、检索找到了什么、现在站在哪个场景、手边有什么东西”。模型不是凭空回复，而是在这些材料里选重点生成本轮 JSON。
