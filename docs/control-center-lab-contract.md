# Akane Control Center Lab Data Contract

This document describes the backend data boundary for `desktop_pet_next/control-center-lab.html`.

## Default Entry

The new control center (`control-center-lab.html`) is the default settings entry
(see `src-tauri/src/main.rs` / `settings_window_url()`). The historical
`settings.html` path is a compatibility redirect to the control center and does
not own any settings behavior.

The lab starts from `src/control-center/mock-data.js`, then hydrates supported fields through the data-source and adapter layer. The page is always rendered with mock data first, then upgraded with real runtime data when available. Backend failure does not block navigation or window chrome buttons.

Current adapter files:

- `src/control-center/snapshot-schema.js`: front-end snapshot ids, version, and JSDoc contract.
- `src/control-center/data-sources.js`: mock/backend data source boundary.
- `src/control-center/data-adapter.js`: normalizes source data into `ControlCenterSnapshot`.
- `src/control-center/action-router.js`: routes user-facing action ids without coupling buttons to backend APIs.
- `src/control-center/action-surface-contract.js`: machine-readable action surface catalog for bridged and deferred controls.

Current real-data slice:

- `control-center-lab.html` uses the backend data source by default and falls back to mock data if the backend is unavailable.
- The overview page hydrates from existing endpoints: `/health`, `/desktop-pet/diagnostics`, `/desktop-pet/workspace/summary`, `/metrics`, and `/resource-manifest`. Its voice TTS/ASR toggles reuse the voice action bridge (`voice.setTtsEnabled`, `voice.setAsrEnabled`), and its desktop sensing toggles reuse the perception action bridge (`perception.desktopContext.setEnabled`, `perception.clipboardContext.setEnabled`, `perception.screenVision.setEnabled`, `perception.proactiveWake.setEnabled`). These overview controls update optimistically before the bridge result. The overview emotion card resolves the current expression image from `resourceManifest` via `normalizeEmotionCards()` — the `emotion.image` field falls back through `image → url → key → emotionId`. The overview now also renders a "最近成果" card sourced from `workspace.outputs` (up to 3 non-empty items).
- The character page hydrates from `/resource-manifest`, plus Tauri `load_pet_state` / `list_character_packs` when running inside the desktop app. `character.openPackFolder`, `character.refresh`, `character.previewEmotion`, `character.selectPack`, and `character.setOutfit` are bridged (settings-command: `setCharacterPack` and `setOutfit`). Outfit tiles preserve local optimistic preview and route `character.setOutfit` on click. Expression browsing, outfit management, and resource repair are registered in the action surface contract as deferred (`character.manageOutfits`, `character.moreExpressions`, `character.resourceRepair`). `character.importZip`, `character.apply`, and `character.restoreDefaults` remain deferred, awaiting file picker, apply semantics, and confirmation semantics. The resource warning action defaults to `character.refresh` but can be overridden by runtime as `character.resourceRepair`.
- The voice page hydrates from `/health`, `/desktop-pet/diagnostics`, optional `/capabilities`, and Tauri `load_pet_state`: `tts.enabled`, `tts.volume`, `tts.speed`, `asr.enabled`, `wakeWord`, `wakeSensitivity`, provider status rows, and diagnostics rows (`整体状态`, `TTS 语音引擎`, `ASR 语音引擎`, `响应延迟`, `网络状态`) are derived from real runtime fields. When `/capabilities` is available, `resolutions.voice.tts.character` and `resolutions.voice.input.asr` drive compact TTS/ASR provider status: active provider, requested provider, fallback provider, degraded state, and safe reason labels. The `/tts` runtime can call a configured loopback GPT-SoVITS-compatible API for character voice requests, then falls back to Edge TTS on missing profile, missing config, unreachable provider, or synthesis failure. GPT-SoVITS model/profile file management is not exposed in the voice page yet; V1 only binds an external local API endpoint and uses safe provider headers for observability. The `voice.test`, `voice.stop`, `voice.previewPlay`, `voice.setSpeed`, `voice.setWakeWord`, and `voice.setWakeSensitivity` buttons now route through the action bridge; in Tauri they emit settings commands `testTts`, `stopTts`, `previewTts`, `setVoiceSpeed`, `setWakeWord`, and `setWakeSensitivity`. TTS enabled, ASR enabled, and voice volume now route through `voice.setTtsEnabled`, `voice.setAsrEnabled`, and `voice.setVolume`, which emit `setVoiceEnabled`, `setVoiceInputEnabled`, and `setVoiceVolume`; the UI updates optimistically before the bridge result. Additional voice configuration controls (`voice.selectTtsVoice`, `voice.selectAsrDevice`, `voice.setAsrLanguage`, `voice.records.clear`, `voice.queue.clear`) are registered in the action surface contract as deferred — they have stable action IDs and `data-action-id` wiring but no real settings command yet. They return `not-implemented` at the router boundary. `voice.setAsrSensitivity` is display-only — it shows the sensitivity value as plain text without `data-action-id`. `music.selectOutputDevice` is similarly display-only in the music page footer.
- The perception page hydrates from Tauri `load_pet_state` and `/desktop-pet/diagnostics`: the four feature cards (`activeWindow`, `clipboard`, `screen`, `proactive`) get their `enabled` state, interval display strings, and frame count from `petState` fields (`desktopContextEnabled`, `clipboardContextEnabled`, `screenVisionEnabled`, `screenVisionIntervalSec`, `screenVisionFrameCount`, `proactiveWakeEnabled`, `proactiveWakeIntervalSec`). The clipboard card only shows capability status placeholders and does not read clipboard content. Local switch toggles, screen-vision interval/frame controls, screen-vision clear action, proactive-wake interval changes, and diagnostics refresh route through the action bridge: `perception.desktopContext.setEnabled`, `perception.clipboardContext.setEnabled`, `perception.screenVision.setEnabled`, `perception.screenVision.setIntervalSec`, `perception.screenVision.setFrameCount`, `perception.screenVision.clear`, `perception.proactiveWake.setEnabled`, `perception.proactiveWake.setIntervalSec`, and `perception.runDiagnostics` emit the respective settings commands with the new boolean/numeric value or `requestSnapshot`. The UI updates optimistically before the bridge result. `perception.activeWindow.details` is client-handled — it toggles an expanded detail view on the active-window card, with `refresh:false` and no backend or Tauri boundary. Navigation and management actions (`perception.privacyHelp`, `perception.managePermissions`, `perception.clipboard.clear`, `perception.events.viewAll`, `perception.suggestion.run`) are registered in the action surface contract as deferred — they have stable action IDs and `data-action-id` wiring but no real backend or Tauri commands yet.
- The music page hydrates from the desktop pet `akane-next-settings-snapshot.music` via Tauri `SETTINGS_SNAPSHOT_EVENT`: current track display name, queue, progress/duration, lyric summary (previous/current/next lines), queue position, playing/paused state, musicPlayMode, volumeNormalization, recommendations, and bottom status bar. Volume is read from `petState.voiceVolume`. System media awareness adds a compact `systemMedia` / `systemLyrics` runtime patch for the external track reported by Windows SMTC; the page may show `System music: Ready` plus `Lyrics: Found / Not found / Disabled / Unavailable`, but it must not render raw track keys, cache paths, or full lyric bodies. The adapter replaces `nowPlaying`, `playlist`, `lyrics`, `activeLyric`, `info`, `recommendations`, and `bottomStatus` fields when a snapshot is available. Button actions (previous, next, pause, stop, clear, seek, queue item selection, setPlayMode, setVolumeNormalization, and playWorkspaceRecommendation) now route through the action bridge; in Tauri they emit the existing settings command event, and outside Tauri they return a structured backend or `not-implemented` result. The music page volume bar reuses `voice.setVolume` because the current desktop runtime has one shared audio volume. Additional music controls (`music.setMood`, `music.refreshRecommendations`, `music.selectOutputDevice`) are registered in the action surface contract as deferred — they have stable action IDs and `data-action-id` wiring but no real execution boundary yet. They return `not-implemented` at the router boundary.
- Verify bridged action mappings with `cd desktop_pet_next && npm run smoke:control-center-actions`.
- Verify unified snapshot data pipeline (hydration, degradation, fallback, inert action contract) with `cd desktop_pet_next && npm run probe:control-center-runtime`.
- Run the full control-center verification matrix (required-file gate + smoke + probe + build) with `cd desktop_pet_next && npm run verify:control-center`.

