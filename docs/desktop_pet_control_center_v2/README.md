# Desktop Pet Control Center V2

Status: design contract, interactive prototype, and componentized V2 Overview candidate. The candidate is not yet the production settings-window authority.

This slice intentionally leaves the existing desktop-pet quick card and `desktop_pet_next/control-center-lab.html` unchanged. The prototype is used to settle information hierarchy, character asset slots, non-destructive framing, chat presentation, theme behavior, and action feedback before production reintegration.

The first production-shaped slice now lives behind `desktop_pet_next/control-center-v2.html`. It has its own small component/store/bridge boundary, reads only real backend and Tauri snapshots, and renders honest connecting, empty, failed, and unavailable states. It does not use the old control center's mock page skeleton. The existing settings window remains authoritative until this candidate passes Tauri verification and the legacy renderer can be removed rather than kept as a second implementation.

## Files

- `contract.md` — product boundary, information architecture, runtime data contract, interaction states, and migration gates.
- `prototype/index.html` — overview, character/appearance, and chat-history prototype.
- `prototype/styles.css` — glass shell, responsive layout, motion, and theme tokens.
- `prototype/app.js` — local-only light/dark themes, framing editor, chat simulation, navigation, and honest simulated action states.
- `../../desktop_pet_next/control-center-v2.html` — Vite candidate entry; not currently opened by the settings window.
- `../../desktop_pet_next/src/control-center-v2/` — componentized shell, Overview, store, host bridge, and real-snapshot view model.

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
