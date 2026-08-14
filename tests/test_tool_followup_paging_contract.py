"""Producer-owned paging contract: shared envelope, slicer and cursor tests."""

from __future__ import annotations

import unittest

from companion_v01.paged_reading import (
    CURSOR_VERSION,
    cursor_binding,
    file_offset_payload,
    make_paged_cursor,
    offset_payload,
    page_complete_lines,
    page_failure_feedback,
    page_progress_lines,
    parse_file_offset_payload,
    parse_paged_cursor,
    slice_page,
)
from companion_v01.tool_handlers.core import ToolFollowupEnvelope
from companion_v01.tool_orchestration_engine import shape_tool_followup


class ShapeToolFollowupContractTests(unittest.TestCase):
    def test_producer_bounded_complete_result_passes_8000_unchanged(self) -> None:
        content = "头\n" + "\n".join(f"第{i}行" + "字" * 40 for i in range(1500))
        self.assertGreater(len(content), 8000)
        envelope = ToolFollowupEnvelope(content=content, producer_bounded=True, complete=True)
        self.assertEqual(shape_tool_followup(envelope, tool_type="read_workspace"), content)

    def test_producer_bounded_incomplete_with_continuation_passes_unchanged(self) -> None:
        content = "头\n" + "\n".join(f"第{i}行" + "字" * 40 for i in range(1500))
        envelope = ToolFollowupEnvelope(
            content=content,
            producer_bounded=True,
            complete=False,
            continuation={"type": "read_workspace", "cursor": "p1.ws.abc.0:100"},
        )
        self.assertEqual(shape_tool_followup(envelope, tool_type="read_workspace"), content)

    def test_producer_bounded_incomplete_without_continuation_is_marked_incomplete(self) -> None:
        envelope = ToolFollowupEnvelope(content="半页内容", producer_bounded=True, complete=False, continuation=None)
        shaped = shape_tool_followup(envelope, tool_type="read_workspace")
        self.assertIn("没有提供可执行 continuation", shaped)
        self.assertNotIn("已截断", shaped)

    def test_complete_with_continuation_is_normalized_to_complete_page(self) -> None:
        envelope = ToolFollowupEnvelope(
            content="完整页",
            producer_bounded=True,
            complete=True,
            continuation={"type": "read_workspace", "cursor": "p1.ws.abc.0:100"},
        )
        self.assertEqual(shape_tool_followup(envelope, tool_type="read_workspace"), "完整页")

    def test_unmigrated_oversize_result_still_hits_8000_insurance(self) -> None:
        content = "行\n" + "\n".join(f"第{i}行" + "长" * 40 for i in range(2000))
        shaped = shape_tool_followup(content, tool_type="list_workspace")
        self.assertLess(len(shaped), 9000)
        self.assertIn("已截断", shaped)

    def test_empty_success_has_stable_placeholder(self) -> None:
        shaped = shape_tool_followup("", tool_type="send_file")
        self.assertTrue(shaped.strip())
        self.assertIn("send_file", shaped)

    def test_plain_short_result_is_untouched(self) -> None:
        shaped = shape_tool_followup("短结果", tool_type="send_file")
        self.assertEqual(shaped, "短结果")


class SlicePageTests(unittest.TestCase):
    def _build_text(self, chars_per_line: int = 30, line_count: int = 4000) -> str:
        return "".join(f"第{i:05d}行-" + "字" * chars_per_line + "\n" for i in range(line_count))

    def test_continuous_pages_reproduce_text_without_loss_or_overlap(self) -> None:
        text = self._build_text()
        start = 0
        pages: list[str] = []
        while start < len(text):
            page, start, total = slice_page(text, start=start, budget_chars=50_000, budget_lines=2_000)
            pages.append(page)
            self.assertGreater(len(page), 0)
        self.assertEqual("".join(pages), text)

    def test_first_and_next_page_budgets_are_respected(self) -> None:
        text = self._build_text()
        page, _next, _total = slice_page(text, start=0, budget_chars=50_000, budget_lines=2_000)
        self.assertLessEqual(len(page), 50_000)
        continuation, _next, _total = slice_page(text, start=_next, budget_chars=32_000, budget_lines=1_000)
        self.assertLessEqual(len(continuation), 32_000)

    def test_pages_cut_at_line_boundaries(self) -> None:
        text = self._build_text()
        page, next_offset, _total = slice_page(text, start=0, budget_chars=10_000, budget_lines=500)
        self.assertTrue(page.endswith("\n"))
        self.assertEqual(text[next_offset - 1], "\n")

    def test_monster_single_line_falls_back_to_char_cut_without_losing_text(self) -> None:
        text = "字" * 100_000
        start = 0
        joined = ""
        while start < len(text):
            page, start, _total = slice_page(text, start=start, budget_chars=40_000, budget_lines=1_000)
            joined += page
        self.assertEqual(joined, text)

    def test_chinese_emoji_and_crlf_survive_paging(self) -> None:
        text = "头🎉👩‍💻\r\n" + "\r\n".join(f"行{i}-😀你好世界-{i}" for i in range(3000))
        start = 0
        joined = ""
        while start < len(text):
            page, start, _total = slice_page(text, start=start, budget_chars=12_000, budget_lines=500)
            joined += page
        self.assertEqual(joined, text)

    def test_line_budget_wins_when_earlier_than_char_budget(self) -> None:
        text = self._build_text(chars_per_line=10, line_count=3000)
        page, _next, _total = slice_page(text, start=0, budget_chars=50_000, budget_lines=1_000)
        self.assertLessEqual(len(page.splitlines()), 1_000)

    def test_empty_and_exhausted_inputs(self) -> None:
        self.assertEqual(slice_page("", start=0), ("", 0, 0))
        page, next_offset, total = slice_page("abc", start=3)
        self.assertEqual((page, next_offset, total), ("", 3, 3))


