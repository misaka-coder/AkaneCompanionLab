# Akane Extracted Packages Ecosystem v0

Status: release-state consolidation guide
Date: 2026-07-13

This document is the shared map for packages extracted from Akane. It is meant
for humans and AI coding agents who need to build a new AI product from these
pieces without rereading every source file first.

The goal is not to move Akane-specific behavior into packages. The goal is to
keep each package narrow, stable, and reusable, while Akane remains one host
application that composes them.

## One-Screen Architecture

```text
host application
  -> channelcore-onebot  OneBot inbound contracts and message normalization
  -> promptpack-core      prompt sections, cache roles, audit hashes
  -> charpack-core        character pack context and resource manifests
  -> memcore              memory lifecycle, retrieval, timeline context
  -> capcore              capability descriptors, validation, permission gate
      -> adapter-python   explicit local Python callables
      -> adapter-mcp      MCP stdio tools
      -> adapter-speech   TTS / ASR capabilities
      -> adapter-comfyui  ComfyUI workflow capabilities
  -> capcore-provider-native-tools
      shared runner: parse -> route -> prepare -> approval -> invoke -> feedback
  -> capcore-provider-openai
      OpenAI Chat Completions native tool schema/result envelope
  -> capcore-provider-anthropic
      Anthropic Messages tool schema/result envelope
  -> capcore-host-utils
      host-side approval store, workspace/path ids, JSON-safe audit previews
```

The host still owns:

- model API clients, keys, model selection, retries, and budgets;
- UI, routes, streaming, Tauri/desktop behavior, QQ delivery, and logs;
- approval UI policy and persistent user grants;
- concrete product prompts and persona wording;
- local data directories, user/session/tenant identity, and deployment scripts.

## Package Map

