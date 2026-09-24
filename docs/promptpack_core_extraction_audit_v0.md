# promptpack-core extraction audit v0

Status: implementation-ready audit
Date: 2026-07-02

This document is a continuation note for extracting a reusable prompt assembly
package from Akane. It is intentionally detailed so a future AI coding agent can
continue after context compaction without rereading the whole project.

## Short Answer

Extract `promptpack-core`, but keep v0.1 narrow.

The valuable reusable layer is not Akane's full `PromptBuilder` and not Akane's
output adapters. The reusable layer is:

- prompt blocks and profiles;
- stable-prefix versus dynamic-tail section ordering;
- prompt audit sections and section fingerprints;
- provider-neutral cache intent metadata;
- safe rendering boundaries for host-supplied context.

Do not move Akane-specific roleplay text, output normalization, model calls,
tool execution, memory storage, or UI/runtime state into this package.

## Why This Package Exists

The reusable Akane ecosystem already has these pieces:

- `memcore`: memory facade, rendered visible memory, retrieval/timeline tools,
  optional final-output memory metadata contract.
- `charpack-core`: character pack resources, persona prompt context, visual
  manifest normalization, context-library projection.
- `capcore`: capability registration, validation, permission gates.
- `capcore-provider-openai` / `capcore-provider-anthropic`: provider-native
  tool schema and tool result feedback.

What is missing is the thin host-neutral layer that says:

```text
stable product/persona/tool/output rules
+ character/memory/tool contracts
+ semi-stable resources / summaries
+ dynamic current state / current time / current user message / tool results
-> provider-ready messages + audit + cache plan
```

Right now Akane does this inside `companion_v01/prompt_builder.py` and related
files. That works for Akane, but every new AI product will otherwise copy and
slightly mutate the same prompt assembly discipline.

## Sources Reviewed

Local Akane files:

- `companion_v01/prompt_builder.py`
- `companion_v01/prompt_blocks.py`
- `companion_v01/prompt_profiles.py`
- `companion_v01/persona_config.py`
- `companion_v01/engine_services/turn_context.py`
- `companion_v01/final_output_engine.py`
- `companion_v01/output_adapters.py`
- `companion_v01/memcore_integration/manager.py`
- `companion_v01/memcore_integration/adapters.py`
- `companion_v01/llm_runtime.py`
- `services/llm_client.py`
- `tests/test_prompt_builder.py`
- `docs/akane_multimode_output_profiles_v1.md`
- `docs/llm_prompt_cache_optimization_notes.md`

Sibling package docs:

- `memcore/AGENTS.md`
- `memcore/docs/model_prompt_playbook_v1.md`
- `charpack-core/docs/usage_flow_v0.md`
- `capcore-provider-openai/docs/agent_integration_playbook_v0.md`
- `capcore-provider-anthropic/docs/agent_integration_playbook_v0.md`

Official prompt caching references checked:

- OpenAI prompt caching guide:
  `https://developers.openai.com/api/docs/guides/prompt-caching`
- OpenAI Chat Completions API reference:
  `https://platform.openai.com/docs/api-reference/chat/create`
- Anthropic prompt caching guide:
  `https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching`
- Anthropic Messages API:
  `https://docs.anthropic.com/en/api/messages`

## Current Akane Prompt Assembly Map

### Static and profile-level prompt text

`companion_v01/prompt_blocks.py` defines:

- `PromptBlock`
- `PromptBlockRegistry`
- block groups:
  - `COMMON_RESPONSE_BLOCKS`
  - `SCENE_STATIC_SYSTEM_BLOCKS`
  - `SCENE_LIVE2D_SYSTEM_BLOCKS`
  - `DESKTOP_PET_SYSTEM_BLOCKS`
  - `QQ_TEXT_SYSTEM_BLOCKS`
- helpers:
  - `build_system_prompt`
  - `build_scene_static_system_prompt`
  - `build_desktop_pet_system_prompt`
  - `build_qq_text_system_prompt`

This is the strongest extraction candidate. The registry/composition mechanism
is generic, but the concrete text is mostly Akane-specific and should stay in
Akane or move to Akane-provided fixtures later.

`companion_v01/prompt_profiles.py` defines:

- `PromptModule`
- `PromptProfile`
- `PromptProfileRegistry`

This is also extractable, with one important change: `promptpack-core` must not
depend on Akane's `ClientMode` enum. A prompt profile should use plain strings
for `id`, `mode`, `modules`, and `block_ids`.

### Main final generation prompt

`PromptBuilder.build_final_generation_context()` currently returns:

- `system_prompt`
- `system_extra_blocks`
- `history_turns`
- `user_prompt`
- `fallback`
- `prompt_audit_sections`

