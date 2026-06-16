# Akane Companion Lab — Project Map

This document maps every source file to its responsibility, organized by layer and subsystem.
Use it to find which file to read when you need to understand or modify a specific area.

Last updated: 2026-05-04

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│  Tauri Desktop Shell (desktop_pet_next)                          │
│  ┌─────────────┐  ┌────────────────┐  ┌──────────────────────┐  │
│  │ main.js      │  │ control-center-lab.js / data-sources.js       │  │
│  │ (pet window) │  │ (old settings) │  │ (new CC, beta)       │  │
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
| `companion_v01/app.py` | FastAPI app construction. Creates `AkaneMemoryEngine`, wires all routers (`core`, `think`, `voice`, `qq`, `gifts`, `reminders`, `sessions`, `system`, `desktop_pet`, `control_center`, `web_static`), starts tracemalloc, mounts CORS, defines `RuntimeMetrics` class. |
| `config.py` | Pydantic `Settings` object. Defines ALL runtime knobs: `RUN_MODE`, embedding model/device/cache, memory compaction thresholds, prompt cache settings, summary/compact batch sizes, all LLM selection knobs, file limits. **One file to change for any behavioral flag.** |

### Core Orchestration

| File | Responsibility |
|------|---------------|
| `companion_v01/engine.py` | `AkaneMemoryEngine` — the central orchestrator. Owns EVERY domain service: `LLMRuntime`, `PromptBuilder`, `RetrievalService`, `BackgroundTaskRunner`, `GiftSystemService`, `CapabilityRegistry`, `TaskWorkspaceService`, `TaskWorkerService`, `PersonaCardService`, `StickerAssetService`, `ResourceManifest`, `GeneratedFileService`, `VectorStore`, `MemoryCompactionService`, `DesktopMusicTimelineService`, `DesktopScreenVisionWorkspace`, mode profiles, tool orchestration, output adapters, etc. When you need to know "what owns what", read the `__init__` of this class. |
| `companion_v01/client_protocol.py` | `ClientMode` and `ClientProtocolContext`. Defines capability boundaries per client (`desktop_pet`, `qq`, `web_static`). Data model for client capability declarations. |

### Routing Layer (`companion_v01/routes/`)

Each file builds a FastAPI `APIRouter` with a `build_*_router()` function.

| File | Routes | Key Responsibility |
|------|--------|-------------------|
| `routes/core.py` | `/resource-manifest`, `/capabilities`, `/character-packs` | Resource manifest + capability listing |
| `routes/think.py` | `/think`, `/think_once` | Think endpoints: dialogue, streaming, tool orchestration. Also `build_think_response_stream`. |
| `routes/desktop_pet.py` | `/desktop-pet/health`, `/desktop-pet/diagnostics`, `/desktop-pet/workspace/summary`, `/desktop-pet/workspace/*`, `/desktop-pet/music/*`, `/desktop-pet/screen-vision/*`, `/desktop-pet/vision-observation` | All desktop-pet-specific health, diagnostics, workspace panel, music timeline, screen vision. |
| `routes/voice.py` | `/tts`, `/asr` | TTS synthesis, ASR recognition |
| `routes/gifts.py` | `/gifts/*` | Gift asset upload, listing, delivery |
| `routes/qq.py` | `/qq/*` | QQ gateway (NapCat) integration |
| `routes/sessions.py` | `/sessions/*` | Session management |
| `routes/reminders.py` | `/reminders/*` | Reminder CRUD |
| `routes/system.py` | `/metrics`, `/health`, `/system/*` | System health, prometheus metrics, admin |
| `routes/control_center.py` | `GET /control-center/snapshot`, `GET/POST /control-center/actions` | **Control Center backend contract.** `build_control_center_snapshot_runtime_providers` aggregates 5 real providers (health, diagnostics, workspace, resourceManifest, metrics). Action endpoint is inert — always returns `not-implemented`. |
| `routes/web_static.py` | Static file serving | Serves the static web frontend (old settings, character creator kit preview) |

### Domain Services

Each file owns a specific business domain. Most are classes instantiated by `AkaneMemoryEngine`.

#### Memory & Retrieval

