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


class GeneratedFileStoreMixin:
    @private_asset_scope
    def add_generated_file(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        output_title: str,
        output_format: str,
        storage_relpath: str,
        mime_type: str = "",
        file_ext: str = "",
        file_size: int = 0,
        source_ids: list[str] | tuple[str, ...] | set[str] | None = None,
        content_card: dict[str, Any] | None = None,
        summary: str = "",
        created_by_tool: str = "",
        version_of_generated_id: str = "",
        version_no: int = 1,
        status: str = "ready",
        delivery_status: str = "pending",
        timestamp: int | None = None,
        generated_id: str = "",
        generated_handle: str = "",
    ) -> dict[str, Any]:
        effective_ts = int(timestamp or time.time())
        normalized_id = str(generated_id or "").strip() or f"generated::{uuid.uuid4()}"
        normalized_format = self._normalize_generated_format(output_format or file_ext)
        normalized_ext = str(file_ext or "").strip().lower().lstrip(".")
        if not normalized_ext:
            normalized_ext = normalized_format
        payload = {
            "generated_id": normalized_id,
            "profile_user_id": str(profile_user_id),
            "session_id": str(session_id),
            "generated_handle": str(generated_handle or "").strip(),
            "sequence_no": 0,
            "status": self._normalize_generated_status(status),
            "output_title": str(output_title or "").strip(),
            "output_format": normalized_format,
            "mime_type": str(mime_type or "").strip(),
            "file_ext": normalized_ext,
            "file_size": max(0, int(file_size or 0)),
            "storage_relpath": str(storage_relpath or "").strip(),
            "source_ids_json": json.dumps(self._normalize_string_list(source_ids), ensure_ascii=False),
            "content_card_json": json.dumps(content_card if isinstance(content_card, dict) else {}, ensure_ascii=False),
            "summary": str(summary or "").strip(),
            "created_by_tool": str(created_by_tool or "").strip(),
            "version_of_generated_id": str(version_of_generated_id or "").strip(),
            "version_no": max(1, int(version_no or 1)),
            "delivery_status": str(delivery_status or "pending").strip().lower()[:32] or "pending",
            "created_at": effective_ts,
            "updated_at": effective_ts,
            "last_used_at": 0,
        }
        with self._connect() as conn:
            if not payload["generated_handle"]:
                row = conn.execute(
                    """
                    SELECT COALESCE(MAX(sequence_no), 0) AS max_seq
                    FROM generated_files
                    WHERE profile_user_id = ? AND session_id = ?
                    """,
                    (payload["profile_user_id"], payload["session_id"]),
                ).fetchone()
                sequence_no = int(row["max_seq"] or 0) + 1 if row else 1
                payload["sequence_no"] = sequence_no
                payload["generated_handle"] = f"gen_{sequence_no:03d}"
            else:
                payload["sequence_no"] = self._sequence_no_from_generated_handle(payload["generated_handle"])
            conn.execute(
                """
                INSERT INTO generated_files (
                    generated_id, profile_user_id, session_id, generated_handle, sequence_no,
                    status, output_title, output_format, mime_type, file_ext, file_size,
                    storage_relpath, source_ids_json, content_card_json, summary,
                    created_by_tool, version_of_generated_id, version_no, delivery_status,
                    created_at, updated_at, last_used_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["generated_id"],
                    payload["profile_user_id"],
                    payload["session_id"],
                    payload["generated_handle"],
                    payload["sequence_no"],
                    payload["status"],
                    payload["output_title"],
                    payload["output_format"],
                    payload["mime_type"],
                    payload["file_ext"],
                    payload["file_size"],
                    payload["storage_relpath"],
                    payload["source_ids_json"],
                    payload["content_card_json"],
                    payload["summary"],
                    payload["created_by_tool"],
                    payload["version_of_generated_id"],
                    payload["version_no"],
                    payload["delivery_status"],
                    payload["created_at"],
                    payload["updated_at"],
                    payload["last_used_at"],
                ),
            )
        return self._row_to_generated_file(payload)



    @private_asset_scope
    def update_generated_file(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        generated_id: str,
        status: str | None = None,
        delivery_status: str | None = None,
        summary: str | None = None,
        content_card: dict[str, Any] | None = None,
        storage_relpath: str | None = None,
        file_size: int | None = None,
        last_used_at: int | None = None,
        updated_at: int | None = None,
    ) -> dict[str, Any] | None:
        normalized_id = str(generated_id or "").strip()
        if not normalized_id:
            return None
        fields: list[str] = []
        params: list[Any] = []
        if status is not None:
            fields.append("status = ?")
            params.append(self._normalize_generated_status(status))
        if delivery_status is not None:
            fields.append("delivery_status = ?")
            params.append(str(delivery_status or "").strip().lower()[:32] or "pending")
        if summary is not None:
            fields.append("summary = ?")
            params.append(str(summary or "").strip())
        if content_card is not None:
            fields.append("content_card_json = ?")
            params.append(json.dumps(content_card if isinstance(content_card, dict) else {}, ensure_ascii=False))
        if storage_relpath is not None:
            fields.append("storage_relpath = ?")
            params.append(str(storage_relpath or "").strip())
        if file_size is not None:
            fields.append("file_size = ?")
            params.append(max(0, int(file_size or 0)))
        if last_used_at is not None:
            fields.append("last_used_at = ?")
            params.append(max(0, int(last_used_at or 0)))
        fields.append("updated_at = ?")
        params.append(int(updated_at or time.time()))
        params.extend([str(profile_user_id), str(session_id), normalized_id])
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM generated_files
                WHERE profile_user_id = ? AND session_id = ? AND generated_id = ?
                LIMIT 1
                """,
                (str(profile_user_id), str(session_id), normalized_id),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                f"""
                UPDATE generated_files
                SET {", ".join(fields)}
                WHERE profile_user_id = ? AND session_id = ? AND generated_id = ?
                """,
                tuple(params),
            )
            updated = conn.execute(
                """
                SELECT * FROM generated_files
                WHERE profile_user_id = ? AND session_id = ? AND generated_id = ?
                LIMIT 1
                """,
                (str(profile_user_id), str(session_id), normalized_id),
            ).fetchone()
        return self._row_to_generated_file(dict(updated)) if updated else None



    @private_asset_scope
    def get_generated_file(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        generated_id: str,
    ) -> dict[str, Any] | None:
        normalized_id = str(generated_id or "").strip()
        if not normalized_id:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM generated_files
                WHERE profile_user_id = ? AND session_id = ? AND generated_id = ?
                LIMIT 1
                """,
                (str(profile_user_id), str(session_id), normalized_id),
            ).fetchone()
        return self._row_to_generated_file(dict(row)) if row else None



    @private_asset_scope
    def list_generated_files(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        statuses: list[str] | tuple[str, ...] | set[str] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        normalized_statuses = self._normalize_generated_status_list(statuses)
        query = [
            """
            SELECT * FROM generated_files
            WHERE profile_user_id = ? AND session_id = ?
            """
        ]
        params: list[Any] = [str(profile_user_id), str(session_id)]
        if normalized_statuses:
            placeholders = ", ".join("?" for _ in normalized_statuses)
            query.append(f"AND status IN ({placeholders})")
            params.extend(normalized_statuses)
        query.append(
            """
            ORDER BY
                CASE WHEN last_used_at > 0 THEN last_used_at ELSE updated_at END DESC,
                updated_at DESC,
                created_at DESC
            LIMIT ?
            """
        )
        params.append(max(1, int(limit or 10)))
        with self._connect() as conn:
            rows = conn.execute("\n".join(query), tuple(params)).fetchall()
        return [self._row_to_generated_file(dict(row)) for row in rows]



    @private_asset_scope
    def find_generated_file(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        query: str,
        statuses: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> dict[str, Any] | None:
        matches = self.find_generated_file_matches(
            profile_user_id=profile_user_id,
            session_id=session_id,
            query=query,
            statuses=statuses,
            limit=12,
        )
        if not matches:
            return None
        best_rank = int(matches[0].get("_match_rank") or 99)
        if best_rank <= 2:
            return {key: value for key, value in matches[0].items() if key != "_match_rank"}
        top_matches = [item for item in matches if int(item.get("_match_rank") or 99) == best_rank]
        if len(top_matches) > 1:
            return None
        return {key: value for key, value in matches[0].items() if key != "_match_rank"}



    @private_asset_scope
    def find_generated_file_matches(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        query: str,
        statuses: list[str] | tuple[str, ...] | set[str] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return []
        normalized_statuses = self._normalize_generated_status_list(statuses)
        clauses = ["profile_user_id = ?", "session_id = ?"]
        params: list[Any] = [str(profile_user_id), str(session_id)]
        if normalized_statuses:
            placeholders = ", ".join("?" for _ in normalized_statuses)
            clauses.append(f"status IN ({placeholders})")
            params.extend(normalized_statuses)
        lowered_query = normalized_query.lower()
        like_query = f"%{lowered_query}%"
        clauses.append(
            """
            (
                generated_id = ?
                OR LOWER(generated_id) = ?
                OR generated_handle = ?
                OR LOWER(generated_handle) = ?
                OR LOWER(output_title) = ?
                OR LOWER(output_title) LIKE ?
                OR LOWER(summary) LIKE ?
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
                        WHEN generated_id = ? OR LOWER(generated_id) = ? THEN 0
                        WHEN generated_handle = ? OR LOWER(generated_handle) = ? THEN 0
                        WHEN CAST(sequence_no AS TEXT) = ? THEN 1
                        WHEN LOWER(output_title) = ? THEN 2
                        WHEN LOWER(output_title) LIKE ? THEN 3
                        WHEN LOWER(summary) LIKE ? THEN 4
                        ELSE 5
                    END AS match_rank
                FROM generated_files
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
            item = self._row_to_generated_file(raw)
            item["_match_rank"] = match_rank
            output.append(item)
        return output
