"""Canonical model-facing contracts for progressive Akane skills."""

from __future__ import annotations

from capcore import CapabilityToolSpec


LOAD_SKILL_TOOL_SPEC = CapabilityToolSpec(
    capability_id="load_skill",
    display_name="Load a task skill",
    description=(
        "Load the full instructions for one skill listed in the current available-Skills catalog. "
        "Use it only when the task clearly matches the skill description; do not load every skill. "
        "Omit resource to read SKILL.md. When those instructions explicitly reference a text file, "
        "call this tool again with that relative resource path. Scripts are not new tools: execute "
        "them through exec_run using the returned execution_cwd and execution_path."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "name": {
                "type": "string",
                "minLength": 1,
                "maxLength": 64,
                "description": "Exact skill name from the available-Skills catalog.",
            },
            "resource": {
                "type": "string",
                "maxLength": 512,
                "description": (
                    "Optional path relative to the skill directory, such as references/format.md. "
                    "Omit it to load SKILL.md."
                ),
            },
        },
        "required": ["name"],
    },
    risk="low",
    confirm="never",
    effects=(),
    visible_in=("desktop", "qq", "web"),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="read_only",
    max_result_bytes=320 * 1024,
)


MANAGE_SKILL_TOOL_SPEC = CapabilityToolSpec(
    capability_id="manage_skill",
    display_name="Validate or publish an Akane skill",
    description=(
        "Validate or atomically publish a skill draft created in the trusted execution workspace. "
        "A draft is a folder containing UTF-8 SKILL.md with YAML frontmatter name and description; "
        "it may also contain references, scripts, and assets. Create or download the draft with "
        "exec_run under skill_drafts/<name>, validate it, then publish it. A successful publish is "
        "hot-reloaded for the next model request without restarting Akane. Publishing changes the "
        "persistent instruction catalog but grants no new permissions. Only the trusted desktop or "
        "the configured owner QQ account may use this tool."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {
                "type": "string",
                "enum": ["validate", "publish"],
                "description": "Validate without changing the catalog, or atomically publish the draft.",
            },
            "draft_path": {
                "type": "string",
                "minLength": 1,
                "maxLength": 512,
                "description": "Directory relative to the execution workspace, normally skill_drafts/<name>.",
            },
            "replace": {
                "type": "boolean",
                "description": "For publish only: replace an existing managed skill with the same name.",
            },
        },
        "required": ["action", "draft_path"],
    },
    risk="medium",
    confirm="never",
    effects=("skill_install",),
    visible_in=("desktop", "qq"),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="idempotent",
    max_result_bytes=8192,
)
