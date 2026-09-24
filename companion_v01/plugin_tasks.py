"""Host-owned Task port backed by the existing HostJobStore."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable, Mapping

from .host_jobs import HostJob, HostJobOwner, HostJobStore


PLUGIN_TASK_SOURCE = "plugin_task"
_TASK_TYPE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_OPERATIONS = frozenset({
    "create", "open", "status", "update", "checkpoint", "checkpoint_status",
    "pause", "pause_boundary", "resume", "cancel",
})
_TERMINAL = frozenset({"completed", "failed", "cancelled", "stale"})
# Losing the request's authority is not the task reaching a terminal state.
_SCOPE_EXPIRED_REASONS = frozenset({"task_generation_expired", "task_invocation_expired"})


def task_rejection(operation: str, reason: str, *, task_id: str = "") -> dict[str, Any]:
    """Reject one request without claiming the underlying task is finished.

    ``complete`` describes the task lifecycle only. A request rejected because
    this invocation's generation or scope expired reports ``scope_expired``
    instead; the task may still be running elsewhere and needs a real recovery
    open rather than being treated as done.
    """

    expired = reason in _SCOPE_EXPIRED_REASONS
    return {
        "task_id": task_id,
        "status": "stale" if expired else "rejected",
        "reason": str(reason or "task_request_rejected"),
        "complete": False,
        "scope_expired": expired,
        "cancel_requested": False,
        "result": None,
        "checkpoint": None,
        "checkpoint_version": 0,
        "checkpoint_fingerprint": "",
        "checkpoint_generation_id": "",
        "checkpoint_updated_at": 0.0,
        "recovery_reason": "",
    }


class HostTaskProvider:
    """Project task operations onto one scoped HostJobStore owner."""

    def __init__(
        self,
        store: HostJobStore,
        *,
        generation_active: Callable[[str, str], bool] | None = None,
    ) -> None:
        self.store = store
        self._generation_active = generation_active

    async def request(self, operation: str, payload: Mapping[str, Any], *, invocation: Any) -> dict[str, Any]:
        if operation not in _OPERATIONS or not isinstance(payload, Mapping):
            return task_rejection(operation, "task_request_invalid")
        if invocation is None or not getattr(invocation, "active", False):
            return task_rejection(operation, "task_invocation_expired")
        context = getattr(invocation, "context", None)
        if context is None or getattr(context, "global_scope", False):
            return task_rejection(operation, "context_unbound")
        profile_user_id = str(getattr(context, "profile_user_id", "") or "").strip()
        session_id = str(getattr(context, "session_id", "") or "").strip()
        character_pack_id = str(getattr(context, "character_pack_id", "") or "").strip()
        if not profile_user_id or not session_id:
            return task_rejection(operation, "context_unbound")
        plugin_id = str(getattr(invocation, "plugin_id", "") or "").strip()
        generation_id = str(getattr(invocation, "generation_id", "") or "").strip()
        if not self._is_generation_active(plugin_id, generation_id):
            self.revoke_generation(plugin_id, generation_id)
            return task_rejection(operation, "task_generation_expired", task_id=str(payload.get("task_id") or ""))
        owner = HostJobOwner(profile_user_id, session_id)
        if operation == "create":
            return self._create(
                payload,
                owner=owner,
                plugin_id=plugin_id,
                generation_id=generation_id,
                character_pack_id=character_pack_id,
            )
        task_id = str(payload.get("task_id") or "").strip()
        if not task_id:
            return task_rejection(operation, "task_id_required")
        job = self.store.get(task_id, owner=owner)
        if job is None:
            return task_rejection(operation, "task_not_found", task_id=task_id)
        if job.character_pack_id != character_pack_id:
            return task_rejection(operation, "task_access_denied", task_id=task_id)
        if not self._belongs(job, plugin_id, generation_id):
            if operation == "open" and payload.get("recover") is True:
                adopted = self.store.adopt_recovery(
                    task_id,
                    owner=owner,
                    plugin_id=plugin_id,
                    generation_id=generation_id,
                    character_pack_id=character_pack_id,
                )
                if not adopted.get("ok") or not isinstance(adopted.get("job"), HostJob):
                    return task_rejection(operation, str(adopted.get("reason") or "task_recovery_failed"), task_id=task_id)
                job = adopted["job"]
            else:
                if self._same_plugin_recovered_by_other_generation(job, plugin_id, generation_id):
                    return task_rejection(operation, "task_generation_expired", task_id=task_id)
                return task_rejection(operation, "task_access_denied", task_id=task_id)
        if operation == "open":
            if payload.get("recover") is not True:
                return _receipt(job, reason="task_opened")
            if job.recovery_state != "unknown":
                return _receipt(job, reason="task_recovery_not_required")
            return _receipt(job, reason="task_recovery_checkpoint_loaded")
        if operation in {"status", "checkpoint_status"}:
            return _receipt(job)
        if operation == "checkpoint":
            saved = self.store.write_checkpoint(
                task_id,
                owner=owner,
                plugin_id=plugin_id,
                generation_id=generation_id,
                character_pack_id=character_pack_id,
                value=payload.get("value"),
                version=payload.get("version"),
            )
            latest = self.store.get(task_id, owner=owner)
            return _receipt(
                latest or job,
                reason=str(saved.get("reason") or "task_checkpoint_failed"),
            )
        if operation == "update":
            return self._update(job, payload, owner=owner)
        if operation == "pause":
            return self._control(job, owner=owner, action="pause")
        if operation == "pause_boundary":
            pause_mode = str(job.payload.get("pause_mode") or "unavailable") if isinstance(job.payload, dict) else "unavailable"
            if pause_mode != "cooperative":
                return _receipt(job, reason="task_pause_unavailable", status_override="pause_unavailable")
            if job.checkpoint_version < 1:
                return _receipt(job, reason="task_checkpoint_required_for_pause_boundary", status_override="pause_unavailable")
            return self._control(job, owner=owner, action="pause_boundary")
        if operation == "resume":
            return self._control(job, owner=owner, action="resume")
        return self._cancel(job, owner=owner)

    def revoke_generation(self, plugin_id: str, generation_id: str) -> tuple[HostJob, ...]:
        jobs = self.store.revoke_plugin_tasks(plugin_id, generation_id)
        for job in jobs:
            if job.status != "running":
                continue
            requested = self.store.request_cancel(job.job_id, owner=job.owner)
            if requested.get("status") == "cancelling":
                # A withdrawn generation may no longer issue new calls, but a
                # running worker still owns the right to report its real
                # terminal outcome.  Do not fabricate cancellation here.
                continue
        return jobs

    def _is_generation_active(self, plugin_id: str, generation_id: str) -> bool:
        if not plugin_id or not generation_id:
            return False
        if self._generation_active is None:
            return True
        try:
            return bool(self._generation_active(plugin_id, generation_id))
        except Exception:
            return False

    def _create(
        self,
        payload,
        *,
        owner: HostJobOwner,
        plugin_id: str,
        generation_id: str,
        character_pack_id: str,
    ) -> dict[str, Any]:
        task_type = str(payload.get("task_type") or "").strip().lower()
        task_payload = payload.get("payload", {})
        idempotency_key = str(payload.get("idempotency_key") or "").strip()
        pause_mode = str(payload.get("pause_mode") or "unavailable").strip().lower()
        if _TASK_TYPE.fullmatch(task_type) is None:
            return task_rejection("create", "task_type_invalid")
        if not isinstance(task_payload, dict):
            return task_rejection("create", "task_payload_object_required")
        if not idempotency_key or len(idempotency_key) > 256:
            return task_rejection("create", "task_idempotency_key_required")
        if pause_mode not in {"cooperative", "unavailable"}:
            return task_rejection("create", "task_pause_mode_invalid")
        stored_payload = {
            "plugin_id": plugin_id,
            "generation_id": generation_id,
            "task_type": task_type,
            "payload": dict(task_payload),
            "pause_mode": pause_mode,
        }
        encoded = json.dumps(stored_payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)
        created = self.store.create(
            owner=owner,
            capability_source=PLUGIN_TASK_SOURCE,
            capability_id=f"{plugin_id}.task.{task_type}",
            payload=stored_payload,
            idempotency_key=idempotency_key,
            argument_fingerprint=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            character_pack_id=character_pack_id,
            turn_id=f"plugin-task:{generation_id}",
            completion_mode="silent",
            memory_mode="current_turn",
        )
        if not created.get("ok"):
            existing = created.get("job")
            if isinstance(existing, HostJob) and existing.character_pack_id != character_pack_id:
                return task_rejection("create", "task_access_denied")
            return task_rejection(
                "create",
                str(created.get("reason") or "task_create_failed"),
                task_id=str(created.get("job_id") or ""),
            )
        job = created.get("job")
        if isinstance(job, HostJob) and job.character_pack_id != character_pack_id:
            return task_rejection("create", "task_access_denied")
        if not isinstance(job, HostJob):
            job = self.store.get(str(created.get("job_id") or ""), owner=owner)
        if isinstance(job, HostJob) and job.character_pack_id != character_pack_id:
            return task_rejection("create", "task_access_denied")
        return _receipt(job, reason="task_created") if isinstance(job, HostJob) else task_rejection("create", "task_create_failed")

    def _update(self, job: HostJob, payload: Mapping[str, Any], *, owner: HostJobOwner) -> dict[str, Any]:
        status = str(payload.get("status") or "").strip().lower()
        if status not in {"running", "completed", "failed", "cancelled"}:
            return _receipt(job, reason="task_status_update_invalid")
        if status == "running":
            if _public_status(job) == "running":
                return _receipt(job, reason="task_already_running")
            claimed = self.store.claim(job.job_id, worker_id=f"plugin-task:{job.job_id}", lease_seconds=31_536_000)
            if claimed.get("ok") and isinstance(claimed.get("job"), HostJob):
                return _receipt(claimed["job"], reason="task_started")
            current = self.store.get(job.job_id, owner=owner)
            return _receipt(current or job, reason=str(claimed.get("reason") or "task_start_failed"))
        current = self.store.get(job.job_id, owner=owner) or job
        if _public_status(current) in _TERMINAL:
            return _receipt(current, reason="task_already_terminal")
        if current.status != "running" or current.control_state not in {"running", "stopping"} or not current.claim_token:
            return _receipt(current, reason="task_not_running")
        result = payload.get("result")
        if status == "completed":
            settled = self.store.succeed(
                current.job_id,
                claim_token=current.claim_token,
                result_summary="plugin_task_completed",
                result={"task_result": result},
            )
        elif status == "failed":
            settled = self.store.fail(
                current.job_id,
                claim_token=current.claim_token,
                error=str(payload.get("reason") or "plugin_task_failed"),
                retryable=False,
                result={"task_result": result},
            )
        else:
            if current.control_state != "stopping":
                return self._cancel(current, owner=owner)
            settled = self.store.confirm_cancelled(
                current.job_id,
                claim_token=current.claim_token,
                result_summary="plugin_task_cancelled",
                result={"reason": "plugin_task_cancelled"},
            )
        latest = self.store.get(current.job_id, owner=owner)
        return _receipt(latest or current, reason=str(settled.get("reason") or "task_update_failed"))

    def _control(self, job: HostJob, *, owner: HostJobOwner, action: str) -> dict[str, Any]:
        if action == "pause":
            pause_mode = str(job.payload.get("pause_mode") or "unavailable") if isinstance(job.payload, dict) else "unavailable"
            if pause_mode != "cooperative" and _public_status(job) == "running":
                return _receipt(job, reason="task_pause_unavailable", status_override="pause_unavailable")
        store_action = "pause" if action == "pause_boundary" else action
        result = self.store.control(
            job.job_id,
            owner=owner,
            action=store_action,
            running_pause=action in {"pause_boundary", "resume"},
        )
        latest = self.store.get(job.job_id, owner=owner)
        status_override = str(result.get("status") or "") if result.get("status") in {
            "pause_unavailable", "resume_unavailable"
        } else ""
        return _receipt(
            latest or job,
            reason=str(result.get("reason") or "task_control_failed"),
            status_override=status_override,
        )

    def _cancel(self, job: HostJob, *, owner: HostJobOwner) -> dict[str, Any]:
        result = self.store.request_cancel(job.job_id, owner=owner)
        latest = self.store.get(job.job_id, owner=owner)
        return _receipt(latest or job, reason=str(result.get("reason") or "task_cancel_failed"))

    @staticmethod
    def _belongs(job: HostJob, plugin_id: str, generation_id: str) -> bool:
        effective_generation = job.recovery_generation_id or str(job.payload.get("generation_id") or "")
        return (
            job.capability_source == PLUGIN_TASK_SOURCE
            and str(job.payload.get("plugin_id") or "") == plugin_id
            and effective_generation == generation_id
        )

    @staticmethod
    def _same_plugin_recovered_by_other_generation(job: HostJob, plugin_id: str, generation_id: str) -> bool:
        return (
            job.capability_source == PLUGIN_TASK_SOURCE
            and str(job.payload.get("plugin_id") or "") == plugin_id
            and bool(job.recovery_generation_id)
            and job.recovery_generation_id != generation_id
        )


def _public_status(job: HostJob) -> str:
    if job.scope_revoked_reason:
        return "stale"
    if job.recovery_state == "unknown":
        return "recovery_unknown"
    if job.status == "queued":
        return "created"
    if job.status == "paused" or (job.status == "running" and job.control_state == "paused"):
        return "paused"
    if job.status == "running" and job.control_state == "stopping":
        return "cancelling"
    if job.status == "succeeded":
        return "completed"
    if job.status in {"failed", "cancelled"}:
        return job.status
    return "running"


def _receipt(job: HostJob, *, reason: str = "", status_override: str = "") -> dict[str, Any]:
    status = status_override or _public_status(job)
    result = job.result.get("task_result") if isinstance(job.result, dict) else None
    public_status = _public_status(job)
    return {
        "task_id": job.job_id,
        "status": status,
        "reason": reason or job.last_error or job.scope_revoked_reason,
        "complete": public_status in _TERMINAL,
        "scope_expired": public_status == "stale",
        "cancel_requested": bool(job.cancel_requested),
        "result": result,
        "checkpoint": job.checkpoint,
        "checkpoint_version": job.checkpoint_version,
        "checkpoint_fingerprint": job.checkpoint_fingerprint,
        "checkpoint_generation_id": job.checkpoint_generation_id,
        "checkpoint_updated_at": job.checkpoint_updated_at,
        "recovery_reason": job.recovery_reason,
    }


__all__ = ["HostTaskProvider", "PLUGIN_TASK_SOURCE", "task_rejection"]