| File | Responsibility |
|------|---------------|
| `companion_v01/memory_compaction_service.py` | `MemoryCompactionService`. Compacts dialogue history into episodic summaries when thresholds are hit. |
| `companion_v01/memory_rendering.py` | `render_semantic_summary_timeline`, `render_summary_timeline`. Formats memory for prompt injection. |
| `companion_v01/retrieval_engine.py` | Retrieval pipeline orchestration: query expansion, multi-stage retrieval, fusion. |
| `companion_v01/retrieval_service.py` | `RetrievalService`. High-level retrieval API consumed by `engine.py`. |
| `companion_v01/retrieval_types.py` | `RetrievalPipelineResult` and related data types. |
| `companion_v01/retrieval_eval_benchmark.py` | Retrieval evaluation benchmark runner. |
| `companion_v01/retrieval_eval_dataset.py` | Retrieval evaluation dataset loader. |
| `companion_v01/embedding_provider.py` | `BaseEmbeddingProvider`, `HashedEmbeddingProvider`, `CachedEmbeddingProvider`. Embedding abstraction layer. |
| `companion_v01/huggingface_provider.py` | `HuggingFaceEmbeddingProvider` — concrete HuggingFace implementation. |
| `companion_v01/vector_entry_builder.py` | Builds vector store entries from dialogue/thoughts. |
| `companion_v01/vector_store.py` | `VectorStore`. Persistent vector database for semantic retrieval. |
| `companion_v01/store.py` | SQLite-backed memory/resource store. Chat messages, sessions, episodic summaries, semantic summaries, eval turns, and vector metadata can be scoped by `character_pack_id` for desktop-pet character isolation; music/gift resources remain profile-scoped. |

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
| `companion_v01/generated_files_delivery.py` | Delivery logic: how generated files are sent to the client. |
| `companion_v01/generated_files_io.py` | File I/O helpers for generated files. |
| `companion_v01/generated_files_media.py` | Media-specific handling for generated files. |

#### Gifts, Tasks & Tools

| File | Responsibility |
|------|---------------|
| `companion_v01/gift_engine.py` | Gift creation, decoration, and delivery pipeline. |
| `companion_v01/gift_assets.py` | Gift asset storage: images, stickers, custom art. |
| `companion_v01/gift_system.py` | `GiftSystemService`. Gift lifecycle management. |
| `companion_v01/sticker_assets.py` | `StickerAssetService`. Sticker collection for gifts. |
| `companion_v01/task_worker.py` | `TaskWorkerService`. Executes delegate tasks: file ops, media processing. |
| `companion_v01/task_worker_tool.py` | `DelegateTaskToolHandler`. Tool-call handler for task delegation from LLM. |
| `companion_v01/task_workspace.py` | `TaskWorkspaceService`. Manages per-task workspaces with files, outputs, logs. |
| `companion_v01/task_workspace_engine.py` | Workspace creation/cleanup engine. |
| `companion_v01/tool_orchestration_engine.py` | Tool orchestration: routing, capability selection, tool execution pipeline. |
| `companion_v01/tool_runtime.py` | Individual tool execution runtime. |
| `companion_v01/capability_registry.py` | `CapabilityRegistry`. Declares and resolves tool capabilities per client mode. |
| `companion_v01/artifact_system.py` | `ArtifactContainerService`. Manages artifact containers for complex outputs. |

#### Desktop Pet Backend

| File | Responsibility |
|------|---------------|
| `companion_v01/desktop_pet_contract.py` | **Contract constants.** `DESKTOP_PET_CONTRACT_VERSION`, `DESKTOP_PET_DEFAULT_OUTFIT`, `DESKTOP_PET_DEFAULT_EMOTION`, capability and endpoint lists. `build_desktop_pet_diagnostics_payload` — assembles the diagnostics response from engine state. `decorate_resource_manifest_for_desktop_pet` — decorates resource manifest with client-specific fields. |
| `companion_v01/desktop_pet_engine.py` | Desktop-pet-specific engine functions: audio attachment ingest, workspace panel building, local path import, file URL decoration. Workspace audio dedup: `dedupe_desktop_workspace_audio_attachment_cards()` (ready-priority, origin-name+file-size+file-ext matching), `find_existing_desktop_audio_attachment_duplicate()`. |
| `companion_v01/desktop_context_engine.py` | Desktop context gathering: active window, clipboard (safely, no content in snapshot), file system events. |
| `companion_v01/desktop_screen_vision.py` | `DesktopScreenVisionWorkspace`. Screen capture pipeline: frame capture, diff detection, OCR, vision model observation. |
| `companion_v01/desktop_music_timeline.py` | `DesktopMusicTimelineService`. Music timeline management: queue, lyrics, progress, mode. |
| `companion_v01/vision_service.py` | Vision model service: image analysis, description generation. |
| `companion_v01/vision_observation_router.py` | Routes vision observations to the right handler (screen vision, gift images, etc.). |
| `companion_v01/visual_context_engine.py` | Visual context building: assembles visual scene descriptions for prompts. |
| `companion_v01/final_output_engine.py` | Final output formatting: handles the last step before delivering to client (speech segments, TTS markup, file delivery). |
| `companion_v01/output_adapters.py` | `OutputAdapterRegistry`. Adapters that transform engine output to client-specific formats. |

