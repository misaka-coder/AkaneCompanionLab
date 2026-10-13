"""Persist idempotent jobs and claim ready work in the shared transaction."""
from __future__ import annotations

import uuid
from typing import Any

from .models import HostJob, HostJobOwner, _COMPLETION_MODES, _MEMORY_MODES, _json_object



class JobAdmissionMixin:
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
            agent_id = payload.get("child_session_id") if source == "subagent" else None
            if agent_id:
                prior = connection.execute("SELECT * FROM host_jobs WHERE capability_source = 'subagent' "
                    "AND profile_user_id = ? AND session_id = ? AND json_extract(payload_json, '$.child_session_id') = ? "
                    "ORDER BY sequence DESC LIMIT 1", (owner.profile_user_id, owner.session_id, agent_id)).fetchone()
                if prior is not None:
                    if prior["subagent_deleted_at"] or prior["subagent_deleting"]:
                        return {"ok": False, "reason": "subagent_deleted"}
                    if prior["status"] in {"queued", "running", "paused"}:
                        return {"ok": False, "reason": "subagent_already_running"}
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
