# Akane Capability Fabric M66

Status: **M66-A through M66-D implemented / M66-E and M66-F in progress / M66-G pending**

Branch audited: `feature/qq-finance-assistant-emquant`

Repository: `AkaneCompanionLab`

Discovery date: 2026-07-16

Original scope: read-only architecture discovery and an implementation-ready design. The same branch subsequently implemented M66-A through the bounded desktop ArtifactBroker slice in M66-D. A 2026-07-16 repair audit rejected the earlier claim that M66-E/F were complete; their honest remaining boundaries are recorded below.

## 0. Implementation closeout — M66-A

Implemented on 2026-07-16:

- capcore `CapabilityToolSpec` now carries versioned input/output schema identity, execution class, idempotency class, material requirements, and result-size metadata. `build_schema_hash()` provides the stable provider-neutral wire fingerprint.
- `OPEN_BROWSER_TOOL_SPEC` is the only `open_browser` semantic/schema authority. Native schema and the temporary legacy handler projection both derive from it; the old `TOOL_METADATA_BY_TYPE` semantic row is gone.
- Akane resolves a leased, instance-bound offer once per generation round and freezes its private execution receipt into the selected invocation. Desktop and QQ omit `open_browser` while no compatible offer is active.
- `DesktopSatelliteService` provides the authenticated outbound control session, heartbeat/lease handling, safe authenticated diagnostics, and broker dispatch. `ExecutorBroker` rechecks instance, lease epoch, offer ID, spec version, schema version, and schema hash immediately before dispatch.
- Tauri verifies the exact `/health.instance_id`, connects by WSS for remote backends (loopback `ws://` remains local-default compatibility), advertises only the reviewed spec/hash, validates the public URL again immediately before the OS action, and acknowledges `accepted` / `running` / terminal `result`. Its in-process invocation ledger prevents the same ID from opening twice across reconnects.
- `AKANE_DESKTOP_SATELLITE_TOKEN` is a separate deployment-managed secret. M66-A uses one pre-provisioned credential per instance; it is never reused as `AKANE_ADMIN_TOKEN`, stored in localStorage, projected into prompts, or returned by diagnostics/health. Challenge-derived short-lived device sessions remain a later security-hardening phase, not a second M66-A runtime path.
- `start_akane_next.ps1`, the Windows bootstrap, and the direct next launcher support explicit `-CloudSatellite -BackendUrl`. Named cloud mode requires an exact remote health match, a satellite token, and HTTPS away from loopback; its code path cannot start, reuse, or stop a local backend. Local-default generates one process-lifetime token shared by the backend and Tauri when it starts both.
- The dormant host `CapabilityAdapterRegistry` boot scan, empty built-in manifest directory, and reload route were deleted. `OpenBrowserToolHandler.execute()` no longer emits fire-and-forget `browser_open_requested`; that event remains only for unmigrated `open_music_search` and `browser_page.open_for_user` callers.

Verification covers authenticated registration, A/B instance rejection, schema/version/hash mismatch, online/offline desktop and QQ projection, frozen receipt privacy, lease expiry, pre/post-ACK disconnect semantics, duplicate invocation, unsafe URL rejection before OS open, minimal public `/health`, launcher isolation, local-default compatibility, Rust/Tauri tests, frontend build, and the full Python suite. No production backend, QQ account, credential, or server was touched during implementation.

### 0.1 Implementation closeout — M66-B through M66-D

Implemented and regression-covered:

- Every built-in model tool projects native schema, risk, confirmation, and validation metadata from its canonical `CapabilityToolSpec`. The legacy `TOOL_METADATA_BY_TYPE` table is now only a compatibility projection for family/budget/operation fields. The provider probe also projects `WEB_SEARCH_TOOL_SPEC` directly instead of restoring a second web-search schema function.
- A selected round freezes the actual handler objects together with the selection. Normalize, validate, budget resolution, sequential execution, and parallel execution consume that same frozen selection even if the live handler map changes before dispatch.
- `ServerLocalOfferIndex` owns built-in readiness. It synchronizes the current handler map before selection, treats handlers without a probe as static in-process offers, fails closed for unknown/probe-failed tools, and partitions its TTL cache by tool/profile/session/client mode. Domain-profile additions go through the same gate.
- `browser_page` is a real server-local offer only when its own `ManagedBrowserPageRunner` passes readiness. An `open_browser` satellite lease no longer falsely advertises `browser_page`.
- Desktop workspace/audio/generated transfers now pass through the instance-owned `ArtifactBroker`. The server rejects paths outside the instance data root and emits instance/size/SHA-256/MIME transfer metadata; Tauri checks all four before publishing an atomic instance-root staging copy.
- Tauri file imports upload bytes with bounded file/count/total limits and preserve profile/session identity. Server upload, audio, workspace-item, character-import, and export staging live below the instance data root. The raw-path `/desktop-pet/workspace/import-local` and workspace `/location` contracts are deleted.
- Existing attachment handles with `arc_`/`aud_`/`doc_`/`img_`/`unk_`/`vid_` prefixes remain a thin input translator until M66-G cleanup. They resolve only through the instance-owned stores and broker; they never re-enable raw paths. Remove this translator after all persisted pre-M66-D attachment records have either aged out or been migrated.

### 0.2 Repair state — M66-E and M66-F remain open

The repair pass completed shared foundations but does **not** close these phases:

- All frontstage and worker Python handlers now dispatch through `ExecutorBroker`; duplicate invocation IDs are idempotent and a broker exception returns structured `execution_unknown` without direct-handler fallback.
- Bound control-center workflows also execute through the broker. A missing runner returns structured unavailable state and no longer creates a fake `queued-but-inert` job.
- MCP prompt exposure requires a real `initialize` + `tools/list` probe with a bounded lease, rather than enabled/configured flags. Plugin exposure checks the live PluginHost state and registered capability ID. Missing liveness fails closed.
- Worker tool descriptions come only from canonical ToolSpecs, and worker readiness uses the same instance/profile-scoped offer semantics.
- The first M66-E short-task Satellite slice now has real Tauri executors for `desktop_context_snapshot`, `system_media_snapshot`, and `system_media_control`, in addition to `open_browser`. They are schema/lease gated and disappear while the PC is offline; see `desktop_satellite_local_capabilities_v1.md`.

M66-E still needs the remaining managed-browser, media-processing, vision, voice, and workflow executors plus durable broker long-job cancellation/uncertainty semantics. M66-F still needs plugin/workflow output bytes to converge fully on ArtifactBroker records and deletion of the remaining route-owned workflow job store. Those are real implementation tasks, not model-visible placeholders. M66-G starts only after both close.

## 1. Decision summary

M66 should not add another capability registry beside the existing ones. The recommended design is:

1. Evolve capcore's existing `CapabilityDescriptor` / `CapabilityToolSpec` into the single semantic `ToolSpec` authority. A provider may offer an implementation of a known `tool_id`; it may not invent the model-facing description, schema, risk, or effects at runtime.
2. Replace, rather than wrap indefinitely, `CapabilityRegistry.select()` plus `ToolReadinessGate.filter_handlers()` with one instance-owned `ToolResolver`. It resolves canonical specs against turn eligibility, live `CapabilityOffer` leases, policy, and version compatibility, and freezes one immutable per-round result.
3. Route every execution source—native tool calls, legacy JSON `tool_call`, internal Python handlers, plugins, MCP, workflow runners, and background workers—through one `ExecutorBroker`. Provider adapters remain concrete adapters, not alternate authorities.
4. Put all file/audio/generated material behind one `ArtifactBroker`. The model uses only opaque `file_`, `audio_`, and `gen_` handles. Absolute paths, provider endpoints, device names, ports, and raw exceptions never enter prompt text, ordinary logs, or model-visible results.
5. Make the PC a `Desktop Satellite`: an outbound-only, instance-bound executor/rendering client. It is not a second Akane backend, owns no formal Memcore/persona/Care/session/QQ state, and has device credentials separate from `AKANE_ADMIN_TOKEN`.
6. Use WSS only for the small bidirectional control plane and HTTPS for artifact bytes. MCP remains a provider-side adapter protocol, not the cloud-to-PC transport.

The first real vertical slice should be `open_browser`. It already has a real cloud-decision-to-desktop-effect shape, requires no artifact transport, and exposes the current false assumption most cleanly: desktop mode does not prove that an executor is online. M66-A must make `open_browser` appear in either desktop-pet or QQ model schemas only while the bound PC offer is active, and must execute it through the broker over the outbound satellite connection.

## 2. Non-negotiable invariants

- Cloud `personal` is the only formal personal memory, persona, Care, session, conversation, and QQ authority.
- A Desktop Satellite is never a personal backend and never starts or mirrors a formal Memcore.
- `finance` remains a separate process/root/QQ/secrets/device-enrollment/offer domain. An executor enrolled to instance A cannot register with or serve instance B.
- No design may require two formal personal backends. In cloud-personal satellite mode, `start_akane_next.ps1` must not start a local personal backend.
- The desktop pet remains locally rendered. Renderer/input capability declarations are separate from executor offers.
- No duplicate model tools such as `local_cover_song` / `cloud_cover_song`. Provider changes do not change `tool_id` or the active schema version.
- PC online/offline state is ephemeral operational state and must not be written to long-term memory.
- `AKANE_ADMIN_TOKEN` is not a device credential.
- `/health` remains exactly the three public fields `status`, `instance_id`, and `root_binding`; capability/device state belongs on an authenticated diagnostics surface.
- No fake success, future-only runtime entry, demo connection, or inert model-visible placeholder.
- Every new unified entry must delete or make thin an old authority path in the same migration slice. Rollback is by reverting a bounded release, not by retaining two live implementations indefinitely.
- `local-default` remains a supported compatibility deployment, but it must converge on the same resolver/broker/artifact contracts rather than grow permanent special semantics.

## 3. Discovery basis

This design was produced from the code, not inferred from the task description. The pass included the required root `AGENTS.md`, the M65 instance-isolation design, the M63 package reintegration policy, the M32 petdesk bridge, all documents under `docs/desktop_pet_character_workshop_v1/`, and targeted `rg` tracing of the runtime paths below.

The starting worktree contained only the user's untracked `.claude/` directory. It was not read, changed, removed, or staged. No online service was contacted, restarted, or changed.

Key implementation areas inspected:

- Model/tool path: `companion_v01/engine.py`, `engine_services/response_builder.py`, `engine_services/tool_rounds.py`, `tool_orchestration_engine.py`, `native_tool_schema.py`, `tool_invocation.py`, `tool_runtime.py`.
- Selection/readiness/catalog: `capability_registry.py`, `tool_readiness.py`, `local_capability_catalog.py`, `local_capability_config.py`, `domain_profiles.py`, `capcore_runtime.py`.
- Adapters/extensions: `capability_adapters/`, `plugin_host.py`, `plugin_tool_bridge.py`, `task_worker.py`, `local_workflow_execution.py`, `local_workflow_runners/comfyui.py`, `routes/capabilities.py`.
- Desktop/file/voice boundary: `desktop_pet_next/src/main.js`, `desktop_pet_next/src-tauri/src/main.rs`, `routes/desktop_pet.py`, `desktop_pet_engine.py`, `client_protocol.py`, `routes/voice.py`, `cover_song.py`, `generated_files_media.py`, `generated_files_cards.py`, `start_akane_next.ps1`.
- Reused package contract: capcore's current `CapabilityDescriptor`, `CapabilityToolSpec`, registry, permission, and projection types.

