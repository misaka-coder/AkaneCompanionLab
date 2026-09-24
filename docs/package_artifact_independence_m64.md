# Package Artifact Independence M64

Status: enforced

Date: 2026-07-13

## Decision

Akane and the extracted packages depend on versioned distribution artifacts,
not on the location of another source checkout.

The release contract forbids:

- editable or relative-path Python dependencies;
- `[tool.uv.sources]` source overrides in release manifests;
- npm `file:../` or `link:../` dependencies;
- host scripts that inject sibling directories into `sys.path`;
- petdesk launch/build scripts that guess `..\petdesk-runtime`;
- hard-coded machine paths in wheel metadata.

An explicit source root is allowed only as a maintainer input while building an
artifact. Consumers never need that source root.

## Python Distribution Flow

`requirements-packages.txt` is the authoritative, exact internal runtime
release set. `requirements-runtime.txt` owns third-party dependencies, and
`requirements.txt` includes both.

Build a complete local wheelhouse:

```powershell
.\.venv\Scripts\python.exe .\scripts\build_extracted_package_wheelhouse.py
```

The command builds all internal wheels, rejects source-path metadata, downloads
the complete runtime dependency closure, hashes every internal artifact, and writes
`package_wheels/akane-package-wheelhouse.json`. The output directory is ignored
by Git and belongs in a release bundle or artifact store, not source control.

Prove consumer independence:

```powershell
.\.venv\Scripts\python.exe .\scripts\verify_extracted_package_independence.py
```

The verifier creates a temporary virtual environment, removes `PYTHONPATH`,
disables the user site, installs only from the wheelhouse with `--no-index`,
runs `pip check`, then executes both cross-package smokes. A pass means the
source repositories were not available to import resolution.

Windows bootstrap accepts either:

- `-PackageWheelhouse` / `AKANE_PACKAGE_WHEELHOUSE`; or
- `-PackageIndexUrl` / `AKANE_PACKAGE_INDEX_URL`.

It rejects missing artifacts and editable/source-directory installations with
structured failure reasons.

## Petdesk Distribution Flow

`petcore-protocol`, `petdesk-character-host`, `petdesk-live2d-pixi-driver`, and
`petdesk-runtime` publish version `0.1.0` package artifacts. Dependants declare
`"petcore-protocol": "0.1.0"`; they do not link its repository.

Build and verify tarballs:

```powershell
.\.venv\Scripts\python.exe .\scripts\build_petdesk_package_artifacts.py
```

The command runs `pnpm pack`, audits the packed manifests, installs all four
tarballs into a temporary project, overrides the shared protocol to the packed
protocol artifact, and imports the reusable libraries. Petdesk runtime helpers
also require `-RuntimeDir` or `PETDESK_RUNTIME_ROOT`; their source location is
never inferred.

## Release Gates

Run these before publishing or bundling:

```powershell
.\.venv\Scripts\python.exe .\scripts\audit_extracted_packages_release.py
.\.venv\Scripts\python.exe -m unittest tests.test_package_independence tests.test_windows_bootstrap -v
.\.venv\Scripts\python.exe .\scripts\verify_extracted_package_independence.py
.\.venv\Scripts\python.exe .\scripts\build_petdesk_package_artifacts.py
```

The normal audit expects clean package repositories. Use `--allow-dirty` only
during an implementation pass; it is not a release acceptance result.
