from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping

from capcore import CapabilityToolSpec
from memcore import build_native_memory_tool_specs

from .client_protocol import ClientMode
from .desktop_satellite_specs import DESKTOP_SATELLITE_TOOL_SPECS
from .onebot_model_actions import MODEL_ONEBOT_ACTION_NAMES, MODEL_ONEBOT_MESSAGE_SELECTOR_KINDS
from .tool_continuation import FINISH_TURN_PARAMETER


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
    spec = next(
        (item for item in specs if str(item.get("name") or "") == package_name),
        None,
    )
    if spec is None:
        raise RuntimeError(f"memcore_native_tool_contract_missing:{package_name}")
    name_projection = {
        "retrieve_for_turn": "retrieve_memory",
        "read_timeline": "read_memory_timeline",
        package_name: product_name,
    }

    def _rename(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): _rename(item) for key, item in value.items()}
        if isinstance(value, list):
            return [_rename(item) for item in value]
        if isinstance(value, str):
            projected = value
            for source_name, target_name in name_projection.items():
                projected = projected.replace(source_name, target_name)
            return projected
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
_BROWSE_MEMORY_DESCRIPTION, _BROWSE_MEMORY_SCHEMA = _memcore_tool_contract(
    "browse_memory",
    "browse_memory",
)
_OPEN_MEMORY_DESCRIPTION, _OPEN_MEMORY_SCHEMA = _memcore_tool_contract(
    "open_memory",
    "open_memory",
)


def _clarify_explicit_kind_contract(schema: dict[str, Any]) -> dict[str, Any]:
    """State the include_explicit/kind_patterns coupling the package schema omits.

    Passing include_explicit=true without kind_patterns (or the reverse) is
    rejected by validation, which historically turned whole retrievals into
    empty results. The descriptions are patched Akane-side so the fix reaches
    the model without a memcore wheel change.
    """
    properties = dict(schema.get("properties") or {})
    if "include_explicit" in properties:
        properties["include_explicit"] = {
            **properties["include_explicit"],
            "description": (
                "Whether this query needs explicit trace/event/material records. "
                "Set true only WITH kind_patterns "
                '(e.g. ["tool.*"]); true without kind_patterns is rejected '
                "and returns nothing. Leave false for ordinary chat."
            ),
        }
    if "kind_patterns" in properties:
        properties["kind_patterns"] = {
            **properties["kind_patterns"],
            "description": (
                'Kinds of explicit trace/event/material records to include (e.g. "tool.*"). '
                "Only valid together with include_explicit=true; "
                "omit both unless tool/material traces are specifically needed."
            ),
        }
    return {**schema, "properties": properties}


_RETRIEVE_MEMORY_SCHEMA = _clarify_explicit_kind_contract(_RETRIEVE_MEMORY_SCHEMA)

COMMON_CLIENT_MODES = (ClientMode.SCENE_STATIC, ClientMode.SCENE_LIVE2D, ClientMode.QQ_TEXT, ClientMode.DESKTOP_PET)
CHAT_FILE_CLIENT_MODES = (ClientMode.QQ_TEXT, ClientMode.DESKTOP_PET)

COMMON_TOOL_NAMES = (
    "retrieve_memory",
    "read_memory_timeline",
    "browse_memory",
    "open_memory",
    "load_skill",
    "load_mcp",
    "invoke_mcp",
    "load_character_context",
)

WEB_SEARCH_TOOL_NAMES = ("web_search",)
EXEC_TOOL_NAMES = (
    "spawn_subagent",
    "manage_project_workspace",
    "project_inspect",
    "workspace_write",
    "workspace_patch",
    "exec_run",
    "exec_status",
    "exec_cancel",
    "exec_input",
    "manage_generated_file",
    "manage_skill",
    "mcp_manage",
)
EXTENSION_MANAGEMENT_TOOL_NAMES = ("manage_extension",)
DESKTOP_BROWSER_TOOL_NAMES = ("browser_page",)
DESKTOP_MUSIC_REQUEST_TOOL_NAMES = ("open_music_search",)
DESKTOP_WORKSPACE_TOOL_NAMES = (
    "list_workspace",
    "read_workspace",
    "register_workspace_items",
)

