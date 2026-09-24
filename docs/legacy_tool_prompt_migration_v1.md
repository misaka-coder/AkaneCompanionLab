# Legacy Tool Prompt Migration V1

## Outcome

Akane keeps the legacy final-JSON `tool_call` protocol for providers that have
not passed native-tool acceptance, but it is no longer allowed to own a second
tool description/schema authority.

The first migration batch is:

- `web_search`;
- `retrieve_memory`;
- `read_memory_timeline`;
- `browse_memory`;
- `open_memory`;
- `onebot_action`.

Their handlers now inherit one host-owned compatibility renderer. The renderer
accepts only a canonical CapCore `CapabilityToolSpec`, copies its description,
and serializes its complete input JSON Schema. P0b-3 replaces the earlier
compact field signature: that signature lost descriptions, enum members,
references and compound constraints, so it could not serve as a callable
contract. The renderer does not derive semantics from handler metadata,
normalizers, runtime state or availability.

Built-in tools keep their existing flat `tool_call` parameters. Adapter/plugin
tools put business parameters in `tool_call.arguments`, with only the tool
selector in the outer `type`. Business fields named `type`, `arguments`, or
`_tool_*` therefore retain their JSON meaning in the compatibility lane too.

## Authority boundary

```text
CapCore CapabilityToolSpec
  ├─ capcore-provider-openai -> provider-native tool schema
  └─ Akane legacy renderer  -> final-JSON tool_call compatibility hint
```

CapCore deliberately does not own natural-language prompt formatting. The
legacy renderer remains in Akane because `tool_call` is an Akane final-output
protocol. Adding this renderer does not create a coordinator or a new tool
registry.

The old per-handler `build_prompt_instruction()` prose for this batch is
deleted. `BaseToolHandler.build_prompt_instruction()` is now the thin default;
these six handlers were the initial batch. Adapter/plugin handlers now use the
same renderer with an explicit argument envelope; the former adapter-specific
12-field/80-character summarizer has been deleted. Other families retain their
existing prompt methods until their semantics have been audited and moved into
their ToolSpecs.

## Audit findings

- The four MemCore descriptions already carry their retrieval/opening
  semantics. The removed “do not announce in speech first” suffix duplicates
  the stable global rule that quick memory reads may be called silently.
- The old web-search prose contained useful multi-source, time-range, cursor,
  and browser-operation boundaries not fully present in its ToolSpec. Those
  semantics were added to `WEB_SEARCH_TOOL_SPEC`, so native and legacy models
  receive them together. The same audit found that `action` was marked required
  even though both the description and executor support cursor-only
  continuation; schema version 2 removes that contradiction while the existing
  normalizer still rejects an empty call.
- The old OneBot prose was shorter than its canonical contract and did not
  expose the exact params/result-truth boundary. That wording now lives in
  `ONEBOT_ACTION_TOOL_SPEC`. Every declared action enum member now appears in
  the legacy contract; `capabilities` remains the route for discovering the
  selected action's additional parameter details.
- The provider package's conservative 900-character description default was
  truncating the canonical `retrieve_memory` description on the native path.
  Akane now requests a limit large enough for the exact stable ToolSpec text;
  it does not invent or append native-only prose.

## Prompt size and cache boundary

Complete contracts cost more context than the previous compact signatures.
The old 6,800-character test budget has been removed because it enforced
information loss. Tool selection controls which contracts are exposed; a
selected contract is not silently shortened. Schema content appears once per
legacy tool, without an additional partial signature or guessed example values.

For native-capable requests, these legacy lines remain excluded exactly as
before. The legacy prompt content changes its existing cache identity. There is no per-turn dynamic
text, timestamp, capability status, session id, result, or memory content in
the renderer, so repeated identical selections remain byte-stable. Rejected
calls carry complete canonical schema and structured validation errors; they
do not reinterpret top-level `properties` as an exhaustive field allowlist
when references or additional properties can define a different contract.

## Next migration rule

Do not migrate the remaining tools mechanically. For each family:

1. compare the hand-authored prompt with its ToolSpec description/schema;
2. move only genuine stable semantics into the ToolSpec;
3. delete the handler prose in the same slice;
4. verify both native and legacy projections plus prompt size;
5. keep product routing, availability and transient status outside ToolSpec.
