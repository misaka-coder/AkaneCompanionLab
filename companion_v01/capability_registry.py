from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping, Protocol

from capcore import CapabilityToolSpec
from memcore import build_native_memory_tool_specs

from .client_protocol import ClientMode
from .desktop_satellite_specs import DESKTOP_SATELLITE_TOOL_SPECS


DOCUMENT_ATTACHMENT_FORMATS = {
    "txt",
    "md",
    "markdown",
    "log",
    "lrc",
    "srt",
    "vtt",
    "json",
    "toml",
    "yaml",
    "yml",
    "csv",
    "ini",
    "cfg",
    "conf",
    "py",
    "js",
    "ts",
    "tsx",
    "jsx",
    "html",
    "css",
    "xml",
    "sql",
    "java",
    "c",
    "cpp",
    "h",
    "hpp",
    "cs",
    "go",
    "rs",
    "pdf",
    "docx",
    "xlsx",
}

DOCUMENT_GENERATED_FORMATS = {"txt", "md", "docx", "xlsx", "pdf", "json", "csv", "html"}
MEDIA_FORMATS = {"mp3", "wav", "flac", "m4a", "aac", "ogg", "opus", "mp4", "mov", "mkv", "webm", "avi"}
IMAGE_GENERATED_FORMATS = {"png", "jpg", "jpeg", "webp", "gif"}


def _memcore_tool_contract(package_name: str, product_name: str) -> tuple[str, dict[str, Any]]:
    """Project the package-owned memory schema into an Akane capability name."""

    specs = build_native_memory_tool_specs(tool_format="plain", include_material_tool=False)
    spec = next(item for item in specs if str(item.get("name") or "") == package_name)

    def _rename(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): _rename(item) for key, item in value.items()}
        if isinstance(value, list):
            return [_rename(item) for item in value]
        if isinstance(value, str):
            return value.replace(package_name, product_name)
        return value

    return str(_rename(spec.get("description")) or ""), dict(_rename(spec.get("parameters")) or {})


_RETRIEVE_MEMORY_DESCRIPTION, _RETRIEVE_MEMORY_SCHEMA = _memcore_tool_contract(
    "retrieve_for_turn",
    "retrieve_memory",
)
_READ_MEMORY_TIMELINE_DESCRIPTION, _READ_MEMORY_TIMELINE_SCHEMA = _memcore_tool_contract(
    "read_timeline",
    "read_memory_timeline",
)

COMMON_CLIENT_MODES = (ClientMode.SCENE_STATIC, ClientMode.SCENE_LIVE2D, ClientMode.QQ_TEXT, ClientMode.DESKTOP_PET)
WEB_SCENE_CLIENT_MODES = (ClientMode.SCENE_STATIC, ClientMode.SCENE_LIVE2D)
CHAT_FILE_CLIENT_MODES = (ClientMode.QQ_TEXT, ClientMode.DESKTOP_PET)

COMMON_TOOL_NAMES = (
    "retrieve_memory",
    "read_memory_timeline",
    "load_character_context",
    "set_reminder",
    "list_reminders",
    "cancel_reminder",
    "manage_persona",
    "manage_task_workspace",
    "delegate_task",
)

WEB_SEARCH_TOOL_NAMES = ("web_search",)
DESKTOP_BROWSER_TOOL_NAMES = ("browser_page",)
DESKTOP_MUSIC_REQUEST_TOOL_NAMES = ("open_music_search",)
DESKTOP_WORKSPACE_TOOL_NAMES = (
    "list_workspace",
    "read_workspace",
    "focus_workspace",
    "register_workspace_items",
)

WEB_SCENE_TOOL_NAMES = (
    "call_npc",
    "check_inventory",
    "manage_gift",
    "manage_artifact",
)

REMOTE_MEDIA_TOOL_NAMES = ("fetch_media_from_url",)

ATTACHMENT_WORKSPACE_TOOL_NAMES = (
    "sync_attachment_workspace",
    "inspect_attachment",
    "retry_attachment",
    "clear_attachment_focus",
)

IMAGE_MATERIAL_TOOL_NAMES = ("load_material",)
IMAGE_GENERATION_TOOL_NAMES = ("generate_image",)
COVER_SONG_TOOL_NAMES = ("cover_song",)

DOCUMENT_WORKBENCH_TOOL_NAMES = (
    "read_attachment_section",
    "compose_file",
    "revise_generated_file",
    "apply_style_to_existing_file",
)

MEDIA_WORKBENCH_TOOL_NAMES = (
    "inspect_media_info",
    "separate_audio_stems",
    "clean_voice_track",
    "transcribe_media",
    "prepare_voice_dataset",
    "convert_media_file",
)

GENERATED_FILE_MANAGEMENT_TOOL_NAMES = (
    "inspect_generated_file",
    "manage_generated_file",
)

FILE_HANDOFF_TOOL_NAMES = ("send_file",)
CONVERSATION_FILE_AUTHORING_TOOL_NAMES = ("compose_file",)
QQ_STICKER_TOOL_NAMES = ("send_sticker",)


OPEN_BROWSER_TOOL_SPEC = CapabilityToolSpec(
    capability_id="open_browser",
    display_name="Open public page",
    description=(
        "当用户明确要求在自己的电脑上打开一个公开 HTTP(S) 网页时使用。"
        "这项能力只负责交给系统浏览器打开，不读取页面，不代表页面内容已被查看。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "minLength": 8,
                "maxLength": 1600,
                "description": "要交给用户系统浏览器打开的公开 HTTP(S) URL。",
            },
            "label": {
                "type": "string",
                "maxLength": 80,
                "description": "可选的页面简称，仅用于自然说明。",
            },
            "reason": {
                "type": "string",
                "maxLength": 120,
                "description": "为什么需要按用户要求打开该页面。",
            },
        },
        "required": ["url"],
        "additionalProperties": False,
    },
    output_schema={
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["succeeded", "failed", "unavailable"]},
            "reason": {"type": "string"},
        },
        "required": ["status"],
        "additionalProperties": False,
    },
    risk="medium",
    confirm="first_time",
    effects=("external_url_open",),
    visible_in=("desktop", "qq"),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=4096,
)


# ── M66-B: Canonical read-only ToolSpecs ────────────────────────────────────
# These are the single semantic/schema authority for the migrated tool family.
# Native schema generation, legacy prompt projection, normalize, validate, and
# execute all derive from these specs; the old hand-authored prose/metadata
# rows in TOOL_METADATA_BY_TYPE are superseded for these tools.

WEB_SEARCH_TOOL_SPEC = CapabilityToolSpec(
    capability_id="web_search",
    display_name="Web search",
    description=(
        "Search public web pages or extract public URL content when the user asks for current, "
        "online, volatile, or verifiable public information. Use it before guessing about current "
        "external state even if the user did not explicitly say search. Do not use it for localhost, "
        "intranet, file paths, login pages, paid pages, or private links."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search", "batch_search", "extract", "get_sub_domains"],
                "description": "Use search for one query, batch_search for multiple queries, extract for one public URL.",
            },
            "query": {
                "type": "string",
                "description": "Search query for action=search, or a single query when action=batch_search is unnecessary.",
            },
            "queries": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 4,
                "description": "Multiple search queries for action=batch_search.",
            },
            "url": {
                "type": "string",
                "description": "Public URL to extract when action=extract.",
            },
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "description": "Maximum search results to return.",
            },
            "max_chars": {
                "type": "integer",
                "minimum": 500,
                "maximum": 5000,
                "description": "Maximum extracted characters for action=extract.",
            },
            "domain": {
                "type": "string",
                "description": "Optional domain filter for search.",
            },
            "sub_domain": {
                "type": "string",
                "description": "Optional sub-domain filter for search.",
            },
            "domains": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 4,
                "description": "Domains for action=get_sub_domains.",
            },
        },
        "required": ["action"],
    },
    risk="low",
    confirm="never",
    effects=(),
    visible_in=("desktop", "qq", "web"),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="read_only",
    max_result_bytes=32768,
)

RETRIEVE_MEMORY_TOOL_SPEC = CapabilityToolSpec(
    capability_id="retrieve_memory",
    display_name="Retrieve memory",
    description=_RETRIEVE_MEMORY_DESCRIPTION,
    input_schema=_RETRIEVE_MEMORY_SCHEMA,
    risk="low",
    confirm="never",
    effects=(),
    visible_in=("desktop", "qq", "web"),
    spec_version="2.0.0",
    schema_version=2,
    execution_class="sync",
    idempotency="read_only",
    max_result_bytes=16384,
)

READ_MEMORY_TIMELINE_TOOL_SPEC = CapabilityToolSpec(
    capability_id="read_memory_timeline",
    display_name="Read memory timeline",
    description=_READ_MEMORY_TIMELINE_DESCRIPTION,
    input_schema=_READ_MEMORY_TIMELINE_SCHEMA,
    risk="low",
    confirm="never",
    effects=(),
    visible_in=("desktop", "qq", "web"),
    spec_version="2.0.0",
    schema_version=2,
    execution_class="sync",
    idempotency="read_only",
    max_result_bytes=16384,
)

# ── M66-C: Canonical ToolSpecs for remaining built-in families ──────────────
# Each family below removes the corresponding TOOL_METADATA_BY_TYPE input_schema
# and build_prompt_instruction() as semantic authorities for that tool.

LOAD_CHARACTER_CONTEXT_TOOL_SPEC = CapabilityToolSpec(
    capability_id="load_character_context",
    display_name="Load character context",
    description=(
        "Load specific entries from the active character pack's context libraries by their target names. "
        "Read-only; does not modify anything."
    ),
    input_schema={
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
    },
    risk="low",
    confirm="never",
    effects=(),
    visible_in=("desktop", "qq", "web"),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="read_only",
    max_result_bytes=32768,
)

SET_REMINDER_TOOL_SPEC = CapabilityToolSpec(
    capability_id="set_reminder",
    display_name="Set reminder",
    description="Create a reminder to notify the user at a specified time. Use only when the user explicitly asks to be reminded later.",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "content": {"type": "string", "maxLength": 120, "description": "What to remind the user about."},
            "time_text": {"type": "string", "maxLength": 60, "description": "Original time expression from the user."},
            "date_label": {"type": "string", "maxLength": 10, "description": "YYYY-MM-DD date, if known."},
            "time_of_day": {"type": "string", "enum": ["morning", "afternoon", "night", "midnight"]},
            "hour": {"type": "integer", "minimum": 0, "maximum": 23, "description": "Hour (0-23)."},
            "minute": {"type": "integer", "minimum": 0, "maximum": 59, "description": "Minute (0-59)."},
            "offset_minutes": {"type": "integer", "minimum": 1, "description": "Relative offset in minutes from now."},
        },
        "required": ["content"],
    },
    risk="low",
    confirm="never",
    effects=("reminder_create",),
    visible_in=("desktop", "qq", "web"),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=4096,
)

