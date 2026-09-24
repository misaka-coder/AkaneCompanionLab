# Petdesk Release Doctor M56

Status: implemented in this slice.
Date: 2026-07-08

## Goal

M53-M55 gave the petdesk release path a build helper, start entry, operator
guide, and safe stop helper. The next productization gap is diagnosis: when
release startup does not behave as expected, the operator should be able to run
one read-only command and see which layer is missing.

M56 adds:

```text
scripts/check_petdesk_release.ps1
```

This is a doctor/status helper. It does not launch the backend, open the
runtime, stop processes, post a turn, or touch the QQ bot.

## Sources Checked

- `start_akane_petdesk_release.ps1`
- `scripts/start_petdesk_runtime.ps1`
- `scripts/build_petdesk_runtime_release.ps1`
- `scripts/stop_petdesk_runtime.ps1`
- `scripts/tools/run_petdesk_mvp_smoke.py`
- `docs/petdesk_operator_guide_m54.md`
- `docs/petdesk_stop_and_discovery_m55.md`
- `README.md`

## Checks

Default checks:

- Akane project root can be found.
- Sibling `petdesk-runtime` directory looks valid.
- Release exe path resolves through `.cargo/config.toml` target-dir when
  available.
- Release exe exists and is non-empty.
- Release starter, build helper, stop helper, and MVP smoke script exist.
- `petdesk_runtime.exe` process state is visible without stopping anything.
- Backend URL is normalized from `-BackendUrl` or `-BackendPort`.
- `/pet/health` is reachable and reports `ok=true`, `status=ready`, and the
  expected bridge endpoints.
- `/pet/health.runtimeEnv.VITE_PETDESK_RESOURCE_MANIFEST_URL` points to a
  fetchable manifest.
- Startup manifest contains `staticImages`.
- `/pet/snapshot` is fetchable and contains a startup visual asset handle.

The script also reports `python`, `cargo`, and `pnpm` availability. Missing
`cargo` or `pnpm` is a warning because those tools are only needed when building
again, not when running an existing release exe.

## Options

```powershell
.\scripts\check_petdesk_release.ps1
.\scripts\check_petdesk_release.ps1 -SkipBackendHttp
.\scripts\check_petdesk_release.ps1 -BackendUrl http://127.0.0.1:9999
.\scripts\check_petdesk_release.ps1 -RuntimeExe <cache-root>\cargo-target\petdesk-runtime\release\petdesk_runtime.exe
.\scripts\check_petdesk_release.ps1 -All
```

Options:

- `-SkipBackendHttp`: skip `/pet/health`, manifest, and snapshot HTTP checks.
  Use this when checking filesystem/process state while the backend is
  intentionally stopped.
- `-BackendUrl`: inspect a specific backend.
- `-BackendPort`: derive `http://127.0.0.1:<port>` when `-BackendUrl` is not
  provided.
- `-RuntimeDir`: inspect a non-default `petdesk-runtime` checkout.
- `-RuntimeExe`: inspect a specific release exe.
- `-All`: report all `petdesk_runtime.exe` processes instead of only the
  process whose path matches the resolved release exe.
- `-TimeoutSeconds`: HTTP timeout for backend checks.

## Output Contract

The script prints structured line prefixes:

```text
[OK] ...
[WARN] ...
[FAIL] ...
[INFO] ...
```

It ends with one of:

```text
AKANE_PETDESK_RELEASE_DOCTOR_OK
AKANE_PETDESK_RELEASE_DOCTOR_FAILED
```

Exit code is `0` when there are no `[FAIL]` checks. Warnings do not fail the
doctor because some warnings, such as "runtime is not currently running", can
be normal before launch.

## Boundary

In scope:

- read-only release path diagnosis;
- operator guide and README discovery;
- tests that lock the doctor as non-mutating.

Out of scope:

- launching Akane backend;
- launching or stopping `petdesk_runtime.exe`;
- posting `/pet/turn`;
- replacing startup smoke;
- installer packaging or auto-update.

The doctor complements the existing smoke scripts:

- Doctor answers: "What is missing right now?"
- Startup smoke answers: "Can the first portrait chain actually fetch the
  runtime assets?"
- MVP smoke answers: "Can a full turn produce visible speech and audio?"

## Validation Plan

```powershell
python -m unittest tests.test_petdesk_runtime_starter -v
python -m ruff check tests\test_petdesk_runtime_starter.py
python -m ruff format --check tests\test_petdesk_runtime_starter.py
python -m py_compile tests\test_petdesk_runtime_starter.py
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\check_petdesk_release.ps1 -SkipBackendHttp
git diff --check -- docs\petdesk_release_doctor_m56.md docs\petdesk_operator_guide_m54.md scripts\check_petdesk_release.ps1 README.md tests\test_petdesk_runtime_starter.py
```

## Validation Run

Passed in this slice:

```text
tests.test_petdesk_runtime_starter: 17 OK
ruff check tests/test_petdesk_runtime_starter.py: OK
ruff format --check tests/test_petdesk_runtime_starter.py: OK
py_compile tests/test_petdesk_runtime_starter.py: OK
check_petdesk_release.ps1 -SkipBackendHttp: OK
git diff --check: OK
```

The doctor reported the release exe at:

```text
<cache-root>\cargo-target\petdesk-runtime\release\petdesk_runtime.exe
```

No `petdesk_runtime.exe` process was running during validation. The backend HTTP
portion was intentionally skipped for the stable script validation pass, and no
backend, QQ bot, or runtime process was started or stopped.
