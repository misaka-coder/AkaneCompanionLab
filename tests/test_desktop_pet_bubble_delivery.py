from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DesktopPetBubbleDeliveryTests(unittest.TestCase):
    def test_segments_are_not_merged_or_replayed_as_one_full_bubble(self) -> None:
        source = (ROOT / "desktop_pet_next/src/main.js").read_text(encoding="utf-8")
        bubble_delivery = (ROOT / "desktop_pet_next/src/bubble-delivery.js").read_text(encoding="utf-8")

        self.assertIn("BUBBLE_SEGMENT_MIN_MS = 2200", bubble_delivery)
        self.assertIn("BUBBLE_SEGMENT_MAX_MS = 3000", bubble_delivery)
        self.assertIn("replayFinalSpeech: false", bubble_delivery)
        self.assertIn("splitStreamedBubbleText", bubble_delivery)
        self.assertNotIn("const CLIENT_SEGMENT_MAX", source)
        self.assertNotIn("function limitClientSegments", source)
        self.assertIn(
            "return splitStreamedBubbleText(source, { minChars: 2, maxChars: CLIENT_SEGMENT_SOFT_LIMIT });",
            source,
        )
        stream_start = source.index('} else if (type === "speech_segment")')
        stream_end = source.index('} else if (type === "file_ready"', stream_start)
        stream_handler = source[stream_start:stream_end]
        self.assertIn("const bubbleSegments = splitSpeechText(text);", stream_handler)
        self.assertIn("for (let index = 0; index < bubbleSegments.length; index += 1)", stream_handler)
        self.assertNotIn("queueStreamedReplySegment(text, turnToken", stream_handler)
        completion_start = source.index("function scheduleStreamedReplyCompletion()")
        completion_end = source.index("function resetStreamingTtsState", completion_start)
        completion = source[completion_start:completion_end]
        self.assertNotIn("displayReplyBubbleText(finalText", completion)
        self.assertIn("completeSegmentedBubbleDelivery", completion)
        self.assertIn("scheduleBubbleReset(completion.dismissCharCount", completion)


if __name__ == "__main__":
    unittest.main()