REMOTE_MEDIA_TOOL_NAMES = ("fetch_media_from_url",)

ATTACHMENT_WORKSPACE_TOOL_NAMES = (
    "inspect_attachment",
    "retry_attachment",
    "clear_attachment_focus",
)

IMAGE_MATERIAL_TOOL_NAMES = ("load_material",)

DOCUMENT_WORKBENCH_TOOL_NAMES = (
    "read_attachment_section",
)

MEDIA_WORKBENCH_TOOL_NAMES = (
    "inspect_media_info",
)

# M68: Shell may replace built-in inspection. Installed extension descriptors
# remain independently discoverable; they are never hidden by this profile.
MEDIA_WORKBENCH_SHELL_OVERLAP_TOOL_NAMES = ("inspect_media_info",)

GENERATED_FILE_MANAGEMENT_TOOL_NAMES = (
    "inspect_generated_file",
    "manage_generated_file",
)

FILE_HANDOFF_TOOL_NAMES = ("send_file",)
QQ_STICKER_TOOL_NAMES = ("send_sticker",)
QQ_ONEBOT_ACTION_TOOL_NAMES = ("onebot_action",)
QQ_MUSIC_CARD_TOOL_NAMES = ("send_music_card",)
QQ_AUDIO_DELIVERY_TOOL_NAMES = ("send_audio",)


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
        "intranet, file paths, login pages, paid pages, or private links. Prefer batch_search for "
        "multiple targets, time ranges, news aggregation, or cross-source verification. If coverage "
        "is narrow, vary the query/source and extract critical pages before claiming broad coverage. "
        "When a result includes a cursor, pass only that cursor and only when more content is needed. "
        "This tool does not open a browser window, click, scroll, or prove that a page was viewed."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search", "batch_search", "extract", "get_sub_domains"],
                "description": (
                    "Use search for one query, batch_search for multiple queries, extract for one public URL. "
                    "Required unless cursor is supplied alone for continuation."
                ),
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
            "cursor": {
                "type": "string",
                "description": "Opaque continuation cursor from a previous web_search page; pass only the cursor to read the next page of the same results or extraction.",
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
        "required": [],
    },
    risk="low",
    confirm="never",
    effects=(),
    visible_in=("desktop", "qq", "web"),
    spec_version="1.2.0",
    schema_version=2,
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

BROWSE_MEMORY_TOOL_SPEC = CapabilityToolSpec(
    capability_id="browse_memory",
    display_name="Browse memory catalog",
    description=_BROWSE_MEMORY_DESCRIPTION,
    input_schema=_BROWSE_MEMORY_SCHEMA,
    risk="low",
    confirm="never",
    effects=(),
    visible_in=("desktop", "qq", "web"),
    spec_version="2.0.0",
    schema_version=2,
    execution_class="sync",
    idempotency="read_only",
    max_result_bytes=65536,
)

