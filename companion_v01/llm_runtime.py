from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from typing import Any, Callable, Generator
from urllib.parse import urlparse

import config
from services.llm_client import build_llm_client


JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
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
REPLY_MEDIUM_ALIASES = {
    "text": "text",
    "文字": "text",
    "文本": "text",
    "voice": "voice",
    "audio": "voice",
    "record": "voice",
    "语音": "voice",
    "both": "both",
    "all": "both",
    "text_voice": "both",
    "voice_text": "both",
    "文字语音": "both",
    "双发": "both",
}


def normalize_reply_medium(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    if not text:
        return ""
    return REPLY_MEDIUM_ALIASES.get(text, "")


@dataclass
class ModelBundle:
    client: Any
    model: str


@dataclass
class NDJSONCallResult:
    events: list[dict[str, Any]]
    event_timings: list[dict[str, Any]]
    elapsed_ms: float
    stopped_early: bool
    completed_stream: bool
    stop_event_type: str
    error: str


@dataclass
class ChatJSONStreamResult:
    parsed: dict[str, Any]
    raw_text: str
    elapsed_ms: float
    error: str
    latest_emotion: str
    latest_speech: str
    latest_reply_medium: str
    stopped_early: bool = False
    early_tool_call: dict[str, Any] | None = None


class _TopLevelJSONStreamTap:
    def __init__(self):
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
        self.latest_reply_medium = ""
        self._ui_emitted = False
        self._delivery_hint_emitted = False
        self._speech_segment_count = 0
        self._last_segment_end = 0
        self._max_speech_segments = 3
        self._speech_segments_array_depth: int | None = None
        self._emitted_speech_segment_keys: set[str] = set()
        self._top_level_speech_seen = False
        self._latest_speech_segments: list[str] = []

    def feed(self, text: Any) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        speech_delta: list[str] = []
        for char in str(text or ""):
            if self.in_string:
                self._consume_string_char(char, events, speech_delta)
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

            if self._speech_segments_array_depth is not None:
                if char == '"' and self.depth == self._speech_segments_array_depth:
                    self._start_string("speech_segment")
                    continue
                if char == "]" and self.depth == self._speech_segments_array_depth:
                    self._speech_segments_array_depth = None
                    self.depth = max(0, self.depth - 1)
                    continue

            if char == "{":
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
                    if self.current_key == "speech_segments" and self.depth == 2:
                        self._speech_segments_array_depth = self.depth
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

        if speech_delta:
            delta_text = "".join(speech_delta)
            if delta_text:
                self.latest_speech += delta_text
                events.append({"type": "speech_chunk", "text": delta_text})
                if self._speech_segment_count < self._max_speech_segments:
                    remaining = self.latest_speech[self._last_segment_end:]
                    match = re.search(r"[。！？!?\n]", remaining)
                    if match:
                        end = self._last_segment_end + match.end()
                        self._emit_speech_segment(
                            events,
                            self.latest_speech[self._last_segment_end:end],
                        )
                        self._last_segment_end = end
        return events

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
        speech_delta: list[str],
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
                        self._append_string_char(decoded, speech_delta)
                return

            self.unicode_buffer = None
            self._append_string_char("u", speech_delta)

        if self.escape_pending:
            self.escape_pending = False
            if char == "u":
                self.unicode_buffer = []
                return
            self._append_string_char(JSON_ESCAPE_MAP.get(char, char), speech_delta)
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
            elif self.string_role == "speech_segment":
                segment_text = self._normalize_speech_segment_text(text)
                if segment_text:
                    self._latest_speech_segments.append(segment_text)
                    if not self._top_level_speech_seen:
                        self.latest_speech = "\n".join(self._latest_speech_segments)
                    self._emit_speech_segment(events, segment_text)
            else:
                if self.captured_value_key == "emotion":
                    self.latest_emotion = text
                    if text and not self._ui_emitted:
                        self._ui_emitted = True
                        events.append({"type": "ui", "emotion": text})
                elif self.captured_value_key == "reply_medium":
                    medium = normalize_reply_medium(text)
                    if medium:
                        self.latest_reply_medium = medium
                        if not self._delivery_hint_emitted:
                            self._delivery_hint_emitted = True
                            events.append({"type": "delivery_hint", "medium": medium})
                self.expecting_value = False
                self.captured_value_key = None
            self.string_role = ""
            self.string_buffer = []
            return

        self._append_string_char(char, speech_delta)

    def _append_string_char(self, char: str, speech_delta: list[str]) -> None:
        self.string_buffer.append(char)
        if self.string_role == "value" and self.captured_value_key == "speech":
            if not self._top_level_speech_seen and self._latest_speech_segments:
                self.latest_speech = ""
            self._top_level_speech_seen = True
            speech_delta.append(char)

    def _emit_speech_segment(self, events: list[dict[str, Any]], text: str) -> bool:
        if self._speech_segment_count >= self._max_speech_segments:
            return False
        segment_text = self._normalize_speech_segment_text(text)
        if not segment_text:
            return False
        key = re.sub(r"\s+", "", segment_text)
        if not key or key in self._emitted_speech_segment_keys:
            return False
        self._emitted_speech_segment_keys.add(key)
        self._speech_segment_count += 1
        events.append({
            "type": "speech_segment",
            "index": self._speech_segment_count - 1,
            "text": segment_text,
        })
        return True

    def _normalize_speech_segment_text(self, text: Any) -> str:
        return " ".join(str(text or "").replace("\r\n", "\n").replace("\r", "\n").splitlines()).strip()

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


class LLMRuntime:
    def __init__(self):
        self.aux = self._build_aux_bundle()
        self.chat = self._build_chat_bundle()
        self._metrics_lock = threading.RLock()
        self._metrics = {
            "aux_json_calls": 0,
            "chat_json_calls": 0,
            "aux_ndjson_calls": 0,
            "chat_stream_calls": 0,
            "errors": 0,
        }

    def _build_aux_bundle(self) -> ModelBundle:
        client = build_llm_client(
            api_key=config.AUX_API_KEY,
            base_url=config.AUX_BASE_URL,
            protocol=getattr(config, "AUX_API_PROTOCOL", "auto"),
            timeout=90.0,
            max_retries=0,
        )
        return ModelBundle(client=client, model=config.AUX_MODEL_NAME)

    def _build_chat_bundle(self) -> ModelBundle:
        client = build_llm_client(
            api_key=config.CHAT_API_KEY,
            base_url=config.CHAT_BASE_URL,
            protocol=getattr(config, "CHAT_API_PROTOCOL", "auto"),
            timeout=120.0,
            max_retries=0,
        )
        return ModelBundle(client=client, model=config.CHAT_MODEL_NAME)

    def call_aux_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        fallback: dict[str, Any],
        temperature: float = 0.2,
        prompt_cache_key: str = "",
    ) -> dict[str, Any]:
        self._record_metric("aux_json_calls")
        return self._call_json(
            bundle=self.aux,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback=fallback,
            temperature=temperature,
            prompt_cache_key=prompt_cache_key,
        )

    def call_chat_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        fallback: dict[str, Any],
        temperature: float = 0.7,
        prompt_cache_key: str = "",
        user_images: list[dict[str, Any]] | None = None,
        native_tools: list[dict[str, Any]] | None = None,
        native_tool_choice: Any = "",
    ) -> dict[str, Any]:
        self._record_metric("chat_json_calls")
        return self._call_json(
            bundle=self.chat,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback=fallback,
            temperature=temperature,
            prompt_cache_key=prompt_cache_key,
            user_images=user_images,
            native_tools=native_tools,
            native_tool_choice=native_tool_choice,
        )

    def call_aux_ndjson(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        on_event: Callable[[dict[str, Any]], bool] | None = None,
        temperature: float = 0.2,
        prompt_cache_key: str = "",
    ) -> NDJSONCallResult:
        self._record_metric("aux_ndjson_calls")
        return self._call_ndjson(
            bundle=self.aux,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            on_event=on_event,
            temperature=temperature,
            prompt_cache_key=prompt_cache_key,
        )

    def stream_chat_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        fallback: dict[str, Any],
        temperature: float = 0.7,
        early_tool_call_validator: Callable[[dict[str, Any]], bool] | None = None,
        prompt_cache_key: str = "",
        user_images: list[dict[str, Any]] | None = None,
    ) -> Generator[dict[str, Any], None, ChatJSONStreamResult]:
        self._record_metric("chat_stream_calls")
        return self._stream_chat_json(
            bundle=self.chat,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback=fallback,
            temperature=temperature,
            early_tool_call_validator=early_tool_call_validator,
            prompt_cache_key=prompt_cache_key,
            user_images=user_images,
        )

    def _call_json(
        self,
        *,
        bundle: ModelBundle,
        system_prompt: str,
        user_prompt: str,
        fallback: dict[str, Any],
        temperature: float,
        prompt_cache_key: str,
        user_images: list[dict[str, Any]] | None = None,
        native_tools: list[dict[str, Any]] | None = None,
        native_tool_choice: Any = "",
    ) -> dict[str, Any]:
        try:
            response = self._create_completion(
                bundle=bundle,
                payload=self._build_completion_kwargs(
                    bundle=bundle,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=temperature,
                    json_mode=True,
                    prompt_cache_key=prompt_cache_key,
                    user_images=user_images,
                    native_tools=native_tools,
                    native_tool_choice=native_tool_choice,
                ),
            )
            native_tool_call = self._extract_native_tool_call(response)
            if native_tool_call is not None:
                return {"tool_call": native_tool_call}
            content = self._extract_text(response)
            parsed = self._extract_json(content)
            if isinstance(parsed, dict):
                return parsed
            recovered = self._recover_partial_chat_json(content, fallback=fallback)
            if isinstance(recovered, dict):
                return recovered
        except Exception:
            self._record_metric("errors")
            pass
        return dict(fallback)

    def _call_ndjson(
        self,
        *,
        bundle: ModelBundle,
        system_prompt: str,
        user_prompt: str,
        on_event: Callable[[dict[str, Any]], bool] | None,
        temperature: float,
        prompt_cache_key: str,
    ) -> NDJSONCallResult:
        import time

        events: list[dict[str, Any]] = []
        event_timings: list[dict[str, Any]] = []
        response: Any = None
        buffer = ""
        start_at = time.perf_counter()
        stopped_early = False
        stop_event_type = ""
        error = ""
        try:
            response = self._create_completion(
                bundle=bundle,
                payload=self._build_completion_kwargs(
                    bundle=bundle,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=temperature,
                    stream=True,
                    prompt_cache_key=prompt_cache_key,
                ),
            )
            for chunk in response:
                text = self._extract_stream_text(chunk)
                if not text:
                    continue
                buffer += text
                buffer, parsed_events = self._drain_ndjson_buffer(buffer)
                for event in parsed_events:
                    events.append(event)
                    event_type = str(event.get("type") or "").strip().lower()
                    event_timings.append(
                        {
                            "index": len(events) - 1,
                            "type": event_type or "unknown",
                            "elapsed_ms": round((time.perf_counter() - start_at) * 1000, 1),
                        }
                    )
                    if on_event and on_event(event):
                        stopped_early = True
                        stop_event_type = event_type or "unknown"
                        break
                if stopped_early:
                    break

            tail_event = self._parse_ndjson_line(buffer) if not stopped_early else None
            if tail_event is not None:
                events.append(tail_event)
                event_type = str(tail_event.get("type") or "").strip().lower()
                event_timings.append(
                    {
                        "index": len(events) - 1,
                        "type": event_type or "unknown",
                        "elapsed_ms": round((time.perf_counter() - start_at) * 1000, 1),
                    }
                )
                if on_event:
                    if on_event(tail_event):
                        stopped_early = True
                        stop_event_type = event_type or "unknown"
        except Exception as exc:
            error = str(exc or "").strip()
            self._record_metric("errors")
        finally:
            self._close_stream(response)
        elapsed_ms = round((time.perf_counter() - start_at) * 1000, 1)
        return NDJSONCallResult(
            events=events,
            event_timings=event_timings,
            elapsed_ms=elapsed_ms,
            stopped_early=stopped_early,
            completed_stream=(not stopped_early and not error),
            stop_event_type=stop_event_type,
            error=error,
        )

    def _stream_chat_json(
        self,
        *,
        bundle: ModelBundle,
        system_prompt: str,
        user_prompt: str,
        fallback: dict[str, Any],
        temperature: float,
        early_tool_call_validator: Callable[[dict[str, Any]], bool] | None,
        prompt_cache_key: str,
        user_images: list[dict[str, Any]] | None = None,
    ) -> Generator[dict[str, Any], None, ChatJSONStreamResult]:
        import time

        response: Any = None
        error = ""
        raw_parts: list[str] = []
        tap = _TopLevelJSONStreamTap()
        start_at = time.perf_counter()
        stopped_early = False
        early_tool_call: dict[str, Any] | None = None
        tool_probe_disabled = False
        try:
            response = self._create_completion(
                bundle=bundle,
                payload=self._build_completion_kwargs(
                    bundle=bundle,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=temperature,
                    stream=True,
                    json_mode=True,
                    prompt_cache_key=prompt_cache_key,
                    user_images=user_images,
                ),
            )
            for chunk in response:
                text = self._extract_stream_text(chunk)
                if not text:
                    continue
                raw_parts.append(text)
                for event in tap.feed(text):
                    yield event
                if not tool_probe_disabled:
                    probe_state, probe_call = self._try_extract_leading_tool_call("".join(raw_parts))
                    if probe_state == "object" and isinstance(probe_call, dict):
                        if early_tool_call_validator is None or early_tool_call_validator(probe_call):
                            early_tool_call = dict(probe_call)
                            stopped_early = True
                            break
                        tool_probe_disabled = True
                    elif probe_state in {"null", "none"}:
                        tool_probe_disabled = True
                if stopped_early:
                    break
        except Exception as exc:
            error = str(exc or "").strip()
            self._record_metric("errors")
        finally:
            self._close_stream(response)

        raw_text = "".join(raw_parts)
        if early_tool_call is not None:
            parsed = {"tool_call": early_tool_call}
        else:
            parsed = self._extract_json(raw_text)
        if not isinstance(parsed, dict):
            parsed = dict(fallback)
        else:
            parsed = dict(parsed)

        if tap.latest_emotion and not parsed.get("emotion"):
            parsed["emotion"] = tap.latest_emotion
        if tap.latest_speech and not parsed.get("speech"):
            parsed["speech"] = tap.latest_speech
        if tap.latest_reply_medium and not parsed.get("reply_medium"):
            parsed["reply_medium"] = tap.latest_reply_medium

        elapsed_ms = round((time.perf_counter() - start_at) * 1000, 1)
        return ChatJSONStreamResult(
            parsed=parsed,
            raw_text=raw_text,
            elapsed_ms=elapsed_ms,
            error=error,
            latest_emotion=tap.latest_emotion,
            latest_speech=tap.latest_speech,
            latest_reply_medium=tap.latest_reply_medium,
            stopped_early=stopped_early,
            early_tool_call=early_tool_call,
        )

    def _try_extract_leading_tool_call(self, text: str) -> tuple[str, dict[str, Any] | None]:
        raw = str(text or "")
        length = len(raw)
        idx = self._skip_json_ws(raw, 0)
        if idx >= length:
            return "pending", None
        if raw[idx] != "{":
            return "none", None
        idx = self._skip_json_ws(raw, idx + 1)
        if idx >= length:
            return "pending", None
        if raw[idx] != '"':
            return "pending", None
        decoder = json.JSONDecoder()
        try:
            key, key_end = decoder.raw_decode(raw, idx)
        except json.JSONDecodeError:
            return "pending", None
        if key != "tool_call":
            return "none", None
        idx = self._skip_json_ws(raw, key_end)
        if idx >= length:
            return "pending", None
        if raw[idx] != ":":
            return "pending", None
        idx = self._skip_json_ws(raw, idx + 1)
        if idx >= length:
            return "pending", None
        if raw.startswith("null", idx):
            return "null", None
        if "null".startswith(raw[idx: min(length, idx + 4)]):
            return "pending", None
        if raw[idx] != "{":
            return "none", None
        value_end = self._find_json_value_end(raw, idx)
        if value_end is None:
            return "pending", None
        try:
            value = json.loads(raw[idx:value_end])
        except Exception:
            return "none", None
        return ("object", value) if isinstance(value, dict) else ("none", None)

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

    def _extract_text(self, response: Any) -> str:
        try:
            return self._flatten_message_content(response.choices[0].message.content).strip()
        except Exception:
            return ""

    def _flatten_message_content(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if text is None:
                        text = item.get("content")
                    if text is None and isinstance(item.get("input_text"), str):
                        text = item.get("input_text")
                else:
                    text = getattr(item, "text", None)
                    if text is None:
                        text = getattr(item, "content", None)
                if text is not None:
                    parts.append(str(text))
            return "".join(parts)
        return str(content or "")

    def _build_completion_kwargs(
        self,
        *,
        bundle: ModelBundle,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        stream: bool = False,
        json_mode: bool = False,
        prompt_cache_key: str = "",
        user_images: list[dict[str, Any]] | None = None,
        native_tools: list[dict[str, Any]] | None = None,
        native_tool_choice: Any = "",
    ) -> dict[str, Any]:
        user_content: str | list[dict[str, Any]]
        image_items = self._normalize_user_image_items(user_images)
        if image_items:
            user_content = [{"type": "text", "text": user_prompt}, *image_items]
        else:
            user_content = user_prompt
        payload: dict[str, Any] = {
            "model": bundle.model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        }
        if stream:
            payload["stream"] = True
        if json_mode and self._should_use_response_json_mode(bundle):
            payload["response_format"] = {"type": "json_object"}
        normalized_tools = self._normalize_native_tools(native_tools)
        if normalized_tools and self._should_send_native_tools(bundle):
            payload["tools"] = normalized_tools
            tool_choice = self._normalize_native_tool_choice(native_tool_choice)
            if tool_choice:
                payload["tool_choice"] = tool_choice
        payload.update(self._build_reasoning_control_kwargs(bundle=bundle))
        payload.update(
            self._build_prompt_cache_kwargs(
                bundle=bundle,
                prompt_cache_key=prompt_cache_key,
            )
        )
        return payload

    def _should_send_native_tools(self, bundle: ModelBundle) -> bool:
        protocol = str(getattr(bundle.client, "_akane_protocol", getattr(bundle.client, "protocol", "")) or "").strip().lower()
        return protocol == "openai"

    def _normalize_native_tools(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        tools: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in value[:64]:
            if not isinstance(raw, dict):
                continue
            if str(raw.get("type") or "").strip() != "function":
                continue
            function = raw.get("function")
            if not isinstance(function, dict):
                continue
            name = str(function.get("name") or "").strip()
            if not name or name in seen or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
                continue
            parameters = function.get("parameters")
            if not isinstance(parameters, dict):
                parameters = {"type": "object", "additionalProperties": True}
            description = " ".join(str(function.get("description") or "").split())[:900]
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": description or f"Call Akane tool {name}.",
                        "parameters": parameters,
                    },
                }
            )
            seen.add(name)
        return tools

    def _normalize_native_tool_choice(self, value: Any) -> Any:
        if isinstance(value, dict):
            return value
        raw = str(value or "").strip().lower()
        return raw if raw in {"auto", "none", "required"} else ""

    def _extract_native_tool_call(self, response: Any) -> dict[str, Any] | None:
        try:
            message = response.choices[0].message
        except Exception:
            return None
        tool_calls = self._get_attr_or_key(message, "tool_calls")
        if not isinstance(tool_calls, list) or not tool_calls:
            return None
        first = tool_calls[0]
        function = self._get_attr_or_key(first, "function")
        if not isinstance(function, dict):
            function = {
                "name": self._get_attr_or_key(function, "name"),
                "arguments": self._get_attr_or_key(function, "arguments"),
            }
        name = str(function.get("name") or "").strip()
        if not name or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
            return None
        arguments = self._decode_native_tool_arguments(function.get("arguments"))
        return {**arguments, "type": name}

    def _decode_native_tool_arguments(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        raw = str(value or "").strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except Exception:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}

    def _get_attr_or_key(self, value: Any, key: str) -> Any:
        if isinstance(value, dict):
            return value.get(key)
        return getattr(value, key, None)

    def _build_reasoning_control_kwargs(self, *, bundle: ModelBundle) -> dict[str, Any]:
        mode = str(getattr(config, "LLM_THINKING_MODE", "disabled") or "").strip().lower()
        if mode in {"", "default", "auto"}:
            return {}
        if mode not in {"enabled", "disabled"}:
            return {}
        if not self._supports_deepseek_thinking_control(bundle):
            return {}
        return {"extra_body": {"thinking": {"type": mode}}}

    def _supports_deepseek_thinking_control(self, bundle: ModelBundle) -> bool:
        protocol = str(getattr(bundle.client, "_akane_protocol", getattr(bundle.client, "protocol", "")) or "").strip().lower()
        if protocol != "openai":
            return False
        model = str(getattr(bundle, "model", "") or "").strip().lower()
        if model.startswith("deepseek-"):
            return True
        base_url = str(getattr(bundle.client, "base_url", "") or "").strip()
        try:
            hostname = str(urlparse(base_url).hostname or "").strip().lower()
        except Exception:
            hostname = ""
        return hostname == "api.deepseek.com" or hostname.endswith(".deepseek.com")

    def _normalize_user_image_items(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        items: list[dict[str, Any]] = []
        for raw in value[:5]:
            if not isinstance(raw, dict):
                continue
            url = str(raw.get("data_url") or raw.get("dataUrl") or raw.get("url") or "").strip()
            if not url.startswith("data:image/"):
                continue
            items.append({"type": "image_url", "image_url": {"url": url}})
        return items

    def _should_use_response_json_mode(self, bundle: ModelBundle) -> bool:
        protocol = str(getattr(bundle.client, "_akane_protocol", getattr(bundle.client, "protocol", "")) or "").strip().lower()
        return protocol == "ollama"

    def _build_prompt_cache_kwargs(
        self,
        *,
        bundle: ModelBundle,
        prompt_cache_key: str,
    ) -> dict[str, Any]:
        if not self._should_send_prompt_cache_hints(bundle):
            return {}

        payload: dict[str, Any] = {}
        normalized_key = self._normalize_prompt_cache_key(prompt_cache_key)
        normalized_retention = self._normalize_prompt_cache_retention(getattr(config, "PROMPT_CACHE_RETENTION", ""))
        if normalized_key:
            payload["prompt_cache_key"] = normalized_key
        if normalized_retention:
            payload["prompt_cache_retention"] = normalized_retention
        return payload

    def _should_send_prompt_cache_hints(self, bundle: ModelBundle) -> bool:
        if not bool(getattr(config, "PROMPT_CACHE_HINTS_ENABLED", True)):
            return False
        protocol = str(getattr(bundle.client, "_akane_protocol", getattr(bundle.client, "protocol", "")) or "").strip().lower()
        if protocol != "openai":
            return False
        if bool(getattr(config, "PROMPT_CACHE_HINTS_FORCE", False)):
            return True
        base_url = str(getattr(bundle.client, "base_url", "") or "").strip()
        return self._looks_like_official_openai_base_url(base_url)

    def _looks_like_official_openai_base_url(self, base_url: str) -> bool:
        raw = str(base_url or "").strip()
        if not raw:
            return True
        try:
            parsed = urlparse(raw)
        except Exception:
            return False
        hostname = str(parsed.hostname or "").strip().lower()
        if not hostname:
            return False
        return hostname == "api.openai.com" or hostname.endswith(".openai.com")

    def _normalize_prompt_cache_key(self, prompt_cache_key: Any) -> str:
        raw = str(prompt_cache_key or "").strip().strip(":")
        if not raw:
            return ""
        namespace = str(getattr(config, "PROMPT_CACHE_NAMESPACE", "akane") or "").strip().strip(":")
        return f"{namespace}:{raw}" if namespace else raw

    def _normalize_prompt_cache_retention(self, value: Any) -> str:
        raw = str(value or "").strip().lower()
        return raw if raw in {"in_memory", "24h"} else ""

    def _create_completion(self, *, bundle: ModelBundle, payload: dict[str, Any]) -> Any:
        try:
            return bundle.client.chat.completions.create(**payload)
        except TypeError:
            stripped = self._without_prompt_cache_hints(payload)
            if stripped != payload:
                return bundle.client.chat.completions.create(**stripped)
            raise
        except Exception as exc:
            if self._should_retry_without_prompt_cache_hints(exc):
                stripped = self._without_prompt_cache_hints(payload)
                if stripped != payload:
                    return bundle.client.chat.completions.create(**stripped)
            raise

    def _without_prompt_cache_hints(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "prompt_cache_key" not in payload and "prompt_cache_retention" not in payload:
            return dict(payload)
        sanitized = dict(payload)
        sanitized.pop("prompt_cache_key", None)
        sanitized.pop("prompt_cache_retention", None)
        return sanitized

    def _should_retry_without_prompt_cache_hints(self, exc: Exception) -> bool:
        message = str(exc or "").strip().lower()
        if not message:
            return False
        return (
            "prompt_cache_key" in message
            or "prompt_cache_retention" in message
            or "unexpected keyword" in message
            or "extra_forbidden" in message
            or "unknown parameter" in message
            or "unrecognized request argument" in message
        )

    def _extract_stream_text(self, chunk: Any) -> str:
        try:
            choice = chunk.choices[0]
        except Exception:
            return ""

        delta = getattr(choice, "delta", None)
        if delta is None and isinstance(choice, dict):
            delta = choice.get("delta")

        if isinstance(delta, dict):
            content = delta.get("content")
        else:
            content = getattr(delta, "content", None)

        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = str(item.get("text") or item.get("content") or "").strip()
                else:
                    text = str(getattr(item, "text", "") or getattr(item, "content", "") or "").strip()
                if text:
                    parts.append(text)
            return "".join(parts)
        return str(content or "")

    def _drain_ndjson_buffer(self, buffer: str) -> tuple[str, list[dict[str, Any]]]:
        remaining = str(buffer or "")
        events: list[dict[str, Any]] = []
        while True:
            newline_index = remaining.find("\n")
            if newline_index < 0:
                break
            line = remaining[:newline_index]
            remaining = remaining[newline_index + 1 :]
            parsed = self._parse_ndjson_line(line)
            if parsed is not None:
                events.append(parsed)
        return remaining, events

    def _parse_ndjson_line(self, line: Any) -> dict[str, Any] | None:
        raw = str(line or "").strip()
        if not raw:
            return None
        try:
            payload = json.loads(raw)
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None

    def _close_stream(self, response: Any) -> None:
        if response is None:
            return
        close = getattr(response, "close", None)
        if callable(close):
            try:
                close()
                return
            except Exception:
                pass
        raw_response = getattr(response, "_response", None)
        close = getattr(raw_response, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    def _record_metric(self, key: str, amount: int = 1) -> None:
        with self._metrics_lock:
            self._metrics[key] = int(self._metrics.get(key, 0)) + int(amount)

    def snapshot_metrics(self) -> dict[str, int]:
        with self._metrics_lock:
            return {key: int(value) for key, value in self._metrics.items()}

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
            return None
        try:
            payload = json.loads(match.group(0))
            return payload if isinstance(payload, dict) else None
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
        reply_medium = normalize_reply_medium(tap.latest_reply_medium)
        raw_segments = self._extract_json_key_value(text, "speech_segments")
        segments: list[str] = []
        if isinstance(raw_segments, list):
            for item in raw_segments:
                value = (item.get("speech") or item.get("text") or "") if isinstance(item, dict) else item
                segment = " ".join(str(value or "").replace("\r\n", "\n").replace("\r", "\n").splitlines()).strip()
                if segment:
                    segments.append(segment[:500])
                if len(segments) >= 3:
                    break
        if not speech and not emotion and not segments and not reply_medium:
            return None
        recovered = dict(fallback)
        if emotion:
            recovered["emotion"] = emotion
        if reply_medium:
            recovered["reply_medium"] = reply_medium
        if segments:
            recovered["speech"] = "\n".join(segments)
            recovered["speech_segments"] = segments
        elif speech:
            recovered["speech"] = speech
            recovered["speech_segments"] = []
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
