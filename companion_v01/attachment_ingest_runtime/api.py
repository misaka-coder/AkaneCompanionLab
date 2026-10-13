"""QQ, local and workspace ingestion entry points and bounded source context."""

from __future__ import annotations

from ..owner_private_session import private_asset_scope

import logging
import mimetypes
import time
from pathlib import Path
from typing import Any
import config
from .definitions import (
    AUDIO_MEDIA_SUFFIXES,
)


logger = logging.getLogger("akane.attachment_ingest")


class AttachmentIngestCallsMixin:
    """Methods use the state owned by AttachmentIngestService."""

    @private_asset_scope
    def ingest_qq_attachments(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        attachments: list[dict[str, Any]],
        character_pack_id: str = "",
        timestamp: int | None = None,
        observe_images: bool = True,
    ) -> list[dict[str, Any]]:
        effective_ts = int(timestamp or time.time())
        created_items: list[dict[str, Any]] = []
        for payload in attachments or []:
            if not isinstance(payload, dict):
                continue
            processing_payload = self._sanitize_qq_materialization_payload(payload)
            processing_payload["_observe_image"] = bool(observe_images)
            kind = self._normalize_kind(payload.get("kind"))
            if kind not in {"image", "document", "audio", "file"}:
                continue
            item = self.attachment_service.create_pending(
                profile_user_id=profile_user_id,
                session_id=session_id,
                source="qq",
                kind=kind,
                origin_name=self._clean_filename(payload.get("origin_name") or payload.get("name") or ""),
                mime_type=str(payload.get("mime_type") or "").strip(),
                file_ext=str(payload.get("file_ext") or "").strip(),
                file_size=self._safe_int(payload.get("file_size")),
                source_event_id=str(payload.get("source_event_id") or "").strip(),
                source_message_id=str(payload.get("source_message_id") or "").strip(),
                detail=self._with_material_context_detail(
                    self._qq_attachment_context_detail(payload),
                    character_pack_id=character_pack_id or str(payload.get("character_pack_id") or ""),
                ),
                timestamp=effective_ts,
            )
            created_items.append(item)
            private_locator = self._private_qq_source_locator(payload)
            locator_ttl = int(getattr(config, "QQ_ATTACHMENT_SOURCE_LOCATOR_TTL_SECONDS", 7 * 24 * 60 * 60) or 0)
            if private_locator and locator_ttl > 0:
                locator_saved_at = int(time.time())
                source_kind = "+".join(
                    name
                    for name, present in (
                        ("onebot", bool(private_locator.get("file_token"))),
                        ("remote_url", bool(private_locator.get("url"))),
                    )
                    if present
                )
                try:
                    self.store.set_attachment_private_source(
                        profile_user_id=profile_user_id,
                        session_id=session_id,
                        attachment_id=str(item.get("attachment_id") or ""),
                        source_kind=source_kind,
                        locator=private_locator,
                        expires_at=locator_saved_at + locator_ttl,
                        timestamp=locator_saved_at,
                    )
                    processing_payload["_private_source_registered"] = True
                except Exception as exc:
                    logger.warning(
                        "attachment private source registration failed: attachment_id=%s error_type=%s",
                        str(item.get("attachment_id") or ""),
                        type(exc).__name__,
                    )
            self.background_tasks.submit(
                lane="attachment",
                name=f"qq_attachment:{item.get('attachment_handle') or item.get('attachment_id')}",
                fn=self._process_qq_attachment,
                args=(item, processing_payload, effective_ts),
            )
        return created_items

    @private_asset_scope
    def ingest_local_file(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        source_path: Path | str,
        origin_name: str = "",
        mime_type: str = "",
        kind: str = "",
        source: str = "local",
        character_pack_id: str = "",
        observe_image: bool = True,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        """Synchronously copy a trusted local file into the attachment inbox."""

        effective_ts = int(timestamp or time.time())
        local_path = Path(source_path)
        if not local_path.exists() or not local_path.is_file():
            raise FileNotFoundError(str(local_path))

        clean_name = self._clean_filename(origin_name or local_path.name)
        guessed_mime = str(mime_type or mimetypes.guess_type(clean_name or str(local_path))[0] or "").strip()
        suffix = local_path.suffix.lower() or Path(clean_name).suffix.lower()
        normalized_kind = self._normalize_kind(kind)
        if not kind:
            normalized_kind = "audio" if suffix in AUDIO_MEDIA_SUFFIXES or guessed_mime.startswith("audio/") else "file"

        item = self.attachment_service.create_pending(
            profile_user_id=profile_user_id,
            session_id=session_id,
            source=str(source or "local").strip() or "local",
            kind=normalized_kind,
            origin_name=clean_name,
            mime_type=guessed_mime,
            file_ext=suffix,
            file_size=local_path.stat().st_size,
            source_event_id="",
            source_message_id="",
            detail=self._material_context_detail(character_pack_id=character_pack_id),
            timestamp=effective_ts,
        )
        self._process_qq_attachment(
            item,
            {
                "origin_name": clean_name,
                "mime_type": guessed_mime,
                "file_ext": suffix,
                "kind": normalized_kind,
                "character_pack_id": character_pack_id,
                "_observe_image": bool(observe_image),
            },
            effective_ts,
            trusted_local_source=local_path,
        )
        return (
            self.store.get_attachment_inbox_item(
                profile_user_id=profile_user_id,
                session_id=session_id,
                attachment_id=str(item.get("attachment_id") or ""),
            )
            or item
        )

    @private_asset_scope
    def register_workspace_file(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        workspace_uri: str,
        character_pack_id: str = "",
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        normalized_uri = str(workspace_uri or "").strip()
        if not normalized_uri.lower().startswith("workspace:"):
            raise ValueError("workspace registration requires a workspace:/ URI")
        if self.workspace_uri_resolver is None:
            raise RuntimeError("workspace file resolver is unavailable")
        source_path = self.workspace_uri_resolver(normalized_uri)
        if source_path is None or not source_path.exists() or not source_path.is_file():
            raise FileNotFoundError(normalized_uri)

        effective_ts = int(timestamp or time.time())
        mime_type = str(mimetypes.guess_type(source_path.name)[0] or "").strip()
        file_ext = source_path.suffix.lower()
        kind = self._infer_local_kind(source_path=source_path, mime_type=mime_type)
        existing = self.store.get_attachment_inbox_item_by_storage_relpath(
            profile_user_id=profile_user_id,
            session_id=session_id,
            storage_relpath=normalized_uri,
            statuses=["ready", "pending_observation", "failed"],
        )
        if existing is not None and str(existing.get("status") or "") == "pending_observation":
            return {
                "status": "already_registered",
                "workspace_uri": normalized_uri,
                "item": existing,
            }

        if existing is None:
            item = self.attachment_service.create_pending(
                profile_user_id=profile_user_id,
                session_id=session_id,
                source="workspace",
                kind=kind,
                origin_name=self._clean_filename(source_path.name),
                mime_type=mime_type,
                file_ext=file_ext,
                file_size=source_path.stat().st_size,
                storage_relpath=normalized_uri,
                detail=self._material_context_detail(character_pack_id=character_pack_id),
                timestamp=effective_ts,
            )
            registration_status = "registered"
        else:
            item = (
                self.store.update_attachment_inbox_item(
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    attachment_id=str(existing.get("attachment_id") or ""),
                    status="pending_observation",
                    short_hint="正在重新读取这个工作区文件。",
                    detail=self._merge_item_detail(
                        existing,
                        self._material_context_detail(character_pack_id=character_pack_id),
                    ),
                    error_message="",
                    mime_type=mime_type,
                    file_ext=file_ext,
                    file_size=source_path.stat().st_size,
                    storage_relpath=normalized_uri,
                    updated_at=effective_ts,
                )
                or existing
            )
            registration_status = "refreshed"

        self._process_qq_attachment(
            item,
            {
                "workspace_uri": normalized_uri,
                "origin_name": source_path.name,
                "mime_type": mime_type,
                "file_ext": file_ext,
                "kind": kind,
                "character_pack_id": character_pack_id,
            },
            effective_ts,
        )
        latest = (
            self.store.get_attachment_inbox_item(
                profile_user_id=profile_user_id,
                session_id=session_id,
                attachment_id=str(item.get("attachment_id") or ""),
            )
            or item
        )
        return {
            "status": registration_status,
            "workspace_uri": normalized_uri,
            "item": latest,
        }

    def _qq_attachment_context_detail(self, payload: dict[str, Any]) -> dict[str, Any]:
        sender_label = self._safe_prompt_label(
            payload.get("sender_label") or payload.get("qq_sender_label") or payload.get("source_sender_label")
        )
        detail: dict[str, Any] = {}
        segment_type = str(payload.get("segment_type") or "").strip()
        if segment_type in {"file", "image", "record", "voice", "video"}:
            detail["qq_source_segment_type"] = segment_type
        quoted_message_id = str(payload.get("quoted_message_id") or "").strip()
        if quoted_message_id:
            detail["qq_quoted_message_id"] = quoted_message_id[:200]
        if sender_label:
            detail["qq_sender_label"] = sender_label
        sender_id = str(payload.get("sender_id") or payload.get("user_id") or "").strip()
        if sender_id:
            detail["qq_sender_id"] = sender_id[:40]
        group_id = str(payload.get("group_id") or "").strip()
        if group_id:
            detail["qq_group_id"] = group_id[:40]
        forward_id = str(payload.get("forward_id") or "").strip()
        if forward_id:
            detail["qq_forward_id"] = forward_id[:200]
        forward_node_index = self._safe_int(payload.get("forward_node_index"))
        if forward_node_index > 0:
            detail["qq_forward_node_index"] = forward_node_index
        character_pack_id = str(payload.get("character_pack_id") or "").strip()
        if character_pack_id:
            detail["character_pack_id"] = character_pack_id[:120]
        return detail

    def _merge_item_detail(self, item: dict[str, Any], detail: dict[str, Any] | None) -> dict[str, Any]:
        base = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        merged = dict(base)
        if isinstance(detail, dict):
            merged.update(detail)
        return merged

    def _material_context_detail(self, *, character_pack_id: str = "") -> dict[str, Any]:
        text = str(character_pack_id or "").strip()
        return {"character_pack_id": text[:120]} if text else {}

    def _with_material_context_detail(
        self,
        detail: dict[str, Any] | None,
        *,
        character_pack_id: str = "",
    ) -> dict[str, Any]:
        merged = dict(detail) if isinstance(detail, dict) else {}
        merged.update(self._material_context_detail(character_pack_id=character_pack_id))
        return merged