#### QQ & Reminders

| File | Responsibility |
|------|---------------|
| `companion_v01/qq_gateway.py` | `NapCatQQGateway`. QQ message send/receive via NapCat HTTP API. |
| `companion_v01/reminder_engine.py` | `ReminderEngine`. Scheduled reminders: create, persist, fire, deliver. |

#### Infrastructure

| File | Responsibility |
|------|---------------|
| `companion_v01/background_tasks.py` | `BackgroundTaskRunner`. Periodic task scheduling: memory compaction, vector reindexing, workspace cleanup. |
| `companion_v01/public_guard.py` | `PublicThinkGuard`. Rate limits, concurrent think limits, daily quotas for public-facing clients. |
| `companion_v01/text_utils.py` | Text normalization, truncation, Unicode handling. |
| `companion_v01/summary_queue.py` | `SummaryQueue`. Batches dialogue turns for periodic summarization. |
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
| `control-center-lab.html` | Default control center HTML. Mounts `#app`, loads `control-center-lab.js`. |
| `settings.html` | Compatibility redirect to `control-center-lab.html`; owns no settings behavior. |
| `workspace.html` | Workspace (hand-side items) window. Loads `workspace.js`. |
| `vite.config.js` | Vite build configuration. Multi-page: index, settings, workspace, control-center-lab. |

### Core Desktop (Pet Window)

| File | Responsibility |
|------|---------------|
| `src/main.js` | **The pet main process (~5400 lines).** Contains: window management, visual renderer, chat pipeline, settings command handler (`handleSettingsCommand` — 40+ commands), desktop context polling, screen vision pipeline, music player, voice I/O (TTS/ASR), session management, `buildSettingsSnapshot`, `broadcastSettingsSnapshot`. Music recommendations pipeline: `buildMusicRecommendationsSnapshot()`, `dedupeMusicRecommendations()`, `refreshWorkspaceMusicRecommendations()`, `scheduleWorkspaceMusicRecommendationsRefresh()`. Idempotent workspace audio playback via `findMusicQueueIndexByWorkspaceAudio()` + `playSourceIdAfterAdd`. `scheduleSettingsSnapshot()` emits after `setPetEmotion()` for live emotion sync. The central nervous system of the desktop app. |
| `src/character-profile.js` | Character identity, pack selection, outfit/emotion management. `buildCharacterSnapshot`, `selectCharacterPack`, `getActiveCharacterPackId`. |
| `src/visual-renderer.js` | `createVisualRenderer`. Character rendering: sprite positioning, emotion switching, visual effects, scale/opacity. |
| `src/workspace.js` | Workspace window UI. Shows hand-side files, outputs, task list. |
| `src/styles.css` | Main pet window styles. |
| `src/workspace.css` | Workspace window styles. |

### Control Center Lab (New, Beta)

