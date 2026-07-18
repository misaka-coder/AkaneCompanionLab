from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Generator
from urllib.parse import urlparse

import config
from capcore_provider_anthropic import (
    collect_anthropic_messages_stream_tool_use_delta,
    parse_anthropic_messages_stream_tool_uses,
    parse_anthropic_messages_tool_uses,
)
from capcore_provider_openai import (
    collect_openai_chat_stream_tool_call_delta,
    parse_openai_chat_stream_tool_calls,
    parse_openai_chat_tool_calls,
)
from services.llm_client import build_llm_client
from .native_tool_schema import NATIVE_TOOL_CAPABILITY_ID_FIELD
from .tool_invocation import NATIVE_ANTHROPIC
from .tool_invocation import NATIVE_OPENAI
from .tool_invocation import NATIVE_TOOL_CALL_FIELD
from .tool_invocation import NATIVE_TOOL_CALLS_FIELD
from .tool_invocation import TOOL_MODEL_NAME_FIELD
from .tool_invocation import TOOL_INVOCATION_ID_FIELD
from .tool_invocation import TOOL_SOURCE_FIELD


logger = logging.getLogger("akane.llm_runtime")


JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
SECRET_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._~+/=-]{8,})"),
    re.compile(r"(?i)((?:api[_-]?key|authorization|x-api-key)\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
)
PROMPT_AUDIT_LOCK = threading.RLock()
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


def _safe_chat_model_override(value: Any) -> str:
    text = str(value or "").strip()
    text = text.strip('`\'"""‘’')
    text = text.rstrip("。.!！?？,，;；")
    text = re.sub(r"\s+", "", text)
    if not text or len(text) > 180:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,179}", text):
        return ""
    return text


@dataclass
class ModelBundle:
    client: Any
    model: str


@dataclass(frozen=True)
class ProviderToolProfile:
    supports_native_tools: bool = False
    native_tools_coexist_with_forced_json: bool = False
    native_call_shape: str = "openai_tool_calls"
    verified: bool = False
    notes: str = ""


DEFAULT_PROVIDER_TOOL_PROFILE = ProviderToolProfile()
CONFIG_ALLOWLISTED_PROVIDER_TOOL_PROFILE = ProviderToolProfile(
    supports_native_tools=True,
    native_tools_coexist_with_forced_json=False,
    verified=False,
    notes=(
        "Enabled by NATIVE_TOOL_PROVIDER_ALLOWLIST. Treat as OpenAI-compatible "
        "prompt-only JSON until a live probe verifies response_format coexistence."
    ),
)
PROVIDER_TOOL_PROFILES: dict[tuple[str, str], ProviderToolProfile] = {
    (
        "api.deepseek.com",
        "deepseek-v4-flash",
    ): ProviderToolProfile(
        supports_native_tools=True,
        native_tools_coexist_with_forced_json=False,
        verified=True,
        notes=(
            "Project probes showed inconsistent forced-JSON coexistence for this model; "
            "keep prompt-only native tool rounds until repeated live eval proves forced JSON stable."
        ),
    ),
    (
        "api.deepseek.com",
        "deepseek-v4-pro",
    ): ProviderToolProfile(
        supports_native_tools=True,
        native_tools_coexist_with_forced_json=True,
        verified=True,
        notes="Project probe: tools and response_format=json_object can coexist.",
    ),
}


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
    native_preface_text: str = ""
    stopped_early: bool = False
    early_tool_call: dict[str, Any] | None = None


def _responses_error_summary(value: Any) -> str:
    """Return bounded provider failure metadata without echoing raw payloads."""

    def _get(item: Any, key: str, default: Any = None) -> Any:
        return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)

    nested = _get(value, "error")
    error = nested if nested is not None else value
    fields: list[str] = []
    for key in ("type", "code"):
        raw = str(_get(error, key, "") or "").strip()
        if raw:
            safe = re.sub(r"[^A-Za-z0-9_.:-]+", "_", raw)[:120]
            fields.append(f"{key}={safe}")
    return " ".join(fields) or "reason=provider_reported_failure"


