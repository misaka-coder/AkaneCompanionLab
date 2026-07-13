# Petdesk Release Pipeline M60

Status: implemented in this slice.
Date: 2026-07-09

## Goal

M53-M59 made the petdesk release path buildable, startable, stoppable,
diagnosable, acceptable, exportable, and auditable. M60 adds one release
pipeline command that orchestrates those existing scripts instead of asking an
operator to remember the sequence.

M60 adds:

```text
scripts/release_petdesk_bundle.ps1
```

This is still not an installer. It is a release candidate pipeline for the
petdesk runtime bundle.

## Sources Checked

- `scripts/build_petdesk_runtime_release.ps1`
- `scripts/accept_petdesk_release.ps1`
- `scripts/export_petdesk_release_bundle.ps1`
- `scripts/audit_petdesk_release_bundle.ps1`
- `docs/petdesk_release_bundle_m58.md`
- `docs/petdesk_release_bundle_audit_m59.md`
- `docs/petdesk_operator_guide_m54.md`

## Flow

Default flow:

```text
acceptance runner
-> bundle exporter
-> release_summary.json
-> manifest update
-> bundle audit
-> final release_summary.json
-> final manifest update
-> final bundle audit
```

With `-BuildFirst`:

```text
build helper
-> acceptance runner
-> bundle exporter
-> summary + audit
```

With `-SkipAcceptance`:

```text
optional build
-> bundle exporter
-> summary + audit
```

This mode is useful when creating an artifact on a machine that does not have a
live backend ready. It is not a full release acceptance.

## Commands

Check the pipeline plan without running child scripts:

```powershell
.\scripts\release_petdesk_bundle.ps1 -CheckOnly
```

Dry run child scripts without writing a bundle:

```powershell
.\scripts\release_petdesk_bundle.ps1 -DryRun
```

Create a release candidate bundle, assuming the backend is already running:

```powershell
.\scripts\release_petdesk_bundle.ps1
```

Build first:

```powershell
.\scripts\release_petdesk_bundle.ps1 -BuildFirst
```

Run full MVP acceptance:

```powershell
.\scripts\release_petdesk_bundle.ps1 -FullAcceptance
```

Export and audit without acceptance:

```powershell
.\scripts\release_petdesk_bundle.ps1 -SkipAcceptance
```

Explicit output:

```powershell
.\scripts\release_petdesk_bundle.ps1 -OutputPath <workspace>\petdesk-release-candidate
```

## Output

Success marker:

```text
AKANE_PETDESK_RELEASE_PIPELINE_OK
```

Other markers:

```text
AKANE_PETDESK_RELEASE_PIPELINE_CHECK_OK
AKANE_PETDESK_RELEASE_PIPELINE_DRY_RUN_OK
AKANE_PETDESK_RELEASE_PIPELINE_FAILED
```

The bundle contains the normal M58/M59 files plus:

```text
release_summary.json
```

The summary is written without local absolute paths. It records:

- source commit;
- whether build and acceptance were requested;
- acceptance mode;
- backend URL;
- bundle relative artifacts;
- pipeline status.

After the summary is written, the pipeline updates `manifest.json` with
`release_summary.json` and runs the M59 bundle audit again.

## Boundary

In scope:

- orchestrating existing build/accept/export/audit scripts;
- writing `release_summary.json`;
- updating bundle manifest for the summary file;
- final bundle audit.

Out of scope:

- opening the runtime window;
- stopping processes;
- replacing lower-level scripts;
- installer packaging;
- code signing;
- auto-update;
- bundling Akane backend or model assets.

By default the pipeline does not start Akane backend. Pass `-StartBackend` only
when intentionally allowing the acceptance runner to start it.

## Validation Plan

```powershell
python -m unittest tests.test_petdesk_runtime_starter -v
python -m ruff check tests\test_petdesk_runtime_starter.py
python -m ruff format --check tests\test_petdesk_runtime_starter.py
python -m py_compile tests\test_petdesk_runtime_starter.py
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\release_petdesk_bundle.ps1 -CheckOnly
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\release_petdesk_bundle.ps1 -DryRun
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\release_petdesk_bundle.ps1 -SkipAcceptance -OutputPath .\reports\petdesk-release-bundles\m60-validation
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\audit_petdesk_release_bundle.ps1 -BundleRoot .\reports\petdesk-release-bundles\m60-validation
git diff --check -- docs\petdesk_release_pipeline_m60.md docs\petdesk_operator_guide_m54.md scripts\release_petdesk_bundle.ps1 README.md tests\test_petdesk_runtime_starter.py
```

## Validation Run

Passed in this slice:

```text
tests.test_petdesk_runtime_starter: 26 OK
ruff check tests/test_petdesk_runtime_starter.py: OK
ruff format --check tests/test_petdesk_runtime_starter.py: OK
py_compile tests/test_petdesk_runtime_starter.py: OK
release_petdesk_bundle.ps1 -CheckOnly: OK
release_petdesk_bundle.ps1 -DryRun: OK
release_petdesk_bundle.ps1 -SkipAcceptance -OutputPath reports/petdesk-release-bundles/m60-validation: OK
audit_petdesk_release_bundle.ps1 -BundleRoot reports/petdesk-release-bundles/m60-validation: OK
git diff --check: OK
```

The validation bundle contained:

```text
README.md
manifest.json
release_summary.json
runtime/petdesk_runtime.exe
scripts/start_petdesk_runtime_bundle.ps1
scripts/audit_petdesk_release_bundle.ps1
docs/petdesk_operator_guide_m54.md
docs/petdesk_release_doctor_m56.md
docs/petdesk_release_acceptance_m57.md
docs/petdesk_release_bundle_m58.md
docs/petdesk_release_bundle_audit_m59.md
```

`release_summary.json` avoided local absolute paths. The ignored validation
bundle was removed after inspection. No runtime window, backend, QQ bot, or
process stop action was triggered during M60 validation.
