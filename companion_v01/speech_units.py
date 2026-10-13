"""Display/spoken-text adapter; memory and sentence segmentation stay generic.

One utterance owns a voice style, not a TTS request. Both streams use MemCore's
sentence parser. Only display text is projected into the legacy speech field.
"""
from __future__ import annotations

import re
from typing import Any

from memcore import StreamingSpeechParser, segment_speech

from .assistant_history_projection import ARCHIVED_SAY

PROTOCOL = "speech_units_v1"
CONTINUOUS_VOICE_MAX_CHARS = 120


def _hidden_say(value: Any) -> bool:
    return isinstance(value, str) and value.strip() in (ARCHIVED_SAY, "[archived]")


def normalize_unit_emotion(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    return value if len(value) <= 80 and not re.search(r"[\x00-\x1f\x7f]", value) else ""


def _emotion_metadata(value: Any) -> dict[str, str]:
    emotion = normalize_unit_emotion(value)
    return {"emotion": emotion} if emotion else {}


def continuous_voice_chunks(text: str) -> list[str]:
    """Bound synthesis length while keeping neighbouring sentences together."""
    text = " ".join(str(text or "").split())
    if not text:
        return []
    if len(text) <= CONTINUOUS_VOICE_MAX_CHARS:
        return [text]
    chunks, current = [], ""
    for sentence in segment_speech(text, min_chars=1, max_chars=CONTINUOUS_VOICE_MAX_CHARS, max_segments=None):
        # Protected quotes may exceed the segmenter's soft limit. The synthesis
        # limit still applies, without dropping their remaining text.
        for start in range(0, len(sentence), CONTINUOUS_VOICE_MAX_CHARS):
            piece = sentence[start:start + CONTINUOUS_VOICE_MAX_CHARS]
            if current and len(current) + len(piece) > CONTINUOUS_VOICE_MAX_CHARS:
                chunks.append(current)
                current = ""
            current += piece
    if current:
        chunks.append(current)
    return chunks


def normalize_utterances(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    units = []
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            continue
        # Missing say is deliberately silent. Never guess a translation or read
        # stage directions through a voice configured for another language.
        say = item.get("say")
        style = item.get("style") if isinstance(item.get("style"), str) else ""
        style = style.strip()
        units.append({"text": item["text"], "say": say if isinstance(say, str) else None,
                      "style": style if len(style) <= 120 and not re.search(r"[\x00-\x1f\x7f]", style) else "",
                      **_emotion_metadata(item.get("emotion"))})
    return units


def project_speech_units(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload.get("utterances"), list):
        return payload
    units = normalize_utterances(payload["utterances"])
    return {**payload, "utterances": units, "speech_protocol": PROTOCOL,
            "speech": "".join(unit["text"] for unit in units)}


def voice_segments(units: Any, *, default_emotion: str = "") -> list[dict[str, Any]]:
    result = []
    emotion = normalize_unit_emotion(default_emotion)
    for unit_index, unit in enumerate(normalize_utterances(units)):
        emotion = unit.get("emotion") or emotion
        if _hidden_say(unit["say"]):
            continue
        for text in segment_speech(unit["say"] or "", min_chars=1,
                                   max_chars=180, max_segments=None):
            result.append({"type": "voice_segment", "index": len(result), "unit_index": unit_index,
                           "text": text, "display_text": unit["text"], "style": unit["style"],
                           **_emotion_metadata(emotion)})
    return result


def _parser():
    return StreamingSpeechParser(mode="plain", min_segment_chars=1, max_segments=None)


class VoiceEventReplay:
    """Suppress a replayed leading stage while retaining positional repetition."""
    def __init__(self):
        self.previous = []
        self.current = []
        self.seen = set()
        self.next_index = 0
        self.delivered = []

    def begin_stage(self):
        if self.current:
            self.previous.append(self.current)
        self.current = []
        self.seen.clear()

    def accept(self, event):
        key = (str(event.get("style") or ""), "".join(str(event.get("text") or "").split()))
        identity = (event.get("index"), key)
        if identity in self.seen:
            return None
        self.seen.add(identity)
        self.current.append(key)
        if any(stage[:len(self.current)] == self.current for stage in self.previous):
            return None
        accepted = {**event, "index": self.next_index}
        self.delivered.append(key)
        self.next_index += 1
        return accepted

    def begin_final(self, events):
        """The canonical reply can span retry stages, excluding tool prefaces."""
        keys = [(str(event.get("style") or ""), "".join(str(event.get("text") or "").split()))
                for event in events]
        prefix = []
        for start in range(len(self.delivered)):
            suffix = self.delivered[start:]
            if keys[:len(suffix)] == suffix:
                prefix = suffix
                break
        self.previous = [prefix] if prefix else []
        self.current = []
        self.seen.clear()


class SpeechUnitsStream:
    """Small path-aware JSON tap, not a second JSON repair/validation system.

    It recognizes only root utterances[i].{emotion,style,text,say}. Escaped strings and
    nested tool arguments cannot masquerade as speech. The final JSON decoder
    remains authoritative. A malformed/unclosed say never flushes its tail.
    """
    def __init__(self):
        self.active = False
        self.complete = False
        self.units: list[dict[str, Any]] = []
        self.latest_text = ""
        self._stack = []
        self._token = None
        self._escape = False
        self._unicode = ""
        self._unicode_left = 0
        self._surrogate = ""
        self._path = ()
        self._key_token = False
        self._text = _parser()
        self._voice = None
        self._voice_index = 0
        self._unit = {}
        self._unit_open = False
        self._spoken_committed = []
        self._voice_meta = {}
        self._emotion = ""
        self._unit_emotion = ""
        self._events = []
        self._delta = []
        self._finished = False

    def _value_path(self):
        if not self._stack:
            return ()
        frame = self._stack[-1]
        return frame["path"] + (frame["key"] if frame["kind"] == "{" else frame["index"],)

    def _done_value(self):
        if self._stack and self._stack[-1]["kind"] == "[":
            self._stack[-1]["index"] += 1

    def _is_field(self, name):
        return len(self._path) == 3 and self._path[0] == "utterances" and self._path[2] == name

    def _flush_delta(self):
        if not self._delta:
            return
        value = "".join(self._delta)
        self._delta.clear()
        if self._is_field("text"):
            self.latest_text += value
            self._events.extend({**event, "speech_protocol": PROTOCOL} for event in self._text.feed(value))
        elif self._is_field("say") and self._voice is not None:
            self._emit_voice(self._voice.feed(value))

    def _emit_voice(self, events):
        for event in events:
            if event.get("type") == "speech_segment":
                if _hidden_say(event["text"]):
                    continue
                self._events.append({"type": "voice_segment", "index": self._voice_index,
                                     **self._voice_meta, "text": event["text"]})
                self._spoken_committed.append(event["text"])
                self._voice_index += 1

    def _append(self, char):
        # JSON may escape an emoji as two UTF-16 code units.
        if self._surrogate:
            high = ord(self._surrogate)
            self._surrogate = ""
            if 0xDC00 <= ord(char) <= 0xDFFF:
                char = chr(0x10000 + ((high - 0xD800) << 10) + ord(char) - 0xDC00)
            else:
                self._append("\ufffd")
        if 0xD800 <= ord(char) <= 0xDBFF:
            self._surrogate = char
            return
        self._token.append(char)
        if not self._key_token and (self._is_field("text") or self._is_field("say")):
            self._delta.append(char)

    def feed(self, source: str) -> list[dict[str, Any]]:
        self._events = []
        for char in source:
            if self._token is not None:
                if self._unicode_left:
                    self._unicode += char
                    self._unicode_left -= 1
                    if not self._unicode_left:
                        try:
                            self._append(chr(int(self._unicode, 16)))
                        except ValueError:
                            self._append("\ufffd")
                    continue
                if self._escape:
                    self._escape = False
                    if char == "u":
                        self._unicode, self._unicode_left = "", 4
                    else:
                        self._append({"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f"}.get(char, char))
                    continue
                if char == "\\":
                    self._escape = True
                elif char == '"':
                    self._flush_delta()
                    value = "".join(self._token)
                    self._token = None
                    if self._key_token:
                        self._stack[-1]["key"] = value
                        self._stack[-1]["wants_key"] = False
                    else:
                        if self._path == ("emotion",):
                            self._emotion = normalize_unit_emotion(value)
                        if len(self._path) == 3 and self._path[0] == "utterances":
                            field = self._path[2]
                            if field in {"text", "say", "style"}:
                                self._unit[field] = value
                            elif field == "emotion" and "text" not in self._unit:
                                self._unit[field] = value
                            if field == "text":
                                self._emotion = normalize_unit_emotion(self._unit.get("emotion")) or self._emotion
                                self._unit_emotion = self._emotion
                                self._events.append({"type": "speech_unit_text", "unit_index": self._path[1], "text": value,
                                                     **_emotion_metadata(self._unit_emotion)})
                            if field == "say" and self._voice is not None:
                                self._emit_voice(self._voice.finish())
                                self._events.append({"type": "voice_unit_end", "unit_index": self._path[1],
                                                     "display_text": self._unit["text"], "has_voice": bool(value.strip()) and not _hidden_say(value),
                                                     **_emotion_metadata(self._unit_emotion)})
                                self._voice = None
                        self._done_value()
                else:
                    self._append(char)
                continue
            if char == '"':
                self._key_token = bool(self._stack and self._stack[-1]["kind"] == "{" and self._stack[-1]["wants_key"])
                self._path = self._value_path()
                self._token = []
                if not self._key_token and self._is_field("say") and "text" in self._unit:
                    self._voice = _parser()
                    self._voice_meta = {"unit_index": self._path[1], "display_text": self._unit["text"],
                                        "style": normalize_utterances([self._unit])[0]["style"],
                                        **_emotion_metadata(self._unit_emotion)}
            elif char in "{[":
                path = self._value_path()
                self._stack.append({"kind": char, "path": path, "key": None, "index": 0, "wants_key": char == "{"})
                if path == ("utterances",) and char == "[":
                    self.active = True
                    self._events.append({"type": "speech_mode", "speech_protocol": PROTOCOL,
                                         **_emotion_metadata(self._emotion)})
                if len(path) == 2 and path[0] == "utterances" and char == "{":
                    self._unit = {}
                    self._unit_open = True
                    self._spoken_committed = []
                    self._voice_meta = {}
                    self._unit_emotion = self._emotion
            elif char in "}]" and self._stack:
                frame = self._stack.pop()
                path = frame["path"]
                if len(path) == 2 and path[0] == "utterances" and frame["kind"] == "{":
                    unit = normalize_utterances([self._unit])
                    if unit:
                        if "say" not in self._unit:
                            warning = {"type": "speech_warning", "code": "missing_say", "unit_index": path[1]}
                            self._events.append(warning)
                        elif _hidden_say(self._unit.get("say")):
                            self._events.append({"type": "speech_warning", "code": "say_hidden", "unit_index": path[1]})
                        # The voice style is snapshotted before say starts. Late
                        # metadata must not change already queued audio on replay.
                        if self._voice_meta:
                            unit[0]["style"] = self._voice_meta["style"]
                        else:
                            for event in voice_segments(unit, default_emotion=self._unit_emotion):
                                self._events.append({**event, "index": self._voice_index, "unit_index": path[1]})
                                self._voice_index += 1
                            self._events.append({"type": "voice_unit_end", "unit_index": path[1],
                                                 "display_text": unit[0]["text"], "has_voice": bool(unit[0]["say"]) and not _hidden_say(unit[0]["say"]),
                                                 **_emotion_metadata(self._unit_emotion)})
                        self.units.extend(unit)
                    self._unit_open = False
                if path == ("utterances",):
                    self.complete = True
                self._done_value()
            elif char == "," and self._stack and self._stack[-1]["kind"] == "{":
                self._stack[-1]["wants_key"] = True
            elif char == "n":
                path = self._value_path()
                if len(path) == 3 and path[0] == "utterances" and path[2] == "say":
                    self._unit["say"] = None
        self._flush_delta()
        return self._events

    def snapshot_units(self):
        units = list(self.units)
        if self._unit_open and isinstance(self._unit.get("text"), str):
            units.extend(normalize_utterances([{
                "text": self._unit["text"], "say": "".join(self._spoken_committed) or None,
                "style": self._voice_meta.get("style", self._unit.get("style", "")),
                **_emotion_metadata(self._unit_emotion),
            }]))
        return units

    def finish(self):
        if self._finished or not self.active:
            return []
        self._finished = True
        # Display tails are committed only when the utterance array is closed.
        if not self.complete:
            return []
        return [{**event, "speech_protocol": PROTOCOL} for event in self._text.finish()
                if event.get("type") == "speech_segment"]
