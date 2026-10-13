"""Own one shared handler map, scheduling lock and durable tool-job runtime."""
from __future__ import annotations

import threading
from typing import Any, Callable

from ..host_jobs import HostJob, HostJobOwner, HostJobStore
from ..tool_handlers.core import ToolExecutionContext, ToolExecutionResult
from .admission import ToolJobAdmissionMixin
from .completion import ToolJobCompletionMixin
from .execution import ToolJobExecutionMixin
from .managed import ManagedToolMixin
from .contracts import HOST_TOOL_JOB_SOURCE, logger, _registered_failure_result


class HostToolJobRuntime(ManagedToolMixin, ToolJobAdmissionMixin, ToolJobExecutionMixin, ToolJobCompletionMixin):
    """Persist, schedule, and finish admitted business tool calls.

    The existing ``BackgroundTaskRunner`` remains the executor.  This runtime
    only joins it to the durable Job store. Managed calls retain their trusted
    context in process; after restart a lost binding fails without replay.
    Unconfirmed running work is settled by the shared store.
    """

    def __init__(
        self,
        *,
        engine: Any,
        store: HostJobStore,
        background_tasks: Any,
        conversation_ref_issuer: Callable[[ToolExecutionContext], str] | None = None,
        terminal_callback: Callable[[HostJob], Any] | None = None,
    ) -> None:
        self.engine = engine
        self.store = store
        self.background_tasks = background_tasks
        self.conversation_ref_issuer = conversation_ref_issuer
        self.terminal_callback = terminal_callback
        self._scheduled: set[str] = set()
        self._completion_scheduled: set[str] = set()
        self._admitted_handlers: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._job_condition = threading.Condition(self._lock)
        self._managed_calls = {}
        from ..host_completion_work import HostCompletionWork
        from ..host_completion_presentation import bound_completion_result
        self.completion_work = HostCompletionWork(store, self.publish_completion,
            bound_result=lambda job, result: bound_completion_result(job, result, engine=self.engine))
        coordinator = getattr(engine, "turn_coordinator", None)
        if coordinator is not None:
            coordinator.bind_stop_listener(self._revoke_turn)

    def _revoke_turn(self, *, profile_user_id, session_id, turn_token):
        for job in self.store.revoke_turn(owner=HostJobOwner(profile_user_id, session_id), turn_token=turn_token):
            if job.status in {"succeeded", "failed", "cancelled"}:
                self.publish_completion(job)

    def revoke_capabilities(self, capability_ids):
        for job in self.store.revoke_capabilities(tuple(capability_ids)):
            if job.status in {"succeeded", "failed", "cancelled"}:
                self.publish_completion(job)

    def recover(self) -> int:
        scheduled = 0
        for job_id in self.store.pending_job_ids(capability_source=HOST_TOOL_JOB_SOURCE):
            if self._schedule(job_id).get("ok"):
                scheduled += 1
        # Completion delivery is one host concern regardless of whether the
        # worker was a built-in long tool, Shell, or a plugin generation.
        for job in self.store.pending_completions():
            if self.publish_completion(job).get("ok"):
                scheduled += 1
        return scheduled

    def _schedule(self, job_id: str) -> dict[str, Any]:
        normalized = str(job_id or "").strip()
        if not normalized:
            return {"ok": False, "status": "invalid", "reason": "host_job_id_required"}
        with self._lock:
            if normalized in self._scheduled:
                return {"ok": True, "status": "duplicate", "reason": "host_job_already_scheduled"}
            self._scheduled.add(normalized)
        try:
            handle = self.background_tasks.submit(
                lane="host-jobs",
                name="long-tool",
                fn=self._run,
                args=(normalized,),
            )
        except Exception as exc:
            with self._lock:
                self._scheduled.discard(normalized)
            return {"ok": False, "status": "failed", "reason": f"host_job_schedule_{type(exc).__name__}"}
        return {
            "ok": True,
            "status": "scheduled",
            "reason": "host_job_scheduled",
            "background_task_id": str(getattr(handle, "task_id", "") or ""),
        }

    @staticmethod
    def _release_handler(handler):
        release = getattr(handler, "release_invocation", None)
        if callable(release):
            try:
                release()
            except Exception as exc:
                # Cleanup failure must not replace an admitted result or skip
                # its terminal notification. Keep the cause without private prose.
                logger.error("host tool handler release failed: %s", type(exc).__name__)

    def _submission_failed(
        self, capability_id: str, job_id: str, *, owner: HostJobOwner,
        reason: str, settle_queued: bool,
    ) -> ToolExecutionResult:
        """Report the existing job; only withdraw new work this request owns."""
        if settle_queued:
            try:
                claim = self.store.claim(job_id, worker_id="scheduler-failure", lease_seconds=30)
                if claim.get("ok"):
                    self.store.fail(job_id, claim_token=claim.get("claim_token"),
                                    error=reason, retryable=False)
                    # Returning this failure does not prove model receipt.
                    # The live request observer/response boundary below owns
                    # consumption; a failed/cancelled request leaves it unread.
            except Exception as exc:
                logger.error("failed to settle tool submission: %s (%s)", job_id, type(exc).__name__)
        try:
            job = self.store.get(job_id, owner=owner)
        except Exception as exc:
            logger.error("failed to read tool submission state: %s (%s)", job_id, type(exc).__name__)
            job = None
        if job is not None and job.status in {"succeeded", "failed", "cancelled"} and job.turn_id:
            self.completion_work.query(job, consumed_by=job.turn_id)
        return _registered_failure_result(capability_id, job_id=job_id, job=job, reason=reason)
