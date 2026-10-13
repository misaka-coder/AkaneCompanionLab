"""Incremental JSON speech and UI projection, independent of provider I/O."""

from __future__ import annotations

from typing import Any

from memcore import StreamingSpeechParser


JSON_ESCAPE_MAP = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}


class _TopLevelJSONStreamTap:
    def __init__(self):
        from ..speech_units import SpeechUnitsStream
        self._units_stream = SpeechUnitsStream()
        self.depth = 0
        self.in_string = False
        self.string_role = ""
        self.string_buffer: list[str] = []
        self.current_key = ""
        self.captured_value_key: str | None = None
        self.expecting_key = False
        self.expecting_colon = False
        self.expecting_value = False
        self.in_primitive = False
        self.escape_pending = False
        self.unicode_buffer: list[str] | None = None
        self.latest_emotion = ""
        self.latest_speech = ""
        self._ui_emitted = False
        self._speech_complete = False
        self._speech_stream = StreamingSpeechParser(
            mode="memcore_json",
            enable_sentence_segments=True,
            min_segment_chars=1,
            max_segments=None,
        )
        self._speech_stream_finished = False
        self._delivered_speech_segments: list[str] = []
        self._root_seen = False
        self._root_prefix: list[str] = []
        self._external_prefix = False

    def feed(self, text: Any) -> list[dict[str, Any]]:
        source = str(text or "")
        events: list[dict[str, Any]] = []
        for char in source:
            if not self._root_seen and self.depth == 0 and char != "{":
                self._root_prefix.append(char)
            if self.in_string:
                self._consume_string_char(char, events)
                continue

            if self.in_primitive:
                if self.depth == 1 and char == ",":
                    self._reset_top_level_pair()
                    continue
                if self.depth == 1 and char == "}":
                    self.in_primitive = False
                    self._clear_pair_state()
                    self.depth = max(0, self.depth - 1)
                    continue
                if char in "{[":
                    self.depth += 1
                elif char in "}]":
                    self.depth = max(0, self.depth - 1)
                continue

            if char in " \t\r\n":
                continue

            if char == "{":
                if not self._root_seen:
                    self._root_seen = True
                    prefix = "".join(self._root_prefix).strip().lower()
                    self._external_prefix = prefix not in {"", "```", "```json"}
                self.depth += 1
                if self.depth == 1:
                    self.expecting_key = True
                    self.expecting_colon = False
                    self.expecting_value = False
                    self.current_key = ""
                elif self.expecting_value:
                    self.expecting_value = False
                continue

            if char == "[":
                self.depth += 1
                if self.expecting_value:
                    self.expecting_value = False
                continue

            if char == "}":
                if self.depth == 1:
                    self._clear_pair_state()
                self.depth = max(0, self.depth - 1)
                continue

            if char == "]":
                self.depth = max(0, self.depth - 1)
                continue

            if self.depth != 1:
                continue

            if char == ",":
                self._reset_top_level_pair()
                continue

            if self.expecting_key:
                if char == '"':
                    self._start_string("key")
                continue

            if self.expecting_colon:
                if char == ":":
                    self.expecting_colon = False
                    self.expecting_value = True
                continue

            if self.expecting_value:
                if char == '"':
                    self._start_string("value")
                    self.captured_value_key = self.current_key
                else:
                    self.expecting_value = False
                    self.in_primitive = True
                continue

        unit_events = self._units_stream.feed(source)
        speech_events = unit_events if self._units_stream.active else self._speech_stream.feed(source)
        for event in speech_events:
            event_type = str(event.get("type") or "")
            if event_type == "speech_chunk":
                self.latest_speech += str(event.get("text") or "")
            if not self._external_prefix and event_type in {"speech_chunk", "speech_segment", "speech_mode", "speech_unit_text", "voice_segment", "voice_unit_end", "speech_warning"}:
                events.append(event)
            if not self._external_prefix and event_type == "speech_segment":
                self._delivered_speech_segments.append(str(event.get("text") or "").strip())
        return [] if self._external_prefix else events

    def finish(self, *, include_incomplete_remainder: bool = True) -> list[dict[str, Any]]:
        if self._speech_stream_finished:
            return []
        self._speech_stream_finished = True
        if not self._speech_complete and not self._units_stream.complete:
            return []
        events = [
            event
            for event in (self._units_stream.finish() if self._units_stream.active else self._speech_stream.finish())
            if str(event.get("type") or "") == "speech_segment"
        ]
        if self._external_prefix:
            return []
        if not include_incomplete_remainder:
            events = [event for event in events if _speech_segment_has_terminal_boundary(event.get("text"))]
        self._delivered_speech_segments.extend(
            str(event.get("text") or "").strip() for event in events if str(event.get("text") or "").strip()
        )
        return events

    @property
    def delivered_speech(self) -> str:
        return "\n".join(item for item in self._delivered_speech_segments if item).strip()

    def _start_string(self, role: str) -> None:
        self.in_string = True
        self.string_role = role
        self.string_buffer = []
        self.escape_pending = False
        self.unicode_buffer = None

    def _consume_string_char(
        self,
        char: str,
        events: list[dict[str, Any]],
    ) -> None:
        if self.unicode_buffer is not None:
            if char.lower() in "0123456789abcdef":
                self.unicode_buffer.append(char)
                if len(self.unicode_buffer) == 4:
                    decoded = ""
                    try:
                        decoded = chr(int("".join(self.unicode_buffer), 16))
                    except Exception:
                        decoded = ""
                    self.unicode_buffer = None
                    if decoded:
                        self._append_string_char(decoded)
                return

            self.unicode_buffer = None
            self._append_string_char("u")

        if self.escape_pending:
            self.escape_pending = False
            if char == "u":
                self.unicode_buffer = []
                return
            self._append_string_char(JSON_ESCAPE_MAP.get(char, char))
            return

        if char == "\\":
            self.escape_pending = True
            return

        if char == '"':
            self.in_string = False
            text = "".join(self.string_buffer)
            if self.string_role == "key":
                self.current_key = text
                self.expecting_key = False
                self.expecting_colon = True
            else:
                if self.captured_value_key == "emotion":
                    self.latest_emotion = text
                    if text and not self._ui_emitted:
                        self._ui_emitted = True
                        events.append({"type": "ui", "emotion": text})
                elif self.captured_value_key == "speech":
                    self._speech_complete = True
                self.expecting_value = False
                self.captured_value_key = None
            self.string_role = ""
            self.string_buffer = []
            return

        self._append_string_char(char)

    def _append_string_char(self, char: str) -> None:
        self.string_buffer.append(char)

    def _reset_top_level_pair(self) -> None:
        self.current_key = ""
        self.captured_value_key = None
        self.expecting_key = True
        self.expecting_colon = False
        self.expecting_value = False
        self.in_primitive = False

    def _clear_pair_state(self) -> None:
        self.current_key = ""
        self.captured_value_key = None
        self.expecting_key = False
        self.expecting_colon = False
        self.expecting_value = False
        self.in_primitive = False


def _speech_segment_has_terminal_boundary(value: Any) -> bool:
    """Whether a streamed tool preface is a complete delivery unit.

    Final replies may intentionally omit punctuation and are flushed normally.
    Tool-call prefaces are different: a provider can close the ``speech`` JSON
    field halfway through a phrase before emitting a native call.  Only a
    sentence boundary is safe to expose as already-delivered QQ/UI speech.
    """

    text = str(value or "").rstrip()
    if not text:
        return False
    closing = "\"'”’》）)]】」』"
    while text and text[-1] in closing:
        text = text[:-1].rstrip()
    return bool(text) and text[-1] in ".!?。！？…"


def _completed_tool_preface_text(value: Any) -> str:
    text = str(value or "").strip()
    last_boundary = max((index for index, char in enumerate(text) if char in ".!?。！？…"), default=-1)
    if last_boundary < 0:
        return ""
    end = last_boundary + 1
    closing = "\"'”’》）)]】」』"
    while end < len(text) and text[end] in closing:
        end += 1
    return text[:end].strip()
