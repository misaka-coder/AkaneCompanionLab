# Petdesk Runtime Architecture v0

Status: implementation planning document
Date: 2026-07-02

This document captures the implementation plan for a reusable desktop-pet
runtime based on TypeScript + Tauri v2. It is intentionally detailed so a human
or AI coding agent can continue after context compression.

The goal is not to copy Akane's current desktop frontend. Akane proves the
product direction, but its current `desktop_pet_next` runtime has accumulated
large JS/Rust files and mixed UI, protocol, system bridge, and product state.
The new runtime should learn from it, not inherit its shape.

## Decision

Use **TypeScript + Tauri v2** for the reusable desktop pet runtime.

Keep Python for the AI/backend side:

- LLM/provider clients;
- `memcore`, `promptpack-core`, `charpack-core`, `capcore`;
- product-specific tools and financial data adapters;
- host API, auth, storage, and deployment policy.

Keep Tauri/TypeScript for the pet runtime:

- desktop window shell;
- static image and Live2D rendering;
- speech bubbles and interaction state;
- TTS playback queue and lip-sync signal consumption;
- transparent window, hit testing, drag/resize, always-on-top;
- backend event bridge over HTTP/SSE/WebSocket.

## Why Not Reuse Akane Frontend Directly

Useful Akane experience:

- `desktop_pet_next` already validates Tauri v2 + transparent frameless window;
- it has a Windows `WM_NCHITTEST` hook for coarse click-through regions;
- it has a `visual-renderer.js` adapter that separates static portrait from
  future Live2D in spirit;
- it has control-center, TTS, ASR, local music, screen vision, and workspace
  experiments that reveal product needs.

Problems to avoid:

- `src/main.js` is too broad and mixes runtime state, UI rendering, protocol,
  backend calls, hit-test sync, audio, music, settings, and product behavior;
- `src-tauri/src/main.rs` is already a large system bridge file;
- control-center UI is product-specific and should not become framework API;
- current hit-test is coarse polygon/rect based, not true per-pixel portrait
  shape;
- Live2D is still only a placeholder/WebGL probe.

The new runtime should be a clean package, with Akane as one host.

## Package Split

Use two layers.

### `petcore-protocol`

Protocol/schema package shared by hosts and desktop clients.

Recommended initial form:

- JSON Schema files as the canonical contract;
- TypeScript types + Zod validators for frontend/runtime;
- optional Python Pydantic/dataclass adapter later if Akane needs direct
  backend validation;
- no Tauri, no Live2D, no renderer, no product prompts.

Owns:

- assistant display envelope schema;
- speech segment schema;
- visual state schema;
- TTS/audio request/result schema;
- action/event names;
- fallback rules by client mode;
- validation/normalization helpers.

Does not own:

- UI rendering;
- windowing;
- model calls;
- memory;
- character pack scanning;
- actual TTS or audio playback.

### `petdesk-runtime`

Tauri v2 + TypeScript desktop runtime.

Owns:

- transparent desktop window;
- renderer adapters: static portrait first, Live2D later;
- bubble queue and display timing;
- motion state machine;
- TTS playback queue and lip-sync hooks;
- backend bridge;
- window shape/hit-test bridge;
- packaging smoke and runtime diagnostics.

Does not own:

- LLM backend;
- financial logic;
- Akane persona/resource rules;
- character-pack authoring UI;
- long-term memory;
- trading/order execution.

## Repository Shape

Planned standalone package:

