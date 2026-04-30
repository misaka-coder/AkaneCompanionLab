from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from companion_v01.engine import AkaneMemoryEngine
from companion_v01.store import MemoryStore
from companion_v01.task_workspace import TaskWorkspaceService
from companion_v01.tool_runtime import ManageTaskWorkspaceToolHandler, ToolExecutionContext, ToolExecutionResult


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

    def test_service_build_prompt_context_renders_open_task_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            service = TaskWorkspaceService(store)
            task = service.create_task(
                profile_user_id="master",
                session_id="qq-private",
                raw_request_text="帮我把视频整理成文档。",
                normalized_goal="下载视频、转写音频、整理 Markdown。",
                steps=[
                    {"title": "下载视频", "status": "done", "note": "video_001 已进工作台"},
                    {"title": "转写音频", "status": "running"},
                ],
                artifacts=[{"id": "video_001", "kind": "video", "title": "测试视频"}],
                timestamp=300,
            )
            service.append_event(
                task_id=task["task_id"],
                event_type="tool_artifacts_recorded",
                from_actor="tool:fetch_media_from_url",
                message="fetch_media_from_url 产出了 1 个可继续使用的产物。",
                status="handled",
                timestamp=310,
            )

            context = service.build_prompt_context(
                profile_user_id="master",
                session_id="qq-private",
            )

            self.assertIn("【当前任务工作区】", context)
            self.assertIn("下载视频、转写音频、整理 Markdown", context)
            self.assertIn("下载视频(done)", context)
            self.assertIn("video_001(video / 测试视频)", context)
            self.assertIn("tool_artifacts_recorded", context)
            self.assertIn("任务工作区只是白板", context)
            self.assertIn("调用真正的处理工具", context)
            self.assertIn("前台状态: 后台正在处理", context)
            self.assertIn("用户问“好了没/到哪了”时，只说当前进度", context)
            self.assertIn("已完成 下载视频", context)
            self.assertIn("正在 转写音频", context)

    def test_service_build_prompt_context_ignores_closed_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            service = TaskWorkspaceService(store)
            task = service.create_task(
                profile_user_id="master",
                session_id="qq-private",
                raw_request_text="已经完成的任务。",
                normalized_goal="已经完成的任务。",
                timestamp=300,
            )
            service.complete_task(task_id=task["task_id"], timestamp=310)

            context = service.build_prompt_context(
                profile_user_id="master",
                session_id="qq-private",
            )

            self.assertEqual(context, "")

    def test_service_build_prompt_context_keeps_completed_task_with_pending_worker_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            service = TaskWorkspaceService(store)
            task = service.create_task(
                profile_user_id="master",
                session_id="qq-private",
                raw_request_text="把音频整理成终稿。",
                normalized_goal="生成终稿并等待前台助手发给用户。",
                artifacts=[{"id": "gen_002", "kind": "md", "title": "终稿"}],
                metadata={
                    "workshop": {
                        "assigned_agent": "document_agent",
                        "status": "running",
                        "brief": "先生成初稿，再整理成终稿。",
                    }
                },
                timestamp=300,
            )
            service.complete_task(task_id=task["task_id"], timestamp=310)
            service.update_task(
                task_id=task["task_id"],
                metadata={
                    "workshop": {
                        "assigned_agent": "document_agent",
                        "status": "done",
                        "brief": "先生成初稿，再整理成终稿。",
                    }
                },
                timestamp=311,
            )
            service.append_event(
                task_id=task["task_id"],
                event_type="worker_completed",
                from_actor="document_agent",
                priority="high",
                message="终稿已经准备好，请前台助手确认后统一发给用户。",
                status="pending",
                timestamp=312,
            )

            context = service.build_prompt_context(
                profile_user_id="master",
                session_id="qq-private",
            )

            self.assertIn("【当前任务工作区】", context)
            self.assertIn("生成终稿并等待前台助手发给用户", context)
            self.assertIn("状态: completed", context)
            self.assertIn("后台工坊: document_agent / done", context)
            self.assertIn("工坊说明: 先生成初稿，再整理成终稿", context)
            self.assertIn("gen_002(md / 终稿)", context)
            self.assertIn("worker_completed", context)
            self.assertIn("前台状态: 后台已完成，等待前台助手确认/交付", context)
            self.assertIn("先问是否发送以及要发送哪一份", context)

    def test_service_build_prompt_context_renders_blocked_frontstage_question(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            service = TaskWorkspaceService(store)
            task = service.create_task(
                profile_user_id="master",
                session_id="qq-private",
                raw_request_text="整理视频成字幕。",
                normalized_goal="整理视频成字幕。",
                status="waiting_user",
                steps=[{"title": "确认字幕格式", "status": "waiting_user"}],
                metadata={
                    "workshop": {
                        "assigned_agent": "media_agent",
                        "status": "blocked",
                        "handoff": {
                            "status": "blocked",
                            "summary": "缺少字幕导出格式。",
                            "user_question": "需要你指定字幕导出 srt 还是 vtt。",
                            "next_action": "ask_user",
                        },
                    }
                },
                timestamp=300,
            )
            service.update_task(
                task_id=task["task_id"],
                status="waiting_user",
                pending_question={
                    "text": "需要你指定字幕导出 srt 还是 vtt。",
                    "reason": "缺少字幕格式。",
                },
                timestamp=310,
            )

            context = service.build_prompt_context(
                profile_user_id="master",
                session_id="qq-private",
            )

            self.assertIn("交接状态: 阻塞，等待用户回答", context)
            self.assertIn("前台状态: 等待用户确认", context)
            self.assertIn("直接问用户：需要你指定字幕导出 srt 还是 vtt。", context)
            self.assertIn("不要复述后台日志或工具错误", context)


class ManageTaskWorkspaceToolHandlerTests(unittest.TestCase):
    def test_handler_creates_updates_asks_completes_and_cleans_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            service = TaskWorkspaceService(store)
            handler = ManageTaskWorkspaceToolHandler(task_workspace_service=service)
            instruction = handler.build_prompt_instruction()
            self.assertIn("创建/更新任务工作区不等于执行任务", instruction)
            self.assertIn("真正的处理工具", instruction)
            self.assertIn("好了没/现在到哪了/还在跑吗", instruction)
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

    def test_handler_inspect_returns_frontstage_progress_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            service = TaskWorkspaceService(store)
            handler = ManageTaskWorkspaceToolHandler(task_workspace_service=service)
            task = service.create_task(
                profile_user_id="master",
                session_id="qq-private",
                raw_request_text="把视频转写并总结。",
                normalized_goal="把视频转写并总结。",
                status="running",
                steps=[
                    {"title": "转写视频", "status": "done"},
                    {"title": "整理总结", "status": "running"},
                ],
                artifacts=[{"id": "gen_001", "kind": "md", "title": "视频转写稿"}],
                metadata={"workshop": {"assigned_agent": "media_agent", "status": "running"}},
                timestamp=500,
            )

            result = handler.execute(
                call={"type": "manage_task_workspace", "action": "inspect", "task_id": task["task_id"]},
                context=ToolExecutionContext(
                    profile_user_id="master",
                    session_id="qq-private",
                    now_ts=520,
                    visual_payload={},
                ),
            )

            self.assertIn("前台状态: 后台正在处理", result.followup_context)
            self.assertIn("已完成 转写视频", result.followup_context)
            self.assertIn("正在 整理总结", result.followup_context)
            self.assertIn("不要长篇解释内部 task_id", result.followup_context)


class TaskWorkspaceEngineIntegrationTests(unittest.TestCase):
    def test_engine_records_generated_tool_artifacts_on_latest_open_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            service = TaskWorkspaceService(store)
            task = service.create_task(
                profile_user_id="master",
                session_id="qq-private",
                raw_request_text="把视频转写成 Markdown。",
                normalized_goal="下载视频、转写并生成 Markdown。",
                timestamp=500,
            )
            engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
            engine.store = store
            engine.task_workspace_service = service

            events, followup = engine._record_tool_result_artifacts_in_task_workspace(
                profile_user_id="master",
                session_id="qq-private",
                now_ts=520,
                tool_result=ToolExecutionResult(
                    tool_type="compose_file",
                    stream_events=[
                        {
                            "type": "generated_file_ready",
                            "generated_file": {
                                "generated_id": "generated::001",
                                "generated_handle": "gen_001",
                                "status": "ready",
                                "output_title": "视频转写稿",
                                "output_format": "md",
                                "file_ext": "md",
                                "file_size": 1234,
                                "created_by_tool": "compose_file",
                            },
                            "send_to_user": True,
                        }
                    ],
                    followup_context="已生成 gen_001。",
                ),
            )

            self.assertEqual(events[0]["type"], "task_workspace_artifacts_recorded")
            self.assertIn("gen_001", followup)
            updated = service.get_task(task["task_id"])
            self.assertIsNotNone(updated)
            assert updated is not None
            self.assertEqual(updated["status"], "running")
            self.assertEqual(updated["artifacts"][0]["id"], "gen_001")
            self.assertEqual(updated["artifacts"][0]["source"], "generated_file")
            workspace_events = service.list_events(task_id=task["task_id"])
            self.assertEqual(workspace_events[-1]["event_type"], "tool_artifacts_recorded")

            duplicate_events, duplicate_followup = engine._record_tool_result_artifacts_in_task_workspace(
                profile_user_id="master",
                session_id="qq-private",
                now_ts=530,
                tool_result=ToolExecutionResult(
                    tool_type="send_file",
                    stream_events=[
                        {
                            "type": "generated_file_ready",
                            "generated_file": {
                                "generated_id": "generated::001",
                                "generated_handle": "gen_001",
                                "output_title": "视频转写稿",
                                "output_format": "md",
                            },
                        }
                    ],
                ),
            )
            self.assertEqual(duplicate_events, [])
            self.assertEqual(duplicate_followup, "")
            self.assertEqual(len(service.get_task(task["task_id"])["artifacts"]), 1)

    def test_engine_records_remote_media_attachment_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            service = TaskWorkspaceService(store)
            task = service.create_task(
                profile_user_id="master",
                session_id="qq-private",
                raw_request_text="下载视频素材。",
                normalized_goal="通过链接获取视频素材。",
                timestamp=600,
            )
            engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
            engine.store = store
            engine.task_workspace_service = service

            events, followup = engine._record_tool_result_artifacts_in_task_workspace(
                profile_user_id="master",
                session_id="qq-private",
                now_ts=620,
                tool_result=ToolExecutionResult(
                    tool_type="fetch_media_from_url",
                    stream_events=[
                        {
                            "type": "attachment_remote_media_ready",
                            "item": {
                                "attachment_id": "attachment::001",
                                "attachment_handle": "video_001",
                                "status": "ready",
                                "kind": "video",
                                "summary_title": "测试视频",
                                "origin_name": "test.mp4",
                                "file_ext": ".mp4",
                                "file_size": 2048,
                                "source": "remote_url",
                            },
                        }
                    ],
                ),
            )

            self.assertEqual(events[0]["type"], "task_workspace_artifacts_recorded")
            self.assertIn("video_001", followup)
            updated = service.get_task(task["task_id"])
            self.assertIsNotNone(updated)
            assert updated is not None
            self.assertEqual(updated["artifacts"][0]["id"], "video_001")
            self.assertEqual(updated["artifacts"][0]["source"], "attachment_inbox")


if __name__ == "__main__":
    unittest.main()
