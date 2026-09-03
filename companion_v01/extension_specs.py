"""Canonical model-facing contract for installed extension management."""

from __future__ import annotations

from capcore import CapabilityToolSpec


MANAGE_EXTENSION_TOOL_SPEC = CapabilityToolSpec(
    capability_id="manage_extension",
    display_name="Manage installed extensions",
    description=(
        "Inspect and manage local Akane plugins. It can build and probe a plugin project, probe a wheel, "
        "publish an approved stage, enable or disable a plugin, reload the isolated plugin generation, "
        "roll back, or uninstall. Staging never activates code; publish requires the exact permission list "
        "returned by staging. Only the trusted desktop or configured owner QQ account may use this tool."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "list",
                    "stage_source",
                    "stage_wheel",
                    "publish",
                    "discard_stage",
                    "enable",
                    "disable",
                    "restart",
                    "rollback",
                    "uninstall",
                ],
            },
            "plugin_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 64,
                "description": "Plugin id for enable, disable, rollback, or uninstall; optional for restart.",
            },
            "path": {
                "type": "string",
                "minLength": 1,
                "maxLength": 2048,
                "description": "Absolute project directory for stage_source, or absolute .whl path for stage_wheel.",
            },
            "stage_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 64,
                "description": "Stage id returned by stage_source/stage_wheel; required for publish or discard_stage.",
            },
            "approved_permissions": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 64},
                "maxItems": 32,
                "description": "For publish, copy the exact permissions array returned by staging.",
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