```text
petdesk-runtime/
  AGENTS.md
  README.md
  docs/
    architecture_v0.md
    window_hit_test_research_v0.md
    live2d_integration_v0.md
    packaging_v0.md
  examples/
    minimal-static-pet/
  src/
    app/
      bootstrap.ts
      runtime.ts
    bridge/
      backend-client.ts
      tauri-commands.ts
      event-stream.ts
    protocol/
      display-envelope.ts
      validators.ts
    state/
      pet-state.ts
      motion-state-machine.ts
      bubble-queue.ts
      audio-queue.ts
    renderer/
      renderer-contract.ts
      static-portrait-renderer.ts
      live2d-renderer.ts
      renderer-factory.ts
    hit-test/
      hit-region.ts
      alpha-mask.ts
      hit-test-sync.ts
    ui/
      PetStage.svelte
      BubbleLayer.svelte
      DebugOverlay.svelte
      SettingsPanel.svelte
  src-tauri/
    src/
      main.rs
      commands/
        window.rs
        hit_test.rs
        assets.rs
        diagnostics.rs
      platform/
        windows_hit_test.rs
        macos_hit_test.rs
        linux_hit_test.rs
```

Akane can later depend on this runtime or copy the example shell first. Avoid
making `petdesk-runtime` import Akane paths directly.

## Tooling

`uv` and `ruff` still apply to Python packages. For this TypeScript/Tauri
runtime, use the Node/Rust equivalents:

- package manager: `pnpm`;
- frontend build: `vite`;
- frontend language: strict `typescript`;
- UI layer: `svelte` for small reactive components and settings panels;
- schema validation: `zod`;
- format/lint: `@biomejs/biome` as the closest practical equivalent to ruff;
- tests: `vitest`;
- optional browser/runtime tests: Playwright later, only after the shell exists;
- Rust checks: `cargo fmt --check`, `cargo clippy`, `cargo check`;
- Tauri: `@tauri-apps/api`, `@tauri-apps/cli`, selected official plugins.

Current local tool availability on this machine:

```text
node: v24.18.0
npm: 11.16.0
pnpm: 10.32.1
```

Useful package versions observed through `npm view` on 2026-07-02:

```text
@tauri-apps/api: 2.11.1
@tauri-apps/cli: 2.11.4
@tauri-apps/plugin-http: 2.5.9
@tauri-apps/plugin-global-shortcut: 2.3.2
@tauri-apps/plugin-fs: 2.5.1
@tauri-apps/plugin-dialog: 2.7.1
typescript: 6.0.3
vite: 8.1.3
svelte: 5.56.4
@sveltejs/vite-plugin-svelte: 7.1.2
@biomejs/biome: 2.5.2
vitest: 4.1.9
zod: 4.4.3
pixi.js: 8.19.0
mitt: 3.0.1
nanoid: 5.1.16
ts-pattern: 5.9.0
@types/node: 26.1.0
```

Do not install these globally. Add them as project dependencies when the
standalone package is scaffolded, so builds are reproducible.

Recommended initial package commands:

```json
{
  "scripts": {
    "dev": "vite --host 127.0.0.1 --port 1420",
    "build": "vite build",
    "typecheck": "tsc --noEmit",
    "lint": "biome check .",
    "format": "biome format --write .",
    "format:check": "biome format .",
    "test": "vitest run",
    "tauri": "tauri",
    "tauri:dev": "tauri dev",
    "tauri:build": "tauri build",
    "verify": "pnpm typecheck && pnpm lint && pnpm test && pnpm build"
  }
}
```

## Development And Distribution Environment

Developer/build machine on Windows needs:

- Node.js + pnpm;
- Rust stable MSVC toolchain;
- Microsoft C++ Build Tools / Visual Studio Build Tools with Desktop C++;
- WebView2 runtime for development;
- optional WiX if building MSI installers.

End users should not need Rust, Visual Studio, Node, or pnpm. They should get a
Tauri-built installer/exe plus any bundled Python backend or sidecar runtime.

Windows distribution notes:

- prefer NSIS setup exe first for normal users;
- use MSI only if there is a clear enterprise deployment need;
- default WebView2 installation mode downloads the bootstrapper when runtime is
  missing;
- for offline client environments, use WebView2 `offlineInstaller` despite the
  much larger installer;
- do not use `skip` unless the installer separately verifies WebView2.

Python backend distribution options for a product:

- simplest development path: frontend points to a separately running Python
  backend;
- first customer MVP path: ship a launcher that starts a bundled backend exe;
- later polished path: Tauri sidecar starts/stops the backend and reports health.

