from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from companion_v01.store import MemoryStore
from companion_v01.task_workspace import TaskWorkspaceService
from companion_v01.tool_runtime import ManageTaskWorkspaceToolHandler, ToolExecutionContext


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


class ManageTaskWorkspaceToolHandlerTests(unittest.TestCase):
    def test_handler_creates_updates_asks_completes_and_cleans_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            service = TaskWorkspaceService(store)
            handler = ManageTaskWorkspaceToolHandler(task_workspace_service=service)
            context = ToolExecutionContext(
                profile_user_id="master",
                session_id="qq-private",
                now_ts=300,
                visual_payload={},
                current_user_source_id="msg_300",
            )

            create_call = handler.normalize_call(
                {
                    "type": "manage_task_workspace",
                    "action": "create",
                    "goal": "把视频下载、转写并整理成 Markdown。",
                    "steps": [
                        {"title": "下载视频", "status": "running"},
                        {"title": "转写音频", "status": "queued"},
                    ],
                    "success_criteria": ["生成 md", "发回用户"],
                }
            )
            self.assertIsNotNone(create_call)
            assert create_call is not None
            created_result = handler.execute(call=create_call, context=context)
            self.assertEqual(created_result.tool_type, "manage_task_workspace")
            self.assertEqual(created_result.stream_events[0]["type"], "task_workspace_created")
            task_id = created_result.state_updates["task_id"]

            task = service.get_task(task_id)
            self.assertIsNotNone(task)
            assert task is not None
            self.assertEqual(task["status"], "running")
            self.assertEqual(task["raw_request"]["source_message_id"], "msg_300")

            update_result = handler.execute(
                call={
                    "type": "manage_task_workspace",
                    "action": "update_steps",
                    "task_id": task_id,
                    "steps": [
                        {"title": "下载视频", "status": "done", "note": "已进工作台"},
                        {"title": "转写音频", "status": "running"},
                    ],
                    "reason": "下载完成。",
                },
                context=context,
            )
            self.assertEqual(update_result.stream_events[0]["type"], "task_workspace_updated")
            updated = service.get_task(task_id)
            self.assertIsNotNone(updated)
            assert updated is not None
            self.assertEqual(updated["steps"][0]["status"], "done")

            artifact_result = handler.execute(
                call={
                    "type": "manage_task_workspace",
                    "action": "add_artifact",
                    "task_id": task_id,
                    "artifacts": [{"id": "gen_001", "kind": "md", "title": "转写稿"}],
                },
                context=context,
            )
            self.assertEqual(artifact_result.stream_events[0]["type"], "task_workspace_artifact_added")
            self.assertEqual(service.get_task(task_id)["artifacts"][0]["id"], "gen_001")

            ask_result = handler.execute(
                call={
                    "type": "manage_task_workspace",
                    "action": "ask_user",
                    "task_id": task_id,
                    "question": "要不要保留时间轴？",
                },
                context=context,
            )
            self.assertEqual(ask_result.stream_events[0]["type"], "task_workspace_question")
            self.assertEqual(service.get_task(task_id)["status"], "waiting_user")

            complete_result = handler.execute(
                call={
                    "type": "manage_task_workspace",
                    "action": "complete",
                    "task_id": task_id,
                    "reason": "全部完成。",
                },
                context=context,
            )
            self.assertEqual(complete_result.stream_events[0]["type"], "task_workspace_completed")
            self.assertEqual(service.get_task(task_id)["status"], "completed")

            cleanup_result = handler.execute(
                call={
                    "type": "manage_task_workspace",
                    "action": "cleanup",
                    "task_id": task_id,
                    "reason": "用户确认收到了。",
                },
                context=context,
            )
            self.assertEqual(cleanup_result.stream_events[0]["type"], "task_workspace_cleaned")
            self.assertEqual(service.get_task(task_id)["status"], "cleaned")

    def test_handler_defaults_to_latest_open_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            service = TaskWorkspaceService(store)
            handler = ManageTaskWorkspaceToolHandler(task_workspace_service=service)
            context = ToolExecutionContext(
                profile_user_id="master",
                session_id="qq-private",
                now_ts=400,
                visual_payload={},
            )
            task = service.create_task(
                profile_user_id="master",
                session_id="qq-private",
                raw_request_text="处理音频。",
                normalized_goal="处理音频。",
                timestamp=390,
            )

            result = handler.execute(
                call={
                    "type": "manage_task_workspace",
                    "action": "add_artifact",
                    "artifacts": [{"id": "gen_009", "kind": "wav"}],
                },
                context=context,
            )

            self.assertEqual(result.state_updates["task_id"], task["task_id"])
            self.assertEqual(service.get_task(task["task_id"])["artifacts"][0]["id"], "gen_009")


if __name__ == "__main__":
    unittest.main()
