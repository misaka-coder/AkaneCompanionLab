# Akane Lean-Down Audit v1

Status: audit report
Date: 2026-07-09

## Scope

This audit looks for redundant implementations, long-running parallel paths,
over-engineering, stale code, and package reintegration risks in
`AkaneCompanionLab`.

The goal is not to add features or introduce more abstraction. The goal is to
identify code paths that make maintainers unsure where the real implementation
lives.

`memcore` public/private packaging status is intentionally out of scope. This
report only records whether `memcore` creates an internal double implementation
inside Akane.

## 1. Executive Summary

- `memcore` is already the default primary memory backend, but legacy memory
  store, retrieval, timeline, vector write, and compaction paths still run in
  the main engine.
- Provider-native tools and legacy JSON `tool_call` now form a long-running
  dual channel. The migration is documented, but the default flip and old-path
  retirement target need to be made concrete.
- `companion_v01/tool_runtime.py` concentrates 40+ tool handlers across memory,
  attachments, browser, workspace, media, gifts, artifacts, persona, and tasks.
  This makes the tool layer hard to reason about.
- The `/pet/*` petdesk bridge is growing beyond a thin bridge by owning TTS,
  audio registry, display envelope, and resource manifest conversion while the
  old `/desktop-pet/*` surface remains broad.
- The MCP capability adapter uses private methods from `capcore-adapter-mcp`,
  which weakens the package boundary that M63 tries to enforce.
- `promptpack-core` is used for prompt blocks, but Akane still hand-assembles
  the final prompt in `PromptBuilder`; ownership should be decided explicitly
  before another assembler path appears.
- The local Python adapter exposes internal text helpers as model-visible tools,
  adding tool noise without clear user-facing value.
- The M50-M60 petdesk release lane is documented as frozen, but many scripts and
  guide entries remain. Future work should repair or replace these, not add more
  wrapper entry points.
- `charpack-core` reintegration is mostly thin, but
  `desktop_pet_character_resources.py` still imports underscored package
  helpers and should move to public APIs.

## 2. Findings

### AKANE-LD-001

ID: `AKANE-LD-001`

Severity: P1

Category: package-reintegration-risk / parallel-path

Files:

- `config.py`
- `companion_v01/engine.py`
- `companion_v01/engine_services/response_builder.py`
- `companion_v01/memcore_integration/manager.py`
- `companion_v01/memcore_integration/timeline.py`

Evidence:

- `config.py` defaults `MEMORY_BACKEND` to `memcore` and describes
  `legacy`/`dual` as compatibility and migration modes.
- `engine.py` still initializes `MemoryTimelineService`, `RetrievalService`,
  `MemoryCompactionService`, and `memcore_manager` in the same live engine
  setup.
- User and assistant turns still write through the legacy store/raw-vector path
  and then dual-write or update `memcore`.
- `response_builder.py` uses `memcore` prompt context in `memcore` mode and
  returns an empty context on failure rather than falling back to legacy memory,
  which means `memcore` is the effective main prompt path.
- `memcore_integration/timeline.py` keeps legacy timeline fallback and still
  delegates acquaintance/mirror helpers to the legacy service.

Why it hurts iteration:

Memory behavior is split across legacy store writes, legacy vector writes,
summary compaction, `memcore` prompt context, `memcore` retrieval, and timeline
facades. A maintainer debugging recall or metadata cannot quickly tell which
system is authoritative.

Recommended action:

Define a memory migration window with owner and removal target. In
`MEMORY_BACKEND=memcore`, stop initializing legacy retrieval, legacy compaction,
and legacy vector writes unless explicitly running `legacy` or `dual`. Keep
legacy code only for import/admin compatibility.

Deletion or migration risk:

High. This touches historical data migration, timeline reads, retrieval tools,
metadata updates, and compaction behavior.

Suggested validation:

- First chat message records raw memory.
- Rendered prompt contains visible `memcore` memory with time anchors.
- Model can call `retrieve_for_turn`.
- Model can call `read_timeline`.
- Assistant reply is recorded.
- Background compaction does not block the visible reply.
- Legacy import/backfill still works when explicitly invoked.

### AKANE-LD-002

ID: `AKANE-LD-002`

Severity: P1

Category: parallel-path / package-reintegration-risk

Files:

- `config.py`
- `companion_v01/tool_invocation.py`
- `companion_v01/llm_runtime.py`
- `companion_v01/tool_orchestration_engine.py`
- `companion_v01/native_tool_schema.py`
- `tests/test_tool_decision_eval.py`
- `docs/tool_system_decoupling_v1.md`

Evidence at audit time:

- `ENABLE_NATIVE_TOOL_DECISION` defaulted to false, while allowlisted tools and
  provider profiles already exist.
- `tool_invocation.py` still says native variants are placeholders for later
  migration, but `llm_runtime.py` already extracts provider native tool calls.
- `tool_orchestration_engine.py` normalizes native and legacy calls back into a
  shared `ToolInvocation`, then converts into the legacy handler call shape for
  validation and execution.
- `native_tool_schema.py` delegates OpenAI envelope construction to
  `capcore-provider-openai`, but still sanitizes legacy prompt clauses and maps
  back into Akane tool ids.
- Tests intentionally run legacy and native decision suites in parallel and
  assert parity.
- `docs/tool_system_decoupling_v1.md` documented the intended native-first
  direction, but the default production setting still kept legacy JSON as the
  main path unless enabled by config.

Current progress:

- The native tool decision total switch now defaults to enabled. Verified
  provider/model profiles use provider native schemas for allowlisted tools;
  unverified providers, explicit disable, or non-allowlisted tools still fall
  back to legacy JSON `tool_call`.
- Remaining LD-002 work is to expand native coverage/allowlist with acceptance
  gates and later decide whether the public `tool_call` field stays as a thin
  fallback or is deleted after native-first stabilizes.

Why it hurts iteration:

Every tool change can require updates in legacy prompt instructions, native
schema projection, provider capability profiles, prompt exclusions, tests, and
tool execution parity. That is a large maintenance surface for one capability.

Recommended action:

Pick a concrete native-first milestone. For verified providers, make native
tools the default. Keep legacy JSON `tool_call` only as fallback for unverified
providers or tools that are explicitly not migrated. Prevent new tool work from
adding only a legacy prompt path.

Deletion or migration risk:

Medium to high. Provider compatibility and streaming tool rounds require live
acceptance, and fallback behavior is important for OpenAI-compatible providers.

Suggested validation:

- `tests/test_tool_decision_eval.py`
- `tests/test_native_web_search_tooling.py`
- native tool smoke/acceptance gate for web, memory, and selected read-only
  tools
- live provider run before flipping the default

### AKANE-LD-003

ID: `AKANE-LD-003`

Severity: P2

Category: over-engineering / stale-code

Files:

- `companion_v01/tool_runtime.py`
- `companion_v01/engine.py`
- `companion_v01/capability_registry.py`

Evidence:

- `tool_runtime.py` contains 42 `ToolHandler` classes in one file, from
  `RetrieveMemoryToolHandler` and `ReadMemoryTimelineToolHandler` through
  browser, workspace, media, generated files, task workspace, gift, artifact,
  and persona management.
- `engine.py` builds one broad handler registry in `_build_tool_handlers`.
- `capability_registry.py` defines many domain modules and includes a
  `desktop_environment` module with no tools and a future-facing hint.

Why it hurts iteration:

The core startup/chat/QQ/desktop-pet experience has to coexist with a very wide
tool universe. Maintainers working on one capability must inspect shared prompt
selection, handler registry, native schema projection, and tests for unrelated
domains.

Recommended action:

Split tool handlers by domain and gate non-core domains behind explicit feature
profiles. Keep the minimal chat/QQ/desktop-pet smoke path small. Remove
future-only prompt hints until real tools exist.

Deletion or migration risk:

Medium. Many tests import handler classes directly from `tool_runtime.py`.

Suggested validation:

- capability selection tests
- QQ chat smoke
- desktop-pet smoke
- attachment/media/generated-file tests by domain

### AKANE-LD-004

ID: `AKANE-LD-004`

Severity: P2

Category: parallel-path / package-reintegration-risk

Files:

- `companion_v01/petdesk_bridge.py`
- `companion_v01/routes/petdesk.py`
- `companion_v01/routes/desktop_pet.py`
- `companion_v01/app.py`
- `docs/petdesk_akane_bridge_m32.md`
- `docs/package_reintegration_policy_m63.md`

Evidence:

- M32 says `/pet/*` is transitional redundancy and should not become a second
  permanent desktop-pet protocol surface.