There is no implemented Akane Skill runtime in `companion_v01/` today. Skill behavior appears only in research/design documents. M66 must therefore describe the boundary and introduce it only with a real later slice; it must not claim an existing runtime.

## 4. As-is architecture

### 4.1 Per-turn model exposure

The actual path is:

```text
engine_services.response_builder.prepare_context()
  ├─ engine._resolve_capability_selection()
  │    └─ engine_services.tool_rounds.resolve_capability_selection()
  │         ├─ build_capability_snapshot()
  │         ├─ CapabilityRegistry.select()
  │         └─ build_adapter_tool_handlers() for dynamic extension names
  ├─ engine._resolve_tool_handlers()
  │    └─ engine_services.tool_rounds.resolve_tool_handlers()
  │         ├─ engine.tool_handlers
  │         ├─ plugin handlers + MCP handlers + Python handlers
  │         ├─ CapabilitySelection tool-name filtering
  │         └─ ToolReadinessGate.filter_handlers()
  ├─ tool_orchestration_engine.build_native_tool_decision_plan()
  │    └─ native_tool_schema.build_openai_native_tool_specs()
  └─ engine._build_tool_prompt_context()
       ├─ resolves selection and ready handlers again
       ├─ CapabilityDisclosure light facts
       └─ handler.build_prompt_instruction() for legacy tools
  -> prompt_builder.PromptBuilder.build_final_generation_context()
       └─ assembles the selected tool context with the rest of the final prompt
```

Important details:

- `response_builder.prepare_context()` resolves capability selection and ready handlers separately for the native path, then `_build_tool_prompt_context()` resolves them again for the legacy path.
- `DomainProfileRegistry.get()` always returns the default profile. `filter_tool_names()` is currently a no-op normalization, so domain-profile tool policy is structurally present but not an active authority today.
- `CapabilityRegistry` selects by client mode and material snapshot. It is not a dependency health registry.
- `ToolReadinessGate` uses handler object identity plus profile/session/mode as a cache key, with default TTLs of 15 seconds for ready and 5 seconds for unavailable. A handler without `capability_status()` is unconditionally treated as ready with `status_check_not_required`.
- Dynamic MCP/Python/plugin handlers are rebuilt during resolution. Because the readiness cache key begins with `id(handler)`, simply adding a status method to a newly constructed dynamic handler would also defeat useful cache reuse unless the authority boundary changes.
- Native tools are additionally restricted by `NATIVE_TOOL_DECISION_ALLOWLIST` and provider/model support. Native-exposed names are excluded from the legacy prompt in that round.
- `web_search` has a hard-coded `native_web_search_tool_schema()` special case instead of using the same semantic source as every other tool.
- The legacy prompt obtains descriptions from each handler's `build_prompt_instruction()`. Native schema generation first tries `ToolMetadata.input_schema`, then falls back to a sanitized form of that same legacy prose. This is a projection bridge, not a canonical semantic definition.

### 4.2 Invocation, validation, execution, and result refill

The live path is:

```text
engine._prepare_tool_round_decisions()
  -> tool_orchestration_engine.normalize_tool_invocation()
       -> engine._resolve_tool_handlers() again
       -> handler.normalize_call()
  -> validate_tool_invocation()
       -> engine._resolve_tool_handlers() again
       -> handler.normalize_call() again
  -> execute_tool_invocation()
       -> validate_tool_invocation() again
       -> engine._resolve_tool_handlers() again
       -> handler.execute()
       -> ToolExecutionResult
       -> ToolResultEnvelope adapter
  -> engine._record_tool_round_result()
       -> stream events
       -> native Anthropic/OpenAI tool history or legacy follow-up prompt
       -> Memcore tool trace
```

`ToolInvocation` currently contains only `name`, `arguments`, `source`, and `id`. It has no instance binding, selected offer receipt, compatible spec/schema hash, deadline, cancellation contract, idempotency policy, or replay ledger.

`ToolMetadata.requires_confirmation` is not a universal execution gate today; it was introduced as contract metadata. Generic adapters and `BrowserPageToolHandler` have explicit capcore permission paths, but built-ins do not automatically inherit one. In particular `open_browser` is tagged medium/confirmation-required in metadata while its emitted desktop event says `requires_confirmation: false`. M66 must make confirmation an enforced ToolSpec/policy/broker rule rather than another descriptive flag.

`execute_tool_invocation()` does not execute against the handler snapshot used to build the model schema. It re-resolves handlers after normalization and again after validation. A dynamic provider may therefore change between schema selection and execution.

`ToolResultEnvelope` currently reports `status="ok"` for any non-`None` `ToolExecutionResult`, even if the actual stream event or follow-up denotes `failed` or `unavailable`. Error interpretation is separately inferred from events and `<tool_use_error>`, so the envelope is not yet a reliable broker result.

Duplicate suppression is also local and short-lived: a per-round `seen_tool_calls` signature prevents repeated calls in one loop, and Memcore tracing deduplicates a trace key, but there is no cross-request execution idempotency ledger.

### 4.3 Dynamic MCP, Python, and plugin tools

`engine_services.tool_rounds.build_adapter_tool_handlers()` merges three sources on demand:

1. `PluginCapabilityToolBridge` projects `PluginHost.capability_descriptors`, an immutable descriptor snapshot captured during plugin activation. Invocation later goes through `PluginHost.invoke_from_consumer()`, which has its own lifecycle-loop, timeout, cancellation, result, and managed-artifact handling.
2. MCP handlers are rebuilt from profile-scoped `capabilities.yaml`. A cached discovered tool enters the model only when `promptExposed` is true. `McpStdioCapabilityAdapter.descriptor_for_tool()` converts MCP discovery metadata into a capcore descriptor; invocation starts/calls the stdio provider later.
3. `AkanePythonCapabilityAdapter` owns three local helper descriptors. All currently set `prompt_exposed=False`, so they are catalogued but do not enter the model path.

`AdapterCapabilityToolHandler` validates and checks capcore permission before `adapter.invoke()`, but it has no `capability_status()`. Therefore a cached MCP discovery record or plugin descriptor is treated as ready by the global readiness gate even when the actual provider has disconnected since discovery/activation. Failure is commonly discovered only at invoke time.

MCP discovery is therefore currently both provider metadata and, after opt-in, model semantics. In the target it must become only a provider adapter discovery input. Runtime discovery may propose an admin-only binding candidate, but it may not create or mutate a model-facing ToolSpec.

### 4.4 Background worker and workflow side paths

`delegate_task` calls `TaskWorkerService.delegate_task()`, which schedules `run_task_sync()` through `BackgroundTaskRunner`. `TaskWorkerService._allowed_handlers()` filters the static `engine.tool_handlers` map using `AGENT_ALLOWED_TOOLS`, then `_execute_worker_tool()` calls:

```text
handler.normalize_call()
  -> handler.execute()
```

This bypasses the full per-turn `ToolReadinessGate`, `ToolInvocation`, validation/envelope, and future broker boundary. It also does not include dynamically rebuilt MCP/plugin handlers.

ComfyUI workflows are another real, separate execution lane. `routes/capabilities.py` preflights and schedules a job, `_run_bound_workflow_job()` creates `WorkflowExecutionRequest`, and `call_workflow_execution_runner()` invokes `ComfyUiWorkflowRunner.execute_workflow()`. The runner is injected by `app.py` and calls the loopback ComfyUI adapter. These workflow routes are real and return byte-backed output handles, but they are control-center/workshop routes rather than model tool handlers. They must eventually use the same offer/broker/artifact contracts instead of remaining a parallel provider/job authority.

### 4.5 What is hidden before the model, and what fails only after invocation

| Current case | Hidden before schema/prompt? | Actual behavior |
|---|---:|---|
| Wrong client mode | Yes | `CapabilityRegistry` module modes remove the tool. |
| Missing document/media/image material | Usually | Material-triggered modules remain latent and omit their heavy tool names. |
| `generate_image` service/provider unavailable | Yes | Handler has `capability_status()` and is removed by `ToolReadinessGate`. |
| `browser_page` runner unavailable | Yes, against backend host | Handler probes `ManagedBrowserPageRunner`; in cloud mode this says whether the cloud host can run Playwright, not whether the user's PC can. |
| `web_search` AnySearch unavailable | Usually | Its handler performs cached/background MCP readiness probing and can be removed. |
| `cover_song` FFmpeg/RVC/model unavailable | Yes | `CoverSongToolHandler.capability_status()` delegates to the cover service. |
| `open_browser` while no desktop is connected | No | The handler has no readiness method and only emits `browser_open_requested`; no executor acknowledgment exists. |
| `transcribe_media` missing faster-whisper/FFmpeg | No | Missing dependencies are checked inside `generated_files_media` after the call. |
| `separate_audio_stems` missing Demucs/FFmpeg | No | Dependency failure occurs in execution. |
| `clean_voice_track` missing DeepFilterNet/FFmpeg | No | Dependency failure occurs in execution; `auto` may fall back to FFmpeg, `ai` fails. |
| `convert_media_file` / `inspect_media_info` missing FFmpeg/FFprobe | No | Dependency failure occurs in execution. |
| MCP stale discovery / disconnected stdio command | No | Generic adapter has no readiness method; cached `promptExposed` metadata remains eligible until invoke fails. |
| Plugin becomes unhealthy after activation | No | Descriptor snapshot can remain visible; `PluginHost.invoke_from_consumer()` reports host/invoke failure later. |

This table is why merely adding more `capability_status()` methods is insufficient. It would still leave multiple readiness authorities, stale cache races, and no binding between the selected status and the actual dispatch.

### 4.6 How provider state reaches prompt and schemas today

- `local_capability_catalog.build_local_capability_catalog()` and `local_capability_config` expose provider/workflow/MCP status primarily to authenticated capability/control-center routes. That catalog does not directly drive every model round.
- `CapabilityRegistry.light_hint` and `CapabilityDisclosure` put semantic capability facts into the legacy prompt. `resolve_capability_disclosures()` changes a selected disclosure to a generic unavailable fact when none of its tools survived readiness.
- Ready handlers feed both native schemas and legacy instructions. Thus a readiness result affects exposure by handler removal, not through a stable resolved receipt.
- Native schema descriptions and parameters come from `ToolMetadata.input_schema`, handler prose, capcore descriptor projection for adapters, or the special web-search schema. Provider-originated MCP/plugin descriptor text can therefore become model-facing text.
- `AdapterCapabilityToolHandler.build_prompt_instruction()` explicitly tells the model that a capability comes from an adapter source. The target forbids exposing provider/host/device/deployment information, so this wording must disappear.
- When a tool disappears between schema selection and execution, validation reports a list of currently available tool names. It does not have the earlier offer receipt or a reason-safe lease failure.