## Frontend/Backend Boundary

The runtime communicates with the host backend through a stable protocol:

```text
petdesk-runtime
  -> GET /pet/health
  -> GET /pet/snapshot
  -> POST /pet/turn
  -> GET /pet/events   (SSE or WebSocket later)
  -> POST /pet/audio/stop
  -> POST /pet/session/new
```

The exact routes are product-owned. The protocol shape should be shared through
`petcore-protocol`.

Backend response envelope:

```json
{
  "schemaVersion": "pet.display.v1",
  "turnId": "turn_...",
  "speech": "visible plain speech",
  "segments": [
    {"id": "seg_1", "text": "first bubble", "voice": {"enabled": true}}
  ],
  "visual": {
    "renderer": "static_portrait",
    "emotion": "happy",
    "outfit": "default",
    "motion": "idle",
    "scene": null
  },
  "audio": {
    "tts": {"enabled": true, "voiceProfileId": "host-owned-id"},
    "bgm": null
  },
  "actions": [],
  "safety": {
    "status": "ok",
    "warnings": []
  }
}
```

Rules:

- host can include handles/ids, not local absolute paths;
- runtime validates with Zod before rendering;
- invalid display payload produces a structured fallback bubble;
- model/tool raw JSON is not rendered directly;
- product-specific financial disclaimers belong in backend/prompt/output policy,
  not in the generic runtime.

## Runtime Modules

### `PetRuntime`

Coordinates backend events, state, renderer, bubbles, and audio.

Responsibilities:

- boot with saved local UI state;
- fetch backend snapshot;
- subscribe to events;
- validate display envelopes;
- dispatch to renderer, bubble queue, audio queue;
- expose debug snapshot.

### `BackendBridge`

Owns all host communication.

Responsibilities:

- timeout/retry policy;
- online/offline status;
- SSE/WebSocket reconnect;
- request cancellation when user interrupts reply;
- no UI state.

### `RendererContract`

Common API for static image and Live2D renderers.

```ts
interface PetRenderer {
  readonly mode: "static_portrait" | "live2d";
  mount(target: HTMLElement): Promise<void>;
  setVisual(visual: PetVisualState): Promise<void>;
  setMotion(motion: PetMotionRequest): Promise<void>;
  setLipSync(level: number): void;
  getHitRegions(): PetHitRegion[];
  getAlphaMask?(): PetAlphaMask | null;
  destroy(): Promise<void>;
}
```

Static portrait renderer comes first. Live2D plugs into the same contract later.

### `MotionStateMachine`

States:

```text
idle
  -> thinking
  -> speaking
  -> interrupted
  -> idle

idle
  -> local_reaction
  -> idle
```

Rules:

- local clicks cannot override active backend speaking unless policy allows;
- stop/interrupt clears bubble/audio queues and returns to idle;
- renderer-specific motion names are mapped from generic motion intents.

### `BubbleQueue`

Responsibilities:

- segment splitting fallback;
- one bubble at a time;
- timing based on text length and TTS state;
- non-interactive bubble layer unless a product explicitly makes it clickable.

### `AudioQueue`

Responsibilities:

- play TTS audio by handle/blob/url;
- expose RMS/volume sample for lip-sync;
- stop current audio on interrupt;
- never fetch arbitrary local file paths from backend text.

## Window Shape And Click-Through

This is the hardest desktop-pet-specific part.

Akane current state:

- Tauri config already uses transparent, frameless, always-on-top window;
- Rust side uses Windows `SetWindowSubclass` and `WM_NCHITTEST`;
- hit regions are coarse rectangles/polygons synced from JS;
- transparent blank areas can return `HTTRANSPARENT`;
- per-pixel alpha-mask hit testing is deferred.

New runtime should support three implementation tiers.

### Tier 1: Whole-window pass-through probe

Use Tauri window ignore-cursor-events for debugging or temporary full
click-through.

Pros:

- easy;
- useful as a diagnostic.

Cons:

