# Petdesk Operator Guide M54

Status: implemented in this slice.
Date: 2026-07-08

## Purpose

This guide is the operator-facing map for the current Akane + petdesk runtime
path. It collects the commands that are now known to work, so future runs do
not depend on remembering the M49-M53 milestone chain.

The current productized path is:

```text
Akane backend
-> /pet health/snapshot/turn/resource-manifest/audio bridge
-> petdesk-runtime release exe
-> startup smoke before opening the window
```

## Quick Start

Use the existing release exe:

```powershell
.\start_akane_petdesk_release.ps1
```

Build the release exe first, then start:

```powershell
.\start_akane_petdesk_release.ps1 -BuildFirst
```

Reuse an already running Akane backend:

```powershell
.\start_akane_petdesk_release.ps1 -SkipBackend -BackendUrl http://127.0.0.1:9999
```

The release wrapper keeps startup smoke enabled by default. If the window opens,
the startup portrait chain has already passed:

```text
/pet/health
-> runtimeEnv.VITE_PETDESK_RESOURCE_MANIFEST_URL
-> /pet/resource-manifest
-> /pet/snapshot.visual.assetHandle
-> staticImages[assetHandle]
-> fetchable image
```

## Build

Normal release build:

```powershell
.\scripts\build_petdesk_runtime_release.ps1
```

This runs the stable local sequence:

```powershell
cargo build --manifest-path src-tauri\Cargo.toml --release -j 1
pnpm tauri:build
```

Expected final exe:

```text
<cache-root>\cargo-target\petdesk-runtime\release\petdesk_runtime.exe
```

No-build checks:

```powershell
.\scripts\build_petdesk_runtime_release.ps1 -CheckOnly
.\scripts\build_petdesk_runtime_release.ps1 -DryRun
```

Skip the serial Cargo warm-up only when intentionally testing the local
toolchain:

```powershell
.\scripts\build_petdesk_runtime_release.ps1 -SkipWarmup
```

If `pnpm tauri:build` hits:

```text
could not exec the linker `link.exe`
拒绝访问。 (os error 5)
```

rerun the normal build helper. It includes the serial warm-up that recovered
the local release build in M51/M52.

## Start Modes

Release mode:

```powershell
.\start_akane_petdesk_release.ps1
```

Release mode, explicit backend:

```powershell
.\start_akane_petdesk_release.ps1 -SkipBackend -BackendUrl http://127.0.0.1:9999
```

Release mode, batch entry:

```bat
start_akane_petdesk_release.bat
```

Development mode remains:

```powershell
.\start_akane_petdesk.ps1
```

The release wrapper does not replace the development starter. Use dev mode when
editing `petdesk-runtime` frontend/Rust source and release mode when validating
the built desktop runtime.

## Smoke And Dry-Run

Startup-only smoke, no runtime window:

```powershell
.\start_akane_petdesk_release.ps1 -SkipBackend -BackendUrl http://127.0.0.1:9999 -StartupSmokeOnly
```

Full MVP smoke, no runtime window:

```powershell
.\start_akane_petdesk_release.ps1 -SkipBackend -BackendUrl http://127.0.0.1:9999 -SmokeOnly
```

Check only:

```powershell
.\start_akane_petdesk_release.ps1 -CheckOnly
```

Dry-run release launch:

```powershell
.\start_akane_petdesk_release.ps1 -DryRun -SkipBackend -BackendUrl http://127.0.0.1:1 -HealthTimeoutSeconds 0
```

Dry-run build and launch:

```powershell
.\start_akane_petdesk_release.ps1 -BuildFirst -DryRun -SkipBackend -BackendUrl http://127.0.0.1:1 -HealthTimeoutSeconds 0
```

## Doctor

Run the read-only release doctor when startup fails or when checking a machine
before acceptance:

```powershell
.\scripts\check_petdesk_release.ps1
```

Filesystem and process checks only, without backend HTTP requests:

```powershell
.\scripts\check_petdesk_release.ps1 -SkipBackendHttp
```

The doctor prints `[OK]`, `[WARN]`, and `[FAIL]` lines and ends with:

```text
AKANE_PETDESK_RELEASE_DOCTOR_OK
AKANE_PETDESK_RELEASE_DOCTOR_FAILED
```

It does not start the backend, open the runtime, stop processes, post
`/pet/turn`, or touch the QQ bot.

## Acceptance

Run quick no-window release acceptance:

```powershell
.\scripts\accept_petdesk_release.ps1
```

This runs the release doctor and startup-only smoke. By default it does not
start the backend; it expects an existing backend at `http://127.0.0.1:9999` or
the explicit `-BackendUrl`.

Run full no-window MVP acceptance, including a `/pet/turn` and TTS audio check:

```powershell
.\scripts\accept_petdesk_release.ps1 -Full
```

Check script wiring without doctor, smoke, backend, or runtime side effects:

```powershell
.\scripts\accept_petdesk_release.ps1 -CheckOnly
```

The acceptance runner ends with:

```text
AKANE_PETDESK_RELEASE_ACCEPTANCE_OK
AKANE_PETDESK_RELEASE_ACCEPTANCE_FAILED
AKANE_PETDESK_RELEASE_ACCEPTANCE_CHECK_OK
```

