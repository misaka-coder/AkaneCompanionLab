import { segmentSpeechForDelivery } from "./speech-delivery.js";

export const SPEECH_UNITS_PROTOCOL = "speech_units_v1";
// Keep in sync with the backend's continuous synthesis delivery limit.
export const CONTINUOUS_VOICE_MAX_CHARS = 120;

export function continuousVoiceChunks(text) {
  const source = String(text || "").replace(/\s+/gu, " ").trim();
  if (!source) return [];
  if (source.length <= CONTINUOUS_VOICE_MAX_CHARS) return [source];
  const chunks = [];
  let current = "";
  for (const sentence of segmentSpeechForDelivery(source, { minChars: 1, maxChars: CONTINUOUS_VOICE_MAX_CHARS })) {
    for (let start = 0; start < sentence.length; start += CONTINUOUS_VOICE_MAX_CHARS) {
      const piece = sentence.slice(start, start + CONTINUOUS_VOICE_MAX_CHARS);
      if (current && current.length + piece.length > CONTINUOUS_VOICE_MAX_CHARS) {
        chunks.push(current);
        current = "";
      }
      current += piece;
    }
  }
  if (current) chunks.push(current);
  return chunks;
}

export function normalizeUnitEmotion(value) {
  const emotion = typeof value === "string" ? value.trim() : "";
  return emotion.length <= 80 && !/[\x00-\x1f\x7f]/u.test(emotion) ? emotion : "";
}

export function utteranceVoiceSegments(utterances, defaultEmotion = "") {
  const events = [];
  let emotion = normalizeUnitEmotion(defaultEmotion);
  for (const [unitIndex, unit] of (Array.isArray(utterances) ? utterances : []).entries()) {
    if (typeof unit?.text !== "string") continue;
    emotion = normalizeUnitEmotion(unit.emotion) || emotion;
    if (typeof unit?.say !== "string") continue;
    for (const text of segmentSpeechForDelivery(unit.say, { minChars: 1 })) {
      events.push({ type: "voice_segment", index: events.length, unit_index: unitIndex,
        text, display_text: unit.text, style: String(unit.style || ""), ...(emotion ? { emotion } : {}) });
    }
  }
  return events;
}

// Final-only delivery uses the same ordering and unit identity as the stream.
export function utteranceDeliveryEvents(utterances, defaultEmotion = "") {
  const voices = utteranceVoiceSegments(utterances, defaultEmotion);
  let emotion = normalizeUnitEmotion(defaultEmotion);
  return (Array.isArray(utterances) ? utterances : []).flatMap((unit, unitIndex) => {
    if (typeof unit?.text !== "string") return [];
    emotion = normalizeUnitEmotion(unit.emotion) || emotion;
    const presentation = emotion ? { emotion } : {};
    if (!unit.text.trim()) return [];
    return [
      { type: "speech_unit_text", unit_index: unitIndex, text: unit.text, ...presentation },
      ...voices.filter(event => event.unit_index === unitIndex),
      { type: "voice_unit_end", unit_index: unitIndex, display_text: unit.text,
        has_voice: typeof unit.say === "string" && Boolean(unit.say.trim()), ...presentation }
    ];
  });
}

const key = (event) => JSON.stringify([String(event.style || ""), String(event.text || "").replace(/\s+/gu, "")]);

// Scope duplicate suppression to a generation/stage, preserving repeated words
// at different positions. A retried stage can replay a previously delivered prefix.
export class VoiceStageReplay {
  constructor() { this.previous = []; this.current = []; this.seen = new Set(); this.delivered = []; }
  accept(event) {
    if (!event?.text) return false;
    const value = key(event);
    const id = `${event.index}:${value}`;
    if (this.seen.has(id)) return false;
    this.seen.add(id);
    this.current.push(value);
    if (this.previous.some(stage => this.current.every((part, index) => stage[index] === part))) return false;
    this.delivered.push(value);
    return true;
  }
  finishStage() {
    if (this.current.length) this.previous.push(this.current);
    this.current = [];
    this.seen.clear();
  }
  missingFinal(events) {
    // Final output describes the last stage, not every tool preface in the turn.
    return events.filter(event => !this.seen.has(`${event.index}:${key(event)}`));
  }
  beginFinal(events) {
    // A canonical final may join several interrupted stages. Match its prefix
    // against the delivered suffix so earlier tool prefaces stay separate.
    const keys = events.map(key);
    const prefix = this.delivered.findIndex((_value, start) => {
      const suffix = this.delivered.slice(start);
      return suffix.every((value, index) => keys[index] === value);
    });
    this.previous = prefix < 0 ? [] : [this.delivered.slice(prefix)];
    this.current = [];
    this.seen.clear();
  }
}

export function voiceQueueItem(event) {
  return { text: String(event.text || "").trim(), displayText: String(event.display_text || ""),
    voiceStyle: String(event.style || ""), speechProtocol: SPEECH_UNITS_PROTOCOL,
    unitIndex: event.unit_index, presentationKey: event.presentation_key, reply: Boolean(event.display_text) };
}

export function canMergeVoiceItems(left, right) {
  return Boolean(left && right && left.voiceStyle === right.voiceStyle && left.speechProtocol === right.speechProtocol
    && left.unitIndex === right.unitIndex && left.presentationKey === right.presentationKey);
}
