from __future__ import annotations

import importlib.util
import ipaddress
import re
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

from .browser_page_download import (
    DEFAULT_MAX_DOWNLOAD_BYTES,
    BrowserDownloadRecord,
    BrowserDownloadTracker,
)
from .browser_page_observation import (
    DEFAULT_MAX_SCREENSHOT_BYTES,
    DEFAULT_MAX_TOTAL_SCREENSHOT_BYTES,
    DEFAULT_OBSERVATION_MAX_RECORDS,
    DEFAULT_OBSERVATION_TTL_SECONDS,
    ObservationRecord,
    ObservationStore,
    generate_observation_id,
    generate_screenshot_id,
    png_dimensions,
    read_live_page_state,
    coordinate_pixels_match,
)

from .browser_page_contract import CONTROL_ACTIONS, managed_capabilities

SNAPSHOT_CAPTURE_MAX_CHARS = 250_000
SNAPSHOT_TTL_SECONDS = 30 * 60
SNAPSHOT_MAX_ENTRIES = 16
SNAPSHOT_MAX_TOTAL_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class BrowserPageResult:
    ok: bool
    status: str
    action: str
    url: str = ""
    title: str = ""
    text: str = ""
    reason: str = ""
    snapshot_id: str = ""
    complete: bool = True
    next_cursor: str = ""
    shown_chars: int = 0
    total_chars: int = 0
    page_revision: str = ""
    retryable: bool = False
    next_action: str = ""
    observation_id: str = ""
    screenshot_id: str = ""
    action_state: str = "not_started"
    observation_state: str = "complete"
    page_changed: str = "unknown"
    visual_status: str = "not_requested"
    model_image: dict[str, Any] | None = None
    viewport: tuple[int, int] = (0, 0)
    screenshot_dimensions: tuple[int, int] = (0, 0)
    download_id: str = ""
    download_status: str = ""
    download_filename: str = ""
    attachment_handle: str = ""
    attachment_id: str = ""
    dialog_events: tuple[dict[str, Any], ...] = ()
    step_results: tuple[dict[str, Any], ...] = ()


