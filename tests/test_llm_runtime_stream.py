from __future__ import annotations

import unittest

from companion_v01.llm_runtime import _TopLevelJSONStreamTap


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


if __name__ == "__main__":
    unittest.main()
