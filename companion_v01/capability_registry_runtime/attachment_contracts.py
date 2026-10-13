"""Attachment focus, image material reload and paged document-reading contracts."""
from __future__ import annotations

from capcore import CapabilityToolSpec


INSPECT_ATTACHMENT_TOOL_SPEC = CapabilityToolSpec(
    capability_id="inspect_attachment",
    display_name="Inspect attachment",
    description=(
        "List the current attachment workspace, or read one material's metadata and existing summary. "
        "The result contains metadata and any existing summary. To examine image pixels, visible text, or visual "
        "details, pass the exact returned handle to load_material. "
        "In QQ groups, 'latest' is restricted to attachments explicitly bound to the current turn; "
        "use 'all' or an exact handle for historical materials. "
        "For multiple images, pass up to five exact handles to load_material."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "target": {"type": "string", "maxLength": 120, "description": "'all' to list, or attachment id / title / filename / 'latest'. In QQ groups, latest only selects a current-turn bound attachment. Defaults to latest."},
            "kind": {"type": "string", "enum": ["any", "image", "file", "document", "audio"], "description": "Optional kind filter. Default any."},
        },
        "required": [],
    },
    risk="low",
    confirm="never",
    effects=(),
    visible_in=("desktop", "qq"),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="read_only",
    max_result_bytes=16384,
)

LOAD_MATERIAL_TOOL_SPEC = CapabilityToolSpec(
    capability_id="load_material",
    display_name="Load material",
    description=(
        "Send the original pixels of one to five images from the current session's workspace into the next multimodal "
        "model round. It examines historical image contents regardless of who sent them. Use inspect_attachment first "
        "when the exact handle is unknown."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "targets": {"type": "array", "items": {"type": "string", "maxLength": 120}, "minItems": 1, "maxItems": 5, "description": "Current-session image handles such as img_001 or gen_002."},
            "purpose": {"type": "string", "maxLength": 240, "description": "Optional neutral verification goal. It is not image evidence and must not presuppose people, numbers, or conclusions."},
        },
        "required": ["targets"],
    },
    risk="low",
    confirm="never",
    effects=(),
    visible_in=("desktop", "qq"),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="read_only",
    max_result_bytes=8192,
)
RETRY_ATTACHMENT_TOOL_SPEC = CapabilityToolSpec(
    capability_id="retry_attachment",
    display_name="Retry attachment",
    description="Retry processing a failed or pending attachment in the workspace.",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "target": {"type": "string", "maxLength": 120, "description": "Attachment id / title / filename, or 'latest'."},
            "kind": {"type": "string", "enum": ["any", "image", "file", "document", "audio"], "description": "Optional kind filter."},
        },
        "required": [],
    },
    risk="low",
    confirm="never",
    effects=("attachment_retry",),
    visible_in=("desktop", "qq"),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="idempotent",
    max_result_bytes=4096,
)

CLEAR_ATTACHMENT_FOCUS_TOOL_SPEC = CapabilityToolSpec(
    capability_id="clear_attachment_focus",
    display_name="Clear attachment focus",
    description=(
        "Remove user-provided materials from the current workbench context when finished. "
        "Only deletes managed attachment bytes when the user explicitly asks. "
        "This tool never manages gen_* results; when the user asks to clear the whole workbench, "
        "also call manage_generated_file for generated results in the same tool round."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "target": {"type": "string", "maxLength": 120, "description": "current | latest | all | attachment id/title/filename."},
            "targets": {"type": "array", "items": {"type": "string", "maxLength": 120}, "maxItems": 30, "description": "Multiple specific materials to clear."},
            "kind": {"type": "string", "enum": ["any", "image", "file", "document", "audio"], "description": "Optional kind filter. Default any."},
            "delete_storage": {"type": "boolean", "description": "Delete original file bytes. Default false."},
            "reason": {"type": "string", "maxLength": 160, "description": "Optional reason."},
        },
        "required": [],
    },
    risk="low",
    confirm="never",
    effects=("workspace_clear",),
    visible_in=("desktop", "qq"),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="idempotent",
    max_result_bytes=4096,
)
READ_ATTACHMENT_SECTION_TOOL_SPEC = CapabilityToolSpec(
    capability_id="read_attachment_section",
    display_name="Read attachment section",
    description=(
        "Expand a specific page, line range, table, or sheet of a long attachment. "
        "Only reveals already-parsed text; reports when no text layer exists."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "target": {"type": "string", "maxLength": 120, "description": "file id / title / filename / 'latest'."},
            "section": {"type": "string", "maxLength": 120, "description": "e.g. '第2页' / '第10-30行' / '第1个表' / 'Sheet1'."},
            "kind": {"type": "string", "enum": ["any", "file", "document"], "description": "Optional. Default document."},
            "cursor": {"type": "string", "description": "Opaque continuation cursor from a previous read_attachment_section page. Pass only the cursor to continue the same attachment section."},
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
