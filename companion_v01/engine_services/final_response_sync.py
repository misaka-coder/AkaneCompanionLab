"""Synchronous final-response attempts and bounded structured/text recovery."""

from __future__ import annotations

from typing import Any

from ..client_protocol import ClientProtocolContext
from ..resource_manifest import ResourceManifest


class FinalResponseSyncMixin:
    """Preserve synchronous provider decisions and retry limits."""

    def _build_final_response(
        self,
        *,
        session_id: str,
        profile_user_id: str,
        user_message: str,
        recent_raw: list[dict[str, Any]],
        recent_episodic_summaries: list[dict[str, Any]],
        recent_semantic_summaries: list[dict[str, Any]],
        confirmed_snippets: list[str],
        now_ts: int,
        current_visual_payload: Any = None,
        extra_user_context: str = "",
        stable_system_context: str = "",
        client_context: ClientProtocolContext | None = None,
        resource_manifest: ResourceManifest | None = None,
        character_pack_id: str = "",
        user_images: list[dict[str, Any]] | None = None,
        allow_tool_call: bool = True,
        final_debug_enabled: bool | None = None,
        chat_model_override: str = "",
        execution_target: Any = None,
        post_user_turns: list[dict[str, Any]] | None = None,
        prompt_exclude_source_ids: list[str] | None = None,
        domain_profile_id: str = "",
        prompt_scope: str = "",
        request_projection_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._is_multimodal_unavailable_target(execution_target):
            return self._multimodal_unavailable_output(
                str(execution_target.get("reason") or "multimodal_model_unavailable")
            )
        request = self._prepare_final_response_request(
            session_id=session_id,
            user_message=user_message,
            recent_raw=recent_raw,
            recent_episodic_summaries=recent_episodic_summaries,
            recent_semantic_summaries=recent_semantic_summaries,
            confirmed_snippets=confirmed_snippets,
            now_ts=now_ts,
            current_visual_payload=current_visual_payload,
            profile_user_id=profile_user_id,
            extra_user_context=extra_user_context,
            stable_system_context=stable_system_context,
            client_context=client_context,
            resource_manifest=resource_manifest,
            character_pack_id=character_pack_id,
            allow_tool_call=allow_tool_call,
            final_debug_enabled=final_debug_enabled,
            chat_model_override=chat_model_override,
            execution_target=execution_target,
            post_user_turns=post_user_turns,
            prompt_exclude_source_ids=prompt_exclude_source_ids,
            domain_profile_id=domain_profile_id,
            prompt_scope=prompt_scope,
            user_images=user_images,
            request_projection_state=request_projection_state,
        )
        if request.failure_output is not None:
            return request.failure_output
        generation_context = request.generation_context
        max_attempts = self._final_response_max_attempts(generation_context)
        prompt_cache_key = self._final_prompt_cache_key(generation_context)
        self._register_cache_usage_scope(prompt_cache_key, generation_context, client_context)
        normalized: dict[str, Any] = {}
        provider_output_raw = ""
        retry_feedback = ""
        transport_failures = 0
        rate_limit_failures = 0
        transport_retry_pending = False
        for attempt in range(1, max_attempts + 1):
            # A malformed decision retries against the same context and tool
            # surface. A legal tool call is a decision, not damaged final text.
            retry_mode = attempt > 1
            metrics_before = self.llm.snapshot_metrics() if hasattr(self.llm, "snapshot_metrics") else {}
            retry_note = (
                self._final_response_retry_note(
                    generation_context,
                    previous_feedback=retry_feedback,
                )
                if retry_mode and not transport_retry_pending
                else ""
            )
            request_kwargs = self._build_final_response_request_kwargs(
                **request.provider_inputs(prompt_cache_key=prompt_cache_key, retry_note=retry_note),
            )
            call_result = (
                self.llm.call_chat_json_result(**request_kwargs) if hasattr(self.llm, "call_chat_json_result") else None
            )
            result = call_result.parsed if call_result is not None else self.llm.call_chat_json(**request_kwargs)
            provider_output_raw = str(getattr(call_result, "raw_text", "") or "")
            call_error = str(getattr(call_result, "error", "") or "").strip()
            failure = self._final_response_provider_failure(call_error)
            if failure is not None:
                return failure
            if call_error:
                transport_failures += 1
            if self._provider_error_is_rate_limited(call_error):
                rate_limit_failures += 1
                if rate_limit_failures >= 2 or attempt >= max_attempts:
                    self._record_final_rate_limit_metric()
                    self._record_final_service_failure_metric()
                    return self._final_service_failure_output(reason="provider_rate_limited")
                transport_retry_pending = True
                if hasattr(self.llm, "record_metric"):
                    self.llm.record_metric("chat_final_rate_limit_retries")
                continue
            if call_error:
                # A host fallback object is not a model decision. Retry based
                # on transport/protocol provenance rather than inspecting the
                # fallback sentence for particular words.
                normalized = {}
                retry_feedback = "provider_error"
                transport_retry_pending = True
                if attempt < max_attempts:
                    if hasattr(self.llm, "record_metric"):
                        self.llm.record_metric("chat_final_response_retries")
                    continue
                break
            transport_retry_pending = False
            metrics_after = self.llm.snapshot_metrics() if hasattr(self.llm, "snapshot_metrics") else {}
            parse_fallback = self._llm_result_used_fallback(
                call_result,
                metrics_before=metrics_before,
                metrics_after=metrics_after,
            )
            normalized = self._normalize_final_response_attempt(
                request=request,
                raw_result=result,
                result=call_result,
                parse_fallback=parse_fallback,
            )
            self._attach_tool_execution_receipts(normalized, generation_context)
            self._attach_nonfatal_memcore_failure(
                normalized,
                generation_context.get("memcore_projection_recovery")
                or generation_context.get("memcore_request_projection_failure"),
            )
            terminal_output = self._final_attempt_terminal_output(
                normalized=normalized,
                parse_fallback=parse_fallback,
                provider_output_raw=provider_output_raw,
                **request.output_inputs(),
            )
            if terminal_output is not None:
                return terminal_output
            retry_feedback = self._final_response_retry_feedback(
                raw_result=result,
                normalized=normalized,
                parse_fallback=parse_fallback,
                provider_output_raw=provider_output_raw,
            )
            self._log_final_response_retry(
                prompt_scope=str(generation_context.get("prompt_scope") or ""),
                attempt=attempt,
                feedback=retry_feedback,
                raw_result=result,
                provider_output_raw=provider_output_raw,
                finish_reason=getattr(call_result, "finish_reason", ""),
                native_finish_reason=getattr(call_result, "native_finish_reason", ""),
            )
            if attempt < max_attempts and hasattr(self.llm, "record_metric"):
                self.llm.record_metric("chat_final_response_retries")
        # Every structured generation failed. A legal tool call still continues
        # the ordinary tool loop. Otherwise one final same-context plain-text
        # generation (no JSON requirement, tool calls disabled) delivers the
        # model's own text; the host wraps presentation defaults. Only when every
        # provider call was unreachable do we emit an explicit service failure
        # notification instead of pretending the model answered.
        if self._final_output_has_tool_call(normalized):
            if provider_output_raw:
                normalized["_provider_output_raw"] = provider_output_raw
            return normalized
        if (
            self._is_retryable_final_output(normalized)
            or self._raw_tool_call_is_pending(provider_output_raw)
            or retry_feedback in {"json_external_text", "history_record_output"}
        ):
            recovered = self._recover_final_response_plain_text(
                **request.recovery_inputs(prompt_cache_key=prompt_cache_key),
            )
            if recovered is not None:
                self._attach_nonfatal_memcore_failure(
                    recovered,
                    generation_context.get("memcore_projection_recovery")
                    or generation_context.get("memcore_request_projection_failure"),
                )
                return recovered
            if transport_failures >= max_attempts and max_attempts > 0:
                self._record_final_service_failure_metric()
                return self._final_service_failure_output(reason="provider_unreachable")
            normalized["_transient_final_failure"] = True
            normalized.pop("_provider_output_raw", None)
        else:
            if provider_output_raw:
                normalized["_provider_output_raw"] = provider_output_raw
        return normalized
