"""Built-in model tool tables; semantic authority stays in their original specs."""
from __future__ import annotations

from typing import Any
from .core import ToolMetadata
from ..capability_registry import (
    BROWSER_PAGE_TOOL_SPEC,
    BROWSE_MEMORY_TOOL_SPEC,
    CLEAR_ATTACHMENT_FOCUS_TOOL_SPEC,
    FETCH_MEDIA_FROM_URL_TOOL_SPEC,
    INSPECT_ATTACHMENT_TOOL_SPEC,
    INSPECT_GENERATED_FILE_TOOL_SPEC,
    INSPECT_MEDIA_INFO_TOOL_SPEC,
    LIST_WORKSPACE_TOOL_SPEC,
    LOAD_CHARACTER_CONTEXT_TOOL_SPEC,
    LOAD_MATERIAL_TOOL_SPEC,
    MANAGE_GENERATED_FILE_TOOL_SPEC,
    OPEN_BROWSER_TOOL_SPEC,
    OPEN_MEMORY_TOOL_SPEC,
    OPEN_MUSIC_SEARCH_TOOL_SPEC,
    ONEBOT_ACTION_TOOL_SPEC,
    READ_ATTACHMENT_SECTION_TOOL_SPEC,
    READ_MEMORY_TIMELINE_TOOL_SPEC,
    READ_WORKSPACE_TOOL_SPEC,
    REGISTER_WORKSPACE_ITEMS_TOOL_SPEC,
    RETRIEVE_MEMORY_TOOL_SPEC,
    RETRY_ATTACHMENT_TOOL_SPEC,
    SEND_AUDIO_TOOL_SPEC,
    SEND_FILE_TOOL_SPEC,
    SEND_MUSIC_CARD_TOOL_SPEC,
    SEND_STICKER_TOOL_SPEC,
    WEB_SEARCH_TOOL_SPEC,
)
from ..execution_specs import EXEC_CANCEL_TOOL_SPEC, EXEC_INPUT_TOOL_SPEC, EXEC_RUN_TOOL_SPEC, EXEC_STATUS_TOOL_SPEC
from ..skill_specs import LOAD_SKILL_TOOL_SPEC, MANAGE_SKILL_TOOL_SPEC
from ..mcp_specs import INVOKE_MCP_TOOL_SPEC, LOAD_MCP_TOOL_SPEC, MCP_MANAGE_TOOL_SPEC
from ..extension_specs import MANAGE_EXTENSION_TOOL_SPEC
from ..project_workspace_specs import (
    MANAGE_PROJECT_WORKSPACE_TOOL_SPEC,
    PROJECT_INSPECT_TOOL_SPEC,
    WORKSPACE_PATCH_TOOL_SPEC,
    WORKSPACE_WRITE_TOOL_SPEC,
)
from ..capability_discovery_specs import CAPABILITY_LOAD_TOOL_SPEC, CAPABILITY_SEARCH_TOOL_SPEC


INSPECT_MEDIA_INFO_INPUT_SCHEMA: dict[str, Any] = {
    "description": (
        "Read media specs (duration, codec, sample rate, channels, bitrate, "
        "resolution, fps) of an existing file/audio/generated item. Read-only; "
        "does not create files."
    ),
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "source_id": {
            "type": "string",
            "maxLength": 120,
            "description": "Handle of an existing media item, e.g. file_001 / audio_001 / gen_001.",
        },
    },
    "required": ["source_id"],
}


LOAD_CHARACTER_CONTEXT_INPUT_SCHEMA: dict[str, Any] = {
    "description": (
        "Load specific entries from the active character pack's context libraries "
        "by their target names. The available target names are listed in the prompt. "
        "Read-only; does not modify anything."
    ),
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "targets": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 20,
            "description": "Target names to load, taken from the character pack's listed libraries/entries.",
        },
    },
    "required": ["targets"],
}


