# Petdesk Release Start Entry M53

Status: implemented in this slice.
Date: 2026-07-08

## Goal

M49-M52 made the release petdesk path real:

```text
M49: petdesk-runtime reads whitelisted launch env at runtime
M50: Akane starter can launch RuntimeMode Release
M51: release exe was rebuilt and manually accepted
M52: release build sequence is scripted
```

M53 makes the release start path easy to remember without changing the default
development launcher.

## Sources Checked

- `docs/petdesk_release_runtime_starter_m50.md`
- `docs/petdesk_release_runtime_acceptance_m51.md`
- `docs/petdesk_release_build_script_m52.md`
- `scripts/start_petdesk_runtime.ps1`
- `scripts/build_petdesk_runtime_release.ps1`
- `start_akane_petdesk.ps1`
- `start_akane_petdesk.bat`

## Decision

Add two thin convenience entries:

```text
start_akane_petdesk_release.ps1
start_akane_petdesk_release.bat
```

They are wrappers only. They do not replace:

```text
start_akane_petdesk.ps1
start_akane_petdesk.bat
```

The release wrapper always launches the starter with:

```text
RuntimeMode = Release
```

It accepts the same useful launch flags as the normal petdesk starter, plus:

```text
-BuildFirst
-SkipBuildWarmup
```

`-BuildFirst` calls:

```powershell
.\scripts\build_petdesk_runtime_release.ps1
```

before launching release mode. `-SkipBuildWarmup` is passed to the build helper
only when intentionally testing whether the serial Cargo warm-up is no longer
needed.

## Usage

Use an existing release exe:

```powershell
.\start_akane_petdesk_release.ps1
```

Build first, then start:

```powershell
.\start_akane_petdesk_release.ps1 -BuildFirst
```

Reuse an already running backend:

```powershell
.\start_akane_petdesk_release.ps1 -SkipBackend -BackendUrl http://127.0.0.1:9999
```

Check paths without launching:

```powershell
.\start_akane_petdesk_release.ps1 -CheckOnly
```

Dry-run build and launch steps:

```powershell
.\start_akane_petdesk_release.ps1 -BuildFirst -DryRun -SkipBackend -BackendUrl http://127.0.0.1:1 -HealthTimeoutSeconds 0
```

Batch entry:

```bat
start_akane_petdesk_release.bat -CheckOnly
```

## Boundary

In scope:

- release convenience wrapper;
- optional build-before-start flag;
- no-window checks and test locks.

Out of scope:

- changing the default petdesk starter from dev to release;
- changing public `start_akane_next.ps1` / `desktop_pet_next`;
- launching release mode from the Python backend;
- changing Akane backend, QQ, memory, prompt, model, or TTS behavior;
- producing installers or updaters.

## Validation Plan

```powershell
python -m unittest tests.test_petdesk_runtime_starter -v
python -m ruff check tests\test_petdesk_runtime_starter.py
python -m ruff format --check tests\test_petdesk_runtime_starter.py
python -m py_compile tests\test_petdesk_runtime_starter.py
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_akane_petdesk_release.ps1 -CheckOnly
cmd /c start_akane_petdesk_release.bat -CheckOnly
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_akane_petdesk_release.ps1 -BuildFirst -CheckOnly
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_akane_petdesk_release.ps1 -BuildFirst -DryRun -SkipBackend -BackendUrl http://127.0.0.1:1 -HealthTimeoutSeconds 0
git diff --check -- docs\petdesk_release_start_entry_m53.md start_akane_petdesk_release.ps1 start_akane_petdesk_release.bat tests\test_petdesk_runtime_starter.py
```

## Validation Run

Passed in this slice:

```text
tests.test_petdesk_runtime_starter: 12 OK
ruff check tests/test_petdesk_runtime_starter.py: OK
ruff format --check tests/test_petdesk_runtime_starter.py: OK
py_compile tests/test_petdesk_runtime_starter.py: OK
start_akane_petdesk_release.ps1 -CheckOnly: OK
start_akane_petdesk_release.bat -CheckOnly: OK
start_akane_petdesk_release.ps1 -BuildFirst -CheckOnly: OK
start_akane_petdesk_release.ps1 -BuildFirst -DryRun -SkipBackend -BackendUrl http://127.0.0.1:1 -HealthTimeoutSeconds 0: OK
git diff --check: OK
```

No runtime window, backend process, or QQ bot process was launched by these
checks.