OPEN_MEMORY_TOOL_SPEC = CapabilityToolSpec(
    capability_id="open_memory",
    display_name="Open memory evidence",
    description=_OPEN_MEMORY_DESCRIPTION,
    input_schema=_OPEN_MEMORY_SCHEMA,
    risk="low",
    confirm="never",
    effects=(),
    visible_in=("desktop", "qq", "web"),
    spec_version="2.0.0",
    schema_version=2,
    execution_class="sync",
    idempotency="read_only",
    max_result_bytes=65536,
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

from .browser_page_contract import EXTRA_PROPERTIES as BROWSER_EXTRA_PROPERTIES

BROWSER_PAGE_TOOL_SPEC = CapabilityToolSpec(
    capability_id="browser_page",
    display_name="Browser page",
    description=(
        "Operate either an Akane-managed browser or the owner's connected personal Chrome on the bound device. "
        "Use session_source=personal_chrome with connect, list_tabs, select_tab; copy returned browser_session_id/device_epoch. "
        "Use handoff before switching to computer_use and disconnect to detach without closing Chrome. Navigate to a public page, "
        "read its text, take an accessibility snapshot or viewport screenshot, scroll, list visible elements, or click/fill/press "
        "authorized controls. Public download events may be captured and queried with download_status; files are not sent automatically. "
        "Managed sessions also support uploads from resource handles, select_option/set_checked/hover, scoped frame reads and short run_actions batches. "
        "Use capabilities to inspect backend differences. Private/intranet URLs require host network scope. "
        "Returned page state is evidence of what was observed, not proof that an action changed the page."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {
                "type": "string",
                "enum": ["navigate", "read_text", "current", "snapshot", "screenshot", "scroll", "elements", "click", "fill", "press", "download_status", "connect", "list_tabs", "select_tab", "disconnect", "handoff", "status", "capabilities", "hover", "select_option", "set_checked", "upload", "run_actions"],
                "description": (
                    "navigate=open a public URL; read_text=extract current page text; current=page status; "
                    "snapshot=accessibility snapshot with element refs; screenshot=capture the visible viewport as a generated PNG; scroll=scroll current page; "
                    "elements=visible link/button/input candidates; click/fill/press=authorized controls; "
                    "download_status=query browser download progress and inbox material handle."
                ),
            },
            "session_source": {"type":"string", "enum":["managed","personal_chrome"], "description":"Default managed. Personal Chrome executes on the owner's bound device; it must be enabled and connected locally."},
            "browser_session_id": {"type":"string", "maxLength":160, "description":"Personal Chrome session token returned by connect; required after connect."},
            "device_epoch": {"type":"string", "maxLength":160, "description":"Personal Chrome device epoch returned by connect."},
            "tab_id": {"type":"string", "maxLength":160, "description":"Opaque ID from list_tabs; required for select_tab."},
            "url": {"type": "string", "maxLength": 1600, "description": "Public http(s) URL; required for navigate."},
            "cursor": {"type": "string", "description": "Opaque continuation cursor from a previous browser_page snapshot; pass only the cursor to read more of the SAME captured snapshot (never re-scrolls or re-clicks)."},
            "open_for_user": {"type": "boolean", "description": "Also open the page in the user's system browser."},
            "scroll_delta": {"type": "integer", "minimum": -2400, "maximum": 2400, "description": "Scroll pixels (positive = down) for scroll."},
            "element_limit": {"type": "integer", "minimum": 1, "maximum": 40, "description": "Max visible elements for elements."},
            "candidate_index": {"type": "integer", "minimum": 1, "maximum": 30, "description": "Numbered candidate from elements for click."},
            "ref": {"type": "string", "description": "Element ref (e.g. e3) from snapshot for click/fill/press."},
            "selector": {"type": "string", "maxLength": 220, "description": "CSS selector fallback for click/fill/press."},
            "coordinate": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Viewport CSS coordinates [x, y] (origin at top-left) for fallback click on canvas or un-semantic elements. Requires screenshot_id.",
            },
            "screenshot_id": {
                "type": "string",
                "maxLength": 80,
                "description": "Screenshot ID from the latest observation to bind visual coordinate clicks to a verified snapshot.",
            },
            "download_id": {
                "type": "string",
                "maxLength": 80,
                "description": "Download task ID for download_status action to query progress and material handle.",
            },
            "text": {"type": "string", "maxLength": 500, "description": "Exact text for fill; empty string clears the field. Personal Chrome currently accepts single-line text only."},
            "key": {
                "type": "string",
                "enum": ["Enter", "Escape", "Tab", "ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight", "PageDown", "PageUp", "Home", "End"],
                "description": "Key for press.",
            },
            "observation_id": {
                "type": "string",
                "maxLength": 80,
                "description": "Required for click/fill/press to bind the target to observed page state; coordinate clicks may bind with screenshot_id instead.",
            },
            "observation_mode": {
                "type": "string",
                "enum": ["hybrid", "text", "visual"],
                "description": "Observation mode: hybrid (AX + temporary screenshot, default for navigate/controls), text (AX/text only), or visual.",
            },
            **BROWSER_EXTRA_PROPERTIES,
        },
        "required": ["action"],
    },
    risk="medium",
    confirm="first_time",
    effects=("browser_action",),
    visible_in=("desktop", "qq"),
    spec_version="1.4.0",
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