| Check | Command (from desktop_pet_next) | Scope |
|-------|--------------------------------|-------|
| Action bridge | `npm run smoke:control-center-actions` | Action bridge mappings, `not-implemented` contract, exception hardening, surface contract consistency |
| Runtime probe | `npm run probe:control-center-runtime` | Unified snapshot happy-path, partial degradation, fallback, all-unavailable null, action inertness |
| Full matrix | `npm run verify:control-center` | Required-file existence gate + smoke actions + runtime probe + build |
| Backend routes | `python -m unittest tests.test_backend_route_modules` (from repo root) | Snapshot endpoint, action endpoint, provider resilience, sensitive content, provider reality check (5 providers), action inertness |
| Whitespace | `git diff --check` (from repo root) | Whitespace and syntax hygiene |

- The backend exposes `GET /control-center/actions` as a contract discovery endpoint, `POST /control-center/actions/{actionId}` as a structured action contract endpoint, and `GET /control-center/snapshot` as a unified runtime data endpoint. The first POST version returns `{ ok: false, status: "not-implemented", actionId, refresh: false }` for every action — it does not execute client-side operations. `GET /control-center/snapshot` returns `{ ok, status, schemaVersion, sourceKind, generatedAt, runtime: { health, diagnostics, workspace, resourceManifest, metrics } }` and aggregates real production providers in `companion_v01/routes/control_center.py` / `build_control_center_snapshot_runtime_providers`:
  - **health**: `_build_snapshot_health` reads `config_module`, returns status, pid, python, contracts
  - **diagnostics**: `_build_snapshot_diagnostics` calls `build_desktop_pet_diagnostics_payload` with engine, runtime_metrics, public_guard
  - **workspace**: `_build_snapshot_workspace` calls `engine.build_desktop_pet_workspace_panel` in async thread
  - **resourceManifest**: `_build_snapshot_resource_manifest` calls `engine.build_resource_manifest` and decorates for desktop_pet
  - **metrics**: `_build_snapshot_metrics_text` aggregates tracemalloc, llm snapshot, vector_store count, reindex status, public_guard state, and runtime counters into prometheus text format
