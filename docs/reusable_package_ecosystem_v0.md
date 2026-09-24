# Akane reusable package ecosystem v0

This document maps the reusable packages extracted from Akane and shows the
recommended composition path for new AI products.

## Why This Exists

Akane is becoming lighter because reusable mechanisms are moving into independent
packages. The goal is not to split code for its own sake. The goal is to make
each reusable layer easier to test, publish, and reuse in future products.

The current extracted ecosystem can already support a small companion/coding
assistant style product:

```text
chat/product host
  |
  |-- channelcore-onebot        OneBot inbound contracts and normalization
  |-- charpack-core              character identity, persona, resources
  |-- memcore                    memory lifecycle and retrieval tools
  |-- capcore                    capability/tool contract
  |   |-- capcore-adapter-python local Python capabilities
  |   |-- capcore-adapter-mcp    MCP capabilities
  |   |-- capcore-adapter-speech TTS/ASR capabilities
  |   |-- capcore-adapter-comfyui image/workflow capabilities
  |   |-- capcore-provider-*     model-native tool envelopes
  |-- capcore-host-utils         workspace, approvals, JSON-safe host helpers
```

## Package Roles

| Package | Role | Host responsibilities |
| --- | --- | --- |
| `channelcore-onebot` | Host-neutral OneBot inbound contracts, event/message-segment normalization, self-id, freshness, atomic replay admission, group-trigger/follow decisions, and quoted-message lookup/scope validation. | Own webhook auth/status mapping, Bot transport binding, sessions, product commands, attachment materialization, vision/model/TTS, and delivery policy. |
| `memcore` | Domain-neutral layered memory: record turns, render visible memory, expose retrieval/timeline tools, compact memory. | Provide real LLM/embedding adapters, namespace policy, prompt assembly, final chat model call. |
| `capcore` | Capability descriptor, slot validation, invocation preparation, risk/effects/approval contract. | Decide which capabilities exist and how the user grants/denies them. |
| `capcore-host-utils` | Host safety utilities: workspace scope, stable display paths, approval persistence, preview redaction, JSON-safe values. | Build the actual UI/CLI approval flow and choose trust mode. |
| `capcore-adapter-python` | Wrap Python callables as capcore capabilities. | Own the callable implementation and side effects. |
| `capcore-adapter-mcp` | Expose MCP server tools as capcore capabilities. | Configure MCP servers, secrets, lifecycle, and discovery policy. |
| `capcore-adapter-speech` | TTS/ASR capability descriptors and clients for speech providers. | Own voice profiles, local endpoints, model files, audio delivery, and privacy. |
| `capcore-adapter-comfyui` | ComfyUI workflow runtime adapter: manifest, upload/prompt/history/view, output assets. | Own profiles, job queue/status, artifact delivery, UI, and user-facing routes. |
| `capcore-provider-native-tools` | Provider-neutral route/invocation/result wrapper for native tool calls. | Feed it parsed provider calls and return feedback to the model. |
| `capcore-provider-openai` | Convert capcore capabilities to OpenAI Chat Completions tools and run tool calls. | Own OpenAI-compatible client call loop and message history. |
| `capcore-provider-anthropic` | Convert capcore capabilities to Anthropic Messages tools and run tool calls. | Own Anthropic client call loop, streaming, and message history. |
| `charpack-core` | Host-neutral character pack runtime: identity, persona projection, resource manifest, context libraries, visual/emotion normalization. | Own pack import/edit UI, delivery, sessions, model calls, TTS, QQ/NapCat, care/shop effects. |

## Recommended New Product Integration Order

1. Add `channelcore-onebot` when the product receives OneBot/NapCat events.
   Map its neutral inbound objects into host sessions before product logic.
2. Start with `charpack-core` if the product has characters, personas, visual
   states, or creator-authored context packs.
3. Add `memcore` for long-running conversations. Use `MemorySystem` as the only
   facade and expose `retrieve_for_turn` plus `read_timeline` as chat-model
   tools.
4. Add `capcore` when the model needs tools. Register local functions with
   `capcore-adapter-python` first because it is the smallest adapter.
5. Add `capcore-provider-openai` or `capcore-provider-anthropic` to convert the
   capabilities into model-native tools.
6. Add `capcore-host-utils` when the host has filesystem/shell/workspace risks
   or wants persistent approvals.
7. Add MCP, speech, and ComfyUI adapters only when the product needs those
   external runtimes.

## 0.1 API Freeze Scope

Names that should remain stable through the 0.1 line:

- `channelcore-onebot`: `InboundMessage`, `InboundParseResult`,
  `ConversationRef`, `ActorRef`, `ReplyRef`, `AttachmentRef`,
  `normalize_inbound_event`.
