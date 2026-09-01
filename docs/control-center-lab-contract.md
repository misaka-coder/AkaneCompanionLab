# Akane Control Center Runtime Contract

> Current authority as of 2026-09-01. Historical mock-first and monolithic-control-center notes were removed after the V2 cutover.

## One Production Entry

`desktop_pet_next/control-center-lab.html` is the only settings implementation. It loads `src/control-center-v2/index.js` directly.

- `settings.html` and `control-center-v2.html` are compatibility redirects.
- `src/control-center-lab.js` is a two-line compatibility import and owns no behavior.
- The frozen Electron `desktop_pet` tree is not a second control-center implementation.

## Runtime Data Flow

The production flow is:

```text
backend snapshot + Tauri live state
        ↓
src/control-center/data-sources.js
        ↓
src/control-center-v2/bridge.js
        ↓
src/control-center-v2/store.js
        ↓
src/control-center-v2/view-model.js
        ↓
src/control-center-v2/components/*
```

There is no mock-first page model, snapshot adapter, or parallel Tauri data source.

### Backend facts

`createBackendControlCenterSource()` reads `/control-center/snapshot` first. The snapshot contains the five backend-owned runtime fields:

- `health`
- `diagnostics`
- `workspace`
- `resourceManifest`
- `metrics`

When the unified endpoint is unavailable or invalid, the same source reads the corresponding individual endpoints. This fallback is protocol compatibility, not a second UI authority. An unavailable backend produces an honest disconnected/empty state; it never falls back to demo data.

Capabilities, voice profiles, approval requests, model settings, Bot catalog, and chat sessions use their dedicated backend routes. Slow optional catalogs must degrade independently and must not block the five-field runtime snapshot.

### Desktop live state

The Tauri `SETTINGS_SNAPSHOT_EVENT` carries high-frequency desktop-owned facts such as current character, expression, music progress, window settings, and presentation state. It patches the V2 store through the bridge. It does not replace backend health, memory, capability, or workspace facts and does not trigger full backend hydration on every progress tick.

Backend and Tauri state may describe different domains. The view model merges them by field ownership; it does not pass either payload through a generic deep-merge adapter.

## Action Flow

Interactive controls use stable action IDs from `src/control-center/action-router.js`:

```text
component data-action-id
        ↓
V2 delegated event handler
        ↓
createControlCenterActionRouter
        ↓
createBackendControlCenterSource.runAction
        ↓
Tauri invoke / settings event / window API / dedicated backend route
```

The source exposes only actions with a real execution boundary. A missing boundary returns:

```json
{"ok":false,"status":"not-implemented","actionId":"…","refresh":false}
```

Local expand/collapse state belongs to V2 components/store and is not represented as a fake backend action. Future-only controls stay in planning documents; they do not enter production constants or UI as disabled placeholders.

### Action families

- Chat and desktop settings emit the shared settings command event.
- Workspace, character workshop/folder, managed skills folder, and voice-profile assignment use Tauri invokes.
- Window minimize/maximize use the Tauri window API; close uses the Tauri command.
- Provider, MCP, workflow, approval, and QQ self-check actions use dedicated backend routes.
- Music actions target the currently active local/system playback owner through the desktop runtime.

Every successful mutation returns a structured result. `refresh:true` requests authoritative state confirmation; client-only presentation changes use `refresh:false`.

## Chat Contract

The chat page has one message-list writer. Initial hydration and explicit refresh read the session through `/sessions/ensure`; older-page loading uses `/sessions/messages` and merges by stable sequence/message identity.

- Sending may show an optimistic pending row immediately.
- Backend history is authoritative after confirmation.
- Loading, thinking, TTS, and transient delivery status never replace the persisted conversation list.
- Switching Bot, session, or character clears incompatible local state before hydrating the new scope.
- HTTP/auth/session failures remain visible as structured UI failure; the list is not silently reset to empty.

## Character and Media Contract

Character pack metadata comes from the backend resource manifest plus the Tauri-installed pack registry. Public projections omit installation paths and source paths. The selected outfit and expression are live desktop state; previewing them must update both the large preview and its cards without waiting for backend polling.

Music state identifies its control target (`local`, `system`, or `none`). Playback buttons operate on that target and then reconcile with a fresh snapshot. TTS preview and music playback have separate activity states and must not overwrite each other.

## Privacy Boundary

The control center may show user-facing capability status, safe resource handles, filenames, model/provider names, and task-relevant paths entered by the user. It must not serialize or render:

- API keys, cookies, tokens, or credential values
- internal database/cache/run-log paths
- MCP environment values or full private launch commands
- character-pack install/source paths in public snapshot cards

Private values remain behind profile-scoped write routes or Tauri commands.

## Verification

From `desktop_pet_next/`:

```bash
npm run smoke:control-center-actions
npm run smoke:control-center-v2
npm run probe:control-center-runtime
npm run verify:control-center
```

The verification matrix checks:

- production action mappings and structured failure behavior;
- unified snapshot projection and individual-endpoint fallback;
- Tauri live-state patches and V2 view-model behavior;
- chat hydration/history continuity;
- absence of the deleted mock/adapter implementation;
- production build and rendered UX structure.

Backend route contracts remain covered by:

```bash
python -m unittest tests.test_backend_route_modules
python -m unittest tests.test_desktop_pet_frontend_contract
```

## Change Rules

When adding a control-center capability:

1. Identify the real owner: backend fact, Tauri live state, local presentation state, or external provider.
2. Add one action/data path at that owner boundary.
3. Project only the fields the current V2 view model consumes.
4. Add a smoke assertion against the real source/bridge path.
5. Do not reintroduce mock-first rendering, generic page adapters, or a second settings implementation.