Individual provider failures return `{ ok: false, status: "unavailable" }` without causing a 500. The frontend attempts the snapshot endpoint first with the same session/client/character query parameters used by the legacy endpoints, and falls back to the individual per-endpoint requests (`/health`, `/desktop-pet/diagnostics`, etc.) when the snapshot is unavailable or all sub-fields fail. The Tauri `SETTINGS_SNAPSHOT_EVENT` handles high-frequency desktop state (music progress, petState) and is not a replacement for the backend snapshot — these two data paths are independent and merge at the adapter layer.
- Control-center buttons are routed through `src/control-center/action-router.js`. Bridged action ids include `chat.new`, `chat.stop`, `workspace.open`, voice actions (`voice.test`, `voice.stop`, `voice.setTtsEnabled`, `voice.setAsrEnabled`, `voice.setVolume`, `voice.previewPlay`, `voice.setSpeed`, `voice.setWakeWord`, `voice.setWakeSensitivity`), `character.openPackFolder`, `character.refresh`, `character.previewEmotion`, `character.selectPack`, `character.setOutfit`, perception settings (`perception.desktopContext.setEnabled`, `perception.clipboardContext.setEnabled`, `perception.screenVision.setEnabled`, `perception.screenVision.setIntervalSec`, `perception.screenVision.setFrameCount`, `perception.screenVision.clear`, `perception.proactiveWake.setEnabled`, `perception.proactiveWake.setIntervalSec`, `perception.runDiagnostics`), window control actions (`window.close`, `window.minimize`, `window.maximize`), advanced run/core operations (`advanced.probeClickThrough`, `advanced.resetWindow`, `advanced.toggleWebgl`, `advanced.setHitTestEnabled`, `advanced.setHitboxOverlay`), music control actions (`music.previous`, `music.next`, `music.pause`, `music.stop`, `music.clear`, `music.seek`, `music.selectQueueItem`, `music.setPlayMode`, `music.setVolumeNormalization`, `music.playWorkspaceRecommendation`), provider config/test/profile actions (`abilities.provider.config.save`, `abilities.provider.healthCheck`, `abilities.provider.ttsTest`, `abilities.provider.voiceProfile.inspectFolder`, `abilities.provider.voiceProfile.save`, `abilities.provider.voiceProfile.assignToCurrentCharacter`, `abilities.provider.voiceProfile.clearCurrentCharacter`), MCP config/discovery actions (`abilities.mcp.config.save`, `abilities.mcp.discover`), approval policy action (`abilities.approvalPolicy.save`), and workflow binding/file actions (`abilities.workflow.config.save`, `abilities.workflow.file.import`, `abilities.workflow.validate`). All bridged actions flow through `dataSource.runAction(actionId, payload)` before reaching backend HTTP or Tauri. Provider config/test/profile actions use dedicated backend routes (`/capabilities/providers/{providerId}/config`, `/capabilities/providers/{providerId}/health-check`, `/capabilities/providers/{providerId}/tts-test`, `/capabilities/providers/{providerId}/voice-profiles/inspect-folder`, and `/capabilities/providers/{providerId}/voice-profiles/{voiceProfileId}/config`), while `abilities.provider.voiceProfile.assignToCurrentCharacter` uses Tauri command `set_character_voice_profile` and `abilities.provider.voiceProfile.clearCurrentCharacter` uses Tauri command `clear_character_voice_profile` to atomically update the current character pack `voice` field. MCP actions use dedicated backend routes (`/capabilities/mcp-servers/{serverId}/config` and `/capabilities/mcp-servers/{serverId}/discover`), approval policy uses `/capabilities/approval-policy`, and workflow actions use dedicated backend routes (`/capabilities/workflows/{workflowId}/config`, `/capabilities/workflows/{workflowId}/file`, and `/capabilities/workflows/{workflowId}/validate`); these families do not fall through to the inert `/control-center/actions/{actionId}` endpoint. Backend workflow preflight/job routes also exist at `/capabilities/workflows/{workflowId}/preflight`, `/capabilities/workflows/{workflowId}/jobs`, `/capabilities/workflow-jobs/{jobId}`, and `/capabilities/workflow-jobs/{jobId}/outputs/{outputHandle}`, but they are not control-center button actions yet. In normal production the app binds a ComfyUI workflow runner for `workflow.workshop.portrait.cutout`; execution still happens only through explicit workflow job routes after provider/workflow config checks, and public job status exposes safe handles rather than image bytes. The output byte route is profile-scoped and only serves completed internal assets. `window.close` invokes the Tauri command `close_window`; `window.minimize` and `window.maximize` use the Tauri window API (minimize / toggleMaximize) via injected bridge or dynamic import. Window actions are client-only and return `not-implemented` when Tauri is unavailable instead of falling back to backend HTTP. Perception toggles/diagnostics emit settings commands with boolean/numeric payload or `requestSnapshot`; voice, character refresh/preview, advanced core actions, and most music actions emit settings commands; `music.playWorkspaceRecommendation` emits `playWorkspaceAudio` with only `{ itemType, handle, title }`; `character.openPackFolder`, `workspace.open`, and current-character voice assignment/clear invoke Tauri commands. `window.notify` remains not-implemented — it has no stable real boundary yet. Other prototype buttons keep the mock/noop fallback until their real boundary is defined.
- The abilities page hydrates from `/desktop-pet/diagnostics`, `/desktop-pet/workspace/summary`, optional `/capabilities`, optional `/capabilities/voice-profiles`, and optional `/capabilities/approval-requests`: backend tool/provider/workflow names are mapped into user-facing modules, and the page updates summary stats, module cards, compact local provider rows, workflow status/config cards, GPT-SoVITS voice profile summaries, MCP server summaries/config panels, recent status rows, safety state, profile approval policy, pending approval count, and Live2D reserved state. MCP servers and discovered MCP tools are visible through `/capabilities`: configured servers appear as `provider.mcp.{serverId}`, discovered tools appear as `kind: "mcp_tool"` with `exposedToPrompt:false`, and no full command path, cwd, args, env value, or secret is exposed. The backend binds a minimal stdio MCP discoverer for `/capabilities/mcp-servers/{serverId}/discover`; discovery performs only `initialize` and `tools/list`, never `tools/call`. A separate minimal MCP caller exists only behind the built-in `web_search` tool for the configured `anysearch` server, allowing read-only public `search` / `batch_search` / `extract` / `get_sub_domains` calls with size limits, public URL checks, and secret/path redaction. The built-in `browser_page` tool is also catalogued as a desktop-browser capability: it opens/operates an Akane-managed visible public browser window via `navigate` / `read_text` / `current` / `snapshot` / `scroll` / `elements`, returning visible link/video candidates plus viewport-filtered accessibility snapshots with element refs for follow-up actions, and high-risk `click` / `fill` / `press` execute only after the profile policy is `trusted_auto_allow` or a future approval checker allows the action. Candidate-based clicks use a 1-based `candidate_index` from the current visible snapshot and navigate directly to the resolved public URL. It does not control the user's already-open system browser tabs. Without that authorization, control actions return `capability_approval_required` and do not touch the page. The tool reports `missing_executor` when Playwright is unavailable and is not a control-center button action. The abilities safety panel shows the current approval mode and pending request count, and exposes a two-mode profile policy switch: `ask_each_time` ("请求批准") and `trusted_auto_allow` ("完全访问"). Saving the switch routes through `abilities.approvalPolicy.save` to `POST /capabilities/approval-policy`; the frontend sends only `defaultMode`, updates optimistically, and refreshes the runtime catalog after the result. "完全访问" changes public catalog metadata for ready high-risk/confirmation-required entries to `approvalMode: "trusted_auto_allow"` but does not bypass URL/path/secret/local-boundary validation, missing config states, or unavailable runners. The frontend renders MCP servers in a separate "外部 MCP 工具" panel with tool counts, broad capability labels, prompt exposure state, and risk/confirmation hints. Expanding a row is client-handled (`abilities.mcp.config.open`); saving config (`abilities.mcp.config.save`) and discovering tools (`abilities.mcp.discover`) are real backend-route actions. Saved command/cwd/args/env values are intentionally not echoed back in the UI; editing an existing server requires re-entering private command details. MCP providers are not rendered as localhost endpoint config rows, and there are no MCP execution buttons. It intentionally does not render raw tool names or workflow slot ids in the collapsed dashboard. The first workflow catalog entry is `workshop.portrait.cutout`, shown as "透明背景处理" with conservative status such as "未配置", "待绑定", or "已绑定"; expanding the row exposes a small workflow binding form for safe relative workflow reference plus input/output slot labels, plus "导入 JSON" for a ComfyUI API workflow export. Importing saves the JSON into the current profile's capability directory through `/capabilities/workflows/{workflowId}/file`, returns only the safe relative workflow path, and never executes ComfyUI. It is still not executable from the control center. The GPT-SoVITS provider targets any external GPT-SoVITS-compatible API — it does not bundle or imply a built-in TTS service. The provider row exposes a five-step configuration wizard, a compact short-test control, a compact voice profile form, and a current-character voice status pill. The wizard (`resolveGptSoVitsWizardStep`) derives the active step from live provider state: step 1 — no endpoint saved; step 2 — endpoint saved but health check failed; step 3 — healthy but no voice profile saved; step 4 — voice profile exists but not bound to the current character pack; step 5 — current character pack is bound. Each step shows a short status label; no `refAudioPath` or `promptText` value is rendered in any wizard step or status chip. The short-test action posts localhost endpoint, test text, optional `voiceProfileId`, and unsaved profile request fields to `/capabilities/providers/{providerId}/tts-test`, receives a bounded base64 audio sample, attempts local playback, and always renders a small audio player when audio is available so browser autoplay blocking does not make the action look inert; it does not persist anything or auto-enable the provider. The voice profile folder inspect action posts only a user-entered local model folder path to `/capabilities/providers/{providerId}/voice-profiles/inspect-folder`, scans for `tts_infer.yaml`, reference audio, `.ckpt`, and `.pth` files, and returns a private `suggestedProfile` for form fill; it does not save, load weights, or auto-enable the provider. The voice profile save action posts safe profile fields to `/capabilities/providers/{providerId}/voice-profiles/{voiceProfileId}/config`; private `refAudioPath` and `promptText` are stored only in the profile-scoped capability config, while public catalog responses expose only `referenceAudioName` and `promptTextLength`. The current-character assignment action calls Tauri `set_character_voice_profile` with only the current pack id, provider, and safe profile id, then refreshes the runtime character packs; the current-character clear action calls Tauri `clear_character_voice_profile` with only the current pack id and restores default TTS fallback. Neither action changes saved private model paths or loads external weights. `abilities.logs.viewAll` is client-handled — it toggles between showing the first 3 call rows and the full call history, with `refresh:false` and no backend or Tauri boundary. `abilities.provider.config.open`, `abilities.mcp.config.open`, and `abilities.workflow.config.open` are also client-handled — they only expand/collapse one provider/MCP/workflow row. `abilities.provider.config.save`, `abilities.provider.healthCheck`, `abilities.provider.ttsTest`, `abilities.provider.voiceProfile.inspectFolder`, `abilities.provider.voiceProfile.save`, `abilities.provider.voiceProfile.assignToCurrentCharacter`, `abilities.provider.voiceProfile.clearCurrentCharacter`, `abilities.mcp.config.save`, `abilities.mcp.discover`, `abilities.approvalPolicy.save`, `abilities.workflow.config.save`, `abilities.workflow.file.import`, and `abilities.workflow.validate` are bridged actions for provider endpoint save/health check/short test/profile folder suggestion/profile save/current character voice assignment/default restore, MCP server config/discovery, approval policy save, workflow binding save, workflow JSON import, and config validation. Remaining navigation and management operations (`abilities.quickAction`, `abilities.manageModules`, `abilities.moreWorkflows`, `abilities.safety.details`, `abilities.live2d.openSettings`) are registered in the action surface contract as deferred — they have stable action IDs and `data-action-id` wiring but no real capability invocation or management commands yet.
- MCP Manager V1 is still a stdio-only productization slice. The panel provides AnySearch, Node/npx, Python, and custom stdio templates, a four-step setup guide, safe tool detail rows, and diagnostics chips. Clicking "保存并发现工具" first saves the current form to `/capabilities/mcp-servers/{serverId}/config`, then calls `/capabilities/mcp-servers/{serverId}/discover`; this prevents users from testing stale saved commands. HTTP / Streamable and SSE are displayed only as future transports, not as working controls.
- The character workshop (`workshop.html`) consumes the local workflow catalog in the portrait management tab. It reads `GET /capabilities/workflows`, finds `workflow.workshop.portrait.cutout`, and renders a compact "透明背景处理" status row with user-facing guidance such as "需要配置本地 ComfyUI", "需要绑定抠图工作流", or "已绑定，可以自动抠图". The read uses the workshop backend/profile boundary but a fixed non-chat `desktop` capability session, and is cached briefly so high-frequency desktop snapshots do not repeatedly hydrate capabilities. Failure to read the catalog degrades to "能力状态未同步" without blocking outfit creation, image import, preview, default portrait selection, or calibration. The "去配置" button opens the settings/control-center window. The "自动抠图" button is hidden unless the workflow status is `ready` and `executionReady:true`; `ready` requires the profile-scoped workflow JSON file to exist, parse, and contain the configured ComfyUI node `inputs` paths. When clicked it reads the currently previewed portrait through Tauri `read_portrait_image`, starts the backend workflow job with explicit image bytes, polls job status, fetches the completed output bytes, and writes a non-overwriting `<emotion>_cutout` portrait through Tauri `import_generated_portrait_image`. The backend never reads character-pack source files or writes generated character-pack assets directly.
- The advanced page hydrates from `/health`, `/desktop-pet/diagnostics`, `/metrics`, and `petState` via `buildAdvancedRuntimePatch`: the system strip shows real running/network state and attempts CPU/memory percent from prometheus metrics; diagnostics metrics patch `应用状态`, `后端健康`, and `内存占用` by label; diagnostics logs are replaced with a status sync timeline; ability overview is derived from `tool_names`; Live2D rows show reserved statuses. `advanced.probeClickThrough` and `advanced.resetWindow` are bridged (settings-command). Core toggles for WebGL, Hit-Test, and Hitbox route through `advanced.toggleWebgl`, `advanced.setHitTestEnabled`, and `advanced.setHitboxOverlay`. `advanced.logs.more` is client-handled — it toggles between showing the first 5 log entries and the full log list, with `refresh:false` and no backend or Tauri boundary. Log management, exit pet, expert options, Live2D status, and ability details (`advanced.logs.clear`, `advanced.exitPet`, `advanced.expertOption`, `advanced.live2d.openStatus`, `advanced.ability.details`) are registered in the action surface contract as deferred — they have stable action IDs and `data-action-id` wiring but no real execution boundary. `advanced.exitPet` requires explicit confirmation before any future binding and must not be mapped to `closePet` or `window.close`.
- Use `?source=mock` to force the static prototype, or `?backend=http://127.0.0.1:9999` to point the lab at another backend.
- `control-center-lab.html` is the only Tauri settings implementation. Stale
  `settings.html` links redirect to it while preserving query/hash values.
  Settings and workspace windows stay non-topmost and are not owned by the pet
  window, so the pet can keep its own always-on-top priority.
