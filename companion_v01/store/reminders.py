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


class ReminderStoreMixin:
    def add_reminder(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        content: str,
        due_ts: int,
        raw_time_text: str = "",
    ) -> dict[str, Any]:
        reminder = {
            "reminder_id": f"reminder::{uuid.uuid4()}",
            "profile_user_id": str(profile_user_id),
            "session_id": str(session_id),
            "content": str(content),
            "due_ts": int(due_ts),
            "date_label": timestamp_to_date_label(due_ts),
            "time_of_day": infer_time_of_day(due_ts),
            "raw_time_text": str(raw_time_text or ""),
            "status": "pending",
            "created_at": int(time.time()),
            "fired_at": 0,
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO reminders (
                    reminder_id, profile_user_id, session_id, content, due_ts,
                    date_label, time_of_day, raw_time_text, status, created_at, fired_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reminder["reminder_id"],
                    reminder["profile_user_id"],
                    reminder["session_id"],
                    reminder["content"],
                    reminder["due_ts"],
                    reminder["date_label"],
                    reminder["time_of_day"],
                    reminder["raw_time_text"],
                    reminder["status"],
                    reminder["created_at"],
                    reminder["fired_at"],
                ),
            )
        return dict(reminder)



    def claim_due_reminders(
        self,
        *,
        profile_user_id: str,
        session_id: str | None = None,
        now_ts: int | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        effective_now_ts = int(now_ts or time.time())
        query = [
            "SELECT * FROM reminders",
            "WHERE profile_user_id = ?",
            "AND status = 'pending'",
            "AND due_ts <= ?",
        ]
        params: list[Any] = [str(profile_user_id), effective_now_ts]
        if session_id is not None:
            query.append("AND session_id = ?")
            params.append(str(session_id))
        query.append("ORDER BY due_ts ASC, created_at ASC LIMIT ?")
        params.append(max(1, int(limit)))

        with self._connect() as conn:
            rows = conn.execute("\n".join(query), tuple(params)).fetchall()
            reminder_ids = [str(row["reminder_id"]) for row in rows]
            if reminder_ids:
                placeholders = ",".join("?" for _ in reminder_ids)
                conn.execute(
                    f"""
                    UPDATE reminders
                    SET status = 'fired', fired_at = ?
                    WHERE reminder_id IN ({placeholders})
                    """,
                    [effective_now_ts, *reminder_ids],
                )
        return [self._row_to_reminder(dict(row), status="fired", fired_at=effective_now_ts) for row in rows]



    def list_reminders(
        self,
        *,
        profile_user_id: str,
        session_id: str | None = None,
        status: str = "pending",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        normalized_status = str(status or "pending").strip().lower() or "pending"
        query = [
            "SELECT * FROM reminders",
            "WHERE profile_user_id = ?",
            "AND status = ?",
        ]
        params: list[Any] = [str(profile_user_id), normalized_status]
        if session_id is not None:
            query.append("AND session_id = ?")
            params.append(str(session_id))
        query.append("ORDER BY due_ts ASC, created_at ASC LIMIT ?")
        params.append(max(1, int(limit)))

        with self._connect() as conn:
            rows = conn.execute("\n".join(query), tuple(params)).fetchall()
        return [self._row_to_reminder(dict(row)) for row in rows]



    def cancel_reminder(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        reminder_id: str,
        cancelled_at: int | None = None,
    ) -> dict[str, Any] | None:
        effective_cancelled_at = int(cancelled_at or time.time())
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM reminders
                WHERE reminder_id = ? AND profile_user_id = ? AND session_id = ? AND status = 'pending'
                LIMIT 1
                """,
                (
                    str(reminder_id),
                    str(profile_user_id),
                    str(session_id),
                ),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """
                UPDATE reminders
                SET status = 'cancelled', fired_at = ?
                WHERE reminder_id = ?
                """,
                (
                    effective_cancelled_at,
                    str(reminder_id),
                ),
            )
        return self._row_to_reminder(dict(row), status="cancelled", fired_at=effective_cancelled_at)