LIST_REMINDERS_TOOL_SPEC = CapabilityToolSpec(
    capability_id="list_reminders",
    display_name="List reminders",
    description="List the user's reminders. Use it when the user asks what reminders they currently have.",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "status": {"type": "string", "enum": ["pending", "done", "all"], "description": "Which reminders to list. Default pending."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10, "description": "Maximum reminders to return. Default 5."},
        },
        "required": [],
    },
    risk="low",
    confirm="never",
    effects=(),
    visible_in=("desktop", "qq", "web"),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="read_only",
    max_result_bytes=8192,
)

CANCEL_REMINDER_TOOL_SPEC = CapabilityToolSpec(
    capability_id="cancel_reminder",
    display_name="Cancel reminder",
    description="Cancel an existing pending reminder. Use only when the user explicitly asks to cancel a specific reminder.",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "reminder_id": {"type": "string", "description": "Exact reminder ID, if known."},
            "target_text": {"type": "string", "maxLength": 80, "description": "Text hint identifying the reminder to cancel."},
            "target_index": {"type": "integer", "description": "1-based index from list_reminders output."},
        },
        "required": [],
    },
    risk="low",
    confirm="never",
    effects=("reminder_cancel",),
    visible_in=("desktop", "qq", "web"),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=4096,
)
CALL_NPC_TOOL_SPEC = CapabilityToolSpec(
    capability_id="call_npc",
    display_name="Call NPC",
    description="Interact with an NPC in the web scene world. Sends a message or action to the specified NPC.",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "npc_id": {"type": "string", "description": "NPC identifier."},
            "message": {"type": "string", "maxLength": 500, "description": "Message or action to send to the NPC."},
            "action": {"type": "string", "description": "Optional action type."},
        },
        "required": [],
    },
    risk="low",
    confirm="never",
    effects=("npc_interaction",),
    visible_in=("web",),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=8192,
)

CHECK_INVENTORY_TOOL_SPEC = CapabilityToolSpec(
    capability_id="check_inventory",
    display_name="Check inventory",
    description="Check gift inventory. Use it when the user asks about gifts on hand or in the gift box.",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "scope": {"type": "string", "enum": ["pending_recent", "pending_all", "kept", "internalized"], "description": "Inventory scope. Prefer pending_recent for what's on hand."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "Maximum items. Default 3 for pending_recent, else 5."},
        },
        "required": [],
    },
    risk="low",
    confirm="never",
    effects=(),
    visible_in=("web",),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="read_only",
    max_result_bytes=8192,
)

MANAGE_GIFT_TOOL_SPEC = CapabilityToolSpec(
    capability_id="manage_gift",
    display_name="Manage gift",
    description="Manage gifts in the scene world: accept, keep, internalize, or return a gift item.",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {"type": "string", "enum": ["accept", "keep", "internalize", "return", "inspect"], "description": "Gift action."},
            "gift_id": {"type": "string", "description": "Gift identifier."},
            "reason": {"type": "string", "maxLength": 200, "description": "Optional reason or reaction."},
        },
        "required": ["action"],
    },
    risk="low",
    confirm="never",
    effects=("gift_state_change",),
    visible_in=("web",),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=4096,
)
MANAGE_ARTIFACT_TOOL_SPEC = CapabilityToolSpec(
    capability_id="manage_artifact",
    display_name="Manage artifact",
    description="Manage artifacts in the scene world: inspect, equip, use, or store an artifact item.",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {"type": "string", "enum": ["inspect", "equip", "use", "store", "list"], "description": "Artifact action."},
            "artifact_id": {"type": "string", "description": "Artifact identifier."},
            "reason": {"type": "string", "maxLength": 200, "description": "Optional reason."},
        },
        "required": ["action"],
    },
    risk="low",
    confirm="never",
    effects=("artifact_state_change",),
    visible_in=("web",),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=4096,
)

MANAGE_PERSONA_TOOL_SPEC = CapabilityToolSpec(
    capability_id="manage_persona",
    display_name="Manage persona",
    description=(
        "Create, update, or inspect expression facets (persona cards) that shape how Akane communicates. "
        "Use to remember or adjust tone, style, nickname, or recurring expression preferences."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {"type": "string", "enum": ["create", "update", "inspect", "list", "delete", "activate", "deactivate"], "description": "Persona action."},
            "persona_id": {"type": "string", "maxLength": 80, "description": "Persona identifier; omit for create."},
            "name": {"type": "string", "maxLength": 80, "description": "Persona name for create/update."},
            "description": {"type": "string", "maxLength": 500, "description": "Persona description or instruction."},
            "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 8, "description": "Optional tags."},
        },
        "required": ["action"],
    },
    risk="medium",
    confirm="never",
    effects=("persona_change",),
    visible_in=("desktop", "qq", "web"),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=8192,
)
MANAGE_TASK_WORKSPACE_TOOL_SPEC = CapabilityToolSpec(
    capability_id="manage_task_workspace",
    display_name="Manage task workspace",
    description=(
        "Create or update a task whiteboard to track multi-step work: record goals, steps, "
        "artifacts, and progress. Use only for tasks that genuinely need tracking."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {"type": "string", "enum": ["create", "update_steps", "add_artifact", "ask_user", "complete", "cleanup", "inspect"], "description": "Task action."},
            "task_id": {"type": "string", "maxLength": 96, "description": "Existing task ID; omit to act on the most recent open task."},
            "goal": {"type": "string", "maxLength": 400, "description": "Task goal for create action."},
            "steps": {"type": "array", "items": {"type": "object"}, "description": "Step list for update_steps."},
            "artifacts": {"type": "array", "items": {"type": "object"}, "description": "Artifact list for add_artifact."},
            "question": {"type": "string", "maxLength": 300, "description": "Question for ask_user action."},
            "reason": {"type": "string", "maxLength": 300, "description": "Optional reason or note."},
        },
        "required": ["action"],
    },
    risk="medium",
    confirm="never",
    effects=("task_workspace_change",),
    visible_in=("desktop", "qq", "web"),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=8192,
)

DELEGATE_TASK_TOOL_SPEC = CapabilityToolSpec(
    capability_id="delegate_task",
    display_name="Delegate task",
    description="Delegate a sub-task to a background worker agent. Use for work that runs independently and reports back.",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "task": {"type": "string", "maxLength": 500, "description": "Task description for the background worker."},
            "tools": {"type": "array", "items": {"type": "string"}, "maxItems": 8, "description": "Allowed tool names for the worker."},
            "context": {"type": "string", "maxLength": 1000, "description": "Additional context for the worker."},
            "task_id": {"type": "string", "maxLength": 80, "description": "Optional task workspace ID to update on completion."},
        },
        "required": ["task"],
    },
    risk="medium",
    confirm="never",
    effects=("background_task_spawn",),
    visible_in=("desktop", "qq", "web"),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="long_task",
    idempotency="effectful",
    max_result_bytes=8192,
)
BROWSER_PAGE_TOOL_SPEC = CapabilityToolSpec(
    capability_id="browser_page",
    display_name="Browser page",
    description=(
        "Open and operate an Akane-managed visible browser window: read, scroll, click numbered candidates, "
        "or submit authorized forms. Does not log in, download, upload, or access private/intranet content."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {"type": "string", "enum": ["open", "read", "scroll", "click", "input", "submit", "close", "status"], "description": "Browser action."},
            "url": {"type": "string", "maxLength": 1600, "description": "Public URL for open action."},
            "target": {"type": "string", "maxLength": 200, "description": "Element target for click/input/scroll."},
            "text": {"type": "string", "maxLength": 500, "description": "Text to input."},
            "reason": {"type": "string", "maxLength": 120, "description": "Why this action is needed."},
        },
        "required": ["action"],
    },
    risk="medium",
    confirm="first_time",
    effects=("browser_action",),
    visible_in=("desktop",),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=32768,
)

OPEN_MUSIC_SEARCH_TOOL_SPEC = CapabilityToolSpec(
    capability_id="open_music_search",
    display_name="Open music search",
    description=(
        "Open a public music platform search page on the desktop browser for the user to find a song. "
        "Does not auto-play, log in, or download."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "title": {"type": "string", "maxLength": 120, "description": "Song title to search for."},
            "artist": {"type": "string", "maxLength": 80, "description": "Optional artist name."},
            "platform": {"type": "string", "enum": ["qq_music", "netease_music", "bilibili", "youtube"], "description": "Music platform. Default qq_music."},
        },
        "required": ["title"],
    },
    risk="medium",
    confirm="first_time",
    effects=("browser_open",),
    visible_in=("desktop",),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=4096,
)
FETCH_MEDIA_FROM_URL_TOOL_SPEC = CapabilityToolSpec(
    capability_id="fetch_media_from_url",
    display_name="Fetch media from URL",
    description=(
        "Download a public video or audio URL into the current workspace so it can be inspected, "
        "transcribed, converted, or sent. Does not handle login-required, paid, or DRM-protected links."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "url": {"type": "string", "maxLength": 1600, "description": "Public media URL to download."},
            "urls": {"type": "array", "items": {"type": "string", "maxLength": 1600}, "maxItems": 5, "description": "Multiple public URLs for batch download."},
            "preferred_title": {"type": "string", "maxLength": 120, "description": "Optional title for the downloaded item."},
        },
        "required": [],
    },
    risk="medium",
    confirm="first_time",
    effects=("file_download",),
    visible_in=("desktop", "qq"),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="idempotent",
    max_result_bytes=8192,
)

