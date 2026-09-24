"""Execute accepted long-running tool calls behind the durable Job authority."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import uuid
from typing import Any, Callable, Mapping
from capcore import CapabilityResult

from .client_protocol import ClientMode
from .host_jobs import HostJob, HostJobOwner, HostJobStore
from .tool_handlers.core import ToolExecutionContext, ToolExecutionResult
from .tool_continuation import bind_result_followup, declared_followup
from .plugin_result_projection import sanitize_capability_result


logger = logging.getLogger("akane.host_tool_jobs")
HOST_TOOL_JOB_SOURCE = "tool"


class HostToolJobRuntime:
    """Persist, schedule, and finish server-local long tool calls.

    The existing ``BackgroundTaskRunner`` remains the executor.  This runtime
    only joins it to the durable Job store and can therefore reconstruct work
    that has not started after a host restart. Unconfirmed running work is
    settled by the shared store, never replayed by this executor.
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

    def accepts(self, *, handler: Any, context: ToolExecutionContext) -> bool:
        spec_getter = getattr(handler, "tool_spec", None)
        try:
            spec = spec_getter() if callable(spec_getter) else None
        except Exception:
            spec = None
        return (
            (context.execution_scope is None or context.execution_scope.pending_work is not None)
            and
            str(getattr(spec, "execution_class", "sync") or "sync").strip().lower() == "long_task"
            and str(getattr(handler, "tool_type", "") or "").strip() != "exec_run"
            and str(context.client_mode or "").strip().lower()
            in {ClientMode.QQ_TEXT.value, ClientMode.DESKTOP_PET.value}
        )

    def submit(
        self,
        *,
        capability_id: str,
        invocation_id: str,
        call: Mapping[str, Any],
        context: ToolExecutionContext,
        domain_profile_id: str = "",
        turn_id: str = "",
        handler: Any = None,
    ) -> ToolExecutionResult:
        try:
            selected = handler or self._resolve_handler(
                capability_id=capability_id, context=context, domain_profile_id=domain_profile_id)
            retain = getattr(selected, "retain_invocation", None)
            selected = retain() if callable(retain) else selected
        except Exception:
            logger.exception("failed to resolve long tool before admission: %s", capability_id)
            return _job_failure_result(capability_id, "long_tool_handler_resolution_failed")
        if selected is None:
            return _job_failure_result(capability_id, "long_tool_handler_unavailable")
        try:
            return self._submit_retained(capability_id=capability_id, invocation_id=invocation_id,
                call=call, context=context, domain_profile_id=domain_profile_id, turn_id=turn_id,
                selected_handler=selected)
        finally:
            self._release_handler(selected)

    def _submit_retained(self, *, capability_id, invocation_id, call, context,
                         domain_profile_id, turn_id, selected_handler):
        conversation_ref = ""
        task_scope = context.execution_scope
        if task_scope is not None:
            conversation_ref = "task:" + task_scope.task_id
        elif self.conversation_ref_issuer is not None:
            try:
                conversation_ref = str(self.conversation_ref_issuer(context) or "").strip()
            except Exception:
                conversation_ref = ""
        if not conversation_ref:
            return _job_failure_result(capability_id, "job_conversation_context_unavailable")

        normalized_call = {
            str(key): value
            for key, value in dict(call or {}).items()
            if not str(key).startswith("_tool_")
        }
        admit = getattr(selected_handler, "admit_execution", None)
        if callable(admit):
            try:
                admission = admit(call=normalized_call, context=context)
            except Exception:
                logger.exception("long tool admission failed: %s", capability_id)
                return _job_failure_result(capability_id, "long_tool_admission_failed")
            blocked = getattr(admission, "result", None)
            if isinstance(blocked, ToolExecutionResult):
                return blocked
            admitted_call = getattr(admission, "call", None)
            if not isinstance(admitted_call, Mapping):
                return _job_failure_result(capability_id, "long_tool_admission_invalid")
            normalized_call = dict(admitted_call)
        policy = self._background_policy(selected_handler)
        if policy is None:
            return _job_failure_result(capability_id, "long_tool_policy_invalid")
        completion_mode, memory_mode = policy
        raw = getattr(getattr(selected_handler, "descriptor", None), "raw", {})
        default_followup = declared_followup(raw) if isinstance(raw, Mapping) and raw else (
            "none" if completion_mode == "silent" else "required")
        # Model-bound jobs publish a durable completion fact. The terminal
        # result decides whether that fact needs a model or direct delivery.
        completion_mode = "agent"
        if task_scope is not None:
            # The live task consumes the terminal fact. Never wake its parent
            # character, including when recovery later settles this job.
            completion_mode = "silent"
            memory_mode = "current_turn"
        payload = {
            "call": normalized_call,
            "client_mode": str(context.client_mode or ""),
            "domain_profile_id": str(domain_profile_id or ""),
            "current_user_source_id": str(context.current_user_source_id or ""),
            "request_context": _safe_request_context(context.request_context),
            "followup": {"default": default_followup, "consumer": context.result_consumer,
                         "required": context.model_result_required or task_scope is not None},
        }
        if task_scope is not None:
            payload["task_scope"] = {"task_id": task_scope.task_id, "working_directory": task_scope.working_directory}
        coordinator = getattr(self.engine, "turn_coordinator", None)
        parent_token = coordinator.active_token(context.profile_user_id, context.session_id) if (
            coordinator is not None and task_scope is None and context.result_consumer == "model") else ""
        if parent_token:
            payload["parent_turn_token"] = parent_token
        fingerprint = _fingerprint(normalized_call)
        created = self.store.create(
            owner=HostJobOwner(context.profile_user_id, context.session_id),
            capability_source=HOST_TOOL_JOB_SOURCE,
            capability_id=capability_id,
            payload=payload,
            idempotency_key=(task_scope.task_id + ":" if task_scope is not None else "") + (str(invocation_id or "").strip() or f"call_{uuid.uuid4().hex}"),
            argument_fingerprint=fingerprint,
            character_pack_id=context.character_pack_id,
            channel=context.client_mode,
            delivery_target=conversation_ref,
            turn_id=str(turn_id or context.current_user_source_id or invocation_id or ""),
            tool_call_id=str(invocation_id or ""),
            completion_mode=completion_mode,
            memory_mode=memory_mode,
        )
        if not created.get("ok"):
            return _job_failure_result(capability_id, str(created.get("reason") or "host_job_create_failed"))

        job_id = str(created.get("job_id") or "")
        check_grant = getattr(selected_handler, "invocation_grant_active", None)
        if callable(check_grant) and not check_grant():
            self.store.revoke_job_capability(job_id, owner=HostJobOwner(context.profile_user_id, context.session_id))
        if parent_token and coordinator.stop_requested(parent_token):
            self._revoke_turn(profile_user_id=context.profile_user_id, session_id=context.session_id, turn_token=parent_token)
        if task_scope is not None and task_scope.pending_work is not None:
            owner = HostJobOwner(context.profile_user_id, context.session_id)
            def inspect():
                job = self.store.get(job_id, owner=owner)
                if job is None:
                    return {"status": "unknown", "reason": "host_job_not_found"}
                if job.status in {"queued", "running"}:
                    return None
                return {"status": job.status, "summary": job.result_summary,
                        "reason": job.last_error, "artifacts": list(job.artifacts)}
            task_scope.pending_work.track(job_id, inspect=inspect,
                                          cancel=lambda: self.store.request_cancel(job_id, owner=owner))
        existing = created.get("job")
        existing_status = str(getattr(existing, "status", "") or "")
        if created.get("status") != "duplicate" or existing_status == "queued":
            # Freeze outside the job lock: publication revokes jobs while
            # holding the generation lock, so the reverse order can deadlock.
            retain = getattr(selected_handler, "retain_invocation", None)
            candidate = retain() if callable(retain) else selected_handler
            with self._lock:
                admitted = self._admitted_handlers.setdefault(job_id, candidate)
            if admitted is not candidate:
                self._release_handler(candidate)
            scheduled = self._schedule(job_id)
            if not scheduled.get("ok"):
                with self._lock:
                    removed = self._admitted_handlers.pop(job_id, None)
                self._release_handler(removed)
                claim = self.store.claim(job_id, worker_id="scheduler-failure", lease_seconds=30)
                if claim.get("ok"):
                    self.store.fail(
                        job_id,
                        claim_token=claim.get("claim_token"),
                        error=str(scheduled.get("reason") or "host_job_schedule_failed"),
                        retryable=False,
                    )
                return _job_failure_result(capability_id, str(scheduled.get("reason") or "host_job_schedule_failed"))
        elif isinstance(existing, HostJob) and existing.completion_status == "pending":
            self._schedule_completion(existing)

        result = _job_accepted_result(
            capability_id,
            job_id=job_id,
            duplicate=created.get("status") == "duplicate",
            job_status=existing_status or "queued",
            control_state=str(getattr(existing, "control_state", "") or ("paused" if existing_status == "paused" else "running")),
            followup_default=(existing.payload.get("followup") or {}).get("default", default_followup)
                if isinstance(existing, HostJob) else default_followup,
        )
        if task_scope is not None:
            result.followup_context = f"Task accepted: job_id={job_id}. Continue independent work; completion will resume this task, not the parent conversation."
        return result

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

    def bind_terminal_callback(self, callback: Callable[[HostJob], Any] | None) -> None:
        self.terminal_callback = callback

    def publish_completion(self, job: HostJob) -> dict[str, Any]:
        """Schedule one already-durable terminal fact through the shared route."""

        if not isinstance(job, HostJob) or job.completion_status != "pending":
            return {"ok": False, "status": "ignored", "reason": "completion_not_pending"}
        return self._schedule_completion(job)

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

    def _schedule_completion(self, job: HostJob) -> dict[str, Any]:
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
            stored_result = {"followup": result.followup.as_dict()} if result.followup is not None else {}
            if isinstance(result.capability_result, CapabilityResult):
                stored_result["capability_result"] = sanitize_capability_result(result.capability_result).as_dict()
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
                        error=f"long_tool_{type(exc).__name__}",
                        retryable=False,
                    )
                except Exception:
                    logger.exception("failed to settle crashed host job: %s", job_id)
            logger.exception("host long tool failed: %s", job_id)
        finally:
            with self._lock:
                self._scheduled.discard(job_id)
                removed = self._admitted_handlers.pop(job_id, None)
            self._release_handler(removed)
            # Both ordinary failures and raised exceptions produce the same
            # durable completion fact. Delivery failure never rewrites execution.
            if job is not None:
                try:
                    terminal = self.store.get(job_id, owner=job.owner)
                    if terminal is not None and terminal.completion_status == "pending":
                        self._schedule_completion(terminal)
                except Exception:
                    logger.exception("failed to schedule host job completion: %s", job_id)

    @staticmethod
    def _release_handler(handler):
        release = getattr(handler, "release_invocation", None)
        if callable(release):
            release()

    def _execute(self, job: HostJob) -> ToolExecutionResult:
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
            request_context=dict(payload.get("request_context") or {}),
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
        from .execution_resource_policy import tool_input_arguments
        from .execution_policies import policy_tool_spec
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
            from .execution_policies import policy_failure_tool_result
            return policy_failure_tool_result(job.capability_id, broker_result.status, broker_result.reason, broker_result.policy_failure)
        if getattr(broker_result, "resource_limit", None) is not None:
            from .execution_resource_policy import resource_limit_tool_result
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
        from .tool_handlers.core import TaskExecutionScope
        scope = payload.get("task_scope")
        return TaskExecutionScope(scope["working_directory"], scope["task_id"]) if isinstance(scope, dict) else None

    def _task_is_live(self, job):
        live = getattr(self.engine, "_live_task_work", {})
        return str(job.payload["task_scope"].get("task_id") or "") in live

    def _resolve_handler(
        self,
        *,
        capability_id: str,
        context: ToolExecutionContext,
        domain_profile_id: str,
    ) -> Any:
        client_payload = {"client_mode": str(context.client_mode or "")}
        client_context = self.engine._resolve_client_protocol_context(client_payload)
        handlers = self.engine._resolve_tool_handlers(
            client_context=client_context,
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            domain_profile_id=str(domain_profile_id or ""),
        )
        return handlers.get(str(capability_id or ""))

    @staticmethod
    def _background_policy(handler: Any) -> tuple[str, str] | None:
        getter = getattr(handler, "background_job_policy", None)
        try:
            policy = getter() if callable(getter) else ("agent", "timeline")
        except Exception:
            return None
        if not isinstance(policy, tuple) or len(policy) != 2:
            return None
        completion = str(policy[0] or "agent").strip().lower()
        memory = str(policy[1] or "timeline").strip().lower()
        if completion not in {"agent", "silent"}:
            return None
        if memory not in {"current_turn", "timeline"}:
            return None
        return completion, memory

    def _publish_terminal(self, job_id: str, owner: HostJobOwner) -> None:
        try:
            callback = self.terminal_callback
            if callback is None:
                return
            completed = self.store.get(job_id, owner=owner)
            if completed is None or completed.completion_status != "pending":
                return
            result = callback(completed)
            delivered = result is True or bool(getattr(result, "ok", False))
            if completed.payload.get("followup"):
                from .tool_continuation import job_followup
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
                from .tool_continuation import job_followup
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


