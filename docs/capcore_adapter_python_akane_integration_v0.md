# capcore-adapter-python Akane integration v0

Status: implemented
Date: 2026-07-01

## Purpose

Akane now uses `capcore-adapter-python` for a small set of local Python
callable capabilities. This is a real host integration slice, not a new
adapter feature pass.

The runtime path is:

```text
Akane local callable
  -> AkanePythonCapabilityAdapter
  -> capcore descriptor / schema / permission gate
  -> AdapterCapabilityToolHandler
  -> PythonCapabilityAdapter.invoke()
```

## Current Local Python Capabilities

The current registered capabilities are low-risk, read-only helpers from
`companion_v01.text_utils`:

- `python.akane.normalize_text`
- `python.akane.extract_semantic_tags`
- `python.akane.detect_time_of_day`

They are intentionally small. They prove the local Python adapter path without
touching shell execution, browser control, file writes, provider keys, or user
profile mutation.

As of the lean-down LD-007 slice, these helpers remain cataloged as internal
Python adapter capabilities but are no longer prompt-exposed by default. They
are deterministic host utilities, not product-meaningful actions for the model
to choose during chat.

## Host-Owned Pieces

Akane owns:

- which local callables are registered;
- prompt exposure policy for local Python capabilities;
- tool prompt wording through `AdapterCapabilityToolHandler`;
- profile/session scoping in `tool_rounds`;
- public catalog entries under `kind="python_tool"`;
- approval policy resolution and UI events.

`capcore-adapter-python` owns only explicit callable registration and invoke
wrapping. It does not scan modules, import strings from model text, or evaluate
Python code supplied by the model.

## Files

- `companion_v01/capability_adapters/python_local.py`
  - Akane wrapper around `PythonCapabilityAdapter`.
- `companion_v01/engine_services/tool_rounds.py`
  - Merges MCP adapter tools and Python adapter tools into dynamic handler
    selection.
- `companion_v01/tool_runtime.py`
  - Reuses `AdapterCapabilityToolHandler` with generic adapter wording instead
    of hard-coded MCP wording.
- `companion_v01/local_capability_catalog.py`
  - Adds public `python_tool` catalog rows.
- `requirements.txt`
  - Adds editable `../capcore-adapter-python`.
- `scripts/bootstrap_akane_windows.ps1`
  - Verifies the sibling package exists and imports.

## Validation

Targeted validation:

```bash
.venv\Scripts\python.exe -B -m unittest tests.test_capability_adapter_python_orchestration -v
.venv\Scripts\python.exe -B -m unittest tests.test_capability_adapter_mcp_orchestration tests.test_tool_runtime.AdapterCapabilityToolHandlerTests tests.test_windows_bootstrap.WindowsBootstrapContractTests -v
```

Broader follow-up validation should include:

```bash
.venv\Scripts\python.exe -B -m unittest tests.test_local_capability_catalog tests.test_backend_route_modules -v
ruff check companion_v01/capability_adapters/python_local.py companion_v01/engine_services/tool_rounds.py companion_v01/tool_runtime.py companion_v01/local_capability_catalog.py tests/test_capability_adapter_python_orchestration.py tests/test_local_capability_catalog.py
git diff --check
```

## Continuation Notes

Next likely friction to watch:

- prompt bloat if more Python callables are exposed by default; LD-007 sets the
  default expectation that deterministic helpers stay hidden from the model;
- whether local utility tools should stay always-on or become profile-configured;
- whether `AdapterCapabilityToolHandler` should eventually move from generic
  JSON followup formatting to source-specific result rendering;
- whether multiple hosts repeat the same local callable registry shape, which
  would justify a reusable registry helper in `capcore-adapter-python`.

Do not add eval/import-string/directory scanning to make registration feel more
automatic. Keep local Python capabilities host-owned and explicit.