SYNC_ATTACHMENT_WORKSPACE_TOOL_SPEC = CapabilityToolSpec(
    capability_id="sync_attachment_workspace",
    display_name="Sync attachment workspace",
    description=(
        "Reorganize the attachment workspace: keep the final set of materials to focus on and collapse the rest. "
        "Submit the final list once; do not toggle items one by one."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "focus_targets": {"type": "array", "items": {"type": "string", "maxLength": 120}, "maxItems": 30, "description": "Final workspace list after reorg: ids / '第2张图' / descriptive names."},
            "kind": {"type": "string", "enum": ["any", "image", "file", "document", "audio"], "description": "Optional kind filter. Default any."},
            "reason": {"type": "string", "maxLength": 160, "description": "Why these materials are needed."},
        },
        "required": [],
    },
    risk="low",
    confirm="never",
    effects=("workspace_reorg",),
    visible_in=("desktop", "qq"),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="idempotent",
    max_result_bytes=8192,
)
INSPECT_ATTACHMENT_TOOL_SPEC = CapabilityToolSpec(
    capability_id="inspect_attachment",
    display_name="Inspect attachment",
    description=(
        "List the current attachment workspace, or open and inspect a single image or file. "
        "To compare multiple materials, prefer sync_attachment_workspace."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "target": {"type": "string", "maxLength": 120, "description": "'all' to list, or attachment id / title / filename / 'latest'. Defaults to latest."},
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
        "Reload one to five original images from the current session's workspace into the next multimodal model round. "
        "Use when an older image must be examined again."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "targets": {"type": "array", "items": {"type": "string", "maxLength": 120}, "minItems": 1, "maxItems": 5, "description": "Current-session image handles such as img_001 or gen_002."},
            "purpose": {"type": "string", "maxLength": 240, "description": "Short reason the original pixels are needed."},
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
        "Remove materials from the current workspace context when finished. "
        "Only deletes storage when the user explicitly asks."
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
    max_result_bytes=32768,
)

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
        },
        "required": [],
    },
    risk="low",
    confirm="never",
    effects=(),
    visible_in=("desktop",),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="read_only",
    max_result_bytes=65536,
)
READ_WORKSPACE_TOOL_SPEC = CapabilityToolSpec(
    capability_id="read_workspace",
    display_name="Read workspace",
    description="Read one or more files from the workspace by their workspace:/ relative paths. Supports text/Word/Excel/PDF and ZIP listings.",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "targets": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 200, "description": "workspace:/ relative file paths from list_workspace."},
            "max_chars": {"type": "integer", "minimum": 1000, "maximum": 4000000, "description": "Maximum characters to read. Default 1000000."},
        },
        "required": ["targets"],
    },
    risk="low",
    confirm="never",
    effects=(),
    visible_in=("desktop",),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="read_only",
    max_result_bytes=65536,
)

FOCUS_WORKSPACE_TOOL_SPEC = CapabilityToolSpec(
    capability_id="focus_workspace",
    display_name="Focus workspace",
    description="Mark one or more workspace items as focused for the current session context.",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "targets": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 20, "description": "workspace:/ relative paths or item handles to focus."},
            "reason": {"type": "string", "maxLength": 200, "description": "Optional reason for focusing these items."},
        },
        "required": ["targets"],
    },
    risk="low",
    confirm="never",
    effects=("workspace_focus",),
    visible_in=("desktop",),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="idempotent",
    max_result_bytes=4096,
)

REGISTER_WORKSPACE_ITEMS_TOOL_SPEC = CapabilityToolSpec(
    capability_id="register_workspace_items",
    display_name="Register workspace items",
    description="Register workspace items as artifacts in the task workspace for tracking and delivery.",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "items": {"type": "array", "items": {"type": "object"}, "minItems": 1, "maxItems": 20, "description": "Items to register, each with id and optional kind/title."},
            "task_id": {"type": "string", "maxLength": 80, "description": "Optional task workspace ID to associate with."},
        },
        "required": ["items"],
    },
    risk="low",
    confirm="never",
    effects=("workspace_register",),
    visible_in=("desktop",),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="idempotent",
    max_result_bytes=4096,
)
COMPOSE_FILE_TOOL_SPEC = CapabilityToolSpec(
    capability_id="compose_file",
    display_name="Compose file",
    description=(
        "把当前对话中已经整理好的内容，或 source_ids 指向的现有材料，生成一个新的文档、表格、"
        "字幕或文本文件。普通聊天回复不要调用。若只是忠实转换现有材料，可不填 content_markdown；"
        "若需要改写、总结或排版，先在 content_markdown/table_rows 中给出要写入的最终内容。"
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "source_ids": {
                "type": "array",
                "items": {"type": "string", "maxLength": 120},
                "maxItems": 20,
                "description": "可选的现有材料句柄，如 file_*、img_*、audio_*、gen_*。",
            },
            "task": {
                "type": "string",
                "maxLength": 500,
                "description": "要整理、转换或导出的目标；不要只写“处理一下”。",
            },
            "output_format": {
                "type": "string",
                "enum": ["md", "txt", "docx", "xlsx", "pdf", "json", "csv", "html", "srt", "lrc", "vtt"],
                "description": "目标文件格式。",
            },
            "output_title": {
                "type": "string",
                "maxLength": 80,
                "description": "可选文件标题；未指定时系统会生成安全标题。",
            },
            "structure": {
                "type": "string",
                "maxLength": 80,
                "description": "可选结构提示，如 summary、table、report、notes。",
            },
            "style": {
                "type": "string",
                "maxLength": 80,
                "description": "可选整体风格提示，如 clean、formal、casual。",
            },
            "fidelity": {
                "type": "string",
                "maxLength": 80,
                "description": "可选保真要求；忠实转换现有材料时可说明 preserve。",
            },
            "content_markdown": {
                "type": "string",
                "maxLength": 80000,
                "description": "要写入文档/文本的完整正文或 Markdown。忠实转换 source_ids 时可留空。",
            },
            "table_rows": {
                "type": "array",
                "items": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 500},
                    "maxItems": 50,
                },
                "maxItems": 1000,
                "description": "生成表格时使用；第一行通常是表头。",
            },
            "formatting": {
                "type": "object",
                "additionalProperties": True,
                "description": "可选白名单样式规则，如 header、columns、rows、row_rules、highlights、auto_width。",
            },
        },
        "required": ["output_format"],
    },
    risk="medium",
    confirm="first_time",
    effects=("file_create",),
    visible_in=("desktop", "qq"),
    spec_version="1.1.0",
    schema_version=2,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=8192,
)

REVISE_GENERATED_FILE_TOOL_SPEC = CapabilityToolSpec(
    capability_id="revise_generated_file",
    display_name="Revise generated file",
    description=(
        "修改一个已经生成的 gen_* 文件并创建新版本，不覆盖旧文件。"
        "instruction 说明修改目标；需要重写正文或表格时，同时提供 content_markdown 或 table_rows。"
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "target": {
                "type": "string",
                "maxLength": 120,
                "description": "要修改的 gen_* 句柄或 latest。",
            },
            "instruction": {
                "type": "string",
                "maxLength": 500,
                "description": "用户要求怎样修改。",
            },
            "output_format": {
                "type": "string",
                "enum": ["md", "txt", "docx", "xlsx", "pdf", "json", "csv", "html"],
                "description": "可选的新版本格式；省略时沿用合适格式。",
            },
            "output_title": {"type": "string", "maxLength": 80, "description": "可选的新版本标题。"},
            "content_markdown": {
                "type": "string",
                "maxLength": 80000,
                "description": "修改后的完整正文或 Markdown；仅描述小改动时可留空。",
            },
            "table_rows": {
                "type": "array",
                "items": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 500},
                    "maxItems": 50,
                },
                "maxItems": 1000,
                "description": "修改后的完整表格行。",
            },
            "formatting": {
                "type": "object",
                "additionalProperties": True,
                "description": "可选白名单样式规则。",
            },
        },
        "required": ["target"],
    },
    risk="medium",
    confirm="first_time",
    effects=("file_revise",),
    visible_in=("desktop", "qq"),
    spec_version="1.1.0",
    schema_version=2,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=8192,
)
APPLY_STYLE_TO_EXISTING_FILE_TOOL_SPEC = CapabilityToolSpec(
    capability_id="apply_style_to_existing_file",
    display_name="Apply style to existing file",
    description=(
        "只给现有 docx/xlsx 材料或生成文件套用样式，不重写正文。"
        "用户要增删改内容时改用 revise_generated_file；从材料整理新文件时用 compose_file。"
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "target": {
                "type": "string",
                "maxLength": 120,
                "description": "file_*、gen_* 句柄或 latest。",
            },
            "target_type": {
                "type": "string",
                "enum": ["attachment", "generated"],
                "description": "可选来源类型；句柄已经明确时可省略。",
            },
            "instruction": {
                "type": "string",
                "maxLength": 500,
                "description": "用户的样式要求，如“姓名列标红、低于60分整行标红”。",
            },
            "output_title": {"type": "string", "maxLength": 80, "description": "可选的样式版标题。"},
            "formatting": {
                "type": "object",
                "additionalProperties": True,
                "description": "可选白名单样式规则，如 header、columns、rows、row_rules、highlights。",
            },
        },
        "required": ["target"],
    },
    risk="medium",
    confirm="first_time",
    effects=("file_style",),
    visible_in=("desktop", "qq"),
    spec_version="1.1.0",
    schema_version=2,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=8192,
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
    max_result_bytes=65536,
)
MANAGE_GENERATED_FILE_TOOL_SPEC = CapabilityToolSpec(
    capability_id="manage_generated_file",
    display_name="Manage generated file",
    description="Manage a previously generated file: archive, delete, rename, or change its delivery status.",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {"type": "string", "enum": ["archive", "delete", "rename", "inspect_status", "list"], "description": "Management action."},
            "target": {"type": "string", "maxLength": 120, "description": "Generated file handle or 'latest'."},
            "new_title": {"type": "string", "maxLength": 120, "description": "New title for rename action."},
        },
        "required": ["action"],
    },
    risk="medium",
    confirm="first_time",
    effects=("file_manage",),
    visible_in=("desktop", "qq"),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=4096,
)

GENERATE_IMAGE_TOOL_SPEC = CapabilityToolSpec(
    capability_id="generate_image",
    display_name="Generate image",
    description=(
        "Generate a new image or edit one to five current-session reference images using the configured image provider. "
        "Use only for explicit image-generation/editing intent."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "prompt": {"type": "string", "minLength": 1, "maxLength": 4000, "description": "Complete creative/edit instruction."},
            "reference_images": {"type": "array", "items": {"type": "string", "maxLength": 120}, "maxItems": 5, "description": "Optional current-session img_*/gen_* handles."},
            "mask_image": {"type": "string", "maxLength": 120, "description": "Optional mask image handle."},
            "size": {"type": "string", "pattern": "^(auto|[0-9]{3,4}x[0-9]{3,4})$", "description": "auto or WIDTHxHEIGHT. Common: 1024x1024, 1536x1024."},
            "quality": {"type": "string", "enum": ["auto", "low", "medium", "high"]},
            "background": {"type": "string", "enum": ["auto", "opaque"]},
            "output_format": {"type": "string", "enum": ["png", "jpeg", "webp"]},
            "compression": {"type": "integer", "minimum": 0, "maximum": 100},
            "input_fidelity": {"type": "string", "enum": ["auto", "low", "high"]},
            "n": {"type": "integer", "minimum": 1, "maximum": 4},
            "output_title": {"type": "string", "maxLength": 80},
            "send_to_user": {"type": "boolean"},
        },
        "required": ["prompt"],
    },
    risk="medium",
    confirm="never",
    effects=("image_generation",),
    visible_in=("desktop", "qq"),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="long_task",
    idempotency="effectful",
    max_result_bytes=8192,
)
SEND_FILE_TOOL_SPEC = CapabilityToolSpec(
    capability_id="send_file",
    display_name="Send file",
    description=(
        "把已经存在的工作台材料或生成文件真正交付给用户。支持 file_*、img_*、audio_*、gen_*。"
        "它不会生成、修改或转码文件；只有工具成功结果才能证明文件已经发出。"
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "target": {"type": "string", "maxLength": 120, "description": "Single file handle or 'latest'."},
            "targets": {"type": "array", "items": {"type": "string", "maxLength": 120}, "maxItems": 10, "description": "Multiple file handles."},
            "delivery_action": {
                "type": "string",
                "enum": ["open", "reveal", "save_desktop", "copy_path"],
                "description": "仅桌宠模式按用户明确要求打开、定位、保存到桌面或复制路径；QQ 发送时省略。",
            },
        },
        "required": [],
    },
    risk="medium",
    confirm="first_time",
    effects=("file_delivery",),
    visible_in=("desktop", "qq"),
    spec_version="1.1.0",
    schema_version=2,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=4096,
)

