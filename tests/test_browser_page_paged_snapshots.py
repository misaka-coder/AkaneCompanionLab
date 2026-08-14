"""Phase 4: immutable browser snapshot paging (cache, cursors, real-action separation)."""

from __future__ import annotations

import unittest

from companion_v01.browser_page_runtime import BrowserPageResult, ManagedBrowserPageRunner
from companion_v01.tool_handlers.core import ToolExecutionContext
from companion_v01.tool_handlers.web_browser import BrowserPageToolHandler


def _context(profile_user_id: str = "p1", session_id: str = "s1") -> ToolExecutionContext:
    return ToolExecutionContext(
        profile_user_id=profile_user_id,
        session_id=session_id,
        now_ts=1_700_000_000,
        visual_payload={},
        client_mode="desktop_pet",
    )


class SnapshotCacheTests(unittest.TestCase):
    def test_store_and_read_snapshot_is_immutable(self) -> None:
        runner = ManagedBrowserPageRunner()
        record = runner.store_snapshot(kind="page", text="A" * 100_000, url="https://example.com", title="t")
        fetched = runner.read_snapshot(record["snapshot_id"])
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["text"], "A" * 100_000)
        record["text"] = "tampered"
        again = runner.read_snapshot(record["snapshot_id"])
        self.assertEqual(again["text"], "A" * 100_000)

    def test_snapshot_expires_after_ttl(self) -> None:
        now = [1000.0]

        class ClockRunner(ManagedBrowserPageRunner):
            pass

        runner = ClockRunner(now=lambda: now[0])
        record = runner.store_snapshot(kind="page", text="x" * 1000)
        now[0] = 1000.0 + runner._snapshot_ttl_seconds + 1
        self.assertIsNone(runner.read_snapshot(record["snapshot_id"]))

    def test_snapshot_cache_evicts_oldest_when_full(self) -> None:
        runner = ManagedBrowserPageRunner(snapshot_max_entries=3)
        first = runner.store_snapshot(kind="page", text="1" * 1000)
        runner.store_snapshot(kind="page", text="2" * 1000)
        runner.store_snapshot(kind="page", text="3" * 1000)
        runner.store_snapshot(kind="page", text="4" * 1000)
        self.assertIsNone(runner.read_snapshot(first["snapshot_id"]))

    def test_close_objects_clears_cache(self) -> None:
        runner = ManagedBrowserPageRunner()
        record = runner.store_snapshot(kind="page", text="x" * 1000)
        runner._close_objects()
        self.assertIsNone(runner.read_snapshot(record["snapshot_id"]))

    def test_missing_snapshot_returns_none(self) -> None:
        runner = ManagedBrowserPageRunner()
        self.assertIsNone(runner.read_snapshot("snap_nonexistent"))


class FakePagedRunner:
    """Stand-in runner exercising the handler path without Playwright."""

    def __init__(self, snapshot_text: str) -> None:
        self.snapshot_text = snapshot_text
        self.run_calls: list[dict] = []
        self.read_calls: list[str] = []
        self.live = True
        self._cache: dict[str, dict] = {}

    def run(self, **kwargs) -> BrowserPageResult:
        self.run_calls.append(dict(kwargs))
        from companion_v01.paged_reading import FIRST_PAGE_BUDGET_CHARS, FIRST_PAGE_BUDGET_LINES, slice_page

        page_text, next_offset, total = slice_page(
            self.snapshot_text,
            start=0,
            budget_chars=FIRST_PAGE_BUDGET_CHARS,
            budget_lines=FIRST_PAGE_BUDGET_LINES,
        )
        snapshot_id = f"snap_fake_{len(self.run_calls)}"
        self._cache[snapshot_id] = {
            "snapshot_id": snapshot_id,
            "kind": "elements" if kwargs.get("action") == "elements" else "page",
            "text": self.snapshot_text,
            "url": "https://example.com/article",
            "title": "Article",
            "revision": f"r{len(self.run_calls)}",
            "created_at": 0.0,
        }
        return BrowserPageResult(
            ok=True,
            status="available",
            action=str(kwargs.get("action") or ""),
            url="https://example.com/article",
            title="Article",
            text=page_text,
            snapshot_id=snapshot_id,
            complete=next_offset >= total,
            next_cursor=f"{snapshot_id}:{next_offset}" if next_offset < total else "",
            shown_chars=len(page_text),
            total_chars=total,
            page_revision=f"r{len(self.run_calls)}",
        )

    def read_snapshot(self, snapshot_id: str):
        self.read_calls.append(str(snapshot_id or ""))
        return self._cache.get(snapshot_id)

    def has_live_page(self) -> bool:
        return self.live