class PagedCursorTests(unittest.TestCase):
    def test_roundtrip_with_file_offset_payload(self) -> None:
        binding = cursor_binding("read_workspace", "p1\x1fs1", "workspace:/Inbox/a.md", "sha:abc")
        cursor = make_paged_cursor(tool="ws", binding=binding, payload=file_offset_payload(file_index=2, offset=12345))
        self.assertTrue(cursor.startswith(f"{CURSOR_VERSION}.ws."))
        payload = parse_paged_cursor(cursor, tool="ws", binding=binding)
        self.assertIsNotNone(payload)
        self.assertEqual(parse_file_offset_payload(payload or ""), (2, 12345))

    def test_wrong_tool_is_rejected(self) -> None:
        binding = cursor_binding("a", "b")
        cursor = make_paged_cursor(tool="ws", binding=binding, payload=offset_payload(offset=0))
        self.assertIsNone(parse_paged_cursor(cursor, tool="we", binding=binding))

    def test_owner_or_fingerprint_change_is_rejected(self) -> None:
        binding = cursor_binding("read_workspace", "p1\x1fs1", "workspace:/a.md", "fp1")
        cursor = make_paged_cursor(tool="ws", binding=binding, payload=offset_payload(offset=10))
        self.assertIsNone(
            parse_paged_cursor(cursor, tool="ws", binding=cursor_binding("read_workspace", "p1\x1fs2", "workspace:/a.md", "fp1"))
        )
        self.assertIsNone(
            parse_paged_cursor(cursor, tool="ws", binding=cursor_binding("read_workspace", "p1\x1fs1", "workspace:/a.md", "fp2"))
        )

    def test_garbage_and_tampered_cursors_are_rejected(self) -> None:
        binding = cursor_binding("x")
        self.assertIsNone(parse_paged_cursor("", tool="ws", binding=binding))
        self.assertIsNone(parse_paged_cursor("p1.ws.zzz.00", tool="ws", binding=binding))
        self.assertIsNone(parse_paged_cursor("p1.ws.zz", tool="ws", binding=binding))
        cursor = make_paged_cursor(tool="ws", binding=binding, payload=offset_payload(offset=1))
        self.assertIsNone(parse_paged_cursor(cursor[:-1] + "z", tool="ws", binding=binding))

    def test_payload_parsing_rejects_malformed_values(self) -> None:
        self.assertEqual(parse_file_offset_payload(""), (0, 0))
        self.assertEqual(parse_file_offset_payload("abc"), (0, 0))
        self.assertEqual(parse_file_offset_payload("-1:-5"), (0, 0))
        self.assertEqual(parse_file_offset_payload("3:400"), (3, 400))


class PageFeedbackTextTests(unittest.TestCase):
    def test_progress_lines_tell_model_answer_ok_and_show_continuation(self) -> None:
        lines = page_progress_lines(
            shown_lines=2000,
            shown_chars=50_000,
            total_chars=100_000,
            next_call_hint='read_workspace(cursor="p1.ws.abc.0:50000")',
        )
        joined = "\n".join(lines)
        self.assertIn("可以直接回答", joined)
        self.assertIn("read_workspace(cursor=", joined)
        self.assertIn("100000", joined)

    def test_complete_lines_report_full_coverage(self) -> None:
        joined = "\n".join(page_complete_lines(shown_lines=10, shown_chars=500, total_chars=500))
        self.assertIn("已完整读取", joined)

    def test_failure_feedback_never_claims_unread_content(self) -> None:
        joined = page_failure_feedback(status="stale_cursor", tool="read_workspace", detail="file changed")
        self.assertIn("stale_cursor", joined)
        self.assertIn("不要声称已经读到未返回的后续内容", joined)
        self.assertNotIn("localhost", joined)


if __name__ == "__main__":
    unittest.main()
