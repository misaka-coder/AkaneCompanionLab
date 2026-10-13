from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
import uuid
from typing import Any, Callable

from ..text_utils import timestamp_to_date_label, infer_time_of_day
from .base import (
    CHARACTER_PACK_ID_PATTERN,
    GENERATED_FILE_FORMAT_PATTERN,
    MessageSourceIdCollisionError,
    normalize_character_pack_id,
    _loads_json_object,
    logger,
)


class MessageStoreMixin:
    def get_session_messages(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str | None = None,
        limit: int = 120,
        before_seq: int | None = None,
    ) -> list[dict[str, Any]]:
        normalized_character_pack_id = (
            normalize_character_pack_id(character_pack_id) if character_pack_id is not None else None
        )
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM (
                    SELECT * FROM chat_messages
                    WHERE profile_user_id = ? AND session_id = ?
                      AND (? IS NULL OR character_pack_id = ?)
                      AND (? IS NULL OR seq_no < ?)
                    ORDER BY seq_no DESC
                    LIMIT ?
                )
                ORDER BY seq_no ASC
                """,
                (
                    str(profile_user_id),
                    str(session_id),
                    normalized_character_pack_id,
                    normalized_character_pack_id,
                    before_seq,
                    before_seq,
                    max(1, int(limit)),
                ),
            ).fetchall()
        return [self._row_to_message(dict(row)) for row in rows]



    def set_message_write_callback(
        self,
        callback: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        self._message_write_callback = callback



    def _notify_message_write(self, message: dict[str, Any]) -> None:
        callback = self._message_write_callback
        if callback is None:
            return
        try:
            callback(dict(message))
        except Exception as exc:
            logger.warning("message write callback failed: %s", exc)



    def get_memory_timeline_messages(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str = "",
        date_from: str,
        date_to: str,
    ) -> list[dict[str, Any]]:
        normalized_character_pack_id = normalize_character_pack_id(character_pack_id)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM chat_messages
                WHERE profile_user_id = ?
                  AND character_pack_id = ?
                  AND date_label >= ?
                  AND date_label <= ?
                ORDER BY timestamp ASC, session_id ASC, seq_no ASC, source_id ASC
                """,
                (
                    str(profile_user_id),
                    normalized_character_pack_id,
                    str(date_from),
                    str(date_to),
                ),
            ).fetchall()
        return [self._row_to_message(dict(row)) for row in rows]



    def list_memory_timeline_days(self) -> list[dict[str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT profile_user_id, character_pack_id, date_label
                FROM chat_messages
                WHERE TRIM(COALESCE(date_label, '')) != ''
                GROUP BY profile_user_id, character_pack_id, date_label
                ORDER BY profile_user_id ASC, character_pack_id ASC, date_label ASC
                """
            ).fetchall()
        return [
            {
                "profile_user_id": str(row["profile_user_id"] or ""),
                "character_pack_id": normalize_character_pack_id(row["character_pack_id"]),
                "date_label": str(row["date_label"] or ""),
            }
            for row in rows
        ]



    def get_memory_timeline_stats(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str = "",
    ) -> dict[str, Any]:
        normalized_character_pack_id = normalize_character_pack_id(character_pack_id)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    MIN(timestamp) AS first_timestamp,
                    MAX(timestamp) AS last_timestamp,
                    COUNT(*) AS message_count,
                    COUNT(DISTINCT date_label) AS active_day_count
                FROM chat_messages
                WHERE profile_user_id = ?
                  AND character_pack_id = ?
                """,
                (str(profile_user_id), normalized_character_pack_id),
            ).fetchone()
        first_timestamp = int(row["first_timestamp"]) if row and row["first_timestamp"] is not None else 0
        last_timestamp = int(row["last_timestamp"]) if row and row["last_timestamp"] is not None else 0
        return {
            "first_timestamp": first_timestamp,
            "last_timestamp": last_timestamp,
            "message_count": int(row["message_count"] or 0) if row else 0,
            "active_day_count": int(row["active_day_count"] or 0) if row else 0,
        }



    def next_seq_no(
        self,
        session_id: str,
        *,
        profile_user_id: str = "",
        character_pack_id: str | None = None,
    ) -> int:
        normalized_character_pack_id = (
            normalize_character_pack_id(character_pack_id) if character_pack_id is not None else None
        )
        clauses = ["session_id = ?"]
        params: list[Any] = [str(session_id)]
        if str(profile_user_id or "").strip():
            clauses.append("profile_user_id = ?")
            params.append(str(profile_user_id))
        if normalized_character_pack_id is not None:
            clauses.append("character_pack_id = ?")
            params.append(normalized_character_pack_id)
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT COALESCE(MAX(seq_no), 0) AS max_seq FROM chat_messages WHERE {' AND '.join(clauses)}",
                params,
            ).fetchone()
        return int(row["max_seq"]) + 1



    def add_message(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        role: str,
        content: str,
        timestamp: int | None = None,
        date_label: str | None = None,
        time_of_day: str | None = None,
        semantic_tags: list[str] | None = None,
        memory_metadata: dict[str, Any] | None = None,
        index_in_vector: bool = True,
        source_id: str = "",
    ) -> dict[str, Any]:
        ts = int(timestamp or time.time())
        normalized_character_pack_id = normalize_character_pack_id(character_pack_id)
        caller_source_id = str(source_id or "").strip()
        normalized_source_id = caller_source_id or str(uuid.uuid4())
        normalized_profile_user_id = str(profile_user_id)
        normalized_session_id = str(session_id)
        normalized_role = str(role)
        normalized_content = str(content)
        if caller_source_id:
            existing = self._resolve_idempotent_message(
                source_id=normalized_source_id,
                profile_user_id=normalized_profile_user_id,
                session_id=normalized_session_id,
                character_pack_id=normalized_character_pack_id,
                role=normalized_role,
                content=normalized_content,
            )
            if existing is not None:
                return existing
        record = {
            "source_id": normalized_source_id,
            "profile_user_id": normalized_profile_user_id,
            "session_id": normalized_session_id,
            "character_pack_id": normalized_character_pack_id,
            "seq_no": self.next_seq_no(
                session_id,
                profile_user_id=profile_user_id,
                character_pack_id=normalized_character_pack_id,
            ),
            "role": normalized_role,
            "content": normalized_content,
            "timestamp": ts,
            "date_label": str(date_label or timestamp_to_date_label(ts)),
            "time_of_day": str(time_of_day or infer_time_of_day(ts)),
            "semantic_tags_json": json.dumps(semantic_tags or [], ensure_ascii=False),
            "memory_metadata_json": json.dumps(memory_metadata or {}, ensure_ascii=False),
            "index_in_vector": 1 if bool(index_in_vector) else 0,
            "is_summarized": 0,
            "summary_id": "",
        }
        self.ensure_session(
            profile_user_id=record["profile_user_id"],
            session_id=record["session_id"],
            character_pack_id=record["character_pack_id"],
            timestamp=record["timestamp"],
        )
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO chat_messages (
                        source_id, profile_user_id, session_id, character_pack_id, seq_no, role, content,
                        timestamp, date_label, time_of_day, semantic_tags_json,
                        memory_metadata_json, index_in_vector, is_summarized, summary_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record["source_id"],
                        record["profile_user_id"],
                        record["session_id"],
                        record["character_pack_id"],
                        record["seq_no"],
                        record["role"],
                        record["content"],
                        record["timestamp"],
                        record["date_label"],
                        record["time_of_day"],
                        record["semantic_tags_json"],
                        record["memory_metadata_json"],
                        record["index_in_vector"],
                        record["is_summarized"],
                        record["summary_id"],
                    ),
                )
        except sqlite3.IntegrityError:
            if caller_source_id:
                existing = self._resolve_idempotent_message(
                    source_id=normalized_source_id,
                    profile_user_id=normalized_profile_user_id,
                    session_id=normalized_session_id,
                    character_pack_id=normalized_character_pack_id,
                    role=normalized_role,
                    content=normalized_content,
                )
                if existing is not None:
                    return existing
            raise
        message = self._row_to_message(record)
        self._notify_message_write(message)
        return message



    def _resolve_idempotent_message(
        self,
        *,
        source_id: str,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        role: str,
        content: str,
    ) -> dict[str, Any] | None:
        existing = self.get_message_by_source_id(source_id)
        if existing is None:
            return None
        immutable_identity = (
            "profile_user_id",
            "session_id",
            "character_pack_id",
            "role",
            "content",
        )
        expected = {
            "profile_user_id": profile_user_id,
            "session_id": session_id,
            "character_pack_id": character_pack_id,
            "role": role,
            "content": content,
        }
        if all(str(existing.get(field) or "") == str(expected[field]) for field in immutable_identity):
            return existing
        raise MessageSourceIdCollisionError()



    def update_message_semantic_tags(self, source_id: str, semantic_tags: list[str]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE chat_messages
                SET semantic_tags_json = ?
                WHERE source_id = ?
                """,
                (
                    json.dumps(semantic_tags or [], ensure_ascii=False),
                    str(source_id),
                ),
            )



    def update_message_memory_metadata(self, source_id: str, memory_metadata: dict[str, Any] | None) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE chat_messages
                SET memory_metadata_json = ?
                WHERE source_id = ?
                """,
                (
                    json.dumps(memory_metadata or {}, ensure_ascii=False),
                    str(source_id),
                ),
            )
        record = self.get_message_by_source_id(source_id)
        if record is not None:
            self._notify_message_write(record)



    def update_message_index_in_vector(self, source_id: str, index_in_vector: bool) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE chat_messages
                SET index_in_vector = ?
                WHERE source_id = ?
                """,
                (
                    1 if bool(index_in_vector) else 0,
                    str(source_id),
                ),
            )



    def get_message_by_source_id(self, source_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM chat_messages WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        return self._row_to_message(dict(row)) if row else None



    def get_message_by_seq_no(
        self,
        session_id: str,
        seq_no: int,
        *,
        profile_user_id: str = "",
        character_pack_id: str | None = None,
    ) -> dict[str, Any] | None:
        clauses = ["session_id = ?", "seq_no = ?"]
        params: list[Any] = [str(session_id), int(seq_no)]
        normalized_profile_user_id = str(profile_user_id or "").strip()
        if normalized_profile_user_id:
            clauses.append("profile_user_id = ?")
            params.append(normalized_profile_user_id)
        if character_pack_id is not None:
            clauses.append("character_pack_id = ?")
            params.append(normalize_character_pack_id(character_pack_id))
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT * FROM chat_messages
                WHERE {" AND ".join(clauses)}
                LIMIT 1
                """,
                params,
            ).fetchone()
        return self._row_to_message(dict(row)) if row else None



    def get_record_by_source_id(self, source_id: str) -> dict[str, Any] | None:
        if source_id.startswith("semantic::"):
            return self.get_semantic_summary_by_id(source_id)
        if source_id.startswith("summary::"):
            return self.get_summary_by_id(source_id)
        return self.get_message_by_source_id(source_id)



    def get_context_slice(
        self,
        session_id: str,
        center_seq_no: int,
        window: int = 1,
        *,
        profile_user_id: str = "",
        character_pack_id: str | None = None,
    ) -> list[dict[str, Any]]:
        start_seq = max(1, int(center_seq_no) - int(window))
        end_seq = int(center_seq_no) + int(window)
        clauses = ["session_id = ?", "seq_no BETWEEN ? AND ?"]
        params: list[Any] = [str(session_id), start_seq, end_seq]
        normalized_profile_user_id = str(profile_user_id or "").strip()
        if normalized_profile_user_id:
            clauses.append("profile_user_id = ?")
            params.append(normalized_profile_user_id)
        if character_pack_id is not None:
            clauses.append("character_pack_id = ?")
            params.append(normalize_character_pack_id(character_pack_id))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM chat_messages
                WHERE {" AND ".join(clauses)}
                ORDER BY seq_no ASC
                """,
                params,
            ).fetchall()
        return [self._row_to_message(dict(row)) for row in rows]
