"""Canonical model-facing contract for installed extension management."""

from __future__ import annotations

from capcore import CapabilityToolSpec


MANAGE_EXTENSION_TOOL_SPEC = CapabilityToolSpec(
    capability_id="manage_extension",
    display_name="Manage installed extensions",
    description=(
        "Browse the configured plugin market with action=market, then stage_market using the selected plugin_id "
        "and exact sha256 as digest. Staging verifies downloaded bytes before probing; review requirements and permissions. "
        "Inspect and manage installed Akane plugins. For source development, run its unittest suite against the "
        "current release SDK, then stage the source project to build and probe an immutable candidate. "
        "Install that stage with the exact returned permission list; the host "
        "publishes and activates it as one operation. Existing plugins can be enabled, disabled, rolled back, "
        "or uninstalled. Only the trusted desktop or configured owner QQ account may use this tool."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "list",
                    "market",
                    "stage_market",
                    "test_source",
                    "stage_source",
                    "stage_wheel",
                    "install",
                    "discard_stage",
                    "enable",
                    "disable",
                    "rollback",
                    "uninstall",
                ],
            },
            "plugin_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 64,
                "description": "Plugin id for stage_market, enable, disable, rollback, or uninstall.",
            },
            "digest": {
                "type": "string", "pattern": "^[a-f0-9]{64}$",
                "description": "For stage_market, copy the exact sha256 of the reviewed market entry.",
            },
            "path": {
                "type": "string",
                "minLength": 1,
                "maxLength": 2048,
                "description": "Absolute project directory for test_source/stage_source, or absolute .whl path for stage_wheel.",
            },
            "stage_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 64,
                "description": "Stage id returned by stage_source/stage_wheel; required for install or discard_stage.",
            },
            "approved_permissions": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 64},
                "maxItems": 32,
                "description": "For install, copy the exact permissions array returned by staging.",
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
