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


class DesktopActivityRuntimeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)

    def assert_no_music_backend_terms(self, prompt: str) -> None:
        for term in FORBIDDEN_MUSIC_PROMPT_TERMS:
            self.assertNotIn(term, prompt)

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

        self.assertIn("类型：Akane 表演/唱歌", prompt)
        self.assertIn("状态：因主人发来消息已暂停", prompt)
        self.assertIn("进度 01:17 / 03:25", prompt)
        self.assertIn("如果你想继续表演，需要输出 activity action", prompt)
        self.assertIn('"action":"play|pause|resume|stop"', prompt)

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

    def test_desktop_prompt_profile_keeps_activity_as_execution_request(self) -> None:
        profile = self.engine._get_prompt_profile_registry().get(ClientMode.DESKTOP_PET)
        fast_prompt = profile.mode_prompt_override(debug_enabled=False)
        debug_prompt = profile.mode_prompt_override(debug_enabled=True)

        for prompt in (fast_prompt, debug_prompt):
            self.assertIn("activity 只用于桌宠播放控制", prompt)
            self.assertIn("activity 是执行请求，不是完成回执", prompt)
            self.assertIn("不要在 speech 里假装动作已经执行", prompt)

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
