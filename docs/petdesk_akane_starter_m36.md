# Petdesk Akane Starter M36

Status: implemented; focused validation and manual starter acceptance passed.
Date: 2026-07-06

## Goal

Make the M35 Akane interaction profile enter a real startup path.

M35 made `/pet/health` expose:

```text
runtimeEnv.VITE_PETDESK_INTERACTION_PROFILE
runtimeEnv.VITE_PETDESK_INTERACTION_PROFILE_JSON
```

But a normal manual launch still required copying those values by hand before
running `petdesk-runtime`. M36 adds an Akane-owned starter that:

```text
Akane launcher
  -> starts or reuses Akane FastAPI backend
  -> GET /pet/health
  -> whitelist runtimeEnv
  -> spawn sibling petdesk-runtime with backend URL and profile env
```

This is a validation/starter path for the new runtime. It does not replace the
public `desktop_pet_next` one-click launcher yet.

## Sources Checked

- `docs/petdesk_akane_bridge_m32.md`
- `docs/petdesk_akane_e2e_acceptance_m33.md`
- `docs/petdesk_akane_layout_profile_m35.md`
- `start_akane_next.ps1`
- `scripts/bootstrap_akane_windows.ps1`
- `desktop_pet_next/scripts/start-next.ps1`
- `tests/test_windows_bootstrap.py`
- `petdesk-runtime/README.md`
- `petdesk-runtime/examples/runtime-starter.ts`
- `petdesk-runtime/examples/runtime-bridge-process.ts`
- `petdesk-runtime/docs/runtime_character_layout_starter_m27.md`

## Current Shape

Akane public launch path:

```text
start_akane.bat / 启动_Akane.bat
  -> scripts/bootstrap_akane_windows.ps1
  -> start_akane_next.ps1
  -> desktop_pet_next release/dev window
```

That path is still the supported public desktop pet path.

Petdesk runtime already has a generic starter for `petdesk-character-host`:

```text
pnpm example:starter
  -> start example character bridge
  -> fetch /pet/health runtimeEnv
  -> pnpm tauri:dev
```

For Akane, the missing piece is a host-side starter that points the sibling
runtime at Akane's real backend instead of the example bridge.

## Decision

Add a separate experimental Akane starter:

```text
scripts/start_petdesk_runtime.ps1
start_akane_petdesk.ps1
start_akane_petdesk.bat
```

The starter:

- locates the Akane project root;
- resolves sibling `..\petdesk-runtime` by default;
- optionally starts the Akane backend through `start_akane_next.ps1 -SkipDesktop`;
- fetches `/pet/health` with `Cache-Control: no-store`;
- accepts only known runtime env keys;
- ignores unknown keys, non-string values, empty values, and values over 20,000 characters;
- sets `VITE_PETDESK_BACKEND_URL`;
- invokes `pnpm tauri:dev` in `petdesk-runtime`;
- restores the process env after the runtime command exits.

The whitelist matches `petdesk-runtime/examples/runtime-bridge-process.ts`:

```text
VITE_PETDESK_INTERACTION_PROFILE
VITE_PETDESK_INTERACTION_PROFILE_JSON
VITE_PETDESK_LIVE2D_MODEL_LAYOUT_PROFILE
VITE_PETDESK_LIVE2D_MODEL_LAYOUT_JSON
VITE_PETDESK_LIVE2D_MOTION_MAP_JSON
VITE_PETDESK_LIVE2D_EXPRESSION_MAP_JSON
```

## Boundary

In scope:

- Akane-owned launch glue for local/manual petdesk acceptance;
- safe `runtimeEnv` transport from `/pet/health` to runtime process env;
- fallback to runtime defaults if health fetch or env parsing fails.

Out of scope:

- replacing `desktop_pet_next` as the default public launcher;
- packaging `petdesk-runtime` as Akane's release exe;
- adding petdesk process supervision to the Python backend;
- adding character-pack scanning to `petdesk-runtime`;
- making `/pet/*` the final canonical desktop-pet protocol.

## Usage

From Akane root:

```powershell
.\start_akane_petdesk.ps1
```

or:

```powershell
.\start_akane_petdesk.bat
```

Useful options:

```powershell
.\start_akane_petdesk.ps1 -BackendPort 10033
.\start_akane_petdesk.ps1 -SkipBackend -BackendUrl http://127.0.0.1:10033
.\start_akane_petdesk.ps1 -RuntimeDir ..\petdesk-runtime
.\start_akane_petdesk.ps1 -CheckOnly
.\start_akane_petdesk.ps1 -SkipBackend -BackendUrl http://127.0.0.1:10033 -DryRun
```

This runs `pnpm tauri:dev`, so it is a source-development starter. Release
packaging is a later decision because Vite `VITE_*` values are startup/build
inputs for the frontend bundle.

## Validation Plan

Focused automated checks:

```powershell
python -m unittest tests.test_petdesk_runtime_starter -v
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_petdesk_runtime.ps1 -CheckOnly
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_akane_petdesk.ps1 -CheckOnly
cmd /c start_akane_petdesk.bat -CheckOnly
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_petdesk_runtime.ps1 -SkipBackend -BackendUrl http://127.0.0.1:1 -HealthTimeoutSeconds 0 -DryRun
git diff --check -- docs\petdesk_akane_starter_m36.md scripts\start_petdesk_runtime.ps1 start_akane_petdesk.ps1 start_akane_petdesk.bat tests\test_petdesk_runtime_starter.py
```

Focused validation result:

```text
tests.test_petdesk_runtime_starter: 3 tests OK
script CheckOnly: OK
root wrapper CheckOnly: OK
batch wrapper CheckOnly: OK
DryRun fallback with unreachable backend: OK, kept only VITE_PETDESK_BACKEND_URL
ruff check: OK
ruff format --check: OK
py_compile: OK
git diff --check: OK
```

Manual acceptance:

- run `.\start_akane_petdesk.ps1`;
- confirm the log prints `Petdesk runtime env: VITE_PETDESK_INTERACTION_PROFILE,...`;
- confirm the runtime window reaches `backend:ready`;
- confirm Akane's M35 portrait layout is active without manual env copying;
- press `S` and confirm `/pet/turn` still streams a response;
- close the Tauri dev window and confirm env restoration does not leak profile values into the caller process.

Manual acceptance result is recorded in `docs/petdesk_akane_manual_acceptance_m37.md`.
