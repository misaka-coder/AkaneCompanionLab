# Akane Memory Retrieval V2

## 1. Goal

V2 changes memory retrieval from a router-gated pipeline into a two-stage recall model:

- a quiet system-side pre-retrieval pass that runs every turn
- an Akane-initiated post-retrieval tool when she decides she needs to think harder

The core intent is to stop asking a short-context router to decide whether Akane should remember. Akane already sees the current raw context, recent episodic summaries, recent semantic summaries, and the user message. She is in a better position to decide whether the visible material is enough.

V2 keeps the existing verifier role. Retrieval candidates should not be injected into Akane's prompt unless the verifier selects them as useful.

## 2. Current Problem

The V1 pipeline is:

```text
router -> retrieval -> verifier -> final Akane response
```

The router has too much responsibility:

- decide whether retrieval is needed
- rewrite the search query
- produce keywords
- extract time hints
- decide whether the current user message should enter vector memory

Because the router only receives a short recent window, it can misjudge cases where the current message depends on broader context, old facts, or subtle reference resolution. It can also skip retrieval before Akane has had a chance to decide what she needs.

## 3. V2 Shape

The new pipeline should be:

```text
save user message
collect visible raw / episodic / semantic context ids
pre-retrieve quietly
pre-verify candidates
build final prompt
Akane may answer directly or call retrieve_memory
if retrieve_memory is called:
    execute post-retrieval
    post-verify candidates
    regenerate final answer with tool followup context
save final answer
```

The old router is removed from the critical path. Query rewriting becomes Akane's responsibility when she calls the memory tool.

## 4. Memory Layers

V2 keeps the existing three retrievable layers:

- `raw`: original chat messages from `chat_messages`
- `summary`: episodic summaries from `memory_summaries`
- `semantic_summary`: long-term semantic memories from `memory_semantic_summaries`

All three layers remain in the vector store.

Raw hits keep the current expansion behavior:

- if a raw message is selected, load a context slice around its `seq_no`
- question-like raw hits may use a wider window
- summaries and semantic summaries render as compact memory snippets

## 5. Pre-Retrieval

Pre-retrieval runs every turn.

Input:

- current user message as the query
- `extract_semantic_tags(user_message)` as baseline keywords
- rule-extracted time hints where available
- visible context source ids as exclusions

Excluded ids should include:

- current user message id
- currently visible unsummarized raw message ids
- currently visible episodic summary ids
- currently visible semantic summary ids

This preserves the current dedupe principle: if Akane can already see a memory in the prompt, retrieval should not return it again.

### 5.1 Silent Empty Result

If pre-retrieval returns no useful verified memory, do not tell Akane.

This is important because Akane may already have enough information in visible raw, summary, or semantic memory. A failed pre-retrieval pass does not mean there is no relevant memory; it only means this quiet background pass did not add anything useful.

Do not inject text like:

```text
本轮没有找到相关记忆。
```

for pre-retrieval failure.

### 5.2 Pre-Retrieval Verifier

Pre-retrieval should still pass through the verifier before injecting snippets.

Reason:

- pre-retrieval is forced every turn
- forced retrieval will often find weakly related old memories during casual chat
- injecting raw candidates directly would tempt Akane to overuse irrelevant old facts

The verifier acts as a quiet filter:

- selected snippets are injected as available memory
- unselected snippets are discarded silently
- mismatch does not produce user-facing or Akane-facing failure text

Verifier output fields can stay unchanged:

- `match_result`
- `need_retry`
- `selected_indexes`
- `retry_query`
- `retry_keywords`
- `retry_time_hint`

The existing NDJSON early-consumption behavior should be preserved.

## 6. Post-Retrieval Tool

Akane gains an internal memory tool:

```json
{"type":"retrieve_memory","query":"扬州城 地点 二十四桥 瘦西湖","keywords":["扬州城","二十四桥","瘦西湖","地点"],"time_hint":{}}
```

Purpose:

- Akane calls it only when visible context and pre-retrieval snippets are not enough
- Akane writes the rewritten query herself
- Akane supplies concrete keywords from the current user question and visible context

This tool should reuse the existing retrieval and verifier logic rather than inventing a second memory path.

### 6.1 Tool Normalization

Suggested accepted fields:

- `type`: fixed as `retrieve_memory`
- `query` or `rewritten_query`: required short search phrase
- `keywords`: optional list of short terms
- `time_hint`: optional object with existing time hint fields

Suggested limits:

- `query`: trim to a reasonable maximum, e.g. 200 characters
- `keywords`: normalize, dedupe, keep about 8
- `time_hint`: normalize with the same helper used by retrieval

### 6.2 Tool Result Semantics

Post-retrieval differs from pre-retrieval in how failure is reported.

If post-retrieval verifier selects memories:

```text
你刚刚主动检索了长期记忆。下面是可能回答主人问题的参考记忆：
...
请基于这些参考记忆自然回应；不要声称系统绝对证明了这些记忆。
```

If post-retrieval verifier selects nothing:

```text
你刚刚主动检索了长期记忆，但这次没有找到足以回答主人问题的相关记忆。请自然说明自己没有想起可靠线索，不要编造。
```

This feedback is allowed because Akane explicitly asked to search.

### 6.3 Loop Limit

Only one post-retrieval tool call should be allowed per final answer cycle.

After `retrieve_memory` runs, the followup final generation should set `allow_tool_call=False`, or at least block another `retrieve_memory` call for the same user turn.

This prevents recursive memory searching.

## 7. `tool_call` First Field

To reduce latency, final JSON should put `tool_call` first.

