# Three-Core Minimal Host V1

## Outcome

The runnable reference at `examples/three_core_minimal_host.py` proves that a
host can compose MemCore, CapCore, and channelcore-onebot through public APIs
without importing `companion_v01`.

The demonstration is deliberately offline. A deterministic chat-model stand-in
emits one OpenAI-compatible native tool call; the Python capability adapter,
provider envelope, memory timeline, settlement, reload, and OneBot outbound
plan are real package executions. `HashedEmbeddingProvider` is explicitly
reported as demo-only and must be replaced in production.

## Frozen lifecycle

```text
OneBot event
  -> channelcore-onebot normalize_inbound_event()
  -> host maps ConversationRef/ActorRef to Namespace/Actor
  -> MemCore begin_turn() and openai_chat context projection
  -> stable CapCore tools + stable MemCore native memory tools
  -> CapCore parses, validates, authorizes, and invokes one capability
  -> host appends exact model-visible action/result to the open MemCore turn
  -> MemCore complete_turn() and compact_after_terminal settlement
  -> open_memory(source_id) restores the exact stored result
  -> channelcore-onebot builds one reply-compatible outbound action
```

## Authority boundaries

| Concern | Authority |
|---|---|
| Ordered inbound QQ segments, actors, mentions, attachments, protocol actions | channelcore-onebot |
| Capability schema, provider-safe names, argument validation, approval, adapter result | CapCore family |
| Timeline truth, context projection, tool correlation, settlement, reload | MemCore |
| Credentials, model API call, namespace mapping, approval UI, file authorization, transport | host |

The host does not recreate segment parsing, memory schemas, tool validation, or
provider history rendering. It only binds product identity and real external
runtime dependencies.

## Cache and result custody

The stable system instruction and composed tool schemas are hashed separately
from dynamic context. Tool order and schema bytes do not change between model
rounds. The exact CapCore-generated `role=tool` content is written once as the
MemCore observation and is byte-equal to the open-turn provider projection.

After final, a sufficiently large observation becomes a
`[compact_reloadable]` card. Its SQLite truth remains intact, and the same
`open_memory(memory_id=source_id, view=content)` contract restores the original
model-visible result. No receipt or second summary replaces that body.

## Real remaining integration friction

The public APIs are sufficient, so V1 adds no coordinator package. A host still
has five legitimate bindings:

1. map platform conversation/user identities to MemCore Namespace and Actor;
2. supply the final chat model and production embedding/summary providers;
3. merge CapCore provider tools with MemCore native memory tools while rejecting
   name collisions;
4. route CapCore calls through its provider runner and MemCore calls through its
   native dispatcher;
5. bind channelcore outbound plans to an authenticated OneBot transport.

The only reusable friction candidate is item 3/4: a provider-neutral tool router
between CapCore and MemCore. Do not extract it from this first reference alone.
After a second independent host proves the same shape, extract the smallest
bridge package or public helper and keep MemCore, CapCore, and channelcore
independent.

## Akane as the second host

The production vertical regression at
`tests/test_three_core_production_slice.py` applies the same contract to
Akane's real QQ host seam:

```text
channelcore inbound normalization
  -> CapCore Python adapter + OpenAI-native schema
  -> Akane product orchestration
  -> MemCore open-turn action/result projection
  -> terminal settlement + open_memory reload
  -> channelcore reply plan + logical result normalization
```

No fourth coordinator was added. The remaining Akane code is product binding:
profile/session ids, permission configuration, tool selection, runtime calls,
and authenticated OneBot transport.

The second-host comparison also showed that no shared coordinator/helper was
needed for native schema projection. Akane's old fallback that inferred a new
native schema from legacy handler metadata or prompt prose has been removed.
Every production handler now has to provide a canonical CapCore `ToolSpec`, and
the OpenAI provider package alone serializes that contract.

This second-host pass also removes an old trace-storage mismatch: executable
path arguments are no longer dropped by field name before MemCore projection.
Credential-shaped arguments remain filtered. Private transport/cache/database
locations must stay outside model-call arguments; a path deliberately supplied
to a tool remains executable and byte-stable across the provider continuation.

## Validation contract

The focused test requires:

- no `companion_v01` import;
- a real group mention with preserved Actor attribution;
- exact OpenAI action/result wire before final;
- one correlated MemCore action and observation;
- settled card plus exact `open_memory` recovery;
- byte-deterministic stable system/tool prefix;
- one channelcore OneBot group action containing exactly one reply segment.