class ManagedBrowserPageRunner:
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

    def capability_status(self) -> dict[str, Any]:
        cached = self._capability_status_cache
        if cached is not None:
            return dict(cached)
        if not self.is_available():
            status = {
                "enabled": False,
                "status": "missing_executor",
                "reason": "playwright_not_installed",
            }
            self._capability_status_cache = status
            return dict(status)
        # Importability alone is not a usable browser. A Playwright package can
        # exist while its browser binary or Linux shared libraries are absent.
        # Probe one invisible launch once per host process so the capability
        # catalog never advertises a tool that will fail on its first action.
        probe = ThreadPoolExecutor(max_workers=1, thread_name_prefix="akane-browser-probe")
        try:
            # Capability catalogs are built from async HTTP handlers. Running
            # Playwright's sync API on that event-loop thread is rejected even
            # though real browser actions (already worker-threaded) work. Probe
            # in the same kind of dedicated thread used by normal actions.
            status = probe.submit(self._probe_browser_runtime).result(timeout=20)
        except Exception:
            status = {
                "enabled": False,
                "status": "missing_executor",
                "reason": "playwright_browser_unavailable",
            }
        finally:
            probe.shutdown(wait=False, cancel_futures=True)
        self._capability_status_cache = status
        return dict(status)

    def _probe_browser_runtime(self) -> dict[str, Any]:
        browser = None
        playwright = None
        try:
            from playwright.sync_api import sync_playwright

            playwright = sync_playwright().start()
            launch_kwargs: dict[str, Any] = {"headless": True}
            if self.browser_channel:
                try:
                    browser = playwright.chromium.launch(channel=self.browser_channel, **launch_kwargs)
                except Exception:
                    browser = playwright.chromium.launch(**launch_kwargs)
            else:
                browser = playwright.chromium.launch(**launch_kwargs)
            return {"enabled": True, "status": "ready", "reason": ""}
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
            if playwright is not None:
                try:
                    playwright.stop()
                except Exception:
                    pass

    def is_available(self) -> bool:
        return importlib.util.find_spec("playwright") is not None

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

    def store_snapshot(
        self,
        *,
        kind: str,
        text: str,
        url: str = "",
        title: str = "",
    ) -> dict[str, Any]:
        """Capture one immutable snapshot into the bounded cache.

        Returns the cache record (``snapshot_id`` etc.) so the caller can page
        it.  Old snapshots stay readable until TTL/eviction: a cursor always
        reads the same captured bytes, never a re-captured page.
        """
        snapshot_id = "snap_" + uuid.uuid4().hex[:16]
        record = {
            "snapshot_id": snapshot_id,
            "kind": str(kind or "page"),
            "text": str(text or "")[: self._snapshot_capture_max_chars],
            "url": str(url or "")[:800],
            "title": str(title or "")[:200],
            "revision": self.page_revision(),
            "created_at": float(self._now()),
        }
        with self._snapshot_lock:
            self._snapshot_cache[snapshot_id] = (record["created_at"], record)
            self._trim_snapshot_cache_locked()
        return dict(record)

    def read_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        """Read a cached immutable snapshot; ``None`` when missing or expired."""
        clean_id = str(snapshot_id or "").strip()
        if not clean_id:
            return None
        now = float(self._now())
        with self._snapshot_lock:
            cached = self._snapshot_cache.get(clean_id)
            if cached is None:
                return None
            created_at, record = cached
            if now - created_at > self._snapshot_ttl_seconds:
                del self._snapshot_cache[clean_id]
                return None
            return dict(record)

    def _trim_snapshot_cache_locked(self) -> None:
        now = float(self._now())
        expired = [
            snapshot_id
            for snapshot_id, (created_at, _record) in self._snapshot_cache.items()
            if now - created_at > self._snapshot_ttl_seconds
        ]
        for snapshot_id in expired:
            del self._snapshot_cache[snapshot_id]
        while len(self._snapshot_cache) > self._snapshot_max_entries:
            oldest = min(
                self._snapshot_cache.items(),
                key=lambda item: item[1][0],
            )[0]
            del self._snapshot_cache[oldest]
        total_bytes = sum(
            len((record.get("text") or "").encode("utf-8"))
            for _created_at, record in self._snapshot_cache.values()
        )
        while total_bytes > self._snapshot_max_total_bytes and len(self._snapshot_cache) > 1:
            oldest = min(
                self._snapshot_cache.items(),
                key=lambda item: item[1][0],
            )[0]
            del self._snapshot_cache[oldest]
            total_bytes = sum(len((record.get("text") or "").encode("utf-8")) for _created_at, record in self._snapshot_cache.values())

    def shutdown(self, *, timeout: float = 5.0) -> None:
        with self._lock:
            if self._shutdown_started.is_set():
                return
            self._shutdown_started.set()
            executor = self._executor
        if executor is None:
            self._close_objects()
            return
        try:
            executor.submit(self._close_objects).result(timeout=timeout)
        except Exception:
            # Browser shutdown is best-effort. A slow Edge/Chromium teardown
            # must not turn an already successful browser task into a caller-
            # visible failure or block host shutdown indefinitely.
            pass
        finally:
            # Keep the queued close on the Playwright owner thread even after
            # the bounded wait expires. Repeated shutdown must never move it
            # to the caller thread or cancel the only pending cleanup.
            executor.shutdown(wait=False, cancel_futures=False)

    def _submit(self, task: Callable[[], BrowserPageResult], *, timeout_seconds: float | None = None) -> BrowserPageResult:
        with self._lock:
            if self._shutdown_started.is_set():
                raise RuntimeError("browser_runner_shutdown")
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="akane-browser-page")
            executor = self._executor
        return executor.submit(task).result(timeout=timeout_seconds or max(8, self.timeout_ms / 1000 + 5))

    def _run_in_browser(
        self,
        *,
        action: str,
        url: str,
        max_chars: int,
        scroll_delta: int,
        element_limit: int,
        selector: str,
        ref: str,
        text: str,
        key: str,
        candidate_index: int,
        screenshot_path: str,
        observation_id: str = "",
        observation_mode: str = "",
        coordinate: tuple[int, int] | list[int] | None = None,
        screenshot_id: str = "",
        download_id: str = "",
        options: dict[str, Any] | None = None,
    ) -> BrowserPageResult:
        if action == "run_actions":
            return self._run_batch(options or {}, observation_id=observation_id, observation_mode=observation_mode)
        safe_to_retry = action in {"navigate", "read_text", "current", "snapshot", "screenshot", "elements", "download_status"}
        for attempt in range(2):
            self._action_state = "not_started"
            try:
                return self._run_in_browser_once(
                    action=action,
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
            except Exception as exc:
                status, reason, retryable, next_action = self._classify_browser_error(exc, action=action)
                if attempt == 0 and safe_to_retry and status == "browser_closed" and self._action_state == "not_started":
                    self._discard_browser_objects()
                    continue
                return BrowserPageResult(
                    ok=False,
                    status=status,
                    action=action,
                    reason=reason,
                    retryable=retryable,
                    next_action=next_action,
                    action_state=self._action_state,
                    observation_state="failed",
                )
        return BrowserPageResult(
            ok=False,
            status="unavailable",
            action=action,
            reason="browser_page_runner_failed",
            retryable=True,
            next_action="navigate",
        )

    def _run_batch(self, options: dict[str, Any], *, observation_id: str, observation_mode: str) -> BrowserPageResult:
        steps = options.get("actions", [])
        if not isinstance(steps, list) or not 1 <= len(steps) <= 6:
            return BrowserPageResult(ok=False, status="invalid_request", action="run_actions", reason="invalid_batch")
        receipts = []
        result = None
        for index, step in enumerate(steps):
            stop_event = options.get("stop_event")
            if self._shutdown_started.is_set() or (stop_event is not None and stop_event.is_set()):
                result = BrowserPageResult(ok=False, status="partial", action="run_actions", reason="batch_wait_expired", next_action="snapshot")
                break
            generation = self._document_generation
            page_id = self._page_id
            before = self._observation_store.get(observation_id)
            frame_document = (before.live_state.get("frame", {}).get("document") if before else None)
            result = self._run_in_browser(action=step["action"], url="", max_chars=50000, scroll_delta=800,
                element_limit=20, selector=step.get("selector", ""), ref="", text=step.get("text", ""),
                key=step.get("key", ""), candidate_index=0, screenshot_path="",
                observation_id=observation_id, observation_mode=observation_mode or "text",
                options={k: v for k, v in step.items() if k not in {"action", "selector", "text", "key"}})
            receipts.append({"index": index, "action": step["action"], "ok": result.ok,
                "action_state": result.action_state, "observation_state": result.observation_state,
                "reason": result.reason, "observation_id": result.observation_id})
            observation_id = result.observation_id
            after = self._observation_store.get(observation_id)
            new_frame_document = (after.live_state.get("frame", {}).get("document") if after else None)
            if not result.ok:
                break
            if index < len(steps) - 1 and (result.dialog_events or generation != self._document_generation
                    or page_id != self._page_id or frame_document != new_frame_document
                    or result.observation_state != "complete"):
                result = replace(result, ok=False, status="partial", reason="batch_stopped_for_new_observation", next_action="snapshot")
                break
        state = result.action_state
        if state == "not_started" and any(r["action_state"] == "executed" for r in receipts):
            state = "executed"
        return replace(result, action="run_actions", action_state=state, step_results=tuple(receipts))

    def _run_in_browser_once(
        self,
        *,
        action: str,
        url: str,
        max_chars: int,
        scroll_delta: int,
        element_limit: int,
        selector: str,
        ref: str,
        text: str,
        key: str,
        candidate_index: int,
        screenshot_path: str,
        observation_id: str = "",
        observation_mode: str = "",
        coordinate: tuple[int, int] | list[int] | None = None,
        screenshot_id: str = "",
        download_id: str = "",
        options: dict[str, Any] | None = None,
    ) -> BrowserPageResult:
        options = options or {}
        if action == "capabilities":
            import json
            return BrowserPageResult(ok=True, status="available", action=action, text=json.dumps(managed_capabilities()))
        if action == "download_status":
            # Playwright dispatches context events while its owning thread is
            # making a browser call. Give a pending download callback one
            # short event turn before reading the tracker.
            if self._page is not None:
                try:
                    self._page.wait_for_timeout(50)
                except Exception:
                    pass
            target_dl_id = str(download_id or "").strip()
            record = self._download_tracker.get(target_dl_id) if target_dl_id else self._download_tracker.latest()
            if record is None:
                return BrowserPageResult(
                    ok=False,
                    status="not_found",
                    action=action,
                    reason="download_not_found",
                    download_id=target_dl_id,
                )
            status_text = (
                f"下载任务标识: {record.download_id}\n"
                f"建议文件名: {record.suggested_filename}\n"
                f"下载状态: {record.status}\n"
                f"文件大小: {record.file_size} 字节\n"
            )
            if record.status == "completed":
                if record.attachment_handle:
                    status_text += f"工作台材料句柄: {record.attachment_handle}\n"
                if record.storage_relpath:
                    status_text += f"存储相对路径: {record.storage_relpath}\n"
            elif record.status in {"failed", "cancelled"}:
                status_text += f"失败/取消原因: {record.error_reason}\n"
            return BrowserPageResult(
                ok=record.status in {"completed", "pending"},
                status=record.status,
                action=action,
                text=status_text,
                download_id=record.download_id,
                download_status=record.status,
                download_filename=record.suggested_filename,
                attachment_handle=record.attachment_handle,
                attachment_id=record.attachment_id,
                action_state="executed",
                observation_state="complete",
            )

        self._dialog_policy = options.get("dialog")
        try:
            page = self._ensure_page()
            self._bring_to_front(page)

            if action in CONTROL_ACTIONS and not observation_id and coordinate is None:
                return BrowserPageResult(
                    ok=False, status="stale_target", action=action,
                    reason="observation_id_required_for_control", retryable=True, next_action="snapshot",
                )

            bound_observation = self._observation_store.get(observation_id) if observation_id else None
            if coordinate is not None and screenshot_id:
                bound_observation = self._observation_store.get_by_screenshot_id(screenshot_id)
            if (bound_observation is not None and action in CONTROL_ACTIONS
                    and bound_observation.browser_generation == self._browser_generation
                    and bound_observation.document_generation == self._document_generation
                    and bound_observation.page_revision == self._page_revision):
                try:
                    live_state = self._scoped_live_state(page, options)
                except Exception:
                    live_state = None
                if not bound_observation.live_state or live_state != bound_observation.live_state:
                    return BrowserPageResult(
                        ok=False, status="stale_target", action=action,
                        reason="page_changed_since_observation", retryable=True,
                        next_action="snapshot", observation_state="failed",
                    )

            # Validate freshness of observation target if caller bound this control action
            if observation_id and action in CONTROL_ACTIONS:
                valid, stale_reason = self._observation_store.validate_action_target(
                    observation_id=observation_id,
                    current_browser_gen=self._browser_generation,
                    current_doc_gen=self._document_generation,
                    current_page_id=self._page_id,
                    current_page_revision=self._page_revision,
                )
                if not valid:
                    return BrowserPageResult(
                        ok=False,
                        status="stale_target",
                        action=action,
                        reason=stale_reason,
                        retryable=True,
                        next_action="snapshot",
                        observation_id=observation_id,
                        action_state="not_started",
                        observation_state="failed",
                        page_changed="no",
                    )

            action_state = "not_started"
            page_changed = "no"
            dl_before = self._download_tracker.latest()
            self._dialog_events[:] = self._dialog_events[-20:]
            dialog_start = len(self._dialog_events)

            if action in {"navigate", "read_text"} and url:
                self._perform_action(lambda: page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms))
                self._bring_to_front(page)
                self._document_generation += 1
                self._advance_page_revision()
                action_state = "executed"
                page_changed = "yes"
            elif action in {"navigate"} and not url:
                return BrowserPageResult(
                    ok=False,
                    status="invalid_request",
                    action=action,
                    reason="url_required",
                    action_state="not_started",
                )

            current_url = str(getattr(page, "url", "") or "")
            if not current_url or current_url == "about:blank":
                return BrowserPageResult(
                    ok=False,
                    status="no_page",
                    action=action,
                    reason="browser_page_empty",
                    retryable=True,
                    next_action="navigate",
                    action_state=action_state,
                )

            if action == "scroll":
                self._perform_action(lambda: page.mouse.wheel(0, self._safe_scroll_delta(scroll_delta)))
                self._brief_visual_pause(page)
                self._advance_page_revision()
                action_state = "executed"
                page_changed = "yes"
            elif action in CONTROL_ACTIONS:
                url_before = current_url
                page_before = page
                document_generation_before_action = self._document_generation
                control_result = self._run_control_action(
                    page,
                    action=action,
                    selector=selector,
                    ref=ref,
                    text=text,
                    key=key,
                    candidate_index=candidate_index,
                    coordinate=coordinate,
                    screenshot_id=screenshot_id,
                    observation_id=observation_id,
                    options=options,
                )
                if control_result is not None:
                    return control_result
                if self._page is not None:
                    page = self._page
                current_url = str(getattr(page, "url", "") or current_url)
                page_switched = page is not page_before
                if current_url != url_before or page_switched:
                    if self._document_generation == document_generation_before_action:
                        self._document_generation += 1
                    page_changed = "yes"
                else:
                    # A control may have changed focus, an input value, or an
                    # in-page widget without a URL change. Do not report a
                    # page change merely because the interaction revision moved.
                    page_changed = "unknown"
                self._advance_page_revision()
                action_state = "executed"
            elif action == "screenshot":
                target = Path(str(screenshot_path or "")).resolve()
                if not str(screenshot_path or "").strip():
                    return BrowserPageResult(
                        ok=False,
                        status="invalid_request",
                        action=action,
                        reason="screenshot_path_required",
                        action_state="not_started",
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(target), full_page=False)
                action_state = "executed"
                page_changed = "no"

            # Check if any download was triggered by the action
            dl_after = self._download_tracker.latest()
            download_record = None
            if dl_after is not None and (dl_before is None or dl_after.download_id != dl_before.download_id):
                download_record = dl_after

            clean_obs_mode = str(observation_mode or "").strip().lower()
            if clean_obs_mode in {"hybrid", "text", "visual"}:
                effective_mode = clean_obs_mode
            elif action in CONTROL_ACTIONS | {"navigate", "scroll"}:
                effective_mode = "hybrid"
            else:
                effective_mode = "text"

            try:
                observed_state = self._scoped_live_state(page, options)
                title = self._safe_title(page)
                snapshot_kind = "elements" if action == "elements" else "page"
                element_candidates: list[dict[str, Any]] = []
                if action == "elements":
                    captured = self._safe_element_summary(page, element_limit=element_limit)
                    element_candidates = self._safe_link_candidate_rows(page, max_items=element_limit)
                elif options.get("frame_selector") or options.get("scope_selector"):
                    scope = self._scope_page(page, options)
                    locator = scope.locator(options.get("scope_selector") or "body")
                    if locator.count() != 1:
                        raise ValueError("scope_selector_must_match_one_element")
                    if action == "read_text":
                        captured = locator.inner_text(timeout=self.timeout_ms)
                    else:
                        captured = locator.aria_snapshot(timeout=self.timeout_ms)
                    captured = ("Observation scope: " + str({k: options[k] for k in ("frame_selector", "scope_selector") if k in options})
                                + "\n" + captured)[:self._snapshot_capture_max_chars]
                else:
                    captured = self._safe_page_snapshot(page, max_chars=self._snapshot_capture_max_chars)
                record = self.store_snapshot(kind=snapshot_kind, text=captured, url=current_url, title=title)
                from .paged_reading import FIRST_PAGE_BUDGET_CHARS, FIRST_PAGE_BUDGET_LINES, slice_page

                page_text, next_offset, total_chars = slice_page(
                    captured,
                    start=0,
                    budget_chars=FIRST_PAGE_BUDGET_CHARS,
                    budget_lines=FIRST_PAGE_BUDGET_LINES,
                )
                complete = next_offset >= total_chars

                screenshot_bytes: bytes | None = None
                screenshot_id = ""
                viewport_box = self._safe_viewport_box(page)
                if effective_mode in {"hybrid", "visual"}:
                    try:
                        screenshot_bytes = self._safe_observation_screenshot(page)
                        screenshot_id = generate_screenshot_id()
                        visual_status = "available"
                        observation_state = "complete"
                    except Exception:
                        visual_status = "visual_unavailable"
                        observation_state = "partial"
                else:
                    visual_status = "not_requested"
                    observation_state = "complete"

                if self._scoped_live_state(page, options) != observed_state:
                    # Preserve text for inspection but do not publish actionable
                    # evidence assembled from two different page states.
                    observation_state = "unstable"
                    screenshot_bytes = None
                    screenshot_id = ""
                    visual_status = "visual_unavailable" if effective_mode != "text" else "not_requested"
                    observed_state = {}

                obs_id = generate_observation_id()
                obs_record = ObservationRecord(
                    observation_id=obs_id,
                    page_id=self._page_id,
                    browser_generation=self._browser_generation,
                    document_generation=self._document_generation,
                    url=current_url,
                    title=title,
                    aria_snapshot=captured if snapshot_kind != "elements" else "",
                    visible_text=captured,
                    screenshot_id=screenshot_id,
                    screenshot_bytes=screenshot_bytes,
                    viewport=viewport_box,
                    screenshot_dimensions=png_dimensions(screenshot_bytes),
                    page_revision=self._page_revision,
                    action_state=action_state,
                    observation_state=observation_state,
                    page_changed=page_changed,
                    visual_status=visual_status,
                    element_candidates=element_candidates,
                    live_state=observed_state,
                    captured_at=float(self._now()),
                )
                obs_record = self._observation_store.put(obs_record)
                model_image = obs_record.build_model_image_dict() if obs_record.visual_status == "available" else None

                return BrowserPageResult(
                    ok=True,
                    status="executed" if action in CONTROL_ACTIONS | {"screenshot"} else "available",
                    action=action,
                    url=current_url,
                    title=title,
                    text=page_text,
                    snapshot_id=str(record.get("snapshot_id") or ""),
                    complete=complete,
                    next_cursor=(
                        f"{record.get('snapshot_id')}:{next_offset}" if not complete else ""
                    ),
                    shown_chars=len(page_text),
                    total_chars=total_chars,
                    page_revision=str(record.get("revision") or ""),
                    observation_id=obs_id,
                    screenshot_id=obs_record.screenshot_id,
                    action_state=action_state,
                    observation_state=obs_record.observation_state,
                    page_changed=page_changed,
                    visual_status=obs_record.visual_status,
                    model_image=model_image,
                    viewport=viewport_box,
                    screenshot_dimensions=obs_record.screenshot_dimensions,
                    download_id=download_record.download_id if download_record else "",
                    download_status=download_record.status if download_record else "",
                    download_filename=download_record.suggested_filename if download_record else "",
                    attachment_handle=download_record.attachment_handle if download_record else "",
                    attachment_id=download_record.attachment_id if download_record else "",
                    dialog_events=tuple(self._dialog_events[dialog_start:]),
                )
            except Exception as obs_exc:
                if action_state == "executed":
                    return BrowserPageResult(
                        ok=True,
                        status="executed",
                        action=action,
                        url=current_url,
                        action_state="executed",
                        observation_state="failed",
                        page_changed=page_changed,
                        reason=f"observation_failed_after_action: {obs_exc}",
                        retryable=True,
                        next_action="snapshot",
                        download_id=download_record.download_id if download_record else "",
                        download_status=download_record.status if download_record else "",
                        download_filename=download_record.suggested_filename if download_record else "",
                        attachment_handle=download_record.attachment_handle if download_record else "",
                        attachment_id=download_record.attachment_id if download_record else "",
                        dialog_events=tuple(self._dialog_events[dialog_start:]),
                    )
                raise
        finally:
            self._dialog_policy = None

    def _scope_page(self, page: Any, options: dict[str, Any]) -> Any:
        selector = options.get("frame_selector")
        if not selector:
            return page
        locator = page.locator(selector)
        if locator.count() != 1:
            raise ValueError("frame_selector_must_match_one_iframe")
        element = locator.element_handle(timeout=self.timeout_ms)
        frame = element.content_frame() if element else None
        if frame is None:
            raise ValueError("frame_unavailable")
        return frame

    def _scoped_live_state(self, page: Any, options: dict[str, Any]) -> dict[str, Any]:
        root = read_live_page_state(page)
        if not options.get("frame_selector"):
            return root
        return {"main": root, "frame_selector": options["frame_selector"],
                "frame": read_live_page_state(self._scope_page(page, options))}

    def _ensure_page(self) -> Any:
        if self._page is not None:
            try:
                if not self._page.is_closed():
                    return self._page
            except Exception:
                pass
        browser = self._browser
        if browser is not None:
            try:
                if not browser.is_connected():
                    self._discard_browser_objects()
            except Exception:
                self._discard_browser_objects()
        from playwright.sync_api import sync_playwright

        self._playwright = self._playwright or sync_playwright().start()
        if self._browser is None:
            self._browser = self._launch_browser()
        try:
            if self._context is None:
                self._context = self._browser.new_context(accept_downloads=True)
                self._context.on("download", self._handle_download)
            self._page = self._context.new_page()
            self._setup_page_listeners(self._page)
        except Exception:
            # A visible browser window can be closed by the user while the host
            # still owns Python proxy objects. Drop the complete browser tree;
            # reusing only the old context poisons every later request.
            self._discard_browser_objects()
            self._playwright = self._playwright or sync_playwright().start()
            self._browser = self._launch_browser()
            self._context = self._browser.new_context(accept_downloads=True)
            self._context.on("download", self._handle_download)
            self._page = self._context.new_page()
            self._setup_page_listeners(self._page)
        self._bring_to_front(self._page)
        return self._page

    def _setup_page_listeners(self, page: Any) -> None:
        try:
            page.on("dialog", self._handle_dialog)
        except Exception:
            pass

    def _handle_download(self, download: Any) -> None:
        try:
            self._download_tracker.handle_playwright_download(
                download,
                profile_user_id=self._profile_user_id,
                session_id=self._session_id,
            )
        except Exception:
            pass

    def _handle_dialog(self, dialog: Any) -> None:
        event = {
            "type": str(getattr(dialog, "type", "") or ""),
            "message": str(getattr(dialog, "message", "") or "")[:200],
            "response": "dismiss", "state": "unknown",
        }
        try:
            policy = self._dialog_policy
            self._dialog_policy = None  # Explicit policy applies to one dialog only.
            if policy and policy.get("action") == "accept":
                event["response"] = "accept"
                if event["type"] == "prompt":
                    dialog.accept(prompt_text=policy.get("prompt_text", ""))
                else:
                    dialog.accept()
            else:
                dialog.dismiss()
            event["state"] = "handled"
        except Exception:
            event["reason"] = "dialog_response_failed"
        finally:
            self._dialog_events.append(event)

    def _launch_browser(self) -> Any:
        launch_kwargs: dict[str, Any] = {"headless": self.headless}
        if not self.browser_channel:
            return self._playwright.chromium.launch(**launch_kwargs)
        try:
            return self._playwright.chromium.launch(channel=self.browser_channel, **launch_kwargs)
        except Exception as channel_exc:
            try:
                return self._playwright.chromium.launch(**launch_kwargs)
            except Exception as fallback_exc:
                channel_reason = str(channel_exc).splitlines()[0][:120]
                fallback_reason = str(fallback_exc).splitlines()[0][:120]
                raise RuntimeError(
                    f"browser_launch_failed channel={self.browser_channel}: {channel_reason}; fallback: {fallback_reason}"
                ) from fallback_exc

    def _close_objects(self) -> None:
        for item in (self._page, self._context, self._browser):
            if item is None:
                continue
            try:
                item.close()
            except Exception:
                pass
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._browser_generation += 1
        self._page_id = "page_" + uuid.uuid4().hex[:8]
        if hasattr(self, "_observation_store"):
            self._observation_store.clear()
        if hasattr(self, "_download_tracker"):
            self._download_tracker.cancel_pending_downloads()
        with self._snapshot_lock:
            self._snapshot_cache.clear()

    def _discard_browser_objects(self) -> None:
        """Forget a closed page/context/browser without stopping Playwright."""
        for item in (self._page, self._context, self._browser):
            if item is None:
                continue
            try:
                item.close()
            except Exception:
                pass
        self._page = None
        self._context = None
        self._browser = None
        self._browser_generation += 1
        self._page_id = "page_" + uuid.uuid4().hex[:8]
        if hasattr(self, "_observation_store"):
            self._observation_store.clear()
        if hasattr(self, "_download_tracker"):
            self._download_tracker.cancel_pending_downloads()

    @staticmethod
    def _classify_browser_error(exc: BaseException, *, action: str) -> tuple[str, str, bool, str]:
        raw = str(exc or "").strip()
        lowered = raw.lower()
        reason = (raw.splitlines()[0] if raw else "browser_page_runner_failed")[:200]
        if any(marker in lowered for marker in ("target page, context or browser has been closed", "browser has been closed", "target closed")):
            return "browser_closed", reason, True, "navigate"
        if "timeout" in lowered:
            return "navigation_timeout" if action in {"navigate", "read_text"} else "action_timeout", reason, True, "snapshot"
        if any(marker in lowered for marker in ("no node found", "resolved to 0 elements", "not attached", "detached")):
            return "stale_target", reason, True, "snapshot"
        if any(marker in lowered for marker in ("name_not_resolved", "connection refused", "connection reset", "net::err")):
            return "site_unreachable", reason, True, "navigate"
        return "unavailable", reason, True, "snapshot"

    def _safe_title(self, page: Any) -> str:
        try:
            return str(page.title() or "").strip()[:200]
        except Exception:
            return ""

    def _safe_observation_screenshot(self, page: Any) -> bytes:
        """Capture a model-only viewport image, masking password controls when supported."""
        try:
            password_controls = page.locator('input[type="password"], textarea[type="password"]')
            return page.screenshot(
                type="png",
                full_page=False,
                timeout=3000,
                mask=[password_controls],
                scale="css",
                caret="initial",
            )
        except TypeError as exc:
            raise RuntimeError("screenshot_masking_unavailable") from exc

    def _safe_body_text(self, page: Any, *, max_chars: int) -> str:
        try:
            text = str(page.inner_text("body", timeout=3000) or "")
        except Exception:
            return ""
        limit = max(500, min(self._snapshot_capture_max_chars, int(max_chars or 3000)))
        return text.replace("\r\n", "\n").replace("\r", "\n").strip()[:limit]

    def _safe_page_snapshot(self, page: Any, *, max_chars: int) -> str:
        limit = max(500, min(self._snapshot_capture_max_chars, int(max_chars or 3000)))
        parts: list[str] = []
        position = self._safe_scroll_position(page)
        if position:
            parts.append(position)
        candidates = self._safe_link_candidates(page, max_items=12)
        if candidates:
            parts.append("Visible link/video candidates:")
            parts.append(candidates)
        snapshot = self._safe_aria_snapshot(page, max_chars=limit)
        if snapshot:
            parts.append("Accessibility snapshot with element refs:")
            parts.append(snapshot)
        else:
            body_text = self._safe_body_text(page, max_chars=limit)
            if body_text:
                parts.append("Body text excerpt:")
                parts.append(body_text)
        return "\n".join(part for part in parts if part).strip()[:limit]

    def _safe_aria_snapshot(self, page: Any, *, max_chars: int) -> str:
        try:
            locator = page.locator("body")
            try:
                snapshot = locator.aria_snapshot(mode="ai", boxes=True, timeout=2500)
            except TypeError:
                try:
                    snapshot = locator.aria_snapshot(mode="ai", timeout=2500)
                except TypeError:
                    snapshot = locator.aria_snapshot(timeout=2500)
        except Exception:
            return ""
        text = str(snapshot or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        limit = max(500, min(self._snapshot_capture_max_chars, int(max_chars or 3000)))
        viewport = self._safe_viewport_box(page)
        visible_text = self._filter_visible_snapshot_lines(text, viewport=viewport)
        return (visible_text or text)[:limit]

    def _safe_link_candidate_rows(self, page: Any, *, max_items: int) -> list[dict[str, Any]]:
        try:
            payload = page.evaluate(
                """
                (maxItems) => {
                    const limit = Math.max(1, Math.min(30, Number(maxItems) || 12));
                    const viewportW = Math.max(1, window.innerWidth || document.documentElement.clientWidth || 1);
                    const viewportH = Math.max(1, window.innerHeight || document.documentElement.clientHeight || 1);
                    function normalize(value) {
                        return String(value || "").replace(/\\s+/g, " ").trim();
                    }
                    function visibleAnchor(anchor) {
                        if (!anchor || !anchor.getClientRects) {
                            return false;
                        }
                        const style = window.getComputedStyle(anchor);
                        if (!style || style.display === "none" || style.visibility === "hidden" || Number(style.opacity || 1) === 0) {
                            return false;
                        }
                        return Array.from(anchor.getClientRects()).some((rect) =>
                            rect.width > 2 &&
                            rect.height > 2 &&
                            rect.bottom >= -48 &&
                            rect.top <= viewportH + 48 &&
                            rect.right >= -48 &&
                            rect.left <= viewportW + 48
                        );
                    }
                    const anchors = Array.from(document.querySelectorAll("a[href]"));
                    const seen = new Set();
                    const rows = [];
                    for (let sourceIndex = 0; sourceIndex < anchors.length; sourceIndex += 1) {
                        const anchor = anchors[sourceIndex];
                        if (!visibleAnchor(anchor)) {
                            continue;
                        }
                        const href = String(anchor.href || anchor.getAttribute("href") || "").trim();
                        if (!href || seen.has(href)) {
                            continue;
                        }
                        const title =
                            normalize(anchor.getAttribute("title")) ||
                            normalize(anchor.getAttribute("aria-label")) ||
                            normalize(anchor.innerText || anchor.textContent);
                        if (!title || title.length < 2) {
                            continue;
                        }
                        const isVideo = /\\/video\\//i.test(href) || /\\bBV[A-Za-z0-9]+/.test(href);
                        const isNav = /\\/anime\\/?$|\\/movie\\/?$|\\/tv\\/?$|\\/guochuang\\/?$|\\/variety\\/?$|\\/documentary\\/?$|\\/c\\//i.test(href);
                        rows.push({
                            title: title.slice(0, 160),
                            href,
                            isVideo,
                            isNav,
                            sourceIndex,
                            score: (isVideo ? 100 : 0) + (!isNav ? 10 : 0) + Math.min(20, Math.round(title.length / 8))
                        });
                        seen.add(href);
                    }
                    rows.sort((a, b) => b.score - a.score);
                    return rows.slice(0, limit);
                }
                """,
                max_items,
            )
        except Exception:
            return []
        if not isinstance(payload, list):
            return []
        rows: list[dict[str, Any]] = []
        for item in payload[: max(1, min(30, int(max_items or 12)))]:
            if not isinstance(item, dict):
                continue
            raw_url = str(item.get("href") or "").strip()
            title = " ".join(str(item.get("title") or "").replace("\r", "\n").split())[:160]
            if not raw_url or not title:
                continue
            absolute_url = self._safe_public_candidate_url(raw_url, base_url=str(getattr(page, "url", "") or ""))
            if not absolute_url:
                continue
            rows.append(
                {
                    "title": title,
                    "url": absolute_url,
                    "kind": "video" if item.get("isVideo") else "link",
                    "source_index": self._safe_int(item.get("sourceIndex"), default=-1, minimum=-1, maximum=100_000),
                }
            )
        return rows

    def _safe_link_candidates(self, page: Any, *, max_items: int) -> str:
        rows = self._safe_link_candidate_rows(page, max_items=max_items)
        lines: list[str] = []
        for item in rows:
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            kind = str(item.get("kind") or "link").strip() or "link"
            if not title or not url:
                continue
            lines.append(f"{len(lines) + 1}. {kind}: {title} -> {url[:240]}")
        return "\n".join(lines)

    def _safe_public_candidate_url(self, value: str, *, base_url: str) -> str:
        try:
            url = urljoin(base_url or "", str(value or "").strip())
        except Exception:
            return ""
        lowered = url.lower()
        if not (lowered.startswith("http://") or lowered.startswith("https://")):
            return ""
        if any(marker in lowered for marker in ("api_key=", "password=", "secret=", "token=")):
            return ""
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").strip().lower().strip("[]")
        if not hostname:
            return ""
        if self._is_private_or_local_host(hostname) and not self.allow_private_network_urls:
            return ""
        return url

    @staticmethod
    def _is_private_or_local_host(hostname: str) -> bool:
        host = str(hostname or "").strip().lower().strip("[]")
        if not host or host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
            return True
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return False
        return bool(ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved)

    def _filter_visible_snapshot_lines(self, text: str, *, viewport: tuple[int, int]) -> str:
        width, height = viewport
        if width <= 0 or height <= 0:
            return text
        box_re = re.compile(r"\s*\[box=(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)\]")
        lines = str(text or "").splitlines()
        kept: list[str] = []
        last_kept_box_indent: int | None = None
        margin = 48
        for raw_line in lines:
            indent = len(raw_line) - len(raw_line.lstrip(" "))
            match = box_re.search(raw_line)
            if match:
                try:
                    x = float(match.group(1))
                    y = float(match.group(2))
                    box_width = float(match.group(3))
                    box_height = float(match.group(4))
                except Exception:
                    last_kept_box_indent = None
                    continue
                visible = (
                    box_width > 1
                    and box_height > 1
                    and y + box_height >= -margin
                    and y <= height + margin
                    and x + box_width >= -margin
                    and x <= width + margin
                )
                if not visible:
                    last_kept_box_indent = None
                    continue
                cleaned = box_re.sub("", raw_line).rstrip()
                kept.append(cleaned)
                last_kept_box_indent = indent
                continue
            if last_kept_box_indent is not None and indent > last_kept_box_indent:
                kept.append(raw_line.rstrip())
                continue
            last_kept_box_indent = None
        return "\n".join(line for line in kept if line.strip()).strip()

    def _safe_viewport_box(self, page: Any) -> tuple[int, int]:
        try:
            payload = page.evaluate(
                """
                () => ({
                    width: Math.max(1, Math.round(window.innerWidth || document.documentElement.clientWidth || 1)),
                    height: Math.max(1, Math.round(window.innerHeight || document.documentElement.clientHeight || 1)),
                })
                """
            )
        except Exception:
            return (0, 0)
        if not isinstance(payload, dict):
            return (0, 0)
        width = self._safe_int(payload.get("width"), default=0, minimum=0, maximum=100_000)
        height = self._safe_int(payload.get("height"), default=0, minimum=0, maximum=100_000)
        return (width, height)

    def _safe_scroll_position(self, page: Any) -> str:
        try:
            payload = page.evaluate(
                """
                () => {
                    const viewportHeight = Math.max(1, Math.round(window.innerHeight || document.documentElement.clientHeight || 1));
                    const scrollHeight = Math.max(
                        viewportHeight,
                        Math.round(
                            document.documentElement.scrollHeight ||
                            (document.body && document.body.scrollHeight) ||
                            viewportHeight
                        )
                    );
                    const scrollY = Math.max(0, Math.round(window.scrollY || document.documentElement.scrollTop || 0));
                    const maxScroll = Math.max(1, scrollHeight - viewportHeight);
                    const progress = Math.max(0, Math.min(100, Math.round((scrollY / maxScroll) * 100)));
                    return { scrollY, viewportHeight, scrollHeight, progress };
                }
                """
            )
        except Exception:
            return ""
        if not isinstance(payload, dict):
            return ""
        scroll_y = self._safe_int(payload.get("scrollY"), default=0, minimum=0, maximum=1_000_000)
        viewport_height = self._safe_int(payload.get("viewportHeight"), default=0, minimum=0, maximum=1_000_000)
        scroll_height = self._safe_int(payload.get("scrollHeight"), default=0, minimum=0, maximum=1_000_000)
        progress = self._safe_int(payload.get("progress"), default=0, minimum=0, maximum=100)
        return f"Page position: {progress}% down (scrollY {scroll_y}, viewport {viewport_height}/{scroll_height})."

    def _safe_scroll_delta(self, value: int) -> int:
        try:
            delta = int(value)
        except Exception:
            delta = 800
        return max(-2400, min(2400, delta or 800))

    def _safe_int(self, value: Any, *, default: int, minimum: int, maximum: int) -> int:
        try:
            number = int(value)
        except Exception:
            number = default
        return max(minimum, min(maximum, number))

    def _safe_element_summary(self, page: Any, *, element_limit: int) -> str:
        try:
            limit_value = int(element_limit or 20)
        except Exception:
            limit_value = 20
        limit = max(1, min(40, limit_value))
        selector = "a, button, input, textarea, select, [role='button'], [role='link'], [contenteditable='true']"
        try:
            locator = page.locator(selector)
            count = min(locator.count(), limit)
        except Exception:
            return ""
        lines: list[str] = []
        for index in range(count):
            try:
                item = locator.nth(index)
                if not item.is_visible(timeout=250):
                    continue
                tag_name = str(item.evaluate("node => node.tagName.toLowerCase()") or "").strip()
                role = str(item.get_attribute("role") or "").strip()
                href = str(item.get_attribute("href") or "").strip()
                aria = str(item.get_attribute("aria-label") or "").strip()
                placeholder = str(item.get_attribute("placeholder") or "").strip()
                text = str(item.inner_text(timeout=500) or "").strip()
                label = aria or placeholder or text or href
                label = " ".join(label.replace("\r", "\n").split())[:140]
                element_type = role or tag_name or "element"
                line = f"{len(lines) + 1}. {element_type}"
                if label:
                    line += f": {label}"
                if href:
                    line += f" ({href[:180]})"
                lines.append(line)
            except Exception:
                continue
            if len(lines) >= limit:
                break
        return "\n".join(lines)

    def _perform_action(self, action: Callable[[], Any]) -> Any:
        # A Playwright timeout can happen after the input was delivered. Only
        # a successful return proves execution; exceptions remain unknown.
        self._action_state = "unknown"
        value = action()
        self._action_state = "executed"
        return value

    def _control_failure(self, exc: Exception, *, action: str, fallback: str) -> BrowserPageResult:
        status, reason, retryable, next_action = self._classify_browser_error(exc, action=action)
        if self._action_state != "not_started":
            self._advance_page_revision()
            next_action = "current"
        return BrowserPageResult(
            ok=False, status=status, action=action, reason=reason or fallback,
            retryable=retryable, next_action=next_action,
            action_state=self._action_state, observation_state="failed",
        )

    def _run_control_action(
        self,
        page: Any,
        *,
        action: str,
        selector: str,
        ref: str,
        text: str,
        key: str,
        candidate_index: int,
        coordinate: tuple[int, int] | list[int] | None = None,
        screenshot_id: str = "",
        observation_id: str = "",
        options: dict[str, Any] | None = None,
    ) -> BrowserPageResult | None:
        safe_selector = str(selector or "").strip()
        safe_ref = str(ref or "").strip()
        safe_candidate_index = self._safe_int(candidate_index, default=0, minimum=0, maximum=30)
        target_selector = f"aria-ref={safe_ref}" if safe_ref else safe_selector

        # 1. Candidate index click strictly bound to frozen observation candidates
        if action == "click" and safe_candidate_index > 0:
            if not observation_id:
                return BrowserPageResult(
                    ok=False,
                    status="stale_target",
                    action=action,
                    reason="observation_id_required_for_candidate_click",
                    retryable=True,
                    next_action="elements",
                )
            target_obs = self._observation_store.get(observation_id)
            if target_obs is None or not target_obs.element_candidates:
                return BrowserPageResult(
                    ok=False,
                    status="stale_target",
                    action=action,
                    reason="elements_observation_required_before_candidate_click",
                    retryable=True,
                    next_action="elements",
                )
            if (
                target_obs.browser_generation != self._browser_generation
                or target_obs.document_generation != self._document_generation
                or target_obs.page_id != self._page_id
                or target_obs.page_revision != self._page_revision
            ):
                return BrowserPageResult(
                    ok=False,
                    status="stale_target",
                    action=action,
                    reason="observation_stale_for_candidate_click",
                    retryable=True,
                    next_action="elements",
                )
            if safe_candidate_index > len(target_obs.element_candidates):
                return BrowserPageResult(
                    ok=False,
                    status="stale_target",
                    action=action,
                    reason=f"candidate_index_{safe_candidate_index}_out_of_range",
                    retryable=True,
                    next_action="elements",
                )
            candidate = target_obs.element_candidates[safe_candidate_index - 1]
            try:
                source_index = self._safe_int(candidate.get("source_index"), default=-1, minimum=-1, maximum=100_000)
                if source_index < 0:
                    raise RuntimeError("candidate_source_missing")
                locator = page.locator("a[href]").nth(source_index)
                if not locator.is_visible():
                    raise RuntimeError("candidate_not_visible")
                current_href = str(locator.get_attribute("href") or "").strip()
                current_url = self._safe_public_candidate_url(
                    current_href,
                    base_url=str(getattr(page, "url", "") or ""),
                )
                expected_url = str(candidate.get("url") or "").strip()
                current_title = " ".join(
                    str(
                        locator.get_attribute("title")
                        or locator.get_attribute("aria-label")
                        or locator.inner_text()
                        or ""
                    ).split()
                )[:160]
                expected_title = " ".join(str(candidate.get("title") or "").split())[:160]
                if current_url != expected_url or current_title != expected_title:
                    raise RuntimeError("candidate_identity_changed")
                self._click_locator_and_switch_to_popup_if_any(page, locator)
                return None
            except Exception as exc:
                return self._control_failure(exc, action=action, fallback="candidate_click_failed")

        # 2. Visual coordinate click fallback
        if action == "click" and not target_selector and coordinate is not None:
            if not screenshot_id:
                return BrowserPageResult(
                    ok=False,
                    status="invalid_request",
                    action=action,
                    reason="screenshot_id_required_for_coordinate_click",
                    retryable=True,
                    next_action="snapshot",
                )
            valid, reason = self._observation_store.validate_coordinate_target(
                screenshot_id=screenshot_id,
                coordinate=coordinate,
                current_browser_gen=self._browser_generation,
                current_doc_gen=self._document_generation,
                observation_id=observation_id,
                current_page_id=self._page_id,
                current_page_revision=self._page_revision,
                current_viewport=self._safe_viewport_box(page),
            )
            if not valid:
                return BrowserPageResult(
                    ok=False,
                    status="stale_target",
                    action=action,
                    reason=reason,
                    retryable=True,
                    next_action="snapshot",
                )
            try:
                x, y = int(coordinate[0]), int(coordinate[1])
                target_state = page.evaluate("""([x, y]) => {
                    const el = document.elementFromPoint(x, y);
                    return {masked: !!el?.closest('input[type=password], textarea[type=password]')};
                }""", [x, y])
                visual_record = self._observation_store.get_by_screenshot_id(screenshot_id)
                if (target_state.get("masked") or visual_record is None
                        or not coordinate_pixels_match(visual_record, self._safe_observation_screenshot(page), x, y)
                        or read_live_page_state(page) != visual_record.live_state):
                    return BrowserPageResult(
                        ok=False, status="stale_target", action=action,
                        reason="coordinate_target_changed_or_masked", next_action="snapshot",
                    )
                context = getattr(page, "context", None)
                if context is not None:
                    new_page = self._run_action_with_optional_popup(
                        page,
                        lambda: self._perform_action(lambda: page.mouse.click(x, y)),
                    )
                    if new_page is not None:
                        self._adopt_popup_page(page, new_page)
                else:
                    self._perform_action(lambda: page.mouse.click(x, y))
                return None
            except Exception as exc:
                return self._control_failure(exc, action=action, fallback="coordinate_click_failed")

        # 3. Standard selector / ref target validation
        if safe_ref and not observation_id:
            return BrowserPageResult(
                ok=False,
                status="stale_target",
                action=action,
                reason="observation_id_required_for_ref",
                retryable=True,
                next_action="snapshot",
            )
        if action in CONTROL_ACTIONS - {"press"} and not target_selector:
            return BrowserPageResult(ok=False, status="invalid_request", action=action, reason="selector_required")
        try:
            options = options or {}
            scope = self._scope_page(page, options)
            if action == "click":
                self._click_locator_and_switch_to_popup_if_any(page, scope.locator(target_selector))
            elif action == "fill":
                self._perform_action(lambda: scope.locator(target_selector).fill(text, timeout=self.timeout_ms))
            elif action == "hover":
                self._perform_action(lambda: scope.locator(target_selector).hover(timeout=self.timeout_ms))
            elif action == "select_option":
                self._perform_action(lambda: scope.locator(target_selector).select_option(value=options["values"], timeout=self.timeout_ms))
            elif action == "set_checked":
                self._perform_action(lambda: scope.locator(target_selector).set_checked(options["checked"], timeout=self.timeout_ms))
            elif action == "upload":
                self._perform_action(lambda: scope.locator(target_selector).set_input_files(options["upload_paths"], timeout=self.timeout_ms))
            elif action == "press":
                if target_selector:
                    self._perform_action(lambda: scope.locator(target_selector).press(str(key or "Enter"), timeout=self.timeout_ms))
                else:
                    self._perform_action(lambda: page.keyboard.press(str(key or "Enter")))
            else:
                return BrowserPageResult(ok=False, status="invalid_request", action=action, reason="unsupported_control_action")
            try:
                page.wait_for_load_state("domcontentloaded", timeout=1500)
            except Exception:
                pass
            return None
        except Exception as exc:
            return self._control_failure(exc, action=action, fallback="browser_control_action_failed")

    def _click_locator_and_switch_to_popup_if_any(self, page: Any, locator: Any) -> None:
        context = getattr(page, "context", None)
        if context is None:
            self._perform_action(lambda: locator.click(timeout=self.timeout_ms))
            return
        new_page = self._run_action_with_optional_popup(
            page,
            lambda: self._perform_action(lambda: locator.click(timeout=self.timeout_ms)),
        )
        if new_page is None:
            self._page = page
            return
        self._adopt_popup_page(page, new_page)

    def _run_action_with_optional_popup(self, page: Any, action: Callable[[], Any]) -> Any | None:
        """Run one action while associating an event-created popup with it."""
        created_pages: list[Any] = []

        def remember_page(candidate: Any) -> None:
            if candidate not in created_pages:
                created_pages.append(candidate)

        page.on("popup", remember_page)
        try:
            action()
            # Pump immediate popup events; this is not a claim of page stability.
            page.wait_for_timeout(150)
        finally:
            page.remove_listener("popup", remember_page)
        if len(created_pages) > 1:
            raise RuntimeError("multiple_popups_created_by_action")
        return created_pages[0] if created_pages else None

    def _adopt_popup_page(self, source_page: Any, new_page: Any) -> None:
        try:
            new_page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
        except Exception:
            pass
        popup_url = str(getattr(new_page, "url", "") or "").strip()
        if not self._safe_public_candidate_url(popup_url, base_url=""):
            try:
                new_page.close()
            except Exception:
                pass
            self._page = source_page
            raise RuntimeError("popup_url_rejected")
        self._page = new_page
        self._setup_page_listeners(new_page)
        self._document_generation += 1
        self._page_id = "page_" + uuid.uuid4().hex[:8]
        self._bring_to_front(new_page)

    def _bring_to_front(self, page: Any) -> None:
        try:
            page.bring_to_front()
        except Exception:
            pass

    def _brief_visual_pause(self, page: Any) -> None:
        try:
            page.wait_for_timeout(200)
        except Exception:
            pass

    def _default_browser_channel(self) -> str:
        return "msedge" if sys.platform == "win32" else ""


class ManagedBrowserSessionManager:
    """Bounded browser sessions keyed by Akane profile + conversation.

    The manager is intentionally small: it owns lifecycle and isolation while
    ``ManagedBrowserPageRunner`` remains the only page executor. This keeps the
    public tool contract stable and prevents a group task from inheriting a
    private chat's page, cookies, popup, or stale closed-window state.
    """

    def __init__(
        self,
        *,
        runner_factory: Callable[[], ManagedBrowserPageRunner] | None = None,
        max_sessions: int = 8,
        idle_ttl_seconds: float = 30 * 60,
        now: Any = None,
    ) -> None:
        self._runner_factory = runner_factory or ManagedBrowserPageRunner
        self._max_sessions = max(1, int(max_sessions))
        self._idle_ttl_seconds = max(30.0, float(idle_ttl_seconds))
        self._now = now or time.time
        self._lock = threading.Lock()
        self._sessions: dict[str, tuple[float, ManagedBrowserPageRunner]] = {}
        self._capability_status_cache: dict[str, Any] | None = None

    def runner_for_session(self, profile_user_id: str, session_id: str) -> ManagedBrowserPageRunner:
        key = self._session_key(profile_user_id, session_id)
        evicted: list[ManagedBrowserPageRunner] = []
        with self._lock:
            now = float(self._now())
            for stale_key, (last_used, runner) in list(self._sessions.items()):
                if now - last_used > self._idle_ttl_seconds:
                    evicted.append(runner)
                    del self._sessions[stale_key]
            cached = self._sessions.get(key)
            if cached is None:
                runner = self._runner_factory()
                self._bind_runner_identity(runner, profile_user_id, session_id)
                self._sessions[key] = (now, runner)
            else:
                runner = cached[1]
                self._sessions[key] = (now, runner)
            while len(self._sessions) > self._max_sessions:
                oldest_key = min(self._sessions.items(), key=lambda item: item[1][0])[0]
                if oldest_key == key and len(self._sessions) > 1:
                    alternatives = [item for item in self._sessions.items() if item[0] != key]
                    oldest_key = min(alternatives, key=lambda item: item[1][0])[0]
                _last_used, old_runner = self._sessions.pop(oldest_key)
                evicted.append(old_runner)
        for old_runner in evicted:
            old_runner.shutdown()
        return runner

    @staticmethod
    def _bind_runner_identity(runner: Any, profile_user_id: str, session_id: str) -> None:
        setter = getattr(runner, "set_session_identity", None)
        if callable(setter):
            setter(profile_user_id, session_id)

    def capability_status(self) -> dict[str, Any]:
        with self._lock:
            cached = self._capability_status_cache
        if cached is not None:
            return dict(cached)
        runner = self._runner_factory()
        try:
            status = runner.capability_status()
        finally:
            runner.shutdown()
        with self._lock:
            self._capability_status_cache = dict(status)
        return dict(status)

    def shutdown(self, *, timeout: float = 5.0) -> None:
        with self._lock:
            runners = [runner for _last_used, runner in self._sessions.values()]
            self._sessions.clear()
        if not runners:
            return
        closer = ThreadPoolExecutor(max_workers=min(4, len(runners)), thread_name_prefix="akane-browser-close")
        futures = [closer.submit(runner.shutdown) for runner in runners]
        wait(futures, timeout=max(0.1, float(timeout)))
        closer.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def _session_key(profile_user_id: str, session_id: str) -> str:
        profile = str(profile_user_id or "").strip() or "anonymous"
        session = str(session_id or "").strip() or profile
        return f"{profile}\x1f{session}"