Current V1 prompt asks the model to output `emotion` and `speech` before `tool_call`. That means the system cannot know whether a tool is needed until after the model has already generated user-facing text.

V2 should change the field order to:

```json
{"tool_call":null,"emotion":"normal","speech":"...","speech_segments":[],"code_snippet":"","memory_tags":"","status":"final","score":0.0,"choices":[],"character":{"outfit":"default"},"scene":{"major":"default","minor":"default","background":"evening","bgm":""},"persona":{"active":""}}
```

For debug mode, prefer:

```json
{"tool_call":null,"thought":"...","emotion":"normal","speech":"...","speech_segments":[],"code_snippet":"","memory_tags":"","status":"final","score":0.0,"choices":[],"character":{"outfit":"default"},"scene":{"major":"default","minor":"default","background":"evening","bgm":""},"persona":{"active":""}}
```

If debug-mode thought-first ordering is still needed for readability, then debug mode will not get the full early-tool-call latency benefit. The production fast path should prioritize `tool_call` first.

## 8. Streaming Early Stop

Putting `tool_call` first only helps if the stream parser can stop early.

The streaming JSON tap should be extended to:

- detect the top-level `tool_call` key
- if value is `null`, continue streaming normally
- if value is an object, capture the full object with nested-brace and string-escape handling
- parse the object immediately when it closes
- emit or return an early tool-call result
- close the upstream LLM stream before `emotion` and `speech` are generated

This is separate from the existing `emotion` and `speech` tap:

- `emotion` still emits early UI state
- `speech` still emits `speech_chunk`
- `tool_call` can stop the stream before either of those appears

### 8.1 Internal Tool Calls Should Not Create Preface Speech

For `retrieve_memory`, an early tool call should not create an assistant preface record.

The call is an internal recall action, not a spoken message. If early stop returns only:

```json
{"tool_call":{"type":"retrieve_memory",...}}
```

normalization must not fill fallback speech and then save that fallback as if Akane said it.

For ordinary tools like `call_npc` or file tools, preface speech may still be useful when the model actually generated speech before the tool call. With `tool_call` first, most tool-first paths will not have preface speech.

## 9. Indexing Current User Message

Removing the router should not remove the current anti-pollution behavior.

Keep a rule-based vector-index policy for obvious memory-test messages, such as:

- "你还记得吗"
- "测试一下你记不记得"
- "我之前说过什么"
- "上次说过什么"

These messages should usually not enter raw vector memory, because future retrieval can otherwise return the test question itself instead of the remembered fact.

This can reuse the current `RAW_INDEX_SKIP_MARKERS` style rule without calling an LLM.

## 10. Debug Payload

The debug payload should stop presenting router output as a required stage.

Suggested V2 debug structure:

- `pre_retrieval`
  - query
  - keywords
  - time hint
  - excluded ids
  - fused hits
  - snippets
- `pre_verifier`
  - verifier output
  - timing
  - selected snippets
- `memory_tool_call`
  - normalized call, if Akane requested one
- `post_retrieval`
  - query
  - keywords
  - time hint
  - excluded ids
  - fused hits
  - snippets
- `post_verifier`
  - verifier output
  - timing
  - selected snippets

For compatibility, the first implementation may keep old field names internally, but external debug naming should move toward pre/post retrieval.

## 11. Non-Goals

V2 does not require:

- changing the SQLite memory schema
- changing vector entry shapes
- changing raw expansion behavior
- changing verifier output fields
- adding a new embedding provider
- exposing pre-retrieval failure to Akane
- allowing unlimited memory-search loops

## 12. Implementation Order

Recommended sequence:

1. Add a retrieval service method that runs retrieval and verifier from explicit query fields, without router dependency.
2. Replace pre-router gating with quiet forced pre-retrieval.
3. Preserve rule-based current-message vector index suppression.
4. Add `RetrieveMemoryToolHandler`.
5. Add `retrieve_memory` to tool packs / capability registry where tool actions are enabled.
6. Change final prompt field order so `tool_call` is first.
7. Extend the streaming JSON tap to capture top-level `tool_call` and stop early.
8. Special-case internal memory tool calls so fallback speech is not saved as spoken dialogue.
9. Update debug printing to show pre/post retrieval stages.
10. Add focused tests for quiet pre-retrieval, memory tool success, memory tool miss, and streaming early stop.

## 13. Behavioral Examples

### 13.1 Casual Chat

User:

```text
今天有点累。
```

System:

- runs pre-retrieval
- verifier rejects weak old memories or there are no candidates
- injects nothing extra
- does not tell Akane that retrieval failed

Akane answers from visible context.

### 13.2 Direct Memory Question With Good Pre-Hit

User:

```text
我之前说我更喜欢哪种咖啡来着？
```

System:

- runs pre-retrieval
- verifier selects relevant preference memory
- injects selected snippets as available memory

Akane answers directly.

### 13.3 Ambiguous Memory Question With No Pre-Hit

User:

```text
那个扬州城地点，你再想想。
```

System:

- pre-retrieval may fail silently
- Akane sees visible context and realizes "那个" refers to places
- Akane calls `retrieve_memory` with a concrete query and keywords
- post-retrieval verifier selects memory or returns a miss

Akane answers after the tool followup.

## 14. Design Principle

Pre-retrieval is a quiet assistant that may hand Akane useful memories.

Post-retrieval is Akane actively searching her own memory.

The system should not overstate either result:

- pre-retrieval miss is silent
- post-retrieval miss is acknowledged because Akane explicitly asked
- selected snippets are reference memories, not absolute proof
