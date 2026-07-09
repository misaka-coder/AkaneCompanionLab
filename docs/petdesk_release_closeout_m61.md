# Petdesk Release Closeout M61

Status: implemented in this slice.
Date: 2026-07-09

## Goal

M50-M60 turned the petdesk runtime release path from a development experiment
into a repeatable release-candidate lane:

```text
release runtime starter
-> release build helper
-> release start entry
-> operator guide
-> stop helper
-> release doctor
-> release acceptance runner
-> release bundle exporter
-> release bundle audit
-> release bundle pipeline
```

M61 closes this release-tooling line. The release scripts are now considered
closed/frozen except repair, safety hardening, and compatibility fixes. New
product work should move to the next stage instead of adding more release
wrappers.

The next stage is:

```text
Live2D Productization L1
```

## Command Map

Build the release runtime:

```powershell
.\scripts\build_petdesk_runtime_release.ps1
```

Start the release runtime through the Akane source tree:

```powershell
.\start_akane_petdesk_release.ps1
```

Stop only the petdesk runtime window:

```powershell
.\scripts\stop_petdesk_runtime.ps1
```

Diagnose a machine or backend state:

```powershell
.\scripts\check_petdesk_release.ps1
```

Run no-window release acceptance:

```powershell
.\scripts\accept_petdesk_release.ps1
```

Export a handoff bundle:

```powershell
.\scripts\export_petdesk_release_bundle.ps1
```

Audit a bundle:

```powershell
.\scripts\audit_petdesk_release_bundle.ps1
```

Run the release bundle pipeline:

```powershell
.\scripts\release_petdesk_bundle.ps1
```

Start an exported bundle:

```powershell
.\scripts\start_petdesk_runtime_bundle.ps1
```

## Guarantees

The current release lane guarantees:

- a release exe can be built through the helper;
- release startup can fail before opening a window when the startup portrait
  chain is broken;
- runtime launch receives only whitelisted `VITE_PETDESK_*` environment keys;
- operators have a read-only doctor;
- operators have no-window quick and full acceptance modes;
- operators have a safe stop helper that targets `petdesk_runtime.exe` and
  does not stop Akane backend or QQ bot;
- exported bundles contain the runtime exe, bundle starter, docs, manifest,
  hashes, and release summary;
- exported bundles can be audited from the source tree or from inside the
  bundle;
- check/dry-run modes exist for release commands that should be inspected
  without opening windows or mutating process state.

## Non-Goals

The release lane still does not provide:

- a full Akane installer;
- auto-update;
- code signing;
- public one-click replacement for `desktop_pet_next`;
- GUI settings/control center work;
- Akane backend, QQ bot, model, prompt, memory, TTS, or character-asset
  packaging;
- Live2D model behavior polish.

Those are productization tracks, not more release-wrapper work.

## Frozen Boundary

After M61, prefer this policy:

- fix release-tooling bugs when validation or real handoff exposes them;
- keep safety repairs in the existing scripts and tests;
- do not add another release entry unless it removes an existing one or solves
  a real operator failure;
- do not mix installer work, backend packaging, or Live2D behavior work into
  this release-tooling line.

## Next Direction

Live2D Productization L1 should focus on model-facing behavior rather than
release plumbing:

- model layout contract: scale, position, viewport, fit mode, and offsets;
- hit-region and click/drag policy;
- expression map and motion map;
- idle, speaking, thinking, error, and fallback states;
- per-character defaults that can be hot-reloaded by the host;
- acceptance that checks what the user sees, hears, and can click.

This is the right next direction because the release path already answers:
"Can we build, start, diagnose, accept, export, audit, and hand off the
runtime?" The next risk is whether Live2D characters feel product-ready inside
that runtime.

## Validation

This closeout document is covered by `tests.test_petdesk_runtime_starter`.
