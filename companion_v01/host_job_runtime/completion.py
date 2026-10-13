"""Arm completion notifications and persist delivery outcomes by event identity."""
from __future__ import annotations

from typing import Any

from .models import HostJob, HostJobOwner, JOB_TERMINAL_STATUSES, _json_object



class JobCompletionMixin:
    def consume_model_completion(self, job: HostJob, *, consumed_by: str,
                                 terminal_observed: bool = False) -> dict[str, Any]:
        """Acknowledge a terminal model input without claiming a user send.

        Output pages and completion admission remain intact. An already queued
        envelope checks this same receipt before requesting another model turn.
        A verified Shell query can precede the terminal observer's database
        write; its receipt may be saved without changing that execution state.
        """
        return self.consume_model_completions([job], consumed_by=consumed_by,
            terminal_observed_ids={job.job_id} if terminal_observed and isinstance(job, HostJob) else ())

    def consume_model_completions(self, jobs, *, consumed_by: str,
                                  terminal_observed_ids=(), mark_delivered=False) -> dict[str, Any]:
        """Commit one frozen model input atomically, with one durable write."""
        if not str(consumed_by or "").strip() or not all(isinstance(job, HostJob) for job in jobs):
            return {"ok": False, "reason": "model_completion_identity_required"}
        observed = set(terminal_observed_ids)
        now = float(self._clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for job in jobs:
                changed = connection.execute(
                """UPDATE host_jobs SET model_consumed_by = CASE
                       WHEN model_consumed_by = '' THEN ? ELSE model_consumed_by END, updated_at = ?,
                       completion_status = CASE WHEN ? AND completion_status = 'pending'
                           THEN 'delivered' ELSE completion_status END,
                       completion_last_error = CASE WHEN ? AND completion_status = 'pending'
                           THEN '' ELSE completion_last_error END
                   WHERE job_id = ? AND profile_user_id = ? AND session_id = ?
                     AND character_pack_id = ? AND channel = ? AND completion_event_id = ?
                     AND (status IN ('succeeded', 'failed', 'cancelled') OR
                          (? AND capability_source = 'execution' AND capability_id = 'exec_run' AND status = 'running'))""",
                (consumed_by, now, mark_delivered, mark_delivered, job.job_id, job.owner.profile_user_id,
                 job.owner.session_id, job.character_pack_id, job.channel, job.completion_event_id, job.job_id in observed),
                ).rowcount
                if changed != 1:
                    connection.rollback()
                    return {"ok": False, "reason": "model_completion_identity_mismatch"}
        return {"ok": True, "reason": ""}

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
