# Public Alpha Release Process

The private development repository must not be made public directly. Removed
audio and other local-only material may still be reachable from its Git
history.

## Supported Method

From the repository root on Windows:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\export_public_alpha.ps1 `
  -InitializeGit
```

The exporter:

- copies tracked and intentional untracked source files from the working tree
- does not copy `.git` history
- excludes `.env`, databases, logs, caches, local research, and build output
- excludes audio, Live2D runtime/sample files, and unverified media
- excludes local imported character packs such as `reimu`
- generates neutral placeholder PNG files required by current imports
- normalizes private absolute Markdown links in the exported copy
- runs the public-release audit
- optionally initializes an empty `main` Git repository without committing

It never deletes or rewrites the private development repository.

## Productization Gate

Passing the exporter and audit means the source snapshot is sanitized. It does
not mean every built feature is product-ready.

Before making a public announcement, review:

- `docs/productization_release_gate_v1.md`

Features such as GPT-SoVITS, MCP, music playback/system-media awareness, QQ, and
local workflows should be promoted only when their configuration, diagnostics,
tests, and troubleshooting paths satisfy the gate. Otherwise, describe them as
Alpha or experimental instead of complete product features.

## Verify the Export

In the exported directory:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\audit_public_release.ps1

powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\bootstrap_akane_windows.ps1 `
  -CheckOnly -Mode Web

python -m unittest discover tests
npm --prefix desktop_pet_next ci
npm --prefix desktop_pet_next run verify:control-center
cargo check --manifest-path desktop_pet_next/src-tauri/Cargo.toml
git diff --check
```

## Before Publishing

- Review `git status --short`.
- Confirm the repository has no commits inherited from the private project.
- Review every file staged for the initial public commit.
- Configure a new remote only for the exported directory.
- Do not copy files back from local asset or user-data directories.
- Mark the release as `v0.1.0-alpha.1`, experimental, and Windows-first.
- Confirm `启动_Akane.bat` reaches first-time configuration or launches the
  Web/desktop client on a clean Windows test account.
- Do not describe the source archive as a desktop installer. A source checkout
  can fall back to Web when Node.js/Rust are unavailable.
- Do not publish the current Tauri exe as a portable binary. Tauri and Python
  now share one per-user data root, but the desktop release still needs a
  managed backend runtime, a redistributable starter-character flow, and a
  clean-account installer smoke test.

The exporter reduces known risk but does not replace human review or legal
advice.
