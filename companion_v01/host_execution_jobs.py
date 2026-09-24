"""Join long Shell runs to the durable Host Job and Agent completion path."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable

from .client_protocol import ClientMode
from .execution_run import ExecutionRunOwner, ExecutionRunTerminalEvent
from .execution_specs import (
    EXEC_STATUS_CANCELLED,
    EXEC_STATUS_COMPLETED,
    EXEC_STATUS_RUNNING,
)
from .host_jobs import HostJob, HostJobOwner, HostJobStore
from .tool_handlers.core import ToolExecutionContext


HOST_EXECUTION_JOB_SOURCE = "execution"
logger = logging.getLogger("akane.host_execution_jobs")


@dataclass(slots=True)
class _TrackedRun:
    job_id: str
    job_owner: HostJobOwner
    run_owner: ExecutionRunOwner
    claim_token: str
    armed: bool = False
    start_observed: bool = False


class HostExecutionJobRuntime:
    """Persist process identity before spawn and wake the Agent after long runs.

    ``ExecutionRunStore`` remains the live process/output authority. ``HostJob``
    adds restart-safe ownership, completion identity, and delivery. A Job starts
    provisionally silent so a command that finishes inside ``exec_run`` does not
    create a redundant second reply; observing ``running`` arms its completion.
    """

    def __init__(
        self,
        *,
        store: HostJobStore,
        conversation_ref_issuer: Callable[[ToolExecutionContext], str] | None,
        completion_publisher: Callable[[HostJob], Any] | None = None,
    ) -> None:
        self.store = store
        self.conversation_ref_issuer = conversation_ref_issuer
        self.completion_publisher = completion_publisher
        self._runs: dict[str, _TrackedRun] = {}
        self._lock = threading.RLock()

    @staticmethod
    def accepts(context: ToolExecutionContext) -> bool:
        return context.execution_scope is None and str(context.client_mode or "").strip().lower() in {
            ClientMode.QQ_TEXT.value,
            ClientMode.DESKTOP_PET.value,
        }

    def bind_provider(self, provider: Any) -> None:
        store = getattr(provider, "store", None)
        binder = getattr(store, "bind_terminal_observer", None)
        if not callable(binder):
            raise TypeError("execution_terminal_observer_unavailable")
        binder(self.observe_terminal)

    def begin(
        self,
        *,
        run_id: str,
        run_owner: ExecutionRunOwner,
        context: ToolExecutionContext,
        argument_fingerprint: str,
    ) -> dict[str, Any]:
        if not self.accepts(context):
            return {"ok": True, "status": "skipped", "tracked": False}
        conversation_ref = ""
        if self.conversation_ref_issuer is not None:
            try:
                conversation_ref = str(self.conversation_ref_issuer(context) or "").strip()
            except Exception:
                conversation_ref = ""
        if not conversation_ref:
            return {
                "ok": False,
                "status": "unavailable",
                "reason": "job_conversation_context_unavailable",
            }
        job_owner = HostJobOwner(context.profile_user_id, context.session_id)
        created = self.store.create(
            owner=job_owner,
            capability_source=HOST_EXECUTION_JOB_SOURCE,
            capability_id="exec_run",
            payload={"run_id": run_id},
            idempotency_key=run_id,
            argument_fingerprint=str(argument_fingerprint or ""),
            character_pack_id=context.character_pack_id,
            channel=context.client_mode,
            delivery_target=conversation_ref,
            tool_call_id=run_id,
            completion_mode="silent",
            # Synchronous completion is already paired in the calling turn.
            # arm_agent_completion promotes this only after exec_run yields.
            memory_mode="current_turn",
        )
        if not created.get("ok") or created.get("status") == "duplicate":
            return {
                "ok": False,
                "status": str(created.get("status") or "failed"),
                "reason": str(created.get("reason") or "host_job_create_failed"),
            }
        job_id = str(created.get("job_id") or "")
        claimed = self.store.claim(
            job_id,
            worker_id=f"execution:{run_owner.provider_id}",
            lease_seconds=900.0,
        )
        if not claimed.get("ok"):
            return {
                "ok": False,
                "status": str(claimed.get("status") or "failed"),
                "reason": str(claimed.get("reason") or "host_job_claim_failed"),
            }
        with self._lock:
            self._runs[run_id] = _TrackedRun(
                job_id=job_id,
                job_owner=job_owner,
                run_owner=run_owner,
                claim_token=str(claimed.get("claim_token") or ""),
            )
        return {"ok": True, "status": "running", "tracked": True, "job_id": job_id}

    def observe_start(
        self,
        run_id: str,
        *,
        status: str,
        exit_code: int | None,
        reason: str,
    ) -> dict[str, Any]:
        """Commit whether the initiating tool call returned sync or async."""

        with self._lock:
            tracked = self._runs.get(str(run_id or ""))
        if tracked is None:
            return {"ok": False, "reason": "execution_job_tracking_missing"}
        tracked.start_observed = True
        normalized_status = str(status or "").strip().lower()
        if normalized_status == EXEC_STATUS_RUNNING:
            armed = self.store.arm_agent_completion(tracked.job_id, owner=tracked.job_owner)
            if not armed.get("ok"):
                return {"ok": False, "reason": "host_job_completion_arm_failed"}
            tracked.armed = True
            completed = self.store.get(tracked.job_id, owner=tracked.job_owner)
            if completed is not None and completed.completion_status == "pending":
                self._publish(completed)
                with self._lock:
                    self._runs.pop(run_id, None)
            return {"ok": True, "status": "armed"}

        # Invalid cwd, spawn failure, and providers without a terminal observer
        # still settle the provisional Job honestly, but remain silent because
        # the initiating exec_run result already contains the failure.
        terminal = ExecutionRunTerminalEvent(
            run_id=run_id,
            owner=tracked.run_owner,
            status=normalized_status,
            exit_code=exit_code,
            reason=str(reason or ""),
            finished_at=0.0,
        )
        self._settle(tracked, terminal)
        with self._lock:
            self._runs.pop(run_id, None)
        return {"ok": True, "status": "silent"}

    def observe_terminal(self, event: ExecutionRunTerminalEvent) -> None:
        with self._lock:
            tracked = self._runs.get(event.run_id)
        if tracked is None or tracked.run_owner != event.owner:
            return
        self._settle(tracked, event)
        if tracked.armed:
            completed = self.store.get(tracked.job_id, owner=tracked.job_owner)
            if completed is not None and completed.completion_status == "pending":
                self._publish(completed)
        if tracked.armed or tracked.start_observed:
            with self._lock:
                self._runs.pop(event.run_id, None)

    def recover(self) -> int:
        """Fail orphaned processes honestly; their in-memory handles are gone."""

        recovered = 0
        for job_id in self.store.pending_job_ids(capability_source=HOST_EXECUTION_JOB_SOURCE):
            claimed = self.store.claim(
                job_id,
                worker_id="execution-recovery",
                lease_seconds=30.0,
            )
            if not claimed.get("ok"):
                continue
            settled = self.store.fail(
                job_id,
                claim_token=claimed.get("claim_token"),
                error="host_restart_process_unavailable",
                retryable=False,
            )
            if settled.get("ok"):
                recovered += 1
        return recovered

    def _settle(self, tracked: _TrackedRun, event: ExecutionRunTerminalEvent) -> None:
        summary = _completion_summary(event)
        if event.status == EXEC_STATUS_COMPLETED:
            self.store.succeed(
                tracked.job_id,
                claim_token=tracked.claim_token,
                result_summary=summary,
            )
        elif event.status == EXEC_STATUS_CANCELLED:
            self.store.confirm_cancelled(
                tracked.job_id,
                claim_token=tracked.claim_token,
            )
        else:
            self.store.fail(
                tracked.job_id,
                claim_token=tracked.claim_token,
                error=summary,
                retryable=False,
            )

    def _publish(self, job: HostJob) -> None:
        if self.completion_publisher is None:
            return
        try:
            self.completion_publisher(job)
        except Exception:
            # The Job stays pending and the shared completion dispatcher will
            # retry it during recovery; process settlement must not be undone.
            logger.exception("execution job completion scheduling failed: %s", job.job_id)


def _completion_summary(event: ExecutionRunTerminalEvent) -> str:
    parts = [
        "命令已结束",
        f"run_id={event.run_id}",
        f"status={event.status}",
    ]
    if event.exit_code is not None:
        parts.append(f"exit_code={event.exit_code}")
    if event.reason:
        parts.append(f"reason={event.reason}")
    return "；".join(parts) + "。需要查看最终输出时调用 exec_status。"


__all__ = ["HOST_EXECUTION_JOB_SOURCE", "HostExecutionJobRuntime"]
