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
| Chat & Voice | durable chat history, composer, bubble behavior, streaming, TTS, voice assignment | provider configuration and test playback |
| Abilities & Permissions | available capabilities, approvals, local execution/workspace access | provider/workflow configuration and failure reasons |
| System & Diagnostics | instance, memory, storage health, updates | logs, advanced settings, recovery actions |

Overview, Character & Appearance, and the core Chat history surface enter the interactive prototype. Advanced voice, abilities, and diagnostics must not enter production until their real data/action boundaries are mapped.

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

Every visual slot also owns non-destructive framing metadata. The original file is never rewritten just because the user changes its presentation:

```ts
type AssetFraming = {
  x: number;      // normalized offset, independent of viewport pixels
  y: number;
  scale: number;
  anchor?: "center" | "bottom" | "top";
};
```

Avatar, portrait, background, and user avatar framing are stored independently. The editor must show each real target aspect ratio, support drag and zoom, expose reset/fit actions, and preserve a readable safe area.

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

Theme mode supports dark, light, and follow-system. A character pack may recommend theme tokens, but explicit user choices win. Theme and framing previews may be local drafts; production save must use the existing character-pack/settings authority and report failure honestly.

Production V2 intentionally keeps shell presentation preferences instance-scoped on the local device. It stores theme/accent/font/opacity/dimming/blur/motion globally for that bound instance and framing separately per character pack. This is not character-pack authoring: the original assets and the desktop pet's calibrated layout remain unchanged, and a failed local write is shown as a failure rather than a saved state.

## 9. Chat surface contract

The dedicated chat surface solves the desktop bubble's deliberate ephemerality: it retains the visible conversation history, supports user and character avatars, and renders the same authoritative reply segments as the pet bubble.

It must not create a second competing character body:

- the desktop pet remains the full-body ambient presence;
- the chat panel uses a crop-aware bust/portrait camera for emotion and activity;
- the presence panel can be collapsed while history and composer remain usable;
- expression, thinking, tool use, approval, delivery, and failure states may affect the portrait/status treatment but must not become repeated synthetic chat messages;
- intermediate narration appears as a message only when it is part of the model's real user-visible speech;
- opening the chat panel never duplicates or replays already delivered bubbles.

User and character bubbles may have separate theme tokens. User avatar is a user preference, while the character avatar comes from the active character pack. Both use independent non-destructive framing.

The chat frame has a stable viewport: its header and composer remain fixed while only the message list scrolls. New content auto-scrolls only when the user is already near the bottom; otherwise the current reading position is preserved and an unread indicator is shown. Older history loads incrementally, and very long sessions may virtualize message rows without changing the authoritative message order.

## 10. Performance and motion gates

- Render shell/navigation from local state without waiting for backend health.
- Hydrate status cards independently; no full-page loading gate.
- Preserve last trustworthy state while refreshing and mark it stale if needed.
- Page changes must not re-decode the same portrait/background.
- Avoid layout shifts when asynchronous fields arrive.
- Button press feedback begins within one frame.
- Typical panel transitions target 160–220 ms.
- Respect `prefers-reduced-motion`.
- Slow, failed, repeated, and cancelled requests remain operable and visible.

## 11. Empty, slow, and failure states

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

## 12. Production migration gates

Production reintegration completed on 2026-08-21 after:

1. Overview and Character & Appearance are accepted visually.
2. Every displayed field maps to a real snapshot source or honest empty state.
3. Every action maps to an existing Tauri/backend boundary or is omitted.
4. The character workshop remains the single full-authoring authority.
5. Existing action bridge and runtime probe tests have a migration plan.
6. Build, UX smoke, narrow viewport, slow/failure, character switch, and manual Tauri checks pass.
7. Old render paths are deleted or reduced to thin adapters; no permanent V1/V2 dual authority.

## 13. Production component boundary

The prototype is intentionally self-contained; production must not grow as another single HTML/JS/CSS authority. The minimum boundary is:

- `ControlCenterShell`: navigation, page composition, overlays, toast region;
- `ControlCenterStore`: one normalized snapshot plus action states;
- `ControlCenterBridge`: the only UI-facing adapter for host/Tauri/backend operations;
- pages: Overview, Chat, Character & Appearance, Abilities, Diagnostics;
- reusable primitives: panel, status chip, action button, scroll region, empty/error state;
- domain components: message list, composer, presence camera, asset framing editor, media control;
- theme tokens: colors, typography, spacing, radius, opacity, motion, and contrast modes.

Page and visual components receive data and emit intents. They do not call Tauri or backend routes directly, own a second copy of conversation truth, or infer success from a request being queued. Framework choice is secondary to these boundaries; a production slice may use small ES modules first, but it must not add new features to the existing monolithic control-center file.
