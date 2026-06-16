# Akane Desktop Pet Next

This is an isolated Tauri/WebView2 prototype line for the Akane desktop pet. It does not replace or import the stable Electron implementation in `desktop_pet/`.

## Quick Start

```powershell
# 1. From the repository root, start the Python backend (separate terminal)
python launch_akane_memory_v01.py

# 2. Enter the Next prototype and install Node dependencies
cd desktop_pet_next
npm install

# 3. Launch the desktop pet (dev mode with hot reload)
npm run tauri -- dev
```

Default backend URL: `http://127.0.0.1:9999`. Change it from the right-click menu or settings window.

For release builds or packaging smoke tests, see the [Commands](#commands) and [Daily Smoke Test](#daily-smoke-test) sections below.

## Scope

- Transparent, frameless, always-on-top Tauri window using the Electron pet's 340x560 base size.
- Creator Kit character metadata is loaded from `../desktop_pet_creator_kit/characters/` at build time and refreshed from disk at runtime in the Tauri app.
- Catgirl Akane static portraits are copied into this prototype package.
- Dragging is limited to an approximate portrait hit region.
- Window position and size are persisted through Rust-side app config storage.
- The right-click menu in the pet window is now a compact quick menu for input, settings, new session, character-resource reload, and exit, so it no longer covers the portrait.
- Scale, opacity, outfit, backend check, always-on-top, taskbar visibility, reset, close, WebGL probe, and temporary click-through probe are exposed from an independent Tauri settings window.
- The settings window is now a wider anime-style control center with left navigation for overview, character, voice, music, desktop sensing, abilities, and advanced/debug controls.
- The settings window can install Creator Kit exported character-pack zip files into `desktop_pet_creator_kit/characters/`, refresh the runtime pack list, apply the imported pack, open that folder, and copy the last installed pack path.
- Scale and opacity have Electron-style quick presets in addition to sliders.
- The settings window includes a resource panel that lists current character-pack outfits, active outfit, resource source, backend status, emotion count, and missing required/recommended expressions.
- Outfit cards can switch the active outfit; switching reloads `/resource-manifest` and persists the selected outfit.
- Windows native hit-test is available behind the `Hit-Test: on/off` menu switch. It uses an approximate portrait polygon plus control rectangles so blank transparent areas can pass through to windows underneath.
- `Hitbox: on/off` draws the current hit regions for tuning the portrait polygon and menu/input regions.
- A visible close button is also available in the top-right corner of the prototype window.
- Character resources are loaded from `/resource-manifest` when the backend is available. Desktop-pet requests include the active Creator Kit `character_pack_id`, so the backend resource prompt and the visible pet use the same character-pack outfit/emotion list. Bundled catgirl assets remain the offline fallback.
- The menu shows resource source, active outfit, expression count, session suffix, and a character-pack-driven emotion preview grid.
- Emotion preview is temporary and non-persistent; it restores the previous expression and does not change the dialogue state.
- Backend offline or manifest failures are surfaced in the settings window, with bundled catgirl assets used as the visible fallback and a quiet reconnect retry while the Tauri app stays open.
- Backend health now prefers `/desktop-pet/health`, falls back to legacy `/health`, and surfaces the desktop-pet contract version plus TTS/ASR endpoint status in settings.
- The settings window can refresh `/desktop-pet/diagnostics` as a read-only capability panel, showing the active character pack, resource count, tool exposure, workspace counts, and safety boundaries for the current desktop-pet mode.
- `/resource-manifest` desktop-pet metadata (`clients.desktop_pet`) is used for default outfit/emotion hints when available, while Web resources continue to live under `web/assets`.
- Minimal `/think` dialogue loop with `client_mode = desktop_pet`, `speech_segments`/`tts` capabilities, and optional `desktop_context` when the context toggle is enabled.
- Single-click the portrait to show a local line without calling the backend. Local reactions can refresh themselves immediately, while active backend replies and TTS are still protected.
- Local single-click reactions temporarily switch expression and then return to `正常`, so they do not restore the previous backend reply expression.
- Double-click the portrait to open chat input. Enter sends, Escape hides.
- The chat input, speech bubble, light menu theme, idle/click/thinking/speaking motion, and bottom-right default placement are aligned with the stable Electron pet direction.
- Enter sending is IME-aware, so Chinese composition will not accidentally submit the input.
- Sent text is kept in a lightweight in-memory input history; use Up/Down in the input to recall or return to the current draft.
- If `/think` fails or times out, the submitted text is restored into the input box for retry/editing.
- Speech bubbles are compact head-top bubbles with a small tail, and they stay non-interactive so they do not block the menu/settings buttons.
- Minimal TTS output is available from the settings window: replies can be read aloud through the existing `/tts` endpoint, with enable/disable, volume, test, and stop controls.
- Minimal voice input is available from the chat input: click the `麦` button or hold `Ctrl+Shift+Space` while the pet window is focused to record, then `/asr` transcribes into the input box for manual confirmation.
- Minimal local music queue playback is available: drag one or more `mp3`, `wav`, `flac`, `ogg`, `m4a`, `aac`, `opus`, or `webm` files onto Akane to play them from the Tauri cache. The quick menu, settings window, and hand-tray window can go previous/next, pause/resume, stop, and manage queue items; the portrait switches to `听歌中` while playing.
- Same-name `.lrc` lyrics are picked up automatically when a local music file is dragged in, and dragging audio plus matching `.lrc` together also works. If there is no `.lrc`, the Next shell uploads the local audio to the existing backend music-timeline pipeline so vocal separation/ASR can prepare nearby lyric lines in the background. Settings and hand-tray windows show the current lyric line while playback progresses.
- Current local music status is attached to `/think` while a track is loaded, including title, queue position, next track, play/pause state, progress, duration, and the current/nearby lyric lines from either local `.lrc` or backend timeline. Akane can naturally refer to the song queue, and simple current-track `activity` actions can pause, resume, stop, previous, next, or switch by `source_id`.
- Minimal desktop context is available behind settings toggles: foreground-window sensing is on by default, clipboard text is off by default, and both are only attached transiently to `/think`.
- Experimental screen vision is available behind the `看屏幕` settings toggle. Summary mode asks WebView2 for screen-share permission, compresses a few frames into a short clip, sends them to `/desktop-pet/vision/clip`, and lets `/think` read only the latest short-term screen impressions.
- Direct screen-vision mode keeps only the latest 1-5 compressed screenshots in the Tauri pet and sends them temporarily with proactive `/think`; new frames replace old frames, and the images are not persisted as memory.
- Proactive wake is available behind the `主动搭话` settings toggle. Wake interval, screen-vision interval, and screen frame count are numeric settings; the settings window also shows a recommended vision interval based on the wake interval.
- A read-only Workspace/hand-tray window is available from the compact menu and settings window. It fetches `/desktop-pet/workspace/summary`, shows files/generated outputs/tasks, has manual refresh, and records the latest refresh time without running file actions yet.
- Replies can be interrupted from the compact menu or the settings window. Stopping a reply aborts/invalidates the active `/think` turn, clears queued TTS, and returns the portrait motion to idle; local click bubbles no longer make the stop control look active.
- Long plain `speech` replies are split client-side into smaller bubble segments when the backend does not provide `speech_segments`.
- The settings window exposes a startup restore toggle for `latest_final_json`, so daily testing can choose whether to restore the previous reply on launch.
- The settings window shows enough active state to disable stop controls while Akane is idle.
- Startup session ensure restores `latest_final_json` as history: it can restore text and backend emotion, but it does not trigger the speaking motion.
- Live `/think` replies can use the CSS-only speaking motion on the current expression image. It never switches to a separate speaking sprite.
- Local single-click lines temporarily change expression and then restore the previous expression without persisting the local expression.
- `新对话` creates a fresh independent `sessionId` while keeping `profileUserId = master`.
- `speech_segments` are displayed one segment at a time and take precedence over `speech`.
- The menu displays backend/resource status, and `重载资源` rechecks health plus `/resource-manifest`.
- The WebGL probe only verifies transparent Canvas/WebGL viability. It is not a Live2D integration yet.
- The visual renderer now goes through a thin adapter (`src/visual-renderer.js`): today's mode is static portrait images plus CSS motion, while future Live2D can plug into the same expression/motion boundary without rewiring dialogue, voice, or resource loading.

## Deferred

- Full Electron feature migration.
- Large backend protocol rewrites beyond the lightweight desktop-pet contract.
- Live2D renderer integration.
- Activity runtime, global ASR shortcut, task reminders, and full debug/workbench panels.
- Web scene Tauri wrapper.
- Per-pixel alpha-mask hit testing. The current native `WM_NCHITTEST` hook is a coarse polygon/bounding-box probe.

## Electron Daily Parity Checklist

| Area | Status | Notes |
| --- | --- | --- |
| Transparent always-on-top pet shell | Done | Tauri/WebView2 transparent frameless window with persisted position/size. |
| Coarse blank-area click-through | Done | Windows `WM_NCHITTEST` polygon/rect regions; per-pixel alpha mask remains deferred. |
| Static portrait resources | Done | `/resource-manifest` first, bundled catgirl fallback. |
| Outfit/emotion switching | Done | Manifest-driven emotion list with alias fallback and temporary preview. |
| Single-click local reaction | Done | Can refresh local lines immediately; active `/think` replies and TTS remain protected. |
| Double-click text input | Done | IME-aware Enter send, Escape hide, empty blur auto-hide. |
| Input reliability | Done | Failed sends restore the submitted text; Up/Down recalls recent sent lines. |
| `/think` dialogue | Done | Uses `client_mode = desktop_pet` and `speech_segments`/`tts` capabilities. |
| Bubble presentation | Done | `speech_segments` take priority; long `speech` is split client-side; bubbles stay compact near the portrait head. |
| Reply interrupt | Done | Stops active turn, queued TTS, and current voice playback. |
| Minimal TTS | Done | Reply read-aloud toggle, volume, test, stop. |
| Local music playback | Done | Drag one or more audio files onto Akane to play as a queue; matching `.lrc` lyrics are shown in settings/hand tray, and songs without `.lrc` can use the backend vocal timeline/ASR pipeline; quick menu/settings/hand tray can previous/next, pause/resume, stop, play queue items, and remove queue items; queue and lyric state are available to `/think`. |
| Session controls | Done | Independent session ID, new session, startup restore toggle, session ID copy. |
| Settings/resource panel | Partial | Daily settings, resource diagnostics, backend contract/TTS/ASR status, connection check, appearance reset, and status separation are present; full debug panel remains deferred. |
| Voice input / ASR | Partial | Focused-window recording button/shortcut calls `/asr` and fills the input box. Global shortcut and richer recorder panel are deferred. |
| Desktop context | Partial | Foreground-window context is cached through a lightweight native probe and attached to `/think`; clipboard text is optional and off by default. |
| Screen vision | Partial | Optional `看屏幕` toggle supports summary mode through `/desktop-pet/vision/clip` and direct mode that sends only the latest 1-5 screenshots with proactive `/think`; neither path writes long-term memory. |
| Proactive wake | Partial | Optional `主动搭话` toggle wakes Akane on a numeric interval and routes the final line through the main `/think` chain with transient visual context. |
| Workspace / hand tray | Partial | Independent read-only Tauri window lists files, generated outputs, tasks, empty states, and latest refresh time from `/desktop-pet/workspace/summary`. File actions and Activity Runtime remain deferred. |
| Activity runtime / BGM / file actions | Deferred | Not part of the current Next daily baseline. |
| Task reminders | Deferred | Keep out until background/runtime behavior is settled. |
| Live2D | Deferred | Canvas/WebGL probe only; full renderer later. |

## Commands

```powershell
npm install
npm run doctor
npm run build
```

`npm run smoke:control-center-actions` validates control-center action bridge mappings, exception hardening, and `not-implemented` behavior.

`npm run probe:control-center-runtime` validates the control-center unified snapshot data pipeline: happy-path hydration from a complete backend snapshot, partial degradation when a single provider fails, fallback to legacy endpoints when the snapshot is broken, graceful null return when all backends are unavailable, action contract inertness (backend POST is never treated as a desktop action execution boundary), and surface contract consistency (all bridged actions are catalogued, all deferred surfaces are not bridged).

`npm run verify:control-center` runs the full control-center verification matrix: smoke actions + runtime probe + build, with a required-file existence gate before execution.

The settings window has one implementation: `control-center-lab.html`. The
`settings.html` path is only a compatibility redirect for stale links and must
not own features. `npm run dev:control-center` remains available as an explicit
control-center development entry. Settings and workspace windows are
non-topmost so the pet keeps its always-on-top priority.

### Control Center Verification Matrix

| Command | Scope |
|---------|-------|
| `npm run smoke:control-center-actions` | Action bridge mappings, `not-implemented` + `refresh:false` contract, exception hardening, surface contract consistency |
| `npm run probe:control-center-runtime` | Unified snapshot data pipeline: happy-path hydration, partial degradation, bad-snapshot fallback, all-unavailable null, action inertness, surface contract |
| `npm run verify:control-center` | Required-file existence gate + smoke actions + runtime probe + build |
| `python -m unittest tests.test_backend_route_modules` (from repo root) | Backend control-center route contract: snapshot endpoint, action endpoint, provider failure resilience, no sensitive content |
| `git diff --check` (from repo root) | Whitespace and syntax hygiene |

After Rust is installed and `cargo` is available in PATH:

```powershell
cargo check --manifest-path src-tauri/Cargo.toml
npm run tauri -- dev
```

`npm run tauri -- dev` also injects `%USERPROFILE%\.cargo\bin` into the process PATH, so it still works from an older VS Code terminal that has not refreshed the Rust PATH yet.

The default backend URL is `http://127.0.0.1:9999`. Start the existing backend before testing replies:

```powershell
# From the repository root:
python launch_akane_memory_v01.py

# Or, if this terminal is already in desktop_pet_next:
python ..\launch_akane_memory_v01.py
```

You can change the backend URL from the right-click/debug menu.

For release-candidate packaging verification:

```powershell
npm run tauri -- build
```

The build command is only a packaging smoke test for the Next prototype. It does not replace the Electron stable app.

Launch the release build through the guarded launcher:

```powershell
npm run start:release
```

Or run the script by itself:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-next.ps1
```

The launcher automatically builds when the release exe is missing, when the
source files are newer than the exe, or when `-Rebuild` is passed:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-next.ps1 -Rebuild
```

Use `-NoBuild` only when you intentionally want to launch an existing exe even
after local source changes. The legacy `-BuildIfMissing` flag is still accepted
for old commands, but it is no longer required.

## Daily Smoke Test

Use this quick pass after changing the Next prototype:

```powershell
cd desktop_pet_next
npm run build
npm run doctor
cargo check --manifest-path src-tauri\Cargo.toml
npm run tauri -- dev
```

Manual checks:

- Window starts transparent, frameless, always on top, and still restores position/scale.
- Blank-area hit-test still lets clicks through more than the Electron rectangle.
- Right-click menu opens without covering the portrait; `设置` and `手边` open only one window each.
- Double-click opens input; Enter sends; Escape hides; empty blur hides.
- Repeated single-clicks refresh local lines and expressions immediately without enabling `停止`.
- Input Up/Down recalls recent sent messages; a failed send restores the submitted text for retry.
- `/think` replies display segments without duplicating `speech`.
- Head-top bubbles stay non-interactive and do not block menu/settings controls.
- `停止` interrupts text, queued TTS, and current voice playback.
- Drag one or more local audio files onto Akane; music starts, expression switches to `听歌中`, auto-next works, and menu/settings/hand-tray previous/next/pause/resume/stop plus queue item controls work.
- Put a same-name `.lrc` beside a local audio file, then drag the audio in; settings and hand tray show the current lyric as playback moves.
- Ask or wait for a reply while music is loaded; Akane can reference the current track, queue state, and nearby lyrics without needing the full Activity Runtime.
- `麦` records, `/asr` fills text into the input box, and does not auto-send.
- Settings changes for backend/session/voice/context are reflected in the Workspace window after refresh or snapshot sync.
- Import a Creator Kit exported character-pack zip from settings; it installs into `desktop_pet_creator_kit/characters/`, refreshes the runtime pack list, and applies the imported pack without restarting the desktop pet.
- Workspace manual refresh updates counts, empty states, and latest refresh/attempt time.
- With `主动搭话` enabled, Akane can wake on the configured interval and speak through `/think` while using recent short-term screen impressions.
- Backend offline state shows a light local-standby message, keeps local click reactions working, and retries quietly while the app stays open.
- `退出` closes the pet plus settings/workspace child windows.
- `npm run doctor` reports Node/npm/Rust/WebView2/backend readiness without changing the machine.
- `npm run tauri -- build` completes or reports a clear local packaging dependency issue.

## Hold List

Keep these out of the daily baseline until the shell stays comfortable for longer testing:

- Full Activity Runtime and BGM controls.
- File actions in Workspace such as open location, clear, delete, or play.
- Task reminder polling.
- Full debug/health workbench parity.
- Global system-wide ASR shortcut.
- Live2D renderer integration.
