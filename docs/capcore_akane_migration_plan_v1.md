# capcore / Akane migration plan v1

Status: active plan
Date: 2026-06-30
Repos:

- `F:\Akane\capcore`
- `F:\Akane\AkaneCompanionLab`

## Purpose

This document is the short engineering plan for the next capcore / Akane cleanup pass.

The current direction is still:

```text
capcore = reusable capability kernel
Akane = host product, adapters, routes, UI, approval queue, concrete runtime
```

Akane has already validated several capcore pieces in real host code. The next step is to move the reusable parts that were discovered during integration back into capcore, then make Akane thinner.

## Current State

Akane already uses capcore in these places:

- `companion_v01/local_capability_config.py`
  - `project_capcore_catalog_fields()` wraps `capcore.descriptor_from_mapping()`.
  - `with_capability_approval_metadata()` and `apply_approval_policy_to_entry()` use `capcore.permission_request_from_mapping()` and `capcore.resolve_permission()`.
  - provider / workflow / voice profile / MCP server / MCP tool public catalog fields now pass through capcore projection.

- `companion_v01/local_capability_catalog.py`
  - backend tool entries project risk / confirm through capcore.
  - static provider entries, prompt modules, and local service probes now receive capcore-projected `confirm` and `requiresConfirmation`.

- `companion_v01/tool_runtime.py`
  - `AdapterCapabilityToolHandler.execute()` follows:

    ```text
    validate_invocation_args -> build_permission_request -> resolve_permission -> adapter.invoke
    ```

  - `BrowserPageToolHandler` uses a manual `PermissionRequest` for high-risk browser control actions.

- `companion_v01/capcore_runtime.py`
  - Akane host glue for profile approval policy.
  - Temporary `sanitize_permission_preview()` implementation for approval previews.
  - Temporary `approval_required_event()` event shaping for Akane stream events.

The latest completed Akane commits on this line are:

- `59ce1c7 Harden capcore execution approval previews`
- `c46e8ac Project Akane catalog entries through capcore`
- `ec49dcd Project MCP tool catalog fields through capcore`
- `31236f9 Extract Akane capcore runtime helpers`

## Boundary Decision

Move into capcore:

- permission preview sanitizing rules that are not Akane-specific;
- argument preview redaction for secret-like names and values;
- local absolute path redaction;
- URL query secret redaction, such as `?token=...`;
- mapping / descriptor / permission helpers that can be used by any host.

Keep in Akane:

- profile-scoped approval policy storage;
- approval request queue and grant lifecycle;
- routes under `companion_v01/routes/capabilities.py`;
- control center UI events and refresh contracts;
- concrete adapters such as MCP stdio, ComfyUI, GPT-SoVITS, ASR;
- browser runner implementation;
- product-specific catalog status such as `missing_config`, `configured`, `disabled`;
- frontend wording and user-facing approval UX.

Important: `companion_v01/capability_approval.py` is mostly host logic. It should not be moved wholesale into capcore. It can call capcore preview sanitizers later.

## Implementation Plan

### Phase 1: Move Preview Sanitizer Into capcore

Target repo: `F:\Akane\capcore`

Files to inspect first:

- `capcore/permission.py`
- `capcore/validation_policy.py`
- `capcore/mapping.py`
- `capcore/__init__.py`
- `tests/test_permission.py`
- `tests/test_mapping.py`

Current capcore behavior:

- `build_permission_request()` uses `summarize_args()`.
- `summarize_args()` redacts sensitive argument names and secret-like literal values.
- mapping values become compact summaries such as `{"type": "object", "keys": [...]}`.
- strings do not yet redact local absolute paths or URL query secrets.

Add a public API, likely in `capcore/permission.py`:

```python
def sanitize_permission_preview(value: Mapping[str, Any] | None) -> dict[str, Any]:
    ...
```

Expected behavior:

- preserve safe keys matching the same general safe-token shape used elsewhere;
- redact keys containing markers like `api_key`, `apikey`, `authorization`, `bearer`, `cookie`, `password`, `secret`, `token`;
- redact URL query secret values, for example `https://x.test/?token=abc` -> `https://x.test/?token=[redacted]`;
- redact bearer and `key=value` secret fragments;
- redact local absolute paths, for example `C:\Users\ExampleUser\a.txt` -> `[local_path]`;
- keep bounded primitive values;
- keep object / array previews compact enough for approval UI.

Implementation note:

- Prefer extending existing preview helpers instead of creating a parallel subsystem.
- Do not make `redirect_uri=http://test.com?code=123` count as a secret by punctuation alone.
- Do not break existing `summarize_args()` tests that expect nested mappings to be summarized rather than deeply exposed.

Suggested tests:

- `summarize_args()` redacts local paths in string values.
- `summarize_args()` redacts URL query `token/password/secret/api_key`.
- sensitive names still become `[redacted]`.
- safe URL query values like `code=123` are not redacted.
- `permission_request_from_mapping()` inherits the safer preview.
- public `sanitize_permission_preview()` is exported from `capcore.__init__`.

Validation:

```bash
uv run --extra dev python -m unittest discover -s tests -v
uv run --extra dev ruff check .
uv run --extra dev ruff format --check .
git diff --check
uv run --extra dev python -m build
```

Commit suggestion:

```text
Add permission preview sanitizer
```

### Phase 2: Switch Akane To capcore Preview API

Target repo: `F:\Akane\AkaneCompanionLab`

