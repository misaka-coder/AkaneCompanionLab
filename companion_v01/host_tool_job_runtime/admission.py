"""Admit long tools, retain the selected handler and register durable work."""
from __future__ import annotations

import uuid
from typing import Any, Mapping

from ..client_protocol import ClientMode
from ..host_jobs import HostJob, HostJobOwner
from ..tool_handlers.core import ToolExecutionContext, ToolExecutionResult
from ..tool_continuation import declared_followup
from .contracts import HOST_TOOL_JOB_SOURCE, logger, _fingerprint, _safe_request_context, _job_accepted_result, _job_failure_result


class ToolJobAdmissionMixin:
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
        managed: Any = None,
    ) -> ToolExecutionResult:
        try:
            selected = handler or self._resolve_handler(
                capability_id=capability_id, context=context, domain_profile_id=domain_profile_id)
            retain = getattr(selected, "retain_invocation", None)
            selected = retain() if callable(retain) else selected
        except Exception as exc:
            logger.error("failed to resolve long tool before admission: %s (%s)", capability_id, type(exc).__name__)
            return _job_failure_result(capability_id, "long_tool_handler_resolution_failed")
        if selected is None:
            return _job_failure_result(capability_id, "long_tool_handler_unavailable")
        try:
            return self._submit_retained(capability_id=capability_id, invocation_id=invocation_id,
                call=call, context=context, domain_profile_id=domain_profile_id, turn_id=turn_id,
                selected_handler=selected, managed=managed)
        finally:
            self._release_handler(selected)

    def _submit_retained(self, *, capability_id, invocation_id, call, context,
                         domain_profile_id, turn_id, selected_handler, managed=None):
        conversation_ref = ""
        task_scope = context.execution_scope
        origin_turn_id = str(turn_id or context.request_context.get("_host_origin_turn_id") or "")
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
        if managed is not None:
            # Recovery must never reconstruct a different grant/target or
            # repeat an ordinary action whose original binding was lost.
            payload["managed_invocation"] = True
        if task_scope is not None:
            payload["task_scope"] = {"task_id": task_scope.task_id, "working_directory": task_scope.working_directory}
        coordinator = getattr(self.engine, "turn_coordinator", None)
        parent_token = coordinator.active_token(context.profile_user_id, context.session_id) if (
            coordinator is not None and task_scope is None and context.result_consumer == "model") else ""
        if parent_token:
            payload["parent_turn_token"] = parent_token
        try:
            fingerprint = _fingerprint(normalized_call)
        except (TypeError, ValueError):
            return _job_failure_result(capability_id, "host_job_payload_not_json_safe")
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
            turn_id=str(origin_turn_id or context.current_user_source_id or invocation_id or ""),
            tool_call_id=str(invocation_id or ""),
            completion_mode=completion_mode,
            memory_mode=memory_mode,
        )
        if not created.get("ok"):
            return _job_failure_result(capability_id, str(created.get("reason") or "host_job_create_failed"))

        job_id = str(created.get("job_id") or "")
        owner = HostJobOwner(context.profile_user_id, context.session_id)
        work = self.completion_work.current(owner=owner, character_pack_id=context.character_pack_id,
            channel=context.client_mode, turn_id=origin_turn_id) if task_scope is None else None
        if work is not None:
            try:
                admitted_job = self.store.get(job_id, owner=owner)
                if admitted_job is not None:
                    self.completion_work.track(admitted_job, wait=False if managed is not None else not bool(call.get("finish_turn")))
            except Exception as exc:
                # Registration already happened. A failed read cannot erase it
                # or prevent the worker/idle completion path from settling it.
                logger.error("completion reference unavailable: %s", type(exc).__name__)
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
                if job.model_consumed_by:
                    return {"status": job.status, "already_observed": True}
                from .results import completion_result
                return {"status": job.status, "summary": job.result_summary,
                        "reason": job.last_error, "artifacts": list(job.artifacts),
                        "_execution_result": completion_result(job)}
            task_scope.pending_work.track(job_id, inspect=inspect,
                                          cancel=lambda: self.store.request_cancel(job_id, owner=owner))
        existing = created.get("job")
        existing_status = str(getattr(existing, "status", "") or "")
        if created.get("status") != "duplicate" or existing_status == "queued":
            # Freeze outside the job lock: publication revokes jobs while
            # holding the generation lock, so the reverse order can deadlock.
            retain = getattr(selected_handler, "retain_invocation", None)
            try:
                candidate = retain() if callable(retain) else selected_handler
            except Exception as exc:
                logger.error("failed to retain long tool worker: %s (%s)", capability_id, type(exc).__name__)
                return self._submission_failed(
                    capability_id, job_id, owner=HostJobOwner(context.profile_user_id, context.session_id),
                    reason="long_tool_handler_retention_failed", settle_queued=created.get("status") != "duplicate")
            with self._lock:
                admitted = self._admitted_handlers.setdefault(job_id, candidate)
                if managed is not None:
                    self._managed_calls.setdefault(job_id, managed)
            if admitted is not candidate:
                self._release_handler(candidate)
            scheduled = self._schedule(job_id)
            if not scheduled.get("ok"):
                with self._lock:
                    removed = self._admitted_handlers.pop(job_id, None)
                    self._managed_calls.pop(job_id, None)
                self._release_handler(removed)
                return self._submission_failed(
                    capability_id, job_id, owner=HostJobOwner(context.profile_user_id, context.session_id),
                    reason=str(scheduled.get("reason") or "host_job_schedule_failed"),
                    settle_queued=created.get("status") != "duplicate")
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
        return result

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
