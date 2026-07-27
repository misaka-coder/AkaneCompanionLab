from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from companion_v01.local_capability_config import save_provider_config, save_voice_profile_config
from companion_v01.qq_gateway import NapCatQQGateway
from companion_v01.routes.qq import (
    QQSessionTurnCoordinator,
    _filter_unsent_reply_messages,
    _process_qq_turn_streaming,
    _synthesize_qq_voice_file,
)
from companion_v01.runtime_settings import BotSettingsView


class FakeTTSClient:
    async def synthesize(self, text: str) -> bytes:
        return b"audio:" + text.encode("utf-8")


class FakeQQGateway:
    def __init__(self) -> None:
        self.text_sends: list[list[str]] = []
        self.voice_sends: list[str] = []

    def resolve_reply_mode(self, session_id: str) -> str:
        return "auto"

    def render_reply_messages(self, frame: dict) -> list[str]:
        segments = frame.get("speech_segments")
        if isinstance(segments, list) and segments:
            return [str(item) for item in segments if str(item).strip()]
        speech = str(frame.get("speech") or "").strip()
        return [speech] if speech else []

    def send_replies(self, context, messages: list[str]) -> dict:
        clean = [str(item).strip() for item in messages if str(item).strip()]
        self.text_sends.append(clean)
        return {"ok": bool(clean), "count": len(clean), "results": [{"ok": True, "message": item} for item in clean]}

    def send_reply(self, context, message: str) -> dict:
        clean = str(message or "").strip()
        self.text_sends.append([clean] if clean else [])
        return {"ok": bool(clean), "message": clean}

    def send_voice(self, context, *, audio_path: str, name: str = "") -> dict:
        self.voice_sends.append(audio_path)
        return {"ok": Path(audio_path).exists(), "file": audio_path}

    def send_generated_files(self, context, tool_events):
        return {"ok": True, "count": 0, "results": []}

    def send_market_charts(self, context, tool_events, *, authorization=""):
        return {"ok": True, "count": 0, "results": []}

    def send_finance_reports(self, context, tool_events, *, authorization=""):
        return {"ok": True, "count": 0, "results": []}

    def send_emotion_mface(self, context, frame, *, qq_delivery_config):
        return {"ok": True, "status": "sent"}

    def send_stickers(self, context, tool_events):
        return {"ok": True, "count": 0, "results": []}