- entire pet becomes unclickable;
- not acceptable as the final desktop-pet interaction model.

### Tier 2: Region hit-test

Frontend sends `PetHitRegion[]` to Rust.

Rust uses platform-specific hit-test APIs:

- Windows: `WM_NCHITTEST`, return `HTCLIENT` or `HTTRANSPARENT`;
- macOS/Linux: later platform adapters, initially degrade to rectangular
  window interaction.

Pros:

- already partly proven by Akane;
- fast and stable;
- works for static image and Live2D approximate regions.

Cons:

- not pixel-perfect;
- needs tuning per renderer/model/layout.

### Tier 3: Alpha-mask hit-test

Frontend or Rust computes an alpha mask for the rendered character.

Static image path:

1. load portrait image;
2. read alpha channel into a compact mask;
3. map window cursor coordinates to image pixel coordinates;
4. alpha above threshold means interactive; alpha below threshold means
   transparent/pass-through.

Live2D path:

1. use model hit areas/raycasting for first pass;
2. optionally render a low-resolution offscreen alpha mask at motion keyframes;
3. throttle updates to avoid `readPixels` performance problems;
4. keep coarse fallback regions when mask is stale.

Pros:

- closest to the "smart outline" desktop-pet feel;
- solves the large invisible rectangle problem.

Cons:

- platform and GPU details matter;
- Live2D masks are dynamic;
- needs careful DPI/scale mapping;
- must be tested across Windows scaling modes.

The "XOR" technique the user remembered is likely a mask-composition or legacy
window-region trick. Treat XOR/AND/OR as implementation details inside mask
generation, not as the architecture. The architecture is: alpha mask + hit-test
+ platform window pass-through.

## Live2D Plan

Live2D is feasible with Tauri because Tauri's frontend is a WebView and Live2D
has a Web SDK. The renderer should be optional and isolated.

Preferred path:

1. start with official Live2D Cubism SDK for Web for license clarity;
2. use PixiJS integration only after checking compatibility and license;
3. never commit proprietary Cubism Core files into a public repo unless license
   terms allow it;
4. keep a static portrait fallback for every character.

Live2D renderer responsibilities:

- load model by host-approved asset handle;
- map protocol `emotion` to expression files;
- map protocol `motion` to motion groups;
- support idle/breath/eye blink/look-at;
- expose `setLipSync(level)` for TTS-driven mouth movement;
- expose coarse hit areas before alpha-mask support;
- report readiness/failure without breaking the rest of the pet.

Do not block the first product MVP on Live2D. The runtime should make static
portrait excellent first, then add Live2D as a renderer plugin.

## Security And Capability Scope

Tauri capabilities should be minimal:

- allow HTTP only to configured backend origins;
- allow asset protocol only for runtime-approved character/audio cache dirs;
- file system plugin only if needed, scoped narrowly;
- dialog plugin for user-picked files only;
- global shortcut plugin only after user-visible settings exist;
- no arbitrary shell plugin in the desktop runtime.

Avoid:

- `csp: null` in the new reusable runtime;
- rendering backend-provided HTML;
- storing API keys in frontend local storage;
- sending local absolute paths to the model;
- allowing model text to choose local files or Live2D model paths.

## Packaging Strategy

Initial Windows product:

```text
petdesk-runtime setup.exe
  -> Tauri shell
  -> bundled static assets
  -> optional bundled backend sidecar
  -> app data under %LOCALAPPDATA%/<ProductName>/
```

Recommended packaging milestones:

1. `tauri build` produces a local installer on developer machine.
2. installer launches static offline pet with no backend.
3. installer launches pet + connects to a manually running backend.
4. sidecar backend is bundled and started by Tauri.
5. installer verifies WebView2 mode and logs clear diagnostics.
6. code signing and auto-update only after MVP behavior is stable.

User-facing installer should not require:

- Rust;
- Visual Studio Build Tools;
- Node.js;
- pnpm;
- Python source checkout.

## MVP Milestones

### M0: Planning And Source Audit

Done in this document:

- Tauri v2 chosen;
- Akane current hit-test and Live2D placeholder reviewed;
- package/tooling recommendations captured.

### M1: `petcore-protocol`

Create standalone protocol package:

- JSON Schema or Zod-first schema;
- display envelope;
- visual/audio/action schemas;
- normalization and fallback helpers;
- README + AGENTS + usage flow;
- tests for invalid payloads.

### M2: `petdesk-runtime` Scaffold

Create standalone Tauri v2 app/package:

- pnpm + Vite + TypeScript + Svelte;
- Biome + Vitest;
- strict CSP baseline;
- transparent frameless window;
- static offline demo character;
- `pnpm verify`, `cargo check`, `tauri build` smoke.

### M3: Static Portrait Runtime

Implement:

- static portrait renderer;
- bubble queue;
- local click reactions;
- basic settings/debug overlay;
- backend health and snapshot fetch;
- protocol validation.

### M4: Window Shape Research

Implement and document:

- whole-window pass-through diagnostic;
- Windows region hit-test using `WM_NCHITTEST`;
- static image alpha-mask hit-test;
- DPI/scale tests;
- failure fallback to region hit-test.

### M5: Backend Bridge

Implement:

- request/response turn call;
- cancellation/interrupt;
- SSE/WebSocket event path;
- offline fallback;
- integration with Akane or financial companion backend.

### M6: Live2D Spike

Implement behind a feature flag:

- load one licensed sample model locally;
- expression/motion mapping;
- mouth/lip-sync signal from audio queue;
- model hit areas;
- static fallback if Live2D fails.

### M7: Product Packaging

Implement:

- Windows setup exe;
- backend sidecar experiment;
- install/uninstall smoke;
- user data root migration policy;
- diagnostics page for WebView2/backend/assets.

## Validation Commands

After scaffold exists:

```powershell
pnpm install
pnpm verify
pnpm tauri:dev
pnpm tauri:build
cargo fmt --manifest-path src-tauri/Cargo.toml --check
cargo clippy --manifest-path src-tauri/Cargo.toml -- -D warnings
cargo check --manifest-path src-tauri/Cargo.toml
git diff --check
```

For Akane integration experiments:

```powershell
.\.venv\Scripts\python.exe .\scripts\smoke_ai_product_host_turn.py
.\.venv\Scripts\python.exe .\scripts\smoke_extracted_package_ecosystem.py
.\.venv\Scripts\python.exe .\scripts\audit_extracted_packages_release.py --allow-dirty
git diff --check
```

## Open Questions

- Should `petcore-protocol` be TypeScript-first with generated Python helpers,
  or JSON-Schema-first with both sides generated?
- Should `petdesk-runtime` be a library package plus example app, or a
  template app first?
- Should financial companion MVP fork Akane's current Tauri shell short-term,
  or start directly on `petdesk-runtime` once M2 exists?
- Which Live2D SDK/license path is acceptable for public distribution?
- Do we need multi-platform desktop pet behavior, or is Windows-first enough
  for the first product?

## Recommended Immediate Next Step

Do **not** start by rewriting all of Akane's desktop pet.

Start with `petcore-protocol` and a very small `petdesk-runtime` scaffold:

1. write the display envelope schema;
2. create a static portrait Tauri window;
3. validate one backend-like JSON payload;
4. render one bubble and one emotion switch;
5. add region hit-test as a separate module;
6. only then connect Akane/financial companion backend.

This gives the委托 project a practical path while keeping the reusable runtime
clean.

## References

- Tauri v2 prerequisites: <https://v2.tauri.app/start/prerequisites/>
- Tauri v2 config reference: <https://v2.tauri.app/reference/config/>
- Tauri v2 Windows installer: <https://v2.tauri.app/distribute/windows-installer/>
- Live2D Cubism SDK manual: <https://docs.live2d.com/en/cubism-sdk-manual/top/>
- Live2D Cubism SDK for Web: <https://docs.live2d.com/en/cubism-sdk-manual/cubism-sdk-for-web/>
