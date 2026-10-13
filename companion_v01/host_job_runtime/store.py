"""Compose one SQLite authority and own its connections, schema and row mapping."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from .admission import JobAdmissionMixin
from .completion import JobCompletionMixin
from .lifecycle import JobLifecycleMixin
from .models import HostJob, HostJobOwner, _json_value_load
from .recovery import JobRecoveryMixin
from .subagents import SubagentRecordsMixin



class HostJobStore(SubagentRecordsMixin, JobAdmissionMixin, JobLifecycleMixin, JobRecoveryMixin, JobCompletionMixin):
    """SQLite-backed job state stored in the bot instance database."""

    def __init__(self, database_path: str | Path, *, clock: Callable[[], float] | None = None) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or time.time
        self._ensure_schema()

    def get(self, job_id: Any, *, owner: HostJobOwner) -> HostJob | None:
        normalized_id = str(job_id or "").strip()
        if not normalized_id or not isinstance(owner, HostJobOwner):
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM host_jobs WHERE job_id = ? AND profile_user_id = ? AND session_id = ?",
                (normalized_id, owner.profile_user_id, owner.session_id),
            ).fetchone()
        return self._row_to_job(row) if row is not None else None

    def get_by_key(self, *, owner: HostJobOwner, capability_source: str,
                   capability_id: str, idempotency_key: str) -> HostJob | None:
        """Resolve a public execution receipt through the existing unique index."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM host_jobs WHERE profile_user_id = ? AND session_id = ? "
                "AND capability_source = ? AND capability_id = ? AND idempotency_key = ?",
                (owner.profile_user_id, owner.session_id, capability_source, capability_id, idempotency_key),
            ).fetchone()
        return self._row_to_job(row) if row is not None else None

    def completion_state(self, job: HostJob) -> dict[str, Any] | None:
        """Fresh receipt/authority check without loading the stored result body."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT completion_event_id, status, model_consumed_by, scope_revoked_reason FROM host_jobs "
                "WHERE job_id = ? AND profile_user_id = ? AND session_id = ? AND character_pack_id = ? AND channel = ?",
                (job.job_id, job.owner.profile_user_id, job.owner.session_id, job.character_pack_id, job.channel),
            ).fetchone()
        return dict(row) if row is not None else None

    @staticmethod
    def _release_expired_claims(connection: sqlite3.Connection, *, now: float) -> int:
        return HostJobStore._fail_lost_claims(connection, now=now, expired_only=True)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30.0)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 30000")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            journal_mode = str(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0] or "").lower()
            if journal_mode != "wal":
                raise sqlite3.OperationalError("host_job_store_wal_unavailable")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS host_jobs (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL UNIQUE,
                    profile_user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    character_pack_id TEXT NOT NULL DEFAULT '',
                    channel TEXT NOT NULL DEFAULT '',
                    delivery_target TEXT NOT NULL DEFAULT '',
                    capability_source TEXT NOT NULL,
                    capability_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL DEFAULT '',
                    tool_call_id TEXT NOT NULL DEFAULT '',
                    idempotency_key TEXT NOT NULL,
                    argument_fingerprint TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    completion_mode TEXT NOT NULL,
                    memory_mode TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    control_state TEXT NOT NULL DEFAULT 'running',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    available_at REAL NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    started_at REAL NOT NULL DEFAULT 0,
                    finished_at REAL NOT NULL DEFAULT 0,
                    lease_until REAL NOT NULL DEFAULT 0,
                    claim_token TEXT NOT NULL DEFAULT '',
                    claimed_by TEXT NOT NULL DEFAULT '',
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    result_summary TEXT NOT NULL DEFAULT '',
                    artifacts_json TEXT NOT NULL DEFAULT '[]',
                    completion_event_id TEXT NOT NULL,
                    completion_status TEXT NOT NULL DEFAULT 'waiting',
                    completion_attempts INTEGER NOT NULL DEFAULT 0,
                    completion_last_error TEXT NOT NULL DEFAULT '',
                    last_error TEXT NOT NULL DEFAULT '',
                    checkpoint_json TEXT NOT NULL DEFAULT 'null',
                    checkpoint_version INTEGER NOT NULL DEFAULT 0,
                    checkpoint_fingerprint TEXT NOT NULL DEFAULT '',
                    checkpoint_generation_id TEXT NOT NULL DEFAULT '',
                    checkpoint_updated_at REAL NOT NULL DEFAULT 0,
                    recovery_generation_id TEXT NOT NULL DEFAULT '',
                    recovery_generation_epoch INTEGER NOT NULL DEFAULT 0,
                    recovery_owner_round INTEGER NOT NULL DEFAULT 0,
                    recovery_epoch INTEGER NOT NULL DEFAULT 0,
                    recovery_state TEXT NOT NULL DEFAULT '',
                    recovery_reason TEXT NOT NULL DEFAULT ''
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_host_jobs_idempotency
                ON host_jobs(profile_user_id, session_id, capability_source, capability_id, idempotency_key);

                CREATE INDEX IF NOT EXISTS idx_host_jobs_ready
                ON host_jobs(status, available_at, sequence);
                """
            )
            existing_columns = {
                str(row["name"] or "")
                for row in connection.execute("PRAGMA table_info(host_jobs)").fetchall()
            }
            migrations = {
                "subagent_deleted_at": "REAL NOT NULL DEFAULT 0",
                "subagent_deleting": "INTEGER NOT NULL DEFAULT 0",
                "subagent_delete_ids_json": "TEXT NOT NULL DEFAULT '[]'",
                "model_consumed_by": "TEXT NOT NULL DEFAULT ''",
                "result_json": "TEXT NOT NULL DEFAULT '{}'",
                "delivery_receipt_json": "TEXT NOT NULL DEFAULT '{}'",
                "scope_revoked_reason": "TEXT NOT NULL DEFAULT ''",
                "control_state": "TEXT NOT NULL DEFAULT 'running'",
                "completion_status": "TEXT NOT NULL DEFAULT 'waiting'",
                "completion_attempts": "INTEGER NOT NULL DEFAULT 0",
                "completion_last_error": "TEXT NOT NULL DEFAULT ''",
                "checkpoint_json": "TEXT NOT NULL DEFAULT 'null'",
                "checkpoint_version": "INTEGER NOT NULL DEFAULT 0",
                "checkpoint_fingerprint": "TEXT NOT NULL DEFAULT ''",
                "checkpoint_generation_id": "TEXT NOT NULL DEFAULT ''",
                "checkpoint_updated_at": "REAL NOT NULL DEFAULT 0",
                "recovery_generation_id": "TEXT NOT NULL DEFAULT ''",
                "recovery_generation_epoch": "INTEGER NOT NULL DEFAULT 0",
                "recovery_owner_round": "INTEGER NOT NULL DEFAULT 0",
                "recovery_epoch": "INTEGER NOT NULL DEFAULT 0",
                "recovery_state": "TEXT NOT NULL DEFAULT ''",
                "recovery_reason": "TEXT NOT NULL DEFAULT ''",
            }
            for name, declaration in migrations.items():
                if name not in existing_columns:
                    connection.execute(f"ALTER TABLE host_jobs ADD COLUMN {name} {declaration}")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_host_subagents ON host_jobs("
                "profile_user_id, session_id, json_extract(payload_json, '$.child_session_id'), sequence) "
                "WHERE capability_source = 'subagent'")
            connection.execute(
                """
                UPDATE host_jobs
                SET completion_status = CASE
                    WHEN completion_mode = 'silent' AND memory_mode = 'current_turn' THEN 'silent' ELSE 'pending' END
                WHERE status IN ('succeeded', 'failed', 'cancelled')
                  AND completion_status = 'waiting'
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_host_jobs_completion
                ON host_jobs(completion_status, sequence)
                """
            )

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> HostJob:
        payload = json.loads(str(row["payload_json"] or "{}"))
        artifacts = json.loads(str(row["artifacts_json"] or "[]"))
        return HostJob(
            job_id=str(row["job_id"] or ""),
            sequence=int(row["sequence"] or 0),
            owner=HostJobOwner(str(row["profile_user_id"] or ""), str(row["session_id"] or "")),
            character_pack_id=str(row["character_pack_id"] or ""),
            channel=str(row["channel"] or ""),
            delivery_target=str(row["delivery_target"] or ""),
            capability_source=str(row["capability_source"] or ""),
            capability_id=str(row["capability_id"] or ""),
            turn_id=str(row["turn_id"] or ""),
            tool_call_id=str(row["tool_call_id"] or ""),
            idempotency_key=str(row["idempotency_key"] or ""),
            argument_fingerprint=str(row["argument_fingerprint"] or ""),
            payload=payload if isinstance(payload, dict) else {},
            completion_mode=str(row["completion_mode"] or ""),
            memory_mode=str(row["memory_mode"] or ""),
            status=str(row["status"] or ""),
            attempts=int(row["attempts"] or 0),
            available_at=float(row["available_at"] or 0),
            created_at=float(row["created_at"] or 0),
            started_at=float(row["started_at"] or 0),
            finished_at=float(row["finished_at"] or 0),
            lease_until=float(row["lease_until"] or 0),
            claim_token=str(row["claim_token"] or ""),
            claimed_by=str(row["claimed_by"] or ""),
            cancel_requested=bool(row["cancel_requested"]),
            result_summary=str(row["result_summary"] or ""),
            artifacts=tuple(dict(item) for item in artifacts if isinstance(item, dict)),
            completion_event_id=str(row["completion_event_id"] or ""),
            completion_status=str(row["completion_status"] or ""),
            completion_attempts=int(row["completion_attempts"] or 0),
            completion_last_error=str(row["completion_last_error"] or ""),
            last_error=str(row["last_error"] or ""),
            checkpoint=_json_value_load(row["checkpoint_json"]),
            checkpoint_version=int(row["checkpoint_version"] or 0),
            checkpoint_fingerprint=str(row["checkpoint_fingerprint"] or ""),
            checkpoint_generation_id=str(row["checkpoint_generation_id"] or ""),
            checkpoint_updated_at=float(row["checkpoint_updated_at"] or 0),
            recovery_generation_id=str(row["recovery_generation_id"] or ""),
            recovery_generation_epoch=int(row["recovery_generation_epoch"] or 0),
            recovery_owner_round=int(row["recovery_owner_round"] or 0),
            recovery_epoch=int(row["recovery_epoch"] or 0),
            recovery_state=str(row["recovery_state"] or ""),
            recovery_reason=str(row["recovery_reason"] or ""),
            result=json.loads(str(row["result_json"] or "{}")),
            delivery_receipt=json.loads(str(row["delivery_receipt_json"] or "{}")),
            scope_revoked_reason=str(row["scope_revoked_reason"] or ""),
            control_state=str(row["control_state"] or "running"),
            model_consumed_by=str(row["model_consumed_by"] or ""),
        )
