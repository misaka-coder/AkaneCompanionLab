from __future__ import annotations

import unittest
from tests.desktop_pet_source import read_pet_runtime_source
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
            self.assertIn("跳转、倍速、暂停后以新快照为准", build_desktop_pet_system_prompt())
            self.assertNotIn("回复送达", prompt)
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
        source = read_pet_runtime_source()
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
        self.assertIn("hunger: 9/100 (critical)", prompt)
        self.assertIn("energy: 18/100 (low)", prompt)
        self.assertIn("affection: 62/100 (warm)", prompt)
        self.assertNotIn("time_phase:", prompt)
        self.assertNotIn("state=", prompt)
        self.assertNotIn("历史聊天、历史投喂、历史道具效果", prompt)
        self.assertIn("可信当前状态，覆盖较早的聊天、记忆、投喂和台词", system_prompt)
        self.assertIn("括号给出宿主判定的档位", system_prompt)
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

        self.assertNotIn("适合", prompt)
        self.assertIn("hunger: 5/100 (critical)", prompt)
        self.assertIn("energy: 4/100 (critical)", prompt)
        self.assertIn("affection: 8/100 (stranger)", prompt)
        self.assertIn("两项同时 critical 时同时体现", system_prompt)
        self.assertIn("饥饿可讨食或请求投喂", system_prompt)
        self.assertIn("疲惫可话少或想休息", system_prompt)
        self.assertIn("stranger 礼貌有距离", system_prompt)

    def test_care_custom_thresholds_events_and_work_status_remain_current_facts(self) -> None:
        from companion_v01.desktop_context_engine import build_desktop_care_prompt

        state = {"hunger": 36, "energy": 40, "affection": 42, "work_task_active": True,
                 "thresholds": {"hunger_low": 60, "hunger_critical": 45,
                                "energy_low": 50, "energy_critical": 30, "affection_warm": 35},
                 "pending_tier_event": {"from_tier": "familiar", "to_tier": "warm", "direction": "up"}}
        desktop = build_desktop_care_prompt(state, _desktop_context())
        self.assertIn("hunger: 36/100 (critical); energy: 40/100 (low)", desktop)
        self.assertIn("affection: 42/100 (warm)", desktop)
        self.assertIn("pending_tier_event: familiar -> warm (up)", desktop)
        self.assertIn("work_task=active", desktop)
        self.assertNotIn("要承认", desktop)
        self.assertNotIn("work_task=active", build_desktop_care_prompt(state, _qq_context()))
        self.assertEqual(build_desktop_care_prompt({**state, "enabled": False}, _desktop_context()), "")

    def test_desktop_pet_frontend_consumes_authoritative_care_snapshot(self) -> None:
        source = read_pet_runtime_source()

        self.assertIn("function applyPayloadCareSnapshot", source)
        self.assertIn("payload?.care_state", source)
        self.assertIn("applyAuthoritativeCareSnapshot(snapshot)", source)
        self.assertNotIn("function applyPayloadStateRequest", source)
        self.assertNotIn("care.affection + affinityDelta", source)

    def test_desktop_pet_shop_has_allowance_safety_valve(self) -> None:
        main_source = read_pet_runtime_source()
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
        self.assertIn("scope: qq_text", prompt)
        self.assertIn("hunger: 18/100 (low)", prompt)
        self.assertIn("energy: 23/100 (low)", prompt)
        self.assertIn("affection: 14/100 (stranger)", prompt)
        self.assertIn("状态属于当前会话", system_prompt)
        self.assertNotIn("QQ 独立好感", system_prompt)

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
        self.assertIn("状态：正在播放", prompt)
        self.assertIn("control_tool: local_music_control", prompt)
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

    def test_desktop_prompt_does_not_define_model_activity(self) -> None:
        prompt = build_desktop_pet_system_prompt()
        self.assertNotIn("activity", prompt)


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
        self.assertIn("control_tool: local_music_control", prompt)

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
        self.assertIn("local_music_control", prompt)
        self.assertIn("工具回执", build_desktop_pet_system_prompt())
        self.assertIn("状态：已暂停", prompt)
        self.assertNotIn("工具回执", prompt)
        self.assertNotIn("activity.action", prompt)

    def test_audio_catalog_without_playback_does_not_create_activity_context(self) -> None:
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

        self.assertEqual(prompt, "")

    def test_playback_prompt_ignores_catalog_but_retains_current_music_facts(self) -> None:
        prompt = self.engine._build_desktop_activity_prompt(
            {"type": "audio_playback", "status": "running", "title": "当前歌曲",
             "source_id": "track:current", "progress_seconds": 37, "duration_seconds": 222,
             "queue_count": 3, "queue_index": 1, "next_title": "下一首歌",
             "queue_titles": ["当前歌曲", "未播放甲", "未播放乙"],
             "lyric_current": "此刻的歌词",
             "recommendations": [{"title": "同名歌", "source_id": "track:one", "reason": "轻快"}],
             "catalog": [{"title": "同名歌", "source_id": "track:one"},
                         {"title": "同名歌", "source_id": "track:two"}]},
            _desktop_context(),
        )
        self.assertIn("当前歌曲（track:current）", prompt)
        self.assertIn("进度 00:37 / 03:42", prompt)
        self.assertIn("队列：第 1 首 / 共 3 首", prompt)
        self.assertIn("下一首：下一首歌", prompt)
        self.assertIn("当前歌词：此刻的歌词", prompt)
        self.assertIn("control_tool: local_music_control", prompt)
        for omitted in ("当前可播放音乐", "推荐：", "同名歌", "track:one", "track:two", "轻快", "未播放甲", "未播放乙"):
            self.assertNotIn(omitted, prompt)

    def test_desktop_prompt_profile_uses_tools_for_side_effects(self) -> None:
        profile = self.engine._get_prompt_profile_registry().get(ClientMode.DESKTOP_PET)
        prompts = [
            profile.system_prompt_override,
            profile.mode_prompt_override(debug_enabled=False),
            profile.mode_prompt_override(debug_enabled=True),
        ]
        self.assertTrue(all("activity" not in prompt for prompt in prompts))


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
        self.assertIn("歌词未就绪或匹配不可信时不补写", build_desktop_pet_system_prompt())
        self.assertNotIn("不要编造歌词", prompt)
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
        self.assertIn("不保证逐字完全准确", build_desktop_pet_system_prompt())
        self.assertNotIn("不保证逐字完全准确", prompt)
        self.assertEqual(prompt.count("雨夜的小歌.mp3"), 1)
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
        self.assertIn("不保证逐字完全准确", build_desktop_pet_system_prompt())
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
        self.assertIn("来源：系统播放器", prompt)
        self.assertIn("control_tool: system_media_control", prompt)
        self.assertEqual(prompt.count("晴天 - 周杰伦"), 1)
        self.assertEqual(prompt.count("02:15"), 1)
        self.assertNotIn("切换到某个具体音频", prompt)
        self.assert_no_music_backend_terms(prompt)

    def test_music_rules_remain_desktop_only_and_lyrics_keep_a_bounded_tail(self) -> None:
        system = build_desktop_pet_system_prompt()
        self.assertIn("proactive_controls 表示主人允许的主动控制", system)
        self.assertNotIn("proactive_controls", build_qq_text_system_prompt())
        prompt = self.engine._build_desktop_activity_prompt(
            {"type": "audio_playback", "status": "running", "title": "测试歌",
             "lyric_previous": "前" * 500, "lyric_current": "今" * 500, "lyric_next": "后" * 500,
             "lyrics": "不应该注入的整首歌词" * 1000},
            _desktop_context(),
        )
        self.assertIn("当前歌词：" + "今" * 120, prompt)
        self.assertIn("上一句歌词：" + "前" * 100, prompt)
        self.assertIn("下一句歌词：" + "后" * 100, prompt)
        self.assertNotIn("整首歌词", prompt)
        self.assertLess(len(prompt), 550)

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

if __name__ == "__main__":
    unittest.main()
