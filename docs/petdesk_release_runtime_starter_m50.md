# Petdesk Release Runtime Starter M50

Status: implemented in this slice.
Date: 2026-07-08

## Goal

M49 made `petdesk-runtime` able to read a safe, whitelisted launch-time
environment through the native `runtime_launch_env` command. That unblocks the
next Akane-side release step:

```text
Akane starter
-> start/reuse backend
-> GET /pet/health and whitelist runtimeEnv
-> optional startup smoke
-> launch built petdesk_runtime.exe with the same env
```

Before M50, the Akane petdesk starter always ended in `pnpm tauri:dev`. That is
correct for source development, but it does not exercise the built runtime path.
This does not replace the public desktop-pet launcher, and release mode is not
the default.

## Sources Checked

- `scripts/start_petdesk_runtime.ps1`
- `start_akane_petdesk.ps1`
- `start_akane_petdesk.bat`
- `docs/petdesk_akane_starter_m36.md`
- `docs/petdesk_startup_preflight_m48.md`
- `<workspace>\petdesk-runtime\docs\runtime_launch_env_m49.md`
- `<workspace>\petdesk-runtime\.cargo\config.toml`
- `<workspace>\petdesk-runtime\src-tauri\Cargo.toml`

## Decision

Add a starter mode:

```powershell
.\start_akane_petdesk.ps1 -RuntimeMode Release
```

Default remains:

```powershell
.\start_akane_petdesk.ps1 -RuntimeMode Dev
```

Release mode:

- keeps the existing backend start/reuse logic;
- keeps `/pet/health` runtime env whitelist and length checks;
- keeps the M48 startup-only smoke before opening the window unless
  `-SkipStartupSmoke` is passed;
- launches a resolved `petdesk_runtime.exe` with scoped process env;
- restores the caller process env after the runtime exits.

The starter can resolve the release exe from:

1. explicit `-RuntimeExe`;
2. `petdesk-runtime\.cargo\config.toml` `target-dir` plus
   `release\petdesk_runtime.exe`;
3. default Cargo/Tauri target locations under the runtime directory.

## Boundary

In scope:

- optional release exe launch path for the Akane-owned petdesk starter;
- root PowerShell wrapper pass-through;
- docs and tests for the new mode;
- check/dry-run validation that does not open the runtime window.

Out of scope:

- making release mode the default;
- replacing the public desktop-pet launcher;
- building the release exe automatically;
- fixing the local Windows release-toolchain `os error 5` blocker recorded in
  M49;
- changing public `start_akane_next.ps1` / `desktop_pet_next`;
- adding process supervision to the Python backend;
- changing QQ, memory, model, prompt, TTS, or `/pet/*` protocol behavior.

## Usage

Source-development launch remains:

```powershell
.\start_akane_petdesk.ps1
```

Release launch with auto-resolved exe:

```powershell
.\start_akane_petdesk.ps1 -RuntimeMode Release
```

Release launch with explicit exe:

```powershell
.\start_akane_petdesk.ps1 -RuntimeMode Release -RuntimeExe <cache-root>\cargo-target\petdesk-runtime\release\petdesk_runtime.exe
```

Dry-run without opening a window:

```powershell
.\start_akane_petdesk.ps1 -RuntimeMode Release -SkipBackend -BackendUrl http://127.0.0.1:1 -HealthTimeoutSeconds 0 -DryRun
```

## Validation Plan

```powershell
python -m unittest tests.test_petdesk_runtime_starter -v
python -m ruff check tests\test_petdesk_runtime_starter.py
python -m ruff format --check tests\test_petdesk_runtime_starter.py
python -m py_compile tests\test_petdesk_runtime_starter.py
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_petdesk_runtime.ps1 -CheckOnly
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_akane_petdesk.ps1 -CheckOnly
cmd /c start_akane_petdesk.bat -CheckOnly
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_petdesk_runtime.ps1 -RuntimeMode Release -CheckOnly
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_petdesk_runtime.ps1 -RuntimeMode Release -SkipBackend -BackendUrl http://127.0.0.1:1 -HealthTimeoutSeconds 0 -DryRun
git diff --check -- docs\petdesk_release_runtime_starter_m50.md scripts\start_petdesk_runtime.ps1 start_akane_petdesk.ps1 tests\test_petdesk_runtime_starter.py
```

Manual acceptance after the release build toolchain is healthy:

```powershell
.\start_akane_petdesk.ps1 -RuntimeMode Release -SkipBackend -BackendUrl http://127.0.0.1:9999
```

Expected result:

- startup smoke passes first;
- built `petdesk_runtime.exe` opens;
- startup portrait is Akane rather than the placeholder;
- the runtime reaches `backend:ready`;
- chat/TTS path behaves the same as the dev starter.