It does not open the runtime window or stop any process. Pass `-StartBackend`
only when intentionally allowing the release wrapper to start the Akane
backend.

## Runtime Bundle

Export a small petdesk runtime bundle for acceptance handoff:

```powershell
.\scripts\export_petdesk_release_bundle.ps1
```

Inspect the bundle shape without writing files:

```powershell
.\scripts\export_petdesk_release_bundle.ps1 -CheckOnly
.\scripts\export_petdesk_release_bundle.ps1 -DryRun
```

Default output goes under ignored `reports/petdesk-release-bundles/`. The
bundle includes:

```text
runtime/petdesk_runtime.exe
scripts/start_petdesk_runtime_bundle.ps1
README.md
manifest.json
docs/*.md
```

The bundle is not a full Akane installer. Start the Akane backend separately,
then run the bundled starter:

```powershell
.\scripts\start_petdesk_runtime_bundle.ps1 -BackendUrl http://127.0.0.1:9999
```

Audit an exported bundle:

```powershell
.\scripts\audit_petdesk_release_bundle.ps1 -BundleRoot .\reports\petdesk-release-bundles\petdesk-release-...
```

Or from inside the bundle:

```powershell
.\scripts\audit_petdesk_release_bundle.ps1
```

The bundle starter does not start the backend, stop processes, build
Rust/Tauri, or touch QQ.

## Release Pipeline

Run the full release bundle pipeline:

```powershell
.\scripts\release_petdesk_bundle.ps1
```

Build first:

```powershell
.\scripts\release_petdesk_bundle.ps1 -BuildFirst
```

Run full MVP acceptance before export:

```powershell
.\scripts\release_petdesk_bundle.ps1 -FullAcceptance
```

Export and audit without a live backend acceptance pass:

```powershell
.\scripts\release_petdesk_bundle.ps1 -SkipAcceptance
```

The pipeline writes `release_summary.json`, adds it to `manifest.json`, and
runs bundle audit after the summary is included. It ends with:

```text
AKANE_PETDESK_RELEASE_PIPELINE_OK
AKANE_PETDESK_RELEASE_PIPELINE_FAILED
AKANE_PETDESK_RELEASE_PIPELINE_CHECK_OK
AKANE_PETDESK_RELEASE_PIPELINE_DRY_RUN_OK
```

It is still not an installer and does not open the runtime window or stop any
process.

## Release Closeout

The release-tooling line is closed/frozen after M61 except repair, safety
hardening, and compatibility fixes. The closeout map is recorded in:

```text
docs/petdesk_release_closeout_m61.md
```

Next product work should move to `Live2D Productization L1` instead of adding
more release wrappers.

## Expected Output

Healthy release startup prints:

```text
[INFO] Runtime mode: Release
[INFO] petdesk-runtime exe: <cache-root>\cargo-target\petdesk-runtime\release\petdesk_runtime.exe
[INFO] Running petdesk startup smoke...
AKANE_PETDESK_STARTUP_SMOKE_OK
[INFO] Starting petdesk-runtime release window...
```

Healthy release window:

- shows Akane rather than the protocol placeholder;
- reaches the backend-ready state;
- can trigger a turn;
- can play TTS audio when the backend provides audio.

## Stop

Close the petdesk window normally when possible.

If a release runtime window must be stopped from PowerShell:

```powershell
.\scripts\stop_petdesk_runtime.ps1
```

To inspect matching processes without closing anything:

```powershell
.\scripts\stop_petdesk_runtime.ps1 -CheckOnly
```

Force-stop only if graceful close fails:

```powershell
.\scripts\stop_petdesk_runtime.ps1 -Force
```

Do not stop the Akane backend or QQ bot unless that is the task. The release
petdesk process is separate from those services.

## Troubleshooting

`petdesk_runtime_release_exe_not_found`

- Run `.\scripts\build_petdesk_runtime_release.ps1`.
- Or pass an explicit `-RuntimeExe`.

Startup smoke fails before window opens:

- Run `.\scripts\check_petdesk_release.ps1` first to separate missing files,
  backend health, manifest, snapshot, and process state.
- The runtime is protected from opening on a broken startup portrait chain.
- Check `/pet/health`, `/pet/resource-manifest`, and `/pet/snapshot`.
- Use `-SkipStartupSmoke` only when intentionally testing fallback behavior.

Window shows placeholder:

- Confirm the startup smoke was not skipped.
- Confirm the release exe is fresh enough to include M49 launch-env support.
- Rebuild with `.\scripts\build_petdesk_runtime_release.ps1`.

No audio:

- Run full smoke with `-SmokeOnly`.
- Check that `/audio/petdesk/<token>` returns non-empty `audio/*`.
- Audio is backend-provided; the release wrapper only transports runtime env
  and starts the desktop process.

`link.exe` access denied during release build:

- Use the M52 build helper rather than raw `pnpm tauri:build`.
- The helper runs the serial Cargo release warm-up before Tauri build.

## What This Guide Does Not Cover

- Installer packaging.
- Auto-update.
- Public `desktop_pet_next` replacement.
- A GUI settings/control panel.
- Akane backend, QQ bot, model, prompt, memory, or TTS configuration.

Those are later productization slices.

## Validation

Guide coverage is locked by `tests.test_petdesk_runtime_starter`.
