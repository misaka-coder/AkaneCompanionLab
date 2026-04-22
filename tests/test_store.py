from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from companion_v01.store import MemoryStore


class MemoryStoreEvalTurnTests(unittest.TestCase):
    def test_attachment_handle_sequence_uses_visible_prefix_not_kind(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            document_item = store.add_attachment_inbox_item(
                profile_user_id="master",
                session_id="master",
                kind="document",
                origin_name="notes.pdf",
                timestamp=100,
            )

            file_item = store.add_attachment_inbox_item(
                profile_user_id="master",
                session_id="master",
                kind="file",
                origin_name="video.mp4",
                timestamp=101,
            )

            self.assertEqual(document_item["attachment_handle"], "file_001")
            self.assertEqual(file_item["attachment_handle"], "file_002")

    def test_session_titles_can_be_listed_and_renamed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            first = store.ensure_session(profile_user_id="user_a", session_id="session_a", timestamp=100)
            second = store.ensure_session(profile_user_id="user_a", session_id="session_b", timestamp=200)

            sessions = store.list_sessions("user_a")

            self.assertEqual(first["display_title"], "新的对话")
            self.assertEqual(second["display_title"], "新的对话 2")
            self.assertEqual([item["session_id"] for item in sessions], ["session_b", "session_a"])

            renamed = store.rename_session(
                profile_user_id="user_a",
                session_id="session_a",
                display_title="蓝桥杯复习",
                timestamp=300,
            )
            self.assertIsNotNone(renamed)
            self.assertEqual(renamed["display_title"], "蓝桥杯复习")
            self.assertEqual(store.get_session("user_a", "session_a")["display_title"], "蓝桥杯复习")

    def test_get_session_messages_returns_most_recent_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            for index in range(1, 6):
                store.add_message(
                    profile_user_id="user_a",
                    session_id="session_a",
                    role="user" if index % 2 else "assistant",
                    content=f"message-{index}",
                    timestamp=100 + index,
                )

            recent_messages = store.get_session_messages(
                profile_user_id="user_a",
                session_id="session_a",
                limit=3,
            )

            self.assertEqual([item["seq_no"] for item in recent_messages], [3, 4, 5])
            self.assertEqual([item["content"] for item in recent_messages], ["message-3", "message-4", "message-5"])

    def test_get_latest_eval_turn_returns_most_recent_final_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            store.append_eval_turn(
                trace_id="trace_1",
                session_id="session_a",
                profile_user_id="user_a",
                user_message="first",
                router_json={"route": "direct_answer"},
                verifier_json={"match_result": "skip"},
                final_json={
                    "emotion": "normal",
                    "character": {"outfit": "校服"},
                    "scene": {"major": "school", "minor": "classroom", "background": "evening", "bgm": "evening"},
                },
            )
            store.append_eval_turn(
                trace_id="trace_2",
                session_id="session_a",
                profile_user_id="user_a",
                user_message="second",
                router_json={"route": "direct_answer"},
                verifier_json={"match_result": "skip"},
                final_json={
                    "emotion": "battle_focus",
                    "character": {"outfit": "armor"},
                    "scene": {"major": "battlefield", "minor": "frontline", "background": "siege_fire", "bgm": "war_drums"},
                },
            )

            latest = store.get_latest_eval_turn("session_a")

            self.assertIsNotNone(latest)
            self.assertEqual(latest["trace_id"], "trace_2")
            self.assertEqual(latest["final_json"]["character"]["outfit"], "armor")
            self.assertEqual(latest["final_json"]["scene"]["bgm"], "war_drums")

    def test_get_latest_eval_turn_for_session_is_profile_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            store.append_eval_turn(
                trace_id="trace_user_a",
                session_id="session_shared",
                profile_user_id="user_a",
                user_message="hello",
                router_json={"route": "direct_answer"},
                verifier_json={"match_result": "skip"},
                final_json={"speech": "给 user_a 的回复"},
            )
            store.append_eval_turn(
                trace_id="trace_user_b",
                session_id="session_shared",
                profile_user_id="user_b",
                user_message="hello",
                router_json={"route": "direct_answer"},
                verifier_json={"match_result": "skip"},
                final_json={"speech": "给 user_b 的回复"},
            )

            latest = store.get_latest_eval_turn_for_session(
                profile_user_id="user_a",
                session_id="session_shared",
            )

            self.assertIsNotNone(latest)
            self.assertEqual(latest["trace_id"], "trace_user_a")
            self.assertEqual(latest["final_json"]["speech"], "给 user_a 的回复")

    def test_claim_due_reminders_marks_rows_as_fired(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            reminder = store.add_reminder(
                profile_user_id="user_a",
                session_id="session_a",
                content="复习微积分",
                due_ts=100,
                raw_time_text="明天晚上八点",
            )

            claimed = store.claim_due_reminders(
                profile_user_id="user_a",
                session_id="session_a",
                now_ts=120,
            )

            self.assertEqual(len(claimed), 1)
            self.assertEqual(claimed[0]["reminder_id"], reminder["reminder_id"])
            self.assertEqual(claimed[0]["status"], "fired")
            self.assertEqual(claimed[0]["fired_at"], 120)

            claimed_again = store.claim_due_reminders(
                profile_user_id="user_a",
                session_id="session_a",
                now_ts=130,
            )
            self.assertEqual(claimed_again, [])

    def test_list_and_cancel_pending_reminders(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            reminder = store.add_reminder(
                profile_user_id="user_a",
                session_id="session_a",
                content="背单词",
                due_ts=200,
                raw_time_text="明天早上",
            )

            pending = store.list_reminders(
                profile_user_id="user_a",
                session_id="session_a",
                status="pending",
            )
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["reminder_id"], reminder["reminder_id"])

            cancelled = store.cancel_reminder(
                profile_user_id="user_a",
                session_id="session_a",
                reminder_id=reminder["reminder_id"],
                cancelled_at=250,
            )
            self.assertIsNotNone(cancelled)
            self.assertEqual(cancelled["status"], "cancelled")

            pending_after = store.list_reminders(
                profile_user_id="user_a",
                session_id="session_a",
                status="pending",
            )
            self.assertEqual(pending_after, [])

    def test_index_in_vector_round_trip_and_reindex_filter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            kept = store.add_message(
                profile_user_id="user_a",
                session_id="session_a",
                role="user",
                content="普通聊天",
                timestamp=100,
                semantic_tags=["普通"],
                index_in_vector=True,
            )
            skipped = store.add_message(
                profile_user_id="user_a",
                session_id="session_a",
                role="user",
                content="你还记得吗",
                timestamp=101,
                semantic_tags=["测试"],
                index_in_vector=False,
            )

            kept_row = store.get_message_by_source_id(kept["source_id"])
            skipped_row = store.get_message_by_source_id(skipped["source_id"])
            reindex_batches = list(store.iter_messages_for_vector_reindex(batch_size=10))
            reindex_ids = [record["source_id"] for batch in reindex_batches for record in batch]

            self.assertIsNotNone(kept_row)
            self.assertIsNotNone(skipped_row)
            self.assertTrue(kept_row["index_in_vector"])
            self.assertFalse(skipped_row["index_in_vector"])
            self.assertEqual(store.count_vectorizable_records(), 1)
            self.assertEqual(reindex_ids, [kept["source_id"]])

    def test_semantic_summary_round_trip_and_visible_episodic_filter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            summary_1 = store.add_summary(
                profile_user_id="user_a",
                session_id="session_a",
                timestamp=100,
                date_label="2026-04-10",
                time_of_day="afternoon",
                period_label="上课前",
                event_type="日常",
                importance=0.6,
                diary_summary="主人提到下午要去上课。",
                key_events=["准备去教室"],
                core_facts=["下午有课"],
                semantic_tags=["上课", "下午"],
                source_start_seq=1,
                source_end_seq=4,
                source_ids=["msg-1", "msg-2"],
            )
            summary_2 = store.add_summary(
                profile_user_id="user_a",
                session_id="session_a",
                timestamp=200,
                date_label="2026-04-10",
                time_of_day="night",
                period_label="下课后",
                event_type="日常",
                importance=0.7,
                diary_summary="主人回来说课上得还行。",
                key_events=["回来报平安"],
                core_facts=["晚上回来了"],
                semantic_tags=["回来", "课程"],
                source_start_seq=5,
                source_end_seq=8,
                source_ids=["msg-3", "msg-4"],
            )

            semantic = store.add_semantic_summary(
                profile_user_id="user_a",
                session_id="session_a",
                timestamp=200,
                period_start_ts=90,
                period_end_ts=200,
                date_label="2026-04-10",
                time_of_day="night",
                importance=0.8,
                semantic_summary="主人这段时间反复提到课程安排，我记住他最近确实在忙学习。",
                stable_facts=["最近有课", "学习安排重要"],
                recurring_topics=["上课", "复习"],
                important_people=[],
                open_loops=["还要继续复习"],
                semantic_tags=["课程", "学习"],
                source_summary_ids=[summary_1["summary_id"]],
            )
            store.mark_summaries_semanticized([summary_1["summary_id"]], semantic["semantic_id"])

            visible_episodic = store.get_visible_episodic_summaries("user_a", limit=10)
            recent_semantic = store.get_recent_semantic_summaries("user_a", limit=3)
            summary_1_reloaded = store.get_summary_by_id(summary_1["summary_id"])
            semantic_record = store.get_record_by_source_id(semantic["semantic_id"])

            self.assertEqual([item["summary_id"] for item in visible_episodic], [summary_2["summary_id"]])
            self.assertEqual(len(recent_semantic), 1)
            self.assertEqual(recent_semantic[0]["semantic_id"], semantic["semantic_id"])
            self.assertEqual(summary_1_reloaded["is_semanticized"], 1)
            self.assertEqual(summary_1_reloaded["semantic_id"], semantic["semantic_id"])
            self.assertEqual(semantic_record["entry_type"], "semantic_summary")
            self.assertIn("学习安排重要", semantic_record["stable_facts"])

    def test_update_semantic_summary_persists_reinforcement_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            semantic = store.add_semantic_summary(
                profile_user_id="user_a",
                session_id="session_a",
                timestamp=200,
                period_start_ts=90,
                period_end_ts=200,
                date_label="2026-04-10",
                time_of_day="night",
                importance=0.7,
                semantic_summary="主人最近总在聊学习安排。",
                stable_facts=["最近在学习"],
                recurring_topics=["复习"],
                important_people=[],
                open_loops=["还要继续复习"],
                semantic_tags=["学习", "复习"],
                source_summary_ids=["summary::1"],
            )

            updated = store.update_semantic_summary(
                semantic_id=semantic["semantic_id"],
                timestamp=260,
                period_start_ts=90,
                period_end_ts=260,
                date_label="2026-04-11",
                time_of_day="morning",
                importance=0.85,
                semantic_summary="主人最近持续在推进学习安排，而且复习节奏更明确了。",
                stable_facts=["最近在学习", "复习节奏更明确"],
                recurring_topics=["复习", "课程安排"],
                important_people=["老师"],
                open_loops=["还要继续复习"],
                semantic_tags=["学习", "复习", "课程安排"],
                source_summary_ids=["summary::1", "summary::2"],
                reinforcement_count=2,
                last_reinforced_ts=260,
            )

            self.assertIsNotNone(updated)
            self.assertEqual(updated["reinforcement_count"], 2)
            self.assertEqual(updated["last_reinforced_ts"], 260)
            self.assertEqual(updated["period_end_ts"], 260)
            self.assertIn("老师", updated["important_people"])
            self.assertEqual(updated["source_summary_ids"], ["summary::1", "summary::2"])

    def test_user_media_asset_round_trip_and_status_update(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            created = store.add_user_media_asset(
                asset_id="gift_1",
                resource_id="gift_bgm_demo",
                profile_user_id="user_a",
                session_id="session_a",
                media_kind="bgm",
                origin_name="夜色.flac",
                display_name="夜色",
                mime_type="audio/flac",
                file_ext=".flac",
                file_size=12345,
                storage_relpath="bucket-a/bgm/gift_1.flac",
                status="offered",
                timestamp=100,
            )

            fetched = store.get_user_media_asset(profile_user_id="user_a", asset_id="gift_1")
            listed = store.list_user_media_assets(profile_user_id="user_a", media_kind="bgm", limit=10)
            updated = store.update_user_media_asset_status(
                profile_user_id="user_a",
                asset_id="gift_1",
                status="internalized",
                timestamp=120,
            )

            self.assertEqual(created["status"], "pending")
            self.assertEqual(created["asset_type"], "audio")
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched["display_name"], "夜色")
            self.assertEqual(fetched["payload"]["filename"], "夜色.flac")
            self.assertEqual(fetched["container_type"], "music_box")
            self.assertEqual(fetched["container_key"], "main")
            self.assertEqual(fetched["container_name"], "曲库")
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0]["resource_id"], "gift_bgm_demo")
            self.assertIsNotNone(updated)
            self.assertEqual(updated["status"], "internalized")
            self.assertEqual(updated["updated_at"], 120)

            artifacts = store.list_artifacts_by_container(
                profile_user_id="user_a",
                container_type="music_box",
                statuses=["internalized"],
                limit=10,
            )
            artifact_count = store.count_artifacts_by_container(
                profile_user_id="user_a",
                container_type="music_box",
                statuses=["internalized"],
            )
            self.assertEqual(len(artifacts), 1)
            self.assertEqual(artifact_count, 1)
            self.assertEqual(artifacts[0]["asset_id"], "gift_1")

    def test_session_gift_focus_can_be_set_and_cleared(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            store.ensure_session(
                profile_user_id="user_a",
                session_id="session_a",
                timestamp=100,
            )

            focused = store.set_session_gift_focus(
                profile_user_id="user_a",
                session_id="session_a",
                asset_id="gift_1",
                timestamp=110,
            )
            cleared = store.clear_session_gift_focus(
                profile_user_id="user_a",
                session_id="session_a",
                timestamp=120,
            )

            self.assertEqual(focused["current_gift_focus_asset_id"], "gift_1")
            self.assertEqual(focused["current_gift_focus_updated_at"], 110)
            self.assertIsNotNone(cleared)
            self.assertEqual(cleared["current_gift_focus_asset_id"], "")

    def test_vision_observation_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))

            created = store.upsert_vision_observation(
                observation_type="scene",
                resource_fingerprint="fingerprint_1",
                target_id="school::classroom::evening",
                source_path="assets/scenes/school/classroom/evening.png",
                public_path="/assets/scenes/school/classroom/evening.png",
                prompt_version="v1",
                provider="override",
                model_name="override",
                status="ready",
                summary="黄昏教室，窗边有暖光。",
                observation={
                    "type": "scene_observation",
                    "summary": "黄昏教室，窗边有暖光。",
                    "entities": ["教室", "窗边"],
                    "mood_tags": ["黄昏", "安静"],
                    "uncertainty": [],
                },
                timestamp=100,
            )
            fetched = store.get_vision_observation(
                observation_type="scene",
                resource_fingerprint="fingerprint_1",
                prompt_version="v1",
            )

            self.assertEqual(created["status"], "ready")
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched["target_id"], "school::classroom::evening")
            self.assertEqual(fetched["provider"], "override")
            self.assertEqual(fetched["observation"]["mood_tags"], ["黄昏", "安静"])


if __name__ == "__main__":
    unittest.main()
