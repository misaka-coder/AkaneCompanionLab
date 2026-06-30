"""Diagnostics helpers for future memcore shadow comparisons."""

from __future__ import annotations

from typing import Any


def safe_status(manager: Any) -> dict[str, Any]:
    if manager is None:
        return {"backend": "legacy", "enabled": False, "available": False, "reason": "not_configured"}
    try:
        status = manager.status()
    except Exception as exc:
        return {"backend": "unknown", "enabled": False, "available": False, "reason": str(exc)}
    return dict(status) if isinstance(status, dict) else {"backend": "unknown", "available": False}
