from __future__ import annotations

from capcore import CapabilityToolSpec

from .project_workspace import PROJECT_PATCH_MAX_CHARS, PROJECT_WRITE_MAX_CHARS


MANAGE_PROJECT_WORKSPACE_TOOL_SPEC = CapabilityToolSpec(
    capability_id="manage_project_workspace",
    display_name="Manage coding project workspace",
    description=(
        "List, create, open, select, close, or archive a durable coding project directory. Basic inspect/write/patch does not require registration. The catalog follows the "
        "same user across QQ private and group conversations. The current selection stays conversation-local "
        "and is shared by all members of a QQ group; tool permissions still follow the requesting actor. "
        "Create makes a host-managed project and requires display_name. Open registers a real existing host directory "
        "and requires path; use open, not create, when the user specifies Desktop or another host location. A selected "
        "project becomes the conversation's default cwd for all coding tools and is also exposed as alias:project. "
        "An explicit cwd overrides one call only; close returns the conversation to the execution root without changing permissions or files."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "create", "open", "select", "archive", "current", "close"],
                "description": "Operation selector; each action uses only its declared action-specific fields.",
            },
            "workspace_id": {"type": "string", "pattern": "^proj_[a-f0-9]{32}$"},
            "display_name": {
                "type": "string",
                "minLength": 1,
                "maxLength": 80,
                "description": "Required for create; optional label for open. Do not use name, title, goal, or steps.",
            },
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
            "working_directory": {"type": "string"},
            "workspaces": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["status"],
        "additionalProperties": True,
    },
    risk="medium",
    confirm="never",
    effects=("project_workspace_state",),
    visible_in=("desktop", "qq"),
    spec_version="1.3.0",
    schema_version=3,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=32 * 1024,
)


PROJECT_INSPECT_TOOL_SPEC = CapabilityToolSpec(
    capability_id="project_inspect",
    display_name="Inspect project source",
    description=(
        "Inspect source through one read-only authority. Omitted cwd uses the selected project, or the trusted execution root when none is selected; "
        "paths may also use cwd, an absolute host path, or an explicitly addressed persistent project. Use list to discover relative "
        "paths, search to locate text with line numbers, and read to load an exact UTF-8 line range with SHA-256. "
        "Long results return an opaque continuation cursor bound to the current session and source fingerprint."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {"type": "string", "enum": ["list", "search", "read"]},
            "workspace_id": {"type": "string", "pattern": "^proj_[a-f0-9]{32}$"},
            "cwd": {
                "type": "string",
                "maxLength": 512,
                "description": "Optional execution cwd. Uses the same relative, alias, and absolute-directory rules as exec_run.",
            },
            "path": {
                "type": "string",
                "minLength": 1,
                "maxLength": 1024,
                "description": "Path relative to cwd/project, or an absolute host path. Use . for the selected root.",
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
            "effective_cwd": {"type": "string"},
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
    spec_version="1.3.0",
    schema_version=3,
    execution_class="sync",
    idempotency="read_only",
    max_result_bytes=64 * 1024,
)


WORKSPACE_WRITE_TOOL_SPEC = CapabilityToolSpec(
    capability_id="workspace_write",
    display_name="Write a project file atomically",
    description=(
        "Create or replace one UTF-8 source file. Omitted cwd uses the selected project, or the trusted execution root when none is selected; "
        "cwd, an absolute host path, or an explicitly addressed persistent project may override it. Writes are atomic "
        "and may be guarded by the prior SHA-256; project registration is optional."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "workspace_id": {"type": "string", "pattern": "^proj_[a-f0-9]{32}$"},
            "cwd": {
                "type": "string",
                "maxLength": 512,
                "description": "Optional execution cwd using the same path rules as exec_run.",
            },
            "path": {
                "type": "string",
                "minLength": 1,
                "maxLength": 1024,
                "description": "Path relative to cwd/project, or an absolute host file path.",
            },
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
            "effective_cwd": {"type": "string"},
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
    spec_version="1.3.0",
    schema_version=3,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=16 * 1024,
)


WORKSPACE_PATCH_TOOL_SPEC = CapabilityToolSpec(
    capability_id="workspace_patch",
    display_name="Apply a count-free source patch atomically",
    description=(
        "Apply a count-free context patch atomically. Do not calculate unified-diff line ranges. Use exactly: "
        "*** Begin Patch, then one or more *** Update File: path / *** Add File: path / *** Delete File: path "
        "sections, then *** End Patch. Update sections contain @@ (or @@ followed by one unique existing anchor "
        "line), with unchanged lines prefixed by one space, removed lines by -, and added lines by +. A rename "
        "uses *** Update File: old followed by *** Move to: new. Prefix every Add File content line with +; a "
        "Delete File section has no body. Omitted cwd uses the selected project, or the trusted execution root when none is selected; cwd or an "
        "explicitly addressed persistent project may override it. Project registration is optional. Supports update, create, delete, and rename "
        "operations for UTF-8 files. All paths, hashes, and hunks are validated before commit; any failure leaves "
        "every target unchanged."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "workspace_id": {"type": "string", "pattern": "^proj_[a-f0-9]{32}$"},
            "cwd": {
                "type": "string",
                "maxLength": 512,
                "description": "Optional execution cwd using the same path rules as exec_run; patch paths stay relative to it.",
            },
            "patch": {
                "type": "string",
                "minLength": 1,
                "maxLength": PROJECT_PATCH_MAX_CHARS,
                "description": (
                    "Count-free patch text bounded by *** Begin Patch and *** End Patch. Never include numeric "
                    "unified-diff ranges such as @@ -10,2 +10,3 @@. Example: *** Begin Patch\\n*** Update File: "
                    "src/app.py\\n@@\\n-old_value\\n+new_value\\n*** End Patch"
                ),
            },
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
            "effective_cwd": {"type": "string"},
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
    spec_version="2.2.0",
    schema_version=5,
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
