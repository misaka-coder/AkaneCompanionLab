"""JSON serialization helpers extracted from store/core.py."""

from __future__ import annotations

import json
from typing import Any


def loads_json_object(value: Any) -> dict[str, Any]:
    """Parse a JSON string or dict into a dict."""
    if isinstance(value, dict):
        return dict(value)
    try:
        payload = json.loads(str(value or "{}"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def safe_json_loads(raw: Any, *, fallback: Any) -> Any:
    """Safely parse JSON, returning fallback on failure."""
    if raw in (None, ""):
        return fallback
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback
