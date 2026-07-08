# Petdesk Startup Preflight M48

Status: implemented in this slice.
Date: 2026-07-08

## Goal

M47 added a cheap startup-only smoke, but a normal source-development launch
could still open `petdesk-runtime` without first proving that the startup
portrait resolves. That is exactly the class of regression that previously let
the runtime show a placeholder on first paint.

M48 makes the normal Akane petdesk starter run the startup-only smoke before
opening the Tauri window.

The user-facing invariant is:

```text
If the starter opens the petdesk window, the initial Akane static portrait chain
has already passed health -> manifest -> snapshot -> image fetch.
```

## Sources Checked

- `docs/petdesk_startup_only_smoke_m47.md`
- `docs/petdesk_startup_gate_m46.md`
- `docs/petdesk_akane_starter_m36.md`
- `scripts/start_petdesk_runtime.ps1`
- `start_akane_petdesk.ps1`
- `start_akane_petdesk.bat`
- `tests/test_petdesk_runtime_starter.py`

## Decision

Run startup-only smoke by default before the runtime window launches.

Default launch:

```powershell
.\start_akane_petdesk.ps1
```

Effective flow:

```text
start/reuse backend
-> GET /pet/health and collect whitelisted runtimeEnv
-> run scripts/tools/run_petdesk_mvp_smoke.py --startup-only
-> launch sibling petdesk-runtime
```

The default preflight does not post `/pet/turn`, so it does not call the model
or TTS provider.

## Escape Hatches

Use `-SkipStartupSmoke` when intentionally debugging a broken bridge or testing
runtime fallback behavior:

```powershell
.\start_akane_petdesk.ps1 -SkipStartupSmoke
```

Existing explicit smoke modes keep their previous meaning:

- `-StartupSmokeOnly`: run startup smoke and exit.
- `-SmokeOnly`: run the full MVP smoke and exit.
- `-RunStartupSmokeBeforeLaunch`: explicit spelling of the default preflight.
- `-RunSmokeBeforeLaunch`: run the full MVP smoke before launch.

`-CheckOnly` and `-DryRun` do not run preflight or launch the runtime.

## Boundary

In scope:

- default startup-only preflight in the Akane petdesk starter;
- a skip flag for intentional fallback debugging;
- wrapper pass-through for the root `start_akane_petdesk.ps1`;
- tests and docs that lock the startup-before-launch policy.

Out of scope:

- changing public `start_akane_next.ps1` / `desktop_pet_next` launcher;
- launching or supervising `petdesk-runtime` from the Python backend;
- changing `/pet/*` protocol shape;
- changing QQ, memory, prompt, model, or TTS behavior.

## Validation Plan

```powershell
python -m unittest tests.test_petdesk_runtime_starter -v
python -m ruff check tests\test_petdesk_runtime_starter.py
python -m ruff format --check tests\test_petdesk_runtime_starter.py
python -m py_compile tests\test_petdesk_runtime_starter.py
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_petdesk_runtime.ps1 -CheckOnly
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_akane_petdesk.ps1 -CheckOnly
cmd /c start_akane_petdesk.bat -CheckOnly
git diff --check -- docs\petdesk_startup_preflight_m48.md scripts\start_petdesk_runtime.ps1 start_akane_petdesk.ps1 tests\test_petdesk_runtime_starter.py
```

Live startup preflight without opening a runtime window remains:

```powershell
.\start_akane_petdesk.ps1 -SkipBackend -BackendUrl http://127.0.0.1:9999 -StartupSmokeOnly
```
