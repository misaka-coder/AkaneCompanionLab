# Petdesk Release Bundle M58

Status: implemented in this slice.
Date: 2026-07-08

## Goal

M53-M57 made the release runtime buildable, startable, stoppable,
diagnosable, and acceptable through no-window smoke scripts. The next
productization step is a small runtime bundle shape that can be handed to
another machine/operator without asking them to remember the source tree layout.

M58 adds:

```text
scripts/export_petdesk_release_bundle.ps1
scripts/start_petdesk_runtime_bundle.ps1
```

This is not a full Akane installer. It is a petdesk runtime artifact bundle for
acceptance work.

## Sources Checked

- `scripts/export_public_alpha.ps1`
- `scripts/audit_public_release.ps1`
- `docs/productization_release_gate_v1.md`
- `scripts/check_petdesk_release.ps1`
- `scripts/accept_petdesk_release.ps1`
- `start_akane_petdesk_release.ps1`
- `scripts/start_petdesk_runtime.ps1`
- `docs/petdesk_operator_guide_m54.md`

## Bundle Shape

Default export root:

```text
reports/petdesk-release-bundles/petdesk-release-<timestamp>/
```

`reports/` is already ignored by git, so generated bundles do not pollute the
tracked source tree.

Bundle contents:

```text
README.md
manifest.json
runtime/petdesk_runtime.exe
scripts/start_petdesk_runtime_bundle.ps1
docs/petdesk_operator_guide_m54.md
docs/petdesk_release_doctor_m56.md
docs/petdesk_release_acceptance_m57.md
docs/petdesk_release_bundle_m58.md
```

The bundle start script is intentionally source-independent:

- it locates `runtime/petdesk_runtime.exe` relative to the bundle root;
- it expects an already running Akane backend;
- it fetches `/pet/health`;
- it whitelists the same runtime env keys used by the source release starter;
- it starts the bundled runtime exe;
- it does not start the backend, stop processes, build Rust/Tauri, or touch QQ.

## Commands

Inspect what would be exported:

```powershell
.\scripts\export_petdesk_release_bundle.ps1 -CheckOnly
```

Dry run:

```powershell
.\scripts\export_petdesk_release_bundle.ps1 -DryRun
```

Export to the default ignored `reports/` location:

```powershell
.\scripts\export_petdesk_release_bundle.ps1
```

Export to an explicit directory:

```powershell
.\scripts\export_petdesk_release_bundle.ps1 -OutputPath F:\Akane\petdesk-release-test
```

Start the exported runtime from inside the bundle:

```powershell
.\scripts\start_petdesk_runtime_bundle.ps1 -BackendUrl http://127.0.0.1:9999
```

## Safety

The exporter:

- refuses to export into the Akane project root;
- refuses to overwrite an existing output path;
- requires a non-empty `petdesk_runtime.exe`;
- writes a manifest with relative bundle paths, file sizes, and SHA-256 hashes;
- keeps generated output under ignored `reports/` by default;
- does not include `.env`, databases, logs, user data, model caches, or source
  build directories;
- does not run acceptance smoke, start backend, open runtime, or stop processes.

## Boundary

In scope:

- runtime bundle export script;
- source-independent bundle start script;
- docs and README/operator-guide discovery;
- tests that lock the bundle shape.

Out of scope:

- full Akane backend packaging;
- installer creation;
- auto-update;
- code signing;
- bundling private character assets or model files.

## Validation Plan

```powershell
python -m unittest tests.test_petdesk_runtime_starter -v
python -m ruff check tests\test_petdesk_runtime_starter.py
python -m ruff format --check tests\test_petdesk_runtime_starter.py
python -m py_compile tests\test_petdesk_runtime_starter.py
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\export_petdesk_release_bundle.ps1 -CheckOnly
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_petdesk_runtime_bundle.ps1 -CheckOnly
git diff --check -- docs\petdesk_release_bundle_m58.md docs\petdesk_operator_guide_m54.md scripts\export_petdesk_release_bundle.ps1 scripts\start_petdesk_runtime_bundle.ps1 README.md tests\test_petdesk_runtime_starter.py
```

## Validation Run

Passed in this slice:

```text
tests.test_petdesk_runtime_starter: 22 OK
ruff check tests/test_petdesk_runtime_starter.py: OK
ruff format --check tests/test_petdesk_runtime_starter.py: OK
py_compile tests/test_petdesk_runtime_starter.py: OK
export_petdesk_release_bundle.ps1 -CheckOnly: OK
export_petdesk_release_bundle.ps1 -DryRun: OK
start_petdesk_runtime_bundle.ps1 -CheckOnly: OK
real export to reports/petdesk-release-bundles/m58-validation: OK
exported bundle start_petdesk_runtime_bundle.ps1 -CheckOnly: OK
git diff --check: OK
```

The real validation export produced:

```text
README.md
manifest.json
runtime/petdesk_runtime.exe
scripts/start_petdesk_runtime_bundle.ps1
docs/petdesk_operator_guide_m54.md
docs/petdesk_release_doctor_m56.md
docs/petdesk_release_acceptance_m57.md
docs/petdesk_release_bundle_m58.md
```

The ignored validation bundle was removed after inspection. No runtime window,
backend, QQ bot, or process stop action was triggered during validation.