INSPECT_ATTACHMENT_INPUT_SCHEMA: dict[str, Any] = {
    "description": (
        "List the current attachment workspace, or open and inspect a single image or file "
        "(temporary context, not gifts/character resources/long-term memory). "
        "For several images, pass their exact handles to load_material."
    ),
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "target": {
            "type": "string",
            "maxLength": 120,
            "description": "'all' to list the workspace, or attachment id / title / filename / 'latest'. Defaults to latest.",
        },
        "kind": {
            "type": "string",
            "enum": ["any", "image", "file", "document", "audio"],
            "description": "Optional kind filter. Default any.",
        },
    },
    "required": [],
}


LOAD_MATERIAL_INPUT_SCHEMA: dict[str, Any] = {
    "description": (
        "Reload one to five original images from the current session's temporary "
        "attachment/generated-file workspace into the next multimodal model round. "
        "Use when an older image must be examined again; not needed when the current "
        "turn already includes the image or when only its saved summary is enough."
    ),
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "targets": {
            "type": "array",
            "items": {"type": "string", "maxLength": 120},
            "minItems": 1,
            "maxItems": 5,
            "description": "Current-session image handles such as img_001 or gen_002.",
        },
        "purpose": {
            "type": "string",
            "maxLength": 240,
            "description": "Short reason the original pixels are needed, e.g. compare details before editing.",
        },
    },
    "required": ["targets"],
}


READ_ATTACHMENT_SECTION_INPUT_SCHEMA: dict[str, Any] = {
    "description": (
        "Expand a specific page / line range / table / sheet of a long attachment in "
        "the workspace. Only reveals already-parsed text; the system reports when a "
        "file has no text layer. Not for image gifts or long-term memory."
    ),
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "target": {
            "type": "string",
            "maxLength": 120,
            "description": "file id / title / filename / 'latest'.",
        },
        "section": {
            "type": "string",
            "maxLength": 120,
            "description": "e.g. '第2页' / '第10-30行' / '第1个表' / 'Sheet1'.",
        },
        "kind": {
            "type": "string",
            "enum": ["any", "file", "document"],
            "description": "Optional. Default document.",
        },
        "cursor": {
            "type": "string",
            "description": "Opaque continuation cursor from a previous attachment-section page; pass only the cursor to continue it.",
        },
    },
    "required": [],
}


LIST_WORKSPACE_INPUT_SCHEMA: dict[str, Any] = {
    "description": (
        "List one or more directories in Akane's accessible workspace folder. Use it "
        "first to see what materials exist. Only workspace:/ relative paths — never "
        "local absolute paths."
    ),
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "paths": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 50,
            "description": "workspace:/ relative directories, e.g. ['workspace:/Inbox']. Omit to list the workspace root.",
        },
        "depth": {
            "type": "integer",
            "minimum": 0,
            "maximum": 8,
            "description": "1 lists direct children; larger expands subdirectories. Default 1.",
        },
        "max_entries": {
            "type": "integer",
            "minimum": 1,
            "maximum": 50000,
            "description": "Maximum entries to return. Default 10000.",
        },
        "cursor": {
            "type": "string",
            "description": "Opaque continuation cursor from a previous list_workspace page; pass only the cursor to list the next page.",
        },
    },
    "required": [],
}


READ_WORKSPACE_INPUT_SCHEMA: dict[str, Any] = {
    "description": (
        "Read one or more files from the workspace by their workspace:/ relative paths "
        "(from list_workspace). Supports text/Word/Excel/PDF and ZIP listings; binary "
        "media returns a status pointing to a dedicated tool. Never guess local absolute paths."
    ),
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "targets": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 200,
            "description": "workspace:/ relative file paths from list_workspace.",
        },
        "cursor": {
            "type": "string",
            "description": "Opaque continuation cursor from a previous read_workspace page; pass only the cursor to read the next page.",
        },
    },
    "required": [],
}


