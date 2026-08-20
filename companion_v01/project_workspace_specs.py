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


PROJECT_INSPECT_TOOL_SPEC = CapabilityToolSpec(
    capability_id="project_inspect",
    display_name="Inspect project source",
    description=(
        "Inspect the selected coding project through one read-only authority. Use list to discover project-relative "
        "paths, search to locate text with line numbers, and read to load an exact UTF-8 line range with SHA-256. "
        "Long results return an opaque continuation cursor bound to the current session and source fingerprint."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {"type": "string", "enum": ["list", "search", "read"]},
            "workspace_id": {"type": "string", "pattern": "^proj_[a-f0-9]{32}$"},
            "path": {
                "type": "string",
                "minLength": 1,
                "maxLength": 1024,
                "description": "Project-relative file or directory. Use . for the project root; never pass a host path.",
            },
            "pattern": {
                "type": "string",
                "minLength": 1,
                "maxLength": 512,
                "description": "list only: glob matched against each project-relative path or basename.",
            },
            "max_depth": {"type": "integer", "minimum": 0, "maximum": 20},
            "query": {"type": "string", "minLength": 1, "maxLength": 4096},
            "include": {
                "type": "string",
                "minLength": 1,
                "maxLength": 512,
                "description": "search only: file glob such as *.py or src/**/*.ts.",
            },
            "regex": {"type": "boolean"},
            "case_sensitive": {"type": "boolean"},
            "include_hidden": {"type": "boolean"},
            "start_line": {"type": "integer", "minimum": 1},
            "line_count": {"type": "integer", "minimum": 1, "maximum": 2000},
            "cursor": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "Opaque continuation position/fingerprint. Repeat the same action and selectors with this cursor; "
                    "the cursor never embeds source text or the search query."
                ),
            },
        },
        "required": ["action"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "status": {"type": "string"},
            "action": {"type": "string"},
            "workspace_id": {"type": "string"},
            "path": {"type": "string"},
            "sha256": {"type": "string"},
            "complete": {"type": "boolean"},
            "next_cursor": {"type": "string"},
        },
        "required": ["status"],
        "additionalProperties": True,
    },
    risk="low",
    confirm="never",
    effects=(),
    visible_in=("desktop", "qq"),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="read_only",
    max_result_bytes=64 * 1024,
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
        "Apply a unified diff atomically to selected project files. Supports update, create, delete, and rename "
        "operations for UTF-8 files. All paths, hashes, and hunks are validated before commit; any failure leaves "
        "every target unchanged."
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
            "files": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "path": {"type": "string"},
                        "source_path": {
                            "type": "string",
                            "description": "Original project-relative path for rename operations.",
                        },
                        "operation": {
                            "type": "string",
                            "enum": ["update", "create", "delete", "rename"],
                        },
                        "bytes": {"type": "integer", "minimum": 0},
                        "sha256": {
                            "type": "string",
                            "pattern": "^$|^[a-f0-9]{64}$",
                            "description": "Result SHA-256, or an empty string after deletion.",
                        },
                    },
                    "required": ["path", "operation", "bytes", "sha256"],
                },
            },
        },
        "required": ["status"],
        "additionalProperties": True,
    },
    risk="medium",
    confirm="never",
    effects=("project_file_write",),
    visible_in=("desktop", "qq"),
    spec_version="1.1.0",
    schema_version=2,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=32 * 1024,
)


__all__ = [
    "MANAGE_PROJECT_WORKSPACE_TOOL_SPEC",
    "PROJECT_INSPECT_TOOL_SPEC",
    "WORKSPACE_PATCH_TOOL_SPEC",
    "WORKSPACE_WRITE_TOOL_SPEC",
]
