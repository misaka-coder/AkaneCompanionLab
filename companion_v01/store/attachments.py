from __future__ import annotations

from ..owner_private_session import private_asset_scope

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


class AttachmentStoreMixin:
    @private_asset_scope
    def add_attachment_inbox_item(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        source: str = "",
        kind: str = "file",
        status: str = "pending_observation",
        origin_name: str = "",
        mime_type: str = "",
        file_ext: str = "",
        file_size: int = 0,
        storage_relpath: str = "",
        source_event_id: str = "",
        source_message_id: str = "",
        summary_title: str = "",
        short_hint: str = "",
        detail: dict[str, Any] | None = None,
        error_message: str = "",
        timestamp: int | None = None,
        expires_at: int = 0,
        attachment_id: str = "",
        attachment_handle: str = "",
    ) -> dict[str, Any]:
        effective_ts = int(timestamp or time.time())
        normalized_id = str(attachment_id or "").strip() or f"attachment::{uuid.uuid4()}"
        normalized_kind = self._normalize_attachment_kind(kind)
        payload = {
            "attachment_id": normalized_id,
            "profile_user_id": str(profile_user_id),
            "session_id": str(session_id),
            "source": str(source or "").strip(),
            "kind": normalized_kind,
            "attachment_handle": str(attachment_handle or "").strip(),
            "sequence_no": 0,
            "status": self._normalize_attachment_status(status),
            "origin_name": str(origin_name or "").strip(),
            "mime_type": str(mime_type or "").strip(),
            "file_ext": str(file_ext or "").strip(),
            "file_size": max(0, int(file_size or 0)),
            "storage_relpath": str(storage_relpath or "").strip(),
            "source_event_id": str(source_event_id or "").strip(),
            "source_message_id": str(source_message_id or "").strip(),
            "summary_title": str(summary_title or "").strip(),
            "short_hint": str(short_hint or "").strip(),
            "detail_json": json.dumps(detail if isinstance(detail, dict) else {}, ensure_ascii=False),
            "error_message": str(error_message or "").strip(),
            "created_at": effective_ts,
            "updated_at": effective_ts,
            "last_used_at": 0,
            "focus_rank": 0,
            "expires_at": max(0, int(expires_at or 0)),
        }
        auto_handle = not payload["attachment_handle"]
        max_attempts = 8 if auto_handle else 1
        next_sequence_no: int | None = None
        with self._attachment_inbox_write_lock:
            with self._connect() as conn:
                for _ in range(max_attempts):
                    if auto_handle:
                        if next_sequence_no is None:
                            next_sequence_no = self._next_attachment_sequence_no(
                                conn=conn,
                                profile_user_id=payload["profile_user_id"],
                                session_id=payload["session_id"],
                                kind=payload["kind"],
                            )
                        payload["sequence_no"] = next_sequence_no
                        payload["attachment_handle"] = (
                            f"{self._attachment_handle_prefix(payload['kind'])}_{next_sequence_no:03d}"
                        )
                    else:
                        payload["sequence_no"] = self._sequence_no_from_attachment_handle(payload["attachment_handle"])
                    try:
                        conn.execute(
                            """
                            INSERT INTO attachment_inbox_items (
                                attachment_id, profile_user_id, session_id, source, kind,
                                attachment_handle, sequence_no, status,
                                origin_name, mime_type, file_ext, file_size, storage_relpath,
                                source_event_id, source_message_id, summary_title, short_hint,
                                detail_json, error_message, created_at, updated_at, last_used_at,
                                focus_rank, expires_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                payload["attachment_id"],
                                payload["profile_user_id"],
                                payload["session_id"],
                                payload["source"],
                                payload["kind"],
                                payload["attachment_handle"],
                                payload["sequence_no"],
                                payload["status"],
                                payload["origin_name"],
                                payload["mime_type"],
                                payload["file_ext"],
                                payload["file_size"],
                                payload["storage_relpath"],
                                payload["source_event_id"],
                                payload["source_message_id"],
                                payload["summary_title"],
                                payload["short_hint"],
                                payload["detail_json"],
                                payload["error_message"],
                                payload["created_at"],
                                payload["updated_at"],
                                payload["last_used_at"],
                                payload["focus_rank"],
                                payload["expires_at"],
                            ),
                        )
                        break
                    except sqlite3.IntegrityError as exc:
                        if auto_handle and self._is_attachment_handle_conflict(exc):
                            next_sequence_no = int(payload["sequence_no"] or 0) + 1
                            continue
                        raise
                else:
                    raise RuntimeError("attachment handle allocation failed after retries")
        return self._row_to_attachment_inbox_item(payload)



    @private_asset_scope
    def set_attachment_private_source(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        attachment_id: str,
        source_kind: str,
        locator: dict[str, Any],
        expires_at: int,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        """Persist host-only attachment retrieval data outside public item rows."""

        normalized_id = str(attachment_id or "").strip()
        if not normalized_id:
            raise ValueError("attachment_id_required")
        effective_ts = int(timestamp or time.time())
        normalized_locator = dict(locator or {}) if isinstance(locator, dict) else {}
        with self._attachment_inbox_write_lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO attachment_inbox_private_sources (
                        attachment_id, profile_user_id, session_id, source_kind,
                        locator_json, created_at, updated_at, expires_at,
                        last_attempt_at, last_attempt_route, last_attempt_status, last_attempt_reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, '', '', '')
                    ON CONFLICT(attachment_id) DO UPDATE SET
                        profile_user_id = excluded.profile_user_id,
                        session_id = excluded.session_id,
                        source_kind = excluded.source_kind,
                        locator_json = excluded.locator_json,
                        updated_at = excluded.updated_at,
                        expires_at = excluded.expires_at
                    """,
                    (
                        normalized_id,
                        str(profile_user_id),
                        str(session_id),
                        str(source_kind or "").strip(),
                        json.dumps(normalized_locator, ensure_ascii=False),
                        effective_ts,
                        effective_ts,
                        max(0, int(expires_at or 0)),
                    ),
                )
        return self.get_attachment_private_source_status(
            profile_user_id=profile_user_id,
            session_id=session_id,
            attachment_id=normalized_id,
            now=effective_ts,
        )



    @private_asset_scope
    def get_attachment_private_source(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        attachment_id: str,
        now: int | None = None,
    ) -> dict[str, Any] | None:
        """Trusted-host accessor; callers must never log or project ``locator``."""

        normalized_id = str(attachment_id or "").strip()
        if not normalized_id:
            return None
        effective_ts = int(now or time.time())
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM attachment_inbox_private_sources
                WHERE profile_user_id = ? AND session_id = ? AND attachment_id = ?
                LIMIT 1
                """,
                (str(profile_user_id), str(session_id), normalized_id),
            ).fetchone()
        if row is None:
            return None
        raw = dict(row)
        expires_at = int(raw.get("expires_at", 0) or 0)
        locator = self._safe_json_loads(raw.get("locator_json"), fallback={})
        if not isinstance(locator, dict):
            locator = {}
        if expires_at > 0 and effective_ts >= expires_at:
            locator = {}
            with self._attachment_inbox_write_lock:
                with self._connect() as conn:
                    conn.execute(
                        """
                        UPDATE attachment_inbox_private_sources
                        SET locator_json = '{}', updated_at = ?
                        WHERE profile_user_id = ? AND session_id = ? AND attachment_id = ?
                        """,
                        (effective_ts, str(profile_user_id), str(session_id), normalized_id),
                    )
        return {
            "attachment_id": normalized_id,
            "profile_user_id": str(raw.get("profile_user_id") or ""),
            "session_id": str(raw.get("session_id") or ""),
            "source_kind": str(raw.get("source_kind") or ""),
            "locator": locator,
            "created_at": int(raw.get("created_at", 0) or 0),
            "updated_at": int(raw.get("updated_at", 0) or 0),
            "expires_at": expires_at,
            "last_attempt_at": int(raw.get("last_attempt_at", 0) or 0),
            "last_attempt_route": str(raw.get("last_attempt_route") or ""),
            "last_attempt_status": str(raw.get("last_attempt_status") or ""),
            "last_attempt_reason": str(raw.get("last_attempt_reason") or ""),
        }



    @private_asset_scope
    def get_attachment_private_source_status(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        attachment_id: str,
        now: int | None = None,
    ) -> dict[str, Any]:
        """Return safe availability metadata without locator values."""

        source = self.get_attachment_private_source(
            profile_user_id=profile_user_id,
            session_id=session_id,
            attachment_id=attachment_id,
            now=now,
        )
        if source is None:
            return {"locator_available": False, "source_kind": ""}
        return {
            "locator_available": bool(source.get("locator")),
            "source_kind": str(source.get("source_kind") or ""),
            "expires_at": int(source.get("expires_at") or 0),
            "last_attempt_at": int(source.get("last_attempt_at") or 0),
            "last_attempt_route": str(source.get("last_attempt_route") or ""),
            "last_attempt_status": str(source.get("last_attempt_status") or ""),
            "last_attempt_reason": str(source.get("last_attempt_reason") or ""),
        }



    @private_asset_scope
    def mark_attachment_private_source_attempt(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        attachment_id: str,
        route: str,
        status: str,
        reason: str = "",
        clear_locator: bool = False,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        effective_ts = int(timestamp or time.time())
        fields = [
            "last_attempt_at = ?",
            "last_attempt_route = ?",
            "last_attempt_status = ?",
            "last_attempt_reason = ?",
            "updated_at = ?",
        ]
        params: list[Any] = [
            effective_ts,
            str(route or "").strip()[:80],
            str(status or "").strip()[:40],
            str(reason or "").strip()[:160],
            effective_ts,
        ]
        if clear_locator:
            fields.append("locator_json = '{}'")
        params.extend([str(profile_user_id), str(session_id), str(attachment_id or "").strip()])
        with self._attachment_inbox_write_lock:
            with self._connect() as conn:
                conn.execute(
                    f"""
                    UPDATE attachment_inbox_private_sources
                    SET {", ".join(fields)}
                    WHERE profile_user_id = ? AND session_id = ? AND attachment_id = ?
                    """,
                    tuple(params),
                )
        return self.get_attachment_private_source_status(
            profile_user_id=profile_user_id,
            session_id=session_id,
            attachment_id=attachment_id,
            now=effective_ts,
        )



    def _next_attachment_sequence_no(
        self,
        *,
        conn: sqlite3.Connection,
        profile_user_id: str,
        session_id: str,
        kind: str,
    ) -> int:
        prefix = self._attachment_handle_prefix(kind)
        row = conn.execute(
            """
            SELECT COALESCE(MAX(sequence_no), 0) AS max_seq
            FROM attachment_inbox_items
            WHERE profile_user_id = ? AND session_id = ? AND attachment_handle LIKE ?
            """,
            (str(profile_user_id), str(session_id), f"{prefix}_%"),
        ).fetchone()
        return int(row["max_seq"] or 0) + 1 if row else 1



    def _is_attachment_handle_conflict(self, exc: sqlite3.IntegrityError) -> bool:
        message = str(exc or "").lower()
        return "attachment_inbox_items.profile_user_id" in message and "attachment_handle" in message



    @private_asset_scope
    def update_attachment_inbox_item(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        attachment_id: str,
        kind: str | None = None,
        status: str | None = None,
        summary_title: str | None = None,
        short_hint: str | None = None,
        detail: dict[str, Any] | None = None,
        error_message: str | None = None,
        mime_type: str | None = None,
        file_ext: str | None = None,
        file_size: int | None = None,
        storage_relpath: str | None = None,
        last_used_at: int | None = None,
        focus_rank: int | None = None,
        expires_at: int | None = None,
        updated_at: int | None = None,
    ) -> dict[str, Any] | None:
        normalized_id = str(attachment_id or "").strip()
        if not normalized_id:
            return None

        fields: list[str] = []
        params: list[Any] = []
        if kind is not None:
            # Keep the original stable attachment id/handle when bytes refine
            # an incoming file's media kind after materialization.
            fields.append("kind = ?")
            params.append(self._normalize_attachment_kind(kind))
        if status is not None:
            fields.append("status = ?")
            params.append(self._normalize_attachment_status(status))
        if summary_title is not None:
            fields.append("summary_title = ?")
            params.append(str(summary_title or "").strip())
        if short_hint is not None:
            fields.append("short_hint = ?")
            params.append(str(short_hint or "").strip())
        if detail is not None:
            fields.append("detail_json = ?")
            params.append(json.dumps(detail if isinstance(detail, dict) else {}, ensure_ascii=False))
        if error_message is not None:
            fields.append("error_message = ?")
            params.append(str(error_message or "").strip())
        if mime_type is not None:
            fields.append("mime_type = ?")
            params.append(str(mime_type or "").strip())
        if file_ext is not None:
            fields.append("file_ext = ?")
            params.append(str(file_ext or "").strip())
        if file_size is not None:
            fields.append("file_size = ?")
            params.append(max(0, int(file_size or 0)))
        if storage_relpath is not None:
            fields.append("storage_relpath = ?")
            params.append(str(storage_relpath or "").strip())
        if last_used_at is not None:
            fields.append("last_used_at = ?")
            params.append(max(0, int(last_used_at or 0)))
        if focus_rank is not None:
            fields.append("focus_rank = ?")
            params.append(max(0, int(focus_rank or 0)))
        if expires_at is not None:
            fields.append("expires_at = ?")
            params.append(max(0, int(expires_at or 0)))
        fields.append("updated_at = ?")
        params.append(int(updated_at or time.time()))
        params.extend([str(profile_user_id), str(session_id), normalized_id])

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM attachment_inbox_items
                WHERE profile_user_id = ? AND session_id = ? AND attachment_id = ?
                LIMIT 1
                """,
                (str(profile_user_id), str(session_id), normalized_id),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                f"""
                UPDATE attachment_inbox_items
                SET {", ".join(fields)}
                WHERE profile_user_id = ? AND session_id = ? AND attachment_id = ?
                """,
                tuple(params),
            )
            updated = conn.execute(
                """
                SELECT * FROM attachment_inbox_items
                WHERE profile_user_id = ? AND session_id = ? AND attachment_id = ?
                LIMIT 1
                """,
                (str(profile_user_id), str(session_id), normalized_id),
            ).fetchone()
        return self._row_to_attachment_inbox_item(dict(updated)) if updated else None



    @private_asset_scope
    def mark_attachment_inbox_item_ready(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        attachment_id: str,
        summary_title: str,
        short_hint: str,
        detail: dict[str, Any],
        focus_batch_seconds: int,
        focus_max_items: int,
        timestamp: int | None = None,
    ) -> dict[str, Any] | None:
        normalized_id = str(attachment_id or "").strip()
        if not normalized_id:
            return None
        effective_ts = int(timestamp or time.time())
        profile_id = str(profile_user_id)
        session = str(session_id)
        threshold = effective_ts - max(0, int(focus_batch_seconds or 0))
        with self._attachment_inbox_write_lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE attachment_inbox_items
                    SET status = 'ready', summary_title = ?, short_hint = ?,
                        detail_json = ?, error_message = '', updated_at = ?
                    WHERE profile_user_id = ? AND session_id = ? AND attachment_id = ?
                    """,
                    (
                        str(summary_title or "").strip(),
                        str(short_hint or "").strip(),
                        json.dumps(detail if isinstance(detail, dict) else {}, ensure_ascii=False),
                        effective_ts,
                        profile_id,
                        session,
                        normalized_id,
                    ),
                )
                candidates = conn.execute(
                    """
                    SELECT attachment_id
                    FROM (
                        SELECT attachment_id, created_at, sequence_no, updated_at
                        FROM attachment_inbox_items
                        WHERE profile_user_id = ? AND session_id = ? AND status = 'ready'
                          AND (created_at >= ? OR attachment_id = ?)
                        ORDER BY created_at DESC, sequence_no DESC, updated_at DESC
                        LIMIT ?
                    )
                    ORDER BY created_at ASC, sequence_no ASC, updated_at ASC
                    """,
                    (
                        profile_id,
                        session,
                        threshold,
                        normalized_id,
                        max(1, int(focus_max_items or 1)),
                    ),
                ).fetchall()
                attachment_ids = [
                    str(row["attachment_id"] or "").strip()
                    for row in candidates
                    if str(row["attachment_id"] or "").strip()
                ]
                conn.execute(
                    """
                    UPDATE attachment_inbox_items
                    SET focus_rank = 0
                    WHERE profile_user_id = ? AND session_id = ?
                      AND status IN ('ready', 'pending_observation', 'failed')
                    """,
                    (profile_id, session),
                )
                for rank, focused_id in enumerate(attachment_ids, start=1):
                    conn.execute(
                        """
                        UPDATE attachment_inbox_items
                        SET focus_rank = ?, last_used_at = ?, updated_at = ?
                        WHERE profile_user_id = ? AND session_id = ? AND attachment_id = ?
                          AND status = 'ready'
                        """,
                        (
                            rank,
                            effective_ts,
                            effective_ts,
                            profile_id,
                            session,
                            focused_id,
                        ),
                    )
                updated = conn.execute(
                    """
                    SELECT * FROM attachment_inbox_items
                    WHERE profile_user_id = ? AND session_id = ? AND attachment_id = ?
                    """,
                    (profile_id, session, normalized_id),
                ).fetchone()
        return self._row_to_attachment_inbox_item(dict(updated)) if updated else None



    @private_asset_scope
    def get_attachment_inbox_item(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        attachment_id: str,
    ) -> dict[str, Any] | None:
        normalized_id = str(attachment_id or "").strip()
        if not normalized_id:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM attachment_inbox_items
                WHERE profile_user_id = ? AND session_id = ? AND attachment_id = ?
                LIMIT 1
                """,
                (str(profile_user_id), str(session_id), normalized_id),
            ).fetchone()
        return self._row_to_attachment_inbox_item(dict(row)) if row else None



    @private_asset_scope
    def get_attachment_inbox_item_by_storage_relpath(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        storage_relpath: str,
        statuses: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> dict[str, Any] | None:
        normalized_relpath = str(storage_relpath or "").strip()
        if not normalized_relpath:
            return None
        normalized_statuses = self._normalize_attachment_status_list(statuses)
        query = [
            """
            SELECT * FROM attachment_inbox_items
            WHERE profile_user_id = ? AND session_id = ? AND storage_relpath = ?
            """
        ]
        params: list[Any] = [str(profile_user_id), str(session_id), normalized_relpath]
        if normalized_statuses:
            placeholders = ", ".join("?" for _ in normalized_statuses)
            query.append(f"AND status IN ({placeholders})")
            params.extend(normalized_statuses)
        query.append("ORDER BY updated_at DESC, created_at DESC LIMIT 1")
        with self._connect() as conn:
            row = conn.execute("\n".join(query), tuple(params)).fetchone()
        return self._row_to_attachment_inbox_item(dict(row)) if row else None



    @private_asset_scope
    def list_attachment_inbox_items(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        statuses: list[str] | tuple[str, ...] | set[str] | None = None,
        kind: str | None = None,
        limit: int | None = 20,
    ) -> list[dict[str, Any]]:
        normalized_statuses = self._normalize_attachment_status_list(statuses)
        query = [
            """
            SELECT * FROM attachment_inbox_items
            WHERE profile_user_id = ? AND session_id = ?
            """
        ]
        params: list[Any] = [str(profile_user_id), str(session_id)]
        if normalized_statuses:
            placeholders = ", ".join("?" for _ in normalized_statuses)
            query.append(f"AND status IN ({placeholders})")
            params.extend(normalized_statuses)
        raw_kind = str(kind or "").strip()
        normalized_kind = self._normalize_attachment_kind(raw_kind) if raw_kind else "any"
        if normalized_kind and normalized_kind != "any":
            query.append("AND kind = ?")
            params.append(normalized_kind)
        query.append(
            """
            ORDER BY
                CASE WHEN last_used_at > 0 THEN last_used_at ELSE updated_at END DESC,
                updated_at DESC,
                created_at DESC
            """
        )
        if limit is not None:
            query.append("LIMIT ?")
            params.append(max(1, int(limit or 20)))
        with self._connect() as conn:
            rows = conn.execute("\n".join(query), tuple(params)).fetchall()
        return [self._row_to_attachment_inbox_item(dict(row)) for row in rows]



    @private_asset_scope
    def find_attachment_inbox_item(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        query: str,
        kind: str | None = None,
        statuses: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> dict[str, Any] | None:
        matches = self.find_attachment_inbox_item_matches(
            profile_user_id=profile_user_id,
            session_id=session_id,
            query=query,
            kind=kind,
            statuses=statuses,
            limit=12,
        )
        if not matches:
            return None
        best_rank = int(matches[0].get("_match_rank") or 99)
        top_matches = [item for item in matches if int(item.get("_match_rank") or 99) == best_rank]
        if len(top_matches) > 1 and best_rank >= 2:
            return None
        return {key: value for key, value in matches[0].items() if key != "_match_rank"}



    @private_asset_scope
    def find_attachment_inbox_item_matches(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        query: str,
        kind: str | None = None,
        statuses: list[str] | tuple[str, ...] | set[str] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return []
        normalized_statuses = self._normalize_attachment_status_list(statuses)
        clauses = [
            "profile_user_id = ?",
            "session_id = ?",
        ]
        params: list[Any] = [str(profile_user_id), str(session_id)]
        if normalized_statuses:
            placeholders = ", ".join("?" for _ in normalized_statuses)
            clauses.append(f"status IN ({placeholders})")
            params.extend(normalized_statuses)
        raw_kind = str(kind or "").strip()
        normalized_kind = self._normalize_attachment_kind(raw_kind) if raw_kind else "any"
        if normalized_kind and normalized_kind != "any":
            clauses.append("kind = ?")
            params.append(normalized_kind)

        lowered_query = normalized_query.lower()
        like_query = f"%{lowered_query}%"
        clauses.append(
            """
            (
                attachment_id = ?
                OR LOWER(attachment_id) = ?
                OR attachment_handle = ?
                OR LOWER(attachment_handle) = ?
                OR LOWER(origin_name) = ?
                OR LOWER(summary_title) = ?
                OR LOWER(origin_name) LIKE ?
                OR LOWER(summary_title) LIKE ?
                OR LOWER(short_hint) LIKE ?
                OR CAST(sequence_no AS TEXT) = ?
            )
            """
        )
        params.extend(
            [
                normalized_query,
                lowered_query,
                normalized_query,
                lowered_query,
                lowered_query,
                lowered_query,
                like_query,
                like_query,
                like_query,
                normalized_query,
            ]
        )
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *,
                    CASE
                        WHEN attachment_id = ? OR LOWER(attachment_id) = ? THEN 0
                        WHEN attachment_handle = ? OR LOWER(attachment_handle) = ? THEN 0
                        WHEN CAST(sequence_no AS TEXT) = ? THEN 1
                        WHEN LOWER(summary_title) = ? OR LOWER(origin_name) = ? THEN 2
                        WHEN LOWER(summary_title) LIKE ? OR LOWER(origin_name) LIKE ? THEN 3
                        WHEN LOWER(short_hint) LIKE ? THEN 4
                        ELSE 5
                    END AS match_rank
                FROM attachment_inbox_items
                WHERE {" AND ".join(clauses)}
                ORDER BY
                    match_rank ASC,
                    updated_at DESC,
                    created_at DESC
                LIMIT ?
                """,
                tuple(
                    [
                        normalized_query,
                        lowered_query,
                        normalized_query,
                        lowered_query,
                        normalized_query,
                        lowered_query,
                        lowered_query,
                        like_query,
                        like_query,
                        like_query,
                    ]
                    + params
                    + [max(1, int(limit or 10))]
                ),
            ).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            raw = dict(row)
            match_rank = int(raw.pop("match_rank", 99) or 99)
            item = self._row_to_attachment_inbox_item(raw)
            item["_match_rank"] = match_rank
            output.append(item)
        return output



    @private_asset_scope
    def clear_attachment_inbox_items(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str = "current",
        kind: str | None = None,
        timestamp: int | None = None,
    ) -> list[dict[str, Any]]:
        normalized_target = str(target or "current").strip()
        effective_ts = int(timestamp or time.time())
        active_statuses = ["ready", "pending_observation", "failed"]
        if normalized_target.lower() in {"all", "全部", "*", "current", "当前", "工作台", "materials", "workspace"}:
            targets = self.list_attachment_inbox_items(
                profile_user_id=profile_user_id,
                session_id=session_id,
                statuses=active_statuses,
                kind=kind,
                limit=200,
            )
        elif normalized_target.lower() in {"latest", "最近", "最新"}:
            targets = self.list_attachment_inbox_items(
                profile_user_id=profile_user_id,
                session_id=session_id,
                statuses=active_statuses,
                kind=kind,
                limit=1,
            )
        else:
            found = self.find_attachment_inbox_item(
                profile_user_id=profile_user_id,
                session_id=session_id,
                query=normalized_target,
                kind=kind,
                statuses=active_statuses,
            )
            targets = [found] if found else []

        ids = [str(item.get("attachment_id") or "").strip() for item in targets if item]
        ids = [item_id for item_id in ids if item_id]
        if not ids:
            return []

        placeholders = ", ".join("?" for _ in ids)
        with self._connect() as conn:
            conn.execute(
                f"""
                UPDATE attachment_inbox_items
                SET status = 'cleared', updated_at = ?
                WHERE profile_user_id = ? AND session_id = ? AND attachment_id IN ({placeholders})
                """,
                (effective_ts, str(profile_user_id), str(session_id), *ids),
            )
            conn.execute(
                f"""
                DELETE FROM attachment_inbox_private_sources
                WHERE profile_user_id = ? AND session_id = ? AND attachment_id IN ({placeholders})
                """,
                (str(profile_user_id), str(session_id), *ids),
            )
        return [dict(item, status="cleared", updated_at=effective_ts) for item in targets if item]



    @private_asset_scope
    def sync_attachment_workspace_focus(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        attachment_ids: list[str],
        timestamp: int | None = None,
    ) -> list[dict[str, Any]]:
        effective_ts = int(timestamp or time.time())
        normalized_ids: list[str] = []
        for item_id in attachment_ids or []:
            normalized = str(item_id or "").strip()
            if normalized and normalized not in normalized_ids:
                normalized_ids.append(normalized)

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE attachment_inbox_items
                SET focus_rank = 0, updated_at = ?
                WHERE profile_user_id = ? AND session_id = ?
                  AND status IN ('ready', 'pending_observation', 'failed')
                """,
                (effective_ts, str(profile_user_id), str(session_id)),
            )
            for rank, attachment_id in enumerate(normalized_ids, start=1):
                conn.execute(
                    """
                    UPDATE attachment_inbox_items
                    SET focus_rank = ?, last_used_at = ?, updated_at = ?
                    WHERE profile_user_id = ? AND session_id = ? AND attachment_id = ?
                      AND status IN ('ready', 'pending_observation', 'failed')
                    """,
                    (
                        rank,
                        effective_ts,
                        effective_ts,
                        str(profile_user_id),
                        str(session_id),
                        attachment_id,
                    ),
                )
            if not normalized_ids:
                return []
            placeholders = ", ".join("?" for _ in normalized_ids)
            rows = conn.execute(
                f"""
                SELECT * FROM attachment_inbox_items
                WHERE profile_user_id = ? AND session_id = ?
                  AND attachment_id IN ({placeholders})
                  AND status IN ('ready', 'pending_observation', 'failed')
                ORDER BY focus_rank ASC, updated_at DESC
                """,
                (str(profile_user_id), str(session_id), *normalized_ids),
            ).fetchall()
        return [self._row_to_attachment_inbox_item(dict(row)) for row in rows]



    @private_asset_scope
    def list_workspace_file_states(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        focused_only: bool = False,
    ) -> list[dict[str, Any]]:
        query = """
            SELECT profile_user_id, session_id, workspace_uri, focus_rank,
                   created_at, updated_at, last_used_at
            FROM workspace_file_states
            WHERE profile_user_id = ? AND session_id = ?
        """
        params: list[Any] = [str(profile_user_id), str(session_id)]
        if focused_only:
            query += " AND focus_rank > 0"
        query += " ORDER BY CASE WHEN focus_rank > 0 THEN 0 ELSE 1 END, focus_rank ASC, updated_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]



    @private_asset_scope
    def update_workspace_file_focus(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        workspace_uris: list[str],
        action: str,
        timestamp: int | None = None,
    ) -> list[dict[str, Any]]:
        normalized_action = str(action or "").strip().lower()
        if normalized_action not in {"add", "set", "remove"}:
            raise ValueError(f"unsupported workspace focus action: {normalized_action}")

        normalized_uris: list[str] = []
        for workspace_uri in workspace_uris or []:
            normalized = str(workspace_uri or "").strip()
            if normalized and normalized not in normalized_uris:
                normalized_uris.append(normalized)

        effective_ts = int(timestamp or time.time())
        profile_id = str(profile_user_id)
        session = str(session_id)
        with self._workspace_file_write_lock:
            with self._connect() as conn:
                if normalized_action == "set":
                    conn.execute(
                        """
                        UPDATE workspace_file_states
                        SET focus_rank = 0, updated_at = ?
                        WHERE profile_user_id = ? AND session_id = ? AND focus_rank > 0
                        """,
                        (effective_ts, profile_id, session),
                    )

                if normalized_action in {"add", "set"}:
                    row = conn.execute(
                        """
                        SELECT COALESCE(MAX(focus_rank), 0) AS max_rank
                        FROM workspace_file_states
                        WHERE profile_user_id = ? AND session_id = ?
                        """,
                        (profile_id, session),
                    ).fetchone()
                    next_rank = int(row["max_rank"] or 0) + 1 if row else 1
                    for workspace_uri in normalized_uris:
                        existing = conn.execute(
                            """
                            SELECT focus_rank
                            FROM workspace_file_states
                            WHERE profile_user_id = ? AND session_id = ? AND workspace_uri = ?
                            """,
                            (profile_id, session, workspace_uri),
                        ).fetchone()
                        if normalized_action == "add" and existing and int(existing["focus_rank"] or 0) > 0:
                            conn.execute(
                                """
                                UPDATE workspace_file_states
                                SET last_used_at = ?, updated_at = ?
                                WHERE profile_user_id = ? AND session_id = ? AND workspace_uri = ?
                                """,
                                (effective_ts, effective_ts, profile_id, session, workspace_uri),
                            )
                            continue
                        conn.execute(
                            """
                            INSERT INTO workspace_file_states (
                                profile_user_id, session_id, workspace_uri, focus_rank,
                                created_at, updated_at, last_used_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(profile_user_id, session_id, workspace_uri)
                            DO UPDATE SET
                                focus_rank = excluded.focus_rank,
                                updated_at = excluded.updated_at,
                                last_used_at = excluded.last_used_at
                            """,
                            (
                                profile_id,
                                session,
                                workspace_uri,
                                next_rank,
                                effective_ts,
                                effective_ts,
                                effective_ts,
                            ),
                        )
                        next_rank += 1
                else:
                    for workspace_uri in normalized_uris:
                        conn.execute(
                            """
                            INSERT INTO workspace_file_states (
                                profile_user_id, session_id, workspace_uri, focus_rank,
                                created_at, updated_at, last_used_at
                            ) VALUES (?, ?, ?, 0, ?, ?, 0)
                            ON CONFLICT(profile_user_id, session_id, workspace_uri)
                            DO UPDATE SET focus_rank = 0, updated_at = excluded.updated_at
                            """,
                            (
                                profile_id,
                                session,
                                workspace_uri,
                                effective_ts,
                                effective_ts,
                            ),
                        )

        return self.list_workspace_file_states(
            profile_user_id=profile_id,
            session_id=session,
        )
