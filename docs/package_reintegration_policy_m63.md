# Package Reintegration Policy M63

Status: policy and audit implemented.
Date: 2026-07-09

## Goal

Akane extracted several reusable packages after the public source-alpha line was
already opened. Some packages have been applied back into Akane. The risk is not
package extraction itself; the risk is applying a package back as a second
parallel implementation while the old Akane implementation keeps growing.

M63 sets the rule for future package reintegration:

```text
Package reintegration must reduce owning implementations.
```

In practical terms:

```text
Applying a package back to Akane is a replacement, not a permanent new path.
```

## Scope

In scope for this document:

- `promptpack-core`
- `charpack-core`
- `capcore`
- `capcore-adapter-*`
- `capcore-provider-*`
- `capcore-host-utils`
- `petcore-protocol`
- `petdesk-character-host`
- `petdesk-live2d-pixi-driver`
- `petdesk-runtime`

Out of scope for M63:

- `memcore`

`memcore` needs a separate public/private decision. M63 does not decide that
question and should not be used to force `memcore` into either public hard
dependency or private optional dependency status.

## Hard Rule

When a reusable package is applied back to Akane, the old Akane implementation
must end in exactly one of these states:

```text
deleted
thin adapter
documented migration window
```

Definitions:

- `deleted`: Akane no longer owns that implementation.
- `thin adapter`: Akane keeps only product-specific glue, config, routing,
  profile selection, UI/API shape, or compatibility re-export. The package owns
  the reusable logic.
- `documented migration window`: temporary coexistence with a written reason,
  one authority implementation, removal target, and tests proving new code does
  not keep expanding the old path.

Not allowed:

- old and new implementations both own business logic indefinitely;
- new package path is added while old callers continue to mutate old logic;
- two places validate the same schema or normalize the same protocol with
  different rules;
- docs say the package owns a behavior while Akane still contains a growing
  full implementation for that behavior;
- tests only check constants/imports and do not prove the intended path is used.

## Reintegration Gate

Before applying a package back to Akane, do this checklist:

1. Find the old implementation entry points with `rg`.
2. List all active callers and tests.
3. Pick one authority implementation.
4. Change old Akane code to `deleted`, `thin adapter`, or `documented migration window`.
5. Add or update tests that prove callers use the intended package-backed path.
6. Update docs with the old implementation status.
7. If a migration window is needed, document:
   - start milestone/commit;
   - why coexistence is needed;
   - old files/functions that must be deleted or collapsed;
   - validation command that keeps the window from expanding.

## Dependency Classes

Use these labels when deciding whether a package can be required by Akane:

```text
public-hard
  Package is public or intended to be public, installable by normal users, and
  can be a normal Akane runtime dependency.

optional-runtime
  Akane can use it when installed, but must start and degrade structurally
  without it.

dev-only
  Local smoke, release tooling, or acceptance helper. It must not become a
  public runtime requirement.

incubating
  Interface is not stable enough to replace old Akane behavior yet.
```

This label is separate from repository layout. A package can be an independent
repo and still be private, public, optional, or dev-only.

## Current Audit

### `promptpack-core`

Current Akane use:

- `companion_v01/prompt_blocks.py` imports `PromptBlock` and subclasses
  `PromptBlockRegistry`.
- prompt text remains Akane-owned product content.

Authority:

- `promptpack-core` owns prompt block/registry primitives and future generic
  assembly/cache-audit primitives.
- Akane owns actual persona wording, mode-specific rules, tool instructions,
  and product prompt content.

Old implementation status:

```text
thin adapter
```

The old Akane prompt block registry has effectively become an Akane content
registry layered on `promptpack-core`. That is acceptable.

Known risk:

- `PromptBuilder` still hand-assembles large final prompt strings. This is not
  automatically duplicate logic because Akane owns product prompt content, but
  any future attempt to use `PromptAssembler` must replace a specific assembly
  path rather than sit beside it forever.

Next cleanup target:

- M64 should audit `PromptBuilder.build_final_generation_context()` and decide
  whether any assembly mechanics should move to `promptpack-core` or remain
  explicitly Akane-owned.

### `charpack-core`

Current Akane use:

- `companion_v01/resource_manifest.py` re-exports `ResourceManifest`.
- `companion_v01/character_context_library.py` re-exports context library
  services.
- `companion_v01/desktop_pet_character_resources.py` imports character resource
  services and helpers.

Authority:

- `charpack-core` owns reusable character pack resources, manifest reading,
  context library projection, visual output normalization, and pack id
  sanitizing.
- Akane owns product routes, creator kit UI, user/session policy, TTS playback,
  QQ delivery, and concrete character assets.

