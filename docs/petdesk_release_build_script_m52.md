# Petdesk Release Build Script M52

Status: implemented in this slice.
Date: 2026-07-08

## Goal

M51 proved that release-mode petdesk startup works, but the successful build
path required remembering a local recovery sequence:

```powershell
cd F:\Akane\petdesk-runtime
cargo build --manifest-path src-tauri\Cargo.toml --release -j 1
pnpm tauri:build
```

M52 turns that sequence into an Akane-owned helper script so future release
acceptance does not depend on memory or chat history.

## Sources Checked

- `docs/petdesk_release_runtime_acceptance_m51.md`
- `scripts/start_petdesk_runtime.ps1`
- `start_akane_petdesk.ps1`
- `F:\Akane\petdesk-runtime\.cargo\config.toml`
- `F:\Akane\petdesk-runtime\src-tauri\Cargo.toml`
- `F:\Akane\petdesk-runtime\package.json`

## Decision

Add:

```text
scripts/build_petdesk_runtime_release.ps1
```

The script:

- locates the Akane project root;
- resolves sibling `..\petdesk-runtime` by default;
- verifies `src-tauri\Cargo.toml` and `package.json`;
- verifies `cargo` and `pnpm` are available;
- derives the expected `petdesk_runtime.exe` path from
  `petdesk-runtime\.cargo\config.toml` `target-dir` when present;
- runs the serial warm-up build by default:

```powershell
cargo build --manifest-path src-tauri\Cargo.toml --release -j 1
```

- then runs:

```powershell
pnpm tauri:build
```

- confirms the final exe exists and reports its path, size, and timestamp.

## Usage

Normal release build:

```powershell
.\scripts\build_petdesk_runtime_release.ps1
```

Only verify paths/tools:

```powershell
.\scripts\build_petdesk_runtime_release.ps1 -CheckOnly
```

Print the exact steps without building:

```powershell
.\scripts\build_petdesk_runtime_release.ps1 -DryRun
```

Use a non-default runtime checkout:

```powershell
.\scripts\build_petdesk_runtime_release.ps1 -RuntimeDir ..\petdesk-runtime
```

Skip the serial warm-up only when intentionally testing whether the machine no
longer needs it:

```powershell
.\scripts\build_petdesk_runtime_release.ps1 -SkipWarmup
```

## Boundary

In scope:

- Akane-side helper for building the sibling `petdesk-runtime` release exe;
- no-window validation commands;
- test locks that the helper keeps the serial warm-up and final exe check.

Out of scope:

- changing `petdesk-runtime` source;
- changing the Akane backend, QQ bot, memory, model, prompt, or TTS behavior;
- launching the release window;
- making release mode the public default launcher;
- producing installers or auto-updaters.

## Validation Plan

```powershell
python -m unittest tests.test_petdesk_runtime_starter -v
python -m ruff check tests\test_petdesk_runtime_starter.py
python -m ruff format --check tests\test_petdesk_runtime_starter.py
python -m py_compile tests\test_petdesk_runtime_starter.py
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_petdesk_runtime_release.ps1 -CheckOnly
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_petdesk_runtime_release.ps1 -DryRun
git diff --check -- docs\petdesk_release_build_script_m52.md scripts\build_petdesk_runtime_release.ps1 tests\test_petdesk_runtime_starter.py
```

Full build validation:

```powershell
.\scripts\build_petdesk_runtime_release.ps1
```

Expected result:

```text
[OK] petdesk-runtime release build completed: ...
```

## Validation Run

Passed in this slice:

```text
tests.test_petdesk_runtime_starter: 10 OK
ruff check tests/test_petdesk_runtime_starter.py: OK
ruff format --check tests/test_petdesk_runtime_starter.py: OK
py_compile tests/test_petdesk_runtime_starter.py: OK
build_petdesk_runtime_release.ps1 -CheckOnly: OK
build_petdesk_runtime_release.ps1 -DryRun: OK
git diff --check: OK
```

Full build validation also passed:

```powershell
.\scripts\build_petdesk_runtime_release.ps1
```

Observed result:

```text
serial cargo release warm-up: Finished in 6m 49s
Tauri release build: Finished in 23.67s
Built application at: F:\Cache\cargo-target\petdesk-runtime\release\petdesk_runtime.exe
petdesk_runtime.exe: 11829760 bytes, 2026-07-08 21:52:00
```
