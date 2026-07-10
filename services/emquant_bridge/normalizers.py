from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def result_error_code(result: Any) -> int:
    try:
        return int(getattr(result, "ErrorCode", 0) or 0)
    except (TypeError, ValueError):
        return -1


def result_error_reason(result: Any) -> str:
    return str(getattr(result, "ErrorMsg", "") or "").strip()[:2000]


def result_serial_id(result: Any) -> int:
    try:
        return max(0, int(getattr(result, "SerialID", 0) or 0))
    except (TypeError, ValueError):
        return 0


def snapshot_emquant_data(result: Any, *, include_data: bool = True) -> dict[str, Any]:
    payload = {
        "error_code": result_error_code(result),
        "error_reason": result_error_reason(result),
        "request_id": _safe_int(getattr(result, "RequestID", 0)),
        "serial_id": result_serial_id(result),
        "codes": _safe_string_list(getattr(result, "Codes", [])),
        "indicators": _safe_string_list(getattr(result, "Indicators", [])),
        "dates": _safe_string_list(getattr(result, "Dates", [])),
    }
    if include_data:
        payload["data"] = _bounded_copy(getattr(result, "Data", {}))
    return payload


def extract_choice_news_records(result: Any) -> tuple[dict[str, Any], ...]:
    indicators = _safe_string_list(getattr(result, "Indicators", []))
    raw_data = getattr(result, "Data", {})
    if not indicators or not isinstance(raw_data, Mapping):
        return ()
    rows: list[dict[str, Any]] = []
    for raw_code, raw_entries in raw_data.items():
        code = str(raw_code or "").strip()
        if not isinstance(raw_entries, Sequence) or isinstance(raw_entries, (str, bytes, bytearray)):
            continue
        for raw_entry in raw_entries:
            if isinstance(raw_entry, Mapping):
                row = {str(key): _bounded_copy(value) for key, value in raw_entry.items()}
            elif isinstance(raw_entry, Sequence) and not isinstance(raw_entry, (str, bytes, bytearray)):
                row = {
                    indicator: _bounded_copy(raw_entry[index])
                    for index, indicator in enumerate(indicators)
                    if index < len(raw_entry)
                }
            else:
                continue
            row.setdefault("code", code)
            rows.append(row)
    return tuple(rows)


def extract_choice_quote_records(result: Any) -> tuple[dict[str, Any], ...]:
    indicators = _safe_string_list(getattr(result, "Indicators", []))
    raw_data = getattr(result, "Data", {})
    if not indicators or not isinstance(raw_data, Mapping):
        return ()
    rows: list[dict[str, Any]] = []
    for raw_code, raw_values in raw_data.items():
        if isinstance(raw_values, Mapping):
            row = {str(key): _bounded_copy(value) for key, value in raw_values.items()}
        elif isinstance(raw_values, Sequence) and not isinstance(raw_values, (str, bytes, bytearray)):
            row = {
                indicator: _bounded_copy(raw_values[index])
                for index, indicator in enumerate(indicators)
                if index < len(raw_values)
            }
        else:
            continue
        row.setdefault("code", str(raw_code or "").strip())
        rows.append(row)
    return tuple(rows)


def _safe_string_list(value: Any, *, limit: int = 512) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [str(item or "").strip()[:240] for item in list(value)[:limit]]


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _bounded_copy(value: Any, *, depth: int = 0) -> Any:
    if depth >= 6:
        return "<depth_limit>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:20_000]
    if isinstance(value, bytes):
        return value[:4096].hex()
    if isinstance(value, Mapping):
        items = list(value.items())[:500]
        return {str(key or "")[:240]: _bounded_copy(item, depth=depth + 1) for key, item in items}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_bounded_copy(item, depth=depth + 1) for item in list(value)[:1000]]
    return str(value)[:2000]
