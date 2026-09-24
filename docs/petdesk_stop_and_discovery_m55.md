# Petdesk Stop And Discovery M55

Status: implemented in this slice.
Date: 2026-07-08

## Goal

M54 documented the operator path, but stopping the release runtime still relied
on manually typing:

```powershell
Get-Process -Name petdesk_runtime -ErrorAction SilentlyContinue | Stop-Process
```

M55 adds a small, safer stop helper and makes the operator guide discoverable
from the main README.

## Sources Checked

- `docs/petdesk_operator_guide_m54.md`
- `scripts/build_petdesk_runtime_release.ps1`
- `scripts/start_petdesk_runtime.ps1`
- `start_akane_petdesk_release.ps1`
- `README.md`

## Decision

Add:

```text
scripts/stop_petdesk_runtime.ps1
```

Default behavior:

- resolve the explicitly configured `petdesk-runtime` release exe path;
- find running `petdesk_runtime.exe` processes whose `Path` matches that exe;
- try graceful `CloseMainWindow()`;
- wait briefly;
- report remaining processes instead of force-killing them.

Explicit escape hatches:

- `-Force`: force-kill matched petdesk runtime processes after graceful close
  timeout.
- `-All`: match all `petdesk_runtime.exe` processes, not only the resolved
  release exe path.
- `-RuntimeExe`: target an explicit release exe path.
- `-CheckOnly`: list matched processes without closing anything.

The script does not stop Akane backend, QQ bot, Python, Node, Rust, or browser
processes.

## Usage

List the release petdesk process candidates:

```powershell
.\scripts\stop_petdesk_runtime.ps1 -CheckOnly
```

Gracefully close the release petdesk window:

```powershell
.\scripts\stop_petdesk_runtime.ps1
```

Force-stop only if graceful close fails:

```powershell
.\scripts\stop_petdesk_runtime.ps1 -Force
```

Stop every `petdesk_runtime.exe` process, regardless of path:

```powershell
.\scripts\stop_petdesk_runtime.ps1 -All -Force
```

## Discovery

README should link the current petdesk operator path:

```text
docs/petdesk_operator_guide_m54.md
```

The goal is not to make petdesk the public default desktop launcher yet. The
goal is to make the validated release runtime path findable for development
and acceptance work.

## Boundary

In scope:

- safe petdesk runtime stop helper;
- operator guide update;
- README discoverability pointer;
- tests that lock the helper does not target backend/QQ processes.

Out of scope:

- changing release/dev startup behavior;
- adding a GUI settings panel;
- process supervision from the Python backend;
- installer packaging or auto-update.

## Validation Plan

```powershell
python -m unittest tests.test_petdesk_runtime_starter -v
python -m ruff check tests\test_petdesk_runtime_starter.py
python -m ruff format --check tests\test_petdesk_runtime_starter.py
python -m py_compile tests\test_petdesk_runtime_starter.py
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop_petdesk_runtime.ps1 -CheckOnly
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop_petdesk_runtime.ps1 -CheckOnly -All
git diff --check -- docs\petdesk_stop_and_discovery_m55.md docs\petdesk_operator_guide_m54.md scripts\stop_petdesk_runtime.ps1 README.md tests\test_petdesk_runtime_starter.py
```

## Validation Run

Passed in this slice:

```text
tests.test_petdesk_runtime_starter: 15 OK
ruff check tests/test_petdesk_runtime_starter.py: OK
ruff format --check tests/test_petdesk_runtime_starter.py: OK
py_compile tests/test_petdesk_runtime_starter.py: OK
stop_petdesk_runtime.ps1 -CheckOnly: OK
stop_petdesk_runtime.ps1 -CheckOnly -All: OK
git diff --check: OK
```

No petdesk runtime process was running after validation, and the stop helper did
not touch the Akane backend or QQ bot.
