"""Focused tests: video-understanding Skill over the real exec_run -> gen_* -> load_material loop.

Covers the QQ video understanding closure slice:
  1. video-understanding is visible in the bundled Skill catalog.
  2. Skill body is a real workflow (ffprobe probe, contact-sheet overview,
     timestamped deep-read frames, output_globs -> gen_*, load_material,
     transcribe_media, honest fallbacks)
     and leaks no absolute path.
  3. Real ffmpeg E2E: a QQ-style video attachment (kind=file, file_* handle)
     is probed with ffprobe through exec_run.
  4. Real ffmpeg E2E: frames are extracted, registered as gen_*, and
     load_material returns real image data for the multimodal round.
  5. load_material on the raw video handle is an honest unresolved (no fake image).
  6. A legacy vid_* handle is rejected before the command runs (contract fix).
  7. Failed extraction registers no fake gen_* handle.
  8. The load_skill + exec_run + load_material round survives MemCore settlement
     and is reloadable via open_memory without path leaks.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from companion_v01.attachment_inbox import AttachmentInboxService
from companion_v01.execution_resources import ExecutionResourceBridge
from companion_v01.execution_run import ExecutionRunOwner
from companion_v01.execution_specs import (
    ARTIFACT_STATUS_NOT_REGISTERED,
    ARTIFACT_STATUS_REGISTERED,
    EXEC_STATUS_COMPLETED,
    EXEC_STATUS_FAILED,
)
from companion_v01.generated_files import GeneratedFileService
from companion_v01.image_materials import SessionImageMaterialResolver
from companion_v01.local_capability_config import save_approval_policy_config
from companion_v01.skill_runtime import SkillRegistry, SKILL_BUNDLED_MOUNT
from companion_v01.store import MemoryStore
from companion_v01.tool_handlers.attachments import LoadMaterialToolHandler
from companion_v01.tool_handlers.core import ToolExecutionContext
from companion_v01.tool_handlers.execution import ExecRunToolHandler

ROOT = Path(__file__).resolve().parents[1]

SKILL_NAME = "video-understanding"

_FFMPEG = shutil.which("ffmpeg")
_FFPROBE = shutil.which("ffprobe")


def _make_synthetic_video(path: Path) -> None:
    """3s 320x240 video with a sine audio track; mpeg4 avoids libx264 dependency."""
    subprocess.run(
        [
            _FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=3:size=320x240:rate=15",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-c:v",
            "mpeg4",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )


class VideoUnderstandingCatalogTests(unittest.TestCase):
    """Items 1-2: bundled skill presence and body contract."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.registry = SkillRegistry(
            bundled_root=ROOT / "skills",
            managed_root=root / "managed",
            execution_workspace_root=root / "workspace",
        )

    def test_video_understanding_is_in_bundled_catalog(self) -> None:
        snapshot = self.registry.snapshot()
        entry = snapshot.by_name().get(SKILL_NAME)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.source, "bundled")
        self.assertIn("video", entry.description.lower())

    def test_skill_body_is_a_real_workflow_without_absolute_paths(self) -> None:
        loaded = self.registry.load(SKILL_NAME)
        self.assertEqual(loaded.status, "loaded")
        body = loaded.content
        self.assertIn("ffprobe", body)
        self.assertIn("ffmpeg", body)
        self.assertIn("contact sheet", body)
        self.assertIn("overview_4x3.jpg", body)
        self.assertIn("tile=4x3", body)
        self.assertIn("fps=12/DURATION", body)
        self.assertIn("-ss HH:MM:SS", body)
        self.assertIn("t_000s.jpg", body)
        self.assertIn("input_resources", body)
        self.assertIn("output_globs", body)
        self.assertIn("generated_resources", body)
        self.assertIn("load_material", body)
        self.assertIn("transcribe_media", body)
        self.assertIn("open_memory", body)
        self.assertIn("When NOT to load", body)
        self.assertIn("exec_run", body)
        self.assertIn("fresh per-run resource workspace", body)
        self.assertIn("Repeat the exact video handle", body)
        self.assertIn('output_globs=["t_*.jpg"]', body)
        self.assertNotIn("python -c", body)
        self.assertNotIn("C:", body)
        self.assertNotIn("C:\\", body)
        self.assertNotIn(str(ROOT), body)
        self.assertNotIn("base64", body)
        self.assertEqual(loaded.execution_cwd, f"alias:{SKILL_BUNDLED_MOUNT}")

    def test_skill_description_carries_routing_boundaries(self) -> None:
        snapshot = self.registry.snapshot()
        description = snapshot.by_name().get(SKILL_NAME).description
        self.assertIn("what a video actually shows or says", description)
        self.assertIn("specific time", description)
        self.assertIn("video attachment is available", description)


