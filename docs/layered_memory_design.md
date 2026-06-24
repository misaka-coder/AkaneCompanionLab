# Layered Memory Design

## Goal

On top of the current two-layer memory:

- `working/raw`: recent unsummarized chat messages
- `episodic summary`: compressed summaries of older raw messages

add a third layer:

- `semantic summary`: higher-level long-term memory distilled from older episodic summaries

The design should preserve three properties:

1. No memory blind window during compaction.
2. Strong time anchoring across all layers.
3. Important long-term facts should remain retrievable even when the original time range is old.

## Current State

Current implementation already does:

- raw messages trigger compaction when unsummarized count reaches `SUMMARY_TRIGGER_COUNT=30`
- the oldest `SUMMARY_BATCH_SIZE=20` raw messages are summarized into one episodic summary
- this naturally leaves about `10` raw messages visible after each compaction cycle
- recent episodic summaries are shown via `get_recent_summaries(..., limit=RECENT_SUMMARY_LIMIT)`
- episodic summaries are stored in SQLite and also upserted into vector memory

This means the raw layer already has a healthy "high watermark -> compact older part -> keep recent tail" pattern.

## Proposed Three-Layer Model

### Layer 1: Working Memory

- Source: `chat_messages` with `is_summarized=0`
- Trigger: existing raw compaction, unchanged
- Rule:
  - when unsummarized raw count reaches `30`
  - summarize the oldest `20`
  - keep the most recent `10`

This remains the short-term conversational layer.

### Layer 2: Episodic Memory

- Source: existing `memory_summaries`
- Meaning: time-based conversation episodes, still close to lived timeline
- Visibility:
  - prompt-visible window is not fixed `5`
  - it should float between `5` and `10`
  - this avoids a blind spot between compaction cycles

Rule:

- while uncompacted episodic summaries are `5~9`, expose them all
- when uncompacted episodic summaries reach `10`
  - compact the oldest `5` episodic summaries into one semantic summary
  - mark those `5` episodic summaries as compacted
  - visible episodic window returns to the newer `5`

This mirrors the existing raw compaction style and prevents "middle-memory emptiness".

### Layer 3: Semantic Memory

- Source: distilled from older episodic summaries
- Meaning: long-term stable memory with time origin preserved
- Prompt-visible window: default `3`
- Retrieval role:
  - supports preference / identity / repeated-topic questions
  - helps when episodic records are old but still important

This layer should not be treated as a pure timeless knowledge base.
It should still retain time provenance.

## Why Semantic, Not Just "Summary of Summaries"

The third layer should not simply duplicate episodic fields.

Episodic memory is still time-slice memory:

- what happened in that period
- what was discussed
- what events/facts were salient then

Semantic memory should instead preserve:

- stable user facts
- repeated preferences
- important recurring people
- long-running topics
- unresolved or long-term commitments

But each semantic memory still keeps:

- source episodic range
- source time span
- importance
- later reinforcement metadata

That gives us "human-like older memory with time roots" rather than a disconnected fact table.

## Storage Design

### Option Chosen

Add a dedicated table instead of overloading `memory_summaries`.

Recommended new table: `memory_semantic_summaries`

Fields:

- `semantic_id TEXT PRIMARY KEY`
- `profile_user_id TEXT NOT NULL`
- `session_id TEXT NOT NULL`
- `created_at INTEGER NOT NULL`
- `period_start_ts INTEGER NOT NULL`
- `period_end_ts INTEGER NOT NULL`
- `date_label TEXT NOT NULL`
- `time_of_day TEXT NOT NULL`
- `importance REAL NOT NULL`
- `semantic_summary TEXT NOT NULL`
- `stable_facts_json TEXT NOT NULL`
- `recurring_topics_json TEXT NOT NULL`
- `important_people_json TEXT NOT NULL`
- `open_loops_json TEXT NOT NULL`
- `semantic_tags_json TEXT NOT NULL`
- `source_summary_ids_json TEXT NOT NULL`
- `reinforcement_count INTEGER NOT NULL DEFAULT 1`
- `last_reinforced_ts INTEGER NOT NULL`

Recommended episodic table additions in `memory_summaries`:

- `is_semanticized INTEGER NOT NULL DEFAULT 0`
- `semantic_id TEXT NOT NULL DEFAULT ''`

Meaning:

- episodic summaries remain stored for provenance
- after compaction into semantic memory, they can be hidden from normal episodic prompt windows
- they still retain traceability to source periods

## Prompt Windows

### Raw

- Keep current behavior.
- Usually recent unsummarized tail stays around `10`.

### Episodic

New rule:

- prompt should show uncompacted episodic summaries up to `EPISODIC_VISIBLE_MAX=10`
- after semantic compaction, only the newer uncompacted tail remains visible
- practical steady-state prompt view becomes around `5`, but can naturally grow to `10` before the next semantic compaction

### Semantic

- prompt shows up to `SEMANTIC_VISIBLE_LIMIT=3`
- sorted by recency of reinforcement or creation, then importance

## Suggested Config

Add to `config.py`:

- `EPISODIC_COMPACT_TRIGGER_COUNT = 10`
- `EPISODIC_COMPACT_BATCH_SIZE = 5`
- `EPISODIC_VISIBLE_MIN = 5`
- `EPISODIC_VISIBLE_MAX = 10`
- `SEMANTIC_VISIBLE_LIMIT = 3`
- `ENABLE_SEMANTIC_MEMORY = false` for rollout gating

Keep existing:

- `SUMMARY_TRIGGER_COUNT = 30`
- `SUMMARY_BATCH_SIZE = 20`

## Semantic Summary Prompt

Add a new prompt alongside `summary_system_prompt`.