Important current ordering:

1. `system_prompt`
   - base final system prompt or profile override;
   - mode/output format addendum;
   - tool prompt context;
   - current assistant persona state inserted after the format rules.
2. `system_extra_blocks`
   - resource context.
3. `user_prompt`
   - debug flag;
   - final user prompt suffix;
   - persona reference context;
   - semantic memory;
   - episodic summaries;
   - raw unsummarized timeline;
   - retrieval snippets;
   - extra context;
   - current visual state;
   - current user message;
   - current time.
4. `prompt_audit_sections`
   - full sections plus named subsections.

The function is Akane-specific because it knows visual defaults, output fallback
shape, current state wording, and persona conventions. The extractable part is
the generic section assembly and audit model.

### Aux prompts

`PromptBuilder` also builds router, verifier, summary, semantic summary, and
semantic reinforcement prompts.

These should not be extracted in v0.1. They are useful design references, but
they are tightly coupled to Akane memory behavior and to memcore's compaction
tasks. If a future package wants reusable aux prompt templates, that should be
another layer after `promptpack-core` is stable.

### Runtime cache behavior

`companion_v01/llm_runtime.py` and `services/llm_client.py` currently provide:

- `prompt_cache_key` on chat and aux calls.
- Official OpenAI-like cache hints only when the base URL looks official, unless
  forced by config.
- `prompt_cache_retention` values normalized to `in_memory` or `24h`.
- Anthropic `system_extra_blocks` kept as separate system text blocks.
- Up to 4 Anthropic system blocks marked with `cache_control: {"type":
  "ephemeral"}` in the local compatibility client.
- Usage normalization for:
  - Anthropic `cache_read_input_tokens` / `cache_creation_input_tokens`;
  - DeepSeek-like `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`;
  - OpenAI-style cached token reporting is noted in docs.
- Prompt audit records for selected cache keys.

`promptpack-core` should not send provider parameters. It should produce a
cache plan/audit that provider layers or host runtimes can interpret.

## What Is Reusable

Reusable now:

- immutable prompt blocks;
- block registry and deterministic composition;
- profile metadata that selects block ids and modules;
- prompt sections with placement and cache role;
- rendering a provider-neutral assembly result;
- audit sections with normalized text, hashes, and rough length metrics;
- stable-prefix hash for churn detection;
- section ordering helpers;
- cache intent metadata such as "stable", "semi_stable", "dynamic",
  "ephemeral", and "provider_cacheable".

Reusable later:

- token budget and truncation policy;
- prompt diff/audit benchmark runner;
- provider adapters that transform cache intent into OpenAI/Anthropic payload
  hints;
- TOML/JSON prompt profile loading;
- output schema prompt helpers.

Not reusable / keep in Akane:

- Akane's concrete Chinese persona and mode text;
- `PersonaConfig` fields and `persona_profiles.toml` schema;
- output fallback object for Akane's scene/character/persona state;
- `final_output_engine.py`;
- `output_adapters.py`;
- model API calls and retry logic;
- native tool execution;
- resource scanning;
- memory storage/retrieval.

## Proposed Package Boundary

Package name: `promptpack-core`.

Dependencies: stdlib-only for v0.1.

Use this package for:

- defining prompt blocks;
- composing blocks into stable text;
- registering prompt profiles;
- creating ordered prompt sections;
- separating stable, semi-stable, dynamic, and ephemeral sections;
- rendering an assembly into simple chat messages or `system_prompt` /
  `system_extra_blocks` / `user_prompt` shapes;
- computing prompt audit hashes and length metrics.

Do not use this package for:

- calling OpenAI, Anthropic, DeepSeek, or local models;
- loading API keys;
- deciding model names;
- rendering approval UI;
- executing capcore tools;
- reading character packs directly;
- recording or retrieving memcore memory;
- normalizing Akane-specific final JSON.

## Prompt Caching Design

Prompt caching must be a first-class design constraint, but v0.1 should stay
provider-neutral.

### General rule

Most providers cache repeated input prefixes. Therefore the prompt assembly
should make the longest stable prefix byte-for-byte stable:

1. fixed product/persona/safety/output/tool rules;
2. fixed memcore or tool-use instructions;
3. stable character pack system context;
4. semi-stable resource manifests or long-lived reference text;
5. dynamic memory/current state/current user message/tool results.

Do not put current time, request ids, rendered visible memory, transient visual
state, or tool results before stable rules.

### OpenAI note

The OpenAI docs describe prompt caching as automatic for supported models and
repeated prefixes, with cached token accounting exposed through usage details.
The Chat Completions API reference also exposes request fields such as
`prompt_cache_key` and `prompt_cache_retention`.

