from __future__ import annotations

from capcore import CapabilityToolSpec

from .project_workspace import PROJECT_PATCH_MAX_CHARS, PROJECT_WRITE_MAX_CHARS


MANAGE_PROJECT_WORKSPACE_TOOL_SPEC = CapabilityToolSpec(
    capability_id="manage_project_workspace",
    display_name="Manage coding project workspace",
    description=(
        "List, create, open, select, or archive a durable coding project directory. The catalog follows the "
        "same user across QQ private and group conversations while the current selection stays conversation-local. "
        "Open registers an existing host directory; a selected project is exposed to exec_run as alias:project."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {"type": "string", "enum": ["list", "create", "open", "select", "archive", "current"]},
            "workspace_id": {"type": "string", "pattern": "^proj_[a-f0-9]{32}$"},
            "display_name": {"type": "string", "minLength": 1, "maxLength": 80},
            "path": {
                "type": "string",
                "minLength": 1,
                "maxLength": 2048,
                "description": "open 时使用的真实宿主绝对目录；必须先由 Shell 输出确认，不要猜测。",
            },
            "include_archived": {"type": "boolean"},
        },
        "required": ["action"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "status": {"type": "string"},
            "reason": {"type": "string"},
            "workspace_id": {"type": "string"},
            "selected_workspace_id": {"type": "string"},
            "alias": {"type": "string"},
            "workspaces": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["status"],
        "additionalProperties": True,
    },
    risk="medium",
    confirm="never",
    effects=("project_workspace_state",),
    visible_in=("desktop", "qq"),
    spec_version="1.1.0",
    schema_version=2,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=32 * 1024,
)


WORKSPACE_WRITE_TOOL_SPEC = CapabilityToolSpec(
    capability_id="workspace_write",
    display_name="Write a project file atomically",
    description=(
        "Create or replace one UTF-8 source file inside the selected project. Use this instead of transporting "
        "source through Shell. Writes are atomic and may be guarded by the prior SHA-256."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "workspace_id": {"type": "string", "pattern": "^proj_[a-f0-9]{32}$"},
            "path": {"type": "string", "minLength": 1, "maxLength": 1024},
            "content": {"type": "string", "maxLength": PROJECT_WRITE_MAX_CHARS},
            "expected_sha256": {"type": "string", "pattern": "^[a-fA-F0-9]{64}$"},
            "mode": {"type": "string", "enum": ["create", "replace", "create_or_replace"]},
        },
        "required": ["path", "content"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["succeeded", "failed", "rejected"]},
            "reason": {"type": "string"},
            "workspace_id": {"type": "string"},
            "path": {"type": "string"},
            "created": {"type": "boolean"},
            "replaced": {"type": "boolean"},
            "bytes": {"type": "integer"},
            "sha256": {"type": "string"},
        },
        "required": ["status"],
        "additionalProperties": True,
    },
    risk="medium",
    confirm="never",
    effects=("project_file_write",),
    visible_in=("desktop", "qq"),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=16 * 1024,
)


WORKSPACE_PATCH_TOOL_SPEC = CapabilityToolSpec(
    capability_id="workspace_patch",
    display_name="Apply a unified diff atomically",
    description=(
        "Apply a unified diff to existing UTF-8 files in the selected project. All hunks are validated before "
        "commit; a failed file or hunk leaves every target unchanged."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "workspace_id": {"type": "string", "pattern": "^proj_[a-f0-9]{32}$"},
            "patch": {"type": "string", "minLength": 1, "maxLength": PROJECT_PATCH_MAX_CHARS},
            "expected_files": {
                "type": "object",
                "additionalProperties": {"type": "string", "pattern": "^[a-fA-F0-9]{64}$"},
            },
        },
        "required": ["patch"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["succeeded", "failed", "rejected"]},
            "reason": {"type": "string"},
            "workspace_id": {"type": "string"},
            "files": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["status"],
        "additionalProperties": True,
    },
    risk="medium",
    confirm="never",
    effects=("project_file_write",),
    visible_in=("desktop", "qq"),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=32 * 1024,
)


__all__ = [
    "MANAGE_PROJECT_WORKSPACE_TOOL_SPEC",
    "WORKSPACE_PATCH_TOOL_SPEC",
    "WORKSPACE_WRITE_TOOL_SPEC",
]
