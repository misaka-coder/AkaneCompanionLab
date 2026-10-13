"""Observed provider requests, cancellable dispatch, retries and response carriers."""

from __future__ import annotations

from typing import Any, Callable
import json
import re

from ..model_request_control import dispatch_model_request
from ..speech_units import project_speech_units
from ..turn_coordination import current_cancellation_check
from .json_stream import _completed_tool_preface_text
from .models import ModelBundle, _CompletionPayload
from .responses import _ResponsesStreamAdapter
from services import provider_continuation as continuation
from services.tool_history import isolate_tool_history


class ProviderExecutionMixin:
    """Methods use the state initialized by LLMRuntime."""

    @staticmethod
    def _dispatch_provider(create: Callable[..., Any], payload: dict[str, Any]) -> Any:
        # Check every actual SDK call, including compatibility and transport retries.
        # Production clients disable SDK retries (max_retries=0).
        return dispatch_model_request(create, payload, current_cancellation_check())

    def _create_completion(self, *, bundle: ModelBundle, payload: dict[str, Any]) -> Any:
        # Direct callers and all compatibility retries share the same boundary.
        # Normal assembly already isolated history before observation/token counts.
        messages = [continuation.carry(m, m, bundle=bundle) if isinstance(m, dict) else m
                    for m in payload.get("messages") or []]
        payload = {**payload, "messages": isolate_tool_history(messages).messages}
        if self._is_responses_protocol(bundle):
            return self._create_responses_completion(bundle=bundle, payload=payload)
        if not self._is_gemini_protocol(bundle) and not self._is_anthropic_protocol(bundle):
            payload["messages"] = [continuation.chat_wire_message(m) for m in payload["messages"]]
        try:
            return self._dispatch_provider(bundle.client.chat.completions.create, payload)
        except TypeError as exc:
            stripped = self._without_prompt_cache_hints(payload)
            if stripped != payload:
                try:
                    return self._dispatch_provider(bundle.client.chat.completions.create, stripped)
                except Exception as retry_exc:
                    self._raise_if_native_tools_rejected(payload=stripped, exc=retry_exc)
                    raise
            self._raise_if_native_tools_rejected(payload=payload, exc=exc)
            raise
        except Exception as exc:
            if self._should_retry_without_prompt_cache_hints(exc):
                stripped = self._without_prompt_cache_hints(payload)
                if stripped != payload:
                    try:
                        return self._dispatch_provider(bundle.client.chat.completions.create, stripped)
                    except Exception as retry_exc:
                        self._raise_if_native_tools_rejected(payload=stripped, exc=retry_exc)
                        raise
            self._raise_if_native_tools_rejected(payload=payload, exc=exc)
            raise

    def _raise_if_native_tools_rejected(
        self,
        *,
        payload: dict[str, Any],
        exc: Exception,
    ) -> None:
        if not payload.get("tools") or not self._provider_explicitly_rejects_native_tools(exc):
            return
        # Native schemas and the legacy JSON tool description are mutually
        # exclusive in the host prompt. Retrying this already-built request
        # after silently deleting ``tools`` leaves the model with neither
        # usable surface and can strand an open tool turn. Capability routing
        # must choose the compatible surface before transport; an unexpected
        # provider rejection is therefore an explicit configuration failure.
        self._record_metric("native_tool_provider_unsupported")
        raise RuntimeError("native_tools_unsupported") from exc

    def _provider_explicitly_rejects_native_tools(self, exc: Exception) -> bool:
        message = " ".join(str(exc or "").strip().lower().split())
        if not message:
            return False
        explicit_phrases = (
            "does not support tools",
            "doesn't support tools",
            "tools are not supported",
            "tool_choice is not supported",
            "tool choice is not supported",
            "function calling is not supported",
            "function calling not supported",
            "does not support function calling",
            "unknown field: tools",
            "unknown field `tools`",
            "unknown parameter: tools",
            "unrecognized request argument supplied: tools",
        )
        return any(phrase in message for phrase in explicit_phrases) or bool(
            re.search(r"unsupported(?:_|\s|-)*(?:parameter|field)?(?:_|\s|-)*(?:tools?|tool_choice)", message)
        )

    def _persistent_turn_messages_from_payload(
        self,
        *,
        payload: dict[str, Any],
        bundle: ModelBundle,
        history_turns: list[dict[str, Any]] | None,
        ephemeral_turns: list[dict[str, Any]] | None,
        post_user_turns: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        """Return the exact provider-shaped persistent messages for this turn.

        The runtime owns message assembly, so it can identify this slot before
        request-scoped evidence is appended, then include any explicit tool
        continuations after that evidence. Hosts must not infer these messages
        from a contiguous suffix of the finished request.
        """

        if isinstance(payload, _CompletionPayload):
            persistent = [dict(payload["messages"][index]) for index in payload.persistent_message_indexes]
            return [continuation.public_message(m) for m in persistent]
        messages = [
            dict(message)
            for message in list(payload.get("messages") or [])
            if isinstance(message, dict)
        ]
        history_count = len(self._normalize_history_turns_for_payload(history_turns, bundle=bundle))
        system_offset = int(
            bool(messages and str(messages[0].get("role") or "").strip().lower() == "system")
        )
        current_index = system_offset + history_count
        if current_index >= len(messages):
            return []
        current = dict(messages[current_index])
        if str(current.get("role") or "").strip().lower() != "user":
            return []
        ephemeral_messages, _tail = self._split_ephemeral_turns_for_payload(ephemeral_turns, bundle=bundle)
        ephemeral_count = len(ephemeral_messages)
        post_user_count = len(self._normalize_history_turns_for_payload(post_user_turns, bundle=bundle))
        post_user_start = current_index + 1 + ephemeral_count
        if post_user_start + post_user_count > len(messages):
            return []
        persistent = [current, *[dict(message) for message in messages[post_user_start : post_user_start + post_user_count]]]
        # DeepSeek requires reasoning_content to be replayed on the next
        # provider request, but it is private open-turn wire state. The request
        # observer is MemCore's durable boundary, so remove it here rather than
        # teaching MemCore a private reasoning record type.
        return [continuation.public_message(m) for m in persistent]

    def _observe_completion_request(
        self,
        *,
        bundle: ModelBundle,
        payload: dict[str, Any],
        observer: Callable[[dict[str, Any]], Any] | None,
        persistent_turn_messages: list[dict[str, Any]] | None = None,
    ) -> None:
        """Expose the final provider request shape immediately before transport.

        The callback is deliberately generic: MemCore remains owned by the host,
        while the runtime is the only place that knows the exact Chat/Responses
        conversion. Request contents are never logged by this boundary.
        """

        if not callable(observer):
            return
        from .memory_prefix import CapturedCompletionRequest
        messages = [continuation.public_message(message) for message in list(payload.get("messages") or []) if isinstance(message, dict)]
        system_message = (
            dict(messages[0])
            if messages and str(messages[0].get("role") or "").strip().lower() == "system"
            else {}
        )
        chat_history = messages[1:] if system_message else messages
        protocol = str(
            getattr(bundle.client, "_akane_protocol", getattr(bundle.client, "protocol", "")) or ""
        ).strip().lower()
        if self._is_responses_protocol(bundle):
            wire_request = self._responses_payload_from_chat({**payload, "messages": messages})
            audit_history = [dict(item) for item in list(wire_request.get("input") or []) if isinstance(item, dict)]
            system_prefix: Any = wire_request.get("instructions", "")
            tool_schema: Any = wire_request.get("tools") or []
        else:
            audit_history = [dict(message) for message in chat_history]
            system_prefix = {
                "message": system_message,
                "extra_blocks": list(payload.get("system_extra_blocks") or []),
            }
            tool_schema = list(payload.get("tools") or [])
        result = observer(
            {
                **({"_compaction_request": CapturedCompletionRequest.capture(
                        bundle, payload, context_window=self._settings_view().llm_context_window)}
                   if getattr(observer, "capture_compaction_context", False) else {}),
                "protocol": protocol,
                "model_route": {"protocol": protocol, "model": str(payload.get("model") or "")},
                "system_prefix": system_prefix,
                "tool_schema": tool_schema,
                "persistent_turn_messages": [
                    dict(message)
                    for message in list(persistent_turn_messages or [])
                    if isinstance(message, dict)
                ],
                "history_messages": [dict(message) for message in chat_history],
                "audit_history_messages": audit_history,
            }
        )
        if isinstance(result, dict) and not bool(result.get("ok", True)):
            reason = str(result.get("reason") or result.get("status") or "request_observer_rejected").strip()
            raise RuntimeError(f"request_observer_rejected:{reason[:120]}")

    def _create_responses_completion(self, *, bundle: ModelBundle, payload: dict[str, Any]) -> Any:
        request = self._responses_payload_from_chat(payload)
        self._record_responses_request_audit_if_enabled(bundle=bundle, request=request)
        try:
            response = self._dispatch_provider(bundle.client.responses.create, request)
        except TypeError:
            stripped = self._without_prompt_cache_hints(request)
            if stripped == request:
                raise
            response = self._dispatch_provider(bundle.client.responses.create, stripped)
        except Exception as exc:
            if not self._should_retry_without_prompt_cache_hints(exc):
                raise
            stripped = self._without_prompt_cache_hints(request)
            if stripped == request:
                raise
            response = self._dispatch_provider(bundle.client.responses.create, stripped)
        if bool(request.get("stream")):
            return _ResponsesStreamAdapter(response)
        return self._adapt_responses_result(response)

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

    def _extract_text(self, response: Any) -> str:
        try:
            return self._flatten_message_content(response.choices[0].message.content).strip()
        except Exception:
            return ""

    def _extract_reasoning_content(self, response: Any) -> str:
        message = self._chat_response_message(response)
        value = self._get_attr_or_key(message, "reasoning_content")
        return str(value or "").strip()

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
            return _completed_tool_preface_text(text)
        if not isinstance(parsed, dict):
            return ""
        speech = str(project_speech_units(parsed).get("speech") or "").strip()
        return _completed_tool_preface_text(speech)

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

    def _get_attr_or_key(self, value: Any, key: str) -> Any:
        if isinstance(value, dict):
            return value.get(key)
        return getattr(value, key, None)

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

    def _extract_stream_reasoning_content(self, chunk: Any) -> str:
        try:
            choice = chunk.choices[0]
        except Exception:
            choices = chunk.get("choices") if isinstance(chunk, dict) else None
            choice = list(choices or [None])[0]
        delta = self._get_attr_or_key(choice, "delta")
        return str(self._get_attr_or_key(delta, "reasoning_content") or "")
