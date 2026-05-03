from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from companion_v01.attachment_ingest import AttachmentIngestService
from companion_v01.attachment_inbox import AttachmentInboxService
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.generated_files import GeneratedFileService
from companion_v01.store import MemoryStore
from companion_v01.task_workspace import TaskWorkspaceService


class _NoopVisionService:
    def schedule_attachment_image_observation(self, **_kwargs):
        return None


def _make_workspace_engine(root: Path) -> AkaneMemoryEngine:
    engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
    engine.base_dir = root
    engine.store = MemoryStore(root / "store")
    engine.attachment_inbox_service = AttachmentInboxService(
        store=engine.store,
        base_dir=root / "attachments",
    )
    engine.vision_service = _NoopVisionService()
    engine.attachment_ingest_service = AttachmentIngestService(
        base_dir=root / "attachments",
        store=engine.store,
        attachment_service=engine.attachment_inbox_service,
        vision_service=engine.vision_service,
    )
    engine.generated_file_service = GeneratedFileService(
        base_dir=root / "generated",
        store=engine.store,
        attachment_service=engine.attachment_inbox_service,
    )
    engine.task_workspace_service = TaskWorkspaceService(store=engine.store)
    return engine


class DesktopWorkspacePanelTests(unittest.TestCase):
    def test_panel_projects_user_facing_cards_without_internal_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = _make_workspace_engine(Path(temp_dir))
            engine.store.add_attachment_inbox_item(
                profile_user_id="master",
                session_id="desktop_pet_test",
                source="desktop_pet",
                kind="audio",
                status="ready",
                origin_name="song.flac",
                file_ext="flac",
                file_size=1024,
                summary_title="一首歌",
                timestamp=100,
            )
            engine.store.add_generated_file(
                profile_user_id="master",
                session_id="desktop_pet_test",
                output_title="整理好的歌词",
                output_format="md",
                storage_relpath="master/desktop_pet_test/lyrics.md",
                file_size=256,
                created_by_tool="compose_file",
                timestamp=110,
            )
            engine.store.add_task_workspace(
                profile_user_id="master",
                session_id="desktop_pet_test",
                status="completed",
                normalized_goal="整理音频结果",
                timestamp=120,
            )

            panel = engine.build_desktop_pet_workspace_panel(
                profile_user_id="master",
                session_id="desktop_pet_test",
            )

            self.assertTrue(panel["ok"])
            self.assertEqual(panel["counts"], {"files": 1, "outputs": 1, "tasks": 1})
            file_card = panel["sections"]["files"][0]
            output_card = panel["sections"]["outputs"][0]
            task_card = panel["sections"]["tasks"][0]
            self.assertEqual(file_card["title"], "一首歌")
            self.assertEqual(file_card["subtitle"], "音频 · FLAC")
            self.assertEqual(output_card["title"], "整理好的歌词")
            self.assertIn("做好的东西", output_card["subtitle"])
            self.assertEqual(task_card["title"], "整理音频结果")
            self.assertNotIn("profile_user_id", file_card)
            self.assertNotIn("session_id", output_card)

    def test_panel_actions_soft_clear_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = _make_workspace_engine(Path(temp_dir))
            attachment = engine.store.add_attachment_inbox_item(
                profile_user_id="master",
                session_id="desktop_pet_test",
                source="desktop_pet",
                kind="file",
                status="ready",
                origin_name="note.txt",
                file_ext="txt",
                summary_title="便签",
                timestamp=100,
            )
            generated = engine.store.add_generated_file(
                profile_user_id="master",
                session_id="desktop_pet_test",
                output_title="便签摘要",
                output_format="md",
                storage_relpath="master/desktop_pet_test/note.md",
                created_by_tool="compose_file",
                timestamp=110,
            )
            task = engine.store.add_task_workspace(
                profile_user_id="master",
                session_id="desktop_pet_test",
                status="completed",
                normalized_goal="收尾任务",
                timestamp=120,
            )

            attachment_result = engine.manage_desktop_pet_workspace_panel(
                profile_user_id="master",
                session_id="desktop_pet_test",
                action="clear",
                item_type="attachment",
                target=attachment["attachment_handle"],
            )
            generated_result = engine.manage_desktop_pet_workspace_panel(
                profile_user_id="master",
                session_id="desktop_pet_test",
                action="clear",
                item_type="generated",
                target=generated["generated_handle"],
            )
            tasks_result = engine.manage_desktop_pet_workspace_panel(
                profile_user_id="master",
                session_id="desktop_pet_test",
                action="clear_completed_tasks",
            )

            self.assertTrue(attachment_result["ok"])
            self.assertTrue(generated_result["ok"])
            self.assertTrue(tasks_result["ok"])
            self.assertEqual(
                engine.store.get_attachment_inbox_item(
                    profile_user_id="master",
                    session_id="desktop_pet_test",
                    attachment_id=attachment["attachment_id"],
                )["status"],
                "cleared",
            )
            self.assertEqual(
                engine.store.get_generated_file(
                    profile_user_id="master",
                    session_id="desktop_pet_test",
                    generated_id=generated["generated_id"],
                )["status"],
                "removed",
            )
            self.assertEqual(engine.store.get_task_workspace(task["task_id"])["status"], "cleaned")

    def test_panel_single_item_actions_require_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = _make_workspace_engine(Path(temp_dir))

            for item_type in ("attachment", "generated", "task"):
                result = engine.manage_desktop_pet_workspace_panel(
                    profile_user_id="master",
                    session_id="desktop_pet_test",
                    action="clear",
                    item_type=item_type,
                    target="",
                )

                self.assertFalse(result["ok"])
                self.assertEqual(result["error"], "missing_target")
                self.assertEqual(result["managed"], [])

    def test_workspace_location_resolvers_return_local_ready_files_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            engine = _make_workspace_engine(root)
            attachment_path = root / "attachments" / "master" / "desktop_pet_test" / "source.txt"
            attachment_path.parent.mkdir(parents=True, exist_ok=True)
            attachment_path.write_text("source", encoding="utf-8")
            generated_path = root / "generated" / "master" / "desktop_pet_test" / "result.md"
            generated_path.parent.mkdir(parents=True, exist_ok=True)
            generated_path.write_text("# result", encoding="utf-8")

            attachment = engine.store.add_attachment_inbox_item(
                profile_user_id="master",
                session_id="desktop_pet_test",
                source="desktop_pet",
                kind="file",
                status="ready",
                origin_name="source.txt",
                file_ext="txt",
                storage_relpath="master/desktop_pet_test/source.txt",
                summary_title="源文件",
                timestamp=100,
            )
            generated = engine.store.add_generated_file(
                profile_user_id="master",
                session_id="desktop_pet_test",
                output_title="生成结果",
                output_format="md",
                storage_relpath="master/desktop_pet_test/result.md",
                created_by_tool="compose_file",
                timestamp=110,
            )

            attachment_resolved = engine.resolve_desktop_pet_attachment_file(
                profile_user_id="master",
                session_id="desktop_pet_test",
                target=attachment["attachment_handle"],
            )
            generated_resolved = engine.resolve_desktop_pet_generated_file(
                profile_user_id="master",
                session_id="desktop_pet_test",
                target=generated["generated_handle"],
            )

            self.assertIsNotNone(attachment_resolved)
            self.assertIsNotNone(generated_resolved)
            self.assertEqual(attachment_resolved[1], attachment_path.resolve())
            self.assertEqual(generated_resolved[1], generated_path.resolve())

            engine.manage_desktop_pet_workspace_panel(
                profile_user_id="master",
                session_id="desktop_pet_test",
                action="clear",
                item_type="attachment",
                target=attachment["attachment_handle"],
            )
            engine.manage_desktop_pet_workspace_panel(
                profile_user_id="master",
                session_id="desktop_pet_test",
                action="clear",
                item_type="generated",
                target=generated["generated_handle"],
            )

            self.assertIsNone(
                engine.resolve_desktop_pet_attachment_file(
                    profile_user_id="master",
                    session_id="desktop_pet_test",
                    target=attachment["attachment_handle"],
                )
            )
            self.assertIsNone(
                engine.resolve_desktop_pet_generated_file(
                    profile_user_id="master",
                    session_id="desktop_pet_test",
                    target=generated["generated_handle"],
                )
            )

    def test_clear_files_action_clears_sources_and_generated_without_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = _make_workspace_engine(Path(temp_dir))
            attachment = engine.store.add_attachment_inbox_item(
                profile_user_id="master",
                session_id="desktop_pet_test",
                source="desktop_pet",
                kind="file",
                status="ready",
                origin_name="source.txt",
                file_ext="txt",
                summary_title="源文件",
                timestamp=100,
            )
            generated = engine.store.add_generated_file(
                profile_user_id="master",
                session_id="desktop_pet_test",
                output_title="生成结果",
                output_format="md",
                storage_relpath="master/desktop_pet_test/result.md",
                created_by_tool="compose_file",
                timestamp=110,
            )
            task = engine.store.add_task_workspace(
                profile_user_id="master",
                session_id="desktop_pet_test",
                status="completed",
                normalized_goal="不要被一键文件清理影响",
                timestamp=120,
            )

            result = engine.manage_desktop_pet_workspace_panel(
                profile_user_id="master",
                session_id="desktop_pet_test",
                action="clear_files",
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["action"], "clear_files")
            self.assertEqual(len(result["managed"]), 2)
            self.assertEqual(
                engine.store.get_attachment_inbox_item(
                    profile_user_id="master",
                    session_id="desktop_pet_test",
                    attachment_id=attachment["attachment_id"],
                )["status"],
                "cleared",
            )
            self.assertEqual(
                engine.store.get_generated_file(
                    profile_user_id="master",
                    session_id="desktop_pet_test",
                    generated_id=generated["generated_id"],
                )["status"],
                "removed",
            )
            self.assertEqual(engine.store.get_task_workspace(task["task_id"])["status"], "completed")

    def test_task_cards_expose_status_group_and_handoff_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = _make_workspace_engine(Path(temp_dir))
            task = engine.store.add_task_workspace(
                profile_user_id="master",
                session_id="desktop_pet_test",
                status="waiting_user",
                normalized_goal="整理音频并生成摘要",
                pending_question={"text": "要不要保留时间轴？"},
                timestamp=130,
            )
            engine.store.append_task_workspace_event(
                task_id=task["task_id"],
                profile_user_id="master",
                session_id="desktop_pet_test",
                event_type="workshop_handoff",
                from_actor="worker",
                payload={
                    "handoff": {
                        "status": "partial",
                        "summary": "转写稿已生成，时间轴格式待确认。",
                        "next_action": "询问用户是否保留时间轴。",
                        "artifacts": [{"id": "gen_001", "title": "转写稿"}],
                    },
                },
                status="pending",
                timestamp=135,
            )

            panel = engine.build_desktop_pet_workspace_panel(
                profile_user_id="master",
                session_id="desktop_pet_test",
            )

            task_card = panel["sections"]["tasks"][0]
            self.assertEqual(task_card["status"], "partial")
            self.assertEqual(task_card["status_group"], "attention")
            self.assertEqual(task_card["artifact_count"], 1)
            self.assertIn("转写稿已生成", task_card["subtitle"])
            self.assertIn("询问用户", task_card["next_action"])

    def test_local_path_import_copies_supported_files_into_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            engine = _make_workspace_engine(root)
            incoming = root / "incoming"
            incoming.mkdir()
            source = incoming / "note.md"
            source.write_text("# note\nhello", encoding="utf-8")

            result = engine.import_desktop_pet_local_paths(
                profile_user_id="master",
                session_id="desktop_pet_test",
                paths=[str(source)],
                timestamp=150,
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["imported"], 1)
            card = result["items"][0]
            self.assertEqual(card["kind"], "document")
            self.assertEqual(card["format"], "md")
            self.assertEqual(card["status"], "ready")

            stored = engine.store.get_attachment_inbox_item(
                profile_user_id="master",
                session_id="desktop_pet_test",
                attachment_id=result["attachments"][0]["attachment_id"],
            )
            self.assertEqual(stored["source"], "desktop_pet")
            self.assertEqual(stored["kind"], "document")
            self.assertNotEqual(Path(stored["storage_relpath"]), source)

            resolved = engine.resolve_desktop_pet_attachment_file(
                profile_user_id="master",
                session_id="desktop_pet_test",
                target=card["handle"],
            )
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved[1].read_text(encoding="utf-8"), "# note\nhello")

    def test_local_path_import_skips_unsupported_files_and_respects_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            engine = _make_workspace_engine(root)
            incoming = root / "incoming"
            incoming.mkdir()
            (incoming / "a.txt").write_text("a", encoding="utf-8")
            (incoming / "b.txt").write_text("b", encoding="utf-8")
            (incoming / "skip.exe").write_bytes(b"nope")

            unsupported = engine.import_desktop_pet_local_paths(
                profile_user_id="master",
                session_id="desktop_pet_test",
                paths=[str(incoming / "skip.exe")],
                timestamp=151,
            )
            limited = engine.import_desktop_pet_local_paths(
                profile_user_id="master",
                session_id="desktop_pet_test",
                paths=[str(incoming)],
                max_files=1,
                timestamp=152,
            )

            self.assertFalse(unsupported["ok"])
            self.assertEqual(unsupported["skipped"][0]["reason"], "unsupported_type")
            self.assertTrue(limited["ok"])
            self.assertEqual(limited["imported"], 1)
            self.assertTrue(any(item["reason"] == "max_files_reached" for item in limited["skipped"]))

    def test_local_audio_import_soft_dedupes_duplicate_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            engine = _make_workspace_engine(root)
            incoming = root / "incoming"
            incoming.mkdir()
            source = incoming / "song.mp3"
            source.write_bytes(b"fake-audio")

            first = engine.import_desktop_pet_local_paths(
                profile_user_id="master",
                session_id="desktop_pet_test",
                paths=[str(source)],
                timestamp=160,
            )
            second = engine.import_desktop_pet_local_paths(
                profile_user_id="master",
                session_id="desktop_pet_test",
                paths=[str(source)],
                timestamp=161,
            )

            self.assertTrue(first["ok"])
            self.assertEqual(first["imported"], 1)
            self.assertEqual(first["duplicate_count"], 0)
            self.assertTrue(second["ok"])
            self.assertEqual(second["imported"], 0)
            self.assertEqual(second["duplicate_count"], 1)
            self.assertEqual(second["skipped"][0]["reason"], "duplicate_source")
            self.assertEqual(second["skipped"][0]["origin_name"], "song.mp3")
            self.assertEqual(second["skipped"][0]["existing_handle"], first["items"][0]["handle"])

    def test_local_import_does_not_soft_dedupe_non_audio_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            engine = _make_workspace_engine(root)
            incoming = root / "incoming"
            incoming.mkdir()
            source = incoming / "note.md"
            source.write_text("# note", encoding="utf-8")

            first = engine.import_desktop_pet_local_paths(
                profile_user_id="master",
                session_id="desktop_pet_test",
                paths=[str(source)],
                timestamp=170,
            )
            second = engine.import_desktop_pet_local_paths(
                profile_user_id="master",
                session_id="desktop_pet_test",
                paths=[str(source)],
                timestamp=171,
            )

            self.assertTrue(first["ok"])
            self.assertTrue(second["ok"])
            self.assertEqual(first["imported"], 1)
            self.assertEqual(second["imported"], 1)
            self.assertEqual(second["duplicate_count"], 0)

    def test_workspace_summary_soft_dedupes_audio_cards_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = _make_workspace_engine(Path(temp_dir))
            old_audio = engine.store.add_attachment_inbox_item(
                profile_user_id="master",
                session_id="desktop_pet_test",
                source="desktop_pet",
                kind="audio",
                status="pending_observation",
                origin_name="same.mp3",
                file_ext="mp3",
                file_size=128,
                summary_title="Same Song",
                timestamp=100,
            )
            ready_audio = engine.store.add_attachment_inbox_item(
                profile_user_id="master",
                session_id="desktop_pet_test",
                source="desktop_pet",
                kind="audio",
                status="ready",
                origin_name="same.mp3",
                file_ext="mp3",
                file_size=128,
                summary_title="Same Song",
                timestamp=110,
            )
            engine.store.add_attachment_inbox_item(
                profile_user_id="master",
                session_id="desktop_pet_test",
                source="desktop_pet",
                kind="document",
                status="ready",
                origin_name="note.md",
                file_ext="md",
                file_size=128,
                summary_title="Same Song",
                timestamp=120,
            )
            engine.store.add_attachment_inbox_item(
                profile_user_id="master",
                session_id="desktop_pet_test",
                source="desktop_pet",
                kind="document",
                status="ready",
                origin_name="note.md",
                file_ext="md",
                file_size=128,
                summary_title="Same Song",
                timestamp=130,
            )

            panel = engine.build_desktop_pet_workspace_panel(
                profile_user_id="master",
                session_id="desktop_pet_test",
            )

            files = panel["sections"]["files"]
            audio_cards = [item for item in files if item["kind"] == "audio"]
            document_cards = [item for item in files if item["kind"] == "document"]
            self.assertEqual(panel["counts"]["files"], 3)
            self.assertEqual(len(audio_cards), 1)
            self.assertEqual(len(document_cards), 2)
            self.assertEqual(audio_cards[0]["handle"], ready_audio["attachment_handle"])
            self.assertEqual(audio_cards[0]["duplicate_count"], 2)
            self.assertEqual(
                set(audio_cards[0]["duplicate_handles"]),
                {old_audio["attachment_handle"], ready_audio["attachment_handle"]},
            )


if __name__ == "__main__":
    unittest.main()
