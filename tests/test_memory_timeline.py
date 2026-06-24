from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from companion_v01.background_tasks import BackgroundTaskRunner
from companion_v01.memory_timeline import MemoryTimelineService
from companion_v01.store import MemoryStore
from companion_v01.tool_runtime import ReadMemoryTimelineToolHandler, ToolExecutionContext


def _ts(year: int, month: int, day: int, hour: int, minute: int = 0) -> int:
    return int(datetime(year, month, day, hour, minute).timestamp())


class MemoryTimelineServiceTests(unittest.TestCase):
    def test_character_pack_memory_lives_under_private_local_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            characters_dir = root / "characters"
            (characters_dir / "reimu").mkdir(parents=True)
            store = MemoryStore(root / "store")
            store.add_message(
                profile_user_id="master",
                session_id="desktop",
                character_pack_id="reimu",
                role="user",
                content="角色私有记忆。",
                timestamp=_ts(2026, 6, 13, 9, 0),
            )
            service = MemoryTimelineService(
                store=store,
                root_dir=root / "memory",
                characters_dir=characters_dir,
            )
            legacy_path = service._legacy_day_file_path(
                profile_user_id="master",
                character_pack_id="reimu",
                date_label="2026-06-13",
            )
            legacy_path.parent.mkdir(parents=True, exist_ok=True)
            legacy_path.write_text("旧镜像", encoding="utf-8")

            result = service.backfill_existing()
            day_path = service.day_file_path(
                profile_user_id="master",
                character_pack_id="reimu",
                date_label="2026-06-13",
            )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(
                day_path,
                characters_dir
                / "reimu"
                / "_local"
                / "memory"
                / "profiles"
                / "master"
                / "days"
                / "2026"
                / "06"
                / "2026-06-13.md",
            )
            self.assertTrue(day_path.is_file())
            self.assertFalse(legacy_path.exists())
            self.assertTrue((characters_dir / "reimu" / "_local" / "README.md").is_file())

    def test_exact_read_uses_actual_message_time_and_character_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            store.add_message(
                profile_user_id="master",
                session_id="desktop",
                character_pack_id="reimu",
                role="user",
                content="昨晚那件事我还记得。",
                timestamp=_ts(2026, 6, 13, 8, 15),
                date_label="2026-06-13",
                time_of_day="night",
            )
            store.add_message(
                profile_user_id="master",
                session_id="desktop",
                character_pack_id="reimu",
                role="assistant",
                content="下午再接着聊吧。",
                timestamp=_ts(2026, 6, 13, 14, 5),
            )
            store.add_message(
                profile_user_id="master",
                session_id="desktop",
                character_pack_id="marisa",
                role="user",
                content="不应跨角色出现。",
                timestamp=_ts(2026, 6, 13, 8, 30),
            )
            service = MemoryTimelineService(
                store=store,
                root_dir=Path(temp_dir) / "memory",
            )

            result = service.read(
                profile_user_id="master",
                character_pack_id="reimu",
                date_from="2026-06-13",
                date_to="2026-06-13",
                time_periods=["morning"],
            )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["message_count"], 1)
            self.assertEqual(result["messages"][0]["content"], "昨晚那件事我还记得。")
            self.assertEqual(result["messages"][0]["actual_time_period"], "morning")

    def test_existing_messages_backfill_to_readable_markdown_with_mood(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            store.add_message(
                profile_user_id="master",
                session_id="desktop",
                character_pack_id="reimu",
                role="user",
                content="今天的视频收到了很多点赞。",
                timestamp=_ts(2026, 6, 13, 10, 20),
                memory_metadata={
                    "mood_tags": ["happy", "proud"],
                    "keywords": ["演示视频", "点赞"],
                },
            )
            store.add_message(
                profile_user_id="master",
                session_id="desktop",
                character_pack_id="reimu",
                role="assistant",
                content="那确实值得高兴。",
                timestamp=_ts(2026, 6, 13, 10, 21),
                memory_metadata={
                    "response_emotion": "smile",
                    "response_outfit": "shrine_maiden",
                    "mood_tags": ["warm"],
                },
            )
            service = MemoryTimelineService(
                store=store,
                root_dir=Path(temp_dir) / "memory",
            )

            result = service.backfill_existing()
            day_path = service.day_file_path(
                profile_user_id="master",
                character_pack_id="reimu",
                date_label="2026-06-13",
            )
            text = day_path.read_text(encoding="utf-8")

            self.assertEqual(result["status"], "ok")
            self.assertTrue(day_path.is_file())
            self.assertIn("# 2026-06-13 原始对话", text)
            self.assertIn("## 上午", text)
            self.assertIn("### 10:20:00 用户", text)
            self.assertIn("记忆余温：开心、欣慰", text)
            self.assertIn("内容检索线索：演示视频、点赞", text)
            self.assertIn("不表示用户或角色主观上特别在意", text)
            self.assertIn("角色当时的表情/情绪：smile", text)
            self.assertIn("角色当时的服装：shrine_maiden", text)
            self.assertIn("> 今天的视频收到了很多点赞。", text)
            self.assertNotIn('"role"', text)
            self.assertNotIn("source_id", text)

    def test_message_callback_refreshes_file_after_metadata_update(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            service = MemoryTimelineService(
                store=store,
                root_dir=Path(temp_dir) / "memory",
            )
            store.set_message_write_callback(service.handle_message_write)
            record = store.add_message(
                profile_user_id="master",
                session_id="desktop",
                character_pack_id="reimu",
                role="user",
                content="先写进数据库。",
                timestamp=_ts(2026, 6, 13, 20, 0),
            )
            store.update_message_memory_metadata(
                record["source_id"],
                {"mood_tags": ["thoughtful"]},
            )

            day_path = service.day_file_path(
                profile_user_id="master",
                character_pack_id="reimu",
                date_label="2026-06-13",
            )
            text = day_path.read_text(encoding="utf-8")

            self.assertIn("先写进数据库。", text)
            self.assertIn("记忆余温：认真", text)

    def test_background_message_callback_refreshes_file_after_completed_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "store")
            background_tasks = BackgroundTaskRunner({"timeline": 1})
            try:
                service = MemoryTimelineService(
                    store=store,
                    root_dir=root / "memory",
                    background_tasks=background_tasks,
                )
                store.set_message_write_callback(service.handle_message_write)
                user_record = store.add_message(
                    profile_user_id="master",
                    session_id="desktop",
                    character_pack_id="reimu",
                    role="user",
                    content="实时写入测试。",
                    timestamp=_ts(2026, 6, 13, 22, 0),
                )
                store.update_message_memory_metadata(
                    user_record["source_id"],
                    {"keywords": ["实时写入"], "mood_tags": ["curious"]},
                )
                store.add_message(
                    profile_user_id="master",
                    session_id="desktop",
                    character_pack_id="reimu",
                    role="assistant",
                    content="已经写进今天的文件了。",
                    timestamp=_ts(2026, 6, 13, 22, 1),
                    memory_metadata={
                        "response_emotion": "smile",
                        "mood_tags": ["warm"],
                    },
                )

                self.assertTrue(background_tasks.wait_idle(lane="timeline", timeout=3.0))
                day_path = service.day_file_path(
                    profile_user_id="master",
                    character_pack_id="reimu",
                    date_label="2026-06-13",
                )
                text = day_path.read_text(encoding="utf-8")

                self.assertIn("实时写入测试。", text)
                self.assertIn("已经写进今天的文件了。", text)
                self.assertIn("内容检索线索：实时写入", text)
                self.assertIn("角色当时的表情/情绪：smile", text)
            finally:
                background_tasks.close()

    def test_acquaintance_prompt_distinguishes_recorded_start_and_active_days(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            for day in (1, 3):
                store.add_message(
                    profile_user_id="master",
                    session_id="desktop",
                    character_pack_id="reimu",
                    role="user",
                    content=f"第 {day} 天。",
                    timestamp=_ts(2026, 6, day, 9, 0),
                )
            service = MemoryTimelineService(
                store=store,
                root_dir=Path(temp_dir) / "memory",
            )

            prompt = service.build_acquaintance_prompt(
                profile_user_id="master",
                character_pack_id="reimu",
                now_ts=_ts(2026, 6, 13, 12, 0),
            )

            self.assertIn("第一次聊天是在 2026-06-01", prompt)
            self.assertIn("认识的第 13 天", prompt)
            self.assertIn("有 2 天留下过对话", prompt)
            self.assertIn("首次留下记录", prompt)


class ReadMemoryTimelineToolHandlerTests(unittest.TestCase):
    def test_tool_is_exact_raw_timeline_and_uses_execution_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            current_query = store.add_message(
                profile_user_id="master",
                session_id="desktop",
                character_pack_id="reimu",
                role="user",
                content="请读取今天上午的原始对话。",
                timestamp=_ts(2026, 6, 13, 10, 0),
            )
            store.add_message(
                profile_user_id="master",
                session_id="desktop",
                character_pack_id="reimu",
                role="assistant",
                content="上午讨论了角色提示词。",
                timestamp=_ts(2026, 6, 13, 9, 0),
            )
            service = MemoryTimelineService(
                store=store,
                root_dir=Path(temp_dir) / "memory",
            )
            handler = ReadMemoryTimelineToolHandler(timeline_service=service)

            call = handler.normalize_call(
                {
                    "type": "read_memory_timeline",
                    "date": "2026-06-13",
                    "time_periods": ["上午"],
                    "profile_user_id": "someone_else",
                    "character_pack_id": "marisa",
                }
            )
            self.assertIsNotNone(call)
            assert call is not None
            result = handler.execute(
                call=call,
                context=ToolExecutionContext(
                    profile_user_id="master",
                    session_id="desktop",
                    character_pack_id="reimu",
                    now_ts=_ts(2026, 6, 13, 12, 0),
                    visual_payload={},
                    current_user_source_id=current_query["source_id"],
                ),
            )

            self.assertIn("原始对话，不是摘要或长期语义记忆", result.followup_context)
            self.assertIn("上午讨论了角色提示词。", result.followup_context)
            self.assertNotIn("请读取今天上午的原始对话。", result.followup_context)
            self.assertEqual(result.state_updates["memory_timeline"]["status"], "ok")
            self.assertEqual(result.state_updates["memory_timeline"]["message_count"], 1)

    def test_prompt_keeps_semantic_and_exact_time_retrieval_separate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = MemoryTimelineService(
                store=MemoryStore(Path(temp_dir)),
                root_dir=Path(temp_dir) / "memory",
            )
            instruction = ReadMemoryTimelineToolHandler(
                timeline_service=service
            ).build_prompt_instruction()

            self.assertIn("不做向量搜索", instruction)
            self.assertIn("不读取阶段摘要或长期记忆", instruction)
            self.assertIn("仍使用 retrieve_memory", instruction)


if __name__ == "__main__":
    unittest.main()
