from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from companion_v01.attachment_inbox import AttachmentInboxService
from companion_v01.store import MemoryStore
from companion_v01.tool_runtime import (
    ClearAttachmentFocusToolHandler,
    InspectAttachmentToolHandler,
    ReadAttachmentSectionToolHandler,
    RetryAttachmentToolHandler,
    SyncAttachmentWorkspaceToolHandler,
    ToolExecutionContext,
)


class AttachmentInboxTests(unittest.TestCase):
    def test_store_roundtrip_and_clear_latest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            first = store.add_attachment_inbox_item(
                profile_user_id="user",
                session_id="session",
                source="qq",
                kind="image",
                origin_name="dinner.jpg",
                timestamp=100,
            )
            second = store.add_attachment_inbox_item(
                profile_user_id="user",
                session_id="session",
                source="qq",
                kind="document",
                origin_name="notes.txt",
                timestamp=110,
            )
            self.assertEqual(first["attachment_handle"], "img_001")
            self.assertEqual(first["sequence_no"], 1)
            self.assertEqual(second["attachment_handle"], "file_001")
            self.assertEqual(second["sequence_no"], 1)

            updated = store.update_attachment_inbox_item(
                profile_user_id="user",
                session_id="session",
                attachment_id=first["attachment_id"],
                status="ready",
                summary_title="晚餐照片",
                short_hint="桌上有米饭、青菜和热汤。",
                detail={"mood_tags": ["日常", "温暖"]},
                updated_at=120,
            )

            self.assertIsNotNone(updated)
            self.assertEqual(updated["status"], "ready")
            self.assertEqual(updated["detail"]["mood_tags"], ["日常", "温暖"])

            listed = store.list_attachment_inbox_items(
                profile_user_id="user",
                session_id="session",
                statuses=["ready", "pending_observation"],
                limit=10,
            )
            self.assertEqual([item["attachment_id"] for item in listed], [first["attachment_id"], second["attachment_id"]])

            cleared = store.clear_attachment_inbox_items(
                profile_user_id="user",
                session_id="session",
                target="latest",
                timestamp=130,
            )
            self.assertEqual(len(cleared), 1)
            self.assertEqual(cleared[0]["attachment_id"], first["attachment_id"])
            remaining = store.list_attachment_inbox_items(
                profile_user_id="user",
                session_id="session",
                statuses=["ready", "pending_observation"],
                limit=10,
            )
            self.assertEqual([item["attachment_id"] for item in remaining], [second["attachment_id"]])
            third = store.add_attachment_inbox_item(
                profile_user_id="user",
                session_id="session",
                source="qq",
                kind="image",
                origin_name="next.jpg",
                timestamp=140,
            )
            self.assertEqual(third["attachment_handle"], "img_002")
            self.assertEqual(third["sequence_no"], 2)

    def test_store_retries_attachment_handle_allocation_on_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            store.add_attachment_inbox_item(
                profile_user_id="user",
                session_id="session",
                source="qq",
                kind="image",
                origin_name="first.png",
                timestamp=100,
            )

            with patch.object(store, "_next_attachment_sequence_no", return_value=1):
                created = store.add_attachment_inbox_item(
                    profile_user_id="user",
                    session_id="session",
                    source="qq",
                    kind="image",
                    origin_name="second.png",
                    timestamp=110,
                )

            self.assertEqual(created["attachment_handle"], "img_002")
            self.assertEqual(created["sequence_no"], 2)

    def test_store_find_attachment_prefers_exact_handle_over_fuzzy_title(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            exact = store.add_attachment_inbox_item(
                profile_user_id="user",
                session_id="session",
                source="qq",
                kind="document",
                origin_name="voice.wav",
                timestamp=100,
            )
            store.update_attachment_inbox_item(
                profile_user_id="user",
                session_id="session",
                attachment_id=exact["attachment_id"],
                status="ready",
                summary_title="原始人声",
                short_hint="第一份附件。",
                updated_at=110,
            )
            fuzzy = store.add_attachment_inbox_item(
                profile_user_id="user",
                session_id="session",
                source="qq",
                kind="document",
                origin_name="notes.txt",
                timestamp=120,
            )
            store.update_attachment_inbox_item(
                profile_user_id="user",
                session_id="session",
                attachment_id=fuzzy["attachment_id"],
                status="ready",
                summary_title="关于 file_001 的处理说明",
                short_hint="第二份附件。",
                updated_at=130,
            )

            found = store.find_attachment_inbox_item(
                profile_user_id="user",
                session_id="session",
                query="file_001",
                statuses=["ready"],
            )

            self.assertIsNotNone(found)
            self.assertEqual(found["attachment_id"], exact["attachment_id"])
            self.assertEqual(found["attachment_handle"], "file_001")

    def test_store_find_attachment_matches_exposes_ambiguous_fuzzy_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            first = store.add_attachment_inbox_item(
                profile_user_id="user",
                session_id="session",
                source="qq",
                kind="image",
                origin_name="menu-breakfast.png",
                timestamp=100,
            )
            second = store.add_attachment_inbox_item(
                profile_user_id="user",
                session_id="session",
                source="qq",
                kind="image",
                origin_name="menu-dinner.png",
                timestamp=110,
            )
            store.update_attachment_inbox_item(
                profile_user_id="user",
                session_id="session",
                attachment_id=first["attachment_id"],
                status="ready",
                summary_title="早餐菜单图",
                short_hint="上面有咖啡和吐司。",
                updated_at=120,
            )
            store.update_attachment_inbox_item(
                profile_user_id="user",
                session_id="session",
                attachment_id=second["attachment_id"],
                status="ready",
                summary_title="晚餐菜单图",
                short_hint="上面有牛排和汤。",
                updated_at=130,
            )

            found = store.find_attachment_inbox_item(
                profile_user_id="user",
                session_id="session",
                query="菜单图",
                kind="image",
                statuses=["ready"],
            )
            matches = store.find_attachment_inbox_item_matches(
                profile_user_id="user",
                session_id="session",
                query="菜单图",
                kind="image",
                statuses=["ready"],
            )

            self.assertIsNone(found)
            self.assertEqual([item["attachment_handle"] for item in matches[:2]], ["img_002", "img_001"])

    def test_service_prompt_renders_detail_index_and_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            service = AttachmentInboxService(store=store)
            ready = service.create_pending(
                profile_user_id="user",
                session_id="session",
                source="qq",
                kind="image",
                origin_name="cat.png",
                timestamp=100,
            )
            service.mark_ready(
                profile_user_id="user",
                session_id="session",
                attachment_id=ready["attachment_id"],
                summary_title="窗边小猫",
                short_hint="一只白猫趴在窗边。",
                detail={"entities": ["白猫", "窗边"], "mood_tags": ["安静"]},
                timestamp=110,
            )
            service.create_pending(
                profile_user_id="user",
                session_id="session",
                source="qq",
                kind="document",
                origin_name="draft.txt",
                timestamp=120,
            )

            prompt = service.build_prompt_context(
                profile_user_id="user",
                session_id="session",
                detail_limit=1,
            )

            self.assertIn("当前材料工作台", prompt)
            self.assertIn("img_001", prompt)
            self.assertIn("第1张图", prompt)
            self.assertIn("窗边小猫", prompt)
            self.assertIn("一只白猫趴在窗边", prompt)
            self.assertIn("来源：QQ", prompt)
            self.assertIn("正在处理", prompt)
            self.assertIn("draft.txt", prompt)
            self.assertIn("sync_attachment_workspace", prompt)

    def test_sync_workspace_focuses_arbitrary_targets_without_renumbering(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            service = AttachmentInboxService(store=store)
            created = []
            for index in range(1, 5):
                item = service.create_pending(
                    profile_user_id="user",
                    session_id="session",
                    source="qq",
                    kind="image",
                    origin_name=f"image-{index}.png",
                    timestamp=100 + index,
                )
                service.mark_ready(
                    profile_user_id="user",
                    session_id="session",
                    attachment_id=item["attachment_id"],
                    summary_title=f"第{index}张临时图",
                    short_hint=f"这是第{index}张图的摘要。",
                    detail={"mood_tags": [f"tag-{index}"]},
                    timestamp=110 + index,
                )
                created.append(item)

            result = service.sync_workspace(
                profile_user_id="user",
                session_id="session",
                focus_targets=["第一张图", "倒数第二张", "img_004"],
                kind="image",
                reason="测试任意多图对比",
                timestamp=200,
            )

            handles = [item["attachment_handle"] for item in result["focused"]]
            self.assertEqual(handles, ["img_001", "img_003", "img_004"])
            prompt = service.build_prompt_context(
                profile_user_id="user",
                session_id="session",
            )
            self.assertIn("当前重点材料 Focus", prompt)
            self.assertIn("img_001", prompt)
            self.assertIn("img_003", prompt)
            self.assertIn("img_004", prompt)
            self.assertIn("旁边的材料清单 Manifest", prompt)
            self.assertIn("img_002", prompt)
            self.assertIn("来源=QQ", prompt)
            self.assertIn("测试任意多图对比", result["followup_context"])

    def test_sync_workspace_allows_more_than_three_focus_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            service = AttachmentInboxService(store=store)
            for index in range(1, 7):
                item = service.create_pending(
                    profile_user_id="user",
                    session_id="session",
                    source="qq",
                    kind="image",
                    origin_name=f"image-{index}.png",
                    timestamp=100 + index,
                )
                service.mark_ready(
                    profile_user_id="user",
                    session_id="session",
                    attachment_id=item["attachment_id"],
                    summary_title=f"第{index}张临时图",
                    short_hint=f"这是第{index}张图。",
                    detail={"entities": [f"entity-{index}"]},
                    timestamp=110 + index,
                )

            result = service.sync_workspace(
                profile_user_id="user",
                session_id="session",
                focus_targets=["img_001", "img_002", "img_003", "img_004", "img_005", "img_006"],
                kind="image",
                reason="六图对比",
                timestamp=200,
            )

            handles = [item["attachment_handle"] for item in result["focused"]]
            self.assertEqual(handles, ["img_001", "img_002", "img_003", "img_004", "img_005", "img_006"])
            self.assertEqual(result["overflow"], [])
            prompt = service.build_prompt_context(profile_user_id="user", session_id="session")
            self.assertIn("img_006", prompt)
            self.assertIn("这是第6张图", prompt)

    def test_wait_for_attachments_settled_reports_pending_until_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            service = AttachmentInboxService(store=store)
            item = service.create_pending(
                profile_user_id="master",
                session_id="qq_pri_1",
                source="qq",
                kind="image",
                origin_name="cat.png",
                timestamp=100,
            )

            result = service.wait_for_attachments_settled(
                profile_user_id="master",
                session_id="qq_pri_1",
                attachment_ids=[str(item["attachment_id"])],
                timeout_seconds=0.01,
                poll_interval_seconds=0.01,
            )

            self.assertFalse(result["ok"])
            self.assertEqual(result["pending"], [item["attachment_id"]])

    def test_manifest_does_not_include_half_text_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            service = AttachmentInboxService(store=store)
            first = service.create_pending(
                profile_user_id="user",
                session_id="session",
                source="qq",
                kind="document",
                origin_name="first.txt",
                timestamp=100,
            )
            service.mark_ready(
                profile_user_id="user",
                session_id="session",
                attachment_id=first["attachment_id"],
                summary_title="第一份文件",
                short_hint="第一份文件摘要。",
                detail={"file_kind": "txt", "file_size": 10, "line_count": 2, "text_preview": "不该出现在Manifest里的正文"},
                timestamp=110,
            )
            second = service.create_pending(
                profile_user_id="user",
                session_id="session",
                source="qq",
                kind="document",
                origin_name="second.txt",
                timestamp=200,
            )
            service.mark_ready(
                profile_user_id="user",
                session_id="session",
                attachment_id=second["attachment_id"],
                summary_title="第二份文件",
                short_hint="第二份文件摘要。",
                detail={"file_kind": "txt", "file_size": 12, "line_count": 1, "text_preview": "当前工作台正文"},
                timestamp=210,
            )

            service.sync_workspace(
                profile_user_id="user",
                session_id="session",
                focus_targets=["file_002"],
                kind="document",
                timestamp=220,
            )
            prompt = service.build_prompt_context(profile_user_id="user", session_id="session")

            self.assertIn("旁边的材料清单 Manifest", prompt)
            self.assertIn("first.txt", prompt)
            self.assertNotIn("不该出现在Manifest里的正文", prompt)
            self.assertIn("当前工作台正文", prompt)

    def test_tool_handlers_inspect_and_clear_attachment_focus(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            service = AttachmentInboxService(store=store)
            pending = service.create_pending(
                profile_user_id="user",
                session_id="session",
                source="qq",
                kind="image",
                origin_name="meal.jpg",
                timestamp=100,
            )
            service.mark_ready(
                profile_user_id="user",
                session_id="session",
                attachment_id=pending["attachment_id"],
                summary_title="晚餐",
                short_hint="盘子里有面包和热汤。",
                detail={"mood_tags": ["温暖"]},
                timestamp=110,
            )
            context = ToolExecutionContext(
                profile_user_id="user",
                session_id="session",
                now_ts=120,
                visual_payload={},
            )

            inspect_handler = InspectAttachmentToolHandler(attachment_service=service)
            inspected = inspect_handler.execute(
                call=inspect_handler.normalize_call({"type": "inspect_attachment", "target": "晚餐"}) or {},
                context=context,
            )
            self.assertIn("盘子里有面包和热汤", inspected.followup_context)
            self.assertEqual(inspected.stream_events[0]["type"], "attachment_inspected")

            clear_handler = ClearAttachmentFocusToolHandler(attachment_service=service)
            cleared = clear_handler.execute(
                call=clear_handler.normalize_call({"type": "clear_attachment_focus", "target": "晚餐"}) or {},
                context=context,
            )
            self.assertIn("移除了 1 个材料", cleared.followup_context)
            self.assertEqual(cleared.stream_events[0]["type"], "attachment_focus_cleared")

    def test_inspect_attachment_requests_confirmation_for_ambiguous_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            service = AttachmentInboxService(store=store)
            first = service.create_pending(
                profile_user_id="user",
                session_id="session",
                source="qq",
                kind="image",
                origin_name="breakfast-menu.png",
                timestamp=100,
            )
            second = service.create_pending(
                profile_user_id="user",
                session_id="session",
                source="qq",
                kind="image",
                origin_name="dinner-menu.png",
                timestamp=101,
            )
            service.mark_ready(
                profile_user_id="user",
                session_id="session",
                attachment_id=first["attachment_id"],
                summary_title="早餐菜单图",
                short_hint="有咖啡、吐司和果酱。",
                timestamp=110,
            )
            service.mark_ready(
                profile_user_id="user",
                session_id="session",
                attachment_id=second["attachment_id"],
                summary_title="晚餐菜单图",
                short_hint="有汤、牛排和沙拉。",
                timestamp=111,
            )

            result = service.inspect_attachment(
                profile_user_id="user",
                session_id="session",
                target="菜单图",
                kind="image",
                timestamp=120,
            )

            self.assertFalse(result["ok"])
            self.assertIn("多个候选", result["followup_context"])
            self.assertIn("img_001", result["followup_context"])
            self.assertIn("img_002", result["followup_context"])

    def test_read_attachment_section_requests_confirmation_for_ambiguous_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            service = AttachmentInboxService(store=store)
            first = service.create_pending(
                profile_user_id="user",
                session_id="session",
                source="qq",
                kind="document",
                origin_name="voice.txt",
                timestamp=100,
            )
            second = service.create_pending(
                profile_user_id="user",
                session_id="session",
                source="qq",
                kind="document",
                origin_name="voice_clean.txt",
                timestamp=101,
            )
            service.mark_ready(
                profile_user_id="user",
                session_id="session",
                attachment_id=first["attachment_id"],
                summary_title="原始人声说明",
                short_hint="第一份文本。",
                detail={"text_preview": "原始人声内容"},
                timestamp=110,
            )
            service.mark_ready(
                profile_user_id="user",
                session_id="session",
                attachment_id=second["attachment_id"],
                summary_title="降噪人声说明",
                short_hint="第二份文本。",
                detail={"text_preview": "降噪人声内容"},
                timestamp=111,
            )

            result = service.read_section(
                profile_user_id="user",
                session_id="session",
                target="人声说明",
                section="全文",
                kind="document",
                timestamp=120,
            )

            self.assertFalse(result["ok"])
            self.assertIn("多个候选", result["followup_context"])
            self.assertIn("file_001", result["followup_context"])
            self.assertIn("file_002", result["followup_context"])

    def test_read_attachment_section_expands_line_range(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            service = AttachmentInboxService(store=store)
            pending = service.create_pending(
                profile_user_id="user",
                session_id="session",
                source="qq",
                kind="document",
                origin_name="notes.txt",
                timestamp=100,
            )
            service.mark_ready(
                profile_user_id="user",
                session_id="session",
                attachment_id=pending["attachment_id"],
                summary_title="长笔记",
                short_hint="有多行内容。",
                detail={"text_preview": "第1行\n第2行\n第3行\n第4行\n第5行"},
                timestamp=110,
            )

            result = service.read_section(
                profile_user_id="user",
                session_id="session",
                target="file_001",
                section="第2-4行",
                kind="document",
                timestamp=120,
            )

            self.assertTrue(result["ok"])
            self.assertIn("第2行", result["content"])
            self.assertIn("第4行", result["content"])
            self.assertNotIn("第5行", result["content"])

    def test_read_attachment_section_can_read_original_file_beyond_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            attachment_root = root / "attachments"
            stored = attachment_root / "master" / "long.txt"
            stored.parent.mkdir(parents=True, exist_ok=True)
            stored.write_text("\n".join(f"原始第{index}行" for index in range(1, 121)), encoding="utf-8")

            store = MemoryStore(root / "db")
            service = AttachmentInboxService(store=store, base_dir=attachment_root)
            pending = service.create_pending(
                profile_user_id="user",
                session_id="session",
                source="qq",
                kind="document",
                origin_name="long.txt",
                storage_relpath="master/long.txt",
                timestamp=100,
            )
            service.mark_ready(
                profile_user_id="user",
                session_id="session",
                attachment_id=pending["attachment_id"],
                summary_title="长文本",
                short_hint="只预览了开头。",
                detail={"text_preview": "原始第1行\n原始第2行", "preview_is_truncated": True},
                timestamp=110,
            )

            result = service.read_section(
                profile_user_id="user",
                session_id="session",
                target="file_001",
                section="第80-82行",
                kind="document",
                timestamp=120,
            )

            self.assertTrue(result["ok"])
            self.assertIn("原始第80行", result["content"])
            self.assertIn("原始第82行", result["content"])
            self.assertNotIn("原始第2行", result["content"])

    def test_read_attachment_section_tool_handler_dispatches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            service = AttachmentInboxService(store=store)
            pending = service.create_pending(
                profile_user_id="user",
                session_id="session",
                source="qq",
                kind="document",
                origin_name="sheet.xlsx",
                timestamp=100,
            )
            service.mark_ready(
                profile_user_id="user",
                session_id="session",
                attachment_id=pending["attachment_id"],
                summary_title="成绩表",
                short_hint="包含 Sheet1。",
                detail={
                    "sheets": [
                        {
                            "name": "Sheet1",
                            "preview_rows": [["姓名", "分数"], ["Akane", "100"]],
                        }
                    ],
                },
                timestamp=110,
            )
            context = ToolExecutionContext(
                profile_user_id="user",
                session_id="session",
                now_ts=120,
                visual_payload={},
            )
            handler = ReadAttachmentSectionToolHandler(attachment_service=service)

            result = handler.execute(
                call=handler.normalize_call(
                    {
                        "type": "read_attachment_section",
                        "target": "file_001",
                        "section": "Sheet1",
                    }
                )
                or {},
                context=context,
            )

            self.assertEqual(result.stream_events[0]["type"], "attachment_section_read")
            self.assertIn("Akane | 100", result.followup_context)

    def test_clear_attachment_focus_accepts_arbitrary_target_list(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            service = AttachmentInboxService(store=store)
            created = []
            for index in range(1, 5):
                item = service.create_pending(
                    profile_user_id="user",
                    session_id="session",
                    source="qq",
                    kind="image",
                    origin_name=f"pic-{index}.png",
                    timestamp=100 + index,
                )
                service.mark_ready(
                    profile_user_id="user",
                    session_id="session",
                    attachment_id=item["attachment_id"],
                    summary_title=f"临时图{index}",
                    short_hint=f"第{index}张图摘要。",
                    timestamp=110 + index,
                )
                created.append(item)

            context = ToolExecutionContext(
                profile_user_id="user",
                session_id="session",
                now_ts=200,
                visual_payload={},
            )
            handler = ClearAttachmentFocusToolHandler(attachment_service=service)
            result = handler.execute(
                call=handler.normalize_call(
                    {
                        "type": "clear_attachment_focus",
                        "targets": ["img_001", "第3张图", "不存在的图"],
                        "kind": "image",
                        "reason": "批量清理测试",
                    }
                )
                or {},
                context=context,
            )

            self.assertIn("移除了 2 个材料", result.followup_context)
            self.assertIn("未找到：不存在的图", result.followup_context)
            self.assertEqual(
                [item["attachment_handle"] for item in result.stream_events[0]["items"]],
                ["img_001", "img_003"],
            )
            remaining = store.list_attachment_inbox_items(
                profile_user_id="user",
                session_id="session",
                statuses=["ready"],
                limit=10,
            )
            self.assertEqual(
                [item["attachment_handle"] for item in remaining],
                ["img_004", "img_002"],
            )

    def test_clear_attachment_focus_requests_confirmation_for_ambiguous_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            service = AttachmentInboxService(store=store)
            first = service.create_pending(
                profile_user_id="user",
                session_id="session",
                source="qq",
                kind="image",
                origin_name="menu-1.png",
                timestamp=100,
            )
            second = service.create_pending(
                profile_user_id="user",
                session_id="session",
                source="qq",
                kind="image",
                origin_name="menu-2.png",
                timestamp=101,
            )
            service.mark_ready(
                profile_user_id="user",
                session_id="session",
                attachment_id=first["attachment_id"],
                summary_title="早餐菜单图",
                short_hint="第一张菜单图。",
                timestamp=110,
            )
            service.mark_ready(
                profile_user_id="user",
                session_id="session",
                attachment_id=second["attachment_id"],
                summary_title="晚餐菜单图",
                short_hint="第二张菜单图。",
                timestamp=111,
            )

            result = service.clear_focus(
                profile_user_id="user",
                session_id="session",
                targets=["菜单图"],
                kind="image",
                reason="测试歧义确认",
                timestamp=120,
            )

            self.assertFalse(result["ok"])
            self.assertIn("多个候选", result["followup_context"])
            self.assertIn("img_001", result["followup_context"])
            self.assertIn("img_002", result["followup_context"])

    def test_sync_workspace_tool_handler_returns_focused_cards(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            service = AttachmentInboxService(store=store)
            for index in range(1, 3):
                item = service.create_pending(
                    profile_user_id="user",
                    session_id="session",
                    source="qq",
                    kind="image",
                    origin_name=f"pic-{index}.png",
                    timestamp=100 + index,
                )
                service.mark_ready(
                    profile_user_id="user",
                    session_id="session",
                    attachment_id=item["attachment_id"],
                    summary_title=f"图{index}",
                    short_hint=f"图{index}摘要。",
                    timestamp=110 + index,
                )
            context = ToolExecutionContext(
                profile_user_id="user",
                session_id="session",
                now_ts=120,
                visual_payload={},
            )
            handler = SyncAttachmentWorkspaceToolHandler(attachment_service=service)
            result = handler.execute(
                call=handler.normalize_call(
                    {
                        "type": "sync_attachment_workspace",
                        "focus_targets": ["img_001", "第二张图"],
                        "kind": "image",
                    }
                )
                or {},
                context=context,
            )

            self.assertIn("2 个材料放到了当前工作台", result.followup_context)
            self.assertEqual(result.stream_events[0]["type"], "attachment_workspace_synced")
            self.assertEqual(
                [item["attachment_handle"] for item in result.stream_events[0]["items"]],
                ["img_001", "img_002"],
            )

    def test_retry_attachment_tool_handler_dispatches_retry(self) -> None:
        class FakeIngestService:
            def __init__(self) -> None:
                self.calls = []

            def retry_attachment(self, **kwargs):
                self.calls.append(kwargs)
                return {
                    "ok": True,
                    "status": "retry_started",
                    "item": {"attachment_handle": "img_001"},
                    "followup_context": "正在重新处理。",
                }

        fake_ingest = FakeIngestService()
        handler = RetryAttachmentToolHandler(attachment_ingest_service=fake_ingest)
        context = ToolExecutionContext(
            profile_user_id="user",
            session_id="session",
            now_ts=123,
            visual_payload={},
        )
        result = handler.execute(
            call=handler.normalize_call(
                {
                    "type": "retry_attachment",
                    "target": "img_001",
                    "kind": "image",
                }
            )
            or {},
            context=context,
        )

        self.assertEqual(fake_ingest.calls[0]["target"], "img_001")
        self.assertEqual(fake_ingest.calls[0]["kind"], "image")
        self.assertEqual(result.stream_events[0]["type"], "attachment_retry_started")
        self.assertIn("正在重新处理", result.followup_context)


if __name__ == "__main__":
    unittest.main()
