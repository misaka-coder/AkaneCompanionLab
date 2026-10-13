"""Per-sender attachment debounce and readiness checks; asset storage is shared."""
from __future__ import annotations

import threading
import time
from typing import Any

import config
from companion_v01.channels.qq.context import QQMessageContext


class QQAttachmentDebounce:
    def __init__(self) -> None:
        self.attachment_debounce_state: dict[str, dict[str, Any]] = {}
        self._attachment_debounce_lock = threading.RLock()

    def register_attachment_debounce(
        self,
        context: QQMessageContext,
        *,
        attachment_ids: list[str],
    ) -> dict[str, Any]:
        """Merge bursty QQ attachment events into one final LLM turn.

        Every event still registers its attachments immediately. The debounce
        only decides which HTTP callback is allowed to wake Akane, so older
        image-only events do not cause "I haven't seen it yet" replies.
        """
        normalized_ids = [str(item or "").strip() for item in attachment_ids or [] if str(item or "").strip()]
        delay_seconds = min(5.0, max(0.0, float(getattr(config, "QQ_ATTACHMENT_DEBOUNCE_SECONDS", 1.2) or 0.0)))
        if not normalized_ids or delay_seconds <= 0:
            return {
                "enabled": False,
                "process": True,
                "delay_seconds": 0.0,
                "attachment_ids": normalized_ids,
            }

        now_ts = time.time()
        key = self._attachment_debounce_key(context)
        with self._attachment_debounce_lock:
            self._prune_attachment_debounce_locked(now_ts=now_ts)
            state = self.attachment_debounce_state.get(key)
            generation = int((state or {}).get("generation") or 0) + 1
            merged_ids = list((state or {}).get("attachment_ids") or [])
            for item_id in normalized_ids:
                if item_id not in merged_ids:
                    merged_ids.append(item_id)
            self.attachment_debounce_state[key] = {
                "generation": generation,
                "attachment_ids": merged_ids,
                "updated_at": now_ts,
                "expires_at": now_ts + max(10.0, delay_seconds + 5.0),
            }
        return {
            "enabled": True,
            "process": True,
            "key": key,
            "generation": generation,
            "delay_seconds": delay_seconds,
            "attachment_ids": merged_ids,
        }

    def consume_attachment_debounce(self, token: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(token, dict) or not bool(token.get("enabled")):
            return {
                "process": True,
                "reason": "disabled",
                "attachment_ids": list(token.get("attachment_ids") or []) if isinstance(token, dict) else [],
            }

        key = str(token.get("key") or "").strip()
        generation = int(token.get("generation") or 0)
        with self._attachment_debounce_lock:
            state = self.attachment_debounce_state.get(key)
            if not state:
                return {"process": False, "reason": "debounce_state_missing", "attachment_ids": []}
            if int(state.get("generation") or 0) != generation:
                return {
                    "process": False,
                    "reason": "superseded_by_newer_attachment_event",
                    "attachment_ids": list(state.get("attachment_ids") or []),
                }
            self.attachment_debounce_state.pop(key, None)
            return {
                "process": True,
                "reason": "latest_attachment_event",
                "attachment_ids": list(state.get("attachment_ids") or []),
            }

    def _attachment_debounce_key(self, context: QQMessageContext) -> str:
        # Group memory is shared, but burst merging should stay tied to the
        # sender who is currently feeding Akane attachments.
        return f"{context.session_id or context.target_id}:{context.user_id or 0}"

    def _prune_attachment_debounce_locked(self, *, now_ts: float) -> None:
        stale_keys = [
            key
            for key, state in self.attachment_debounce_state.items()
            if float(state.get("expires_at") or 0.0) <= now_ts
        ]
        for key in stale_keys:
            self.attachment_debounce_state.pop(key, None)


def qq_attachment_ready_wait_seconds(context: Any, config_module: Any) -> float:
    base_wait = max(0.0, float(getattr(config_module, "QQ_ATTACHMENT_READY_WAIT_SECONDS", 8.0) or 0.0))
    attachments = list(getattr(context, "attachments", None) or [])
    has_image = any(
        isinstance(item, dict) and str(item.get("kind") or "").strip().lower() == "image" for item in attachments
    )
    if not has_image:
        return base_wait

    vision_timeout = max(1.0, float(getattr(config_module, "VISION_REQUEST_TIMEOUT", 60.0) or 60.0))
    return max(base_wait, vision_timeout + 5.0)


def qq_pending_image_attachment_ids(
    registered_items: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    wait_result: dict[str, Any] | None,
) -> list[str]:
    if not isinstance(wait_result, dict):
        return []
    pending_ids = {
        str(item or "").strip()
        for item in list(wait_result.get("pending") or [])
        if str(item or "").strip()
    }
    if not pending_ids:
        return []

    kinds_by_id = wait_result.get("kinds_by_id") if isinstance(wait_result.get("kinds_by_id"), dict) else {}
    image_ids_from_wait = [
        item_id
        for item_id in pending_ids
        if str(kinds_by_id.get(item_id) or "").strip().lower() == "image"
    ]
    if image_ids_from_wait:
        return sorted(image_ids_from_wait)

    image_ids: list[str] = []
    for item in registered_items or []:
        if not isinstance(item, dict):
            continue
        attachment_id = str(item.get("attachment_id") or "").strip()
        if not attachment_id or attachment_id not in pending_ids:
            continue
        if str(item.get("kind") or "").strip().lower() == "image":
            image_ids.append(attachment_id)
    return image_ids