Old implementation status:

```text
thin adapter
```

The old `ResourceManifest` implementation is no longer Akane-owned. Akane keeps
compatibility import paths so existing callers can continue using
`companion_v01.resource_manifest.ResourceManifest`.

LD009 cleanup:

- `desktop_pet_character_resources.py` no longer imports underscored helpers
  from `charpack-core`; it remains a compatibility re-export over public
  character resource services, constants, and pack-id sanitizing.

### `capcore`

Current Akane use:

- `companion_v01/capcore_runtime.py` uses capcore permission primitives.
- `companion_v01/local_capability_config.py` uses capcore approval and
  projection helpers.
- `companion_v01/tool_runtime.py` uses capcore schema validation and permission
  request builders.
- `companion_v01/capability_adapters/types.py`,
  `protocol.py`, and `manifest_loader.py` are compatibility re-export layers.

Authority:

- `capcore` owns capability descriptor types, schema projection, invocation
  argument validation, risk/confirm/effects policy, and permission
  request/decision primitives.
- Akane owns concrete tool handlers, product profile selection, approval UI
  state, local config files, and final execution orchestration.

Old implementation status:

```text
thin adapter with active host-owned orchestration
```

Akane still has a large `tool_runtime.py`, but that file owns product tools and
handler orchestration. It must not grow a second schema validator or permission
policy that duplicates capcore.

Known risk:

- Legacy JSON `tool_call` and provider-native tool calls coexist. That is a
  migration window for model invocation shape, not permission/schema ownership.
  The bridge must not become a second provider envelope implementation.

### `capcore-provider-openai`

Current Akane use:

- `companion_v01/native_tool_schema.py` delegates OpenAI tool schema envelope
  generation to `build_openai_chat_tool_set`.
- `companion_v01/llm_runtime.py` delegates OpenAI non-streaming and streaming
  tool-call parsing to provider package parsers.

Authority:

- `capcore-provider-openai` owns OpenAI Chat Completions native tool schema
  envelope and tool-call parsing.
- Akane owns model selection, provider allowlist, forced JSON policy, and
  mapping parsed native invocations back into the existing internal tool shape.

Old implementation status:

```text
documented migration window
```

The old handwritten `tool_call` JSON path remains for providers/modes that
cannot use native tools yet. It is allowed only as a compatibility path. New
OpenAI-native envelope or parser logic should go into `capcore-provider-openai`,
not Akane.

### `capcore-provider-native-tools`

Current Akane use:

- Runtime dependency through provider package ecosystem and release audit.
- No broad direct Akane import in the backend path.

Authority:

- `capcore-provider-native-tools` owns reusable provider-neutral invocation
  hygiene.
- Akane owns whether to use that runner in a given provider flow.

Old implementation status:

```text
dev/ecosystem support
```

Do not add another Akane-owned generic native-tool runner if this package
already covers the use case.

### `capcore-provider-anthropic`

Current Akane use:

- Ecosystem package and smoke coverage.
- Not a current Akane runtime hard dependency.

Authority:

- package owns Anthropic tool schema and tool-use parsing.

Old implementation status:

```text
dev-only / public-later
```

If Akane later adds Anthropic native tools, it should use this package directly
instead of copying the OpenAI adapter pattern into Akane.

### `capcore-host-utils`

Current Akane use:

- Ecosystem package and release audit.
- Not a current Akane runtime hard dependency.

Authority:

- package owns generic workspace/approval utility patterns.
- Akane owns its concrete approval UI and product policy.

Old implementation status:

```text
public-later
```

Future adoption must replace specific Akane helper code or remain out of the
runtime path.

### `capcore-adapter-python`

Current Akane use:

- `companion_v01/capability_adapters/python_local.py` subclasses
  `PythonCapabilityAdapter` and defines Akane-specific callable specs.

Authority:

- package owns local callable adapter invocation and descriptor projection.
- Akane owns which local functions are exposed and the concrete function bodies.

Old implementation status:

```text
thin adapter
```

This is the desired pattern.

### `capcore-adapter-mcp`

Current Akane use:

- `companion_v01/capability_adapters/mcp_stdio.py` wraps
  `McpStdioCapabilityAdapter`.
- Akane keeps existing MCP argv/env hydration and profile config through
  `_AkaneMcpClient`.

Authority:

- package owns MCP tool-to-capcore descriptor conversion, capability id
  handling, and JSON-safe invocation arguments.
- Akane owns server config, low-risk allowlist, approval mode, and existing
  process-calling policy.

Old implementation status:

```text
thin adapter with host-owned client bridge
```