Files to inspect first:

- `companion_v01/capcore_runtime.py`
- `companion_v01/tool_runtime.py`
- `companion_v01/capability_approval.py`
- `tests/test_capcore_runtime.py`
- `tests/test_tool_runtime.py`
- `tests/test_backend_route_modules.py`

Expected Akane changes:

- import `sanitize_permission_preview` from capcore;
- remove local regex constants and helper functions from `companion_v01/capcore_runtime.py`;
- keep `approval_policy_for_profile()`, `resolve_permission_for_profile()`, `manual_permission_request()`, and `approval_required_event()` as Akane host glue;
- keep `approval_required_event()` responsible for Akane event field names, but delegate preview cleaning to capcore;
- optionally replace `capability_approval.py` preview sanitizer with capcore's sanitizer if it preserves the current route behavior.

Do not move or delete yet:

- `CapabilityApprovalStore`;
- approval request TTL / grant lifecycle;
- route payload normalization;
- UI event names.

Tests to run:

```bash
python -B -m py_compile companion_v01\capcore_runtime.py tests\test_capcore_runtime.py tests\test_tool_runtime.py
ruff check companion_v01\capcore_runtime.py tests\test_capcore_runtime.py tests\test_tool_runtime.py
ruff format --check companion_v01\capcore_runtime.py tests\test_capcore_runtime.py tests\test_tool_runtime.py
python -B -m unittest tests.test_capcore_runtime tests.test_tool_runtime tests.test_capability_adapter_mcp_orchestration tests.test_capability_adapter_mcp_stdio tests.test_backend_route_modules.BackendRouteModuleTests -v
git diff --check
```

Commit suggestion:

```text
Use capcore permission preview sanitizer
```

### Phase 3: Decide Whether To Move Catalog Projection Helper

Current Akane helper:

- `companion_v01/local_capability_config.py::project_capcore_catalog_fields()`

It currently:

- copies a host entry;
- builds a `CapabilityDescriptor` with `capcore.descriptor_from_mapping()`;
- writes back `risk`, `confirm`, `requiresConfirmation`, and canonical `effects`.

This is generic enough to consider moving into capcore, but it is lower priority than preview sanitizing.

Possible capcore API:

```python
def project_mapping_fields(
    data: Mapping[str, Any],
    *,
    default_risk: RiskLevel = "medium",
    default_confirm: ConfirmPolicy = "first_time",
    ...
) -> dict[str, Any]:
    ...
```

Decision rule:

- Move it if another host or another Akane module needs the same projection shape.
- Keep it in Akane if the only remaining behavior is Akane's public catalog field naming.

If moved:

- add tests in capcore `tests/test_mapping.py`;
- update Akane `project_capcore_catalog_fields()` to become a tiny compatibility wrapper or delete it after call sites switch.

### Phase 4: Remove Akane Duplicates Carefully

Only start this after Phase 1 and Phase 2 are committed.

Candidates:

- local preview sanitizer functions in `capcore_runtime.py`;
- duplicate preview sanitizing in `capability_approval.py`, if capcore API can match route behavior;
- small repeated catalog projection wrappers in `local_capability_catalog.py`, if capcore gets `project_mapping_fields()`;
- any remaining hand-written risk/confirm normalization that duplicates capcore mapping.

Do not remove:

- product status mapping such as `APPROVAL_DISABLED_STATUSES`;
- route-level structured failures;
- approval store;
- concrete adapter classes;
- browser control action-specific preview generation, because it intentionally exposes `selector/ref/candidateIndex/key/textLength` rather than raw input text.

### Phase 5: Documentation Cleanup

Update or cross-link:

- `docs/capcore_akane_integration_v0.md`
- `docs/capcore_design_v0.md`
- capcore `README.md`

The docs should make this clear:

```text
capcore provides reusable gates and projection helpers.
Akane owns persisted policy, approval queue, concrete adapters, UI, and product statuses.
```

## Handoff Notes For Context Compaction

If context gets compressed, resume like this:

1. Check both worktrees:

   ```bash
   cd F:\Akane\AkaneCompanionLab
   git status --short
   cd F:\Akane\capcore
   git status --short
   ```

2. Remember the persistent unrelated Akane dirty file:

   ```text
   docs/capability_adapter_v1_m1_handoff_prompt.md
   ```

   Do not stage it unless the user explicitly asks.

3. Start in `F:\Akane\capcore` with Phase 1.

4. After capcore commit, return to Akane Phase 2.

5. Keep commits small:

   - one capcore commit for public sanitizer API;
   - one Akane commit for switching to that API;
   - later commits for duplicate deletion.

## Acceptance Checklist

capcore:

- permission preview redacts secret fields;
- permission preview redacts bearer / secret-like fragments;
- permission preview redacts local absolute paths;
- URL query `token/password/secret/api_key` values are redacted;
- ordinary URL punctuation and non-secret query fields remain usable;
- `permission_request_from_mapping()` and `build_permission_request()` use the same preview policy;
- full capcore validation passes.

Akane:

- MCP high-risk tool still requires approval and does not execute before approval;
- trusted auto allow still executes high-risk tools after validation;
- browser click/fill/press still require approval by default;
- browser trusted auto allow still works;
- approval stream events do not leak API keys, bearer tokens, or local absolute paths;
- approval request route still redacts payload preview;
- unrelated document changes are not included in migration commits.