def _safe_request_context(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    allowed = (
        "actor_stable_id",
        "actor_profile_user_id",
        "authorization_profile_user_id",
        "group_id",
        "user_id",
        "message_id",
    )
    return {
        key: source[key]
        for key in allowed
        if key in source and isinstance(source[key], (str, int, float, bool))
    }


def _fingerprint(call: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(call), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _artifact_references(result: ToolExecutionResult) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    seen: set[str] = set()
    updates = result.state_updates if isinstance(result.state_updates, dict) else {}
    execution = updates.get("capability_execution", {})
    for resource in execution.get("generated_resources", []) if isinstance(execution, Mapping) else []:
        if isinstance(resource, Mapping) and resource.get("handle"):
            handle = str(resource["handle"])
            if handle not in seen:
                seen.add(handle)
                references.append({"handle": handle, "source": "generated_resources",
                                   "sha256": str(resource.get("sha256") or "")})
    for key, value in updates.items():
        if not str(key).endswith("_handles") or not isinstance(value, (list, tuple)):
            continue
        for raw in value:
            handle = str(raw or "").strip()
            if handle and handle not in seen:
                seen.add(handle)
                references.append({"handle": handle, "source": str(key)})
    for event in result.stream_events:
        if not isinstance(event, Mapping):
            continue
        generated = event.get("generated_file")
        if not isinstance(generated, Mapping):
            continue
        handle = str(
            generated.get("generated_handle")
            or generated.get("handle")
            or generated.get("generated_id")
            or ""
        ).strip()
        if handle and handle not in seen:
            seen.add(handle)
            references.append({"handle": handle, "source": "generated_file_ready",
                               "sha256": str(generated.get("sha256") or "")})
        elif handle and generated.get("sha256"):
            for reference in references:
                if reference["handle"] == handle:
                    reference["sha256"] = str(generated["sha256"])
        mode = event.get("delivery_mode")
        if handle and isinstance(mode, str) and mode in {"file", "voice", "both"}:
            for reference in references:
                if reference["handle"] == handle:
                    reference["delivery_mode"] = mode
                    reference["send_to_user"] = event.get("send_to_user") is True
    return references


def _job_accepted_result(
    capability_id: str,
    *,
    job_id: str,
    duplicate: bool,
    job_status: str,
    control_state: str = "",
    followup_default: str = "none",
) -> ToolExecutionResult:
    status = str(job_status or "queued").strip().lower()
    registration = "任务已登记；本次没有重复启动" if duplicate else "后台任务已可靠登记"
    notification = "完成结果会保存在任务中；是否需要回复由实际结果和消费需求决定。"
    return ToolExecutionResult(
        tool_type=capability_id,
        stream_events=[{
            "type": "background_job_accepted",
            "tool_type": capability_id,
            "status": "accepted",
            "job_id": job_id,
            "job_status": status,
            "control_state": str(control_state or ("paused" if status == "paused" else "running")).strip().lower(),
            "duplicate": bool(duplicate),
        }],
        followup_context=(
            f"{registration}（job_id: {job_id}，状态: {status}）。{notification}"
            "无需停在这里轮询，可以先完成不依赖结果的内容。"
        ),
        state_updates={
            "capability_execution": {
                "tool_type": capability_id,
                "status": "accepted",
                "job_id": job_id,
                "job_status": status,
                "followup_default": followup_default,
            }
        },
    )


def _job_failure_result(capability_id: str, reason: str) -> ToolExecutionResult:
    safe_reason = str(reason or "host_job_unavailable")[:160]
    return ToolExecutionResult(
        tool_type=capability_id,
        stream_events=[{
            "type": "background_job_rejected",
            "tool_type": capability_id,
            "status": "failed",
            "reason": safe_reason,
        }],
        followup_context=(
            f"<tool_use_error>后台任务没有成功登记（{safe_reason}），因此尚未开始。"
            "请如实告诉用户，不要声称任务正在运行。</tool_use_error>"
        ),
        state_updates={
            "capability_execution": {
                "tool_type": capability_id,
                "status": "failed",
                "reason": safe_reason,
            }
        },
    )


__all__ = ["HOST_TOOL_JOB_SOURCE", "HostToolJobRuntime"]