### 4.7 Authority count and overlap

There is no honest single “registry count,” because different objects own different dimensions and several objects overlap. The useful count is by authority dimension:

| Dimension | Minimum current authority-like surfaces | Current sources |
|---|---:|---|
| Model semantics and schema | 5 | `CapabilityRegistry.light_hint`; handler `build_prompt_instruction()`; `TOOL_METADATA_BY_TYPE.input_schema`; capcore `CapabilityDescriptor.short_hint/inputs`; `native_web_search_tool_schema()` special case. |
| Eligibility and availability | 7 | `CapabilityRegistry` mode/material triggers; `DomainProfile` filter structure (currently default/no-op); presence in `engine.tool_handlers`; `ToolReadinessGate`; `local_capability_config/catalog` status/probes; cached MCP discovery; `PluginHost` descriptor/lifecycle snapshot. |
| Dispatch/execution | 4 | Built-in handler execution; generic adapter handler execution; `PluginHost.invoke_from_consumer()` lifecycle path; `TaskWorkerService` direct handler execution. The ComfyUI workflow route is an additional non-model provider/job lane. |
| Artifact/location representation | At least 4 overlapping forms | Attachment handles, generated handles, `workspace:/` relative locations, and absolute backend/local paths in events/routes/follow-ups. |

These row counts must not be summed as if they were independent registries. They show why M66 cannot be implemented as a fifth registry. The target count is one semantic authority (ToolSpec), one ephemeral live-offer index consumed by one resolver, one execution broker, and one artifact broker.

Registry/catalog-shaped objects that deserve explicit treatment are:

- `CapabilityRegistry`: active prompt-time mode/material selector and light-hint source.
- `engine.tool_handlers` plus `TOOL_METADATA_BY_TYPE`: active executor lookup plus partial schema/risk metadata.
- `ToolReadinessGate`: active availability cache for the few handlers that implement probes.
- `local_capability_config` / `local_capability_catalog`: persisted provider/workflow/MCP config and control-center status view; it overlaps availability but is not the per-turn authority.
- `PluginHost._capabilities`: active immutable plugin descriptor/executor snapshot.
- MCP discovery in profile config: active dynamic tool descriptor cache for prompt-exposed MCP tools.
- capcore `CapabilityAdapterRegistry` created in `engine.__init__()`: a dormant shadow manifest registry. `builtin_capability_manifests/` contains only `.gitkeep`; outside the reload route it does not feed model selection or execution. It should be deleted in M66-A rather than kept beside the fabric.
- Python adapter specs: a real descriptor source, currently non-prompt-exposed.

The overlap can be summarized as:

```text
CapabilityRegistry ── mode/material names + light hints ─┐
DomainProfile ─────── policy-shaped names (no-op today) ├─> ready handler subset
engine.tool_handlers ─ executor presence ────────────────┤
dynamic MCP/plugin/Python descriptors ───────────────────┤
ToolReadinessGate ─ short-lived status ──────────────────┘
          ├─> native schema: metadata/capcore/special web schema
          ├─> legacy prompt: handler prose + disclosures
          └─> execution: resolve again, then one of several dispatch lanes
```

### 4.8 Desktop/cloud boundary as implemented

#### Renderer declarations are not executor offers

`client_protocol.ClientCapability` describes response/input/rendering features such as speech segments, TTS playback, desktop context, screen vision, file drop, audio playback, and tool-action events. `desktop_pet_next/src/main.js::buildClientCapabilities()` sends:

```text
speech_segments, tts, file_drop, tool_actions, audio_playback
```

and conditionally `desktop_context` / `screen_vision`.

None of these proves that RVC, faster-whisper, FFmpeg, DeepFilterNet, Playwright, a filesystem executor, or a browser executor is alive. M66 must keep `client_capabilities` as a presentation/input negotiation contract and use `CapabilityOffer` for actual execution.

The screen/context chains are also client input, not executors:

- `main.js::collectDesktopContextForTurn()` calls Tauri `get_desktop_context_snapshot`, optionally reads bounded clipboard text, and sends the result as `desktop_context` in `/think`.
- Screen-vision direct mode uses `getDisplayMedia()`, compresses recent frames to JPEG data URLs, and sends them as `desktop_screen_frames` in the current `/think` request.
- Screen-vision summary mode batches frames and posts `/desktop-pet/vision/clip`; `routes.desktop_pet.desktop_pet_submit_screen_vision_clip()` calls `engine.submit_desktop_screen_vision_clip()`, while latest/reaction/clear routes operate on that per-profile/session workspace.

These are user-granted observation/upload paths. `desktop_context` or `screen_vision` in `client_capabilities` means the client can supply/render that input contract; it must not make unrelated local tools ready. A future model-initiated screen capture would require its own reviewed ToolSpec, offer, policy, and approval rather than reusing this declaration.

#### Browser semantics

- `OpenBrowserToolHandler.execute()` only emits `browser_open_requested`. `main.js::handleBrowserOpenEvent()` validates the public URL again and calls Tauri `open_external_url`. This is a useful end-to-end seed, but it has no lease, executor acknowledgment, instance-bound receipt, or QQ-to-PC route.
- `BrowserPageToolHandler` directly owns `ManagedBrowserPageRunner`. In cloud-personal deployment, `browser_page` opens and manipulates a browser on the cloud server, not on the user's PC. Its current model semantics are therefore wrong for the intended cloud-brain mode. The stable tool remains `browser_page`; the provider must become a Desktop Satellite offer.
- `open_music_search` also emits `browser_open_requested` and must later follow the same executor semantics without becoming a provider-specific model tool.

#### Shared-filesystem assumptions

Two concrete chains require frontend and backend to see the same filesystem:

1. Local drop import:

   ```text
   main.js::importDroppedFilesToWorkspace()
     -> POST /desktop-pet/workspace/import-local with Windows absolute paths
     -> routes.desktop_pet.desktop_pet_workspace_import_local()
     -> engine.import_desktop_pet_local_paths()
     -> desktop_pet_engine.import_desktop_pet_local_paths()
     -> backend reads those paths
   ```

   A cloud backend cannot read a Windows path sent by the PC.

2. Open/reveal/export generated or attachment files:

   ```text
   main.js::fetchWorkspaceItemLocation()
     -> GET .../workspace/{attachments|generated}/{handle}/location
     -> backend returns Path(path).resolve() as JSON
     -> open_local_file / show_item_in_folder / export_file_to_desktop
   ```

   In cloud mode this returns a server path, leaks it to the client, and cannot open it on the PC.

There is already reusable transport groundwork:

- `/desktop-pet/attachments/audio` uploads multipart bytes rather than expecting the server to read the client path.
- Attachment/generated `.../{handle}/content` routes return bytes by handle.
- Tauri has an instance-rooted `cache/desktop_pet/export_staging` and an atomic staging-to-desktop publication flow.

M66 should consolidate these pieces into ArtifactBroker instead of adding another file API.

#### Prompt/event path leakage

`generated_files_cards.py` constructs multiple model follow-up strings containing `本地路径：<absolute_path>`. The affected families include compose, revise, style, conversion, separation, voice cleaning, dataset preparation, and other generated-file summaries. `SendFileToolHandler` and its compatibility subclass also put `absolute_path` into `desktop_delivery.path` events.

Existing trace sanitizers redact some Windows-looking paths, but sanitization after arbitrary strings have been assembled is not a sufficient authority boundary. The target result contract must not contain the path in the first place.

#### TTS, ASR, Whisper, and RVC

- `/tts` chooses a configured GPT-SoVITS loopback provider or Edge TTS, returns audio bytes, and the desktop plays them. In cloud mode `127.0.0.1` means the cloud host. Edge TTS can remain a server-local offer; a user's local GPT-SoVITS must be a Desktop Satellite offer.
- `/asr` uploads recording bytes, tries the configured OpenAI-compatible loopback adapter, then falls back to backend faster-whisper/FFmpeg. A user's local Whisper installation cannot be reached by cloud loopback and must be offered by the satellite.
- `RvcWebUiProvider` intentionally accepts only localhost endpoints. `CoverSongService` therefore runs relative to the backend host today. Under a cloud brain, local RVC must execute on the Desktop Satellite.
- Media workbench tools often inspect FFmpeg/Demucs/DeepFilterNet/faster-whisper only after invocation; their offer producers must perform bounded readiness and advertise the constraints before the resolver selects them.

#### Launcher and formal-personal risk

`start_akane_next.ps1` currently sets `AKANE_BACKEND_URL` to loopback and, unless `-SkipBackend` is supplied, starts or restarts a local Akane backend after checking the three-field instance health contract. That behavior is valid for `local-default`, but a cloud-personal satellite mode must be a distinct launcher mode that refuses to start a backend and connects the desktop to the verified remote personal instance. Otherwise it creates precisely the forbidden second formal personal backend.

## 5. Target architecture

### 5.1 Components and ownership

```text
                          one Akane instance process

canonical capcore ToolSpecs ──────────────┐
turn/material/channel context ────────────┼─> ToolResolver
policy + instance grants ─────────────────┤      │
live CapabilityOffer index ───────────────┘      ├─ model-safe schemas/facts
                                                  └─ frozen execution receipts
                                                            │
native / legacy / Skill / worker invocation ───────────────> ExecutorBroker
                                                            │
                          ┌─────────────────────────────────┼────────────────────┐
                          │                                 │                    │
                   server-local adapter              Desktop Satellite      MCP/plugin adapter
                          │                                 │                    │
                          └──────── result + artifacts ─────┴────────────────────┘
                                                            │
                                                     ArtifactBroker
                                                            │
                                     cloud store / HTTPS transfer / PC staging
```

Ownership rules:

- **ToolSpec** owns stable model semantics. It is the only source for prompt description, input/output schema, risk, effects, confirmation policy, material requirements, execution class, and idempotency semantics.
- **CapabilityOffer** is ephemeral operational evidence that a particular instance-bound executor can implement a known ToolSpec version under stated internal constraints. It cannot override ToolSpec semantics.
- **ToolResolver** computes per-round eligibility and availability; it stores no second semantic catalog.
- **ExecutorBroker** owns dispatch state, second validation, idempotency, deadlines, cancellation, retry, and uncertainty handling.
- **ArtifactBroker** owns all cross-location material identity and transfer. An executor privately resolves handles to local paths; the model and ordinary server logs do not.
- **Provider adapters** translate broker requests to a concrete local function, plugin, MCP process, satellite RPC, or workflow runner. They do not own model policy or orchestration.
- **Skill** declares stable semantic requirements and instructions. **Orchestrator** owns steps, budgets, parallelism, retries, and structured degradation. Neither selects an OS, endpoint, provider, port, or deployment location.

### 5.2 Do not create a fifth registry

The implementation should evolve existing abstractions in place:

