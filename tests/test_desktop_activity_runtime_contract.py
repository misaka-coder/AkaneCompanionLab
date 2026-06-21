from __future__ import annotations

import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

from companion_v01.client_protocol import (
    ClientCapability,
    ClientMode,
    ClientProtocolContext,
)
from companion_v01.desktop_music_timeline import DesktopMusicTimelineService
from companion_v01.engine import AkaneMemoryEngine


FORBIDDEN_MUSIC_PROMPT_TERMS = ("转写稿", "时间轴", "ASR", "人声分离", "系统片段", "后台处理", "后台准备")


def _desktop_context(*, with_audio: bool = True) -> ClientProtocolContext:
    capabilities = [
        ClientCapability.SPEECH_SEGMENTS.value,
        ClientCapability.FILE_DROP.value,
        ClientCapability.TOOL_ACTIONS.value,
    ]
    if with_audio:
        capabilities.append(ClientCapability.AUDIO_PLAYBACK.value)
    return ClientProtocolContext(
        requested_mode=ClientMode.DESKTOP_PET,
        effective_mode=ClientMode.DESKTOP_PET,
        capabilities=tuple(capabilities),
        output_profile=ClientMode.DESKTOP_PET.value,
        renderer_profile=ClientMode.DESKTOP_PET.value,
    )


def _qq_context() -> ClientProtocolContext:
    return ClientProtocolContext(
        requested_mode=ClientMode.QQ_TEXT,
        effective_mode=ClientMode.QQ_TEXT,
        capabilities=(ClientCapability.SPEECH_SEGMENTS.value,),
        output_profile=ClientMode.QQ_TEXT.value,
        renderer_profile=ClientMode.QQ_TEXT.value,
    )


class DesktopActivityRuntimeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)

    def assert_no_music_backend_terms(self, prompt: str) -> None:
        for term in FORBIDDEN_MUSIC_PROMPT_TERMS:
            self.assertNotIn(term, prompt)

    def test_care_feed_turn_is_not_transient_user_message(self) -> None:
        self.assertFalse(
            self.engine._is_transient_user_turn(
                {
                    "turn_kind": "desktop_pet_care_feed",
                    "transient_user_message": False,
                }
            )
        )
        self.assertFalse(
            self.engine._is_transient_user_turn(
                {
                    "client_turn_kind": "desktop_pet_care_feed",
                }
            )
        )

    def test_proactive_turn_is_transient_user_message(self) -> None:
        self.assertTrue(self.engine._is_transient_user_turn({"turn_kind": "desktop_pet_proactive"}))
        self.assertTrue(self.engine._is_transient_user_turn({"client_turn_kind": "proactive"}))

    def test_desktop_care_prompt_renders_as_temporary_context(self) -> None:
        prompt = self.engine._build_turn_extra_user_context(
            {
                "desktop_care": {
                    "enabled": True,
                    "now": 1716192000000,
                    "hunger": 9,
                    "energy": 18,
                    "affection": 62,
                    "thresholds": {
                        "hunger_low": 25,
                        "hunger_critical": 12,
                        "energy_low": 25,
                        "energy_critical": 12,
                    },
                }
            },
            _desktop_context(),
        )

        self.assertIn("【当前养成状态（本轮临时状态，不写入长期记忆）】", prompt)
        self.assertIn("饥饿值越低表示越饿", prompt)
        self.assertIn("精力值越高表示越精神", prompt)
        self.assertIn("不要把饥饿和精力对调", prompt)
        self.assertIn("当前生理状态只以本轮这组数值为准", prompt)
        self.assertIn("历史聊天、历史投喂、历史道具效果", prompt)
        self.assertIn("如果历史与本轮数值冲突，忽略历史", prompt)
        self.assertIn("饥饿 9/100，精力 18/100，好感 62/100", prompt)
        self.assertIn("生活节奏", prompt)
        self.assertIn("身体状态偏低，容易没精神也惦记吃的", prompt)
        self.assertIn("【饥饿压制】", prompt)
        self.assertIn("平时的独立感和矜持会完全瓦解", prompt)
        self.assertIn("饥饿值很低不是不饿", prompt)
        self.assertIn("不要说'不饿了'", prompt)
        self.assertIn("表情倾向：hungry 或 snack", prompt)
        self.assertIn("好感阶段", prompt)
        self.assertIn("关系已经变暖", prompt)
        self.assertIn("state_request.affinity 可给较高正值", prompt)
        self.assertIn("不要生硬复述这些数值", prompt)

    def test_desktop_care_prompt_combines_hungry_and_sleepy_state(self) -> None:
        prompt = self.engine._build_turn_extra_user_context(
            {
                "desktop_care": {
                    "enabled": True,
                    "now": "2026-06-21T02:30:00",
                    "hunger": 5,
                    "energy": 4,
                    "affection": 8,
                }
            },
            _desktop_context(),
        )

        self.assertIn("深夜", prompt)
        self.assertIn("又饿又困", prompt)
        self.assertIn("【生理压制】", prompt)
        self.assertIn("什么没节操的事都做得出来", prompt)
        self.assertIn("hungry、sleepy、tired 或 yawn", prompt)
        self.assertNotIn("饥饿已经很低", prompt)
        self.assertNotIn("精力已经很低", prompt)
        self.assertIn("好感阶段", prompt)
        self.assertIn("还不太了解这个人", prompt)

    def test_desktop_pet_frontend_consumes_state_request_affinity(self) -> None:
        source = Path("desktop_pet_next/src/main.js").read_text(encoding="utf-8")

        self.assertIn("function applyPayloadStateRequest", source)
        self.assertIn("payload?.state_request", source)
        self.assertIn("care.affection + affinityDelta", source)
        self.assertIn("persistCareRuntimeChange()", source)

    def test_desktop_pet_shop_has_allowance_safety_valve(self) -> None:
        main_source = Path("desktop_pet_next/src/main.js").read_text(encoding="utf-8")
        shop_source = Path("desktop_pet_next/src/shop.js").read_text(encoding="utf-8")
        shop_html = Path("desktop_pet_next/shop.html").read_text(encoding="utf-8")
        template = Path("desktop_pet_creator_kit/templates/character_pack/character.json").read_text(
            encoding="utf-8"
        )

        self.assertIn("function claimCareAllowance", main_source)
        self.assertIn('case "claimCareAllowance"', main_source)
        self.assertIn("care.lastAllowanceAt = now", main_source)
        self.assertIn("persistCareRuntimeChange()", main_source)
        self.assertIn("claimCareAllowance", shop_source)
        self.assertIn("renderAllowance", shop_source)
        self.assertIn('id="allowance-panel"', shop_html)
        self.assertIn('"allowance"', template)
        self.assertIn('"max_coins"', template)

    def test_qq_care_prompt_shares_vitals_but_separates_affection_scope(self) -> None:
        prompt = self.engine._build_turn_extra_user_context(
            {
                "desktop_care": {
                    "enabled": True,
                    "source": "care_runtime",
                    "shared_vitals": True,
                    "affection_scope": "qq_text",
                    "hunger": 18,
                    "energy": 23,
                    "affection": 14,
                }
            },
            _qq_context(),
        )

        self.assertIn("【当前养成状态（QQ 临时上下文；本轮不写入长期记忆）】", prompt)
        self.assertIn("饥饿 18/100，精力 23/100，QQ好感 14/100", prompt)
        self.assertIn("饥饿和精力与桌宠共享", prompt)
        self.assertIn("QQ好感只代表 QQ 互动关系", prompt)
        self.assertIn("和桌宠好感分开计算", prompt)

    def test_audio_playback_prompt_does_not_imply_interruption(self) -> None:
        prompt = self.engine._build_desktop_activity_prompt(
            {
                "type": "audio_playback",
                "status": "running",
                "title": "雨夜的小歌.mp3",
                "source_id": "file_012",
                "progress_seconds": 37,
                "duration_seconds": 222,
            },
            _desktop_context(),
        )

        self.assertIn("【当前桌宠活动】", prompt)
        self.assertIn("类型：普通音频播放", prompt)
        self.assertIn("雨夜的小歌.mp3", prompt)
        self.assertIn("进度 00:37 / 03:42", prompt)
        self.assertIn("普通音频不会因为本轮消息自动暂停", prompt)
        self.assertNotIn("主人发消息时表演已暂停", prompt)

    def test_audio_playback_prompt_includes_current_lyric_without_backend_terms(self) -> None:
        prompt = self.engine._build_desktop_activity_prompt(
            {
                "type": "audio_playback",
                "status": "running",
                "title": "雨夜的小歌.mp3",
                "source_id": "file_012",
                "lyric_previous": "雨落在窗边",
                "lyric_current": "你把灯光留给我",
                "lyric_next": "我就轻轻唱下去",
            },
            _desktop_context(),
        )

        self.assertIn("当前歌词：你把灯光留给我", prompt)
        self.assertIn("上一句歌词：雨落在窗边", prompt)
        self.assertIn("下一句歌词：我就轻轻唱下去", prompt)
        self.assert_no_music_backend_terms(prompt)

    def test_desktop_audio_capability_discourages_task_workspace_for_playback_control(self) -> None:
        prompt = self.engine._build_client_mode_prompt_context(_desktop_context())

        self.assertIn("桌宠支持轻量 activity 控制", prompt)
        self.assertIn("activity 是执行请求，不是完成回执", prompt)
        self.assertIn("不要在 speech 里假装已经播放、暂停或继续", prompt)
        self.assertIn("播放、暂停、继续、切歌这类轻量桌宠播放控制不要创建任务工作区", prompt)

    def test_vocal_performance_interrupted_prompt_requires_activity_action(self) -> None:
        prompt = self.engine._build_desktop_activity_prompt(
            {
                "type": "vocal_performance",
                "status": "interrupted",
                "title": "Akane 翻唱.wav",
                "source_id": "gen_018",
                "progress_seconds": 77,
                "duration_seconds": 205,
            },
            _desktop_context(),
        )

        self.assertIn("类型：角色表演/唱歌", prompt)
        self.assertIn("状态：因主人发来消息已暂停", prompt)
        self.assertIn("进度 01:17 / 03:25", prompt)
        self.assertIn("如果你想继续表演，需要输出 activity action", prompt)
        self.assertIn('"action":"play|pause|resume|stop|previous|next"', prompt)

    def test_activity_prompt_frames_action_as_request_not_success_receipt(self) -> None:
        prompt = self.engine._build_desktop_activity_prompt(
            {
                "type": "audio_playback",
                "status": "paused",
                "title": "手边的歌.flac",
                "source_id": "audio_008",
                "progress_seconds": 61,
            },
            _desktop_context(),
        )

        self.assertIn("activity 是给桌宠执行的请求，不是执行成功回执", prompt)
        self.assertIn("speech 里不要说已经播放、已经暂停或已经继续", prompt)
        self.assertIn("切换到某个具体音频时，play 应尽量带 source_id", prompt)
        self.assertIn("只继续当前音频时，用 resume + target=current", prompt)

    def test_audio_recommendations_prompt_exposes_playable_catalog_without_current_track(self) -> None:
        prompt = self.engine._build_desktop_activity_prompt(
            {
                "type": "audio_recommendations",
                "status": "idle",
                "recommendations": [
                    {
                        "title": "Starry Days",
                        "reason": "手边音频",
                        "source_id": "workspace:attachment:audio_001",
                    }
                ],
                "catalog": [
                    {
                        "title": "Starry Days",
                        "reason": "手边音频",
                        "source_id": "workspace:attachment:audio_001",
                    }
                ],
            },
            _desktop_context(),
        )

        self.assertIn("类型：可播放音乐推荐", prompt)
        self.assertIn("当前 Akane 音乐推荐", prompt)
        self.assertIn("Starry Days", prompt)
        self.assertIn("workspace:attachment:audio_001", prompt)
        self.assertIn("当前可播放音乐", prompt)

    def test_desktop_prompt_profile_keeps_activity_as_execution_request(self) -> None:
        profile = self.engine._get_prompt_profile_registry().get(ClientMode.DESKTOP_PET)
        prompts = [
            profile.system_prompt_override,
            profile.mode_prompt_override(debug_enabled=False),
            profile.mode_prompt_override(debug_enabled=True),
        ]

        self.assertTrue(any("activity 只用于桌宠播放控制" in prompt for prompt in prompts))
        self.assertTrue(any("activity 是给桌宠执行的请求" in prompt for prompt in prompts))
        self.assertTrue(any("不要在 speech 里假装动作已经播放、暂停或继续" in prompt for prompt in prompts))

    def test_activity_prompt_is_desktop_audio_capability_only(self) -> None:
        activity = {
            "type": "audio_playback",
            "status": "running",
            "title": "test.mp3",
        }
        qq_context = ClientProtocolContext(
            requested_mode=ClientMode.QQ_TEXT,
            effective_mode=ClientMode.QQ_TEXT,
            capabilities=(ClientCapability.SPEECH_SEGMENTS.value,),
            output_profile=ClientMode.QQ_TEXT.value,
            renderer_profile=ClientMode.QQ_TEXT.value,
        )

        self.assertEqual(self.engine._build_desktop_activity_prompt(activity, qq_context), "")
        self.assertEqual(
            self.engine._build_desktop_activity_prompt(activity, _desktop_context(with_audio=False)),
            "",
        )

    def test_missing_timeline_prompt_does_not_invent_lyrics(self) -> None:
        class EmptyTimelineStore:
            def get_desktop_music_timeline(self, *, timeline_id: str):
                return None

            def get_desktop_music_timeline_by_source(self, *, profile_user_id: str, session_id: str, source_id: str):
                return None

        self.engine.desktop_music_timeline_service = DesktopMusicTimelineService(
            store=EmptyTimelineStore(),
            generated_file_service=None,
        )
        prompt = self.engine._build_desktop_activity_prompt(
            {
                "type": "audio_playback",
                "status": "running",
                "title": "雨夜的小歌.mp3",
                "source_id": "audio_001",
                "progress_seconds": 42,
                "duration_seconds": 180,
            },
            _desktop_context(),
            profile_user_id="master",
            session_id="desktop_pet_test",
        )

        self.assertIn("【当前音乐位置】", prompt)
        self.assertIn("歌词线索还没准备好", prompt)
        self.assertIn("不要编造歌词内容", prompt)
        self.assert_no_music_backend_terms(prompt)

    def test_ready_timeline_prompt_injects_nearby_segments_only(self) -> None:
        class ReadyTimelineStore:
            def get_desktop_music_timeline(self, *, timeline_id: str):
                return None

            def get_desktop_music_timeline_by_source(self, *, profile_user_id: str, session_id: str, source_id: str):
                return {
                    "timeline_id": "music_timeline::1",
                    "profile_user_id": profile_user_id,
                    "session_id": session_id,
                    "source_id": source_id,
                    "title": "雨夜的小歌.mp3",
                    "status": "ready",
                    "source_kind": "attachment#music_timeline_vocal_v1#vocal_asr",
                    "ready_until_seconds": 120,
                    "rolling_summary": "前半段是轻柔的雨夜氛围。",
                    "segments": [
                        {"start": 1, "end": 5, "text": "很早的前奏旁白"},
                        {"start": 35, "end": 39, "text": "窗外的雨声慢慢靠近"},
                        {"start": 43, "end": 48, "text": "你说今晚也想有人陪你"},
                        {"start": 100, "end": 104, "text": "很后面的歌词"},
                    ],
                }

        self.engine.desktop_music_timeline_service = DesktopMusicTimelineService(
            store=ReadyTimelineStore(),
            generated_file_service=None,
        )
        prompt = self.engine._build_desktop_activity_prompt(
            {
                "type": "audio_playback",
                "status": "running",
                "title": "雨夜的小歌.mp3",
                "source_id": "audio_001",
                "progress_seconds": 42,
                "duration_seconds": 180,
            },
            _desktop_context(),
            profile_user_id="master",
            session_id="desktop_pet_test",
        )

        self.assertIn("此刻附近隐约听到的词句", prompt)
        self.assertIn("窗外的雨声慢慢靠近", prompt)
        self.assertIn("你说今晚也想有人陪你", prompt)
        self.assertNotIn("很早的前奏旁白", prompt)
        self.assertNotIn("很后面的歌词", prompt)
        self.assertIn("不保证逐字完全准确", prompt)
        self.assertNotIn("受伴奏影响", prompt)
        self.assert_no_music_backend_terms(prompt)

    def test_mixed_asr_fallback_prompt_warns_about_uncertainty(self) -> None:
        class MixedTimelineStore:
            def get_desktop_music_timeline(self, *, timeline_id: str):
                return None

            def get_desktop_music_timeline_by_source(self, *, profile_user_id: str, session_id: str, source_id: str):
                return {
                    "timeline_id": "music_timeline::mixed",
                    "profile_user_id": profile_user_id,
                    "session_id": session_id,
                    "source_id": source_id,
                    "title": "热闹的歌.mp3",
                    "status": "ready",
                    "quality": "mixed_asr",
                    "ready_until_seconds": 90,
                    "segments": [
                        {"start": 48, "end": 51, "text": "人潮里忽然想起你"},
                        {"start": 55, "end": 58, "text": "把晚风都交给回忆"},
                    ],
                }

        self.engine.desktop_music_timeline_service = DesktopMusicTimelineService(
            store=MixedTimelineStore(),
            generated_file_service=None,
        )
        prompt = self.engine._build_desktop_activity_prompt(
            {
                "type": "audio_playback",
                "status": "running",
                "title": "热闹的歌.mp3",
                "source_id": "audio_002",
                "progress_seconds": 50,
                "duration_seconds": 180,
            },
            _desktop_context(),
            profile_user_id="master",
            session_id="desktop_pet_test",
        )

        self.assertIn("人潮里忽然想起你", prompt)
        self.assertIn("不保证逐字完全准确", prompt)
        self.assertIn("受伴奏影响", prompt)
        self.assert_no_music_backend_terms(prompt)

    def test_timeline_build_prefers_vocal_track_before_mixed_audio(self) -> None:
        class VocalFirstTimelineService(DesktopMusicTimelineService):
            def __init__(self, *, store, generated_file_service, vocals_path: Path | None):
                super().__init__(store=store, generated_file_service=generated_file_service)
                self.vocals_path = vocals_path
                self.calls = []

            def _separate_vocals_to_cache(self, *, source_path: Path, work_dir: Path):
                return self.vocals_path

            def _transcribe_audio_path(self, *, audio_path: Path, source: dict, ffmpeg_path: str, quality: str):
                self.calls.append((audio_path.name, quality))
                return {
                    "status": "ready",
                    "quality": quality,
                    "segments": [{"start": 0, "end": 1, "text": "测试"}],
                }

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = root / "song.mp3"
            source_path.write_bytes(b"fake")
            vocals_path = root / "vocals.wav"
            vocals_path.write_bytes(b"fake")

            service = VocalFirstTimelineService(
                store=None,
                generated_file_service=None,
                vocals_path=vocals_path,
            )
            with patch("companion_v01.desktop_music_timeline.importlib.util.find_spec", return_value=object()), patch(
                "companion_v01.desktop_music_timeline.shutil.which",
                return_value="ffmpeg",
            ):
                vocal_result = service._transcribe_source({"absolute_path": source_path})

            self.assertEqual(vocal_result["quality"], "vocal_asr")
            self.assertEqual(service.calls, [("vocals.wav", "vocal_asr")])

            service = VocalFirstTimelineService(
                store=None,
                generated_file_service=None,
                vocals_path=None,
            )
            with patch("companion_v01.desktop_music_timeline.importlib.util.find_spec", return_value=object()), patch(
                "companion_v01.desktop_music_timeline.shutil.which",
                return_value="ffmpeg",
            ):
                mixed_result = service._transcribe_source({"absolute_path": source_path})

            self.assertEqual(mixed_result["quality"], "mixed_asr")
            self.assertEqual(service.calls, [("song.mp3", "mixed_asr")])

    def test_direct_vocal_source_skips_separation(self) -> None:
        class DirectVocalTimelineService(DesktopMusicTimelineService):
            def __init__(self):
                super().__init__(store=None, generated_file_service=None)
                self.calls = []
                self.separation_called = False

            def _separate_vocals_to_cache(self, *, source_path: Path, work_dir: Path):
                self.separation_called = True
                return None

            def _transcribe_audio_path(self, *, audio_path: Path, source: dict, ffmpeg_path: str, quality: str):
                self.calls.append((audio_path.name, quality))
                return {
                    "status": "ready",
                    "quality": quality,
                    "segments": [{"start": 0, "end": 1, "text": "纯人声测试"}],
                }

        with TemporaryDirectory() as tmp:
            source_path = Path(tmp) / "voice.wav"
            source_path.write_bytes(b"fake")
            service = DirectVocalTimelineService()
            with patch("companion_v01.desktop_music_timeline.importlib.util.find_spec", return_value=object()), patch(
                "companion_v01.desktop_music_timeline.shutil.which",
                return_value="ffmpeg",
            ):
                result = service._transcribe_source({"absolute_path": source_path, "role": "vocals"})

            self.assertEqual(result["quality"], "vocal_asr")
            self.assertEqual(service.calls, [("voice.wav", "vocal_asr")])
            self.assertFalse(service.separation_called)

    def test_empty_vocal_separation_falls_back_to_source_audio(self) -> None:
        class EmptyVocalFallbackTimelineService(DesktopMusicTimelineService):
            def __init__(self, *, vocals_path: Path):
                super().__init__(store=None, generated_file_service=None)
                self.vocals_path = vocals_path
                self.calls = []

            def _separate_vocals_to_cache(self, *, source_path: Path, work_dir: Path):
                return self.vocals_path

            def _transcribe_audio_path(self, *, audio_path: Path, source: dict, ffmpeg_path: str, quality: str):
                self.calls.append((audio_path.name, quality))
                if quality == "vocal_asr":
                    return {"status": "ready", "quality": quality, "segments": []}
                return {
                    "status": "ready",
                    "quality": quality,
                    "segments": [{"start": 0, "end": 1, "text": "回退原音频测试"}],
                }

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = root / "song.mp3"
            source_path.write_bytes(b"fake")
            vocals_path = root / "vocals.wav"
            vocals_path.write_bytes(b"fake")
            service = EmptyVocalFallbackTimelineService(vocals_path=vocals_path)
            with patch("companion_v01.desktop_music_timeline.importlib.util.find_spec", return_value=object()), patch(
                "companion_v01.desktop_music_timeline.shutil.which",
                return_value="ffmpeg",
            ):
                result = service._transcribe_source({"absolute_path": source_path})

            self.assertEqual(result["quality"], "mixed_asr")
            self.assertEqual(service.calls, [("vocals.wav", "vocal_asr"), ("song.mp3", "mixed_asr")])

    def test_instrumental_source_skips_separation_and_transcription(self) -> None:
        class InstrumentalTimelineService(DesktopMusicTimelineService):
            def __init__(self):
                super().__init__(store=None, generated_file_service=None)
                self.separation_called = False
                self.transcribe_called = False

            def _separate_vocals_to_cache(self, *, source_path: Path, work_dir: Path):
                self.separation_called = True
                return None

            def _transcribe_audio_path(self, *, audio_path: Path, source: dict, ffmpeg_path: str, quality: str):
                self.transcribe_called = True
                return {"status": "ready", "quality": quality, "segments": [{"start": 0, "end": 1, "text": "不应出现"}]}

        with TemporaryDirectory() as tmp:
            source_path = Path(tmp) / "piano_pure_music.wav"
            source_path.write_bytes(b"fake")
            service = InstrumentalTimelineService()
            with patch("companion_v01.desktop_music_timeline.importlib.util.find_spec", return_value=object()), patch(
                "companion_v01.desktop_music_timeline.shutil.which",
                return_value="ffmpeg",
            ):
                result = service._transcribe_source({"absolute_path": source_path, "role": "instrumental"})

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["error"], "instrumental_has_no_lyrics")
            self.assertFalse(service.separation_called)
            self.assertFalse(service.transcribe_called)

    def test_activity_prompt_triggers_timeline_prepare_for_workspace_audio(self) -> None:
        class PrepareAwareTimelineService:
            def __init__(self):
                self.prepared_activity = None

            def prepare_timeline(self, *, profile_user_id: str, session_id: str, activity: dict):
                self.prepared_activity = dict(activity)
                return {
                    "ok": True,
                    "timeline": {
                        "timeline_id": "music_timeline::pending",
                        "source_id": activity.get("source_id"),
                        "status": "pending",
                    },
                    "scheduled": True,
                }

            def build_prompt_projection(self, *, profile_user_id: str, session_id: str, activity: dict):
                return "【当前音乐位置】\n- 歌词线索还没准备好，当前还不能确定唱到哪一句。"

        service = PrepareAwareTimelineService()
        self.engine.desktop_music_timeline_service = service
        prompt = self.engine._build_desktop_activity_prompt(
            {
                "type": "audio_playback",
                "status": "running",
                "title": "工作台音频.flac",
                "source_id": "audio_001",
                "attachment_handle": "audio_001",
                "progress_seconds": 9,
            },
            _desktop_context(),
            profile_user_id="master",
            session_id="desktop_pet_test",
        )

        self.assertEqual(service.prepared_activity["source_id"], "audio_001")
        self.assertIn("歌词线索还没准备好", prompt)
        self.assert_no_music_backend_terms(prompt)

    def test_local_lyric_activity_skips_backend_timeline_prepare(self) -> None:
        class PrepareAwareTimelineService:
            def __init__(self):
                self.prepared_activity = None

            def prepare_timeline(self, *, profile_user_id: str, session_id: str, activity: dict):
                self.prepared_activity = dict(activity)
                return {"ok": True, "timeline": None, "scheduled": True}

            def build_prompt_projection(self, *, profile_user_id: str, session_id: str, activity: dict):
                return "【当前音乐位置】\n- 这首歌的歌词线索还没准备好。"

        service = PrepareAwareTimelineService()
        self.engine.desktop_music_timeline_service = service
        prompt = self.engine._build_desktop_activity_prompt(
            {
                "type": "audio_playback",
                "status": "running",
                "title": "带歌词的歌.flac",
                "source_id": "audio_lrc",
                "lyric_current": "这句是本地歌词",
                "lyric_next": "下一句也在本地",
                "progress_seconds": 12,
            },
            _desktop_context(),
            profile_user_id="master",
            session_id="desktop_pet_test",
        )

        self.assertIsNone(service.prepared_activity)
        self.assertIn("当前歌词：这句是本地歌词", prompt)
        self.assertNotIn("歌词线索还没准备好", prompt)
        self.assert_no_music_backend_terms(prompt)

    def test_system_media_activity_skips_backend_timeline_prepare(self) -> None:
        class PrepareAwareTimelineService:
            def __init__(self):
                self.prepared_activity = None

            def prepare_timeline(self, *, profile_user_id: str, session_id: str, activity: dict):
                self.prepared_activity = dict(activity)
                return {"ok": True, "timeline": None, "scheduled": True}

            def build_prompt_projection(self, *, profile_user_id: str, session_id: str, activity: dict):
                return "【当前音乐位置】\n- 这首歌的歌词线索还没准备好。"

        service = PrepareAwareTimelineService()
        self.engine.desktop_music_timeline_service = service
        prompt = self.engine._build_desktop_activity_prompt(
            {
                "type": "audio_playback",
                "status": "running",
                "title": "晴天 - 周杰伦",
                "source_id": "system_media:qqmusic-qingtian",
                "source_kind": "system_media",
                "system_media": True,
                "progress_seconds": 135,
            },
            _desktop_context(),
            profile_user_id="master",
            session_id="desktop_pet_test",
        )

        self.assertIsNone(service.prepared_activity)
        self.assertIn("晴天 - 周杰伦", prompt)
        self.assertIn("进度 02:15", prompt)
        self.assertIn("歌词线索还没准备好", prompt)
        self.assertIn("系统媒体来自 Windows 当前媒体会话", prompt)
        self.assertIn("系统媒体控制请求，不是执行成功回执", prompt)
        self.assertIn('"action":"play|pause|resume|stop|previous|next"', prompt)
        self.assertIn('"source_id":"system_media:qqmusic-qingtian"', prompt)
        self.assertNotIn("切换到某个具体音频", prompt)
        self.assert_no_music_backend_terms(prompt)

    def test_ready_audio_does_not_start_timeline_prepare(self) -> None:
        class PrepareAwareTimelineService:
            def __init__(self):
                self.prepared_activity = None

            def prepare_timeline(self, *, profile_user_id: str, session_id: str, activity: dict):
                self.prepared_activity = dict(activity)
                return {"ok": True, "timeline": None, "scheduled": True}

            def build_prompt_projection(self, *, profile_user_id: str, session_id: str, activity: dict):
                return "【当前音乐位置】\n- 这首歌的歌词线索还没准备好。"

        service = PrepareAwareTimelineService()
        self.engine.desktop_music_timeline_service = service
        prompt = self.engine._build_desktop_activity_prompt(
            {
                "type": "audio_playback",
                "status": "ready",
                "title": "刚拖进来的歌.flac",
                "source_id": "audio_002",
                "attachment_handle": "audio_002",
                "progress_seconds": 0,
            },
            _desktop_context(),
            profile_user_id="master",
            session_id="desktop_pet_test",
        )

        self.assertIsNone(service.prepared_activity)
        self.assertIn("已放在手边，尚未播放", prompt)
        self.assertIn("歌词线索还没准备好", prompt)
        self.assert_no_music_backend_terms(prompt)

    def test_normalize_activity_action_keeps_only_safe_actions(self) -> None:
        action = self.engine._normalize_activity_action(
            {
                "action": "resume",
                "target": "current",
                "type": "vocal_performance",
                "source_id": "gen_018",
            }
        )

        self.assertEqual(
            action,
            {
                "action": "resume",
                "target": "current",
                "type": "vocal_performance",
                "source_id": "gen_018",
            },
        )
        self.assertEqual(
            self.engine._normalize_activity_action({"action": "next"}),
            {
                "action": "next",
                "target": "current",
            },
        )
        self.assertEqual(
            self.engine._normalize_activity_action({"action": "previous", "source_id": "local:1"}),
            {
                "action": "previous",
                "target": "current",
                "source_id": "local:1",
            },
        )
        self.assertIsNone(self.engine._normalize_activity_action({"action": "continue"}))

    def test_desktop_runtime_only_interrupts_vocal_performance(self) -> None:
        runtime_path = Path("desktop_pet/renderer/services/ActivityRuntime.js")
        source = runtime_path.read_text(encoding="utf-8")

        self.assertIn('interruptForUserMessage()', source)
        self.assertIn('this._current?.type === "vocal_performance"', source)
        self.assertIn('this._current.status === "running"', source)
        self.assertIn('nextStatus: "interrupted"', source)
        self.assertNotIn('this._current?.type === "audio_playback" && this._current.status === "running"', source)


if __name__ == "__main__":
    unittest.main()
