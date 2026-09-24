"""Durable one-shot subagent jobs using the shared Host Job authority."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import uuid
from typing import Any, Callable

from .host_jobs import HostJob, HostJobOwner, HostJobStore
from .subagent_runtime import (
    SubagentProviderRegistry,
    SubagentRunResult,
    SubagentStartRequest,
)


HOST_SUBAGENT_JOB_SOURCE = "subagent"
logger = logging.getLogger("akane.host_subagent_jobs")


class HostSubagentJobRuntime:
    """Persist and execute one-shot children without another task state machine."""

    def __init__(
        self,
        *,
        store: HostJobStore,
        providers: SubagentProviderRegistry,
        provider_name: str,
        background_tasks: Any,
        completion_publisher: Callable[[HostJob], Any] | None = None,
        task_controller: Callable[[str, str], dict[str, Any]] | None = None,
    ) -> None:
        self.store = store
        self.providers = providers
        self.provider_name = str(provider_name or "").strip()
        self.background_tasks = background_tasks
        self.completion_publisher = completion_publisher
        self.task_controller = task_controller
        self._scheduled: set[str] = set()
        self._lock = threading.RLock()
        self._shutdown = threading.Event()

    def request_shutdown(self) -> None:
        self._shutdown.set()

    def submit(
        self,
        *,
        owner: HostJobOwner,
        task: str,
        label: str = "",
        working_directory: str = "",
        allowed_tools: tuple[str, ...] = (),
        model: str = "",
        reasoning_effort: str = "",
        execution_context: dict[str, str] | None = None,
        conversation_ref: str,
        character_pack_id: str = "",
        channel: str = "",
        turn_id: str = "",
        tool_call_id: str = "",
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        stable_key = str(idempotency_key or tool_call_id or f"subagent:{uuid.uuid4().hex}")
        child_session_id = _child_session_id(owner, stable_key)
        request = SubagentStartRequest(
            task=str(task or ""),
            child_session_id=child_session_id,
            parent_profile_user_id=owner.profile_user_id,
            parent_session_id=owner.session_id,
            label=str(label or ""),
            working_directory=str(working_directory or ""),
            allowed_tools=tuple(allowed_tools or ()),
            model=str(model or ""),
            reasoning_effort=str(reasoning_effort or ""),
            execution_context=dict(execution_context or {}),
        )
        reason = self.providers.validate(self.provider_name, request)
        if reason:
            return {"ok": False, "status": "failed", "reason": reason}
        reference = str(conversation_ref or "").strip()
        if not reference:
            return {"ok": False, "status": "failed", "reason": "subagent_conversation_context_unavailable"}
        payload = _request_payload(request)
        fingerprint = _fingerprint(payload)
        created = self.store.create(
            owner=owner,
            capability_source=HOST_SUBAGENT_JOB_SOURCE,
            capability_id="spawn_subagent",
            payload=payload,
            idempotency_key=stable_key,
            argument_fingerprint=fingerprint,
            character_pack_id=character_pack_id,
            channel=channel,
            delivery_target=reference,
            turn_id=turn_id,
            tool_call_id=tool_call_id,
            completion_mode="agent",
            memory_mode="current_turn",
        )
        if not created.get("ok"):
            return {
                "ok": False,
                "status": "failed",
                "reason": str(created.get("reason") or "subagent_job_create_failed"),
            }
        job_id = str(created.get("job_id") or "")
        existing = created.get("job") if isinstance(created.get("job"), HostJob) else None
        existing_status = str(getattr(existing, "status", "") or "")
        if created.get("status") != "duplicate" or existing_status == "queued":
            if not self._schedule(job_id):
                self._settle_scheduler_failure(job_id, owner=owner)
                return {
                    "ok": False,
                    "status": "failed",
                    "reason": "subagent_scheduler_failed",
                    "job_id": job_id,
                }
        elif existing is not None and existing.completion_status == "pending":
            self._publish(existing)
        return {
            "ok": True,
            "status": "accepted",
            "reason": "subagent_job_persisted",
            "job_id": job_id,
            "job_status": existing_status or "queued",
            "control_state": str(getattr(existing, "control_state", "") or ("paused" if existing_status == "paused" else "running")).strip().lower(),
            "child_session_id": str((existing.payload if existing else payload).get("child_session_id") or ""),
            "duplicate": created.get("status") == "duplicate",
        }

    def recover(self) -> int:
        scheduled = 0
        for job_id in self.store.pending_job_ids(capability_source=HOST_SUBAGENT_JOB_SOURCE):
            if self._schedule(job_id):
                scheduled += 1
        return scheduled

    def control(self, job_id: str, *, owner: HostJobOwner, action: str) -> dict[str, Any]:
        """Control a child through its live TaskWork and durable job record."""

        job = self.store.get(job_id, owner=owner)
        if job is None or job.capability_source != HOST_SUBAGENT_JOB_SOURCE:
            return {"ok": False, "status": "unknown", "reason": "host_job_not_found"}
        normalized = str(action or "").strip().lower()
        if normalized not in {"pause", "resume", "stop"}:
            return {"ok": False, "status": "invalid", "reason": "host_job_control_action_invalid"}
        payload = job.payload if isinstance(job.payload, dict) else {}
        task_id = str(payload.get("child_session_id") or "")
        if job.status == "running" and self.task_controller is not None and task_id:
            if normalized == "stop":
                live = self.task_controller(task_id, "stop")
                stored = self.store.request_cancel(job.job_id, owner=owner)
                if not live.get("ok") and live.get("reason") != "task_not_running":
                    return live
                return stored
            live = self.task_controller(task_id, normalized)
            if not live.get("ok"):
                return live
            persisted = self.store.control(
                job.job_id, owner=owner, action=normalized, running_pause=True,
            )
            if persisted.get("ok"):
                return {**persisted, "task": live}
            return persisted
        persisted = self.store.control(job.job_id, owner=owner, action=normalized)
        if persisted.get("ok") and normalized == "resume" and persisted.get("status") == "queued":
            if not self._schedule(job.job_id):
                return {"ok": False, "status": "failed", "reason": "subagent_scheduler_failed"}
        return persisted

    def _schedule(self, job_id: str) -> bool:
        if self._shutdown.is_set():
            return False
        with self._lock:
            if job_id in self._scheduled:
                return True
            self._scheduled.add(job_id)
        try:
            self.background_tasks.submit(
                lane="subagents",
                name="one-shot-subagent",
                fn=self._run,
                args=(job_id,),
            )
            return True
        except Exception:
            with self._lock:
                self._scheduled.discard(job_id)
            return False

    def _run(self, job_id: str) -> None:
        claim_token = ""
        job: HostJob | None = None
        try:
            if self._shutdown.is_set():
                return
            claim = self.store.claim(
                job_id,
                worker_id=f"subagent:{threading.get_ident()}",
                lease_seconds=3600,
            )
            if not claim.get("ok"):
                return
            claim_token = str(claim.get("claim_token") or "")
            job = claim.get("job") if isinstance(claim.get("job"), HostJob) else None
            if job is None or job.capability_source != HOST_SUBAGENT_JOB_SOURCE:
                raise RuntimeError("subagent_job_record_invalid")
            request = _request_from_job(job)
            result = self.providers.execute(
                self.provider_name,
                request,
                cancelled=lambda: self._cancel_requested(job),
            )
            settled = self._settle(job, claim_token=claim_token, result=result)
            if settled:
                terminal = self.store.get(job.job_id, owner=job.owner)
                if terminal is not None and terminal.completion_status == "pending":
                    self._publish(terminal)
        except Exception as exc:
            if job is not None and claim_token:
                settled = self.store.fail(
                    job.job_id,
                    claim_token=claim_token,
                    error=(str(exc) if str(exc).startswith("subagent_") else f"subagent_{type(exc).__name__}"),
                    retryable=False,
                )
                if settled.get("ok"):
                    terminal = self.store.get(job.job_id, owner=job.owner)
                    if terminal is not None and terminal.completion_status == "pending":
                        self._publish(terminal)
            logger.exception("host subagent job failed: %s", job_id)
        finally:
            with self._lock:
                self._scheduled.discard(job_id)

    def _settle(self, job: HostJob, *, claim_token: str, result: SubagentRunResult) -> bool:
        if result.status == "succeeded":
            settled = self.store.succeed(
                job.job_id,
                claim_token=claim_token,
                result_summary=result.summary,
                artifacts=result.artifacts,
            )
        elif result.status == "cancelled":
            settled = self.store.confirm_cancelled(job.job_id, claim_token=claim_token,
                                                   result_summary=result.summary, artifacts=result.artifacts)
        else:
            settled = self.store.fail(
                job.job_id,
                claim_token=claim_token,
                error=result.reason or "subagent_failed",
                retryable=False,
                result_summary=result.summary,
                artifacts=result.artifacts,
            )
        return bool(settled.get("ok"))

    def _cancel_requested(self, job: HostJob) -> bool:
        current = self.store.get(job.job_id, owner=job.owner)
        return (self._shutdown.is_set() or current is None or current.status != "running"
                or current.claim_token != job.claim_token or bool(current.cancel_requested))

    def _settle_scheduler_failure(self, job_id: str, *, owner: HostJobOwner) -> None:
        claim = self.store.claim(job_id, worker_id="subagent-admission", lease_seconds=30)
        if not claim.get("ok"):
            return
        settled = self.store.fail(
            job_id,
            claim_token=claim.get("claim_token"),
            error="subagent_scheduler_failed",
            retryable=False,
        )
        if not settled.get("ok"):
            return
        terminal = self.store.get(job_id, owner=owner)
        if terminal is not None:
            self.store.mark_completion_delivered(
                terminal.job_id,
                completion_event_id=terminal.completion_event_id,
            )

    def _publish(self, job: HostJob) -> None:
        if self.completion_publisher is None:
            return
        try:
            self.completion_publisher(job)
        except Exception:
            logger.exception("subagent completion publish failed: %s", job.job_id)


def _request_payload(request: SubagentStartRequest) -> dict[str, Any]:
    return {
        "task": request.task,
        "child_session_id": request.child_session_id,
        "parent_profile_user_id": request.parent_profile_user_id,
        "parent_session_id": request.parent_session_id,
        "label": request.label,
        "working_directory": request.working_directory,
        "allowed_tools": list(request.allowed_tools),
        "model": request.model,
        "reasoning_effort": request.reasoning_effort,
        "execution_context": dict(request.execution_context),
    }


def _request_from_job(job: HostJob) -> SubagentStartRequest:
    payload = job.payload if isinstance(job.payload, dict) else {}
    return SubagentStartRequest(
        task=str(payload.get("task") or ""),
        child_session_id=str(payload.get("child_session_id") or ""),
        parent_profile_user_id=job.owner.profile_user_id,
        parent_session_id=job.owner.session_id,
        label=str(payload.get("label") or ""),
        working_directory=str(payload.get("working_directory") or ""),
        allowed_tools=tuple(str(item or "") for item in list(payload.get("allowed_tools") or [])),
        model=str(payload.get("model") or ""),
        reasoning_effort=str(payload.get("reasoning_effort") or ""),
        execution_context=dict(payload.get("execution_context") or {}),
    )


def _fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _child_session_id(owner: HostJobOwner, idempotency_key: str) -> str:
    material = "\x00".join((owner.profile_user_id, owner.session_id, idempotency_key))
    return "subagent_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


__all__ = ["HOST_SUBAGENT_JOB_SOURCE", "HostSubagentJobRuntime"]
