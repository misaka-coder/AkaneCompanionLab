from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from companion_v01.attachment_inbox import AttachmentInboxService
from tests.generated_artifact_fixtures import register_text_artifact
from companion_v01.generated_files import GeneratedFileService
from companion_v01.store import MemoryStore


class ResourceVisibilityContractTests(unittest.TestCase):
    def _build_services(
        self,
        root: Path,
    ) -> tuple[
        Path,
        MemoryStore,
        AttachmentInboxService,
        GeneratedFileService,
    ]:
        attachment_root = root / "attachments"
        store = MemoryStore(root / "db")
        attachment_service = AttachmentInboxService(store=store, base_dir=attachment_root)
        generated_service = GeneratedFileService(
            base_dir=root / "generated_files",
            store=store,
            attachment_service=attachment_service,
        )
        return attachment_root, store, attachment_service, generated_service

    def test_attachment_focus_contract_keeps_full_image_visual_card(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, _, attachment_service, _ = self._build_services(root)
            image = attachment_service.create_pending(
                profile_user_id="user",
                session_id="session",
                source="qq",
                kind="image",
                origin_name="breakfast-menu.png",
                timestamp=100,
            )
            attachment_service.mark_ready(
                profile_user_id="user",
                session_id="session",
                attachment_id=image["attachment_id"],
                summary_title="早餐菜单图",
                short_hint="桌上摊着一张早餐菜单。",
                detail={
                    "summary": "桌上摊着一张早餐菜单，旁边放着一杯咖啡。",
                    "scene_description": "木桌上摆着菜单、马克杯和黄油吐司，镜头略微俯视。",
                    "entities": ["菜单", "咖啡", "吐司", "黄油"],
                    "mood_tags": ["明亮", "早餐"],
                    "uncertainty": "菜单右下角的小字看不清。",
                },
                timestamp=110,
            )

            prompt = attachment_service.build_prompt_context(
                profile_user_id="user",
                session_id="session",
            )

            self.assertIn("【当前材料工作台】", prompt)
            self.assertIn("当前重点材料 Focus", prompt)
            self.assertIn("img_001", prompt)
            self.assertIn("视觉描述：", prompt)
            self.assertIn("木桌上摆着菜单、马克杯和黄油吐司", prompt)
            self.assertIn("要素：菜单, 咖啡, 吐司, 黄油", prompt)
            self.assertIn("标签：明亮, 早餐", prompt)
            self.assertIn("不确定处：菜单右下角的小字看不清", prompt)

    def test_attachment_media_contract_shows_lightweight_specs_without_inspect(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, _, attachment_service, _ = self._build_services(root)
            audio = attachment_service.create_pending(
                profile_user_id="user",
                session_id="session",
                source="qq",
                kind="audio",
                origin_name="vocal.wav",
                timestamp=100,
            )
            attachment_service.mark_ready(
                profile_user_id="user",
                session_id="session",
                attachment_id=audio["attachment_id"],
                summary_title="原始人声",
                short_hint="刚上传的人声素材。",
                detail={
                    "media_info": {
                        "format_name": "wav",
                        "duration_seconds": 192.0,
                        "file_size": 18432000,
                        "audio": {
                            "codec": "pcm_s16le",
                            "sample_rate": 48000,
                            "channels": 2,
                            "bit_rate": 1536000,
                        },
                    }
                },
                timestamp=110,
            )

            prompt = attachment_service.build_prompt_context(
                profile_user_id="user",
                session_id="session",
            )

            self.assertIn("audio_001", prompt)
            self.assertIn("媒体信息：容器/格式：wav", prompt)
            self.assertIn("时长：3:12", prompt)
            self.assertIn("音频：编码 pcm_s16le，48000Hz，2声道，1.54Mbps。", prompt)
            self.assertIn("说明：这是轻量媒体规格卡", prompt)

    def test_generated_media_contract_shows_specs_and_processing_roles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, store, attachment_service, generated_service = self._build_services(root)
            store.add_generated_file(
                profile_user_id="user",
                session_id="session",
                output_title="昔涟_原始人声",
                output_format="flac",
                storage_relpath="user/session/vocals.flac",
                file_size=17425491,
                content_card={
                    "summary": "从 audio_001 分离出原始人声轨。",
                    "source": {"handle": "audio_001", "title": "昔涟.wav"},
                    "separation": {"stem_role": "vocals", "mode": "demucs", "output_format": "flac"},
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
                summary="从 audio_001 分离出原始人声轨。",
                created_by_tool="separate_audio_stems",
                timestamp=100,
            )
            store.add_generated_file(
                profile_user_id="user",
                session_id="session",
                output_title="昔涟_伴奏",
                output_format="flac",
                storage_relpath="user/session/instrumental.flac",
                file_size=17300000,
                content_card={
                    "summary": "从 audio_001 分离出伴奏轨。",
                    "source": {"handle": "audio_001", "title": "昔涟.wav"},
                    "separation": {"stem_role": "instrumental", "mode": "demucs", "output_format": "flac"},
                    "media_info": {
                        "format_name": "flac",
                        "duration_seconds": 192.0,
                        "file_size": 17300000,
                        "audio": {
                            "codec": "flac",
                            "sample_rate": 48000,
                            "channels": 2,
                            "bit_rate": 1400000,
                        },
                    },
                },
                summary="从 audio_001 分离出伴奏轨。",
                created_by_tool="separate_audio_stems",
                timestamp=110,
            )
            store.add_generated_file(
                profile_user_id="user",
                session_id="session",
                output_title="昔涟_降噪人声",
                output_format="flac",
                storage_relpath="user/session/vocals_denoised.flac",
                file_size=16800000,
                content_card={
                    "summary": "对 gen_001 做了降噪净化。",
                    "source": {"handle": "gen_001", "title": "昔涟_原始人声"},
                    "voice_cleaning": {"mode": "denoise", "backend_used": "demucs"},
                    "media_info": {
                        "format_name": "flac",
                        "duration_seconds": 192.0,
                        "file_size": 16800000,
                        "audio": {
                            "codec": "flac",
                            "sample_rate": 48000,
                            "channels": 2,
                            "bit_rate": 1365000,
                        },
                    },
                },
                summary="对 gen_001 做了降噪净化。",
                created_by_tool="clean_voice_track",
                timestamp=120,
            )

            prompt = generated_service.build_prompt_context(
                profile_user_id="user",
                session_id="session",
                limit=8,
            )

            self.assertIn("【生成文件工作台】", prompt)
            self.assertIn("gen_001：昔涟_原始人声.flac", prompt)
            self.assertIn("gen_002：昔涟_伴奏.flac", prompt)
            self.assertIn("gen_003：昔涟_降噪人声.flac", prompt)
            self.assertIn("来源工具：separate_audio_stems", prompt)
            self.assertIn("来源工具：clean_voice_track", prompt)
            self.assertIn("音轨角色：vocals", prompt)
            self.assertIn("音轨角色：instrumental", prompt)
            self.assertIn("净化信息：mode=denoise，backend=demucs", prompt)
            self.assertIn("媒体规格：格式：flac；时长：3:12", prompt)
            self.assertIn("音频：编码 flac，48000Hz，2声道，1.41Mbps。", prompt)

    def test_attachment_and_generated_contexts_coexist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            attachment_root, store, attachment_service, generated_service = self._build_services(root)
            stored = attachment_root / "master" / "meeting.txt"
            stored.parent.mkdir(parents=True, exist_ok=True)
            stored.write_text("会议纪要第一段\n会议纪要第二段", encoding="utf-8")

            attachment = attachment_service.create_pending(
                profile_user_id="user",
                session_id="session",
                source="qq",
                kind="document",
                origin_name="meeting.txt",
                storage_relpath="master/meeting.txt",
                timestamp=100,
            )
            attachment_service.mark_ready(
                profile_user_id="user",
                session_id="session",
                attachment_id=attachment["attachment_id"],
                summary_title="会议纪要",
                short_hint="只预览了开头。",
                detail={"text_preview": "会议纪要第一段", "preview_is_truncated": True},
                timestamp=110,
            )
            store.add_generated_file(
                profile_user_id="user",
                session_id="session",
                output_title="会议纪要整理",
                output_format="md",
                storage_relpath="user/session/meeting.md",
                file_size=1024,
                content_card={
                    "summary": "根据 file_001 整理的会议纪要。",
                    "source": {"handle": "file_001", "title": "会议纪要"},
                    "content_preview": "# 会议纪要整理\n\n- 第一项\n- 第二项",
                },
                summary="根据 file_001 整理的会议纪要。",
                created_by_tool="compose_file",
                timestamp=120,
            )
            combined = "\n\n".join(
                [
                    attachment_service.build_prompt_context(
                        profile_user_id="user",
                        session_id="session",
                    ),
                    generated_service.build_prompt_context(
                        profile_user_id="user",
                        session_id="session",
                        limit=8,
                    ),
                ]
            )

            self.assertIn("【当前材料工作台】", combined)
            self.assertIn("【生成文件工作台】", combined)
            self.assertIn("file_001", combined)
            self.assertIn("会议纪要第二段", combined)
            self.assertIn("gen_001：会议纪要整理.md", combined)

    def test_end_to_end_resource_visibility_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            attachment_root, _, attachment_service, generated_service = self._build_services(root)
            stored = attachment_root / "master" / "long.txt"
            stored.parent.mkdir(parents=True, exist_ok=True)
            stored.write_text("\n".join(f"原始第{index}行" for index in range(1, 240)), encoding="utf-8")

            attachment = attachment_service.create_pending(
                profile_user_id="user",
                session_id="session",
                source="qq",
                kind="document",
                origin_name="long.txt",
                storage_relpath="master/long.txt",
                timestamp=100,
            )
            attachment_service.mark_ready(
                profile_user_id="user",
                session_id="session",
                attachment_id=attachment["attachment_id"],
                summary_title="长文本",
                short_hint="只预览了开头。",
                detail={"text_preview": "原始第1行\n原始第2行", "preview_is_truncated": True},
                timestamp=110,
            )

            attachment_prompt = attachment_service.build_prompt_context(
                profile_user_id="user",
                session_id="session",
            )
            self.assertIn("file_001", attachment_prompt)
            self.assertIn("原始第80行", attachment_prompt)

            from companion_v01.tool_runtime import (
                SendFileToolHandler,
                ToolExecutionContext,
            )

            def tool_context(now_ts: int) -> ToolExecutionContext:
                return ToolExecutionContext(
                    profile_user_id="user",
                    session_id="session",
                    now_ts=now_ts,
                    visual_payload={},
                    current_user_source_id=f"msg_{now_ts}",
                )

            composed = register_text_artifact(
                generated_service, profile_user_id="user", session_id="session",
                output_title="长文本整理", output_format="md",
                content="\n".join(f"原始第{i}行" for i in range(1, 201)), timestamp=120,
                source_ids=[attachment["attachment_id"]],
            )
            revised = register_text_artifact(
                generated_service, profile_user_id="user", session_id="session",
                output_title="长文本整理_摘要版", output_format="md",
                content="# 执行摘要\n\n这是补了摘要的整理版。\n\n原始第1行\n原始第2行",
                timestamp=130, revision_of=composed["generated_handle"],
            )
            self.assertEqual(revised["generated_handle"], "gen_002")
            self.assertEqual(revised["version_of_generated_id"], composed["generated_id"])
            self.assertIn("原始第200行", Path(composed["absolute_path"]).read_text(encoding="utf-8"))

            send_handler = SendFileToolHandler(generated_file_service=generated_service)
            send_call = send_handler.normalize_call(
                {
                    "type": "send_file",
                    "targets": ["gen_002"],
                }
            )
            send_result = send_handler.execute(
                call=send_call or {},
                context=tool_context(140),
            )
            self.assertEqual([event["type"] for event in send_result.stream_events], ["file_ready"])
            self.assertEqual(send_result.stream_events[0]["file"]["handle"], "gen_002")

            generated_prompt = generated_service.build_prompt_context(
                profile_user_id="user",
                session_id="session",
                limit=8,
            )
            combined = "\n\n".join([attachment_prompt, generated_prompt])

            self.assertIn("gen_001：长文本整理.md", generated_prompt)
            self.assertIn("gen_002：长文本整理_摘要版.md", generated_prompt)
            self.assertIn("来源工具：fixture.register", generated_prompt)
            self.assertIn("【当前材料工作台】", combined)
            self.assertIn("【生成文件工作台】", combined)


if __name__ == "__main__":
    unittest.main()
