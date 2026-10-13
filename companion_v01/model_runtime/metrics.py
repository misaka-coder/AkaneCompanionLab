"""Instance metrics, bounded error details and provider finish diagnostics."""

from __future__ import annotations

from typing import Any
import logging
import re


logger = logging.getLogger("akane.llm_runtime")


SECRET_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._~+/=-]{8,})"),
    re.compile(r"(?i)((?:api[_-]?key|authorization|x-api-key)\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
)


class RuntimeMetricsMixin:
    """Methods use the state initialized by LLMRuntime."""

    def _record_cache_metrics(self, response: Any, *, prompt_cache_key: str = "", requested_model: str = "") -> None:
        try:
            usage = getattr(response, "usage", None)
            if usage is None and isinstance(response, dict):
                usage = response.get("usage")
            normalized_cache_key = str(prompt_cache_key or "")
            scoped = normalized_cache_key in getattr(self, "_cache_usage_scopes", {})
            if usage is None and not scoped:
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
            if not creation:
                creation = self._nested_usage_int(usage, "input_tokens_details", "cache_write_tokens")
            if not creation:
                creation = self._nested_usage_int(usage, "prompt_tokens_details", "cache_write_tokens")
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
                if self._cache_read_reported(usage):
                    self._record_metric("cache_reported_input_tokens", reported_input)
                else:
                    self._record_metric("cache_unreported_calls")
                if read:
                    self._record_metric("cache_hit_calls")
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
            if scoped or (reported_input and self._should_record_prompt_audit(normalized_cache_key)):
                self._append_prompt_usage_audit(
                    prompt_cache_key=normalized_cache_key,
                    read_tokens=read,
                    creation_tokens=creation,
                    input_tokens=reported_input,
                    output_tokens=reported_output,
                    response=response,
                    requested_model=requested_model,
                )
        except Exception as exc:
            self._record_metric("cache_metrics_errors")
            logger.warning("Cache usage could not be recorded: %s", type(exc).__name__)

    @staticmethod
    def _cache_read_reported(usage: Any) -> bool:
        def get(value, key):
            return value.get(key) if isinstance(value, dict) else getattr(value, key, None)

        normalized = get(usage, "_akane_cache_read_reported")
        if isinstance(normalized, bool):
            return normalized
        candidates = [get(usage, "cache_read_input_tokens"), get(usage, "prompt_cache_hit_tokens")]
        candidates.extend(get(get(usage, parent), "cached_tokens")
                          for parent in ("input_tokens_details", "prompt_tokens_details"))
        for value in candidates:
            try:
                if value is not None and not isinstance(value, bool) and int(value) >= 0:
                    return True
            except (TypeError, ValueError, OverflowError):
                continue
        return False

    def _usage_int(self, usage: Any, key: str) -> int:
        value = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, 0)
        try:
            return int(value or 0)
        except Exception:
            return 0

    def _nested_usage_int(self, usage: Any, parent: str, key: str) -> int:
        nested = usage.get(parent) if isinstance(usage, dict) else getattr(usage, parent, None)
        return self._usage_int(nested, key) if nested is not None else 0

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

    @staticmethod
    def _response_finish_reason(response: Any, *, native: bool = False) -> str:
        try:
            choices = response.get("choices") if isinstance(response, dict) else getattr(response, "choices", None)
            choice = list(choices or [])[0]
            field = "native_finish_reason" if native else "finish_reason"
            reason = (
                str(choice.get(field) or "")
                if isinstance(choice, dict)
                else str(getattr(choice, field, "") or "")
            ).strip().lower()
            # These are protocol enums, not provider prose or diagnostic bodies.
            return reason if re.fullmatch(r"[a-z0-9_]{1,64}", reason) else ""
        except Exception:
            return ""

    def _note_provider_finish_failure(
        self, *, phase: str, finish_reason: str = "", native_finish_reason: str = ""
    ) -> bool:
        """Keep upstream tool-generation failures distinct from JSON parsing.

        Diagnostic only: do not turn this into a transport error or change the
        engine's existing invalid-decision retry/fallback path.
        """
        if "malformed_function_call" not in {finish_reason, native_finish_reason}:
            return False
        self._record_metric("provider_malformed_function_call")
        detail = {
            "phase": phase,
            "type": "ProviderMalformedFunctionCall",
            "message": (
                f"finish_reason={finish_reason} native_finish_reason={native_finish_reason}; "
                "upstream failed to generate a valid tool call"
            ),
        }
        lock = getattr(self, "_last_error_lock", None)
        if lock is not None:
            with lock:
                self._last_error = detail
        logger.warning("llm provider decision failed phase=%s type=%s %s",
                       phase, detail["type"], detail["message"])
        return True

    def _note_truncation(self, response: Any, *, phase: str) -> bool:
        """Surface silent length-truncation through the metric + last-error
        channels (this file's structured-failure pattern; INV-3).

        A provider output cap can still cut the response mid-JSON, which then
        parses or recovers as if it were complete. The Anthropic stop
        reason maps to finish_reason="length"; without this, a truncated reply
        is indistinguishable from a normal short answer or a fallback.
        """
        finish_reason = self._response_finish_reason(response)
        if finish_reason != "length":
            return False
        sample = self._extract_text(response)
        self._record_metric("response_truncated")
        lock = getattr(self, "_last_error_lock", None)
        if lock is None:
            return True
        detail = {
            "phase": str(phase or ""),
            "type": "ResponseTruncated",
            "message": self._sanitize_error_message(
                f"finish_reason=length, output cut off (chars={len(sample)}); tail={sample[-200:]!r}"
            ),
        }
        with lock:
            self._last_error = detail
        return True

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
