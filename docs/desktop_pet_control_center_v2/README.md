# Desktop Pet Control Center V2

Status: production control center, cut over on 2026-08-21. It covers Overview, Chat, Character & Appearance, Voice & Wake, Model Service, Abilities & Permissions, and System & Diagnostics.

The canonical settings entry is `desktop_pet_next/control-center-lab.html`. It loads the componentized V2 implementation directly. `settings.html` and `control-center-v2.html` are compatibility redirects and own no settings behavior.

The implementation has a small component/store/bridge boundary, reads only real backend and Tauri snapshots, and renders honest connecting, empty, failed, and unavailable states. Model settings use the existing admin bridge without returning saved keys. Bot switching rebinds the data source only after the desktop snapshot confirms the requested Bot. Voice preview and character changes also require observed runtime confirmation. System & Diagnostics never invents a log timeline. Device-local presentation preferences never rewrite character assets or compete with workshop calibration.

## Files

- `contract.md` — product boundary, information architecture, runtime data contract, interaction states, and migration gates.
- `production_gap_audit.md` — V2/legacy coverage, prohibited mock migration, replacement gates, and cutover order.
- `prototype/index.html` — overview, character/appearance, and chat-history prototype.
- `prototype/styles.css` — glass shell, responsive layout, motion, and theme tokens.
- `prototype/app.js` — local-only light/dark themes, framing editor, chat simulation, navigation, and honest simulated action states.
- `../../desktop_pet_next/control-center-lab.html` — canonical Vite/Tauri settings entry.
- `../../desktop_pet_next/control-center-v2.html` — stale-link compatibility redirect to the canonical entry.
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

## Production V2 preview

From `desktop_pet_next`:

```powershell
npm run dev
```

Open `http://127.0.0.1:1420/control-center-lab.html`. When the local backend is not running, the page deliberately shows a reconnectable empty state instead of sample data. In Tauri it also subscribes to the existing settings snapshot event for current activity, expression, and music state.

## Real Tauri path

From `desktop_pet_next`:

```powershell
npm run tauri -- dev
```

This starts the normal desktop-pet development runtime. The production frameless `settings` window uses the established `settings` label, so snapshot events, commands, folder entrances, the character workshop, music control, and window controls use the production bridge.

There is no implementation selector. `AKANE_OPEN_MODEL_SETTINGS=1` selects `control-center-lab.html?page=model`; all other settings launches select `control-center-lab.html`.

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
11. System & Diagnostics distinguishes backend health from desktop live-state availability, exposes no sensitive paths, and omits recent events until a real event source exists.
12. Chat loads older history through the read-only session cursor, preserves the visible reading position after prepending rows, and keeps newly arriving tail messages behind an unread affordance when the reader is away from the bottom.

The 2026-08-21 acceptance pass covered real Tauri connection loss, window chrome, drag/minimize/maximize/restore, workspace/workshop/folder entrances, repeated action pending state, slow model discovery, model secret masking, model deep-link startup, and canonical entry startup.
