"""Child identity, input mailboxes and deletion markers in the Host Job store."""
from __future__ import annotations

import json
from typing import Any

from .models import HostJobOwner


class SubagentRecordsMixin:
    def subagent_input_receipt(self, agent_id: str, source_id: str, *, owner: HostJobOwner):
        with self._connect() as connection:
            row = connection.execute("SELECT host_jobs.job_id, input.value FROM host_jobs, "
                "json_each(host_jobs.checkpoint_json, '$.inputs') AS input "
                "WHERE capability_source = 'subagent' AND profile_user_id = ? AND session_id = ? "
                "AND json_extract(payload_json, '$.child_session_id') = ? "
                "AND json_extract(input.value, '$.source_id') = ? ORDER BY sequence DESC LIMIT 1",
                (owner.profile_user_id, owner.session_id, agent_id, source_id)).fetchone()
        return {"job_id": row["job_id"], **json.loads(row["value"])} if row is not None else None

    def subagent_delete_ids(self, agent_id: str, *, owner: HostJobOwner, source_ids=None):
        with self._connect() as connection:
            if source_ids is not None:
                connection.execute("UPDATE host_jobs SET subagent_delete_ids_json = ? WHERE capability_source = 'subagent' "
                    "AND profile_user_id = ? AND session_id = ? AND json_extract(payload_json, '$.child_session_id') = ? "
                    "AND subagent_deleting = 1", (json.dumps(source_ids), owner.profile_user_id, owner.session_id, agent_id))
                return source_ids
            row = connection.execute("SELECT subagent_delete_ids_json FROM host_jobs WHERE capability_source = 'subagent' "
                "AND profile_user_id = ? AND session_id = ? AND json_extract(payload_json, '$.child_session_id') = ? "
                "ORDER BY sequence DESC LIMIT 1", (owner.profile_user_id, owner.session_id, agent_id)).fetchone()
        return json.loads(row[0]) if row else []

    def subagent_record(self, agent_id: str, *, owner: HostJobOwner, character_pack_id: str = "",
                        channel: str = "", actor_id: str = "") -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM host_jobs WHERE capability_source = 'subagent' "
                "AND profile_user_id = ? AND session_id = ? AND character_pack_id = ? AND channel = ? "
                "AND json_extract(payload_json, '$.child_session_id') = ? ORDER BY sequence DESC LIMIT 1",
                (owner.profile_user_id, owner.session_id, character_pack_id, channel, agent_id)).fetchone()
        if row is None:
            return None
        job = self._row_to_job(row)
        if actor_id and job.payload.get("execution_context", {}).get("actor_stable_id", "") != actor_id:
            return None
        return {"job": job, "deleted": bool(row["subagent_deleted_at"]), "deleting": bool(row["subagent_deleting"])}

    def list_subagents(self, *, owner: HostJobOwner, character_pack_id: str = "", channel: str = "",
                       actor_id: str = "", cursor: int = 0, limit: int = 20) -> dict[str, Any]:
        limit = max(1, min(50, int(limit)))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM host_jobs WHERE sequence IN (SELECT MAX(sequence) FROM host_jobs "
                "WHERE capability_source = 'subagent' AND profile_user_id = ? AND session_id = ? "
                "AND character_pack_id = ? AND channel = ? GROUP BY json_extract(payload_json, '$.child_session_id')) "
                "AND subagent_deleted_at = 0 AND (? = 0 OR sequence < ?) "
                "AND (? = '' OR json_extract(payload_json, '$.execution_context.actor_stable_id') = ?) "
                "ORDER BY sequence DESC LIMIT ?",
                (owner.profile_user_id, owner.session_id, character_pack_id, channel, cursor, cursor,
                 actor_id, actor_id, limit + 1)).fetchall()
        agents, used_bytes, last_sequence = [], 0, 0
        for row in rows[:limit]:
            job = self._row_to_job(row)
            item = {"agent_id": job.payload.get("child_session_id"),
                "job_id": job.job_id, "label": job.payload.get("label", ""), "status": job.status,
                "control_state": job.control_state, "model": job.payload.get("model", ""),
                "summary": job.result_summary[:300]}
            if row["subagent_deleting"]:
                item["status"] = "deleting"
            size = len(json.dumps(item, ensure_ascii=False).encode("utf-8")) + 2
            if agents and used_bytes + size > 60000:
                break
            agents.append(item)
            used_bytes += size
            last_sequence = job.sequence
        return {"ok": True, "agents": agents,
                "next_cursor": last_sequence if len(rows) > len(agents) else None}

    def append_subagent_input(self, job_id: str, *, owner: HostJobOwner, source_id: str, task: str) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM host_jobs WHERE job_id = ? AND profile_user_id = ? AND session_id = ? "
                "AND capability_source = 'subagent'", (job_id, owner.profile_user_id, owner.session_id)).fetchone()
            if row is None or row["subagent_deleted_at"] or row["subagent_deleting"]:
                return {"ok": False, "reason": "subagent_not_found"}
            if row["status"] not in {"queued", "paused", "running"}:
                return {"ok": False, "reason": "subagent_execution_finished"}
            checkpoint = json.loads(row["checkpoint_json"] or "null") or {}
            inputs = checkpoint.setdefault("inputs", [])
            duplicate = next((item for item in inputs if item["source_id"] == source_id), None)
            if duplicate and duplicate["task"] != task:
                return {"ok": False, "reason": "subagent_input_idempotency_collision"}
            if not duplicate and checkpoint.get("closed_to_input"):
                return {"ok": False, "reason": "subagent_execution_finishing"}
            if not duplicate:
                inputs.append({"source_id": source_id, "task": task, "consumed": False})
                connection.execute("UPDATE host_jobs SET checkpoint_json = ?, updated_at = ? WHERE job_id = ?",
                    (json.dumps(checkpoint, ensure_ascii=False), self._clock(), job_id))
            return {"ok": True, "duplicate": bool(duplicate)}

    def seal_subagent_inputs(self, job_id: str, *, owner: HostJobOwner) -> bool:
        """Atomically close admission after every accepted input was persisted."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT checkpoint_json FROM host_jobs WHERE job_id = ? AND profile_user_id = ? "
                "AND session_id = ? AND capability_source = 'subagent'", (job_id, owner.profile_user_id, owner.session_id)).fetchone()
            if row is None:
                return True
            checkpoint = json.loads(row["checkpoint_json"] or "null") or {}
            if any(not item.get("consumed") for item in checkpoint.get("inputs", [])):
                return False
            checkpoint["closed_to_input"] = True
            connection.execute("UPDATE host_jobs SET checkpoint_json = ? WHERE job_id = ?",
                               (json.dumps(checkpoint, ensure_ascii=False), job_id))
            return True

    def subagent_inputs(self, job_id: str, *, owner: HostJobOwner) -> list[dict[str, Any]]:
        job = self.get(job_id, owner=owner)
        if job is None or job.capability_source != "subagent":
            return []
        return [item for item in (job.checkpoint or {}).get("inputs", []) if not item.get("consumed")]

    def pending_subagent_inputs(self, job_id: str, *, owner: HostJobOwner) -> list[dict[str, Any]]:
        job = self.get(job_id, owner=owner)
        if job is None or job.capability_source != "subagent":
            return []
        with self._connect() as connection:
            rows = connection.execute("SELECT host_jobs.job_id, input.value FROM host_jobs, "
                "json_each(host_jobs.checkpoint_json, '$.inputs') AS input "
                "WHERE capability_source = 'subagent' AND profile_user_id = ? AND session_id = ? "
                "AND json_extract(payload_json, '$.child_session_id') = ? "
                "AND json_extract(input.value, '$.consumed') = 0 ORDER BY sequence ASC, CAST(input.key AS INTEGER) ASC",
                (owner.profile_user_id, owner.session_id, job.payload["child_session_id"])).fetchall()
        return [{"job_id": row["job_id"], **json.loads(row["value"])} for row in rows]

    def close_subagent_inputs(self, job_id: str, *, owner: HostJobOwner) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE host_jobs SET checkpoint_json = json_set(COALESCE(NULLIF(checkpoint_json, 'null'), '{}'), "
                "'$.closed_to_input', json('true')) WHERE job_id = ? AND profile_user_id = ? AND session_id = ? "
                "AND capability_source = 'subagent'", (job_id, owner.profile_user_id, owner.session_id))

    def acknowledge_subagent_inputs(self, job_id: str, *, owner: HostJobOwner, source_ids: list[str]) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT checkpoint_json FROM host_jobs WHERE job_id = ? AND profile_user_id = ? "
                "AND session_id = ? AND capability_source = 'subagent'", (job_id, owner.profile_user_id, owner.session_id)).fetchone()
            if row is None:
                return
            checkpoint = json.loads(row["checkpoint_json"] or "null") or {}
            for item in checkpoint.get("inputs", []):
                if item["source_id"] in source_ids:
                    item["consumed"] = True
            connection.execute("UPDATE host_jobs SET checkpoint_json = ? WHERE job_id = ?",
                               (json.dumps(checkpoint, ensure_ascii=False), job_id))

    def begin_subagent_deletion(self, agent_id: str, *, owner: HostJobOwner,
                               character_pack_id: str = "", channel: str = "", actor_id: str = "") -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute("SELECT * FROM host_jobs WHERE capability_source = 'subagent' "
                "AND profile_user_id = ? AND session_id = ? AND character_pack_id = ? AND channel = ? "
                "AND json_extract(payload_json, '$.child_session_id') = ? ORDER BY sequence ASC",
                (owner.profile_user_id, owner.session_id, character_pack_id, channel, agent_id)).fetchall()
            if not rows or (actor_id and json.loads(rows[-1]["payload_json"]).get("execution_context", {}).get("actor_stable_id") != actor_id):
                return {"ok": False, "reason": "subagent_not_found"}
            if all(row["subagent_deleted_at"] for row in rows):
                return {"ok": True, "status": "deleted"}
            if any(row["status"] in {"queued", "running", "paused"} for row in rows):
                return {"ok": False, "reason": "subagent_stop_before_delete"}
            if any(not row["model_consumed_by"] and row["completion_status"] != "silent" for row in rows):
                return {"ok": False, "reason": "subagent_report_not_consumed"}
            connection.executemany("UPDATE host_jobs SET subagent_deleting = 1 WHERE job_id = ?",
                                   [(row["job_id"],) for row in rows])
        return {"ok": True, "status": "deleting"}

    def finish_subagent_deletion(self, agent_id: str, *, owner: HostJobOwner) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE host_jobs SET subagent_deleted_at = ?, subagent_deleting = 0, subagent_delete_ids_json = '[]' "
                "WHERE capability_source = 'subagent' AND profile_user_id = ? AND session_id = ? "
                "AND json_extract(payload_json, '$.child_session_id') = ? AND subagent_deleting = 1",
                (max(0.001, float(self._clock())), owner.profile_user_id, owner.session_id, agent_id))
