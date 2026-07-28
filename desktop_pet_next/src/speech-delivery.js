const END_PUNCTUATION = new Set(["。", "！", "？", "!", "?"]);
const CLOSERS = new Set(["\"", "'", "”", "’", ")", "]", "}", "）", "】", "》", "」", "』"]);
const OPEN_TO_CLOSE = new Map([
  ["(", ")"],
  ["[", "]"],
  ["{", "}"],
  ["（", "）"],
  ["【", "】"],
  ["《", "》"],
  ["「", "」"],
  ["『", "』"],
  ["“", "”"],
  ["‘", "’"]
]);
const ABBREVIATIONS = new Set([
  "e.g",
  "i.e",
  "u.s",
  "u.k",
  "mr",
  "mrs",
  "ms",
  "dr",
  "prof",
  "sr",
  "jr",
  "vs",
  "etc",
  "inc",
  "ltd",
  "co",
  "corp",
  "no",
  "st"
]);

// MemCore's speech_segment events remain authoritative. This syntax-only
// fallback is used when an older/degraded backend returns speech without
// segments, and when one long segment needs smaller TTS delivery chunks.
export function segmentSpeechForDelivery(text, { minChars = 2, maxChars = 180 } = {}) {
  const source = String(text || "").replace(/\r\n?/gu, "\n").trim();
  if (!source) return [];

  const minimum = Math.max(1, Number(minChars) || 1);
  const maximum = Math.max(minimum, Number(maxChars) || 180);
  const raw = [];
  let start = 0;
  let index = 0;

  while (index < source.length) {
    if (source[index] === "\n") {
      appendSegment(raw, source.slice(start, index));
      start = index + 1;
      index += 1;
      continue;
    }
    if (isSentenceEnd(source, index)) {
      const end = consumeSentenceTail(source, index);
      appendSegment(raw, source.slice(start, end));
      start = end;
      index = end;
      continue;
    }
    if (index - start + 1 >= maximum) {
      const cut = softCut(source, start, index + 1, minimum);
      if (cut > start) {
        appendSegment(raw, source.slice(start, cut));
        start = cut;
        index = cut;
        continue;
      }
    }
    index += 1;
  }
  appendSegment(raw, source.slice(start));
  return mergeShortSegments(raw, minimum);
}

function appendSegment(items, value) {
  const text = String(value || "")
    .split(/\r?\n/gu)
    .join(" ")
    .trim();
  if (text) items.push(text);
}

function isSentenceEnd(text, index) {
  const character = text[index];
  if (isInsideProtectedSpan(text, index)) return false;
  if (END_PUNCTUATION.has(character)) return true;
  if (character === "…") return text[index + 1] === "…";
  if (character !== ".") return false;
  return isPeriodSentenceEnd(text, index);
}

function isPeriodSentenceEnd(text, index) {
  const previous = index > 0 ? text[index - 1] : "";
  const next = index + 1 < text.length ? text[index + 1] : "";
  if (/\d/u.test(previous) && /\d/u.test(next)) return false;
  if (/[\p{L}\p{N}]/u.test(previous) && /[\p{L}\p{N}]/u.test(next)) return false;
  if (isNumberedListMarker(text, index)) return false;
  const token = tokenEndingAt(text, index).toLowerCase().replace(/\.+$/u, "");
  return !ABBREVIATIONS.has(token);
}

function isInsideProtectedSpan(text, index) {
  const stack = [];
  let asciiQuoteOpen = false;
  let escaped = false;
  for (const character of text.slice(0, index)) {
    if (escaped) {
      escaped = false;
      continue;
    }
    if (character === "\\") {
      escaped = true;
      continue;
    }
    if (character === '"') {
      asciiQuoteOpen = !asciiQuoteOpen;
      continue;
    }
    if (asciiQuoteOpen) continue;
    if (OPEN_TO_CLOSE.has(character)) {
      stack.push(OPEN_TO_CLOSE.get(character));
      continue;
    }
    if (stack.length && character === stack[stack.length - 1]) stack.pop();
  }
  return asciiQuoteOpen || stack.length > 0;
}

function isNumberedListMarker(text, index) {
  let digitStart = index;
  while (digitStart > 0 && /\d/u.test(text[digitStart - 1])) digitStart -= 1;
  if (digitStart === index) return false;

  const lineStart = text.lastIndexOf("\n", digitStart - 1) + 1;
  const linePrefix = text.slice(lineStart, digitStart);
  if (linePrefix.trim()) {
    let boundary = digitStart - 1;
    while (boundary >= lineStart && /\s/u.test(text[boundary])) boundary -= 1;
    while (boundary >= lineStart && CLOSERS.has(text[boundary])) boundary -= 1;
    if (
      boundary >= lineStart &&
      !END_PUNCTUATION.has(text[boundary]) &&
      text[boundary] !== "…"
    ) {
      return false;
    }
  }

  let next = index + 1;
  while (next < text.length && (text[next] === " " || text[next] === "\t")) next += 1;
  return next < text.length && text[next] !== "\n";
}

function tokenEndingAt(text, index) {
  let start = index;
  while (start > 0 && !/\s/u.test(text[start - 1])) {
    if ("([{（【《「『\"'“‘".includes(text[start - 1])) break;
    start -= 1;
  }
  return text.slice(start, index + 1);
}

function consumeSentenceTail(text, index) {
  let end = index + 1;
  while (
    end < text.length &&
    (END_PUNCTUATION.has(text[end]) || text[end] === "." || text[end] === "…" || CLOSERS.has(text[end]))
  ) {
    if (text[end] === "." && !isPeriodSentenceEnd(text, end)) break;
    end += 1;
  }
  return end;
}

function softCut(text, start, end, minimum) {
  const window = text.slice(start, end);
  for (let offset = window.length; offset > 0; offset -= 1) {
    if (offset < minimum) break;
    if (/[，,；;、\s]/u.test(window[offset - 1]) && !isInsideProtectedSpan(window, offset - 1)) {
      return start + offset;
    }
  }
  return window.length >= minimum ? end : start;
}

function mergeShortSegments(raw, minimum) {
  const output = [];
  let pending = "";
  for (const item of raw) {
    const segment = pending ? `${pending}${item}` : item;
    pending = "";
    if (segment.length < minimum) {
      if (output.length) output[output.length - 1] += segment;
      else pending = segment;
      continue;
    }
    output.push(segment);
  }
  if (pending) {
    if (output.length) output[output.length - 1] += pending;
    else output.push(pending);
  }
  return output;
}
