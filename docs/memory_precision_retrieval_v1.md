# Akane Memory Precision Retrieval V1

## 1. Goal

This document defines the next retrieval upgrade for Akane's memory system.

The current memory stack already has:

- `raw`: original chat messages
- `summary`: episodic summaries
- `semantic_summary`: long-term semantic summaries
- vector entries bound back to SQLite records by source id
- visible-context exclusion to avoid retrieving memory Akane already sees
- optional verifier selection before retrieved snippets enter the final prompt

The V1 precision upgrade does not replace that stack. It adds structured filter
signals so Akane's `retrieve_memory` tool can narrow candidates before similarity
ranking.

Core idea:

```text
query + keywords = similarity signals
time_range + source_layers + subject_scopes + categories = filter signals
importance/confidence = cautious threshold or rerank signals
```

## 2. Recommended V1 Runtime Baseline

The precision filters only show their value when the semantic recall layer is
good enough. For the V1 local baseline:

- Embedding model: `BAAI/bge-m3`.
- Vector database: keep the current local Chroma `PersistentClient` first.
- Startup behavior: use local-cache-only loading by default; if the model is
  not cached or `EMBEDDING_MODEL_NAME` does not point to a local directory,
  fall back to hashed embeddings instead of blocking backend startup on network
  retries.

This keeps the database adapter stable while making the semantic side strong
enough to expose the benefit of `memory_metadata` filters. Qdrant or LanceDB can
be evaluated after the BGE-M3 baseline has benchmark data.

## 3. Non-Goals

- Do not make retrieval depend on a single exact category.
- Do not reward records merely because they match more categories.
- Do not use entities as a hard filter in the first implementation.
- Do not require all filters to match when candidate count is low.
- Do not make every turn call an expensive verifier.

The system should become more precise without becoming brittle.

## 4. Retrieval Tool Parameters

Proposed `retrieve_memory` call shape:

```json
{
  "query": "用户喜欢喝什么饮料",
  "keywords": ["喜欢", "喝", "饮料", "可乐"],
  "time_range": null,
  "source_layers": ["raw", "summary", "semantic_summary"],
  "subject_scopes": ["user"],
  "categories": ["preference"],
  "importance_min": null,
  "limit": 6
}
```

Field roles:

| Field | Role | Filter Type |
|---|---|---|
| `query` | main semantic search text | similarity |
| `keywords` | keyword/BM25 and query expansion | similarity |
| `time_range` | explicit or inferred date range | hard filter when confident |
| `source_layers` | raw / summary / semantic_summary | hard filter |
| `subject_scopes` | which side the memory is about | OR filter |
| `categories` | stable coarse memory class | OR filter |
| `importance_min` | optional threshold for broad searches | cautious filter |
| `limit` | desired final snippets | output control |

## 5. Subject Scopes

`subject_scopes` is a coarse "which side is this memory about" field. It is
more stable than exact entities and useful for filtering roleplay memories.

Recommended values:

| Scope | Meaning |
|---|---|
| `user` | about the user: preferences, profile, plans, state |
| `assistant` | about the current character/assistant: persona, settings, promises |
| `other` | about another person, character, object, project, or external topic |

Rules:

- Store multiple scopes if needed.
- Retrieval scope matching is OR: any overlap qualifies.
- If Akane is unsure, omit this field or pass multiple scopes.
- Do not give extra score for matching more scopes.
- Relationship memories usually use `["user", "assistant"]` plus
  `categories:["relationship"]`.
- Project/topic memories usually use `["other"]` plus `project_work`,
  `system_meta`, or concrete keywords.

Examples:

```json
{
  "text": "主人提到自己喜欢喝可乐。",
  "subject_scopes": ["user"],
  "categories": ["preference"],
  "keywords": ["喜欢", "喝", "可乐", "饮料", "汽水"]
}
```

```json
{
  "text": "主人和 Akane 约定下次继续整理记忆系统。",
  "subject_scopes": ["user", "assistant"],
  "categories": ["plan_goal", "project_work"],
  "keywords": ["约定", "下次", "记忆系统", "整理"]
}
```

## 6. Categories

`categories` is a stable coarse taxonomy. It should be small enough that models
can choose reliably.

Recommended initial values:

| Category | Meaning |
|---|---|
| `casual` | low-value small talk or greetings |
| `preference` | likes, dislikes, taste, aesthetics |
| `personal_profile` | identity, habits, stable user traits |
| `plan_goal` | plans, goals, todos, promises |
| `project_work` | code, creation, research, concrete projects |
| `relationship` | people, relationship facts, shared social context |
| `emotion_state` | mood, pressure, physical/mental state |
| `life_event` | concrete real-world events |
| `memory_query` | user is asking about past memory |
| `system_meta` | Akane/system/persona/tool/config discussion |

Rules:

- Store multiple categories if needed.
- Retrieval category matching is OR: any overlap qualifies.
- Do not require all categories to match.
- Do not add a category-overlap bonus in V1.

Reason: category is a gate, not the similarity engine. Ranking should still come
from query similarity, keyword match, time match, importance, and source layer.

## 7. Entities Are Not Hard Filters

Exact entities are useful, but they are not stable enough for V1 hard filtering.

Example risk:

```text
stored entities: ["可乐"]
query entity: ["饮料"]
```

The memory is relevant, but exact entity intersection is empty. Therefore:

- entities may be stored for debug, future rerank, or keyword expansion
- entities may become keywords/aliases
- entities should not be required for candidate admission in V1

First implementation should rely on:

```text
hard/OR filters: time_range, source_layers, subject_scopes, categories
similarity: query, keywords
```

## 8. Importance And Confidence

Importance is already present on summaries and semantic summaries. It can be
extended to raw/event-like records later.

Use importance carefully:

- good for broad questions like "我喜欢什么" or "我是什么样的人"
- risky for concrete questions like "我之前说过可乐吗"
- should not hide exact low-importance facts when the query is specific

Recommended V1 behavior:

- treat `importance_min` as optional
- ignore it if the query has concrete keywords and candidate count is low
- include importance in rerank/debug even when not filtering

Confidence has a similar role. It is useful for filtering obviously unreliable
records, but should not block recall unless the record is known bad.

## 9. Filter Relaxation

Every precision filter must have an automatic fallback path.

Suggested stages:

```text
stage 1: source_layers + time_range + subject_scope OR + category OR + importance_min
stage 2: drop importance_min
stage 3: drop categories
stage 4: drop subject_scopes
stage 5: drop time_range if it was weak/inferred
stage 6: query + keywords across selected/default layers
```

Rules:

- Stop relaxing once enough candidates exist, for example `candidate_count >= 12`.
- If time was explicit and high confidence, keep it longer.
- If time was vague ("上次", "之前"), treat it as weak and relax earlier.
- Never relax profile/character-pack safety boundaries.

## 10. Ranking

After filtering, rank with a fused score.

Suggested signals:

```text
semantic/vector score
keyword/BM25 score
time match strength
importance
last reinforced / recency
source layer preference
visible-context exclusion
```

V1 should not include:

```text
category overlap count bonus
subject scope overlap count bonus
entity hard-match requirement
```

Layer notes:

- `raw` is best for exact wording and adjacent dialogue.
- `summary` is best for older episodes and medium-range recall.
- `semantic_summary` is best for stable facts and repeated themes.

## 11. Ingestion Metadata

When records are saved or summarized, attach structured metadata:

```json
{
  "subject_scopes": ["user"],
  "categories": ["preference"],
  "keywords": ["可乐", "饮料", "喜欢", "汽水"],
  "importance": 0.72,
  "confidence": 0.8
}
```

For raw messages, this can start as simple model output or deterministic fallback.
For summaries and semantic summaries, the existing summary model can output the
new fields together with summary text and importance.

If model output is missing or invalid:

- derive keywords with `extract_semantic_tags`
- default `subject_scopes` to `[]`
- default `categories` to `[]` or `casual` only when clearly low-value
- keep the record searchable by query/keywords

## 12. Final Output Metadata Group

The final Akane response will eventually need to output memory storage metadata.
Those fields should not be scattered across the top-level JSON.

Use one grouped object:

```json
{
  "emotion": "normal",
  "speech": "主人喜欢喝可乐这件事，我记住啦。",
  "speech_segments": [],
  "tool_call": null,
  "code_snippet": "",
  "status": "final",
  "score": 0.0,
  "choices": [],
  "character": {"outfit": "default"},
  "scene": {"major": "default", "minor": "default", "background": "evening", "bgm": ""},
  "persona": {"active": ""},
  "memory_metadata": {
    "keywords": ["可乐", "饮料", "喜欢", "汽水"],
    "subject_scopes": ["user"],
    "categories": ["preference"],
    "importance": 0.72,
    "confidence": 0.8
  }
}
```

Rules:

- Keep `emotion`, `speech`, `speech_segments`, and `tool_call` near the front.
- `tool_call` must remain immediately after `speech_segments` for streaming/tool handling.
- Storage-only metadata should be near the end of the JSON.
- Do not add separate top-level fields such as `memory_importance`,
  `memory_categories`, or `memory_subject_scopes`.
- Keep the prompt wording short and forceful. The model should understand that
  `memory_metadata` is for storage only, not user-facing speech.
- Existing `memory_tags` can be migrated into `memory_metadata.keywords`.

Suggested prompt wording:

```text
memory_metadata 只用于后台记忆入库，不会展示给用户。
如果当前用户消息没有值得长期检索的事实，输出空数组和 importance=0。
keywords 写 0-4 个短词；subject_scopes 和 categories 从固定枚举里选，拿不准可留空或多选。
importance 是 0-1 数字，confidence 是你对这些标签的把握。
```

This keeps memory fields together and prevents future prompt/schema drift where
importance, categories, and keywords appear in unrelated positions.

## 13. Debug Payload

Precision retrieval must be observable. Each retrieval should expose:

```json
{
  "requested_filters": {
    "time_range": null,
    "source_layers": ["raw", "summary", "semantic_summary"],
    "subject_scopes": ["user"],
    "categories": ["preference"],
    "importance_min": null
  },
  "applied_filters": ["source_layers", "subject_scopes", "categories"],
  "relaxed_filters": ["importance_min"],
  "candidate_count_before": 120,
  "candidate_count_after": 18,
  "relaxation_stage": 2,
  "top_hits": []
}
```

This is required for tuning. Without it, precision retrieval becomes guesswork.

## 14. Implementation Slices

Suggested order:

1. Add `memory_metadata` to the final output prompt/schema and normalize it.
2. Persist `memory_metadata.keywords`, `subject_scopes`, `categories`,
   `importance`, and `confidence` on saved records.
3. Include those fields in vector metadata and reindex flow.
4. Extend `retrieve_memory` tool schema with optional filters.
5. Implement OR filtering and relaxation in retrieval service.
6. Add debug payload for applied and relaxed filters.
7. Update retrieval eval dataset/report to show filter hit statistics.
8. Only then consider event-card memory as a separate layer.

## 15. Design Summary

V1 precision retrieval should be conservative:

```text
Use stable filters to remove obvious noise.
Use query and keywords to decide relevance.
Relax filters automatically when candidate count is low.
Avoid clever overlap bonuses until evaluation proves they help.
```

The immediate goal is not perfect memory. The goal is to make Akane's active
memory tool return fewer wrong candidates without making correct memories
unreachable.
