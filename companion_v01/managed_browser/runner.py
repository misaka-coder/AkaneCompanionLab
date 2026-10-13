"""Public runner composition and per-session mutable state.

Mixin owners share this instance; only this class initializes browser objects,
threading primitives, revision counters, and snapshot/observation/download stores.
"""
from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from ..browser_page_download import BrowserDownloadRecord, BrowserDownloadTracker
from ..browser_page_observation import (
    DEFAULT_MAX_SCREENSHOT_BYTES, DEFAULT_MAX_TOTAL_SCREENSHOT_BYTES,
    DEFAULT_OBSERVATION_MAX_RECORDS, ObservationRecord, ObservationStore,
)
from .models import (
    BrowserPageResult, SNAPSHOT_CAPTURE_MAX_CHARS, SNAPSHOT_TTL_SECONDS,
    SNAPSHOT_MAX_ENTRIES, SNAPSHOT_MAX_TOTAL_BYTES,
)
from .lifecycle import BrowserLifecycleMixin
from .reading import BrowserReadingMixin
from .candidates import BrowserCandidatesMixin
from .actions import BrowserActionsMixin
from .execution import BrowserExecutionMixin
from .observing import BrowserObservingMixin


class ManagedBrowserPageRunner(
    BrowserLifecycleMixin,
    BrowserReadingMixin,
    BrowserCandidatesMixin,
    BrowserActionsMixin,
    BrowserExecutionMixin,
    BrowserObservingMixin,
):
    """Small optional Playwright runner for Akane-owned browser page actions.

    Playwright is intentionally optional here. The capability can be catalogued
    and tested without pulling in browser binaries; real execution becomes
    available when the local runtime has Playwright installed. The default is a
    visible Akane-managed browser window so users can see browser actions.

    Every page-observing action captures an immutable snapshot into a bounded
    in-memory cache (TTL + entry count + total bytes).  Continuation cursors
    read pages from that same captured snapshot: they never re-scroll, never
    re-click, and never treat a live-changed page as the tail of an old one.
    """

    def __init__(
        self,
        *,
        headless: bool = False,
        timeout_ms: int = 15000,
        browser_channel: str | None = None,
        allow_private_network_urls: bool = False,
        snapshot_ttl_seconds: float = SNAPSHOT_TTL_SECONDS,
        snapshot_max_entries: int = SNAPSHOT_MAX_ENTRIES,
        snapshot_max_total_bytes: int = SNAPSHOT_MAX_TOTAL_BYTES,
        snapshot_capture_max_chars: int = SNAPSHOT_CAPTURE_MAX_CHARS,
        attachment_service: Any = None,
        download_dir: Path | str | None = None,
        observation_max_screenshot_bytes: int = DEFAULT_MAX_SCREENSHOT_BYTES,
        observation_max_total_screenshot_bytes: int = DEFAULT_MAX_TOTAL_SCREENSHOT_BYTES,
        now: Any = None,
    ) -> None:
        self.headless = bool(headless)
        self.timeout_ms = max(3000, min(60000, int(timeout_ms or 15000)))
        self.browser_channel = self._default_browser_channel() if browser_channel is None else str(browser_channel or "").strip()
        self.allow_private_network_urls = bool(allow_private_network_urls)
        self._executor: ThreadPoolExecutor | None = None
        self._shutdown_started = threading.Event()
        self._lock = threading.Lock()
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._context: Any | None = None
        self._page: Any | None = None
        self._now = now or time.time
        self._snapshot_ttl_seconds = max(1.0, float(snapshot_ttl_seconds))
        self._snapshot_max_entries = max(1, int(snapshot_max_entries))
        self._snapshot_max_total_bytes = max(1024, int(snapshot_max_total_bytes))
        self._snapshot_capture_max_chars = max(1000, int(snapshot_capture_max_chars))
        self._snapshot_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._snapshot_lock = threading.Lock()
        self._page_revision = 0
        self._browser_generation = 1
        self._document_generation = 1
        self._page_id = "page_" + uuid.uuid4().hex[:8]
        self._profile_user_id = "default_user"
        self._session_id = "default_session"
        self._observation_store = ObservationStore(
            max_records=max(DEFAULT_OBSERVATION_MAX_RECORDS, self._snapshot_max_entries * 2),
            ttl_seconds=self._snapshot_ttl_seconds,
            max_screenshot_bytes=observation_max_screenshot_bytes,
            max_total_screenshot_bytes=observation_max_total_screenshot_bytes,
            now=self._now,
        )
        self._download_tracker = BrowserDownloadTracker(
            attachment_service=attachment_service,
            download_dir=download_dir,
            now=self._now,
        )
        self._dialog_events: list[dict[str, Any]] = []
        self._dialog_policy: dict[str, Any] | None = None
        self._action_state = "not_started"
        self._capability_status_cache: dict[str, Any] | None = None

    def set_attachment_service(self, service: Any) -> None:
        self._download_tracker.set_attachment_service(service)

    def set_session_identity(self, profile_user_id: str, session_id: str) -> None:
        self._profile_user_id = str(profile_user_id or "default_user").strip() or "default_user"
        self._session_id = str(session_id or "default_session").strip() or "default_session"

    def get_download(self, download_id: str) -> BrowserDownloadRecord | None:
        return self._download_tracker.get(download_id)

    def latest_download(self) -> BrowserDownloadRecord | None:
        return self._download_tracker.latest()

    def get_observation(self, observation_id: str) -> ObservationRecord | None:
        return self._observation_store.get(observation_id)

    def latest_observation(self) -> ObservationRecord | None:
        return self._observation_store.latest()

    def has_live_page(self) -> bool:
        page = self._page
        if page is None:
            return False
        try:
            return not page.is_closed()
        except Exception:
            return False

    def page_revision(self) -> str:
        return f"r{max(0, int(self._page_revision or 0))}"

    def _advance_page_revision(self) -> str:
        with self._lock:
            self._page_revision += 1
            return self.page_revision()

    def run(
        self,
        *,
        action: str,
        url: str = "",
        max_chars: int = 3000,
        scroll_delta: int = 800,
        element_limit: int = 20,
        selector: str = "",
        ref: str = "",
        text: str = "",
        key: str = "",
        candidate_index: int = 0,
        screenshot_path: str = "",
        observation_id: str = "",
        observation_mode: str = "",
        coordinate: tuple[int, int] | list[int] | None = None,
        screenshot_id: str = "",
        download_id: str = "",
        options: dict[str, Any] | None = None,
    ) -> BrowserPageResult:
        normalized_action = str(action or "").strip() or "current"
        if not self.is_available():
            return BrowserPageResult(
                ok=False,
                status="unavailable",
                action=normalized_action,
                reason="playwright_not_installed",
            )
        if normalized_action == "run_actions":
            stopped = threading.Event()
            options = {**(options or {}), "stop_event": stopped}
            try:
                return self._submit(lambda: self._run_batch(options, observation_id=observation_id,
                    observation_mode=observation_mode), timeout_seconds=6 * (self.timeout_ms / 1000 + 10) + 10)
            except TimeoutError:
                stopped.set()
                raise
        return self._submit(
            lambda: self._run_in_browser(
                action=normalized_action,
                url=url,
                max_chars=max_chars,
                scroll_delta=scroll_delta,
                element_limit=element_limit,
                selector=selector,
                ref=ref,
                text=text,
                key=key,
                candidate_index=candidate_index,
                screenshot_path=screenshot_path,
                observation_id=observation_id,
                observation_mode=observation_mode,
                coordinate=coordinate,
                screenshot_id=screenshot_id,
                download_id=download_id,
                options=options,
            )
        )
