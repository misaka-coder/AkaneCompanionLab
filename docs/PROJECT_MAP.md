# Akane Companion Lab — Project Map

This document locates commonly used implementations by layer and subsystem.
Start with the [task-oriented development entry](DEVELOPMENT.md) for a bounded reading path,
then use this map for related files. Historical design documents describe context rather than current APIs.

Desktop development entry audit: 2026-10-11.

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│  Tauri Desktop Shell (desktop_pet_next)                          │
│  ┌─────────────┐  ┌────────────────┐  ┌──────────────────────┐  │
│  │ main.js      │  │ control-center-v2/    / data-sources.js       │  │
│  │ → src/pet/  │  │ (settings UI)  │  │ (backend source)    │  │
│  └──────┬───────┘  └──────┬─────────┘  └──────────┬───────────┘  │
│         │                 │                        │              │
│         └─────────┬───────┴────────────┬───────────┘              │
│                   │ Tauri bridge       │                          │
│     ┌─────────────┤ (emit/invoke)      ├──────────────┐           │
│     │ settings    │                    │ backend      │           │
│     │ commands    │  window actions    │ HTTP fetch   │           │
│     └──────┬──────┘  └────────┬───────┘  └──────┬────┘           │
└────────────┼──────────────────┼─────────────────┼────────────────┘
             │                  │                 │
