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


class AssetStoreMixin:
    def add_gift_asset(
        self,
        *,
        asset_id: str,
        resource_id: str,
        profile_user_id: str,
        session_id: str,
        display_name: str,
        asset_type: str,
        origin_event_type: str,
        origin_source_id: str = "",
        source_ids: list[str] | None = None,
        payload: dict[str, Any] | None = None,
        media_kind: str = "",
        origin_name: str = "",
        mime_type: str = "",
        file_ext: str = "",
        file_size: int = 0,
        storage_relpath: str = "",
        status: str = "pending",
        timestamp: int | None = None,
        last_decision_at: int | None = None,
        last_touched_at: int | None = None,
        container_type: str = "",
        container_key: str = "",
        container_name: str = "",
        artifact_flags: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        effective_ts = int(timestamp or time.time())
        normalized_status = self._normalize_gift_status(status)
        normalized_source_ids = self._normalize_string_list(source_ids)
        normalized_origin_source_id = str(origin_source_id or "").strip()
        if normalized_origin_source_id and normalized_origin_source_id not in normalized_source_ids:
            normalized_source_ids.insert(0, normalized_origin_source_id)
        normalized_payload = payload if isinstance(payload, dict) else {}
        normalized_container = self._derive_container_fields(
            asset_type=asset_type,
            status=normalized_status,
            payload=normalized_payload,
            container_type=container_type,
            container_key=container_key,
            container_name=container_name,
            artifact_flags=artifact_flags,
        )
        payload = {
            "asset_id": str(asset_id),
            "resource_id": str(resource_id),
            "profile_user_id": str(profile_user_id),
            "session_id": str(session_id),
            "display_name": str(display_name),
            "asset_type": str(asset_type or "").strip(),
            "origin_event_type": str(origin_event_type or "").strip(),
            "origin_source_id": normalized_origin_source_id,
            "source_ids_json": json.dumps(normalized_source_ids, ensure_ascii=False),
            "payload_json": json.dumps(normalized_payload, ensure_ascii=False),
            "media_kind": str(media_kind or "").strip(),
            "origin_name": str(origin_name or "").strip(),
            "display_name": str(display_name),
            "mime_type": str(mime_type or ""),
            "file_ext": str(file_ext or ""),
            "file_size": max(0, int(file_size or 0)),
            "storage_relpath": str(storage_relpath),
            "status": normalized_status,
            "container_type": normalized_container["container_type"],
            "container_key": normalized_container["container_key"],
            "container_name": normalized_container["container_name"],
            "artifact_flags_json": json.dumps(normalized_container["artifact_flags"], ensure_ascii=False),
            "created_at": effective_ts,
            "updated_at": effective_ts,
            "last_decision_at": int(last_decision_at or 0),
            "last_touched_at": int(last_touched_at or effective_ts),
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_media_assets (
                    asset_id, resource_id, profile_user_id, session_id, media_kind,
                    origin_name, display_name, mime_type, file_ext, file_size,
                    storage_relpath, status, container_type, container_key, container_name, artifact_flags_json, created_at, updated_at,
                    asset_type, origin_event_type, origin_source_id, source_ids_json,
                    payload_json, last_decision_at, last_touched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["asset_id"],
                    payload["resource_id"],
                    payload["profile_user_id"],
                    payload["session_id"],
                    payload["media_kind"],
                    payload["origin_name"],
                    payload["display_name"],
                    payload["mime_type"],
                    payload["file_ext"],
                    payload["file_size"],
                    payload["storage_relpath"],
                    payload["status"],
                    payload["container_type"],
                    payload["container_key"],
                    payload["container_name"],
                    payload["artifact_flags_json"],
                    payload["created_at"],
                    payload["updated_at"],
                    payload["asset_type"],
                    payload["origin_event_type"],
                    payload["origin_source_id"],
                    payload["source_ids_json"],
                    payload["payload_json"],
                    payload["last_decision_at"],
                    payload["last_touched_at"],
                ),
            )
        return self.get_gift_asset(profile_user_id=payload["profile_user_id"], asset_id=payload["asset_id"]) or payload



    def add_user_media_asset(
        self,
        *,
        asset_id: str,
        resource_id: str,
        profile_user_id: str,
        session_id: str,
        media_kind: str,
        origin_name: str,
        display_name: str,
        mime_type: str,
        file_ext: str,
        file_size: int,
        storage_relpath: str,
        status: str = "pending",
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        payload = {
            "filename": str(origin_name or "").strip(),
            "mime_type": str(mime_type or "").strip(),
            "file_ext": str(file_ext or "").strip(),
            "file_size": max(0, int(file_size or 0)),
            "storage_relpath": str(storage_relpath or "").strip(),
        }
        return self.add_gift_asset(
            asset_id=asset_id,
            resource_id=resource_id,
            profile_user_id=profile_user_id,
            session_id=session_id,
            display_name=display_name,
            asset_type=self._infer_asset_type_from_media_kind(media_kind),
            origin_event_type="upload",
            payload=payload,
            media_kind=media_kind,
            origin_name=origin_name,
            mime_type=mime_type,
            file_ext=file_ext,
            file_size=file_size,
            storage_relpath=storage_relpath,
            status=status,
            timestamp=timestamp,
        )



    def get_gift_asset(
        self,
        *,
        profile_user_id: str,
        asset_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM user_media_assets
                WHERE profile_user_id = ? AND asset_id = ?
                LIMIT 1
                """,
                (str(profile_user_id), str(asset_id)),
            ).fetchone()
        return self._row_to_user_media_asset(dict(row)) if row else None



    def get_user_media_asset(
        self,
        *,
        profile_user_id: str,
        asset_id: str,
    ) -> dict[str, Any] | None:
        return self.get_gift_asset(profile_user_id=profile_user_id, asset_id=asset_id)



    def delete_gift_asset(
        self,
        *,
        profile_user_id: str,
        asset_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM user_media_assets
                WHERE profile_user_id = ? AND asset_id = ?
                LIMIT 1
                """,
                (str(profile_user_id), str(asset_id)),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """
                DELETE FROM user_media_assets
                WHERE profile_user_id = ? AND asset_id = ?
                """,
                (str(profile_user_id), str(asset_id)),
            )
        return self._row_to_user_media_asset(dict(row))



    def list_gift_assets(
        self,
        *,
        profile_user_id: str,
        session_id: str | None = None,
        asset_type: str | None = None,
        status: str | None = None,
        statuses: list[str] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        query = [
            """
            SELECT * FROM user_media_assets
            WHERE profile_user_id = ?
            """
        ]
        params: list[Any] = [str(profile_user_id)]
        normalized_session_id = str(session_id or "").strip()
        if normalized_session_id:
            query.append("AND session_id = ?")
            params.append(normalized_session_id)
        normalized_asset_type = str(asset_type or "").strip()
        if normalized_asset_type:
            query.append("AND asset_type = ?")
            params.append(normalized_asset_type)

        normalized_statuses = self._normalize_gift_status_list(statuses)
        normalized_status = self._normalize_gift_status(status) if status else ""
        if normalized_status and normalized_status not in normalized_statuses:
            normalized_statuses.append(normalized_status)
        if normalized_statuses:
            placeholders = ", ".join("?" for _ in normalized_statuses)
            query.append(f"AND status IN ({placeholders})")
            params.extend(normalized_statuses)

        query.append("ORDER BY updated_at DESC, created_at DESC")
        query.append("LIMIT ?")
        params.append(max(1, int(limit or 50)))
        with self._connect() as conn:
            rows = conn.execute("\n".join(query), tuple(params)).fetchall()
        return [self._row_to_user_media_asset(dict(row)) for row in rows]



    def list_user_media_assets(
        self,
        *,
        profile_user_id: str,
        media_kind: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return self.list_gift_assets(
            profile_user_id=profile_user_id,
            asset_type=self._infer_asset_type_from_media_kind(media_kind) if media_kind else None,
            status=status,
            limit=limit,
        )



    def count_gift_assets(
        self,
        *,
        profile_user_id: str,
        session_id: str | None = None,
        asset_type: str | None = None,
        statuses: list[str] | None = None,
    ) -> int:
        query = [
            """
            SELECT COUNT(*) AS cnt FROM user_media_assets
            WHERE profile_user_id = ?
            """
        ]
        params: list[Any] = [str(profile_user_id)]
        normalized_session_id = str(session_id or "").strip()
        if normalized_session_id:
            query.append("AND session_id = ?")
            params.append(normalized_session_id)
        normalized_asset_type = str(asset_type or "").strip()
        if normalized_asset_type:
            query.append("AND asset_type = ?")
            params.append(normalized_asset_type)
        normalized_statuses = self._normalize_gift_status_list(statuses)
        if normalized_statuses:
            placeholders = ", ".join("?" for _ in normalized_statuses)
            query.append(f"AND status IN ({placeholders})")
            params.extend(normalized_statuses)
        with self._connect() as conn:
            row = conn.execute("\n".join(query), tuple(params)).fetchone()
        return int(row["cnt"] or 0) if row else 0



    def list_artifacts_by_container(
        self,
        *,
        profile_user_id: str,
        container_type: str,
        container_key: str | None = None,
        statuses: list[str] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        normalized_container_type = self._normalize_container_type(container_type)
        if not normalized_container_type:
            return []

        query = [
            """
            SELECT * FROM user_media_assets
            WHERE profile_user_id = ? AND container_type = ?
            """
        ]
        params: list[Any] = [str(profile_user_id), normalized_container_type]

        normalized_container_key = str(container_key or "").strip()
        if normalized_container_key:
            query.append("AND container_key = ?")
            params.append(normalized_container_key)

        normalized_statuses = self._normalize_gift_status_list(statuses)
        if normalized_statuses:
            placeholders = ", ".join("?" for _ in normalized_statuses)
            query.append(f"AND status IN ({placeholders})")
            params.extend(normalized_statuses)

        query.append("ORDER BY updated_at DESC, created_at DESC")
        query.append("LIMIT ?")
        params.append(max(1, int(limit or 50)))
        with self._connect() as conn:
            rows = conn.execute("\n".join(query), tuple(params)).fetchall()
        return [self._row_to_user_media_asset(dict(row)) for row in rows]



    def count_artifacts_by_container(
        self,
        *,
        profile_user_id: str,
        container_type: str,
        container_key: str | None = None,
        statuses: list[str] | None = None,
    ) -> int:
        normalized_container_type = self._normalize_container_type(container_type)
        if not normalized_container_type:
            return 0

        query = [
            """
            SELECT COUNT(*) AS cnt FROM user_media_assets
            WHERE profile_user_id = ? AND container_type = ?
            """
        ]
        params: list[Any] = [str(profile_user_id), normalized_container_type]

        normalized_container_key = str(container_key or "").strip()
        if normalized_container_key:
            query.append("AND container_key = ?")
            params.append(normalized_container_key)

        normalized_statuses = self._normalize_gift_status_list(statuses)
        if normalized_statuses:
            placeholders = ", ".join("?" for _ in normalized_statuses)
            query.append(f"AND status IN ({placeholders})")
            params.extend(normalized_statuses)

        with self._connect() as conn:
            row = conn.execute("\n".join(query), tuple(params)).fetchone()
        return int(row["cnt"] or 0) if row else 0



    def list_artifact_groups_by_container(
        self,
        *,
        profile_user_id: str,
        container_type: str,
        statuses: list[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        normalized_container_type = self._normalize_container_type(container_type)
        if not normalized_container_type:
            return []

        query = [
            """
            SELECT
                container_key,
                container_name,
                COUNT(*) AS total_count,
                MAX(updated_at) AS latest_updated_at,
                MAX(created_at) AS latest_created_at
            FROM user_media_assets
            WHERE profile_user_id = ? AND container_type = ?
            """
        ]
        params: list[Any] = [str(profile_user_id), normalized_container_type]

        normalized_statuses = self._normalize_gift_status_list(statuses)
        if normalized_statuses:
            placeholders = ", ".join("?" for _ in normalized_statuses)
            query.append(f"AND status IN ({placeholders})")
            params.extend(normalized_statuses)

        query.extend(
            [
                "GROUP BY container_key, container_name",
                "ORDER BY latest_updated_at DESC, latest_created_at DESC, container_name ASC",
                "LIMIT ?",
            ]
        )
        params.append(max(1, int(limit or 100)))
        with self._connect() as conn:
            rows = conn.execute("\n".join(query), tuple(params)).fetchall()
        return [
            {
                "container_key": str(row["container_key"] or "").strip(),
                "container_name": str(row["container_name"] or "").strip(),
                "total_count": int(row["total_count"] or 0),
                "latest_updated_at": int(row["latest_updated_at"] or 0),
                "latest_created_at": int(row["latest_created_at"] or 0),
            }
            for row in rows
        ]



    def update_gift_asset(
        self,
        *,
        profile_user_id: str,
        asset_id: str,
        display_name: str | None = None,
        status: str | None = None,
        timestamp: int | None = None,
        source_ids: list[str] | None = None,
        payload: dict[str, Any] | None = None,
        last_decision_at: int | None = None,
        last_touched_at: int | None = None,
        container_type: str | None = None,
        container_key: str | None = None,
        container_name: str | None = None,
        artifact_flags: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        effective_ts = int(timestamp or time.time())
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM user_media_assets
                WHERE profile_user_id = ? AND asset_id = ?
                LIMIT 1
                """,
                (str(profile_user_id), str(asset_id)),
            ).fetchone()
            if row is None:
                return None
            current = self._row_to_user_media_asset(dict(row))
            next_display_name = (
                str(display_name).strip() if display_name is not None else str(current.get("display_name") or "")
            )
            next_status = self._normalize_gift_status(status) if status else current["status"]
            merged_source_ids = list(current["source_ids"])
            for source_id in self._normalize_string_list(source_ids):
                if source_id not in merged_source_ids:
                    merged_source_ids.append(source_id)
            next_source_ids = merged_source_ids
            next_payload = payload if isinstance(payload, dict) else dict(current["payload"])
            next_last_decision_at = (
                int(last_decision_at) if last_decision_at is not None else int(current.get("last_decision_at") or 0)
            )
            next_last_touched_at = int(last_touched_at) if last_touched_at is not None else effective_ts
            next_container = self._derive_container_fields(
                asset_type=current["asset_type"],
                status=next_status,
                payload=next_payload,
                container_type=current["container_type"] if container_type is None else container_type,
                container_key="" if container_key is None else container_key,
                container_name="" if container_name is None else container_name,
                artifact_flags=current["artifact_flags"] if artifact_flags is None else artifact_flags,
            )
            conn.execute(
                """
                UPDATE user_media_assets
                SET display_name = ?, status = ?, updated_at = ?, source_ids_json = ?, payload_json = ?,
                    last_decision_at = ?, last_touched_at = ?, container_type = ?, container_key = ?,
                    container_name = ?, artifact_flags_json = ?
                WHERE profile_user_id = ? AND asset_id = ?
                """,
                (
                    next_display_name,
                    next_status,
                    effective_ts,
                    json.dumps(next_source_ids, ensure_ascii=False),
                    json.dumps(next_payload, ensure_ascii=False),
                    next_last_decision_at,
                    next_last_touched_at,
                    next_container["container_type"],
                    next_container["container_key"],
                    next_container["container_name"],
                    json.dumps(next_container["artifact_flags"], ensure_ascii=False),
                    str(profile_user_id),
                    str(asset_id),
                ),
            )
        return self._row_to_user_media_asset(
            {
                **dict(row),
                "display_name": next_display_name,
                "status": next_status,
                "updated_at": effective_ts,
                "source_ids_json": json.dumps(next_source_ids, ensure_ascii=False),
                "payload_json": json.dumps(next_payload, ensure_ascii=False),
                "last_decision_at": next_last_decision_at,
                "last_touched_at": next_last_touched_at,
                "container_type": next_container["container_type"],
                "container_key": next_container["container_key"],
                "container_name": next_container["container_name"],
                "artifact_flags_json": json.dumps(next_container["artifact_flags"], ensure_ascii=False),
            }
        )



    def update_user_media_asset_status(
        self,
        *,
        profile_user_id: str,
        asset_id: str,
        status: str,
        timestamp: int | None = None,
    ) -> dict[str, Any] | None:
        return self.update_gift_asset(
            profile_user_id=profile_user_id,
            asset_id=asset_id,
            status=status,
            timestamp=timestamp,
            last_decision_at=int(timestamp or time.time()),
            last_touched_at=int(timestamp or time.time()),
        )
