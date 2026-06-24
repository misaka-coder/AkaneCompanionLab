from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from companion_v01.routes.qq import _process_qq_turn_streaming, _synthesize_qq_voice_file


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

    def send_voice(self, context, *, audio_path: str, name: str = "") -> dict:
        self.voice_sends.append(audio_path)
        return {"ok": Path(audio_path).exists(), "file": audio_path}

    def send_generated_files(self, context, tool_events):
        return {"ok": True, "count": 0, "results": []}

    def send_stickers(self, context, tool_events):
        return {"ok": True, "count": 0, "results": []}


class QQVoiceDeliveryTests(unittest.TestCase):
    def test_group_voice_uses_owner_tts_profile_scope(self) -> None:
        captured_payload: dict = {}

        def fake_resolver(**kwargs):
            captured_payload.update(dict(kwargs["payload"]))
            return {"activeProviderId": "provider.tts.edge", "status": "ready"}

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("companion_v01.routes.qq._resolve_tts_runtime_provider", side_effect=fake_resolver):
                result = _synthesize_qq_voice_file(
                    engine=object(),
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


if __name__ == "__main__":
    unittest.main()
