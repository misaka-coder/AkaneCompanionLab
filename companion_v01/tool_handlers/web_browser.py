"""Stable public constructors for browser and web search tools.

Implementation responsibilities are composed in web_browser_runtime.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

import config

from ..anysearch_rest_client import AnySearchRestClient
from ..browser_page_runtime import ManagedBrowserPageRunner, ManagedBrowserSessionManager
from ..browser_page_contract import CONTROL_ACTIONS, MANAGED_ACTIONS
from ..capability_registry import OPEN_BROWSER_TOOL_SPEC, WEB_SEARCH_TOOL_SPEC
from ..mcp_stdio_discoverer import McpToolCaller
from .core import BaseToolHandler
from .web_browser_runtime import (
    BrowserCommonMixin,
    BrowserPageRuntimeMixin,
    MusicSearchMixin,
    OpenBrowserMixin,
    WebSearchRuntimeMixin,
)
# Preserve the former module-level helper import for compatibility.
from .web_browser_runtime.search_transport import _mcp_server_has_transport_config

class OpenBrowserToolHandler(BrowserCommonMixin, OpenBrowserMixin, BaseToolHandler):
    tool_type = "open_browser"
    policy_accepted_native_tool = True

    def __init__(self, *, offer_source: Any = None) -> None:
        self._offer_source = offer_source

    def tool_spec(self):
        return OPEN_BROWSER_TOOL_SPEC


class OpenMusicSearchToolHandler(MusicSearchMixin, BaseToolHandler):
    tool_type = "open_music_search"

    def __init__(self, *, offer_source: Any = None, executor_broker: Any = None) -> None:
        from ..executor_broker import ExecutorBroker

        self._offer_source = offer_source
        self.executor_broker = executor_broker or ExecutorBroker(offer_source)

    PLATFORM_URLS: dict[str, tuple[str, str]] = {
        "qq_music": ("QQ 音乐", "https://y.qq.com/n/ryqq/search?w={query}"),
        "netease_music": ("网易云音乐", "https://music.163.com/#/search/m/?s={query}&type=1"),
        "bilibili": ("哔哩哔哩", "https://search.bilibili.com/all?keyword={query}"),
        "youtube": ("YouTube", "https://www.youtube.com/results?search_query={query}"),
    }

    PLATFORM_ALIASES: dict[str, str] = {
        "qq": "qq_music",
        "qqmusic": "qq_music",
        "qq_music": "qq_music",
        "yqq": "qq_music",
        "qq音乐": "qq_music",
        "qq 音乐": "qq_music",
        "netease": "netease_music",
        "netease_music": "netease_music",
        "163": "netease_music",
        "网易": "netease_music",
        "网易云": "netease_music",
        "网易云音乐": "netease_music",
        "b站": "bilibili",
        "bili": "bilibili",
        "bilibili": "bilibili",
        "哔哩哔哩": "bilibili",
        "youtube": "youtube",
        "yt": "youtube",
        "油管": "youtube",
    }

    SECRET_MARKERS = ("api_key", "apikey", "authorization", "bearer", "cookie", "password", "secret", "token")


class BrowserPageToolHandler(BrowserPageRuntimeMixin, BrowserCommonMixin, BaseToolHandler):
    tool_type = "browser_page"

    ALLOWED_ACTIONS = MANAGED_ACTIONS
    CONTROL_ACTIONS = CONTROL_ACTIONS | {"run_actions"}
    CONTROL_ACTION_ID_PREFIX = "browser_page"
    ALLOWED_PRESS_KEYS = {
        "Enter",
        "Escape",
        "Tab",
        "ArrowDown",
        "ArrowUp",
        "ArrowLeft",
        "ArrowRight",
        "PageDown",
        "PageUp",
        "Home",
        "End",
    }
    MAX_ELEMENT_LIMIT = 40
    MAX_SELECTOR_CHARS = 220
    MAX_FILL_TEXT_CHARS = 500

    def __init__(
        self,
        *,
        browser_runner: Any = None,
        generated_file_service: Any = None,
        attachment_service: Any = None,
        image_material_resolver: Any = None,
        allow_private_network_urls: bool | None = None,
        config_base_dir: Path | str | None = None,
        approval_checker: Callable[..., bool] | None = None,
        personal_offer_source: Any = None,
        executor_broker: Any = None,
        approval_store: Any = None,
    ) -> None:
        self.allow_private_network_urls = (
            bool(getattr(config, "BROWSER_PAGE_PRIVATE_NETWORK_ACCESS", False))
            if allow_private_network_urls is None
            else bool(allow_private_network_urls)
        )
        if browser_runner is None:
            headless = bool(getattr(config, "BROWSER_PAGE_HEADLESS", False))
            self.browser_runner = ManagedBrowserSessionManager(
                background_probe=True,
                runner_factory=lambda: ManagedBrowserPageRunner(
                    headless=headless,
                    attachment_service=attachment_service,
                    allow_private_network_urls=self.allow_private_network_urls,
                ),
            )
        else:
            self.browser_runner = browser_runner
        self.generated_file_service = generated_file_service
        self.attachment_service = attachment_service
        self.image_material_resolver = image_material_resolver
        self.config_base_dir = config_base_dir if config_base_dir is not None else getattr(config, "DATA_DIR", None)
        self.approval_checker = approval_checker
        from ..personal_browser import PersonalBrowserBackend
        self.personal_browser = PersonalBrowserBackend(offer_source=personal_offer_source, broker=executor_broker,
            approval_store=approval_store, config_base_dir=self.config_base_dir)


class WebSearchToolHandler(WebSearchRuntimeMixin, BrowserCommonMixin, BaseToolHandler):
    tool_type = "web_search"

    ALLOWED_ACTIONS = {"search", "batch_search", "extract", "get_sub_domains"}
    MAX_QUERY_LENGTH = 240
    MAX_QUERIES = 4
    MAX_RESULTS = 10

    def tool_spec(self):  # M66-B: canonical ToolSpec authority
        return WEB_SEARCH_TOOL_SPEC

    def __init__(
        self,
        *,
        config_base_dir: Path | str | None = None,
        server_id: str = "anysearch",
        mcp_tool_caller: Any = None,
        readiness_mcp_tool_caller: Any = None,
        anysearch_rest_client: Any = None,
        readiness_ready_ttl_seconds: float = 30 * 60,
        readiness_failure_ttl_seconds: float = 60.0,
        readiness_probe_in_background: bool = True,
        readiness_clock=time.monotonic,
    ) -> None:
        self.config_base_dir = config_base_dir if config_base_dir is not None else getattr(config, "DATA_DIR", None)
        self.server_id = str(server_id or "anysearch").strip() or "anysearch"
        mcp_timeout_seconds = float(getattr(config, "WEB_SEARCH_MCP_TIMEOUT_SECONDS", 35.0) or 35.0)
        self.mcp_tool_caller = mcp_tool_caller or McpToolCaller(
            timeout_seconds=mcp_timeout_seconds
        )
        self.readiness_mcp_tool_caller = readiness_mcp_tool_caller or self.mcp_tool_caller
        self.anysearch_rest_client = anysearch_rest_client or AnySearchRestClient(
            timeout_seconds=mcp_timeout_seconds
        )
        self._readiness_ready_ttl_seconds = max(30.0, float(readiness_ready_ttl_seconds))
        self._readiness_failure_ttl_seconds = max(5.0, float(readiness_failure_ttl_seconds))
        self._readiness_probe_in_background = bool(readiness_probe_in_background)
        self._readiness_clock = readiness_clock
        self._readiness_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
        self._readiness_probes_inflight: set[tuple[str, str]] = set()
        self._readiness_lock = threading.RLock()
