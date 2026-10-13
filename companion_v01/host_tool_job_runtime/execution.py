"""Execute claimed work in its original scope and settle actual worker outcomes."""
from __future__ import annotations

import threading
from capcore import CapabilityResult

from ..host_jobs import HostJob
from ..tool_handlers.core import ToolExecutionContext, ToolExecutionResult
from ..tool_continuation import bind_result_followup
from .contracts import logger, _artifact_references
from .results import stored_tool_result


class ToolJobExecutionMixin:
    def _run(self, job_id: str) -> None:
        claim_token = ""
        job: HostJob | None = None
        try:
            claim = self.store.claim(
                job_id,
                worker_id=f"tool-worker:{threading.get_ident()}",
                lease_seconds=3600,
            )
            job = claim.get("job") if isinstance(claim.get("job"), HostJob) else None
            if not claim.get("ok"):
                return
            claim_token = str(claim.get("claim_token") or "")
            if job is None:
                self.store.fail(job_id, claim_token=claim_token, error="host_job_record_invalid", retryable=False)
                return
            if job.payload.get("task_scope") and not self._task_is_live(job):
                self.store.fail(job_id, claim_token=claim_token, error="task_owner_not_running", retryable=False)
                return
            result = self._execute(job)
            status, reason = self.engine._tool_hook_result_status(result)
            stored_result = stored_tool_result(result)
            if result.followup_envelope is not None:
                envelope = result.followup_envelope
                result.followup_context = envelope.content
            if status == "cancelled":
                settled = self.store.confirm_cancelled(
                    job_id,
                    claim_token=claim_token,
                    result_summary=str(result.followup_context or ""),
                    artifacts=_artifact_references(result),
                    result=stored_result,
                )
            elif status == "succeeded":
                settled = self.store.succeed(
                    job_id,
                    claim_token=claim_token,
                    result_summary=str(result.followup_context or ""),
                    artifacts=_artifact_references(result),
                    result=stored_result,
                )
            else:
                settled = self.store.fail(
                    job_id,
                    claim_token=claim_token,
                    error=reason or status or "long_tool_failed",
                    retryable=False,
                    result_summary=str(result.followup_context or ""),
                    artifacts=_artifact_references(result),
                    result=stored_result,
                )
            if not settled.get("ok"):
                raise RuntimeError(str(settled.get("reason") or "host_job_result_not_saved"))
        except Exception as exc:
            if claim_token:
                try:
                    self.store.fail(
                        job_id,
                        claim_token=claim_token,
                        error="original_execution_context_lost_no_replay" if isinstance(exc, LookupError)
                            and str(exc) == "original_execution_context_lost_no_replay" else f"long_tool_{type(exc).__name__}",
                        retryable=False,
                    )
                except Exception:
                    logger.exception("failed to settle crashed host job: %s", job_id)
            logger.exception("host long tool failed: %s", job_id)
        finally:
            with self._lock:
                self._scheduled.discard(job_id)
                removed = self._admitted_handlers.pop(job_id, None)
                managed = self._managed_calls.get(job_id)
                if managed is not None:
                    managed.completed.set()
                    publish = managed.released
                    if publish:
                        self._managed_calls.pop(job_id, None)
                else:
                    publish = True
                self._job_condition.notify_all()
            self._release_handler(removed)
            # Both ordinary failures and raised exceptions produce the same
            # durable completion fact. Delivery failure never rewrites execution.
            if job is not None:
                try:
                    terminal = self.store.get(job_id, owner=job.owner)
                    if terminal is not None and terminal.completion_status == "pending" and publish:
                        self._schedule_completion(terminal)
                except Exception:
                    logger.exception("failed to schedule host job completion: %s", job_id)

    def _execute(self, job: HostJob) -> ToolExecutionResult:
        if job.payload.get("managed_invocation"):
            with self._lock:
                managed = self._managed_calls.get(job.job_id)
            if managed is None:
                raise LookupError("original_execution_context_lost_no_replay")
            return self._execute_managed(job, managed)
        # The worker may run after the caller has left its context. Restore
        # only the signed delivery origin; queue ownership remains physical.
        from ..owner_private_session import private_turn_scope
        payload = {"user_id": job.owner.session_id, "real_user_id": job.owner.profile_user_id,
                   "character_pack_id": job.character_pack_id,
                   "client_mode": str(job.payload.get("client_mode") or job.channel)}
        payload["_memory_desktop_ref" if payload["client_mode"] == "desktop_pet" else "_memory_qq_ref"] = job.delivery_target
        with private_turn_scope(self.engine, payload):
            return self._execute_in_scope(job, payload)

    def _execute_in_scope(self, job: HostJob, origin: dict) -> ToolExecutionResult:
        payload = job.payload if isinstance(job.payload, dict) else {}
        followup = payload.get("followup") or {"default": "none" if job.completion_mode == "silent" else "required"}
        call = dict(payload.get("call") or {})
        client_payload = {"client_mode": str(payload.get("client_mode") or job.channel)}
        client_context = self.engine._resolve_client_protocol_context(client_payload)
        with self._lock:
            handler = self._admitted_handlers.get(job.job_id)
        if handler is None:
            handlers = self.engine._resolve_tool_handlers(
                client_context=client_context,
                profile_user_id=job.owner.profile_user_id,
                session_id=job.owner.session_id,
                domain_profile_id=str(payload.get("domain_profile_id") or ""),
            )
            handler = handlers.get(job.capability_id)
        if handler is None:
            raise LookupError("long_tool_handler_unavailable")
        context = ToolExecutionContext(
            profile_user_id=job.owner.profile_user_id,
            session_id=job.owner.session_id,
            now_ts=int(job.created_at),
            visual_payload={
                "_profile_user_id": job.owner.profile_user_id,
                "_character_pack_id": job.character_pack_id,
            },
            character_pack_id=job.character_pack_id,
            current_user_source_id=str(payload.get("current_user_source_id") or ""),
            client_mode=str(payload.get("client_mode") or job.channel),
            request_context={**dict(payload.get("request_context") or {}),
                             **{key: value for key, value in origin.items() if key.startswith("_memory_")}},
            execution_scope=self._restored_task_scope(payload),
            cancel_requested=lambda: self._cancel_requested(job),
            result_consumer=followup.get("consumer", "model"),
            model_result_required=followup.get("required") is True,
            followup_default=followup["default"],
        )
        check_grant = getattr(handler, "invocation_grant_active", None)
        if callable(check_grant) and not check_grant():
            self.store.revoke_job_capability(job.job_id, owner=job.owner)
            return bind_result_followup(ToolExecutionResult(tool_type=job.capability_id,
                capability_result=CapabilityResult(is_error=True, status="cancelled", reason="plugin_capability_revoked")),
                context=context, default=followup["default"])
        execute = getattr(handler, "execute_admitted", None)
        if not callable(execute):
            execute = handler.execute
        from ..execution_resource_policy import tool_input_arguments
        from ..execution_policies import policy_tool_spec
        broker_result = self.engine.executor_broker.execute_server_local(
            tool_id=job.capability_id,
            invocation_id=job.tool_call_id or job.job_id,
            dispatch=lambda: execute(call=call, context=context),
            ledger_scope=f"{job.owner.profile_user_id}\x1f{job.owner.session_id}" + (
                "\x1f" + context.execution_scope.task_id if context.execution_scope is not None else ""),
            request_data={"arguments": call},
            input_arguments=tool_input_arguments(handler, call),
            policy_spec=policy_tool_spec(handler), policy_client_mode=context.client_mode,
        )
        if getattr(broker_result, "policy_failure", None) is not None:
            from ..execution_policies import policy_failure_tool_result
            return policy_failure_tool_result(job.capability_id, broker_result.status, broker_result.reason, broker_result.policy_failure)
        if getattr(broker_result, "resource_limit", None) is not None:
            from ..execution_resource_policy import resource_limit_tool_result
            return resource_limit_tool_result(job.capability_id, broker_result.resource_limit)
        if broker_result.status != "succeeded" or not isinstance(broker_result.result, ToolExecutionResult):
            raise RuntimeError(str(broker_result.reason or "long_tool_execution_failed"))
        if callable(check_grant) and not check_grant():
            self.store.revoke_job_capability(job.job_id, owner=job.owner)
        return bind_result_followup(broker_result.result, context=context, default=followup["default"],
                                    delivery_managed=True)

    def _cancel_requested(self, job: HostJob) -> bool:
        current = self.store.get(job.job_id, owner=job.owner)
        return bool(current is not None and (current.cancel_requested or current.scope_revoked_reason))

    @staticmethod
    def _restored_task_scope(payload):
        from ..tool_handlers.core import TaskExecutionScope
        scope = payload.get("task_scope")
        return TaskExecutionScope(scope["working_directory"], scope["task_id"]) if isinstance(scope, dict) else None

    def _task_is_live(self, job):
        live = getattr(self.engine, "_live_task_work", {})
        return str(job.payload["task_scope"].get("task_id") or "") in live
