"""Stable model-runtime calls used by engine, memory, subagents and voice."""

from __future__ import annotations

from typing import Any, Callable, Generator

from ..model_request_control import ModelRequestCancelled
from .memory_prefix import MemoryPrefixCallsMixin
from .models import (
    ChatJSONResult,
    ChatJSONStreamResult,
    ChatTextResult,
    ModelExecutionTarget,
    NDJSONCallResult,
)


class ModelCallsMixin(MemoryPrefixCallsMixin):
    """Methods use the state initialized by LLMRuntime."""

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

    def call_memcore_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        fallback: dict[str, Any],
        temperature: float = 0.2,
        prompt_cache_key: str = "",
        request_timeout_s: float = 30.0,
        chat_model_override: str = "",
    ) -> dict[str, Any]:
        """Use the explicit summary route, or this conversation's chat target."""
        self._record_metric("aux_json_calls")
        with self._bundle_lock:
            bundle = self.memcore_summary
            if bundle is None:
                bundle = self._chat_bundle_for_override(chat_model_override)
        if bundle.client is None:
            return dict(fallback)
        return self._call_json(
            bundle=bundle,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback=fallback,
            temperature=temperature,
            prompt_cache_key=prompt_cache_key,
            request_timeout_s=request_timeout_s,
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
        ephemeral_turns: list[dict[str, Any]] | None = None,
        post_user_turns: list[dict[str, Any]] | None = None,
        prompt_audit_sections: list[dict[str, Any]] | None = None,
        chat_model_override: str = "",
        request_observer: Callable[[dict[str, Any]], Any] | None = None,
        execution_target: ModelExecutionTarget | None = None,
    ) -> dict[str, Any]:
        return self.call_chat_json_result(
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
            chat_model_override=chat_model_override,
            request_observer=request_observer,
            execution_target=execution_target,
        ).parsed

    def call_chat_json_result(
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
        ephemeral_turns: list[dict[str, Any]] | None = None,
        post_user_turns: list[dict[str, Any]] | None = None,
        prompt_audit_sections: list[dict[str, Any]] | None = None,
        chat_model_override: str = "",
        request_observer: Callable[[dict[str, Any]], Any] | None = None,
        execution_target: ModelExecutionTarget | None = None,
        max_output_tokens: int = 0,
        request_timeout_s: float = 0.0,
    ) -> ChatJSONResult:
        self._record_metric("chat_json_calls")
        return self._call_json_result(
            bundle=self._request_bundle(
                execution_target=execution_target,
                chat_model_override=chat_model_override,
            ),
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
            max_output_tokens=max_output_tokens,
            request_timeout_s=request_timeout_s,
            force_response_json=False,
        )

    def call_chat_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        prompt_cache_key: str = "",
        user_images: list[dict[str, Any]] | None = None,
        native_tools: list[dict[str, Any]] | None = None,
        system_extra_blocks: list[str] | None = None,
        history_turns: list[dict[str, Any]] | None = None,
        ephemeral_turns: list[dict[str, Any]] | None = None,
        post_user_turns: list[dict[str, Any]] | None = None,
        prompt_audit_sections: list[dict[str, Any]] | None = None,
        chat_model_override: str = "",
        request_observer: Callable[[dict[str, Any]], Any] | None = None,
        execution_target: ModelExecutionTarget | None = None,
        max_output_tokens: int = 0,
    ) -> ChatTextResult:
        """Plain-text completion retaining tool schemas but disabling calls.

        Used only as the terminal final-response recovery: the model authors
        the answer as ordinary text, the host wraps presentation defaults.
        The request keeps the structured attempts' schemas, stable prefix and
        cache key. Tool choice is always none; recovery feedback stays in the
        caller's tail.
        """
        self._record_metric("chat_text_calls")
        bundle = self._request_bundle(
            execution_target=execution_target,
            chat_model_override=chat_model_override,
        )
        try:
            request_payload = self._build_completion_kwargs(
                bundle=bundle,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=temperature,
                json_mode=False,
                prompt_cache_key=prompt_cache_key,
                user_images=user_images,
                native_tools=native_tools,
                native_tool_choice="none",
                system_extra_blocks=system_extra_blocks,
                history_turns=history_turns,
                ephemeral_turns=ephemeral_turns,
                post_user_turns=post_user_turns,
                prompt_audit_sections=prompt_audit_sections,
                max_output_tokens=max_output_tokens,
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
            text = self._extract_text(response)
            if self._note_truncation(response, phase="call_chat_text"):
                return ChatTextResult(text="", raw_text=text, error="response_truncated")
            if self._extract_native_tool_calls(response, native_tools=native_tools, bundle=bundle):
                self._record_metric("chat_text_tool_call_rejected")
                return ChatTextResult(text="", raw_text=text, error="unexpected_tool_call")
            return ChatTextResult(text=text, raw_text=text)
        except ModelRequestCancelled:
            raise
        except Exception as exc:
            self._record_metric("errors")
            self._capture_runtime_error(exc, phase="call_chat_text")
            return ChatTextResult(text="", raw_text="", error=str(exc or "").strip())

    def chat_supports_native_tools(
        self,
        *,
        chat_model_override: str = "",
        execution_target: ModelExecutionTarget | None = None,
    ) -> bool:
        bundle = self._request_bundle(
            execution_target=execution_target,
            chat_model_override=chat_model_override,
        )
        return self._should_send_native_tools(bundle)

    def chat_provider_protocol(
        self,
        *,
        chat_model_override: str = "",
        execution_target: ModelExecutionTarget | None = None,
    ) -> str:
        bundle = self._request_bundle(
            execution_target=execution_target,
            chat_model_override=chat_model_override,
        )
        client = getattr(bundle, "client", bundle)
        return str(getattr(client, "_akane_protocol", getattr(client, "protocol", "")) or "").strip().lower()

    def normalize_chat_history_turns(
        self,
        history_turns: list[dict[str, Any]] | None,
        *,
        chat_model_override: str = "",
        execution_target: ModelExecutionTarget | None = None,
    ) -> list[dict[str, Any]]:
        bundle = self._request_bundle(
            execution_target=execution_target,
            chat_model_override=chat_model_override,
        )
        return self._normalize_history_turns_for_payload(history_turns, bundle=bundle)

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
        ephemeral_turns: list[dict[str, Any]] | None = None,
        post_user_turns: list[dict[str, Any]] | None = None,
        prompt_audit_sections: list[dict[str, Any]] | None = None,
        chat_model_override: str = "",
        request_observer: Callable[[dict[str, Any]], Any] | None = None,
        execution_target: ModelExecutionTarget | None = None,
    ) -> Generator[dict[str, Any], None, ChatJSONStreamResult]:
        self._record_metric("chat_stream_calls")
        return self._stream_chat_json(
            bundle=self._request_bundle(
                execution_target=execution_target,
                chat_model_override=chat_model_override,
            ),
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
            ephemeral_turns=ephemeral_turns,
            post_user_turns=post_user_turns,
            prompt_audit_sections=prompt_audit_sections,
            request_observer=request_observer,
            force_response_json=False,
        )
