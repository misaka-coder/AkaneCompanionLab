"""Memcore materials operations."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from typing import Any
from ..owner_private_session import memory_scope
from ..model_image_inputs import image_source_metadata

logger = logging.getLogger("akane.memcore")


class MemcoreMaterialWritesMixin:
    @memory_scope
    def append_turn_media_input(
        self,
        *,
        items: list[dict[str, Any]],
        turn_id: str,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        related_source_ids: list[str] | None = None,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        """Append a safe placeholder for provider-only media after a tool result.

        Raw bytes/data URLs deliberately never cross this boundary.  The actual
        multimodal blocks are frozen from the observed provider request, where
        MemCore replaces them with its persistent media-omitted marker.
        """

        operation = "append_turn_media_input"
        resolved_turn_id = str(turn_id or "").strip()
        if not resolved_turn_id:
            return self._status(operation, False, "invalid_record", reason="turn_id_required")
        safe_items: list[dict[str, str]] = []
        for index, raw in enumerate(list(items or [])[:5]):
            if not isinstance(raw, dict):
                continue
            attachment_id = self._safe_media_reference(raw.get("attachment_id"))
            handle = self._safe_media_reference(raw.get("attachment_handle"))
            mime_type = str(raw.get("mime_type") or raw.get("content_type") or "").strip().lower()
            if not re.fullmatch(r"image/[a-z0-9.+-]{1,80}", mime_type):
                mime_type = "image"
            safe_items.append(
                {
                    "attachment_id": attachment_id,
                    "attachment_handle": handle or f"image_{index + 1}",
                    "mime_type": mime_type,
                    **image_source_metadata(raw),
                }
            )
        if not safe_items:
            return self._status(operation, False, "invalid_record", reason="media_items_required")
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return self._status(operation, False, "unavailable", reason=self._reason)
        related = sorted(
            {
                str(source_id or "").strip()
                for source_id in list(related_source_ids or [])
                if str(source_id or "").strip()
            }
        )
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "turn_id": resolved_turn_id,
                    "items": safe_items,
                    "related_source_ids": related,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8", errors="ignore")
        ).hexdigest()[:32]
        source_id = f"media:{fingerprint}"
        labels = [item["attachment_handle"] for item in safe_items]
        try:
            memcore = self._memcore_module or self._import_memcore()
            entry = memcore.TimelineEntryInput(
                source_id=source_id,
                kind="material.model_input",
                origin=memcore.EntryOrigin.ENVIRONMENT,
                turn_role=memcore.TurnRole.INTERMEDIATE,
                semantic_text=f"工具为当前模型请求加载了图片：{', '.join(labels)}。",
                timestamp=int(timestamp or time.time()),
                payload={"items": safe_items},
                trace_metadata={"status": "ready", "media_count": len(safe_items)},
                memory_metadata={},
                annotation_status=memcore.AnnotationStatus.UNANNOTATED,
                retrieval_policy=memcore.RetrievalPolicy.EXPLICIT,
                retrieval_visibility=memcore.RetrievalVisibility.EXPLICIT,
                semanticize=False,
                prompt_visible=True,
                compatibility_role="user.attachment image",
            )
            stored = system.append_entry(entry, turn_id=resolved_turn_id)
            return {
                **self._status(
                    operation,
                    True,
                    "recorded",
                    source_id=stored.source_id,
                    index_status=stored.index_status,
                ),
                "turn_id": resolved_turn_id,
                "item_count": len(safe_items),
            }
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore %s failed: %s", operation, reason)
            return self._status(operation, False, "failed", source_id=source_id, reason=reason)

    @staticmethod
    def _safe_media_reference(value: Any) -> str:
        reference = str(value or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,120}", reference):
            return ""
        return reference

    @memory_scope
    def record_material_reference(
        self,
        *,
        item: dict[str, Any],
        profile_user_id: str = "",
        session_id: str = "",
        character_pack_id: str = "",
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        return self._record_material_event(
            operation="record_material_reference",
            event_type="reference",
            item=item,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            timestamp=timestamp,
        )

    @memory_scope
    def record_material_cleanup(
        self,
        *,
        item: dict[str, Any],
        profile_user_id: str = "",
        session_id: str = "",
        character_pack_id: str = "",
        timestamp: int | None = None,
        reason: str = "",
        delete_storage: bool = False,
    ) -> dict[str, Any]:
        return self._record_material_event(
            operation="record_material_cleanup",
            event_type="cleanup",
            item=item,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            timestamp=timestamp,
            reason=reason,
            delete_storage=delete_storage,
        )

    @memory_scope
    def record_generated_workspace_cleanup(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        action: str,
        status: str,
        managed: list[dict[str, Any]] | None = None,
        failures: list[dict[str, Any]] | None = None,
        unresolved: list[str] | None = None,
        reason: str = "",
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        """Append one safe fact for a channel/UI initiated generated-file cleanup.

        Native model tool calls already persist their tool result in the open
        turn and must not call this adapter.  This method exists for direct QQ
        commands and desktop panel actions that otherwise mutate the generated
        file store without leaving any newer evidence in the model timeline.
        """

        operation = "record_generated_workspace_cleanup"
        profile = str(profile_user_id or "").strip()
        session = str(session_id or "").strip()
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile,
            session_id=session,
            character_pack_id=str(character_pack_id or "").strip(),
        )
        if system is None:
            return self._status(operation, False, "unavailable", reason=self._reason)

        normalized_action = self._kind_segment(action, fallback="archive")
        normalized_status = self._kind_segment(status, fallback="unknown")
        managed_items = [item for item in list(managed or []) if isinstance(item, dict)]
        failure_items = [item for item in list(failures or []) if isinstance(item, dict)]
        unresolved_items = [str(item or "").strip() for item in list(unresolved or []) if str(item or "").strip()]
        safe_files: list[str] = []
        for item in managed_items[:30]:
            handle = self._safe_task_event_text(
                item.get("generated_handle") or item.get("generated_id"),
                limit=120,
            )
            title = self._safe_task_event_text(item.get("output_title"), limit=180)
            if handle and title and title != handle:
                safe_files.append(f"{handle} ({title})")
            elif handle or title:
                safe_files.append(handle or title)

        safe_failures: list[str] = []
        for item in failure_items[:20]:
            target = self._safe_task_event_text(
                item.get("target") or item.get("generated_handle") or item.get("generated_id"),
                limit=120,
            )
            code = self._safe_task_event_text(item.get("code") or item.get("error"), limit=80)
            failure_reason = self._safe_task_event_text(item.get("reason"), limit=240)
            label = ": ".join(part for part in (target, code) if part)
            if failure_reason:
                label = f"{label} - {failure_reason}" if label else failure_reason
            if label:
                safe_failures.append(label)

        safe_unresolved = [
            text
            for item in unresolved_items[:20]
            if (text := self._safe_task_event_text(item, limit=120))
        ]
        safe_reason = self._safe_task_event_text(reason, limit=320)
        fields = {
            "action": normalized_action,
            "status": normalized_status,
            "managed_count": str(len(managed_items)),
            "failure_count": str(len(failure_items)),
            "unresolved_count": str(len(unresolved_items)),
            **({"files": "; ".join(safe_files)} if safe_files else {}),
            **({"failures": "; ".join(safe_failures)} if safe_failures else {}),
            **({"unresolved": ", ".join(safe_unresolved)} if safe_unresolved else {}),
            **({"reason": safe_reason} if safe_reason else {}),
        }
        effective_ts = int(timestamp or time.time())
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "profile_user_id": profile,
                    "session_id": session,
                    "character_pack_id": str(character_pack_id or "").strip(),
                    "timestamp": effective_ts,
                    "fields": fields,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8", errors="ignore")
        ).hexdigest()[:24]
        source_id = f"workspace:generated_cleanup:{effective_ts}:{fingerprint}"
        try:
            stored = system.record_external_event(
                event_type="workspace.generated_cleanup",
                fields=fields,
                source="workspace_management",
                timestamp=effective_ts,
                source_id=source_id,
                topic_terms=["工作台", "生成文件", "清理"],
            )
            return self._status(
                operation,
                True,
                "recorded",
                source_id=str(stored.get("source_id") or source_id),
                index_status=str(stored.get("index_status") or ""),
            )
        except Exception as exc:
            failed_reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore %s failed: %s", operation, failed_reason)
            return self._status(operation, False, "failed", source_id=source_id, reason=failed_reason)

    def _record_material_event(
        self,
        *,
        operation: str,
        event_type: str,
        item: dict[str, Any],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        timestamp: int | None,
        reason: str = "",
        delete_storage: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(item, dict):
            return self._status(operation, False, "invalid_record", reason="item_required")
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        profile = str(profile_user_id or item.get("profile_user_id") or "").strip()
        session = str(session_id or item.get("session_id") or "").strip()
        character = str(
            character_pack_id or detail.get("character_pack_id") or item.get("character_pack_id") or ""
        ).strip()
        attachment_id = str(item.get("attachment_id") or "").strip()
        if not attachment_id:
            return self._status(operation, False, "invalid_record", reason="attachment_id_required")
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile,
            session_id=session,
            character_pack_id=character,
        )
        if system is None:
            return self._status(operation, False, "unavailable", source_id=attachment_id, reason=self._reason)

        effective_ts = int(timestamp or item.get("updated_at") or item.get("created_at") or time.time())
        handle = str(item.get("attachment_handle") or "").strip()
        file_id = handle or attachment_id
        status_part = self._attachment_file_status(item, event_type=event_type, delete_storage=delete_storage)
        source_id = f"attachment:{attachment_id}:{event_type}:{status_part}:{effective_ts}"
        try:
            memcore = self._memcore_module or self._import_memcore()
            material_kind = str(item.get("kind") or "file").strip() or "file"
            kind_label = self._kind_segment(material_kind, fallback="material")
            file_key = self._kind_segment(file_id, fallback="file")
            filename = self._attachment_filename(item)
            derived_status = self._attachment_derived_status(item)
            failure = self._attachment_failure(item)
            source_message_id = image_source_metadata(item).get("source_message_id", "")
            if event_type == "cleanup":
                semantic_text = memcore.render_material_cleanup_text(
                    file_id=file_id,
                    kind=material_kind,
                    filename=filename,
                    file_status=status_part,
                    derived_status=derived_status,
                    reason=reason,
                )
                origin = memcore.EntryOrigin.ENVIRONMENT
                actor = None
                compatibility_role = f"system.material_cleanup {kind_label} {file_key}"
            else:
                actor_stable_id, actor_display_name = self._attachment_actor_identity(item)
                semantic_text = memcore.render_material_reference_text(
                    file_id=file_id,
                    kind=material_kind,
                    filename=filename,
                    mime_type=str(item.get("mime_type") or ""),
                    file_status=status_part,
                    derived_status=derived_status,
                )
                if failure:
                    semantic_text = "\n".join(
                        [
                            semantic_text,
                            "failure:",
                            f"  code: {failure['code']}",
                            f"  reason: {failure['reason']}",
                            *(
                                [f"  observed_bytes: {failure['observed_bytes']}"]
                                if failure.get("observed_bytes")
                                else []
                            ),
                            *([f"  limit_bytes: {failure['limit_bytes']}"] if failure.get("limit_bytes") else []),
                        ]
                    )
                origin = memcore.EntryOrigin.USER
                actor = self._build_actor(actor_stable_id, actor_display_name)
                compatibility_role = f"user.attachment {kind_label} {file_key}"
            entry = memcore.TimelineEntryInput(
                source_id=source_id,
                kind=f"material.{event_type}",
                origin=origin,
                turn_role=None,
                semantic_text=semantic_text,
                timestamp=effective_ts,
                payload={
                    "file_id": file_id,
                    "kind": material_kind,
                    "filename": filename,
                    "mime_type": str(item.get("mime_type") or ""),
                    "file_status": status_part,
                    "derived_status": derived_status,
                    **({"source_message_id": source_message_id} if source_message_id else {}),
                    **({"failure": failure} if failure else {}),
                    **({"reason": str(reason or "")} if event_type == "cleanup" else {}),
                },
                actor=actor,
                memory_metadata={},
                annotation_status=memcore.AnnotationStatus.UNANNOTATED,
                retrieval_policy=memcore.RetrievalPolicy.EXPLICIT,
                retrieval_visibility=memcore.RetrievalVisibility.EXPLICIT,
                semanticize=True,
                compatibility_role=compatibility_role,
            )
            written = system.append_standalone_entry(entry)
            return self._status(
                operation,
                True,
                "recorded",
                source_id=str(written.source_id or source_id),
                index_status=str(written.index_status or ""),
            )
        except Exception as exc:
            failed_reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore %s failed: %s", operation, failed_reason)
            return self._status(operation, False, "failed", source_id=source_id, reason=failed_reason)

    @staticmethod
    def _attachment_filename(item: dict[str, Any]) -> str:
        for key in ("origin_name", "summary_title", "attachment_handle", "attachment_id"):
            text = str(item.get(key) or "").strip()
            if text:
                return text[:160]
        return "attachment"

    @staticmethod
    def _attachment_actor_identity(item: dict[str, Any]) -> tuple[str, str]:
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        raw_id = str(detail.get("qq_sender_id") or "").strip()
        if not raw_id:
            return "", ""
        stable_id = raw_id if raw_id.startswith("qq:") else f"qq:{raw_id}"
        display_name = str(detail.get("qq_sender_label") or "").strip()
        return stable_id, display_name

    @staticmethod
    def _attachment_file_status(
        item: dict[str, Any],
        *,
        event_type: str,
        delete_storage: bool,
    ) -> str:
        if event_type == "cleanup":
            return "deleted" if delete_storage else "cleared"
        status = str(item.get("status") or "").strip().lower()
        if status == "pending_observation":
            return "processing"
        if status in {"ready", "failed", "cleared"}:
            return status
        return status or "unknown"

    @staticmethod
    def _attachment_derived_status(item: dict[str, Any]) -> str:
        status = str(item.get("status") or "").strip().lower()
        if status == "pending_observation":
            return "processing"
        if status == "failed":
            return "failed"
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        if str(item.get("summary_title") or "").strip() or str(item.get("short_hint") or "").strip() or detail:
            return "ready"
        if status == "ready":
            return "ready"
        return "unknown"

    @staticmethod
    def _attachment_failure(item: dict[str, Any]) -> dict[str, Any]:
        if str(item.get("status") or "").strip().lower() != "failed":
            return {}
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        raw_failure = detail.get("failure") if isinstance(detail.get("failure"), dict) else {}
        code = str(raw_failure.get("code") or item.get("error_message") or "attachment_failed").strip()
        reason = " ".join(str(raw_failure.get("reason") or item.get("short_hint") or "附件处理失败。").split()).strip()
        failure: dict[str, Any] = {
            "code": code[:120],
            "reason": reason[:500],
        }
        for field in ("observed_bytes", "limit_bytes"):
            try:
                value = max(0, int(raw_failure.get(field) or 0))
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                failure[field] = value
        return failure
