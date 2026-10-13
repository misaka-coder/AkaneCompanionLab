import json
import unittest
from types import SimpleNamespace

from memcore import StreamingSpeechParser

from companion_v01.channels.qq.delivery.stream import _coalesce_deferred_stream_messages, _filter_unsent_reply_messages
from companion_v01.channels.qq.turns.streaming import _process_qq_turn_streaming
from companion_v01.final_output_engine import normalize_speech_payload
from tests.test_qq_voice_delivery import FakeQQGateway


class QQStreamReplayTests(unittest.TestCase):
    def test_tool_sent_body_is_reconciled_with_stream_and_final_once(self):
        for streaming in (True, False):
            for extra in ("", "这是新的补充。"):
                with self.subTest(streaming=streaming, extra=extra):
                    gateway = FakeQQGateway()
                    receipt = {"type": "qq_visible_action_receipt", "action": "send_group_msg",
                               "ok": True, "status": "success", "message_id": 77,
                               "current_conversation_text": "（站起身）\n第一句。第二句！"}
                    class Engine:
                        def process_turn_stream(self, payload):
                            yield {"type": "assistant_stage_decision", "has_tool_call": True}
                            yield receipt
                            for text in ["（站起身）\n第一句。", "第二句！", extra]:
                                if text:
                                    yield {"type": "speech_segment", "text": text}
                            yield {"type": "assistant_stage_decision", "has_tool_call": False}
                            yield {"type": "final_ui", "payload": {
                                "speech_segments": ["（站起身）\n第一句。第二句！" + extra],
                                "tool_events": [receipt]}}
                    _process_qq_turn_streaming(engine=Engine(), qq_gateway=gateway,
                        context=SimpleNamespace(session_id="group", profile_user_id="owner",
                                                character_pack_id="", reply_mode="text"),
                        turn_payload={"message": "测试"},
                        config_module=SimpleNamespace(QQ_STREAM_REPLIES_ENABLED=streaming))
                    expected = extra if streaming else extra.rstrip("。")
                    self.assertEqual(gateway.text_sends, [[expected]] if extra else [])

    def test_final_only_tool_receipt_prevents_replayed_delivery(self):
        gateway = FakeQQGateway()
        class Engine:
            def process_turn_stream(self, payload):
                yield {"type": "final_ui", "payload": {"speech": "已发出的正文。", "tool_events": [{
                    "type": "qq_visible_action_receipt", "action": "send_group_msg", "ok": True,
                    "status": "success", "message_id": 77, "current_conversation_text": "已发出的正文。"}]}}
        _process_qq_turn_streaming(engine=Engine(), qq_gateway=gateway,
            context=SimpleNamespace(session_id="group", profile_user_id="owner", character_pack_id="", reply_mode="text"),
            turn_payload={"message": "测试"}, config_module=SimpleNamespace(QQ_STREAM_REPLIES_ENABLED=False))
        self.assertEqual(gateway.text_sends, [])

    def test_failed_or_other_chat_tool_receipt_keeps_normal_reply(self):
        for receipt in (
            {"ok": False, "status": "failed", "current_conversation_text": "第一句。"},
            {"ok": True, "status": "success"},
        ):
            gateway = FakeQQGateway()
            event = {"type": "qq_visible_action_receipt", "action": "send_group_msg", **receipt}
            class Engine:
                def process_turn_stream(self, payload):
                    yield event
                    yield {"type": "speech_segment", "text": "第一句。"}
                    yield {"type": "final_ui", "payload": {"speech": "第一句。", "tool_events": [event]}}
            _process_qq_turn_streaming(engine=Engine(), qq_gateway=gateway,
                context=SimpleNamespace(session_id="group", profile_user_id="owner", character_pack_id="", reply_mode="text"),
                turn_payload={"message": "测试"}, config_module=SimpleNamespace(QQ_STREAM_REPLIES_ENABLED=True))
            self.assertEqual(gateway.text_sends, [["第一句。"]])

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
