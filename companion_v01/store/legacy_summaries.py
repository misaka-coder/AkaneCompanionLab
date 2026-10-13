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


class LegacySummaryStoreMixin:
    def get_unsummarized_messages(
        self,
        session_id: str,
        *,
        character_pack_id: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized_character_pack_id = (
            normalize_character_pack_id(character_pack_id) if character_pack_id is not None else None
        )
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM chat_messages
                WHERE session_id = ? AND is_summarized = 0
                  AND (? IS NULL OR character_pack_id = ?)
                ORDER BY seq_no ASC
                """,
                (session_id, normalized_character_pack_id, normalized_character_pack_id),
            ).fetchall()
        return [self._row_to_message(dict(row)) for row in rows]



    def get_unsummarized_count(
        self,
        session_id: str,
        *,
        character_pack_id: str | None = None,
    ) -> int:
        normalized_character_pack_id = (
            normalize_character_pack_id(character_pack_id) if character_pack_id is not None else None
        )
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM chat_messages
                WHERE session_id = ? AND is_summarized = 0
                  AND (? IS NULL OR character_pack_id = ?)
                """,
                (session_id, normalized_character_pack_id, normalized_character_pack_id),
            ).fetchone()
        return int(row["cnt"])



    def get_oldest_unsummarized_batch(
        self,
        session_id: str,
        limit: int = 20,
        *,
        character_pack_id: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized_character_pack_id = (
            normalize_character_pack_id(character_pack_id) if character_pack_id is not None else None
        )
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM chat_messages
                WHERE session_id = ? AND is_summarized = 0
                  AND (? IS NULL OR character_pack_id = ?)
                ORDER BY seq_no ASC
                LIMIT ?
                """,
                (session_id, normalized_character_pack_id, normalized_character_pack_id, int(limit)),
            ).fetchall()
        return [self._row_to_message(dict(row)) for row in rows]



    def mark_messages_summarized(self, source_ids: list[str], summary_id: str) -> None:
        if not source_ids:
            return
        placeholders = ",".join("?" for _ in source_ids)
        with self._connect() as conn:
            conn.execute(
                f"""
                UPDATE chat_messages
                SET is_summarized = 1, summary_id = ?
                WHERE source_id IN ({placeholders})
                """,
                [summary_id, *source_ids],
            )



    def add_summary(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        timestamp: int,
        date_label: str,
        time_of_day: str,
        period_label: str,
        event_type: str,
        importance: float,
        diary_summary: str,
        key_events: list[str],
        core_facts: list[str],
        semantic_tags: list[str],
        source_start_seq: int,
        source_end_seq: int,
        source_ids: list[str],
        memory_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_character_pack_id = normalize_character_pack_id(character_pack_id)
        payload = {
            "summary_id": f"summary::{uuid.uuid4()}",
            "profile_user_id": str(profile_user_id),
            "session_id": str(session_id),
            "character_pack_id": normalized_character_pack_id,
            "timestamp": int(timestamp),
            "date_label": str(date_label),
            "time_of_day": str(time_of_day),
            "period_label": str(period_label),
            "event_type": str(event_type),
            "importance": float(max(0.0, min(1.0, importance))),
            "diary_summary": str(diary_summary),
            "key_events_json": json.dumps(key_events, ensure_ascii=False),
            "core_facts_json": json.dumps(core_facts, ensure_ascii=False),
            "semantic_tags_json": json.dumps(semantic_tags, ensure_ascii=False),
            "memory_metadata_json": json.dumps(memory_metadata or {}, ensure_ascii=False),
            "source_start_seq": int(source_start_seq),
            "source_end_seq": int(source_end_seq),
            "source_ids_json": json.dumps(source_ids, ensure_ascii=False),
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_summaries (
                    summary_id, profile_user_id, session_id, character_pack_id, timestamp, date_label, time_of_day,
                    period_label, event_type, importance, diary_summary, key_events_json,
                    core_facts_json, semantic_tags_json, memory_metadata_json, is_semanticized, semantic_id,
                    source_start_seq, source_end_seq, source_ids_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["summary_id"],
                    payload["profile_user_id"],
                    payload["session_id"],
                    payload["character_pack_id"],
                    payload["timestamp"],
                    payload["date_label"],
                    payload["time_of_day"],
                    payload["period_label"],
                    payload["event_type"],
                    payload["importance"],
                    payload["diary_summary"],
                    payload["key_events_json"],
                    payload["core_facts_json"],
                    payload["semantic_tags_json"],
                    payload["memory_metadata_json"],
                    0,
                    "",
                    payload["source_start_seq"],
                    payload["source_end_seq"],
                    payload["source_ids_json"],
                ),
            )
        return self._row_to_summary(payload)



    def get_recent_summaries(
        self,
        profile_user_id: str,
        limit: int = 5,
        *,
        character_pack_id: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized_character_pack_id = (
            normalize_character_pack_id(character_pack_id) if character_pack_id is not None else None
        )
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM memory_summaries
                WHERE profile_user_id = ?
                  AND (? IS NULL OR character_pack_id = ?)
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (profile_user_id, normalized_character_pack_id, normalized_character_pack_id, int(limit)),
            ).fetchall()
        return [self._row_to_summary(dict(row)) for row in rows]



    def get_visible_episodic_summaries(
        self,
        profile_user_id: str,
        limit: int = 10,
        *,
        character_pack_id: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized_character_pack_id = (
            normalize_character_pack_id(character_pack_id) if character_pack_id is not None else None
        )
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM memory_summaries
                WHERE profile_user_id = ? AND is_semanticized = 0
                  AND (? IS NULL OR character_pack_id = ?)
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (profile_user_id, normalized_character_pack_id, normalized_character_pack_id, int(limit)),
            ).fetchall()
        return [self._row_to_summary(dict(row)) for row in rows]



    def get_unsemanticized_summary_count(
        self,
        session_id: str,
        *,
        character_pack_id: str | None = None,
    ) -> int:
        normalized_character_pack_id = (
            normalize_character_pack_id(character_pack_id) if character_pack_id is not None else None
        )
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM memory_summaries
                WHERE session_id = ? AND is_semanticized = 0
                  AND (? IS NULL OR character_pack_id = ?)
                """,
                (session_id, normalized_character_pack_id, normalized_character_pack_id),
            ).fetchone()
        return int(row["cnt"])



    def get_oldest_unsemanticized_summaries(
        self,
        session_id: str,
        limit: int = 5,
        *,
        character_pack_id: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized_character_pack_id = (
            normalize_character_pack_id(character_pack_id) if character_pack_id is not None else None
        )
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM memory_summaries
                WHERE session_id = ? AND is_semanticized = 0
                  AND (? IS NULL OR character_pack_id = ?)
                ORDER BY timestamp ASC
                LIMIT ?
                """,
                (session_id, normalized_character_pack_id, normalized_character_pack_id, int(limit)),
            ).fetchall()
        return [self._row_to_summary(dict(row)) for row in rows]



    def mark_summaries_semanticized(self, summary_ids: list[str], semantic_id: str) -> None:
        if not summary_ids:
            return
        placeholders = ",".join("?" for _ in summary_ids)
        with self._connect() as conn:
            conn.execute(
                f"""
                UPDATE memory_summaries
                SET is_semanticized = 1, semantic_id = ?
                WHERE summary_id IN ({placeholders})
                """,
                [str(semantic_id), *summary_ids],
            )



    def add_semantic_summary(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        timestamp: int,
        period_start_ts: int,
        period_end_ts: int,
        date_label: str,
        time_of_day: str,
        importance: float,
        semantic_summary: str,
        stable_facts: list[str],
        recurring_topics: list[str],
        important_people: list[str],
        open_loops: list[str],
        semantic_tags: list[str],
        source_summary_ids: list[str],
        memory_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_character_pack_id = normalize_character_pack_id(character_pack_id)
        payload = {
            "semantic_id": f"semantic::{uuid.uuid4()}",
            "profile_user_id": str(profile_user_id),
            "session_id": str(session_id),
            "character_pack_id": normalized_character_pack_id,
            "created_at": int(time.time()),
            "timestamp": int(timestamp),
            "period_start_ts": int(period_start_ts),
            "period_end_ts": int(period_end_ts),
            "date_label": str(date_label),
            "time_of_day": str(time_of_day),
            "importance": float(max(0.0, min(1.0, importance))),
            "semantic_summary": str(semantic_summary),
            "stable_facts_json": json.dumps(stable_facts, ensure_ascii=False),
            "recurring_topics_json": json.dumps(recurring_topics, ensure_ascii=False),
            "important_people_json": json.dumps(important_people, ensure_ascii=False),
            "open_loops_json": json.dumps(open_loops, ensure_ascii=False),
            "semantic_tags_json": json.dumps(semantic_tags, ensure_ascii=False),
            "memory_metadata_json": json.dumps(memory_metadata or {}, ensure_ascii=False),
            "source_summary_ids_json": json.dumps(source_summary_ids, ensure_ascii=False),
            "reinforcement_count": 1,
            "last_reinforced_ts": int(timestamp),
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_semantic_summaries (
                    semantic_id, profile_user_id, session_id, character_pack_id, created_at, timestamp,
                    period_start_ts, period_end_ts, date_label, time_of_day, importance,
                    semantic_summary, stable_facts_json, recurring_topics_json,
                    important_people_json, open_loops_json, semantic_tags_json, memory_metadata_json,
                    source_summary_ids_json, reinforcement_count, last_reinforced_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["semantic_id"],
                    payload["profile_user_id"],
                    payload["session_id"],
                    payload["character_pack_id"],
                    payload["created_at"],
                    payload["timestamp"],
                    payload["period_start_ts"],
                    payload["period_end_ts"],
                    payload["date_label"],
                    payload["time_of_day"],
                    payload["importance"],
                    payload["semantic_summary"],
                    payload["stable_facts_json"],
                    payload["recurring_topics_json"],
                    payload["important_people_json"],
                    payload["open_loops_json"],
                    payload["semantic_tags_json"],
                    payload["memory_metadata_json"],
                    payload["source_summary_ids_json"],
                    payload["reinforcement_count"],
                    payload["last_reinforced_ts"],
                ),
            )
        return self._row_to_semantic_summary(payload)



    def update_semantic_summary(
        self,
        *,
        semantic_id: str,
        timestamp: int,
        period_start_ts: int,
        period_end_ts: int,
        date_label: str,
        time_of_day: str,
        importance: float,
        semantic_summary: str,
        stable_facts: list[str],
        recurring_topics: list[str],
        important_people: list[str],
        open_loops: list[str],
        semantic_tags: list[str],
        source_summary_ids: list[str],
        reinforcement_count: int,
        last_reinforced_ts: int,
        memory_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        payload = {
            "semantic_id": str(semantic_id),
            "timestamp": int(timestamp),
            "period_start_ts": int(period_start_ts),
            "period_end_ts": int(period_end_ts),
            "date_label": str(date_label),
            "time_of_day": str(time_of_day),
            "importance": float(max(0.0, min(1.0, importance))),
            "semantic_summary": str(semantic_summary),
            "stable_facts_json": json.dumps(stable_facts, ensure_ascii=False),
            "recurring_topics_json": json.dumps(recurring_topics, ensure_ascii=False),
            "important_people_json": json.dumps(important_people, ensure_ascii=False),
            "open_loops_json": json.dumps(open_loops, ensure_ascii=False),
            "semantic_tags_json": json.dumps(semantic_tags, ensure_ascii=False),
            "memory_metadata_json": json.dumps(memory_metadata or {}, ensure_ascii=False),
            "source_summary_ids_json": json.dumps(source_summary_ids, ensure_ascii=False),
            "reinforcement_count": max(1, int(reinforcement_count)),
            "last_reinforced_ts": int(last_reinforced_ts),
        }
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE memory_semantic_summaries
                SET timestamp = ?, period_start_ts = ?, period_end_ts = ?, date_label = ?, time_of_day = ?,
                    importance = ?, semantic_summary = ?, stable_facts_json = ?, recurring_topics_json = ?,
                    important_people_json = ?, open_loops_json = ?, semantic_tags_json = ?,
                    memory_metadata_json = ?, source_summary_ids_json = ?, reinforcement_count = ?, last_reinforced_ts = ?
                WHERE semantic_id = ?
                """,
                (
                    payload["timestamp"],
                    payload["period_start_ts"],
                    payload["period_end_ts"],
                    payload["date_label"],
                    payload["time_of_day"],
                    payload["importance"],
                    payload["semantic_summary"],
                    payload["stable_facts_json"],
                    payload["recurring_topics_json"],
                    payload["important_people_json"],
                    payload["open_loops_json"],
                    payload["semantic_tags_json"],
                    payload["memory_metadata_json"],
                    payload["source_summary_ids_json"],
                    payload["reinforcement_count"],
                    payload["last_reinforced_ts"],
                    payload["semantic_id"],
                ),
            )
        return self.get_semantic_summary_by_id(payload["semantic_id"])



    def get_recent_semantic_summaries(
        self,
        profile_user_id: str,
        limit: int = 3,
        *,
        character_pack_id: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized_character_pack_id = (
            normalize_character_pack_id(character_pack_id) if character_pack_id is not None else None
        )
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM memory_semantic_summaries
                WHERE profile_user_id = ?
                  AND (? IS NULL OR character_pack_id = ?)
                ORDER BY last_reinforced_ts DESC, importance DESC, timestamp DESC
                LIMIT ?
                """,
                (profile_user_id, normalized_character_pack_id, normalized_character_pack_id, int(limit)),
            ).fetchall()
        return [self._row_to_semantic_summary(dict(row)) for row in rows]



    def count_vectorizable_records(self) -> int:
        with self._connect() as conn:
            chat_count = int(
                conn.execute("SELECT COUNT(*) FROM chat_messages WHERE index_in_vector = 1").fetchone()[0] or 0
            )
            summary_count = int(conn.execute("SELECT COUNT(*) FROM memory_summaries").fetchone()[0] or 0)
            semantic_count = int(conn.execute("SELECT COUNT(*) FROM memory_semantic_summaries").fetchone()[0] or 0)
        return chat_count + summary_count + semantic_count



    def iter_messages_for_vector_reindex(self, batch_size: int = 64):
        yield from self._iter_table_batches(
            table_name="chat_messages",
            converter=self._row_to_message,
            batch_size=batch_size,
            where_clause="index_in_vector = 1",
        )



    def iter_summaries_for_vector_reindex(self, batch_size: int = 64):
        yield from self._iter_table_batches(
            table_name="memory_summaries",
            converter=self._row_to_summary,
            batch_size=batch_size,
        )



    def iter_semantic_summaries_for_vector_reindex(self, batch_size: int = 64):
        yield from self._iter_table_batches(
            table_name="memory_semantic_summaries",
            converter=self._row_to_semantic_summary,
            batch_size=batch_size,
        )



    def get_summary_by_id(self, summary_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM memory_summaries WHERE summary_id = ?",
                (summary_id,),
            ).fetchone()
        return self._row_to_summary(dict(row)) if row else None



    def get_semantic_summary_by_id(self, semantic_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM memory_semantic_summaries WHERE semantic_id = ?",
                (semantic_id,),
            ).fetchone()
        return self._row_to_semantic_summary(dict(row)) if row else None
