"""Durable FIFO inbox for host-owned conversation work.

The inbox stores JSON-safe event facts in the existing bot instance database.
It owns durability, idempotency and leases; ``TurnCoordinator`` still owns the
single active model turn and its safe steering boundary.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator


@dataclass(frozen=True, slots=True)
class SessionInboxItem:
    item_id: str
    sequence: int
    session_key: str
    profile_user_id: str
    session_id: str
    kind: str
    source: str
    source_event_id: str
    payload: dict[str, Any]
    status: str
    attempts: int
    available_at: float
    created_at: float
    claimed_at: float
    lease_until: float
    claim_token: str
    last_error: str


class SessionInboxStore:
    """SQLite-backed session work queue stored beside the MemCore tables."""

    def __init__(self, database_path: str | Path, *, clock: Callable[[], float] | None = None) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or time.time
        self._ensure_schema()

    def enqueue(
        self,
        *,
        session_key: Any,
        profile_user_id: Any,
        session_id: Any,
        kind: Any,
        payload: Any,
        source: Any,
        source_event_id: Any = "",
        available_at: float | None = None,
    ) -> dict[str, Any]:
        normalized_key = str(session_key or "").strip()
        normalized_profile = str(profile_user_id or "").strip()
        normalized_session = str(session_id or "").strip()
        normalized_kind = str(kind or "").strip()
        normalized_source = str(source or "").strip()
        normalized_event_id = str(source_event_id or "").strip()
        if not all((normalized_key, normalized_profile, normalized_session, normalized_kind, normalized_source)):
            return {"ok": False, "status": "invalid", "reason": "session_inbox_identity_required"}
        if not isinstance(payload, dict):
            return {"ok": False, "status": "invalid", "reason": "session_inbox_payload_object_required"}
        try:
            payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        except (TypeError, ValueError):
            return {"ok": False, "status": "invalid", "reason": "session_inbox_payload_not_json_safe"}

        now = float(self._clock())
        item_id = f"inbox_{uuid.uuid4().hex}"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if normalized_event_id:
                existing = connection.execute(
                    """
                    SELECT * FROM session_inbox_items
                    WHERE source = ? AND source_event_id = ?
                    LIMIT 1
                    """,
                    (normalized_source, normalized_event_id),
                ).fetchone()
                if existing is not None:
                    item = self._row_to_item(existing)
                    immutable_match = (
                        item.session_key == normalized_key
                        and item.profile_user_id == normalized_profile
                        and item.session_id == normalized_session
                        and item.kind == normalized_kind
                        and item.payload == payload
                    )
                    if not immutable_match:
                        return {
                            "ok": False,
                            "status": "collision",
                            "reason": "source_event_identity_collision",
                            "item_id": item.item_id,
                            "sequence": item.sequence,
                        }
                    return {
                        "ok": True,
                        "status": "duplicate",
                        "reason": "source_event_already_registered",
                        "item": item,
                        "item_id": item.item_id,
                        "sequence": item.sequence,
                    }
            cursor = connection.execute(
                """
                INSERT INTO session_inbox_items (
                    item_id, session_key, profile_user_id, session_id, kind,
                    source, source_event_id, payload_json, status, attempts,
                    available_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?)
                """,
                (
                    item_id,
                    normalized_key,
                    normalized_profile,
                    normalized_session,
                    normalized_kind,
                    normalized_source,
                    normalized_event_id,
                    payload_json,
                    max(now, float(available_at if available_at is not None else now)),
                    now,
                    now,
                ),
            )
            sequence = int(cursor.lastrowid or 0)
        return {
            "ok": True,
            "status": "queued",
            "reason": "session_inbox_persisted",
            "item_id": item_id,
            "sequence": sequence,
        }

    def claim_next(
        self,
        session_key: Any,
        *,
        worker_id: Any,
        lease_seconds: float = 60.0,
    ) -> dict[str, Any]:
        normalized_key = str(session_key or "").strip()
        normalized_worker = str(worker_id or "").strip()
        if not normalized_key or not normalized_worker:
            return {"ok": False, "status": "invalid", "reason": "session_key_and_worker_required"}
        now = float(self._clock())
        lease_until = now + max(1.0, float(lease_seconds))
        claim_token = f"claim_{uuid.uuid4().hex}"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._release_expired_claims(connection, now=now)
            active = connection.execute(
                """
                SELECT item_id FROM session_inbox_items
                WHERE session_key = ? AND status = 'claimed' AND lease_until > ?
                LIMIT 1
                """,
                (normalized_key, now),
            ).fetchone()
            if active is not None:
                return {"ok": False, "status": "busy", "reason": "session_item_already_claimed"}
            row = connection.execute(
                """
                SELECT * FROM session_inbox_items
                WHERE session_key = ? AND status = 'queued' AND available_at <= ?
                ORDER BY sequence ASC
                LIMIT 1
                """,
                (normalized_key, now),
            ).fetchone()
            if row is None:
                return {"ok": False, "status": "idle", "reason": "no_ready_session_item"}
            changed = connection.execute(
                """
                UPDATE session_inbox_items
                SET status = 'claimed', attempts = attempts + 1, claimed_at = ?,
                    lease_until = ?, claim_token = ?, claimed_by = ?, updated_at = ?
                WHERE item_id = ? AND status = 'queued'
                """,
                (now, lease_until, claim_token, normalized_worker, now, row["item_id"]),
            ).rowcount
            if changed != 1:
                return {"ok": False, "status": "conflict", "reason": "session_item_claim_conflict"}
            claimed = connection.execute(
                "SELECT * FROM session_inbox_items WHERE item_id = ?",
                (row["item_id"],),
            ).fetchone()
        return {
            "ok": True,
            "status": "claimed",
            "reason": "session_item_claimed",
            "item": self._row_to_item(claimed),
            "claim_token": claim_token,
        }

    def commit(self, item_id: Any, *, claim_token: Any) -> dict[str, Any]:
        return self._finish_claim(
            item_id=item_id,
            claim_token=claim_token,
            status="committed",
            last_error="",
        )

    def fail(
        self,
        item_id: Any,
        *,
        claim_token: Any,
        error: Any,
        retryable: bool,
        retry_delay_seconds: float = 0.0,
    ) -> dict[str, Any]:
        normalized_item_id = str(item_id or "").strip()
        normalized_token = str(claim_token or "").strip()
        if not normalized_item_id or not normalized_token:
            return {"ok": False, "status": "invalid", "reason": "item_id_and_claim_token_required"}
        now = float(self._clock())
        next_status = "queued" if retryable else "failed"
        available_at = now + max(0.0, float(retry_delay_seconds)) if retryable else now
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                """
                UPDATE session_inbox_items
                SET status = ?, available_at = ?, lease_until = 0,
                    claim_token = '', claimed_by = '', updated_at = ?,
                    completed_at = CASE WHEN ? = 'failed' THEN ? ELSE 0 END,
                    last_error = ?
                WHERE item_id = ? AND status = 'claimed' AND claim_token = ?
                """,
                (
                    next_status,
                    available_at,
                    now,
                    next_status,
                    now,
                    str(error or "").strip()[:500],
                    normalized_item_id,
                    normalized_token,
                ),
            ).rowcount
        if changed != 1:
            return {"ok": False, "status": "stale", "reason": "session_item_claim_not_owned"}
        return {
            "ok": True,
            "status": "retryable" if retryable else "failed",
            "reason": "session_item_requeued" if retryable else "session_item_failed",
        }

    def get(self, item_id: Any) -> SessionInboxItem | None:
        normalized = str(item_id or "").strip()
        if not normalized:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM session_inbox_items WHERE item_id = ?",
                (normalized,),
            ).fetchone()
        return self._row_to_item(row) if row is not None else None

    def pending_session_keys(self) -> list[str]:
        now = float(self._clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._release_expired_claims(connection, now=now)
            rows = connection.execute(
                """
                SELECT session_key, MIN(sequence) AS first_sequence
                FROM session_inbox_items
                WHERE status = 'queued'
                GROUP BY session_key
                ORDER BY first_sequence ASC
                """
            ).fetchall()
        return [str(row["session_key"] or "") for row in rows if str(row["session_key"] or "")]

    def recover_abandoned_claims(self) -> int:
        """Release claims from a previous host process during single-owner startup."""

        now = float(self._clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                """
                UPDATE session_inbox_items
                SET status = 'queued', lease_until = 0, claim_token = '', claimed_by = '', updated_at = ?
                WHERE status = 'claimed'
                """,
                (now,),
            ).rowcount
        return int(changed or 0)

    def pending_count(self, session_key: Any) -> int:
        normalized = str(session_key or "").strip()
        if not normalized:
            return 0
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count FROM session_inbox_items
                WHERE session_key = ? AND status IN ('queued', 'claimed')
                """,
                (normalized,),
            ).fetchone()
        return int(row["count"] or 0) if row is not None else 0

    def _finish_claim(
        self,
        *,
        item_id: Any,
        claim_token: Any,
        status: str,
        last_error: str,
    ) -> dict[str, Any]:
        normalized_item_id = str(item_id or "").strip()
        normalized_token = str(claim_token or "").strip()
        if not normalized_item_id or not normalized_token:
            return {"ok": False, "status": "invalid", "reason": "item_id_and_claim_token_required"}
        now = float(self._clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                """
                UPDATE session_inbox_items
                SET status = ?, lease_until = 0, claim_token = '', claimed_by = '',
                    updated_at = ?, completed_at = ?, last_error = ?
                WHERE item_id = ? AND status = 'claimed' AND claim_token = ?
                """,
                (status, now, now, last_error, normalized_item_id, normalized_token),
            ).rowcount
        if changed != 1:
            return {"ok": False, "status": "stale", "reason": "session_item_claim_not_owned"}
        return {"ok": True, "status": status, "reason": f"session_item_{status}"}

    @staticmethod
    def _release_expired_claims(connection: sqlite3.Connection, *, now: float) -> int:
        return int(
            connection.execute(
                """
                UPDATE session_inbox_items
                SET status = 'queued', lease_until = 0, claim_token = '', claimed_by = '', updated_at = ?
                WHERE status = 'claimed' AND lease_until <= ?
                """,
                (now, now),
            ).rowcount
            or 0
        )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS session_inbox_items (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id TEXT NOT NULL UNIQUE,
                    session_key TEXT NOT NULL,
                    profile_user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_event_id TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    available_at REAL NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    claimed_at REAL NOT NULL DEFAULT 0,
                    lease_until REAL NOT NULL DEFAULT 0,
                    claim_token TEXT NOT NULL DEFAULT '',
                    claimed_by TEXT NOT NULL DEFAULT '',
                    completed_at REAL NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT ''
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_session_inbox_source_event
                ON session_inbox_items(source, source_event_id)
                WHERE source_event_id != '';

                CREATE INDEX IF NOT EXISTS idx_session_inbox_ready
                ON session_inbox_items(session_key, status, available_at, sequence);
                """
            )

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> SessionInboxItem:
        raw_payload = json.loads(str(row["payload_json"] or "{}"))
        payload = raw_payload if isinstance(raw_payload, dict) else {}
        return SessionInboxItem(
            item_id=str(row["item_id"] or ""),
            sequence=int(row["sequence"] or 0),
            session_key=str(row["session_key"] or ""),
            profile_user_id=str(row["profile_user_id"] or ""),
            session_id=str(row["session_id"] or ""),
            kind=str(row["kind"] or ""),
            source=str(row["source"] or ""),
            source_event_id=str(row["source_event_id"] or ""),
            payload=payload,
            status=str(row["status"] or ""),
            attempts=int(row["attempts"] or 0),
            available_at=float(row["available_at"] or 0),
            created_at=float(row["created_at"] or 0),
            claimed_at=float(row["claimed_at"] or 0),
            lease_until=float(row["lease_until"] or 0),
            claim_token=str(row["claim_token"] or ""),
            last_error=str(row["last_error"] or ""),
        )


__all__ = ["SessionInboxItem", "SessionInboxStore"]
