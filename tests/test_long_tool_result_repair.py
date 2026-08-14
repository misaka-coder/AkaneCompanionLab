"""Regression tests for defects found in the M68 long-result review."""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from companion_v01.capability_registry import (
    FOCUS_WORKSPACE_TOOL_SPEC,
    READ_WORKSPACE_TOOL_SPEC,
    REGISTER_WORKSPACE_ITEMS_TOOL_SPEC,
)
from companion_v01.paged_reading import slice_page
from companion_v01.store import MemoryStore
from companion_v01.tool_handlers.attachments import ReadAttachmentSectionToolHandler
from companion_v01.tool_handlers.core import ToolExecutionContext
from companion_v01.tool_handlers.workspace import (
    FocusWorkspaceToolHandler,
    ReadWorkspaceToolHandler,
    RegisterWorkspaceItemsToolHandler,
)
from companion_v01.workspace_files import WorkspaceFileService


def _context() -> ToolExecutionContext:
    return ToolExecutionContext(
        profile_user_id="p1",
        session_id="s1",
        character_pack_id="reimu",
        now_ts=1_700_000_000,
        visual_payload={},
    )


class _FakeIngest:
    def register_workspace_file(self, *, workspace_uri: str, **_kwargs):
        stem = Path(workspace_uri).stem
        return {
            "status": "registered",
            "item": {"status": "ready", "attachment_handle": f"file_{stem}"},
        }


class _FakeAttachmentService:
    def __init__(self, text: str) -> None:
        self.text = text

    def read_section(self, **_kwargs):
        return {
            "ok": True,
            "item": {"attachment_id": "file_001"},
            "content": self.text,
            "followup_context": "must be replaced by the paged model view",
        }


class LongToolResultRepairTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="akane_long_repair_")
        self.root = Path(self.temp.name)
        self.store = MemoryStore(self.root / "data")
        self.workspace = WorkspaceFileService(root_dir=self.root / "workspace", store=self.store)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_slice_page_does_not_manufacture_tail_for_short_final_line(self) -> None:
        text = "header\nrow\nlast line"
        page, offset, total = slice_page(text, start=0, budget_chars=50_000, budget_lines=2_000)
        self.assertEqual(page, text)
        self.assertEqual(offset, total)

    def test_read_workspace_no_longer_advertises_ignored_max_chars(self) -> None:
        properties = READ_WORKSPACE_TOOL_SPEC.input_schema["properties"]
        self.assertNotIn("max_chars", properties)
        handler = ReadWorkspaceToolHandler(workspace_service=self.workspace)
        self.assertNotIn("max_chars", handler.build_prompt_instruction())
        normalized = handler.normalize_call(
            {"type": "read_workspace", "targets": ["workspace:/Inbox/a.md"], "max_chars": 1000}
        )
        self.assertEqual(set(normalized or {}), {"type", "targets"})

    def test_focus_large_file_returns_read_workspace_continuation(self) -> None:
        target = self.root / "workspace" / "Inbox" / "large.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("line payload\n" * 10_000, encoding="utf-8")
        handler = FocusWorkspaceToolHandler(workspace_service=self.workspace)
        result = handler.execute(
            call={"type": "focus_workspace", "action": "add", "targets": ["workspace:/Inbox/large.md"]},
            context=_context(),
        )
        self.assertFalse(result.followup_envelope.complete)
        self.assertEqual(result.followup_envelope.continuation["type"], "read_workspace")
        self.assertLess(len(result.followup_context), 55_000)

    def test_register_batch_exposes_every_handle_across_pages(self) -> None:
        directory = self.root / "workspace" / "Many"
        directory.mkdir(parents=True)
        for index in range(180):
            (directory / f"item_{index:04d}_{'x' * 45}.txt").write_text("x", encoding="utf-8")
        handler = RegisterWorkspaceItemsToolHandler(
            workspace_service=self.workspace,
            attachment_ingest_service=_FakeIngest(),
        )
        call = handler.normalize_call(
            {"type": "register_workspace_items", "targets": ["workspace:/Many"], "max_files": 500}
        )
        seen: set[str] = set()
        pages = 0
        while call:
            result = handler.execute(call=call, context=_context())
            self.assertNotIn("未逐条展示回执", result.followup_context)
            seen.update(re.findall(r"-> (file_[^ ]+)", result.followup_context))
            pages += 1
            continuation = result.followup_envelope.continuation
            if continuation is None:
                self.assertTrue(result.followup_envelope.complete)
                break
            self.assertFalse(result.followup_envelope.complete)
            call = continuation
            self.assertLess(pages, 20)
        self.assertGreater(pages, 1)
        self.assertEqual(len(seen), 180)

    def test_attachment_section_pages_are_lossless_and_owner_bound(self) -> None:
        text = "\n".join(f"row {index} {'x' * 50}" for index in range(3_000))
        handler = ReadAttachmentSectionToolHandler(attachment_service=_FakeAttachmentService(text))
        call = {"type": "read_attachment_section", "target": "file_001", "section": "全文", "kind": "document"}
        seen = ""
        pages = 0
        while call:
            result = handler.execute(call=call, context=_context())
            # Extract exactly the source page between the stable wrapper lines.
            body = result.followup_context.split("本页内容如下：\n", 1)[1].split(
                "\n请基于这段展开内容", 1
            )[0]
            seen += body
            pages += 1
            continuation = result.followup_envelope.continuation
            if continuation is None:
                break
            call = continuation
            self.assertLess(pages, 20)
        self.assertEqual(seen, text)
        self.assertGreater(pages, 1)

    def test_native_workspace_specs_match_handler_contracts(self) -> None:
        focus = FOCUS_WORKSPACE_TOOL_SPEC.input_schema["properties"]
        register = REGISTER_WORKSPACE_ITEMS_TOOL_SPEC.input_schema["properties"]
        self.assertEqual(set(focus), {"action", "targets", "recursive"})
        self.assertEqual(set(register), {"targets", "recursive", "max_files", "cursor"})


if __name__ == "__main__":
    unittest.main()