| Package | Owns | Does not own | 0.1 stable surface |
| --- | --- | --- | --- |
| `channelcore-onebot` | Host-neutral OneBot private/group/poke event contracts, text/CQ/attachment/reply/@ normalization, self-id, freshness, atomic replay admission, group-trigger/follow decisions, and quoted-message lookup/scope validation | Webhook secrets/status mapping, Bot transport binding, host sessions, product commands, attachment materialization, vision/model/TTS, and outbound delivery | `InboundMessage`, `InboundParseResult`, `ConversationRef`, `ActorRef`, `ReplyRef`, `AttachmentRef`, `EventAdmissionConfig`, `OneBotEventAdmission`, `GroupTriggerPolicy`, `QuotedMessage`, `QuoteLookupResult`, `resolve_quoted_message`, `normalize_inbound_event` |
| `memcore` | `MemorySystem` facade, raw/episodic/semantic memory, prompt context, retrieval, timeline, optional final JSON output adapter | Host chat loop, production model/embedding selection, UI, provider calls | `MemorySystem`, `MemoryConfig`, `Namespace`, `Actor`, `SQLiteMemoryStore`, `InMemoryVectorIndex`, `LLMClient`, `EmbeddingProvider`, `build_chat_output_contract_prompt`, `parse_chat_output` |
| `promptpack-core` | Prompt blocks/profiles/sections, stable/dynamic assembly, cache role metadata, prompt audit hashes | Provider calls, Akane prompt text, memory, tools, output parsing | `PromptBlock`, `PromptBlockRegistry`, `PromptProfile`, `PromptProfileRegistry`, `PromptSection`, `PromptAssembler`, `PromptAssembly`, cache/audit helpers |
| `charpack-core` | Character pack metadata, persona/resource prompt projection, context library reading, visual output normalization | Pack editor UI, TTS playback, QQ sending, desktop rendering, memory/session policy | `CharacterPackResourceService`, `ResourceManifest`, `CharacterContextLibraryService`, `sanitize_character_pack_id` |
| `capcore` | Capability descriptors/manifests, schema projection, invocation arg validation, risk/confirm/effects policy, permission request/decision primitives, provider-safe name map | Concrete adapters, approval UI/store, provider envelopes, model calls | `CapabilityDescriptor`, `CapabilityIOSlot`, `CapabilityResult`, `InvocationContext`, `CapabilityRegistry`, `prepare_invocation`, `build_tool_specs`, `build_tool_name_map`, `ApprovalPolicy` |
| `capcore-adapter-python` | Explicit local callable registration as `CapabilityAdapter` | Arbitrary code execution, directory scanning, approval, provider schemas | `PythonCapabilityAdapter`, `PythonCapabilitySpec`, `python_capability` |
| `capcore-adapter-mcp` | MCP stdio server discovery/call through MCP SDK, MCP tool to capcore descriptor conversion | Trust policy beyond explicit overrides, provider envelopes, host config UI | `McpStdioCapabilityAdapter`, `McpStdioServerConfig`, `McpToolOverride`, `McpToolRecord`, `McpSdkStdioClient` |
| `capcore-adapter-speech` | Speech capabilities: OpenAI-compatible ASR, TTS clients/adapters, Edge TTS/GPT-SoVITS-compatible helpers | Audio playback UI, profile storage, local model install/process management, remote SaaS secrets | `OpenAICompatTTSAdapter`, `OpenAICompatASRAdapter`, `GptSovitsTTSClient`, `EdgeTTSClient` |
| `capcore-adapter-comfyui` | Loopback ComfyUI workflow execution, upload/prompt/history/view, workflow slot mapping, output asset normalization | Job UI, artifact storage, ComfyUI process/model management, remote access policy | `ComfyUiCapabilityAdapter`, `ComfyUiWorkflowCapability`, manifest builder helpers |
| `capcore-provider-native-tools` | Shared provider-neutral runner and result feedback hygiene | OpenAI/Anthropic schema envelopes, provider parsing, SDK calls | `CapabilityRoute`, `run_native_tool_invocation`, `NativeToolRunResult`, JSON-safe/model-feedback helpers |
| `capcore-provider-openai` | OpenAI Chat Completions tool schema, safe name map, tool call parsing, streaming collection, `role="tool"` messages | OpenAI SDK calls, keys, model allowlists, approval persistence | `build_openai_chat_tool_set`, `parse_openai_chat_tool_calls`, `run_openai_chat_tool_invocation`, `build_openai_chat_tool_messages` |
| `capcore-provider-anthropic` | Anthropic Messages tool schema, safe name map, tool-use parsing, streaming collection, `tool_result` message blocks | Anthropic SDK calls, keys, model allowlists, approval persistence | `build_anthropic_messages_tool_set`, `parse_anthropic_messages_tool_uses`, `run_anthropic_messages_tool_invocation`, `build_anthropic_messages_tool_result_message` |
| `capcore-host-utils` | Host approval fingerprints/store, workspace path ids, JSON-safe approval previews | Provider calls, concrete tools, UI rendering, product policy | `WorkspaceScope`, `SQLiteApprovalStore`, `build_approval_fingerprint`, `preview_approval_arguments`, `build_approval_preview` |

## Standard Host Flow

For a normal AI product turn:

1. If the product receives OneBot events, normalize them with
   `channelcore-onebot` before mapping to host identity and context. Keep
   provider locators out of public summaries and let the host decide whether
   passive messages or attachments enter product flows.
2. Resolve host identity and context:
   - `tenant_id`, `user_id`, `domain_id`, `conversation_id`;
   - current client mode or surface, such as `desktop`, `web`, `qq_text`;
   - active character pack/profile, if any.
3. Record the incoming user turn with `memcore.MemorySystem.record_user_turn`.
4. Build memory context:
   - `build_prompt_context(current=cur)`;
   - `render_prompt_context(ctx)`;
   - expose `retrieve_for_turn(current=cur, ...)` and `read_timeline(...)` as
     model tools when the product wants chat-model-driven memory lookup.
5. Build character/resource context with `charpack-core` if the product has a
   character pack:
   - `build_persona_prompt_context(...)`;
   - stable identity/reference before history;
   - `manifest.build_character_catalog_prompt_context()` as the stable visual
     catalog when relevant;
   - `persona["resource_context"]` near the current turn for the active outfit
     and valid emotion ids;
   - normalize visual output after the model reply.
6. Assemble prompt sections with `promptpack-core`:
   - stable system/developer rules first;
   - stable persona/reference/resource catalog next;
   - append-only memory after the stable prefix;
   - current input, active resource state, retrieved snippets, current time,
     live state, and tool results at the tail.