- `memcore`: `MemorySystem`, `MemoryConfig`, `Namespace`, `Actor`,
  `SQLiteMemoryStore`, `InMemoryVectorIndex`, `HashedEmbeddingProvider`,
  `LLMClient`, `EmbeddingProvider`, `TokenCounter`, `build_chat_output_contract_prompt`,
  `parse_chat_output`.
- `capcore`: `CapabilityDescriptor`, `CapabilityIOSlot`,
  `CapabilityToolSpec`, `InvocationContext`, `ApprovalPolicy`,
  `prepare_invocation`, `build_tool_specs`, `build_tool_name_map`.
- `capcore-adapter-python`: `PythonCapabilityAdapter`,
  `PythonCapabilitySpec.from_callable`.
- `capcore-provider-native-tools`: `CapabilityRoute`,
  `route_capabilities_from_adapter`, `run_native_tool_invocation`,
  `NativeToolRunResult`.
- `capcore-provider-openai`: `build_openai_chat_tool_set`,
  `parse_openai_chat_tool_calls`, `run_openai_chat_tool_invocation`,
  `build_openai_chat_tool_messages`, `route_capabilities_from_adapter`.
- `capcore-provider-anthropic`: `build_anthropic_messages_tool_set`,
  `parse_anthropic_messages_tool_uses`, `run_anthropic_messages_tool_invocation`,
  `build_anthropic_messages_tool_result_message`.
- `capcore-host-utils`: `WorkspaceScope`, `SQLiteApprovalStore`,
  `StoredApprovalPolicy`, `build_approval_preview`, `preview_approval_arguments`,
  `json_safe_value`.
- `charpack-core`: `CharacterPackResourceService`,
  `DesktopPetCharacterResourceService`, `ResourceManifest`,
  `CharacterContextLibraryService`, `sanitize_character_pack_id`.

Compatibility aliases such as `DesktopPetCharacterResourceService` should not be
removed in 0.1 because Akane still uses the older names.

## Unified Prompt/Runtime Shape

A host turn should usually look like this:

```python
identity = character_packs.build_character_identity(pack_id)
persona = character_packs.build_persona_prompt_context(pack_id, client_mode=client_mode)
manifest = character_packs.get_manifest(pack_id)
stable_character_resources = manifest.build_character_catalog_prompt_context()

cur = mem.record_user_turn(user_text, timestamp=now_ts)
visible_memory = mem.render_prompt_context(mem.build_prompt_context(current=cur))

tools = build_provider_tools(capcore_capabilities)
result = call_chat_model(
    stable_system_prompt,
    persona["system_context"],
    persona["reference_context"],
    stable_character_resources,
    visible_memory,
    user_text,
    persona["resource_context"],
    tools,
)

# If the model calls tools:
# provider parse -> capcore route -> adapter.invoke -> provider tool result message.

visual = manifest.normalize_visual_output(model_visual_payload)
mem.record_assistant_turn(final_speech, in_reply_to=cur, timestamp=now_ts2)
mem.compact_due_background()
```

Keep stable product/tool rules, character identity/reference, and resource
catalog before append-only memory. Keep the active outfit/emotion resource
context near the current turn, then normalize the model output through the same
manifest. This preserves provider prefixes without hiding current resources.

## Cross-Package Smoke

The smoke lives outside Akane so it can validate the extracted stack without
importing Akane runtime:

```text
<workspace>/package-smokes/companion_stack_smoke
```

Run it with:

```bash
cd <workspace>/package-smokes/companion_stack_smoke
uv run python companion_stack_smoke.py
uv run --extra dev ruff check .
uv run --extra dev ruff format --check .
```

It proves:

- `charpack-core` reads a character pack and normalizes emotion output.
- `memcore` records prior and current turns and exposes retrievable memory.
- `capcore-adapter-python` wraps a local memory tool.
- `capcore-provider-openai` builds a model-native tool schema, maps model tool
  names back to capability ids, executes the adapter, and produces a tool
  result message.

It intentionally does not call OpenAI, Anthropic, MCP, TTS, or ComfyUI. Those
belong to package-specific smoke tests because network/model/local-runtime
availability would make this stack smoke flaky.

## What Remains In Akane For Now

Do not rush these out without another audit:

- generated file and attachment delivery;
- profile/session persistence;
- desktop/Tauri route contracts;
- QQ/NapCat event handling and delivery;
- product-specific care/shop state;
- job queues, runtime logs, and user-facing artifact URLs.

Good next extraction candidates:

- `artifact-core`: generated-file/attachment metadata and safe delivery
  primitives, if we can keep storage/routes out.
- `profile-core`: declarative profile loading and validation, if we can avoid
  Akane-specific session and UI assumptions.
- `promptpack-core`: reusable prompt assembly profiles, if it can stay provider
  and product neutral.