| File | Responsibility |
|------|---------------|
| `src/control-center-lab.js` | **New control center (~2270 lines).** Full SPA with 7 pages (Overview, Character, Voice, Music, Perception, Abilities, Advanced). Initializes from mock data, then hydrates from backend snapshot + Tauri `load_pet_state`. Manages action routing, local optimistic state, scroll positions, page navigation. Features: action feedback (onAfterAction → snapshot refresh), live emotion preview sync (normalizeEmotionCards from manifest), recent outputs card, music recommendations projection. |
| `src/control-center-lab.css` | Control center styles (~5400 lines). Layout, glass cards, sky background, media queries. |
| `src/control-center/action-router.js` | `CONTROL_CENTER_ACTIONS` (77 action ID constants), `CONTROL_CENTER_BRIDGED_ACTION_IDS` (44 bridged), `createControlCenterActionRouter` — action dispatch: registered handler → dataSource → not-implemented. |
| `src/control-center/action-surface-contract.js` | Machine-readable catalog: every action ID classified as `bridged` (44), `client-handled` (3), or `deferred` (31). Exports `getUncataloguedBridgedActionIds()`. |
| `src/control-center/data-sources.js` | Data source factory: `createMockControlCenterSource`, `createTauriControlCenterSource`, `createBackendControlCenterSource`. Unified snapshot pipeline (`tryReadUnifiedSnapshot`), individual endpoint fallback. 8 `build*RuntimePatch` functions (health, overview, character, voice, perception, music, advanced, recentOutputs). Prometheus metrics parser. Action execution via `runTauriControlCenterAction`. Emotion card resolution from manifest via `normalizeEmotionCards`/`findManifestEntry`. |
| `src/control-center/data-adapter.js` | `createControlCenterSnapshot(raw)` — normalizes raw source data into typed `ControlCenterSnapshot`. 7 `adapt*Page` functions, `patchRowsByLabel`, label-based patching for overview/advanced. |
| `src/control-center/mock-data.js` | Default mock data for ALL 7 pages. Fallback when backend is unavailable. |
| `src/control-center/action-helpers.js` | `createControlCenterActionPayloadFromDataset` — converts `data-*` attributes to structured action payloads. `secondsFromIntervalLabel`. |
| `src/control-center/snapshot-schema.js` | `CONTROL_CENTER_SCHEMA_VERSION`, `CONTROL_CENTER_PAGE_IDS`, `isKnownControlCenterPage`. |

### Tauri Backend (Rust)

| File | Responsibility |
|------|---------------|
| `src-tauri/src/main.rs` | Tauri app entry. Window creation (pet, control center, workspace). `load_pet_state` command and the single `control-center-lab.html` settings route. Desktop context snapshot provider. |
| `src-tauri/Cargo.toml` | Rust dependencies. Tauri v2, window APIs, file system. |
| `src-tauri/tauri.conf.json` | Tauri configuration: window labels, permissions, security policies. |

---

## Layer 3: Verification & Scripts

### Control Center Verification Matrix

Run order: `npm run verify:control-center` in `desktop_pet_next/`.

| Script | What It Verifies |
|--------|-----------------|
| `desktop_pet_next/scripts/control-center-action-bridge-smoke.mjs` | **40 bridged actions** emit/invoke to correct Tauri commands. 31 deferred surfaces are not bridged. 3 client-handled return `refresh:false` with no emit. Surface contract consistency. 77 action IDs all classified. |
| `desktop_pet_next/scripts/control-center-runtime-probe.mjs` | **7 scenarios**: production-shaped snapshot patches all 7 pages, partial degradation, bad snapshot fallback, all-unavailable null, action contract inert, surface contract consistency, bootstrap contract (source metadata). |
| `desktop_pet_next/scripts/control-center-ux-smoke.mjs` | Built artifacts exist, HTML structure valid, CSS has scroll/disabled rules, built JS has window chrome action IDs + nav labels. Screenshots (Puppeteer optional). |
| `desktop_pet_next/scripts/control-center-verify.mjs` | **Orchestrator.** Runs all checks in order: required files → smoke actions → runtime probe → build → UX smoke. Exits non-zero on failure. |

| Script | Purpose |
|--------|---------|
| `desktop_pet_next/scripts/doctor.mjs` | Environment check: Node version, npm deps, Tauri CLI. |
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
| `tests/test_retrieval_service.py` | Retrieval pipeline: query expansion, fusion, ranking. |
| `tests/test_vector_store.py` | Vector store: CRUD, search, batch operations. |
| `tests/test_memory_compaction.py` | (In `test_engine_visible_context.py` and related tests.) |
| `tests/test_repository_hygiene.py` | Repo hygiene: no unused imports, no broken references. |
| `tests/quick_regression_suite.py` | Quick smoke for critical paths. |
| Other `tests/test_*.py` | Each maps 1:1 to a companion_v01 module (e.g., `test_gift_system.py` → `gift_system.py`). |

---

## Layer 4: Other Directories

### `desktop_pet/` (Old Electron Version)

Electron-based prototype. Not maintained. The Tauri version (`desktop_pet_next/`) is the active one.

### `desktop_pet_creator_kit/`

Character pack creation toolkit. Contains character asset templates, scripts, and docs for creating custom character packs.

### `docs/`

Design documents and specifications. Key reference docs:

| Document | Topic |
|----------|-------|
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

### "I want to add a new settings command"