SEND_FILE_TOOL_SPEC = CapabilityToolSpec(
    capability_id="send_file",
    display_name="Send file",
    description=(
        "把已经存在的工作台材料或生成文件真正交付给用户。支持 file_*、img_*、audio_*、gen_*。"
        "工具结果或上下文已经给出句柄时，必须传入那个精确句柄，不要改用 latest；"
        "latest 只适合确实不知道句柄、且用户笼统指向最近文件的情况。"
        "若只知道文件类型，可用 latest_generated 或 latest_attachment，避免在生成物和用户附件之间选错。"
        "只有实际文件路径时，传 path（可选 cwd）一次完成原样登记和发送；无需复制到当前工作区。"
        "相对路径以调用任务/项目目录为准，绝对路径省略 cwd，且必须属于已授权目录。"
        "文档、图片、音视频、压缩包及未知扩展名均可按文件交付；不保证预览、播放或转码。"
        "它不会生成、修改或转码文件；只有工具成功结果才能证明文件已经进入客户端投递队列。"
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "target": {
                "type": "string",
                "maxLength": 120,
                "description": (
                    "Single exact file handle (preferred), or latest/latest_generated/latest_attachment "
                    "only when no exact handle is known."
                ),
            },
            "targets": {
                "type": "array",
                "items": {"type": "string", "maxLength": 120},
                "maxItems": 10,
                "description": "One or more exact file handles. Reuse handles returned by prior tools.",
            },
            "path": {
                "type": "string", "minLength": 1, "maxLength": 4096,
                "description": "Existing file in an authorized project; register unchanged and queue for delivery. Use instead of target/targets only when no handle exists.",
            },
            "cwd": {
                "type": "string", "maxLength": 4096,
                "description": "Base directory for relative path; defaults to calling task/current project. Omit for absolute path. Never guess a plugin's process directory.",
            },
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
    spec_version="1.3.0",
    schema_version=4,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=4096,
)

SEND_STICKER_TOOL_SPEC = CapabilityToolSpec(
    capability_id="send_sticker",
    display_name="Send sticker",
    description="Send one available static sticker in the current QQ conversation.",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "sticker": {
                "type": "string",
                "maxLength": 80,
                "description": "Exact sticker id from the available sticker list in the current prompt.",
            },
        },
        "required": ["sticker"],
    },
    risk="low",
    confirm="never",
    effects=("sticker_delivery",),
    visible_in=("qq",),
    spec_version="2.0.0",
    schema_version=2,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=2048,
)