SEND_GENERATED_FILE_TOOL_SPEC = CapabilityToolSpec(
    capability_id="send_generated_file",
    display_name="Send generated file",
    description="Send a previously generated file to the user. Alias for send_file focused on gen_* handles.",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "target": {"type": "string", "maxLength": 120, "description": "Generated file handle (gen_*) or 'latest'."},
            "targets": {"type": "array", "items": {"type": "string", "maxLength": 120}, "maxItems": 10, "description": "Multiple generated file handles."},
        },
        "required": [],
    },
    risk="medium",
    confirm="first_time",
    effects=("file_delivery",),
    visible_in=("desktop", "qq"),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=4096,
)

SEND_STICKER_TOOL_SPEC = CapabilityToolSpec(
    capability_id="send_sticker",
    display_name="Send sticker",
    description="Send a static sticker in QQ chat when the atmosphere calls for it.",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "emotion": {"type": "string", "maxLength": 40, "description": "Emotion or mood for the sticker selection."},
            "sticker_id": {"type": "string", "maxLength": 80, "description": "Optional specific sticker identifier."},
        },
        "required": [],
    },
    risk="low",
    confirm="never",
    effects=("sticker_delivery",),
    visible_in=("qq",),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=2048,
)
INSPECT_MEDIA_INFO_TOOL_SPEC = CapabilityToolSpec(
    capability_id="inspect_media_info",
    display_name="Inspect media info",
    description="Read media specs (duration, codec, sample rate, channels, bitrate, resolution, fps) of an existing file. Read-only.",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "source_id": {"type": "string", "maxLength": 120, "description": "Handle of an existing media item, e.g. file_001 / audio_001 / gen_001."},
        },
        "required": ["source_id"],
    },
    risk="low",
    confirm="never",
    effects=(),
    visible_in=("desktop", "qq"),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="read_only",
    max_result_bytes=4096,
)

SEPARATE_AUDIO_STEMS_TOOL_SPEC = CapabilityToolSpec(
    capability_id="separate_audio_stems",
    display_name="Separate audio stems",
    description=(
        "把一个现有音频或带音轨视频拆成人声、伴奏两个独立文件。当前只支持人声/伴奏两轨，"
        "一次成功结果应明确返回两个 gen_* 句柄。它只生成结果；用户要收到文件时，等结果返回后再用 send_file。"
        "普通聊天交付默认 mp3；只有用户明确要无损或后续处理需要时才选 wav/flac。"
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "source_id": {
                "type": "string",
                "maxLength": 120,
                "description": "来源音频/视频句柄，如 audio_*、file_*、gen_*。",
            },
            "output_format": {
                "type": "string",
                "enum": ["mp3", "wav", "flac"],
                "description": "两个结果的格式；默认 mp3，wav/flac 体积会明显更大。",
            },
            "output_title": {
                "type": "string",
                "maxLength": 80,
                "description": "可选基础标题，系统会分别加上人声/伴奏。",
            },
        },
        "required": ["source_id"],
    },
    risk="medium",
    confirm="first_time",
    effects=("file_create",),
    visible_in=("desktop", "qq"),
    spec_version="1.1.0",
    schema_version=2,
    execution_class="long_task",
    idempotency="effectful",
    max_result_bytes=8192,
)
CLEAN_VOICE_TRACK_TOOL_SPEC = CapabilityToolSpec(
    capability_id="clean_voice_track",
    display_name="Clean voice track",
    description=(
        "净化现有语音或人声轨，可做降噪、去混响、去回声或人声聚焦。"
        "它不负责普通转码、裁剪和音量调整；这些任务使用 convert_media_file。"
        "成功后返回一个新的 gen_* 句柄，需要交付时再调用 send_file。"
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "source_id": {
                "type": "string",
                "maxLength": 120,
                "description": "来源语音/人声句柄，如 audio_*、file_*、gen_*。",
            },
            "mode": {
                "type": "string",
                "enum": ["denoise", "dereverb", "deecho", "voice_focus"],
                "description": "处理意图；默认 denoise。",
            },
            "quality": {
                "type": "string",
                "enum": ["auto", "ai", "basic"],
                "description": "auto 优先 AI、不可用时基础降级；ai 只接受 AI；basic 直接基础净化。",
            },
            "output_format": {
                "type": "string",
                "enum": ["wav", "flac", "mp3"],
                "description": "输出格式；默认 wav 便于后续处理，直接聊天交付可选 mp3。",
            },
            "output_title": {"type": "string", "maxLength": 80, "description": "可选输出标题。"},
            "post_filter": {
                "type": "boolean",
                "description": "仅 AI 净化时的额外后处理；用户未提出且证据不足时不要硬填。",
            },
        },
        "required": ["source_id"],
    },
    risk="medium",
    confirm="first_time",
    effects=("file_create",),
    visible_in=("desktop", "qq"),
    spec_version="1.1.0",
    schema_version=2,
    execution_class="long_task",
    idempotency="effectful",
    max_result_bytes=8192,
)

TRANSCRIBE_MEDIA_TOOL_SPEC = CapabilityToolSpec(
    capability_id="transcribe_media",
    display_name="Transcribe media",
    description=(
        "把一个或多个现有音频/视频转写成文字稿或字幕文件。它只负责转写，不替代后续总结；"
        "需要总结时先取得真实转写结果，再基于结果继续处理。成功后返回一个或多个 gen_* 句柄。"
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "source_ids": {
                "type": "array",
                "items": {"type": "string", "maxLength": 120},
                "minItems": 1,
                "maxItems": 20,
                "description": "来源句柄列表，如 audio_*、file_*、gen_*；单个来源也放入列表。",
            },
            "output_format": {
                "type": "string",
                "enum": ["md", "txt", "srt", "vtt", "json"],
                "description": "文字稿默认 md；字幕选 srt/vtt。",
            },
            "output_title": {"type": "string", "maxLength": 80, "description": "可选输出标题。"},
            "language": {
                "type": "string",
                "enum": ["zh", "en", "ja", "ko", "auto"],
                "description": "语音语言；不确定时用 auto。",
            },
            "with_timestamps": {"type": "boolean", "description": "是否保留时间戳；默认 true。"},
            "merge_outputs": {
                "type": "boolean",
                "description": "多个来源是否合并成一份转写稿；默认 true。",
            },
            "model_size": {
                "type": "string",
                "enum": ["tiny", "base", "small", "medium", "large-v2", "large-v3"],
                "description": "可选识别模型；未明确要求时用默认 small。",
            },
            "vad_filter": {"type": "boolean", "description": "是否过滤静音段；默认 true。"},
        },
        "required": ["source_ids"],
    },
    risk="medium",
    confirm="first_time",
    effects=("file_create",),
    visible_in=("desktop", "qq"),
    spec_version="1.1.0",
    schema_version=2,
    execution_class="long_task",
    idempotency="effectful",
    max_result_bytes=8192,
)
PREPARE_VOICE_DATASET_TOOL_SPEC = CapabilityToolSpec(
    capability_id="prepare_voice_dataset",
    display_name="Prepare voice dataset",
    description=(
        "把一个或多个人声/语音来源切片、检查并打包为 GPT-SoVITS、RVC 或归档训练素材。"
        "它会生成 manifest 和 zip，不负责训练模型。用户没指定细节时只选合适 profile，"
        "不要凭空填写一整套切片参数。"
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "source_ids": {
                "type": "array",
                "items": {"type": "string", "maxLength": 120},
                "minItems": 1,
                "maxItems": 20,
                "description": "来源人声/语音句柄列表。",
            },
            "profile": {
                "type": "string",
                "enum": ["gpt_sovits", "rvc", "archive"],
                "description": "目标素材预设；默认 gpt_sovits。",
            },
            "output_title": {"type": "string", "maxLength": 80, "description": "可选训练集标题。"},
            "target_sr": {
                "type": "integer",
                "minimum": 8000,
                "maximum": 192000,
                "description": "可选目标采样率；用户没指定时省略。",
            },
            "mono": {"type": "boolean", "description": "是否转单声道；默认 true。"},
            "min_clip_seconds": {
                "type": "number",
                "minimum": 0.5,
                "maximum": 30.0,
                "description": "可选最短切片秒数。",
            },
            "max_clip_seconds": {
                "type": "number",
                "minimum": 1.0,
                "maximum": 60.0,
                "description": "可选最长切片秒数。",
            },
            "silence_threshold_db": {"type": "number", "description": "可选静音阈值 dB。"},
            "min_silence_ms": {"type": "integer", "minimum": 0, "description": "可选最短静音间隔。"},
            "max_silence_kept_ms": {
                "type": "integer",
                "minimum": 0,
                "description": "可选切片中保留的最大静音时长。",
            },
            "clean_first": {"type": "boolean", "description": "是否先做轻量净化；默认 false。"},
            "normalize_volume": {"type": "boolean", "description": "是否做音量标准化；默认 false。"},
        },
        "required": ["source_ids"],
    },
    risk="medium",
    confirm="first_time",
    effects=("file_create",),
    visible_in=("desktop", "qq"),
    spec_version="1.1.0",
    schema_version=2,
    execution_class="long_task",
    idempotency="effectful",
    max_result_bytes=8192,
)

