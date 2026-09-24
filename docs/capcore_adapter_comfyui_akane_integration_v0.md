# capcore-adapter-comfyui Akane integration v0

Status: implemented
Date: 2026-07-01

## Purpose

Akane now uses `capcore-adapter-comfyui` for the reusable ComfyUI workflow
adapter layer. This keeps ComfyUI HTTP calls, workflow slot patching, output
image extraction, workflow execution data classes, and capcore descriptor/invoke
conversion outside the Akane host.

The runtime path is:

```text
Akane workflow route/profile config
  -> ComfyUiWorkflowRunner
  -> capcore-adapter-comfyui ComfyUiCapabilityAdapter
  -> ComfyUiClient public loopback routes
  -> WorkflowExecutionResult / WorkflowExecutionAsset
  -> Akane job status and image delivery
```

## Package Boundary

`capcore-adapter-comfyui` owns:

- loopback-only ComfyUI endpoint normalization;
- `POST /upload/image`, `POST /prompt`, `GET /history/{prompt_id}`,
  and `GET /view` client flow;
- safe opaque filename/subfolder/prompt/client id handling;
- workflow JSON size checks and relative manifest workflow path resolution;
- `node.inputs.field` slot patching on copied workflow JSON;
- safe output image ref extraction from ComfyUI history;
- `WorkflowExecutionRequest`, `WorkflowExecutionAsset`,
  `WorkflowExecutionResult`, and result normalization helpers;
- `ComfyUiCapabilityAdapter` and `build_comfyui_adapter_from_manifest()`.

Akane still owns:

- provider/workflow profile config storage;
- workflow file import and public route validation;
- background job queue and job status shape;
- browser/Desktop UI and image delivery endpoints;
- approval UX and user-facing local capability catalog;
- deciding whether a configured workflow is exposed to the model.

Do not move Akane route/config/job/UI behavior into the package.

## Changed Files

- `requirements.txt`
  - Loads the exact package release set from `requirements-packages.txt`.
- `scripts/bootstrap_akane_windows.ps1`
  - Verifies the installed package version and rejects source/editable installs.
- `companion_v01/capability_adapters/comfyui.py`
  - Compatibility re-export for the extracted adapter.
- `companion_v01/local_workflow_execution.py`
  - Compatibility re-export for extracted workflow execution data/helpers.
  - Keeps only the Akane runner protocol and `call_workflow_execution_runner()`
    shim because routes accept host-provided runner callables.
- `companion_v01/local_workflow_runners/comfyui.py`
  - Keeps `ComfyUiWorkflowRunner`, which loads Akane profile config and invokes
    the extracted adapter.
  - Re-exports ComfyUI client/workflow helper names used by older Akane tests
    and imports.
- `README.md` and `docs/core_dependency_strategy_v0.md`
  - Document the versioned artifact dependency.
- `tests/test_windows_bootstrap.py`
  - Covers the new bootstrap dependency checks.

## Validation

Focused validation used for this integration:

```bash
python -m unittest tests.test_capability_adapter_comfyui tests.test_local_workflow_runners tests.test_windows_bootstrap -v
python -m unittest tests.test_backend_route_modules -v
python -m py_compile companion_v01/capability_adapters/comfyui.py companion_v01/local_workflow_execution.py companion_v01/local_workflow_runners/comfyui.py companion_v01/routes/capabilities.py
ruff check companion_v01/capability_adapters/comfyui.py companion_v01/local_workflow_execution.py companion_v01/local_workflow_runners/comfyui.py tests/test_windows_bootstrap.py
ruff format --check companion_v01/capability_adapters/comfyui.py companion_v01/local_workflow_execution.py companion_v01/local_workflow_runners/comfyui.py tests/test_windows_bootstrap.py
git diff --check
```

When running outside the bootstrapped Akane venv, install the same versioned
release set from the wheelhouse:

```powershell
python -m pip install --no-index --find-links .\package_wheels -r requirements-packages.txt
```

## Continuation Notes

- Keep `ComfyUiWorkflowRunner` in Akane unless multiple hosts need the same
  profile-config loader shape.
- If a future host wants remote ComfyUI endpoints, add a separate host-owned
  policy layer first; do not silently loosen loopback-only defaults.
- If more workflow adapter types appear, consider a small host runner registry,
  but keep the adapter packages provider-specific and UI-free.
- If output delivery changes, update Akane routes only. The package should keep
  returning in-memory `WorkflowExecutionAsset` objects and safe output metadata.
