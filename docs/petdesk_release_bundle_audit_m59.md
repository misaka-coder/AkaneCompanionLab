# Petdesk Release Bundle Audit M59

Status: implemented in this slice.
Date: 2026-07-08

## Goal

M58 created a small petdesk runtime bundle, but the exported directory still
needed a dedicated verifier. M59 adds an audit script that can run from the
source tree or from inside an exported bundle.

M59 adds:

```text
scripts/audit_petdesk_release_bundle.ps1
```

The exporter now includes this audit script and this M59 document in future
bundles.

## Sources Checked

- `scripts/export_petdesk_release_bundle.ps1`
- `scripts/start_petdesk_runtime_bundle.ps1`
- `docs/petdesk_release_bundle_m58.md`
- `docs/petdesk_operator_guide_m54.md`
- `scripts/audit_public_release.ps1`
- `scripts/export_public_alpha.ps1`

## Checks

The bundle audit checks:

- bundle root exists;
- `manifest.json` exists and parses as JSON;
- manifest schema is `akane.petdesk.releaseBundle.v1`;
- required files are present;
- `runtime/petdesk_runtime.exe` exists and is non-empty;
- `scripts/start_petdesk_runtime_bundle.ps1` exists;
- `scripts/audit_petdesk_release_bundle.ps1` exists;
- no unexpected top-level source directories are present, such as
  `companion_v01`, `users_data`, `runtime_logs`, `node_modules`, `target`, or
  `.git`;
- no `.env`, database, log, archive, source map, PDB, or extra executable files
  are present;
- every file listed in manifest has matching byte size and SHA-256;
- every non-manifest file in the bundle is listed in manifest.

The audit is intentionally read-only. It does not start the runtime, start the
backend, stop processes, build source code, or call HTTP endpoints.

## Commands

Audit a bundle from the source tree:

```powershell
.\scripts\audit_petdesk_release_bundle.ps1 -BundleRoot .\reports\petdesk-release-bundles\petdesk-release-...
```

Audit from inside an exported bundle:

```powershell
.\scripts\audit_petdesk_release_bundle.ps1
```

Expected success marker:

```text
AKANE_PETDESK_RELEASE_BUNDLE_AUDIT_OK
```

Expected failure marker:

```text
AKANE_PETDESK_RELEASE_BUNDLE_AUDIT_FAILED
```

## Boundary

In scope:

- read-only bundle verification;
- manifest hash/size integrity checks;
- forbidden file and directory checks;
- exporter update so future bundles carry their own audit script.

Out of scope:

- code signing;
- installer creation;
- auto-update;
- backend packaging;
- smoke-testing a live backend.

## Validation Plan

```powershell
python -m unittest tests.test_petdesk_runtime_starter -v
python -m ruff check tests\test_petdesk_runtime_starter.py
python -m ruff format --check tests\test_petdesk_runtime_starter.py
python -m py_compile tests\test_petdesk_runtime_starter.py
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\export_petdesk_release_bundle.ps1 -CheckOnly
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\audit_petdesk_release_bundle.ps1 -BundleRoot .\reports\petdesk-release-bundles\m59-validation
git diff --check -- docs\petdesk_release_bundle_audit_m59.md docs\petdesk_operator_guide_m54.md docs\petdesk_release_bundle_m58.md scripts\audit_petdesk_release_bundle.ps1 scripts\export_petdesk_release_bundle.ps1 README.md tests\test_petdesk_runtime_starter.py
```

## Validation Run

Passed in this slice:

```text
tests.test_petdesk_runtime_starter: 24 OK
ruff check tests/test_petdesk_runtime_starter.py: OK
ruff format --check tests/test_petdesk_runtime_starter.py: OK
py_compile tests/test_petdesk_runtime_starter.py: OK
export_petdesk_release_bundle.ps1 -CheckOnly: OK
real export to reports/petdesk-release-bundles/m59-validation: OK
source-tree audit of m59-validation bundle: OK
in-bundle audit of m59-validation bundle: OK
git diff --check: OK
```

The validation bundle contained:

```text
README.md
manifest.json
runtime/petdesk_runtime.exe
scripts/start_petdesk_runtime_bundle.ps1
scripts/audit_petdesk_release_bundle.ps1
docs/petdesk_operator_guide_m54.md
docs/petdesk_release_doctor_m56.md
docs/petdesk_release_acceptance_m57.md
docs/petdesk_release_bundle_m58.md
docs/petdesk_release_bundle_audit_m59.md
```

The ignored validation bundle was removed after audit. No runtime window,
backend, QQ bot, HTTP request, or process stop action was triggered by the
audit validation.
