from __future__ import annotations

import unittest
from types import SimpleNamespace

from companion_v01.llm_runtime import LLMRuntime, _TopLevelJSONStreamTap


class TopLevelJSONStreamTapTests(unittest.TestCase):
    def test_emits_ui_event_and_speech_chunks_from_split_json(self) -> None:
        tap = _TopLevelJSONStreamTap()
        events = []
        chunks = [
            '{"emotion":"happy","speech":"喵呜，',
            '主人欢迎回来……',
            '课上辛苦啦！","status":"final"}',
        ]

        for chunk in chunks:
            events.extend(tap.feed(chunk))

        self.assertEqual(events[0], {"type": "ui", "emotion": "happy"})
        speech_events = [event for event in events if event.get("type") == "speech_chunk"]
        self.assertGreaterEqual(len(speech_events), 1)
        self.assertEqual(tap.latest_emotion, "happy")
        self.assertEqual(tap.latest_speech, "喵呜，主人欢迎回来……课上辛苦啦！")

    def test_emits_speech_segments_from_array_as_each_item_closes(self) -> None:
        tap = _TopLevelJSONStreamTap()

        first_events = tap.feed('{"emotion":"happy","speech":"","speech_segments":["第一句。')
        self.assertFalse([event for event in first_events if event.get("type") == "speech_segment"])

        second_events = tap.feed('","第二句。')
        segment_events = [event for event in second_events if event.get("type") == "speech_segment"]
        self.assertEqual(segment_events, [{"type": "speech_segment", "index": 0, "text": "第一句。"}])
        self.assertEqual(tap.latest_speech, "第一句。")

        third_events = tap.feed('"],"tool_call":null}')
        segment_events = [event for event in third_events if event.get("type") == "speech_segment"]
        self.assertEqual(segment_events, [{"type": "speech_segment", "index": 1, "text": "第二句。"}])
        self.assertEqual(tap.latest_speech, "第一句。\n第二句。")

    def test_emits_delivery_hint_before_speech_when_reply_medium_closes(self) -> None:
        tap = _TopLevelJSONStreamTap()

        events = tap.feed('{"emotion":"happy","reply_medium":"voice","speech":"第一句。')

        self.assertIn({"type": "delivery_hint", "medium": "voice"}, events)
        self.assertEqual(tap.latest_reply_medium, "voice")
        delivery_index = events.index({"type": "delivery_hint", "medium": "voice"})
        first_speech_index = next(index for index, event in enumerate(events) if event.get("type") == "speech_chunk")
        self.assertLess(delivery_index, first_speech_index)

    def test_speech_segments_array_does_not_duplicate_speech_field_segments(self) -> None:
        tap = _TopLevelJSONStreamTap()
        events = []

        events.extend(tap.feed('{"emotion":"happy","speech":"第一句。","speech_segments":["第一句。","第二句。"]}'))

        segment_events = [event for event in events if event.get("type") == "speech_segment"]
        self.assertEqual(
            segment_events,
            [
                {"type": "speech_segment", "index": 0, "text": "第一句。"},
                {"type": "speech_segment", "index": 1, "text": "第二句。"},
            ],
        )
        self.assertEqual(tap.latest_speech, "第一句。")

    def test_leading_tool_call_probe_handles_null(self) -> None:
        runtime = object.__new__(LLMRuntime)

        state, call = runtime._try_extract_leading_tool_call('{"tool_call":null,"emotion":"normal"}')

        self.assertEqual(state, "null")
        self.assertIsNone(call)

    def test_leading_tool_call_probe_extracts_object(self) -> None:
        runtime = object.__new__(LLMRuntime)

        state, call = runtime._try_extract_leading_tool_call(
            '{"tool_call":{"type":"retrieve_memory","query":"扬州城","keywords":["二十四桥"]},"emotion":"normal"}'
        )

        self.assertEqual(state, "object")
        self.assertEqual(call["type"], "retrieve_memory")
        self.assertEqual(call["query"], "扬州城")
        self.assertEqual(call["keywords"], ["二十四桥"])

    def test_stream_tool_call_probe_extracts_object_after_speech_segments(self) -> None:
        runtime = object.__new__(LLMRuntime)

        state, call = runtime._try_extract_stream_tool_call(
            '{"emotion":"normal","speech":"我查一下。","speech_segments":[],"tool_call":{"type":"retrieve_memory","query":"扬州城","keywords":["二十四桥"]}'
        )

        self.assertEqual(state, "object")
        self.assertEqual(call["type"], "retrieve_memory")
        self.assertEqual(call["query"], "扬州城")
        self.assertEqual(call["keywords"], ["二十四桥"])

    def test_stream_tool_call_probe_waits_for_prior_value_to_close(self) -> None:
        runtime = object.__new__(LLMRuntime)

        state, call = runtime._try_extract_stream_tool_call(
            '{"emotion":"normal","speech":"我还没说完'
        )

        self.assertEqual(state, "pending")
        self.assertIsNone(call)

    def test_stream_chat_json_stops_on_tool_call_after_speech_segments(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        runtime._build_completion_kwargs = lambda **_kwargs: {}
        runtime._record_cache_metrics = lambda _response: None
        runtime._close_stream = lambda _response: None

        def chunk(text: str) -> SimpleNamespace:
            return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])

        runtime._create_completion = lambda **_kwargs: [
            chunk('{"emotion":"normal","speech":"我查一下。","speech_segments":[],'),
            chunk('"tool_call":{"type":"retrieve_memory","query":"扬州城"}}'),
            chunk(',"status":"final"}'),
        ]

        generator = runtime._stream_chat_json(
            bundle=SimpleNamespace(),
            system_prompt="system",
            user_prompt="user",
            fallback={"emotion": "normal", "speech": "", "speech_segments": [], "tool_call": None},
            temperature=0.0,
            early_tool_call_validator=None,
            prompt_cache_key="test:stream_tool_call_after_speech",
        )

        while True:
            try:
                next(generator)
            except StopIteration as exc:
                result = exc.value
                break

        self.assertTrue(result.stopped_early)
        self.assertEqual(result.parsed["tool_call"]["type"], "retrieve_memory")
        self.assertEqual(result.parsed["tool_call"]["query"], "扬州城")
        self.assertNotIn('"status"', result.raw_text)

    def test_extract_text_flattens_content_blocks(self) -> None:
        runtime = object.__new__(LLMRuntime)

        text = runtime._flatten_message_content(
            [
                {"type": "text", "text": '{"emotion":"normal",'},
                {"type": "text", "text": '"speech":"在的。"}'},
            ]
        )

        self.assertEqual(text, '{"emotion":"normal","speech":"在的。"}')

    def test_extract_json_uses_first_balanced_object(self) -> None:
        runtime = object.__new__(LLMRuntime)

        parsed = runtime._extract_json(
            '好的，JSON 如下：\n```json\n{"emotion":"happy","speech":"在的。"}\n```\n{"ignored":true}'
        )

        self.assertEqual(parsed, {"emotion": "happy", "speech": "在的。"})

    def test_partial_chat_json_recovery_keeps_generated_speech(self) -> None:
        runtime = object.__new__(LLMRuntime)

        recovered = runtime._recover_partial_chat_json(
            '{"emotion":"happy","speech":"我听到啦，主人。","speech_segments":[]',
            fallback={"emotion": "normal", "speech": "fallback", "speech_segments": []},
        )

        self.assertEqual(recovered["emotion"], "happy")
        self.assertEqual(recovered["speech"], "我听到啦，主人。")
        self.assertEqual(recovered["speech_segments"], [])

    def test_partial_chat_json_recovery_keeps_speech_segments(self) -> None:
        runtime = object.__new__(LLMRuntime)

        recovered = runtime._recover_partial_chat_json(
            '{"emotion":"happy","speech":"","speech_segments":["第一句。","第二句。"],"tool_call":null',
            fallback={"emotion": "normal", "speech": "fallback", "speech_segments": []},
        )

        self.assertEqual(recovered["emotion"], "happy")
        self.assertEqual(recovered["speech"], "第一句。\n第二句。")
        self.assertEqual(recovered["speech_segments"], ["第一句。", "第二句。"])


if __name__ == "__main__":
    unittest.main()