Implication for `promptpack-core`:

- Produce stable-prefix ordering and fingerprints.
- Expose a recommended cache key, but do not inject OpenAI fields itself.
- Keep OpenAI-specific retry/fallback behavior in host/provider runtime.

### Anthropic note

Anthropic prompt caching uses explicit cache control on content blocks. Their
docs recommend placing cache breakpoints after reusable long content and putting
frequently changing text later.

Implication for `promptpack-core`:

- Mark sections with cache intent.
- Render cacheable system/developer blocks separately so an Anthropic provider
  adapter can add `cache_control`.
- Do not assume every section should be cached; dynamic current user content
  should remain uncached.

### Akane-specific cache lesson

Akane's existing cache notes say 70-75% cache hit on the main chat path can be a
healthy range because visual state, current memory, current time, and retrieval
snippets are legitimately dynamic. The goal is not maximum hit rate at any cost.

`promptpack-core` should optimize for:

- stable prefix discipline;
- observability of churn;
- avoiding duplicate dynamic content;
- preserving memory accuracy and reply quality.

It should not promise a fixed hit-rate target.

### DeepSeek-like automatic disk cache lesson

DeepSeek-style providers are especially important for roleplay hosts because
they are widely used and inexpensive for long companion chats. Their disk cache
is automatic, but it behaves like a complete-prefix-unit cache: a later request
must fully match a prefix unit that has already been materialized by prior
requests. Shared long prefixes may be detected and written after overlap is
observed, so a two-request benchmark can under-report steady-state cache
benefits.

Implications for `promptpack-core`:

- keep append-only message order where possible;
- never rewrite or reorder old history just for formatting;
- keep stable rules/persona/tool contracts before dynamic memory and current
  input;
- expose cumulative prefix hashes, not only a stable-only hash;
- benchmark at least three repeated-prefix turns for DeepSeek-like providers;
- keep provider usage observation in host runtime, such as
  `prompt_cache_hit_tokens` and `prompt_cache_miss_tokens`.

## Proposed Public API v0.1

### Types

```python
from promptpack_core import (
    CacheRole,
    PromptAssembly,
    PromptAssembler,
    PromptBlock,
    PromptBlockRegistry,
    PromptPlacement,
    PromptProfile,
    PromptProfileRegistry,
    PromptSection,
    cumulative_prefix_hashes,
)
```

`CacheRole`:

- `STABLE`
- `SEMI_STABLE`
- `DYNAMIC`
- `EPHEMERAL`

`PromptPlacement`:

- `SYSTEM`
- `DEVELOPER`
- `USER`
- `ASSISTANT`
- `TOOL`

`PromptBlock`:

- `id: str`
- `text: str`
- optional `placement`
- optional `cache_role`
- optional `metadata`

`PromptSection`:

- `name: str`
- `text: str`
- `placement: PromptPlacement`
- `cache_role: CacheRole`
- `priority: int = 0`
- `metadata: dict[str, str]`

`PromptProfile`:

- `id: str`
- `mode: str`
- `modules: tuple[str, ...]`
- `block_ids: tuple[str, ...]`
- optional `fallback_profile_id`
- optional `metadata`

`PromptAssembly`:

- ordered `sections`;
- derived `messages`;
- `system_prompt`;
- `system_extra_blocks`;
- `user_prompt`;
- `audit_sections`;
- `stable_prefix_hash`;
- `cumulative_prefix_hashes`;
- `cache_plan`.

### Registry behavior

The registry should:

- preserve deterministic order;
- ignore or fail on missing blocks according to an explicit policy;
- never reorder block ids alphabetically;
- normalize ids by stripping whitespace;
- avoid mutating registered blocks.

### Assembler behavior

The assembler should:

- keep `STABLE` sections before `SEMI_STABLE`, then `DYNAMIC`, then
  `EPHEMERAL`, unless the caller passes explicit ordered sections;
- within a role/placement, preserve caller order;
- render empty sections out by default;
- compute fingerprints from full, untruncated section text;
- compute cumulative full-prefix hashes at every section boundary;
- keep audit snapshots safe and bounded separately from fingerprints.

Fingerprint rule:

- Hash canonical section identity and full text.
- Never truncate before hashing.
- If future token budgeting truncates rendered text, audit should record both
  original and rendered lengths.

## Integration Shape With Existing Packages

### With charpack-core

Host flow:

```python
context = character_service.build_persona_prompt_context(pack_id, client_mode=mode)
assembler.add_section(
    "character.system_context",
    context["system_context"],
    placement="system",
    cache_role="stable",
)
assembler.add_section(
    "character.reference_context",
    context["reference_context"],
    placement="user",
    cache_role="semi_stable",
)
```

