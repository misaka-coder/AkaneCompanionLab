"""Structured completions, exact raw results and transport recovery."""

from __future__ import annotations

from typing import Any, Callable
import json

from ..model_request_control import ModelRequestCancelled
from ..tool_invocation import (
    NATIVE_REASONING_CONTENT_FIELD,
    NATIVE_TOOL_CALLS_FIELD,
    NATIVE_TOOL_CALL_FIELD,
)
from .json_parsing import _memory_metadata_truth
from .models import ChatJSONResult, ChatJSONStreamResult, ModelBundle
from services import provider_continuation as continuation


class JSONCallsMixin:
    """Methods use the state initialized by LLMRuntime."""

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
        ephemeral_turns: list[dict[str, Any]] | None = None,
        post_user_turns: list[dict[str, Any]] | None = None,
        prompt_audit_sections: list[dict[str, Any]] | None = None,
        request_observer: Callable[[dict[str, Any]], Any] | None = None,
        request_timeout_s: float = 0.0,
        force_response_json: bool = True,
    ) -> dict[str, Any]:
        return self._call_json_result(
            bundle=bundle,
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
            ephemeral_turns=ephemeral_turns,
            post_user_turns=post_user_turns,
            prompt_audit_sections=prompt_audit_sections,
            request_observer=request_observer,
            request_timeout_s=request_timeout_s,
            force_response_json=force_response_json,
        ).parsed

    def _call_json_result(
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
        ephemeral_turns: list[dict[str, Any]] | None = None,
        post_user_turns: list[dict[str, Any]] | None = None,
        prompt_audit_sections: list[dict[str, Any]] | None = None,
        request_observer: Callable[[dict[str, Any]], Any] | None = None,
        max_output_tokens: int = 0,
        request_timeout_s: float = 0.0,
        force_response_json: bool = True,
    ) -> ChatJSONResult:
        native_requested = bool(self._normalize_native_tools(native_tools))
        content = ""
        finish_reasons: dict[str, str] = {}
        request_attempts = 1
        try:
            request_payload = self._build_completion_kwargs(
                bundle=bundle,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=temperature,
                json_mode=force_response_json,
                prompt_cache_key=prompt_cache_key,
                user_images=user_images,
                native_tools=native_tools,
                native_tool_choice=native_tool_choice,
                system_extra_blocks=system_extra_blocks,
                history_turns=history_turns,
                ephemeral_turns=ephemeral_turns,
                post_user_turns=post_user_turns,
                prompt_audit_sections=prompt_audit_sections,
                max_output_tokens=max_output_tokens,
                request_timeout_s=request_timeout_s,
            )
            self._observe_completion_request(
                bundle=bundle,
                payload=request_payload,
                observer=request_observer,
                persistent_turn_messages=self._persistent_turn_messages_from_payload(
                    payload=request_payload,
                    bundle=bundle,
                    history_turns=history_turns,
                    ephemeral_turns=ephemeral_turns,
                    post_user_turns=post_user_turns,
                ),
            )
            response = self._create_completion(bundle=bundle, payload=request_payload)
            self._record_cache_metrics(response, prompt_cache_key=prompt_cache_key,
                                       requested_model=str(request_payload.get("model") or getattr(bundle, "model", "") or ""))
            content = self._extract_text(response)
            finish_reasons = {
                "finish_reason": self._response_finish_reason(response),
                "native_finish_reason": self._response_finish_reason(response, native=True),
            }
            if self._note_truncation(response, phase="call_json"):
                fallback_payload = dict(fallback)
                metadata_status, metadata_present = _memory_metadata_truth(
                    fallback_payload,
                    accepted_status="accepted_host",
                    require_signal=True,
                )
                return ChatJSONResult(
                    parsed=fallback_payload,
                    raw_text=content,
                    **finish_reasons,
                    error="response_truncated",
                    fallback_used=True,
                    metadata_status=metadata_status,
                    metadata_present=metadata_present,
                )
            native_tool_calls = self._extract_native_tool_calls(response, native_tools=native_tools, bundle=bundle)
            if native_tool_calls:
                self._record_metric("native_tool_call_extracted")
                parsed = {
                    NATIVE_TOOL_CALLS_FIELD: native_tool_calls,
                    NATIVE_TOOL_CALL_FIELD: native_tool_calls[0],
                    "tool_call": None,
                }
                native_preface_text = self._native_preface_text_from_content(content)
                if native_preface_text:
                    parsed["speech"] = native_preface_text
                reasoning_content = self._extract_reasoning_content(response)
                if reasoning_content:
                    parsed[NATIVE_REASONING_CONTENT_FIELD] = reasoning_content
                extras: dict = {}
                continuation.collect_chat_extras(response, extras)
                state = continuation.capture(response, bundle=bundle, calls=native_tool_calls,
                                             extras=continuation.chat_extras(extras))
                if state:
                    parsed[continuation.RESULT_FIELD] = state
                metadata_status, metadata_present = _memory_metadata_truth(
                    parsed,
                    accepted_status="accepted_model",
                )
                return ChatJSONResult(
                    parsed=parsed,
                    raw_text=content,
                    **finish_reasons,
                    metadata_status=metadata_status,
                    metadata_present=metadata_present,
                )
            if native_requested:
                self._record_metric("native_tool_no_call")
            parsed = self._extract_json(content)
            if isinstance(parsed, dict):
                from ..speech_units import project_speech_units
                parsed = project_speech_units(parsed)
                metadata_status, metadata_present = _memory_metadata_truth(
                    parsed,
                    accepted_status="accepted_model",
                )
                return ChatJSONResult(
                    parsed=parsed,
                    raw_text=content,
                    **finish_reasons,
                    metadata_status=metadata_status,
                    metadata_present=metadata_present,
                )
            recovered = self._recover_partial_chat_json(content, fallback=fallback)
            if isinstance(recovered, dict):
                metadata_status, metadata_present = _memory_metadata_truth(
                    recovered,
                    accepted_status="accepted_host",
                    require_signal=True,
                )
                return ChatJSONResult(
                    parsed=recovered,
                    raw_text=content,
                    **finish_reasons,
                    metadata_status=metadata_status,
                    metadata_present=metadata_present,
                )
            if not self._note_provider_finish_failure(phase="call_json", **finish_reasons):
                self._note_parse_fallback(content, phase="call_json")
        except ModelRequestCancelled:
            raise
        except Exception as exc:
            # Some OpenAI-compatible gateways have occasionally returned a
            # valid response followed by a second JSON document on
            # non-streaming requests. The SDK raises ``JSONDecodeError: Extra
            # data`` before we can inspect the model output. Retry the same
            # request through the streaming adapter only for that transport
            # shape; this keeps the normal path, prompt prefix and cache key
            # unchanged while allowing proactive plugin turns to complete.
            if isinstance(exc, json.JSONDecodeError):
                request_attempts = 2
                recovered = self._recover_json_call_via_stream(
                    bundle=bundle,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    fallback=fallback,
                    temperature=temperature,
                    prompt_cache_key=prompt_cache_key,
                    user_images=user_images,
                    native_tools=native_tools,
                    native_tool_choice=native_tool_choice,
                    early_tool_call_validator=None,
                    system_extra_blocks=system_extra_blocks,
                    history_turns=history_turns,
                    ephemeral_turns=ephemeral_turns,
                    post_user_turns=post_user_turns,
                    prompt_audit_sections=prompt_audit_sections,
                    request_observer=request_observer,
                    force_response_json=force_response_json,
                )
                if recovered is not None:
                    self._record_metric("chat_nonstream_stream_recoveries")
                    return recovered
            self._record_metric("errors")
            self._capture_runtime_error(exc, phase="call_json")
            self._record_metric("chat_json_fallbacks")
            fallback_payload = dict(fallback)
            metadata_status, metadata_present = _memory_metadata_truth(
                fallback_payload,
                accepted_status="accepted_host",
                require_signal=True,
            )
            return ChatJSONResult(
                parsed=fallback_payload,
                raw_text="",
                **finish_reasons,
                error=str(exc or "").strip(),
                request_attempts=request_attempts,
                fallback_used=True,
                metadata_status=metadata_status,
                metadata_present=metadata_present,
            )
        self._record_metric("chat_json_fallbacks")
        fallback_payload = dict(fallback)
        metadata_status, metadata_present = _memory_metadata_truth(
            fallback_payload,
            accepted_status="accepted_host",
            require_signal=True,
        )
        return ChatJSONResult(
            parsed=fallback_payload,
            raw_text=content,
            **finish_reasons,
            fallback_used=True,
            metadata_status=metadata_status,
            metadata_present=metadata_present,
        )

    def _recover_json_call_via_stream(
        self,
        **kwargs: Any,
    ) -> ChatJSONResult | None:
        """Recover a non-stream JSON decode failure with one stream.

        This is deliberately transport-only.  The request payload, model,
        prompt-cache key and native-tool schemas are forwarded unchanged;
        only the wire mode changes.  A failed or fallback stream is ignored so
        the caller still returns its ordinary structured fallback.
        """

        try:
            iterator = self._stream_chat_json(**kwargs)
            while True:
                next(iterator)
        except StopIteration as stopped:
            stream_result = stopped.value
        except ModelRequestCancelled:
            raise
        except Exception:
            return None
        if not isinstance(stream_result, ChatJSONStreamResult):
            return None
        if stream_result.error or stream_result.fallback_used:
            return None
        return ChatJSONResult(
            parsed=dict(stream_result.parsed or {}),
            request_attempts=2,
            raw_text=str(stream_result.raw_text or ""),
            error=str(stream_result.error or ""),
            fallback_used=False,
            metadata_status=str(stream_result.metadata_status or "missing"),
            metadata_present=bool(stream_result.metadata_present),
            finish_reason=stream_result.finish_reason,
            native_finish_reason=stream_result.native_finish_reason,
        )
