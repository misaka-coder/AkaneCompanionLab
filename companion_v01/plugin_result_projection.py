"""Lossless public JSON results; presentation budgets belong to consumers.

Plugin result content is a public contract. Do not infer confidentiality from
business field names or path-shaped text. Values explicitly obtained from a
private connection are checked against that invocation's private-value set.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace
import re
from collections.abc import Mapping
from typing import Any

from capcore import CapabilityResult
from akane_plugin.results import Result, validate_followup


# Compatibility export: no plugin-wide business byte limit. Process/transport
# budgets and model presentation limits are separate concerns.
MAX_PLUGIN_RESPONSE_BYTES = None
_SAFE_STATUS_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_ERROR_MESSAGES = {
    "plugin_result_invalid": "Expected a CapabilityResult.",
    "plugin_result_non_finite_number": "JSON numbers must be finite.",
    "plugin_result_invalid_key": "JSON object keys must be strings.",
    "plugin_result_unsupported_type": "Result contains a value that is not a JSON type.",
    "plugin_result_cycle": "Result contains a circular reference.",
    "plugin_result_serialization_depth_exceeded": "Result exceeds the JSON serializer's nesting capacity.",
    "plugin_result_not_json_serializable": "Result cannot be encoded as UTF-8 JSON.",
    "plugin_result_private_data": "Result contains a private value obtained from a connection.",
    "plugin_result_followup_invalid": "Followup must be auto, required, or none.",
}


class _ResultContractError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def sanitize_capability_result(result: CapabilityResult) -> CapabilityResult:
    """Snapshot the same public value for in-process, worker and HTTP callers."""

    projected = project_capability_result(result)
    result_type = Result if "followup" in projected else CapabilityResult
    normalized = result_type(
        is_error=not bool(projected["ok"]),
        status=str(projected["status"]),
        reason=str(projected["reason"]),
        content=projected["content"],
        **({"followup": projected["followup"]} if "followup" in projected else {}),
    )
    return replace(normalized, value=projected["value"]) if "value" in projected else normalized


def project_capability_result(result: CapabilityResult) -> dict[str, Any]:
    if not isinstance(result, CapabilityResult) or not isinstance(result.is_error, bool):
        return _error("plugin_result_invalid")

    # PluginHost owns this scope. The worker's private connection callback uses
    # the same port; plugin arguments cannot manufacture the private-value set.
    from .plugin_resources import current_resource_invocation

    scope = current_resource_invocation.get()
    private_values = tuple(scope.private_values) if scope is not None else ()
    try:
        try:
            followup = validate_followup(getattr(result, "followup", None), optional=True)
        except ValueError:
            return _error("plugin_result_followup_invalid")
        _check_private_text(str(result.status or ""), private_values)
        _check_private_text(str(result.reason or ""), private_values)
        content = _project_json_value(result.content, ancestors=set(), private_values=private_values)
        payload = {
            "ok": not result.is_error,
            "status": _safe_status(result.status, fallback="error" if result.is_error else "ok"),
            "reason": _safe_status(result.reason, fallback=""),
            "content": content,
        }
        if result.has_value:
            payload["value"] = _project_json_value(result.value, ancestors=set(), private_values=private_values)
        if followup is not None:
            payload["followup"] = followup
        # Reject encoding failures here, before they break the worker RPC lane.
        json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        return payload
    except _ResultContractError as exc:
        return _error(exc.code)
    except RecursionError:
        return _error("plugin_result_serialization_depth_exceeded")
    except (TypeError, ValueError, OverflowError, UnicodeError):
        return _error("plugin_result_not_json_serializable")


def _project_json_value(value: Any, *, ancestors: set[int], private_values: tuple[str, ...]) -> Any:
    if value is None or isinstance(value, bool) or isinstance(value, int):
        if value is not None:
            _check_private_text(json.dumps(value), private_values)
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _ResultContractError("plugin_result_non_finite_number")
        _check_private_text(json.dumps(value), private_values)
        return value
    if isinstance(value, str):
        _check_private_text(value, private_values)
        return value
    if not isinstance(value, (Mapping, list, tuple)):
        raise _ResultContractError("plugin_result_unsupported_type")
    identity = id(value)
    if identity in ancestors:
        raise _ResultContractError("plugin_result_cycle")
    ancestors.add(identity)
    try:
        if isinstance(value, Mapping):
            projected: dict[str, Any] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise _ResultContractError("plugin_result_invalid_key")
                _check_private_text(key, private_values)
                projected[key] = _project_json_value(item, ancestors=ancestors, private_values=private_values)
            return projected
        # Tuple support remains at the Python boundary; wire JSON uses arrays.
        # Repeated references are valid; only cycles fail.
        return [_project_json_value(item, ancestors=ancestors, private_values=private_values) for item in value]
    finally:
        ancestors.remove(identity)


def _check_private_text(value: str, private_values: tuple[str, ...]) -> None:
    if any(private and private in value for private in private_values):
        raise _ResultContractError("plugin_result_private_data")


def _safe_status(value: Any, *, fallback: str) -> str:
    normalized = str(value or "").strip()
    return normalized if _SAFE_STATUS_PATTERN.fullmatch(normalized) else fallback


def _error(code: str) -> dict[str, Any]:
    return {
        "ok": False, "status": "error", "reason": code,
        "content": {"code": code, "message": _ERROR_MESSAGES[code]},
    }


__all__ = [
    "MAX_PLUGIN_RESPONSE_BYTES",
    "project_capability_result",
    "sanitize_capability_result",
]
