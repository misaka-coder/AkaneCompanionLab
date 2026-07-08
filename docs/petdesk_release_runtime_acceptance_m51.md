# Petdesk Release Runtime Acceptance M51

Status: accepted after fresh release rebuild.
Date: 2026-07-08

## Goal

M49 added the `petdesk-runtime` native launch-env bridge, and M50 added an
Akane starter mode that can launch a built `petdesk_runtime.exe`.

M51 verifies the release-mode path end to end:

```text
Akane backend already running
-> start_akane_petdesk.ps1 -RuntimeMode Release -SkipBackend
-> startup smoke proves manifest/snapshot/image
-> built petdesk_runtime.exe opens
-> release runtime reads launch env
-> startup portrait is Akane rather than placeholder
```

## Sources Checked

- `docs/petdesk_release_runtime_starter_m50.md`
- `F:\Akane\petdesk-runtime\docs\runtime_launch_env_m49.md`
- `scripts/start_petdesk_runtime.ps1`
- `start_akane_petdesk.ps1`
- `F:\Akane\petdesk-runtime\.cargo\config.toml`
- `F:\Cache\cargo-target\petdesk-runtime\release\petdesk_runtime.exe`

## Important Freshness Check

The currently discovered release exe is:

```text
F:\Cache\cargo-target\petdesk-runtime\release\petdesk_runtime.exe
```

At the start of M51 it was last written on `2026-07-03 18:19`, which is older
than M49/M50. That old binary cannot prove the M49 `runtime_launch_env` bridge,
because the bridge is source code added after the binary was built.

Therefore M51 must either:

1. rebuild `petdesk-runtime` release successfully, then run the release starter;
2. or record release acceptance as blocked by the local release build toolchain.

Launching the stale release exe would be a false acceptance.

## Validation Plan

No-window checks:

```powershell
.\start_akane_petdesk.ps1 -RuntimeMode Release -CheckOnly
.\start_akane_petdesk.ps1 -RuntimeMode Release -SkipBackend -BackendUrl http://127.0.0.1:9999 -StartupSmokeOnly
```

Release rebuild attempt:

```powershell
cd F:\Akane\petdesk-runtime
pnpm tauri:build
```

If the first Tauri build hits the local Windows linker `os error 5`, the
working recovery path observed in M51 is:

```powershell
cd F:\Akane\petdesk-runtime
cargo build --manifest-path src-tauri\Cargo.toml --release -j 1
pnpm tauri:build
```

Manual window acceptance, only after a fresh release binary exists:

```powershell
cd F:\Akane\AkaneCompanionLab
.\start_akane_petdesk.ps1 -RuntimeMode Release -SkipBackend -BackendUrl http://127.0.0.1:9999
```

Expected result:

- startup smoke passes before opening the window;
- release `petdesk_runtime.exe` opens;
- startup portrait is Akane, not the placeholder;
- backend state reaches ready;
- user can trigger a turn and hear audio.

## Boundary

In scope:

- release-mode acceptance evidence;
- freshness check for the release binary;
- documenting whether the current machine can build a valid release runtime.

Out of scope:

- changing Akane backend, QQ bot, memory, model, prompt, or TTS behavior;
- changing `petdesk-runtime` source unless a release-only acceptance blocker is
  found;
- replacing the public desktop-pet launcher.

## Result

No-window release starter checks passed:

```text
start_akane_petdesk.ps1 -RuntimeMode Release -CheckOnly
-> OK
-> resolved F:\Cache\cargo-target\petdesk-runtime\release\petdesk_runtime.exe
-> no backend or runtime process launched
```

Startup-only bridge smoke passed against the already running Akane backend:

```text
start_akane_petdesk.ps1 -RuntimeMode Release -SkipBackend -BackendUrl http://127.0.0.1:9999 -StartupSmokeOnly
-> AKANE_PETDESK_STARTUP_SMOKE_OK
-> runtimeEnv keys:
   VITE_PETDESK_INTERACTION_PROFILE
   VITE_PETDESK_INTERACTION_PROFILE_JSON
   VITE_PETDESK_RESOURCE_MANIFEST_URL
-> startup asset handle: akane_sample/static/default/normal
-> startup image URL: /petdesk-character-packs/akane_sample/assets/characters/default/normal.png
-> startup image bytes: 1936054
-> startup static image count: 38
```

The first fresh release build was attempted:

```powershell
cd F:\Akane\petdesk-runtime
pnpm tauri:build
```

The frontend `pnpm build` completed, but the native release build failed at the
Windows linker execution step:

```text
error: could not exec the linker `link.exe`
note: 拒绝访问。 (os error 5)
link.exe: D:\Program Files\VC\Tools\MSVC\14.50.35717\bin\HostX64\x64\link.exe
crate at failure: serialize-to-javascript-impl
```

Then the release build was recovered with a serial Cargo build:

```powershell
cd F:\Akane\petdesk-runtime
cargo build --manifest-path src-tauri\Cargo.toml --release -j 1
```

Result:

```text
Finished `release` profile [optimized] target(s) in 1m 51s
```

After that, the full Tauri release build passed:

```powershell
cd F:\Akane\petdesk-runtime
pnpm tauri:build
```

Result:

```text
Finished `release` profile [optimized] target(s) in 28.83s
Built application at: F:\Cache\cargo-target\petdesk-runtime\release\petdesk_runtime.exe
```

Fresh release binary:

```text
F:\Cache\cargo-target\petdesk-runtime\release\petdesk_runtime.exe
last write time: 2026-07-08 21:11:39
size: 11829760 bytes
```

Manual release-mode starter acceptance was then run:

```powershell
.\start_akane_petdesk.ps1 -RuntimeMode Release -SkipBackend -BackendUrl http://127.0.0.1:9999
```

Starter output confirmed:

```text
AKANE_PETDESK_STARTUP_SMOKE_OK
Starting petdesk-runtime release window...
```

Runtime process:

```text
petdesk_runtime.exe
PID: 36052
exe: F:\Cache\cargo-target\petdesk-runtime\release\petdesk_runtime.exe
```

Manual visual/audio acceptance:

- user confirmed the release window was OK;
- startup was Akane rather than the placeholder;
- interaction/audio had no observed issue.

## Next Step

Keep the release starter opt-in for now. The next packaging slice can turn the
observed recovery path into a documented or scripted release build command so
future builds do not depend on remembering to run the serial Cargo warm-up.
