"""Phase 2: paged workspace/file reading contract (read_workspace / list_workspace)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from companion_v01.store import MemoryStore
from companion_v01.tool_handlers.core import ToolExecutionContext
from companion_v01.tool_handlers.workspace import ListWorkspaceToolHandler, ReadWorkspaceToolHandler
from companion_v01.workspace_files import WorkspaceFileService


def _context(profile_user_id: str = "p1", session_id: str = "s1") -> ToolExecutionContext:
    return ToolExecutionContext(
        profile_user_id=profile_user_id,
        session_id=session_id,
        now_ts=1_700_000_000,
        visual_payload={},
    )


class WorkspacePagedReadingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="akane_ws_page_"))
        self.base_dir = self.tmp / "data"
        self.store = MemoryStore(str(self.base_dir))
        self.service = WorkspaceFileService(root_dir=self.tmp / "ws", store=self.store)
        self.handler = ReadWorkspaceToolHandler(workspace_service=self.service)
        self.inbox = self.tmp / "ws" / "Inbox"
        self.inbox.mkdir(parents=True, exist_ok=True)

    def _write(self, relative: str, text: str) -> None:
        target = self.tmp / "ws" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))

    def test_100k_file_pages_losslessly_without_overlap(self) -> None:
        text = "头🎉\r\n" + "\r\n".join(f"第{i}行 内容-{'长'*40}" for i in range(2200))
        self.assertGreater(len(text), 100_000)
        self._write("Inbox/report.md", text)

        first = self.handler.execute(call={"type": "read_workspace", "targets": ["workspace:/Inbox/report.md"]}, context=_context())
        envelope = first.followup_envelope
        self.assertIsNotNone(envelope)
        self.assertTrue(envelope.producer_bounded)
        self.assertFalse(envelope.complete)
        self.assertIsNotNone(envelope.continuation)

        joined = ""
        cursor: str | None = None
        rounds = 0
        while True:
            if cursor is None:
                result = first
            else:
                result = self.handler.execute(
                    call={"type": "read_workspace", "cursor": cursor}, context=_context()
                )
                self.assertIsNotNone(result.followup_envelope)
            service_page = self.service.read_items_paged(
                profile_user_id="p1",
                session_id="s1",
                targets=["workspace:/Inbox/report.md"] if cursor is None else [],
                cursor=cursor,
            )
            self.assertEqual(service_page["status"], "ok")
            for item in service_page["items"]:
                chunk = str(item.get("content") or "")
                self.assertIn(chunk, str(result.followup_context or ""))
                joined += chunk
            next_cont = result.followup_envelope.continuation
            cursor = next_cont["cursor"] if next_cont else None
            rounds += 1
            if cursor is None:
                break
            self.assertLess(rounds, 30)
        self.assertTrue(rounds > 1)
        self.assertEqual(joined, text)
        self.assertTrue(result.followup_envelope.complete)
        self.assertIsNone(result.followup_envelope.continuation)

    def test_cross_session_cursor_is_rejected(self) -> None:
        self._write("Inbox/a.md", "x" * 60_000)
        first = self.handler.execute(call={"type": "read_workspace", "targets": ["workspace:/Inbox/a.md"]}, context=_context())
        cursor = first.followup_envelope.continuation["cursor"]
        foreign = self.handler.execute(
            call={"type": "read_workspace", "cursor": cursor},
            context=_context(session_id="other-session"),
        )
        self.assertEqual(foreign.followup_envelope.diagnostics["status"], "cursor_invalid")
        self.assertIn("cursor_invalid", str(foreign.followup_context))

    def test_changed_file_returns_stale_cursor(self) -> None:
        self._write("Inbox/a.md", "A" * 80_000)
        first = self.handler.execute(call={"type": "read_workspace", "targets": ["workspace:/Inbox/a.md"]}, context=_context())
        cursor = first.followup_envelope.continuation["cursor"]
        self._write("Inbox/a.md", "B" * 80_000)
        continuation = self.handler.execute(call={"type": "read_workspace", "cursor": cursor}, context=_context())
        self.assertEqual(continuation.followup_envelope.diagnostics["status"], "stale_cursor")
        self.assertIn("stale_cursor", str(continuation.followup_context))

    def test_changed_content_with_identical_stat_metadata_is_still_stale(self) -> None:
        import os

        self._write("Inbox/a.md", "A" * 80_000)
        first = self.handler.execute(call={"type": "read_workspace", "targets": ["workspace:/Inbox/a.md"]}, context=_context())
        cursor = first.followup_envelope.continuation["cursor"]
        target = self.tmp / "ws" / "Inbox" / "a.md"
        stat = target.stat()
        target.write_bytes(("B" * 80_000).encode("utf-8"))
        os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        continuation = self.handler.execute(call={"type": "read_workspace", "cursor": cursor}, context=_context())
        self.assertEqual(continuation.followup_envelope.diagnostics["status"], "stale_cursor")

    def test_deleted_file_returns_source_missing(self) -> None:
        self._write("Inbox/a.md", "A" * 80_000)
        first = self.handler.execute(call={"type": "read_workspace", "targets": ["workspace:/Inbox/a.md"]}, context=_context())
        cursor = first.followup_envelope.continuation["cursor"]
        (self.tmp / "ws" / "Inbox" / "a.md").unlink()
        continuation = self.handler.execute(call={"type": "read_workspace", "cursor": cursor}, context=_context())
        self.assertEqual(continuation.followup_envelope.diagnostics["status"], "source_missing")

    def test_multi_file_order_is_stable_and_complete(self) -> None:
        self._write("Inbox/a.md", "A" * 70_000)
        self._write("Inbox/b.md", "B" * 70_000)
        seen_uris: list[str] = []
        first = self.handler.execute(
            call={"type": "read_workspace", "targets": ["workspace:/Inbox/a.md", "workspace:/Inbox/b.md"]},
            context=_context(),
        )
        seen_uris.extend(self._uris(first))
        cursor = first.followup_envelope.continuation["cursor"]
        while cursor:
            continuation = self.handler.execute(call={"type": "read_workspace", "cursor": cursor}, context=_context())
            seen_uris.extend(self._uris(continuation))
            next_cont = continuation.followup_envelope.continuation
            cursor = next_cont["cursor"] if next_cont else None
        self.assertIn("workspace:/Inbox/a.md", seen_uris)
        self.assertIn("workspace:/Inbox/b.md", seen_uris)
        first_b = next((index for index, uri in enumerate(seen_uris) if uri.endswith("b.md")), None)
        last_a = max(index for index, uri in enumerate(seen_uris) if uri.endswith("a.md"))
        self.assertLess(last_a, first_b)

    @staticmethod
    def _uris(result) -> list[str]:
        for event in list(getattr(result, "stream_events", None) or []):
            if str(event.get("type") or "") == "workspace_items_read":
                return [str(item.get("uri") or "") for item in event.get("items") or []]
        import re

        return re.findall(r"### (workspace:/[^\n]+)", str(result.followup_context or ""))

    def test_small_file_is_complete_on_first_page(self) -> None:
        self._write("Inbox/small.md", "只有一小段。\n" * 10)
        first = self.handler.execute(call={"type": "read_workspace", "targets": ["workspace:/Inbox/small.md"]}, context=_context())
        self.assertTrue(first.followup_envelope.complete)
        self.assertIsNone(first.followup_envelope.continuation)
        self.assertIn("只有一小段", str(first.followup_context))

    def test_envelope_bypasses_8000_insurance(self) -> None:
        text = "\n".join(f"第{i}行" + "长" * 30 for i in range(700))
        self.assertGreater(len(text), 8000)
        self._write("Inbox/long.md", text)
        first = self.handler.execute(call={"type": "read_workspace", "targets": ["workspace:/Inbox/long.md"]}, context=_context())
        self.assertNotIn("已截断", str(first.followup_context))

    def test_cursor_call_ignores_conflicting_targets(self) -> None:
        self._write("Inbox/a.md", "A" * 60_000)
        first = self.handler.execute(call={"type": "read_workspace", "targets": ["workspace:/Inbox/a.md"]}, context=_context())
        cursor = first.followup_envelope.continuation["cursor"]
        normalized = self.handler.normalize_call(
            {"type": "read_workspace", "cursor": cursor, "targets": ["workspace:/Inbox/other.md"]}
        )
        self.assertEqual(set(normalized.keys()), {"type", "cursor"})

    def test_failure_pages_are_producer_bounded_and_complete(self) -> None:
        first = self.handler.execute(call={"type": "read_workspace", "cursor": "p1.ws.deadbeefdeadbeef.303a30"}, context=_context())
        envelope = first.followup_envelope
        self.assertTrue(envelope.producer_bounded)
        self.assertTrue(envelope.complete)
        self.assertIsNone(envelope.continuation)


class ListWorkspacePagedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="akane_ws_list_"))
        self.store = MemoryStore(str(self.tmp / "data"))
        self.service = WorkspaceFileService(root_dir=self.tmp / "ws", store=self.store)
        self.handler = ListWorkspaceToolHandler(workspace_service=self.service)
        many = self.tmp / "ws" / "Many"
        many.mkdir(parents=True)
        for index in range(600):
            (many / f"file_{index:04d}.txt").write_text("x", encoding="utf-8")

    def test_listing_pages_by_complete_entries(self) -> None:
        first = self.handler.execute(
            call={"type": "list_workspace", "paths": ["workspace:/Many"], "depth": 1},
            context=_context(),
        )
        envelope = first.followup_envelope
        self.assertFalse(envelope.complete)
        self.assertIsNotNone(envelope.continuation)
        shown_first = envelope.diagnostics["shown_entries"]
        cursor = envelope.continuation["cursor"]
        seen = shown_first
        while cursor:
            continuation = self.handler.execute(call={"type": "list_workspace", "cursor": cursor}, context=_context())
            next_cont = continuation.followup_envelope.continuation
            seen = continuation.followup_envelope.diagnostics["shown_entries"]
            cursor = next_cont["cursor"] if next_cont else None
        self.assertEqual(seen, 600)
        self.assertEqual(first.followup_envelope.diagnostics["total_entries"], 600)

    def test_listing_cursor_rejected_cross_session(self) -> None:
        first = self.handler.execute(
            call={"type": "list_workspace", "paths": ["workspace:/Many"]}, context=_context()
        )
        cursor = first.followup_envelope.continuation["cursor"]
        foreign = self.handler.execute(call={"type": "list_workspace", "cursor": cursor}, context=_context(session_id="s9"))
        self.assertEqual(foreign.followup_envelope.diagnostics["status"], "cursor_invalid")


if __name__ == "__main__":
    unittest.main()
