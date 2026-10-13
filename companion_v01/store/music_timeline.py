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


class MusicTimelineStoreMixin:
    def upsert_desktop_music_timeline(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        source_id: str,
        source_kind: str = "",
        source_handle: str = "",
        title: str = "",
        status: str = "pending",
        segments: list[dict[str, Any]] | None = None,
        rolling_summary: str = "",
        ready_until_seconds: float = 0,
        transcript_generated_id: str = "",
        transcript_generated_handle: str = "",
        error_message: str = "",
        source_size: int = 0,
        source_mtime_ns: int = 0,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        normalized_source_id = str(source_id or "").strip()
        if not normalized_source_id:
            raise ValueError("source_id is required for desktop music timeline")
        effective_ts = int(timestamp or time.time())
        existing = self.get_desktop_music_timeline_by_source(
            profile_user_id=profile_user_id,
            session_id=session_id,
            source_id=normalized_source_id,
        )
        if existing is None:
            timeline_id = f"music_timeline::{uuid.uuid4()}"
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO desktop_music_timelines (
                        timeline_id, profile_user_id, session_id, source_id,
                        source_kind, source_handle, title, status, segments_json,
                        rolling_summary, ready_until_seconds, transcript_generated_id,
                        transcript_generated_handle, error_message, source_size,
                        source_mtime_ns, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        timeline_id,
                        str(profile_user_id),
                        str(session_id),
                        normalized_source_id,
                        str(source_kind or "").strip(),
                        str(source_handle or "").strip(),
                        str(title or "").strip(),
                        self._normalize_desktop_music_timeline_status(status),
                        json.dumps(segments if isinstance(segments, list) else [], ensure_ascii=False),
                        str(rolling_summary or "").strip(),
                        max(0.0, float(ready_until_seconds or 0)),
                        str(transcript_generated_id or "").strip(),
                        str(transcript_generated_handle or "").strip(),
                        str(error_message or "").strip(),
                        max(0, int(source_size or 0)),
                        max(0, int(source_mtime_ns or 0)),
                        effective_ts,
                        effective_ts,
                    ),
                )
            return self.get_desktop_music_timeline(timeline_id=timeline_id) or {}

        updated = self.update_desktop_music_timeline(
            profile_user_id=profile_user_id,
            session_id=session_id,
            timeline_id=str(existing.get("timeline_id") or ""),
            source_kind=source_kind,
            source_handle=source_handle,
            title=title,
            status=status,
            segments=segments,
            rolling_summary=rolling_summary,
            ready_until_seconds=ready_until_seconds,
            transcript_generated_id=transcript_generated_id,
            transcript_generated_handle=transcript_generated_handle,
            error_message=error_message,
            source_size=source_size,
            source_mtime_ns=source_mtime_ns,
            updated_at=effective_ts,
        )
        return updated or existing



    def update_desktop_music_timeline(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        timeline_id: str,
        source_kind: str | None = None,
        source_handle: str | None = None,
        title: str | None = None,
        status: str | None = None,
        segments: list[dict[str, Any]] | None = None,
        rolling_summary: str | None = None,
        ready_until_seconds: float | None = None,
        transcript_generated_id: str | None = None,
        transcript_generated_handle: str | None = None,
        error_message: str | None = None,
        source_size: int | None = None,
        source_mtime_ns: int | None = None,
        updated_at: int | None = None,
    ) -> dict[str, Any] | None:
        normalized_id = str(timeline_id or "").strip()
        if not normalized_id:
            return None
        fields: list[str] = []
        params: list[Any] = []
        if source_kind is not None:
            fields.append("source_kind = ?")
            params.append(str(source_kind or "").strip())
        if source_handle is not None:
            fields.append("source_handle = ?")
            params.append(str(source_handle or "").strip())
        if title is not None:
            fields.append("title = ?")
            params.append(str(title or "").strip())
        if status is not None:
            fields.append("status = ?")
            params.append(self._normalize_desktop_music_timeline_status(status))
        if segments is not None:
            fields.append("segments_json = ?")
            params.append(json.dumps(segments if isinstance(segments, list) else [], ensure_ascii=False))
        if rolling_summary is not None:
            fields.append("rolling_summary = ?")
            params.append(str(rolling_summary or "").strip())
        if ready_until_seconds is not None:
            fields.append("ready_until_seconds = ?")
            params.append(max(0.0, float(ready_until_seconds or 0)))
        if transcript_generated_id is not None:
            fields.append("transcript_generated_id = ?")
            params.append(str(transcript_generated_id or "").strip())
        if transcript_generated_handle is not None:
            fields.append("transcript_generated_handle = ?")
            params.append(str(transcript_generated_handle or "").strip())
        if error_message is not None:
            fields.append("error_message = ?")
            params.append(str(error_message or "").strip())
        if source_size is not None:
            fields.append("source_size = ?")
            params.append(max(0, int(source_size or 0)))
        if source_mtime_ns is not None:
            fields.append("source_mtime_ns = ?")
            params.append(max(0, int(source_mtime_ns or 0)))
        fields.append("updated_at = ?")
        params.append(int(updated_at or time.time()))
        params.extend([str(profile_user_id), str(session_id), normalized_id])

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM desktop_music_timelines
                WHERE profile_user_id = ? AND session_id = ? AND timeline_id = ?
                LIMIT 1
                """,
                (str(profile_user_id), str(session_id), normalized_id),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                f"""
                UPDATE desktop_music_timelines
                SET {", ".join(fields)}
                WHERE profile_user_id = ? AND session_id = ? AND timeline_id = ?
                """,
                tuple(params),
            )
            updated = conn.execute(
                """
                SELECT * FROM desktop_music_timelines
                WHERE profile_user_id = ? AND session_id = ? AND timeline_id = ?
                LIMIT 1
                """,
                (str(profile_user_id), str(session_id), normalized_id),
            ).fetchone()
        return self._row_to_desktop_music_timeline(dict(updated)) if updated else None



    def get_desktop_music_timeline(self, *, timeline_id: str) -> dict[str, Any] | None:
        normalized_id = str(timeline_id or "").strip()
        if not normalized_id:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM desktop_music_timelines WHERE timeline_id = ? LIMIT 1",
                (normalized_id,),
            ).fetchone()
        return self._row_to_desktop_music_timeline(dict(row)) if row else None



    def get_desktop_music_timeline_by_source(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        source_id: str,
    ) -> dict[str, Any] | None:
        normalized_source_id = str(source_id or "").strip()
        if not normalized_source_id:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM desktop_music_timelines
                WHERE profile_user_id = ? AND session_id = ? AND source_id = ?
                LIMIT 1
                """,
                (str(profile_user_id), str(session_id), normalized_source_id),
            ).fetchone()
        return self._row_to_desktop_music_timeline(dict(row)) if row else None
