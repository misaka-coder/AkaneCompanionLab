"""Public search, managed-page interaction and desktop open-page contracts."""
from __future__ import annotations

from capcore import CapabilityToolSpec
from ..browser_page_contract import EXTRA_PROPERTIES as BROWSER_EXTRA_PROPERTIES


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