- Extend capcore `CapabilityDescriptor` / `CapabilityToolSpec`; do not create an Akane-only duplicate ToolSpec type.
- Evolve `companion_v01/capability_registry.py` from its current module selector into the host `ToolResolver`, then remove the compatibility `CapabilityRegistry` facade after all callers move.
- Turn `engine.tool_handlers` into an executor-adapter lookup only. A handler/adaptor may normalize private executor input and execute; it may not carry a second prompt description/schema.
- Replace `ToolReadinessGate` with offer publication/expiry. Existing `capability_status()` methods may temporarily feed server-local offer producers, then become private health probes.
- Make `local_capability_catalog` a read-only projection of ToolSpecs, policy, and live offers for the control center. It must stop independently probing and deciding model readiness.
- Delete the dormant capcore manifest registry boot scan and reload route in M66-A. If reviewed manifest loading is needed later, it must load canonical ToolSpecs into the same catalog, not remain a separate runtime registry.

The end-state authority count is:

| Concern | End-state authority |
|---|---|
| Model semantics/schema/risk/effects/material contract | capcore ToolSpec catalog |
| Live executor availability | instance-scoped CapabilityOffer index with leases |
| Per-round exposure | ToolResolver pure resolution result |
| Dispatch/idempotency/deadline/cancel/retry | ExecutorBroker |
| Material identity/location/transfer | ArtifactBroker |
| Steps/budget/parallel/degradation | Orchestrator |

### 5.3 ToolSpec draft

The exact reusable type belongs in capcore. The following is the minimum contract, not an instruction to create a parallel Akane dataclass:

```python
ToolSpec(
    tool_id="browser_page",              # stable semantic ID
    spec_version="1.0.0",                # semantic contract version
    schema_version=1,                     # input/output wire schema version
    schema_hash="sha256:...",            # canonical schema fingerprint
    display_name="Browser page",
    prompt_description="Open and operate an Akane-managed visible public web page ...",
    input_schema={...},
    output_schema={...},
    visible_surfaces=("desktop", "qq"),
    risk="high",
    confirm="always",
    effects=("browser_action",),
    material_requirements=(),
    execution_class="sync",               # sync | long_task
    idempotency="effectful",               # read_only | idempotent | effectful
    max_result_bytes=...,
)
```

Required rules:

- `tool_id` describes user-visible semantics, not location. `browser_page`, `cover_song`, and `transcribe_media` remain stable regardless of which executor wins.
- A provider switch must not alter `tool_id`, active `schema_version`, or model description. A real schema migration increments `schema_version` and is deployed as a ToolSpec migration, not smuggled through provider discovery.
- `prompt_description` is the only model-description authority. Native provider serializers and the legacy JSON fallback project from the same text/schema.
- `risk`, `confirm`, and `effects` are fail-closed. Unknown effects require review/confirmation; an offer cannot lower them.
- `material_requirements` describes kinds and access needs, for example source audio handle, maximum bytes, or local-presence preference. It never contains a path.
- `execution_class` decides whether the broker expects a bounded synchronous result or a durable job acknowledgment.
- `idempotency` controls retry behavior. A provider cannot claim an effectful ToolSpec is read-only.

Current capcore types already cover ID, display name, short hint, input slots/schema, risk, confirmation, effects, and visible surfaces. M66 needs to add explicit semantic/schema versions, material requirements, output contract, execution class, idempotency, and result-size policy. Provider/lease/instance fields do not belong in ToolSpec; they belong in CapabilityOffer.

### 5.4 CapabilityOffer draft

```python
CapabilityOffer(
    offer_id="offer_<opaque>",
    instance_id="personal",
    tool_id="browser_page",
    executor_id="exec_<opaque>",          # internal only
    provider_kind="desktop_satellite",    # server_local | desktop_satellite | mcp | plugin
    device_id="device_<opaque>",           # empty for server-local; internal only
    compatible_spec=">=1.0.0,<2.0.0",
    schema_versions=(1,),
    schema_hashes=("sha256:...",),
    lease_epoch="lease_<random>",
    issued_at="...",
    expires_at="...",
    state="active",
    constraints={
        "max_input_bytes": 268435456,
        "material_access": ("local", "download"),
        "max_concurrency": 1,
        "execution_classes": ("sync",),
    },
)
```

Rules:

- Offers are authenticated, instance-scoped, ephemeral operational state. They are not written to Memcore or conversation history.
- An offer names only canonical `tool_id` plus compatible versions and constraints. It does not carry model prompt text.
- A server-local handler, a Desktop Satellite, an MCP-backed adapter, and a plugin all publish the same logical shape.
- Default network lease parameters for the first implementation: heartbeat every 10 seconds, lease TTL 30 seconds, and a new random `lease_epoch` on every reconnect. These values are internal configuration, never prompt content.
- A clean WSS disconnect excludes the offers immediately; TTL is the safety net for a lost disconnect/partition.
- Server-local offers use a process-start epoch and are renewed by the process-local producer. A failed/stalled health probe stops renewal rather than leaving a permanent “ready” flag.
- MCP offers exist only while a live adapter session/health contract is satisfied. Cached discovery by itself is not an offer.
- `incompatible`, `revoked`, `draining`, `disconnected`, and `expired` offers are never selected.
- Offer details are visible only on authenticated diagnostics/control-center surfaces. The model receives neither provider kind nor reason internals.

### 5.5 Offer state machine

```text
registered/pending
      ├─ authenticated + compatible + healthy ─> active
      ├─ version mismatch ─────────────────────> incompatible
      └─ denied binding ───────────────────────> revoked

active
  ├─ graceful shutdown ─> draining ─> disconnected ─> expired
  ├─ socket loss ────────> disconnected ─────────────> expired
  ├─ lease not renewed ──────────────────────────────> expired
  ├─ policy/admin revoke ────────────────────────────> revoked
  └─ new incompatible spec deployment ──────────────> incompatible
```

Only `active` is eligible. `draining` may finish already-acknowledged invocations but receives no new dispatch. Reconnect always creates a new lease epoch; a receipt issued against the old epoch cannot dispatch on the new connection without a fresh resolution.

### 5.6 ToolResolver

Resolver input:

```python
ResolveContext(
    instance_id=...,
    profile_user_id=...,
    session_id=...,
    client_mode=...,
    channel="desktop_pet" | "qq" | ...,
    material_handles=(...),
    explicit_intent_tags=(...),
    skill_requirements=(...),
    now=...,
)
```

For each canonical ToolSpec, resolution is ordered and fail-closed:

1. **Eligibility**: surface/channel, material kind/size/ownership, turn intent, and instance feature profile.
2. **Availability**: at least one active, unexpired offer bound to the same instance.
3. **Policy**: instance/channel/device grant and confirmation rules. Policy can hide or require confirmation; it cannot change semantics.
4. **Compatibility**: ToolSpec version, schema version/hash, execution class, and material constraints.
5. **Deterministic offer choice**: policy-controlled priority such as local material affinity, then server-local or satellite preference. Provider identity never affects the tool name shown to the model.

Resolver output is immutable for the round:

```python
ResolvedToolSet(
    resolution_id="resolve_<opaque>",
    instance_id="personal",
    specs=(ToolSpec, ...),                  # model-safe projection source
    receipts={
        "open_browser": ExecutionReceipt(
            offer_id="offer_<opaque>",
            lease_epoch="lease_<random>",
            offer_expires_at="...",
            spec_version="1.0.0",
            schema_version=1,
            schema_hash="sha256:...",
            policy_hash="sha256:...",
        )
    },
    unavailable_facts=(...),
)
```

The same `ResolvedToolSet` must be passed to native-schema construction, legacy prompt projection, normalization, validation, and broker submission. Those stages must not call `_resolve_tool_handlers()` again.

The broker still performs a second check immediately before dispatch. This is not a second resolver authority; it verifies that the frozen receipt is still valid. If it is not, the invocation terminates as `unavailable_before_dispatch` and the model gets a short semantic fact, for example “当前无法执行本地桌面动作，请直接说明暂时不可用。” It does not get a path, port, provider, device name, exception class, or endpoint.

### 5.7 Native and legacy model projection

- Native-first remains the preferred model protocol. `capcore-provider-openai` and other provider adapters serialize the exact resolved ToolSpecs.
- The legacy JSON `tool_call` path remains a thin compatibility adapter for model providers that have not passed native-tool acceptance. It receives the same resolved ToolSpecs and emits the same `tool_id`/schema semantics.
- `native_web_search_tool_schema()` is removed after `web_search` receives its canonical ToolSpec.
- Handler `build_prompt_instruction()` is removed as a semantic authority. During family-by-family migration it may be a generated thin projection of ToolSpec, never hand-authored parallel prose.
- `TOOL_METADATA_BY_TYPE` is removed family by family after each ToolSpec is canonical. It must not remain as a second schema/risk table.
- An unavailable tool has no callable schema in either native or legacy mode. Brief unavailable facts are not fake tools and contain no recovery internals.

### 5.8 ExecutorBroker and invocation contract

The target invocation extends the current provider-neutral type:

```python
BrokerInvocation(
    invocation_id="call_<opaque>",
    instance_id="personal",
    tool_id="open_browser",
    arguments={...},
    resolution_id="resolve_<opaque>",
    receipt=ExecutionReceipt(...),
    source="native_openai" | "native_anthropic" | "legacy_json" | "skill" | "worker",
    created_at="...",
    deadline_at="...",
    idempotency_class="effectful",
    confirmation_receipt=None,
)
```

Broker duties, in order:

1. Validate instance ID, ToolSpec/schema hash, arguments, materials, deadline, and replay state.
2. Re-check offer state, lease epoch/expiry, policy hash, and current compatibility.
3. Perform the execution-time second confirmation. If confirmation is required, bind the approval to `instance_id + invocation_id + tool_id + arguments digest + offer_id + lease_epoch + spec/schema hash + expiry`. A changed offer or arguments invalidates approval.
4. Reserve the invocation in a durable-enough instance-scoped idempotency ledger before dispatch.
5. Materialize/transfer required artifacts through ArtifactBroker.
6. Dispatch to the chosen adapter and require an acknowledgment.
7. Record progress, terminal result, sanitized model feedback, and artifact handles.
8. On reconnect, reconcile nonterminal invocation IDs before deciding retry or uncertainty.

The first implementation should classify bounded commands with a normal deadline of at most 30 seconds as `sync`. Media/workflow operations that cannot reliably finish inside that bound are `long_task`: the broker returns a durable job acknowledgment, progress can continue independently, and final artifacts are attached to the job. A synchronous HTTP request must not be held open for an RVC/Whisper/ComfyUI-scale task.

Retry rules:

- Before executor acknowledgment, a read-only/idempotent invocation may be dispatched once more within its deadline after a fresh compatible resolution.
- After `running` acknowledgment, read-only/idempotent work may be reconciled and, only with a provider idempotency guarantee, retried in a bounded way.
- Effectful work is never blindly retried after acknowledgment or an uncertain disconnect. It becomes `execution_unknown` until the executor reconciles the same `invocation_id`.
- A duplicate `invocation_id` returns the stored terminal result or current state and never starts a second execution.
- Provider switching is allowed only before effectful execution begins or for a ToolSpec whose idempotency contract explicitly permits it.
- Cancellation is best effort after dispatch. “Cancel requested” is not “cancelled”; only executor acknowledgment makes it terminal `cancelled`.