class VideoUnderstandingExecutionLoopTests(unittest.TestCase):
    """Items 3-7: real ffprobe/ffmpeg through the exec_run resource path."""

    def setUp(self) -> None:
        if not (_FFMPEG and _FFPROBE):
            self.skipTest("ffmpeg/ffprobe unavailable")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.workspace = root / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.store = MemoryStore(root / "db")
        self.attachment_service = AttachmentInboxService(store=self.store, base_dir=root)
        self.generated_service = GeneratedFileService(
            base_dir=root / "generated_files",
            store=self.store,
            attachment_service=self.attachment_service,
        )
        self.bridge = ExecutionResourceBridge(
            generated_file_service=self.generated_service,
            workspace_root=self.workspace,
        )
        save_approval_policy_config(
            base_dir=root,
            profile_user_id="alice",
            payload={"defaultMode": "trusted_auto_allow"},
        )
        from companion_v01.execution_local import TrustedLocalExecutor

        self.provider = TrustedLocalExecutor(
            workspace_root=self.workspace,
            run_log_dir=root / "runlogs",
            provider_id="local",
        )
        self.handler = ExecRunToolHandler(
            execution_provider=self.provider,
            config_base_dir=root,
            approval_store=None,
            resource_bridge=self.bridge,
        )
        self.material_handler = LoadMaterialToolHandler(
            image_material_resolver=SessionImageMaterialResolver(
                attachment_service=self.attachment_service,
                generated_file_service=self.generated_service,
            )
        )
        self.owner = ExecutionRunOwner(profile_user_id="alice", session_id="s1", provider_id="local")
        video = root / "source.mp4"
        _make_synthetic_video(video)
        self.handle = self._register_video(video)

    def _register_video(self, path: Path) -> str:
        stored = path.parent / "master" / path.name
        stored.parent.mkdir(parents=True, exist_ok=True)
        stored.write_bytes(path.read_bytes())
        item = self.store.add_attachment_inbox_item(
            profile_user_id="alice",
            session_id="s1",
            kind="file",
            source="test",
            status="ready",
            origin_name=path.name,
            mime_type="video/mp4",
            file_ext=".mp4",
            file_size=path.stat().st_size,
            storage_relpath=f"master/{path.name}",
            detail={"file_kind": "video"},
            timestamp=100,
        )
        handle = str(item["attachment_handle"])
        self.assertTrue(handle.startswith("file_"), handle)
        return handle

    def _context(self) -> ToolExecutionContext:
        return ToolExecutionContext(
            profile_user_id="alice",
            session_id="s1",
            now_ts=0,
            visual_payload={},
            client_mode="qq",
        )

    def test_probe_video_specs_through_exec_run(self) -> None:
        result = self.handler.execute(
            call={
                "type": "exec_run",
                "command": "ffprobe -v error -show_format -show_streams -print_format json inputs/source.mp4",
                "initial_wait_seconds": 2,
                "input_resources": [{"handle": self.handle, "as": "inputs/source.mp4"}],
            },
            context=self._context(),
        )
        event = result.stream_events[0]
        self.assertEqual(event["status"], EXEC_STATUS_COMPLETED)
        self.assertIn('"codec_type": "video"', result.followup_context)
        self.assertIn('"codec_type": "audio"', result.followup_context)
        self.assertIn('"width": 320', result.followup_context)
        self.assertIn('"height": 240', result.followup_context)
        self.assertIn("duration", result.followup_context)
        self.assertNotIn(str(self._tmp.name), result.followup_context)

    def test_extract_frames_registers_gen_then_load_material_returns_images(self) -> None:
        result = self.handler.execute(
            call={
                "type": "exec_run",
                "command": (
                    f'"{sys.executable}" -c "from pathlib import Path; '
                    "Path('outputs').mkdir(parents=True, exist_ok=True)\" && "
                    "ffmpeg -hide_banner -loglevel error -y -i inputs/source.mp4 "
                    "-ss 00:00:00 -frames:v 1 outputs/t_000s.jpg && "
                    "ffmpeg -hide_banner -loglevel error -y -i inputs/source.mp4 "
                    "-ss 00:00:01 -frames:v 1 outputs/t_001s.jpg"
                ),
                "initial_wait_seconds": 2,
                "input_resources": [{"handle": self.handle, "as": "inputs/source.mp4"}],
                "output_globs": ["outputs/t_*.jpg"],
            },
            context=self._context(),
        )
        state = result.state_updates["capability_execution"]
        self.assertEqual(state["status"], EXEC_STATUS_COMPLETED)
        self.assertEqual(state.get("artifact_status"), ARTIFACT_STATUS_REGISTERED)
        resources = state.get("generated_resources", [])
        self.assertEqual(len(resources), 2, resources)
        handles = [str(item["handle"]) for item in resources]
        self.assertTrue(all(item.startswith("gen_") for item in handles), handles)
        self.assertEqual({item["name"] for item in resources}, {"t_000s.jpg", "t_001s.jpg"})
        self.assertEqual({item["media_type"] for item in resources}, {"image/jpeg"})
        self.assertIn("t_000s.jpg", result.followup_context)
        self.assertIn("t_001s.jpg", result.followup_context)

        loaded = self.material_handler.execute(
            call={
                "type": "load_material",
                "targets": handles,
                "purpose": "查看视频关键帧内容",
            },
            context=self._context(),
        )
        event = loaded.stream_events[0]
        self.assertEqual(event["status"], "ready")
        self.assertEqual(event["image_count"], 2)
        images = [item for item in loaded.model_image_inputs if isinstance(item, dict)]
        self.assertEqual(len(images), 2)
        self.assertTrue(all(item["data_url"].startswith("data:image/jpeg;base64,") for item in images), images)
        self.assertEqual({item["attachment_handle"] for item in images}, set(handles))
        self.assertIn("原生多模态通道", loaded.followup_context)

    def test_contact_sheet_registers_one_image_then_loads_as_one_visual_input(self) -> None:
        result = self.handler.execute(
            call={
                "type": "exec_run",
                "command": (
                    "ffmpeg -hide_banner -loglevel error -y -i inputs/source.mp4 "
                    '-vf "fps=3,scale=160:120:force_original_aspect_ratio=decrease,'
                    'pad=160:120:(ow-iw)/2:(oh-ih)/2,tile=3x3:padding=2:margin=2" '
                    "-frames:v 1 overview_3x3.jpg"
                ),
                "initial_wait_seconds": 2,
                "input_resources": [{"handle": self.handle, "as": "inputs/source.mp4"}],
                "output_globs": ["overview_3x3.jpg"],
            },
            context=self._context(),
        )
        state = result.state_updates["capability_execution"]
        self.assertEqual(state["status"], EXEC_STATUS_COMPLETED)
        self.assertEqual(state.get("artifact_status"), ARTIFACT_STATUS_REGISTERED)
        resources = state.get("generated_resources", [])
        self.assertEqual(len(resources), 1, resources)
        self.assertEqual(resources[0]["name"], "overview_3x3.jpg")
        self.assertEqual(resources[0]["media_type"], "image/jpeg")

        loaded = self.material_handler.execute(
            call={
                "type": "load_material",
                "targets": [resources[0]["handle"]],
                "purpose": "3x3 视频概览联系表，按从左到右、从上到下查看",
            },
            context=self._context(),
        )
        self.assertEqual(loaded.stream_events[0]["status"], "ready")
        self.assertEqual(loaded.stream_events[0]["image_count"], 1)
        self.assertEqual(len(loaded.model_image_inputs), 1)

    def test_load_material_on_raw_video_handle_is_honest_unresolved(self) -> None:
        result = self.material_handler.execute(
            call={
                "type": "load_material",
                "targets": [self.handle],
                "purpose": "直接看视频内容",
            },
            context=self._context(),
        )
        event = result.stream_events[0]
        self.assertEqual(event["status"], "unavailable")
        self.assertIn("不要假装看到了原图", result.followup_context)
        self.assertEqual(result.model_image_inputs, [])

    def test_legacy_vid_handle_is_rejected_before_running(self) -> None:
        result = self.handler.execute(
            call={
                "type": "exec_run",
                "command": "ffprobe -v error -show_format -show_streams inputs/source.mp4",
                "initial_wait_seconds": 2,
                "input_resources": [{"handle": "vid_001", "as": "inputs/source.mp4"}],
            },
            context=self._context(),
        )
        state = result.state_updates["capability_execution"]
        self.assertEqual(state["status"], "blocked")
        self.assertIn("input_handle_not_found:vid_001", state.get("reason", ""))
        self.assertIn("没有执行", result.followup_context)

    def test_failed_extraction_registers_no_frames(self) -> None:
        result = self.handler.execute(
            call={
                "type": "exec_run",
                "command": (
                    "mkdir outputs && ffmpeg -hide_banner -loglevel error -y -i inputs/source.mp4 "
                    "-vf nope -frames:v 2 outputs/frame_%02d.jpg"
                ),
                "initial_wait_seconds": 2,
                "input_resources": [{"handle": self.handle, "as": "inputs/source.mp4"}],
                "output_globs": ["outputs/frame_*.jpg"],
            },
            context=self._context(),
        )
        state = result.state_updates["capability_execution"]
        self.assertEqual(state["status"], EXEC_STATUS_FAILED)
        self.assertEqual(state.get("artifact_status"), ARTIFACT_STATUS_NOT_REGISTERED)
        self.assertNotIn("generated_resources", state)


