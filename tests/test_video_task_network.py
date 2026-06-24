from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import config
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.store import MemoryStore
from companion_v01.task_workspace import TaskWorkspaceService
from companion_v01.task_worker import TaskWorkerService
from companion_v01.tool_runtime import BaseToolHandler, ToolExecutionContext, ToolExecutionResult


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


class FakeVideoArtifactRegistry:
    def __init__(self) -> None:
        self.attachments: list[dict[str, Any]] = []
        self.generated: list[dict[str, Any]] = []
        self.tool_calls: list[str] = []
        self._attachment_no = 1
        self._generated_no = 1

    def attachment_ready(self, *, title: str = "测试视频", kind: str = "video", ext: str = ".mp4") -> dict[str, Any]:
        handle = f"{kind}_{self._attachment_no:03d}"
        self._attachment_no += 1
        item = {
            "attachment_id": f"attachment::{handle}",
            "attachment_handle": handle,
            "status": "ready",
            "kind": kind,
            "summary_title": title,
            "origin_name": f"{title}{ext}",
            "file_ext": ext,
            "file_size": 2048,
            "mime_type": "video/mp4" if kind == "video" else "audio/wav",
            "source": "remote_url",
        }
        self.attachments.append(item)
        return {"type": "attachment_remote_media_ready", "item": dict(item)}

    def generated_ready(self, *, tool_type: str, title: str, output_format: str) -> dict[str, Any]:
        handle = f"gen_{self._generated_no:03d}"
        self._generated_no += 1
        generated = {
            "generated_id": f"generated::{handle}",
            "generated_handle": handle,
            "status": "ready",
            "output_title": title,
            "output_format": output_format,
            "file_ext": output_format,
            "file_size": 4096,
            "created_by_tool": tool_type,
        }
        self.generated.append(generated)
        return {
            "type": "generated_file_ready",
            "generated_file": dict(generated),
            "send_to_user": False,
        }

    def attachment_context(self, _profile_user_id: str, _session_id: str) -> str:
        if not self.attachments:
            return ""
        lines = ["【临时附件区】"]
        for item in self.attachments:
            lines.append(
                f"- {item['attachment_handle']}：{item['summary_title']} "
                f"({item['kind']} / {item['file_ext']} / {item['file_size']} bytes)"
            )
        return "\n".join(lines)

    def generated_context(self, _profile_user_id: str, _session_id: str) -> str:
        if not self.generated:
            return ""
        lines = ["【生成文件区】"]
        for item in self.generated:
            lines.append(
                f"- {item['generated_handle']}：{item['output_title']}.{item['output_format']} "
                f"来源工具：{item['created_by_tool']}"
            )
        return "\n".join(lines)


class FakeVideoToolHandler(BaseToolHandler):
    def __init__(self, *, tool_type: str, registry: FakeVideoArtifactRegistry) -> None:
        self.tool_type = tool_type
        self.registry = registry
        self.calls: list[dict[str, Any]] = []

    def build_prompt_instruction(self) -> str:
        return f"- {self.tool_type}：测试用视频路网工具。"

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "") != self.tool_type:
            return None
        return dict(value)

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        self.calls.append(dict(call))
        self.registry.tool_calls.append(self.tool_type)
        events: list[dict[str, Any]] = []
        followup = ""

        if self.tool_type == "fetch_media_from_url":
            events.append(self.registry.attachment_ready(title=str(call.get("preferred_title") or "测试视频")))
            followup = "已下载视频 video_001。"
        elif self.tool_type == "convert_media_file":
            events.append(
                self.registry.generated_ready(
                    tool_type=self.tool_type,
                    title=str(call.get("output_title") or "视频音频轨"),
                    output_format=str(call.get("output_format") or "wav").lstrip("."),
                )
            )
            followup = f"已从 {call.get('source_id')} 提取/转换音频。"
        elif self.tool_type == "transcribe_media":
            output_format = str(call.get("output_format") or "md").lstrip(".")
            title = "视频字幕" if output_format == "srt" else "视频转写稿"
            events.append(self.registry.generated_ready(tool_type=self.tool_type, title=title, output_format=output_format))
            followup = f"已转写 {call.get('source_ids') or call.get('source_id')}。"
        elif self.tool_type == "compose_file":
            events.append(
                self.registry.generated_ready(
                    tool_type=self.tool_type,
                    title=str(call.get("output_title") or "视频总结"),
                    output_format=str(call.get("output_format") or "md").lstrip("."),
                )
            )
            followup = "已整理 Markdown 总结。"
        elif self.tool_type == "separate_audio_stems":
            events.append(self.registry.generated_ready(tool_type=self.tool_type, title="视频_人声", output_format="wav"))
            events.append(self.registry.generated_ready(tool_type=self.tool_type, title="视频_伴奏", output_format="wav"))
            followup = f"已从 {call.get('source_id')} 分离人声和伴奏。"
        elif self.tool_type == "clean_voice_track":
            events.append(self.registry.generated_ready(tool_type=self.tool_type, title="视频_降噪人声", output_format="wav"))
            followup = f"已净化 {call.get('source_id')}。"
        elif self.tool_type == "prepare_voice_dataset":
            events.append(self.registry.generated_ready(tool_type=self.tool_type, title="训练素材包", output_format="zip"))
            followup = f"已整理训练素材 {call.get('source_ids') or call.get('source_id')}。"

        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=events,
            followup_context=followup,
        )


