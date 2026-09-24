# Petdesk Akane MVP Closeout M41

Status: implemented and live smoke passed.
Date: 2026-07-07

## Goal

Turn the current Akane + `petdesk-runtime` path from a hand-debugged demo into
a repeatable MVP smoke path.

M40 proved the product-level chain manually:

```text
runtime input
  -> Akane POST /pet/turn
  -> display envelope
  -> audio resource_manifest
  -> /audio/petdesk/<token>
  -> petdesk-runtime audio queue
  -> audible TTS
```

M41 should make that same chain easy to check before opening or promoting the
runtime window.

## Sources Checked

- `docs/petdesk_akane_bridge_m32.md`
- `docs/petdesk_akane_starter_m36.md`
- `docs/petdesk_akane_manual_acceptance_m37.md`
- `docs/petdesk_akane_audio_bridge_m38.md`
- `docs/petdesk_akane_audio_acceptance_m39.md`
- `start_akane_petdesk.ps1`
- `scripts/start_petdesk_runtime.ps1`
- `companion_v01/routes/petdesk.py`
- `companion_v01/petdesk_bridge.py`
- `tests/test_petdesk_bridge.py`
- `tests/test_petdesk_runtime_starter.py`
- `petdesk-runtime/docs/akane_turn_input_m40.md`

## Decision

Add an Akane-owned MVP smoke tool:

```powershell
python scripts\tools\run_petdesk_mvp_smoke.py --base-url http://127.0.0.1:9999
```

The smoke checks:

- `GET /pet/health` returns ready bridge metadata;
- `POST /pet/turn` returns an SSE stream;
- stream contains at least `resource_manifest`, `display`, and `done`;
- final display has visible speech;
- when audio is required, the final manifest contains exactly the referenced
  audio handle and safe `/audio/petdesk/<token>` URL;
- `GET /audio/petdesk/<token>` returns non-empty `audio/*` bytes.

Wire the same smoke into the starter as an optional gate:

```powershell
.\start_akane_petdesk.ps1 -SmokeOnly
.\start_akane_petdesk.ps1 -RunSmokeBeforeLaunch
```

This keeps normal startup fast while making the MVP chain reproducible.

## Boundary

In scope:

- live HTTP smoke against an already running or starter-launched Akane backend;
- optional starter flags for smoke-only and smoke-before-launch;
- no-store requests and structured JSON summary;
- safe checks for audio handle and URL shape.

Out of scope:

- replacing `desktop_pet_next` as the public launcher;
- packaging `petdesk-runtime` release binaries;
- making audio timing or lip-sync assertions;
- changing the `/pet/*` runtime contract;
- changing model, memory, prompt, or TTS provider behavior.

## Usage

From Akane root, with backend already running:

```powershell
python scripts\tools\run_petdesk_mvp_smoke.py --base-url http://127.0.0.1:9999
```

Through the starter:

```powershell
.\start_akane_petdesk.ps1 -SkipBackend -BackendUrl http://127.0.0.1:9999 -SmokeOnly
.\start_akane_petdesk.ps1 -SkipBackend -BackendUrl http://127.0.0.1:9999 -RunSmokeBeforeLaunch
```

If TTS is intentionally unavailable, pass:

```powershell
python scripts\tools\run_petdesk_mvp_smoke.py --base-url http://127.0.0.1:9999 --no-require-audio
```

That mode verifies text/display and reports `audio_required: false`; it is not a
full MVP audio acceptance.

## Validation Plan

Focused automated checks:

```powershell
python -m unittest tests.test_petdesk_mvp_smoke -v
python -m unittest tests.test_petdesk_runtime_starter -v
python -m ruff check scripts\tools\run_petdesk_mvp_smoke.py tests\test_petdesk_mvp_smoke.py
python -m ruff format --check scripts\tools\run_petdesk_mvp_smoke.py tests\test_petdesk_mvp_smoke.py
python -m py_compile scripts\tools\run_petdesk_mvp_smoke.py tests\test_petdesk_mvp_smoke.py
git diff --check -- docs\petdesk_akane_mvp_closeout_m41.md scripts\tools\run_petdesk_mvp_smoke.py scripts\start_petdesk_runtime.ps1 start_akane_petdesk.ps1 tests\test_petdesk_mvp_smoke.py tests\test_petdesk_runtime_starter.py
```

Manual/live smoke:

```powershell
python scripts\tools\run_petdesk_mvp_smoke.py --base-url http://127.0.0.1:9999
```

Expected marker:

```text
AKANE_PETDESK_MVP_SMOKE_OK
```

## Implemented Files

- `scripts/tools/run_petdesk_mvp_smoke.py`
- `scripts/start_petdesk_runtime.ps1`
- `start_akane_petdesk.ps1`
- `tests/test_petdesk_mvp_smoke.py`
- `tests/test_petdesk_runtime_starter.py`

## Validation Run

Focused automated checks:

```powershell
python -m unittest tests.test_petdesk_mvp_smoke -v
python -m unittest tests.test_petdesk_runtime_starter -v
python -m ruff check scripts\tools\run_petdesk_mvp_smoke.py tests\test_petdesk_mvp_smoke.py tests\test_petdesk_runtime_starter.py
python -m ruff format --check scripts\tools\run_petdesk_mvp_smoke.py tests\test_petdesk_mvp_smoke.py tests\test_petdesk_runtime_starter.py
python -m py_compile scripts\tools\run_petdesk_mvp_smoke.py tests\test_petdesk_mvp_smoke.py tests\test_petdesk_runtime_starter.py
git diff --check -- docs\petdesk_akane_mvp_closeout_m41.md scripts\tools\run_petdesk_mvp_smoke.py scripts\start_petdesk_runtime.ps1 start_akane_petdesk.ps1 tests\test_petdesk_mvp_smoke.py tests\test_petdesk_runtime_starter.py
```

Results:

```text
tests.test_petdesk_mvp_smoke: 3 tests OK
tests.test_petdesk_runtime_starter: 4 tests OK
ruff check: OK
ruff format --check: OK
py_compile: OK
git diff --check: OK, with existing CRLF warnings on PowerShell files
```

Live starter smoke:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_akane_petdesk.ps1 -SkipBackend -BackendUrl http://127.0.0.1:9999 -SmokeOnly -HealthTimeoutSeconds 10
```

Result:

```text
AKANE_PETDESK_MVP_SMOKE_OK
event_order: resource_manifest -> display -> resource_manifest -> display -> done
audio_present: true
audio_content_type: audio/mpeg
audio_bytes: 23616
```
