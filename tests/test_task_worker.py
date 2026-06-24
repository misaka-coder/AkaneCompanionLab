from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

import config

from companion_v01.attachment_inbox import AttachmentInboxService
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.generated_files import GeneratedFileService
from companion_v01.store import MemoryStore
from companion_v01.task_worker import TaskWorkerService
from companion_v01.task_worker_tool import DelegateTaskToolHandler
from companion_v01.task_workspace import TaskWorkspaceService
from companion_v01.tool_runtime import (
    BaseToolHandler,
    ComposeFileToolHandler,
    ManageTaskWorkspaceToolHandler,
    ReviseGeneratedFileToolHandler,
    SendFileToolHandler,
    ToolExecutionContext,
    ToolExecutionResult,
)


class FakeLLM:
    def __init__(self, outputs: list[dict[str, Any]]) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict[str, str]] = []

    def call_chat_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        fallback: dict[str, Any],
        temperature: float = 0.7,
        prompt_cache_key: str = "",
    ) -> dict[str, Any]:
        self.calls.append({"system": system_prompt, "user": user_prompt})
        if not self.outputs:
            return dict(fallback)
        return dict(self.outputs.pop(0))


class TaskWorkerArtifactSelectionTests(unittest.TestCase):
    def test_handoff_artifacts_keep_only_requested_audio_role(self) -> None:
        worker = TaskWorkerService.__new__(TaskWorkerService)
        task = {
            "normalized_goal": "帮我从这首歌里分离一下，我只要人声。",
            "raw_request": {"text": "只要人声就好"},
            "metadata": {"workshop": {"brief": "分离人声，伴奏只是中间结果。"}},
        }
        artifacts = [
            {
                "id": "gen_001",
                "kind": "wav",
                "title": "歌曲_人声",
                "source": "generated_file",
                "tool": "separate_audio_stems",
                "created_by_tool": "separate_audio_stems",
                "stem_role": "vocals",
                "delivery_role": "workspace_material",
            },
            {
                "id": "gen_002",
                "kind": "wav",
                "title": "歌曲_伴奏",
                "source": "generated_file",
                "tool": "separate_audio_stems",
                "created_by_tool": "separate_audio_stems",
                "stem_role": "instrumental",
                "delivery_role": "workspace_material",
            },
        ]

        selected = worker._select_handoff_artifacts(task=task, artifacts=artifacts)

        self.assertEqual([item["id"] for item in selected], ["gen_001"])

    def test_handoff_artifacts_prefer_final_dataset_over_intermediates(self) -> None:
        worker = TaskWorkerService.__new__(TaskWorkerService)
        task = {
            "normalized_goal": "从视频里分离人声、降噪，并切片准备训练素材。",
            "metadata": {"workshop": {"expected_outputs": ["训练素材包"]}},
        }
        artifacts = [
            {"id": "gen_001", "kind": "wav", "title": "人声", "source": "generated_file", "tool": "separate_audio_stems", "stem_role": "vocals"},
            {"id": "gen_002", "kind": "wav", "title": "伴奏", "source": "generated_file", "tool": "separate_audio_stems", "stem_role": "instrumental"},
            {"id": "gen_003", "kind": "wav", "title": "降噪人声", "source": "generated_file", "tool": "clean_voice_track"},
            {"id": "gen_004", "kind": "zip", "title": "训练素材包", "source": "generated_file", "tool": "prepare_voice_dataset"},
        ]

        selected = worker._select_handoff_artifacts(task=task, artifacts=artifacts)

        self.assertEqual([item["id"] for item in selected], ["gen_004"])


class FakeComposeFileHandler(BaseToolHandler):
    tool_type = "compose_file"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def build_prompt_instruction(self) -> str:
        return "- compose_file：生成文件。"

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "") != self.tool_type:
            return None
        return dict(value)

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        self.calls.append(dict(call))
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "generated_file_ready",
                    "generated_file": {
                        "generated_id": "generated::001",
                        "generated_handle": "gen_001",
                        "status": "ready",
                        "output_title": "后台总结",
                        "output_format": "md",
                        "file_ext": "md",
                        "created_by_tool": "compose_file",
                    },
                    "send_to_user": False,
                }
            ],
            followup_context="已生成 gen_001。",
        )