CONVERT_MEDIA_FILE_TOOL_SPEC = CapabilityToolSpec(
    capability_id="convert_media_file",
    display_name="Convert media file",
    description=(
        "转换或重新编码一个现有音频/视频，也可截取片段、调整音量、去头尾静音、淡入淡出或调速。"
        "它不做人声分离、语音净化或转写。成功后返回一个新的 gen_* 句柄，需要交付时再调用 send_file。"
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "source_id": {
                "type": "string",
                "maxLength": 120,
                "description": "来源媒体句柄，如 audio_*、file_*、gen_*。",
            },
            "output_format": {
                "type": "string",
                "enum": ["mp3", "wav", "flac", "m4a", "aac", "ogg", "opus"],
                "description": "目标音频格式；从视频提取音轨时也使用这里。",
            },
            "output_title": {"type": "string", "maxLength": 80, "description": "可选输出标题。"},
            "start_time": {"type": "string", "maxLength": 40, "description": "可选开始时间，如 00:00:35。"},
            "end_time": {"type": "string", "maxLength": 40, "description": "可选结束时间，如 00:01:20。"},
            "normalize_volume": {"type": "boolean", "description": "是否做响度标准化。"},
            "volume_gain_db": {"type": "number", "minimum": -30, "maximum": 30, "description": "整体音量增减 dB。"},
            "trim_silence": {"type": "boolean", "description": "是否去掉头尾静音。"},
            "fade_in_seconds": {"type": "number", "minimum": 0, "maximum": 30},
            "fade_out_seconds": {"type": "number", "minimum": 0, "maximum": 30},
            "speed_ratio": {"type": "number", "minimum": 0.25, "maximum": 4.0},
            "bitrate": {"type": "string", "maxLength": 20, "description": "可选输出码率，如 192k。"},
            "sample_rate": {"type": "integer", "minimum": 8000, "maximum": 192000},
            "channels": {"type": "integer", "minimum": 1, "maximum": 8},
        },
        "required": ["source_id", "output_format"],
    },
    risk="medium",
    confirm="first_time",
    effects=("file_create",),
    visible_in=("desktop", "qq"),
    spec_version="1.1.0",
    schema_version=2,
    execution_class="long_task",
    idempotency="effectful",
    max_result_bytes=8192,
)
COVER_SONG_TOOL_SPEC = CapabilityToolSpec(
    capability_id="cover_song",
    display_name="Cover song",
    description=(
        "Create an AI cover from a current-session audio/video material using a local RVC voice model. "
        "Separates vocals, converts the lead vocal, mixes with instrumental, and stores as a generated artifact."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "source_id": {"type": "string", "maxLength": 120, "description": "Optional source audio/video/generated handle."},
            "song_title": {"type": "string", "maxLength": 120, "description": "Song title. Required when restoring a cached cover without source_id."},
            "artist": {"type": "string", "maxLength": 80, "description": "Optional original artist for cache disambiguation."},
            "voice_model": {"type": "string", "maxLength": 120, "description": "Target local RVC model name, or auto for the configured default."},
            "pitch_shift": {"type": "integer", "minimum": -24, "maximum": 24},
            "index_rate": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "filter_radius": {"type": "integer", "minimum": 0, "maximum": 7},
            "rms_mix_rate": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "protect": {"type": "number", "minimum": 0.0, "maximum": 0.5},
            "vocal_gain_db": {"type": "number", "minimum": -12.0, "maximum": 12.0},
            "instrumental_gain_db": {"type": "number", "minimum": -12.0, "maximum": 6.0},
            "output_format": {"type": "string", "enum": ["mp3", "flac", "wav"]},
            "delivery": {"type": "string", "enum": ["auto", "voice", "file", "both", "none"]},
            "force_rebuild": {"type": "boolean"},
        },
        "required": [],
    },
    risk="medium",
    confirm="never",
    effects=("file_create", "audio_delivery"),
    visible_in=("desktop", "qq"),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="long_task",
    idempotency="effectful",
    max_result_bytes=8192,
)

# ── End M66-C canonical ToolSpecs ───────────────────────────────────────────


@dataclass(frozen=True)
class ExecutionReceipt:
    instance_id: str
    tool_id: str
    offer_id: str
    lease_epoch: str
    offer_expires_at: float
    spec_version: str
    schema_version: int
    schema_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "tool_id": self.tool_id,
            "offer_id": self.offer_id,
            "lease_epoch": self.lease_epoch,
            "offer_expires_at": self.offer_expires_at,
            "spec_version": self.spec_version,
            "schema_version": self.schema_version,
            "schema_hash": self.schema_hash,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "ExecutionReceipt | None":
        if not isinstance(value, Mapping):
            return None
        try:
            receipt = cls(
                instance_id=str(value.get("instance_id") or "").strip(),
                tool_id=str(value.get("tool_id") or "").strip(),
                offer_id=str(value.get("offer_id") or "").strip(),
                lease_epoch=str(value.get("lease_epoch") or "").strip(),
                offer_expires_at=float(value.get("offer_expires_at") or 0),
                spec_version=str(value.get("spec_version") or "").strip(),
                schema_version=int(value.get("schema_version") or 0),
                schema_hash=str(value.get("schema_hash") or "").strip().lower(),
            )
        except (TypeError, ValueError):
            return None
        if not all(
            (
                receipt.instance_id,
                receipt.tool_id,
                receipt.offer_id,
                receipt.lease_epoch,
                receipt.spec_version,
                receipt.schema_hash,
            )
        ):
            return None
        return receipt


@dataclass(frozen=True)
class BrokerExecutionResult:
    status: str
    reason: str = ""
    model_feedback: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ServerLocalBrokerResult:
    status: str
    reason: str = ""
    result: Any = None


class CapabilityOfferSource(Protocol):
    instance_id: str

    def resolve_receipt(self, spec: CapabilityToolSpec) -> ExecutionReceipt | None: ...

    def validate_receipt(self, spec: CapabilityToolSpec, receipt: ExecutionReceipt) -> str: ...

    def dispatch(
        self,
        *,
        spec: CapabilityToolSpec,
        receipt: ExecutionReceipt,
        invocation_id: str,
        arguments: Mapping[str, Any],
        timeout_seconds: float,
    ) -> BrokerExecutionResult: ...


class ExecutorBroker:
    """Instance-owned dispatch and idempotency boundary for every executor."""

    def __init__(self, offer_source: CapabilityOfferSource | None, *, clock=time.time) -> None:
        self.offer_source = offer_source
        self._clock = clock
        self._lock = threading.RLock()
        self._ledger: dict[
            tuple[str, str],
            tuple[str, BrokerExecutionResult | None],
        ] = {}
        self._server_local_ledger: dict[
            tuple[str, str],
            tuple[str, str, ServerLocalBrokerResult | None],
        ] = {}

    def execute_server_local(
        self,
        *,
        tool_id: str,
        invocation_id: str,
        dispatch: Callable[[], Any],
        retain_result: bool = True,
        ledger_scope: str = "",
        request_data: Mapping[str, Any] | None = None,
    ) -> ServerLocalBrokerResult:
        clean_tool_id = str(tool_id or "").strip()
        clean_invocation_id = str(invocation_id or "").strip()
        if not clean_tool_id:
            return ServerLocalBrokerResult(status="rejected", reason="missing_tool_id")
        if not clean_invocation_id:
            return ServerLocalBrokerResult(status="rejected", reason="missing_invocation_id")
        if not callable(dispatch):
            return ServerLocalBrokerResult(status="rejected", reason="invalid_server_local_dispatch")
        ledger_key = (str(ledger_scope or "").strip(), clean_invocation_id)
        request_fingerprint = _broker_request_fingerprint(
            {"tool_id": clean_tool_id, "request": dict(request_data or {})}
        )

        with self._lock:
            if ledger_key in self._ledger:
                return ServerLocalBrokerResult(
                    status="rejected",
                    reason="invocation_id_executor_conflict",
                )
            existing = self._server_local_ledger.get(ledger_key)
            if existing is not None:
                existing_tool_id, existing_fingerprint, existing_result = existing
                if existing_tool_id != clean_tool_id:
                    return ServerLocalBrokerResult(
                        status="rejected",
                        reason="invocation_id_tool_conflict",
                    )
                if existing_fingerprint != request_fingerprint:
                    return ServerLocalBrokerResult(
                        status="rejected",
                        reason="invocation_id_request_conflict",
                    )
                if existing_result is None:
                    return ServerLocalBrokerResult(
                        status="running",
                        reason="duplicate_invocation_in_progress",
                    )
                return existing_result
            self._server_local_ledger[ledger_key] = (
                clean_tool_id,
                request_fingerprint,
                None,
            )
            if len(self._server_local_ledger) > 512:
                terminal = [
                    (key, value)
                    for key, value in self._server_local_ledger.items()
                    if value[2] is not None
                ]
                self._server_local_ledger = dict(terminal[-384:])
                self._server_local_ledger[ledger_key] = (
                    clean_tool_id,
                    request_fingerprint,
                    None,
                )

        try:
            raw_result = dispatch()
            if raw_result is None:
                result = ServerLocalBrokerResult(
                    status="failed",
                    reason="server_local_empty_result",
                )
            else:
                result = ServerLocalBrokerResult(
                    status="succeeded",
                    result=raw_result,
                )
        except Exception:
            result = ServerLocalBrokerResult(
                status="execution_unknown",
                reason="server_local_dispatch_failed",
            )

        ledger_result = result
        if result.status == "succeeded" and not retain_result:
            ledger_result = ServerLocalBrokerResult(status="succeeded", reason="completed")
        with self._lock:
            self._server_local_ledger[ledger_key] = (
                clean_tool_id,
                request_fingerprint,
                ledger_result,
            )
        return result

    def execute(
        self,
        *,
        spec: CapabilityToolSpec,
        receipt_value: Mapping[str, Any] | None,
        invocation_id: str,
        arguments: Mapping[str, Any],
        timeout_seconds: float = 15.0,
        ledger_scope: str = "",
    ) -> BrokerExecutionResult:
        receipt = ExecutionReceipt.from_mapping(receipt_value)
        if receipt is None:
            return BrokerExecutionResult(
                status="rejected",
                reason="missing_execution_receipt",
                model_feedback="当前桌面动作没有有效的执行凭据，不能执行。",
            )
        if receipt.offer_expires_at <= float(self._clock()):
            return BrokerExecutionResult(
                status="unavailable_before_dispatch",
                reason="offer_expired",
                model_feedback="当前无法连接桌面执行器，请直接说明这次暂时不能打开网页。",
            )
        clean_invocation_id = str(invocation_id or "").strip()
        if not clean_invocation_id:
            return BrokerExecutionResult(status="rejected", reason="missing_invocation_id")
        ledger_key = (str(ledger_scope or "").strip(), clean_invocation_id)
        request_fingerprint = _broker_request_fingerprint(
            {
                "tool_id": spec.capability_id,
                "schema_hash": spec.schema_hash,
                "instance_id": receipt.instance_id,
                "arguments": dict(arguments),
            }
        )
        with self._lock:
            if ledger_key in self._server_local_ledger:
                return BrokerExecutionResult(
                    status="rejected",
                    reason="invocation_id_executor_conflict",
                )
            if ledger_key in self._ledger:
                existing_fingerprint, existing_result = self._ledger[ledger_key]
                if existing_fingerprint != request_fingerprint:
                    return BrokerExecutionResult(
                        status="rejected",
                        reason="invocation_id_request_conflict",
                    )
                if existing_result is None:
                    return BrokerExecutionResult(
                        status="running",
                        reason="duplicate_invocation_in_progress",
                        model_feedback="同一桌面动作已经在处理中，不会重复执行。",
                    )
                return existing_result
            self._ledger[ledger_key] = (request_fingerprint, None)
            if len(self._ledger) > 512:
                terminal = [
                    (key, value)
                    for key, value in self._ledger.items()
                    if value[1] is not None
                ]
                self._ledger = dict(terminal[-384:])
                self._ledger[ledger_key] = (request_fingerprint, None)
        source = self.offer_source
        if source is None:
            result = BrokerExecutionResult(
                status="unavailable_before_dispatch",
                reason="executor_unavailable",
                model_feedback="当前无法连接桌面执行器，请直接说明这次暂时不能打开网页。",
            )
        else:
            reason = source.validate_receipt(spec, receipt)
            if reason:
                result = BrokerExecutionResult(
                    status="unavailable_before_dispatch",
                    reason=reason,
                    model_feedback="当前无法连接桌面执行器，请直接说明这次暂时不能打开网页。",
                )
            else:
                try:
                    result = source.dispatch(
                        spec=spec,
                        receipt=receipt,
                        invocation_id=clean_invocation_id,
                        arguments=dict(arguments),
                        timeout_seconds=max(1.0, min(30.0, float(timeout_seconds))),
                    )
                except Exception:
                    result = BrokerExecutionResult(
                        status="execution_unknown",
                        reason="executor_dispatch_failed",
                        model_feedback="桌面动作的执行结果暂时无法确认，请不要声称网页已经打开。",
                    )
        with self._lock:
            self._ledger[ledger_key] = (request_fingerprint, result)
        return result


