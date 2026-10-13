# Public Alpha Release Process

The private development repository must not be made public directly. Removed
audio and other local-only material may still be reachable from its Git
history.

## Supported Method

From the repository root on Windows:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\export_public_alpha.ps1 `
  -SourceRef HEAD `
  -InitializeGit
```

The exporter:

- copies the selected commit when `-SourceRef` is supplied; uncommitted changes
  and untracked files stay local
- without `-SourceRef`, copies tracked files from the working tree only
- does not copy `.git` history
- excludes `.env`, databases, logs, caches, local research, and build output
- excludes audio, Live2D runtime/sample files, and unverified media
- excludes all character packs except the documented `akane_v1` starter pack,
  and excludes deployment scripts/records specific to the author's accounts
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
  -CheckOnly -Mode Backend

python -m unittest discover tests
npm --prefix desktop_pet_next ci
npm --prefix desktop_pet_next run verify:control-center
cargo check --manifest-path desktop_pet_next/src-tauri/Cargo.toml
git diff --check
```

## Before Publishing

### Updating the existing public repository

The development repository and the public repository have separate Git
histories. Export a committed source version, audit that output, then apply it
to a checkout of the public repository as one new commit. Preserve the public
repository's existing history; do not push the development branch or force-push.

Release dependencies must match `requirements-packages.txt`. The public source
does not bundle external voice models, audio, user settings, or memories. Attach
a validated wheelhouse to the GitHub release when pinned internal packages are
not available on the public package index. State its Python/platform coverage;
do not describe a Windows/Python-specific wheelhouse as a universal package.

MemCore releases use their own version, tag, wheel and source distribution.
Akane's `akane-plugin` package version is independent of the desktop Alpha
version.

- Review `git status --short`.
- Confirm the repository has no commits inherited from the private project.
- Review every file staged for the initial public commit.
- Configure a new remote only for the exported directory.
- Do not copy files back from local asset or user-data directories.
- Mark the release as an experimental, Windows-first Alpha with a new version
  matching the desktop package and Tauri manifests.
- Confirm `启动_Akane.bat` reaches first-time configuration or launches the
  desktop client on a clean Windows test account.
- Do not describe the source archive as a desktop installer. A source checkout
  requires desktop build tools or a prepared executable; backend-only mode has no UI.
- Do not publish the current Tauri exe as a portable binary. Tauri and Python
  now share one per-user data root, but the desktop release still needs a
  managed backend runtime, a redistributable starter-character flow, and a
  clean-account installer smoke test.

The exporter reduces known risk but does not replace human review or legal
advice.