### 5.9 Invocation state machine

```text
accepted
  -> reserved
  -> awaiting_materials
  -> awaiting_confirmation
  -> dispatched
  -> running
  -> succeeded

Terminal/exception states:
  rejected
  unavailable_before_dispatch
  failed
  timed_out
  cancelled
  execution_unknown
```

Key transitions:

- Readiness passed, then executor disappears before dispatch: `unavailable_before_dispatch`; no effect and no success claim.
- Dispatch sent but no acknowledgment by deadline: `timed_out` if the transport proves it was not accepted; otherwise `execution_unknown` for effectful work.
- Executor acknowledged `running`, then disconnected: `execution_unknown` until reconciliation. No blind effectful retry.
- Executor returns structured failure: `failed` with a public reason enum and safe short feedback.
- Deadline expires while running: send cancel, then settle `timed_out` only when the executor reports no successful completion; otherwise reconcile the actual result.
- Result envelope status is derived from broker terminal state, not from the presence of a non-empty handler object.

### 5.10 ArtifactBroker

Canonical model-visible handles:

- `file_...`: source document/image/general file material.
- `audio_...`: source audio material where the specialized prefix materially helps tool schema clarity.
- `gen_...`: generated result.

Current `img_...` and `workspace:/...` inputs may be accepted by a short-lived local-default boundary translator, but new ToolSpecs must emit and consume only the canonical handle set. The translator normalizes before resolution and is removed after stored references are migrated; it is not another location authority.

Draft artifact record:

```python
ArtifactRef(
    handle="gen_<opaque>",
    instance_id="personal",
    owner_scope="profile/session policy reference",
    kind="audio" | "image" | "document" | "binary",
    mime_type="audio/wav",
    size_bytes=...,
    sha256="...",
    state="available",
    locations=(
        ArtifactLocation(kind="cloud_store", locator=<private>),
        ArtifactLocation(kind="satellite", executor_id=<private>, locator=<private>),
    ),
)
```

Artifact rules:

- Location locators are private broker/executor data and never enter ToolSpec, prompt, normal result events, Memcore, or ordinary logs.
- Local-to-cloud input uses a short-lived, single-purpose HTTPS upload ticket scoped to instance, handle, direction, maximum size, MIME policy, content hash, and expiry. The satellite initiates the upload.
- Cloud-to-local delivery uses HTTPS download to the bound instance's Tauri staging directory. The satellite validates length/hash/MIME, publishes with an atomic rename, then calls open/reveal/export.
- Tauri must open only a locally issued staging token/path created by the satellite after successful download. It must never pass a backend-returned absolute path directly to `open_local_file`.
- Large bytes do not travel over WSS. WSS carries only invocation, progress, cancellation, artifact metadata, and transfer-ticket references.
- Failed or partial transfers leave no “ready” artifact and no fake delivery event. Partial files are cleaned by a bounded local staging policy.
- Standard logs use handle, size, hash prefix, reason enum, instance-safe correlation ID, and timing. They do not log raw path, bearer token, signed URL, provider stderr, or raw exception text.

Artifact transfer state:

```text
declared -> locating -> transferring -> verifying -> available
                       ├──────────────> failed
available -> staging -> staged -> opened/revealed/exported
                    └─> failed
available/staged -> expired/cleaned
```

### 5.11 Desktop Satellite

The satellite may be embedded in the Tauri process or be a narrowly scoped local companion process, but its product boundary is fixed:

- It renders locally and may execute approved local capabilities.
- It has no chat engine, formal memory, persona, Care authority, QQ gateway, or autonomous conversation loop.
- It makes an outbound TLS/WSS connection to exactly one verified Akane `instance_id`; it opens no home-router/public inbound port.
- One installation binding has one instance credential. Changing instances requires explicit unpair/re-enrollment. A `personal` device cannot silently become a `finance` device.
- Its private device key is generated locally and stored in the OS credential store/keychain. Enrollment uses a short-lived one-time pairing code from an authenticated instance control surface. The server stores the public key/fingerprint, instance binding, revocation state, and allowed capability grants.
- After enrollment, the server issues short-lived session credentials following a nonce challenge. The device credential and session token are distinct from `AKANE_ADMIN_TOKEN`, QQ secrets, and user model/API keys.
- Every offer and invocation is checked against authenticated connection instance, device, lease epoch, ToolSpec/schema hash, and policy.
- Going offline immediately removes the connection's offers; TTL covers network partitions where close is not observed.
- The renderer's `client_capabilities` payload never creates an offer. Offers come only from the authenticated executor control connection after local dependency checks.

Launcher modes must become explicit:

| Mode | Backend behavior | Satellite behavior |
|---|---|---|
| `local-default` compatibility | Start/reuse the one local backend as today, with the existing three-field instance check | Connect to that same local instance and publish local offers; never create another backend. |
| cloud `personal` desktop/satellite | Refuse to start a local backend; require remote URL and verify `/health` instance/root binding | Connect outbound to the cloud personal instance and render/execute locally. |
| named `finance` | Independent explicit DataRoot/backend/credential enrollment | Only a separately enrolled finance satellite may offer tools; no personal credential reuse. |

QQ behavior is deliberately simple:

- PC offline or lease expired: local-only tools are absent from the QQ model schema. The request is not silently queued for later execution. If the user's intent specifically needs the missing local action, the model receives one short semantic unavailable fact and explains that the PC capability is currently unavailable.
- PC online: only tools with an active compatible offer and channel/device policy grant enter the schema. Public/read-only/idempotent work follows policy; high-risk or effectful actions still require a bound approval. The model never learns which PC/provider was selected.
- Online/offline state is never summarized into long-term memory.

### 5.12 MCP, Python handler, plugin, workflow, Skill, and Orchestrator boundaries

#### MCP

- MCP is a provider adapter. MCP discovery creates admin-visible provider methods and compatibility candidates, not automatic ToolSpecs.
- A discovered MCP method can serve the model only after it is explicitly bound to a reviewed canonical `tool_id` and compatible schema/version. Arbitrary discovery text does not become the prompt description.
- A live MCP session publishes/renews offers; cached discovery without a live session publishes none.
- MCP risk/annotations may raise restrictions but may not lower canonical ToolSpec risk/effects.
- MCP transport remains internal to the provider adapter. It is not the cloud-to-satellite protocol.

#### Internal Python and built-in handlers

- An in-process Python function can be a direct server-local executor adapter. It is not forced through MCP.
- `engine.tool_handlers` may remain temporarily as the adapter lookup, but descriptions/schema/readiness are removed from it as families migrate.

#### Plugins and workflows

- A trusted plugin package may contribute a reviewed canonical ToolSpec at startup through the one capcore catalog; its live adapter separately publishes an offer. Plugin lifecycle loss expires the offer without leaving prompt residue.
- `PluginHost` may retain adapter lifecycle ownership and its timeout implementation internally, but broker state/deadline/idempotency is the outer authority.
- ComfyUI and other workflows are long-task provider adapters. Their provider/workflow config can remain host-owned configuration, while readiness becomes offers, job execution goes through ExecutorBroker, and bytes go through ArtifactBroker.

#### Skills

A Skill manifest/body may reference only:

```yaml
required_tools: [transcribe_media]
optional_tools: [clean_voice_track, compose_file]
required_materials: [audio]
```

It may not inspect Windows/Linux, localhost, a port, provider ID, device ID, MCP server ID, or deployment location. Skill text is progressively loaded only when selected; it is not a permanent capability-status dump.

Resolver returns structured Skill readiness:

```json
{
  "status": "ready|degraded|blocked",
  "missing_required": ["transcribe_media"],
  "missing_optional": ["clean_voice_track"],
  "available_tools": ["compose_file"]
}
```

- Missing required capability: Skill does not start and returns a short structured block reason.
- Missing optional capability: Skill starts in `degraded` mode and follows an authored semantic fallback.
- No fallback may claim an unavailable step succeeded.

There is no current Skill runtime, so no Skill registration or prompt placeholder should be added before the first real Skill slice.

#### Orchestrator

The Orchestrator receives stable ToolSpecs, resolver output, and Skill requirements. It owns step ordering, budgets, bounded parallelism, retry policy within ToolSpec idempotency, long-job waiting, and semantic degradation. It never chooses by operating system, endpoint, provider, host, port, or local/cloud naming. `TaskWorkerService` must eventually become an Orchestrator consumer of Resolver/Broker, not a direct handler caller.

### 5.13 Transport decision

| Option | Decision | Reason |
|---|---|---|
| WebSocket over TLS | **Use for control plane** | True bidirectional invocation/result/progress/cancel/heartbeat, immediate disconnect detection, outbound PC connection, and one maintained product protocol. |
| HTTPS upload/download | **Use for artifacts** | Streaming/backpressure/range/size controls are better suited to bytes; keeps WSS messages small and inspectable. |
| Long polling | Do not implement in M66 | Adds state latency, hanging request lifecycle, harder cancellation, and a second transport implementation. Add only after real network evidence proves WSS unusable, not as a future-only placeholder. |
| Tailscale/SSH | Ops/development only | Useful for private diagnostics but does not express ToolSpec, instance binding, offer leases, artifact handles, approvals, or idempotency. It also imposes a network product on end users. |
| MCP transport | Provider-side only | MCP lacks Akane's device enrollment, instance lease, artifact staging, approval receipt, and reconnect/idempotency semantics. Using it cloud-to-PC would create the wrong authority boundary. |
| Existing `/think` NDJSON stream | Keep for conversation/render events | It is request-scoped and server-to-client; it is not a persistent authenticated executor control plane and cannot serve QQ-to-PC execution. |

### 5.14 Security and credential boundaries

- TLS is mandatory. The satellite performs instance verification, then signs a server nonce with its enrolled device key before receiving a short-lived session credential.
- Pairing codes are single-use, short-lived, scoped to one instance, and never accepted as admin tokens. Enrollment and revocation are audited with safe IDs only.
- The server checks that the connection's authenticated instance equals every offer, receipt, invocation, and artifact ticket instance. A mismatch is rejected before registration or dispatch.
- Device capability grants are allowlists scoped to stable `tool_id` and, where needed, channel/effect constraints. Unknown new ToolSpec versions are denied until reviewed.
- Confirmation receipts bind exact arguments digest, material handles, ToolSpec/schema, offer lease, instance, expiry, and invocation. Replaying approval for another device/offer/argument set fails.
- Invocation IDs and nonce/timestamp windows prevent replay. The broker/executor retain dedupe records at least through the invocation deadline plus the configured replay window; effectful terminal records survive reconnect long enough to prevent duplicate user-visible action.
- Artifact tickets are direction-specific, short-lived, bounded by size/MIME/hash, and reveal no filesystem path. Signed URLs and tokens are redacted at construction, not merely by a later logger filter.
- Provider stderr/raw exceptions stay in bounded private diagnostics only after secret/path scrubbing; model feedback and ordinary logs use reason enums.
- The public `/health` route is unchanged. Authenticated capability-fabric diagnostics may report safe counts/states but not credentials, endpoints, local paths, or provider raw errors.

