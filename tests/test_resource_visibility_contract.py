from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from companion_v01.attachment_inbox import AttachmentInboxService
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.generated_files import GeneratedFileService
from companion_v01.store import MemoryStore
from companion_v01.task_workspace import TaskWorkspaceService
from companion_v01.tool_runtime import (
    ComposeFileToolHandler,
    ReviseGeneratedFileToolHandler,
    SendFileToolHandler,
    ToolExecutionContext,
)


class ResourceVisibilityContractTests(unittest.TestCase):
    def _build_services(
        self,
        root: Path,
    ) -> tuple[
        Path,
        MemoryStore,
        AttachmentInboxService,
        GeneratedFileService,
        TaskWorkspaceService,
        AkaneMemoryEngine,
    ]:
        attachment_root = root / "attachments"
        store = MemoryStore(root / "db")
        attachment_service = AttachmentInboxService(store=store, base_dir=attachment_root)
        generated_service = GeneratedFileService(
            base_dir=root / "generated_files",
            store=store,
            attachment_service=attachment_service,
        )
        task_service = TaskWorkspaceService(store)
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.store = store
        engine.task_workspace_service = task_service
        return attachment_root, store, attachment_service, generated_service, task_service, engine

    def _tool_context(self, *, now_ts: int) -> ToolExecutionContext:
        return ToolExecutionContext(
            profile_user_id="user",
            session_id="session",
            now_ts=now_ts,
            visual_payload={},
            current_user_source_id=f"msg_{now_ts}",
        )

    def _record_generated_artifacts(
        self,
        *,
        engine: AkaneMemoryEngine,
        tool_result,
        now_ts: int,
    ) -> tuple[list[dict[str, object]], str]:
        return engine._record_tool_result_artifacts_in_task_workspace(
            profile_user_id="user",
            session_id="session",
            tool_result=tool_result,
            now_ts=now_ts,
        )

    def _combined_resource_context(
        self,
        *,
        attachment_service: AttachmentInboxService,
        generated_service: GeneratedFileService,
        task_service: TaskWorkspaceService,
    ) -> str:
        return "\n\n".join(
            section
            for section in [
                attachment_service.build_prompt_context(
                    profile_user_id="user",
                    session_id="session",
                ),
                generated_service.build_prompt_context(
                    profile_user_id="user",
                    session_id="session",
                    limit=8,
                ),
                task_service.build_prompt_context(
                    profile_user_id="user",
                    session_id="session",
                ),
            ]
            if section
        )

    def test_attachment_focus_contract_keeps_full_image_visual_card(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, _, attachment_service, _, _, _ = self._build_services(root)
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
            _, _, attachment_service, _, _, _ = self._build_services(root)
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
            _, store, attachment_service, generated_service, _, _ = self._build_services(root)
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

    def test_task_workspace_contract_coexists_with_attachment_and_generated_contexts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            attachment_root, store, attachment_service, generated_service, task_service, _ = self._build_services(root)
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
            task_service.create_task(
                profile_user_id="user",
                session_id="session",
                raw_request_text="把会议纪要整理一下。",
                normalized_goal="读取附件、整理生成稿并继续推进。",
                steps=[{"title": "整理会议纪要", "status": "running"}],
                artifacts=[{"id": "gen_001", "kind": "md", "title": "会议纪要整理"}],
                timestamp=130,
            )

            combined = self._combined_resource_context(
                attachment_service=attachment_service,
                generated_service=generated_service,
                task_service=task_service,
            )

            self.assertIn("【当前材料工作台】", combined)
            self.assertIn("【生成文件工作台】", combined)
            self.assertIn("【当前任务工作区】", combined)
            self.assertIn("file_001", combined)
            self.assertIn("会议纪要第二段", combined)
            self.assertIn("gen_001：会议纪要整理.md", combined)
            self.assertIn("任务工作区只是白板", combined)
            self.assertIn("gen_001(md / 会议纪要整理)", combined)

    def test_end_to_end_resource_visibility_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            attachment_root, _, attachment_service, generated_service, task_service, engine = self._build_services(root)
            stored = attachment_root / "master" / "long.txt"
            stored.parent.mkdir(parents=True, exist_ok=True)
            stored.write_text("\n".join(f"原始第{index}行" for index in range(1, 240)), encoding="utf-8")

            task_service.create_task(
                profile_user_id="user",
                session_id="session",
                raw_request_text="把长文整理后发我。",
                normalized_goal="读取附件、生成整理稿、继续修改并发回用户。",
                steps=[
                    {"title": "读取原附件", "status": "done"},
                    {"title": "生成整理稿", "status": "running"},
                ],
                timestamp=90,
            )

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

            compose_handler = ComposeFileToolHandler(generated_file_service=generated_service)
            compose_call = compose_handler.normalize_call(
                {
                    "type": "compose_file",
                    "source_ids": ["file_001"],
                    "task": "忠实转成 Markdown，保留原文内容",
                    "output_format": "md",
                    "output_title": "长文本整理",
                    "send_to_user": False,
                }
            )
            compose_result = compose_handler.execute(
                call=compose_call or {},
                context=self._tool_context(now_ts=120),
            )
            composed = compose_result.stream_events[0]["generated_file"]
            composed_text = Path(composed["absolute_path"]).read_text(encoding="utf-8")
            self.assertIn("原始第200行", composed_text)
            self.assertNotIn("注意：上面是系统可安全展开的片段", composed_text)

            compose_events, compose_followup = self._record_generated_artifacts(
                engine=engine,
                tool_result=compose_result,
                now_ts=121,
            )
            self.assertEqual(compose_events[0]["type"], "task_workspace_artifacts_recorded")
            self.assertIn("gen_001", compose_followup)

            revise_handler = ReviseGeneratedFileToolHandler(generated_file_service=generated_service)
            revise_call = revise_handler.normalize_call(
                {
                    "type": "revise_generated_file",
                    "target": "gen_001",
                    "instruction": "在开头补一段执行摘要",
                    "output_format": "md",
                    "output_title": "长文本整理_摘要版",
                    "content_markdown": "# 执行摘要\n\n这是补了摘要的整理版。\n\n原始第1行\n原始第2行",
                    "send_to_user": False,
                }
            )
            revise_result = revise_handler.execute(
                call=revise_call or {},
                context=self._tool_context(now_ts=130),
            )
            revised = revise_result.stream_events[0]["generated_file"]
            self.assertEqual(revised["generated_handle"], "gen_002")

            revise_events, revise_followup = self._record_generated_artifacts(
                engine=engine,
                tool_result=revise_result,
                now_ts=131,
            )
            self.assertEqual(revise_events[0]["type"], "task_workspace_artifacts_recorded")
            self.assertIn("gen_002", revise_followup)

            send_handler = SendFileToolHandler(generated_file_service=generated_service)
            send_call = send_handler.normalize_call(
                {
                    "type": "send_file",
                    "targets": ["gen_002"],
                }
            )
            send_result = send_handler.execute(
                call=send_call or {},
                context=self._tool_context(now_ts=140),
            )
            self.assertEqual([event["type"] for event in send_result.stream_events], ["file_ready"])
            self.assertEqual(send_result.stream_events[0]["file"]["handle"], "gen_002")

            generated_prompt = generated_service.build_prompt_context(
                profile_user_id="user",
                session_id="session",
                limit=8,
            )
            task_prompt = task_service.build_prompt_context(
                profile_user_id="user",
                session_id="session",
            )
            combined = self._combined_resource_context(
                attachment_service=attachment_service,
                generated_service=generated_service,
                task_service=task_service,
            )

            self.assertIn("gen_001：长文本整理.md", generated_prompt)
            self.assertIn("gen_002：长文本整理_摘要版.md", generated_prompt)
            self.assertIn("来源工具：compose_file", generated_prompt)
            self.assertIn("来源工具：revise_generated_file", generated_prompt)
            self.assertIn("任务工作区只是白板", task_prompt)
            self.assertIn("gen_001(md / 长文本整理)", task_prompt)
            self.assertIn("gen_002(md / 长文本整理_摘要版)", task_prompt)
            self.assertIn("tool_artifacts_recorded", task_prompt)
            self.assertIn("【当前材料工作台】", combined)
            self.assertIn("【生成文件工作台】", combined)
            self.assertIn("【当前任务工作区】", combined)


if __name__ == "__main__":
    unittest.main()