- M63 says `petdesk-runtime` owns desktop rendering, resource resolving, audio
  queue, native hit regions, and runtime window behavior, while Akane `/pet/*`
  must remain a bridge.
- `petdesk_bridge.py` owns runtime manifest construction, display envelope
  conversion, safe handle/url normalization, speech segment normalization, and
  motion fallback.
- `routes/petdesk.py` owns `/pet/health`, `/pet/snapshot`,
  `/pet/resource-manifest`, `/pet/turn`, TTS synthesis fallback, an in-memory
  audio registry, and `/audio/petdesk/{token}`.
- `routes/desktop_pet.py` still exposes broad legacy desktop-pet endpoints for
  workspace, local import, audio upload/content, generated file content, file
  locations, music timeline, and vision.
- `app.py` mounts both `/desktop-pet-character-packs` and
  `/petdesk-character-packs`.

Why it hurts iteration:

Desktop-pet work can land in the legacy desktop API, the `/pet/*` bridge, or
`petdesk-runtime`. The bridge has started owning product behavior, not just
contract conversion.

Recommended action:

Choose the canonical surface. Either make `/pet/*` the sole runtime contract and
freeze `/desktop-pet/*` as compatibility, or move reusable resource/audio
contract shaping into petdesk protocol/package code and keep Akane as thin
backend routing glue.

Deletion or migration risk:

Medium. This affects petdesk release startup, static portrait display, TTS,
audio playback, and desktop workspace operations.

Suggested validation:

- `tests/test_petdesk_bridge.py`
- desktop-pet backend contract tests
- petdesk release acceptance
- manual static portrait plus TTS playback smoke

### AKANE-LD-005

ID: `AKANE-LD-005`

Severity: P2

Category: package-reintegration-risk

Files:

- `companion_v01/capability_adapters/mcp_stdio.py`
- `companion_v01/engine_services/tool_rounds.py`
- `docs/package_reintegration_policy_m63.md`

Evidence:

- M63 says `capcore-adapter-mcp` owns MCP tool-to-capcore descriptor conversion,
  capability id handling, and JSON-safe invocation arguments.
- `McpStdioCapabilityAdapter._descriptor_for_tool` calls
  `self._core._descriptor_for_tool(...)`.
- `McpStdioCapabilityAdapter._capability_id` calls
  `self._core._capability_id(...)`.
- `engine_services/tool_rounds.py` calls the adapter's private
  `_descriptor_for_tool` while building prompt-exposed dynamic adapter tools.

Why it hurts iteration:

Akane depends on private package internals. A package refactor can break the
host, and the adapter is not truly thin even if it appears to be a wrapper.

Recommended action:

Add a public descriptor projection API to `capcore-adapter-mcp`, then replace
Akane private calls with that API. Akane should only own server config,
profile policy, and process-calling policy.

Deletion or migration risk:

Low to medium. Capability id stability and prompt exposure behavior need to be
preserved.

Suggested validation:

- MCP adapter orchestration tests
- local capability catalog snapshot tests
- one configured MCP prompt-exposed tool smoke

### AKANE-LD-006

ID: `AKANE-LD-006`

Severity: P2

Category: over-engineering / package-reintegration-risk

Files:

- `companion_v01/prompt_blocks.py`
- `companion_v01/prompt_builder.py`
- `companion_v01/prompt_profiles.py`
- `companion_v01/engine_services/response_builder.py`
- `docs/package_reintegration_policy_m63.md`

Evidence:

- `prompt_blocks.py` subclasses `promptpack_core.PromptBlockRegistry`, which is
  an acceptable Akane content registry over package primitives.
- `prompt_profiles.py` builds mode-specific system prompt overrides from block
  ids.
- `response_builder.py` resolves a prompt profile and passes overrides into
  `PromptBuilder`.
- `PromptBuilder.build_final_generation_context()` still hand-assembles the
  final system prompt, format addendum, persona state, resource context, memory
  context, user prompt, current time, and prompt audit sections.
- M63 explicitly says M64 should audit this path and decide whether assembly
  mechanics move to `promptpack-core` or remain Akane-owned.

Why it hurts iteration:

The package owns prompt primitives and future assembly/cache-audit primitives,
while Akane still owns a large handwritten assembly path. Without a decision,
the next change can easily create a second assembler.

Recommended action:

Make an explicit M64 decision. Either keep final assembly Akane-owned and
document that `promptpack-core` stops at block primitives for this product, or
migrate one concrete assembly path to `promptpack-core` and delete the old
handwritten path.

