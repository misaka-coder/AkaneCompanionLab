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
    batch_id: str = ""
    processing_started_at: float = 0.0


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
        input_fingerprint: str = "",
    ) -> dict[str, Any]:
        normalized_key = str(session_key or "").strip()
        normalized_profile = str(profile_user_id or "").strip()
        normalized_session = str(session_id or "").strip()
        normalized_kind = str(kind or "").strip()
        normalized_source = str(source or "").strip()
        normalized_event_id = str(source_event_id or "").strip()
        if (
            not isinstance(input_fingerprint, str)
            or input_fingerprint
            and (len(input_fingerprint) != 64 or any(c not in "0123456789abcdef" for c in input_fingerprint))
        ):
            return {"ok": False, "status": "invalid", "reason": "invalid_inbox_input_fingerprint"}
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
                        and (
                            bool(input_fingerprint)
                            and existing["input_fingerprint"] == input_fingerprint
                            or not input_fingerprint
                            and not existing["input_fingerprint"]
                            and item.payload == payload
                        )
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
                    available_at, created_at, updated_at, input_fingerprint
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?)
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
                    input_fingerprint,
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
        expected_item_id: Any = "",
        batch_key: Callable[[SessionInboxItem], str] | None = None,
        max_batch_items: int = 32,
        max_batch_bytes: int = 65_536,
    ) -> dict[str, Any]:
        normalized_key = str(session_key or "").strip()
        normalized_worker = str(worker_id or "").strip()
        if not normalized_key or not normalized_worker:
            return {"ok": False, "status": "invalid", "reason": "session_key_and_worker_required"}
        now = float(self._clock())
        lease_until = now + max(1.0, float(lease_seconds))
        claim_token = f"claim_{uuid.uuid4().hex}"
        expected_id = str(expected_item_id or "").strip()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._release_expired_claims(connection, now=now)
            # An expected item is an ordered steer reservation for the model
            # turn that already owns the session. It may coexist with that
            # turn's runner claim, but it still cannot jump an earlier queued
            # item. Ordinary runner claims remain strictly single-owner.
            if not expected_id:
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
                WHERE session_key = ? AND status = 'queued'
                ORDER BY sequence ASC
                LIMIT 1
                """,
                (normalized_key,),
            ).fetchone()
            if row is None:
                return {"ok": False, "status": "idle", "reason": "no_ready_session_item"}
            if float(row["available_at"]) > now:
                return {
                    "ok": False,
                    "status": "deferred",
                    "reason": "earlier_session_item_deferred",
                    "retry_after": float(row["available_at"]) - now,
                }
            if expected_id and str(row["item_id"] or "") != expected_id:
                return {
                    "ok": False,
                    "status": "blocked",
                    "reason": "earlier_session_item_waiting",
                    "next_item_id": str(row["item_id"] or ""),
                }
            rows = [row]
            frozen_batch = str(row["batch_id"] or "")
            if frozen_batch:
                # Membership is fixed by the first claim, not by retry timing.
                # Replaying a batch must retain its model/delivery identity.
                rows = connection.execute(
                    "SELECT * FROM session_inbox_items WHERE batch_id = ? ORDER BY sequence",
                    (frozen_batch,),
                ).fetchall()
                if expected_id or any(item["status"] != "queued" for item in rows):
                    return {"ok": False, "status": "blocked", "reason": "session_batch_not_claimable"}
            elif batch_key is not None and not expected_id:
                first = self._row_to_item(row)
                key = batch_key(first)
                if key:
                    size = len(str(row["payload_json"]).encode("utf-8"))
                    candidates = connection.execute(
                        """SELECT * FROM session_inbox_items
                           WHERE session_key = ? AND sequence > ? AND status IN ('queued', 'claimed')
                           ORDER BY sequence LIMIT ?""",
                        (normalized_key, row["sequence"], max(0, int(max_batch_items) - 1)),
                    ).fetchall()
                    for candidate in candidates:
                        item = self._row_to_item(candidate)
                        item_size = len(str(candidate["payload_json"]).encode("utf-8"))
                        if (
                            item.status != "queued"
                            or item.available_at > now
                            or item.batch_id
                            or item.source != first.source
                            or item.kind != first.kind
                            or item.profile_user_id != first.profile_user_id
                            or item.session_id != first.session_id
                            or size + item_size > max_batch_bytes
                            or batch_key(item) != key
                        ):
                            break
                        rows.append(candidate)
                        size += item_size
                    # Freeze even a singleton: a retry may not absorb later work.
                    frozen_batch = f"batch_{uuid.uuid4().hex}"
            claimed_items = []
            for selected in rows:
                connection.execute(
                    """UPDATE session_inbox_items
                       SET status = 'claimed', attempts = attempts + 1, claimed_at = ?,
                           lease_until = ?, claim_token = ?, claimed_by = ?, updated_at = ?, batch_id = ?
                       WHERE item_id = ? AND status = 'queued'""",
                    (now, lease_until, claim_token, normalized_worker, now, frozen_batch, selected["item_id"]),
                )
                claimed_items.append(
                    self._row_to_item(
                        connection.execute(
                            "SELECT * FROM session_inbox_items WHERE item_id = ?",
                            (selected["item_id"],),
                        ).fetchone()
                    )
                )
        return {
            "ok": True,
            "status": "claimed",
            "reason": "session_item_claimed",
            "item": claimed_items[0],
            "items": claimed_items,
            "claim_token": claim_token,
        }

    def settle_claims(
        self,
        items: list[SessionInboxItem],
        *,
        status: str,
        error: str = "",
        retry_delay_seconds: float = 0.0,
    ) -> dict[str, Any]:
        """Commit or fail a claimed envelope atomically, retaining each input fact."""
        if status not in {"committed", "queued", "failed"} or not items:
            return {"ok": False, "status": "invalid", "reason": "invalid_session_settlement"}
        now = float(self._clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not self._owns_claims(connection, items):
                return {"ok": False, "status": "stale", "reason": "session_item_claim_not_owned"}
            if status == "queued" and any(
                connection.execute(
                    "SELECT processing_started_at FROM session_inbox_items WHERE item_id = ?",
                    (item.item_id,),
                ).fetchone()[0]
                for item in items
            ):
                # The model may already have performed external actions. A
                # retryable transport exception is not proof it is safe to replay.
                status, error = "failed", "session_turn_outcome_unknown"
            for item in items:
                connection.execute(
                    """UPDATE session_inbox_items
                       SET status = ?, available_at = ?, lease_until = 0, claim_token = '', claimed_by = '',
                           updated_at = ?, completed_at = ?, last_error = ? WHERE item_id = ?""",
                    (
                        status,
                        now + max(0.0, retry_delay_seconds) if status == "queued" else now,
                        now,
                        0 if status == "queued" else now,
                        str(error)[:500],
                        item.item_id,
                    ),
                )
        return {"ok": True, "status": status, "reason": "session_claims_settled"}

    def begin_processing(self, items: list[SessionInboxItem]) -> dict[str, Any]:
        """Fence model-side effects before entering a non-replayable Agent turn."""
        now = float(self._clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not items or not self._owns_claims(connection, items):
                return {"ok": False, "status": "stale", "reason": "session_item_claim_not_owned"}
            for item in items:
                started = connection.execute(
                    "SELECT processing_started_at FROM session_inbox_items WHERE item_id = ?",
                    (item.item_id,),
                ).fetchone()[0]
                if started:
                    return {"ok": False, "status": "blocked", "reason": "session_turn_already_started"}
            for item in items:
                connection.execute(
                    "UPDATE session_inbox_items SET processing_started_at = ?, updated_at = ? WHERE item_id = ?",
                    (now, now, item.item_id),
                )
        return {"ok": True, "status": "started", "reason": "session_processing_fenced"}

    def renew_claims(self, items: list[SessionInboxItem], *, lease_seconds: float) -> dict[str, Any]:
        now = float(self._clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not items or not self._owns_claims(connection, items):
                return {"ok": False, "status": "stale", "reason": "session_item_claim_not_owned"}
            for item in items:
                connection.execute(
                    "UPDATE session_inbox_items SET lease_until = ?, updated_at = ? WHERE item_id = ?",
                    (now + max(1.0, lease_seconds), now, item.item_id),
                )
        return {"ok": True, "status": "renewed", "reason": "session_claims_renewed"}

    @staticmethod
    def _owns_claims(connection: sqlite3.Connection, items: list[SessionInboxItem]) -> bool:
        if len({item.item_id for item in items}) != len(items):
            return False
        for item in items:
            row = connection.execute(
                "SELECT status, claim_token, batch_id FROM session_inbox_items WHERE item_id = ?",
                (item.item_id,),
            ).fetchone()
            if (
                row is None
                or row["status"] != "claimed"
                or not item.claim_token
                or row["claim_token"] != item.claim_token
                or str(row["batch_id"] or "") != item.batch_id
            ):
                return False
        for batch_id in {item.batch_id for item in items if item.batch_id}:
            expected = {
                row["item_id"]
                for row in connection.execute(
                    "SELECT item_id FROM session_inbox_items WHERE batch_id = ?",
                    (batch_id,),
                )
            }
            if expected != {item.item_id for item in items if item.batch_id == batch_id}:
                return False
        return True

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
                WHERE item_id = ? AND status = 'claimed' AND claim_token = ? AND batch_id = ''
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
                SET status = CASE WHEN processing_started_at > 0 THEN 'failed' ELSE 'queued' END,
                    last_error = CASE WHEN processing_started_at > 0 THEN 'session_turn_outcome_unknown' ELSE last_error END,
                    lease_until = 0, claim_token = '', claimed_by = '', updated_at = ?
                WHERE status = 'claimed'
                """,
                (now,),
            ).rowcount
        return int(changed or 0)

    def owned_claim_count(self, worker_id: str) -> int:
        """Read-only shutdown fence, including claims handed to active turns."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM session_inbox_items WHERE status = 'claimed' AND claimed_by = ?",
                (worker_id,),
            ).fetchone()
        return int(row[0])

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
                WHERE item_id = ? AND status = 'claimed' AND claim_token = ? AND batch_id = ''
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
                SET status = CASE WHEN processing_started_at > 0 THEN 'failed' ELSE 'queued' END,
                    last_error = CASE WHEN processing_started_at > 0 THEN 'session_turn_outcome_unknown' ELSE last_error END,
                    lease_until = 0, claim_token = '', claimed_by = '', updated_at = ?
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
            connection.execute("BEGIN IMMEDIATE")
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(session_inbox_items)")}
            if "batch_id" not in columns:
                connection.execute("ALTER TABLE session_inbox_items ADD COLUMN batch_id TEXT NOT NULL DEFAULT ''")
            if "processing_started_at" not in columns:
                connection.execute(
                    "ALTER TABLE session_inbox_items ADD COLUMN processing_started_at REAL NOT NULL DEFAULT 0"
                )
            if "input_fingerprint" not in columns:
                connection.execute(
                    "ALTER TABLE session_inbox_items ADD COLUMN input_fingerprint TEXT NOT NULL DEFAULT ''"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_session_inbox_batch ON session_inbox_items(batch_id) WHERE batch_id != ''"
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
            batch_id=str(row["batch_id"] or ""),
            processing_started_at=float(row["processing_started_at"] or 0),
        )


__all__ = ["SessionInboxItem", "SessionInboxStore"]