ONEBOT_ACTION_TOOL_SPEC = CapabilityToolSpec(
    capability_id="onebot_action",
    display_name="OneBot action",
    description=(
        "Call an explicitly exposed QQ/NapCat interaction through the current Bot. "
        "Use capabilities when exact parameters are unknown. Supports messages and message segments, "
        "history, forwards, pokes, emoji reactions, likes, member info and owner-authorized recall. "
        "Unambiguous current group, private peer, sender and current-message ids may be omitted. "
        "Ordinary participants are limited to the current conversation; cross-conversation actions and "
        "delete_msg require the configured owner. Credentials and account/group administration are not exposed. "
        "For actions that take message_id, message_selector can resolve the current message, its quoted message, "
        "or the Nth most recent successfully sent Bot message in this conversation. "
        "Pass the exact params object for the chosen action ({} for capabilities), and treat the returned "
        "ok/status/reason as authoritative instead of assuming delivery or success."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {
                "type": "string",
                "enum": list(MODEL_ONEBOT_ACTION_NAMES),
                "description": "One exposed action name; use capabilities to inspect parameter hints and policy.",
            },
            "params": {
                "type": "object",
                "additionalProperties": True,
                "description": "Exact OneBot request object for the chosen action; use {} for capabilities.",
            },
            "finish_turn": dict(FINISH_TURN_PARAMETER),
            "message_selector": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": list(MODEL_ONEBOT_MESSAGE_SELECTOR_KINDS),
                        "description": "Resolve a real message_id from the current QQ conversation.",
                    },
                    "position": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 64,
                        "description": "For recent_bot_message only; 1 is the latest successful Bot message.",
                    },
                },
                "required": ["kind"],
                "description": "Optional host-side message reference; omit when params.message_id is explicit.",
            },
        },
        "required": ["action", "params"],
    },
    risk="medium",
    confirm="never",
    effects=("qq_interaction",),
    visible_in=("qq",),
    spec_version="1.2.0",
    schema_version=2,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=512 * 1024,
)
SEND_MUSIC_CARD_TOOL_SPEC = CapabilityToolSpec(
    capability_id="send_music_card",
    display_name="Send music card",
    description=(
        "把网易云歌曲作为原生音乐卡片交付给当前 QQ 会话。"
        "track_id 是网易云纯数字歌曲 ID。"
        "track_id 必须是本工具结果、用户输入或已展开工具轨迹中真实出现的精确 ID，严禁根据歌名猜测或编造。"
        "本工具只尝试原生音乐卡片，并把 QQ 的真实传输结果直接返回模型；不会暗中改发语音。"
        "卡片失败后，模型可依据结果决定是否寻找公开音频 URL，再调用 send_audio。"
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "platform": {
                "type": "string",
                "enum": ["netease_music"],
                "description": "原生卡片平台；当前仅支持网易云。",
            },
            "track_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 64,
                "description": "精确的网易云纯数字歌曲 ID。",
            },
        },
        "required": ["platform", "track_id"],
    },
    risk="low",
    confirm="never",
    effects=("music_card_delivery",),
    visible_in=("qq",),
    spec_version="2.0.0",
    schema_version=5,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=4096,
)

SEND_AUDIO_TOOL_SPEC = CapabilityToolSpec(
    capability_id="send_audio",
    display_name="Send audio",
    description=(
        "把一段真实音频交付到当前 QQ 会话。source 可以是当前会话已有的 audio_*/file_*/gen_* 音频句柄，"
        "也可以是公开 HTTP(S) 音频直链；平台名称和歌曲 ID 不是语音交付参数。"
        "本工具只发送可直接播放的 QQ 语音；需要普通文件时另用 send_file，需要两种表现时可并行调用两项工具。"
        "工具会返回真实传输结果，"
        "失败不会导致回合终止，也不会被系统改成另一种交付方式。"
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "source": {
                "type": "string",
                "minLength": 1,
                "maxLength": 2048,
                "description": "精确音频句柄（如 audio_001/gen_001）或公开 HTTP(S) 音频 URL。",
            },
            "name": {
                "type": "string",
                "maxLength": 160,
                "description": "可选的人类可读音频名称；不用于定位本地文件。",
            },
        },
        "required": ["source"],
    },
    risk="low",
    confirm="never",
    effects=("audio_delivery",),
    visible_in=("qq",),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=4096,
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

# ── End M66-C canonical ToolSpecs ───────────────────────────────────────────


