# Desktop Pet Control Center V2

Status: design contract, interactive prototype, and componentized V2 candidate covering Overview, Chat, Character & Appearance, Voice & Wake, and Abilities & Permissions. The candidate is available through an explicit Tauri test launcher, but is not yet the production settings-window authority.

This slice intentionally leaves the existing desktop-pet quick card and `desktop_pet_next/control-center-lab.html` unchanged. The prototype is used to settle information hierarchy, character asset slots, non-destructive framing, chat presentation, theme behavior, and action feedback before production reintegration.

The production-shaped candidate now lives behind `desktop_pet_next/control-center-v2.html`. It has its own small component/store/bridge boundary, reads only real backend and Tauri snapshots, and renders honest connecting, empty, failed, and unavailable states. Overview, Chat, Character & Appearance, Voice & Wake, and the first Abilities & Permissions slice are implemented. The voice page uses the existing TTS/ASR settings and waits for live playback confirmation; it deliberately keeps character imagery in the shared rail instead of repeating a portrait on every page. Device-local presentation preferences cover system/dark/light mode, restrained accent presets, font presets, panel opacity, background dimming, glass blur, reduced motion, plus independent avatar/portrait/background framing. These preferences never rewrite character assets or compete with workshop calibration. The existing settings window remains authoritative until this candidate passes Tauri verification and the legacy renderer can be removed rather than kept as a second implementation.

## Files

- `contract.md` — product boundary, information architecture, runtime data contract, interaction states, and migration gates.
- `production_gap_audit.md` — V2/legacy coverage, prohibited mock migration, replacement gates, and cutover order.
- `prototype/index.html` — overview, character/appearance, and chat-history prototype.
- `prototype/styles.css` — glass shell, responsive layout, motion, and theme tokens.
- `prototype/app.js` — local-only light/dark themes, framing editor, chat simulation, navigation, and honest simulated action states.
- `../../desktop_pet_next/control-center-v2.html` — Vite candidate entry; opened by the settings window only through the explicit V2 test launcher.
- `../../desktop_pet_next/src/control-center-v2/` — componentized shell, Overview, Character & Appearance, shared action primitive, store, host bridge, and real-snapshot view model.

## Local preview

From the repository root:

```powershell
python -m http.server 4177
```

Open:

```text
http://127.0.0.1:4177/docs/desktop_pet_control_center_v2/prototype/
```

The prototype uses existing sample assets from `desktop_pet_next` to demonstrate slots. Selecting a local image only previews it in the current browser session; it does not edit a character pack.

## V2 candidate preview

From `desktop_pet_next`:

```powershell
npm run dev
```

Open `http://127.0.0.1:1420/control-center-v2.html`. When the local backend is not running, the candidate deliberately shows a reconnectable empty state instead of sample data. In Tauri it also subscribes to the existing settings snapshot event for current activity, expression, and music state.

## Real Tauri test path

From `desktop_pet_next`:

```powershell
npm run dev:control-center-v2
```

This starts the normal desktop-pet development runtime and opens V2 in the real frameless `settings` window. The window keeps the established `settings` label, so snapshot events, commands, folder entrances, the character workshop, music control, and window controls use the production bridge instead of a second test-only protocol.

The selector is intentionally opt-in and process-local:

- this command sets `AKANE_CONTROL_CENTER_ENTRY=v2` only for its Tauri child process;
- ordinary `npm run tauri -- dev`, release startup, and packaged builds still open `control-center-lab.html`;
- an empty or unknown selector also falls back to `control-center-lab.html`;
- `AKANE_OPEN_MODEL_SETTINGS` continues to select the legacy model page unless V2 was explicitly selected.

Manual acceptance for this stage:

1. V2 opens automatically and can be dragged, minimized, maximized/restored, and closed.
2. The connection chip and activity state follow the running desktop pet; no sample character or fake activity appears while disconnected.
3. “新对话” and “停止回复” only report confirmation after a settings snapshot shows the corresponding state change.
4. “打开工作区”, “角色工坊”, and “角色包目录” open their real host targets.
5. Music controls report failure or unconfirmed execution honestly when no controllable session exists.
6. Character & Appearance reads the current pack, outfit, expressions, portrait, and resource health from real snapshots; device-local theme/framing changes provide immediate preview and report whether persistence succeeded.
7. Pack, outfit, and expression changes confirm only after the desktop-pet snapshot reports the requested value; otherwise the UI reports an unconfirmed execution.
8. Abilities & Permissions reads the real capability catalog, shows the current safety boundary, and changes approval mode only through the profile approval-policy route.
9. Full authoring, uploads, and framing calibration remain in the character workshop instead of creating a second character-pack authority.
10. Voice toggles, volume, speed, wake settings, preview, and stop use the existing settings-command bridge; preview reports success only after the live snapshot shows playback.

This test path is not a production cutover. The legacy settings entry remains authoritative until System & Diagnostics and the remaining acceptance gates are complete and the old renderer can be deleted rather than kept in parallel.
