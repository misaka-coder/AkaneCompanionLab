const END_PUNCTUATION = new Set(["。", "！", "？", "!", "?"]);
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
  ["‘", "’"], ["〈", "〉"], ["〔", "〕"], ["〖", "〗"], ["〘", "〙"],
  ["〚", "〛"], ["［", "］"], ["｛", "｝"], ["｟", "｠"], ["«", "»"], ["‹", "›"], ["<", ">"]
]);
const CLOSERS = new Set([...OPEN_TO_CLOSE.values(), '"', "'", "`"]);
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
export function segmentSpeechForDelivery(text, { minChars = 2, maxChars = 180, allowHardCut = true } = {}) {
  const source = String(text || "").replace(/\r\n?/gu, "\n").trim();
  if (!source) return [];

  const minimum = Math.max(1, Number(minChars) || 1);
  const maximum = Math.max(minimum, Number(maxChars) || 180);
  const protectedSpans = protectedSpanBoundaries(source);
  const raw = [];
  let start = 0;
  let index = 0;

  while (index < source.length) {
    if (source[index] === "\n" && !protectedSpans[index]) {
      appendSegment(raw, source.slice(start, index));
      start = index + 1;
      index += 1;
      continue;
    }
    if (isSentenceEnd(source, index, protectedSpans)) {
      const end = consumeSentenceTail(source, index, protectedSpans);
      appendSegment(raw, source.slice(start, end));
      start = end;
      index = end;
      continue;
    }
    if (index - start + 1 >= maximum) {
      const cut = softCut(source, start, index + 1, minimum, allowHardCut, protectedSpans);
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

function isSentenceEnd(text, index, protectedSpans) {
  const character = text[index];
  if (protectedSpans[index]) return false;
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

function protectedSpanBoundaries(text) {
  const stack = [];
  const boundaries = [];
  let codeEnd = 0;
  let escaped = false;
  for (let pos = 0; pos < text.length; pos += 1) {
    boundaries.push(stack.length > 0);
    if (pos < codeEnd) continue;
    const character = text[pos];
    const previous = text[pos - 1] || "";
    const following = text[pos + 1] || "";
    if (escaped) {
      escaped = false;
      continue;
    }
    if (character === "\\") {
      escaped = true;
      continue;
    }
    if (character === "`") {
      let end = pos + 1;
      while (text[end] === "`") end += 1;
      const delimiter = text.slice(pos, end);
      codeEnd = end;
      if (stack.at(-1) === delimiter) stack.pop();
      else if (!stack.at(-1)?.startsWith("`")) stack.push(delimiter);
      continue;
    }
    if (stack.at(-1)?.startsWith("`")) continue;
    if ((character === "'" || character === "’") && /[a-z0-9]/iu.test(previous) && /[a-z0-9]/iu.test(following)) continue;
    if (stack.length && character === stack.at(-1)) { stack.pop(); continue; }
    if (character === '"' || character === "'") {
      if (!/[a-z0-9]/iu.test(previous)) stack.push(character);
      continue;
    }
    if (character === "<" && (!following || /\s/u.test(following) || following === "=" || /[\p{L}\p{N}]/u.test(previous))) continue;
    if (OPEN_TO_CLOSE.has(character)) {
      stack.push(OPEN_TO_CLOSE.get(character));
      continue;
    }
  }
  boundaries.push(stack.length > 0);
  return boundaries;
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

function consumeSentenceTail(text, index, protectedSpans) {
  let end = index + 1;
  while (
    end < text.length &&
    (END_PUNCTUATION.has(text[end]) || text[end] === "." || text[end] === "…" || CLOSERS.has(text[end]))
  ) {
    if (CLOSERS.has(text[end]) && protectedSpans[end + 1]) break;
    if (text[end] === "." && !isPeriodSentenceEnd(text, end)) break;
    end += 1;
  }
  return end;
}

function softCut(text, start, end, minimum, allowHardCut, protectedSpans) {
  const window = text.slice(start, end);
  for (let offset = window.length; offset > 0; offset -= 1) {
    if (offset < minimum) break;
    if (/[，,；;、\s]/u.test(window[offset - 1]) && !protectedSpans[start + offset - 1]) {
      return start + offset;
    }
  }
  return allowHardCut && window.length >= minimum ? end : start;
}

// Final frames can resegment text already delivered as streaming sentences.
// Subtract exact text across those boundaries, consuming each occurrence once.
// Keep punctuation significant; this is transport reconciliation, not similarity.
export function missingReplySegments(finalSegments, deliveredSegments) {
  const key = (text) => String(text || "").replace(/\s+/gu, "");
  const remaining = (deliveredSegments || []).map(key).filter(Boolean);
  const missing = [];
  for (const segment of finalSegments || []) {
    let text = String(segment || "").trim();
    while (text && remaining.length) {
      const target = key(text);
      let bestCount = 0, bestStart = -1;
      for (let start = 0; start < remaining.length; start += 1) {
        let prefix = "";
        for (let end = start; end < remaining.length; end += 1) {
          prefix += remaining[end];
          if (!target.startsWith(prefix) && !prefix.startsWith(target)) break;
          const count = Math.min(target.length, prefix.length);
          if (count > bestCount) { bestCount = count; bestStart = start; }
        }
      }
      if (!bestCount) break;
      let rawEnd = 0, removed = 0;
      while (rawEnd < text.length && removed < bestCount) {
        if (!/\s/u.test(text[rawEnd])) removed += 1;
        rawEnd += 1;
      }
      text = text.slice(rawEnd).trimStart();
      while (bestCount && bestStart < remaining.length) {
        const consumed = Math.min(bestCount, remaining[bestStart].length);
        remaining[bestStart] = remaining[bestStart].slice(consumed);
        bestCount -= consumed;
        if (!remaining[bestStart]) remaining.splice(bestStart, 1);
      }
    }
    if (text) missing.push(text);
  }
  return missing;
}

export class SpeechStageReplay {
  constructor() {
    this.previous = [];
    this.current = "";
    this.candidates = [];
    this.seen = new Set();
    this.withheld = "";
  }
  push(text, index) {
    const raw = String(text || "").trim();
    if (!raw) return "";
    const key = (value) => value.replace(/\s+/gu, "");
    if (index !== null && index !== undefined && Number.isFinite(Number(index))) {
      const eventKey = `${index}:${key(raw)}`;
      if (this.seen.has(eventKey)) return "";
      this.seen.add(eventKey);
    }
    const before = key(this.current);
    this.current += raw;
    const current = key(this.current);
    const continuing = this.candidates.filter(old => old.startsWith(current));
    const completed = this.candidates.filter(old => current.startsWith(old) && old.length > before.length);
    if (continuing.length) {
      this.candidates = continuing;
      this.withheld = continuing.includes(current) ? "" : [this.withheld, raw].filter(Boolean).join(" ");
      return "";
    }
    this.candidates = [];
    const withheld = this.withheld;
    this.withheld = "";
    if (!completed.length) return [withheld, raw].filter(Boolean).join(" ");
    let count = Math.max(...completed.map(old => old.length)) - before.length;
    let offset = 0;
    while (offset < raw.length && count > 0) {
      if (!/\s/u.test(raw[offset])) count -= 1;
      offset += 1;
    }
    return raw.slice(offset).trimStart();
  }
  finishStage() {
    const current = this.current.replace(/\s+/gu, "");
    if (current && !this.previous.includes(current)) this.previous.push(current);
    this.current = "";
    this.candidates = [...this.previous];
    this.seen.clear();
    this.withheld = "";
  }
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
