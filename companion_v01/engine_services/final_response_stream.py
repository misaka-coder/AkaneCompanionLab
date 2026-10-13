"""Streaming final-response orchestration with request-owned delivery state."""

from __future__ import annotations

from typing import Any, Generator

from ..client_protocol import ClientProtocolContext
from ..resource_manifest import ResourceManifest
from .final_response_contracts import FinalResponseRequest, FinalResponseStreamState


class FinalResponseStreamMixin:
    """Coordinate attempts, preserving event order and bounded recovery."""

    def _stream_final_response(
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
    ) -> Generator[dict[str, Any], None, dict[str, Any]]:
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
        speaker_identity = self._resolve_turn_speaker_identity(
            client_context,
            character_pack_id,
        )
        yield {
            "type": "turn_start",
            "speaker": speaker_identity["assistant_name"],
        }
        return (yield from self._execute_final_response_stream(request))

    def _execute_final_response_stream(
        self,
        request: FinalResponseRequest,
    ) -> Generator[dict[str, Any], None, dict[str, Any]]:
        context = request.generation_context
        max_attempts = self._final_response_max_attempts(context)
        cache_key = self._final_prompt_cache_key(context)
        self._register_cache_usage_scope(cache_key, context, request.client_context)
        state = FinalResponseStreamState()
        for attempt in range(1, max_attempts + 1):
            retry_mode = attempt > 1
            if retry_mode:
                yield {"type": "turn_progress", "phase": "retrying_model", "attempt": attempt}
            state.attempts_made += 1
            metrics_before = self.llm.snapshot_metrics() if hasattr(self.llm, "snapshot_metrics") else {}
            delivered_before = list(state.delivered_segments)
            units_before = list(state.delivered_utterances)
            retry_note = ""
            if retry_mode:
                retry_note = (
                    self._stream_final_response_recovery_feedback(
                        delivered_before or [unit["text"] for unit in units_before],
                    )
                    if delivered_before or units_before
                    else self._final_response_retry_note(context, previous_feedback=state.retry_feedback)
                )
            request_kwargs = self._build_final_response_request_kwargs(
                **request.provider_inputs(prompt_cache_key=cache_key, retry_note=retry_note),
            )
            forwarded = yield from self._forward_final_response_attempt(
                request=request,
                request_kwargs=request_kwargs,
                delivered_segments=state.delivered_segments,
                retry_mode=retry_mode,
            )
            result = forwarded.result
            metrics_after = self.llm.snapshot_metrics() if hasattr(self.llm, "snapshot_metrics") else {}
            parse_fallback = self._llm_result_used_fallback(
                result,
                metrics_before=metrics_before,
                metrics_after=metrics_after,
            )
            error = str(getattr(result, "error", "") or "").strip()
            rate_limited = self._provider_error_is_rate_limited(error)
            state.provider_output_raw = str(getattr(result, "raw_text", "") or "")
            failure = self._final_response_provider_failure(error)
            if failure is not None:
                return failure
            if error:
                state.transport_failures += 1
                state.stream_error = error
                state.stream_partial = {
                    "emotion": str(getattr(result, "latest_emotion", "") or ""),
                    "speech": str(getattr(result, "latest_speech", "") or ""),
                }
            state.normalized = self._normalize_final_response_attempt(
                request=request,
                raw_result=getattr(result, "parsed", None),
                result=result,
                parse_fallback=parse_fallback,
            )
            self._attach_tool_execution_receipts(state.normalized, context)
            preface_text = str(getattr(result, "native_preface_text", "") or "").strip()
            if preface_text:
                state.normalized["_native_preface_text"] = preface_text
            terminal = (
                None
                if error
                else self._final_attempt_terminal_output(
                    normalized=state.normalized,
                    parse_fallback=parse_fallback,
                    provider_output_raw=state.provider_output_raw,
                    **request.output_inputs(),
                )
            )
            if terminal is not None:
                state.normalized = self._merge_streamed_speech_into_final(
                    terminal,
                    delivered_segments=delivered_before if retry_mode else [],
                    delivered_utterances=units_before if retry_mode and (
                        state.had_speech_units or isinstance(terminal.get("utterances"), list)
                    ) else [],
                )
                if self._final_output_has_tool_call(state.normalized) and len(state.delivered_segments) > len(
                    delivered_before
                ):
                    state.normalized["_native_preface_streamed"] = True
                state.stream_error = ""
                state.stream_partial = {}
                state.retry_feedback = ""
                break
            state.delivered_utterances = self._merge_delivered_utterances(
                state.delivered_utterances, forwarded.delivered_utterances,
            )
            state.had_speech_units |= forwarded.had_speech_units
            state.retry_feedback = self._final_response_retry_feedback(
                raw_result=getattr(result, "parsed", None),
                normalized=state.normalized,
                parse_fallback=parse_fallback,
                provider_output_raw=state.provider_output_raw,
            )
            if not rate_limited:
                self._log_final_response_retry(
                    prompt_scope=str(context.get("prompt_scope") or ""),
                    attempt=attempt,
                    feedback=state.retry_feedback,
                    raw_result=getattr(result, "parsed", None),
                    provider_output_raw=state.provider_output_raw,
                    finish_reason=getattr(result, "finish_reason", ""),
                    native_finish_reason=getattr(result, "native_finish_reason", ""),
                )
            if forwarded.had_speech_chunk:
                yield {
                    "type": "speech_reset",
                    "speech": "\n".join(state.delivered_segments),
                    "attempt": attempt,
                    "reason": "attempt_not_deliverable",
                }
            if error:
                if state.delivered_segments or state.delivered_utterances:
                    if attempt < max_attempts:
                        self._record_final_response_metric("chat_final_response_retries")
                        continue
                    break
                fallback = yield from self._recover_final_response_stream_transport(
                    request=request,
                    request_kwargs=request_kwargs,
                    stream_rate_limited=rate_limited,
                )
                if fallback.failure_output is not None:
                    return fallback.failure_output
                state.normalized = fallback.normalized
                state.provider_output_raw = fallback.provider_output_raw
                state.nonstream_transport_failure = fallback.transport_failure
                if fallback.recovered:
                    state.stream_error = ""
                    state.stream_partial = {}
                    state.retry_feedback = ""
                break
            if attempt < max_attempts:
                self._record_final_response_metric("chat_final_response_retries")
        return (yield from self._finish_final_response_stream(request, state, prompt_cache_key=cache_key))

    def _finish_final_response_stream(
        self,
        request: FinalResponseRequest,
        state: FinalResponseStreamState,
        *,
        prompt_cache_key: str,
    ) -> Generator[dict[str, Any], None, dict[str, Any]]:
        if state.stream_error and (state.delivered_segments or state.delivered_utterances):
            recovered = self._recover_final_response_plain_text(
                **request.recovery_inputs(prompt_cache_key=prompt_cache_key),
                delivered_speech_segments=state.delivered_segments or [
                    unit["text"] for unit in state.delivered_utterances
                ],
            )
            if recovered is not None:
                state.normalized = self._merge_streamed_speech_into_final(
                    recovered,
                    delivered_segments=state.delivered_segments,
                    delivered_utterances=state.delivered_utterances if state.had_speech_units else [],
                )
                state.stream_error = ""
                state.stream_partial = {}
                state.retry_feedback = ""
        # Forwarded events are never replayed during completion.
        if state.stream_error:
            yield {"type": "stream_error", "message": state.stream_error, "partial": state.stream_partial}
            if state.nonstream_transport_failure and state.transport_failures >= state.attempts_made:
                self._record_final_service_failure_metric()
                state.normalized = self._final_service_failure_output(reason="provider_unreachable")
            else:
                state.normalized["_transient_final_failure"] = True
                state.normalized.pop("_provider_output_raw", None)
        elif self._final_output_has_tool_call(state.normalized):
            if state.provider_output_raw:
                state.normalized["_provider_output_raw"] = state.provider_output_raw
        elif (
            self._is_retryable_final_output(state.normalized)
            or self._raw_tool_call_is_pending(state.provider_output_raw)
            or state.retry_feedback in {"json_external_text", "history_record_output"}
        ):
            recovered = self._recover_final_response_plain_text(
                **request.recovery_inputs(prompt_cache_key=prompt_cache_key),
                delivered_speech_segments=state.delivered_segments,
            )
            if recovered is not None:
                state.normalized = self._merge_streamed_speech_into_final(
                    recovered,
                    delivered_segments=state.delivered_segments,
                    delivered_utterances=state.delivered_utterances if state.had_speech_units else [],
                )
            elif state.transport_failures >= state.attempts_made and state.attempts_made > 0:
                self._record_final_service_failure_metric()
                state.normalized = self._final_service_failure_output(reason="provider_unreachable")
            else:
                state.normalized["_transient_final_failure"] = True
                state.normalized.pop("_provider_output_raw", None)
        elif state.provider_output_raw:
            state.normalized["_provider_output_raw"] = state.provider_output_raw
        context = request.generation_context
        self._attach_nonfatal_memcore_failure(
            state.normalized,
            context.get("memcore_projection_recovery") or context.get("memcore_request_projection_failure"),
        )
        return state.normalized