- The Tauri settings snapshot event is treated as a lightweight runtime patch for high-frequency music state. It must not trigger full backend hydration on every music progress update; full hydration remains reserved for initial load and explicit action refresh.
- Backend responses are converted into page runtime patches such as `overviewRuntime` and `characterRuntime`; render functions only consume `ControlCenterSnapshot` fields.

## Action Surface Classification

Every control-center action falls into one of three tiers. This classification is machine-readable via `src/control-center/action-surface-contract.js`.

### Bridged (57 actions)

Actions with a real execution boundary: a Tauri settings command, a Tauri invoke, a Tauri window API call, or a dedicated backend route. These are wired through `dataSource.runAction` before reaching their execution boundary. Buttons have `data-action-id` and are NOT disabled.

### Client-Handled (6 actions)

Actions handled entirely within `control-center-lab.js` via `router.registerHandlers`. They have no backend or Tauri boundary — they toggle local UI state and return `refresh:false`.

| Action ID | Page | Behavior |
|-----------|------|----------|
| `perception.activeWindow.details` | Perception | Toggles expanded detail view on the active-window card |
| `abilities.logs.viewAll` | Abilities | Toggles between first 3 call rows and full call history |
| `abilities.provider.config.open` | Abilities | Toggles the local provider configuration panel for one provider |
| `abilities.mcp.config.open` | Abilities | Toggles the local MCP server configuration panel |
| `abilities.workflow.config.open` | Abilities | Toggles the local workflow binding panel for one workflow |
| `advanced.logs.more` | Advanced | Toggles between first 5 log entries and full log list |

Client-handled actions are NOT in `CONTROL_CENTER_BRIDGED_ACTION_IDS`. Their buttons are enabled (not `data-action-unavailable`). Calling `dataSource.runAction` directly on them returns `not-implemented`. They never emit or invoke.

### Deferred / Disabled (31 surfaces)

Actions with stable action IDs but no real execution boundary. Buttons are rendered with `data-action-id` but are marked `data-action-unavailable="true"`, `aria-disabled="true"`, and `disabled` by `applyActionAvailability`.

