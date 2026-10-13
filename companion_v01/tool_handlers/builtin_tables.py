"""Built-in model tool tables; semantic authority stays in their original specs."""
from __future__ import annotations

from typing import Any, Mapping
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
from ..text_card_specs import RENDER_CARD_TOOL_SPEC


# Existing schema exports are aliases of the canonical specs, not a second definition.
INSPECT_MEDIA_INFO_INPUT_SCHEMA = INSPECT_MEDIA_INFO_TOOL_SPEC.input_schema
LOAD_CHARACTER_CONTEXT_INPUT_SCHEMA = LOAD_CHARACTER_CONTEXT_TOOL_SPEC.input_schema
INSPECT_ATTACHMENT_INPUT_SCHEMA = INSPECT_ATTACHMENT_TOOL_SPEC.input_schema
LOAD_MATERIAL_INPUT_SCHEMA = LOAD_MATERIAL_TOOL_SPEC.input_schema
READ_ATTACHMENT_SECTION_INPUT_SCHEMA = READ_ATTACHMENT_SECTION_TOOL_SPEC.input_schema
LIST_WORKSPACE_INPUT_SCHEMA = LIST_WORKSPACE_TOOL_SPEC.input_schema
READ_WORKSPACE_INPUT_SCHEMA = READ_WORKSPACE_TOOL_SPEC.input_schema
INSPECT_GENERATED_FILE_INPUT_SCHEMA = INSPECT_GENERATED_FILE_TOOL_SPEC.input_schema


# Supplemental classification only; schema, risk and confirmation come from ToolSpec.
_BUILTIN_TOOL_CLASSIFICATION: dict[str, dict[str, Any]] = {
    'retrieve_memory': {'family': 'memory', 'operation': 'read', 'default_round_budget': 3},
    'read_memory_timeline': {'family': 'memory', 'operation': 'read', 'default_round_budget': 3},
    'browse_memory': {'family': 'memory', 'operation': 'read', 'default_round_budget': 3},
    'open_memory': {'family': 'memory', 'operation': 'read', 'default_round_budget': 3},
    'load_character_context': {'family': 'character_context', 'operation': 'read', 'default_round_budget': 3},
    'web_search': {'family': 'web_research', 'operation': 'read', 'default_round_budget': 8},
    'browser_page': {'family': 'browser_control', 'operation': 'mixed', 'default_round_budget': 16},
    'open_music_search': {'family': 'music_request', 'operation': 'control', 'default_round_budget': 4},
    'fetch_media_from_url': {'family': 'media_fetch', 'operation': 'control', 'default_round_budget': 4},
    'inspect_attachment': {'family': 'file_workspace', 'operation': 'read', 'default_round_budget': 3},
    'load_material': {'family': 'file_workspace', 'operation': 'read', 'default_round_budget': 4},
    'retry_attachment': {'family': 'file_workspace', 'operation': 'control', 'default_round_budget': 3},
    'clear_attachment_focus': {'family': 'file_workspace', 'operation': 'control', 'default_round_budget': 3},
    'read_attachment_section': {'family': 'file_workspace', 'operation': 'read', 'default_round_budget': 3},
    'list_workspace': {'family': 'file_workspace', 'operation': 'read', 'default_round_budget': 4},
    'read_workspace': {'family': 'file_workspace', 'operation': 'read', 'default_round_budget': 4},
    'register_workspace_items': {'family': 'file_workspace', 'operation': 'control', 'default_round_budget': 4},
    'inspect_generated_file': {'family': 'file_workspace', 'operation': 'read', 'default_round_budget': 3},
    'manage_generated_file': {'family': 'file_workspace', 'operation': 'control', 'default_round_budget': 3},
    'send_file': {'family': 'file_handoff', 'operation': 'control', 'default_round_budget': 3},
    'render_card': {'family': 'file_authoring', 'operation': 'write'},
    'send_audio': {'family': 'qq_audio_delivery', 'operation': 'control', 'default_round_budget': 3},
    'send_sticker': {'family': 'social_delivery', 'operation': 'control', 'default_round_budget': 3},
    'send_music_card': {'family': 'qq_music_delivery', 'operation': 'control', 'default_round_budget': 3},
    'inspect_media_info': {'family': 'media_workbench', 'operation': 'read', 'default_round_budget': 3},
    'exec_run': {'family': 'execution', 'operation': 'control', 'default_round_budget': 4, 'background': True},
    'exec_status': {'family': 'execution', 'operation': 'read', 'default_round_budget': 4},
    'exec_cancel': {'family': 'execution', 'operation': 'control', 'default_round_budget': 3},
    'exec_input': {'family': 'execution', 'operation': 'control', 'default_round_budget': 4},
    'load_skill': {'family': 'skill', 'operation': 'read', 'default_round_budget': 4},
    'manage_skill': {'family': 'skill', 'operation': 'control', 'default_round_budget': 4},
    'mcp_manage': {'family': 'mcp', 'operation': 'control', 'default_round_budget': 4},
    'manage_extension': {'family': 'extension', 'operation': 'control', 'default_round_budget': 4},
    'load_mcp': {'family': 'mcp', 'operation': 'read', 'default_round_budget': 4},
    'invoke_mcp': {'family': 'mcp', 'operation': 'external', 'default_round_budget': 8},
    'manage_project_workspace': {'family': 'project_workspace', 'operation': 'control', 'default_round_budget': 4},
    'project_inspect': {'family': 'project_workspace', 'operation': 'read', 'default_round_budget': 8},
    'workspace_write': {'family': 'project_workspace', 'operation': 'control', 'default_round_budget': 8},
    'workspace_patch': {'family': 'project_workspace', 'operation': 'control', 'default_round_budget': 8},
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
    "render_card": RENDER_CARD_TOOL_SPEC,
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


def _project_tool_metadata_from_spec(spec: Any, *, classification: Mapping[str, Any] | None = None) -> ToolMetadata:
    """Project legacy metadata from the canonical spec and optional classification."""
    if classification is None:
        capability_id = str(getattr(spec, "capability_id", "") or "").strip()
        classification = _BUILTIN_TOOL_CLASSIFICATION.get(capability_id)
    details = dict(classification or {})
    return ToolMetadata(
        family=details.get("family", "general"),
        operation=details.get("operation", "mixed"),
        risk=str(getattr(spec, "risk", "") or "medium"),
        default_round_budget=details.get("default_round_budget", 3),
        background=details.get("background", False),
        aliases=tuple(details.get("aliases", ())),
        input_schema=getattr(spec, "input_schema", None),
        requires_confirmation=str(getattr(spec, "confirm", "never") or "never") != "never",
    )


# Compatibility projection only: capcore ToolSpec is the semantic/schema authority.
TOOL_METADATA_BY_TYPE = {
    tool_type: _project_tool_metadata_from_spec(spec)
    for tool_type, spec in TOOL_SPEC_BY_TYPE.items()
}
