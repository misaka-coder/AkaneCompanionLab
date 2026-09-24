from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
import wave
import zipfile
from array import array
from pathlib import Path
from unittest.mock import patch

from companion_v01.attachment_inbox import AttachmentInboxService
from companion_v01.generated_files import GeneratedFileService
from companion_v01.store import MemoryStore
from tests.generated_artifact_fixtures import register_text_artifact
from companion_v01.tool_runtime import (
    InspectGeneratedFileToolHandler,
    InspectMediaInfoToolHandler,
    ManageGeneratedFileToolHandler,
    SendFileToolHandler,
    ToolExecutionContext,
)


def _write_test_wav(path: Path, *, sample_rate: int = 1000) -> None:
    samples = array("h")

    def extend_silence(seconds: float) -> None:
        samples.extend([0] * int(sample_rate * seconds))

    def extend_tone(seconds: float, amplitude: int = 9000) -> None:
        count = int(sample_rate * seconds)
        for index in range(count):
            samples.append(amplitude if (index // 25) % 2 == 0 else -amplitude)

    extend_silence(0.4)
    extend_tone(4.0)
    extend_silence(0.6)
    extend_tone(1.0)
    extend_silence(0.6)
    extend_tone(14.0)
    extend_silence(0.4)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(samples.tobytes())


class GeneratedFileTests(unittest.TestCase):
    def test_json_paging_is_lossless_beyond_four_million_characters(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = MemoryStore(root / "store")
            service = GeneratedFileService(
                base_dir=root / "generated", store=store,
                attachment_service=AttachmentInboxService(store=store),
            )
            source = json.dumps({"text": "界" * 4_000_100, "last": "tail-marker"}, ensure_ascii=False)
            path = service.allocate_output_path(
                profile_user_id="user", session_id="session", title="complete-result",
                output_format="json", timestamp=100,
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
            generated = service.register_generated_artifact(
                profile_user_id="user", session_id="session", output_path=path,
                output_title="complete-result", output_format="json", mime_type="application/json",
                content_card={}, summary="", created_by_tool="fixture.result", timestamp=100,
            )
            reader = InspectGeneratedFileToolHandler(generated_file_service=service)
            context = ToolExecutionContext("user", "session", 1, {})
            call = {"type": reader.tool_type, "target": generated["generated_handle"],
                    "section": "content", "max_chars": 40000}
            pages = []
            first_cursor = None
            for _ in range(140):
                result = reader.execute(call=reader.normalize_call(call), context=context)
                pages.append(result.stream_events[0]["inspection"]["content"])
                if result.followup_envelope.complete:
                    break
                call = dict(result.followup_envelope.continuation)
                first_cursor = first_cursor or dict(call)
            else:
                self.fail("paging did not reach the end of the JSON file")
            self.assertEqual(len(pages[0]), 40000)
            self.assertEqual("".join(pages), source)
            self.assertIn("tail-marker", pages[-1])
            # A cursor cannot silently follow changed or missing source bytes.
            path = Path(generated["absolute_path"])
            path.write_text(source + "\n", encoding="utf-8")
            stale = reader.execute(call=reader.normalize_call(first_cursor), context=context)
            self.assertEqual(stale.followup_envelope.diagnostics["status"], "stale_cursor")
            with patch.object(service, "_read_plain_text_file", side_effect=OSError("private storage path")):
                failed = service.inspect_generated_file(
                    profile_user_id="user", session_id="session", target=generated["generated_handle"],
                )
                self.assertFalse(failed["ok"])
                self.assertEqual(failed["error"], "generated_file_read_failed")
                self.assertIsNone(failed["inspection"])
                self.assertNotIn("private storage path", failed["followup_context"])
                failed_call = reader.execute(
                    call={"type": reader.tool_type, "target": generated["generated_handle"]}, context=context,
                )
                self.assertEqual(failed_call.stream_events[0]["type"], "tool_execution_failed")
                self.assertEqual(failed_call.state_updates["operation_failure"]["reason"], "generated_file_read_failed")
                self.assertNotIn("private storage path", failed_call.followup_context)

    def test_inspect_generated_lrc_reads_lyrics_instead_of_binary_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = MemoryStore(root / "db")
            service = GeneratedFileService(
                base_dir=root / "generated", store=store,
                attachment_service=AttachmentInboxService(store=store),
            )
            path = service.allocate_output_path(
                profile_user_id="user", session_id="session", title="歌词", output_format="lrc",
                timestamp=100, allow_generic_format=True,
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("[00:01.00]第一句\n[00:02.00]末句\n", encoding="utf-8")
            generated = service.register_generated_artifact(
                profile_user_id="user", session_id="session", output_path=path,
                output_title="歌词", output_format="lrc", mime_type="text/plain",
                content_card={}, summary="", created_by_tool="fixture.register",
                allow_generic_format=True, timestamp=100,
            )
            result = service.inspect_generated_file(
                profile_user_id="user", session_id="session", target=generated["generated_handle"],
                section="content", max_chars=2000,
            )
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["inspection"]["source_kind"], "lrc")
            self.assertIn("[00:02.00]末句", result["inspection"]["content"])

    def test_media_tools_preserve_failed_attachment_root_cause(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            attachment_service = AttachmentInboxService(store=store)
            generated_service = GeneratedFileService(
                base_dir=root / "generated_files",
                store=store,
                attachment_service=attachment_service,
            )
            failed = store.add_attachment_inbox_item(
                profile_user_id="user",
                session_id="session",
                kind="file",
                status="failed",
                origin_name="彩虹.flac",
                mime_type="audio/flac",
                file_ext=".flac",
                file_size=27_461_517,
                error_message="attachment_too_large",
                short_hint="文件超过当前大小限制。",
                detail={
                    "failure": {
                        "code": "attachment_too_large",
                        "reason": "文件超过当前大小限制。",
                        "observed_bytes": 27_461_517,
                        "limit_bytes": 20_971_520,
                    }
                },
                timestamp=100,
            )
            handle = failed["attachment_handle"]

            results = [
                generated_service.inspect_media_info(
                    profile_user_id="user",
                    session_id="session",
                    source_target=handle,
                ),
            ]

        for result in results:
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"], "attachment_too_large")
            self.assertIn("来源附件此前已经失败", result["followup_context"])
            self.assertIn("文件超过当前大小限制", result["followup_context"])
            self.assertNotIn("本地来源文件不存在", result["followup_context"])

    def test_store_generated_file_roundtrip_and_handles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            first = store.add_generated_file(
                profile_user_id="user",
                session_id="session",
                output_title="学习计划",
                output_format="md",
                storage_relpath="user/session/plan.md",
                source_ids=["file_001"],
                content_card={"summary": "学习计划摘要"},
                summary="学习计划摘要",
                timestamp=100,
            )
            second = store.add_generated_file(
                profile_user_id="user",
                session_id="session",
                output_title="学习计划 Word",
                output_format="docx",
                storage_relpath="user/session/plan.docx",
                timestamp=110,
            )

            self.assertEqual(first["generated_handle"], "gen_001")
            self.assertEqual(second["generated_handle"], "gen_002")
            self.assertEqual(first["source_ids"], ["file_001"])
            self.assertEqual(first["content_card"]["summary"], "学习计划摘要")

            listed = store.list_generated_files(
                profile_user_id="user",
                session_id="session",
                statuses=["ready"],
                limit=10,
            )
            self.assertEqual([item["generated_handle"] for item in listed], ["gen_002", "gen_001"])

            found = store.find_generated_file(
                profile_user_id="user",
                session_id="session",
                query="学习计划",
            )
            self.assertIsNotNone(found)
            self.assertIn(found["generated_handle"], {"gen_001", "gen_002"})

    def test_find_generated_file_prefers_exact_title_over_newer_partial_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            vocal = store.add_generated_file(
                profile_user_id="user",
                session_id="session",
                output_title="昔涟_人声",
                output_format="flac",
                storage_relpath="user/session/vocal.flac",
                timestamp=100,
            )
            denoised = store.add_generated_file(
                profile_user_id="user",
                session_id="session",
                output_title="昔涟_人声_降噪",
                output_format="flac",
                storage_relpath="user/session/vocal_denoised.flac",
                timestamp=200,
            )

            found = store.find_generated_file(
                profile_user_id="user",
                session_id="session",
                query="昔涟_人声",
                statuses=["ready"],
            )

            self.assertIsNotNone(found)
            self.assertEqual(found["generated_id"], vocal["generated_id"])
            self.assertNotEqual(found["generated_id"], denoised["generated_id"])

    def test_find_generated_file_prefers_exact_handle_over_similar_title(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            target = store.add_generated_file(
                profile_user_id="user",
                session_id="session",
                generated_handle="gen_033",
                output_title="原始人声",
                output_format="flac",
                storage_relpath="user/session/vocal.flac",
                timestamp=100,
            )
            store.add_generated_file(
                profile_user_id="user",
                session_id="session",
                generated_handle="gen_035",
                output_title="这是 gen_033 的降噪版",
                output_format="flac",
                storage_relpath="user/session/vocal_denoised.flac",
                timestamp=200,
            )

            found = store.find_generated_file(
                profile_user_id="user",
                session_id="session",
                query="gen_033",
                statuses=["ready"],
            )

            self.assertIsNotNone(found)
            self.assertEqual(found["generated_id"], target["generated_id"])
            self.assertEqual(found["generated_handle"], "gen_033")

    def test_find_generated_file_requires_confirmation_for_ambiguous_fuzzy_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            store.add_generated_file(
                profile_user_id="user",
                session_id="session",
                output_title="昔涟_人声",
                output_format="flac",
                storage_relpath="user/session/vocal.flac",
                timestamp=100,
            )
            store.add_generated_file(
                profile_user_id="user",
                session_id="session",
                output_title="昔涟_人声_降噪",
                output_format="flac",
                storage_relpath="user/session/vocal_denoised.flac",
                timestamp=200,
            )

            found = store.find_generated_file(
                profile_user_id="user",
                session_id="session",
                query="人声",
                statuses=["ready"],
            )

            self.assertIsNone(found)

    def test_generated_prompt_context_exposes_media_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root)
            attachment_service = AttachmentInboxService(store=store)
            generated_service = GeneratedFileService(
                base_dir=root / "generated_files",
                store=store,
                attachment_service=attachment_service,
            )
            store.add_generated_file(
                profile_user_id="user",
                session_id="session",
                output_title="昔涟_人声",
                output_format="flac",
                storage_relpath="user/session/vocal.flac",
                file_size=17425491,
                content_card={
                    "summary": "从 file_016 分离出人声轨。",
                    "source": {"handle": "file_016", "title": "昔涟"},
                    "separation": {"stem_role": "vocals"},
                    "media_info": {
                        "format_name": "flac",
                        "duration_seconds": 192.0,
                        "file_size": 17425491,
                        "audio": {
                            "codec": "flac",
                            "sample_rate": 48000,
                            "channels": 2,
                            "bit_rate": 1411000,
                        },
                    },
                },
                summary="从 file_016 分离出人声轨。",
                created_by_tool="separate_audio_stems",
                timestamp=100,
            )

            context = generated_service.build_prompt_context(
                profile_user_id="user",
                session_id="session",
                limit=3,
            )

            self.assertIn("大小：", context)
            self.assertIn("生成：", context)
            self.assertIn("来源工具：separate_audio_stems", context)
            self.assertIn("来源：file_016", context)
            self.assertIn("音轨角色：vocals", context)
            self.assertIn("媒体规格：格式：flac", context)
            self.assertIn("时长：3:12", context)
            self.assertIn("音频：编码 flac，48000Hz，2声道，1.41Mbps。", context)

    def test_inspect_media_info_reads_ffprobe_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            attachment_root = root / "attachments"
            stored = attachment_root / "master" / "clip.mp4"
            stored.parent.mkdir(parents=True, exist_ok=True)
            stored.write_bytes(b"fake video payload")

            store = MemoryStore(root / "db")
            attachment_service = AttachmentInboxService(store=store, base_dir=attachment_root)
            generated_service = GeneratedFileService(
                base_dir=root / "generated_files",
                store=store,
                attachment_service=attachment_service,
            )
            attachment = attachment_service.create_pending(
                profile_user_id="user",
                session_id="session",
                source="qq",
                kind="file",
                origin_name="clip.mp4",
                storage_relpath="master/clip.mp4",
                timestamp=100,
            )
            attachment_service.mark_ready(
                profile_user_id="user",
                session_id="session",
                attachment_id=attachment["attachment_id"],
                summary_title="clip.mp4",
                short_hint="一段视频。",
                detail={"file_kind": "mp4"},
                timestamp=110,
            )

            def fake_run(command, **kwargs):
                class Result:
                    returncode = 0
                    stderr = ""
                    stdout = """
                    {
                      "format": {
                        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
                        "duration": "184.2",
                        "size": "1234567",
                        "bit_rate": "320000"
                      },
                      "streams": [
                        {
                          "index": 0,
                          "codec_type": "video",
                          "codec_name": "h264",
                          "width": 1920,
                          "height": 1080,
                          "avg_frame_rate": "30000/1001"
                        },
                        {
                          "index": 1,
                          "codec_type": "audio",
                          "codec_name": "aac",
                          "sample_rate": "44100",
                          "channels": 2,
                          "bit_rate": "128000"
                        }
                      ]
                    }
                    """

                return Result()

            with (
                patch("companion_v01.generated_files.shutil.which", return_value="ffprobe"),
                patch(
                    "companion_v01.generated_files_media.subprocess.run",
                    side_effect=fake_run,
                ),
            ):
                result = generated_service.inspect_media_info(
                    profile_user_id="user",
                    session_id="session",
                    source_target="file_001",
                    timestamp=120,
                )

            self.assertTrue(result["ok"])
            media_info = result["media_info"]
            self.assertEqual(media_info["duration_seconds"], 184.2)
            self.assertEqual(media_info["audio"]["codec"], "aac")
            self.assertEqual(media_info["audio"]["sample_rate"], 44100)
            self.assertEqual(media_info["video"]["width"], 1920)
            self.assertEqual(media_info["video"]["height"], 1080)
            self.assertAlmostEqual(media_info["video"]["fps"], 29.97, places=2)
            self.assertIn("音频", result["followup_context"])
            self.assertIn("视频", result["followup_context"])


    def test_inspect_media_info_tool_handler_emits_media_info_event(self) -> None:
        class FakeGeneratedService:
            def inspect_media_info(self, **kwargs):
                return {
                    "ok": True,
                    "media_info": {"duration_seconds": 12.0, "audio": {"codec": "mp3"}},
                    "followup_context": "读取完成。",
                }

        handler = InspectMediaInfoToolHandler(generated_file_service=FakeGeneratedService())
        call = handler.normalize_call({"type": "inspect_media_info", "source_id": "audio_001"})

        self.assertIsNotNone(call)
        result = handler.execute(
            call=call or {},
            context=ToolExecutionContext(
                profile_user_id="user",
                session_id="session",
                now_ts=100,
                visual_payload={},
            ),
        )

        self.assertEqual(result.stream_events[0]["type"], "media_info_inspected")
        self.assertEqual(result.stream_events[0]["media_info"]["duration_seconds"], 12.0)
        self.assertEqual(result.followup_context, "读取完成。")

    def test_media_handlers_do_not_report_ready_without_real_executors(self) -> None:
        class FakeGeneratedService:
            @staticmethod
            def media_inspection_status():
                return {"enabled": True, "status": "ready", "reason": "", "provider": "ffprobe"}

        service = FakeGeneratedService()
        statuses = {
            "inspect": InspectMediaInfoToolHandler(generated_file_service=service).capability_status(),
        }

        self.assertEqual(statuses["inspect"]["provider"], "ffprobe")

    def test_send_file_supports_generated_and_attachment_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            attachment_root = root / "attachments"
            attachment_path = attachment_root / "master" / "clip.mp4"
            attachment_path.parent.mkdir(parents=True, exist_ok=True)
            attachment_path.write_bytes(b"video")
            attachment_service = AttachmentInboxService(store=store, base_dir=attachment_root)
            generated_service = GeneratedFileService(
                base_dir=root / "generated_files",
                store=store,
                attachment_service=attachment_service,
            )
            attachment = attachment_service.create_pending(
                profile_user_id="user",
                session_id="session",
                source="qq",
                kind="video",
                origin_name="clip.mp4",
                storage_relpath="master/clip.mp4",
                file_ext="mp4",
                timestamp=90,
            )
            attachment_service.mark_ready(
                profile_user_id="user",
                session_id="session",
                attachment_id=attachment["attachment_id"],
                summary_title="测试视频",
                short_hint="一个测试视频。",
                detail={"media_info": {"video": {"codec": "h264"}}},
                timestamp=95,
            )
            register_text_artifact(generated_service,
                profile_user_id="user",
                session_id="session",
                output_format="txt",
                output_title="小结",
                content="生成内容",
                timestamp=100,
            )

            result = generated_service.send_file(
                profile_user_id="user",
                session_id="session",
                targets=["file_001", "gen_001"],
                timestamp=130,
            )

            self.assertTrue(result["ok"])
            self.assertEqual([item["source_type"] for item in result["files"]], ["attachment", "generated"])
            self.assertEqual(result["files"][0]["name"], "clip.mp4")
            self.assertEqual(result["files"][1]["handle"], "gen_001")
            self.assertIn("2 个已有文件", result["followup_context"])
            self.assertIn("尚无最终回执", result["followup_context"])
            self.assertNotIn(str(root), result["followup_context"])

    def test_send_file_explicit_latest_kind_avoids_cross_workspace_misselection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            attachment_root = root / "attachments"
            attachment_path = attachment_root / "master" / "reference.png"
            attachment_path.parent.mkdir(parents=True, exist_ok=True)
            attachment_path.write_bytes(b"reference")
            attachment_service = AttachmentInboxService(store=store, base_dir=attachment_root)
            generated_service = GeneratedFileService(
                base_dir=root / "generated_files",
                store=store,
                attachment_service=attachment_service,
            )
            generated = register_text_artifact(generated_service,
                profile_user_id="user",
                session_id="session",
                output_format="txt",
                output_title="准确结果",
                content="generated",
                timestamp=100,
            )
            attachment = attachment_service.create_pending(
                profile_user_id="user",
                session_id="session",
                source="qq",
                kind="image",
                origin_name="reference.png",
                storage_relpath="master/reference.png",
                file_ext="png",
                timestamp=200,
            )
            attachment_service.mark_ready(
                profile_user_id="user",
                session_id="session",
                attachment_id=attachment["attachment_id"],
                summary_title="参考图",
                short_hint="用户随后上传的参考图。",
                detail={},
                timestamp=210,
            )

            generated_result = generated_service.send_file(
                profile_user_id="user",
                session_id="session",
                target="latest_generated",
                timestamp=300,
            )
            attachment_result = generated_service.send_file(
                profile_user_id="user",
                session_id="session",
                target="latest_attachment",
                timestamp=301,
            )
            deduplicated_result = generated_service.send_file(
                profile_user_id="user",
                session_id="session",
                targets=[generated["generated_handle"], generated["generated_id"]],
                timestamp=302,
            )

            self.assertEqual(generated_result["files"][0]["handle"], generated["generated_handle"])
            self.assertEqual(attachment_result["files"][0]["handle"], attachment["attachment_handle"])
            self.assertEqual(len(deduplicated_result["files"]), 1)

    def test_send_file_requests_confirmation_for_ambiguous_generated_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            attachment_service = AttachmentInboxService(store=store)
            generated_service = GeneratedFileService(
                base_dir=root / "generated_files",
                store=store,
                attachment_service=attachment_service,
            )
            first = register_text_artifact(generated_service,
                profile_user_id="user",
                session_id="session",
                output_format="txt",
                output_title="昔涟_人声",
                content="原始人声文件",
                timestamp=100,
            )
            second = register_text_artifact(generated_service,
                profile_user_id="user",
                session_id="session",
                output_format="txt",
                output_title="昔涟_人声_降噪",
                content="降噪人声文件",
                timestamp=110,
            )

            result = generated_service.send_file(
                profile_user_id="user",
                session_id="session",
                targets=["人声"],
                timestamp=130,
            )

            self.assertFalse(result["ok"])
            self.assertEqual(result["error"], "file_target_ambiguous")
            self.assertIn("存在多个候选", result["followup_context"])
            self.assertIn(first["generated_handle"], result["followup_context"])
            self.assertIn(second["generated_handle"], result["followup_context"])

    def test_send_file_keeps_generated_exact_send_when_attachment_target_is_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            attachment_root = root / "attachments"
            first_path = attachment_root / "master" / "menu-breakfast.png"
            second_path = attachment_root / "master" / "menu-dinner.png"
            first_path.parent.mkdir(parents=True, exist_ok=True)
            first_path.write_bytes(b"img1")
            second_path.write_bytes(b"img2")
            attachment_service = AttachmentInboxService(store=store, base_dir=attachment_root)
            generated_service = GeneratedFileService(
                base_dir=root / "generated_files",
                store=store,
                attachment_service=attachment_service,
            )
            first = attachment_service.create_pending(
                profile_user_id="user",
                session_id="session",
                source="qq",
                kind="image",
                origin_name="menu-breakfast.png",
                storage_relpath="master/menu-breakfast.png",
                timestamp=90,
            )
            second = attachment_service.create_pending(
                profile_user_id="user",
                session_id="session",
                source="qq",
                kind="image",
                origin_name="menu-dinner.png",
                storage_relpath="master/menu-dinner.png",
                timestamp=91,
            )
            attachment_service.mark_ready(
                profile_user_id="user",
                session_id="session",
                attachment_id=first["attachment_id"],
                summary_title="早餐菜单图",
                short_hint="第一张菜单图。",
                timestamp=95,
            )
            attachment_service.mark_ready(
                profile_user_id="user",
                session_id="session",
                attachment_id=second["attachment_id"],
                summary_title="晚餐菜单图",
                short_hint="第二张菜单图。",
                timestamp=96,
            )
            register_text_artifact(generated_service,
                profile_user_id="user",
                session_id="session",
                output_format="txt",
                output_title="小结",
                content="生成内容",
                timestamp=100,
            )

            result = generated_service.send_file(
                profile_user_id="user",
                session_id="session",
                targets=["菜单图", "gen_001"],
                timestamp=130,
            )

            self.assertTrue(result["ok"])
            self.assertEqual([item["handle"] for item in result["files"]], ["gen_001"])
            self.assertIn("存在多个候选", result["followup_context"])
            self.assertIn("img_001", result["followup_context"])
            self.assertIn("img_002", result["followup_context"])
            self.assertIn("gen_001", result["followup_context"])

    def test_send_file_tool_handler_emits_generic_file_events(self) -> None:
        class FakeGeneratedService:
            def send_file(self, **kwargs):
                return {
                    "ok": True,
                    "files": [
                        {
                            "source_type": "attachment",
                            "source_id": "attachment::1",
                            "handle": "file_001",
                            "absolute_path": "C:/tmp/video.mp4",
                            "name": "video.mp4",
                        },
                        {
                            "source_type": "generated",
                            "source_id": "generated::1",
                            "generated_id": "generated::1",
                            "handle": "gen_001",
                            "absolute_path": "C:/tmp/out.md",
                            "name": "out.md",
                        },
                    ],
                    "followup_context": "send_file ok",
                }

        handler = SendFileToolHandler(generated_file_service=FakeGeneratedService())
        call = handler.normalize_call(
            {
                "type": "send_file",
                "targets": ["file_001", "gen_001"],
            }
        )
        context = ToolExecutionContext(
            profile_user_id="user",
            session_id="session",
            now_ts=130,
            visual_payload={},
        )

        result = handler.execute(call=call or {}, context=context)

        self.assertEqual([event["type"] for event in result.stream_events], ["file_ready", "file_ready"])
        self.assertEqual(result.stream_events[0]["file"]["handle"], "file_001")
        self.assertEqual(result.stream_events[1]["file"]["generated_id"], "generated::1")
        self.assertIn("send_file ok", result.followup_context)

    def test_send_file_tool_instruction_explains_material_and_result_selection(self) -> None:
        instruction = SendFileToolHandler(generated_file_service=object()).build_prompt_instruction()

        self.assertIn("原始材料", instruction)
        self.assertIn("gen_ 是工具生成的结果", instruction)
        self.assertIn("用户只要结果时不要顺带发送原始材料", instruction)
        self.assertIn("一次选择多个", instruction)

    def test_send_file_tool_handler_carries_desktop_delivery_action(self) -> None:
        class FakeGeneratedService:
            def send_file(self, **kwargs):
                return {
                    "ok": True,
                    "files": [
                        {
                            "source_type": "generated",
                            "source_id": "generated::1",
                            "generated_id": "generated::1",
                            "handle": "gen_001",
                            "absolute_path": "C:/tmp/out.txt",
                            "name": "out.txt",
                        }
                    ],
                    "followup_context": "send_file ok",
                }

        handler = SendFileToolHandler(generated_file_service=FakeGeneratedService())
        call = handler.normalize_call(
            {
                "type": "send_file",
                "target": "gen_001",
                "delivery_action": "save_desktop",
            }
        )
        context = ToolExecutionContext(
            profile_user_id="user",
            session_id="session",
            now_ts=130,
            visual_payload={},
            client_mode="desktop_pet",
        )

        result = handler.execute(call=call or {}, context=context)

        self.assertEqual(result.stream_events[0]["delivery_action"], "save_desktop")
        self.assertEqual(result.stream_events[0]["client_mode"], "desktop_pet")
        self.assertEqual(result.stream_events[0]["desktop_delivery"]["action"], "save_desktop")
        self.assertEqual(result.stream_events[0]["desktop_delivery"]["handle"], "gen_001")

    def test_send_file_tool_handler_ignores_desktop_delivery_action_for_qq(self) -> None:
        class FakeGeneratedService:
            def send_file(self, **kwargs):
                return {
                    "ok": True,
                    "files": [
                        {
                            "source_type": "generated",
                            "source_id": "generated::1",
                            "generated_id": "generated::1",
                            "handle": "gen_001",
                            "absolute_path": "C:/tmp/out.txt",
                            "name": "out.txt",
                        }
                    ],
                    "followup_context": "send_file ok",
                }

        handler = SendFileToolHandler(generated_file_service=FakeGeneratedService())
        call = handler.normalize_call(
            {
                "type": "send_file",
                "target": "gen_001",
                "delivery_action": "save_desktop",
            }
        )
        context = ToolExecutionContext(
            profile_user_id="user",
            session_id="session",
            now_ts=130,
            visual_payload={},
            client_mode="qq_text",
        )

        result = handler.execute(call=call or {}, context=context)

        self.assertEqual(result.stream_events[0]["client_mode"], "qq_text")
        self.assertNotIn("delivery_action", result.stream_events[0])
        self.assertNotIn("desktop_delivery", result.stream_events[0])

    def test_inspect_generated_file_reads_text_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root)
            attachment_service = AttachmentInboxService(store=store)
            generated_service = GeneratedFileService(
                base_dir=root / "generated_files",
                store=store,
                attachment_service=attachment_service,
            )
            register_text_artifact(generated_service,
                profile_user_id="user",
                session_id="session",
                output_format="md",
                output_title="小结",
                content="# 小结\n\n第一段内容。\n\n最后一段内容。",
                timestamp=100,
            )

            result = generated_service.inspect_generated_file(
                profile_user_id="user",
                session_id="session",
                target="gen_001",
                section="tail",
                max_chars=40,
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["inspection"]["section"], "tail")
            self.assertIn("最后一段内容", result["inspection"]["content"])
            self.assertIn(result["inspection"]["content"], result["followup_context"])

    def test_inspect_generated_file_reads_zip_manifest_and_list(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root)
            attachment_service = AttachmentInboxService(store=store)
            generated_service = GeneratedFileService(
                base_dir=root / "generated_files",
                store=store,
                attachment_service=attachment_service,
            )
            zip_path = root / "generated_files" / "user" / "session" / "dataset.zip"
            zip_path.parent.mkdir(parents=True, exist_ok=True)
            manifest = {"title": "测试训练集", "stats": {"slice_count": 2}}
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
                archive.writestr("README.md", "# 测试训练集\n")
                archive.writestr("slices/clip_001.wav", b"RIFF")
            store.add_generated_file(
                profile_user_id="user",
                session_id="session",
                output_title="测试训练集",
                output_format="zip",
                storage_relpath="user/session/dataset.zip",
                mime_type="application/zip",
                file_ext="zip",
                file_size=zip_path.stat().st_size,
                content_card={"summary": "训练集 zip"},
                summary="训练集 zip",
                timestamp=100,
            )

            manifest_result = generated_service.inspect_generated_file(
                profile_user_id="user",
                session_id="session",
                target="gen_001",
                section="manifest",
                max_chars=2000,
            )
            list_result = generated_service.inspect_generated_file(
                profile_user_id="user",
                session_id="session",
                target="gen_001",
                section="file_list",
                max_chars=2000,
            )

            self.assertTrue(manifest_result["ok"])
            self.assertIn("slice_count", manifest_result["inspection"]["content"])
            self.assertIn("manifest.json", list_result["inspection"]["content"])
            self.assertIn("slices/clip_001.wav", list_result["inspection"]["content"])

    def test_inspect_generated_file_tool_handler_emits_inspection_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root)
            attachment_service = AttachmentInboxService(store=store)
            generated_service = GeneratedFileService(
                base_dir=root / "generated_files",
                store=store,
                attachment_service=attachment_service,
            )
            register_text_artifact(generated_service,
                profile_user_id="user",
                session_id="session",
                output_format="txt",
                output_title="小结",
                content="可以回头查看的内容",
                timestamp=100,
            )
            handler = InspectGeneratedFileToolHandler(generated_file_service=generated_service)
            call = handler.normalize_call(
                {
                    "type": "inspect_generated_file",
                    "section": "content",
                }
            )
            context = ToolExecutionContext(
                profile_user_id="user",
                session_id="session",
                now_ts=130,
                visual_payload={},
            )

            result = handler.execute(call=call or {}, context=context)

            self.assertEqual((call or {}).get("target"), "latest")
            self.assertEqual(result.stream_events[0]["type"], "generated_file_inspected")
            self.assertIn("可以回头查看", result.followup_context)

    def test_manage_generated_files_archive_hides_from_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root)
            attachment_service = AttachmentInboxService(store=store)
            generated_service = GeneratedFileService(
                base_dir=root / "generated_files",
                store=store,
                attachment_service=attachment_service,
            )
            register_text_artifact(generated_service,
                profile_user_id="user",
                session_id="session",
                output_format="txt",
                output_title="小结",
                content="旧内容",
                timestamp=100,
            )

            result = generated_service.manage_generated_files(
                profile_user_id="user",
                session_id="session",
                action="archive",
                targets=["gen_001"],
                reason="旧版本不用了",
                timestamp=130,
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["managed"][0]["status"], "removed")
            self.assertEqual(
                generated_service.build_prompt_context(profile_user_id="user", session_id="session"),
                "",
            )

    def test_manage_generated_files_delete_removes_disk_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root)
            attachment_service = AttachmentInboxService(store=store)
            generated_service = GeneratedFileService(
                base_dir=root / "generated_files",
                store=store,
                attachment_service=attachment_service,
            )
            generated = register_text_artifact(generated_service,
                profile_user_id="user",
                session_id="session",
                output_format="txt",
                output_title="小结",
                content="旧内容",
                timestamp=100,
            )
            output_path = Path(generated["absolute_path"])
            self.assertTrue(output_path.exists())

            result = generated_service.manage_generated_files(
                profile_user_id="user",
                session_id="session",
                action="delete",
                targets=["gen_001"],
                timestamp=130,
            )

            self.assertTrue(result["ok"])
            self.assertFalse(output_path.exists())
            self.assertTrue(result["managed"][0]["file_deleted"])
            self.assertEqual(result["managed"][0]["status"], "removed")

    def test_manage_generated_file_tool_handler_emits_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root)
            attachment_service = AttachmentInboxService(store=store)
            generated_service = GeneratedFileService(
                base_dir=root / "generated_files",
                store=store,
                attachment_service=attachment_service,
            )
            register_text_artifact(generated_service,
                profile_user_id="user",
                session_id="session",
                output_format="txt",
                output_title="小结",
                content="旧内容",
                timestamp=100,
            )
            handler = ManageGeneratedFileToolHandler(generated_file_service=generated_service)
            call = handler.normalize_call(
                {
                    "type": "manage_generated_file",
                    "action": "archive",
                    "targets": ["gen_001"],
                    "reason": "测试清理",
                }
            )
            context = ToolExecutionContext(
                profile_user_id="user",
                session_id="session",
                now_ts=130,
                visual_payload={},
            )

            result = handler.execute(call=call or {}, context=context)

            self.assertEqual(result.stream_events[0]["type"], "generated_files_managed")
            self.assertEqual(result.stream_events[0]["action"], "archive")
            self.assertIn("manage_generated_file", result.followup_context)


if __name__ == "__main__":
    unittest.main()
