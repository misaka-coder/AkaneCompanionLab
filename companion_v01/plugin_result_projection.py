"""Bounded, JSON-safe projection for results returned by in-process plugins."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from typing import Any

from capcore import CapabilityResult


MAX_PLUGIN_RESPONSE_BYTES = 64 * 1024
_MAX_RESULT_DEPTH = 8
_MAX_RESULT_ITEMS = 64
_MAX_RESULT_STRING_LENGTH = 4096
_MAX_RESULT_KEY_LENGTH = 128
_SAFE_STATUS_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_WINDOWS_ABSOLUTE_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]")
_POSIX_LOCAL_PATH = re.compile(r"(?<![:A-Za-z0-9])/(?:home|Users|tmp|var|etc|opt|root)(?:/|$)")
_SENSITIVE_VALUE = re.compile(r"(?i)\b(?:api[_-]?key|secret|token|password)\s*[:=]")
_FORBIDDEN_RESULT_KEY_PARTS = (
    "traceback",
    "exception",
    "absolute_path",
    "distribution_path",
    "entry_point",
    "module_path",
    "storage_path",
    "api_key",
    "secret",
    "token",
)


def sanitize_capability_result(result: CapabilityResult) -> CapabilityResult:
    """Return the bounded public contract shared by every PluginHost consumer."""

    projected = project_capability_result(result)
    return CapabilityResult(
        is_error=not bool(projected["ok"]),
        status=str(projected["status"]),
        reason=str(projected["reason"]),
        content=projected["content"],
    )


def project_capability_result(result: CapabilityResult) -> dict[str, Any]:
    if not isinstance(result, CapabilityResult):
        return _safe_error("plugin_result_invalid")

    status = _safe_status(result.status, fallback="error" if result.is_error else "ok")
    reason = _safe_status(result.reason, fallback="")
    try:
        content = _project_json_value(result.content, depth=0)
    except ValueError:
        return _safe_error("plugin_result_not_safe")

    payload = {
        "ok": not result.is_error,
        "status": status,
        "reason": reason,
        "content": content,
    }
    try:
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        return _safe_error("plugin_result_not_json_serializable")
    if len(encoded) > MAX_PLUGIN_RESPONSE_BYTES:
        return _safe_error("plugin_result_too_large")
    return payload


def _project_json_value(value: Any, *, depth: int) -> Any:
    if depth > _MAX_RESULT_DEPTH:
        raise ValueError("result_depth_exceeded")
    if value is None or isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non_finite_number")
        return value
    if isinstance(value, str):
        if len(value) > _MAX_RESULT_STRING_LENGTH:
            raise ValueError("result_string_too_large")
        if _looks_like_local_absolute_path(value):
            raise ValueError("local_path_forbidden")
        return value
    if isinstance(value, Mapping):
        if len(value) > _MAX_RESULT_ITEMS:
            raise ValueError("result_mapping_too_large")
        projected: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str) or not raw_key or len(raw_key) > _MAX_RESULT_KEY_LENGTH:
                raise ValueError("invalid_result_key")
            normalized_key = raw_key.lower()
            if any(part in normalized_key for part in _FORBIDDEN_RESULT_KEY_PARTS):
                raise ValueError("sensitive_result_key")
            projected[raw_key] = _project_json_value(raw_value, depth=depth + 1)
        return projected
    if isinstance(value, (list, tuple)):
        if len(value) > _MAX_RESULT_ITEMS:
            raise ValueError("result_sequence_too_large")
        return [_project_json_value(item, depth=depth + 1) for item in value]
    raise ValueError("unsupported_result_type")


def _looks_like_local_absolute_path(value: str) -> bool:
    stripped = value.strip()
    return bool(
        _WINDOWS_ABSOLUTE_PATH.search(stripped)
        or _POSIX_LOCAL_PATH.search(stripped)
        or stripped.startswith("/")
        or stripped.startswith("\\\\")
        or "traceback (most recent call last)" in stripped.lower()
        or _SENSITIVE_VALUE.search(stripped)
    )


def _safe_status(value: Any, *, fallback: str) -> str:
    normalized = str(value or "").strip()
    return normalized if _SAFE_STATUS_PATTERN.fullmatch(normalized) else fallback


def _safe_error(reason: str) -> dict[str, Any]:
    return {"ok": False, "status": "error", "reason": reason, "content": None}


__all__ = [
    "MAX_PLUGIN_RESPONSE_BYTES",
    "project_capability_result",
    "sanitize_capability_result",
]
