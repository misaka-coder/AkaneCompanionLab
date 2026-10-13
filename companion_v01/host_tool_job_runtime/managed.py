"""Run an admitted business call once, independently of foreground waiting."""
from __future__ import annotations

import copy
import threading
import time
from contextvars import copy_context
from dataclasses import dataclass, field, replace

from ..execution_wait import foreground_wait_seconds, batch_wait_seconds
from ..execution_specs import normalize_status_wait_seconds
from ..host_jobs import HostJobOwner
from ..tool_handlers.core import ToolExecutionResult, ToolFollowupEnvelope
from .contracts import _job_accepted_result, _job_failure_result
from .results import completion_result


@dataclass
class ManagedCall:
    context: object
    dispatch: object
    variables: object = field(default_factory=copy_context)
    completed: threading.Event = field(default_factory=threading.Event)
    released: bool = False
    result: ToolExecutionResult | None = None


class ManagedToolMixin:
    def invoke(self, *, handler, capability_id, invocation_id, call, context,
               dispatch, domain_profile_id="", background=False):
        started = time.monotonic()
        execution = ManagedCall(context, dispatch)
        # submit performs adapter admission/approval and retains the exact
        # implementation before the worker can start. Context stays in process.
        accepted = self.submit(handler=handler, capability_id=capability_id, invocation_id=invocation_id,
            call=call, context=context, domain_profile_id=domain_profile_id, managed=execution)
        job_id = accepted.state_updates.get("capability_execution", {}).get("job_id")
        if not job_id:
            return accepted
        owner = HostJobOwner(context.profile_user_id, context.session_id)
        with self._lock:
            execution = self._managed_calls.get(job_id, execution)
        admitted_ms = (time.monotonic() - started) * 1000
        waiting = time.monotonic()
        remaining = 0 if background else foreground_wait_seconds(batch_wait_seconds())
        deadline = waiting + remaining
        while not background and not execution.completed.is_set():
            if context.cancel_requested and context.cancel_requested():
                self.store.request_cancel(job_id, owner=owner)
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            execution.completed.wait(min(remaining, .1))
        with self._lock:
            job = self.store.get(job_id, owner=owner)
            direct = not background and job is not None and job.status in {"succeeded", "failed", "cancelled"}
            execution.released = True
            if execution.completed.is_set():
                self._managed_calls.pop(job_id, None)
        if job is None:
            return _job_failure_result(capability_id, "host_job_not_found")
        if direct:
            # The next successful model response acknowledges this actual
            # result. Recovery cannot notify it a second time in the meantime.
            self.completion_work.query(job, consumed_by=job.turn_id)
            if context.execution_scope is not None and context.execution_scope.pending_work is not None:
                context.execution_scope.pending_work.acknowledge(job_id)
            result = copy.deepcopy(execution.result) if execution.result is not None else completion_result(job, identity=False)
        else:
            result = _job_accepted_result(capability_id, job_id=job_id,
                duplicate=accepted.state_updates.get("capability_execution", {}).get("duplicate", False),
                job_status=job.status, control_state=job.control_state)
            # No new implicit window after control was handed back.
            if job.completion_status == "pending":
                self._schedule_completion(job)
        result.execution_timing.update(admission_ms=round(admitted_ms, 3),
            foreground_wait_ms=round((time.monotonic() - waiting) * 1000, 3))
        return result

    def _execute_managed(self, job, execution):
        from ..tool_batch import cancelled_tool_result
        from ..tool_continuation import bind_result_followup
        with self._lock:
            handler = self._admitted_handlers.get(job.job_id)
        context = execution.context
        original_cancel = context.cancel_requested
        cancelled = lambda: self._cancel_requested(job) or bool(original_cancel and original_cancel())
        context = replace(context, cancel_requested=cancelled,
            followup_default=job.payload["followup"]["default"])
        def run():
            if cancelled():
                return cancelled_tool_result(job.capability_id)
            check = getattr(handler, "invocation_grant_active", None)
            if callable(check) and not check():
                self.store.revoke_job_capability(job.job_id, owner=job.owner)
                return cancelled_tool_result(job.capability_id)
            result = execution.dispatch(context, dict(job.payload["call"]), handler)
            if not isinstance(result, ToolExecutionResult):
                raise TypeError("tool_result_required")
            if callable(check) and not check():
                self.store.revoke_job_capability(job.job_id, owner=job.owner)
            return bind_result_followup(result, context=context,
                default=result.followup.mode if result.followup is not None else context.followup_default,
                allow_end=job.payload["call"].get("finish_turn") is True, delivery_managed=True)
        started = time.monotonic()
        result = execution.variables.run(run)
        result.execution_timing["business_execution_ms"] = round((time.monotonic() - started) * 1000, 3)
        execution.result = result
        return result

    def task_control(self, *, tool_type, call, context):
        """Use the same public job ID; controls never enter the business pool."""
        job_id = str(call.get("run_id") or "")
        if not job_id.startswith("job_") or tool_type not in {"exec_status", "exec_cancel"}:
            return None
        owner = HostJobOwner(context.profile_user_id, context.session_id)
        job = self.store.get(job_id, owner=owner)
        actor = str(context.request_context.get("actor_stable_id") or "")
        task_id = getattr(context.execution_scope, "task_id", "")
        if (job is None or job.capability_source not in {"tool", "subagent"} or job.character_pack_id != context.character_pack_id
            or job.channel != context.client_mode
            or (job.payload.get("task_scope") or {}).get("task_id", "") != task_id
            or (str((job.payload.get("request_context") or job.payload.get("execution_context") or {}).get("actor_stable_id") or "") not in {"", actor})):
            return ToolExecutionResult(tool_type=tool_type,
                followup_context="<tool_use_error>Task unavailable in this execution scope.</tool_use_error>",
                stream_events=[{"type": "tool_execution_failed", "status": "unknown", "reason": "host_job_not_found"}])
        if tool_type == "exec_cancel":
            subagents = getattr(self.engine, "host_subagent_jobs", None)
            cancelled = subagents.control(job_id, owner=owner, action="stop") if (
                job.capability_source == "subagent" and subagents is not None) else self.store.request_cancel(job_id, owner=owner)
            job = self.store.get(job_id, owner=owner)
            if job.completion_status == "pending":
                self._schedule_completion(job)
            return ToolExecutionResult(tool_type=tool_type,
                followup_context=f"job_id={job_id}; status={cancelled['status']}; reason={cancelled['reason']}",
                state_updates={"capability_execution": {"job_id": job_id, "status": cancelled["status"]}})
        seconds = normalize_status_wait_seconds(call.get("wait_seconds"))
        deadline = time.monotonic() + seconds
        with self._job_condition:
            while job.status in {"queued", "running", "paused"}:
                if context.cancel_requested and context.cancel_requested():
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._job_condition.wait(min(.2, remaining))
                job = self.store.get(job_id, owner=owner)
        if job.status in {"queued", "running", "paused"}:
            return ToolExecutionResult(tool_type=tool_type,
                followup_context=f"job_id={job_id}; status={job.status}; control_state={job.control_state}",
                state_updates={"capability_execution": {"job_id": job_id, "status": job.status}})
        result = completion_result(job)
        result.tool_type = tool_type
        body = job.result_summary or job.last_error or "(no output)"
        cursor = str(call.get("cursor") or "")
        offset = 0
        if cursor:
            try:
                prefix, position = cursor.rsplit(":", 1)
                offset = int(position)
                if prefix != job_id or not 0 <= offset <= len(body):
                    raise ValueError("invalid_cursor")
            except ValueError:
                return _job_failure_result(tool_type, "invalid_task_cursor")
        # Producer pagination is already complete as a page. Do not truncate
        # its continuation; otherwise page our stored full body losslessly.
        end = len(body) if result.followup_envelope and result.followup_envelope.producer_bounded else min(len(body), offset + 12000)
        next_cursor = f"{job_id}:{end}" if end < len(body) else None
        work = self.completion_work.current(owner=owner, character_pack_id=job.character_pack_id,
            channel=job.channel, turn_id=str(context.request_context.get("_host_origin_turn_id") or ""))
        previously_offered = work is not None and (job_id in work.offered or job_id in work.queried)
        if offset or job.model_consumed_by or previously_offered or job.scope_revoked_reason:
            result.stream_events, result.state_updates, result.model_image_inputs, result.raw_turns = [], {}, [], []
        if next_cursor or offset:
            result.followup_context = f"job_id={job_id}; status={job.status}; next_cursor={next_cursor}\n" + body[offset:end]
            result.followup_envelope = ToolFollowupEnvelope(content=result.followup_context,
                producer_bounded=True, complete=next_cursor is None,
                continuation={"tool": "exec_status", "run_id": job_id, "cursor": next_cursor} if next_cursor else None)
        result.state_updates["capability_execution"] = {"job_id": job_id, "status": job.status, "next_cursor": next_cursor}
        self.completion_work.query(job, consumed_by=str(context.request_context.get("_host_origin_turn_id") or context.current_user_source_id or context.invocation_id))
        scope = context.execution_scope
        if scope is not None and scope.pending_work is not None:
            scope.pending_work.acknowledge(job_id)
        return result
