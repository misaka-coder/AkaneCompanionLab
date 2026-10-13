"""Bounded stream-to-nonstream-to-uncached recovery with provider provenance."""

from __future__ import annotations

from typing import Any, Generator

from ..tool_invocation import NATIVE_TOOL_CALL_FIELD, NATIVE_TOOL_CALLS_FIELD
from .final_response_contracts import FinalResponseJSONAttempt, FinalResponseRequest, FinalResponseStreamFallback


class FinalResponseFallbackMixin:
    """Retry an undelivered stream using the equivalent provider request."""

    def _call_final_response_json_attempt(self, request_kwargs: dict[str, Any]) -> FinalResponseJSONAttempt:
        metrics_before = self.llm.snapshot_metrics() if hasattr(self.llm, "snapshot_metrics") else {}
        result = (
            self.llm.call_chat_json_result(**request_kwargs) if hasattr(self.llm, "call_chat_json_result") else None
        )
        parsed = result.parsed if result is not None else self.llm.call_chat_json(**request_kwargs)
        error = str(getattr(result, "error", "") or "").strip()
        metrics_after = self.llm.snapshot_metrics() if hasattr(self.llm, "snapshot_metrics") else {}
        return FinalResponseJSONAttempt(
            result=result,
            parsed=parsed,
            error=error,
            provider_output_raw=str(getattr(result, "raw_text", "") or ""),
            parse_fallback=self._llm_result_used_fallback(
                result,
                metrics_before=metrics_before,
                metrics_after=metrics_after,
            ),
            transport_failure=(
                bool(error and error != "response_truncated")
                or int(metrics_after.get("errors", 0) or 0) > int(metrics_before.get("errors", 0) or 0)
            ),
        )

    def _recover_final_response_stream_transport(
        self,
        *,
        request: FinalResponseRequest,
        request_kwargs: dict[str, Any],
        stream_rate_limited: bool,
    ) -> Generator[dict[str, Any], None, FinalResponseStreamFallback]:
        self._record_final_response_metric("chat_stream_nonstream_fallbacks")
        normalized: dict[str, Any] = {}
        used_uncached = False
        # One equivalent request, followed by one uncached request only when
        # transport failed. Format defects never trigger the uncached branch.
        for uncached in (False, True):
            yield {
                "type": "turn_progress", "phase": "retrying_model",
                "transport": "nonstream_uncached" if uncached else "nonstream",
            }
            kwargs = {**request_kwargs, "prompt_cache_key": ""} if uncached else request_kwargs
            if uncached:
                self._record_final_response_metric("chat_stream_uncached_fallbacks")
                used_uncached = True
            attempt = self._call_final_response_json_attempt(kwargs)
            failure = self._final_response_provider_failure(attempt.error)
            if (
                failure is None
                and not uncached
                and stream_rate_limited
                and self._provider_error_is_rate_limited(attempt.error)
            ):
                self._record_final_rate_limit_metric()
                self._record_final_service_failure_metric()
                failure = self._final_service_failure_output(reason="provider_rate_limited")
            if failure is not None:
                return FinalResponseStreamFallback(
                    normalized=normalized,
                    provider_output_raw=attempt.provider_output_raw,
                    transport_failure=attempt.transport_failure,
                    recovered=False,
                    failure_output=failure,
                )
            normalized = self._normalize_final_response_attempt(
                request=request,
                raw_result=attempt.parsed,
                result=attempt.result,
                parse_fallback=attempt.parse_fallback,
            )
            if not attempt.transport_failure or uncached:
                break
        self._attach_tool_execution_receipts(normalized, request.generation_context)
        if normalized.get(NATIVE_TOOL_CALL_FIELD) or normalized.get(NATIVE_TOOL_CALLS_FIELD):
            preface_text = str(normalized.get("speech") or "").strip()
            if preface_text:
                normalized["_native_preface_text"] = preface_text
        terminal = (
            None
            if attempt.transport_failure
            else self._final_attempt_terminal_output(
                normalized=normalized,
                parse_fallback=attempt.parse_fallback,
                provider_output_raw=attempt.provider_output_raw,
                **request.output_inputs(),
            )
        )
        recovered = terminal is not None
        if recovered:
            normalized = terminal
            self._record_final_response_metric("chat_stream_nonstream_recoveries")
            if used_uncached:
                self._record_final_response_metric("chat_stream_uncached_recoveries")
        else:
            self._record_final_response_metric("chat_stream_nonstream_fallback_failures")
        return FinalResponseStreamFallback(
            normalized=normalized,
            provider_output_raw=attempt.provider_output_raw,
            transport_failure=attempt.transport_failure,
            recovered=recovered,
        )
