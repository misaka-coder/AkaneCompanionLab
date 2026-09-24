# Akane Lean-Down LD-007 Python Helper Tools

Status: implemented
Date: 2026-07-09

## Decision

The local Python adapter remains available as an internal adapter path, but the
current `python.akane.*` text helpers are no longer exposed to the chat model as
prompt-callable tools.

Affected helpers:

- `python.akane.normalize_text`
- `python.akane.extract_semantic_tags`
- `python.akane.detect_time_of_day`

## Why

These helpers are deterministic host utilities, not user-facing product
capabilities. Exposing them to the model adds tool-choice noise without making
Akane feel more capable to the user.

The lean-down rule for this slice is:

```text
Keep deterministic host utilities in host code; expose only product-meaningful
actions to the model.
```

## Runtime Shape

- The helper descriptors still appear in the local capability catalog as
  internal Python adapter capabilities.
- `exposedToPrompt` is now `false`.
- Normal profile-scoped tool selection no longer builds dynamic handlers for
  these helpers.
- A host can still explicitly wire and invoke the adapter path in tests or a
  future deliberate feature.

## Validation

Targeted validation:

```powershell
python -m unittest tests.test_capability_adapter_python_orchestration tests.test_local_capability_catalog -v
python -m py_compile companion_v01/capability_adapters/python_local.py tests/test_capability_adapter_python_orchestration.py tests/test_local_capability_catalog.py
ruff check companion_v01/capability_adapters/python_local.py tests/test_capability_adapter_python_orchestration.py tests/test_local_capability_catalog.py
ruff format --check companion_v01/capability_adapters/python_local.py tests/test_capability_adapter_python_orchestration.py tests/test_local_capability_catalog.py
git diff --check -- companion_v01/capability_adapters/python_local.py tests/test_capability_adapter_python_orchestration.py tests/test_local_capability_catalog.py docs/akane_lean_down_ld007_python_helpers.md
```
