from __future__ import annotations

import json
from types import SimpleNamespace
import unittest

from companion_v01.client_protocol import ClientMode
from companion_v01.llm_runtime import ChatJSONResult
from companion_v01.plugin_event_delivery import apply_plugin_notification_output_policy
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
                apply_plugin_notification_output_policy(normalized, _payload)
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
                turn_kind="plugin_event",
                plugin_external_event={"event_type": "finance.news", "source": "akane.finance"},
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
                self.assertEqual(result["send_result"]["status"], "suppressed")

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

    def test_legacy_non_delivery_labels_never_send_the_decision_or_envelope(self):
        for text in (
            "【不推送】没有新增信息。",
            "【暂不推送】证据不足。",
            "[暂时不推送]研究过程",
            "【暂不作为确定事实推送】数据尚未核实。",
            "[不作为已核实消息推送]只作为内部核验线索。",
            "【财经快讯｜10:18】\n【暂不推送】证据不足。",
        ):
            with self.subTest(text=text):
                frame, gateway, result = self._deliver(text)
                self.assertEqual(frame["_notification_suppressed"], "legacy_notification_silence")
                self.assertFalse(frame.get("_deliberate_silence"))
                self.assertEqual(gateway.sent, [])
                self.assertEqual(gateway.emotions, 0)
                self.assertEqual(result["reply_messages"], [])
                self.assertEqual(result["send_result"]["status"], "suppressed")
                self.assertEqual(result["send_result"]["count"], 0)
                self.assertEqual(
                    result["text_suppression"], {"status": "suppressed", "reason": "legacy_notification_silence"}
                )

    def test_ordinary_user_discussion_and_news_quotes_are_not_suppressed(self):
        for text, single_message in (
            ("【暂不推送】是你问到的标签。", False),
            ("公告中的“【暂不推送】”指该平台的设置。", True),
            ("【不良资产风险通知】公布了新的资产处置数据。", True),
        ):
            with self.subTest(text=text):
                frame, gateway, result = self._deliver(text, single_message=single_message)
                self.assertNotIn("_notification_suppressed", frame)
                self.assertTrue(gateway.sent)
                self.assertEqual(result["text_suppression"]["status"], "not_suppressed")

    def test_model_cannot_invent_host_suppression_marker(self):
        class LLM:
            @staticmethod
            def snapshot_metrics():
                return {}

            @staticmethod
            def call_chat_json_result(**kwargs):
                payload = {"speech": "正常回答", "_notification_suppressed": "legacy_notification_silence"}
                return ChatJSONResult(parsed=payload, raw_text=json.dumps(payload))

        recovery = final_repair.FinalRecoveryTests()
        engine = recovery._real_normalize_engine(LLM(), client_mode=ClientMode.QQ_TEXT)
        frame = recovery._run_nonstream(engine)
        self.assertNotIn("_notification_suppressed", frame)
        self.assertEqual(frame["speech"], "正常回答")

    def test_compatibility_guard_is_not_a_general_text_filter(self):
        good = {
            "client_mode": "qq_text",
            "turn_kind": "plugin_event",
            "plugin_text_delivery": "single_message",
            "plugin_external_event": {"event_type": "finance.news"},
        }
        for override in (
            {"client_mode": "desktop_pet"},
            {"turn_kind": "user"},
            {"plugin_text_delivery": "default"},
            {"plugin_external_event": None},
        ):
            frame = {"speech": "【暂不推送】正常讨论标题"}
            apply_plugin_notification_output_policy(frame, {**good, **override})
            self.assertNotIn("_notification_suppressed", frame)
            self.assertEqual(frame["speech"], "【暂不推送】正常讨论标题")
        failed = {"speech": "【暂不推送】", "_transient_final_failure": True}
        apply_plugin_notification_output_policy(failed, good)
        self.assertNotIn("_notification_suppressed", failed)


if __name__ == "__main__":
    unittest.main()
