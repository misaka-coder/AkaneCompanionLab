import { segmentSpeechForDelivery } from "./speech-delivery.js";

export const BUBBLE_SEGMENT_MIN_MS = 2200;
export const BUBBLE_SEGMENT_MAX_MS = 3000;
export const BUBBLE_SEGMENT_CHAR_RATE = 90;

// A streamed ``speech_segment`` is the completed top-level ``speech`` JSON
// string, not necessarily one of the final MemCore/QQ delivery segments. Split
// it before it enters the display queue so the later authoritative final frame
// cannot mistake one oversized bubble for an already segmented reply.
export function splitStreamedBubbleText(text, { minChars = 2, maxChars = 56 } = {}) {
  return segmentSpeechForDelivery(text, { minChars, maxChars });
}

export function getBubbleSegmentDisplayDelay(text) {
  return Math.max(
    BUBBLE_SEGMENT_MIN_MS,
    Math.min(BUBBLE_SEGMENT_MAX_MS, String(text || "").length * BUBBLE_SEGMENT_CHAR_RATE)
  );
}

// The authoritative final speech is useful for deduplication and persistence,
// but it must not replace the last displayed segment. Doing so recreates one
// oversized bubble after the segmented queue has already completed.
export function completeSegmentedBubbleDelivery({ lastSegment = "" } = {}) {
  const text = String(lastSegment || "").trim();
  return {
    replayFinalSpeech: false,
    dismissCharCount: Math.max(text.length, 4)
  };
}