These include: `character.importZip`, `character.apply`, `character.restoreDefaults`, `character.manageOutfits`, `character.moreExpressions`, `character.resourceRepair`, `voice.selectTtsVoice`, `voice.selectAsrDevice`, `voice.setAsrLanguage`, `voice.records.clear`, `voice.queue.clear`, `music.setMood`, `music.refreshRecommendations`, `perception.privacyHelp`, `perception.managePermissions`, `perception.clipboard.clear`, `perception.events.viewAll`, `perception.suggestion.run`, `abilities.quickAction`, `abilities.manageModules`, `abilities.moreWorkflows`, `abilities.safety.details`, `abilities.live2d.openSettings`, `advanced.logs.clear`, `advanced.exitPet`, `advanced.expertOption`, `advanced.live2d.openStatus`, `advanced.ability.details`, `window.notify`.

### Display-Only (no data-action-id)

Two deferred actions are rendered as display-only text without `data-action-id` — they show current state but are not buttons:

| Action ID | Element | Display |
|-----------|---------|---------|
| `voice.setAsrSensitivity` | `<strong>` in ASR card | Sensitivity percentage |
| `music.selectOutputDevice` | `<strong>` in music footer | Output device name |

### Deferred UX Audit — Complete

Every deferred action has been reviewed and classified:

- **No fake bridges.** Actions like `character.importZip`, `music.setMood`, `advanced.exitPet` remain deferred because there is no real boundary ready. Not a single "placeholder bridge" was added just to reduce the appearance of empty buttons.
- **Disabled by default.** `applyActionAvailability` marks all non-bridged, non-client-handled action buttons as `aria-disabled="true"`, `disabled`, and `data-action-unavailable="true"`. Users see the button but cannot click it.
- **Display-only downgrade.** Two deferred actions (`voice.setAsrSensitivity`, `music.selectOutputDevice`) were rendered as `<strong>` with `data-action-id`, making them look clickable. They were downgraded to plain `<strong>` with no `data-action-id`, removing the false affordance.
- **6 client-handled actions** provide real local behavior (expand/collapse UI sections) with `refresh:false` and no backend/Tauri boundary.
- **31 remaining deferred surfaces** are documented with reasons in `action-surface-contract.js`. No expiration date is set — they become bridged when the real boundary is ready, not before.

### Design Principle

**禁止为了减少摆设感而硬接假动作。** Do not bridge an action just to make the UI feel more complete. Every bridged action must have a real settings command handler in `main.js`, a real Tauri invoke, a real Tauri window API call, or a dedicated backend route. Mock fallbacks and noop stubs are acceptable as transitional states; fake execution (emitting a command that is silently ignored, or returning `{ ok: true }` without side effects) is not.

## Bootstrap Contract

How the control center acquires its backend URL, session, profile, and character-state parameters:

### Priority Chain (highest → lowest)

1. **URL query params** (`?backend=...`, `?session_id=...`, `?character_pack_id=...`, `?outfit=...`)
2. **Tauri `load_pet_state` invoke** — returns petState with `backendUrl`, `sessionId`, `profileUserId`, `characterPackId`, `outfit`, `currentEmotion`, plus all runtime flag fields
3. **localStorage** — `akane.controlCenter.backendUrl`, `akane.controlCenter.sessionId`, `akane.controlCenter.characterPackId` etc.
4. **DEFAULT_BACKEND_URL** (`http://127.0.0.1:9999`) and default session/profile (`"control-center-lab"`, `"master"`)

The `createControlCenterDataSourceOptions` function in `control-center-lab.js` implements this chain. The bootstrap fields that flow into every `readSnapshot()` call via `commonParams` are:

| Field | URL param | petState key | Default |
|-------|-----------|-------------|---------|
| backendUrl | `backend` / `backend_url` | `backendUrl` | `http://127.0.0.1:9999` |
| sessionId | `session_id` / `user_id` | `sessionId` | `control-center-lab` |
| profileUserId | `real_user_id` / `profile_user_id` | `profileUserId` | `master` |
| characterPackId | `character_pack_id` / `characterPackId` | `characterPackId` | empty string |
| outfit | `outfit` | `outfit` | empty string |
| emotion | `emotion` | `currentEmotion` | empty string |

When `characterPackId` is non-empty, chat-memory and session endpoints use it as the default memory scope. This isolates desktop-pet raw messages, episodic summaries, semantic summaries, eval-turn restore data, and vector retrieval per character. Music and gift resources stay scoped to `profileUserId` by default so the user's library remains shared across characters.

### Source Metadata

Every data source exposes metadata for observability:

- **`dataSource.kind`**: `"backend"` | `"mock"` | `"tauri"`
- **`dataSource.backendUrl`**: the resolved backend URL (or `null` for mock/tauri sources)
- **`dataSource.fallbackReason`**: `null` when the source is functioning, or a string reason (`"mock-source"`, `"unified-snapshot-unavailable"`, `"all-backend-endpoints-failed"`, `"no-fetch-impl"`)
- **`dataSource.getFallbackReason()`**: returns the most recent fallback reason after the last `readSnapshot()` call

When `readSnapshot()` returns `null` (all backends unavailable), the page does not white-screen: the initial mock snapshot remains displayed, `applyActionAvailability` keeps buttons in their correct disabled state, and nav/page switching works normally.

### Metadata Flow Through Snapshot

The `createControlCenterSnapshot()` function now passes through `backendUrl` and `fallbackReason` from the raw data source output. Every snapshot object includes:

```js
{
  sourceKind: "backend" | "mock" | "tauri",
  backendUrl: "http://127.0.0.1:9999" | null,
  fallbackReason: null | "unified-snapshot-unavailable" | "all-backend-endpoints-failed" | "..."
}
```

These fields are verified in scenario 1 (production-shaped snapshot) and scenario 2 (partial degradation) of the runtime probe. The smoke test also confirms all 94 action IDs in `CONTROL_CENTER_ACTIONS` are classified as bridged, client-handled, or deferred — no unclassified action IDs exist, and duplicate page surfaces must stay in the same status category.

### Button Status Audit

Every action ID in `CONTROL_CENTER_ACTIONS` is assigned to exactly one status category. Surface entries may repeat when the same action appears on multiple pages, for example music controls on both Overview and Music.

| Status | Count | Criteria |
|--------|-------|----------|
| **Bridged** | 57 unique action IDs / 66 surface entries | Real settings command, Tauri invoke, Tauri window API, or dedicated backend route |
| **Client-handled** | 6 | Local UI toggle, `refresh:false`, no backend/Tauri boundary |
| **Deferred/Disabled** | 31 | Stable ID, button disabled via `data-action-unavailable`, documented reason |
| **Unclassified** | 0 | Enforced by smoke test: `Object.values(CONTROL_CENTER_ACTIONS) ⊆ surface contract`, with exactly one status category per action ID |

