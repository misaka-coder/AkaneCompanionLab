"""Explicit final-response failure states and associated metrics."""

from __future__ import annotations

import re
from typing import Any


class FinalResponseFailureMixin:
    """Preserve failure reasons without exposing internal projection details."""

    def _final_response_provider_failure(self, error: str) -> dict[str, Any] | None:
        """Failures which must stop every transport and recovery path."""
        if "request_observer_rejected:" in error:
            return self._memcore_projection_failure_output(
                {"status": "failed", "reason": "request_projection_record_failed"}
            )
        if "native_tools_unsupported" in error:
            self._record_final_service_failure_metric()
            return self._final_service_failure_output(reason="provider_native_tools_unsupported")
        if self._provider_error_is_timeout(error):
            # A full transport timeout has already consumed the wait budget.
            # Replaying it as nonstream and then uncached can add two more
            # 120-second waits without improving connectivity.
            self._record_final_response_metric("chat_final_provider_timeouts")
            self._record_final_service_failure_metric()
            return self._final_service_failure_output(reason="provider_timeout")
        return None

    @staticmethod
    def _provider_error_is_timeout(error: Any) -> bool:
        text = str(error or "").strip().lower()
        return text in {"timeout", "timed out"} or bool(re.search(
            r"\b(?:apitimeouterror|readtimeout|connecttimeout|pooltimeout|timeouterror)\b"
            r"|\b(?:request|read(?: operation)?|connect(?:ion)?|pool) (?:timed out|timeout)\b", text
        ))

    def _record_final_response_metric(self, name: str) -> None:
        record = getattr(self.llm, "record_metric", None)
        if callable(record):
            record(name)

    def _record_final_plain_text_recovery_metric(self) -> None:
        self._record_final_response_metric("chat_final_plain_text_recoveries")

    def _record_final_service_failure_metric(self) -> None:
        self._record_final_response_metric("chat_final_service_failures")

    def _record_final_rate_limit_metric(self) -> None:
        self._record_final_response_metric("chat_final_rate_limited")

    @staticmethod
    def _provider_error_is_rate_limited(error: Any) -> bool:
        text = str(error or "").strip().lower()
        if not text:
            return False
        return bool(re.search(r"\b429\b", text)) or any(
            marker in text
            for marker in (
                "upstream_rate_limited",
                "ratelimiterror",
                "rate limit",
                "rate_limit",
                "http 429",
                "status 429",
                "status_code=429",
                "status_code: 429",
            )
        )

    @staticmethod
    def _final_service_failure_output(*, reason: str = "") -> dict[str, Any]:
        """Structured service-failure terminal state.

        Only emitted when every provider call in the turn was unreachable;
        never pretends the model answered.
        """
        return {
            "emotion": "concerned",
            "speech": (
                "消息已收到，但模型响应超时，这次没能完成回复。请稍后再试。"
                if reason == "provider_timeout"
                else "我这边服务暂时不可用，这次没能完成回复。请稍后再试一次。"
            ),
            "tool_call": None,
            "memory_metadata": {},
            "_transient_final_failure": True,
            "_service_failure": {
                "status": "unavailable",
                "reason": str(reason or "provider_unreachable")[:80],
            },
        }

    def _record_final_response_parse_recovery_metric(self) -> None:
        self._record_final_response_metric("chat_final_response_parse_recoveries")

    @staticmethod
    def _safe_memcore_failure_code(value: Any, *, fallback: str) -> str:
        text = str(value or "").strip().lower()
        if re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{0,119}", text):
            return text
        return str(fallback or "memcore_failure")[:120]

    @staticmethod
    def _memcore_projection_failure_output(failure: dict[str, Any]) -> dict[str, Any]:
        raw_detail = str(failure.get("detail") or "").strip()
        detail = (
            FinalResponseFailureMixin._safe_memcore_failure_code(raw_detail, fallback="projection_detail_unavailable")
            if raw_detail
            else ""
        )
        return {
            "emotion": "concerned",
            "speech": "这次上下文没有完整衔接成功，我先不在证据有缺口的情况下乱答。请稍后再试。",
            "tool_call": None,
            "memory_metadata": {},
            "_transient_final_failure": True,
            "_memcore_failure": {
                "status": FinalResponseFailureMixin._safe_memcore_failure_code(
                    failure.get("status"),
                    fallback="failed",
                )[:40],
                "reason": FinalResponseFailureMixin._safe_memcore_failure_code(
                    failure.get("reason"),
                    fallback="projection_unavailable",
                ),
                **({"detail": detail} if detail else {}),
            },
        }