`promptpack-core` should not import `charpack_core`.

### With memcore

Host flow:

```python
ctx = mem.build_prompt_context(current=cur)
rendered = mem.render_prompt_context(ctx)
assembler.add_section(
    "memory.visible",
    rendered,
    placement="user",
    cache_role="dynamic",
)
```

Add stable memcore tool/output-contract instructions separately as stable
sections. Dynamic rendered memory must not enter the stable prefix.

`promptpack-core` should not import `memcore`.

### With capcore/provider packages

Host flow:

```python
tool_text = build_host_tool_instruction(tool_set)
assembler.add_section(
    "tools.instructions",
    tool_text,
    placement="system",
    cache_role="stable",
)
```

Provider-native tool schemas are passed through provider SDK payloads, not
through prompt text, when native tools are available.

`promptpack-core` should not import `capcore`.

## Implementation Phases

### Phase 0: audit document

This file.

Validation:

```bash
git diff --check
```

### Phase 1: create `promptpack-core`

Directory:

```text
<workspace>\promptpack-core
```

Files:

```text
promptpack-core/
  pyproject.toml
  README.md
  AGENTS.md
  promptpack_core/
    __init__.py
    blocks.py
    profiles.py
    sections.py
    assembly.py
    audit.py
  docs/
    usage_flow_v0.md
    implementation_v0.md
  examples/
    minimal_prompt_assembly.py
  tests/
    test_blocks.py
    test_profiles.py
    test_assembly.py
```

Initial API:

- block registry;
- profile registry;
- prompt sections;
- assembly rendering;
- audit hashes;
- minimal cache plan.

Validation:

```bash
uv run --extra dev python -m unittest discover -s tests -v
uv run --extra dev ruff check .
uv run --extra dev ruff format --check .
uv run examples/minimal_prompt_assembly.py
uv run --extra dev python -m build
git diff --check
```

### Phase 2: Akane compatibility slice

Do not rewrite `PromptBuilder`.

Start with a tiny internal use:

- optionally replace `PromptBlock` / `PromptBlockRegistry` in
  `companion_v01/prompt_blocks.py` with imports from `promptpack-core`, while
  leaving all Akane block text in Akane;
- keep all tests passing;
- do not migrate final generation context yet.

Validation:

```bash
.venv/Scripts/python.exe -m unittest tests.test_prompt_builder -v
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe scripts/verify_extracted_package_independence.py
git diff --check
```

### Phase 3: prompt audit adapter

After Phase 1 is stable, add an Akane adapter that converts
`prompt_audit_sections` into `promptpack-core` audit records. This gives Akane
better churn observability without changing prompt behavior.

Do not change prompt ordering in this phase.

### Phase 4: full final-generation assembly migration

Only after prompt audit proves stable:

- move generic assembly logic into `promptpack-core`;
- keep Akane-specific fallback/output contract in Akane;
- compare cache hit, prompt tokens, latency, and reply quality before and after.

## Acceptance Criteria For Phase 1

`promptpack-core` v0.1 is acceptable when:

- package is stdlib-only;
- README and AGENTS explain host usage without reading source;
- all public objects are exported from `promptpack_core.__init__`;
- block/profile composition is deterministic;
- stable prefix hash changes when stable text changes and remains unchanged when
  only dynamic sections change;
- audit hashes are full-text hashes, not truncated previews;
- example demonstrates charpack/memcore/capcore-style sections without importing
  those packages;
- tests, ruff, format, example, and build pass.

## Risks And Guardrails

Risk: package becomes a second prompt framework with too much opinion.

Guardrail: v0.1 only provides data structures and deterministic rendering.
Akane and host apps own content.

Risk: cache metadata becomes provider-specific too early.

Guardrail: expose provider-neutral `CacheRole` and section metadata. Provider
packages or host runtime decide how to map it to payload fields.

Risk: full Akane prompt migration changes behavior.

Guardrail: first integration should only reuse block/profile primitives or audit
conversion. Do not reorder final prompts in the first Akane integration commit.

Risk: fingerprints accidentally use truncated previews.

Guardrail: full text for hash, bounded text only for preview/logging.

Risk: dynamic memory moves into stable prefix.

Guardrail: tests should assert current time/current user message/rendered
visible memory are dynamic and do not affect stable-prefix hash.

## Commit Discipline

Current Akane worktree has an unrelated modified file:

```text
docs/capability_adapter_v1_m1_handoff_prompt.md
```

Do not stage, revert, or edit it unless the user explicitly asks.

Commit separately:

1. Akane audit document.
2. `promptpack-core` package.
3. Any later Akane integration slice.