### 5.15 Prompt budget and status containment

The resolver must avoid replacing today's schema sprawl with offer-status prose:

1. Only eligible, policy-allowed, compatible tools with an active offer are schema candidates.
2. Relevance ranking uses explicit intent, current material kinds, channel/surface, and selected Skill requirements. It does not expose provider status.
3. Initial guardrails: at most 16 callable tools and about 6,000 tool-schema tokens per round. These are measured budgets, not semantic limits; if exceeded, deterministic relevance ranking chooses a subset and records a private metric.
4. Canonical prompt descriptions should normally fit within 360 characters; detailed safety belongs in schema/effects/policy rather than repeated prose. Input schemas remain precise and versioned.
5. At most two unavailable semantic facts, each at most 80 Chinese characters, may be included only when the user's current intent/material directly matches an unavailable capability or a selected Skill needs it. Generic PC/provider heartbeat status never enters the prompt.
6. The model never sees offer lists, device IDs, provider names, hosts, ports, endpoints, TTLs, local paths, raw exceptions, or “try port X” recovery advice.
7. Skill catalog metadata is small; the Skill body is loaded only after selection. Tool schemas required by that Skill still pass through the same Resolver.
8. Tool results above their declared budget are stored by ArtifactBroker and returned as a stable preview plus handle, never a local path.

### 5.16 Instance and compatibility boundaries

#### `personal`

- Cloud personal owns all formal conversational state.
- Its offer index accepts only personal-enrolled device credentials.
- Personal artifact handles, staging namespace, invocation ledger, policy, and diagnostics are instance-scoped.
- Desktop reconnect does not import or create memory; it only renews offers and renders/executes.

#### `finance`

- Finance has independent ToolSpecs enabled by its instance feature profile, offers, device credentials, artifacts, broker ledger, and QQ path.
- A personal satellite offer is invisible to finance resolution even if `tool_id` and schema match.
- Finance may have no desktop satellite; in that case only its server-local/MCP/plugin offers can enter schemas.

#### `local-default`

- Existing all-in-one capabilities continue to work by publishing server-local or locally connected satellite offers for `local-default`.
- Existing persisted provider/workflow configuration is preserved and adapted; it is not treated as a live offer without a health lease.
- Native-first and the thin legacy model-call adapter both use the same Resolver result.
- Legacy `img_` / `workspace:/` references may be normalized at one compatibility boundary during migration, but are not emitted by new schemas.
- No named instance may fall back to local-default DataRoot, credentials, artifacts, or offer index.

## 6. Explicit anti-patterns

The following are rejected designs:

- Add `CapabilityFabricRegistry` beside `CapabilityRegistry`, `ToolReadinessGate`, local catalog, handler registry, and capcore registry without deleting old authority.
- Create provider/location tools such as `local_cover_song`, `cloud_cover_song`, `desktop_browser_page`, or `mcp_web_search`.
- Let MCP discovery, plugin runtime text, device offers, or `client_capabilities` author model descriptions/schema.
- Treat `tool_actions`, desktop mode, renderer heartbeat, or an open `/think` response as executor availability.
- Start a local formal personal backend when connecting the desktop to cloud personal.
- Give one device credential access to multiple named instances or reuse personal enrollment for finance.
- Use `AKANE_ADMIN_TOKEN`, QQ secret, or model API key as a device credential.
- Store PC online/offline status in memory, session summaries, persona, or Care.
- Return backend absolute paths to Tauri, put local paths into prompt/result/events, or ask a cloud backend to read a PC path.
- Send large artifacts through WebSocket messages or expose home-network inbound ports.
- Blindly retry effectful work after an acknowledged dispatch or uncertain disconnect.
- Keep both handler prose/metadata and canonical ToolSpec prose/schema as long-lived editable sources.
- Keep cached MCP tools in the prompt after MCP disconnect.
- Let Skill/Orchestrator branch on OS, host, provider, port, or deployment location.
- Add model-visible future-only tools, fake job success, inert offers, or demo satellite connections.
- Expand public `/health` with provider/device/offer fields.

## 7. Incremental migration plan

Each phase below is a separately verifiable release slice. “Rollback” means reverting that bounded application/package release and any additive metadata migration; it does not authorize keeping old and new live authorities indefinitely.

### M66-A — Implemented: instance-bound `open_browser` Desktop Satellite

**Outcome**

One stable `open_browser` ToolSpec is available in desktop-pet and QQ rounds only while the one bound Desktop Satellite has an active compatible lease. Execution goes through a minimal broker over outbound WSS and is acknowledged by Tauri. No artifact transport is required in this phase.

The reviewed starting contract should preserve the public-HTTP(S)-only URL validation, declare an `external_url_open` effect, be at least medium risk, and require the instance/device's explicit capability grant plus the canonical confirmation policy. Neither an offer nor current fire-and-forget behavior may lower that policy.

**Implementation entries**

- Extend capcore ToolSpec with version/schema/idempotency/execution-class fields needed by the slice; define canonical `open_browser` semantics once.
- Evolve `companion_v01/capability_registry.py` with the minimal ToolResolver/offer input for this tool. Do not add a parallel registry file that leaves its old selection active for the same tool.
- Extend `tool_invocation.py` and `tool_orchestration_engine.py` so the frozen `open_browser` receipt flows from schema selection to a minimal ExecutorBroker dispatch.
- Add an instance-authenticated capability-fabric WSS route/service under the existing Akane app lifecycle. This is a transport/session service, not a semantic registry.
- Add Tauri outbound enrollment/session/heartbeat/offer/invocation handling in `desktop_pet_next/src-tauri/src/main.rs`; keep `main.js` responsible for rendering, not availability authority.
- Add an explicit cloud-satellite launcher mode to `start_akane_next.ps1` that requires remote backend verification and refuses to start a backend. Preserve existing `local-default` mode.
- Add authenticated, non-public satellite diagnostics without touching `/health`.

**Old path deleted or made thin in the same slice**

- Remove `open_browser` from `CapabilityRegistry`'s unconditional desktop-browser module selection. `browser_page` remains on its old path only until its later slice.
- Delete `OpenBrowserToolHandler.execute()`'s fire-and-forget `browser_open_requested` semantics for this tool. If a class remains for argument normalization during the slice, it is a thin broker adapter with no prompt description/schema/readiness authority.
- Stop using `client_capabilities.tool_actions` or desktop mode as evidence that `open_browser` can execute.
- Delete engine initialization/scan of the dormant capcore `CapabilityAdapterRegistry`, `_resolve_profile_capability_manifests_dir()` if unused afterward, and `/capabilities/adapter-registry/reload`. Its built-in directory is empty and it is not a live model/execution path; leaving it would make the new fabric another registry.
- `browser_open_requested` may remain for `open_music_search`/`browser_page.open_for_user` during their documented migration windows, but not as `open_browser`'s execution path.

**Verification**

- Unit/contract tests for offer lease/epoch, same-instance binding, version/hash match, frozen receipt, duplicate invocation, safe reason shaping, and exact `/health` keys.
- Real local smoke with an isolated backend and Tauri: online schema contains `open_browser`; an explicit public URL opens once and returns acknowledgment; disconnect removes the schema immediately/within TTL; QQ test input can invoke only while online.
- Negative smoke for private/localhost URLs, expired approvals, A-device-to-B-instance registration, drop after resolution but before dispatch, and duplicate invocation ID.

**Rollback boundary**

Revert the M66-A capcore/Akane/Tauri release together. Enrollment records are additive and can remain revoked/ignored by the previous version. Do not retain a production flag that executes both the old stream-event route and the broker route.

**User-visible change**

When the PC is online, asking Akane from QQ or the desktop to open an approved public page opens it on the bound PC with a real success/failure acknowledgment. When the PC is offline, Akane no longer claims it asked a nonexistent desktop to open something; the tool is absent and Akane explains the temporary limitation only when relevant.

### M66-B — Implemented: canonical schema projection and frozen round for read-only tools

**Outcome**

Move a low-risk family—`web_search`, `retrieve_memory`, `read_memory_timeline`, and the already native-accepted read-only set—to canonical ToolSpecs. Native and legacy projections use one `ResolvedToolSet`, and normalize/validate/execute do not re-resolve that set.

**Implementation entries**

- Add reviewed ToolSpecs and golden schema hashes in capcore/Akane host composition.
- Change `response_builder.prepare_context()` to resolve once per round and pass the result to native schema, legacy prompt, normalization, validation, and broker.
- Make server-local implementations publish active offers; adapt `web_search` readiness to offer renewal.
- Update provider serializers and legacy prompt adapter to project from ToolSpec.

**Old path deleted or made thin**

- Delete `native_web_search_tool_schema()`.
- Delete migrated tools' hand-authored schema/risk rows from `TOOL_METADATA_BY_TYPE` and their hand-authored prompt descriptions, or make the methods generated one-line ToolSpec projections only for a short named migration window.
- Remove migrated capability light-hint semantics from `CapabilityRegistry`; keep only non-migrated modules until their phase.
- Remove repeated `_resolve_tool_handlers()` calls from normalize/validate/execute for migrated invocations; all use the frozen receipt.

**Verification**

- Golden native/legacy schema equivalence tests.
- Existing native acceptance tests run against ToolSpec projections.
- Provider switch test proves `tool_id`, schema version/hash, and model description stay unchanged.
- Offer expiry after resolution but before execution returns `unavailable_before_dispatch`, not unknown tool or false success.

**Rollback boundary**

Revert the ToolSpec package and host release as a unit. Specs are code/versioned data, so there is no user database downgrade. No parallel hand-authored schema is retained for rollback.

**User-visible change**

Read-only tools behave the same when available, but unavailable search/providers stop lingering in the prompt and mid-round loss produces a concise honest failure.

### M66-C — Implemented: built-in resolver/readiness convergence

**Outcome**

Migrate remaining built-in tool families to canonical ToolSpecs and server-local/satellite offers. Material eligibility, channel policy, and availability converge in ToolResolver. This phase is implemented family by family, but each family commit removes its old semantic/readiness source before the next family starts.

**Implementation entries**

- Encode material requirements now represented by `CapabilitySnapshot` triggers into ToolSpecs/resolver eligibility.
- Publish server-local offers for document/image/media/reminder/persona/task/workspace implementations, with bounded health producers for external dependencies.
- Make local capability/control-center views project the canonical specs and live offer states.
- Complete structured broker result mapping so failed/unavailable `ToolExecutionResult` can no longer become envelope `status=ok`.

**Old path deleted or made thin**