Recommended output fields:

- `semantic_summary`
- `importance`
- `stable_facts`
- `recurring_topics`
- `important_people`
- `open_loops`

Example shape:

```json
{
  "semantic_summary": "最近这一段时间里，主人反复提到学习安排，我记住了他对复习进度其实挺上心的。",
  "importance": 0.84,
  "stable_facts": [
    "用户最近长期关注高数复习",
    "用户会主动给自己安排学习节点"
  ],
  "recurring_topics": [
    "学习规划",
    "复习节奏"
  ],
  "important_people": [],
  "open_loops": [
    "后续还会继续推进高数复习"
  ]
}
```

Prompt requirements:

- summarize older episodic summaries, not raw dialogue
- keep stable facts tighter and less chatty than episodic diary text
- preserve only facts supported by multiple episodic slices or clearly important items
- do not hallucinate new long-term traits

## Semantic Compaction Flow

New background loop, parallel to current raw summary cycle:

1. fetch uncompacted episodic summaries for a profile/session
2. if uncompacted count < `EPISODIC_COMPACT_TRIGGER_COUNT`, stop
3. take oldest `EPISODIC_COMPACT_BATCH_SIZE=5`
4. run semantic summarization
5. write one semantic summary row
6. mark those `5` episodic rows as `is_semanticized=1`, `semantic_id=<new id>`
7. upsert semantic summary into vector store

Important:

- this should be asynchronous like the current summary queue
- it should respect the same generation/cancellation safety model

## Retrieval Design

### Current

Current retrieval fuses:

- semantic vector search over stored documents
- keyword/BM25 search over stored documents + semantic tags

### Proposed

Add semantic-memory retrieval as a third source.

Candidate sources:

- raw records
- episodic summaries
- semantic summaries

Recommended first implementation:

1. store semantic summaries in vector DB as normal entries
2. tag them with `entry_type="semantic_summary"`
3. keep one unified retrieval query path
4. reuse existing RRF fusion
5. add rerank bonus based on entry type

Suggested rerank preference:

- for direct recent-event questions:
  - raw > episodic > semantic
- for long-term preference/identity questions:
  - semantic gets a modest bonus

Practical V1 heuristic:

- `entry_type == "semantic_summary"` gets a small positive rerank bonus when query contains words like:
  - `一直`
  - `总是`
  - `喜欢`
  - `习惯`
  - `经常`
  - `长期`

This keeps the first version simple.

## Prompt Assembly Design

Current final prompt includes:

- recent raw timeline
- recent summaries
- retrieved memory snippets

Proposed final prompt adds:

- recent raw timeline
- recent episodic timeline
- recent semantic memory timeline
- retrieved memory snippets

Recommended order:

1. current user message
2. recent raw timeline
3. recent episodic summaries (`5~10`)
4. recent semantic summaries (`<=3`)
5. verifier-confirmed retrieved snippets

This preserves "recent first, stable long-term second".

## Visibility Rules

### Episodic visibility query

Need a new store method such as:

- `get_visible_episodic_summaries(profile_user_id, limit=10)`

Behavior:

- return most recent episodic summaries where `is_semanticized=0`
- limit to `EPISODIC_VISIBLE_MAX`

### Semantic visibility query

Need:

- `get_recent_semantic_summaries(profile_user_id, limit=3)`

Behavior:

- return semantic memories ordered by:
  - `last_reinforced_ts DESC`
  - `importance DESC`
  - `created_at DESC`

## Reinforcement Strategy

To better simulate human memory:

- semantic memories should not only be created once
- they can be reinforced when future episodic summaries overlap with the same stable facts

V1 can postpone actual merging.
But schema should already include:

- `reinforcement_count`
- `last_reinforced_ts`

V1 simplest rule:

- no merging yet
- only create new semantic summaries
- reinforcement fields stay ready for V2

## Avoiding Distortion

Main risk: repeated compression amplifies mistakes.

Mitigations:

1. keep episodic source links on every semantic summary
2. do not delete episodic rows
3. semantic prompt should prefer stable facts, not exhaustive detail
4. semantic memory should not dominate retrieval by default
5. verifier still validates retrieved memory snippets before final answer

## Rollout Plan

### Phase 1: Storage + Prompt + Config

- add `memory_semantic_summaries`
- add semantic prompt
- add config values
- add store methods for semantic rows and episodic semanticization flags

### Phase 2: Background Semantic Compaction

- add semantic compaction loop
- hook after raw summary generation or schedule in same queue
- add vector upsert for semantic summaries

### Phase 3: Prompt Consumption

- replace `get_recent_summaries(limit=5)` in final prompt assembly
- use:
  - visible episodic summaries
  - recent semantic summaries

### Phase 4: Retrieval Integration

- include semantic summary entries in retrieval/rerank
- tune entry-type bonus conservatively

### Phase 5: Reinforcement / Dedup

- optional later work
- merge or reinforce similar semantic summaries

## Recommended First Implementation Decisions

To keep the first version low-risk:

- keep raw compaction unchanged
- add a separate semantic table instead of mutating summary meaning
- compact episodic `10 -> compress oldest 5 -> leave recent 5`
- keep semantic visible count at `3`
- store semantic summaries in vector DB
- keep verifier unchanged at first
- add retrieval weighting only after storage and prompt integration are stable

## Summary

This design gives:

- no short-term or mid-term memory gap
- time-shaped episodic memory
- stable long-term memory that does not vanish only because it is old
- a natural path toward more human-like recall

In short:

- `working` remembers "what just happened"
- `episodic` remembers "what happened in that period"
- `semantic` remembers "what kind of person / habit / long-term thread this has become"
