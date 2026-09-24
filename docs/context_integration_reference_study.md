# MemCore Context Integration Reference Study

Status: Phase 0 research output for the Context Contract V1 work.

The study is based on the checked-out reference sources in `F:\Temp\memcore-reference-study`,
the read-only DeepSeek Harness tree, the read-only OpenCode tree, and the read-only
OpenHands tree. Letta's checked-out repository is now only a landing page; its own
`AGENTS.md` says that the retired Python server is not evidence for current behavior.

## Decision Summary

MemCore should expose an OpenAI-Agents-style session wrapper around one authoritative
context surface. The wrapper owns timeline entry creation, provider projection, open-turn
preservation, terminal settlement, and recovery. Hosts keep persona/system content,
actual tool execution, credentials, UI, and the agent loop.

The public integration must have a typed surface result and a provider-neutral normalized
event boundary. Provider-shaped context rendering belongs in MemCore; the host retains its
SDK transport serializer. A separate conformance entry point must compare a real host
transport capture with the ContextSurface, not only inspect rows in SQLite.

## Reference Findings

### Mem0

Mem0's narrow entry is `Memory.add(messages, ...)` plus `Memory.search(query, ...)`.
`add` accepts a message or a list of role/content messages and can infer facts; `search`
returns ranked memory records under explicit filters. This is useful for a retrieval API
and namespace discipline.

It does not own the current request, provider-native tool-call/result blocks, an open tool
round, final model input, or replay after a failed compaction. MemCore therefore adopts the
small, discoverable call shape but rejects the idea that retrieval is a substitute for
complete context ownership.

### OpenAI Agents SDK

The `Session` protocol requires `get_items`, `add_items`, `pop_item`, and `clear_session`.
The compaction session is a decorator: it wraps an existing session, snapshots history,
replaces it after compaction, and restores the previous history when replacement fails or
is cancelled. This directly answers the minimum-host-method and failure-isolation
questions.

MemCore adopts the wrapper/decorator shape and transactional replacement behavior. It does
not adopt a provider-only compaction endpoint as the authority because MemCore must also
support Anthropic and DeepSeek, preserve open tool rounds, and settle reloadable operation
cards with byte-exact `open_memory`.

### LangGraph

`BaseCheckpointSaver` separates checkpoint reads/writes from pending writes. The contract
has independent `get_tuple`, `list`, `put`, and `put_writes` operations, and the returned
checkpoint tuple carries pending writes separately from the durable checkpoint.

MemCore adopts the separation of durable state, pending work, and an independent
conformance surface. It rejects making a checkpoint store the final provider message
sequence: a checkpoint is persistence state, while the provider wire is an explicit
projection that must be captured and hashed.

### OpenCode

OpenCode keeps a durable message/event timeline and treats UI/runtime transformations as
projections over that timeline. Its session/message APIs page and reconcile events while
preserving message identity and tool parts. This supports deterministic history replay and
avoids treating a UI transcript as the model contract.

MemCore adopts the durable-event-to-provider-projection separation and stable source IDs.
It rejects copying OpenCode's product-specific message-part and UI pagination model into
the core contract.

### DeepSeek Harness

The Harness documentation makes `session/event` the durable fact stream and
`agent/pre-step` the hook that decides what the model sees. Tool schemas are registered
through the tool registry, while system sections are independent prompt contributions.
This gives MemCore the correct integration points without changing the agent loop.

MemCore adopts the event subscription, pre-step replacement, and tool-registry pattern.
The plugin must normalize events and delegate rendering/settlement to MemCore. It rejects
Harness-private time prefixes, private settlement thresholds, and a plugin-owned second
card/projection implementation.

### Letta

The checked-out Letta repository explicitly says it is a landing page and that the old
Python server is retired. The current integration direction is an App Server plus Agent
SDK. The useful architectural lesson is that a stateful agent service needs a stable
external boundary, but the retired implementation is not a valid source for a MemCore
adapter.

MemCore adopts the separation between an agent runtime and a remotely or locally owned
state service. It rejects using the archived server or old Python package as a compatibility
target.

### OpenHands

The read-only OpenHands tree uses a durable event path with replay, pagination, and
explicit recovery/error states. Its test guidance treats event identity, pagination, and
failure visibility as contracts. This is relevant to bridge restart and partial-history
handling.

MemCore adopts explicit event identity, structured failure, and restart-aware recovery. It
rejects importing OpenHands' product UI, workspace, and backend-selection concerns into a
context component.

## Required Answers

1. **Minimum user code:** construct the wrapper with a real `MemorySystem`, bind
   `session.input_callback` through the runner's request-boundary configuration, then run
   normally. A Session wrapper by itself cannot see the current input early enough to own
   the first provider request and must not be advertised as authoritative integration.
2. **Host methods:** the wrapped Session implements the normal `get_items`, `add_items`,
   `pop_item`, and `clear_session` protocol. Hosts do not implement timeline, settlement,
   projection, or serializers. The original Session remains the complete-history recovery
   source when MemCore cannot reproduce an item shape safely.
3. **Final model input owner:** MemCore owns the model-visible message sequence inside its
   scope. The host still owns system/persona/tool policy content and the provider call.
4. **No tool rerun:** tool calls and results are persisted as paired immutable events with
   correlation IDs. Rebuilding a surface only replays frozen messages or a reload card; it
   never invokes the host tool executor.
5. **Compaction failure:** it is isolated from the agent loop. The completed reply remains
   committed and the surface falls back to full history with a structured diagnostic.
6. **Plugin removal:** the host remains runnable because the original session/history is
   preserved and the wrapper is an optional projection layer. No MemCore failure may
   replace it with an empty history.
7. **Third-party verification:** `validate_context_adapter(adapter)` proves provider-shape
   conformance only. The fixed fixtures are expected ContextSurface examples, not transport
   captures. A host can claim authoritative integration only after capturing the exact
   conversation segment at its final provider transport boundary and passing it to
   `validate_provider_wire_capture(surface, captured_context_messages)`.
8. **Hidden complexity:** wrappers hide timeline entry construction, timestamp/event
   formatting, provider-shaped tool replay, settlement planning, projection hashes,
   recovery, and `open_memory` card generation/dispatch. The host still performs the final
   SDK request serialization.

## Adopted vs Rejected

Adopted: session decorators, host lifecycle hooks, durable event identity, pending-work
separation, provider-shaped context rendering in one package, transactional surface
replacement, and independent conformance tests.

Rejected: retrieval-only integration, provider-specific compaction as the core authority,
checkpoint rows as a replacement for provider wire, private host time prefixes, host-owned
copies of MemCore's context renderer, and UI/runtime models leaking into the contract.
