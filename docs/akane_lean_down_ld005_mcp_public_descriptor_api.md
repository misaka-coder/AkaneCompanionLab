# Akane Lean-Down LD-005 MCP Public Descriptor API

Status: implemented
Date: 2026-07-09

## Decision

Akane no longer calls private descriptor or capability-id methods on
`capcore-adapter-mcp`.

`capcore-adapter-mcp` now exposes:

```python
McpStdioCapabilityAdapter.descriptor_for_tool(tool_record)
```

Akane uses that public API when building prompt-exposed dynamic MCP handlers
from trusted profile config.

## Why

MCP descriptor conversion and capability-id handling belong to
`capcore-adapter-mcp`. Akane should own only profile config, process policy,
approval-mode projection, and route/orchestration glue.

Calling package-private methods such as `_descriptor_for_tool` or
`_capability_id` made the wrapper look thin while still depending on package
internals.

## Runtime Shape

- `capcore-adapter-mcp` owns MCP tool-to-capcore descriptor projection.
- Akane converts its profile-config tool mapping into `McpToolRecord`.
- Akane adds Akane-specific raw approval metadata after descriptor projection.
- Dynamic prompt tool handlers call `adapter.descriptor_for_tool(tool)`.

## Validation

Targeted validation:

```powershell
# capcore-adapter-mcp
uv run --extra dev python -m unittest tests.test_adapter -v
uv run --extra dev ruff check .
uv run --extra dev ruff format --check .
git diff --check
uv run --extra dev python -m build

# Akane
python -m unittest tests.test_capability_adapter_mcp_orchestration tests.test_capability_adapter_mcp_stdio tests.test_package_reintegration_policy -v
python -m py_compile companion_v01/capability_adapters/mcp_stdio.py companion_v01/engine_services/tool_rounds.py tests/test_package_reintegration_policy.py
ruff check companion_v01/capability_adapters/mcp_stdio.py companion_v01/engine_services/tool_rounds.py tests/test_package_reintegration_policy.py
ruff format --check companion_v01/capability_adapters/mcp_stdio.py companion_v01/engine_services/tool_rounds.py tests/test_package_reintegration_policy.py
git diff --check -- companion_v01/capability_adapters/mcp_stdio.py companion_v01/engine_services/tool_rounds.py tests/test_package_reintegration_policy.py docs/package_reintegration_policy_m63.md docs/akane_lean_down_ld005_mcp_public_descriptor_api.md
```
