from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