The host-owned client bridge is acceptable because it binds Akane config and
process policy. Do not reimplement package-owned descriptor/invocation
normalization in Akane.

### `capcore-adapter-speech`

Current Akane use:

- `openai_compat_tts.py` and `openai_compat_asr.py` re-export package adapters.
- voice and petdesk routes invoke those adapters when configured.

Authority:

- package owns reusable OpenAI-compatible ASR/TTS adapter behavior.
- Akane owns provider profile storage, route responses, playback/audio delivery,
  and fallback policy.

Old implementation status:

```text
thin adapter / partial migration
```

Known risk:

- Akane still has older TTS service paths and route-level fallback behavior.
  That can be valid product policy, but any reusable speech client logic that
  overlaps package behavior should be collapsed into `capcore-adapter-speech`
  before more providers are added.

### `capcore-adapter-comfyui`

Current Akane use:

- `capability_adapters/comfyui.py` re-exports package adapter/API.
- `local_workflow_execution.py` imports package execution dataclasses and
  normalizers.
- `local_workflow_runners/comfyui.py` runs configured Akane workflows through
  `ComfyUiCapabilityAdapter`.

Authority:

- package owns ComfyUI workflow invocation, slot mapping, upload/prompt/history
  handling, and output asset normalization.
- Akane owns workflow profile config, product routes, artifact storage, and UI.

Old implementation status:

```text
thin adapter
```

Do not grow another local workflow execution engine inside Akane when the
package API can be extended instead.

## Petdesk Package Audit

### `petcore-protocol`

Current Akane use:

- Contract source for petdesk runtime events and resource/display shapes.
- Akane Python backend does not import it directly.

Authority:

- package owns TypeScript protocol contracts for petdesk runtimes and hosts.
- Akane owns `/pet/*` bridge payload construction until a Python projection is
  deliberately introduced.

Old implementation status:

```text
contract package; no Python hard dependency
```

If Akane adds Python protocol helpers later, they should be generated or
implemented as a package-owned projection, not copied ad hoc in the backend.

### `petdesk-character-host`

Current Akane use:

- Character host package for TS/Node bridge and hot-reload workflows.
- Akane backend does not embed it.

Authority:

- package owns generic character pack scanning/projection for TS hosts.
- Akane owns its existing Python character resource service and public routes.

Old implementation status:

```text
dev/release ecosystem support
```

Do not embed Node host behavior into the Python backend just to avoid writing a
proper bridge.

### `petdesk-runtime`

Current Akane use:

- sibling Tauri/WebView2 runtime launched by Akane starter scripts;
- consumed through `/pet/health`, `/pet/resource-manifest`, `/pet/snapshot`,
  `/pet/turn`, and audio resource routes.

Authority:

- `petdesk-runtime` owns desktop rendering, resource resolver, renderer manager,
  audio queue, native hit regions, and runtime window behavior.
- Akane owns backend turn generation, TTS synthesis, character resources,
  session policy, and compatibility bridge output.

Old implementation status:

```text
side-by-side replacement path
```

The old Electron/desktop pet remains frozen for existing user entry points, but
new desktop runtime behavior should go into `petdesk-runtime`. Akane `/pet/*`
must remain a bridge, not a second runtime.

### `petdesk-live2d-pixi-driver`

Current Akane use:

- runtime-side Live2D driver package.
- Not a Python backend dependency.

Authority:

- package owns Pixi/Cubism driver adapter behavior.
- Akane owns character pack selection and product acceptance.

Old implementation status:

```text
runtime plugin; Live2D Productization L1 pending
```

Live2D product work should extend this runtime-side package/contract rather
than add Live2D rendering logic to the Python backend.

## Preferred Next Work

M64 should be a concrete cleanup slice, not another broad policy pass. The best
candidate is:

```text
promptpack-core reintegration cleanup
```

M64 should inspect `PromptBuilder` and `prompt_blocks.py`, then decide whether
there is any duplicated prompt assembly ownership. If there is, collapse it. If
not, document that `PromptBuilder` is Akane product composition and
`promptpack-core` remains the generic block/assembly primitive.

## Validation

M63 is document/policy-only. Validation should include:

```powershell
python -m unittest tests.test_package_reintegration_policy -v
python -m ruff check tests\test_package_reintegration_policy.py
python -m ruff format --check tests\test_package_reintegration_policy.py
python -m py_compile tests\test_package_reintegration_policy.py
git diff --check -- AGENTS.md README.md docs\package_reintegration_policy_m63.md tests\test_package_reintegration_policy.py
```

No runtime, backend, QQ bot, petdesk window, package build, or package test
process needs to be started for this phase.