class QQVoiceDeliveryTests(unittest.TestCase):
    def test_session_turn_coordinator_serializes_the_same_timeline(self) -> None:
        async def exercise() -> list[str]:
            coordinator = QQSessionTurnCoordinator()
            entered: list[str] = []
            first_entered = asyncio.Event()
            release_first = asyncio.Event()

            async def worker(name: str) -> None:
                async with coordinator.hold("qq_group_shared_1", "qq_group_1"):
                    entered.append(name)
                    if name == "first":
                        first_entered.set()
                        await release_first.wait()

            first = asyncio.create_task(worker("first"))
            await first_entered.wait()
            second = asyncio.create_task(worker("second"))
            await asyncio.sleep(0)
            self.assertEqual(entered, ["first"])
            release_first.set()
            await asyncio.gather(first, second)
            return entered

        self.assertEqual(asyncio.run(exercise()), ["first", "second"])

    def test_session_turn_coordinator_keeps_different_timelines_concurrent(self) -> None:
        async def exercise() -> int:
            coordinator = QQSessionTurnCoordinator()
            active = 0
            maximum = 0
            both_entered = asyncio.Event()
            release = asyncio.Event()

            async def worker(session_id: str) -> None:
                nonlocal active, maximum
                async with coordinator.hold("qq_group_shared", session_id):
                    active += 1
                    maximum = max(maximum, active)
                    if active == 2:
                        both_entered.set()
                    await release.wait()
                    active -= 1

            tasks = [
                asyncio.create_task(worker("qq_group_1")),
                asyncio.create_task(worker("qq_group_2")),
            ]
            await asyncio.wait_for(both_entered.wait(), timeout=1)
            release.set()
            await asyncio.gather(*tasks)
            return maximum

        self.assertEqual(asyncio.run(exercise()), 2)

    def test_streamed_segments_use_only_one_onebot_reply_frame(self) -> None:
        class FakeEngine:
            desktop_pet_character_resources = None

            def process_turn_stream(self, payload: dict):
                yield {"type": "speech_segment", "text": "在"}
                yield {"type": "speech_segment", "text": "怎么了"}
                yield {"type": "assistant_stage_decision", "has_tool_call": False}
                yield {
                    "type": "final_ui",
                    "payload": {
                        "reply_medium": "text",
                        "speech": "在\n怎么了",
                        "speech_segments": ["在", "怎么了"],
                        "tool_events": [],
                    },
                }

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"status": "ok", "retcode": 0, "data": {}}

        gateway = NapCatQQGateway()
        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "group",
                "self_id": 10001,
                "group_id": 30003,
                "user_id": 20002,
                "message_id": "stream-route-reply-budget-1",
                "message": [
                    {"type": "at", "data": {"qq": "10001"}},
                    {"type": "text", "data": {"text": " 在吗"}},
                ],
            }
        )
        with patch(
            "companion_v01.onebot_transport.requests.Session.request",
            side_effect=[FakeResponse(), FakeResponse()],
        ) as request:
            result = _process_qq_turn_streaming(
                engine=FakeEngine(),
                qq_gateway=gateway,
                context=context,
                turn_payload=context.to_turn_payload(),
                config_module=SimpleNamespace(
                    QQ_STREAM_REPLIES_ENABLED=True,
                    QQ_STREAM_MAX_SEGMENTS=8,
                    QQ_REPLY_MAX_SEGMENTS=8,
                    QQ_VOICE_MAX_SEGMENTS=3,
                    QQ_VOICE_MAX_TEXT_CHARS=280,
                ),
            )

        self.assertTrue(result["send_result"]["ok"])
        self.assertEqual(request.call_count, 2)
        messages = [call.kwargs["json"]["message"] for call in request.call_args_list]
        self.assertEqual([segment["type"] for segment in messages[0]], ["reply", "text"])
        self.assertEqual([segment["type"] for segment in messages[1]], ["text"])
        self.assertEqual(sum(segment["type"] == "reply" for message in messages for segment in message), 1)

    def test_auto_mode_sends_native_tool_preface_before_final_reply(self) -> None:
        class FakeEngine:
            def process_turn_stream(self, payload: dict):
                yield {"type": "speech_segment", "text": "我先看看这张图。"}
                yield {"type": "assistant_stage_decision", "has_tool_call": True, "tool_type": "generate_image"}
                yield {
                    "type": "final_ui",
                    "payload": {
                        "reply_medium": "text",
                        "speech": "图片好了。",
                        "speech_segments": ["图片好了。"],
                        "tool_events": [],
                    },
                }

        gateway = FakeQQGateway()
        _process_qq_turn_streaming(
            engine=FakeEngine(),
            qq_gateway=gateway,
            context=SimpleNamespace(
                session_id="qq_pri_1",
                profile_user_id="qq_1",
                character_pack_id="",
                reply_mode="auto",
            ),
            turn_payload={"message": "生图"},
            config_module=SimpleNamespace(
                QQ_STREAM_REPLIES_ENABLED=True,
                QQ_STREAM_MAX_SEGMENTS=8,
                QQ_REPLY_MAX_SEGMENTS=8,
                QQ_VOICE_MAX_SEGMENTS=3,
                QQ_VOICE_MAX_TEXT_CHARS=280,
            ),
        )

        self.assertEqual(gateway.text_sends, [["我先看看这张图。"], ["图片好了。"]])

    def test_streamed_generated_file_event_survives_empty_final_model_reply(self) -> None:
        class DeliveryGateway(FakeQQGateway):
            def __init__(self) -> None:
                super().__init__()
                self.delivery_events: list[dict] = []

            def add_delivery_note(self, session_id: str, note: str) -> None:
                return None

            def send_generated_files(self, context, tool_events):
                self.delivery_events = list(tool_events or [])
                return {
                    "ok": True,
                    "status": "sent",
                    "count": 1,
                    "results": [{"ok": True, "generated_id": "generated::image-1"}],
                }

        class FakeEngine:
            def process_turn_stream(self, payload: dict):
                yield {"type": "assistant_stage_decision", "has_tool_call": True, "tool_type": "generate_image"}
                yield {
                    "type": "generated_file_ready",
                    "send_to_user": True,
                    "generated_file": {
                        "generated_id": "generated::image-1",
                        "generated_handle": "gen_001",
                        "absolute_path": "C:/managed/gen_001.png",
                        "file_ext": "png",
                    },
                }
                yield {"type": "final_ui", "payload": {"speech": "", "tool_events": []}}

        gateway = DeliveryGateway()
        result = _process_qq_turn_streaming(
            engine=FakeEngine(),
            qq_gateway=gateway,
            context=SimpleNamespace(
                session_id="qq_pri_1",
                profile_user_id="qq_1",
                character_pack_id="",
                reply_mode="text",
            ),
            turn_payload={"message": "生成图片"},
            config_module=SimpleNamespace(
                QQ_STREAM_REPLIES_ENABLED=True,
                QQ_STREAM_MAX_SEGMENTS=8,
                QQ_REPLY_MAX_SEGMENTS=8,
                QQ_VOICE_MAX_SEGMENTS=3,
                QQ_VOICE_MAX_TEXT_CHARS=280,
            ),
        )

        self.assertEqual(
            len([item for item in gateway.delivery_events if item.get("type") == "generated_file_ready"]),
            1,
        )
        self.assertEqual(result["final_reply_fallback_result"]["status"], "generated_result_notice_sent")
        self.assertIn("结果已经生成", repr(gateway.text_sends))

    def test_non_file_tool_event_is_not_duplicated_when_stream_events_are_merged(self) -> None:
        class DeliveryGateway(FakeQQGateway):
            def __init__(self) -> None:
                super().__init__()
                self.tool_events: list[dict] = []

            def send_stickers(self, context, tool_events):
                self.tool_events = list(tool_events or [])
                return {"ok": True, "count": 0, "results": []}

        class FakeEngine:
            def process_turn_stream(self, payload: dict):
                yield {
                    "type": "final_ui",
                    "payload": {
                        "speech": "完成。",
                        "speech_segments": ["完成。"],
                        "tool_events": [{"type": "state_update", "value": "ready"}],
                    },
                }

        gateway = DeliveryGateway()
        _process_qq_turn_streaming(
            engine=FakeEngine(),
            qq_gateway=gateway,
            context=SimpleNamespace(
                session_id="qq_pri_1",
                profile_user_id="qq_1",
                character_pack_id="",
                reply_mode="text",
            ),
            turn_payload={"message": "更新状态"},
            config_module=SimpleNamespace(
                QQ_STREAM_REPLIES_ENABLED=True,
                QQ_STREAM_MAX_SEGMENTS=8,
                QQ_REPLY_MAX_SEGMENTS=8,
                QQ_VOICE_MAX_SEGMENTS=3,
                QQ_VOICE_MAX_TEXT_CHARS=280,
            ),
        )

        self.assertEqual(gateway.tool_events, [{"type": "state_update", "value": "ready"}])

    def test_native_tool_preface_is_sent_but_system_working_status_is_not(self) -> None:
        class FakeEngine:
            def process_turn_stream(self, payload: dict):
                yield {"type": "speech_segment", "text": "我先看看这张图。"}
                yield {
                    "type": "assistant_stage_decision",
                    "has_tool_call": True,
                    "tool_type": "image_understanding",
                }
                yield {"type": "assistant_working", "message": "系统正在处理图片。"}
                yield {
                    "type": "final_ui",
                    "payload": {
                        "reply_medium": "text",
                        "speech": "看好了，画面里是一只猫。",
                        "speech_segments": ["看好了，画面里是一只猫。"],
                        "tool_events": [],
                    },
                }

        gateway = FakeQQGateway()
        _process_qq_turn_streaming(
            engine=FakeEngine(),
            qq_gateway=gateway,
            context=SimpleNamespace(
                session_id="qq_pri_1",
                profile_user_id="qq_1",
                character_pack_id="",
                reply_mode="text",
            ),
            turn_payload={"message": "看看这张图"},
            config_module=SimpleNamespace(
                QQ_STREAM_REPLIES_ENABLED=True,
                QQ_STREAM_MAX_SEGMENTS=8,
                QQ_REPLY_MAX_SEGMENTS=8,
                QQ_VOICE_MAX_SEGMENTS=3,
                QQ_VOICE_MAX_TEXT_CHARS=280,
            ),
        )

        self.assertEqual(
            gateway.text_sends,
            [["我先看看这张图。"], ["看好了，画面里是一只猫。"]],
        )
        self.assertNotIn("系统正在处理图片。", repr(gateway.text_sends))

    def test_streamed_segments_are_not_resent_as_one_final_bubble(self) -> None:
        streamed = [
            "第一段已经发出",
            "第二段也已经发出",
            "第三段同样发出",
        ]
        final = "第一段已经发出。\n\n第二段也已经发出！\n\n第三段同样发出。"

        self.assertEqual(_filter_unsent_reply_messages([final], streamed), [])

    def test_streamed_prefix_keeps_only_new_final_tail(self) -> None:
        streamed = ["第一段已经发出", "第二段也已经发出"]
        final = "第一段已经发出。\n第二段也已经发出。\n这是最终阶段新增的结论。"

        self.assertEqual(_filter_unsent_reply_messages([final], streamed), ["这是最终阶段新增的结论"])

    def test_streamed_normal_reply_does_not_append_transient_persona_fallback(self) -> None:
        class FakeEngine:
            def process_turn_stream(self, payload: dict):
                yield {"type": "speech_segment", "text": "这是模型已经正常生成并发出的回复。"}
                yield {"type": "assistant_stage_decision", "has_tool_call": False}
                yield {
                    "type": "final_ui",
                    "payload": {
                        "speech": "我在认真听你说，要不要再多告诉我一点？",
                        "speech_segments": ["我在认真听你说，要不要再多告诉我一点？"],
                        "tool_events": [],
                        "_transient_final_failure": True,
                    },
                }

        gateway = FakeQQGateway()
        result = _process_qq_turn_streaming(
            engine=FakeEngine(),
            qq_gateway=gateway,
            context=SimpleNamespace(
                session_id="qq_pri_1",
                profile_user_id="qq_1",
                character_pack_id="",
                reply_mode="text",
            ),
            turn_payload={"message": "继续说"},
            config_module=SimpleNamespace(
                QQ_STREAM_REPLIES_ENABLED=True,
                QQ_STREAM_MAX_SEGMENTS=8,
                QQ_REPLY_MAX_SEGMENTS=8,
                QQ_VOICE_MAX_SEGMENTS=3,
                QQ_VOICE_MAX_TEXT_CHARS=280,
            ),
        )

        self.assertEqual(gateway.text_sends, [["这是模型已经正常生成并发出的回复。"]])
        self.assertEqual(result["reply_messages"], ["这是模型已经正常生成并发出的回复。"])
        self.assertEqual(result["send_result"]["deferred_count"], 0)
        self.assertNotIn("我在认真听你说", repr(gateway.text_sends))

    def test_transient_failure_without_text_sends_visible_failure_notice(self) -> None:
        class FakeEngine:
            def process_turn_stream(self, payload: dict):
                yield {
                    "type": "final_ui",
                    "payload": {
                        "emotion": "concerned",
                        "speech": "我在认真听你说，要不要再多告诉我一点？",
                        "speech_segments": ["我在认真听你说，要不要再多告诉我一点？"],
                        "tool_events": [],
                        "_transient_final_failure": True,
                    },
                }

        gateway = FakeQQGateway()
        result = _process_qq_turn_streaming(
            engine=FakeEngine(),
            qq_gateway=gateway,
            context=SimpleNamespace(
                session_id="qq_pri_failure",
                profile_user_id="qq_1",
                character_pack_id="",
                reply_mode="text",
            ),
            turn_payload={"message": "继续处理"},
            config_module=SimpleNamespace(
                QQ_STREAM_REPLIES_ENABLED=True,
                QQ_STREAM_MAX_SEGMENTS=8,
                QQ_REPLY_MAX_SEGMENTS=8,
                QQ_VOICE_MAX_SEGMENTS=3,
                QQ_VOICE_MAX_TEXT_CHARS=280,
            ),
        )

        self.assertEqual(len(gateway.text_sends), 1)
        self.assertIn("没有形成可交付的文字结果", gateway.text_sends[0][0])
        self.assertTrue(result["final_failure_notice_result"]["ok"])

    def test_group_voice_uses_owner_tts_profile_scope(self) -> None:
        captured_payload: dict = {}
        captured_base_dir: list[Path] = []

        def fake_resolver(**kwargs):
            captured_payload.update(dict(kwargs["payload"]))
            captured_base_dir.append(Path(kwargs["base_dir"]))
            return {"activeProviderId": "provider.tts.edge", "status": "ready"}

        with tempfile.TemporaryDirectory() as temp_dir:
            capability_dir = Path(temp_dir) / "users_data"
            with patch("companion_v01.routes.qq._resolve_tts_runtime_provider", side_effect=fake_resolver):
                result = _synthesize_qq_voice_file(
                    engine=SimpleNamespace(capability_config_base_dir=capability_dir),
                    config_module=SimpleNamespace(DATA_DIR=temp_dir, WEB_OWNER_PROFILE_USER_ID="master"),
                    tts_client=FakeTTSClient(),
                    text="群聊语音测试",
                    context=SimpleNamespace(
                        profile_user_id="qq_group_shared_123456",
                        character_pack_id="reimu",
                    ),
                )

        self.assertTrue(result["ok"])
        self.assertEqual(captured_payload["real_user_id"], "master")
        self.assertEqual(captured_payload["profile_user_id"], "master")
        self.assertEqual(captured_payload["character_pack_id"], "reimu")
        self.assertEqual(result["tts_profile_user_id"], "master")
        self.assertEqual(captured_base_dir, [capability_dir])

    def test_group_voice_reads_the_current_bot_capability_directory(self) -> None:
        class FakeGptSovitsClient:
            async def synthesize(self, text: str, **kwargs):
                self.text = text
                self.kwargs = kwargs
                return SimpleNamespace(audio=b"gpt-sovits-audio", media_type="audio/wav")

        with tempfile.TemporaryDirectory() as temp_dir:
            bot_data_dir = Path(temp_dir) / "bot"
            capability_dir = bot_data_dir / "users_data"
            self.assertTrue(
                save_provider_config(
                    base_dir=capability_dir,
                    profile_user_id="master",
                    provider_id="provider.tts.gpt_sovits.local",
                    payload={"enabled": True, "endpoint": "http://127.0.0.1:9880"},
                )["ok"]
            )
            self.assertTrue(
                save_voice_profile_config(
                    base_dir=capability_dir,
                    profile_user_id="master",
                    voice_profile_id="dania",
                    payload={
                        "enabled": True,
                        "providerId": "provider.tts.gpt_sovits.local",
                        "refAudioPath": str(Path(temp_dir) / "reference.wav"),
                        "promptText": "参考文本",
                        "promptLang": "zh",
                    },
                )["ok"]
            )
            client = FakeGptSovitsClient()
            engine = SimpleNamespace(
                capability_config_base_dir=capability_dir,
                desktop_pet_character_resources=SimpleNamespace(
                    build_character_voice_preference=lambda _pack_id: {
                        "provider": "gpt_sovits",
                        "profileId": "dania",
                    }
                ),
            )
            result = _synthesize_qq_voice_file(
                engine=engine,
                config_module=SimpleNamespace(DATA_DIR=bot_data_dir, WEB_OWNER_PROFILE_USER_ID="master"),
                tts_client=FakeTTSClient(),
                text="真实 QQ GPT-SoVITS 测试",
                context=SimpleNamespace(
                    profile_user_id="qq_group_shared_123456",
                    character_pack_id="reimu",
                ),
                gpt_sovits_client_factory=lambda _endpoint: client,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["provider"], "provider.tts.gpt_sovits.local")
        self.assertEqual(result["media_type"], "audio/wav")
        self.assertEqual(client.kwargs["voice_profile_id"], "dania")
        self.assertEqual(client.kwargs["profile"]["promptText"], "参考文本")

    def test_group_voice_does_not_silently_replace_gpt_sovits_with_edge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch(
                "companion_v01.routes.qq._resolve_tts_runtime_provider",
                return_value={
                    "status": "degraded",
                    "reason": "provider_endpoint_missing",
                    "requestedProviderId": "provider.tts.gpt_sovits.local",
                    "activeProviderId": "provider.tts.edge",
                    "fallbackProviderId": "provider.tts.edge",
                },
            ):
                result = _synthesize_qq_voice_file(
                    engine=SimpleNamespace(capability_config_base_dir=Path(temp_dir) / "users_data"),
                    config_module=SimpleNamespace(DATA_DIR=temp_dir, WEB_OWNER_PROFILE_USER_ID="master"),
                    tts_client=FakeTTSClient(),
                    text="不要换成微软声线",
                    context=SimpleNamespace(
                        profile_user_id="qq_group_shared_123456",
                        character_pack_id="reimu",
                    ),
                )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "provider_endpoint_missing")
        self.assertEqual(result["requested_provider"], "provider.tts.gpt_sovits.local")
        self.assertEqual(result["provider"], "provider.tts.edge")

    def test_auto_voice_hint_sends_record_without_streaming_text(self) -> None:
        class FakeEngine:
            def process_turn_stream(self, payload: dict):
                yield {"type": "delivery_hint", "medium": "voice"}
                yield {"type": "speech_segment", "text": "第一句。"}
                yield {"type": "assistant_stage_decision"}
                yield {
                    "type": "final_ui",
                    "payload": {
                        "reply_medium": "voice",
                        "speech": "",
                        "speech_segments": ["第一句。"],
                        "tool_events": [],
                    },
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            gateway = FakeQQGateway()
            result = _process_qq_turn_streaming(
                engine=FakeEngine(),
                qq_gateway=gateway,
                context=SimpleNamespace(
                    session_id="qq_pri_1",
                    profile_user_id="qq_1",
                    character_pack_id="",
                    reply_mode="auto",
                ),
                turn_payload={"message": "hi"},
                config_module=SimpleNamespace(
                    DATA_DIR=temp_dir,
                    QQ_STREAM_REPLIES_ENABLED=True,
                    QQ_STREAM_MAX_SEGMENTS=8,
                    QQ_REPLY_MAX_SEGMENTS=8,
                    QQ_VOICE_MAX_SEGMENTS=3,
                    QQ_VOICE_MAX_TEXT_CHARS=280,
                ),
                tts_client=FakeTTSClient(),
            )

        self.assertEqual(gateway.text_sends, [])
        self.assertEqual(len(gateway.voice_sends), 1)
        self.assertTrue(result["send_result"]["ok"])
        self.assertEqual(result["send_result"]["delivery"]["medium"], "voice")
        self.assertTrue(result["send_result"]["delivery"]["voice_enabled"])

    def test_auto_voice_hint_downgrades_long_text_to_text(self) -> None:
        long_text = "这是一段偏长的回复。" * 20

        class FakeEngine:
            def process_turn_stream(self, payload: dict):
                yield {"type": "delivery_hint", "medium": "voice"}
                yield {
                    "type": "final_ui",
                    "payload": {
                        "reply_medium": "voice",
                        "speech": long_text,
                        "speech_segments": [long_text],
                        "tool_events": [],
                    },
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            gateway = FakeQQGateway()
            result = _process_qq_turn_streaming(
                engine=FakeEngine(),
                qq_gateway=gateway,
                context=SimpleNamespace(
                    session_id="qq_pri_1",
                    profile_user_id="qq_1",
                    character_pack_id="",
                    reply_mode="auto",
                ),
                turn_payload={"message": "hi"},
                config_module=SimpleNamespace(
                    DATA_DIR=temp_dir,
                    QQ_STREAM_REPLIES_ENABLED=True,
                    QQ_STREAM_MAX_SEGMENTS=8,
                    QQ_REPLY_MAX_SEGMENTS=8,
                    QQ_VOICE_MAX_SEGMENTS=3,
                    QQ_VOICE_MAX_TEXT_CHARS=40,
                ),
                tts_client=FakeTTSClient(),
            )

        self.assertEqual(gateway.text_sends, [[long_text]])
        self.assertEqual(gateway.voice_sends, [])
        self.assertTrue(result["send_result"]["ok"])
        self.assertEqual(result["send_result"]["delivery"]["voice_reason"], "auto_voice_text_too_long")
        self.assertFalse(result["send_result"]["delivery"]["voice_enabled"])

    def test_bot_settings_override_qq_voice_limit_without_mutating_config(self) -> None:
        long_text = "这是一段偏长的回复。" * 10

        class FakeEngine:
            def process_turn_stream(self, payload: dict):
                yield {
                    "type": "final_ui",
                    "payload": {
                        "reply_medium": "voice",
                        "speech": long_text,
                        "speech_segments": [long_text],
                        "tool_events": [],
                    },
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            gateway = FakeQQGateway()
            result = _process_qq_turn_streaming(
                engine=FakeEngine(),
                qq_gateway=gateway,
                context=SimpleNamespace(
                    session_id="qq_pri_1",
                    profile_user_id="qq_1",
                    character_pack_id="",
                    reply_mode="auto",
                ),
                turn_payload={"message": "hi"},
                config_module=SimpleNamespace(
                    DATA_DIR=temp_dir,
                    QQ_STREAM_REPLIES_ENABLED=True,
                    QQ_STREAM_MAX_SEGMENTS=8,
                    QQ_REPLY_MAX_SEGMENTS=8,
                    QQ_VOICE_MAX_SEGMENTS=3,
                    QQ_VOICE_MAX_TEXT_CHARS=280,
                ),
                settings=BotSettingsView(qq_voice_max_text_chars=40),
                tts_client=FakeTTSClient(),
            )

        self.assertEqual(gateway.text_sends, [[long_text]])
        self.assertEqual(gateway.voice_sends, [])
        self.assertEqual(result["send_result"]["delivery"]["voice_reason"], "auto_voice_text_too_long")

    def test_failed_file_delivery_sends_truthful_feedback(self) -> None:
        class FailedGateway(FakeQQGateway):
            def __init__(self) -> None:
                super().__init__()
                self.delivery_notes: list[str] = []

            def send_generated_files(self, context, tool_events):
                return {
                    "ok": False,
                    "status": "failed",
                    "count": 1,
                    "results": [{"ok": False, "reason": "onebot_upload_failed"}],
                }

            def add_delivery_note(self, session_id: str, note: str) -> None:
                self.delivery_notes.append(note)

        class FakeEngine:
            def process_turn_stream(self, payload: dict):
                yield {
                    "type": "final_ui",
                    "payload": {
                        "reply_medium": "text",
                        "speech": "我先准备一下。",
                        "speech_segments": ["我先准备一下。"],
                        "tool_events": [{"type": "file_ready", "send_to_user": True}],
                    },
                }

        gateway = FailedGateway()
        result = _process_qq_turn_streaming(
            engine=FakeEngine(),
            qq_gateway=gateway,
            context=SimpleNamespace(
                session_id="qq_pri_1",
                profile_user_id="qq_1",
                character_pack_id="",
                reply_mode="text",
            ),
            turn_payload={"message": "在吗"},
            config_module=SimpleNamespace(
                QQ_STREAM_REPLIES_ENABLED=False,
                QQ_STREAM_MAX_SEGMENTS=0,
                QQ_REPLY_MAX_SEGMENTS=8,
                QQ_VOICE_MAX_SEGMENTS=3,
                QQ_VOICE_MAX_TEXT_CHARS=280,
            ),
        )

        self.assertEqual(result["file_delivery_feedback_result"]["status"], "failure_notice_sent")
        self.assertTrue(any("文件这次发送失败" in message for batch in gateway.text_sends for message in batch))
        self.assertNotIn("我先准备一下", repr(gateway.text_sends))
        self.assertTrue(any("文件发送失败" in note for note in gateway.delivery_notes))


if __name__ == "__main__":
    unittest.main()
