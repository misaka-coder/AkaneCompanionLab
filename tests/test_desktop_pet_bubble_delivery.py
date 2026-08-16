from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DesktopPetBubbleDeliveryTests(unittest.TestCase):
    def test_speech_derived_segments_drive_live_delivery_without_becoming_body_authority(self) -> None:
        source = (ROOT / "desktop_pet_next/src/main.js").read_text(encoding="utf-8")
        bubble_delivery = (ROOT / "desktop_pet_next/src/bubble-delivery.js").read_text(encoding="utf-8")

        self.assertIn("BUBBLE_SEGMENT_MIN_MS = 2200", bubble_delivery)
        self.assertIn("BUBBLE_SEGMENT_MAX_MS = 3000", bubble_delivery)
        self.assertNotIn("splitStreamedBubbleText", bubble_delivery)
        self.assertNotIn("const CLIENT_SEGMENT_MAX", source)
        self.assertNotIn("function limitClientSegments", source)
        self.assertIn(
            "return segmentSpeechForDelivery(source, { minChars: 2, maxChars: CLIENT_SEGMENT_SOFT_LIMIT });",
            source,
        )
        stream_start = source.index('} else if (type === "speech_segment")')
        stream_end = source.index('} else if (type === "file_ready"', stream_start)
        stream_handler = source[stream_start:stream_end]
        self.assertIn("queueStreamedReplySegment(text, turnToken)", stream_handler)
        self.assertIn("queueStreamedTtsSegment(text, turnToken, event?.index);", stream_handler)
        self.assertIn("function queueStreamedReplySegment", source)
        self.assertIn("function showNextStreamedReplySegment", source)
        self.assertIn("getSegmentDisplayDelay(text)", source)
        self.assertIn("suppressSpeech: hasStreamedReply", source)
        self.assertNotIn("displayStreamingReplyPreview", source)
        self.assertNotIn("queueLiveReplyPayloadItems", source)
        segments_start = source.index("const speech = String(payload.speech")
        segments_end = source.index("if (!speech) return false;", segments_start)
        segment_render = source[segments_start:segments_end]
        self.assertIn("? splitSpeechText(speech)", segment_render)
        self.assertIn("showSpeechSegments(segments, { speaking });", segment_render)


if __name__ == "__main__":
    unittest.main()
