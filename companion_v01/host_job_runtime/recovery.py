"""Persist plugin checkpoints and fence or explicitly adopt lost claims."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .models import HostJobOwner, JOB_TERMINAL_STATUSES, _json_value, _sha256_text



class JobRecoveryMixin:
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

    def recover_abandoned_claims(self) -> int:
        """Fence lost workers without replaying unconfirmed external effects."""
        now = float(self._clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._fail_lost_claims(connection, now=now, expired_only=False)

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
