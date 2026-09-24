"""Durable authority for host-owned background jobs.

Workers may run in threads, processes, plugins, or desktop satellites.  This
store is the single source of truth for job identity, ownership, leases,
cancellation, terminal results, and completion event identity.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator


JOB_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
JOB_CONTROL_STATES = frozenset({"running", "paused", "stopping", "stopped"})
_COMPLETION_MODES = frozenset({"agent", "silent"})
_MEMORY_MODES = frozenset({"current_turn", "timeline"})


@dataclass(frozen=True, slots=True)
class HostJobOwner:
    profile_user_id: str
    session_id: str

    def __post_init__(self) -> None:
        for field_name in ("profile_user_id", "session_id"):
            normalized = str(getattr(self, field_name) or "").strip()
            if not normalized:
                raise ValueError(f"host_job_{field_name}_required")
            object.__setattr__(self, field_name, normalized)


@dataclass(frozen=True, slots=True)
class HostJob:
    job_id: str
    sequence: int
    owner: HostJobOwner
    character_pack_id: str
    channel: str
    delivery_target: str
    capability_source: str
    capability_id: str
    turn_id: str
    tool_call_id: str
    idempotency_key: str
    argument_fingerprint: str
    payload: dict[str, Any]
    completion_mode: str
    memory_mode: str
    status: str
    attempts: int
    available_at: float
    created_at: float
    started_at: float
    finished_at: float
    lease_until: float
    claim_token: str
    claimed_by: str
    cancel_requested: bool
    result_summary: str
    artifacts: tuple[dict[str, Any], ...]
    completion_event_id: str
    completion_status: str
    completion_attempts: int
    completion_last_error: str
    last_error: str
    result: dict[str, Any] = field(default_factory=dict)
    delivery_receipt: dict[str, Any] = field(default_factory=dict)
    scope_revoked_reason: str = ""
    control_state: str = "running"
    checkpoint: Any = None
    checkpoint_version: int = 0
    checkpoint_fingerprint: str = ""
    checkpoint_generation_id: str = ""
    checkpoint_updated_at: float = 0.0
    recovery_generation_id: str = ""
    recovery_generation_epoch: int = 0
    recovery_owner_round: int = 0
    recovery_epoch: int = 0
    recovery_state: str = ""
    recovery_reason: str = ""


class HostJobStore:
    """SQLite-backed job state stored in the bot instance database."""

    def __init__(self, database_path: str | Path, *, clock: Callable[[], float] | None = None) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or time.time
        self._ensure_schema()

    def create(
        self,
        *,
        owner: HostJobOwner,
        capability_source: Any,
        capability_id: Any,
        payload: Any,
        idempotency_key: Any,
        argument_fingerprint: Any,
        character_pack_id: Any = "",
        channel: Any = "",
        delivery_target: Any = "",
        turn_id: Any = "",
        tool_call_id: Any = "",
        completion_mode: Any = "agent",
        memory_mode: Any = "timeline",
        available_at: float | None = None,
    ) -> dict[str, Any]:
        if not isinstance(owner, HostJobOwner):
            return {"ok": False, "status": "invalid", "reason": "host_job_owner_required"}
        source = str(capability_source or "").strip()
        capability = str(capability_id or "").strip()
        idempotency = str(idempotency_key or "").strip()
        fingerprint = str(argument_fingerprint or "").strip()
        completion = str(completion_mode or "").strip().lower()
        memory = str(memory_mode or "").strip().lower()
        if not all((source, capability, idempotency, fingerprint)):
            return {"ok": False, "status": "invalid", "reason": "host_job_identity_required"}
        if completion not in _COMPLETION_MODES or memory not in _MEMORY_MODES:
            return {"ok": False, "status": "invalid", "reason": "host_job_delivery_policy_invalid"}
        if not isinstance(payload, dict):
            return {"ok": False, "status": "invalid", "reason": "host_job_payload_object_required"}
        payload_json = _json_object(payload)
        if payload_json is None:
            return {"ok": False, "status": "invalid", "reason": "host_job_payload_not_json_safe"}

        immutable = {
            "profile_user_id": owner.profile_user_id,
            "session_id": owner.session_id,
            "character_pack_id": str(character_pack_id or "").strip(),
            "channel": str(channel or "").strip(),
            "delivery_target": str(delivery_target or "").strip(),
            "capability_source": source,
            "capability_id": capability,
            "turn_id": str(turn_id or "").strip(),
            "tool_call_id": str(tool_call_id or "").strip(),
            "idempotency_key": idempotency,
            "argument_fingerprint": fingerprint,
            "payload_json": payload_json,
            "completion_mode": completion,
            "memory_mode": memory,
        }
        now = float(self._clock())
        job_id = f"job_{uuid.uuid4().hex}"
        completion_event_id = f"job-completed:{job_id}"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM host_jobs
                WHERE profile_user_id = ? AND session_id = ?
                  AND capability_source = ? AND capability_id = ? AND idempotency_key = ?
                LIMIT 1
                """,
                (owner.profile_user_id, owner.session_id, source, capability, idempotency),
            ).fetchone()
            if existing is not None:
                job = self._row_to_job(existing)
                if not self._immutable_match(job, immutable):
                    return {
                        "ok": False,
                        "status": "collision",
                        "reason": "host_job_idempotency_collision",
                        "job_id": job.job_id,
                    }
                return {
                    "ok": True,
                    "status": "duplicate",
                    "reason": "host_job_already_registered",
                    "job_id": job.job_id,
                    "job": job,
                }
            cursor = connection.execute(
                """
                INSERT INTO host_jobs (
                    job_id, profile_user_id, session_id, character_pack_id, channel,
                    delivery_target, capability_source, capability_id, turn_id,
                    tool_call_id, idempotency_key, argument_fingerprint, payload_json,
                    completion_mode, memory_mode, status, attempts, available_at,
                    created_at, updated_at, completion_event_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    immutable["profile_user_id"],
                    immutable["session_id"],
                    immutable["character_pack_id"],
                    immutable["channel"],
                    immutable["delivery_target"],
                    source,
                    capability,
                    immutable["turn_id"],
                    immutable["tool_call_id"],
                    idempotency,
                    fingerprint,
                    payload_json,
                    completion,
                    memory,
                    max(now, float(available_at if available_at is not None else now)),
                    now,
                    now,
                    completion_event_id,
                ),
            )
            sequence = int(cursor.lastrowid or 0)
        return {
            "ok": True,
            "status": "queued",
            "reason": "host_job_persisted",
            "job_id": job_id,
            "sequence": sequence,
            "completion_event_id": completion_event_id,
        }

    def claim_next(
        self,
        *,
        worker_id: Any,
        lease_seconds: float = 300.0,
        capability_source: Any = "",
    ) -> dict[str, Any]:
        worker = str(worker_id or "").strip()
        source = str(capability_source or "").strip()
        if not worker:
            return {"ok": False, "status": "invalid", "reason": "host_job_worker_required"}
        now = float(self._clock())
        claim_token = f"job-claim_{uuid.uuid4().hex}"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._release_expired_claims(connection, now=now)
            source_clause = " AND capability_source = ?" if source else ""
            arguments: tuple[Any, ...] = (now, source) if source else (now,)
            row = connection.execute(
                f"""
                SELECT * FROM host_jobs
                WHERE status = 'queued' AND available_at <= ?{source_clause}
                ORDER BY sequence ASC
                LIMIT 1
                """,
                arguments,
            ).fetchone()
            if row is None:
                return {"ok": False, "status": "idle", "reason": "no_ready_host_job"}
            job_id = str(row["job_id"] or "")
            changed = connection.execute(
                """
                UPDATE host_jobs
                SET status = 'running', attempts = attempts + 1, started_at = ?,
                    lease_until = ?, claim_token = ?, claimed_by = ?, updated_at = ?
                WHERE job_id = ? AND status = 'queued'
                """,
                (now, now + max(1.0, float(lease_seconds)), claim_token, worker, now, job_id),
            ).rowcount
            if changed != 1:
                return {"ok": False, "status": "conflict", "reason": "host_job_claim_conflict"}
            claimed = connection.execute("SELECT * FROM host_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return {
            "ok": True,
            "status": "running",
            "reason": "host_job_claimed",
            "claim_token": claim_token,
            "job": self._row_to_job(claimed),
        }

    def claim(
        self,
        job_id: Any,
        *,
        worker_id: Any,
        lease_seconds: float = 300.0,
    ) -> dict[str, Any]:
        normalized_id = str(job_id or "").strip()
        worker = str(worker_id or "").strip()
        if not normalized_id or not worker:
            return {"ok": False, "status": "invalid", "reason": "job_id_and_worker_required"}
        now = float(self._clock())
        claim_token = f"job-claim_{uuid.uuid4().hex}"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._release_expired_claims(connection, now=now)
            row = connection.execute(
                "SELECT * FROM host_jobs WHERE job_id = ?",
                (normalized_id,),
            ).fetchone()
            if row is None:
                return {"ok": False, "status": "unknown", "reason": "host_job_not_found"}
            status = str(row["status"] or "")
            if status != "queued":
                return {
                    "ok": False, "status": status, "reason": "host_job_not_claimable",
                    "job": self._row_to_job(row),
                }
            if float(row["available_at"] or 0) > now:
                return {"ok": False, "status": "waiting", "reason": "host_job_not_ready"}
            changed = connection.execute(
                """
                UPDATE host_jobs
                SET status = 'running', attempts = attempts + 1, started_at = ?,
                    lease_until = ?, claim_token = ?, claimed_by = ?, updated_at = ?
                WHERE job_id = ? AND status = 'queued'
                """,
                (now, now + max(1.0, float(lease_seconds)), claim_token, worker, now, normalized_id),
            ).rowcount
            if changed != 1:
                return {"ok": False, "status": "conflict", "reason": "host_job_claim_conflict"}
            claimed = connection.execute("SELECT * FROM host_jobs WHERE job_id = ?", (normalized_id,)).fetchone()
        return {
            "ok": True,
            "status": "running",
            "reason": "host_job_claimed",
            "claim_token": claim_token,
            "job": self._row_to_job(claimed),
        }

    def pending_job_ids(self, *, capability_source: Any = "") -> list[str]:
        source = str(capability_source or "").strip()
        now = float(self._clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._release_expired_claims(connection, now=now)
            source_clause = " AND capability_source = ?" if source else ""
            arguments: tuple[Any, ...] = (now, source) if source else (now,)
            rows = connection.execute(
                f"""
                SELECT job_id FROM host_jobs
                WHERE status = 'queued' AND available_at <= ?{source_clause}
                ORDER BY sequence ASC
                """,
                arguments,
            ).fetchall()
        return [str(row["job_id"] or "") for row in rows if str(row["job_id"] or "")]

    def succeed(
        self,
        job_id: Any,
        *,
        claim_token: Any,
        result_summary: Any = "",
        artifacts: Any = (),
        result: Any = None,
    ) -> dict[str, Any]:
        artifacts_json = _json_artifacts(artifacts)
        if artifacts_json is None:
            return {"ok": False, "status": "invalid", "reason": "host_job_artifacts_not_json_safe"}
        return self._finish(
            job_id,
            claim_token=claim_token,
            status="succeeded",
            result_summary=str(result_summary or "")[:4_000],
            artifacts_json=artifacts_json,
            last_error="",
            result=result,
        )

    def fail(
        self,
        job_id: Any,
        *,
        claim_token: Any,
        error: Any,
        retryable: bool,
        retry_delay_seconds: float = 0.0,
        result_summary: Any = "",
        artifacts: Any = (),
        result: Any = None,
    ) -> dict[str, Any]:
        result_json = _json_object(result if result is not None else {}) if result is None or isinstance(result, dict) else None
        if result_json is None:
            return {"ok": False, "status": "invalid", "reason": "host_job_result_not_json_safe"}
        artifacts_json = _json_artifacts(artifacts)
        if artifacts_json is None:
            return {"ok": False, "status": "invalid", "reason": "host_job_artifacts_not_json_safe"}
        normalized_id = str(job_id or "").strip()
        token = str(claim_token or "").strip()
        if not normalized_id or not token:
            return {"ok": False, "status": "invalid", "reason": "job_id_and_claim_token_required"}
        now = float(self._clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT cancel_requested FROM host_jobs WHERE job_id = ? AND status = 'running' AND control_state != 'paused' AND claim_token = ?",
                (normalized_id, token),
            ).fetchone()
            if row is None:
                return {"ok": False, "status": "stale", "reason": "host_job_claim_not_owned"}
            # A cancellation request is not evidence that execution stopped.
            # Respect it by suppressing retries, not by fabricating cancellation.
            next_status = "queued" if retryable and not row["cancel_requested"] else "failed"
            finished_at = now if next_status in JOB_TERMINAL_STATUSES else 0.0
            connection.execute(
                """
                UPDATE host_jobs
                SET status = ?, available_at = ?, lease_until = 0, claim_token = '',
                    claimed_by = '', updated_at = ?, finished_at = ?, last_error = ?,
                    result_summary = ?, artifacts_json = ?, result_json = ?,
                    completion_status = CASE
                        WHEN ? = 'queued' THEN completion_status
                        WHEN completion_mode = 'silent' AND memory_mode = 'current_turn' THEN 'silent'
                        ELSE 'pending' END
                WHERE job_id = ? AND status = 'running' AND claim_token = ?
                """,
                (
                    next_status,
                    now + max(0.0, float(retry_delay_seconds)) if next_status == "queued" else now,
                    now,
                    finished_at,
                    str(error or "")[:500],
                    str(result_summary or "")[:4_000],
                    artifacts_json,
                    result_json,
                    next_status,
                    normalized_id,
                    token,
                ),
            )
        return {
            "ok": True,
            "status": next_status,
            "reason": "host_job_requeued" if next_status == "queued" else f"host_job_{next_status}",
        }

    def revoke_turn(self, *, owner: HostJobOwner, turn_token: str) -> tuple[HostJob, ...]:
        if not turn_token:
            return ()
        return self._revoke_scope(
            "profile_user_id = ? AND session_id = ? AND json_extract(payload_json, '$.parent_turn_token') = ?",
            (owner.profile_user_id, owner.session_id, turn_token), reason="parent_turn_stopped")

    def revoke_capabilities(self, capability_ids: tuple[str, ...]) -> tuple[HostJob, ...]:
        if not capability_ids:
            return ()
        return self._revoke_scope(
            "capability_source = 'tool' AND capability_id IN (" + ",".join("?" for _ in capability_ids) + ")",
            capability_ids, reason="plugin_capability_revoked")

    def revoke_job_capability(self, job_id: str, *, owner: HostJobOwner) -> tuple[HostJob, ...]:
        return self._revoke_scope(
            "job_id = ? AND profile_user_id = ? AND session_id = ? AND capability_source = 'tool'",
            (job_id, owner.profile_user_id, owner.session_id), reason="plugin_capability_revoked")

    def revoke_plugin_tasks(self, plugin_id: str, generation_id: str) -> tuple[HostJob, ...]:
        """Withdraw queued plugin tasks when their generation loses authority."""

        return self._revoke_scope(
            "capability_source = 'plugin_task'"
            " AND json_extract(payload_json, '$.plugin_id') = ?"
            " AND (json_extract(payload_json, '$.generation_id') = ? OR recovery_generation_id = ?)"
            " AND (recovery_generation_id = '' OR recovery_generation_id = ?)",
            (
                str(plugin_id or ""),
                str(generation_id or ""),
                str(generation_id or ""),
                str(generation_id or ""),
            ),
            reason="plugin_task_generation_revoked",
        )

    def _revoke_scope(self, predicate: str, arguments: tuple, *, reason: str) -> tuple[HostJob, ...]:
        """Withdraw future work without rewriting completed execution or its result."""
        now = float(self._clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute("SELECT job_id FROM host_jobs WHERE scope_revoked_reason = '' AND " + predicate,
                                      arguments).fetchall()
            if not rows:
                return ()
            ids = tuple(row["job_id"] for row in rows)
            connection.execute("""
                UPDATE host_jobs SET scope_revoked_reason = ?,
                    cancel_requested = CASE WHEN status IN ('queued', 'running') THEN 1 ELSE cancel_requested END,
                    finished_at = CASE WHEN status = 'queued' THEN ? ELSE finished_at END,
                    control_state = CASE WHEN status IN ('queued', 'paused') THEN 'stopped' ELSE control_state END,
                    completion_status = CASE WHEN status = 'queued' THEN
                        CASE WHEN completion_mode = 'silent' AND memory_mode = 'current_turn' THEN 'silent' ELSE 'pending' END
                        ELSE completion_status END,
                    status = CASE WHEN status IN ('queued', 'paused') THEN 'cancelled' ELSE status END, updated_at = ?
                WHERE scope_revoked_reason = '' AND """ + predicate, (reason, now, now, *arguments))
            return tuple(self._row_to_job(connection.execute("SELECT * FROM host_jobs WHERE job_id = ?", (job_id,)).fetchone())
                         for job_id in ids)

    def request_cancel(self, job_id: Any, *, owner: HostJobOwner) -> dict[str, Any]:
        normalized_id = str(job_id or "").strip()
        if not normalized_id or not isinstance(owner, HostJobOwner):
            return {"ok": False, "status": "invalid", "reason": "host_job_owner_required"}
        now = float(self._clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM host_jobs WHERE job_id = ? AND profile_user_id = ? AND session_id = ?",
                (normalized_id, owner.profile_user_id, owner.session_id),
            ).fetchone()
            if row is None:
                return {"ok": False, "status": "unknown", "reason": "host_job_not_found"}
            status = str(row["status"] or "")
            if status in JOB_TERMINAL_STATUSES:
                return {"ok": True, "status": status, "reason": "host_job_already_terminal"}
            if status == "queued":
                connection.execute(
                    """
                    UPDATE host_jobs
                    SET status = 'cancelled', cancel_requested = 1, updated_at = ?, finished_at = ?,
                        control_state = 'stopped',
                        completion_status = CASE
                            WHEN completion_mode = 'silent' AND memory_mode = 'current_turn' THEN 'silent' ELSE 'pending' END
                    WHERE job_id = ? AND status = 'queued'
                    """,
                    (now, now, normalized_id),
                )
                return {"ok": True, "status": "cancelled", "reason": "host_job_cancelled"}
            if status == "paused":
                connection.execute(
                    """
                    UPDATE host_jobs
                    SET status = 'cancelled', cancel_requested = 1, control_state = 'stopped',
                        updated_at = ?, finished_at = ?,
                        completion_status = CASE
                            WHEN completion_mode = 'silent' AND memory_mode = 'current_turn' THEN 'silent' ELSE 'pending' END
                    WHERE job_id = ? AND status = 'paused'
                    """,
                    (now, now, normalized_id),
                )
                return {"ok": True, "status": "cancelled", "reason": "host_job_cancelled"}
            connection.execute(
                "UPDATE host_jobs SET cancel_requested = 1, control_state = 'stopping', updated_at = ? WHERE job_id = ? AND status = 'running'",
                (now, normalized_id),
            )
        return {"ok": True, "status": "cancelling", "reason": "host_job_cancel_requested"}

    def control(self, job_id: Any, *, owner: HostJobOwner, action: Any, running_pause: bool = False) -> dict[str, Any]:
        """Apply a durable job control command without claiming completion."""

        normalized_id = str(job_id or "").strip()
        normalized_action = str(action or "").strip().lower()
        if not normalized_id or not isinstance(owner, HostJobOwner):
            return {"ok": False, "status": "invalid", "reason": "host_job_owner_required"}
        if normalized_action not in {"pause", "resume", "stop"}:
            return {"ok": False, "status": "invalid", "reason": "host_job_control_action_invalid"}
        if normalized_action == "stop":
            return self.request_cancel(normalized_id, owner=owner)
        now = float(self._clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, control_state, recovery_state FROM host_jobs WHERE job_id = ? AND profile_user_id = ? AND session_id = ?",
                (normalized_id, owner.profile_user_id, owner.session_id),
            ).fetchone()
            if row is None:
                return {"ok": False, "status": "unknown", "reason": "host_job_not_found"}
            status = str(row["status"] or "")
            control_state = str(row["control_state"] or "running")
            if status in JOB_TERMINAL_STATUSES:
                if normalized_action == "resume" and str(row["recovery_state"] or "") == "unknown":
                    connection.execute(
                        """
                        UPDATE host_jobs
                        SET status = 'queued', control_state = 'running', cancel_requested = 0,
                            lease_until = 0, claim_token = '', claimed_by = '', available_at = ?,
                            finished_at = 0, completion_status = 'waiting', recovery_state = '',
                            recovery_reason = '', updated_at = ?
                        WHERE job_id = ? AND profile_user_id = ? AND session_id = ?
                          AND status = 'failed' AND recovery_state = 'unknown'
                        """,
                        (now, now, normalized_id, owner.profile_user_id, owner.session_id),
                    )
                    return {"ok": True, "status": "queued", "reason": "host_job_recovery_queued"}
                return {"ok": True, "status": status, "reason": "host_job_already_terminal"}
            if normalized_action == "pause":
                if control_state == "paused":
                    return {"ok": True, "status": "paused", "reason": "host_job_already_paused"}
                if status == "queued":
                    connection.execute(
                        "UPDATE host_jobs SET status = 'paused', control_state = 'paused', updated_at = ? WHERE job_id = ? AND status = 'queued'",
                        (now, normalized_id),
                    )
                    return {"ok": True, "status": "paused", "reason": "host_job_paused"}
                if status == "running" and running_pause:
                    connection.execute(
                        "UPDATE host_jobs SET control_state = 'paused', updated_at = ? WHERE job_id = ? AND status = 'running'",
                        (now, normalized_id),
                    )
                    return {"ok": True, "status": "paused", "reason": "host_job_paused"}
                return {"ok": False, "status": "pause_unavailable", "reason": "host_job_running_pause_unsupported"}
            if status == "paused":
                connection.execute(
                    "UPDATE host_jobs SET status = 'queued', control_state = 'running', updated_at = ? WHERE job_id = ? AND status = 'paused'",
                    (now, normalized_id),
                )
                return {"ok": True, "status": "queued", "reason": "host_job_resumed"}
            if status == "running" and control_state == "paused" and running_pause:
                connection.execute(
                    "UPDATE host_jobs SET control_state = 'running', updated_at = ? WHERE job_id = ? AND status = 'running'",
                    (now, normalized_id),
                )
                return {"ok": True, "status": "running", "reason": "host_job_resumed"}
            if status == "running":
                return {"ok": False, "status": "resume_unavailable", "reason": "host_job_not_paused"}
            return {"ok": False, "status": status, "reason": "host_job_not_resumable"}

    def write_checkpoint(
        self,
        job_id: Any,
        *,
        owner: HostJobOwner,
        plugin_id: Any,
        generation_id: Any,
        value: Any,
        version: Any,
        character_pack_id: Any = None,
    ) -> dict[str, Any]:
        """Atomically persist one plugin-owned business checkpoint.

        Checkpoints are deliberately separate from host lifecycle state.  The
        host validates scope and generation ownership, while the plugin owns
        the JSON value and its monotonically increasing business version.
        """

        normalized_id = str(job_id or "").strip()
        normalized_plugin = str(plugin_id or "").strip()
        normalized_generation = str(generation_id or "").strip()
        if not normalized_id or not isinstance(owner, HostJobOwner):
            return {"ok": False, "status": "invalid", "reason": "host_job_owner_required"}
        if not normalized_plugin or not normalized_generation:
            return {"ok": False, "status": "invalid", "reason": "task_checkpoint_identity_required"}
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            return {"ok": False, "status": "invalid", "reason": "task_checkpoint_version_invalid"}
        encoded = _json_value(value)
        if encoded is None:
            return {"ok": False, "status": "invalid", "reason": "task_checkpoint_not_json_safe"}
        fingerprint = _sha256_text(encoded)
        now = float(self._clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM host_jobs WHERE job_id = ? AND profile_user_id = ? AND session_id = ?",
                (normalized_id, owner.profile_user_id, owner.session_id),
            ).fetchone()
            if row is None:
                return {"ok": False, "status": "unknown", "reason": "host_job_not_found"}
            payload = json.loads(str(row["payload_json"] or "{}"))
            if str(row["capability_source"] or "") != "plugin_task" or not isinstance(payload, dict):
                return {"ok": False, "status": "rejected", "reason": "task_checkpoint_access_denied"}
            if character_pack_id is not None and str(row["character_pack_id"] or "") != str(character_pack_id or "").strip():
                return {"ok": False, "status": "rejected", "reason": "task_checkpoint_access_denied"}
            if str(payload.get("plugin_id") or "") != normalized_plugin:
                return {"ok": False, "status": "rejected", "reason": "task_checkpoint_access_denied"}
            effective_generation = str(row["recovery_generation_id"] or "") or str(payload.get("generation_id") or "")
            if effective_generation != normalized_generation:
                return {"ok": False, "status": "stale", "reason": "task_generation_expired"}
            if str(row["scope_revoked_reason"] or ""):
                return {"ok": False, "status": "stale", "reason": "task_generation_expired"}
            if str(row["status"] or "") in JOB_TERMINAL_STATUSES:
                return {"ok": False, "status": "rejected", "reason": "task_checkpoint_terminal"}
            current_version = int(row["checkpoint_version"] or 0)
            current_json = str(row["checkpoint_json"] or "null")
            if version == current_version:
                if current_json == encoded:
                    return {"ok": True, "status": "duplicate", "reason": "task_checkpoint_already_saved",
                            "job": self._row_to_job(row)}
                return {"ok": False, "status": "conflict", "reason": "task_checkpoint_version_conflict"}
            if version < current_version:
                return {"ok": False, "status": "stale", "reason": "task_checkpoint_version_stale"}
            changed = connection.execute(
                """
                UPDATE host_jobs
                SET checkpoint_json = ?, checkpoint_version = ?, checkpoint_fingerprint = ?,
                    checkpoint_generation_id = ?, checkpoint_updated_at = ?, updated_at = ?
                WHERE job_id = ? AND profile_user_id = ? AND session_id = ?
                  AND checkpoint_version < ? AND scope_revoked_reason = ''
                """,
                (
                    encoded, version, fingerprint, normalized_generation, now, now, normalized_id,
                    owner.profile_user_id, owner.session_id, version,
                ),
            ).rowcount
            if changed != 1:
                return {"ok": False, "status": "conflict", "reason": "task_checkpoint_write_race"}
            saved = connection.execute("SELECT * FROM host_jobs WHERE job_id = ?", (normalized_id,)).fetchone()
        return {"ok": True, "status": "saved", "reason": "task_checkpoint_saved", "job": self._row_to_job(saved)}

    def adopt_recovery(
        self,
        job_id: Any,
        *,
        owner: HostJobOwner,
        plugin_id: Any,
        generation_id: Any,
        character_pack_id: Any = None,
    ) -> dict[str, Any]:
        """Explicitly bind a recovery-unknown task to the current generation."""

        normalized_id = str(job_id or "").strip()
        normalized_plugin = str(plugin_id or "").strip()
        normalized_generation = str(generation_id or "").strip()
        if not normalized_id or not isinstance(owner, HostJobOwner):
            return {"ok": False, "status": "invalid", "reason": "host_job_owner_required"}
        if not normalized_plugin or not normalized_generation:
            return {"ok": False, "status": "invalid", "reason": "task_recovery_identity_required"}
        now = float(self._clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM host_jobs WHERE job_id = ? AND profile_user_id = ? AND session_id = ?",
                (normalized_id, owner.profile_user_id, owner.session_id),
            ).fetchone()
            if row is None:
                return {"ok": False, "status": "unknown", "reason": "host_job_not_found"}
            payload = json.loads(str(row["payload_json"] or "{}"))
            if (
                str(row["capability_source"] or "") != "plugin_task"
                or not isinstance(payload, dict)
                or str(payload.get("plugin_id") or "") != normalized_plugin
            ):
                return {"ok": False, "status": "rejected", "reason": "task_recovery_access_denied"}
            if character_pack_id is not None and str(row["character_pack_id"] or "") != str(character_pack_id or "").strip():
                return {"ok": False, "status": "rejected", "reason": "task_recovery_access_denied"}
            if str(row["scope_revoked_reason"] or ""):
                return {"ok": False, "status": "stale", "reason": "task_generation_expired"}
            previous = str(row["recovery_generation_id"] or "")
            if previous and previous != normalized_generation:
                # Another generation already owns a recovery round. Refuse the
                # takeover as a conflict before the round-state test below, so a
                # caller is never told "not available" when the real answer is
                # "someone else owns this round and has not lost it".
                if (
                    int(row["recovery_owner_round"] or 0) >= int(row["recovery_epoch"] or 0)
                    and str(row["claim_token"] or "")
                ):
                    return {"ok": False, "status": "conflict", "reason": "task_recovery_already_adopted"}
            if str(row["recovery_state"] or "") != "unknown":
                return {"ok": False, "status": str(row["status"] or ""), "reason": "task_recovery_not_available"}
            previous_round = int(row["recovery_owner_round"] or 0)
            recovery_epoch = int(row["recovery_epoch"] or 0)
            # ``recovery_epoch`` counts real lost claims only. The round granted
            # with it is the loss round itself; a handoff past it is reserved for
            # exactly one owner that never re-claimed the work. Two facts are
            # therefore enough to keep recovery ownership single-valued and
            # terminating: a live claim always keeps its round, and a handoff can
            # only be granted when the current owner holds no claim and no handoff
            # has already been taken from this epoch.
            unclaimed_round = (
                bool(previous) and previous != normalized_generation and not str(row["claim_token"] or "")
            )
            if previous and previous_round > recovery_epoch:
                # This round was already handed over once; the next owner needs a
                # real loss before it can take the round.
                return {"ok": False, "status": "conflict", "reason": "task_recovery_already_adopted"}
            if previous and previous_round == recovery_epoch and not unclaimed_round:
                # The owner holds a live claim, or already started work in this
                # round: taking it over would preempt real execution.
                return {"ok": False, "status": "conflict", "reason": "task_recovery_already_adopted"}
            if unclaimed_round and previous_round == recovery_epoch:
                next_round = recovery_epoch + 1
                adopted_reason = "task_recovery_round_handed_over"
            else:
                next_round = recovery_epoch
                adopted_reason = "task_recovery_adopted"
            connection.execute(
                """
                UPDATE host_jobs
                SET recovery_generation_id = ?, recovery_owner_round = ?, recovery_generation_epoch = ?,
                    recovery_reason = ?, updated_at = ?
                WHERE job_id = ? AND profile_user_id = ? AND session_id = ?
                  AND recovery_state = 'unknown'
                """,
                (
                    normalized_generation,
                    next_round,
                    next_round,
                    adopted_reason,
                    now,
                    normalized_id,
                    owner.profile_user_id,
                    owner.session_id,
                ),
            )
            adopted = connection.execute("SELECT * FROM host_jobs WHERE job_id = ?", (normalized_id,)).fetchone()
        return {
            "ok": True,
            "status": "recovery_unknown",
            "reason": adopted_reason,
            "job": self._row_to_job(adopted),
        }

    def confirm_cancelled(self, job_id: Any, *, claim_token: Any, result_summary: Any = "", artifacts: Any = (), result: Any = None) -> dict[str, Any]:
        artifacts_json = _json_artifacts(artifacts)
        if artifacts_json is None:
            return {"ok": False, "status": "invalid", "reason": "host_job_artifacts_not_json_safe"}
        return self._finish(
            job_id,
            claim_token=claim_token,
            status="cancelled",
            result_summary=str(result_summary or "")[:4_000],
            artifacts_json=artifacts_json,
            last_error="cancelled",
            result=result,
            allowed_control_states=("running", "stopping"),
        )

    def arm_agent_completion(self, job_id: Any, *, owner: HostJobOwner) -> dict[str, Any]:
        """Promote a provisionally silent Job after its caller observes async work.

        Shell commands are persisted before process spawn, but short commands
        still finish inside the initiating tool call and must not produce a
        duplicate Agent reply. The execution bridge calls this only after
        ``exec_run`` has returned ``running``. If the process wins that small
        race and has already ended, its silent terminal fact becomes pending
        atomically instead of being lost.
        """

        normalized_id = str(job_id or "").strip()
        if not normalized_id or not isinstance(owner, HostJobOwner):
            return {"ok": False, "status": "invalid", "reason": "host_job_owner_required"}
        now = float(self._clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT status, completion_mode, completion_status
                FROM host_jobs
                WHERE job_id = ? AND profile_user_id = ? AND session_id = ?
                """,
                (normalized_id, owner.profile_user_id, owner.session_id),
            ).fetchone()
            if row is None:
                return {"ok": False, "status": "unknown", "reason": "host_job_not_found"}
            status = str(row["status"] or "")
            completion_mode = str(row["completion_mode"] or "")
            completion_status = str(row["completion_status"] or "")
            if status not in {"running", *JOB_TERMINAL_STATUSES}:
                return {"ok": False, "status": status, "reason": "host_job_not_started"}
            if completion_mode == "agent":
                return {"ok": True, "status": status, "reason": "host_job_completion_already_armed"}
            if completion_mode != "silent":
                return {"ok": False, "status": status, "reason": "host_job_completion_mode_conflict"}
            next_completion_status = (
                "pending"
                if status in JOB_TERMINAL_STATUSES and completion_status == "silent"
                else completion_status
            )
            connection.execute(
                """
                UPDATE host_jobs
                SET completion_mode = 'agent', memory_mode = 'timeline', completion_status = ?, updated_at = ?
                WHERE job_id = ? AND profile_user_id = ? AND session_id = ?
                  AND completion_mode = 'silent'
                """,
                (
                    next_completion_status,
                    now,
                    normalized_id,
                    owner.profile_user_id,
                    owner.session_id,
                ),
            )
        return {"ok": True, "status": status, "reason": "host_job_completion_armed"}

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

    def recover_abandoned_claims(self) -> int:
        """Fence lost workers without replaying unconfirmed external effects."""
        now = float(self._clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._fail_lost_claims(connection, now=now, expired_only=False)

    def pending_completions(self, *, capability_source: Any = "") -> list[HostJob]:
        source = str(capability_source or "").strip()
        source_clause = " AND capability_source = ?" if source else ""
        arguments: tuple[Any, ...] = (source,) if source else ()
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM host_jobs
                WHERE status IN ('succeeded', 'failed', 'cancelled')
                  AND completion_status = 'pending'{source_clause}
                ORDER BY sequence ASC
                """,
                arguments,
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

    def mark_completion_delivered(self, job_id: Any, *, completion_event_id: Any) -> dict[str, Any]:
        normalized_id = str(job_id or "").strip()
        event_id = str(completion_event_id or "").strip()
        if not normalized_id or not event_id:
            return {"ok": False, "status": "invalid", "reason": "completion_identity_required"}
        now = float(self._clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT completion_status, completion_event_id FROM host_jobs WHERE job_id = ?",
                (normalized_id,),
            ).fetchone()
            if row is None:
                return {"ok": False, "status": "unknown", "reason": "host_job_not_found"}
            if str(row["completion_event_id"] or "") != event_id:
                return {"ok": False, "status": "rejected", "reason": "completion_event_mismatch"}
            status = str(row["completion_status"] or "")
            if status == "delivered":
                return {"ok": True, "status": "delivered", "reason": "completion_already_delivered"}
            if status != "pending":
                return {"ok": False, "status": status, "reason": "completion_not_pending"}
            connection.execute(
                """
                UPDATE host_jobs
                SET completion_status = 'delivered', completion_last_error = '', updated_at = ?
                WHERE job_id = ? AND completion_status = 'pending'
                """,
                (now, normalized_id),
            )
        return {"ok": True, "status": "delivered", "reason": "completion_delivered"}

    def record_delivery_receipt(self, job_id: str, *, owner: HostJobOwner, completion_event_id: str,
                                receipt: dict[str, Any], admission_only: bool = False) -> dict[str, Any]:
        encoded = _json_object(receipt) if isinstance(receipt, dict) else None
        if encoded is None:
            return {"ok": False, "reason": "host_job_delivery_receipt_invalid"}
        with self._connect() as connection:
            changed = connection.execute("""
                UPDATE host_jobs SET delivery_receipt_json = CASE
                    WHEN ? AND json_extract(delivery_receipt_json, '$.stage') = 'delivery'
                    THEN delivery_receipt_json ELSE ? END,
                    updated_at = ?
                WHERE job_id = ? AND profile_user_id = ? AND session_id = ? AND completion_event_id = ?
                  AND status IN ('succeeded', 'failed', 'cancelled')
                """, (admission_only, encoded, float(self._clock()), job_id, owner.profile_user_id, owner.session_id,
                      completion_event_id)).rowcount
        return {"ok": changed == 1, "reason": "" if changed == 1 else "host_job_completion_identity_mismatch"}

    def record_completion_failure(self, job_id: Any, *, error: Any) -> dict[str, Any]:
        normalized_id = str(job_id or "").strip()
        if not normalized_id:
            return {"ok": False, "status": "invalid", "reason": "job_id_required"}
        now = float(self._clock())
        with self._connect() as connection:
            changed = connection.execute(
                """
                UPDATE host_jobs
                SET completion_attempts = completion_attempts + 1,
                    completion_last_error = ?, updated_at = ?
                WHERE job_id = ? AND completion_status = 'pending'
                """,
                (str(error or "completion_delivery_failed")[:500], now, normalized_id),
            ).rowcount
        if changed != 1:
            return {"ok": False, "status": "stale", "reason": "completion_not_pending"}
        return {"ok": True, "status": "pending", "reason": "completion_failure_recorded"}

    def _finish(
        self,
        job_id: Any,
        *,
        claim_token: Any,
        status: str,
        result_summary: str,
        artifacts_json: str,
        last_error: str,
        result: Any = None,
        allowed_control_states: tuple[str, ...] = ("running", "stopping"),
    ) -> dict[str, Any]:
        result_json = _json_object(result if result is not None else {}) if result is None or isinstance(result, dict) else None
        if result_json is None:
            return {"ok": False, "status": "invalid", "reason": "host_job_result_not_json_safe"}
        normalized_id = str(job_id or "").strip()
        token = str(claim_token or "").strip()
        if not normalized_id or not token or status not in JOB_TERMINAL_STATUSES:
            return {"ok": False, "status": "invalid", "reason": "host_job_finish_invalid"}
        now = float(self._clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            control_clause = ",".join("?" for _ in allowed_control_states)
            changed = connection.execute(
                f"""
                UPDATE host_jobs
                SET status = ?, lease_until = 0, claim_token = '', claimed_by = '',
                    control_state = ?,
                    updated_at = ?, finished_at = ?, result_summary = ?, artifacts_json = ?, last_error = ?, result_json = ?,
                    completion_status = CASE
                        WHEN completion_mode = 'silent' AND memory_mode = 'current_turn' THEN 'silent' ELSE 'pending' END
                WHERE job_id = ? AND status = 'running'
                  AND control_state IN ({control_clause}) AND claim_token = ?
                """,
                (
                    status,
                    "stopped",
                    now,
                    now,
                    result_summary,
                    artifacts_json,
                    last_error,
                    result_json,
                    normalized_id,
                    *allowed_control_states,
                    token,
                ),
            ).rowcount
        if changed != 1:
            return {"ok": False, "status": "stale", "reason": "host_job_claim_not_owned"}
        return {"ok": True, "status": status, "reason": f"host_job_{status}"}

    @staticmethod
    def _immutable_match(job: HostJob, immutable: dict[str, str]) -> bool:
        return (
            job.owner.profile_user_id == immutable["profile_user_id"]
            and job.owner.session_id == immutable["session_id"]
            and job.character_pack_id == immutable["character_pack_id"]
            and job.channel == immutable["channel"]
            and job.delivery_target == immutable["delivery_target"]
            and job.capability_source == immutable["capability_source"]
            and job.capability_id == immutable["capability_id"]
            and job.turn_id == immutable["turn_id"]
            and job.tool_call_id == immutable["tool_call_id"]
            and job.idempotency_key == immutable["idempotency_key"]
            and job.argument_fingerprint == immutable["argument_fingerprint"]
            and _json_object(job.payload) == immutable["payload_json"]
            and job.completion_mode == immutable["completion_mode"]
            and job.memory_mode == immutable["memory_mode"]
        )

    @staticmethod
    def _release_expired_claims(connection: sqlite3.Connection, *, now: float) -> int:
        return HostJobStore._fail_lost_claims(connection, now=now, expired_only=True)

    @staticmethod
    def _fail_lost_claims(
        connection: sqlite3.Connection, *, now: float, expired_only: bool,
    ) -> int:
        # Neither a dead worker nor an expired lease proves that an external
        # operation failed or stopped. Only explicit worker-confirmed failures
        # may use fail(retryable=True); lost claims must never auto-replay.
        reason = "lease_expired_outcome_unknown" if expired_only else "host_restart_outcome_unknown"
        condition = " AND lease_until <= ?" if expired_only else ""
        arguments = (now, now, reason, reason, now) if expired_only else (now, now, reason, reason)
        count = connection.execute(
            f"""
            UPDATE host_jobs
            SET status = 'failed', lease_until = 0, claim_token = '', claimed_by = '',
                control_state = 'stopped',
                recovery_epoch = recovery_epoch + 1,
                updated_at = ?, finished_at = ?, last_error = ?,
                recovery_state = 'unknown', recovery_reason = ?,
                result_summary = 'Execution outcome is unknown; external work may still have taken effect. Not automatically retried.',
                completion_status = CASE
                    WHEN completion_mode = 'silent' AND memory_mode = 'current_turn' THEN 'silent' ELSE 'pending' END
            WHERE status = 'running'{condition}
            """,
            arguments,
        ).rowcount
        return int(count or 0)

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
        )


def _json_object(value: dict[str, Any]) -> str | None:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        return None


def _json_value(value: Any) -> str | None:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        return None


def _json_value_load(value: Any) -> Any:
    try:
        return json.loads(str(value if value is not None else "null"))
    except (TypeError, ValueError):
        return None


def _sha256_text(value: str) -> str:
    import hashlib
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_artifacts(value: Any) -> str | None:
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, dict) for item in value):
        return None
    try:
        return json.dumps(list(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError):
        return None


__all__ = ["HostJob", "HostJobOwner", "HostJobStore", "JOB_TERMINAL_STATUSES", "JOB_CONTROL_STATES"]
