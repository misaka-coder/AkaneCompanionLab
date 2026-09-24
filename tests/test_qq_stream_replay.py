from types import SimpleNamespace
import json
import unittest

from memcore import StreamingSpeechParser
from companion_v01.final_output_engine import normalize_speech_payload
from companion_v01.routes.qq import _filter_unsent_reply_messages, _process_qq_turn_streaming, _coalesce_deferred_stream_messages
from tests.test_qq_voice_delivery import FakeQQGateway


class QQStreamReplayTests(unittest.TestCase):
    def test_plugin_progress_reaches_qq_before_model_turn_finishes(self):
        gateway = FakeQQGateway()
        case = self
        class Engine:
            def process_turn_stream(self, payload):
                yield {"type": "speech_segment", "text": "我正在核对传送门框架。"}
                yield {"type": "assistant_stage_decision", "has_tool_call": True}
                case.assertEqual(gateway.text_sends, [["我正在核对传送门框架。"]])
                yield {"type": "speech_segment", "text": "框架方向需要调整，我先修正。"}
                yield {"type": "assistant_stage_decision", "has_tool_call": True}
                case.assertEqual(len(gateway.text_sends), 2)
                yield {"type": "final_ui", "payload": {"speech": "框架方向需要调整，我先修正。", "tool_events": []}}
        result = _process_qq_turn_streaming(engine=Engine(), qq_gateway=gateway,
            context=SimpleNamespace(session_id="group", profile_user_id="owner", character_pack_id="", reply_mode="auto"),
            turn_payload={"message": "continue", "turn_kind": "plugin_event", "client_mode": "qq_text",
                          "plugin_external_event": {"source": "akane.minecraft", "event_type": "plugin.turn.requested"}},
            config_module=SimpleNamespace(QQ_STREAM_REPLIES_ENABLED=True))
        self.assertTrue(result["send_result"]["ok"])
        self.assertEqual(len(gateway.text_sends), 2)

    def test_shared_parser_keeps_nested_pairs_whole_through_qq_delivery(self):
        gateway = FakeQQGateway()
        speech = '她说：“看《书里的[\'Long English, with punctuation! And another sentence?\']》吧。”结束。'
        speech = speech.replace('Long English,', 'Long English, ' * 150)
        normalized, segments = normalize_speech_payload(speech=speech)
        self.assertEqual(segments, [speech])

        class Engine:
            def process_turn_stream(self, payload):
                parser = StreamingSpeechParser(mode="memcore_json", max_segment_chars=24)
                wire = json.dumps({"speech": speech}, ensure_ascii=False)
                for offset in range(0, len(wire), 37):
                    yield from parser.feed(wire[offset:offset + 37])
                yield from parser.finish()
                yield {"type": "assistant_stage_decision", "has_tool_call": False}
                yield {"type": "final_ui", "payload": {"speech": normalized, "speech_segments": segments}}

        result = _process_qq_turn_streaming(
            engine=Engine(), qq_gateway=gateway,
            context=SimpleNamespace(session_id="private", profile_user_id="owner", character_pack_id="", reply_mode="text"),
            turn_payload={"message": "测试完整分段"}, config_module=SimpleNamespace(QQ_STREAM_REPLIES_ENABLED=True),
        )
        self.assertTrue(result["send_result"]["ok"])
        self.assertEqual(gateway.text_sends, [[speech]])
        self.assertEqual(_coalesce_deferred_stream_messages([speech]), [speech])

    def test_progress_arrives_before_tool_and_final_replay_is_not_sent_again(self):
        gateway = FakeQQGateway()
        case = self

        class Engine:
            def process_turn_stream(self, payload):
                yield {"type": "speech_segment", "text": "我先写笔记。"}
                case.assertEqual(gateway.text_sends, [["我先写笔记。"]])
                yield {"type": "assistant_stage_decision", "has_tool_call": True}
                # A real tool would execute here, after its progress is visible.
                yield {"type": "speech_segment", "text": "笔记已保存。"}
                case.assertEqual(gateway.text_sends[-1], ["笔记已保存。"])
                yield {"type": "speech_segment", "text": "我来导出。"}
                yield {"type": "assistant_stage_decision", "has_tool_call": True}
                yield {"type": "speech_segment", "text": "笔记已保存。我来导出。"}
                case.assertEqual(len(gateway.text_sends), 3)
                yield {"type": "speech_segment", "text": "文件已交给发送器。"}
                case.assertEqual(gateway.text_sends[-1], ["文件已交给发送器。"])
                yield {"type": "assistant_stage_decision", "has_tool_call": False}
                yield {"type": "final_ui", "payload": {"speech_segments": [
                    "笔记已保存。", "我来导出。文件已交给发送器。"], "tool_events": []}}

        result = _process_qq_turn_streaming(engine=Engine(), qq_gateway=gateway,
            context=SimpleNamespace(session_id="private", profile_user_id="owner", character_pack_id="", reply_mode="text"),
            turn_payload={"message": "每步给我反馈"}, config_module=SimpleNamespace(QQ_STREAM_REPLIES_ENABLED=True))
        self.assertTrue(result["send_result"]["ok"])
        self.assertEqual(gateway.text_sends, [["我先写笔记。"], ["笔记已保存。"], ["我来导出。"], ["文件已交给发送器。"]])

    def test_final_reconciles_different_segment_boundaries(self):
        self.assertEqual(_filter_unsent_reply_messages(
            ["第一步已完成。第二步已完成。", "文件已保存。发送还没成功。"],
            ["第一步已完成", "第二步已完成", "文件已保存"]), ["发送还没成功"])

    def test_similar_new_result_is_preserved(self):
        self.assertEqual(_filter_unsent_reply_messages(
            ["文件发送失败，请稍后再试。"], ["文件发送成功，请查收。"]), ["文件发送失败，请稍后再试。"])

    def test_repeated_sentences_keep_occurrence_count(self):
        self.assertEqual(_filter_unsent_reply_messages(["好。", "好。"], ["好。"]), ["好。"])


if __name__ == "__main__":
    unittest.main()
