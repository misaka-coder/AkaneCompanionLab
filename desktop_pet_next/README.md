# Akane Desktop Pet Next

This is an isolated Tauri/WebView2 prototype line for the Akane desktop pet. It does not replace or import the stable Electron implementation in `desktop_pet/`.

## Scope

- Transparent, frameless, always-on-top Tauri window using the Electron pet's 340x560 base size.
- Minimal Creator Kit character metadata is loaded from `../desktop_pet_creator_kit/characters/akane_sample/character.json`.
- Catgirl Akane static portraits are copied into this prototype package.
- Dragging is limited to an approximate portrait hit region.
- Window position and size are persisted through Rust-side app config storage.
- The right-click menu in the pet window is now a compact quick menu for input, settings, new session, resource reload, and exit, so it no longer covers the portrait.
- Scale, opacity, outfit, backend check, always-on-top, taskbar visibility, reset, close, WebGL probe, and temporary click-through probe are exposed from an independent Tauri settings window.
- Scale and opacity have Electron-style quick presets in addition to sliders.
- The settings window includes a resource panel that lists manifest outfits, active outfit, resource source, backend status, emotion count, and missing required/recommended expressions.
- Outfit cards can switch the active outfit; switching reloads `/resource-manifest` and persists the selected outfit.
- Windows native hit-test is available behind the `Hit-Test: on/off` menu switch. It uses an approximate portrait polygon plus control rectangles so blank transparent areas can pass through to windows underneath.
- `Hitbox: on/off` draws the current hit regions for tuning the portrait polygon and menu/input regions.
- A visible close button is also available in the top-right corner of the prototype window.
- Character resources are loaded from `/resource-manifest` when the backend is available, with bundled catgirl assets as an offline fallback.
- The menu shows resource source, active outfit, expression count, session suffix, and a manifest-driven emotion preview grid.
- Emotion preview is temporary and non-persistent; it restores the previous expression and does not change the dialogue state.
- Backend offline or manifest failures are surfaced in the settings window, with bundled catgirl assets used as the visible fallback and a quiet reconnect retry while the Tauri app stays open.
- Backend health now prefers `/desktop-pet/health`, falls back to legacy `/health`, and surfaces the desktop-pet contract version plus TTS/ASR endpoint status in settings.
- `/resource-manifest` desktop-pet metadata (`clients.desktop_pet`) is used for default outfit/emotion hints when available.
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
- Minimal local music queue playback is available: drag one or more `mp3`, `wav`, `flac`, `ogg`, `m4a`, `aac`, `opus`, or `webm` files onto Akane to play them from the Tauri cache. The quick menu and settings window can go previous/next, pause/resume, or stop music, and the portrait switches to `听歌中` while playing.
- Current local music status is attached to `/think` while a track is loaded, including title, queue position, next track, play/pause state, progress, and duration. Akane can naturally refer to the song queue, and simple current-track `activity` actions can pause, resume, stop, previous, next, or switch by `source_id`.
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
- The menu displays backend/resource status, and `重载立绘` rechecks health plus `/resource-manifest`.
- The WebGL probe only verifies transparent Canvas/WebGL viability. It is not a Live2D integration yet.

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
| Local music playback | Done | Drag one or more audio files onto Akane to play as a queue; quick menu/settings can previous/next, pause/resume, and stop; queue state is available to `/think`. |
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

After Rust is installed and `cargo` is available in PATH:

```powershell
cargo check --manifest-path src-tauri/Cargo.toml
npm run tauri -- dev
```

`npm run tauri -- dev` also injects `%USERPROFILE%\.cargo\bin` into the process PATH, so it still works from an older VS Code terminal that has not refreshed the Rust PATH yet.

The default backend URL is `http://127.0.0.1:9999`. Start the existing backend separately before testing replies:

```powershell
python launch_akane_memory_v01.py
```

You can change the backend URL from the right-click/debug menu.

For release-candidate packaging verification:

```powershell
npm run tauri -- build
```

The build command is only a packaging smoke test for the Next prototype. It does not replace the Electron stable app.

After packaging, launch the release exe directly:

```powershell
npm run start:release
```

Or run the script by itself:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-next.ps1
```

If the release exe is missing, rebuild first with `npm run tauri -- build`, or run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-next.ps1 -BuildIfMissing
```

## Daily Smoke Test

Use this quick pass after changing the Next prototype:

```powershell
cd F:\Akane\AkaneCompanionLab\desktop_pet_next
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
- Drag one or more local audio files onto Akane; music starts, expression switches to `听歌中`, auto-next works, and menu/settings previous/next/pause/resume/stop controls work.
- Ask or wait for a reply while music is loaded; Akane can reference the current track and queue state without needing the full Activity Runtime.
- `麦` records, `/asr` fills text into the input box, and does not auto-send.
- Settings changes for backend/session/voice/context are reflected in the Workspace window after refresh or snapshot sync.
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
