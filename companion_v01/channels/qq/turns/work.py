"""QQ session keys, durable work payloads and queue wait measurements."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from companion_v01.session_inbox import SessionInboxItem


def _session_work_key(context: Any) -> str:
    profile_user_id = str(getattr(context, "profile_user_id", "") or "").strip()
    session_id = str(getattr(context, "session_id", "") or "").strip()
    return f"{profile_user_id}\0{session_id}"


def _durable_qq_work_payload(
    *,
    event: dict[str, Any],
    turn_payload: dict[str, Any],
    record_only: bool = False,
) -> dict[str, Any]:
    safe_turn_payload = dict(turn_payload)
    # Native pixels are recreated from the already-managed attachment IDs
    # when the item is claimed. Keeping base64 in SQLite would duplicate
    # large artifacts and make ordinary queue operations expensive.
    safe_turn_payload.pop("native_user_images", None)
    return {
        "event": dict(event),
        "turn_payload": safe_turn_payload,
        "record_only": bool(record_only),
    }


def _session_item_wait_ms(item: Any) -> float:
    if isinstance(item, SessionInboxItem):
        return max(0.0, (time.time() - float(item.created_at or 0)) * 1000)
    return max(0.0, (time.perf_counter() - float(getattr(item, "enqueued_at", 0) or 0)) * 1000)






def passive_batch_key(item: SessionInboxItem) -> str:
    """Batch adjacent observations in one memory scope, across group senders."""
    if item.source != "qq" or item.kind != "passive":
        return ""
    payload = item.payload.get("turn_payload")
    if not isinstance(payload, dict):
        return ""
    scope = [
        item.profile_user_id, item.session_id, payload.get("character_pack_id", ""),
        payload.get("client_mode", "qq_text"), bool(item.payload.get("record_only")),
    ]
    return hashlib.sha256(json.dumps(scope, ensure_ascii=False).encode("utf-8")).hexdigest()


def passive_retry_can_yield(item: SessionInboxItem) -> bool:
    """Only failed observation writes may yield their retry wait to later work."""
    return item.source == "qq" and item.kind == "passive" and item.last_error == "passive_memory_record_failed"