class _FakeLLM:
    pass


class _FakeEmbeddingProvider:
    name = "hashed"
    version = "test"
    dimension = 8

    def embed_text(self, text: str) -> list[float]:
        return [0.0] * self.dimension


class VideoUnderstandingMemcoreTraceTests(unittest.TestCase):
    """Item 8: the skill round (load_skill + exec_run + load_material) survives settlement."""

    def test_video_round_survives_settlement_and_is_reloadable(self) -> None:
        import config
        from unittest.mock import patch

        from companion_v01.memcore_integration.manager import MemcoreManager

        frame_result = (
            "命令已执行完成（exit_code=0，run_id=execrun_video）。\n"
            "已登记生成资源：gen_001(frame_01.jpg, image/jpeg)、gen_002(frame_02.jpg, image/jpeg)。"
            "帧时间戳：frame_01≈0.0s，frame_02≈1.0s。\n"
            + ("y" * 1500)
            + "\n需要交付时调用 send_file，不要自动替用户发送。"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(config, "MEMCORE_OPERATION_PROJECTION_POLICY", "compact_after_terminal"):
                manager = MemcoreManager(
                    backend="memcore",
                    storage_path=Path(temp_dir) / "video.sqlite3",
                    visible_scope="conversation",
                    enable_flavor=False,
                    shadow_compare=False,
                    llm=_FakeLLM(),
                    embedding_provider=_FakeEmbeddingProvider(),
                )
                try:
                    opened = manager.begin_input_turn(
                        {"source_id": "user-video", "content": "这个视频讲了什么", "timestamp": 100},
                        profile_user_id="u1",
                        session_id="s1",
                        character_pack_id="char",
                    )
                    turn_id = str(opened.get("turn_id") or "")
                    batch = manager.record_tool_batch(
                        exchanges=[
                            {
                                "tool_name": "load_skill",
                                "tool_call_id": "call-load-skill",
                                "tool_input": {"name": SKILL_NAME},
                                "result": f"已加载 Skill {SKILL_NAME}（正文指令）。",
                                "source": "load_skill",
                                "timestamp": 101,
                                "source_id_prefix": "tooltrace-skill",
                                "result_status": "success",
                            },
                            {
                                "tool_name": "exec_run",
                                "tool_call_id": "call-exec-video",
                                "tool_input": {
                                    "command": (
                                        "mkdir outputs && ffmpeg -y -i inputs/source.mp4 "
                                        "-vf fps=1 -frames:v 2 outputs/frame_%02d.jpg"
                                    ),
                                    "input_resources": [{"handle": "file_001", "as": "inputs/source.mp4"}],
                                    "output_globs": ["outputs/frame_*.jpg"],
                                },
                                "result": frame_result,
                                "source": "exec_run",
                                "timestamp": 102,
                                "source_id_prefix": "tooltrace-video",
                                "result_status": "success",
                            },
                            {
                                "tool_name": "load_material",
                                "tool_call_id": "call-load-frames",
                                "tool_input": {"targets": ["gen_001", "gen_002"], "purpose": "查看关键帧"},
                                "result": "已把当前会话材料 gen_001, gen_002 的原始图片通过原生多模态通道加载到下一轮。",
                                "source": "load_material",
                                "timestamp": 103,
                                "source_id_prefix": "tooltrace-frames",
                                "result_status": "success",
                            },
                        ],
                        turn_id=turn_id,
                        profile_user_id="u1",
                        session_id="s1",
                        character_pack_id="char",
                    )
                    self.assertTrue(batch["ok"], batch)
                    observation_sids = [
                        str(item["tool_result_source_id"])
                        for item in batch["exchanges"]
                        if item.get("tool_result_source_id")
                    ]
                    self.assertEqual(len(observation_sids), 3)
                    manager.build_context_projection(
                        provider_profile="openai",
                        profile_user_id="u1",
                        session_id="s1",
                        character_pack_id="char",
                    )
                    manager.complete_input_turn(
                        turn_id=turn_id,
                        assistant_record={"source_id": "assistant-video", "content": "看到了前两秒的画面。", "timestamp": 104},
                        memory_metadata={},
                        provider_output_raw="看到了前两秒的画面。",
                        profile_user_id="u1",
                        session_id="s1",
                        character_pack_id="char",
                        provider_profile="openai",
                        provider_projection={"role": "assistant", "content": "看到了前两秒的画面。"},
                    )
                    projection = manager.build_context_projection(
                        provider_profile="openai",
                        profile_user_id="u1",
                        session_id="s1",
                        character_pack_id="char",
                    )
                    tool_payloads = [m for m in projection.get("payloads") or [] if m.get("role") == "tool"]
                    self.assertEqual(len(tool_payloads), 3)
                    card_text = "\n".join(str(m.get("content") or "") for m in tool_payloads)
                    self.assertIn("[compact_reloadable]", card_text)
                    self.assertIn("tool: exec_run", card_text)
                    self.assertIn("reload: open_memory(memory_id=", card_text)
                    self.assertIn("status: success", card_text)
                    self.assertIn("已加载 Skill video-understanding", card_text)
                    self.assertIn("已把当前会话材料 gen_001, gen_002 的原始图片通过原生多模态通道加载到下一轮", card_text)
                    self.assertNotIn("run_id=execrun_video", card_text)
                    self.assertNotIn("y" * 64, card_text)
                    self.assertNotIn(str(temp_dir), card_text)
                    self.assertNotIn("C:\\", card_text)
                    system = manager._get_system(
                        profile_user_id="u1",
                        session_id="s1",
                        character_pack_id="char",
                    )
                    entries = manager._store.get_turn_entries(namespace=system.namespace, turn_id=turn_id)
                    observations = {
                        str(getattr(entry, "source_id", "") or ""): entry
                        for entry in entries
                        if str(getattr(entry, "source_id", "") or "") in observation_sids
                    }
                    self.assertEqual(set(observations), set(observation_sids))
                    stored = str(dict(observations[observation_sids[1]].payload or {}).get("output") or "")
                    self.assertIn("run_id=execrun_video", stored)
                    self.assertIn("gen_001(frame_01.jpg, image/jpeg)", stored)
                    self.assertIn("frame_02≈1.0s", stored)
                    self.assertNotIn("C:\\", stored)
                    expanded = manager.open_memory(
                        profile_user_id="u1",
                        session_id="s1",
                        character_pack_id="char",
                        arguments={"memory_id": observation_sids[1], "view": "content"},
                    )
                    self.assertTrue(expanded["ok"], expanded)
                    self.assertIn("gen_001(frame_01.jpg, image/jpeg)", expanded["text"])
                    self.assertIn("run_id=execrun_video", expanded["text"])
                finally:
                    manager.close()


if __name__ == "__main__":
    unittest.main()