class FakeSendFileHandler(BaseToolHandler):
    tool_type = "send_file"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def build_prompt_instruction(self) -> str:
        return "- send_file：测试用发送工具。"

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "") != self.tool_type:
            return None
        targets = value.get("targets") if isinstance(value.get("targets"), list) else [value.get("target")]
        return {"type": self.tool_type, "targets": [str(item) for item in targets if str(item or "").strip()]}

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        self.calls.append(dict(call))
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {"type": "file_ready", "file": {"handle": target, "name": target}}
                for target in list(call.get("targets") or [])
            ],
            followup_context="Akane 已按精确 handle 发送文件。",
        )


class VideoTaskNetworkTests(unittest.TestCase):
    def test_worker_can_choose_distinct_video_tool_routes_without_fixed_pipeline(self) -> None:
        cases = [
            {
                "name": "download_original_video",
                "goal": "下载视频链接并把原视频发回给主人。",
                "outputs": [
                    self._continue("fetch_media_from_url", {"url": "https://example.test/video.mp4", "preferred_title": "测试视频"}, "下载视频"),
                    self._done("原视频已经下载好，交给前台助手发送。", "send_to_user", ["下载视频"]),
                ],
                "expected_sequence": ["fetch_media_from_url"],
                "expected_artifacts": ["video_001"],
                "final_send_target": "video_001",
            },
            {
                "name": "extract_audio_from_video_link",
                "goal": "从视频链接里提取音频。",
                "outputs": [
                    self._continue("fetch_media_from_url", {"url": "https://example.test/video.mp4", "preferred_title": "测试视频"}, "下载视频"),
                    self._continue(
                        "convert_media_file",
                        {"source_id": "video_001", "output_format": "wav", "output_title": "视频音频轨"},
                        "提取音频",
                        done=["下载视频"],
                    ),
                    self._done("音频轨已经提取好。", "ask_confirmation", ["下载视频", "提取音频"]),
                ],
                "expected_sequence": ["fetch_media_from_url", "convert_media_file"],
                "expected_artifacts": ["video_001", "gen_001"],
                "final_send_target": "gen_001",
            },
            {
                "name": "transcribe_video",
                "goal": "把已有视频转写成文字稿。",
                "outputs": [
                    self._continue("transcribe_media", {"source_ids": ["video_001"], "output_format": "md"}, "转写视频"),
                    self._done("视频文字稿已经生成。", "ask_confirmation", ["转写视频"]),
                ],
                "expected_sequence": ["transcribe_media"],
                "expected_artifacts": ["gen_001"],
                "final_send_target": "gen_001",
            },
            {
                "name": "summarize_video_markdown",
                "goal": "把视频内容总结成 Markdown 文件。",
                "outputs": [
                    self._continue("transcribe_media", {"source_ids": ["video_001"], "output_format": "md"}, "转写视频"),
                    self._continue(
                        "compose_file",
                        {"output_title": "视频总结", "output_format": "md", "source_ids": ["gen_001"]},
                        "整理总结",
                        done=["转写视频"],
                    ),
                    self._done("Markdown 总结已经准备好。", "ask_confirmation", ["转写视频", "整理总结"]),
                ],
                "expected_sequence": ["transcribe_media", "compose_file"],
                "expected_artifacts": ["gen_001", "gen_002"],
                "final_send_target": "gen_002",
            },
            {
                "name": "subtitle_file",
                "goal": "给视频生成字幕文件。",
                "outputs": [
                    self._continue("transcribe_media", {"source_ids": ["video_001"], "output_format": "srt"}, "生成字幕"),
                    self._done("字幕文件已经生成。", "ask_confirmation", ["生成字幕"]),
                ],
                "expected_sequence": ["transcribe_media"],
                "expected_artifacts": ["gen_001"],
                "final_send_target": "gen_001",
            },
            {
                "name": "voice_dataset_pipeline",
                "goal": "从视频里分离人声、降噪，并切片准备训练素材。",
                "outputs": [
                    self._continue("separate_audio_stems", {"source_id": "video_001"}, "分离人声"),
                    self._continue("clean_voice_track", {"source_id": "gen_001"}, "降噪人声", done=["分离人声"]),
                    self._continue(
                        "prepare_voice_dataset",
                        {"source_ids": ["gen_003"], "dataset_name": "akane_voice"},
                        "切片整理训练素材",
                        done=["分离人声", "降噪人声"],
                    ),
                    self._done("训练素材包已经准备好。", "ask_confirmation", ["分离人声", "降噪人声", "切片整理训练素材"]),
                ],
                "expected_sequence": ["separate_audio_stems", "clean_voice_track", "prepare_voice_dataset"],
                "expected_artifacts": ["gen_001", "gen_002", "gen_003", "gen_004"],
                "final_send_target": "gen_004",
            },
        ]

        observed_sequences: set[tuple[str, ...]] = set()
        for case in cases:
            with self.subTest(case=case["name"]):
                result = self._run_route(case)
                observed_sequences.add(tuple(result["actual_sequence"]))
                self.assertEqual(result["actual_sequence"], case["expected_sequence"])
                self.assertEqual(result["artifact_ids"], case["expected_artifacts"])
                self.assertNotIn("- send_file", result["first_system_prompt"])
                self.assertEqual(result["worker_send_calls"], [])
                self.assertTrue(result["send_result"].stream_events)
                self.assertEqual(result["send_result"].stream_events[0]["file"]["handle"], case["final_send_target"])
                self.assertIn("交接状态: 完成，等待前台助手交付/确认", result["task_prompt"])
                self.assertIn(case["final_send_target"], result["task_prompt"])
                for handle in case["expected_artifacts"]:
                    self.assertIn(handle, result["task_prompt"])
                for handle in case["expected_artifacts"]:
                    if handle.startswith("gen_"):
                        self.assertIn(handle, result["generated_context"])
                self.assertTrue(
                    any(case["expected_artifacts"][0] in call["user"] for call in result["llm_calls"][1:]),
                    msg=f"{case['name']} did not expose first intermediate artifact to later worker rounds",
                )

        self.assertGreaterEqual(len(observed_sequences), 5)

    def test_worker_media_routes_keep_delivery_on_akane_side(self) -> None:
        result = self._run_route(
            {
                "name": "attempted_worker_delivery_guard",
                "goal": "提取视频音频并交给前台助手发送。",
                "outputs": [
                    self._continue(
                        "convert_media_file",
                        {
                            "source_id": "video_001",
                            "output_format": "wav",
                            "output_title": "视频音频轨",
                            "send_to_user": True,
                        },
                        "提取音频",
                    ),
                    self._done("音频轨已经准备好，请前台助手统一发送。", "send_to_user", ["提取音频"]),
                ],
                "expected_sequence": ["convert_media_file"],
                "expected_artifacts": ["gen_001"],
                "final_send_target": "gen_001",
            }
        )

        convert_calls = result["handler_calls"]["convert_media_file"]
        self.assertEqual(len(convert_calls), 1)
        self.assertFalse(convert_calls[0]["send_to_user"])
        self.assertEqual(result["worker_send_calls"], [])
        self.assertEqual(result["send_result"].stream_events[0]["file"]["handle"], "gen_001")

    def _run_route(self, case: dict[str, Any]) -> dict[str, Any]:
        with patch.object(config, "MAX_TASK_WORKER_ROUNDS", 5, create=True), tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            workspace = TaskWorkspaceService(store)
            engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
            engine.store = store
            engine.task_workspace_service = workspace
            registry = FakeVideoArtifactRegistry()
            if list(case["expected_sequence"] or [""])[0] != "fetch_media_from_url":
                registry.attachment_ready(title="测试视频")
            llm = FakeLLM(list(case["outputs"]))
            media_tools = {
                tool_type: FakeVideoToolHandler(tool_type=tool_type, registry=registry)
                for tool_type in [
                    "fetch_media_from_url",
                    "convert_media_file",
                    "transcribe_media",
                    "compose_file",
                    "separate_audio_stems",
                    "clean_voice_track",
                    "prepare_voice_dataset",
                ]
            }
            send_handler = FakeSendFileHandler()

            worker = TaskWorkerService(
                llm=llm,  # type: ignore[arg-type]
                task_workspace_service=workspace,
                background_tasks=None,
                tool_handlers_provider=lambda: {**media_tools, "send_file": send_handler},
                attachment_context_builder=registry.attachment_context,
                generated_context_builder=registry.generated_context,
                record_tool_artifacts=engine._record_tool_result_artifacts_in_task_workspace,
            )
            delegated = worker.delegate_task(
                profile_user_id="master",
                session_id="qq-private",
                brief=str(case["goal"]),
                agent="media_agent",
                normalized_goal=str(case["goal"]),
                auto_start=False,
                timestamp=100,
            )

            summary = worker.run_task_sync(
                task_id=delegated.task_id,
                profile_user_id="master",
                session_id="qq-private",
                assigned_agent="media_agent",
                now_ts=110,
            )
            self.assertEqual(summary.status, "done")

            task = workspace.get_task(delegated.task_id)
            self.assertIsNotNone(task)
            assert task is not None
            send_call = send_handler.normalize_call({"type": "send_file", "targets": [case["final_send_target"]]})
            self.assertIsNotNone(send_call)
            assert send_call is not None
            send_result = send_handler.execute(
                call=send_call,
                context=ToolExecutionContext(
                    profile_user_id="master",
                    session_id="qq-private",
                    now_ts=200,
                    visual_payload={},
                ),
            )

            return {
                "actual_sequence": [
                    str(tool_type)
                    for tool_type in registry.tool_calls
                ],
                "artifact_ids": [str(item.get("id") or "") for item in list(task.get("artifacts") or [])],
                "first_system_prompt": llm.calls[0]["system"],
                "llm_calls": list(llm.calls),
                "generated_context": registry.generated_context("master", "qq-private"),
                "task_prompt": workspace.build_prompt_context(profile_user_id="master", session_id="qq-private"),
                "worker_send_calls": list(send_handler.calls[:-1]),
                "send_result": send_result,
                "handler_calls": {tool_type: list(handler.calls) for tool_type, handler in media_tools.items()},
            }

    def _continue(
        self,
        tool_type: str,
        call: dict[str, Any],
        step_title: str,
        *,
        done: list[str] | None = None,
    ) -> dict[str, Any]:
        steps = [{"title": title, "status": "done"} for title in list(done or [])]
        steps.append({"title": step_title, "status": "running"})
        tool_call = {"type": tool_type, **call}
        return {
            "status": "continue",
            "message": f"继续执行：{step_title}",
            "tool_call": tool_call,
            "steps": steps,
        }

    def _done(self, message: str, next_action: str, done_steps: list[str]) -> dict[str, Any]:
        return {
            "status": "done",
            "message": message,
            "tool_call": None,
            "handoff": {
                "summary": message,
                "next_action": next_action,
            },
            "steps": [{"title": title, "status": "done"} for title in done_steps],
        }


if __name__ == "__main__":
    unittest.main()
