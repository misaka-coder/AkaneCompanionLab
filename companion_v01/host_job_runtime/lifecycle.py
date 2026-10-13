"""Owner-scoped control, capability withdrawal and worker-confirmed results."""
from __future__ import annotations

from typing import Any

from .models import HostJob, HostJobOwner, JOB_TERMINAL_STATUSES, _json_artifacts, _json_object



class JobLifecycleMixin:
    def renew_claim(self, job_id: str, *, claim_token: str, lease_seconds: float = 300) -> bool:
        now = float(self._clock())
        with self._connect() as connection:
            return connection.execute("UPDATE host_jobs SET lease_until = ? WHERE job_id = ? "
                "AND status = 'running' AND claim_token = ? AND lease_until > ?",
                (now + lease_seconds, job_id, claim_token, now)).rowcount == 1

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
            result_summary=str(result_summary or ""),
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
                """
                SELECT cancel_requested FROM host_jobs
                WHERE job_id = ? AND status = 'running' AND control_state != 'paused' AND claim_token = ?
                  AND NOT (capability_source = 'plugin_task' AND scope_revoked_reason != '')
                """,
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
                  AND NOT (capability_source = 'plugin_task' AND scope_revoked_reason != '')
                """,
                (
                    next_status,
                    now + max(0.0, float(retry_delay_seconds)) if next_status == "queued" else now,
                    now,
                    finished_at,
                    str(error or "")[:500],
                    str(result_summary or ""),
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
                    cancel_requested = CASE WHEN status IN ('queued', 'paused', 'running') THEN 1 ELSE cancel_requested END,
                    finished_at = CASE WHEN status IN ('queued', 'paused') THEN ? ELSE finished_at END,
                    control_state = CASE WHEN status IN ('queued', 'paused') THEN 'stopped' ELSE control_state END,
                    completion_status = CASE WHEN status IN ('queued', 'paused') THEN
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
                """
                SELECT status, control_state, recovery_state, cancel_requested, scope_revoked_reason
                FROM host_jobs WHERE job_id = ? AND profile_user_id = ? AND session_id = ?
                """,
                (normalized_id, owner.profile_user_id, owner.session_id),
            ).fetchone()
            if row is None:
                return {"ok": False, "status": "unknown", "reason": "host_job_not_found"}
            status = str(row["status"] or "")
            control_state = str(row["control_state"] or "running")
            if status in JOB_TERMINAL_STATUSES:
                if normalized_action == "resume" and str(row["recovery_state"] or "") == "unknown":
                    if str(row["scope_revoked_reason"] or ""):
                        return {"ok": False, "status": "rejected", "reason": "host_job_scope_revoked"}
                    connection.execute(
                        """
                        UPDATE host_jobs
                        SET status = 'queued', control_state = 'running', cancel_requested = 0,
                            lease_until = 0, claim_token = '', claimed_by = '', available_at = ?,
                            finished_at = 0, completion_status = 'waiting', model_consumed_by = '', recovery_state = '',
                            recovery_reason = '', updated_at = ?
                        WHERE job_id = ? AND profile_user_id = ? AND session_id = ?
                          AND status = 'failed' AND recovery_state = 'unknown'
                        """,
                        (now, now, normalized_id, owner.profile_user_id, owner.session_id),
                    )
                    return {"ok": True, "status": "queued", "reason": "host_job_recovery_queued"}
                return {"ok": True, "status": status, "reason": "host_job_already_terminal"}
            # A cooperative pause must not supersede a durable stop request.
            if status == "running" and (bool(row["cancel_requested"]) or control_state in {"stopping", "stopped"}):
                return {"ok": False, "status": "cancelling", "reason": "host_job_cancel_requested"}
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

    def confirm_cancelled(self, job_id: Any, *, claim_token: Any, result_summary: Any = "", artifacts: Any = (), result: Any = None) -> dict[str, Any]:
        artifacts_json = _json_artifacts(artifacts)
        if artifacts_json is None:
            return {"ok": False, "status": "invalid", "reason": "host_job_artifacts_not_json_safe"}
        return self._finish(
            job_id,
            claim_token=claim_token,
            status="cancelled",
            result_summary=str(result_summary or ""),
            artifacts_json=artifacts_json,
            last_error="cancelled",
            result=result,
            allowed_control_states=("running", "stopping"),
        )

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
                  AND NOT (capability_source = 'plugin_task' AND scope_revoked_reason != '')
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
