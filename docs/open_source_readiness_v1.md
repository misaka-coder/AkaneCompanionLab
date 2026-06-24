# Open-Source Readiness V1

Updated: 2026-06-15

This document separates what is ready for a source Alpha from what still blocks
a normal end-user desktop release.

## Release Position

AkaneCompanionLab can be published as a history-free, audited source Alpha.
It must not yet be described as a cross-platform desktop application or a
portable Windows installer.

The source Alpha export is a sanitization gate, not a product-completeness gate.
Before a public announcement, use `docs/productization_release_gate_v1.md` to
decide which already-built capabilities can be advertised as productized
features and which must remain Alpha or experimental.

## Current Support

| Surface | Status | Boundary |
| --- | --- | --- |
| Windows source bootstrap | Ready | Creates `.venv`, installs dependencies, then launches Desktop or Web and opens visible model settings when needed |
| Web client | Ready for Alpha | Runs from the FastAPI backend |
| Windows Tauri desktop | Source-build Alpha | Requires a build made inside the current checkout |
| QQ adapter | Optional Alpha | Requires an external NapCat/OneBot deployment |
| Linux/macOS backend | Manual/best effort | No first-class bootstrap or CI matrix yet |
| Linux/macOS desktop | Unsupported | No release promise |

## P0 Completed

- One public Windows entry: `启动_Akane.bat`.
- ASCII entry for terminals and automation: `start_akane.bat`.
- Repeatable bootstrap: `scripts/bootstrap_akane_windows.ps1`.
- First-run `.venv`, dependency, and advanced `.env` preparation.
- Visible model configuration in both the Tauri control center and Web settings.
- Provider presets, model discovery, connection test, redacted reads, and
  runtime reload without a second launch.
- Automatic Web fallback when the Tauri toolchain is unavailable.
- Public export and audit smoke test.
- README platform matrix and honest local-model boundary.

## P0 Blockers Before A Desktop Installer

### 1. Shared per-user character data root

The shared path contract is implemented. Python and Tauri now default to
`%LOCALAPPDATA%\Akane\` on Windows, honor `AKANE_DATA_ROOT`, and use the same
`characters`, `users_data`, `state`, and `logs` subdirectories. The Windows
launcher copies only missing legacy files and never deletes or overwrites user
data. Tauri also embeds the new-character JSON template instead of reading it
from the source checkout.

Remaining release work:

- package at least one redistributable starter character or first-run import
  flow with the installer;
- exercise migration and uninstall/data-preservation behavior on a clean
  Windows account;
- keep `_local` memory private and excluded from character-pack export.

### 2. Backend distribution boundary

The desktop app currently expects a separately running Python backend.

Choose and document one supported release shape:

- installer bundles a managed Python/backend runtime; or
- installer requires a separately installed backend and diagnoses it clearly.

Do not ship an exe that silently depends on a source checkout.

### 3. Dependency profiles

`requirements.txt` still combines core chat dependencies with Chroma, TTS,
document processing, media download, and lyrics support. Some modules such as
the vector store and Edge TTS are imported at startup.

Required result:

- make optional capabilities import-safe when their package is absent;
- define a small core requirements file;
- keep document/media/vector/local-ML extras explicit;
- test core-only startup in CI.

## P1 After Installer Blockers

- Add Linux backend CI and a shell bootstrap.
- Add a clean-machine Windows installer smoke test.
- Add signed checksums and release notes for generated artifacts.
- Add update/uninstall and data-preservation behavior.
- Measure cold start, dependency installation time, and first successful reply.

## Release Acceptance

A public source Alpha is acceptable when:

- `scripts/export_public_alpha.ps1` and the audit pass;
- the Windows bootstrap check passes in the exported snapshot;
- no private history, keys, databases, logs, research corpora, or unlicensed
  media are present;
- README calls the desktop path Windows-first and source-build;
- unsupported features return structured unavailable states.

A Windows installer is acceptable only after the shared data-root and backend
distribution blockers above are resolved.
