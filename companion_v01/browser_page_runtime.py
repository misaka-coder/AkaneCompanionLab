from __future__ import annotations

import importlib.util
import re
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin

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
        snapshot_ttl_seconds: float = SNAPSHOT_TTL_SECONDS,
        snapshot_max_entries: int = SNAPSHOT_MAX_ENTRIES,
        snapshot_max_total_bytes: int = SNAPSHOT_MAX_TOTAL_BYTES,
        snapshot_capture_max_chars: int = SNAPSHOT_CAPTURE_MAX_CHARS,
        now: Any = None,
    ) -> None:
        self.headless = bool(headless)
        self.timeout_ms = max(3000, min(60000, int(timeout_ms or 15000)))
        self.browser_channel = self._default_browser_channel() if browser_channel is None else str(browser_channel or "").strip()
        self._executor: ThreadPoolExecutor | None = None
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
        self._capability_status_cache: dict[str, Any] | None = None

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
    ) -> BrowserPageResult:
        normalized_action = str(action or "").strip() or "current"
        if not self.is_available():
            return BrowserPageResult(
                ok=False,
                status="unavailable",
                action=normalized_action,
                reason="playwright_not_installed",
            )
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

    def shutdown(self) -> None:
        executor = self._executor
        if executor is None:
            self._close_objects()
            return
        try:
            executor.submit(self._close_objects).result(timeout=5)
        except Exception:
            # Browser shutdown is best-effort. A slow Edge/Chromium teardown
            # must not turn an already successful browser task into a caller-
            # visible failure or block host shutdown indefinitely.
            pass
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    def _submit(self, task: Callable[[], BrowserPageResult]) -> BrowserPageResult:
        with self._lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="akane-browser-page")
            executor = self._executor
        return executor.submit(task).result(timeout=max(8, self.timeout_ms / 1000 + 5))

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
    ) -> BrowserPageResult:
        safe_to_retry = action in {"navigate", "read_text", "current", "snapshot", "screenshot", "elements"}
        for attempt in range(2):
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
                )
            except Exception as exc:
                status, reason, retryable, next_action = self._classify_browser_error(exc, action=action)
                if attempt == 0 and safe_to_retry and status == "browser_closed":
                    self._discard_browser_objects()
                    continue
                return BrowserPageResult(
                    ok=False,
                    status=status,
                    action=action,
                    reason=reason,
                    retryable=retryable,
                    next_action=next_action,
                )
        return BrowserPageResult(
            ok=False,
            status="unavailable",
            action=action,
            reason="browser_page_runner_failed",
            retryable=True,
            next_action="navigate",
        )

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
    ) -> BrowserPageResult:
        try:
            page = self._ensure_page()
            self._bring_to_front(page)
            if action in {"navigate", "read_text"} and url:
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                self._bring_to_front(page)
                self._advance_page_revision()
            elif action in {"navigate"} and not url:
                return BrowserPageResult(ok=False, status="invalid_request", action=action, reason="url_required")

            current_url = str(getattr(page, "url", "") or "")
            if not current_url or current_url == "about:blank":
                return BrowserPageResult(
                    ok=False,
                    status="no_page",
                    action=action,
                    reason="browser_page_empty",
                    retryable=True,
                    next_action="navigate",
                )
            if action == "scroll":
                page.mouse.wheel(0, self._safe_scroll_delta(scroll_delta))
                self._brief_visual_pause(page)
                self._advance_page_revision()
            elif action in {"click", "fill", "press"}:
                control_result = self._run_control_action(
                    page,
                    action=action,
                    selector=selector,
                    ref=ref,
                    text=text,
                    key=key,
                    candidate_index=candidate_index,
                )
                if control_result is not None:
                    return control_result
                if self._page is not None:
                    page = self._page
                self._advance_page_revision()
                current_url = str(getattr(page, "url", "") or current_url)
            elif action == "screenshot":
                target = Path(str(screenshot_path or "")).resolve()
                if not str(screenshot_path or "").strip():
                    return BrowserPageResult(
                        ok=False,
                        status="invalid_request",
                        action=action,
                        reason="screenshot_path_required",
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(target), full_page=False)

            title = self._safe_title(page)
            snapshot_kind = "elements" if action == "elements" else "page"
            if action == "elements":
                captured = self._safe_element_summary(page, element_limit=element_limit)
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
            return BrowserPageResult(
                ok=True,
                status="executed" if action in {"click", "fill", "press", "screenshot"} else "available",
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
            )
        except Exception:
            raise

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
            self._context = self._context or self._browser.new_context()
            self._page = self._context.new_page()
        except Exception:
            # A visible browser window can be closed by the user while the host
            # still owns Python proxy objects. Drop the complete browser tree;
            # reusing only the old context poisons every later request.
            self._discard_browser_objects()
            self._playwright = self._playwright or sync_playwright().start()
            self._browser = self._launch_browser()
            self._context = self._browser.new_context()
            self._page = self._context.new_page()
        self._bring_to_front(self._page)
        return self._page

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

    def _safe_link_candidate_by_index(self, page: Any, *, candidate_index: int) -> dict[str, Any] | None:
        index = self._safe_int(candidate_index, default=0, minimum=0, maximum=30)
        if index <= 0:
            return None
        rows = self._safe_link_candidate_rows(page, max_items=max(12, index))
        if index > len(rows):
            return None
        return dict(rows[index - 1])

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
        return url

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
    ) -> BrowserPageResult | None:
        safe_selector = str(selector or "").strip()
        safe_ref = str(ref or "").strip()
        safe_candidate_index = self._safe_int(candidate_index, default=0, minimum=0, maximum=30)
        target_selector = f"aria-ref={safe_ref}" if safe_ref else safe_selector
        if action == "click" and safe_candidate_index > 0:
            candidate = self._safe_link_candidate_by_index(page, candidate_index=safe_candidate_index)
            if not candidate:
                return BrowserPageResult(
                    ok=False,
                    status="stale_target",
                    action=action,
                    reason="candidate_not_found",
                    retryable=True,
                    next_action="elements",
                )
            try:
                source_index = self._safe_int(candidate.get("source_index"), default=-1, minimum=-1, maximum=100_000)
                if source_index < 0:
                    raise RuntimeError("candidate_source_missing")
                locator = page.locator("a[href]").nth(source_index)
                self._click_locator_and_switch_to_popup_if_any(page, locator)
                return None
            except Exception as exc:
                status, reason, retryable, next_action = self._classify_browser_error(exc, action=action)
                return BrowserPageResult(
                    ok=False,
                    status=status,
                    action=action,
                    reason=reason or "candidate_click_failed",
                    retryable=retryable,
                    next_action=next_action,
                )
        if action in {"click", "fill"} and not target_selector:
            return BrowserPageResult(ok=False, status="invalid_request", action=action, reason="selector_required")
        try:
            if action == "click":
                self._click_locator_and_switch_to_popup_if_any(page, page.locator(target_selector).first)
            elif action == "fill":
                page.fill(target_selector, str(text or "")[:500], timeout=self.timeout_ms)
            elif action == "press":
                if target_selector:
                    page.press(target_selector, str(key or "Enter"), timeout=self.timeout_ms)
                else:
                    page.keyboard.press(str(key or "Enter"))
            else:
                return BrowserPageResult(ok=False, status="invalid_request", action=action, reason="unsupported_control_action")
            try:
                page.wait_for_load_state("domcontentloaded", timeout=1500)
            except Exception:
                pass
            return None
        except Exception as exc:
            status, reason, retryable, next_action = self._classify_browser_error(exc, action=action)
            return BrowserPageResult(
                ok=False,
                status=status,
                action=action,
                reason=reason or "browser_control_action_failed",
                retryable=retryable,
                next_action=next_action,
            )

    def _click_locator_and_switch_to_popup_if_any(self, page: Any, locator: Any) -> None:
        context = getattr(page, "context", None)
        if context is None:
            locator.click(timeout=self.timeout_ms)
            return
        before = list(getattr(context, "pages", []) or [])
        locator.click(timeout=self.timeout_ms)
        self._brief_visual_pause(page)
        after = list(getattr(context, "pages", []) or [])
        new_pages = [candidate for candidate in after if candidate not in before]
        new_page = new_pages[-1] if new_pages else None
        if new_page is None:
            self._page = page
            return
        self._page = new_page
        try:
            new_page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
        except Exception:
            pass
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
