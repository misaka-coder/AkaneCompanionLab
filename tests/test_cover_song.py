"""Neutral provider and existing QQ delivery regression checks."""

import json
import unittest
from unittest.mock import patch

from plugins.akane_cover_song.src.akane_cover_song import RvcWebUiProvider
from companion_v01.qq_gateway import NapCatQQGateway, QQMessageContext


class CoverProtocolAndDeliveryTests(unittest.TestCase):
    def test_rvc_provider_extracts_safe_stage_timings(self) -> None:
        provider = RvcWebUiProvider(base_url="http://127.0.0.1:7899")

        timings = provider._parse_rvc_timings(
            "Success.\nIndex:\nC:/private/model.index.\nTime:\nnpy: 1.25s, f0: 2.50s, infer: 3.75s."
        )

        self.assertEqual(
            timings,
            {
                "feature_extraction": 1.25,
                "pitch_extraction": 2.5,
                "voice_synthesis": 3.75,
            },
        )
        self.assertNotIn("private", json.dumps(timings))

    def test_rvc_provider_discovers_model_choices_from_named_dependency(self) -> None:
        provider = RvcWebUiProvider(base_url="http://127.0.0.1:7899")
        config = {
            "components": [
                {"id": 6, "type": "dropdown", "props": {"label": "推理音色", "choices": ["A.pth", ["B.pth", "B.pth"]]}},
            ],
            "dependencies": [{"api_name": "infer_change_voice", "inputs": [6], "outputs": []}],
        }
        with patch.object(provider, "_load_config", return_value=config):
            self.assertEqual(provider.list_voice_models(), ["A.pth", "B.pth"])
            self.assertEqual(provider.resolve_voice_model("b", default_model=""), "B.pth")

    def test_rvc_capability_probe_uses_short_forced_timeout(self) -> None:
        provider = RvcWebUiProvider(base_url="http://127.0.0.1:7899")
        config = {
            "components": [
                {"id": 6, "type": "dropdown", "props": {"label": "推理音色", "choices": ["A.pth"]}},
            ],
            "dependencies": [{"api_name": "infer_change_voice", "inputs": [6], "outputs": []}],
        }

        with (
            patch.object(provider, "_request_json", return_value=config) as request,
            patch("time.monotonic", return_value=100.0),
        ):
            status = provider.capability_status()

        self.assertTrue(status["enabled"])
        self.assertEqual(request.call_args.args, ("GET", "/config"))
        self.assertEqual(request.call_args.kwargs["deadline"], 102.0)

    def test_qq_audio_delivery_mode_can_send_cover_as_voice(self) -> None:
        gateway = NapCatQQGateway()
        context = QQMessageContext(
            should_respond=True,
            reason="test",
            target_id=123,
            user_id=123,
            session_id="qq_pri_123",
            profile_user_id="qq_123",
            clean_message="给我翻唱这首歌",
        )
        event = {
            "type": "generated_file_ready",
            "send_to_user": True,
            "delivery_mode": "voice",
            "generated_file": {
                "generated_id": "generated::1",
                "absolute_path": "C:/tmp/cover.mp3",
                "output_title": "测试翻唱",
                "file_ext": "mp3",
                "mime_type": "audio/mpeg",
            },
        }
        with (
            patch.object(gateway, "send_voice", return_value={"ok": True}) as send_voice,
            patch.object(gateway, "send_file", return_value={"ok": True}) as send_file,
        ):
            result = gateway.send_generated_files(context, [event])

        self.assertTrue(result["ok"])
        send_voice.assert_called_once()
        send_file.assert_not_called()

    def test_cover_song_both_delivery_result_hides_local_path(self) -> None:
        gateway = NapCatQQGateway()
        context = QQMessageContext(
            should_respond=True,
            reason="test",
            target_id=123,
            user_id=123,
            session_id="qq_pri_123",
            profile_user_id="qq_123",
            clean_message="语音和文件都发我",
        )
        private_path = r"C:\private\cover.mp3"
        event = {
            "type": "generated_file_ready",
            "send_to_user": True,
            "delivery_mode": "both",
            "generated_file": {
                "generated_id": "generated::1",
                "absolute_path": private_path,
                "output_title": "测试翻唱",
                "file_ext": "mp3",
                "mime_type": "audio/mpeg",
            },
        }
        with (
            patch.object(gateway, "send_voice", return_value={"ok": True}) as send_voice,
            patch.object(gateway, "send_file", return_value={"ok": True}) as send_file,
        ):
            result = gateway.send_generated_files(context, [event])

        self.assertTrue(result["ok"])
        self.assertEqual(result["results"][0]["delivery_mode"], "both")
        self.assertNotIn(private_path, repr(result))
        send_voice.assert_called_once()
        send_file.assert_called_once()
