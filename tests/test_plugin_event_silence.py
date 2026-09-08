from __future__ import annotations

import json
from types import SimpleNamespace
import unittest

from companion_v01.client_protocol import ClientMode
from companion_v01.llm_runtime import ChatJSONResult
from companion_v01.routes.qq import _process_qq_turn_streaming
from tests import test_final_json_repair as final_repair


class PluginEventSilenceTests(unittest.TestCase):
    def _deliver(self, speech: str, *, single_message: bool = True):
        class LLM:
            @staticmethod
            def snapshot_metrics():
                return {}

            @staticmethod
            def call_chat_json_result(**_kwargs):
                payload = {"speech": speech, "tool_call": None}
                return ChatJSONResult(parsed=payload, raw_text=json.dumps(payload))

        recovery = final_repair.FinalRecoveryTests()
        real_engine = recovery._real_normalize_engine(LLM(), client_mode=ClientMode.QQ_TEXT)
        normalized = recovery._run_nonstream(real_engine)
        normalized["tool_events"] = []

        class Engine:
            desktop_pet_character_resources = None

            @staticmethod
            def process_turn_stream(_payload):
                # The delivery policy must also suppress intermediate text.
                if single_message:
                    yield {"type": "speech_segment", "text": "研究过程，不应单独发送"}
                yield {"type": "final_ui", "payload": normalized}

            @staticmethod
            def process_turn(_payload):
                raise AssertionError("valid final output must not retry")

        class Gateway:
            def __init__(self):
                self.sent = []
                self.emotions = 0

            @staticmethod
            def resolve_reply_mode(_session_id):
                return "text"

            @staticmethod
            def render_reply_messages(frame):
                return [frame["speech"]] if frame.get("speech") else []

            def send_replies(self, _context, messages):
                self.sent.extend(messages)
                return {"ok": True, "count": len(messages), "results": []}

            def send_reply(self, _context, text, **_kwargs):
                self.sent.append(text)
                return {"ok": True, "status": "sent"}

            @staticmethod
            def no_artifact(_context, events, **_kwargs):
                assert not events
                return {"ok": True, "count": 0, "results": []}

            send_generated_files = no_artifact
            send_music_cards = no_artifact
            send_market_charts = no_artifact
            send_finance_reports = no_artifact
            send_stickers = no_artifact

            def send_emotion_mface(self, *_args, **_kwargs):
                self.emotions += 1
                return {"ok": True, "status": "skipped"}

        gateway = Gateway()
        payload = {"user_id": "test", "message": "event.finance.news"}
        if single_message:
            payload.update(
                plugin_text_delivery="single_message",
                plugin_text_prefix="【财经快讯｜10:18】",
                plugin_text_suffix="原文链接：https://finance.eastmoney.com/a/test.html",
            )
        result = _process_qq_turn_streaming(
            engine=Engine(),
            qq_gateway=gateway,
            context=SimpleNamespace(session_id="test", reply_mode="text"),
            turn_payload=payload,
            config_module=SimpleNamespace(QQ_STREAM_REPLIES_ENABLED=True),
        )
        return normalized, gateway, result

    def test_explicit_empty_speech_suppresses_entire_plugin_envelope(self):
        for value in ("", " \n\t"):
            with self.subTest(value=value):
                frame, gateway, result = self._deliver(value)
                self.assertTrue(frame["_deliberate_silence"])
                self.assertEqual(gateway.sent, [])
                self.assertEqual(gateway.emotions, 0)
                self.assertEqual(result["reply_messages"], [])
                self.assertEqual(result["final_failure_notice_result"]["status"], "skipped")

    def test_analysis_keeps_single_trusted_envelope(self):
        frame, gateway, result = self._deliver("有新的经营数据，仍需核验。")
        self.assertFalse(frame.get("_deliberate_silence"))
        self.assertEqual(len(gateway.sent), 1)
        self.assertEqual(
            gateway.sent[0],
            "【财经快讯｜10:18】\n有新的经营数据，仍需核验。\n原文链接：https://finance.eastmoney.com/a/test.html",
        )
        self.assertTrue(result["send_result"]["ok"])

    def test_ordinary_explanation_of_old_label_is_not_a_control_protocol(self):
        text = "你看到的【不推送】是旧标签，现在已经改正。"
        frame, gateway, _result = self._deliver(text, single_message=False)
        self.assertFalse(frame.get("_deliberate_silence"))
        self.assertEqual(gateway.sent, [text])


if __name__ == "__main__":
    unittest.main()
