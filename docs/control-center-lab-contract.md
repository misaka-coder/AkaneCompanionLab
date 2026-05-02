# Akane Control Center Lab Data Contract

This document describes the backend data boundary for `desktop_pet_next/control-center-lab.html`.
The lab still starts from `src/control-center/mock-data.js`, then hydrates supported fields through the data-source and adapter layer.

Current adapter files:

- `src/control-center/snapshot-schema.js`: front-end snapshot ids, version, and JSDoc contract.
- `src/control-center/data-sources.js`: mock/backend data source boundary.
- `src/control-center/data-adapter.js`: normalizes source data into `ControlCenterSnapshot`.
- `src/control-center/action-router.js`: routes user-facing action ids without coupling buttons to backend APIs.

Current real-data slice:

- `control-center-lab.html` uses the backend data source by default and falls back to mock data if the backend is unavailable.
- The overview page hydrates from existing endpoints: `/health`, `/desktop-pet/diagnostics`, `/desktop-pet/workspace/summary`, and `/metrics`.
- The character page hydrates from `/resource-manifest`, plus Tauri `load_pet_state` / `list_character_packs` when running inside the desktop app.
- The voice page hydrates from `/health`, `/desktop-pet/diagnostics`, and Tauri `load_pet_state`: `tts.enabled`, `tts.volume`, `asr.enabled`, and diagnostics rows (`整体状态`, `TTS 语音引擎`, `ASR 语音引擎`, `响应延迟`, `网络状态`) are derived from real runtime fields. Button actions (test voice, stop voice, clear records, clear queue) remain disconnected — they are UI stubs in `action-router.js` awaiting Tauri command wiring.
- The perception page hydrates from Tauri `load_pet_state` and `/desktop-pet/diagnostics`: the four feature cards (`activeWindow`, `clipboard`, `screen`, `proactive`) get their `enabled` state, interval display strings, and frame count from `petState` fields (`desktopContextEnabled`, `clipboardContextEnabled`, `screenVisionEnabled`, `screenVisionIntervalSec`, `screenVisionFrameCount`, `proactiveWakeEnabled`, `proactiveWakeIntervalSec`). The clipboard card only shows capability status placeholders and does not read clipboard content. Diagnostics rows are patched with current sensing status and sync time. Button actions, system permissions list, sensing events timeline, and privacy suggestions remain mock — they are UI stubs not yet connected to backend or Tauri commands.
- The music page hydrates from the desktop pet `akane-next-settings-snapshot.music` via Tauri `SETTINGS_SNAPSHOT_EVENT`: current track display name, queue, progress/duration, lyric summary (previous/current/next lines), queue position, and bottom status bar. Volume is read from `petState.voiceVolume`. The adapter replaces `nowPlaying`, `playlist`, `lyrics`, `activeLyric`, `info`, and `bottomStatus` fields when a snapshot is available. Button actions (previous, next, pause, stop, clear) remain mock — they are UI stubs not yet connected to Tauri commands.
- The abilities page hydrates from `/desktop-pet/diagnostics` and `/desktop-pet/workspace/summary`: backend tool names are mapped into user-facing modules, and the page updates summary stats, module cards, workflow examples, recent status rows, safety state, and Live2D reserved state. It intentionally does not render raw tool names.
- Use `?source=mock` to force the static prototype, or `?backend=http://127.0.0.1:9999` to point the lab at another backend.
- Backend responses are converted into page runtime patches such as `overviewRuntime` and `characterRuntime`; render functions only consume `ControlCenterSnapshot` fields.

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
- `emotion`: current expression name and preview asset.
- `voice`: TTS/ASR enabled state and service status.
- `music`: current song, artist, cover asset, player state, available controls.
- `sense`: sensing toggles, authorization note, feature enabled states.
- `abilities`: visible ability modules as user-facing names, icons, colors, enabled state.
- `health`: CPU, memory, storage, temperature, errors, warnings, app version, update state.

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
- `recommendations`: recommended tracks, cover asset, duration, play command.
- `output`: volume normalization, selected device, available devices.

All songs, covers, and recommendations in the prototype are placeholders. Production data should reflect the real music library and playback service.

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

The UI should call an adapter such as `runControlCenterAction(actionId, payload)` later. The prototype buttons currently do not call backend APIs.

In the lab implementation, buttons use `data-action-id` and are routed through `createControlCenterActionRouter`.
Mock actions resolve locally; real backend or Tauri actions should be registered in the router instead of being called directly from render functions.

## Assets

Use stable asset keys where possible:

- `assetKey`: preferred for bundled/local Akane resources.
- `url`: optional for future remote or user-provided assets.
- `fallbackAssetKey`: used when a package asset is missing.

The UI should never assume a specific song, outfit, or expression exists. It should render whatever the backend reports for the active package and runtime state.