1. `desktop_pet_next/src/main.js` — add case to `handleSettingsCommand`, state field to `DEFAULT_STATE`/`normalizeState`/`buildSettingsSnapshot`
2. `desktop_pet_next/src/control-center/data-sources.js` — add to `settingsCommandByActionId`
3. `desktop_pet_next/src/control-center/action-router.js` — add to `CONTROL_CENTER_BRIDGED_ACTION_IDS`
4. `desktop_pet_next/src/control-center/action-surface-contract.js` — change from `deferred` to `bridged`
5. `desktop_pet_next/scripts/control-center-action-bridge-smoke.mjs` — add test case
6. `docs/control-center-lab-contract.md` — update count/docs

### "I want to understand how a page gets its data"

1. Backend: `companion_v01/routes/control_center.py` → provider functions (`_build_snapshot_*`)
2. Frontend: `desktop_pet_next/src/control-center/data-sources.js` → `build*RuntimePatch` functions
3. Adapter: `desktop_pet_next/src/control-center/data-adapter.js` → `adapt*Page` functions
4. Render: `desktop_pet_next/src/control-center-lab.js` → `render*Page` functions

### "I want to add a new page to the control center"

1. `desktop_pet_next/src/control-center/mock-data.js` — add mock page data
2. `desktop_pet_next/src/control-center/data-sources.js` — add `build*RuntimePatch` for runtime data
3. `desktop_pet_next/src/control-center/data-adapter.js` — add `adapt*Page`
4. `desktop_pet_next/src/control-center/snapshot-schema.js` — register page ID
5. `desktop_pet_next/src/control-center-lab.js` — add `render*Page`, navigation item, state

### "The backend crash on startup"

1. `config.py` — check environment variables
2. `companion_v01/app.py` — check which import fails
3. `companion_v01/engine.py` — which service init fails

### "The control center shows mock data but not real data"

1. Check `desktop_pet_next/src/control-center-lab.js` → `hydrateControlCenterSnapshot` — is TauriRuntime available?
2. Check `desktop_pet_next/src/control-center/data-sources.js` → `createBackendControlCenterSource` → `readSnapshot` — which step returns null?
3. Check backend: `curl http://127.0.0.1:9999/control-center/snapshot?user_id=test&client=desktop_pet`
4. Check `companion_v01/routes/control_center.py` → `build_control_center_snapshot_runtime_providers` — are all 5 providers returning real data?

### "A button looks clickable but does nothing"

1. Find its `data-action-id` in `desktop_pet_next/src/control-center-lab.js`
2. Check `desktop_pet_next/src/control-center/action-surface-contract.js` — is it `bridged`, `client-handled`, or `deferred`?
3. If `bridged`: check `desktop_pet_next/src/control-center/data-sources.js` → `settingsCommandByActionId` / `tauriInvokeByActionId` → check `main.js` → `handleSettingsCommand` for the command handler
4. If `deferred`: it's intentionally disabled. Read the `reason` string in the surface contract.
5. Use `npm run smoke:control-center-actions` to verify all bridge mappings.

### "Tauri won't start / window won't open"

1. `desktop_pet_next/src-tauri/src/main.rs` — window creation logic
2. `desktop_pet_next/src-tauri/tauri.conf.json` — window labels, permissions
3. `desktop_pet_next/src-tauri/Cargo.toml` — Tauri version, feature flags

### "LLM response is wrong / bad quality"

1. `companion_v01/prompt_builder.py` — what blocks are in the prompt?
2. `companion_v01/prompt_profiles.py` — which profile is active?
3. `companion_v01/mode_profiles.py` — which mode selects the profile?
4. `companion_v01/persona_config.py` — `PERSONA` base definition
5. `companion_v01/llm_runtime.py` — which model, what parameters?
6. `config.py` — model selection flags

---

## File Count Summary

| Layer | Files | Lines (approx) |
|-------|-------|-----------|
| Python Backend (companion_v01) | ~55 .py | ~80,000 |
| Python Services | 2 .py | ~500 |
| Python Config | 1 .py | ~200 |
| Python Tests | ~45 .py | ~12,000 |
| Tauri Frontend (desktop_pet_next/src) | ~12 .js + 4 .css | ~12,000 |
| Control Center (src/control-center) | 7 .js | ~3,500 |
| Verification Scripts | 4 .mjs | ~2,000 |
| Rust (src-tauri) | 1 .rs | ~2,000 |
| Docs | ~45 .md | ~50,000 |
| **Total** | **~175 source files** | **~160,000** |