7. Build model-native tool schemas from selected capcore capabilities:
   - OpenAI: `build_openai_chat_tool_set(...)`;
   - Anthropic: `build_anthropic_messages_tool_set(...)`;
   - keep the returned tool set for parsing the same model response.
8. Call the model in the host app. Provider packages do not call the model.
9. Parse any tool calls/tool uses using the same provider tool set.
10. Execute each parsed invocation through the provider runner:
   - parse error / unknown tool becomes structured feedback;
   - `capcore.prepare_invocation()` validates args and resolves permission;
   - host approval callback decides user confirmation when required;
   - adapter invokes only after validation and permission pass.
11. Feed provider-correct tool results back to the model:
    - OpenAI: `build_openai_chat_tool_messages(results)`;
    - Anthropic: `build_anthropic_messages_tool_result_message(results)`.
12. Parse/normalize final assistant output, update memcore metadata, record the
    assistant turn, and trigger background compaction.

For a fuller host blueprint covering turn policy, tool concurrency,
cache-friendly ordering, failure handling, observability, budgets, and when to
extract a shared agent loop, read `docs/ai_product_host_blueprint_v0.md`.

## Cache-Friendly Prompt Rules

The ecosystem is designed to work with explicit provider caches and
DeepSeek-like automatic prefix caches.

Keep stable prefix text byte-for-byte stable:

- stable system/persona/tool/output rules;
- stable memcore tool instructions and final JSON contract;
- long reference text that rarely changes.

Keep dynamic text after the stable prefix:

- current time and request/session ids;
- rendered visible memory;
- retrieved snippets and timeline reads;
- current user input;
- current visual/desktop state;
- tool results;
- per-turn debug flags and temporary task context.

Use `PromptAssembly.stable_prefix_hash` and
`PromptAssembly.cumulative_prefix_hashes` to diagnose churn. If hashes before
the first dynamic memory section change every turn, the host is wasting cache.

Do not use `PromptAssembler.assemble(stable_first=True)` for strictly
chronological assistant/tool history. Preserve provider message order yourself,
or call `assemble(stable_first=False)`.

## Capability And Tool Rules

Always preserve this order:

```text
capability discovery
  -> host selection/filtering
  -> provider-native schema build
  -> provider tool call parse
  -> capcore prepare_invocation
  -> host approval callback / approval store
  -> adapter.invoke
  -> provider-native result feedback
```

Do not bypass `prepare_invocation()` just because a tool call came from a
trusted model. The model is not the authority for local filesystem, shell,
network, media generation, TTS, ASR, ComfyUI, or MCP actions.

For multiple tool calls returned by a provider, the parser should return all of
them. The host decides whether to run them sequentially or concurrently. Current
provider packages intentionally avoid owning that orchestration policy.

## AI-Agent Read Order

When integrating one package into a host, read that package's `AGENTS.md`,
`README.md`, usage flow doc, example, and nearby tests first.

For cross-package work, read in this order:

1. This document.
2. `promptpack-core/AGENTS.md`
3. `memcore/AGENTS.md`
4. `charpack-core/AGENTS.md`
5. `capcore/AGENTS.md`
6. The concrete adapter `AGENTS.md` files you are using.
7. The provider package `AGENTS.md` files for the model providers you are using.
8. `capcore-host-utils/AGENTS.md` if the host persists approvals or exposes
   local filesystem/shell-like actions.

## Release-State Checklist

Every extracted package should have:

- `README.md` with minimal use, boundaries, and validation commands;
- `AGENTS.md` with AI-agent integration rules;
- `docs/usage_flow_*.md` for host wiring;
- `examples/` runnable without real secrets or remote services where possible;
- `MANIFEST.in` or equivalent package config so sdist includes docs/examples
  when those docs are part of the package value;
- focused tests for public API and edge cases;
- `uv run --extra dev python -m unittest discover -s tests -v`;
- `uv run --extra dev ruff check .`;
- `uv run --extra dev ruff format --check .`;
- package-specific smoke example;
- `uv run --extra dev python -m build`;
- `git diff --check` when the package is a git repo.

Akane host integration should additionally keep:

