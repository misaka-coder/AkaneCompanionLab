from __future__ import annotations

from collections.abc import Callable
import time
from typing import Any, TypeVar


T = TypeVar("T")

DEFAULT_PUBLIC_RETRY_MAX_ATTEMPTS = 3
DEFAULT_PUBLIC_RETRY_BACKOFF_SECONDS = 0.2


def call_with_transient_retry(
    operation: Callable[[], T],
    *,
    max_attempts: int = DEFAULT_PUBLIC_RETRY_MAX_ATTEMPTS,
    backoff_seconds: float = DEFAULT_PUBLIC_RETRY_BACKOFF_SECONDS,
    sleeper: Callable[[float], Any] = time.sleep,
) -> T:
    attempts, backoff = normalize_public_retry_policy(
        max_attempts=max_attempts,
        backoff_seconds=backoff_seconds,
    )
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as exc:
            if attempt >= attempts or not is_retryable_public_upstream_error(exc):
                raise
            if backoff > 0:
                sleeper(backoff * attempt)
    raise RuntimeError("public upstream retry loop exhausted")


def normalize_public_retry_policy(*, max_attempts: Any, backoff_seconds: Any) -> tuple[int, float]:
    return _bounded_attempts(max_attempts), _bounded_backoff(backoff_seconds)


def is_retryable_public_upstream_error(exc: Exception) -> bool:
    for current in _exception_chain(exc):
        if isinstance(current, TimeoutError):
            return True
        if isinstance(current, (ConnectionResetError, ConnectionAbortedError)):
            return True
        name = type(current).__name__.lower()
        if any(
            marker in name
            for marker in (
                "timeout",
                "connectionreset",
                "connectionaborted",
                "sslerror",
                "certificateerror",
                "curlerror",
            )
        ):
            return True
        message = str(current).strip().lower()
        if any(
            marker in message
            for marker in (
                "connection reset",
                "reset by peer",
                "connection aborted",
                "remote end closed connection",
                "ssl connect error",
                "ssl connection error",
                "ssl certificate problem",
                "certificate verify failed",
                "certificate verification failed",
                "unexpected eof while reading",
                "curl: (35)",
                "curl: (56)",
                "curl_cffi",
                "pycurl",
            )
        ):
            return True
        status_code = _http_status_code(current)
        if status_code is not None and 500 <= status_code <= 599:
            return True
    return False


def _exception_chain(exc: Exception) -> tuple[BaseException, ...]:
    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    ordered: list[BaseException] = []
    while pending:
        current = pending.pop(0)
        identity = id(current)
        if identity in seen:
            continue
        seen.add(identity)
        ordered.append(current)
        cause = getattr(current, "__cause__", None)
        context = getattr(current, "__context__", None)
        if isinstance(cause, BaseException):
            pending.append(cause)
        if isinstance(context, BaseException):
            pending.append(context)
    return tuple(ordered)


def _http_status_code(exc: BaseException) -> int | None:
    candidates = (
        getattr(exc, "status_code", None),
        getattr(exc, "code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    )
    for value in candidates:
        if isinstance(value, bool):
            continue
        try:
            status_code = int(value)
        except (TypeError, ValueError):
            continue
        if 100 <= status_code <= 599:
            return status_code
    return None


def _bounded_attempts(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("max_attempts must be an integer")
    attempts = int(value)
    if attempts < 1 or attempts > DEFAULT_PUBLIC_RETRY_MAX_ATTEMPTS:
        raise ValueError(f"max_attempts must be between 1 and {DEFAULT_PUBLIC_RETRY_MAX_ATTEMPTS}")
    return attempts


def _bounded_backoff(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("backoff_seconds must be numeric")
    backoff = float(value)
    if backoff < 0 or backoff > 5:
        raise ValueError("backoff_seconds must be between zero and five seconds")
    return backoff


__all__ = [
    "DEFAULT_PUBLIC_RETRY_BACKOFF_SECONDS",
    "DEFAULT_PUBLIC_RETRY_MAX_ATTEMPTS",
    "call_with_transient_retry",
    "is_retryable_public_upstream_error",
    "normalize_public_retry_policy",
]
