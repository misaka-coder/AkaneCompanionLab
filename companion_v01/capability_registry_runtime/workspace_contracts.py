"""Workspace registration and generated-file lifecycle tool contracts."""
from __future__ import annotations

from capcore import CapabilityToolSpec


LIST_WORKSPACE_TOOL_SPEC = CapabilityToolSpec(
    capability_id="list_workspace",
    display_name="List workspace",
    description="List one or more directories in Akane's accessible workspace folder. Use it first to see what materials exist.",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "paths": {"type": "array", "items": {"type": "string"}, "maxItems": 50, "description": "workspace:/ relative directories. Omit to list the workspace root."},
            "depth": {"type": "integer", "minimum": 0, "maximum": 8, "description": "1 lists direct children; larger expands subdirectories. Default 1."},
            "max_entries": {"type": "integer", "minimum": 1, "maximum": 50000, "description": "Maximum entries to return. Default 10000."},
            "cursor": {"type": "string", "description": "Opaque continuation cursor from a previous list_workspace page. Pass only the cursor to list the next page of complete entries; do not combine it with paths/depth/max_entries."},
        },
        "required": [],
    },
    risk="low",
    confirm="never",
    effects=(),
    visible_in=("desktop",),
    spec_version="1.1.0",
    schema_version=1,
    execution_class="sync",
    idempotency="read_only",
    max_result_bytes=65536,
)
READ_WORKSPACE_TOOL_SPEC = CapabilityToolSpec(
    capability_id="read_workspace",
    display_name="Read workspace",
    description="Read one or more files from the workspace by their workspace:/ relative paths. Supports text/Word/Excel/PDF and ZIP listings. Long files are returned in complete line pages with a continuation cursor.",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "targets": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 200, "description": "workspace:/ relative file paths from list_workspace."},
            "cursor": {"type": "string", "description": "Opaque continuation cursor from a previous read_workspace page. Pass only the cursor to read the next page of the same files; do not combine it with targets."},
        },
        "required": [],
    },
    risk="low",
    confirm="never",
    effects=(),
    visible_in=("desktop",),
    spec_version="1.2.0",
    schema_version=1,
    execution_class="sync",
    idempotency="read_only",
    max_result_bytes=65536,
)

REGISTER_WORKSPACE_ITEMS_TOOL_SPEC = CapabilityToolSpec(
    capability_id="register_workspace_items",
    display_name="Register workspace items",
    description="Register existing workspace files as attachment handles for media processing and delivery. Large batches return complete handle receipts in continuation pages.",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "targets": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 500, "description": "workspace:/ file or directory paths from list_workspace."},
            "recursive": {"type": "boolean", "description": "Recursively register files under directory targets. Default true."},
            "max_files": {"type": "integer", "minimum": 1, "maximum": 5000, "description": "Maximum files in this registration operation. Default 500."},
            "cursor": {"type": "string", "description": "Opaque continuation cursor from a previous registration page. Pass only the cursor to continue registering the same resolved file set."},
        },
        "required": [],
    },
    risk="low",
    confirm="never",
    effects=("workspace_register",),
    visible_in=("desktop",),
    spec_version="1.1.0",
    schema_version=1,
    execution_class="sync",
    idempotency="idempotent",
    max_result_bytes=32768,
)
INSPECT_GENERATED_FILE_TOOL_SPEC = CapabilityToolSpec(
    capability_id="inspect_generated_file",
    display_name="Inspect generated file",
    description=(
        "Re-read a file you generated: its body, head/tail, zip file list, or manifest. "
        "Read-only — does not send, modify, or delete."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "target": {"type": "string", "maxLength": 120, "description": "Generated id / 'latest' / file title. Default latest."},
            "section": {"type": "string", "maxLength": 260, "description": "content | head | tail | summary | file_list | manifest | file:<name>. Default content."},
            "max_chars": {"type": "integer", "minimum": 500, "maximum": 40000, "description": "Maximum characters. Default 12000."},
            "cursor": {"type": "string", "description": "Opaque continuation cursor from a previous inspect_generated_file content page; pass only the cursor to read the next page."},
        },
        "required": [],
    },
    risk="low",
    confirm="never",
    effects=(),
    visible_in=("desktop", "qq"),
    spec_version="1.1.0",
    schema_version=1,
    execution_class="sync",
    idempotency="read_only",
    max_result_bytes=65536,
)
MANAGE_GENERATED_FILE_TOOL_SPEC = CapabilityToolSpec(
    capability_id="manage_generated_file",
    display_name="Manage generated file",
    description=(
        "Manage files Akane generated in the current workbench. "
        "register copies an existing project file unchanged into managed storage and returns a gen_* handle and SHA-256; it does not send the file. "
        "archive only hides results; delete removes managed file bytes and hides results; "
        "purge also clears the stored content card. This tool never manages user attachments. "
        "When the user asks to clear the whole workbench, call this for generated results and "
        "clear_attachment_focus for user materials in the same tool round."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {
                "type": "string",
                "enum": ["register", "archive", "delete", "purge"],
                "description": "register copies an existing project file unchanged and returns its gen_* handle and SHA-256. archive hides; delete removes managed bytes; purge also clears stored content.",
            },
            "target": {
                "type": "string",
                "maxLength": 120,
                "description": "One generated handle/title, or latest/all. Prefer targets for multiple items.",
            },
            "targets": {
                "type": "array",
                "items": {"type": "string", "maxLength": 120},
                "maxItems": 50,
                "description": "Multiple generated handles/titles; use ['all'] for the whole generated shelf.",
            },
            "reason": {"type": "string", "maxLength": 200, "description": "Optional user-facing cleanup reason."},
            "path": {"type": "string", "maxLength": 4096, "description": "Required for register: project-relative file path, or an absolute file path inside your registered project."},
            "cwd": {"type": "string", "maxLength": 4096, "description": "Optional register directory; defaults to this task's project. Omit with an absolute path."},
        },
        "required": ["action"],
    },
    risk="medium",
    confirm="never",
    effects=("file_manage",),
    visible_in=("desktop", "qq"),
    spec_version="1.2.0",
    schema_version=3,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=4096,
)