INSPECT_GENERATED_FILE_INPUT_SCHEMA: dict[str, Any] = {
    "description": (
        "Re-read a file you generated (e.g. gen_001): its body, head/tail, zip file "
        "list, or manifest. Read-only — does not send, modify, or delete. To resend a "
        "file use send_file; use only currently available capabilities to edit it."
    ),
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "target": {
            "type": "string",
            "maxLength": 120,
            "description": "Generated id / 'latest' / file title. Default latest.",
        },
        "section": {
            "type": "string",
            "maxLength": 260,
            "description": "content | head | tail | summary | file_list | manifest | file:<name>. Default content.",
        },
        "max_chars": {
            "type": "integer",
            "minimum": 500,
            "maximum": 40000,
            "description": "Maximum characters. Default 12000.",
        },
        "cursor": {
            "type": "string",
            "description": "Opaque continuation cursor from a previous inspect_generated_file content page; pass only the cursor to read the next page.",
        },
    },
    "required": [],
}


TOOL_METADATA_BY_TYPE: dict[str, ToolMetadata] = {
    "retrieve_memory": ToolMetadata(
        family="memory",
        operation="read",
        risk="low",
        default_round_budget=3,
        input_schema=RETRIEVE_MEMORY_TOOL_SPEC.input_schema,
    ),
    "read_memory_timeline": ToolMetadata(
        family="memory",
        operation="read",
        risk="low",
        default_round_budget=3,
        input_schema=READ_MEMORY_TIMELINE_TOOL_SPEC.input_schema,
    ),
    "browse_memory": ToolMetadata(
        family="memory",
        operation="read",
        risk="low",
        default_round_budget=3,
        input_schema=BROWSE_MEMORY_TOOL_SPEC.input_schema,
    ),
    "open_memory": ToolMetadata(
        family="memory",
        operation="read",
        risk="low",
        default_round_budget=3,
        input_schema=OPEN_MEMORY_TOOL_SPEC.input_schema,
    ),
    "load_character_context": ToolMetadata(
        family="character_context",
        operation="read",
        risk="low",
        default_round_budget=3,
        input_schema=LOAD_CHARACTER_CONTEXT_INPUT_SCHEMA,
    ),
    "web_search": ToolMetadata(family="web_research", operation="read", risk="low", default_round_budget=8),
    "browser_page": ToolMetadata(
        family="browser_control", operation="mixed", risk="medium", default_round_budget=16, requires_confirmation=True
    ),
    "open_music_search": ToolMetadata(
        family="music_request", operation="control", risk="medium", default_round_budget=4, requires_confirmation=True
    ),
    "fetch_media_from_url": ToolMetadata(
        family="media_fetch", operation="control", risk="medium", default_round_budget=4, requires_confirmation=True
    ),
    "inspect_attachment": ToolMetadata(
        family="file_workspace",
        operation="read",
        risk="low",
        default_round_budget=3,
        input_schema=INSPECT_ATTACHMENT_INPUT_SCHEMA,
    ),
    "load_material": ToolMetadata(
        family="file_workspace",
        operation="read",
        risk="low",
        default_round_budget=4,
        input_schema=LOAD_MATERIAL_INPUT_SCHEMA,
    ),
    "retry_attachment": ToolMetadata(family="file_workspace", operation="control", risk="low", default_round_budget=3),
    "clear_attachment_focus": ToolMetadata(
        family="file_workspace", operation="control", risk="low", default_round_budget=3
    ),
    "read_attachment_section": ToolMetadata(
        family="file_workspace",
        operation="read",
        risk="low",
        default_round_budget=3,
        input_schema=READ_ATTACHMENT_SECTION_INPUT_SCHEMA,
    ),
    "list_workspace": ToolMetadata(
        family="file_workspace",
        operation="read",
        risk="low",
        default_round_budget=4,
        input_schema=LIST_WORKSPACE_INPUT_SCHEMA,
    ),
    "read_workspace": ToolMetadata(
        family="file_workspace",
        operation="read",
        risk="low",
        default_round_budget=4,
        input_schema=READ_WORKSPACE_INPUT_SCHEMA,
    ),
    "register_workspace_items": ToolMetadata(
        family="file_workspace", operation="control", risk="low", default_round_budget=4
    ),
    "inspect_generated_file": ToolMetadata(
        family="file_workspace",
        operation="read",
        risk="low",
        default_round_budget=3,
        input_schema=INSPECT_GENERATED_FILE_INPUT_SCHEMA,
    ),
    "manage_generated_file": ToolMetadata(
        family="file_workspace", operation="control", risk="medium", default_round_budget=3, requires_confirmation=True
    ),
    "send_file": ToolMetadata(
        family="file_handoff", operation="control", risk="medium", default_round_budget=3, requires_confirmation=True
    ),
    "send_audio": ToolMetadata(
        family="qq_audio_delivery", operation="control", risk="low", default_round_budget=3
    ),
    "send_sticker": ToolMetadata(family="social_delivery", operation="control", risk="low", default_round_budget=3),
    "send_music_card": ToolMetadata(
        family="qq_music_delivery", operation="control", risk="low", default_round_budget=3
    ),
    "inspect_media_info": ToolMetadata(
        family="media_workbench",
        operation="read",
        risk="low",
        default_round_budget=3,
        input_schema=INSPECT_MEDIA_INFO_INPUT_SCHEMA,
    ),
    "exec_run": ToolMetadata(
        family="execution",
        operation="control",
        default_round_budget=4,
        background=True,
    ),
    "exec_status": ToolMetadata(
        family="execution",
        operation="read",
        default_round_budget=4,
    ),
    "exec_cancel": ToolMetadata(
        family="execution",
        operation="control",
        default_round_budget=3,
    ),
    "exec_input": ToolMetadata(family="execution", operation="control", risk="high", default_round_budget=4),
    "load_skill": ToolMetadata(
        family="skill",
        operation="read",
        default_round_budget=4,
    ),
    "manage_skill": ToolMetadata(
        family="skill",
        operation="control",
        default_round_budget=4,
    ),
    "mcp_manage": ToolMetadata(
        family="mcp",
        operation="control",
        default_round_budget=4,
    ),
    "manage_extension": ToolMetadata(
        family="extension",
        operation="control",
        default_round_budget=4,
    ),
    "load_mcp": ToolMetadata(
        family="mcp",
        operation="read",
        default_round_budget=4,
    ),
    "invoke_mcp": ToolMetadata(
        family="mcp",
        operation="external",
        default_round_budget=8,
    ),
    "manage_project_workspace": ToolMetadata(
        family="project_workspace",
        operation="control",
        default_round_budget=4,
    ),
    "project_inspect": ToolMetadata(
        family="project_workspace",
        operation="read",
        default_round_budget=8,
    ),
    "workspace_write": ToolMetadata(
        family="project_workspace",
        operation="control",
        default_round_budget=8,
    ),
    "workspace_patch": ToolMetadata(
        family="project_workspace",
        operation="control",
        default_round_budget=8,
    ),
}