def _broker_request_fingerprint(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class CapabilitySnapshot:
    client_mode: ClientMode
    has_any_attachment: bool = False
    has_document_attachment: bool = False
    has_media_attachment: bool = False
    has_image_attachment: bool = False
    has_generated_file: bool = False
    has_document_generated_file: bool = False
    has_media_generated_file: bool = False
    has_image_generated_file: bool = False
    has_workspace_file: bool = False
    has_document_workspace_file: bool = False
    has_media_workspace_file: bool = False
    has_image_workspace_file: bool = False
    has_cover_song_cache: bool = False
    has_pending_gift: bool = False


@dataclass(frozen=True)
class CapabilityModule:
    name: str
    layer: str
    modes: tuple[ClientMode, ...]
    tools: tuple[str, ...]
    light_hint: str
    trigger: Callable[[CapabilitySnapshot], bool]
    latent_reason: str = ""
    activation_hint: str = ""
    unavailable_reason: str = ""
    recovery_hint: str = ""

    def applies_to_mode(self, mode: ClientMode) -> bool:
        return mode in self.modes


@dataclass(frozen=True)
class CapabilitySelection:
    light_hints: tuple[str, ...]
    tool_names: tuple[str, ...]
    module_names: tuple[str, ...]
    schema_tool_names: tuple[str, ...] = ()
    native_tool_names: tuple[str, ...] = ()
    layer_names: tuple[str, ...] = ()
    disclosures: tuple[CapabilityDisclosure, ...] = ()
    tool_specs: tuple[CapabilityToolSpec, ...] = ()
    execution_receipts: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    resolved_handlers: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)


@dataclass(frozen=True)
class CapabilityDisclosure:
    """Small model-visible capability fact, separate from callable tool schemas."""

    capability_id: str
    state: str
    summary: str
    reason: str = ""
    activation: str = ""
    tool_names: tuple[str, ...] = ()
    unavailable_reason: str = ""
    recovery_hint: str = ""


def resolve_capability_disclosures(
    selection: CapabilitySelection,
    *,
    available_tool_names: tuple[str, ...] | list[str] | set[str],
) -> tuple[CapabilityDisclosure, ...]:
    """Apply runtime readiness results without exposing a hidden tool schema."""

    available = {str(name or "").strip() for name in available_tool_names if str(name or "").strip()}
    selected = set(selection.tool_names)
    resolved: list[CapabilityDisclosure] = []
    for disclosure in selection.disclosures:
        active_tools = selected.intersection(disclosure.tool_names)
        if disclosure.state != "ready" or not active_tools or active_tools.intersection(available):
            resolved.append(disclosure)
            continue
        resolved.append(
            replace(
                disclosure,
                state="unavailable",
                reason=(disclosure.unavailable_reason or "这项能力依赖的本地组件或外部服务当前没有通过可用性检查。"),
                activation=(disclosure.recovery_hint or "依赖恢复并通过下一次检查后，系统会自动重新开放对应工具。"),
            )
        )
    return tuple(resolved)


def _always(_: CapabilitySnapshot) -> bool:
    return True


def _has_any_attachment(snapshot: CapabilitySnapshot) -> bool:
    return snapshot.has_any_attachment


def _has_document_context(snapshot: CapabilitySnapshot) -> bool:
    return (
        snapshot.has_document_attachment or snapshot.has_document_generated_file or snapshot.has_document_workspace_file
    )


def _has_media_context(snapshot: CapabilitySnapshot) -> bool:
    return snapshot.has_media_attachment or snapshot.has_media_generated_file or snapshot.has_media_workspace_file


def _has_cover_song_context(snapshot: CapabilitySnapshot) -> bool:
    return _has_media_context(snapshot) or snapshot.has_cover_song_cache


def _has_image_context(snapshot: CapabilitySnapshot) -> bool:
    return snapshot.has_image_attachment or snapshot.has_image_generated_file or snapshot.has_image_workspace_file


def _has_generated_file(snapshot: CapabilitySnapshot) -> bool:
    return snapshot.has_generated_file


def _has_deliverable_file(snapshot: CapabilitySnapshot) -> bool:
    return snapshot.has_any_attachment or snapshot.has_generated_file


def _is_web_scene(snapshot: CapabilitySnapshot) -> bool:
    return snapshot.client_mode in {ClientMode.SCENE_STATIC, ClientMode.SCENE_LIVE2D}


# ── M66-E: Server-local offer index ─────────────────────────────────────────
# Replaces ToolReadinessGate for server-local tools. Every concrete handler is
# registered. Handlers with a capability_status() probe are checked with TTL
# caching; handlers without one are static in-process offers. Unknown tools are
# rejected so a stale registry can never make a capability fail open.

_SERVER_OFFER_READY_TTL: float = 15.0
_SERVER_OFFER_UNAVAILABLE_TTL: float = 5.0
_SERVER_OFFER_READY_STATUSES: frozenset[str] = frozenset(
    {"available", "degraded", "ok", "ready"}
)

class ServerLocalOfferIndex:
    """Lightweight per-process offer index backed by capability_status() probes.

    Handlers are registered by tool_id. is_offered() returns True only when the
    most recent probe returned an enabled/ready status. Results are cached with
    separate TTLs for ready (15 s) and unavailable (5 s) outcomes.
    """

    def __init__(
        self,
        *,
        ready_ttl_seconds: float = _SERVER_OFFER_READY_TTL,
        unavailable_ttl_seconds: float = _SERVER_OFFER_UNAVAILABLE_TTL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._handlers: dict[str, Any] = {}
        self._cache: dict[tuple[str, str, str, str], tuple[float, bool]] = {}
        self._lock = threading.RLock()
        self._ready_ttl = max(1.0, float(ready_ttl_seconds))
        self._unavailable_ttl = max(1.0, float(unavailable_ttl_seconds))
        self._clock = clock

    def register(self, tool_id: str, handler: Any) -> None:
        tool_id = str(tool_id or "").strip()
        if not tool_id or handler is None:
            return
        with self._lock:
            self._handlers[tool_id] = handler
            self._cache = {
                key: value
                for key, value in self._cache.items()
                if key[0] != tool_id
            }

    def replace_handlers(self, handlers: Mapping[str, Any]) -> None:
        replacement = {
            str(tool_id or "").strip(): handler
            for tool_id, handler in dict(handlers or {}).items()
            if str(tool_id or "").strip() and handler is not None
        }
        with self._lock:
            changed = {
                tool_id
                for tool_id in set(self._handlers).union(replacement)
                if self._handlers.get(tool_id) is not replacement.get(tool_id)
            }
            self._handlers = replacement
            if changed:
                self._cache = {
                    key: value
                    for key, value in self._cache.items()
                    if key[0] not in changed
                }

    def is_offered(
        self,
        tool_id: str,
        *,
        profile_user_id: str = "",
        session_id: str = "",
        client_mode: str = "",
    ) -> bool:
        tool_id = str(tool_id or "").strip()
        if not tool_id:
            return False
        cache_key = (
            tool_id,
            str(profile_user_id or ""),
            str(session_id or ""),
            str(client_mode or ""),
        )
        with self._lock:
            handler = self._handlers.get(tool_id)
            if handler is None:
                return False
            now = self._clock()
            cached = self._cache.get(cache_key)
            if cached is not None and cached[0] > now:
                return cached[1]
        # Probe outside the lock to avoid blocking other callers.
        offered = self._probe(handler, profile_user_id=profile_user_id, session_id=session_id, client_mode=client_mode)
        ttl = self._ready_ttl if offered else self._unavailable_ttl
        with self._lock:
            self._cache[cache_key] = (self._clock() + ttl, offered)
        return offered

    def _probe(
        self,
        handler: Any,
        *,
        profile_user_id: str,
        session_id: str,
        client_mode: str,
    ) -> bool:
        fn = getattr(handler, "capability_status", None)
        if not callable(fn):
            return True
        try:
            import inspect
            sig = inspect.signature(fn)
            params = set(sig.parameters)
            accepts_kwargs = any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in sig.parameters.values()
            )
            kwargs: dict[str, Any] = {}
            if accepts_kwargs or "profile_user_id" in params:
                kwargs["profile_user_id"] = profile_user_id
            if accepts_kwargs or "session_id" in params:
                kwargs["session_id"] = session_id
            if accepts_kwargs or "client_mode" in params:
                kwargs["client_mode"] = client_mode
            result = fn(**kwargs)
        except Exception:
            return False
        if isinstance(result, bool):
            return result
        if isinstance(result, Mapping):
            if result.get("enabled") is not True:
                return False
            status = str(result.get("status") or "").strip().lower()
            return status in _SERVER_OFFER_READY_STATUSES
        return False


# ── End M66-E ServerLocalOfferIndex ─────────────────────────────────────────


class CapabilityRegistry:
    """Select lightweight ability hints and full tool instructions per turn."""

    def __init__(
        self,
        modules: tuple[CapabilityModule, ...] | None = None,
        *,
        offer_source: CapabilityOfferSource | None = None,
        server_offer_index: "ServerLocalOfferIndex | None" = None,
    ) -> None:
        self.modules = modules or self._default_modules()
        self.offer_source = offer_source
        # M66-E: server-local offer index for tools backed by capability_status() probes
        self.server_offer_index: ServerLocalOfferIndex | None = server_offer_index

    def select(
        self,
        snapshot: CapabilitySnapshot,
        *,
        allowed_tool_names: tuple[str, ...] | None = None,
        hidden_tool_names: tuple[str, ...] = (),
        intent_text: str = "",
        profile_user_id: str = "",
        session_id: str = "",
    ) -> CapabilitySelection:
        hints: list[str] = []
        tools: list[str] = []
        schema_tools: list[str] = []
        module_names: list[str] = []
        layer_names: list[str] = []
        disclosures: list[CapabilityDisclosure] = []
        seen_tools: set[str] = set()
        seen_schema_tools: set[str] = set()
        seen_hints: set[str] = set()
        seen_layers: set[str] = set()
        allowed = (
            {str(name or "").strip() for name in allowed_tool_names if str(name or "").strip()}
            if allowed_tool_names is not None
            else None
        )
        hidden = {str(name or "").strip() for name in hidden_tool_names if str(name or "").strip()}
        for module in self.modules:
            if not module.applies_to_mode(snapshot.client_mode):
                continue
            module_tools = tuple(
                tool_name
                for tool_name in module.tools
                if tool_name not in hidden and (allowed is None or tool_name in allowed)
            )
            if not module_tools:
                continue
            hint = module.light_hint.strip()
            is_ready = module.trigger(snapshot)
            if not is_ready:
                if hint and module.activation_hint.strip() and hint not in seen_hints:
                    seen_hints.add(hint)
                    hints.append(hint)
                if hint and module.activation_hint.strip():
                    disclosures.append(
                        CapabilityDisclosure(
                            capability_id=module.name,
                            state="latent",
                            summary=hint,
                            reason=module.latent_reason.strip(),
                            activation=module.activation_hint.strip(),
                            tool_names=module_tools,
                            unavailable_reason=module.unavailable_reason.strip(),
                            recovery_hint=module.recovery_hint.strip(),
                        )
                    )
                continue

            ready_module_tools: list[str] = []
            for tool_name in module_tools:
                if tool_name not in seen_schema_tools:
                    seen_schema_tools.add(tool_name)
                    schema_tools.append(tool_name)
                if tool_name in seen_tools:
                    ready_module_tools.append(tool_name)
                    continue
                # Server-local offer gate. The index is synchronized with the
                # current handler map before each selection and fails closed.
                if self.server_offer_index is not None:
                    client_mode_val = str(
                        getattr(getattr(snapshot, "client_mode", ""), "value", snapshot.client_mode) or ""
                    )
                    if not self.server_offer_index.is_offered(
                        tool_name,
                        profile_user_id=profile_user_id,
                        session_id=session_id,
                        client_mode=client_mode_val,
                    ):
                        disclosures.append(
                            CapabilityDisclosure(
                                capability_id=module.name,
                                state="unavailable",
                                summary=f"这项能力（{tool_name}）当前不可用。",
                                reason=(
                                    module.unavailable_reason
                                    or "这项能力依赖的本地组件或外部服务当前没有通过可用性检查。"
                                ),
                                activation=module.recovery_hint,
                                tool_names=(tool_name,),
                            )
                        )
                        continue
                seen_tools.add(tool_name)
                tools.append(tool_name)
                ready_module_tools.append(tool_name)

            if not ready_module_tools:
                continue
            if hint and hint not in seen_hints:
                seen_hints.add(hint)
                hints.append(hint)
            if hint:
                disclosures.append(
                    CapabilityDisclosure(
                        capability_id=module.name,
                        state="ready",
                        summary=hint,
                        tool_names=tuple(ready_module_tools),
                        unavailable_reason=module.unavailable_reason.strip(),
                        recovery_hint=module.recovery_hint.strip(),
                    )
                )
            module_names.append(module.name)
            layer = str(module.layer or "").strip()
            if layer and layer not in seen_layers:
                seen_layers.add(layer)
                layer_names.append(layer)
        tool_specs: list[CapabilityToolSpec] = []
        execution_receipts: dict[str, Mapping[str, Any]] = {}
        if snapshot.client_mode in {ClientMode.DESKTOP_PET, ClientMode.QQ_TEXT}:
            receipt = self._resolve_offer_receipt(OPEN_BROWSER_TOOL_SPEC)
            browser_allowed = "open_browser" not in hidden and (allowed is None or "open_browser" in allowed)
            if self.offer_source is not None and browser_allowed:
                hint = OPEN_BROWSER_TOOL_SPEC.description
                if receipt is not None:
                    if "open_browser" not in seen_schema_tools:
                        schema_tools.append("open_browser")
                        seen_schema_tools.add("open_browser")
                    if "desktop_browser_open" not in module_names:
                        module_names.append("desktop_browser_open")
                    if "desktop_browser" not in seen_layers:
                        layer_names.append("desktop_browser")
                        seen_layers.add("desktop_browser")
                    if hint not in seen_hints:
                        hints.append(hint)
                        seen_hints.add(hint)
                    tool_specs.append(OPEN_BROWSER_TOOL_SPEC)
                    if "open_browser" not in seen_tools:
                        tools.append("open_browser")
                        seen_tools.add("open_browser")
                    disclosures.append(
                        CapabilityDisclosure(
                            capability_id="desktop_browser_open",
                            state="ready",
                            summary=hint,
                            tool_names=("open_browser",),
                        )
                    )
                    execution_receipts["open_browser"] = receipt.as_dict()
                else:
                    disclosures.append(
                        CapabilityDisclosure(
                            capability_id="desktop_browser_open",
                            state="unavailable",
                            summary=hint,
                            reason="当前没有在线且已授权的桌面执行器。",
                            activation="桌面客户端重新连接后，这项能力会自动恢复。",
                            tool_names=("open_browser",),
                        )
                    )
            for spec in DESKTOP_SATELLITE_TOOL_SPECS:
                tool_name = spec.capability_id
                if (
                    self.offer_source is None
                    or tool_name in hidden
                    or (allowed is not None and tool_name not in allowed)
                ):
                    continue
                receipt = self._resolve_offer_receipt(spec)
                if receipt is not None:
                    if tool_name not in seen_schema_tools:
                        schema_tools.append(tool_name)
                        seen_schema_tools.add(tool_name)
                    if spec.capability_id not in seen_layers:
                        layer_names.append(spec.capability_id)
                        seen_layers.add(spec.capability_id)
                    if spec.description not in seen_hints:
                        hints.append(spec.description)
                        seen_hints.add(spec.description)
                    tool_specs.append(spec)
                    if tool_name not in seen_tools:
                        tools.append(tool_name)
                        seen_tools.add(tool_name)
                    disclosures.append(
                        CapabilityDisclosure(
                            capability_id=spec.capability_id,
                            state="ready",
                            summary=spec.description,
                            tool_names=(tool_name,),
                        )
                    )
                    execution_receipts[tool_name] = receipt.as_dict()
                else:
                    disclosures.append(
                        CapabilityDisclosure(
                            capability_id=spec.capability_id,
                            state="unavailable",
                            summary=spec.description,
                            reason="当前没有在线且已授权的桌面执行器。",
                            activation="桌面客户端重新连接后，这项能力会自动恢复。",
                            tool_names=(tool_name,),
                        )
                    )
        return CapabilitySelection(
            light_hints=tuple(hints),
            tool_names=tuple(tools),
            module_names=tuple(module_names),
            schema_tool_names=tuple(schema_tools),
            layer_names=tuple(layer_names),
            disclosures=tuple(disclosures),
            tool_specs=tuple(tool_specs),
            execution_receipts=execution_receipts,
        )

    def _resolve_offer_receipt(self, spec: CapabilityToolSpec) -> ExecutionReceipt | None:
        source = self.offer_source
        if source is None:
            return None
        try:
            return source.resolve_receipt(spec)
        except Exception:
            return None

    def tool_names_for_mode(self, mode: ClientMode) -> tuple[str, ...]:
        selected: list[str] = []
        seen: set[str] = set()
        for module in self.modules:
            if not module.applies_to_mode(mode):
                continue
            for tool_name in module.tools:
                if tool_name in seen:
                    continue
                seen.add(tool_name)
                selected.append(tool_name)
        return tuple(selected)

    def _default_modules(self) -> tuple[CapabilityModule, ...]:
        return (
            CapabilityModule(
                name="base",
                layer="common",
                modes=COMMON_CLIENT_MODES,
                tools=COMMON_TOOL_NAMES,
                light_hint="需要过去对话、长期事实、偏好或约定时用 retrieve_memory；需要具体日期/时段原始记录时用 read_memory_timeline。普通闲聊和稳定常识直接回复。你还可以设置/查看/取消提醒、维护表达侧面、记录任务或委派后台工坊。",
                trigger=_always,
            ),
            CapabilityModule(
                name="internet_access",
                layer="web",
                modes=COMMON_CLIENT_MODES,
                tools=WEB_SEARCH_TOOL_NAMES,
                light_hint="需要当前/最新/实时/近期的公开信息时用 web_search，不必等用户说“搜索”；例：日经指数、七月新番、最新模型价格。稳定常识和闲聊直接回复。不要访问私密、内网或登录内容。",
                trigger=_always,
                unavailable_reason="联网搜索服务当前正在检测，或没有通过所在网络节点的可用性检查。",
                recovery_hint="网络或搜索服务恢复后会自动重新开放；当前不要假装已经查到实时结果。",
            ),
            CapabilityModule(
                name="desktop_managed_browser",
                layer="desktop_browser",
                modes=(ClientMode.DESKTOP_PET,),
                tools=DESKTOP_BROWSER_TOOL_NAMES,
                light_hint="桌宠模式下，browser_page 会打开并操作 Akane 可见托管浏览器窗口，用于读取、滚动、按可见候选序号打开链接，以及经授权的点击/输入。不要接管用户手动打开的浏览器标签页，不要登录、下载、上传或访问私密/内网内容。",
                trigger=_always,
            ),
            CapabilityModule(
                name="desktop_music_request",
                layer="music_request",
                modes=(ClientMode.DESKTOP_PET,),
                tools=DESKTOP_MUSIC_REQUEST_TOOL_NAMES,
                light_hint="桌宠模式下，当用户明确要点歌或搜索一首歌来听时，可以用 open_music_search 打开公开音乐平台搜索页；它不代表已经播放成功，后续点击/输入仍按浏览器授权边界处理。",
                trigger=_always,
            ),
            CapabilityModule(
                name="desktop_file_workspace",
                layer="desktop_workspace",
                modes=(ClientMode.DESKTOP_PET,),
                tools=DESKTOP_WORKSPACE_TOOL_NAMES,
                light_hint="桌宠模式下，你始终拥有一个可主动查询的 Akane 文件工作区。用户提到刚放入、寻找、处理或清理某个文件时，先从 workspace:/ 调用 list_workspace 查询，不要先让用户提供本机绝对路径；你也可以批量读取、聚焦材料，并把文件原地登记为文档或媒体工具可用的附件 handle。",
                trigger=_always,
            ),
            CapabilityModule(
                name="remote_media_fetch",
                layer="shared_media",
                modes=CHAT_FILE_CLIENT_MODES,
                tools=REMOTE_MEDIA_TOOL_NAMES,
                light_hint="你也可以先把公开音频/视频链接下载进当前工作台；如果用户只要原视频/原音频，下载后直接交付原文件，不要多做转写、转码或净化。",
                trigger=_always,
            ),
            CapabilityModule(
                name="attachment_workspace",
                layer="shared_attachment_workspace",
                modes=CHAT_FILE_CLIENT_MODES,
                tools=ATTACHMENT_WORKSPACE_TOOL_NAMES,
                light_hint="你可以接收临时图片和文件，并整理当前工作台；文件交付由当前客户端自己的文件交付层处理。",
                trigger=_has_any_attachment,
            ),
            CapabilityModule(
                name="image_material_reload",
                layer="shared_image_material",
                modes=CHAT_FILE_CLIENT_MODES,
                tools=IMAGE_MATERIAL_TOOL_NAMES,
                light_hint="需要重新观察当前会话较早的原图或生成图时，可以按 handle 加载原始图片；当前轮已经带图或摘要足够时不必重复加载。",
                trigger=_has_image_context,
                latent_reason="当前会话还没有可重新加载的图片材料。",
                activation_hint="用户上传图片或生成一张图片后，这项材料读取能力会自动开放。",
            ),
            CapabilityModule(
                name="image_generation",
                layer="shared_image_generation",
                modes=CHAT_FILE_CLIENT_MODES,
                tools=IMAGE_GENERATION_TOOL_NAMES,
                light_hint="用户明确要文生图、图生图、融合多张图片或继续修改生成图时，可以调用已配置的云端图片生成能力；使用当前会话 img_/gen_ handle，不填写路径或 URL。",
                trigger=_always,
                unavailable_reason="当前配置的图片中转没有通过 Images API 可用性检查，因此没有暴露生图工具。",
                recovery_hint="中转恢复 Images API 或切换到支持生图的 provider 后，系统会自动重新开放；当前不要声称已经生成图片。",
            ),
            CapabilityModule(
                name="conversation_file_authoring",
                layer="shared_file_authoring",
                modes=CHAT_FILE_CLIENT_MODES,
                tools=CONVERSATION_FILE_AUTHORING_TOOL_NAMES,
                light_hint="即使没有附件，你也可以把当前对话中已经整理好的内容直接生成文件并交给当前端；用户说开始/直接做/生成时，不要只口头承诺。",
                trigger=_always,
            ),
            CapabilityModule(
                name="qq_file_delivery",
                layer="qq_delivery",
                modes=(ClientMode.QQ_TEXT,),
                tools=FILE_HANDOFF_TOOL_NAMES,
                light_hint="在 QQ 里，你可以把已有工作台材料或生成文件发回给用户；只发送已有文件，不替代生成、转码或修改。",
                trigger=_has_deliverable_file,
            ),
            CapabilityModule(
                name="desktop_file_handoff",
                layer="desktop_workspace",
                modes=(ClientMode.DESKTOP_PET,),
                tools=FILE_HANDOFF_TOOL_NAMES,
                light_hint="在桌宠里，你可以把已有工作台材料或生成文件交给桌宠工作台打开、播放或继续处理；只交付已有文件，不替代生成、转码或修改。",
                trigger=_has_deliverable_file,
            ),
            CapabilityModule(
                name="sticker_pack",
                layer="qq_delivery",
                modes=(ClientMode.QQ_TEXT,),
                tools=QQ_STICKER_TOOL_NAMES,
                light_hint="你有一组静态表情包；聊天氛围适合时可以发送一张表情包，但不要为了展示功能而频繁发送。",
                trigger=_always,
            ),
            CapabilityModule(
                name="document_workbench",
                layer="shared_document",
                modes=CHAT_FILE_CLIENT_MODES,
                tools=DOCUMENT_WORKBENCH_TOOL_NAMES,
                light_hint="你可以阅读、整理、转换和样式加工文本、Office、PDF 等文档。",
                trigger=_has_document_context,
                latent_reason="当前会话和可见工作区里还没有可处理的文档材料，因此没有展开文档读取与修改工具。",
                activation_hint="用户上传文档，或在桌宠的 Akane 工作区放入文档后会自动开放；若工作区文件尚无 handle，先登记再继续处理。当前对话内容仍可直接生成新文档。",
                unavailable_reason="文档处理组件当前没有通过可用性检查。",
                recovery_hint="文档组件恢复后会自动重新开放；已有材料无需重复上传。",
            ),
            CapabilityModule(
                name="media_workbench",
                layer="shared_media",
                modes=CHAT_FILE_CLIENT_MODES,
                tools=MEDIA_WORKBENCH_TOOL_NAMES,
                light_hint="你可以直接处理音频/视频任务：转写、转码、降噪、分离人声、切片打包训练素材等；处理工具返回成果句柄后，再按用户要求调用 send_file 交付。",
                trigger=_has_media_context,
                latent_reason="当前会话和可见工作区里还没有可处理的音频或视频，因此没有展开媒体处理工具。",
                activation_hint="用户上传音频/视频、提供可下载的公开媒体链接，或在桌宠的 Akane 工作区放入媒体文件后会自动开放；工作区文件可先登记为 handle。",
                unavailable_reason="媒体处理所需的本地组件当前没有通过可用性检查。",
                recovery_hint="媒体组件恢复后会自动重新开放；已有材料无需重复上传。",
            ),
            CapabilityModule(
                name="cover_song",
                layer="shared_media",
                modes=CHAT_FILE_CLIENT_MODES,
                tools=COVER_SONG_TOOL_NAMES,
                light_hint=(
                    "你可以用本地角色音色翻唱用户提供的歌曲，并把转换后的人声与原伴奏重新混合成完整音频；"
                    "没有歌曲材料时请自然请用户发送，已完成的歌曲可以按歌名从缓存再次交付。"
                    "直接调用 cover_song 完成；拿到成果句柄后再按用户要求调用 send_file。"
                ),
                trigger=_has_cover_song_context,
                latent_reason="当前还没有歌曲音频、视频或可复用的媒体结果，因此暂不展开翻唱工具。",
                activation_hint="用户上传一首歌、提供可下载的公开歌曲链接，或把歌曲放进桌宠的 Akane 工作区后即可触发；同时需要本机 RVC 服务和至少一个可用音色模型。",
                unavailable_reason="本机 RVC 服务、FFmpeg 或可用音色模型当前没有通过检查。",
                recovery_hint="启动本机 RVC 服务并准备可用音色模型后会自动重新开放；歌曲材料若已经存在，不需要再次上传。",
            ),
            CapabilityModule(
                name="generated_file_management",
                layer="shared_file_authoring",
                modes=CHAT_FILE_CLIENT_MODES,
                tools=GENERATED_FILE_MANAGEMENT_TOOL_NAMES,
                light_hint="你可以回看、交付、归档、删除或清理自己刚生成的文件。",
                trigger=_has_generated_file,
            ),
            CapabilityModule(
                name="web_scene_world",
                layer="web_scene",
                modes=WEB_SCENE_CLIENT_MODES,
                tools=WEB_SCENE_TOOL_NAMES,
                light_hint="你可以围绕当前场景、礼物、藏品和临时 NPC 参与小世界构建。",
                trigger=_is_web_scene,
            ),
        )


