# Petdesk Release Acceptance M57

Status: implemented in this slice.
Date: 2026-07-08

## Goal

M56 added a read-only release doctor, but operators still had to remember the
right sequence for acceptance:

```text
doctor
-> startup-only smoke
-> optional full MVP smoke
```

M57 adds one acceptance entry:

```text
scripts/accept_petdesk_release.ps1
```

The acceptance entry does not replace the release starter. It is a no-window
acceptance runner for checking whether the current machine/backend/runtime
chain is ready to trust.

## Sources Checked

- `scripts/check_petdesk_release.ps1`
- `start_akane_petdesk_release.ps1`
- `scripts/start_petdesk_runtime.ps1`
- `scripts/tools/run_petdesk_mvp_smoke.py`
- `docs/petdesk_operator_guide_m54.md`
- `docs/petdesk_release_doctor_m56.md`
- `README.md`

## Default Flow

Default command:

```powershell
.\scripts\accept_petdesk_release.ps1
```

Default behavior:

1. Run `scripts/check_petdesk_release.ps1`.
2. Run `start_akane_petdesk_release.ps1 -SkipBackend -StartupSmokeOnly`.
3. Print `AKANE_PETDESK_RELEASE_ACCEPTANCE_OK` only if both steps pass.

The default does not start the backend. It expects the Akane backend to already
be running at `http://127.0.0.1:9999` or at the explicit `-BackendUrl`.

## Modes

Quick acceptance, same as default:

```powershell
.\scripts\accept_petdesk_release.ps1
```

Full acceptance with a real `/pet/turn` and audio check:

```powershell
.\scripts\accept_petdesk_release.ps1 -Full
```

Use a backend already running elsewhere:

```powershell
.\scripts\accept_petdesk_release.ps1 -BackendUrl http://127.0.0.1:9999
```

Allow the release wrapper to start the backend explicitly:

```powershell
.\scripts\accept_petdesk_release.ps1 -StartBackend -ReuseBackend
```

Check script wiring without doctor, smoke, backend, or runtime side effects:

```powershell
.\scripts\accept_petdesk_release.ps1 -CheckOnly
```

Skip the doctor only when intentionally isolating smoke behavior:

```powershell
.\scripts\accept_petdesk_release.ps1 -SkipDoctor
```

Skip smoke only when intentionally running doctor-only acceptance:

```powershell
.\scripts\accept_petdesk_release.ps1 -SkipSmoke
```

## Output Contract

The script prints structured line prefixes:

```text
[INFO] ...
[OK] ...
[WARN] ...
[FAIL] ...
```

It ends with one of:

```text
AKANE_PETDESK_RELEASE_ACCEPTANCE_OK
AKANE_PETDESK_RELEASE_ACCEPTANCE_FAILED
AKANE_PETDESK_RELEASE_ACCEPTANCE_CHECK_OK
```

Exit code is `0` only for OK and CheckOnly outcomes.

## Boundary

In scope:

- one command that sequences doctor and smoke;
- no-window release acceptance;
- explicit `-Full` mode for turn/audio acceptance;
- README/operator-guide discovery;
- tests that lock the script as an acceptance runner rather than a process
  supervisor.

Out of scope:

- replacing `start_akane_petdesk_release.ps1`;
- opening the runtime window;
- stopping `petdesk_runtime.exe`;
- silently starting the backend by default;
- installer packaging or auto-update.

## Validation Plan

```powershell
python -m unittest tests.test_petdesk_runtime_starter -v
python -m ruff check tests\test_petdesk_runtime_starter.py
python -m ruff format --check tests\test_petdesk_runtime_starter.py
python -m py_compile tests\test_petdesk_runtime_starter.py
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\accept_petdesk_release.ps1 -CheckOnly
git diff --check -- docs\petdesk_release_acceptance_m57.md docs\petdesk_operator_guide_m54.md scripts\accept_petdesk_release.ps1 README.md tests\test_petdesk_runtime_starter.py
```

## Validation Run

Passed in this slice:

```text
tests.test_petdesk_runtime_starter: 19 OK
ruff check tests/test_petdesk_runtime_starter.py: OK
ruff format --check tests/test_petdesk_runtime_starter.py: OK
py_compile tests/test_petdesk_runtime_starter.py: OK
accept_petdesk_release.ps1 -CheckOnly: OK
git diff --check: OK
```

`-CheckOnly` reported that no doctor, smoke, backend, or runtime process was
launched. Full/default acceptance was not run in validation because it requires
a deliberately selected live backend state.