# M66-C: Single canonical ToolSpec lookup for all built-in tools.
# BaseToolHandler.tool_spec() uses this by default. Handlers that define their
# own explicit tool_spec() override this lookup — identical result, more explicit.
TOOL_SPEC_BY_TYPE: dict[str, Any] = {
    "capability_search": CAPABILITY_SEARCH_TOOL_SPEC,
    "capability_load": CAPABILITY_LOAD_TOOL_SPEC,
    "retrieve_memory": RETRIEVE_MEMORY_TOOL_SPEC,
    "read_memory_timeline": READ_MEMORY_TIMELINE_TOOL_SPEC,
    "browse_memory": BROWSE_MEMORY_TOOL_SPEC,
    "open_memory": OPEN_MEMORY_TOOL_SPEC,
    "load_character_context": LOAD_CHARACTER_CONTEXT_TOOL_SPEC,
    "web_search": WEB_SEARCH_TOOL_SPEC,
    "browser_page": BROWSER_PAGE_TOOL_SPEC,
    "open_music_search": OPEN_MUSIC_SEARCH_TOOL_SPEC,
    "fetch_media_from_url": FETCH_MEDIA_FROM_URL_TOOL_SPEC,
    "inspect_attachment": INSPECT_ATTACHMENT_TOOL_SPEC,
    "load_material": LOAD_MATERIAL_TOOL_SPEC,
    "retry_attachment": RETRY_ATTACHMENT_TOOL_SPEC,
    "clear_attachment_focus": CLEAR_ATTACHMENT_FOCUS_TOOL_SPEC,
    "read_attachment_section": READ_ATTACHMENT_SECTION_TOOL_SPEC,
    "list_workspace": LIST_WORKSPACE_TOOL_SPEC,
    "read_workspace": READ_WORKSPACE_TOOL_SPEC,
    "register_workspace_items": REGISTER_WORKSPACE_ITEMS_TOOL_SPEC,
    "inspect_generated_file": INSPECT_GENERATED_FILE_TOOL_SPEC,
    "manage_generated_file": MANAGE_GENERATED_FILE_TOOL_SPEC,
    "send_file": SEND_FILE_TOOL_SPEC,
    "send_audio": SEND_AUDIO_TOOL_SPEC,
    "send_sticker": SEND_STICKER_TOOL_SPEC,
    "send_music_card": SEND_MUSIC_CARD_TOOL_SPEC,
    "onebot_action": ONEBOT_ACTION_TOOL_SPEC,
    "inspect_media_info": INSPECT_MEDIA_INFO_TOOL_SPEC,
    "exec_run": EXEC_RUN_TOOL_SPEC,
    "exec_status": EXEC_STATUS_TOOL_SPEC,
    "exec_cancel": EXEC_CANCEL_TOOL_SPEC,
    "exec_input": EXEC_INPUT_TOOL_SPEC,
    "load_skill": LOAD_SKILL_TOOL_SPEC,
    "manage_skill": MANAGE_SKILL_TOOL_SPEC,
    "mcp_manage": MCP_MANAGE_TOOL_SPEC,
    "manage_extension": MANAGE_EXTENSION_TOOL_SPEC,
    "load_mcp": LOAD_MCP_TOOL_SPEC,
    "invoke_mcp": INVOKE_MCP_TOOL_SPEC,
    "manage_project_workspace": MANAGE_PROJECT_WORKSPACE_TOOL_SPEC,
    "project_inspect": PROJECT_INSPECT_TOOL_SPEC,
    "workspace_write": WORKSPACE_WRITE_TOOL_SPEC,
    "workspace_patch": WORKSPACE_PATCH_TOOL_SPEC,
}


def _project_tool_metadata_from_spec(tool_type: str, spec: Any) -> ToolMetadata:
    legacy = TOOL_METADATA_BY_TYPE.get(tool_type) or ToolMetadata()
    return ToolMetadata(
        family=legacy.family,
        operation=legacy.operation,
        risk=str(getattr(spec, "risk", "") or legacy.risk),
        default_round_budget=legacy.default_round_budget,
        background=legacy.background,
        aliases=legacy.aliases,
        input_schema=getattr(spec, "input_schema", None),
        requires_confirmation=str(getattr(spec, "confirm", "never") or "never") != "never",
    )


# Compatibility projection only: capcore ToolSpec is the semantic/schema authority.
TOOL_METADATA_BY_TYPE = {
    tool_type: _project_tool_metadata_from_spec(tool_type, spec)
    for tool_type, spec in TOOL_SPEC_BY_TYPE.items()
}