┌────────────┼──────────────────┼─────────────────┼────────────────┐
│  Python Backend (companion_v01)                  │                │
│  ┌─────────▼──────────┐       │       ┌─────────▼──────────┐    │
│  │ /control-center/*  │       │       │ /desktop-pet/*     │    │
│  │ (snapshot, actions)│       │       │ (health, think,    │    │
│  └────────────────────┘       │       │  workspace, etc.)  │    │
│  ┌────────────────────┐       │       └────────────────────┘    │
│  │ engine.py          │       │                                 │
│  │ (central orchestrator)     │                                 │
│  └────────────────────┘       │                                 │
│  ┌────────────────────────────────────────────────────────┐     │
│  │ DOMAIN MODULES: memory, persona, gifts, tasks, media,  │     │
│  │ vision, embedding, retrieval, attachments, qq, ...     │     │
│  └────────────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────────────┘
```

---

## Layer 1: Python Backend (`companion_v01/`)

The backend is a FastAPI application. Entry: `app.py`. Configuration: `config.py`.

### Entry & Wiring

| File | Responsibility |
|------|---------------|
| `companion_v01/app.py` | FastAPI Host entry. Builds the Bot registry through the runtime factory, wires Bot and Host routes, mounts static resources/CORS, and owns the application startup/shutdown boundary. |
| `companion_v01/bot_runtime.py` | Public one-Bot construction and lifecycle API: `BotRuntime`, `BotRuntimeFactory`, `RuntimeMetrics` and startup logging. Internal helpers live in `bot_runtime_components/`. |
| `companion_v01/bot_runtime_components/` | Runtime ownership and public controls (`runtime`), saved configuration and ordered assembly (`factory`), plugin generation/engine bindings (`plugins`), conversation and shared job bindings (`services`), serialized startup/drain/shutdown (`lifecycle`), and terminal job projection (`completion`). See [bot_runtime_split_v1.md](bot_runtime_split_v1.md). |
| `config.py` | Pydantic `Settings` definitions and environment defaults. Saved editable values are loaded through `settings_overrides.py`; model-service and session model selections have their own owners. See [configuration scope](DEVELOPMENT.md#配置来源与生效范围). |
| `companion_v01/settings_catalog.py` | Setting metadata, editability, sensitivity and when each change takes effect. `settings_overrides.py` owns validation and persistence; `bot_runtime_components/factory.py` assembles the runtime configuration. |

### Core Orchestration

| File | Responsibility |
|------|---------------|
| `companion_v01/engine.py` | `AkaneMemoryEngine` — the central orchestrator. Owns `LLMRuntime`, `PromptBuilder`, `RetrievalService`, shared background execution, gifts, capabilities, persona cards, attachments/generated files, memory compaction, desktop services, tool orchestration and output adapters. When you need to know "what owns what", read the `__init__` of this class. |
| `companion_v01/engine_services/turn_preparation.py`, `turn_rounds.py`, `turn_completion.py` | The three stages called by `engine.py:_run_turn_core`: prepare context, run model/tool rounds, then save and produce output. QQ and desktop ingress paths are in [DEVELOPMENT.md](DEVELOPMENT.md#聊天请求从哪里走). |
| `companion_v01/client_protocol.py` | `ClientMode` and `ClientProtocolContext`. Defines capability boundaries per client (`desktop_pet`, `qq`, `web_static`). Data model for client capability declarations. |

### Routing Layer (`companion_v01/routes/`)

Each file builds a FastAPI `APIRouter` with a `build_*_router()` function.

| File | Routes | Key Responsibility |
|------|--------|-------------------|
| `routes/core.py` | `/resource-manifest`, `/capabilities`, `/character-packs` | Resource manifest + capability listing |
| `routes/think.py` | `/think`, `/think_once` | Dialogue ingress and turn coordination. `routes/think_stream.py` advances engine streams and emits progress while waiting. |
| `routes/desktop_pet.py` | `/desktop-pet/health`, `/desktop-pet/diagnostics`, `/desktop-pet/workspace/summary`, `/desktop-pet/workspace/*`, `/desktop-pet/music/*` | Desktop-pet health, diagnostics, workspace panel and music timeline. Retired screen-summary `/desktop-pet/vision/*` routes return 410; real screen images enter `/think`. |
| `routes/voice.py` | `/tts`, `/asr` | TTS synthesis, ASR recognition |
| `routes/gifts.py` | `/gifts/*` | Gift asset upload, listing, delivery |
| `routes/qq.py` | `/qq/*` | QQ gateway (NapCat) integration |
| `routes/sessions.py` | `/sessions/*` | Session management |
| `routes/system.py` | `/metrics`, `/health`, `/system/*` | System health, prometheus metrics, admin |
| `routes/control_center.py` | `GET /control-center/snapshot`, `GET /control-center/settings-catalog`, `POST /control-center/settings-catalog/{key}` | Aggregates 7 runtime fields and exposes typed settings. Real actions use the desktop bridge, native commands or explicit service routes; the inert generic action routes have been removed. |

### Domain Services

Each file owns a specific business domain. Most are classes instantiated by `AkaneMemoryEngine`.

#### Memory & Retrieval (MemCore Managed)

| File | Responsibility |
|------|---------------|
| `companion_v01/memcore_integration/manager.py` | `MemcoreManager`. Bridges host runtime to MemCore Unified Timeline V2 engine. |
| `companion_v01/memcore_integration/timeline.py` | `MemcoreTimelineToolService`. Adapts timeline reads (`read_memory_timeline`) to MemCore. |
| `companion_v01/retrieval_engine.py` | Memory tool adapters (`retrieve_memory`) and host permission checks. |
| `companion_v01/memory_read_policy.py` | Security boundary and permission validation for memory access across QQ/desktop scopes. |
| `companion_v01/tool_handlers/memory.py` | Native tool execution entry points for LLM memory interaction. |
| `companion_v01/embedding_provider.py` | `BaseEmbeddingProvider`, `HashedEmbeddingProvider`, `CachedEmbeddingProvider`. Embedding abstraction layer. |
| `companion_v01/store/` | SQLite-backed modular store (`akane_memory_v01.db`). Domain mixins for sessions, messages, reminders, attachments, workspaces, personas, and assets with `MemoryStore` facade. |

#### LLM & Prompt

| File | Responsibility |
|------|---------------|
| `companion_v01/llm_runtime.py` | `LLMRuntime`. LLM API client: models, streaming, thinking, rate limiting. |
| `companion_v01/prompt_builder.py` | `PromptBuilder`. Assembles full prompt from persona, memory, context blocks. |
| `companion_v01/prompt_blocks.py` | Individual prompt block templates (system, persona, memory, context, task). |
| `companion_v01/prompt_profiles.py` | `PromptModule`, `PromptProfileRegistry`. Named prompt configurations per "mode". |
| `companion_v01/mode_profiles.py` | `ModeProfileRegistry`. Runtime mode profiles that select prompt + capability combos. |
| `services/llm_client.py` | Low-level HTTP client for LLM API calls. |
| `services/tts_client.py` | `EdgeTTSClient`. TTS client for Microsoft Edge TTS. |

#### Persona & Characters

| File | Responsibility |
|------|---------------|
| `companion_v01/persona_config.py` | `PERSONA` — the base persona definition (name, traits, tone, backstory). |
| `companion_v01/persona_system.py` | `PersonaCardService`. Dynamic persona evolution: trait drift, mood, relationship tracking. |
| `companion_v01/npc_runtime.py` | `GenericNPCRuntime`. Generic NPC dialogue and behavior runtime. |
| `companion_v01/desktop_pet_character_resources.py` | `DesktopPetCharacterResourceService`. Character pack: outfits, emotions, assets. |
| `companion_v01/resource_manifest.py` | `ResourceManifest`. Manages resource schemas, manifests, and character/outfit/emotion data structures. |

#### Attachments & Generated Files

| File | Responsibility |
|------|---------------|
| `companion_v01/attachment_inbox.py` | `AttachmentInboxService`. Attachment import queue, file scanning, deduplication. |
| `companion_v01/attachment_ingest.py` | `AttachmentIngestService`. Parsing, chunking, media extraction from attachments. |
| `companion_v01/generated_files.py` | `GeneratedFileService`. Tracks AI-generated files (documents, images, audio). |
| `companion_v01/generated_files_cards.py` | Generated file UI card templates. |
| `companion_v01/generated_files_delivery.py` | Stable generated-file helper entry: scoped selection, delivery preparation, inspection and archive/delete management. |
| `companion_v01/generated_file_delivery_runtime/` | Scoped generated/attachment/media resolution (`resolution`), batch delivery preparation (`delivery`), bounded previews and actual tails (`inspection`), and confirmed record updates plus managed deletion (`management`). See [generated_files_delivery_split_v1.md](generated_files_delivery_split_v1.md). |
| `companion_v01/generated_files_io.py` | File I/O helpers for generated files. |
| `companion_v01/generated_files_media.py` | Media-specific handling for generated files. |

#### Gifts & Tools

| File | Responsibility |
|------|---------------|
| `companion_v01/gift_engine.py` | Gift creation, decoration, and delivery pipeline. |
| `companion_v01/gift_assets.py` | Gift asset storage: images, stickers, custom art. |
| `companion_v01/gift_system.py` | `GiftSystemService`. Gift lifecycle management. |
| `companion_v01/sticker_assets.py` | `StickerAssetService`. Sticker collection for gifts. |
| `companion_v01/tool_orchestration_engine.py` | Public tool-orchestration facade. Parse-only model call parsing is in `engine_services/tool_protocol.py`; request-local preparation is in `tool_orchestration/validation.py`; execution consumes that prepared call in `tool_orchestration/execution.py`. |
| `companion_v01/tool_handlers/builtin_tables.py` | Built-in `TOOL_SPEC_BY_TYPE` lookup and compatibility metadata projection. Specs own schema/risk/confirmation; `_BUILTIN_TOOL_CLASSIFICATION` holds supplemental category data. |
| `companion_v01/capability_registry_runtime/*_contracts.py` and `tool_handlers/` | Canonical built-in `CapabilityToolSpec` declarations and per-handler business normalization/execution. Local exceptions can declare their spec beside the handler. |
| `companion_v01/engine_services/tool_protocol.py` | `_prepare_tool_round_decisions`: retains provider call identity and wire values without business handler normalization. |
| `companion_v01/tool_orchestration/normalization.py`, `validation.py`, `execution.py` | Parse-only `parse_tool_call`; one request-local `prepare_tool_invocation`; shared validation and execution of its prepared arguments. |
| `companion_v01/tool_runtime.py` | Individual tool execution runtime. |
| `companion_v01/tool_handlers/execution.py` | Stable execution handler entry: run, input, status and cancellation tools with the existing provider, approval and resource contracts. |
| `companion_v01/tool_handlers/execution_runtime/` | Shared services and ownership (`base`), command/input approval (`approvals`), command admission and job wiring (`run`), input/status/cancel handlers (`control`), and bounded receipts plus confirmed artifact settlement (`receipts`). See [execution_handlers_split_v1.md](execution_handlers_split_v1.md). |
| `companion_v01/execution_run.py` | Stable execution kernel entry: provider contracts, owner-scoped run store, run-bound cursors and execution/status/cancellation tools. |
| `companion_v01/execution_runtime/` | Provider and record contracts (`models`), shared state/lock and terminal observers (`store`), UTF-8 output windows and cursors (`output`), dispatch and bounded polling (`commands`), snapshot validation (`mapping`), and redaction/receipt markers (`feedback`). See [execution_run_split_v1.md](execution_run_split_v1.md). |
| `companion_v01/execution_resources.py` | Stable execution resource entry: owner-scoped input staging, persistent project output snapshots, artifact registration and terminal cleanup. |
| `companion_v01/execution_resource_runtime/` | Resource contracts and path normalization (`contracts`), input staging and project snapshots (`staging`), bounded/idempotent artifact registration (`registration`), and shared bridge state, run admission and cleanup ownership (`bridge`). See [execution_resources_split_v1.md](execution_resources_split_v1.md). |
| `companion_v01/execution_local.py` | Stable host-local execution entry: `TrustedLocalExecutor`, `ExecutionPathError`, existing execution contracts and output/Windows parsing helpers. |
| `companion_v01/local_execution_runtime/` | Shared provider state and command admission (`executor`), cwd/mount authority (`paths`), host environment and probes (`environment`), Windows command language (`shell`), confirmed process lifecycle (`process`), and ordered capture/persisted output (`output`). See [execution_local_split_v1.md](execution_local_split_v1.md). |
| `companion_v01/project_workspace.py` | Stable project service entry: `ProjectWorkspaceService`, ownership/error models and existing read/write/patch constants. |
| `companion_v01/project_workspace_runtime/` | Shared service and lock (`service`), contracts (`definitions`), project ownership and selections (`catalog`), filesystem authority (`paths`), bounded inspection (`inspection`), atomic writes and patch rollback (`writing`), and patch grammar/context matching (`patching`). See [project_workspace_split_v1.md](project_workspace_split_v1.md). |
| `companion_v01/browser_page_runtime.py` | Stable managed-browser entry: `BrowserPageResult`, `ManagedBrowserPageRunner`, `ManagedBrowserSessionManager` and existing cache/evidence exports. |
| `companion_v01/managed_browser/` | Runner state and composition (`runner`), session isolation (`sessions`), owning thread and browser events (`lifecycle`), immutable snapshots (`reading`), link/element evidence (`candidates`), bound controls (`actions`), recovery/batches (`execution`), observation receipts (`observing`) and result contracts (`models`). See [browser_page_runtime_split_v1.md](browser_page_runtime_split_v1.md). |
| `companion_v01/capability_registry.py` | Compatibility facade for tool contracts, frozen turn records and `CapabilityRegistry`; preserves the existing `ExecutorBroker` exports. |
| `companion_v01/capability_registry_runtime/` | Domain contracts (`memory_contracts`, `browser_contracts`, `attachment_contracts`, `workspace_contracts`, `delivery_contracts`, `media_contracts`); `models` holds frozen records, `context` classifies materials, `modules` defines ordered profiles, `offers` probes server-local handlers, and `selection` composes stable schemas and executable offers. See [capability_registry_split_v1.md](capability_registry_split_v1.md). |
| `companion_v01/artifact_system.py` | `ArtifactContainerService`. Manages artifact containers for complex outputs. |

#### Desktop Pet Backend

| File | Responsibility |
|------|---------------|
| `companion_v01/desktop_pet_contract.py` | **Contract constants.** `DESKTOP_PET_CONTRACT_VERSION`, `DESKTOP_PET_DEFAULT_OUTFIT`, `DESKTOP_PET_DEFAULT_EMOTION`, capability and endpoint lists. `build_desktop_pet_diagnostics_payload` — assembles the diagnostics response from engine state. `decorate_resource_manifest_for_desktop_pet` — decorates resource manifest with client-specific fields. |
| `companion_v01/desktop_pet_engine.py` | Desktop-pet-specific engine functions: audio attachment ingest, workspace panel building, local path import, file URL decoration. Workspace audio dedup: `dedupe_desktop_workspace_audio_attachment_cards()` (ready-priority, origin-name+file-size+file-ext matching), `find_existing_desktop_audio_attachment_duplicate()`. |
| `companion_v01/desktop_context_engine.py` | Desktop context gathering: active window, clipboard (safely, no content in snapshot), file system events. |
| `desktop_pet_next/src/screen-observation.js` | Direct temporal screen input: bounded client buffer, ordered frames/contact sheet plus latest frame. Current `/think` uses the normal character/vision turn. Legacy summary workspace deleted; see `desktop_screen_observation_direct_v1.md`. |
| `companion_v01/desktop_music_timeline.py` | `DesktopMusicTimelineService`. Music timeline management: queue, lyrics, progress, mode. |
| `companion_v01/vision_service.py` | Vision model service: image analysis, description generation. |
| `companion_v01/vision_observation_router.py` | Routes vision observations to the right handler (screen vision, gift images, etc.). |
| `companion_v01/visual_context_engine.py` | Visual context building: assembles visual scene descriptions for prompts. |
| `companion_v01/final_output_engine.py` | Final output formatting: handles the last step before delivering to client (speech segments, TTS markup, file delivery). |
| `companion_v01/output_adapters.py` | `OutputAdapterRegistry`. Adapters that transform engine output to client-specific formats. |

#### QQ

| File | Responsibility |
|------|---------------|
| `companion_v01/channels/qq/` | QQ protocol, inbound events, turn adapters, commands, card rendering and delivery. See the [QQ development map](../companion_v01/channels/qq/README.md) for file responsibilities, data flow and tests. |
| `companion_v01/routes/qq.py` | QQ HTTP authentication, request parsing and responses; delegates to the channel's assembled event handler. |

#### Infrastructure

| File | Responsibility |
|------|---------------|
| `companion_v01/background_tasks.py` | `BackgroundTaskRunner`. Periodic task scheduling: memory compaction, vector reindexing, workspace cleanup. |
| `companion_v01/host_jobs.py` | Stable durable job entry: `HostJob`, `HostJobOwner`, `HostJobStore` and lifecycle constants; all job runtimes retain the shared instance database. |
| `companion_v01/host_job_runtime/` | Immutable contracts and JSON validation (`models`), shared SQLite store/schema (`store`), idempotent admission and claims (`admission`), control/revocation/results (`lifecycle`), checkpoints and explicit recovery (`recovery`), and notification identities/receipts (`completion`). See [host_jobs_split_v1.md](host_jobs_split_v1.md). |
| `companion_v01/host_tool_jobs.py` | Stable long-tool runtime entry: `HostToolJobRuntime`, tool-job identity and existing artifact/result helper exports. |
| `companion_v01/host_tool_job_runtime/` | Shared runner, lock, handler ownership and recovery (`runtime`), handler admission and durable registration (`admission`), actual execution/cancellation/settlement (`execution`), terminal notification scheduling and receipts (`completion`), and safe snapshots plus artifact/result projections (`contracts`). See [host_tool_jobs_split_v1.md](host_tool_jobs_split_v1.md). |
| `companion_v01/plugin_generation.py` | Stable managed plugin process entry: protocol constants, process and QQ broker exports, and the legacy worker command alias. |
| `companion_v01/plugin_generation_process/` | Shared process state, snapshots, port bindings and QQ composition (`process`), prepare/activate/drain/terminate (`lifecycle`), JSON control lane and request correlation (`transport`), invocation scopes and artifact handoff (`invocation`), Hook/QQ dispatch (`dispatch`), and validated declarations plus public failures (`contracts`). See [plugin_generation_split_v1.md](plugin_generation_split_v1.md). |
| `companion_v01/plugin_active_generation.py` | Stable active-generation entry: validated snapshots, frozen scopes, capability bindings, publication and Bot-facing event/Hook/QQ brokers. |
| `companion_v01/active_generation_runtime/` | Read-only candidate snapshots and indexes (`snapshot`), frozen scopes and process leases (`bindings`), serialized publication/withdrawal and truthful cleanup (`lifecycle`), capability/event/Hook/QQ routing (`dispatch`), and one shared active runtime plus stable bridges (`runtime`). See [plugin_active_generation_split_v1.md](plugin_active_generation_split_v1.md). |
| `companion_v01/plugin_generation_callbacks.py` | Stable reverse callback entry: the host router and worker capability/event/task/connection/resource/notification adapters, with historical exports preserved. |
| `companion_v01/plugin_callback_runtime/` | Shared routing state, admission and wire replies (`router`), invocation authority and release (`scopes`), real host port calls (`host_ports`), atomic scheduling/cancellation and fallback-loop cleanup (`scheduling`), and worker requests plus result decoding (`worker_ports`). See [plugin_generation_callbacks_split_v1.md](plugin_generation_callbacks_split_v1.md). |
| `companion_v01/plugin_installation.py` | Stable artifact-store entry: wheel/source staging, dependency supply, exact permission publication, selected release resolution and last-good rollback. Historical imports remain available. |
| `companion_v01/plugin_artifact_runtime/` | One shared store and lock (`store`), artifact contracts (`contracts`), safe locators/inventory/atomic I/O (`storage`), wheel/source/test staging (`staging`), isolated installation and preparation (`installation`), dependency preflight and public diagnostics (`dependencies`), and committed publication plus retained/retryable release cleanup (`publication`). See [plugin_installation_split_v1.md](plugin_installation_split_v1.md). |
| `companion_v01/extension_management.py` | Stable instance plugin-management entry: selection storage, safe catalog, reviewed installation, enablement, restart, rollback, uninstall and diagnostics. Historical imports remain available. |
| `companion_v01/extension_management_runtime/` | One service and operation lock (`service`), contracts and restoration proof (`contracts`), atomic selection/binding overlays (`selections`), public inventory and connection configuration (`inventory`), lifecycle and reconciliation (`lifecycle`), installation/restore transactions (`installation`), and market/staging/cancellation settlement plus sync diagnostics (`operations`). See [extension_management_split_v1.md](extension_management_split_v1.md). |
| `companion_v01/voice_runtime/durable_ports.py` | Stable durable voice entry: SQLite event journal, projection outbox, command receipts, text artifacts and synthesized audio artifacts. Historical imports remain available. |
| `companion_v01/voice_runtime/durable_storage/` | One journal identity and lock (`journal`), existing schema and integrity checks (`schema`), atomic event/projection publication and replay (`events`), command claims and durable observations (`commands`), immutable text/audio envelopes (`artifacts`), result contracts (`contracts`), and namespaces/canonical records/atomic file publication (`storage`). See [voice_durable_ports_split_v1.md](voice_durable_ports_split_v1.md). |
| `companion_v01/voice_runtime/host.py` | Stable voice Host entry: the owning conversation Host, typed outcomes and journal/command/projection protocols. Historical imports remain available. |
| `companion_v01/voice_runtime/host_runtime/` | One authoritative snapshot and persist-before-advance dispatch (`runtime`), result/port contracts (`contracts`), command validation and one-layer driving (`commands`), durable observation recovery (`receipts`), ordered projection delivery/acknowledgement (`projections`), and structured effect port calls (`ports`). See [voice_host_split_v1.md](voice_host_split_v1.md). |
| `companion_v01/voice_runtime/thinking_agent.py` | Stable thinking executor entry: generation start outcomes, immutable jobs, the owning executor and recovery event factory. Historical imports remain available. |
| `companion_v01/voice_runtime/thinking_runtime/` | One job/callback registry and cancellation lock (`executor`), turn/candidate contexts and recovery events (`context`), durable generation/cancellation observations (`commands`), background admission (`scheduling`), restart recovery (`recovery`), and model stream consumption/terminal settlement (`stream`). See [voice_thinking_split_v1.md](voice_thinking_split_v1.md). |
| `companion_v01/voice_runtime/tts_executor.py` | Stable TTS executor entry: command routing, text/audio port protocols and the owning synthesis executor. Historical imports remain available. |
| `companion_v01/voice_runtime/tts_runtime/` | One executor state and cancellation lock (`executor`), command routing/ports/lineage (`commands`), bounded background admission and Host completion (`scheduling`), provider synthesis and immutable audio publication (`synthesis`), and deterministic observations plus durable audio recovery (`results`). See [voice_tts_split_v1.md](voice_tts_split_v1.md). |
| `companion_v01/voice_runtime/playback_delivery.py` | Stable playback delivery entry: audio/control requests, the owning live channel, client acknowledgements and Host command execution/recovery. Historical imports remain available. |
| `companion_v01/voice_runtime/playback_runtime/` | One channel state and lock (`channel`), request/result contracts and validation helpers (`contracts`), audio offers and outbound queue (`delivery`), enqueue/start/terminal acknowledgements (`receipts`), duck/resume/stop offers and client receipts (`controls`), notifications/presentation/outcomes/retryable close settlement (`lifecycle`), and command routing plus orphan recovery (`executor`). See [voice_playback_delivery_split_v1.md](voice_playback_delivery_split_v1.md). |
| `companion_v01/voice_runtime/production.py` | Stable production voice entry: the owning service, continuous ASR call, event factory and typed MemCore projection. Historical imports remain available. |
| `companion_v01/voice_runtime/production_runtime/` | One service state/cache/lock (`service`), continuous call and turn context (`sessions`), serialized Host construction and recovery wiring (`assembly`), scoped ingress and actual turn/channel construction (`coordination`), pending command recovery and response startup (`recovery`), owning shutdown and channel release (`lifecycle`), and production events plus typed memory projection (`events`). See [voice_production_split_v1.md](voice_production_split_v1.md). |
| `companion_v01/public_guard.py` | `PublicThinkGuard`. Rate limits, concurrent think limits, daily quotas for public-facing clients. |
| `companion_v01/text_utils.py` | Text normalization, truncation, Unicode handling. |
| `companion_v01/memcore_integration/compaction.py` | MemCore maintenance admission and asynchronous compaction. The retired host summary queue is no longer part of the runtime. |
| `companion_v01/media_bridge_engine.py` | Media bridge: audio separation, format conversion, demucs integration. |

### External Services Layer

| File | Responsibility |
|------|---------------|
| `services/llm_client.py` | Low-level HTTP client for LLM API calls (OpenAI-compatible). |
| `services/tts_client.py` | `EdgeTTSClient`. Microsoft Edge TTS synthesis client. |

---

## Layer 2: Tauri Desktop Shell (`desktop_pet_next/`)

Built with Tauri v2 + Vite. Entry: `index.html` → `src/main.js`.

### Shell Entry Points

| File | Responsibility |
|------|---------------|
| `index.html` | Main pet window HTML. Mounts `#app`, loads `main.js`. |
| `control-center-lab.html` | Sole production control center HTML. Mounts `#app`, loads `src/control-center-v2/index.js`. |
| `control-center-v2.html` | Compatibility redirect to `control-center-lab.html`; preserves query/hash and owns no renderer. |
| `settings.html` | Compatibility redirect to `control-center-lab.html`; owns no settings behavior. |
| `workspace.html` | Workspace (hand-side items) window. Loads `workspace.js`. |
| `gomoku.html` | Shared-state Gomoku window; [complete integration reference](../examples/desktop_features/gomoku/README.md). |
| `vite.config.js` | HTML entries for the pet, control center, scene, panel, shop, workshop, workspace and Gomoku, plus compatibility redirects. |

### Core Desktop (Pet Window)

| File | Responsibility |
|------|---------------|
| `src/main.js` | Thin entry: native platform ports, bundled assets, `createPetApplication`, start. |
| `src/pet/application.js` | Composes the existing controllers and owns application startup. |
| `src/pet/chat.js` | Normal chat/stream/tool turn and final delivery; reuse it to involve the character in a feature. |
| `src/pet/settings.js`, `state.js` | Settings command handling/snapshots and current client state. |
| `src/pet/plugin-service.js`, `scope.js` | Program service binding using client identity and stale-response rejection. |
| `src/pet/gomoku.js`, `src/gomoku.js` | Main-window game/chat orchestration and child-window board rendering. |
| `src/character-profile.js` | Character identity, pack selection, outfit/emotion management. `buildCharacterSnapshot`, `selectCharacterPack`, `getActiveCharacterPackId`. |
| `src/visual-renderer.js` | `createVisualRenderer`. Character rendering: sprite positioning, emotion switching, visual effects, scale/opacity. |
| `src/workspace.js` | Six-line workspace window entry. Composes the native host and application. |
| `src/workspace/` | Workspace lifecycle and scoped refresh, native/HTTP transport, project management, file actions, item/music rendering and event cleanup. See [workspace_window_split_v1.md](workspace_window_split_v1.md) for ownership and checks. |
| `src/styles.css` | Main pet window styles. |
| `src/workspace.css` | Workspace window styles. |

### Control Center (Production V2)

| File | Responsibility |
|------|---------------|
| `control-center-lab.html` | Canonical settings window, loads `src/control-center-v2/index.js` directly. HTML redirects remain for `settings.html` and `control-center-v2.html`; no legacy JavaScript adapter. |
| `src/control-center-v2/` | Production component/store/bridge implementation. Seven destinations: Overview, Chat, Character & Appearance, Voice & Wake, Model Service, Abilities & Permissions, System & Diagnostics. Uses real backend/Tauri state, observed action confirmation, honest empty/failure states, presentation preferences, and Bot rebinding. |
| `src/control-center-v2/styles.css` | Production control-center styles, themes, wide/narrow layouts, reduced motion, framing, and action states. |
| `src/control-center/action-router.js` | Stable production action IDs and dispatch: registered V2 handler → production data source → structured `not-implemented`. |
| `src/control-center/data-sources.js` | Single production backend source. Reads the unified snapshot with individual-endpoint compatibility fallback, projects runtime domains for V2, and routes actions to Tauri/settings/window/dedicated backend boundaries. |
| `src/control-center-v2/app/events.js` | Production event binding and action payloads, delegated to page controllers. |

### Tauri Backend (Rust)

| File | Responsibility |
|------|---------------|
| `src-tauri/src/main.rs` | Native command registration and application entry; implementations live in the corresponding modules. |
| `src-tauri/src/pet_windows/` | Window commands, lifecycle, placement; `docked.rs` shares the panel/Gomoku native shell. |
| `src-tauri/Cargo.toml` | Rust dependencies. Tauri v2, window APIs, file system. |
| `src-tauri/tauri.conf.json` | Tauri startup window, development URL, embedded frontend and security configuration. |
| `src-tauri/capabilities/*.json` | Window labels and permissions for close, drag and cross-window events. |

---

## Layer 3: Verification & Scripts

### Control Center Verification Matrix

Run order: `npm run verify:control-center` in `desktop_pet_next/`.

| Script | What It Verifies |
|--------|-----------------|
| `desktop_pet_next/scripts/control-center-action-bridge-smoke.mjs` | 90 production actions, 27 event actions, 9 invokes and 14 backend routes. Checks dispatch and action classifications. `chat.new` has been retired. |
| `desktop_pet_next/scripts/control-center-runtime-probe.mjs` | **7 scenarios**: production-shaped snapshot patches all 7 pages, partial degradation, bad snapshot fallback, all-unavailable null, action contract inert, surface contract consistency, bootstrap contract (source metadata). |
| `desktop_pet_next/scripts/control-center-ux-smoke.mjs` | Built artifacts exist, HTML structure valid, CSS has scroll/disabled rules, built JS has window chrome action IDs + nav labels. Screenshots (Puppeteer optional). |
| `desktop_pet_next/scripts/control-center-verify.mjs` | **Orchestrator.** Runs all checks in order: required files → smoke actions → runtime probe → build → UX smoke. Exits non-zero on failure. |

| Script | Purpose |
|--------|---------|
| `desktop_pet_next/scripts/doctor.mjs` | Environment check: Node version, npm deps, Tauri CLI. |
| `scripts/check_desktop_feature.py` | Read-only page, Tauri, conventional controller, bridge-event and UI/panel wiring checks; build freshness; optional plugin contribution and running-EXE checks. Plugin catalog output does not prove installed source-code identity. |
| `desktop_pet_next/scripts/start-next.ps1` | Guarded release launcher for the Tauri desktop pet; rebuilds when the release exe is missing, stale, or `-Rebuild` is passed. |
| `desktop_pet_next/scripts/tauri-with-cargo-path.ps1` | Tauri CLI wrapper with cargo PATH setup. |

### Python Test Suite

| File | Tests |
|------|-------|
| `tests/test_backend_route_modules.py` | **30 tests.** Snapshot endpoint (contract shape, real providers, provider failure, sensitive content, resource manifest drive), action endpoint (not-implemented, catalog, window.close, unknown action, non-object payload, empty body), metrics/log non-blocking. |
| `tests/test_desktop_pet_backend_contract.py` | Desktop pet contract: diagnostics payload, resource manifest decoration, workspace panel. |
| `tests/test_desktop_pet_frontend_contract.py` | Frontend contract: data flow from backend to frontend data sources. |
| `tests/test_control_center_*.py` | (See `test_backend_route_modules.py` — control center tests are consolidated there.) |
| `tests/test_engine_visible_context.py` | LLM visible context: prompt assembly, memory injection, persona rendering. |
| `tests/test_prompt_builder.py` | Prompt builder: all prompt blocks, mode profiles. |
| `tests/test_memcore_integration.py` | MemCore Unified Timeline V2 integration, tools, and context surfaces. |
| `tests/test_vector_store.py` | Vector store: CRUD, search, batch operations. |
| `tests/test_memory_compaction.py` | (In `test_engine_visible_context.py` and related tests.) |
| `tests/test_repository_hygiene.py` | Repo hygiene: no unused imports, no broken references. |
| `tests/quick_regression_suite.py` | Quick smoke for critical paths. |
| Other `tests/test_*.py` | Each maps 1:1 to a companion_v01 module (e.g., `test_gift_system.py` → `gift_system.py`). |

---

## Layer 4: Other Directories

### Retired frontends

The Electron client (`desktop_pet/`) and backend-served Web chat/preview pages have been removed. `desktop_pet_next/` is the sole desktop implementation. `web/assets/` retains shared art used by resource manifests, the shop and scenes; it is not another client. Browser automation and Vite/Playwright development checks remain active. See [frontend cleanup preparation](frontend_cleanup_preparation_v1.md).

### `desktop_pet_creator_kit/`

Character pack creation toolkit. Contains character asset templates, scripts, and docs for creating custom character packs.

### `docs/`

Design documents and specifications. Key reference docs:

| Document | Topic |
|----------|-------|
| `DEVELOPMENT.md` | Current task entry, identity meanings, feature wiring and relevant verification. |
| `control-center-lab-contract.md` | **Primary reference for the new control center.** Backend contract, runtime completeness table, action surface classification, bootstrap contract, verification matrix. |
| `desktop_pet_v01.md` | Original desktop pet design doc. |
| `desktop_activity_runtime_v0.md` | Desktop activity perception design. |
| `desktop_live2d_risk_assessment_v1.md` | Live2D integration risk analysis. |
| `character_engine_blueprint_v1.md` | Character engine architecture. |
| `persona_evolution_system_v1.md` | Persona evolution design. |
| `layered_memory_design.md` | Layered memory architecture. |
| `capability_registry_v1.md` | Capability registry design. |
| `client_mode_capability_boundaries_v1.md` | Client mode isolation design. |
| `PROJECT_MAP.md` | **This document.** |
| Other `docs/*.md` | Feature-specific design docs. |

### `deploy/`

Deployment configuration: nginx, systemd, environment templates.

### `documents/`

User-facing docs: navigation guide, vision novel framework draft, memory design plan.

---

## Quick Reference: Common Tasks

### "I want to add an interactive desktop feature"

Read [the complete Gomoku reference](../examples/desktop_features/gomoku/README.md), then
run `.venv/Scripts/python -B scripts/check_desktop_feature.py gomoku`. The reference names every registration
point and separates browser preview, SDK tests and native/model acceptance. The diagnostic checks the
controller import/composition/bridge, paired event references, and UI/panel opening command; it does not
simulate native window behavior.

### "I want to add a model tool"

For an ordinary plugin, start with [SDK function tools](../akane_plugin/README.md) and
[the executable math plugin](../examples/plugins/akane_sdk_math/); import only `akane_plugin`.
For a built-in host tool, use `capability_registry_runtime/*_contracts.py` (or its local handler spec),
then the matching `tool_handlers/` implementation. Follow parse → prepare → execute in
`engine_services/tool_protocol.py` and `tool_orchestration/`. Use the task tests named in
[DEVELOPMENT](DEVELOPMENT.md#改工具参数或新增插件工具).

### "UI and the model should share business state"

Use the Gomoku example: `src/pet/plugin-service.js` calls a versioned service from the UI;
the plugin's `@plugin.tool` calls the same business state from the model. UI-to-model participation
uses the normal `chat.sendMessage` path. Program services do not start a model request.

### "I changed code but the running feature did not change"

Plugin source goes through `stage_source` → review → `install` → active; a catalog confirms state and
contribution declarations, not code identity. Use the installation digest and activation result as
deployment evidence. Frontend changes need `npm run build`; Tauri/native changes need a release build;
update the desktop instance's EXE and restart the desktop separately from restarting the backend.

### "Which configuration value wins?"

Read `bot_runtime_components/factory.py:_load_runtime_settings` and `runtime_settings.py:BotSettingsView`.
Saved per-Bot overrides, `model_service.json`, factory overrides and the QQ session `chat_model_override`
have distinct owners and overlay points. `settings-catalog.current` is the current config-view value,
not the final value for every specialized service or request.

### "I want to add a new settings action"

Follow the canonical [Settings Action Development Guide](desktop_pet_control_center_v2/README.md#settings-action-development-guide) for action declarations, routing, execution, receipts, and the relevant checks. It distinguishes main-window commands, direct native calls, and backend actions; derived mappings do not need manual registration.

### "I want to understand how a page gets its data"

1. Backend: `companion_v01/routes/control_center.py` → provider functions (`_build_snapshot_*`)
2. Frontend: `desktop_pet_next/src/control-center/data-sources.js` → `build*RuntimePatch` functions
3. Store/view model: `desktop_pet_next/src/control-center-v2/store.js` and `view-model.js`
4. Render: `desktop_pet_next/src/control-center-v2/components/*`

### "I want to add a new page to the control center"

1. `desktop_pet_next/src/control-center-v2/store.js` — define the page-owned state
2. `desktop_pet_next/src/control-center-v2/view-model.js` — project runtime facts for the page
3. `desktop_pet_next/src/control-center-v2/components/` — add the component and navigation entry
4. `desktop_pet_next/src/control-center/data-sources.js` — add only the runtime fields with a real owner
5. Add V2 smoke coverage; do not create mock-first page data

### "The backend crash on startup"

1. `config.py` — check environment variables
2. `companion_v01/app.py` — check which import fails
3. `companion_v01/engine.py` — which service init fails

### "The control center does not show runtime data"

1. Check `desktop_pet_next/src/control-center-v2/bridge.js` — did backend and Tauri snapshots arrive?
2. Check `desktop_pet_next/src/control-center/data-sources.js` → `createBackendControlCenterSource` → `readSnapshot` — which field is unavailable?
3. Use the active instance's backend URL and Bot route prefix to GET `/control-center/snapshot?user_id=test&client=desktop_pet`; read the actual port from launcher status/configuration rather than assuming a fixed port.
4. Check `companion_v01/routes/control_center.py` → `build_control_center_snapshot_runtime_providers` — is the provider for the missing field returning real data?

### "A button looks clickable but does nothing"

1. Find its `data-action-id` in `desktop_pet_next/src/control-center-v2/components/`.
2. Check whether it is local presentation state or a production action in `action-router.js`.
3. For production actions, follow `data-sources.js` → settings command / Tauri invoke / window API / dedicated backend route.
4. If there is no real execution boundary, remove the false affordance instead of returning fake success.
5. Use `npm run smoke:control-center-actions` to verify production bridge mappings.

### "Tauri won't start / window won't open"

1. `desktop_pet_next/src-tauri/src/main.rs` — command registration; `pet_windows/` owns window creation
2. `desktop_pet_next/src-tauri/capabilities/*.json` — window labels and permissions
3. `desktop_pet_next/src-tauri/Cargo.toml` — Tauri version, feature flags

### "LLM response is wrong / bad quality"

1. `companion_v01/prompt_builder.py` — what blocks are in the prompt?
2. `companion_v01/prompt_profiles.py` — which profile is active?
3. `companion_v01/mode_profiles.py` — which mode selects the profile?
4. `companion_v01/persona_config.py` — `PERSONA` base definition
5. `companion_v01/llm_runtime.py` — which model, what parameters?
6. `config.py` — model selection flags

---