from .executor_broker import (
    ExecutionReceipt, BrokerExecutionResult, ServerLocalBrokerResult,
    CapabilityOfferSource, ExecutorBroker, _broker_request_fingerprint,
)


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
    has_pending_gift: bool = False
    # Host-frozen execution provider present (host config, not transient readiness).
    execution_enabled: bool = False
    # 当前 QQ 会话是否由主人显式开放执行（另受宿主 EXECUTION_QQ_ENABLED 总闸约束）。
    execution_qq_enabled: bool = False


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
    native_tool_aliases: Mapping[str, str] = field(default_factory=dict)
    layer_names: tuple[str, ...] = ()
    disclosures: tuple[CapabilityDisclosure, ...] = ()
    tool_specs: tuple[CapabilityToolSpec, ...] = ()
    execution_receipts: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    resolved_handlers: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)
    # None means normal host resolution; an empty set deliberately allows no
    # execution. Lazy MCP dispatch must preserve this host-owned ceiling.
    execution_allowlist: frozenset[str] | None = None
    # Presentation and published contracts never grant execution permission.
    capability_catalog: Any = field(default=None, compare=False, repr=False)
    exposure_modes: Mapping[str, str] = field(default_factory=dict)
    exposure_preferences: Mapping[str, Any] = field(default_factory=dict)
    published_contracts: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)


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


def _execution_enabled(snapshot: CapabilitySnapshot) -> bool:
    return snapshot.execution_enabled


def _execution_qq_enabled(snapshot: CapabilitySnapshot) -> bool:
    return snapshot.execution_qq_enabled


def _execution_available_in_mode(snapshot: CapabilitySnapshot) -> bool:
    """Host-frozen execution availability for the current client mode.

    QQ relies on the owner-opened Shell flag; desktop relies on the configured
    execution provider. Neither depends on transient provider readiness, so the
    capability schema stays byte-identical while readiness fluctuates.
    """
    if snapshot.client_mode == ClientMode.QQ_TEXT:
        return snapshot.execution_qq_enabled
    return snapshot.execution_enabled


def _has_image_context(snapshot: CapabilitySnapshot) -> bool:
    return snapshot.has_image_attachment or snapshot.has_image_generated_file or snapshot.has_image_workspace_file


def _has_generated_file(snapshot: CapabilitySnapshot) -> bool:
    return snapshot.has_generated_file


