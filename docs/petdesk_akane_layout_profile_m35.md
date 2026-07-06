# Petdesk Akane Layout Profile M35

Status: implemented; focused validation passed.
Date: 2026-07-06

## Goal

Make the Akane static-portrait runtime window feel closer to a desktop pet after M34 proved the real Tauri bridge works.

The target is not simply "make the window wider". The user-visible issue is:

- Akane's portrait looks smaller than expected for the amount of desktop area involved;
- transparent-looking areas can still feel like they block the desktop;
- the runtime should keep the host-neutral boundary and let Akane provide host-owned layout hints.

## Findings

M34 verified the real chain:

```text
Akane /pet/*
-> petdesk-runtime Tauri window
-> snapshot
-> resource manifest
-> static portrait
-> /pet/turn stream
```

The default Akane `/pet/snapshot` uses:

```text
assetHandle: akane_sample/static/default/normal
url: /petdesk-character-packs/akane_sample/assets/characters/default/normal.png
```

The real local image, relative to the Akane workspace, is:

```text
..\AkaneData\characters\akane_sample\assets\characters\default\normal.png
```

Alpha-bounds check for the image:

```text
source size: 1024 x 1536
alpha bbox: 971 x 1536
left transparent: 2.64%
right transparent: 2.54%
top transparent: 0%
bottom transparent: 0%
```

So the main problem is not a huge transparent margin baked into the PNG.

Current runtime defaults:

```text
window: 340 x 560
static image slot: left 18%, bottom 7%, width 64%, height 66%
native portrait hit regions: alpha bands, default 32 bands
M34 observed native region count: 35
```

The alpha-band native hit-test is better than a single full rectangle, but each band is still one min-x/max-x rectangle. That can include
transparent gaps between hair, body, hands, and ribbons, so "transparent" desktop area may still be treated as interactive.

## Design

M35 keeps the boundary:

- `petdesk-runtime` remains host-neutral.
- Akane provides runtime startup hints through `/pet/health.runtimeEnv`.
- The runtime starter must whitelist interaction profile env keys, just like it already whitelists Live2D layout env keys.
- Static portrait native hit-test should generate tighter segmented alpha regions, bounded under Rust's 128-region limit.

Akane profile direction:

- keep the window compact enough that invisible space is not excessive;
- enlarge the static portrait inside the window;
- move the portrait closer to the lower-left/center instead of a small centered slot;
- keep bubble and controls interactive;
- keep portrait dragging enabled.

Runtime hit-test direction:

- split each alpha band into contiguous alpha segments instead of one wide rectangle;
- cap total generated portrait regions so the Rust boundary remains safe;
- prefer the largest regions if the cap is exceeded;
- keep fallback behavior unchanged while the hit map is loading or failed.

## Implemented Changes

Akane:

- added `build_petdesk_runtime_env()` in `companion_v01/petdesk_bridge.py`;
- `/pet/health` now includes:
  - `runtimeEnv.VITE_PETDESK_INTERACTION_PROFILE=default`;
  - `runtimeEnv.VITE_PETDESK_INTERACTION_PROFILE_JSON=<compact JSON>`;
- the profile is host-owned starter glue. It does not enter `/pet/snapshot`, `/pet/turn`, or the display envelope;
- focused tests cover the health payload, profile shape, and absence of local character-pack paths in the env JSON.

petdesk-runtime:

- whitelisted `VITE_PETDESK_INTERACTION_PROFILE` and `VITE_PETDESK_INTERACTION_PROFILE_JSON` in the starter env bridge;
- updated starter env tests and docs;
- changed static image alpha hit regions from one rectangle per band to contiguous alpha segments;
- raised static image defaults to `maxNativeHitBands=64` and `maxNativeHitRegions=112`;
- committed as `274356f Tighten static portrait hit regions`.

## Akane Profile

The profile currently emitted by Akane is:

```text
window base size: 320 x 560
min/max scale: 0.7 / 1.45
bubble: top 12px, left 12px, max width min(236px, calc(100% - 72px))
drag handle: top 92px, left 88%
static slot: left 3%, bottom 0%, width 94%, height 90%
debug: right 10px, bottom 10px
native hit test: include bubble and controls
portrait drag: enabled
```

This is deliberately a startup hint, not a protocol field. A different host can keep using the same runtime protocol with its own interaction profile.

## Validation

Runtime validation, already passed before the runtime commit:

```powershell
cd ..\petdesk-runtime
pnpm verify
cargo fmt --manifest-path src-tauri\Cargo.toml --check
cargo check --manifest-path src-tauri\Cargo.toml
cargo test --manifest-path src-tauri\Cargo.toml --no-run
git diff --check
```

Akane focused validation:

```powershell
cd ..\AkaneCompanionLab
uv run --with-editable ..\charpack-core --with-editable ..\promptpack-core --with-editable ..\memcore --with-editable ..\capcore --with-editable ..\capcore-adapter-mcp --with-editable ..\capcore-adapter-python --with-editable ..\capcore-adapter-speech --with-editable ..\capcore-adapter-comfyui --with-editable ..\capcore-provider-native-tools --with-editable ..\capcore-provider-openai python -m unittest tests.test_petdesk_bridge -v
git diff --check -- docs\petdesk_akane_layout_profile_m35.md companion_v01\petdesk_bridge.py tests\test_petdesk_bridge.py
```

Focused test result:

```text
tests.test_petdesk_bridge: 5 tests OK
```

Manual acceptance after M35 is still useful, because the real desktop feel depends on the current monitor, scale factor, and Akane image asset:

- start Akane on `127.0.0.1:10033`;
- fetch `/pet/health` and verify `runtimeEnv.VITE_PETDESK_INTERACTION_PROFILE_JSON`;
- start `petdesk-runtime` with the backend URL and the profile JSON;
- verify Akane portrait appears larger;
- verify `S` still triggers `/pet/turn`;
- verify obvious transparent empty areas click through or at least feel less obstructive than M34.