Deletion or migration risk:

Medium. Prompt byte stability, cache prefix behavior, JSON output schema, and
prompt audit sections are sensitive.

Suggested validation:

- `tests/test_prompt_builder.py`
- prompt audit section snapshot
- QQ final response smoke
- desktop-pet final response smoke

### AKANE-LD-007

ID: `AKANE-LD-007`

Severity: P3

Category: over-engineering

Files:

- `companion_v01/capability_adapters/python_local.py`
- `companion_v01/engine_services/tool_rounds.py`

Evidence:

- `python_local.py` exposes `python.akane.normalize_text`,
  `python.akane.extract_semantic_tags`, and
  `python.akane.detect_time_of_day` with `prompt_exposed=True`.
- `engine_services/tool_rounds.py` appends prompt-exposed dynamic adapter tools
  to the current capability selection.

Why it hurts iteration:

These are deterministic internal helpers, not user-facing capabilities. Making
them visible to the model increases tool choice noise and creates more prompt
surface without improving current Akane user experience.

Recommended action:

Mark these helper capabilities as not prompt-exposed, or remove the adapter
registration entirely and keep the functions as deterministic host code.

Deletion or migration risk:

Low.

Suggested validation:

- Python adapter orchestration tests
- capability selection output does not include `python.akane.*` helpers

### AKANE-LD-008

ID: `AKANE-LD-008`

Severity: P2

Category: stale-code / over-engineering

Files:

- `docs/petdesk_release_closeout_m61.md`
- `docs/petdesk_operator_guide_m54.md`
- `README.md`
- `scripts/build_petdesk_runtime_release.ps1`
- `scripts/check_petdesk_release.ps1`
- `scripts/accept_petdesk_release.ps1`
- `scripts/export_petdesk_release_bundle.ps1`
- `scripts/audit_petdesk_release_bundle.ps1`
- `scripts/release_petdesk_bundle.ps1`
- `scripts/start_petdesk_runtime_bundle.ps1`
- `scripts/start_petdesk_runtime.ps1`
- `scripts/stop_petdesk_runtime.ps1`

Evidence:

- M61 says the M50-M60 petdesk release-tooling line is closed/frozen except for
  repair, safety hardening, and compatibility fixes.
- The repo has many release/runtime wrapper scripts and a long operator guide
  command map.
- README still lists many petdesk release commands.

Why it hurts iteration:

Future desktop productization work can keep adding wrappers instead of reducing
or replacing existing ones. The release lane becomes a second product surface.

Recommended action:

Mark the release scripts as maintenance-only. New release commands should
replace existing commands or fix a real operator failure. Move old milestone
docs into an archive/index and keep README focused on the recommended current
entry points.

Deletion or migration risk:

Low to medium. Operators may rely on existing script names.

Suggested validation:

- `check_petdesk_release.ps1 -CheckOnly`
- release acceptance dry run
- bundle audit dry run
- stop helper check mode

### AKANE-LD-009

ID: `AKANE-LD-009`

Severity: P3

Category: package-reintegration-risk

Files:

- `companion_v01/resource_manifest.py`
- `companion_v01/desktop_pet_character_resources.py`
- `companion_v01/character_context_library.py`
- `docs/package_reintegration_policy_m63.md`

Evidence:

- `resource_manifest.py` cleanly re-exports `charpack_core.resource_manifest.ResourceManifest`.
- `character_context_library.py` cleanly imports public context library classes.
- `desktop_pet_character_resources.py` imports several underscored helpers from
  `charpack_core.character_resources`, including `_clean_text`,
  `_coerce_alias_map`, `_resolve_manifest_asset_file`, and related helpers.
- M63 already calls this a coupling smell that is acceptable only as a
  transition bridge.

Why it hurts iteration:

Akane depends on package internals. `charpack-core` cannot freely refactor those
helpers, and the Akane adapter is less thin than intended.

Recommended action:

Promote the needed helper behavior to public `charpack-core` APIs, or reduce
Akane to public class/function re-exports only.

Deletion or migration risk:

Low.

Suggested validation:

- character resource tests
- petdesk resource manifest tests
- QQ emotion alias and character-pack prompt context regression tests

## Validation Note

This was a read-only audit. No code was changed as part of the audit itself.
No tests were run for this report.
