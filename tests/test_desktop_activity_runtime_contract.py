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
from companion_v01.prompt_blocks import build_desktop_pet_system_prompt, build_qq_text_system_prompt


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

    def test_video_and_unknown_browser_media_do_not_enter_music_pipeline(self):
        for kind in ("video_playback", "media_playback"):
            prompt = self.engine._build_desktop_activity_prompt(
                {"type": kind, "title": "测试视频", "status": "running", "progress_seconds": 80,
                 "duration_seconds": 180, "source_kind": "system_media", "captured_at": 1789000000},
                _desktop_context(), profile_user_id="master", session_id="desktop_pet_test")
            self.assertIn("采样时进度：01:20", prompt)
            self.assertIn("拖动", prompt)
            self.assertNotIn("【当前桌宠活动】", prompt)
            self.assertNotIn("当前歌词：", prompt)

    def test_care_feed_turn_is_not_transient_user_message(self) -> None:
        self.assertFalse(
            self.engine._is_transient_user_turn(
                {
                    "turn_kind": "desktop_pet_care_feed",
                    "transient_user_message": False,
                }
            )
        )
        source = Path("desktop_pet_next/src/main.js").read_text(encoding="utf-8")
        self.assertIn("刚才发生的互动：我投喂了你「${itemName}」。", source)
        self.assertNotIn("刚才发生的互动：用户投喂了你${itemName}。", source)
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

        system_prompt = build_desktop_pet_system_prompt()

        self.assertIn("【care.state｜宿主当前值】", prompt)
        self.assertIn("scope: desktop_pet", prompt)
        self.assertIn("hunger=9/100, hunger_level=critical", prompt)
        self.assertIn("energy=18/100, energy_level=low", prompt)
        self.assertIn("affection: 62/100 (affection_tier=warm)", prompt)
        self.assertIn("time_phase:", prompt)
        self.assertNotIn("历史聊天、历史投喂、历史道具效果", prompt)
        self.assertIn("可信当前状态，覆盖较早的聊天、记忆、投喂和台词", system_prompt)
        self.assertIn("hunger_level=critical", system_prompt)
        self.assertIn("饥饿可讨食或请求投喂", system_prompt)
        self.assertIn("warm 可柔软、打趣或主动", system_prompt)
        self.assertIn("不复述数值", system_prompt)

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

        system_prompt = build_desktop_pet_system_prompt()

        self.assertIn("深夜", prompt)
        self.assertIn("state=又饿又困——两项都到临界线", prompt)
        self.assertIn("hunger=5/100, hunger_level=critical", prompt)
        self.assertIn("energy=4/100, energy_level=critical", prompt)
        self.assertIn("affection_tier=stranger", prompt)
        self.assertIn("两项同时 critical 时同时体现", system_prompt)
        self.assertIn("饥饿可讨食或请求投喂", system_prompt)
        self.assertIn("疲惫可话少或想休息", system_prompt)
        self.assertIn("stranger 礼貌有距离", system_prompt)

    def test_desktop_pet_frontend_consumes_authoritative_care_snapshot(self) -> None:
        source = Path("desktop_pet_next/src/main.js").read_text(encoding="utf-8")

        self.assertIn("function applyPayloadCareSnapshot", source)
        self.assertIn("payload?.care_state", source)
        self.assertIn("applyAuthoritativeCareSnapshot(snapshot)", source)
        self.assertNotIn("function applyPayloadStateRequest", source)
        self.assertNotIn("care.affection + affinityDelta", source)

    def test_desktop_pet_shop_has_allowance_safety_valve(self) -> None:
        main_source = Path("desktop_pet_next/src/main.js").read_text(encoding="utf-8")
        shop_source = Path("desktop_pet_next/src/shop.js").read_text(encoding="utf-8")
        shop_html = Path("desktop_pet_next/shop.html").read_text(encoding="utf-8")
        template = Path("desktop_pet_creator_kit/templates/character_pack/character.json").read_text(
            encoding="utf-8"
        )

        self.assertIn("function claimCareAllowance", main_source)
        self.assertIn('case "claimCareAllowance"', main_source)
        self.assertIn('performDesktopCareAction("claim_allowance")', main_source)
        self.assertNotIn("care.lastAllowanceAt = now", main_source)
        self.assertNotIn("function persistCareRuntimeChange", main_source)
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

        system_prompt = build_qq_text_system_prompt()

        self.assertIn("【care.state｜宿主当前值】", prompt)
        self.assertIn("scope: qq_text（饥饿/精力与桌宠共享；affection 为 QQ 独立好感）", prompt)
        self.assertIn("hunger=18/100, hunger_level=low", prompt)
        self.assertIn("energy=23/100, energy_level=low", prompt)
        self.assertIn("affection: 14/100 (affection_tier=stranger)", prompt)
        self.assertIn("scope=qq_text", system_prompt)
        self.assertIn("使用 QQ 独立好感", system_prompt)

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

    def test_desktop_audio_capability_uses_lightweight_activity_requests(self) -> None:
        prompt = build_desktop_pet_system_prompt()

        self.assertIn("activity 是给桌宠执行的请求，不是完成回执", prompt)
        self.assertIn("不要在 speech 里假装动作已经播放、暂停或继续", prompt)
        self.assertIn("播放、暂停、继续、停止和切歌属于轻量桌宠控制", prompt)

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

        self.assertTrue(any("桌宠需要播放、暂停、继续、停止" in prompt for prompt in prompts))
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

    def test_timeline_build_prefers_vocal_track_before_mixed_audio(self):
        calls = []
        def transcribe(**kwargs):
            calls.append(kwargs["source_id"])
            return {"status": "ready", "segments": [{"start": 0, "end": 1, "text": "fixture"}]}
        service = DesktopMusicTimelineService(store=None, generated_file_service=None, transcriber=transcribe,
            vocal_preparer=lambda **_: {"status": "ready", "handle": "gen_vocals"})
        result = service._transcribe_source({"source_id": "audio_1"})
        self.assertEqual(result["quality"], "vocal_asr")
        self.assertEqual(calls, ["gen_vocals"])
        service.vocal_preparer = None
        result = service._transcribe_source({"source_id": "audio_1"})
        self.assertEqual(result["quality"], "mixed_asr")
        self.assertEqual(calls, ["gen_vocals", "audio_1"])

    def test_direct_vocal_source_skips_separation(self):
        def fail(**_):
            raise AssertionError("Separation should not run")
        calls = []
        def transcribe(**kwargs):
            calls.append(kwargs["source_id"])
            return {"status": "ready", "segments": [{"text": "fixture"}]}
        service = DesktopMusicTimelineService(store=None, generated_file_service=None, vocal_preparer=fail, transcriber=transcribe)
        result = service._transcribe_source({"source_id": "gen_vocals", "role": "vocals"})
        self.assertEqual(result["quality"], "vocal_asr")
        self.assertEqual(calls, ["gen_vocals"])

    def test_empty_vocal_separation_falls_back_to_source_audio(self):
        calls = []
        def transcribe(**kwargs):
            handle = kwargs["source_id"]
            calls.append(handle)
            return {"status": "ready", "segments": [] if handle == "gen_vocals" else [{"text": "fixture"}]}
        service = DesktopMusicTimelineService(store=None, generated_file_service=None, transcriber=transcribe,
            vocal_preparer=lambda **_: {"status": "ready", "handle": "gen_vocals"})
        result = service._transcribe_source({"source_id": "audio_1"})
        self.assertEqual(result["quality"], "mixed_asr")
        self.assertEqual(calls, ["gen_vocals", "audio_1"])

    def test_instrumental_source_skips_separation_and_transcription(self):
        calls = []
        def operation(**_):
            calls.append("unexpected")
            return {}
        service = DesktopMusicTimelineService(store=None, generated_file_service=None,
            vocal_preparer=operation, transcriber=operation)
        result = service._transcribe_source({"source_id": "gen_instrumental", "role": "instrumental"})
        self.assertEqual(result["error"], "instrumental_has_no_lyrics")
        self.assertEqual(calls, [])

    def test_missing_transcription_plugin_does_not_load_a_model(self):
        service = DesktopMusicTimelineService(store=None, generated_file_service=None)
        result = service._transcribe_source({"source_id": "audio_1"})
        self.assertEqual(result["error"], "transcription_plugin_unavailable")

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
