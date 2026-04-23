from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from companion_v01.store import MemoryStore
from companion_v01.task_workspace import TaskWorkspaceService


class TaskWorkspaceStoreTests(unittest.TestCase):
    def test_store_roundtrip_update_and_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            task = store.add_task_workspace(
                profile_user_id="master",
                session_id="qq-private",
                owner="Akane",
                status="running",
                raw_request={"text": "把视频总结成 Word"},
                normalized_goal="下载视频，转写音频，并整理成 Word。",
                success_criteria=["生成 docx", "发回用户"],
                constraints=["不要自动删除原始素材"],
                steps=[{"id": "step_1", "title": "下载视频", "status": "done"}],
                artifacts=[{"id": "gen_001", "kind": "docx"}],
                pending_question={"text": "需要保留字幕吗？"},
                metadata={"mode": "qq_text"},
                timestamp=100,
            )

            self.assertTrue(task["task_id"].startswith("task::"))
            self.assertEqual(task["status"], "running")
            self.assertEqual(task["raw_request"]["text"], "把视频总结成 Word")
            self.assertEqual(task["success_criteria"], ["生成 docx", "发回用户"])
            self.assertEqual(task["steps"][0]["status"], "done")
            self.assertEqual(task["artifacts"][0]["id"], "gen_001")
            self.assertEqual(task["pending_question"]["text"], "需要保留字幕吗？")

            updated = store.update_task_workspace(
                task_id=task["task_id"],
                status="waiting_user",
                steps=[
                    {"id": "step_1", "title": "下载视频", "status": "done"},
                    {"id": "step_2", "title": "确认字幕", "status": "waiting_user"},
                ],
                pending_question={"text": "要不要字幕时间轴？"},
                updated_at=120,
            )

            self.assertIsNotNone(updated)
            assert updated is not None
            self.assertEqual(updated["status"], "waiting_user")
            self.assertEqual(updated["updated_at"], 120)
            self.assertEqual(len(updated["steps"]), 2)
            self.assertEqual(updated["pending_question"]["text"], "要不要字幕时间轴？")

            listed = store.list_task_workspaces(
                profile_user_id="master",
                session_id="qq-private",
                statuses=["waiting_user"],
            )
            self.assertEqual([item["task_id"] for item in listed], [task["task_id"]])

            event = store.append_task_workspace_event(
                task_id=task["task_id"],
                profile_user_id="master",
                session_id="qq-private",
                event_type="agent_question",
                from_actor="media_agent",
                priority="high",
                requires_user=True,
                message="字幕要带时间轴吗？",
                payload={"options": ["带", "不带"]},
                timestamp=130,
            )

            self.assertTrue(event["event_id"].startswith("task_event::"))
            self.assertEqual(event["priority"], "high")
            self.assertTrue(event["requires_user"])
            pending = store.list_task_workspace_events(task_id=task["task_id"], status="pending")
            self.assertEqual([item["event_id"] for item in pending], [event["event_id"]])

            handled = store.mark_task_workspace_event_handled(event_id=event["event_id"], handled_at=140)
            self.assertIsNotNone(handled)
            assert handled is not None
            self.assertEqual(handled["status"], "handled")
            self.assertEqual(handled["handled_at"], 140)
            self.assertEqual(store.list_task_workspace_events(task_id=task["task_id"], status="pending"), [])

    def test_service_creates_completion_and_cleanup_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            service = TaskWorkspaceService(store)

            task = service.create_task(
                profile_user_id="master",
                session_id="web-main",
                raw_request_text="帮我处理这批音频",
                source_message_id="msg_001",
                normalized_goal="分离人声、切片、转写。",
                success_criteria=["生成切片", "生成转写稿"],
                timestamp=200,
            )

            events = service.list_events(task_id=task["task_id"])
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event_type"], "task_created")
            self.assertEqual(events[0]["payload"]["source_message_id"], "msg_001")

            completed = service.complete_task(
                task_id=task["task_id"],
                artifacts=[{"id": "gen_002", "kind": "md"}],
                message="全部处理完毕。",
                timestamp=240,
            )
            self.assertIsNotNone(completed)
            assert completed is not None
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(completed["completed_at"], 240)
            self.assertEqual(completed["artifacts"], [{"id": "gen_002", "kind": "md"}])

            cleaned = service.cleanup_task(
                task_id=task["task_id"],
                mode="clean_scratch",
                reason="用户确认完成后清理工作记忆。",
                timestamp=260,
            )
            self.assertIsNotNone(cleaned)
            assert cleaned is not None
            self.assertEqual(cleaned["status"], "cleaned")
            self.assertEqual(cleaned["cleaned_at"], 260)
            self.assertEqual(cleaned["metadata"]["cleanup"]["mode"], "clean_scratch")

            final_events = service.list_events(task_id=task["task_id"])
            self.assertEqual(
                [event["event_type"] for event in final_events],
                ["task_created", "task_completed", "task_cleaned"],
            )


if __name__ == "__main__":
    unittest.main()
