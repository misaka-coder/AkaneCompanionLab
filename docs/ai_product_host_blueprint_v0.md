# AI Product Host Blueprint v0

Status: integration blueprint for new host applications
Date: 2026-07-02

This document explains how to build a real AI product host from the packages
extracted out of Akane. It is for humans and AI coding agents who need a
complete product loop without reading every package's source first.

The extracted packages are enough for the core of a large AI product. They are
not a whole product platform by themselves. The host application still owns the
model clients, UI, streaming, persistence policy, permissions UX, deployment,
observability, and product-specific behavior.

## Target Shape

```text
client request
  -> host identity/session resolver
  -> memcore user-turn record
  -> promptpack stable/dynamic prompt assembly
  -> provider-native tool schema build
  -> host model call
  -> provider tool-call parse
  -> capcore prepare/approval/adapter invoke
  -> provider-native tool result feedback
  -> host model follow-up
  -> final output parse/normalize
  -> memcore metadata + assistant-turn record
  -> UI/event delivery + background compaction
```

The host should treat this as one product transaction with structured partial
results. Do not let individual packages own cross-cutting policy such as model
budgets, retry limits, user authorization, UI rendering, or deployment paths.

## Package Roles

| Layer | Packages | Host uses them for |
| --- | --- | --- |
| Prompt discipline | `promptpack-core` | Stable/dynamic section ordering, cache roles, prompt hashes |
| Memory | `memcore` | Turn recording, visible context, retrieval, timeline reads, optional final JSON adapter |
| Character runtime | `charpack-core` | Character identity/persona/resources and visual output normalization |
| Tool contracts | `capcore` | Capability descriptors, schemas, validation, risk/effect policy |
| Tool sources | `capcore-adapter-python`, `capcore-adapter-mcp`, `capcore-adapter-speech`, `capcore-adapter-comfyui` | Concrete capability adapters |
| Provider envelopes | `capcore-provider-openai`, `capcore-provider-anthropic` | Native tool schema, tool call parsing, tool result feedback |
| Shared runner | `capcore-provider-native-tools` | Provider-neutral execution hygiene used by provider packages |
| Host safety helpers | `capcore-host-utils` | Approval store/fingerprints, workspace path ids, JSON-safe approval previews |

Do not add host APIs, secrets, UI behavior, or provider SDK calls to these
packages. Add those in the host.

## Standard Turn Loop

1. **Resolve identity and surface**
   - `tenant_id`, `user_id`, `domain_id`, `conversation_id`;
   - optional `Actor` for group chat speaker attribution;
   - current surface such as `web`, `desktop`, `qq_text`, `cli`;
   - active character pack/profile and enabled tool groups.

2. **Record the user turn**
   - call `MemorySystem.record_user_turn(...)` immediately after input is
     accepted;
   - pass a stable timestamp and timezone;
   - attach host-known metadata only when it is trustworthy.

3. **Build visible memory**
   - call `build_prompt_context(current=cur)`;
   - call `render_prompt_context(ctx)`;
   - expose model tools that wrap `retrieve_for_turn(current=cur, ...)` and
     `read_timeline(...)`.

4. **Build persona/resource context**
   - if the product has characters, use `charpack-core` for persona/resource
     prompts;
   - keep host-private paths and profile secrets out of prompts;
   - after final output, normalize visual/emotion choices against the manifest.

5. **Assemble prompt**
   - use `promptpack-core` sections with stable rules first;
   - put dynamic memory, current time, current user message, runtime state, and
     tool results after the stable prefix;
   - keep chronological assistant/tool history in provider message order. Do
     not reorder history through `stable_first=True`.

6. **Expose selected tools**
   - list capabilities from chosen adapters;
   - filter by product policy, surface, user role, and current mode;
   - build provider tool sets with OpenAI/Anthropic provider packages;
   - keep the exact returned tool set for parsing the model response.

7. **Call the model**
   - provider packages do not call model APIs;
   - the host owns keys, retries, model allowlists, streaming, and budgets.

8. **Execute tool calls**
   - parse all provider tool calls/tool uses;
   - decide execution policy: sequential, limited concurrency, or reject mixed
     unsafe calls;
   - execute through provider runners so `capcore.prepare_invocation()` and
     approval are always applied;
   - feed provider-shaped result messages/blocks back to the model.

9. **Finalize response**
   - parse final JSON output when using `memcore`'s output contract;
   - never store invalid JSON or tool envelopes as natural language memory;
   - normalize character visuals/resources before UI delivery;
   - record assistant speech with `record_assistant_turn(...)`;
   - call `compact_due_background()` for live chat.

## Tool Execution Policy

Provider parsers should return every tool call the model emitted. The host owns
the execution policy.

Recommended defaults:

- Run read-only, low-risk independent calls with bounded concurrency.
- Run write/file/shell/media-generation calls sequentially unless the host has
  explicit conflict detection.
- Never run a high-risk call before approval is granted.
- If a batch mixes approval-required and safe calls, either:
  - ask first and then run the batch in deterministic order; or
  - run safe reads first, then ask for risky writes.
- Feed every parse, validation, permission, and adapter failure back as
  structured tool feedback. Do not silently drop a failed tool call.

For coding assistants, keep shell/file tools in the host layer and expose them
through `capcore` descriptors. Use `capcore-host-utils.WorkspaceScope` for
workspace path ids and approval previews. Approval UI should say exactly what
is being authorized and whether the grant is one-shot, exact-args, tool-level,
or broader session access.

## Prompt Cache Discipline

Cache-friendly hosts keep stable prefixes byte-for-byte stable.

Put in the stable prefix:

- product/persona/safety/tool rules;
- stable output contracts;
- stable tool descriptions and schemas;
- long unchanged reference documents.

Put after the stable prefix:

- current time, request ids, trace ids, session ids;
- rendered visible memory and retrieved snippets;
- current user input;
- current UI/desktop/game state;
- tool results;
- temporary task context and debug flags.

Use `PromptAssembly.stable_prefix_hash` and
`PromptAssembly.cumulative_prefix_hashes` to detect accidental churn. For
DeepSeek-like automatic prefix caches, unchanged long context should appear
before the variable question so the provider can reuse cached prefix units.

## Failure Handling

Large AI products need boring, explicit failure states.

| Failure | Host response |
| --- | --- |
| Model call timeout | retry within budget or surface `model_timeout` |
| Tool parse error | return provider-shaped error feedback to the model |
| Unknown tool name | return structured unknown-tool feedback |
| Validation failure | return `invalid_arguments` feedback with safe details |
| Approval missing/denied | return permission feedback; do not invoke adapter |
| Adapter exception | hide raw exception by default; expose only on trusted dev surfaces |
| Invalid final JSON | retry once with the same stable prefix or surface structured failure |
| Memory compaction failure | log and continue visible reply; do not store fake summaries |

Do not turn missing external services into fake success. TTS, ASR, ComfyUI,
MCP, shell, and local media actions should degrade with explicit status.

## Observability And Budgets

The host should attach a trace id internally, but not inside the stable prompt
prefix. Record:

- prompt section ids and cache-role hashes;
- selected model/provider and latency;
- token usage and provider cache hit/miss fields when available;
- tool call ids, capability ids, risk level, approval decision, latency, and
  sanitized result status;
- memory record ids and compaction status;
- final output parse status.

Budget policies belong to the host:

- max model turns per user request;
- max tool calls per turn;
- max concurrent tools;
- max approval prompts per request;
- max retrieved memory snippets;
- max generated media jobs;
- retry limits per provider/tool.

## Product Profiles

Start with narrow host profiles. Add packages only when the product actually
needs the capability.

| Product | Minimal package set |
| --- | --- |
| Coding assistant | `memcore`, `promptpack-core`, `capcore`, `capcore-adapter-python`, provider packages, `capcore-host-utils` |
| Character companion | coding/chat base plus `charpack-core`, `capcore-adapter-speech` |
| Image workflow app | chat/tool base plus `capcore-adapter-comfyui` |
| MCP-heavy agent | chat/tool base plus `capcore-adapter-mcp` |
| Voice notebook | `memcore`, `promptpack-core`, `capcore`, `capcore-adapter-speech` |

Avoid forcing every product through the full Akane stack. The package ecosystem
is composable; the host should pick the smallest useful subset.

## When To Extract `agentloop-core`

Do not extract a shared turn engine until two or more hosts repeat the same
code shape with the same policy needs.

Extract later if these stay stable across Akane, code-pal, and a third product:

- provider-neutral turn state machine;
- message/tool-result accumulator;
- bounded tool concurrency scheduler;
- retry/budget policy objects;
- final output parser/retry hook;
- tracing event schema.

Do not extract:

- UI rendering;
- concrete file/shell tools;
- product prompts/persona;
- model keys/config storage;
- deployment scripts;
- user-facing approval UX.

## Validation

For host-level integration work in Akane, run:

```powershell
.\.venv\Scripts\python.exe .\scripts\smoke_ai_product_host_turn.py
.\.venv\Scripts\python.exe .\scripts\smoke_extracted_package_ecosystem.py
.\.venv\Scripts\python.exe .\scripts\audit_extracted_packages_release.py
git diff --check
```

The host turn smoke is no-network and does not call OpenAI, Anthropic,
DeepSeek, MCP stdio, ComfyUI, TTS, ASR, or shell commands. It verifies the
recommended product loop shape against real package APIs.
