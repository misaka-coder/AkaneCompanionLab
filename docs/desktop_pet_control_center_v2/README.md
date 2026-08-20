# Desktop Pet Control Center V2

Status: design contract and interactive prototype only. It is not wired into the production control center.

This slice intentionally leaves the existing desktop-pet quick card and `desktop_pet_next/control-center-lab.html` unchanged. The prototype is used to settle information hierarchy, character asset slots, theme behavior, and action feedback before production reintegration.

## Files

- `contract.md` — product boundary, information architecture, runtime data contract, interaction states, and migration gates.
- `prototype/index.html` — overview and character/appearance prototype.
- `prototype/styles.css` — glass shell, responsive layout, motion, and theme tokens.
- `prototype/app.js` — local-only theme controls, image previews, navigation, and honest simulated action states.

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
