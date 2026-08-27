"""Canonical model-facing contract for Host-managed MCP connections."""

from __future__ import annotations

from capcore import CapabilityToolSpec


MCP_MANAGE_TOOL_SPEC = CapabilityToolSpec(
    capability_id="mcp_manage",
    display_name="Manage MCP connections",
    description=(
        "List, configure, discover, enable, disable, restart, or remove an MCP connection owned by "
        "this Akane Host. For a local stdio server, install its npm/Python/binary package separately "
        "with exec_run, then configure its command here. A successful configure starts and initializes "
        "the candidate before replacing the last-good registration. Remove stops the managed session "
        "and removes only Akane's connection config; it never uninstalls external packages or deletes "
        "external source. Only the trusted desktop or configured owner QQ account may use this tool."
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
            "prompt_exposed_tools": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 160},
                "maxItems": 128,
                "description": "Discovered MCP tool names to expose to the model; use ['*'] for all discovered tools.",
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
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="idempotent",
    max_result_bytes=32 * 1024,
)
