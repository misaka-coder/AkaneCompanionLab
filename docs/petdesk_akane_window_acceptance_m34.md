# Petdesk Akane Window Acceptance M34

Status: planned.
Date: 2026-07-06

## Goal

Run the actual `petdesk-runtime` Tauri window against Akane's M32 `/pet/*` bridge and record whether the visible desktop
experience works, not just the HTTP/parser contract.

This follows M33, where Level 1 and Level 2 passed:

- Akane `/pet/health`, `/pet/snapshot`, `/pet/turn` work from current source.
- `petdesk-runtime`'s own `PetBackendClient` and resource resolver can consume Akane events.
- static image URLs under `/petdesk-character-packs/...` can be fetched as PNG.

## Scope

In scope:

- start a temporary Akane backend from current source on `127.0.0.1:10033`;
- start `petdesk-runtime` with `VITE_PETDESK_BACKEND_URL=http://127.0.0.1:10033`;
- visually inspect the real Tauri window;
- press/click the runtime `S` control to trigger `/pet/turn`;
- record whether the bubble, debug panel, and static portrait update as expected.

Out of scope:

- replacing the old Akane desktop pet;
- changing Live2D integration;
- adding new runtime UI;
- running installer/package validation;
- treating this bridge as canonical without the M32 redundancy guard decision.

## Expected Visible Signals

Startup:

- a transparent frameless `Petdesk Runtime` window appears;
- the window does not stay on the offline-only demo state;
- debug panel reaches `backend:ready`;
- snapshot speech appears: `我在，petdesk runtime 已连接。`;
- visual renderer is `static_portrait`;
- portrait image is resolved from Akane's `/petdesk-character-packs/...` route.

Turn stream:

- clicking `S` starts a backend stream;
- debug panel changes to `backend:streaming` during the turn and returns to `backend:ready`;
- Akane backend logs a `POST /pet/turn`;
- bubble text updates to a real Akane reply;
- resource manifest update does not break the renderer;
- static portrait remains visible after update.

Failure is acceptable only if structured:

- backend error must show `backend:error` or a visible error/fallback state;
- no silent hang in `streaming`;
- no blank renderer after manifest update.

## Commands

Temporary Akane backend:

```powershell
cd <workspace>\AkaneCompanionLab
$env:PYTHONPATH='<workspace>\capcore;<workspace>\capcore-adapter-mcp;<workspace>\capcore-adapter-python;<workspace>\capcore-adapter-speech;<workspace>\capcore-adapter-comfyui;<workspace>\charpack-core;<workspace>\promptpack-core;<workspace>\capcore-provider-native-tools;<workspace>\capcore-provider-openai;<workspace>\memcore'
$env:COMPANION_HOST='127.0.0.1'
$env:COMPANION_PORT='10033'
.\.venv\Scripts\python.exe launch_akane_memory_v01.py
```

Runtime window:

```powershell
cd <workspace>\petdesk-runtime
$env:VITE_PETDESK_BACKEND_URL='http://127.0.0.1:10033'
pnpm tauri:dev
```

## Result Log

Attempt 1:

- temporary Akane backend on `127.0.0.1:10033` started successfully;
- `petdesk-runtime` was launched with `VITE_PETDESK_BACKEND_URL=http://127.0.0.1:10033`;
- the Tauri window did not open because Rust compilation failed before startup;
- failure point: `windows-sys` compilation could not execute `rustc.exe`;
- observed error: `拒绝访问。 (os error 5)`;
- Level 3 visible acceptance is not complete yet.

Next retry should use a fresh `CARGO_TARGET_DIR` to avoid a locked or poisoned target cache before considering deeper toolchain changes.

Attempt 2:

- temporary Akane backend on `127.0.0.1:10033` started successfully;
- `/pet/health` returned `ok: true`, `status: ready`, and the Akane character-pack manifest;
- retried `pnpm tauri:dev` with `CARGO_TARGET_DIR=<cache-root>\cargo-target\petdesk-runtime-m34`;
- the previous `windows-sys` failure did not reproduce, but compilation later failed while linking `web_atoms`;
- observed error: `could not exec the linker rust-lld.exe`, `拒绝访问。 (os error 5)`;
- direct `rust-lld.exe --version` was executable, so the failure appears to be Cargo/build-time process execution or local security interference rather than a missing binary;
- Level 3 visible acceptance is still not complete.

Attempt 3:

- retried the project default target directory with `CARGO_BUILD_JOBS=1`;
- compilation again failed before the Tauri window opened;
- observed error: `could not exec the linker rust-lld.exe`, `拒绝访问。 (os error 5)` while compiling `thiserror-impl`;
- this rules out a single poisoned fresh target directory and makes the blocker look like a local Rust linker/process execution issue;
- temporary Akane backend was stopped after the failed attempts;
- the existing Akane backend on port `9999` was not touched.

Current conclusion:

- Akane's `/pet/*` bridge still reaches `ready` from current source.
- The real Tauri window acceptance cannot be completed in this session because Rust compilation is blocked before runtime startup.
- No `petdesk-runtime` source changes were made for this retry.
- A practical next retry is to temporarily remove or override `.cargo/config.toml`'s `-C linker=rust-lld.exe`, or fix the local Windows security/toolchain issue that is denying Cargo's linker process.
- `link.exe` is not currently available in `PATH`, so switching to the MSVC linker is not available without extra environment/toolchain setup.

## Resume After Runtime Linker Fix

Date: 2026-07-06

`petdesk-runtime` commit `accc67f Relax Windows linker configuration` removed the default `rust-lld.exe` hard binding while keeping the
F-drive Cargo target directory. After that change, `cargo check` and `cargo test --no-run` passed in `petdesk-runtime`, so the real window
acceptance was retried.

Attempt 4:

- temporary Akane backend on `127.0.0.1:10033` started successfully;
- `/pet/health` returned `ok: true`, `status: ready`, and the Akane character-pack manifest;
- `pnpm tauri:dev` compiled and launched `petdesk_runtime.exe`;
- Akane backend logged `GET /pet/snapshot HTTP/1.1` with `200 OK`;
- the real Tauri window showed the Akane static portrait and snapshot speech;
- runtime debug panel showed `backend:ready`, `static_portrait`, `static_portrait:ready`, `manifest:update`, `audio:idle:0`, and
  `windows-wm-nchittest`;
- clicking the runtime `S` debug control triggered `OPTIONS /pet/turn` and `POST /pet/turn` with `200 OK`;
- Akane logged `petdesk_turn_complete` for session `petdesk`;
- the portrait resource under `/petdesk-character-packs/akane_sample/.../normal.png` returned `200 OK` or `304 Not Modified`;
- the window remained visible after the turn and did not fall into blank/error state.

Result:

- Level 3 real Tauri window acceptance passed for the current Akane `/pet/*` bridge.
- Remaining product issue: the default `340x560` runtime window is narrow for Akane's current static portrait and bubble. The bridge works,
  but layout/profile tuning is still needed before this feels like a polished replacement for the old desktop pet.
