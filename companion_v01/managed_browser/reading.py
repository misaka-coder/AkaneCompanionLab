"""Immutable snapshot paging and visible page text."""
from __future__ import annotations

import re
import uuid
from typing import Any


class BrowserReadingMixin:
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