class BrowserPagePagedTests(unittest.TestCase):
    def _make_snapshot_text(self, lines: int = 2500) -> str:
        return "\n".join(f"{index}. 元素或正文行 内容-{'字'*40}" for index in range(lines)) + "\n"

    def test_snapshot_pages_are_lossless_and_never_retrigger_actions(self) -> None:
        text = self._make_snapshot_text()
        self.assertGreater(len(text), 50_000)
        runner = FakePagedRunner(text)
        handler = BrowserPageToolHandler(browser_runner=runner)

        first = handler.execute(call={"type": "browser_page", "action": "snapshot"}, context=_context())
        envelope = first.followup_envelope
        self.assertTrue(envelope.producer_bounded)
        self.assertFalse(envelope.complete)
        self.assertEqual(runner.run_calls[-1]["action"], "snapshot")
        joined = self._page_body(first.followup_context)
        cursor = envelope.continuation["cursor"]
        while cursor:
            continuation = handler.execute(call={"type": "browser_page", "cursor": cursor}, context=_context())
            joined += self._page_body(continuation.followup_context)
            next_cont = continuation.followup_envelope.continuation
            cursor = next_cont["cursor"] if next_cont else None
        self.assertEqual(joined, text)
        self.assertEqual(len(runner.run_calls), 1)
        self.assertGreater(len(runner.read_calls), 0)

    @staticmethod
    def _page_body(followup: str) -> str:
        marker = "页面状态快照：\n"
        position = followup.find(marker)
        if position == -1:
            return ""
        body = followup[position + len(marker):]
        for tail in (
            "\n请只基于这份公开页面状态回答",
            "\n下一步提示",
            "\n页面已在 Akane",
            "\n本页之后还有未展示的快照内容",
            "\n已读完这份快照",
            "\n同时已请求桌宠",
        ):
            cut = body.find(tail)
            if cut != -1:
                body = body[:cut]
        return body

    def test_cursor_rejected_cross_session(self) -> None:
        runner = FakePagedRunner(self._make_snapshot_text())
        handler = BrowserPageToolHandler(browser_runner=runner)
        first = handler.execute(call={"type": "browser_page", "action": "snapshot"}, context=_context())
        cursor = first.followup_envelope.continuation["cursor"]
        foreign = handler.execute(
            call={"type": "browser_page", "cursor": cursor}, context=_context(session_id="other")
        )
        self.assertEqual(foreign.followup_envelope.diagnostics["status"], "cursor_invalid")

    def test_snapshot_expired_and_page_closed_are_structured(self) -> None:
        text = self._make_snapshot_text(2500)
        runner = FakePagedRunner(text)
        handler = BrowserPageToolHandler(browser_runner=runner)
        first = handler.execute(call={"type": "browser_page", "action": "snapshot"}, context=_context())
        cursor = first.followup_envelope.continuation["cursor"]
        runner._cache.clear()
        expired = handler.execute(call={"type": "browser_page", "cursor": cursor}, context=_context())
        self.assertEqual(expired.followup_envelope.diagnostics["status"], "snapshot_expired")
        runner.live = False
        closed = handler.execute(call={"type": "browser_page", "cursor": cursor}, context=_context())
        self.assertEqual(closed.followup_envelope.diagnostics["status"], "page_closed")

    def test_scroll_creates_new_snapshot_and_revision(self) -> None:
        runner = FakePagedRunner(self._make_snapshot_text(100))
        handler = BrowserPageToolHandler(browser_runner=runner)
        first = handler.execute(call={"type": "browser_page", "action": "snapshot"}, context=_context())
        scrolled = handler.execute(
            call={"type": "browser_page", "action": "scroll", "scroll_delta": 800}, context=_context()
        )
        self.assertNotEqual(
            first.followup_envelope.diagnostics["snapshot_id"],
            scrolled.followup_envelope.diagnostics["snapshot_id"],
        )
        self.assertEqual(runner.run_calls[0]["action"], "snapshot")
        self.assertEqual(runner.run_calls[1]["action"], "scroll")

    def test_elements_snapshot_pages_by_complete_lines(self) -> None:
        runner = FakePagedRunner(self._make_snapshot_text(2000))
        handler = BrowserPageToolHandler(browser_runner=runner)
        first = handler.execute(
            call={"type": "browser_page", "action": "elements", "element_limit": 40}, context=_context()
        )
        envelope = first.followup_envelope
        self.assertIsNotNone(envelope)
        self.assertIn("元素摘要：", first.followup_context)

    def test_short_snapshot_is_complete_without_cursor(self) -> None:
        runner = FakePagedRunner("短页面")
        handler = BrowserPageToolHandler(browser_runner=runner)
        first = handler.execute(call={"type": "browser_page", "action": "current"}, context=_context())
        self.assertTrue(first.followup_envelope.complete)
        self.assertIsNone(first.followup_envelope.continuation)
        self.assertNotIn("已截断", first.followup_context)


if __name__ == "__main__":
    unittest.main()
