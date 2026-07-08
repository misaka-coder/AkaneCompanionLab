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
F:\Cache\cargo-target\petdesk-runtime\release\petdesk_runtime.exe
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

## Expected Output

Healthy release startup prints:

```text
[INFO] Runtime mode: Release
[INFO] petdesk-runtime exe: F:\Cache\cargo-target\petdesk-runtime\release\petdesk_runtime.exe
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
Get-Process -Name petdesk_runtime -ErrorAction SilentlyContinue | Stop-Process
```

Do not stop the Akane backend or QQ bot unless that is the task. The release
petdesk process is separate from those services.

## Troubleshooting

`petdesk_runtime_release_exe_not_found`

- Run `.\scripts\build_petdesk_runtime_release.ps1`.
- Or pass an explicit `-RuntimeExe`.

Startup smoke fails before window opens:

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
