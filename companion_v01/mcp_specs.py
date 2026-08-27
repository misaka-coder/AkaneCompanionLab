"""Canonical model-facing contract for Host-managed MCP connections."""

from __future__ import annotations

from capcore import CapabilityToolSpec


LOAD_MCP_TOOL_SPEC = CapabilityToolSpec(
    capability_id="load_mcp",
    display_name="Load MCP tools",
    description=(
        "Load the native tools from one or more installed and enabled MCP servers for the current "
        "agent turn. Use the compact MCP directory in the prompt to choose server_ids. This does "
        "not install software, change permissions, or persist tool schemas into later turns."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "server_ids": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 80},
                "minItems": 1,
                "maxItems": 16,
            },
        },
        "required": ["server_ids"],
    },
    risk="low",
    confirm="never",
    effects=(),
    visible_in=("desktop", "qq"),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="idempotent",
    max_result_bytes=8 * 1024,
)


MCP_MANAGE_TOOL_SPEC = CapabilityToolSpec(
    capability_id="mcp_manage",
    display_name="Manage MCP connections",
    description=(
        "List, configure, discover, enable, disable, restart, or remove an MCP connection owned by "
        "this Akane Host. For a local stdio server, install its npm/Python/binary package separately "
        "with exec_run, then configure its command here. A successful configure starts and initializes "
        "the candidate before replacing the last-good registration. Remove stops the managed session "
        "and removes only Akane's connection config; it never uninstalls external packages or deletes "
        "external source. Before installing or configuring third-party MCP software, verify its current "
        "official repository or registry entry, exact package/binary/image, startup command, authentication "
        "flow, and maintenance status with live tools; model memory and search snippets are not sufficient. "
        "Only the trusted desktop or configured owner QQ account may use this tool."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "configure", "discover", "enable", "disable", "restart", "remove"],
            },
            "server_id": {"type": "string", "minLength": 1, "maxLength": 80},
            "display_name": {"type": "string", "maxLength": 120},
            "transport": {"type": "string", "enum": ["stdio", "streamable_http"]},
            "command": {"type": "string", "maxLength": 1024},
            "args": {"type": "array", "items": {"type": "string", "maxLength": 2048}, "maxItems": 64},
            "cwd": {"type": "string", "maxLength": 1024},
            "env": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "Host environment bindings. Use ${ENV_NAME} placeholders only; never pass credential literals.",
            },
            "url": {"type": "string", "maxLength": 2048},
            "headers": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "Remote headers with ${ENV_NAME} placeholders; never pass credential literals.",
            },
            "enabled": {"type": "boolean"},
            "catalog_description": {
                "type": "string",
                "maxLength": 240,
                "description": "Stable one-line description shown in the compact MCP directory.",
            },
            "activation_mode": {
                "type": "string",
                "enum": ["on_demand", "pinned"],
                "description": "on_demand keeps schemas hidden until load_mcp; pinned exposes only pinned_tools.",
            },
            "pinned_tools": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 160},
                "maxItems": 128,
                "description": "Exact discovered tool names kept resident only when activation_mode is pinned.",
            },
            "low_risk_allowlist": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 160},
                "maxItems": 128,
            },
        },
        "required": ["action"],
    },
    risk="medium",
    confirm="never",
    effects=("mcp_config_mutation",),
    visible_in=("desktop", "qq"),
    spec_version="1.0.1",
    schema_version=1,
    execution_class="sync",
    idempotency="idempotent",
    max_result_bytes=32 * 1024,
)