- exact internal release pins in `requirements-packages.txt`;
- a complete wheelhouse or explicitly configured package index;
- no editable, sibling, `file:../`, `link:../`, or absolute source dependency;
- a source-blind clean-environment install gate;
- a cross-package smoke command;
- a release-state metadata audit command:
  `.\.venv\Scripts\python.exe .\scripts\audit_extracted_packages_release.py`.

The host independence gate is:

```powershell
.\.venv\Scripts\python.exe .\scripts\build_extracted_package_wheelhouse.py
.\.venv\Scripts\python.exe .\scripts\verify_extracted_package_independence.py
.\.venv\Scripts\python.exe .\scripts\build_petdesk_package_artifacts.py
```

## Cross-Package Smoke

Akane keeps one provider-free, no-network smoke script:

```bash
.venv\Scripts\python.exe scripts\smoke_extracted_package_ecosystem.py
```

It verifies:

- `charpack-core` can scan a temporary character pack and normalize visual
  output;
- `memcore` can record and render prompt memory context;
- `promptpack-core` can compose stable/semi-stable/dynamic sections and produce
  cache/audit hashes;
- `capcore-adapter-python` can expose an explicit local callable;
- `capcore-adapter-mcp` can expose a fake MCP tool through the adapter protocol;
- `capcore-provider-openai` can build tools, parse multiple OpenAI tool calls,
  execute both capability sources, and build `role="tool"` feedback messages;
- `capcore-provider-anthropic` can do the same for Anthropic Messages
  `tool_use` and `tool_result` blocks.

The smoke intentionally does not call OpenAI, Anthropic, DeepSeek, MCP stdio,
ComfyUI, TTS, ASR, or any remote/local service.

Akane also keeps one no-network host-turn smoke script:

```bash
.\.venv\Scripts\python.exe .\scripts\smoke_ai_product_host_turn.py
```

It verifies the recommended product loop shape:

- `memcore` user-turn record and final assistant writeback;
- `promptpack-core` stable/dynamic prompt assembly and stable prefix hash;
- `charpack-core` persona/resource context and visual normalization;
- `capcore-adapter-python` capability registration;
- `capcore-provider-openai` schema, tool-call parse, invocation, and
  `role="tool"` feedback;
- final memcore JSON parse and metadata update.

## Release Audit

Akane also keeps one lightweight release-state audit script:

```bash
.\.venv\Scripts\python.exe .\scripts\audit_extracted_packages_release.py
```

It verifies the source release metadata without installing dependencies:

- all 12 extracted package source directories supplied to the maintainer audit exist;
- each package is a git repository with a clean worktree;
- each package has `README.md`, `AGENTS.md`, `LICENSE`, `MANIFEST.in`,
  `docs/`, `examples/`, `tests/`, and `pyproject.toml`;
- each package's `project.name` and `project.version` match the 0.1 package
  map;
- each `MANIFEST.in` includes AI/human integration docs, examples, and tests;
- Akane has exact runtime package pins and an artifact-only bootstrap contract;
- Python and petdesk package manifests contain no sibling source dependency;
- runtime launch helpers require an explicit runtime root rather than guessing
  a checkout location.

The audit inspects source trees because it is a maintainer release gate. Akane's
installed runtime does not discover or import those trees; that stronger claim
belongs to `verify_extracted_package_independence.py`.

Use `--allow-dirty` only while editing release metadata. A release-state pass
should run without it.

## Current Next Steps

Recommended next hardening tasks:

1. Keep the release audit and cross-package smoke green whenever an extracted
   package is added or a public package file moves.
2. Keep the cross-package smoke green whenever package public APIs change.
3. Avoid turning release audit into a large monorepo manager; package behavior
   still belongs to each package's own tests and smoke examples.
4. For product work, choose packages by product boundary:
   - coding assistant: `memcore + capcore + host-utils + provider packages`;
   - character companion: add `charpack-core + promptpack-core + speech`;
   - image workflow app: add `capcore-adapter-comfyui`;
   - MCP-heavy agent: add `capcore-adapter-mcp` and host approval UX.
5. For reusable desktop-pet runtime work, follow
   `docs/petdesk_runtime_architecture_v0.md`; do not copy Akane's current
   broad frontend files as a framework.
