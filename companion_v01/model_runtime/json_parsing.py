"""JSON extraction, recovery and memory annotation provenance."""

from __future__ import annotations

from typing import Any
import json
import re

from memcore import memory_metadata_has_signal as memcore_metadata_has_signal

from .json_stream import _TopLevelJSONStreamTap


JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _memory_metadata_truth(
    payload: Any,
    *,
    accepted_status: str,
    require_signal: bool = False,
) -> tuple[str, bool]:
    """Describe annotation provenance without changing reply delivery.

    Model JSON may legitimately contain an empty metadata object. Host fallback
    and recovery objects, however, only count as host annotations when they
    carry an actual memory signal; their template defaults must not masquerade
    as accepted model output.
    """

    if not isinstance(payload, dict) or "memory_metadata" not in payload:
        return "missing", False
    metadata = payload.get("memory_metadata")
    if not isinstance(metadata, dict):
        return "invalid", True
    if require_signal and not _memory_metadata_has_signal(metadata):
        return "missing", True
    return accepted_status, True


def _memory_metadata_has_signal(metadata: dict[str, Any]) -> bool:
    return memcore_metadata_has_signal(metadata)


class JSONParsingMixin:
    """Methods use the state initialized by LLMRuntime."""

    def _try_extract_stream_tool_call(self, text: str) -> tuple[str, dict[str, Any] | None]:
        raw = str(text or "")
        length = len(raw)
        idx = self._skip_json_ws(raw, 0)
        if idx >= length:
            return "pending", None
        if raw[idx] != "{":
            return "none", None
        decoder = json.JSONDecoder()
        idx += 1

        while True:
            idx = self._skip_json_ws(raw, idx)
            if idx >= length:
                return "pending", None
            if raw[idx] == "}":
                return "none", None
            if raw[idx] != '"':
                return "pending", None
            try:
                key, key_end = decoder.raw_decode(raw, idx)
            except json.JSONDecodeError:
                return "pending", None
            if not isinstance(key, str):
                return "none", None
            idx = self._skip_json_ws(raw, key_end)
            if idx >= length:
                return "pending", None
            if raw[idx] != ":":
                return "pending", None
            idx = self._skip_json_ws(raw, idx + 1)
            if idx >= length:
                return "pending", None
            try:
                value, value_end = decoder.raw_decode(raw, idx)
            except json.JSONDecodeError:
                return "pending", None
            if key == "tool_call":
                if value is None:
                    return "null", None
                return ("object", value) if isinstance(value, dict) else ("none", None)
            idx = self._skip_json_ws(raw, value_end)
            if idx >= length:
                return "pending", None
            if raw[idx] == ",":
                idx += 1
                continue
            if raw[idx] == "}":
                return "none", None
            return "pending", None

    def _try_extract_leading_tool_call(self, text: str) -> tuple[str, dict[str, Any] | None]:
        return self._try_extract_stream_tool_call(text)

    def _skip_json_ws(self, text: str, start: int) -> int:
        idx = int(start)
        while idx < len(text) and text[idx] in " \t\r\n":
            idx += 1
        return idx

    def _find_json_value_end(self, text: str, start: int) -> int | None:
        depth = 0
        in_string = False
        escape_pending = False
        for idx in range(int(start), len(text)):
            char = text[idx]
            if in_string:
                if escape_pending:
                    escape_pending = False
                elif char == "\\":
                    escape_pending = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
                continue
            if char in "{[":
                depth += 1
                continue
            if char in "}]":
                depth -= 1
                if depth == 0:
                    return idx + 1
                if depth < 0:
                    return None
        return None

    def _extract_json(self, text: str) -> dict[str, Any] | None:
        raw = str(text or "").strip()
        if not raw:
            return None
        try:
            payload = json.loads(raw)
            return payload if isinstance(payload, dict) else None
        except Exception:
            pass
        parsed = self._extract_first_json_object(raw)
        if isinstance(parsed, dict):
            return parsed
        match = JSON_RE.search(raw)
        if not match:
            repaired = self._repair_json(raw)
            return repaired
        try:
            payload = json.loads(match.group(0))
            return payload if isinstance(payload, dict) else None
        except Exception:
            repaired = self._repair_json(match.group(0))
            return repaired

    def _repair_json(self, text: str) -> dict[str, Any] | None:
        try:
            import json_repair

            repaired = json_repair.repair_json(text, return_objects=True)
            return repaired if isinstance(repaired, dict) else None
        except Exception:
            return None

    def _extract_first_json_object(self, text: str) -> dict[str, Any] | None:
        raw = str(text or "")
        decoder = json.JSONDecoder()
        for match in re.finditer(r"\{", raw):
            start = match.start()
            prefix = raw[:start].rstrip()
            if prefix and prefix[-1] in {":", "[", ","}:
                continue
            try:
                payload, _end = decoder.raw_decode(raw[start:])
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        return None

    def _recover_partial_chat_json(self, text: str, *, fallback: dict[str, Any]) -> dict[str, Any] | None:
        tap = _TopLevelJSONStreamTap()
        tap.feed(text)
        speech = str(tap.latest_speech or "").strip()
        emotion = str(tap.latest_emotion or "").strip()
        if not speech and not emotion:
            return None
        recovered = dict(fallback)
        if tap._units_stream.active:
            from ..speech_units import project_speech_units
            recovered = project_speech_units({**recovered, "utterances": tap._units_stream.snapshot_units()})
        if emotion:
            recovered["emotion"] = emotion
        if speech:
            recovered["speech"] = speech
        recovered.pop("speech_segments", None)
        return recovered

    def _extract_json_key_value(self, text: str, key: str) -> Any:
        raw = str(text or "")
        key_text = str(key or "").strip()
        if not key_text:
            return None
        pattern = re.compile(r'"' + re.escape(key_text) + r'"\s*:\s*')
        for match in pattern.finditer(raw):
            start = self._skip_json_ws(raw, match.end())
            if start >= len(raw):
                continue
            end = self._find_json_value_end(raw, start)
            if end is None:
                continue
            try:
                return json.loads(raw[start:end])
            except Exception:
                continue
        return None
