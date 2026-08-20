# Desktop Pet Control Center V2 Contract

Status: proposed product contract, 2026-08-20.

## 1. Product boundary

The desktop pet has three distinct surfaces:

1. **Pet window** — character, speech bubble, direct input, local reactions.
2. **Quick card** — compact translucent menu for music, quick actions, scale, opacity, and entry points. Keep the current direction.
3. **Control center** — discover, configure, preview, authorize, and diagnose real capabilities. This is the redesign target.

The standalone character workshop remains the authority for full character authoring: persona, outfits, expressions, calibration, test chat, import, and export. The control center offers quick character switching, resource status, appearance preview, and direct entry points into the workshop or character folder. It must not duplicate the full workshop.

Akane and Reimu are sample characters, not shell requirements.

## 2. Product priorities

In order:

1. Fast, understandable operation.
2. Honest and immediate action feedback.
3. Character visibility and theme customization.
4. Clear layout with progressive disclosure.
5. Smooth motion and stable rendering.
6. Diagnostics for advanced users.

Beauty cannot hide unavailable actions, stale state, or slow feedback.

## 3. Information architecture

Production navigation should converge to five destinations:

| Destination | Default content | Detailed content |
|---|---|---|
| Overview | active character, connection, current activity, music, recent delivery, common actions | activity details and last error |
| Character & Appearance | quick switch, asset preview, theme, opacity, scale, font | open workshop, open pack folder, validate/reload assets |
| Dialogue & Voice | bubble behavior, streaming, TTS, voice assignment | provider configuration and test playback |
| Abilities & Permissions | available capabilities, approvals, local execution/workspace access | provider/workflow configuration and failure reasons |
| System & Diagnostics | instance, memory, storage health, updates | logs, advanced settings, recovery actions |

Only Overview and Character & Appearance enter the first interactive prototype. The other destinations must not enter production until their real data/action boundaries are mapped.

Music is a shared runtime capability. It belongs in the quick card and Overview unless its real feature density later justifies a dedicated page.

## 4. Character-neutral asset slots

The shell consumes normalized slots and never imports a character-specific file directly:

```ts
type ControlCenterCharacterVisuals = {
  avatar?: AssetHandle;
  portrait?: AssetHandle;
  background?: AssetHandle;
  thumbnail?: AssetHandle;
  musicCover?: AssetHandle;
  theme?: {
    accent?: string;
    accentStrong?: string;
    surfaceTint?: string;
    fontFamily?: string;
  };
};
```

Future character-pack metadata may declare these slots, but physical host paths stay behind Tauri/resource handles. The UI receives safe asset URLs or bytes, never database/cache paths.

Fallback order:

- background missing → neutral shell background;
- portrait missing → avatar;
- avatar missing → generated initial/neutral symbol;
- Live2D missing → static portrait;
- theme missing → neutral system theme;
- music missing → hide the player content instead of showing fake playback.

## 5. Runtime view model

The control center should render one normalized snapshot:

```ts
type ControlCenterViewModel = {
  shell: {
    connected: boolean;
    connectionStatus: "connected" | "connecting" | "disconnected" | "failed";
    instanceLabel: string;
  };
  character: {
    packId: string;
    displayName: string;
    outfit: string;
    emotion: string;
    visuals: ControlCenterCharacterVisuals;
    resourceWarnings: string[];
  } | null;
  activity: {
    phase: "idle" | "thinking" | "using_tool" | "waiting" | "needs_approval" | "delivering" | "failed";
    label: string;
    detail?: string;
    startedAt?: number;
  };
  music: {
    available: boolean;
    title?: string;
    artist?: string;
    cover?: AssetHandle;
    playback: "playing" | "paused" | "stopped" | "unknown";
  };
  recentDelivery?: {
    label: string;
    status: "queued" | "delivered" | "failed" | "unknown";
  };
  actions: Record<string, {
    available: boolean;
    reason?: string;
  }>;
};
```

Counts such as module/tool totals are displayed only when computed from the current authoritative snapshot. No demo constants enter runtime.

## 6. Action feedback contract

Every interactive control follows:

```text
idle → pressed → pending → confirmed
                         ↘ failed
                         ↘ execution_unknown
```

- `pressed`: immediate visual feedback in 0–100 ms.
- `pending`: label/icon/progress changes without blocking unrelated UI.
- `confirmed`: reflect observed runtime state, not merely request acceptance.
- `failed`: restore the previous control state and show the real reason.
- `execution_unknown`: say the instruction was sent but the state change was not confirmed.

For playback, a paused player shows the play triangle and a playing player shows pause bars. Clicking immediately produces press/pending feedback; the semantic icon changes only after confirmed playback state. Do not use optimistic success when the runtime returns unknown.

Folder, workshop, import, save, reload, approval, stop-reply, and test actions use the same contract.

## 7. Progressive disclosure

The first viewport contains only:

- active character and visual preview;
- connection and current activity;
- current music when available;
- recent delivery when present;
- three to five real frequent actions.

Long explanations live in tooltips, expandable help, or diagnostics. Advanced provider fields, raw identifiers, logs, and capability schemas never occupy Overview.

Unavailable optional capabilities are hidden when absence is normal. They remain visible but disabled only when the user needs the control to understand or resolve the absence; the UI must show a reason and a path forward.

## 8. Appearance and theme contract

Supported user-facing controls:

- background, avatar, portrait, and optional music cover;
- accent color;
- surface opacity and background dimming;
- blur amount with a no-blur/high-contrast fallback;
- system font presets and character-pack-declared font assets;
- reduced motion.

The shell uses theme tokens rather than arbitrary CSS injection. Text contrast must remain readable over every background. Large images receive cached thumbnails for lists and reuse decoded resources while switching pages.

## 9. Performance and motion gates

- Render shell/navigation from local state without waiting for backend health.
- Hydrate status cards independently; no full-page loading gate.
- Preserve last trustworthy state while refreshing and mark it stale if needed.
- Page changes must not re-decode the same portrait/background.
- Avoid layout shifts when asynchronous fields arrive.
- Button press feedback begins within one frame.
- Typical panel transitions target 160–220 ms.
- Respect `prefers-reduced-motion`.
- Slow, failed, repeated, and cancelled requests remain operable and visible.

## 10. Empty, slow, and failure states

Required states before production replacement:

- no character pack;
- character without avatar/portrait/background;
- disconnected and reconnecting;
- backend slow with shell still usable;
- action rejected, failed, or execution unknown;
- music unavailable;
- no recent delivery;
- character switch during loading;
- repeated click while an action is pending.

## 11. Production migration gates

The prototype does not replace `control-center-lab.html`. Reintegration proceeds only after:

1. Overview and Character & Appearance are accepted visually.
2. Every displayed field maps to a real snapshot source or honest empty state.
3. Every action maps to an existing Tauri/backend boundary or is omitted.
4. The character workshop remains the single full-authoring authority.
5. Existing action bridge and runtime probe tests have a migration plan.
6. Build, UX smoke, narrow viewport, slow/failure, character switch, and manual Tauri checks pass.
7. Old render paths are deleted or reduced to thin adapters; no permanent V1/V2 dual authority.
