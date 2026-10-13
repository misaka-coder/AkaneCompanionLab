"""Visible link candidates, URL boundaries, and element summaries."""
from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urljoin, urlparse


class BrowserCandidatesMixin:
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
            parsed = urlparse(url)
            hostname = (parsed.hostname or "").strip().lower().strip("[]")
            # Accessing port also validates malformed and out-of-range values.
            parsed.port
        except Exception:
            return ""
        lowered = url.lower()
        if not (lowered.startswith("http://") or lowered.startswith("https://")):
            return ""
        if any(marker in lowered for marker in ("api_key=", "password=", "secret=", "token=")):
            return ""
        if not hostname or parsed.username is not None or parsed.password is not None:
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
                if href and not self._safe_public_candidate_url(href, base_url=str(getattr(page, "url", "") or "")):
                    href = ""
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