def is_document_attachment(item: dict) -> bool:
    kind = str(item.get("kind") or "").strip().lower()
    if kind == "document":
        return True
    detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
    file_kind = str(detail.get("file_kind") or item.get("file_ext") or "").strip().lower().lstrip(".")
    mime_type = str(item.get("mime_type") or detail.get("mime_type") or "").strip().lower()
    return file_kind in DOCUMENT_ATTACHMENT_FORMATS or mime_type.startswith("text/")


def is_media_attachment(item: dict) -> bool:
    kind = str(item.get("kind") or "").strip().lower()
    if kind == "audio":
        return True
    detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
    file_kind = str(detail.get("file_kind") or item.get("file_ext") or "").strip().lower().lstrip(".")
    mime_type = str(item.get("mime_type") or detail.get("mime_type") or "").strip().lower()
    return bool(detail.get("media_info")) or file_kind in MEDIA_FORMATS or mime_type.startswith(("audio/", "video/"))


def is_image_attachment(item: dict) -> bool:
    kind = str(item.get("kind") or "").strip().lower()
    mime_type = str(item.get("mime_type") or "").strip().lower()
    return kind == "image" or mime_type.startswith("image/")


def is_document_generated_file(item: dict) -> bool:
    output_format = str(item.get("output_format") or item.get("file_ext") or "").strip().lower().lstrip(".")
    return output_format in DOCUMENT_GENERATED_FORMATS


def is_media_generated_file(item: dict) -> bool:
    output_format = str(item.get("output_format") or item.get("file_ext") or "").strip().lower().lstrip(".")
    return output_format in MEDIA_FORMATS


def is_image_generated_file(item: dict) -> bool:
    output_format = str(item.get("output_format") or item.get("file_ext") or "").strip().lower().lstrip(".")
    mime_type = str(item.get("mime_type") or "").strip().lower()
    return output_format in IMAGE_GENERATED_FORMATS or mime_type.startswith("image/")