- Delete `ToolReadinessGate` after the last handler family publishes offers.
- Delete remaining hand-authored `TOOL_METADATA_BY_TYPE` semantic/schema rows and handler `build_prompt_instruction()` prose after each family moves.
- Delete `CapabilityRegistry` module/light-hint selector after its final material/mode rule is represented by ToolSpec/resolver; retain at most a temporary import-compatible facade that immediately delegates, then remove it in the same phase.
- Make `local_capability_catalog` a pure safe projection; delete its independent tool/provider readiness decisions and duplicate probes where offer producers now own health.
- Keep `local_capability_config` as host configuration/policy storage, not live availability authority.

**Verification**

- For every ToolSpec: schema golden test, material eligibility positive/negative test, offer expiry test, policy test, and result-status test.
- Explicit dependency-negative tests for FFmpeg, FFprobe, Demucs, DeepFilterNet, faster-whisper, RVC, Playwright, image generation, and MCP-backed search.
- Prompt snapshots prove no provider/source labels and no unavailable tool schemas.

**Rollback boundary**

Each family is a revertable commit/release with no concurrent semantic implementation. If a family fails acceptance, revert that family before starting the next; do not restore an always-on shadow registry.

**User-visible change**

Akane stops offering tools whose actual dependency is missing. Users see fewer “called it, then discovered it is not installed” failures and receive short actionable semantic degradation instead.

### M66-D — Implemented: desktop ArtifactBroker and removal of shared-filesystem assumptions

**Outcome**

All model and cross-device file operations use `file_` / `audio_` / `gen_` handles. Local drop transfers bytes on demand; cloud results download into instance-specific desktop staging before open/reveal/export.

**Implementation entries**

- Reuse attachment/generated stores and content routes behind one ArtifactBroker record/transfer API.
- Replace local drop path JSON with multipart or ticketed HTTPS upload and handle registration.
- Add Tauri download-to-staging with size/hash/MIME verification and atomic publication; pass only a local staging reference to open/reveal/export.
- Update ToolSpecs and tool results to accept/return canonical handles only.
- Add bounded preview-plus-handle storage for large model results.

**Old path deleted or made thin**

- Delete `/desktop-pet/workspace/{attachments|generated}/{handle}/location` routes.
- Delete `main.js::fetchWorkspaceItemLocation()` and all direct use of backend-returned paths.
- Delete `desktop_delivery.path` / `absolute_path` from stream events and generated-file cards.
- Delete `generated_files_cards.py` follow-up lines containing `本地路径：...`.
- Delete `/desktop-pet/workspace/import-local`'s absolute-path payload contract; if the route name is retained, it becomes a thin byte/ticket adapter with no path input.
- Remove `workspace:/` and `img_` from new model schemas. A single compatibility normalizer may accept old stored references during the documented migration window, then is deleted after migration.

**Verification**

- Cross-machine tests where backend and Tauri have intentionally unrelated roots.
- Raw path/token/signed URL scans over prompts, logs, stream events, result envelopes, Memcore traces, and API responses.
- Interrupted upload/download, wrong hash, oversized file, MIME mismatch, expired ticket, cross-instance handle, and partial staging cleanup tests.
- Tauri open/reveal/export spy proves no command runs until verified download completes.

**Rollback boundary**

Artifact records are additive and existing file bytes remain in their stores. Rollback is a coordinated server/Tauri release revert before removing old client compatibility; no production release serves both raw-path and broker-open semantics indefinitely.

**User-visible change**

Dragging a file into a cloud-connected pet actually uploads it, and a cloud-generated file can be downloaded and opened on the PC. Users no longer receive server paths that cannot exist locally.

### M66-E — In progress: local browser/media/voice execution and long tasks

**Outcome**

Move `browser_page`, `open_music_search`, local GPT-SoVITS, local Whisper/ASR, RVC cover song, FFmpeg/Demucs/DeepFilterNet media work, and configured local workflows to Desktop Satellite offers where appropriate. Keep Edge TTS and genuinely cloud-installed executors as server-local offers. Long work uses broker jobs.

**Implementation entries**

- Add satellite adapters and dependency-specific offer producers with constraints/concurrency.
- Change `/tts` and `/asr` resolution to choose eligible offers by stable semantic operation, while preserving actual audio-byte response UX.
- Move browser-page visible window control to the PC satellite.
- Add long-task acknowledgment/progress/cancel/reconcile for RVC, Whisper batch, media conversion/separation/cleaning, and ComfyUI-scale work.
- Preserve server-local implementations for `local-default` or a cloud host only when that same instance actually has a live server-local offer.

**Old path deleted or made thin**

- Remove the assumption that cloud loopback endpoints describe the user's PC.
- Make `BrowserPageToolHandler` a broker adapter; remove direct cloud-host `ManagedBrowserPageRunner` selection for cloud personal.
- Move local provider endpoint/path configuration ownership to the satellite/device side or a private executor config channel; cloud model/control paths retain only safe grant/status projections.
- Make direct TTS/ASR provider selection helpers thin broker clients after parity; do not retain separate provider fallback policy beside Resolver.
- Replace direct workflow runner invocation/job state in `routes/capabilities.py` with broker long jobs; `local_workflow_execution.py` becomes a thin adapter contract or is deleted if capcore's execution contract fully covers it.

**Verification**

- Real local smoke for visible `browser_page`, Edge/server TTS, satellite GPT-SoVITS, satellite Whisper, one FFmpeg operation, one dependency-negative media case, RVC readiness, and one ComfyUI job where installed.
- Disconnect before dispatch, while running, and after artifact creation; cancel and reconnect reconciliation.
- PC offline QQ schema contains no local-only media/browser tool; online with explicit grants it can run and deliver a real result.

**Rollback boundary**

Revert each executor family as a coordinated server/satellite release. Preserve provider configuration data for rollback, but only one runtime selection policy is active in a release.

**User-visible change**

Cloud Akane can use the user's actual local RVC/Whisper/TTS/media/browser capabilities when the PC is online, without moving Akane's memory or persona to the PC. Long tasks report real progress and do not fake completion on timeout.

### M66-F — In progress: MCP, plugin, workflow, and background-worker convergence

**Outcome**

Every extension/provider lane publishes offers and every invocation goes through Resolver/Broker/ArtifactBroker. MCP remains an adapter; plugin lifecycle remains internal; background workers no longer call handlers directly.

**Implementation entries**

- Add reviewed MCP-method-to-canonical-ToolSpec bindings; publish offers only from live MCP sessions.
- Make `PluginCapabilityToolBridge` publish canonical specs/offers and dispatch through the broker while keeping `PluginHost` lifecycle protection.
- Route `TaskWorkerService` and ComfyUI/control-center jobs through broker APIs using source `worker`/`workflow`.
- Unify managed plugin artifacts with ArtifactBroker handles.

**Old path deleted or made thin**

- Delete per-turn dynamic MCP handler reconstruction from cached `capabilities.yaml` as a model exposure authority.
- Remove `promptExposed` as sufficient runtime readiness; it remains at most an admin-reviewed catalog preference translated into ToolSpec policy.
- Make `PluginCapabilityToolBridge.build_tool_handlers()` a thin offer/adapter projection or delete it when direct broker registration is complete.
- Delete `TaskWorkerService._execute_worker_tool()` direct `normalize_call()`/`execute()` path and its independent tool prompt descriptions; worker receives resolved ToolSpecs.
- Delete workflow/provider job state that duplicates broker jobs after migration.

**Verification**

- Real stdio MCP connect/disconnect/reconnect and schema removal.
- Plugin activation, health loss, timeout, cancellation, managed artifact, and schema-stability tests.
- Worker tool calls prove full policy/readiness/instance/idempotency path and no direct handler execution.
- Workflow outputs prove ArtifactBroker ownership and long-job behavior.

**Rollback boundary**

Revert adapter family releases independently; reviewed bindings/config remain data, but cached discovery cannot remain an active parallel prompt source.

**User-visible change**

Disconnected MCP/plugins stop appearing immediately, and background/workflow operations produce the same honest status and artifact delivery behavior as frontstage calls.

### M66-G — First real Skill and Orchestrator slice, then final compatibility cleanup

**Outcome**

Introduce Skill runtime only with one concrete workflow, for example “transcribe media, optionally clean voice, then compose the requested summary,” using stable `transcribe_media`, optional `clean_voice_track`, and `compose_file`. The Orchestrator handles steps/budget/retry/degradation through Resolver/Broker.

**Implementation entries**

- Define a reviewed Skill manifest/body with required/optional stable tools and material kinds.
- Add progressive Skill loading and a structured capability-requirement result.
- Add Orchestrator step/budget/parallel/retry rules without provider/OS checks.
- Add the context-message/result-envelope field only if the real Skill execution model needs it; do not add unused runtime placeholders in earlier phases.

**Old path deleted or made thin**

- Replace equivalent hard-coded multi-step worker prompt/provider checks for that concrete workflow; do not keep both Skill and bespoke orchestration.
- Remove final migrated handler prompt/schema facades and old compatibility normalizers whose data migration is complete.
- Keep legacy model `tool_call` only as the already documented thin provider-protocol adapter; it does not regain semantic/readiness authority.

**Verification**

- Missing required tool yields `blocked` with no execution.
- Missing optional tool yields `degraded` and follows the authored fallback.
- All tools present executes the real workflow with bounded rounds and real artifacts.
- Provider swap does not change Skill text or tool/schema IDs.
- Prompt budget snapshots confirm progressive loading and no capability-state dump.

**Rollback boundary**

Remove/revert the one Skill and restore the single previous orchestrated workflow release. Do not ship an empty Skill marketplace or fake Skill tool as a fallback.

**User-visible change**

The first multi-step workflow becomes more consistent and can explain a real missing/optional capability without exposing provider mechanics or pretending skipped work succeeded.

## 8. Deletion ledger

This ledger is a required implementation checklist, not optional cleanup.

| Phase | Delete | Make thin | Preserve as authority |
|---|---|---|---|
| M66-A | Dormant `engine.capability_adapter_registry` init/scan, empty manifest reload route; `open_browser` direct fire-and-forget execution | `OpenBrowserToolHandler` only if needed as temporary argument/broker adapter; `browser_open_requested` remains solely for not-yet-migrated callers | Canonical `open_browser` ToolSpec, offer index, minimal resolver/broker, satellite connection |
| M66-B | `native_web_search_tool_schema()`; migrated metadata schemas/prose; repeated handler resolution for migrated calls | Legacy JSON protocol projection from ToolSpec | Canonical read-only ToolSpecs, frozen `ResolvedToolSet`, server-local offers |
| M66-C | `ToolReadinessGate`; remaining migrated `TOOL_METADATA_BY_TYPE` semantics and handler prompt prose; final old `CapabilityRegistry` selector | `local_capability_catalog` as safe view; `local_capability_config` as config/policy store; handler map as executor lookup only | ToolResolver + ToolSpecs + offers |
| M66-D | `/location` routes; raw-path import contract; `fetchWorkspaceItemLocation()`; `desktop_delivery.path`; generated follow-up absolute paths | Temporary legacy handle normalizer with explicit removal date/window | ArtifactBroker and canonical handles |
| M66-E | Cloud-host assumptions for PC browser/RVC/Whisper/GPT-SoVITS; duplicate direct provider selection/job policy | Existing concrete runners/services as executor adapters | Resolver/Broker long jobs and satellite/server-local offers |
| M66-F | Cached MCP discovery as prompt authority; worker direct handler execution; duplicate workflow/plugin job dispatch | MCP/plugin/workflow adapters and PluginHost lifecycle internals | Broker dispatch, live offers, ArtifactBroker |
| M66-G | Bespoke orchestration equivalent to the first real Skill; expired compatibility normalizers/facades | Legacy model `tool_call` as provider-protocol adapter only | Skill requirements + Orchestrator over Resolver/Broker |

