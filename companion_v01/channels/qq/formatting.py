"""Text and time formatting shared by QQ input, command and reply projections."""
from __future__ import annotations

import time
from typing import Any


def _format_qq_timestamp(value: Any) -> str:
    try:
        timestamp = int(value or 0)
    except (TypeError, ValueError):
        return ""
    if timestamp <= 0:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(timestamp))
    except (OSError, OverflowError, ValueError):
        return ""


def _qq_item_time_label(item: dict[str, Any]) -> str:
    created_label = _format_qq_timestamp(item.get("created_at"))
    updated_label = _format_qq_timestamp(item.get("updated_at"))
    if created_label and updated_label and updated_label != created_label:
        return f"加入 {created_label}，更新 {updated_label}"
    if created_label:
        return f"加入 {created_label}"
    if updated_label:
        return f"更新 {updated_label}"
    return ""


def _normalize_reply_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\r\n", "\n").replace("\r", "\n").split()).strip()