`music.playWorkspaceRecommendation` is bridged in this batch as a client-only settings command. `abilities.provider.config.save`, `abilities.provider.healthCheck`, `abilities.provider.ttsTest`, `abilities.provider.voiceProfile.inspectFolder`, and `abilities.provider.voiceProfile.save` are bridged to dedicated provider routes, while `abilities.provider.voiceProfile.assignToCurrentCharacter` and `abilities.provider.voiceProfile.clearCurrentCharacter` are bridged to Tauri `set_character_voice_profile` / `clear_character_voice_profile`; `abilities.provider.config.open` is client-handled only. `abilities.provider.ttsTest` returns only a bounded short audio sample for local playback and does not persist voice profile/model fields. `abilities.provider.voiceProfile.inspectFolder` returns suggested form fields from a local GPT-SoVITS model folder and does not persist or load model weights. `abilities.provider.voiceProfile.save` persists profile-scoped GPT-SoVITS request fields but does not load model weights or switch external GPT-SoVITS models. `abilities.provider.voiceProfile.assignToCurrentCharacter` updates only the current character pack `voice` section with a safe provider/profile id; `abilities.provider.voiceProfile.clearCurrentCharacter` clears that preference back to the default voice fallback without deleting the saved profile. `abilities.mcp.config.save` and `abilities.mcp.discover` are bridged to dedicated MCP server routes; `abilities.mcp.config.open` is client-handled only. `abilities.approvalPolicy.save` is bridged to the profile approval policy route and sends only `defaultMode`. `abilities.workflow.file.import` is bridged to a dedicated workflow file route that saves user-imported JSON without executing the workflow. `voice.records.clear`, `voice.queue.clear`, and `advanced.logs.clear` remain deferred because `main.js` has no corresponding settings command handler.

### Probe Verification

Scenario 7 of `control-center-runtime-probe.mjs` verifies:
- Mock source has `kind: "mock"`, `backendUrl: null`, `fallbackReason: "mock-source"`
- Backend source has `kind: "backend"`, preserves configured `backendUrl`
- Default `backendUrl` starts with `http://`
- Unavailable backend: `readSnapshot()` returns `null`, `getFallbackReason()` returns a meaningful string
- Available backend: `readSnapshot()` returns data, `getFallbackReason()` returns `null` (cleared)
- Source metadata JSON contains no sensitive fields (`api_key`, `password`, `secret`, `token`)

## Runtime Completeness

The backend snapshot endpoint (`GET /control-center/snapshot`) uses **production providers** in `companion_v01/routes/control_center.py` / `build_control_center_snapshot_runtime_providers`. The five runtime fields (`health`, `diagnostics`, `workspace`, `resourceManifest`, `metrics`) aggregate real data from engine, config_module, runtime_metrics, public_guard, tracemalloc, llm, and vector_store. Individual provider failures degrade only the failing field.

Provider reality is enforced by `test_control_center_snapshot_providers_are_reality_not_placeholder` in `tests/test_backend_route_modules.py` — all 5 fields are checked for real data structure, none are allowed to return `{ ok: false, status: "unavailable" }` under normal conditions.

The **Tauri `SETTINGS_SNAPSHOT_EVENT`** provides an independent data path for high-frequency desktop state (music progress, petState, runtime status). It is not a replacement for the backend snapshot — these two sources merge at the adapter layer (`data-adapter.js`). When the backend snapshot returns data without `musicRuntime`, the mock music data passes through unchanged (not overwritten).

Fields driven by real backend data:

| Page | Runtime-driven fields | Source |
|------|----------------------|--------|
| Overview | `status.items`, `connection.rows`, `pack.name/version`, `emotion.name`/`emotion.image` (from manifest), `voice.enabled/status`, `sense.toggles.enabled`, `abilities` labels, `health` tiles (memory, storage, CPU, errors, alerts, contract version), `recentOutputs` (up to 3 items) | health, diagnostics, workspace, metrics, resourceManifest |
| Character | `hero`, `selectedPack`, `selectedPackId`, `packInfo`, `completeness`, `outfits`, `emotions`, `warning`, `resources` (when pack asset count available) | resourceManifest, diagnostics, petState |
| Voice | `tts.enabled/volume`, `tts.providerStatus`, `asr.enabled`, `asr.providerStatus`, `diagnostics` rows | health, diagnostics, capabilities catalog, petState |
| Perception | `featureCards[*].enabled`, screen-vision interval/frames, proactive interval; clipboard shows capability status only (no content) | petState, diagnostics |
| Music | `nowPlaying`, `playlist`, `lyrics`, `activeLyric`, `info`, `bottomStatus`, `currentPlayMode`, `outputDevice`, `volumeNormalization`, `recommendations` | Tauri music snapshot + petState (backend snapshot does NOT overwrite music) |
| Abilities | `overview.stats`, `availability`, `note`, `modules` (from tool names and capability catalog), `providers` (compact local provider status/config entry), `workflows` (read-only status cards from workflow catalog plus examples), `calls` (diagnostics-derived status rows), `safety` | diagnostics, workspace, capabilities catalog |
| Advanced | `systemStrip` (CPU/memory/network/status), `coreSettings` (webgl/hitTest/hitbox), `diagnostics.metrics` (应用状态, 后端健康, 内存占用), `diagnostics.logs`, `abilityOverview` (from tool names), `live2d` (reserved) | health, diagnostics, metrics, petState |

Fields that remain in mock data (no backend provider):

| Field | Reason |
|-------|--------|
| `voice.tts.voice`, `voice.tts.speed` | No backend voice catalog or speed config |
| `voice.asr.device`, `voice.asr.language`, `voice.asr.sensitivity` | No backend ASR device/language config |
| `voice.preview.text` | Voice preview is UI demonstration text |
| `voice.records`, `voice.queue` | Recognition records and synthesis queue are local (no backend) |
| `voice.processing` | Processing options are UI template |
| `voice.wakeWord`, `voice.wakeSensitivity` | Wake-word config is local/Tauri, not available via backend |
| `musicPage.modes` | Mood modes are UI templates |
| `perceptionPage.events`, `perceptionPage.suggestion` | Sensing events and suggestions are UI demonstration |
| `perceptionPage.privacy`, `perceptionPage.permissions` | Static explanatory text |
| `abilitiesPage.quickActions` | Template action buttons (no capability invocation boundary) |
| `abilitiesPage.calls` (full detail) | Individual invocation history not exposed via snapshot; diagnostics-derived status rows are shown instead |
| `advancedPage.diagnostics.metrics["帧率 (FPS)"]` | No backend FPS data; mock value shown when runtime data unavailable |
| `advancedPage.operations`, `advancedPage.expertOptions` | Static UI configuration |
| `overviewPage.health["CPU 占用"]` | Uses backend `cpu_percent` when available; otherwise shows service status |

The snapshot endpoint never returns: prompt text, chat messages, API keys, secrets, clipboard content, screenshot content, or file full text. Individual provider failures degrade only the failing field — other fields remain available — and the endpoint still returns 200.

The backend **`POST /control-center/actions/{actionId}`** endpoint is **inert by design** — every action returns `{ ok: false, status: "not-implemented", refresh: false }`. This is verified by `test_control_center_action_inert_refresh_only` which checks window.close, music.next, character.importZip, and unknown.action all return not-implemented. No desktop, window, or music operation can be triggered through the backend action endpoint. Provider config actions intentionally bypass this inert endpoint and use the separate `/capabilities/providers/...` routes.


## Contract Shape

Recommended snapshot shape:

```ts
type ControlCenterSnapshot = {
  shell: ShellData;
  overview: OverviewData;
  character: CharacterData;
  voice: VoiceData;
  music: MusicData;
  perception: PerceptionData;
  abilities: AbilitiesData;
  advanced: AdvancedData;
};
```

Recommended update cadence:

- Static metadata, navigation, character assets: load on open and refresh on change.
- Service health, CPU, memory, temperature, latency: refresh every 1-2 seconds.
- Music progress and voice preview progress: refresh every 250 ms-1 second while active.
- Logs, sensing events, ability calls: push from backend when available, or poll as a fallback.

## Shared Shell

Backend domains: `app.runtime`, `app.navigation`, `app.assets`.

Fields:

- `defaultPage`: initial page id.
- `version`: application version shown in the prototype.
- `onlineStatus`: status label and detail for the sidebar online card.
- `backgroundAsset`: local asset key or URL for the global background.
- `navItems`: visible page ids, labels, icon keys, and enabled state.
- `windowActions`: available shell actions such as minimize, maximize, close, notification, settings.

## Overview

Backend domains: `companion.session`, `service.health`, `command.actions`, `character.package`, `expression.runtime`, `voice.service`, `music.playback`, `desktop.sensing`, `capability.registry`, `system.metrics`.

Fields:

- `status`: title, connection badge, hero asset, connection state, current character package, current expression, ability availability summary.
- `connection`: service status, response latency, sync status, session duration.
- `quickActions`: user-facing action label, icon key, tone, command id.
- `pack`: selected character package name, version, release date, manage action.
- `emotion`: current expression name and preview image. The image is resolved from `resourceManifest` via `normalizeEmotionCards(fallback: image|url|key)` when available.
- `voice`: TTS/ASR enabled state and service status. Rows carry stable ids/action ids so the overview card can reuse the voice action bridge.
- `music`: current song, artist, cover asset, player state, available controls. The mini-player controls reuse the music action bridge (`music.previous`, `music.next`, `music.pause`, `music.stop`, `music.clear`).
- `sense`: sensing toggles, authorization note, feature enabled states. Toggles carry stable ids/action ids so the overview card can reuse the perception action bridge.
- `abilities`: visible ability modules as user-facing names, icons, colors, enabled state.
- `recentOutputs`: recent generated file summaries (title, subtitle/format). Rendered as "最近成果" card. Backend data from `workspace.outputs`, empty state when none available.
- `health`: CPU (`cpu_percent` when available), memory, storage, temperature, errors, warnings, app version, update state.

## Character

Backend domains: `character.package`, `character.assets.*`, `character.package.validation`.

Fields:

- `selectedPack`: current package id, display name, version, author, description.
- `hero`: large character asset for the page hero.
- `resourceCompleteness`: percent and validation status.
- `actions`: import zip, open package folder, apply, refresh, restore defaults.
- `outfits`: outfit id, name, thumbnail asset, current flag, availability.
- `expressions`: expression id, name, thumbnail asset, preview flag.
- `missingResources`: missing asset groups, counts, severity, repair action.
- `resourceCounts`: action, expression, outfit, background counts.
- `tips`: user-facing maintenance tips.

The page is a template. Actual clothing, expression, and resource lists must come from the selected character package and runtime validation.

## Voice

Backend domains: `voice.tts`, `voice.asr`, `voice.preview`, `voice.recognition`, `voice.synthesisQueue`, `voice.processing`, `voice.diagnostics`.

Fields:

- `tts`: enabled state, selected voice, output volume, speaking speed.
- `asr`: enabled state, microphone device, recognition language, input sensitivity, real-time input meter.
- `preview`: character image, preview text, playback state, progress, waveform data.
- `actions`: test voice, stop voice, clear records, clear queue.
- `recognitionLog`: recognized text, timestamp, confidence.
- `synthesisQueue`: queued text, duration, current position, status.
- `processing`: noise reduction, echo cancellation, wake word, wake sensitivity.
- `diagnostics`: overall state, ASR latency, TTS latency, engine online state, sample rate, channel mode, network status.

## Music

Backend domains: `music.playback`, `music.queue`, `music.lyrics`, `music.recommendations`, `audio.output`.

Fields:

- `nowPlaying`: track id, title, artist, cover asset, quality label, current time, duration, liked state.
- `player`: playback state, mode, volume, seek position, available controls.
- `lyrics`: current lyric lines, active line index, lyric source state.
- `queue`: track list with id, title, artist, duration, cover asset, current flag.
- `info`: duration, source, audio quality, waveform data.
- `moods`: mood presets and active mood.
- `recommendations`: desktop-runtime recommendation projection. Workspace audio candidates are preferred and are not auto-enqueued; if no workspace audio is available, the runtime falls back to queue-based recommendations. In pure mock/demo mode this may come from `mock-data.js`; once a Tauri music snapshot exists it must come from `buildMusicRecommendationsSnapshot()` and may be an empty array. Dedup uses two sets: `seenHandles` (exact match) and `seenSoftKeys` (title+format+sizeBytes fuzzy match); empty titles bypass soft merging. On the Python side, `dedupe_desktop_workspace_audio_attachment_cards()` in `desktop_pet_engine.py` provides workspace-level dedup with ready-priority replacement before ingest.
- `output`: volume normalization, selected device, available devices.

Mock songs and covers are placeholders. Runtime recommendations are derived from workspace audio candidates or the local music queue and are shared with `/think` through `desktop_activity.recommendations`; they must not include local paths, cached paths, storage paths, handles, source ids, or full lyrics. Clicking a workspace recommendation emits `music.playWorkspaceRecommendation` and then `playWorkspaceAudio`; only `{ itemType, handle, title }` is allowed in the control-center action payload.

## Desktop Sensing

Backend domains: `desktop.sensing.*`, `desktop.permissions`, `companion.proactiveChat`, `companion.suggestions`.

Fields:

- `features`: foreground window sensing, clipboard text, screen reading, proactive chat; each needs enabled state, description, and permission state.
- `activeWindow`: app name, title, version or process detail, thumbnail if available.
- `clipboard`: recent text preview, source app, timestamp, clear action.
- `capture`: screen capture interval, retained frame count, local-only processing flag.
- `proactiveChat`: interval options, active interval, interruption policy.
- `privacy`: privacy and safety text shown to users.
- `permissions`: screen capture, clipboard, microphone, file access states and manage action.
- `events`: recent sensing events with time, type, title, details.
- `suggestions`: Akane discoveries and suggested actions based on current context.
- `diagnostics`: capture FPS, OCR state, last update time, manual diagnostic action.

## Abilities

Backend domains: `capability.registry`, `capability.modules`, `capability.workflows`, `capability.invocations`, `security.policy`, `live2d.runtime`.

Fields:

- `summary`: available module count, granted permission count, pending approval count, availability percent.
- `quickActions`: user-facing action name, icon key, command id, tone.
- `modules`: module id, user-facing name, description, status, permission summary, ability count.
- `workflows`: workflow examples with ordered steps and command ids.
- `history`: invocation timestamp, module, operation summary, status, duration, trigger type.
- `safety`: protected operations, approval requirement, current guard state.
- `live2d`: model, motion, renderer, physics status.

Do not expose raw backend tool names in this page. Map backend capabilities to user-facing modules before rendering.

## Advanced

Backend domains: `system.metrics`, `rendering.runtime`, `app.operations`, `app.diagnostics`, `app.logs`, `live2d.runtime`, `capability.registry`, `app.expertOptions`.

Fields:

- `systemStrip`: running state, CPU, memory, network state.
- `rendering`: WebGL, hit-test, hitbox enabled states and descriptions.
- `operations`: actions such as temporary window transparency, reset window, exit companion.
- `diagnostics`: app status, backend health, FPS, memory usage.
- `logs`: recent runtime logs with timestamp, level, source, message.
- `live2d`: reserved Live2D model, motion, renderer, physics state.
- `abilities`: compact ability overview for advanced diagnostics.
- `expertOptions`: developer mode, detailed logs, hardware acceleration, low-latency mode, auto update.

## Actions

Actions should be exposed as command ids with user-facing labels:

```ts
type ControlCenterAction = {
  id: string;
  label: string;
  page: string;
  tone?: "pink" | "blue" | "green" | "orange" | "purple" | "danger";
  requiresConfirmation?: boolean;
  requiresPermission?: string;
};
```

The UI calls the action router instead of backend or Tauri APIs directly.

In the lab implementation, buttons use `data-action-id` and are routed through `createControlCenterActionRouter`.
Mock actions resolve locally; real backend or Tauri actions should be registered in the router or implemented behind `dataSource.runAction` instead of being called directly from render functions.

Current bridged action ids:

- `chat.new`
- `chat.stop`
- `workspace.open`
- `voice.test`
- `voice.stop`
- `voice.setTtsEnabled`
- `voice.setAsrEnabled`
- `voice.setVolume`
- `voice.previewPlay`
- `voice.setSpeed`
- `voice.setWakeWord`
- `voice.setWakeSensitivity`
- `character.openPackFolder`
- `character.refresh`
- `character.previewEmotion`
- `character.selectPack`
- `character.setOutfit`
- `perception.desktopContext.setEnabled`
- `perception.clipboardContext.setEnabled`
- `perception.screenVision.setEnabled`
- `perception.screenVision.setIntervalSec`
- `perception.screenVision.setFrameCount`
- `perception.screenVision.clear`
- `perception.proactiveWake.setEnabled`
- `perception.proactiveWake.setIntervalSec`
- `perception.runDiagnostics`
- `window.close`
- `window.minimize`
- `window.maximize`
- `advanced.probeClickThrough`
- `advanced.resetWindow`
- `advanced.toggleWebgl`
- `advanced.setHitTestEnabled`
- `advanced.setHitboxOverlay`
- `music.previous`
- `music.next`
- `music.pause`
- `music.stop`
- `music.clear`
- `music.seek`
- `music.selectQueueItem`
- `music.setPlayMode`
- `music.setVolumeNormalization`
- `music.playWorkspaceRecommendation`
- `abilities.provider.config.save`
- `abilities.provider.healthCheck`
- `abilities.provider.ttsTest`
- `abilities.provider.voiceProfile.inspectFolder`
- `abilities.provider.voiceProfile.save`
- `abilities.provider.voiceProfile.assignToCurrentCharacter`
- `abilities.provider.voiceProfile.clearCurrentCharacter`
- `abilities.mcp.config.save`
- `abilities.mcp.discover`
- `abilities.approvalPolicy.save`
- `abilities.workflow.config.save`
- `abilities.workflow.file.import`
- `abilities.workflow.validate`

Action results should be structured. A successful side-effect should include a snapshot refresh hint:

```ts
type ControlCenterActionResult = {
  ok: boolean;
  status?: string;
  actionId: string;
  refresh?: boolean;
  payload?: unknown;
  error?: string;
};
```

If a real action has no backend or Tauri implementation yet, return:

```json
{
  "ok": false,
  "status": "not-implemented",
  "actionId": "character.importZip",
  "refresh": false
}
```

If an action handler or data source throws, the router resolves with a structured failure instead of rejecting:

```json
{
  "ok": false,
  "status": "failed",
  "actionId": "music.next",
  "error": "failure message",
  "refresh": true
}
```

Deferred action surfaces:

The machine-readable source of truth is `src/control-center/action-surface-contract.js`; `npm run smoke:control-center-actions` verifies that all bridged ids are catalogued and all deferred ids remain unbridged.

- Character import zip, apply, restore defaults, and outfit/expression management are deferred until file selection, payload shape, and destructive/restore confirmation semantics are defined. Pack selection and outfit tiles are bridged through `character.selectPack` and `character.setOutfit`.
- Voice selector, ASR device/language, record clearing, and queue clearing remain UI stubs until stable settings commands or backend contracts exist; ASR sensitivity and music output device are display-only text.
- Music mood presets, recommendation refresh, and output device controls remain UI stubs until playback payloads and backend/Tauri ownership are defined.
- Perception privacy help, permission management, clipboard clear, event log expansion, and suggestions are deferred until their real data/action boundaries exist; active-window details are client-handled locally.
- Abilities quick actions, module/permission management, workflow expansion, safety policy details, and Live2D settings remain deferred because they currently represent capability navigation or policy surfaces, not concrete desktop commands; call log expansion is client-handled locally.
- Advanced log clearing, ability overview cards, Live2D status, expert options, and `退出桌宠` remain deferred. More-log expansion is client-handled locally. `退出桌宠` must not be bridged without an explicit confirmation and ownership decision.

Parallel next slices:

- Character apply/outfit/import slice: define payloads for `character.importZip`, `character.setOutfit`, `character.apply`, and `character.restoreDefaults`, including confirmation behavior and file-picker ownership.
- Voice configuration slice: define catalog/device payloads for `voice.selectTtsVoice`, `voice.setSpeed`, `voice.selectAsrDevice`, `voice.setAsrLanguage`, `voice.setAsrSensitivity`, and clear commands.
- Music interaction slice: define playback ownership for `music.setPlayMode`, `music.setMood`, `music.refreshRecommendations`, `music.setVolumeNormalization`, and `music.selectOutputDevice`.
- Capability/navigation slice: decide whether abilities, permissions, logs, privacy help, diagnostics, and Live2D settings open local panels, backend routes, or remain read-only.

`window.maximize` currently toggles maximize state through the Tauri window API. If strict maximize and unmaximize commands are needed later, add separate action ids instead of changing this action's current behavior.

The router also accepts registered handlers and an `onAfterAction(result)` hook so callers can refresh the snapshot after an action without coupling render functions to backend or Tauri APIs. The concrete implementation `handleControlCenterActionResult` in `control-center-lab.js` calls `scheduleRuntimeSnapshotHydrate()` and emits `requestSnapshot` (in Tauri). This couples with `bindSettingsSnapshotListener` which listens for `SETTINGS_SNAPSHOT_EVENT` and applies the runtime patch via `applySettingsSnapshotPatch` → `buildMusicRuntimePatch` / `buildOverviewRuntimePatch`. The patching triggers re-render of the active page when the user is on music or overview.

## Assets

Use stable asset keys where possible:

- `assetKey`: preferred for bundled/local Akane resources.
- `url`: optional for future remote or user-provided assets.
- `fallbackAssetKey`: used when a package asset is missing.

The UI should never assume a specific song, outfit, or expression exists. It should render whatever the backend reports for the active package and runtime state.