def _has_deliverable_file(snapshot: CapabilitySnapshot) -> bool:
    return snapshot.has_any_attachment or snapshot.has_generated_file


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

    Handlers are registered by tool_id. A handler may explicitly return an
    ``offered`` configuration fact when transient readiness must not control
    execution authority. Other handlers retain the enabled/ready contract.
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
            if isinstance(result.get("offered"), bool):
                return bool(result.get("offered"))
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
            if module.name == "media_workbench" and _execution_available_in_mode(snapshot):
                module_tools = tuple(
                    tool_name
                    for tool_name in module_tools
                    if tool_name not in MEDIA_WORKBENCH_SHELL_OVERLAP_TOOL_NAMES
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
                if "open_browser" not in seen_schema_tools:
                    schema_tools.append("open_browser")
                    seen_schema_tools.add("open_browser")
                tool_specs.append(OPEN_BROWSER_TOOL_SPEC)
                if receipt is not None:
                    if "desktop_browser_open" not in module_names:
                        module_names.append("desktop_browser_open")
                    if "desktop_browser" not in seen_layers:
                        layer_names.append("desktop_browser")
                        seen_layers.add("desktop_browser")
                    if hint not in seen_hints:
                        hints.append(hint)
                        seen_hints.add(hint)
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
                # The configured desktop capability profile owns the schema.
                # Executor readiness only controls whether a receipt can be
                # attached for this turn; reconnects must not rewrite the
                # provider prompt prefix and destroy its cache branch.
                if tool_name not in seen_schema_tools:
                    schema_tools.append(tool_name)
                    seen_schema_tools.add(tool_name)
                tool_specs.append(spec)
                if receipt is not None:
                    if spec.capability_id not in seen_layers:
                        layer_names.append(spec.capability_id)
                        seen_layers.add(spec.capability_id)
                    if spec.description not in seen_hints:
                        hints.append(spec.description)
                        seen_hints.add(spec.description)
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
                light_hint="记忆：retrieve_memory 查找旧事实、偏好与约定；browse_memory 浏览多日目录；read_memory_timeline 读取已知时段的原始时间线；open_memory 用返回的 memory_id 展开正文或来源。Skills：任务匹配目录说明时用 load_skill 加载手册。",
                trigger=_always,
            ),
            CapabilityModule(
                name="internet_access",
                layer="web",
                modes=COMMON_CLIENT_MODES,
                tools=WEB_SEARCH_TOOL_NAMES,
                light_hint="web_search 检索当前、实时或需要公开来源验证的信息，例如指数、新番目录和模型价格；它只访问公开互联网，私密、内网和登录资源会返回不可用。",
                trigger=_always,
                unavailable_reason="联网搜索服务当前正在检测，或没有通过所在网络节点的可用性检查。",
                recovery_hint="网络或搜索服务恢复后会自动重新开放；恢复前可基于已有稳定知识回答，并明确实时信息尚未核验。",
            ),
            CapabilityModule(
                name="execution",
                layer="execution",
                modes=(ClientMode.DESKTOP_PET,),
                tools=EXEC_TOOL_NAMES,
                light_hint=(
                    "执行宿主上的命令与项目工具：project_inspect、workspace_write、workspace_patch 与 exec_run 共用 cwd，"
                    "可在已发现的真实目录读取、修改、构建和测试；manage_project_workspace 的 create/open/select 设置当前项目，"
                    "之后四个工具省略 cwd 时会继续在该项目工作。"
                    "exec_status 查询长命令进度，exec_cancel 停止命令。"
                ),
                trigger=_execution_enabled,
                unavailable_reason="本机执行提供者当前没有通过可用性检查。",
                recovery_hint="执行工作区或提供者恢复后会自动重新开放；当前不要假装已经执行命令。",
            ),
            CapabilityModule(
                name="execution_qq",
                layer="execution",
                modes=(ClientMode.QQ_TEXT,),
                tools=EXEC_TOOL_NAMES,
                light_hint=(
                    "当前 QQ 会话已由主人开放 Shell；project_inspect、workspace_write、workspace_patch 与 exec_run 共用 cwd，"
                    "可在 QQ Bot 后端所在机器的已发现真实目录中读取、修改、构建和测试；manage_project_workspace 的 create/open/select 设置当前项目，"
                    "之后四个工具省略 cwd 时会继续在该项目工作。"
                    "exec_status 查询长命令进度，exec_cancel 停止命令；/access ops 查看当前操作权限。"
                ),
                trigger=_execution_qq_enabled,
                unavailable_reason="当前 QQ 会话的 Shell 没有开放，或执行提供者没有通过可用性检查。",
                recovery_hint="主人可在当前私聊或群聊发送 /access ops on；当前不要假装已经执行命令。",
            ),
            CapabilityModule(
                name="extension_management",
                layer="extension",
                modes=(ClientMode.DESKTOP_PET, ClientMode.QQ_TEXT),
                tools=EXTENSION_MANAGEMENT_TOOL_NAMES,
                light_hint="manage_extension 查看、暂存、安装、启停、回滚或移除本机插件；只有主人可以修改。",
                trigger=_always,
            ),
            CapabilityModule(
                name="desktop_managed_browser",
                layer="desktop_browser",
                modes=(ClientMode.DESKTOP_PET, ClientMode.QQ_TEXT),
                tools=DESKTOP_BROWSER_TOOL_NAMES,
                light_hint="browser_page 会打开并操作当前 Akane 宿主的可见托管浏览器窗口，用于读取、滚动、按可见候选序号打开链接，以及经授权的点击/输入；公开下载可通过 download_status 查询并进入材料工作台。个人已登录 Chrome 使用 session_source=personal_chrome，通过绑定电脑完成连接、list_tabs/select_tab；需要本机开启及 Chrome 许可。切换 computer_use 前 handoff，交接后重新观察。",
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
                light_hint="load_material 按 handle 把当前会话较早原图或生成图的像素送入视觉回合；inspect_attachment 用于先确认材料及其 handle。",
                trigger=_has_image_context,
                latent_reason="当前会话还没有可重新加载的图片材料。",
                activation_hint="用户上传图片或生成一张图片后，这项材料读取能力会自动开放。",
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
                name="qq_onebot_actions",
                layer="qq_delivery",
                modes=(ClientMode.QQ_TEXT,),
                tools=QQ_ONEBOT_ACTION_TOOL_NAMES,
                light_hint=(
                    "QQ 中需要读取聊天记录、发送消息段或合并转发、戳一戳、回应消息表情、点赞、"
                    "查询成员信息或撤回消息时用 onebot_action；不确定参数先查 capabilities。"
                    "当前群、当前私聊对象、当前发送者或当前消息可在语义明确时由宿主补全；"
                    "普通成员限当前会话，跨群/跨私聊与撤回需要主人，工具结果是真实 OneBot 回执。"
                ),
                trigger=_always,
            ),
            CapabilityModule(
                name="qq_native_music_card",
                layer="qq_delivery",
                modes=(ClientMode.QQ_TEXT,),
                tools=QQ_MUSIC_CARD_TOOL_NAMES,
                light_hint="在 QQ 会话里，你可以把已确认歌曲 ID 的网易云歌曲作为原生音乐卡片发送；工具会返回真实成败，不会自动改发语音。",
                trigger=_always,
            ),
            CapabilityModule(
                name="qq_audio_delivery",
                layer="qq_delivery",
                modes=(ClientMode.QQ_TEXT,),
                tools=QQ_AUDIO_DELIVERY_TOOL_NAMES,
                light_hint="在 QQ 会话里，你可以把公开音频直链或已有音频句柄发送为 QQ 语音；普通文件仍使用 send_file。",
                trigger=_always,
            ),
            CapabilityModule(
                name="document_workbench",
                layer="shared_document",
                modes=CHAT_FILE_CLIENT_MODES,
                tools=DOCUMENT_WORKBENCH_TOOL_NAMES,
                light_hint="你可以分段读取当前会话中的文本、Office、PDF 等文档材料。",
                trigger=_has_document_context,
                latent_reason="当前会话和可见工作区里还没有可处理的文档材料，因此没有展开文档读取工具。",
                activation_hint="用户上传文档，或在桌宠的 Akane 工作区放入文档后会自动开放；若工作区文件尚无 handle，先登记再继续处理。",
                unavailable_reason="文档处理组件当前没有通过可用性检查。",
                recovery_hint="文档组件恢复后会自动重新开放；已有材料无需重复上传。",
            ),
            CapabilityModule(
                name="media_workbench",
                layer="shared_media",
                modes=CHAT_FILE_CLIENT_MODES,
                tools=MEDIA_WORKBENCH_TOOL_NAMES,
                light_hint="你可以使用当前可用的媒体工具处理素材；处理工具返回成果句柄后，再按用户要求调用 send_file 交付。",
                trigger=_has_media_context,
                latent_reason="当前会话和可见工作区里还没有可处理的音频或视频，因此没有展开媒体处理工具。",
                activation_hint="用户上传音频/视频、提供可下载的公开媒体链接，或在桌宠的 Akane 工作区放入媒体文件后会自动开放；工作区文件可先登记为 handle。",
                unavailable_reason="媒体处理所需的本地组件当前没有通过可用性检查。",
                recovery_hint="媒体组件恢复后会自动重新开放；已有材料无需重复上传。",
            ),
            CapabilityModule(
                name="generated_file_management",
                layer="shared_file_authoring",
                modes=CHAT_FILE_CLIENT_MODES,
                tools=GENERATED_FILE_MANAGEMENT_TOOL_NAMES,
                light_hint="你可以回看、交付、归档、删除或清理自己刚生成的文件。",
                trigger=_has_generated_file,
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
