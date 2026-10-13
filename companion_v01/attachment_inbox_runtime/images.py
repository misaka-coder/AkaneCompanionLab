"""Images for AttachmentInboxService."""

from __future__ import annotations

from ..owner_private_session import private_asset_scope

import base64
import mimetypes
import time
from typing import Any
from ..image_materials import sniff_supported_image_media_type
from ..model_image_inputs import attachment_image_source_metadata
from .definitions import NATIVE_IMAGE_MEDIA_TYPES


class AttachmentImagesMixin:
    @private_asset_scope
    def build_native_image_inputs(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        attachment_ids: list[str],
        timeout_seconds: float = 8.0,
        max_count: int = 5,
        max_bytes_per_image: int = 8 * 1024 * 1024,
        max_total_bytes: int = 20 * 1024 * 1024,
    ) -> dict[str, Any]:
        normalized_ids = list(
            dict.fromkeys(str(item or "").strip() for item in attachment_ids or [] if str(item or "").strip())
        )
        image_limit = max(1, min(5, int(max_count or 5)))
        per_image_limit = max(128 * 1024, int(max_bytes_per_image or 0))
        total_limit = max(per_image_limit, int(max_total_bytes or 0))
        deadline = time.time() + max(0.0, min(30.0, float(timeout_seconds or 0.0)))
        latest: dict[str, dict[str, Any]] = {}

        while normalized_ids:
            latest = self._load_attachment_items_by_id(
                profile_user_id=profile_user_id,
                session_id=session_id,
                attachment_ids=normalized_ids,
            )
            waiting = False
            for attachment_id in normalized_ids:
                item = latest.get(attachment_id)
                if not isinstance(item, dict):
                    continue
                kind = str(item.get("kind") or "").strip().lower()
                if kind in {"file", "document"}:
                    # File segments may contain images. The materializer commits
                    # storage and byte-verified kind together; don't decide from
                    # the initial filename/MIME while that operation is pending.
                    if item.get("status") == "pending_observation" and not item.get("storage_relpath"):
                        waiting = True
                        break
                    continue
                if kind != "image":
                    continue
                source_path = self._resolve_storage_path(item)
                if source_path is not None and source_path.exists() and source_path.is_file():
                    continue
                if str(item.get("status") or "").strip().lower() not in {"failed", "cleared"}:
                    waiting = True
                    break
            if not waiting or time.time() >= deadline:
                break
            time.sleep(min(0.2, max(0.0, deadline - time.time())))

        images: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        total_bytes = 0
        for attachment_id in normalized_ids:
            item = latest.get(attachment_id)
            if not isinstance(item, dict):
                skipped.append({"attachment_id": attachment_id, "reason": "missing_attachment"})
                continue
            if str(item.get("kind") or "").strip().lower() != "image":
                continue
            if len(images) >= image_limit:
                skipped.append({"attachment_id": attachment_id, "reason": "image_count_limit"})
                continue
            source_path = self._resolve_storage_path(item)
            if source_path is None or not source_path.exists() or not source_path.is_file():
                skipped.append({"attachment_id": attachment_id, "reason": "image_not_materialized"})
                continue
            try:
                file_size = int(source_path.stat().st_size)
            except OSError:
                skipped.append({"attachment_id": attachment_id, "reason": "image_unreadable"})
                continue
            if file_size <= 0 or file_size > per_image_limit:
                skipped.append({"attachment_id": attachment_id, "reason": "image_size_limit"})
                continue
            if total_bytes + file_size > total_limit:
                skipped.append({"attachment_id": attachment_id, "reason": "image_total_size_limit"})
                continue
            try:
                image_bytes = source_path.read_bytes()
            except OSError:
                skipped.append({"attachment_id": attachment_id, "reason": "image_unreadable"})
                continue
            declared_media_type = str(
                item.get("mime_type") or mimetypes.guess_type(source_path.name)[0] or ""
            ).lower()
            if declared_media_type == "image/jpg":
                declared_media_type = "image/jpeg"
            media_type = sniff_supported_image_media_type(image_bytes) or declared_media_type
            if media_type not in NATIVE_IMAGE_MEDIA_TYPES:
                skipped.append({"attachment_id": attachment_id, "reason": "unsupported_image_type"})
                continue
            total_bytes += len(image_bytes)
            images.append(
                {
                    "attachment_id": attachment_id,
                    "attachment_handle": str(item.get("attachment_handle") or attachment_id).strip(),
                    "title": self._display_name(item),
                    "media_type": media_type,
                    **attachment_image_source_metadata(item),
                    "data_url": f"data:{media_type};base64,{base64.b64encode(image_bytes).decode('ascii')}",
                }
            )

        return {
            "ok": bool(images),
            "status": "ready"
            if len(images) == len([item for item in latest.values() if item.get("kind") == "image"])
            else "partial",
            "images": images,
            "image_count": len(images),
            "total_bytes": total_bytes,
            "skipped": skipped,
        }
