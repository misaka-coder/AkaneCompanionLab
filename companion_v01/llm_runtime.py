from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from typing import Any, Callable, Generator

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
        self._ui_emitted = False

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
            else:
                if self.captured_value_key == "emotion":
                    self.latest_emotion = text
                    if text and not self._ui_emitted:
                        self._ui_emitted = True
                        events.append({"type": "ui", "emotion": text})
                self.expecting_value = False
                self.captured_value_key = None
            self.string_role = ""
            self.string_buffer = []
            return

        self._append_string_char(char, speech_delta)

    def _append_string_char(self, char: str, speech_delta: list[str]) -> None:
        self.string_buffer.append(char)
        if self.string_role == "value" and self.captured_value_key == "speech":
            speech_delta.append(char)

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
    ) -> dict[str, Any]:
        self._record_metric("aux_json_calls")
        return self._call_json(bundle=self.aux, system_prompt=system_prompt, user_prompt=user_prompt, fallback=fallback, temperature=temperature)

    def call_chat_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        fallback: dict[str, Any],
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        self._record_metric("chat_json_calls")
        return self._call_json(bundle=self.chat, system_prompt=system_prompt, user_prompt=user_prompt, fallback=fallback, temperature=temperature)

    def call_aux_ndjson(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        on_event: Callable[[dict[str, Any]], bool] | None = None,
        temperature: float = 0.2,
    ) -> NDJSONCallResult:
        self._record_metric("aux_ndjson_calls")
        return self._call_ndjson(
            bundle=self.aux,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            on_event=on_event,
            temperature=temperature,
        )

    def stream_chat_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        fallback: dict[str, Any],
        temperature: float = 0.7,
    ) -> Generator[dict[str, Any], None, ChatJSONStreamResult]:
        self._record_metric("chat_stream_calls")
        return self._stream_chat_json(
            bundle=self.chat,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback=fallback,
            temperature=temperature,
        )

    def _call_json(
        self,
        *,
        bundle: ModelBundle,
        system_prompt: str,
        user_prompt: str,
        fallback: dict[str, Any],
        temperature: float,
    ) -> dict[str, Any]:
        try:
            response = bundle.client.chat.completions.create(
                **self._build_completion_kwargs(
                    bundle=bundle,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=temperature,
                    json_mode=True,
                )
            )
            content = self._extract_text(response)
            parsed = self._extract_json(content)
            if isinstance(parsed, dict):
                return parsed
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
            response = bundle.client.chat.completions.create(
                model=bundle.model,
                temperature=temperature,
                stream=True,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
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
    ) -> Generator[dict[str, Any], None, ChatJSONStreamResult]:
        import time

        response: Any = None
        error = ""
        raw_parts: list[str] = []
        tap = _TopLevelJSONStreamTap()
        start_at = time.perf_counter()
        try:
            response = bundle.client.chat.completions.create(
                **self._build_completion_kwargs(
                    bundle=bundle,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=temperature,
                    stream=True,
                    json_mode=True,
                )
            )
            for chunk in response:
                text = self._extract_stream_text(chunk)
                if not text:
                    continue
                raw_parts.append(text)
                for event in tap.feed(text):
                    yield event
        except Exception as exc:
            error = str(exc or "").strip()
            self._record_metric("errors")
        finally:
            self._close_stream(response)

        raw_text = "".join(raw_parts)
        parsed = self._extract_json(raw_text)
        if not isinstance(parsed, dict):
            parsed = dict(fallback)
        else:
            parsed = dict(parsed)

        if tap.latest_emotion and not parsed.get("emotion"):
            parsed["emotion"] = tap.latest_emotion
        if tap.latest_speech and not parsed.get("speech"):
            parsed["speech"] = tap.latest_speech

        elapsed_ms = round((time.perf_counter() - start_at) * 1000, 1)
        return ChatJSONStreamResult(
            parsed=parsed,
            raw_text=raw_text,
            elapsed_ms=elapsed_ms,
            error=error,
            latest_emotion=tap.latest_emotion,
            latest_speech=tap.latest_speech,
        )

    def _extract_text(self, response: Any) -> str:
        try:
            return str(response.choices[0].message.content or "").strip()
        except Exception:
            return ""

    def _build_completion_kwargs(
        self,
        *,
        bundle: ModelBundle,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        stream: bool = False,
        json_mode: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": bundle.model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if stream:
            payload["stream"] = True
        if json_mode and self._should_use_response_json_mode(bundle):
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _should_use_response_json_mode(self, bundle: ModelBundle) -> bool:
        protocol = str(getattr(bundle.client, "_akane_protocol", getattr(bundle.client, "protocol", "")) or "").strip().lower()
        return protocol == "ollama"

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
        match = JSON_RE.search(raw)
        if not match:
            return None
        try:
            payload = json.loads(match.group(0))
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None
