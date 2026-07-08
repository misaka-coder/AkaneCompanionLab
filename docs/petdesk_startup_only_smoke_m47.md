# Petdesk Startup-Only Smoke M47

Status: implemented in this slice.
Date: 2026-07-08

## Goal

M46 made first-paint resource resolution part of the full MVP smoke. That
worked, but the full smoke still posts a real `/pet/turn`, which can call the
model and TTS provider.

M47 splits out a cheap startup-only smoke for the common question:

```text
Will petdesk-runtime resolve the initial Akane portrait instead of showing the
runtime placeholder?
```

The startup-only smoke must prove the startup manifest/snapshot/image chain
without sending a user turn.

## Sources Checked

- `docs/petdesk_startup_gate_m46.md`
- `docs/petdesk_akane_mvp_closeout_m41.md`
- `scripts/tools/run_petdesk_mvp_smoke.py`
- `scripts/start_petdesk_runtime.ps1`
- `start_akane_petdesk.ps1`
- `start_akane_petdesk.bat`
- `tests/test_petdesk_mvp_smoke.py`
- `tests/test_petdesk_runtime_starter.py`

## Decision

Add a startup-only mode to the existing Akane-owned smoke tool:

```powershell
python scripts\tools\run_petdesk_mvp_smoke.py --base-url http://127.0.0.1:9999 --startup-only
```

The mode checks only:

1. `/pet/health` is ready.
2. `runtimeEnv.VITE_PETDESK_RESOURCE_MANIFEST_URL` is present.
3. The startup resource manifest has `staticImages`.
4. `/pet/snapshot.visual.assetHandle` is safe.
5. `staticImages[assetHandle]` exists.
6. The mapped image URL is under an allowed petdesk resource prefix.
7. The mapped image is fetchable as non-empty `image/*`.

It returns a summary with `mode: "startup_only"` and does not post
`/pet/turn`.

Keep the full MVP smoke as the stronger end-to-end gate:

```powershell
python scripts\tools\run_petdesk_mvp_smoke.py --base-url http://127.0.0.1:9999
```

## Starter Flags

Expose the startup-only mode through the Akane starter:

```powershell
.\start_akane_petdesk.ps1 -StartupSmokeOnly
.\start_akane_petdesk.ps1 -RunStartupSmokeBeforeLaunch
```

`-SmokeOnly` and `-RunSmokeBeforeLaunch` still run the full MVP smoke and may
trigger `/pet/turn` plus TTS.

## Boundary

In scope:

- Python smoke `--startup-only`;
- PowerShell starter flags for startup-only smoke;
- fake HTTP tests proving no `/pet/turn` is posted;
- doc/test locks for the new distinction.

Out of scope:

- changing normal launcher defaults;
- changing `/pet/*` contracts;
- launching/screenshotting Tauri;
- changing QQ, memory, prompts, or TTS provider logic.

## Validation Plan

```powershell
python -m unittest tests.test_petdesk_mvp_smoke tests.test_petdesk_runtime_starter -v
python -m ruff check scripts\tools\run_petdesk_mvp_smoke.py tests\test_petdesk_mvp_smoke.py tests\test_petdesk_runtime_starter.py
python -m ruff format --check scripts\tools\run_petdesk_mvp_smoke.py tests\test_petdesk_mvp_smoke.py tests\test_petdesk_runtime_starter.py
python -m py_compile scripts\tools\run_petdesk_mvp_smoke.py tests\test_petdesk_mvp_smoke.py tests\test_petdesk_runtime_starter.py
git diff --check -- docs\petdesk_startup_only_smoke_m47.md scripts\tools\run_petdesk_mvp_smoke.py scripts\start_petdesk_runtime.ps1 start_akane_petdesk.ps1 tests\test_petdesk_mvp_smoke.py tests\test_petdesk_runtime_starter.py
```

Manual/live startup smoke:

```powershell
.\start_akane_petdesk.ps1 -SkipBackend -BackendUrl http://127.0.0.1:9999 -StartupSmokeOnly
```

Expected marker:

```text
AKANE_PETDESK_STARTUP_SMOKE_OK
```
