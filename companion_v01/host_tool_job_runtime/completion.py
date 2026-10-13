"""Schedule terminal notifications and record admission or delivery outcomes."""
from __future__ import annotations

from typing import Any, Callable

from ..host_jobs import HostJob, HostJobOwner
from .contracts import logger


class ToolJobCompletionMixin:
    def bind_terminal_callback(self, callback: Callable[[HostJob], Any] | None) -> None:
        self.terminal_callback = callback

    def publish_completion(self, job: HostJob) -> dict[str, Any]:
        """Schedule one already-durable terminal fact through the shared route."""

        if not isinstance(job, HostJob) or job.completion_status != "pending":
            return {"ok": False, "status": "ignored", "reason": "completion_not_pending"}
        return self._schedule_completion(job)

    def _schedule_completion(self, job: HostJob) -> dict[str, Any]:
        if self.completion_work.hold(job):
            return {"ok": True, "status": "held", "reason": "completion_owned_by_active_turn"}
        normalized = str(getattr(job, "job_id", "") or "").strip()
        if not normalized:
            return {"ok": False, "status": "invalid", "reason": "host_job_id_required"}
        if self.terminal_callback is None:
            return {"ok": False, "status": "unavailable", "reason": "completion_callback_unavailable"}
        with self._lock:
            if normalized in self._completion_scheduled:
                return {"ok": True, "status": "duplicate", "reason": "completion_already_scheduled"}
            self._completion_scheduled.add(normalized)
        try:
            handle = self.background_tasks.submit(
                lane="host-job-completions",
                name="job-completion",
                fn=self._publish_terminal,
                args=(normalized, job.owner),
            )
        except Exception as exc:
            with self._lock:
                self._completion_scheduled.discard(normalized)
            return {"ok": False, "status": "failed", "reason": f"completion_schedule_{type(exc).__name__}"}
        return {
            "ok": True,
            "status": "scheduled",
            "reason": "completion_scheduled",
            "background_task_id": str(getattr(handle, "task_id", "") or ""),
        }

    def _publish_terminal(self, job_id: str, owner: HostJobOwner) -> None:
        try:
            callback = self.terminal_callback
            if callback is None:
                return
            completed = self.store.get(job_id, owner=owner)
            if completed is None or completed.completion_status != "pending":
                return
            if self.completion_work.hold(completed):
                return
            result = callback(completed)
            delivered = result is True or bool(getattr(result, "ok", False))
            if completed.payload.get("followup"):
                from ..tool_continuation import job_followup
                status = str(getattr(result, "status", "accepted" if delivered else "failed"))
                self.store.record_delivery_receipt(job_id, owner=owner, completion_event_id=completed.completion_event_id,
                    admission_only=True,
                    receipt={"stage": "admission", "status": status,
                             "model_status": "pending" if job_followup(completed).requires_model else "not_requested",
                             "reason": str(getattr(result, "reason", "")),
                             "delivery_status": str(getattr(result, "delivery_status", "") or "not_requested")})
            if delivered:
                self.store.mark_completion_delivered(
                    completed.job_id,
                    completion_event_id=completed.completion_event_id,
                )
            else:
                reason = str(getattr(result, "reason", "") or "completion_delivery_failed")
                self.store.record_completion_failure(completed.job_id, error=reason)
        except Exception as exc:
            completed = self.store.get(job_id, owner=owner)
            if completed is not None and completed.payload.get("followup"):
                from ..tool_continuation import job_followup
                self.store.record_delivery_receipt(job_id, owner=owner, completion_event_id=completed.completion_event_id,
                    admission_only=True, receipt={"stage": "admission", "status": "failed",
                        "model_status": "unknown" if job_followup(completed).requires_model else "not_requested",
                        "delivery_status": "unknown", "reason": f"completion_delivery_{type(exc).__name__}"})
            self.store.record_completion_failure(
                job_id,
                error=f"completion_delivery_{type(exc).__name__}",
            )
            logger.exception("host job terminal callback failed: %s", job_id)
        finally:
            with self._lock:
                self._completion_scheduled.discard(job_id)