“Preserve” never means preserve provider-specific model semantics. Concrete execution code can remain because an adapter is not an authority when it cannot author ToolSpec, availability, policy, or orchestration.

## 9. Negative test matrix

All tests must assert structured status/reason and absence of side effects; none may pass by checking only that a field exists.

| ID | Scenario | Required assertion |
|---|---|---|
| N01 | Offer expires before the round | Tool is absent from native schemas and legacy tool prompt; at most a short relevant semantic fact appears. |
| N02 | Readiness/resolution succeeds, executor disconnects before dispatch | Broker returns `unavailable_before_dispatch`; executor receives no action; model sees no provider/path/port/raw error. |
| N03 | Executor disconnects after `running` | Effectful invocation becomes `execution_unknown`, is not blindly retried, and reconciles by the same invocation ID on reconnect. |
| N04 | Invocation exceeds deadline | Cancel is requested; final state distinguishes `timed_out`, `cancelled`, and `execution_unknown`; no false success. |
| N05 | Provider changes for the same tool | `tool_id`, spec/schema version/hash, native schema, legacy projection, Skill requirement, and model description are byte-stable. |
| N06 | Offer supports the wrong schema/spec version | Offer state is `incompatible`, tool is not exposed through that offer, dispatch is rejected before effect. |
| N07 | Same invocation replayed/duplicated | One execution maximum; duplicate returns stored running/terminal state. Test process reconnect and client retry. |
| N08 | Device enrolled to instance A registers/offers/serves B | Enrollment, offer, invocation, and artifact access all fail closed before registration/dispatch. |
| N09 | PC offline for a QQ request | Local-only tools are absent; no queued-later promise; no online state written to memory. |
| N10 | PC online for a QQ request | Only explicitly granted compatible tools appear; an approved tool executes on the bound PC with a real acknowledgment. |
| N11 | High-risk QQ desktop action without approval | No dispatch; approval receipt cannot be reused for different arguments/offer/lease/instance. |
| N12 | Raw path/token/provider stderr/raw exception introduced by executor | Prompt, standard logs, events, result envelope, API response, and Memcore trace contain only handle/safe reason; leak scan fails the test. |
| N13 | Cloud file is requested for open/reveal/export | Tauri command is not called until HTTPS download, size/hash/MIME verification, and local atomic staging complete. |
| N14 | Artifact ticket expired/wrong instance/wrong direction/wrong hash/oversized | Transfer fails, no ready artifact or open event, partial staging is cleaned. |
| N15 | `local-default` without satellite-only dependencies | Existing server-local capabilities continue through offers/resolver/broker; legacy provider-model fallback still works. |
| N16 | MCP process disconnects after discovery | Offers expire/disconnect and MCP tools disappear from both prompt channels; cached discovery remains admin data only. |
| N17 | MCP reconnects with changed description but same bound method | Canonical ToolSpec description/schema remains unchanged; incompatible method schema is rejected. |
| N18 | Plugin health fails after activation | Offer disappears; descriptor does not remain as a callable prompt tool; in-flight state follows broker rules. |
| N19 | Skill missing required capability | Structured `blocked`, lists stable missing `tool_id`, executes no step, exposes no provider. |
| N20 | Skill missing optional capability | Structured `degraded`, authored fallback runs, no claim that optional work succeeded. |
| N21 | Worker attempts a non-allowed or unavailable tool | Resolver/policy rejects through broker; direct handler execution is impossible. |
| N22 | Renderer sends `tool_actions` but no executor WSS exists | No offer is synthesized and no local tool enters schema. |
| N23 | Clean disconnect followed by reconnect | Old lease receipts cannot dispatch on new epoch; fresh round resolves the new offer. |
| N24 | Public health contract | Response key set is exactly `{status, instance_id, root_binding}` for healthy/unavailable cases; no offer/device/provider fields. |
| N25 | Named personal and finance both running in isolated test roots | Handles, offers, ledgers, credentials, diagnostics, and staging cannot cross; no named fallback to local-default. |
| N26 | Cloud-personal launcher mode | It refuses to start/restart any backend and refuses an instance-mismatched remote `/health`. |
| N27 | Effectful invocation gets no ACK after send | State is not silently retried or marked success; reconciliation determines outcome. |
| N28 | All offers for a tool are draining | Tool is absent for new rounds; already acknowledged invocation may finish. |

Test fixtures may simulate timing/transport deterministically, but acceptance cannot stop at fixtures. Each completed executor family also needs a real process/device smoke.

## 10. Real smoke plan

No smoke in this design pass may touch production. Implementation smoke uses isolated test instance roots, test credentials, and a test QQ account/gateway where QQ behavior is exercised.

### 10.1 M66-A real smoke

1. Start isolated `personal-smoke` and `finance-smoke` backends with distinct roots/ports and verify each public `/health` has exactly three keys.
2. Enroll one real Tauri satellite only to `personal-smoke`; confirm finance rejects the same device credential.
3. Observe a real outbound WSS session and active `open_browser` offer through authenticated diagnostics.
4. Run a real model/schema capture in desktop and QQ modes; assert `open_browser` is present without provider/device/host/port text.
5. Ask the test QQ account to open a safe public URL. Verify one real OS-open action and one acknowledged broker terminal result.
6. Disconnect/exit Tauri; verify the offer is excluded immediately or no later than TTL, then capture a new QQ round with no `open_browser` schema.
7. Force disconnect after resolution but before dispatch and after `running`; verify the two distinct terminal/uncertain behaviors.
8. Replay the same invocation ID and verify no second browser open.

### 10.2 Artifact real smoke

1. Run backend and Tauri with deliberately unrelated filesystem roots/machines or containers.
2. Drop a real small file and audio file; verify byte upload and canonical handles, with no PC path in server request/log/prompt.
3. Produce/download a real generated file; verify content hash, instance staging, then real open/reveal/export.
4. Interrupt a larger transfer, corrupt a hash, expire a ticket, and attempt cross-instance retrieval; verify no open and no final ready event.
5. Scan captured prompts, standard logs, stream events, result JSON, and Memcore trace for drive-letter paths, UNC paths, `/home/...`, bearer/token-like values, signed URL queries, provider stderr, and raw exception class names.

### 10.3 Provider/executor real smoke

- Start then stop a real stdio MCP provider after discovery; capture prompt/schema before and after disconnect.
- Run one real server-local read tool and then switch to a compatible alternate offer; compare canonical schema bytes.
- Where installed, run real FFmpeg, faster-whisper, GPT-SoVITS, RVC, browser-page, and ComfyUI jobs through the broker. For missing dependencies, verify pre-schema omission instead of invoking a fake adapter.
- Cancel one real long task, disconnect one during execution, reconnect, and reconcile by invocation ID.
- Run the existing local-default startup and representative memory/search/file/media behavior through the unified contracts.

### 10.4 Skill/Orchestrator real smoke

- Execute the first concrete Skill once with every required/optional capability, once without an optional capability, and once without a required capability.
- Verify real artifact output in the full case, authored degraded output in the optional-missing case, and no execution in the required-missing case.
- Swap a compatible provider and prove the Skill body/requirements and model tool schema do not change.

## 11. Acceptance gates by layer

### Resolver gate

- Every emitted schema has an active compatible offer at resolution time.
- No ineligible, expired, disconnected, draining, revoked, or incompatible offer can produce a schema.
- Native and legacy projections share one canonical spec and one frozen resolution.
- Unavailable facts obey count/length/sanitization budgets.

### Broker gate

- Every execution source reaches the same broker.
- Dispatch always rechecks instance/lease/spec/schema/policy/deadline/materials.
- Duplicate and uncertain execution behavior is deterministic and effect-aware.
- Result envelope terminal status matches the broker state.

### Artifact gate

- Model/tool results use only canonical handles.
- No cross-location open occurs before verified staging.
- Paths/tokens/signed URLs/raw errors are absent from public/prompt/log contracts.

### Instance/security gate

- Personal/finance/local-default roots, credentials, offers, ledgers, artifacts, and staging are isolated.
- Cloud-personal desktop mode cannot start a second backend.
- Device credentials are separate from admin tokens.
- Public `/health` remains exactly three fields.

### User-experience gate

- Offline means the model does not offer or claim the action.
- Online approved capability produces a real observable effect/result.
- Timeout/disconnect/degradation is visible as a truthful concise state, not a fake completion.
- Desktop bubble/action/TTS/file-open behavior agrees with broker outcome.

## 12. Main technical risks

1. **Semantic drift during authority removal.** Existing behavior is encoded across handler prose, metadata schemas, capcore descriptors, hard-coded native schema, and material modules. Moving a family without golden native/legacy/schema/UX tests could silently alter model decisions or permissions. Mitigation: family-sized atomic migration, canonical schema hashes, and deletion in the same slice.
2. **Distributed execution uncertainty.** A WSS disconnect can occur after an effect starts but before acknowledgment/result. Naive retries can duplicate browser/file/media actions; naive failures can hide a completed action. Mitigation: durable instance-scoped invocation IDs, executor dedupe/reconciliation, lease epochs, confirmation binding, and no blind effectful retry.
3. **Artifact locality and secret/path leakage.** Current code mixes handles, workspace locations, and absolute paths. Cross-machine transfer introduces large-byte, staging, signed-ticket, and instance-confusion risks. Mitigation: ArtifactBroker as the only location authority, HTTPS tickets, hash/size/MIME checks, atomic staging, construction-time redaction, and cross-root real smoke.

## 13. Recommended first implementation ticket

M66-A should be accepted only when all of the following are true in one focused slice:

- A reviewed, versioned `open_browser` ToolSpec is the sole semantic/schema source.
- One outbound, instance-authenticated Desktop Satellite connection publishes a leased offer.
- ToolResolver exposes `open_browser` only for that active offer and freezes its receipt for the round.
- ExecutorBroker rechecks the receipt and obtains a real Tauri acknowledgment with idempotent invocation handling.
- Desktop/QQ offline and online behavior is proven with a real local smoke.
- The old `open_browser` fire-and-forget availability assumption is removed.
- The dormant capcore adapter manifest registry and reload route are removed so M66 does not begin life as another registry.
- `/health` remains unchanged and no raw provider/device/path/token/error enters model-visible or standard-log output.

That slice proves the hard distributed boundary while avoiding artifact transfer and long-task complexity. Once it passes, the same contracts can absorb read-only server tools, files, media, MCP, plugins, workers, and Skills without changing model tool semantics.
