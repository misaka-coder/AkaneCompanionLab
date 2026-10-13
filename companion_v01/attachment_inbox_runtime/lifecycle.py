"""Lifecycle for AttachmentInboxService."""

from __future__ import annotations

from ..owner_private_session import private_asset_scope

import time
from typing import Any
from .definitions import READY_ATTACHMENT_STATUSES, AUTO_FOCUS_BATCH_SECONDS, AUTO_FOCUS_MAX_ITEMS, logger


class AttachmentLifecycleMixin:
    def activity_prompt_context_lifecycle(self) -> str:
        """Declare how the main-chat activity index is delivered.

        A configured recorder writes reference/ready/cleanup transitions to
        the append-only timeline, so an authoritative projection does not
        need the complete index repeated on every turn.  Standalone consumers
        without that recorder retain the per-turn fallback.
        """

        return (
            "event_backed"
            if callable(self.material_trace_recorder) and self._material_timeline_healthy
            else "turn"
        )

    @private_asset_scope
    def create_pending(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        source: str,
        kind: str,
        origin_name: str = "",
        mime_type: str = "",
        file_ext: str = "",
        file_size: int = 0,
        storage_relpath: str = "",
        source_event_id: str = "",
        source_message_id: str = "",
        detail: dict[str, Any] | None = None,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        item = self.store.add_attachment_inbox_item(
            profile_user_id=profile_user_id,
            session_id=session_id,
            source=source,
            kind=kind,
            status="pending_observation",
            origin_name=origin_name,
            mime_type=mime_type,
            file_ext=file_ext,
            file_size=file_size,
            storage_relpath=storage_relpath,
            source_event_id=source_event_id,
            source_message_id=source_message_id,
            detail=detail if isinstance(detail, dict) else None,
            timestamp=timestamp,
        )
        self.record_material_status(item, event_type="reference", timestamp=timestamp)
        return item

    @private_asset_scope
    def mark_ready(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        attachment_id: str,
        summary_title: str = "",
        short_hint: str = "",
        detail: dict[str, Any] | None = None,
        timestamp: int | None = None,
    ) -> dict[str, Any] | None:
        item = self.store.mark_attachment_inbox_item_ready(
            profile_user_id=profile_user_id,
            session_id=session_id,
            attachment_id=attachment_id,
            summary_title=summary_title,
            short_hint=short_hint,
            detail=detail if isinstance(detail, dict) else {},
            focus_batch_seconds=AUTO_FOCUS_BATCH_SECONDS,
            focus_max_items=AUTO_FOCUS_MAX_ITEMS,
            timestamp=timestamp,
        )
        self.record_material_status(item, event_type="reference", timestamp=timestamp)
        return item

    def record_material_status(
        self,
        item: dict[str, Any] | None,
        *,
        event_type: str,
        timestamp: int | None = None,
        reason: str = "",
        delete_storage: bool = False,
    ) -> None:
        recorder = self.material_trace_recorder
        if recorder is None or not isinstance(item, dict):
            return
        try:
            result = recorder(
                event_type=str(event_type or "reference"),
                item=dict(item),
                timestamp=int(timestamp or item.get("updated_at") or item.get("created_at") or time.time()),
                reason=str(reason or ""),
                delete_storage=bool(delete_storage),
            )
            self._material_timeline_healthy = isinstance(result, dict) and bool(result.get("ok"))
        except Exception as exc:
            self._material_timeline_healthy = False
            logger.debug("attachment material trace recorder failed: %s", exc)

    @private_asset_scope
    def wait_for_attachments_settled(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        attachment_ids: list[str],
        timeout_seconds: float = 8.0,
        poll_interval_seconds: float = 0.2,
    ) -> dict[str, Any]:
        normalized_ids = [str(item or "").strip() for item in attachment_ids or [] if str(item or "").strip()]
        if not normalized_ids:
            return {"ok": True, "ready": [], "failed": [], "pending": [], "missing": []}

        deadline = time.time() + max(0.0, float(timeout_seconds or 0.0))
        poll_interval = min(1.0, max(0.05, float(poll_interval_seconds or 0.2)))
        latest: dict[str, dict[str, Any]] = {}
        while True:
            latest = self._load_attachment_items_by_id(
                profile_user_id=profile_user_id,
                session_id=session_id,
                attachment_ids=normalized_ids,
            )
            pending = [
                item_id
                for item_id in normalized_ids
                if str((latest.get(item_id) or {}).get("status") or "missing") == "pending_observation"
            ]
            if not pending or time.time() >= deadline:
                break
            time.sleep(min(poll_interval, max(0.0, deadline - time.time())))

        ready: list[str] = []
        failed: list[str] = []
        pending_final: list[str] = []
        missing: list[str] = []
        kinds_by_id: dict[str, str] = {}
        for item_id in normalized_ids:
            latest_item = latest.get(item_id) or {}
            status = str(latest_item.get("status") or "missing")
            kind = str(latest_item.get("kind") or "").strip().lower()
            if kind:
                kinds_by_id[item_id] = kind
            if status == "ready":
                ready.append(item_id)
            elif status == "failed":
                failed.append(item_id)
            elif status == "pending_observation":
                pending_final.append(item_id)
            else:
                missing.append(item_id)
        return {
            "ok": not pending_final,
            "ready": ready,
            "failed": failed,
            "pending": pending_final,
            "missing": missing,
            "kinds_by_id": kinds_by_id,
            "items_by_id": {
                item_id: dict(latest[item_id]) for item_id in normalized_ids if isinstance(latest.get(item_id), dict)
            },
        }

    def _auto_focus_recent_batch(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        anchor: dict[str, Any],
        timestamp: int | None = None,
    ) -> None:
        effective_ts = int(timestamp or anchor.get("updated_at") or time.time())
        threshold = effective_ts - AUTO_FOCUS_BATCH_SECONDS
        candidates = self.store.list_attachment_inbox_items(
            profile_user_id=profile_user_id,
            session_id=session_id,
            statuses=list(READY_ATTACHMENT_STATUSES),
            limit=80,
        )
        recent = [
            item for item in candidates if int(item.get("updated_at") or item.get("created_at") or 0) >= threshold
        ]
        anchor_id = str(anchor.get("attachment_id") or "").strip()
        if anchor_id and anchor_id not in {str(item.get("attachment_id") or "") for item in recent}:
            recent.append(anchor)
        recent = sorted(
            recent,
            key=lambda item: (
                int(item.get("created_at") or 0),
                int(item.get("sequence_no") or 0),
                int(item.get("updated_at") or 0),
            ),
        )[-AUTO_FOCUS_MAX_ITEMS:]
        attachment_ids = [
            str(item.get("attachment_id") or "").strip()
            for item in recent
            if str(item.get("attachment_id") or "").strip()
        ]
        if attachment_ids:
            self.store.sync_attachment_workspace_focus(
                profile_user_id=profile_user_id,
                session_id=session_id,
                attachment_ids=attachment_ids,
                timestamp=effective_ts,
            )