class TaskWorkerServiceTests(unittest.TestCase):
    def test_worker_executes_allowed_tool_and_records_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            workspace = TaskWorkspaceService(store)
            engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
            engine.store = store
            engine.task_workspace_service = workspace
            llm = FakeLLM(
                [
                    {
                        "status": "continue",
                        "message": "先生成文件。",
                        "tool_call": {
                            "type": "compose_file",
                            "output_title": "后台总结",
                            "output_format": "md",
                            "content_markdown": "后台内容",
                        },
                        "steps": [{"title": "生成文件", "status": "running"}],
                    },
                    {
                        "status": "done",
                        "message": "后台任务完成。",
                        "tool_call": None,
                    },
                ]
            )
            compose_handler = FakeComposeFileHandler()
            worker = TaskWorkerService(
                llm=llm,  # type: ignore[arg-type]
                task_workspace_service=workspace,
                background_tasks=None,
                tool_handlers_provider=lambda: {"compose_file": compose_handler},
                attachment_context_builder=lambda _profile, _session: "",
                generated_context_builder=lambda _profile, _session: "",
                record_tool_artifacts=engine._record_tool_result_artifacts_in_task_workspace,
            )
            delegated = worker.delegate_task(
                profile_user_id="master",
                session_id="qq-private",
                brief="把当前内容整理成 Markdown。",
                agent="document_agent",
                normalized_goal="生成 Markdown 文件。",
                auto_start=False,
                timestamp=100,
            )

            summary = worker.run_task_sync(
                task_id=delegated.task_id,
                profile_user_id="master",
                session_id="qq-private",
                assigned_agent="document_agent",
                now_ts=110,
            )

            self.assertEqual(summary.status, "done")
            task = workspace.get_task(delegated.task_id)
            self.assertIsNotNone(task)
            assert task is not None
            self.assertEqual(task["status"], "completed")
            self.assertEqual(task["artifacts"][0]["id"], "gen_001")
            self.assertEqual(compose_handler.calls[0]["send_to_user"], False)
            events = workspace.list_events(task_id=delegated.task_id)
            self.assertTrue(any(event["event_type"] == "worker_completed" for event in events))
            self.assertIn("compose_file", llm.calls[0]["system"])

    def test_worker_multi_round_closure_keeps_results_for_akane_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            workspace = TaskWorkspaceService(store)
            attachment_service = AttachmentInboxService(store=store, base_dir=root / "attachments")
            generated_service = GeneratedFileService(
                base_dir=root / "generated_files",
                store=store,
                attachment_service=attachment_service,
            )
            engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
            engine.store = store
            engine.task_workspace_service = workspace
            llm = FakeLLM(
                [
                    {
                        "status": "continue",
                        "message": "先生成初稿。",
                        "tool_call": {
                            "type": "compose_file",
                            "output_title": "后台总结",
                            "output_format": "md",
                            "content_markdown": "# 后台总结\n\n第一版内容。",
                        },
                        "steps": [{"title": "生成初稿", "status": "running"}],
                    },
                    {
                        "status": "continue",
                        "message": "再整理一个终稿版本。",
                        "tool_call": {
                            "type": "revise_generated_file",
                            "target": "gen_001",
                            "instruction": "补一段可以直接汇报给用户的总结",
                            "output_title": "后台总结_终稿",
                            "output_format": "md",
                            "content_markdown": "# 后台总结\n\n第一版内容。\n\n总结：可以直接发给主人。",
                        },
                        "steps": [
                            {"title": "生成初稿", "status": "done"},
                            {"title": "整理终稿", "status": "running"},
                        ],
                    },
                    {
                        "status": "done",
                        "message": "终稿已经准备好，请前台助手确认后统一发给用户。",
                        "tool_call": None,
                        "handoff": {
                            "summary": "终稿已经准备好，建议前台助手先请用户确认是否发送。",
                            "next_action": "ask_confirmation",
                        },
                        "steps": [
                            {"title": "生成初稿", "status": "done"},
                            {"title": "整理终稿", "status": "done"},
                        ],
                    },
                ]
            )
            worker = TaskWorkerService(
                llm=llm,  # type: ignore[arg-type]
                task_workspace_service=workspace,
                background_tasks=None,
                tool_handlers_provider=lambda: {
                    "compose_file": ComposeFileToolHandler(generated_file_service=generated_service),
                    "revise_generated_file": ReviseGeneratedFileToolHandler(generated_file_service=generated_service),
                },
                attachment_context_builder=lambda _profile, _session: "",
                generated_context_builder=lambda profile, session: generated_service.build_prompt_context(
                    profile_user_id=profile,
                    session_id=session,
                    limit=8,
                ),
                record_tool_artifacts=engine._record_tool_result_artifacts_in_task_workspace,
            )
            delegated = worker.delegate_task(
                profile_user_id="master",
                session_id="qq-private",
                brief="整理一份后台总结，完成后交给前台助手统一发送。",
                agent="document_agent",
                normalized_goal="生成终稿并等待前台助手发给用户。",
                auto_start=False,
                timestamp=100,
            )

            summary = worker.run_task_sync(
                task_id=delegated.task_id,
                profile_user_id="master",
                session_id="qq-private",
                assigned_agent="document_agent",
                now_ts=110,
            )

            self.assertEqual(summary.status, "done")
            task = workspace.get_task(delegated.task_id)
            self.assertIsNotNone(task)
            assert task is not None
            self.assertEqual(task["status"], "completed")
            self.assertEqual([item["id"] for item in task["artifacts"]], ["gen_001", "gen_002"])
            self.assertEqual([step["title"] for step in task["steps"]], ["生成初稿", "整理终稿"])
            self.assertTrue(all(step["status"] == "done" for step in task["steps"]))

            generated_prompt = generated_service.build_prompt_context(
                profile_user_id="master",
                session_id="qq-private",
                limit=8,
            )
            self.assertIn("gen_001：后台总结.md", generated_prompt)
            self.assertIn("gen_002：后台总结_终稿.md", generated_prompt)
            self.assertIn("来源工具：compose_file", generated_prompt)
            self.assertIn("来源工具：revise_generated_file", generated_prompt)

            task_prompt = workspace.build_prompt_context(
                profile_user_id="master",
                session_id="qq-private",
            )
            self.assertIn("状态: completed", task_prompt)
            self.assertIn("后台工坊: document_agent / done", task_prompt)
            self.assertIn("worker_completed", task_prompt)
            self.assertIn("gen_002(md / 后台总结_终稿)", task_prompt)
            self.assertIn("交接状态: 完成，等待前台助手交付/确认", task_prompt)
            self.assertIn("交接摘要: 终稿已经准备好，建议前台助手先请用户确认是否发送。", task_prompt)
            self.assertIn("交接候选: gen_002(md / 后台总结_终稿)", task_prompt)
            self.assertIn("建议接手: 先请用户确认要不要发送、以及具体发送哪份结果", task_prompt)

            inspect_handler = ManageTaskWorkspaceToolHandler(task_workspace_service=workspace)
            inspect_result = inspect_handler.execute(
                call={
                    "type": "manage_task_workspace",
                    "action": "inspect",
                    "task_id": delegated.task_id,
                },
                context=ToolExecutionContext(
                    profile_user_id="master",
                    session_id="qq-private",
                    now_ts=140,
                    visual_payload={},
                ),
            )
            self.assertIn("后台工坊：document_agent / done", inspect_result.followup_context)
            self.assertIn("当前产物：gen_001；gen_002", inspect_result.followup_context)
            self.assertIn("最近事件：worker_completed", inspect_result.followup_context)
            self.assertIn("交接状态: 完成，等待前台助手交付/确认", inspect_result.followup_context)
            self.assertIn("建议接手: 先请用户确认要不要发送、以及具体发送哪份结果", inspect_result.followup_context)

            events = workspace.list_events(task_id=delegated.task_id)
            completed_events = [event for event in events if event["event_type"] == "worker_completed"]
            self.assertEqual(completed_events[-1]["payload"]["handoff"]["next_action"], "ask_confirmation")

            send_handler = SendFileToolHandler(generated_file_service=generated_service)
            send_result = send_handler.execute(
                call=send_handler.normalize_call({"type": "send_file", "targets": ["gen_002"]}) or {},
                context=ToolExecutionContext(
                    profile_user_id="master",
                    session_id="qq-private",
                    now_ts=150,
                    visual_payload={},
                ),
            )
            self.assertEqual([event["type"] for event in send_result.stream_events], ["file_ready"])
            self.assertEqual(send_result.stream_events[0]["file"]["handle"], "gen_002")

    def test_worker_blocked_returns_question_back_to_akane(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            workspace = TaskWorkspaceService(store)
            llm = FakeLLM(
                [
                    {
                        "status": "blocked",
                        "message": "缺少最终输出格式。",
                        "question": "需要你指定这次导出 md 还是 docx。",
                        "tool_call": None,
                        "handoff": {
                            "summary": "已经确认材料能整理，但必须先确定最终输出格式。",
                            "next_action": "ask_user",
                            "user_question": "需要你指定这次导出 md 还是 docx。",
                        },
                        "steps": [{"title": "确认输出格式", "status": "waiting_user"}],
                    }
                ]
            )
            compose_handler = FakeComposeFileHandler()
            worker = TaskWorkerService(
                llm=llm,  # type: ignore[arg-type]
                task_workspace_service=workspace,
                background_tasks=None,
                tool_handlers_provider=lambda: {"compose_file": compose_handler},
                attachment_context_builder=lambda _profile, _session: "",
                generated_context_builder=lambda _profile, _session: "",
                record_tool_artifacts=lambda **_kwargs: ([], ""),
            )
            delegated = worker.delegate_task(
                profile_user_id="master",
                session_id="qq-private",
                brief="整理当前材料。",
                agent="document_agent",
                normalized_goal="整理当前材料。",
                auto_start=False,
                timestamp=100,
            )

            summary = worker.run_task_sync(
                task_id=delegated.task_id,
                profile_user_id="master",
                session_id="qq-private",
                assigned_agent="document_agent",
                now_ts=110,
            )

            self.assertEqual(summary.status, "blocked")
            task = workspace.get_task(delegated.task_id)
            self.assertIsNotNone(task)
            assert task is not None
            self.assertEqual(task["status"], "waiting_user")
            self.assertEqual(task["pending_question"]["text"], "需要你指定这次导出 md 还是 docx。")
            self.assertEqual(compose_handler.calls, [])

            prompt = workspace.build_prompt_context(
                profile_user_id="master",
                session_id="qq-private",
            )
            self.assertIn("等待用户确认: 需要你指定这次导出 md 还是 docx。", prompt)
            self.assertIn("交接状态: 阻塞，等待用户回答", prompt)
            self.assertIn("交接摘要: 已经确认材料能整理，但必须先确定最终输出格式。", prompt)
            self.assertIn("需要问用户: 需要你指定这次导出 md 还是 docx。", prompt)
            self.assertIn("建议接手: 把交接问题改成自然口吻直接问用户", prompt)
            events = workspace.list_events(task_id=delegated.task_id)
            blocked_events = [event for event in events if event["event_type"] == "worker_blocked"]
            self.assertEqual(len(blocked_events), 1)
            self.assertTrue(blocked_events[0]["requires_user"])
            self.assertEqual(blocked_events[0]["message"], "缺少最终输出格式。")
            self.assertEqual(blocked_events[0]["payload"]["handoff"]["next_action"], "ask_user")

            inspect_handler = ManageTaskWorkspaceToolHandler(task_workspace_service=workspace)
            inspect_result = inspect_handler.execute(
                call={
                    "type": "manage_task_workspace",
                    "action": "inspect",
                    "task_id": delegated.task_id,
                },
                context=ToolExecutionContext(
                    profile_user_id="master",
                    session_id="qq-private",
                    now_ts=140,
                    visual_payload={},
                ),
            )
            self.assertIn("需要问用户: 需要你指定这次导出 md 还是 docx。", inspect_result.followup_context)

    def test_worker_partial_handoff_reports_existing_artifacts_and_remaining_work(self) -> None:
        previous_rounds = getattr(config, "MAX_TASK_WORKER_ROUNDS", 3)
        config.MAX_TASK_WORKER_ROUNDS = 1
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                store = MemoryStore(Path(temp_dir))
                workspace = TaskWorkspaceService(store)
                engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
                engine.store = store
                engine.task_workspace_service = workspace
                llm = FakeLLM(
                    [
                        {
                            "status": "continue",
                            "message": "先产出一个初稿，后面还要整理终稿。",
                            "tool_call": {
                                "type": "compose_file",
                                "output_title": "后台总结",
                                "output_format": "md",
                                "content_markdown": "# 后台总结\n\n初稿。",
                            },
                            "steps": [
                                {"title": "生成初稿", "status": "running"},
                                {"title": "整理终稿", "status": "queued"},
                            ],
                        }
                    ]
                )
                compose_handler = FakeComposeFileHandler()
                worker = TaskWorkerService(
                    llm=llm,  # type: ignore[arg-type]
                    task_workspace_service=workspace,
                    background_tasks=None,
                    tool_handlers_provider=lambda: {"compose_file": compose_handler},
                    attachment_context_builder=lambda _profile, _session: "",
                    generated_context_builder=lambda _profile, _session: "",
                    record_tool_artifacts=engine._record_tool_result_artifacts_in_task_workspace,
                )
                delegated = worker.delegate_task(
                    profile_user_id="master",
                    session_id="qq-private",
                    brief="先生成初稿，再整理终稿。",
                    agent="document_agent",
                    normalized_goal="生成初稿和终稿。",
                    auto_start=False,
                    timestamp=100,
                )

                summary = worker.run_task_sync(
                    task_id=delegated.task_id,
                    profile_user_id="master",
                    session_id="qq-private",
                    assigned_agent="document_agent",
                    now_ts=110,
                )

                self.assertEqual(summary.status, "paused")
                task = workspace.get_task(delegated.task_id)
                self.assertIsNotNone(task)
                assert task is not None
                self.assertEqual(task["status"], "running")
                self.assertEqual(task["artifacts"][0]["id"], "gen_001")
                self.assertEqual(task["metadata"]["workshop"]["status"], "paused")
                handoff = task["metadata"]["workshop"]["handoff"]
                self.assertEqual(handoff["status"], "partial")
                self.assertEqual(handoff["next_action"], "continue_work")
                self.assertEqual(handoff["active_steps"], ["生成初稿"])
                self.assertEqual(handoff["remaining_steps"], ["整理终稿"])

                prompt = workspace.build_prompt_context(
                    profile_user_id="master",
                    session_id="qq-private",
                )
                self.assertIn("交接状态: 部分完成，等待前台助手接手推进", prompt)
                self.assertIn("还在推进: 生成初稿", prompt)
                self.assertIn("仍需处理: 整理终稿", prompt)
                self.assertIn("交接候选: gen_001(md / 后台总结)", prompt)
                self.assertIn("要不要先发送现有成果", prompt)

                events = workspace.list_events(task_id=delegated.task_id)
                round_limit_events = [event for event in events if event["event_type"] == "worker_round_limit"]
                self.assertEqual(round_limit_events[-1]["payload"]["handoff"]["status"], "partial")
        finally:
            config.MAX_TASK_WORKER_ROUNDS = previous_rounds

    def test_delegate_tool_normalizes_and_starts_worker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            workspace = TaskWorkspaceService(store)
            llm = FakeLLM([{"status": "done", "message": "完成。", "tool_call": None}])
            worker = TaskWorkerService(
                llm=llm,  # type: ignore[arg-type]
                task_workspace_service=workspace,
                background_tasks=None,
                tool_handlers_provider=lambda: {},
                attachment_context_builder=lambda _profile, _session: "",
                generated_context_builder=lambda _profile, _session: "",
                record_tool_artifacts=lambda **_kwargs: ([], ""),
            )
            handler = DelegateTaskToolHandler(task_worker_service=worker)
            instruction = handler.build_prompt_instruction()
            self.assertIn("不要把一句话就能直接完成的小事委派出去", instruction)
            self.assertIn("发送已有文件", instruction)
            self.assertIn("直接调用对应处理工具", instruction)
            self.assertIn("简短告诉用户后台已经开始", instruction)
            call = handler.normalize_call(
                {
                    "type": "delegate_task",
                    "agent": "doc_agent",
                    "brief": "整理成三种格式。",
                    "inputs": ["file_001"],
                    "expected_outputs": ["md", "docx", "txt"],
                }
            )
            self.assertIsNotNone(call)
            assert call is not None

            result = handler.execute(
                call=call,
                context=ToolExecutionContext(
                    profile_user_id="master",
                    session_id="qq-private",
                    now_ts=200,
                    visual_payload={},
                    current_user_source_id="msg_200",
                ),
            )

            self.assertEqual(result.tool_type, "delegate_task")
            self.assertEqual(result.state_updates["assigned_agent"], "document_agent")
            self.assertFalse(result.state_updates["started"])
            self.assertIn("后台工坊已接收任务", result.followup_context)
            self.assertIn("只用一句简短前台话", result.followup_context)
            self.assertIn("不要长篇解释内部流程或 task_id", result.followup_context)
            self.assertIn("不要声称产物已经完成", result.followup_context)


if __name__ == "__main__":
    unittest.main()
