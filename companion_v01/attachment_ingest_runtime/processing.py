"""Attachment processing and observed ready or failed state transitions."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any
import config
from ..image_materials import verified_file_image_media_type
from .definitions import (
    QQ_ATTACHMENT_DEFAULT_MAX_BYTES,
)


class AttachmentProcessingMixin:
    """Methods use the state owned by AttachmentIngestService."""

    def _process_qq_attachment(
        self,
        item: dict[str, Any],
        payload: dict[str, Any],
        timestamp: int,
        *,
        trusted_local_source: Path | None = None,
    ) -> None:
        try:
            source_path = self._materialize_attachment_file(
                item=item,
                payload=payload,
                trusted_local_source=trusted_local_source,
            )
            if source_path is None:
                self._mark_private_source_attempt(
                    item=item,
                    payload=payload,
                    status="failed",
                    reason="attachment_source_unavailable",
                    timestamp=timestamp,
                )
                self._mark_failed(item, "attachment_source_unavailable", timestamp=timestamp)
                return

            workspace_uri = str(payload.get("workspace_uri") or "").strip()
            relpath = workspace_uri or self._storage_relpath(source_path)
            mime_type = str(payload.get("mime_type") or mimetypes.guess_type(str(source_path))[0] or "").strip()
            file_ext = source_path.suffix.lower()
            kind = str(item.get("kind") or "").strip().lower()
            if kind in {"document", "file"}:
                verified_mime = verified_file_image_media_type(source_path)
                if verified_mime:
                    kind, mime_type = "image", verified_mime
            item = self.store.update_attachment_inbox_item(
                profile_user_id=str(item.get("profile_user_id") or ""),
                session_id=str(item.get("session_id") or ""),
                attachment_id=str(item.get("attachment_id") or ""),
                kind=kind,
                storage_relpath=relpath,
                mime_type=mime_type,
                file_ext=file_ext,
                file_size=source_path.stat().st_size if source_path.exists() else 0,
                updated_at=timestamp,
            ) or {**item, "kind": kind, "mime_type": mime_type, "storage_relpath": relpath, "file_ext": file_ext}
            self._mark_private_source_attempt(
                item=item,
                payload=payload,
                status="success",
                timestamp=timestamp,
                clear_locator=True,
            )

            if kind == "image" and self.vision_service is not None and bool(payload.get("_observe_image", True)):
                scheduled = self.vision_service.schedule_attachment_image_observation(
                    attachment={
                        **item,
                        "storage_relpath": relpath,
                        "mime_type": mime_type,
                        "file_ext": file_ext,
                    },
                    source_path=source_path,
                )
                if scheduled is None:
                    self._mark_failed(item, "图片已接收，但视觉模型尚未配置或无法启动。", timestamp=timestamp)
                elif str(scheduled.get("status") or "").strip().lower() == "ready":
                    self._mark_image_ready_from_observation(
                        item=item,
                        observation=scheduled,
                        timestamp=timestamp,
                    )
                return

            file_card = self._build_file_card(source_path=source_path, item=item, mime_type=mime_type)
            self.attachment_service.mark_ready(
                profile_user_id=str(item.get("profile_user_id") or ""),
                session_id=str(item.get("session_id") or ""),
                attachment_id=str(item.get("attachment_id") or ""),
                summary_title=file_card["summary_title"],
                short_hint=file_card["short_hint"],
                detail=self._merge_item_detail(item, file_card["detail"]),
                timestamp=timestamp,
            )
        except Exception as exc:
            self._mark_private_source_attempt(
                item=item,
                payload=payload,
                status="failed",
                reason=self._material_failure_code(str(exc)),
                timestamp=timestamp,
            )
            self._mark_failed(item, str(exc), timestamp=timestamp)

    def _mark_failed(self, item: dict[str, Any], error: str, *, timestamp: int) -> None:
        error_code = self._material_failure_code(error)
        readable_error = self._humanize_failure(error_code, kind=str(item.get("kind") or ""))
        failure: dict[str, Any] = {
            "code": error_code,
            "reason": readable_error,
            "failed_at": timestamp,
        }
        if error_code == "attachment_too_large":
            observed_bytes = self._safe_int(item.get("file_size"))
            limit_bytes = int(
                getattr(config, "QQ_ATTACHMENT_MAX_BYTES", QQ_ATTACHMENT_DEFAULT_MAX_BYTES)
                or QQ_ATTACHMENT_DEFAULT_MAX_BYTES
            )
            if observed_bytes > 0:
                failure["observed_bytes"] = observed_bytes
            if limit_bytes > 0:
                failure["limit_bytes"] = limit_bytes
        updated = self.store.update_attachment_inbox_item(
            profile_user_id=str(item.get("profile_user_id") or ""),
            session_id=str(item.get("session_id") or ""),
            attachment_id=str(item.get("attachment_id") or ""),
            status="failed",
            error_message=error_code,
            short_hint=readable_error,
            detail=self._merge_item_detail(
                item,
                {"failure": failure},
            ),
            updated_at=timestamp,
        )
        self.attachment_service.record_material_status(
            updated or dict(item, status="failed", short_hint=readable_error, updated_at=timestamp),
            event_type="reference",
            timestamp=timestamp,
            reason=readable_error,
        )

    def _mark_image_ready_from_observation(
        self,
        *,
        item: dict[str, Any],
        observation: dict[str, Any],
        timestamp: int,
    ) -> None:
        card = observation.get("observation") if isinstance(observation.get("observation"), dict) else {}
        card = self._merge_item_detail(item, card)
        summary = str(card.get("summary") or observation.get("summary") or "").strip()
        summary_title = (
            str(card.get("summary_title") or "").strip()
            or str(item.get("origin_name") or "").strip()
            or str(item.get("attachment_handle") or "").strip()
            or "图片附件"
        )
        self.attachment_service.mark_ready(
            profile_user_id=str(item.get("profile_user_id") or ""),
            session_id=str(item.get("session_id") or ""),
            attachment_id=str(item.get("attachment_id") or ""),
            summary_title=summary_title,
            short_hint=summary,
            detail=card,
            timestamp=timestamp,
        )
