from __future__ import annotations

import importlib.util
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urljoin


@dataclass(frozen=True)
class BrowserPageResult:
    ok: bool
    status: str
    action: str
    url: str = ""
    title: str = ""
    text: str = ""
    reason: str = ""


class ManagedBrowserPageRunner:
    """Small optional Playwright runner for Akane-owned browser page actions.

    Playwright is intentionally optional here. The capability can be catalogued
    and tested without pulling in browser binaries; real execution becomes
    available when the local runtime has Playwright installed. The default is a
    visible Akane-managed browser window so users can see browser actions.
    """

    def __init__(
        self,
        *,
        headless: bool = False,
        timeout_ms: int = 15000,
        browser_channel: str | None = None,
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

    def capability_status(self) -> dict[str, Any]:
        if not self.is_available():
            return {
                "enabled": False,
                "status": "missing_executor",
                "reason": "playwright_not_installed",
            }
        return {"enabled": True, "status": "ready", "reason": ""}

    def is_available(self) -> bool:
        return importlib.util.find_spec("playwright") is not None

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
            )
        )

    def shutdown(self) -> None:
        executor = self._executor
        if executor is None:
            self._close_objects()
            return
        try:
            executor.submit(self._close_objects).result(timeout=5)
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
    ) -> BrowserPageResult:
        try:
            page = self._ensure_page()
            self._bring_to_front(page)
            if action in {"navigate", "read_text"} and url:
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                self._bring_to_front(page)
            elif action in {"navigate"} and not url:
                return BrowserPageResult(ok=False, status="invalid_request", action=action, reason="url_required")

            current_url = str(getattr(page, "url", "") or "")
            if not current_url or current_url == "about:blank":
                return BrowserPageResult(ok=False, status="no_page", action=action, reason="browser_page_empty")
            if action == "scroll":
                page.mouse.wheel(0, self._safe_scroll_delta(scroll_delta))
                self._brief_visual_pause(page)
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
                current_url = str(getattr(page, "url", "") or current_url)

            title = self._safe_title(page)
            text = ""
            if action in {"navigate", "read_text", "current", "scroll", "snapshot", "click", "fill", "press"}:
                text = self._safe_page_snapshot(page, max_chars=max_chars)
            elif action == "elements":
                text = self._safe_element_summary(page, element_limit=element_limit)
            return BrowserPageResult(
                ok=True,
                status="executed" if action in {"click", "fill", "press"} else "available",
                action=action,
                url=current_url,
                title=title,
                text=text,
            )
        except Exception as exc:
            return BrowserPageResult(
                ok=False,
                status="unavailable",
                action=action,
                reason=str(exc)[:160] or "browser_page_runner_failed",
            )

    def _ensure_page(self) -> Any:
        if self._page is not None:
            try:
                if not self._page.is_closed():
                    return self._page
            except AttributeError:
                return self._page
        from playwright.sync_api import sync_playwright

        self._playwright = self._playwright or sync_playwright().start()
        if self._browser is None:
            self._browser = self._launch_browser()
        self._context = self._context or self._browser.new_context()
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
        limit = max(500, min(5000, int(max_chars or 3000)))
        return text.replace("\r\n", "\n").replace("\r", "\n").strip()[:limit]

    def _safe_page_snapshot(self, page: Any, *, max_chars: int) -> str:
        limit = max(500, min(5000, int(max_chars or 3000)))
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
        limit = max(500, min(5000, int(max_chars or 3000)))
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
                    for (const anchor of anchors) {
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

    def _safe_link_candidate_url_by_index(self, page: Any, *, candidate_index: int) -> str:
        index = self._safe_int(candidate_index, default=0, minimum=0, maximum=30)
        if index <= 0:
            return ""
        rows = self._safe_link_candidate_rows(page, max_items=max(12, index))
        if index > len(rows):
            return ""
        return str(rows[index - 1].get("url") or "").strip()

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
            candidate_url = self._safe_link_candidate_url_by_index(page, candidate_index=safe_candidate_index)
            if not candidate_url:
                return BrowserPageResult(
                    ok=False,
                    status="invalid_request",
                    action=action,
                    reason="candidate_not_found",
                )
            try:
                page.goto(candidate_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                self._bring_to_front(page)
                return None
            except Exception as exc:
                return BrowserPageResult(
                    ok=False,
                    status="unavailable",
                    action=action,
                    reason=str(exc)[:160] or "candidate_navigation_failed",
                )
        if action in {"click", "fill"} and not target_selector:
            return BrowserPageResult(ok=False, status="invalid_request", action=action, reason="selector_required")
        try:
            if action == "click":
                self._click_and_switch_to_popup_if_any(page, target_selector)
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
            return BrowserPageResult(
                ok=False,
                status="unavailable",
                action=action,
                reason=str(exc)[:160] or "browser_control_action_failed",
            )

    def _click_and_switch_to_popup_if_any(self, page: Any, selector: str) -> None:
        direct_url = self._safe_link_href_for_selector(page, selector)
        if direct_url:
            page.goto(direct_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            self._bring_to_front(page)
            return
        context = getattr(page, "context", None)
        if context is None:
            page.click(selector, timeout=self.timeout_ms)
            return
        try:
            with context.expect_page(timeout=2500) as page_info:
                page.click(selector, timeout=self.timeout_ms)
            new_page = page_info.value
        except Exception as exc:
            if "timeout" in type(exc).__name__.lower() or "timeout" in str(exc).lower():
                return
            raise
            return
        if new_page is None:
            return
        self._page = new_page
        try:
            new_page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
        except Exception:
            pass
        self._bring_to_front(new_page)

    def _safe_link_href_for_selector(self, page: Any, selector: str) -> str:
        try:
            locator = page.locator(selector).first
            href = str(locator.get_attribute("href", timeout=1500) or "").strip()
        except Exception:
            return ""
        return self._safe_public_candidate_url(href, base_url=str(getattr(page, "url", "") or ""))

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