class _ResponsesStreamAdapter:
    """Expose Responses streaming events through the small ChatCompletion
    surface consumed by the existing Akane stream parser.

    This keeps the rest of the engine provider-neutral while still preserving
    function-call ids, incremental arguments and final usage telemetry.
    """

    def __init__(self, raw_stream: Any):
        self._raw_stream = raw_stream
        self.usage: Any = None
        self._tool_indexes: dict[str, int] = {}
        self._argument_deltas_seen: set[int] = set()

    @staticmethod
    def _get(value: Any, key: str, default: Any = None) -> Any:
        return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)

    def _tool_index(self, event: Any, item: Any = None) -> int:
        raw = self._get(event, "output_index", None)
        try:
            return int(raw)
        except Exception:
            pass
        identity = str(
            self._get(item, "call_id", "")
            or self._get(item, "id", "")
            or self._get(event, "item_id", "")
        ).strip()
        if identity not in self._tool_indexes:
            self._tool_indexes[identity] = len(self._tool_indexes)
        return self._tool_indexes[identity]

    @staticmethod
    def _chunk(*, content: str = "", tool_calls: list[dict[str, Any]] | None = None, usage: Any = None,
               finish_reason: str | None = None) -> Any:
        delta = SimpleNamespace(content=content or None, tool_calls=tool_calls or None)
        choice = SimpleNamespace(index=0, delta=delta, finish_reason=finish_reason)
        return SimpleNamespace(choices=[choice], usage=usage)

    def __iter__(self):
        for event in self._raw_stream:
            event_type = str(self._get(event, "type", "") or "")
            if event_type == "response.output_text.delta":
                delta = str(self._get(event, "delta", "") or "")
                if delta:
                    yield self._chunk(content=delta)
                continue
            if event_type == "response.output_item.added":
                item = self._get(event, "item")
                if str(self._get(item, "type", "") or "") != "function_call":
                    continue
                index = self._tool_index(event, item)
                call_id = str(self._get(item, "call_id", "") or self._get(item, "id", "") or "")
                name = str(self._get(item, "name", "") or "")
                yield self._chunk(
                    tool_calls=[{
                        "index": index,
                        "id": call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": ""},
                    }]
                )
                continue
            if event_type == "response.function_call_arguments.delta":
                index = self._tool_index(event)
                delta = str(self._get(event, "delta", "") or "")
                if delta:
                    self._argument_deltas_seen.add(index)
                    yield self._chunk(
                        tool_calls=[{
                            "index": index,
                            "type": "function",
                            "function": {"arguments": delta},
                        }]
                    )
                continue
            if event_type == "response.output_item.done":
                item = self._get(event, "item")
                if str(self._get(item, "type", "") or "") != "function_call":
                    continue
                index = self._tool_index(event, item)
                arguments = str(self._get(item, "arguments", "") or "")
                if arguments and index not in self._argument_deltas_seen:
                    yield self._chunk(
                        tool_calls=[{
                            "index": index,
                            "type": "function",
                            "function": {"arguments": arguments},
                        }]
                    )
                continue
            if event_type in {"error", "response.failed", "response.cancelled"}:
                response = self._get(event, "response")
                detail = response if response is not None else event
                raise RuntimeError(f"responses_stream_failed {_responses_error_summary(detail)}")
            if event_type in {"response.completed", "response.incomplete"}:
                response = self._get(event, "response")
                self.usage = self._get(response, "usage")
                finish_reason = "length" if event_type == "response.incomplete" else "stop"
                yield self._chunk(usage=self.usage, finish_reason=finish_reason)

    def close(self) -> None:
        close = getattr(self._raw_stream, "close", None)
        if callable(close):
            close()


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
                    remaining = self.latest_speech[self._last_segment_end :]
                    match = re.search(r"[。！？!?\n]", remaining)
                    if match:
                        end = self._last_segment_end + match.end()
                        self._emit_speech_segment(
                            events,
                            self.latest_speech[self._last_segment_end : end],
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
        events.append(
            {
                "type": "speech_segment",
                "index": self._speech_segment_count - 1,
                "text": segment_text,
            }
        )
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
    def __init__(self, *, log_dir: Path | str | None = None, instance_id: str = ""):
        self.log_dir = (
            Path(log_dir)
            if log_dir is not None
            else Path(str(getattr(config, "LOG_DIR", "") or "logs"))
        )
        safe_instance_id = str(instance_id or "local-default").strip()
        self.instance_id = (
            safe_instance_id
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", safe_instance_id)
            else "unknown"
        )
        self._bundle_lock = threading.RLock()
        self.aux = self._build_aux_bundle()
        self.chat = self._build_chat_bundle()
        self._metrics_lock = threading.RLock()
        self._metrics = {
            "aux_json_calls": 0,
            "chat_json_calls": 0,
            "aux_ndjson_calls": 0,
            "chat_stream_calls": 0,
            "errors": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "cache_usage_calls": 0,
            "cache_hit_calls": 0,
            "reported_input_tokens": 0,
            "reported_output_tokens": 0,
            "final_cache_read_tokens": 0,
            "final_cache_creation_tokens": 0,
            "final_reported_input_tokens": 0,
            "final_reported_output_tokens": 0,
            "final_cache_usage_calls": 0,
            "final_cache_hit_calls": 0,
            "plugin_proactive_cache_read_tokens": 0,
            "plugin_proactive_cache_creation_tokens": 0,
            "plugin_proactive_reported_input_tokens": 0,
            "plugin_proactive_reported_output_tokens": 0,
            "plugin_proactive_cache_usage_calls": 0,
            "plugin_proactive_cache_hit_calls": 0,
            "chat_json_fallbacks": 0,
            "native_tool_decision_sent": 0,
            "native_tool_provider_unsupported": 0,
            "native_tool_call_extracted": 0,
            "native_tool_calls_extra": 0,
            "native_tool_calls_truncated": 0,
            "native_tool_no_call": 0,
            "native_tool_forced_json_suppressed": 0,
        }
        self._last_error_lock = threading.RLock()
        self._last_error: dict[str, str] = {}

    def reload_from_config(self) -> dict[str, str]:
        aux = self._build_aux_bundle()
        chat = self._build_chat_bundle()
        with self._bundle_lock:
            self.aux = aux
            self.chat = chat
        return {
            "status": "reloaded",
            "auxModel": aux.model,
            "chatModel": chat.model,
        }

    def _build_aux_bundle(self) -> ModelBundle:
        client = build_llm_client(
            api_key=config.AUX_API_KEY,
            base_url=config.AUX_BASE_URL,
            protocol=getattr(config, "AUX_API_PROTOCOL", "auto"),
            timeout=90.0,
            max_retries=0,
        )
        setattr(client, "_akane_bundle_role", "aux")
        return ModelBundle(client=client, model=config.AUX_MODEL_NAME)

    def _build_chat_bundle(self) -> ModelBundle:
        client = build_llm_client(
            api_key=config.CHAT_API_KEY,
            base_url=config.CHAT_BASE_URL,
            protocol=getattr(config, "CHAT_API_PROTOCOL", "auto"),
            timeout=120.0,
            max_retries=0,
        )
        setattr(client, "_akane_bundle_role", "chat")
        return ModelBundle(client=client, model=config.CHAT_MODEL_NAME)

    def _chat_bundle_for_override(self, chat_model_override: str = "") -> ModelBundle:
        model_override = _safe_chat_model_override(chat_model_override)
        with self._bundle_lock:
            bundle = self.chat
        if not model_override:
            return bundle
        return ModelBundle(client=bundle.client, model=model_override)

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
        system_extra_blocks: list[str] | None = None,
        history_turns: list[dict[str, Any]] | None = None,
        post_user_turns: list[dict[str, Any]] | None = None,
        prompt_audit_sections: list[dict[str, Any]] | None = None,
        chat_model_override: str = "",
    ) -> dict[str, Any]:
        self._record_metric("chat_json_calls")
        return self._call_json(
            bundle=self._chat_bundle_for_override(chat_model_override),
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback=fallback,
            temperature=temperature,
            prompt_cache_key=prompt_cache_key,
            user_images=user_images,
            native_tools=native_tools,
            native_tool_choice=native_tool_choice,
            system_extra_blocks=system_extra_blocks,
            history_turns=history_turns,
            post_user_turns=post_user_turns,
            prompt_audit_sections=prompt_audit_sections,
        )

    def chat_supports_native_tools(self, *, chat_model_override: str = "") -> bool:
        bundle = self._chat_bundle_for_override(chat_model_override)
        return self._should_send_native_tools(bundle)

    def record_metric(self, key: str, amount: int = 1) -> None:
        self._record_metric(key, amount)

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
        native_tools: list[dict[str, Any]] | None = None,
        native_tool_choice: Any = "",
        system_extra_blocks: list[str] | None = None,
        history_turns: list[dict[str, Any]] | None = None,
        post_user_turns: list[dict[str, Any]] | None = None,
        prompt_audit_sections: list[dict[str, Any]] | None = None,
        chat_model_override: str = "",
    ) -> Generator[dict[str, Any], None, ChatJSONStreamResult]:
        self._record_metric("chat_stream_calls")
        return self._stream_chat_json(
            bundle=self._chat_bundle_for_override(chat_model_override),
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback=fallback,
            temperature=temperature,
            early_tool_call_validator=early_tool_call_validator,
            prompt_cache_key=prompt_cache_key,
            user_images=user_images,
            native_tools=native_tools,
            native_tool_choice=native_tool_choice,
            system_extra_blocks=system_extra_blocks,
            history_turns=history_turns,
            post_user_turns=post_user_turns,
            prompt_audit_sections=prompt_audit_sections,
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
        system_extra_blocks: list[str] | None = None,
        history_turns: list[dict[str, Any]] | None = None,
        post_user_turns: list[dict[str, Any]] | None = None,
        prompt_audit_sections: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        native_requested = bool(self._normalize_native_tools(native_tools))
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
                    system_extra_blocks=system_extra_blocks,
                    history_turns=history_turns,
                    post_user_turns=post_user_turns,
                    prompt_audit_sections=prompt_audit_sections,
                ),
            )
            self._record_cache_metrics(response, prompt_cache_key=prompt_cache_key)
            native_tool_calls = self._extract_native_tool_calls(response, native_tools=native_tools, bundle=bundle)
            if native_tool_calls:
                self._record_metric("native_tool_call_extracted")
                parsed = {
                    NATIVE_TOOL_CALLS_FIELD: native_tool_calls,
                    NATIVE_TOOL_CALL_FIELD: native_tool_calls[0],
                    "tool_call": None,
                }
                native_preface_text = self._native_preface_text_from_content(self._extract_text(response))
                if native_preface_text:
                    parsed["speech"] = native_preface_text
                    parsed["speech_segments"] = [native_preface_text]
                return parsed
            if native_requested:
                self._record_metric("native_tool_no_call")
            self._note_truncation(response, phase="call_json")
            content = self._extract_text(response)
            parsed = self._extract_json(content)
            if isinstance(parsed, dict):
                return parsed
            recovered = self._recover_partial_chat_json(content, fallback=fallback)
            if isinstance(recovered, dict):
                return recovered
            self._note_parse_fallback(content, phase="call_json")
        except Exception as exc:
            self._record_metric("errors")
            self._capture_runtime_error(exc, phase="call_json")
        self._record_metric("chat_json_fallbacks")
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
                self._record_cache_metrics(chunk, prompt_cache_key=prompt_cache_key)
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
            self._capture_runtime_error(exc, phase="call_ndjson")
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
        native_tools: list[dict[str, Any]] | None = None,
        native_tool_choice: Any = "",
        system_extra_blocks: list[str] | None = None,
        history_turns: list[dict[str, Any]] | None = None,
        post_user_turns: list[dict[str, Any]] | None = None,
        prompt_audit_sections: list[dict[str, Any]] | None = None,
    ) -> Generator[dict[str, Any], None, ChatJSONStreamResult]:
        import time

        native_requested = bool(self._normalize_native_tools(native_tools))
        response: Any = None
        error = ""
        raw_parts: list[str] = []
        native_tool_parts: dict[Any, dict[str, Any]] = {}
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
                    native_tools=native_tools,
                    native_tool_choice=native_tool_choice,
                    system_extra_blocks=system_extra_blocks,
                    history_turns=history_turns,
                    post_user_turns=post_user_turns,
                    prompt_audit_sections=prompt_audit_sections,
                ),
            )
            for chunk in response:
                self._record_cache_metrics(chunk, prompt_cache_key=prompt_cache_key)
                self._collect_stream_native_tool_call_parts(chunk, native_tool_parts, bundle=bundle)
                text = self._extract_stream_text(chunk)
                if not text:
                    continue
                raw_parts.append(text)
                for event in tap.feed(text):
                    yield event
                if not tool_probe_disabled:
                    probe_state, probe_call = self._try_extract_stream_tool_call("".join(raw_parts))
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
            self._capture_runtime_error(exc, phase="stream_chat_json")
        finally:
            self._record_cache_metrics(response, prompt_cache_key=prompt_cache_key)
            self._close_stream(response)

        raw_text = "".join(raw_parts)
        native_tool_calls = self._stream_native_tool_calls_from_parts(
            native_tool_parts,
            native_tools=native_tools,
            bundle=bundle,
        )
        if native_tool_calls:
            self._record_metric("native_tool_call_extracted")
            parsed = {
                NATIVE_TOOL_CALLS_FIELD: native_tool_calls,
                NATIVE_TOOL_CALL_FIELD: native_tool_calls[0],
                "tool_call": None,
            }
            native_preface_text = "" if tap.latest_speech else self._native_preface_text_from_content(raw_text)
            if native_preface_text:
                parsed["speech"] = native_preface_text
                parsed["speech_segments"] = [native_preface_text]
        elif native_requested:
            self._record_metric("native_tool_no_call")
            parsed = self._extract_json(raw_text)
        elif early_tool_call is not None:
            parsed = {"tool_call": early_tool_call}
        else:
            parsed = self._extract_json(raw_text)
        if not isinstance(parsed, dict):
            self._record_metric("chat_json_fallbacks")
            self._note_parse_fallback(raw_text, phase="stream_chat_json")
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
            native_preface_text=native_preface_text if native_tool_calls else "",
            stopped_early=stopped_early,
            early_tool_call=early_tool_call,
        )

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

    def _extract_text(self, response: Any) -> str:
        try:
            return self._flatten_message_content(response.choices[0].message.content).strip()
        except Exception:
            return ""

    @staticmethod
    def _native_preface_text_from_content(content: Any) -> str:
        """Return only user-facing assistant text emitted before a native tool call."""

        text = str(content or "").strip()
        if not text:
            return ""
        candidate = text
        if candidate.startswith("```") and candidate.endswith("```"):
            lines = candidate.splitlines()
            if len(lines) >= 3:
                candidate = "\n".join(lines[1:-1]).strip()
        try:
            parsed = json.loads(candidate)
        except (TypeError, ValueError, json.JSONDecodeError):
            return text
        if not isinstance(parsed, dict):
            return ""
        speech = str(parsed.get("speech") or "").strip()
        if speech:
            return speech
        segments = parsed.get("speech_segments")
        if isinstance(segments, list):
            return "\n".join(str(item or "").strip() for item in segments if str(item or "").strip())
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
        system_extra_blocks: list[str] | None = None,
        history_turns: list[dict[str, Any]] | None = None,
        post_user_turns: list[dict[str, Any]] | None = None,
        prompt_audit_sections: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        user_content: str | list[dict[str, Any]]
        image_items = self._normalize_user_image_items(user_images)
        if image_items:
            user_content = [{"type": "text", "text": user_prompt}, *image_items]
        else:
            user_content = user_prompt
        filtered_system_extra_blocks = self._normalize_system_extra_blocks(system_extra_blocks)
        effective_system_prompt = str(system_prompt or "")
        if filtered_system_extra_blocks and not self._is_anthropic_protocol(bundle):
            effective_system_prompt = "\n\n".join(
                part for part in [effective_system_prompt.strip(), *filtered_system_extra_blocks] if part
            )
        messages: list[dict[str, Any]] = [{"role": "system", "content": effective_system_prompt}]
        for turn in history_turns or []:
            role = str(turn.get("role", "") or "").strip().lower()
            content = self._normalize_message_content_for_payload(turn.get("content"))
            if content and role in {"user", "assistant"}:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_content})
        for turn in post_user_turns or []:
            normalized_turn = self._normalize_post_user_turn_for_payload(turn, bundle=bundle)
            if normalized_turn is not None:
                messages.append(normalized_turn)
        payload: dict[str, Any] = {
            "model": bundle.model,
            "messages": messages,
        }
        # Current OpenAI reasoning models reject sampling controls when explicit
        # reasoning effort is selected. Keep temperature for all legacy paths.
        if not (self._is_responses_protocol(bundle) and self._responses_reasoning_effort(bundle)):
            payload["temperature"] = temperature
        if stream:
            payload["stream"] = True
            if self._supports_stream_usage(bundle):
                payload["stream_options"] = {"include_usage": True}
        normalized_tools = self._normalize_native_tools(native_tools)
        native_tool_profile = self._native_tool_profile(bundle)
        should_send_native_tools = bool(normalized_tools and native_tool_profile.supports_native_tools)
        should_use_response_json = bool(json_mode and self._should_use_response_json_mode(bundle))
        if json_mode and should_send_native_tools:
            if native_tool_profile.native_tools_coexist_with_forced_json:
                should_use_response_json = True
            else:
                should_use_response_json = False
                self._record_metric("native_tool_forced_json_suppressed")
        if should_use_response_json:
            payload["response_format"] = {"type": "json_object"}
            self._ensure_json_keyword(messages)
        if normalized_tools and should_send_native_tools:
            payload["tools"] = normalized_tools
            self._record_metric("native_tool_decision_sent")
            tool_choice = self._normalize_native_tool_choice(native_tool_choice)
            if tool_choice:
                payload["tool_choice"] = tool_choice
        elif normalized_tools:
            self._record_metric("native_tool_provider_unsupported")
        payload.update(self._build_reasoning_control_kwargs(bundle=bundle))
        payload.update(
            self._build_prompt_cache_kwargs(
                bundle=bundle,
                prompt_cache_key=prompt_cache_key,
            )
        )
        if filtered_system_extra_blocks and self._is_anthropic_protocol(bundle):
            payload["system_extra_blocks"] = filtered_system_extra_blocks
        self._record_prompt_audit_if_enabled(
            bundle=bundle,
            prompt_cache_key=prompt_cache_key,
            messages=messages,
            system_extra_blocks=filtered_system_extra_blocks if self._is_anthropic_protocol(bundle) else [],
            history_turns=history_turns,
            post_user_turns=post_user_turns,
            user_prompt=user_prompt,
            user_image_count=len(image_items),
            prompt_audit_sections=prompt_audit_sections,
            stream=stream,
            json_mode=json_mode,
            native_tools=normalized_tools if should_send_native_tools else [],
        )
        self._enforce_prompt_token_limits(payload)
        return payload

    def _enforce_prompt_token_limits(self, payload: dict[str, Any]) -> None:
        configured_limits = [
            max(0, int(getattr(config, "LLM_AUTO_COMPACT_TOKEN_LIMIT", 0) or 0)),
            max(0, int(getattr(config, "LLM_CONTEXT_WINDOW", 0) or 0)),
        ]
        limits = [value for value in configured_limits if value > 0]
        if not limits:
            return
        limit = min(limits)
        serialized = json.dumps(
            {
                "messages": payload.get("messages") or [],
                "tools": payload.get("tools") or [],
                "system_extra_blocks": payload.get("system_extra_blocks") or [],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        estimated = self._estimate_prompt_tokens(serialized)
        if estimated > limit:
            self._record_metric("prompt_token_limit_exceeded")
            raise ValueError(f"llm_prompt_token_limit_exceeded estimated={estimated} limit={limit}")

    def _normalize_message_content_for_payload(self, content: Any) -> str | list[dict[str, Any]]:
        if isinstance(content, list):
            blocks = [dict(item) for item in content if isinstance(item, dict)]
            return blocks if blocks else self._flatten_message_content(content).strip()
        return str(content or "").strip()

    def _normalize_post_user_turn_for_payload(
        self,
        turn: Any,
        *,
        bundle: ModelBundle,
    ) -> dict[str, Any] | None:
        if not isinstance(turn, dict):
            return None
        role = str(turn.get("role", "") or "").strip().lower()
        content = self._normalize_message_content_for_payload(turn.get("content"))
        if not self._is_anthropic_protocol(bundle):
            if role == "assistant":
                tool_calls = self._normalize_openai_history_tool_calls(turn.get("tool_calls"))
                if tool_calls:
                    message: dict[str, Any] = {"role": "assistant", "tool_calls": tool_calls}
                    if content:
                        message["content"] = content
                    return message
            if role == "tool":
                tool_call_id = str(turn.get("tool_call_id") or "").strip()
                if tool_call_id:
                    return {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": content if isinstance(content, str) else self._flatten_message_content(content),
                    }
        if content and role in {"user", "assistant"}:
            return {"role": role, "content": content}
        return None

    @staticmethod
    def _normalize_openai_history_tool_calls(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        normalized: list[dict[str, Any]] = []
        for raw in value[:4]:
            if not isinstance(raw, dict):
                continue
            call_id = str(raw.get("id") or "").strip()
            function = raw.get("function")
            if not call_id or not isinstance(function, dict):
                continue
            name = str(function.get("name") or "").strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
                continue
            arguments = function.get("arguments", "")
            if not isinstance(arguments, str):
                try:
                    arguments = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
                except (TypeError, ValueError):
                    continue
            normalized.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }
            )
        return normalized

    def _normalize_system_extra_blocks(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item or "").strip() for item in value if str(item or "").strip()]

    def _is_anthropic_protocol(self, bundle: ModelBundle) -> bool:
        client = getattr(bundle, "client", bundle)
        protocol = str(getattr(client, "_akane_protocol", getattr(client, "protocol", "")) or "").strip().lower()
        return protocol == "anthropic"

    def _is_responses_protocol(self, bundle: ModelBundle) -> bool:
        client = getattr(bundle, "client", bundle)
        protocol = str(getattr(client, "_akane_protocol", getattr(client, "protocol", "")) or "").strip().lower()
        return protocol == "responses"

    def _supports_stream_usage(self, bundle: ModelBundle) -> bool:
        if self._is_responses_protocol(bundle):
            return False
        return self._supports_deepseek_thinking_control(bundle)

    def _record_prompt_audit_if_enabled(
        self,
        *,
        bundle: ModelBundle,
        prompt_cache_key: str,
        messages: list[dict[str, Any]],
        system_extra_blocks: list[str],
        history_turns: list[dict[str, Any]] | None,
        post_user_turns: list[dict[str, Any]] | None,
        user_prompt: str,
        user_image_count: int,
        prompt_audit_sections: list[dict[str, Any]] | None,
        stream: bool,
        json_mode: bool,
        native_tools: list[dict[str, Any]],
    ) -> None:
        if not self._should_record_prompt_audit(prompt_cache_key):
            return
        try:
            payload_sections = self._build_payload_audit_sections(
                messages=messages,
                system_extra_blocks=system_extra_blocks,
                history_turns=history_turns,
                post_user_turns=post_user_turns,
                user_prompt=user_prompt,
            )
            record = {
                "record_type": "prompt",
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                "instance_id": str(getattr(self, "instance_id", "local-default") or "local-default"),
                "prompt_cache_key": str(prompt_cache_key or ""),
                "model": str(getattr(bundle, "model", "") or ""),
                "protocol": str(
                    getattr(bundle.client, "_akane_protocol", getattr(bundle.client, "protocol", "")) or ""
                ),
                "stream": bool(stream),
                "json_mode": bool(json_mode),
                "message_count": len(messages),
                "history_turn_count": len(history_turns or []),
                "post_user_turn_count": len(post_user_turns or []),
                "user_image_count": max(0, int(user_image_count or 0)),
                "native_tool_count": len(native_tools),
                "native_tool_names": [
                    str(
                        (item.get("function") or {}).get("name")
                        or item.get("name")
                        or ""
                    ).strip()
                    for item in native_tools[:64]
                    if isinstance(item, dict)
                    and str((item.get("function") or {}).get("name") or item.get("name") or "").strip()
                ],
                "native_tool_schema": self._audit_text_section(
                    "payload.native_tools",
                    json.dumps(
                        native_tools,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    ),
                ),
                "payload_totals": self._sum_audit_sections(payload_sections),
                "payload_sections": payload_sections,
                "source_sections": self._normalize_prompt_audit_sections(prompt_audit_sections),
            }
            self._append_prompt_audit_record(record)
        except Exception:
            pass

    def _should_record_prompt_audit(self, prompt_cache_key: str) -> bool:
        if not bool(getattr(config, "LLM_PROMPT_AUDIT_ENABLED", False)):
            return False
        key = str(prompt_cache_key or "").strip()
        if key == "chat:final" or key.startswith(("chat:final:", "chat:plugin_proactive:")):
            return True
        return bool(getattr(config, "LLM_PROMPT_AUDIT_INCLUDE_AUX", False))

    def _build_payload_audit_sections(
        self,
        *,
        messages: list[dict[str, Any]],
        system_extra_blocks: list[str],
        history_turns: list[dict[str, Any]] | None,
        post_user_turns: list[dict[str, Any]] | None,
        user_prompt: str,
    ) -> list[dict[str, Any]]:
        sections: list[dict[str, Any]] = []
        if messages:
            sections.append(
                self._audit_text_section(
                    "payload.system_message", self._flatten_message_content(messages[0].get("content"))
                )
            )
        history_text = "\n".join(
            f"{str(turn.get('role') or '').strip().lower()}:{self._flatten_message_content(turn.get('content'))}"
            for turn in history_turns or []
            if str(turn.get("content") or "").strip()
        )
        sections.append(self._audit_text_section("payload.history_turns", history_text))
        sections.append(self._audit_text_section("payload.user_prompt", user_prompt))
        post_user_text = "\n".join(
            f"{str(turn.get('role') or '').strip().lower()}:{self._flatten_message_content(turn.get('content'))}"
            for turn in post_user_turns or []
            if turn.get("content")
        )
        if post_user_text:
            sections.append(self._audit_text_section("payload.post_user_turns", post_user_text))
        if system_extra_blocks:
            sections.append(self._audit_text_section("payload.system_extra_blocks", "\n\n".join(system_extra_blocks)))
        return sections

    def _normalize_prompt_audit_sections(self, sections: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        if not isinstance(sections, list):
            return normalized
        for idx, item in enumerate(sections):
            if isinstance(item, dict):
                name = str(item.get("name") or f"section_{idx}").strip() or f"section_{idx}"
                text = item.get("text", "")
            else:
                name = f"section_{idx}"
                text = item
            normalized.append(self._audit_text_section(name, self._flatten_message_content(text)))
        return normalized

    def _audit_text_section(self, name: str, text: str) -> dict[str, Any]:
        raw = str(text or "")
        digest = hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:16] if raw else ""
        return {
            "name": str(name or "section"),
            "chars": len(raw),
            "estimated_tokens": self._estimate_prompt_tokens(raw),
            "sha256_16": digest,
            "empty": not bool(raw),
        }

    def _estimate_prompt_tokens(self, text: str) -> int:
        raw = str(text or "")
        if not raw:
            return 0
        cjk_chars = sum(1 for char in raw if "\u4e00" <= char <= "\u9fff")
        non_cjk_chars = max(0, len(raw) - cjk_chars)
        return int(cjk_chars + ((non_cjk_chars + 3) // 4))

    def _sum_audit_sections(self, sections: list[dict[str, Any]]) -> dict[str, int]:
        return {
            "chars": sum(int(section.get("chars") or 0) for section in sections),
            "estimated_tokens": sum(int(section.get("estimated_tokens") or 0) for section in sections),
        }

    def _append_prompt_audit_record(self, record: dict[str, Any]) -> None:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_dir = getattr(self, "log_dir", None)
        log_root = Path(log_dir) if log_dir is not None else Path(str(getattr(config, "LOG_DIR", "") or "logs"))
        path = log_root / "llm_prompt_audit" / f"{day}.jsonl"
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        with PROMPT_AUDIT_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def _append_prompt_usage_audit(
        self,
        *,
        prompt_cache_key: str,
        read_tokens: int,
        creation_tokens: int,
        input_tokens: int,
        output_tokens: int,
        response: Any,
    ) -> None:
        if not self._should_record_prompt_audit(prompt_cache_key):
            return
        model = ""
        if isinstance(response, dict):
            model = str(response.get("model") or "")
        else:
            model = str(getattr(response, "model", "") or "")
        self._append_prompt_audit_record(
            {
                "record_type": "usage",
                "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "instance_id": str(getattr(self, "instance_id", "local-default") or "local-default"),
                "prompt_cache_key": str(prompt_cache_key or ""),
                "model": model,
                "reported_input_tokens": max(0, int(input_tokens or 0)),
                "reported_output_tokens": max(0, int(output_tokens or 0)),
                "cache_read_tokens": max(0, int(read_tokens or 0)),
                "cache_creation_tokens": max(0, int(creation_tokens or 0)),
                "cache_hit_ratio": (
                    round(max(0, int(read_tokens or 0)) / max(1, int(input_tokens or 0)), 6)
                    if int(input_tokens or 0) > 0
                    else 0.0
                ),
            }
        )

    def _record_cache_metrics(self, response: Any, *, prompt_cache_key: str = "") -> None:
        try:
            usage = getattr(response, "usage", None)
            if usage is None and isinstance(response, dict):
                usage = response.get("usage")
            if usage is None:
                return
            # Anthropic: cache_read_input_tokens / cache_creation_input_tokens
            read = self._usage_int(usage, "cache_read_input_tokens")
            creation = self._usage_int(usage, "cache_creation_input_tokens")
            # DeepSeek: prompt_cache_hit_tokens / prompt_cache_miss_tokens
            if not read:
                read = self._usage_int(usage, "prompt_cache_hit_tokens")
            if not creation:
                creation = self._usage_int(usage, "prompt_cache_miss_tokens")
            # OpenAI Responses and Chat Completions report cached input under
            # nested detail objects rather than the Anthropic/DeepSeek fields.
            if not read:
                read = self._nested_usage_int(usage, "input_tokens_details", "cached_tokens")
            if not read:
                read = self._nested_usage_int(usage, "prompt_tokens_details", "cached_tokens")
            if read:
                self._record_metric("cache_read_tokens", read)
            if creation:
                self._record_metric("cache_creation_tokens", creation)
            reported_input = self._usage_int(usage, "prompt_tokens")
            if not reported_input:
                reported_input = self._usage_int(usage, "input_tokens")
            reported_output = self._usage_int(usage, "completion_tokens")
            if not reported_output:
                reported_output = self._usage_int(usage, "output_tokens")
            if reported_input:
                self._record_metric("reported_input_tokens", reported_input)
            if reported_output:
                self._record_metric("reported_output_tokens", reported_output)
            if reported_input:
                self._record_metric("cache_usage_calls")
                if read:
                    self._record_metric("cache_hit_calls")
            normalized_cache_key = str(prompt_cache_key or "")
            if normalized_cache_key.startswith("chat:final:") or normalized_cache_key == "chat:final":
                if read:
                    self._record_metric("final_cache_read_tokens", read)
                if creation:
                    self._record_metric("final_cache_creation_tokens", creation)
                if reported_input:
                    self._record_metric("final_reported_input_tokens", reported_input)
                    self._record_metric("final_cache_usage_calls")
                    if read:
                        self._record_metric("final_cache_hit_calls")
                if reported_output:
                    self._record_metric("final_reported_output_tokens", reported_output)
            if normalized_cache_key.startswith("chat:plugin_proactive:"):
                if read:
                    self._record_metric("plugin_proactive_cache_read_tokens", read)
                if creation:
                    self._record_metric("plugin_proactive_cache_creation_tokens", creation)
                if reported_input:
                    self._record_metric("plugin_proactive_reported_input_tokens", reported_input)
                    self._record_metric("plugin_proactive_cache_usage_calls")
                    if read:
                        self._record_metric("plugin_proactive_cache_hit_calls")
                if reported_output:
                    self._record_metric("plugin_proactive_reported_output_tokens", reported_output)
            if reported_input and self._should_record_prompt_audit(normalized_cache_key):
                self._append_prompt_usage_audit(
                    prompt_cache_key=normalized_cache_key,
                    read_tokens=read,
                    creation_tokens=creation,
                    input_tokens=reported_input,
                    output_tokens=reported_output,
                    response=response,
                )
        except Exception:
            pass

    def _usage_int(self, usage: Any, key: str) -> int:
        value = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, 0)
        try:
            return int(value or 0)
        except Exception:
            return 0

    def _nested_usage_int(self, usage: Any, parent: str, key: str) -> int:
        nested = usage.get(parent) if isinstance(usage, dict) else getattr(usage, parent, None)
        return self._usage_int(nested, key) if nested is not None else 0

    def _should_send_native_tools(self, bundle: ModelBundle) -> bool:
        return bool(self._native_tool_profile(bundle).supports_native_tools)

    def _native_tool_profile(self, bundle: ModelBundle) -> ProviderToolProfile:
        protocol = (
            str(getattr(bundle.client, "_akane_protocol", getattr(bundle.client, "protocol", "")) or "").strip().lower()
        )
        if protocol == "anthropic":
            return ProviderToolProfile(
                supports_native_tools=True,
                native_tools_coexist_with_forced_json=False,
                native_call_shape="anthropic_tool_use",
                verified=True,
                notes="Anthropic Messages API supports native tools/tool_use; forced JSON is not sent on this protocol.",
            )
        if protocol == "responses":
            return ProviderToolProfile(
                supports_native_tools=True,
                native_tools_coexist_with_forced_json=False,
                native_call_shape="responses_function_call",
                verified=True,
                notes="OpenAI Responses wire protocol with function_call/function_call_output items.",
            )
        if protocol != "openai":
            return DEFAULT_PROVIDER_TOOL_PROFILE
        host = self._bundle_base_host(bundle)
        model = str(getattr(bundle, "model", "") or "").strip().lower()
        profile = PROVIDER_TOOL_PROFILES.get((host, model))
        if profile is not None:
            return profile
        return self._configured_native_tool_profile(host=host, model=model)

    def _configured_native_tool_profile(self, *, host: str, model: str) -> ProviderToolProfile:
        clean_host = str(host or "").strip().lower()
        clean_model = str(model or "").strip().lower()
        if not clean_host or not clean_model:
            return DEFAULT_PROVIDER_TOOL_PROFILE
        raw_allowlist = str(getattr(config, "NATIVE_TOOL_PROVIDER_ALLOWLIST", "") or "").strip()
        if not raw_allowlist:
            return DEFAULT_PROVIDER_TOOL_PROFILE
        for raw_item in raw_allowlist.split(","):
            item = str(raw_item or "").strip()
            if not item:
                continue
            parsed = self._parse_native_tool_provider_allowlist_item(item)
            if parsed is None:
                continue
            allowed_host, allowed_model, coexist_json = parsed
            if allowed_host not in {"*", clean_host}:
                continue
            if allowed_model not in {"*", clean_model}:
                continue
            if coexist_json:
                return ProviderToolProfile(
                    supports_native_tools=True,
                    native_tools_coexist_with_forced_json=True,
                    verified=False,
                    notes=(
                        "Enabled by NATIVE_TOOL_PROVIDER_ALLOWLIST with json coexistence. "
                        "Use only after probing the gateway/model."
                    ),
                )
            return CONFIG_ALLOWLISTED_PROVIDER_TOOL_PROFILE
        return DEFAULT_PROVIDER_TOOL_PROFILE

    def _parse_native_tool_provider_allowlist_item(self, item: str) -> tuple[str, str, bool] | None:
        text = str(item or "").strip().lower()
        if not text:
            return None
        if "://" in text:
            parsed_url = urlparse(text)
            text = parsed_url.netloc or parsed_url.path
            if parsed_url.path and parsed_url.netloc and ":" in parsed_url.path.strip("/"):
                text = f"{parsed_url.netloc}:{parsed_url.path.strip('/')}"
        parts = [part.strip() for part in text.split(":") if part.strip()]
        if not parts:
            return None
        host = self._normalize_native_tool_allowlist_host(parts[0])
        model = parts[1] if len(parts) >= 2 else "*"
        mode = parts[2] if len(parts) >= 3 else ""
        if not host or not model:
            return None
        return host, model, mode in {"json", "response_json", "forced_json"}

    def _normalize_native_tool_allowlist_host(self, value: str) -> str:
        raw = str(value or "").strip().lower()
        if raw == "*":
            return raw
        if "://" in raw:
            raw = urlparse(raw).netloc
        if "/" in raw:
            raw = raw.split("/", 1)[0]
        if "@" in raw:
            raw = raw.rsplit("@", 1)[-1]
        if ":" in raw:
            raw = raw.split(":", 1)[0]
        return raw.strip()

    def _bundle_base_host(self, bundle: ModelBundle) -> str:
        raw = str(getattr(bundle.client, "base_url", "") or "").strip()
        if raw and "://" not in raw:
            raw = f"https://{raw}"
        try:
            return str(urlparse(raw).hostname or "").strip().lower()
        except Exception:
            return ""

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
            tool = {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description or f"Call Akane tool {name}.",
                    "parameters": parameters,
                },
            }
            if bool(function.get("strict")):
                tool["function"]["strict"] = True
            tools.append(tool)
            seen.add(name)
        return tools

    def _normalize_native_tool_choice(self, value: Any) -> Any:
        if isinstance(value, dict):
            return value
        raw = str(value or "").strip().lower()
        return raw if raw in {"auto", "none", "required"} else ""

    def _extract_native_tool_call(
        self,
        response: Any,
        *,
        native_tools: list[dict[str, Any]] | None = None,
        bundle: ModelBundle | None = None,
    ) -> dict[str, Any] | None:
        calls = self._extract_native_tool_calls(response, native_tools=native_tools, bundle=bundle)
        return calls[0] if calls else None

    def _extract_native_tool_calls(
        self,
        response: Any,
        *,
        native_tools: list[dict[str, Any]] | None = None,
        bundle: ModelBundle | None = None,
    ) -> list[dict[str, Any]]:
        source = NATIVE_OPENAI
        if bundle is not None and self._is_anthropic_protocol(bundle):
            invocations = parse_anthropic_messages_tool_uses(self._chat_response_message(response))
            source = NATIVE_ANTHROPIC
        else:
            invocations = parse_openai_chat_tool_calls(response)
        if not invocations:
            return []
        if len(invocations) > 1:
            self._record_metric("native_tool_calls_extra", len(invocations) - 1)
        if len(invocations) > 4:
            self._record_metric("native_tool_calls_truncated", len(invocations) - 4)
        calls = [
            self._native_invocation_to_tool_call(invocation, native_tools=native_tools, source=source)
            for invocation in invocations[:4]
        ]
        return [call for call in calls if call is not None]

    def _collect_stream_native_tool_call_parts(
        self,
        chunk: Any,
        parts: dict[Any, dict[str, Any]],
        *,
        bundle: ModelBundle | None = None,
    ) -> None:
        if bundle is not None and self._is_anthropic_protocol(bundle):
            collect_anthropic_messages_stream_tool_use_delta(chunk, parts)
            return
        collect_openai_chat_stream_tool_call_delta(chunk, parts)

    def _stream_native_tool_call_from_parts(
        self,
        parts: dict[Any, dict[str, Any]],
        *,
        native_tools: list[dict[str, Any]] | None = None,
        bundle: ModelBundle | None = None,
    ) -> dict[str, Any] | None:
        calls = self._stream_native_tool_calls_from_parts(parts, native_tools=native_tools, bundle=bundle)
        return calls[0] if calls else None

    def _stream_native_tool_calls_from_parts(
        self,
        parts: dict[Any, dict[str, Any]],
        *,
        native_tools: list[dict[str, Any]] | None = None,
        bundle: ModelBundle | None = None,
    ) -> list[dict[str, Any]]:
        source = NATIVE_OPENAI
        if bundle is not None and self._is_anthropic_protocol(bundle):
            invocations = parse_anthropic_messages_stream_tool_uses(parts)
            source = NATIVE_ANTHROPIC
        else:
            invocations = parse_openai_chat_stream_tool_calls(parts)
        if not invocations:
            return []
        if len(invocations) > 1:
            self._record_metric("native_tool_calls_extra", len(invocations) - 1)
        if len(invocations) > 4:
            self._record_metric("native_tool_calls_truncated", len(invocations) - 4)
        calls = [
            self._native_invocation_to_tool_call(invocation, native_tools=native_tools, source=source)
            for invocation in invocations[:4]
        ]
        return [call for call in calls if call is not None]

    def _native_invocation_to_tool_call(
        self,
        invocation: Any,
        *,
        native_tools: list[dict[str, Any]] | None,
        source: str = NATIVE_OPENAI,
    ) -> dict[str, Any] | None:
        model_name = str(getattr(invocation, "model_name", "") or "").strip()
        if not model_name or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", model_name):
            return None
        name_map = self._native_tool_model_name_map(native_tools)
        capability_id = name_map.get(model_name, str(getattr(invocation, "capability_id", "") or model_name).strip())
        if not capability_id:
            return None
        arguments = getattr(invocation, "arguments", {})
        result = {
            **(dict(arguments) if isinstance(arguments, dict) else {}),
            "type": capability_id,
            TOOL_SOURCE_FIELD: source if source in {NATIVE_OPENAI, NATIVE_ANTHROPIC} else NATIVE_OPENAI,
        }
        call_id = self._native_invocation_provider_id(invocation, source=source)
        if call_id:
            result[TOOL_INVOCATION_ID_FIELD] = call_id
        if model_name != capability_id:
            result[TOOL_MODEL_NAME_FIELD] = model_name
        return result

    def _native_invocation_provider_id(self, invocation: Any, *, source: str = NATIVE_OPENAI) -> str:
        raw = getattr(invocation, "raw", None)
        if isinstance(raw, dict) and "id" in raw:
            return str(raw.get("id") or "").strip()
        provider_id = str(getattr(invocation, "id", "") or "").strip()
        if source == NATIVE_ANTHROPIC and provider_id:
            return provider_id
        return ""

    def _chat_response_message(self, response: Any) -> Any:
        try:
            return response.choices[0].message
        except Exception:
            if isinstance(response, dict):
                try:
                    return response.get("choices", [{}])[0].get("message")
                except Exception:
                    return response
            return response

    def _native_tool_model_name_map(self, native_tools: list[dict[str, Any]] | None) -> dict[str, str]:
        mapping: dict[str, str] = {}
        if not isinstance(native_tools, list):
            return mapping
        for raw in native_tools:
            if not isinstance(raw, dict):
                continue
            function = raw.get("function")
            if not isinstance(function, dict):
                continue
            model_name = str(function.get("name") or "").strip()
            if not model_name:
                continue
            capability_id = str(raw.get(NATIVE_TOOL_CAPABILITY_ID_FIELD) or "").strip() or model_name
            mapping[model_name] = capability_id
        return mapping

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
        if self._is_responses_protocol(bundle):
            effort = self._responses_reasoning_effort(bundle)
            return {"reasoning": {"effort": effort}} if effort else {}
        mode = str(getattr(config, "LLM_THINKING_MODE", "disabled") or "").strip().lower()
        if mode in {"", "default", "auto"}:
            return {}
        if mode not in {"enabled", "disabled"}:
            return {}
        if not self._supports_deepseek_thinking_control(bundle):
            return {}
        return {"extra_body": {"thinking": {"type": mode}}}

    def _responses_reasoning_effort(self, bundle: ModelBundle | None = None) -> str:
        client = getattr(bundle, "client", bundle)
        role = str(getattr(client, "_akane_bundle_role", "") or "").strip().lower()
        role_setting = {
            "aux": "LLM_AUX_REASONING_EFFORT",
            "chat": "LLM_CHAT_REASONING_EFFORT",
        }.get(role, "")
        role_value = str(getattr(config, role_setting, "") or "").strip().lower() if role_setting else ""
        value = role_value or str(getattr(config, "LLM_REASONING_EFFORT", "") or "").strip().lower()
        return value if value in {"none", "minimal", "low", "medium", "high", "xhigh", "max"} else ""

    def _supports_deepseek_thinking_control(self, bundle: ModelBundle) -> bool:
        protocol = (
            str(getattr(bundle.client, "_akane_protocol", getattr(bundle.client, "protocol", "")) or "").strip().lower()
        )
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
        # Force response_format=json_object for OpenAI-compatible providers so the
        # structured persona JSON is actually enforced (not just prompt-requested).
        # Historically this was ollama-only, which left DeepSeek/OpenAI chat replies
        # unconstrained and prone to malformed JSON -> fallback. Anthropic uses a
        # different API and must NOT receive response_format.
        protocol = (
            str(getattr(bundle.client, "_akane_protocol", getattr(bundle.client, "protocol", "")) or "").strip().lower()
        )
        if protocol == "responses" and self._bundle_base_host(bundle) == "api.pinaic.com":
            # PinAI currently exposes the Responses endpoint, but its
            # gpt-5.6-sol upstream returns HTTP 502 whenever
            # text.format=json_object is present. Akane's prompts still carry
            # the strict JSON contract and the normal parser/fallback remains
            # authoritative; omit only this unsupported wire hint.
            return False
        return protocol in {"ollama", "openai", "responses"}

    def _ensure_json_keyword(self, messages: list[dict[str, Any]]) -> None:
        # OpenAI/DeepSeek reject response_format=json_object unless the messages
        # contain the literal word "json". Akane's persona prompt is Chinese and may
        # not include it, so append a short note when it's missing.
        for message in messages:
            content = message.get("content")
            if isinstance(content, str) and "json" in content.lower():
                return
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and "json" in str(part.get("text") or "").lower():
                        return
        if messages and str(messages[0].get("role") or "") == "system":
            messages[0]["content"] = (
                str(messages[0].get("content") or "").rstrip()
                + "\n（本轮只输出一个合法的 JSON object，不要输出多余文字。）"
            )

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
        protocol = (
            str(getattr(bundle.client, "_akane_protocol", getattr(bundle.client, "protocol", "")) or "").strip().lower()
        )
        if protocol not in {"openai", "responses"}:
            return False
        if protocol == "responses":
            return True
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
        if raw in {"in_memory", "in-memory"}:
            return "in-memory"
        return raw if raw == "24h" else ""

    def _create_completion(self, *, bundle: ModelBundle, payload: dict[str, Any]) -> Any:
        if self._is_responses_protocol(bundle):
            return self._create_responses_completion(bundle=bundle, payload=payload)
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

    def _create_responses_completion(self, *, bundle: ModelBundle, payload: dict[str, Any]) -> Any:
        request = self._responses_payload_from_chat(payload)
        try:
            response = bundle.client.responses.create(**request)
        except TypeError:
            stripped = self._without_prompt_cache_hints(request)
            if stripped == request:
                raise
            response = bundle.client.responses.create(**stripped)
        except Exception as exc:
            if not self._should_retry_without_prompt_cache_hints(exc):
                raise
            stripped = self._without_prompt_cache_hints(request)
            if stripped == request:
                raise
            response = bundle.client.responses.create(**stripped)
        if bool(request.get("stream")):
            return _ResponsesStreamAdapter(response)
        return self._adapt_responses_result(response)

    def _responses_payload_from_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = dict(payload)
        messages = list(request.pop("messages", []) or [])
        request.pop("stream_options", None)
        system_text = ""
        if messages and str(messages[0].get("role") or "").strip().lower() == "system":
            system_text = self._flatten_message_content(messages.pop(0).get("content")).strip()
        if system_text:
            request["instructions"] = system_text
        request["input"] = self._responses_input_from_messages(messages)
        response_format = request.pop("response_format", None)
        if isinstance(response_format, dict) and response_format.get("type") == "json_object":
            request["text"] = {"format": {"type": "json_object"}}
        tools = request.get("tools")
        if isinstance(tools, list):
            request["tools"] = self._responses_tools_from_chat(tools)
            request["parallel_tool_calls"] = True
        tool_choice = request.get("tool_choice")
        if isinstance(tool_choice, dict):
            function = tool_choice.get("function")
            if isinstance(function, dict) and str(function.get("name") or "").strip():
                request["tool_choice"] = {"type": "function", "name": str(function["name"]).strip()}
        request["store"] = not bool(getattr(config, "LLM_DISABLE_RESPONSE_STORAGE", True))
        if request.get("prompt_cache_retention") == "in_memory":
            request["prompt_cache_retention"] = "in-memory"
        return request

    def _responses_input_from_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for message in messages:
            role = str(message.get("role") or "").strip().lower()
            if role == "assistant" and isinstance(message.get("tool_calls"), list):
                content = self._flatten_message_content(message.get("content")).strip()
                if content:
                    items.append({"role": "assistant", "content": content})
                for call in self._normalize_openai_history_tool_calls(message.get("tool_calls")):
                    function = call["function"]
                    items.append({
                        "type": "function_call",
                        "call_id": call["id"],
                        "name": function["name"],
                        "arguments": function["arguments"],
                    })
                continue
            if role == "tool":
                call_id = str(message.get("tool_call_id") or "").strip()
                if call_id:
                    items.append({
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": self._flatten_message_content(message.get("content")),
                    })
                continue
            if role not in {"user", "assistant", "developer", "system"}:
                continue
            content = message.get("content")
            items.append({"role": role, "content": self._responses_message_content(content)})
        return items

    def _responses_message_content(self, content: Any) -> Any:
        if not isinstance(content, list):
            return str(content or "")
        blocks: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "")
            if block_type == "text":
                blocks.append({"type": "input_text", "text": str(block.get("text") or "")})
            elif block_type == "image_url":
                image_url = block.get("image_url")
                url = image_url.get("url") if isinstance(image_url, dict) else image_url
                if str(url or "").startswith("data:image/"):
                    blocks.append({"type": "input_image", "image_url": str(url)})
        return blocks or self._flatten_message_content(content)

    def _responses_tools_from_chat(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for tool in tools:
            function = tool.get("function") if isinstance(tool, dict) else None
            if not isinstance(function, dict):
                continue
            item = {
                "type": "function",
                "name": str(function.get("name") or ""),
                "description": str(function.get("description") or ""),
                "parameters": function.get("parameters") or {"type": "object", "additionalProperties": True},
            }
            if "strict" in function:
                item["strict"] = bool(function.get("strict"))
            result.append(item)
        return result

    def _adapt_responses_result(self, response: Any) -> Any:
        status = str(self._get_attr_or_key(response, "status") or "").strip().lower()
        if status in {"failed", "cancelled"}:
            raise RuntimeError(f"responses_{status} {_responses_error_summary(response)}")
        output = self._get_attr_or_key(response, "output") or []
        tool_calls: list[dict[str, Any]] = []
        text_parts: list[str] = []
        direct_text = str(self._get_attr_or_key(response, "output_text") or "")
        if direct_text:
            text_parts.append(direct_text)
        for item in output:
            item_type = str(self._get_attr_or_key(item, "type") or "")
            if item_type == "function_call":
                call_id = str(
                    self._get_attr_or_key(item, "call_id")
                    or self._get_attr_or_key(item, "id")
                    or ""
                )
                tool_calls.append({
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": str(self._get_attr_or_key(item, "name") or ""),
                        "arguments": str(self._get_attr_or_key(item, "arguments") or "{}"),
                    },
                })
            elif item_type == "message" and not direct_text:
                for block in self._get_attr_or_key(item, "content") or []:
                    if str(self._get_attr_or_key(block, "type") or "") == "output_text":
                        text_parts.append(str(self._get_attr_or_key(block, "text") or ""))
        message = SimpleNamespace(content="".join(text_parts), tool_calls=tool_calls)
        choice = SimpleNamespace(message=message, finish_reason="length" if status == "incomplete" else "stop")
        return SimpleNamespace(choices=[choice], usage=self._get_attr_or_key(response, "usage"), raw_response=response)

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
        lock = getattr(self, "_metrics_lock", None)
        metrics = getattr(self, "_metrics", None)
        if lock is None or not isinstance(metrics, dict):
            return
        with lock:
            metrics[key] = int(metrics.get(key, 0)) + int(amount)

    def snapshot_metrics(self) -> dict[str, int]:
        with self._metrics_lock:
            return {key: int(value) for key, value in self._metrics.items()}

    def snapshot_last_error(self) -> dict[str, str]:
        lock = getattr(self, "_last_error_lock", None)
        last_error = getattr(self, "_last_error", None)
        if lock is None or not isinstance(last_error, dict):
            return {}
        with lock:
            return {str(key): str(value) for key, value in last_error.items()}

    def _record_error_detail(self, exc: Exception, *, phase: str = "") -> None:
        lock = getattr(self, "_last_error_lock", None)
        if lock is None:
            return
        detail = {
            "phase": str(phase or ""),
            "type": exc.__class__.__name__,
            "message": self._sanitize_error_message(str(exc or "")),
        }
        with lock:
            self._last_error = detail

    def _capture_runtime_error(self, exc: Exception, *, phase: str) -> None:
        """Record and log one bounded, secret-redacted provider failure."""

        self._record_error_detail(exc, phase=phase)
        detail = self.snapshot_last_error()
        logger.warning(
            "llm request failed phase=%s type=%s message=%s",
            str(detail.get("phase") or phase),
            str(detail.get("type") or exc.__class__.__name__),
            str(detail.get("message") or "provider_request_failed"),
        )

    def _note_truncation(self, response: Any, *, phase: str) -> None:
        """Surface silent length-truncation through the metric + last-error
        channels (this file's structured-failure pattern; INV-3).

        A provider output cap can still cut the response mid-JSON, which then
        parses or recovers as if it were complete. The Anthropic stop
        reason maps to finish_reason="length"; without this, a truncated reply
        is indistinguishable from a normal short answer or a fallback.
        """
        try:
            choice = response.choices[0]
            finish_reason = (
                str(choice.get("finish_reason") or "")
                if isinstance(choice, dict)
                else str(getattr(choice, "finish_reason", "") or "")
            )
        except Exception:
            return
        if finish_reason.strip().lower() != "length":
            return
        sample = self._extract_text(response)
        self._record_metric("response_truncated")
        lock = getattr(self, "_last_error_lock", None)
        if lock is None:
            return
        detail = {
            "phase": str(phase or ""),
            "type": "ResponseTruncated",
            "message": self._sanitize_error_message(
                f"finish_reason=length, output cut off (chars={len(sample)}); tail={sample[-200:]!r}"
            ),
        }
        with lock:
            self._last_error = detail

    def _note_parse_fallback(self, raw_text: Any, *, phase: str) -> None:
        """Record a sanitized sample when a response can't be parsed as JSON and
        we fall back (INV-3). The fallback is counted via chat_json_fallbacks;
        without a sample a malformed reply silently becomes a fallback with
        nothing to reproduce it from. Secret-redacted + capped via
        _sanitize_error_message; head (not tail) is the diagnostic part here —
        it shows e.g. prose-instead-of-JSON or a wrong-shaped object.
        """
        lock = getattr(self, "_last_error_lock", None)
        if lock is None:
            return
        sample = str(raw_text or "")
        detail = {
            "phase": str(phase or ""),
            "type": "ChatJSONFallback",
            "message": self._sanitize_error_message(
                f"response not valid JSON, used fallback (chars={len(sample)}); head={sample[:200]!r}"
            ),
        }
        with lock:
            self._last_error = detail

    def _sanitize_error_message(self, message: str, *, max_chars: int = 1000) -> str:
        text = str(message or "")
        for pattern in SECRET_PATTERNS:
            if pattern.groups >= 2:
                text = pattern.sub(lambda match: f"{match.group(1)}[redacted]", text)
            else:
                text = pattern.sub("[redacted]", text)
        if len(text) > max_chars:
            text = text[: max_chars - 3].rstrip() + "..."
        return text

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
