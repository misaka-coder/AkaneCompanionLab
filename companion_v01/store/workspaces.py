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


class WorkspaceStoreMixin:
    def add_project_workspace(
        self,
        *,
        workspace_id: str,
        display_name: str,
        owner_kind: str,
        owner_id: str,
        actor_scope: str,
        root_relpath: str,
        root_kind: str = "managed",
        host_root_path: str = "",
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        now_ts = int(timestamp or time.time())
        payload = {
            "workspace_id": str(workspace_id),
            "display_name": str(display_name),
            "owner_kind": str(owner_kind),
            "owner_id": str(owner_id),
            "actor_scope": str(actor_scope),
            "state": "active",
            "root_relpath": str(root_relpath),
            "root_kind": str(root_kind),
            "host_root_path": str(host_root_path),
            "created_at": now_ts,
            "updated_at": now_ts,
            "archived_at": 0,
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO project_workspaces (
                    workspace_id, display_name, owner_kind, owner_id, actor_scope,
                    state, root_relpath, root_kind, host_root_path,
                    created_at, updated_at, archived_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(payload[key] for key in (
                    "workspace_id", "display_name", "owner_kind", "owner_id", "actor_scope",
                    "state", "root_relpath", "root_kind", "host_root_path",
                    "created_at", "updated_at", "archived_at",
                )),
            )
        return payload



    def get_project_workspace(self, workspace_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM project_workspaces WHERE workspace_id = ? LIMIT 1",
                (str(workspace_id or "").strip(),),
            ).fetchone()
        return dict(row) if row else None



    def list_project_workspaces(
        self,
        *,
        owner_kind: str,
        owner_id: str,
        actor_scope: str,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        query = (
            "SELECT * FROM project_workspaces "
            "WHERE owner_kind = ? AND owner_id = ? AND actor_scope = ?"
        )
        params: list[Any] = [str(owner_kind), str(owner_id), str(actor_scope)]
        if not include_archived:
            query += " AND state = 'active'"
        query += " ORDER BY updated_at DESC, created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [dict(row) for row in rows]



    def reassign_project_workspaces(
        self,
        *,
        from_owner_kind: str,
        from_owner_id: str,
        from_actor_scope: str,
        to_owner_kind: str,
        to_owner_id: str,
        to_actor_scope: str,
        timestamp: int | None = None,
    ) -> int:
        """Move one exact legacy catalog to a new ownership identity."""

        now_ts = int(timestamp or time.time())
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE project_workspaces
                SET owner_kind = ?, owner_id = ?, actor_scope = ?, updated_at = ?
                WHERE owner_kind = ? AND owner_id = ? AND actor_scope = ?
                """,
                (
                    str(to_owner_kind),
                    str(to_owner_id),
                    str(to_actor_scope),
                    now_ts,
                    str(from_owner_kind),
                    str(from_owner_id),
                    str(from_actor_scope),
                ),
            )
            return max(0, int(cursor.rowcount or 0))



    def update_project_workspace_state(
        self,
        *,
        workspace_id: str,
        state: str,
        timestamp: int | None = None,
    ) -> dict[str, Any] | None:
        clean_state = str(state or "").strip()
        if clean_state not in {"active", "archived"}:
            raise ValueError("invalid_project_workspace_state")
        now_ts = int(timestamp or time.time())
        archived_at = now_ts if clean_state == "archived" else 0
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE project_workspaces
                SET state = ?, updated_at = ?, archived_at = ?
                WHERE workspace_id = ?
                """,
                (clean_state, now_ts, archived_at, str(workspace_id or "").strip()),
            )
            row = conn.execute(
                "SELECT * FROM project_workspaces WHERE workspace_id = ? LIMIT 1",
                (str(workspace_id or "").strip(),),
            ).fetchone()
        return dict(row) if row else None



    def set_project_workspace_selection(
        self,
        *,
        selection_key: str,
        workspace_id: str,
        timestamp: int | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO project_workspace_selections (selection_key, workspace_id, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(selection_key) DO UPDATE SET
                    workspace_id = excluded.workspace_id,
                    updated_at = excluded.updated_at
                """,
                (str(selection_key), str(workspace_id), int(timestamp or time.time())),
            )



    def get_project_workspace_selection(self, *, selection_key: str) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT workspace_id FROM project_workspace_selections WHERE selection_key = ? LIMIT 1",
                (str(selection_key),),
            ).fetchone()
        return str(row["workspace_id"] or "") if row else ""



    def clear_project_workspace_selection(
        self,
        *,
        selection_key: str,
        workspace_id: str = "",
    ) -> None:
        with self._connect() as conn:
            if workspace_id:
                conn.execute(
                    "DELETE FROM project_workspace_selections WHERE selection_key = ? AND workspace_id = ?",
                    (str(selection_key), str(workspace_id)),
                )
            else:
                conn.execute(
                    "DELETE FROM project_workspace_selections WHERE selection_key = ?",
                    (str(selection_key),),
                )



    def add_task_workspace(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        owner: str = "Akane",
        status: str = "queued",
        raw_request: dict[str, Any] | None = None,
        normalized_goal: str = "",
        success_criteria: list[Any] | tuple[Any, ...] | None = None,
        constraints: list[Any] | tuple[Any, ...] | None = None,
        steps: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
        artifacts: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
        pending_question: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        timestamp: int | None = None,
        task_id: str = "",
    ) -> dict[str, Any]:
        effective_ts = int(timestamp or time.time())
        payload = {
            "task_id": str(task_id or "").strip() or f"task::{uuid.uuid4()}",
            "profile_user_id": str(profile_user_id),
            "session_id": str(session_id),
            "owner": str(owner or "Akane").strip()[:64] or "Akane",
            "status": self._normalize_task_workspace_status(status),
            "raw_request_json": json.dumps(raw_request if isinstance(raw_request, dict) else {}, ensure_ascii=False),
            "normalized_goal": str(normalized_goal or "").strip(),
            "success_criteria_json": json.dumps(list(success_criteria or []), ensure_ascii=False),
            "constraints_json": json.dumps(list(constraints or []), ensure_ascii=False),
            "steps_json": json.dumps(list(steps or []), ensure_ascii=False),
            "artifacts_json": json.dumps(list(artifacts or []), ensure_ascii=False),
            "pending_question_json": json.dumps(
                pending_question if isinstance(pending_question, dict) else {},
                ensure_ascii=False,
            ),
            "metadata_json": json.dumps(metadata if isinstance(metadata, dict) else {}, ensure_ascii=False),
            "created_at": effective_ts,
            "updated_at": effective_ts,
            "completed_at": 0,
            "cleaned_at": 0,
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO task_workspaces (
                    task_id, profile_user_id, session_id, owner, status,
                    raw_request_json, normalized_goal, success_criteria_json, constraints_json,
                    steps_json, artifacts_json, pending_question_json, metadata_json,
                    created_at, updated_at, completed_at, cleaned_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["task_id"],
                    payload["profile_user_id"],
                    payload["session_id"],
                    payload["owner"],
                    payload["status"],
                    payload["raw_request_json"],
                    payload["normalized_goal"],
                    payload["success_criteria_json"],
                    payload["constraints_json"],
                    payload["steps_json"],
                    payload["artifacts_json"],
                    payload["pending_question_json"],
                    payload["metadata_json"],
                    payload["created_at"],
                    payload["updated_at"],
                    payload["completed_at"],
                    payload["cleaned_at"],
                ),
            )
        return self._row_to_task_workspace(payload)



    def get_task_workspace(self, task_id: str) -> dict[str, Any] | None:
        normalized_id = str(task_id or "").strip()
        if not normalized_id:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM task_workspaces
                WHERE task_id = ?
                LIMIT 1
                """,
                (normalized_id,),
            ).fetchone()
        return self._row_to_task_workspace(dict(row)) if row else None



    def list_task_workspaces(
        self,
        *,
        profile_user_id: str,
        session_id: str | None = None,
        statuses: list[str] | tuple[str, ...] | set[str] | None = None,
        limit: int | None = 20,
    ) -> list[dict[str, Any]]:
        query = [
            """
            SELECT * FROM task_workspaces
            WHERE profile_user_id = ?
            """
        ]
        params: list[Any] = [str(profile_user_id)]
        normalized_session_id = str(session_id or "").strip()
        if normalized_session_id:
            query.append("AND session_id = ?")
            params.append(normalized_session_id)
        normalized_statuses = self._normalize_task_workspace_status_list(statuses)
        if normalized_statuses:
            placeholders = ", ".join("?" for _ in normalized_statuses)
            query.append(f"AND status IN ({placeholders})")
            params.extend(normalized_statuses)
        query.append("ORDER BY updated_at DESC, created_at DESC")
        if limit is not None:
            query.append("LIMIT ?")
            params.append(max(1, int(limit or 20)))
        with self._connect() as conn:
            rows = conn.execute("\n".join(query), tuple(params)).fetchall()
        return [self._row_to_task_workspace(dict(row)) for row in rows]



    def update_task_workspace(
        self,
        *,
        task_id: str,
        status: str | None = None,
        owner: str | None = None,
        raw_request: dict[str, Any] | None = None,
        normalized_goal: str | None = None,
        success_criteria: list[Any] | tuple[Any, ...] | None = None,
        constraints: list[Any] | tuple[Any, ...] | None = None,
        steps: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
        artifacts: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
        pending_question: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        completed_at: int | None = None,
        cleaned_at: int | None = None,
        updated_at: int | None = None,
    ) -> dict[str, Any] | None:
        normalized_id = str(task_id or "").strip()
        if not normalized_id:
            return None
        fields: list[str] = []
        params: list[Any] = []
        if status is not None:
            fields.append("status = ?")
            params.append(self._normalize_task_workspace_status(status))
        if owner is not None:
            fields.append("owner = ?")
            params.append(str(owner or "Akane").strip()[:64] or "Akane")
        if raw_request is not None:
            fields.append("raw_request_json = ?")
            params.append(json.dumps(raw_request if isinstance(raw_request, dict) else {}, ensure_ascii=False))
        if normalized_goal is not None:
            fields.append("normalized_goal = ?")
            params.append(str(normalized_goal or "").strip())
        if success_criteria is not None:
            fields.append("success_criteria_json = ?")
            params.append(json.dumps(list(success_criteria or []), ensure_ascii=False))
        if constraints is not None:
            fields.append("constraints_json = ?")
            params.append(json.dumps(list(constraints or []), ensure_ascii=False))
        if steps is not None:
            fields.append("steps_json = ?")
            params.append(json.dumps(list(steps or []), ensure_ascii=False))
        if artifacts is not None:
            fields.append("artifacts_json = ?")
            params.append(json.dumps(list(artifacts or []), ensure_ascii=False))
        if pending_question is not None:
            fields.append("pending_question_json = ?")
            params.append(
                json.dumps(pending_question if isinstance(pending_question, dict) else {}, ensure_ascii=False)
            )
        if metadata is not None:
            fields.append("metadata_json = ?")
            params.append(json.dumps(metadata if isinstance(metadata, dict) else {}, ensure_ascii=False))
        if completed_at is not None:
            fields.append("completed_at = ?")
            params.append(max(0, int(completed_at or 0)))
        if cleaned_at is not None:
            fields.append("cleaned_at = ?")
            params.append(max(0, int(cleaned_at or 0)))
        fields.append("updated_at = ?")
        params.append(int(updated_at or time.time()))
        params.append(normalized_id)
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT * FROM task_workspaces
                WHERE task_id = ?
                LIMIT 1
                """,
                (normalized_id,),
            ).fetchone()
            if existing is None:
                return None
            conn.execute(
                f"""
                UPDATE task_workspaces
                SET {", ".join(fields)}
                WHERE task_id = ?
                """,
                tuple(params),
            )
            updated = conn.execute(
                """
                SELECT * FROM task_workspaces
                WHERE task_id = ?
                LIMIT 1
                """,
                (normalized_id,),
            ).fetchone()
        return self._row_to_task_workspace(dict(updated)) if updated else None



    def append_task_workspace_event(
        self,
        *,
        task_id: str,
        profile_user_id: str,
        session_id: str,
        event_type: str,
        from_actor: str = "",
        priority: str = "normal",
        requires_user: bool = False,
        message: str = "",
        payload: dict[str, Any] | None = None,
        status: str = "pending",
        timestamp: int | None = None,
        event_id: str = "",
    ) -> dict[str, Any]:
        effective_ts = int(timestamp or time.time())
        event = {
            "event_id": str(event_id or "").strip() or f"task_event::{uuid.uuid4()}",
            "task_id": str(task_id or "").strip(),
            "profile_user_id": str(profile_user_id),
            "session_id": str(session_id),
            "event_type": str(event_type or "").strip()[:64] or "note",
            "from_actor": str(from_actor or "").strip()[:64],
            "priority": self._normalize_task_event_priority(priority),
            "requires_user": 1 if requires_user else 0,
            "message": str(message or "").strip(),
            "payload_json": json.dumps(payload if isinstance(payload, dict) else {}, ensure_ascii=False),
            "status": self._normalize_task_event_status(status),
            "created_at": effective_ts,
            "handled_at": 0,
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO task_workspace_events (
                    event_id, task_id, profile_user_id, session_id, event_type,
                    from_actor, priority, requires_user, message, payload_json,
                    status, created_at, handled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["event_id"],
                    event["task_id"],
                    event["profile_user_id"],
                    event["session_id"],
                    event["event_type"],
                    event["from_actor"],
                    event["priority"],
                    event["requires_user"],
                    event["message"],
                    event["payload_json"],
                    event["status"],
                    event["created_at"],
                    event["handled_at"],
                ),
            )
        return self._row_to_task_workspace_event(event)



    def list_task_workspace_events(
        self,
        *,
        task_id: str | None = None,
        profile_user_id: str | None = None,
        session_id: str | None = None,
        status: str | None = None,
        limit: int | None = 50,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        normalized_task_id = str(task_id or "").strip()
        if normalized_task_id:
            clauses.append("task_id = ?")
            params.append(normalized_task_id)
        normalized_profile = str(profile_user_id or "").strip()
        if normalized_profile:
            clauses.append("profile_user_id = ?")
            params.append(normalized_profile)
        normalized_session = str(session_id or "").strip()
        if normalized_session:
            clauses.append("session_id = ?")
            params.append(normalized_session)
        normalized_status = str(status or "").strip()
        if normalized_status:
            clauses.append("status = ?")
            params.append(self._normalize_task_event_status(normalized_status))
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        limit_sql = ""
        if limit is not None:
            limit_sql = "LIMIT ?"
            params.append(max(1, int(limit or 50)))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM task_workspace_events
                {where_sql}
                ORDER BY created_at ASC
                {limit_sql}
                """,
                tuple(params),
            ).fetchall()
        return [self._row_to_task_workspace_event(dict(row)) for row in rows]



    def mark_task_workspace_event_handled(
        self,
        *,
        event_id: str,
        status: str = "handled",
        handled_at: int | None = None,
    ) -> dict[str, Any] | None:
        normalized_id = str(event_id or "").strip()
        if not normalized_id:
            return None
        effective_ts = int(handled_at or time.time())
        normalized_status = self._normalize_task_event_status(status)
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT * FROM task_workspace_events
                WHERE event_id = ?
                LIMIT 1
                """,
                (normalized_id,),
            ).fetchone()
            if existing is None:
                return None
            conn.execute(
                """
                UPDATE task_workspace_events
                SET status = ?, handled_at = ?
                WHERE event_id = ?
                """,
                (normalized_status, effective_ts, normalized_id),
            )
            updated = conn.execute(
                """
                SELECT * FROM task_workspace_events
                WHERE event_id = ?
                LIMIT 1
                """,
                (normalized_id,),
            ).fetchone()
        return self._row_to_task_workspace_event(dict(updated)) if updated else None
