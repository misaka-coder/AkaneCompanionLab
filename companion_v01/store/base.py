"""Base utilities and mappers for store mixins."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any, Callable

from ..text_utils import timestamp_to_date_label, infer_time_of_day

CHARACTER_PACK_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
GENERATED_FILE_FORMAT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_+-]{0,15}$")
logger = logging.getLogger("akane.store")


class MessageSourceIdCollisionError(ValueError):
    """A caller-owned message source ID points at different immutable content."""
    status = "collision"
    reason = "message_source_id_collision"

    def __init__(self) -> None:
        super().__init__(self.reason)


def normalize_character_pack_id(value: Any) -> str:
    pack_id = str(value or "").strip()
    if not pack_id or not CHARACTER_PACK_ID_PATTERN.fullmatch(pack_id):
        return ""
    return pack_id


def _loads_json_object(value: Any) -> dict[str, Any]:
    from .json_utils import loads_json_object as _fn
    return _fn(value)


class BaseStoreMixin:
    """Row mappers, normalizers, and JSON helpers shared by all store mixins."""

    def _row_to_message(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "source_id": row["source_id"],
            "profile_user_id": row["profile_user_id"],
            "session_id": row["session_id"],
            "character_pack_id": normalize_character_pack_id(row.get("character_pack_id", "")),
            "seq_no": int(row["seq_no"]),
            "role": row["role"],
            "content": row["content"],
            "timestamp": int(row["timestamp"]),
            "date_label": row["date_label"],
            "time_of_day": row["time_of_day"],
            "semantic_tags": json.loads(row.get("semantic_tags_json") or "[]"),
            "memory_metadata": _loads_json_object(row.get("memory_metadata_json")),
            "index_in_vector": bool(int(row.get("index_in_vector", 1) or 0)),
            "is_summarized": int(row.get("is_summarized", 0)),
            "summary_id": row.get("summary_id", "") or "",
            "entry_type": "raw",
        }



    def _row_to_summary(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "summary_id": row["summary_id"],
            "source_id": row["summary_id"],
            "profile_user_id": row["profile_user_id"],
            "session_id": row["session_id"],
            "character_pack_id": normalize_character_pack_id(row.get("character_pack_id", "")),
            "timestamp": int(row["timestamp"]),
            "date_label": row["date_label"],
            "time_of_day": row["time_of_day"],
            "period_label": row["period_label"],
            "event_type": row["event_type"],
            "importance": float(row["importance"]),
            "diary_summary": row["diary_summary"],
            "key_events": json.loads(row.get("key_events_json") or "[]"),
            "core_facts": json.loads(row.get("core_facts_json") or "[]"),
            "semantic_tags": json.loads(row.get("semantic_tags_json") or "[]"),
            "memory_metadata": _loads_json_object(row.get("memory_metadata_json")),
            "is_semanticized": int(row.get("is_semanticized", 0)),
            "semantic_id": row.get("semantic_id", "") or "",
            "source_start_seq": int(row["source_start_seq"]),
            "source_end_seq": int(row["source_end_seq"]),
            "source_ids": json.loads(row.get("source_ids_json") or "[]"),
            "entry_type": "summary",
        }



    def _row_to_semantic_summary(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "semantic_id": row["semantic_id"],
            "source_id": row["semantic_id"],
            "profile_user_id": row["profile_user_id"],
            "session_id": row["session_id"],
            "character_pack_id": normalize_character_pack_id(row.get("character_pack_id", "")),
            "created_at": int(row["created_at"]),
            "timestamp": int(row["timestamp"]),
            "period_start_ts": int(row["period_start_ts"]),
            "period_end_ts": int(row["period_end_ts"]),
            "date_label": row["date_label"],
            "time_of_day": row["time_of_day"],
            "importance": float(row["importance"]),
            "semantic_summary": row["semantic_summary"],
            "stable_facts": json.loads(row.get("stable_facts_json") or "[]"),
            "recurring_topics": json.loads(row.get("recurring_topics_json") or "[]"),
            "important_people": json.loads(row.get("important_people_json") or "[]"),
            "open_loops": json.loads(row.get("open_loops_json") or "[]"),
            "semantic_tags": json.loads(row.get("semantic_tags_json") or "[]"),
            "memory_metadata": _loads_json_object(row.get("memory_metadata_json")),
            "source_summary_ids": json.loads(row.get("source_summary_ids_json") or "[]"),
            "reinforcement_count": int(row.get("reinforcement_count", 1) or 1),
            "last_reinforced_ts": int(row.get("last_reinforced_ts", row["timestamp"]) or row["timestamp"]),
            "entry_type": "semantic_summary",
        }



    def _iter_table_batches(
        self,
        *,
        table_name: str,
        converter,
        batch_size: int,
        where_clause: str = "",
    ):
        normalized_batch_size = max(1, int(batch_size))
        last_rowid = 0
        normalized_where = str(where_clause or "").strip()
        if normalized_where:
            normalized_where = f"({normalized_where}) AND rowid > ?"
        else:
            normalized_where = "rowid > ?"
        while True:
            with self._connect() as conn:
                rows = conn.execute(
                    f"""
                    SELECT rowid AS _rowid, * FROM {table_name}
                    WHERE {normalized_where}
                    ORDER BY rowid ASC
                    LIMIT ?
                    """,
                    (int(last_rowid), normalized_batch_size),
                ).fetchall()
            if not rows:
                return
            payload = [converter(dict(row)) for row in rows]
            if payload:
                yield payload
            last_rowid = int(rows[-1]["_rowid"])



    def _row_to_eval_turn(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "trace_id": row["trace_id"],
            "created_at": int(row["created_at"]),
            "session_id": row["session_id"],
            "profile_user_id": row["profile_user_id"],
            "character_pack_id": normalize_character_pack_id(row.get("character_pack_id", "")),
            "user_message": row["user_message"],
            "router_json": json.loads(row.get("router_json") or "{}"),
            "verifier_json": json.loads(row.get("verifier_json") or "{}"),
            "final_json": json.loads(row.get("final_json") or "{}"),
        }



    def _row_to_session(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "session_id": str(row["session_id"]),
            "profile_user_id": str(row["profile_user_id"]),
            "character_pack_id": normalize_character_pack_id(row.get("character_pack_id", "")),
            "display_title": str(row.get("display_title", "") or "").strip() or "新的对话",
            "current_gift_focus_asset_id": str(row.get("current_gift_focus_asset_id", "") or "").strip(),
            "current_gift_focus_updated_at": int(row.get("current_gift_focus_updated_at", 0) or 0),
            "created_at": int(row.get("created_at", 0) or 0),
            "updated_at": int(row.get("updated_at", row.get("created_at", 0)) or 0),
        }



    def _row_to_reminder(
        self,
        row: dict[str, Any],
        *,
        status: str | None = None,
        fired_at: int | None = None,
    ) -> dict[str, Any]:
        return {
            "reminder_id": row["reminder_id"],
            "profile_user_id": row["profile_user_id"],
            "session_id": row["session_id"],
            "content": row["content"],
            "due_ts": int(row["due_ts"]),
            "date_label": row["date_label"],
            "time_of_day": row["time_of_day"],
            "raw_time_text": row.get("raw_time_text", "") or "",
            "status": status or row.get("status", "pending") or "pending",
            "created_at": int(row["created_at"]),
            "fired_at": int(fired_at if fired_at is not None else row.get("fired_at", 0) or 0),
        }



    def _row_to_persona_card(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "card_id": str(row.get("card_id") or ""),
            "profile_user_id": str(row.get("profile_user_id") or ""),
            "session_id": str(row.get("session_id") or ""),
            "name": str(row.get("name") or ""),
            "status": self._normalize_persona_status(row.get("status")),
            "summary": str(row.get("summary") or ""),
            "speech_style": str(row.get("speech_style") or ""),
            "interaction_bias": str(row.get("interaction_bias") or ""),
            "resource_preference": str(row.get("resource_preference") or ""),
            "switch_hint": str(row.get("switch_hint") or ""),
            "unsuitable_contexts": str(row.get("unsuitable_contexts") or ""),
            "created_reason": str(row.get("created_reason") or ""),
            "updated_reason": str(row.get("updated_reason") or ""),
            "source_ids": self._normalize_string_list(self._safe_json_loads(row.get("source_ids_json"), fallback=[])),
            "created_at": int(row.get("created_at", 0) or 0),
            "updated_at": int(row.get("updated_at", row.get("created_at", 0)) or 0),
            "archived_at": int(row.get("archived_at", 0) or 0),
        }



    def _row_to_persona_event(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = self._safe_json_loads(row.get("payload_json"), fallback={})
        if not isinstance(payload, dict):
            payload = {}
        return {
            "event_id": str(row.get("event_id") or ""),
            "profile_user_id": str(row.get("profile_user_id") or ""),
            "session_id": str(row.get("session_id") or ""),
            "card_id": str(row.get("card_id") or ""),
            "event_type": str(row.get("event_type") or ""),
            "reason": str(row.get("reason") or ""),
            "source_id": str(row.get("source_id") or ""),
            "created_at": int(row.get("created_at", 0) or 0),
            "payload": payload,
        }



    def _row_to_user_media_asset(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = self._safe_json_loads(row.get("payload_json"), fallback={})
        if not isinstance(payload, dict):
            payload = {}
        legacy_payload = self._build_legacy_gift_payload(row)
        merged_payload = {
            **legacy_payload,
            **payload,
        }
        asset_type = str(row.get("asset_type") or "").strip() or self._infer_asset_type_from_media_kind(
            row.get("media_kind")
        )
        status = self._normalize_gift_status(row.get("status"))
        origin_source_id = str(row.get("origin_source_id") or "").strip()
        source_ids = self._normalize_string_list(self._safe_json_loads(row.get("source_ids_json"), fallback=[]))
        if origin_source_id and origin_source_id not in source_ids:
            source_ids.insert(0, origin_source_id)
        artifact_flags = self._safe_json_loads(row.get("artifact_flags_json"), fallback={})
        if not isinstance(artifact_flags, dict):
            artifact_flags = {}
        container = self._derive_container_fields(
            asset_type=asset_type,
            status=status,
            payload=merged_payload,
            container_type=row.get("container_type"),
            container_key=row.get("container_key"),
            container_name=row.get("container_name"),
            artifact_flags=artifact_flags,
        )
        return {
            "asset_id": str(row["asset_id"]),
            "resource_id": str(row.get("resource_id") or ""),
            "profile_user_id": str(row["profile_user_id"]),
            "session_id": str(row["session_id"]),
            "media_kind": str(row.get("media_kind") or ""),
            "origin_name": str(row.get("origin_name") or ""),
            "display_name": str(row.get("display_name") or ""),
            "mime_type": str(row.get("mime_type") or ""),
            "file_ext": str(row.get("file_ext") or ""),
            "file_size": int(row.get("file_size", 0) or 0),
            "storage_relpath": str(row.get("storage_relpath") or ""),
            "status": status,
            "asset_type": asset_type,
            "origin_event_type": str(row.get("origin_event_type") or "").strip(),
            "origin_source_id": origin_source_id,
            "source_ids": source_ids,
            "payload": merged_payload,
            "created_at": int(row.get("created_at", 0) or 0),
            "updated_at": int(row.get("updated_at", row.get("created_at", 0)) or 0),
            "last_decision_at": int(row.get("last_decision_at", 0) or 0),
            "last_touched_at": int(row.get("last_touched_at", row.get("updated_at", row.get("created_at", 0))) or 0),
            "container_type": container["container_type"],
            "container_key": container["container_key"],
            "container_name": container["container_name"],
            "artifact_flags": container["artifact_flags"],
        }



    def _row_to_attachment_inbox_item(self, row: dict[str, Any]) -> dict[str, Any]:
        detail = self._safe_json_loads(row.get("detail_json"), fallback={})
        if not isinstance(detail, dict):
            detail = {}
        return {
            "attachment_id": str(row.get("attachment_id") or ""),
            "profile_user_id": str(row.get("profile_user_id") or ""),
            "session_id": str(row.get("session_id") or ""),
            "source": str(row.get("source") or ""),
            "kind": self._normalize_attachment_kind(row.get("kind")),
            "attachment_handle": str(row.get("attachment_handle") or ""),
            "sequence_no": int(row.get("sequence_no", 0) or 0),
            "status": self._normalize_attachment_status(row.get("status")),
            "origin_name": str(row.get("origin_name") or ""),
            "mime_type": str(row.get("mime_type") or ""),
            "file_ext": str(row.get("file_ext") or ""),
            "file_size": int(row.get("file_size", 0) or 0),
            "storage_relpath": str(row.get("storage_relpath") or ""),
            "source_event_id": str(row.get("source_event_id") or ""),
            "source_message_id": str(row.get("source_message_id") or ""),
            "summary_title": str(row.get("summary_title") or ""),
            "short_hint": str(row.get("short_hint") or ""),
            "detail": detail,
            "error_message": str(row.get("error_message") or ""),
            "created_at": int(row.get("created_at", 0) or 0),
            "updated_at": int(row.get("updated_at", row.get("created_at", 0)) or 0),
            "last_used_at": int(row.get("last_used_at", 0) or 0),
            "focus_rank": int(row.get("focus_rank", 0) or 0),
            "expires_at": int(row.get("expires_at", 0) or 0),
        }



    def _row_to_generated_file(self, row: dict[str, Any]) -> dict[str, Any]:
        content_card = self._safe_json_loads(row.get("content_card_json"), fallback={})
        if not isinstance(content_card, dict):
            content_card = {}
        source_ids = self._safe_json_loads(row.get("source_ids_json"), fallback=[])
        if not isinstance(source_ids, list):
            source_ids = []
        return {
            "generated_id": str(row.get("generated_id") or ""),
            "profile_user_id": str(row.get("profile_user_id") or ""),
            "session_id": str(row.get("session_id") or ""),
            "generated_handle": str(row.get("generated_handle") or ""),
            "sequence_no": int(row.get("sequence_no", 0) or 0),
            "status": self._normalize_generated_status(row.get("status")),
            "output_title": str(row.get("output_title") or ""),
            "output_format": self._normalize_generated_format(row.get("output_format")),
            "mime_type": str(row.get("mime_type") or ""),
            "file_ext": str(row.get("file_ext") or ""),
            "file_size": int(row.get("file_size", 0) or 0),
            "storage_relpath": str(row.get("storage_relpath") or ""),
            "source_ids": [str(item or "").strip() for item in source_ids if str(item or "").strip()],
            "content_card": content_card,
            "summary": str(row.get("summary") or ""),
            "created_by_tool": str(row.get("created_by_tool") or ""),
            "version_of_generated_id": str(row.get("version_of_generated_id") or ""),
            "version_no": int(row.get("version_no", 1) or 1),
            "delivery_status": str(row.get("delivery_status") or "pending"),
            "created_at": int(row.get("created_at", 0) or 0),
            "updated_at": int(row.get("updated_at", row.get("created_at", 0)) or 0),
            "last_used_at": int(row.get("last_used_at", 0) or 0),
        }



    def _row_to_desktop_music_timeline(self, row: dict[str, Any]) -> dict[str, Any]:
        segments = self._safe_json_loads(row.get("segments_json"), fallback=[])
        if not isinstance(segments, list):
            segments = []
        return {
            "timeline_id": str(row.get("timeline_id") or ""),
            "profile_user_id": str(row.get("profile_user_id") or ""),
            "session_id": str(row.get("session_id") or ""),
            "source_id": str(row.get("source_id") or ""),
            "source_kind": str(row.get("source_kind") or ""),
            "source_handle": str(row.get("source_handle") or ""),
            "title": str(row.get("title") or ""),
            "status": self._normalize_desktop_music_timeline_status(row.get("status")),
            "segments": [item for item in segments if isinstance(item, dict)],
            "rolling_summary": str(row.get("rolling_summary") or ""),
            "ready_until_seconds": float(row.get("ready_until_seconds", 0) or 0),
            "transcript_generated_id": str(row.get("transcript_generated_id") or ""),
            "transcript_generated_handle": str(row.get("transcript_generated_handle") or ""),
            "error_message": str(row.get("error_message") or ""),
            "source_size": int(row.get("source_size", 0) or 0),
            "source_mtime_ns": int(row.get("source_mtime_ns", 0) or 0),
            "created_at": int(row.get("created_at", 0) or 0),
            "updated_at": int(row.get("updated_at", row.get("created_at", 0)) or 0),
        }



    def _row_to_task_workspace(self, row: dict[str, Any]) -> dict[str, Any]:
        raw_request = self._safe_json_loads(row.get("raw_request_json"), fallback={})
        if not isinstance(raw_request, dict):
            raw_request = {}
        success_criteria = self._safe_json_loads(row.get("success_criteria_json"), fallback=[])
        if not isinstance(success_criteria, list):
            success_criteria = []
        constraints = self._safe_json_loads(row.get("constraints_json"), fallback=[])
        if not isinstance(constraints, list):
            constraints = []
        steps = self._safe_json_loads(row.get("steps_json"), fallback=[])
        if not isinstance(steps, list):
            steps = []
        artifacts = self._safe_json_loads(row.get("artifacts_json"), fallback=[])
        if not isinstance(artifacts, list):
            artifacts = []
        pending_question = self._safe_json_loads(row.get("pending_question_json"), fallback={})
        if not isinstance(pending_question, dict):
            pending_question = {}
        metadata = self._safe_json_loads(row.get("metadata_json"), fallback={})
        if not isinstance(metadata, dict):
            metadata = {}
        return {
            "task_id": str(row.get("task_id") or ""),
            "profile_user_id": str(row.get("profile_user_id") or ""),
            "session_id": str(row.get("session_id") or ""),
            "owner": str(row.get("owner") or "Akane"),
            "status": self._normalize_task_workspace_status(row.get("status")),
            "raw_request": raw_request,
            "normalized_goal": str(row.get("normalized_goal") or ""),
            "success_criteria": success_criteria,
            "constraints": constraints,
            "steps": steps,
            "artifacts": artifacts,
            "pending_question": pending_question,
            "metadata": metadata,
            "created_at": int(row.get("created_at", 0) or 0),
            "updated_at": int(row.get("updated_at", row.get("created_at", 0)) or 0),
            "completed_at": int(row.get("completed_at", 0) or 0),
            "cleaned_at": int(row.get("cleaned_at", 0) or 0),
        }



    def _row_to_task_workspace_event(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = self._safe_json_loads(row.get("payload_json"), fallback={})
        if not isinstance(payload, dict):
            payload = {}
        return {
            "event_id": str(row.get("event_id") or ""),
            "task_id": str(row.get("task_id") or ""),
            "profile_user_id": str(row.get("profile_user_id") or ""),
            "session_id": str(row.get("session_id") or ""),
            "event_type": str(row.get("event_type") or ""),
            "from_actor": str(row.get("from_actor") or ""),
            "priority": self._normalize_task_event_priority(row.get("priority")),
            "requires_user": bool(int(row.get("requires_user", 0) or 0)),
            "message": str(row.get("message") or ""),
            "payload": payload,
            "status": self._normalize_task_event_status(row.get("status")),
            "created_at": int(row.get("created_at", 0) or 0),
            "handled_at": int(row.get("handled_at", 0) or 0),
        }



    def _row_to_vision_observation(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = self._safe_json_loads(row.get("observation_json"), fallback={})
        if not isinstance(payload, dict):
            payload = {}
        return {
            "observation_id": str(row.get("observation_id") or ""),
            "observation_type": str(row.get("observation_type") or ""),
            "resource_fingerprint": str(row.get("resource_fingerprint") or ""),
            "target_id": str(row.get("target_id") or ""),
            "source_path": str(row.get("source_path") or ""),
            "public_path": str(row.get("public_path") or ""),
            "prompt_version": str(row.get("prompt_version") or "v1"),
            "provider": str(row.get("provider") or ""),
            "model_name": str(row.get("model_name") or ""),
            "status": str(row.get("status") or "ready"),
            "summary": str(row.get("summary") or ""),
            "observation": payload,
            "error_message": str(row.get("error_message") or ""),
            "created_at": int(row.get("created_at", 0) or 0),
            "updated_at": int(row.get("updated_at", row.get("created_at", 0)) or 0),
        }



    def _normalize_gift_status(self, value: Any) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"", "offered"}:
            return "pending"
        if normalized == "saved":
            return "kept"
        if normalized in {"pending", "kept", "internalized", "rejected"}:
            return normalized
        return "pending"



    def _normalize_persona_status(self, value: Any) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"active", "inactive", "archived", "deleted"}:
            return normalized
        return "inactive"



    def _normalize_attachment_status(self, value: Any) -> str:
        normalized = str(value or "").strip().lower()
        aliases = {
            "pending": "pending_observation",
            "processing": "pending_observation",
            "observing": "pending_observation",
            "done": "ready",
            "ok": "ready",
            "error": "failed",
            "deleted": "cleared",
            "removed": "cleared",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized in {"pending_observation", "ready", "failed", "cleared"}:
            return normalized
        return "pending_observation"



    def _normalize_desktop_music_timeline_status(self, value: Any) -> str:
        normalized = str(value or "").strip().lower()
        aliases = {
            "queued": "pending",
            "building": "processing",
            "done": "ready",
            "ok": "ready",
            "error": "failed",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized in {"pending", "processing", "ready", "failed", "partial"}:
            return normalized
        return "pending"



    def _normalize_attachment_kind(self, value: Any) -> str:
        normalized = str(value or "").strip().lower()
        aliases = {
            "photo": "image",
            "picture": "image",
            "pic": "image",
            "img": "image",
            "file": "file",
            "doc": "document",
            "text": "document",
            "txt": "document",
            "pdf": "document",
            "music": "audio",
            "song": "audio",
            "voice": "audio",
            "any": "any",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized in {"any", "image", "file", "document", "audio"}:
            return normalized
        return "file"



    def _normalize_generated_status(self, value: Any) -> str:
        normalized = str(value or "").strip().lower()
        aliases = {
            "done": "ready",
            "ok": "ready",
            "created": "ready",
            "error": "failed",
            "deleted": "removed",
            "cleared": "removed",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized in {"ready", "failed", "removed"}:
            return normalized
        return "ready"



    def _normalize_generated_format(self, value: Any) -> str:
        normalized = str(value or "").strip().lower().lstrip(".")
        aliases = {
            "markdown": "md",
            "text": "txt",
            "plain": "txt",
            "word": "docx",
            "excel": "xlsx",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized in {
            "txt",
            "md",
            "docx",
            "xlsx",
            "pdf",
            "json",
            "csv",
            "html",
            "srt",
            "vtt",
            "zip",
            "mp3",
            "wav",
            "flac",
            "m4a",
            "aac",
            "ogg",
            "opus",
            "png",
        }:
            return normalized
        # ExecutionResourceBridge validates generic artifact formats before
        # they reach the store.  The store must preserve that stable wire
        # value instead of silently turning a real .java/.py/.cpp file into
        # Markdown.  Keep the grammar deliberately filename-safe so direct
        # legacy callers still cannot smuggle paths or arbitrary labels into
        # generated-file metadata.
        if GENERATED_FILE_FORMAT_PATTERN.fullmatch(normalized):
            return normalized
        return "md"



    def _attachment_handle_prefix(self, kind: Any) -> str:
        normalized = self._normalize_attachment_kind(kind)
        if normalized == "image":
            return "img"
        if normalized == "audio":
            return "audio"
        return "file"



    def _sequence_no_from_generated_handle(self, value: Any) -> int:
        text = str(value or "").strip()
        if "_" not in text:
            return 0
        try:
            return int(text.rsplit("_", 1)[-1])
        except ValueError:
            return 0



    def _sequence_no_from_attachment_handle(self, value: Any) -> int:
        text = str(value or "").strip()
        if "_" not in text:
            return 0
        try:
            return int(text.rsplit("_", 1)[-1])
        except ValueError:
            return 0



    def _normalize_gift_status_list(self, values: list[str] | tuple[str, ...] | set[str] | None) -> list[str]:
        normalized: list[str] = []
        for value in values or []:
            status = self._normalize_gift_status(value)
            if status not in normalized:
                normalized.append(status)
        return normalized



    def _normalize_attachment_status_list(
        self,
        values: list[str] | tuple[str, ...] | set[str] | None,
    ) -> list[str]:
        normalized: list[str] = []
        for value in values or []:
            status = self._normalize_attachment_status(value)
            if status not in normalized:
                normalized.append(status)
        return normalized



    def _normalize_generated_status_list(
        self,
        values: list[str] | tuple[str, ...] | set[str] | None,
    ) -> list[str]:
        normalized: list[str] = []
        for value in values or []:
            status = self._normalize_generated_status(value)
            if status not in normalized:
                normalized.append(status)
        return normalized



    def _normalize_task_workspace_status(self, value: Any) -> str:
        normalized = str(value or "").strip().lower()
        aliases = {
            "todo": "queued",
            "pending": "queued",
            "working": "running",
            "done": "completed",
            "ok": "completed",
            "error": "failed",
            "cancelled": "canceled",
            "deleted": "cleaned",
            "cleared": "cleaned",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized in {"queued", "running", "waiting_user", "completed", "failed", "canceled", "cleaned"}:
            return normalized
        return "queued"



    def _normalize_task_workspace_status_list(
        self,
        values: list[str] | tuple[str, ...] | set[str] | None,
    ) -> list[str]:
        normalized: list[str] = []
        for value in values or []:
            status = self._normalize_task_workspace_status(value)
            if status not in normalized:
                normalized.append(status)
        return normalized



    def _normalize_task_event_status(self, value: Any) -> str:
        normalized = str(value or "").strip().lower()
        aliases = {
            "done": "handled",
            "ok": "handled",
            "read": "handled",
            "new": "pending",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized in {"pending", "handled", "dismissed"}:
            return normalized
        return "pending"



    def _normalize_task_event_priority(self, value: Any) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"low", "normal", "high", "urgent"}:
            return normalized
        return "normal"



    def _normalize_string_list(self, values: Any) -> list[str]:
        if values is None:
            return []
        if isinstance(values, (str, bytes)):
            raw_values = [values]
        elif isinstance(values, (list, tuple, set)):
            raw_values = list(values)
        else:
            return []

        normalized: list[str] = []
        for value in raw_values:
            item = str(value or "").strip()
            if item and item not in normalized:
                normalized.append(item)
        return normalized



    def _infer_asset_type_from_media_kind(self, media_kind: Any) -> str:
        normalized = str(media_kind or "").strip().lower()
        if normalized in {"bgm", "audio", "music", "song"}:
            return "audio"
        if normalized in {"image", "background", "picture"}:
            return "image"
        if normalized in {"virtual", "story_gift", "item"}:
            return "virtual"
        return normalized or "audio"



    def _build_legacy_gift_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        origin_name = str(row.get("origin_name") or "").strip()
        mime_type = str(row.get("mime_type") or "").strip()
        file_ext = str(row.get("file_ext") or "").strip()
        storage_relpath = str(row.get("storage_relpath") or "").strip()

        if origin_name:
            payload["filename"] = origin_name
        if mime_type:
            payload["mime_type"] = mime_type
        if file_ext:
            payload["file_ext"] = file_ext
        if storage_relpath:
            payload["storage_relpath"] = storage_relpath
        file_size = int(row.get("file_size", 0) or 0)
        if file_size > 0:
            payload["file_size"] = file_size
        return payload



    def _normalize_container_type(self, value: Any) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"music", "music_box", "bgm_box"}:
            return "music_box"
        if normalized in {"album", "photo_album"}:
            return "album"
        if normalized in {"note_box", "notes", "note"}:
            return "note_box"
        if normalized in {"keepsake_box", "keepsake", "collection_box", "collection"}:
            return "keepsake_box"
        return normalized



    def _derive_container_fields(
        self,
        *,
        asset_type: Any,
        status: Any,
        payload: dict[str, Any] | None,
        container_type: Any,
        container_key: Any,
        container_name: Any,
        artifact_flags: Any,
    ) -> dict[str, Any]:
        normalized_asset_type = str(asset_type or "").strip().lower() or "audio"
        normalized_status = self._normalize_gift_status(status)
        normalized_payload = payload if isinstance(payload, dict) else {}
        normalized_flags = artifact_flags if isinstance(artifact_flags, dict) else {}

        resolved_type = str(container_type or "").strip().lower()
        resolved_key = str(container_key or "").strip()
        resolved_name = str(container_name or "").strip()

        if not resolved_type:
            if normalized_asset_type == "audio":
                resolved_type = "music_box"
            elif normalized_asset_type == "image":
                resolved_type = "album"
            elif normalized_asset_type in {"text", "note"}:
                resolved_type = "note_box"
            else:
                resolved_type = "keepsake_box"

        if not resolved_key:
            if resolved_type == "music_box":
                resolved_key = "main"
            elif resolved_type == "album":
                resolved_key = str(normalized_payload.get("collection_key") or "").strip() or "memories"
            elif resolved_type == "note_box":
                resolved_key = "notes"
            else:
                resolved_key = "keepsakes"

        if not resolved_name:
            if resolved_type == "music_box":
                resolved_name = "曲库"
            elif resolved_type == "album":
                resolved_name = str(normalized_payload.get("collection_name") or "").strip() or "回忆"
            elif resolved_type == "note_box":
                resolved_name = "便签盒"
            else:
                resolved_name = "收藏盒"

        next_flags = dict(normalized_flags)
        next_flags.setdefault("archived", normalized_status in {"kept", "internalized"})
        next_flags.setdefault("container_version", 1)
        return {
            "container_type": resolved_type,
            "container_key": resolved_key[:64],
            "container_name": resolved_name[:32],
            "artifact_flags": next_flags,
        }



    def _safe_json_loads(self, raw: Any, *, fallback: Any) -> Any:
        from .json_utils import safe_json_loads as _fn

        return _fn(raw, fallback=fallback)
