"""Canonical model-facing contract for installed extension management."""

from __future__ import annotations

from capcore import CapabilityToolSpec


MANAGE_EXTENSION_TOOL_SPEC = CapabilityToolSpec(
    capability_id="manage_extension",
    display_name="Manage installed extensions",
    description=(
        "List installed Akane plugins, persistently enable or disable one plugin, or restart the current "
        "plugin host. This manages only plugins already declared or installed on this Host; it does not "
        "search a marketplace, download code, or pretend to install missing artifacts. Enabling is committed "
        "only after the candidate plugin becomes active; a failed candidate is rolled back. Only the trusted "
        "desktop or configured owner QQ account may use this tool."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "enable", "disable", "restart"],
            },
            "plugin_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
                "description": "Required for enable/disable. Optional for restart; restart is host-wide in V1.",
            },
        },
        "required": ["action"],
    },
    risk="medium",
    confirm="never",
    effects=("plugin_state_mutation",),
    visible_in=("desktop", "qq"),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="idempotent",
    max_result_bytes=32 * 1024,
)


__all__ = ["MANAGE_EXTENSION_TOOL_SPEC"]
