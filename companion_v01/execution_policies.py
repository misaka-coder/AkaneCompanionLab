"""Deployer-selected pure policy services around the existing broker dispatch."""
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass, replace
import json
import re
import threading
import time

from capcore import CapabilityResult, build_tool_spec, validate_tool_spec_result
from akane_plugin import ServiceDependency
from akane_plugin.policies import (
    POLICY_REQUEST_SCHEMA, POLICY_DECISION_SCHEMA, POLICY_OBSERVATION_SCHEMA,
    POLICY_SERVICE_PREFIX, POLICY_TRANSFORM_SCHEMA, POLICY_PRESENTATION_SCHEMA, POLICY_PRESENTATION_RESULT_SCHEMA,
    POLICY_WRAP_REQUEST_SCHEMA, POLICY_WRAP_RESULT_SCHEMA, policy_service_id,
)
from akane_plugin.service_contracts import service_method_info, service_target


policy_evaluation = ContextVar("execution_policy_evaluation", default=False)
_result_transform = ContextVar("execution_policy_result_transform", default=None)
_result_presentation = ContextVar("execution_policy_result_presentation", default=None)


def present_execution_result(tool_id, spec, result, rendered):
    presenter = _result_presentation.get()
    if presenter is None or policy_evaluation.get():
        return rendered
    return presenter(tool_id, spec, result, rendered)


def transform_execution_result(tool_id, spec, result):
    """Host-only pre-projection handoff; policy dependency calls cannot inherit it."""
    transform = _result_transform.get()
    if transform is None or policy_evaluation.get():
        return result
    return transform(tool_id, spec, result)


def _json_copy(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _public_reason(reason, fallback):
    return reason if isinstance(reason, str) and re.fullmatch(r"[a-z][a-z0-9_.-]{0,127}", reason) else fallback


def policy_tool_spec(handler):
    getter = getattr(handler, "tool_spec", None)
    return getter() if callable(getter) else None


def valid_policy_descriptor(descriptor):
    info = service_method_info(descriptor)
    if not info or not info["service_id"].startswith(POLICY_SERVICE_PREFIX) or info["version"] != 1:
        return False
    if descriptor.effects or descriptor.risk != "low" or descriptor.confirm != "never" or descriptor.outputs:
        return False
    schemas = {"before": (POLICY_REQUEST_SCHEMA, POLICY_DECISION_SCHEMA),
               "wrap": (POLICY_WRAP_REQUEST_SCHEMA, POLICY_WRAP_RESULT_SCHEMA),
               "transform": (POLICY_OBSERVATION_SCHEMA, POLICY_TRANSFORM_SCHEMA),
               "present": (POLICY_PRESENTATION_SCHEMA, POLICY_PRESENTATION_RESULT_SCHEMA),
               "observe": (POLICY_OBSERVATION_SCHEMA, {"type": "null"})}
    if info["method"] not in schemas:
        return False
    spec = build_tool_spec(descriptor)
    return (spec.input_schema, spec.output_schema) == schemas[info["method"]]


@dataclass(frozen=True)
class PolicySelection:
    policy_id: str
    options: dict

    @property
    def dependency(self):
        return ServiceDependency(policy_service_id(self.policy_id), 1)


def load_execution_policies(value):
    try:
        raw = json.loads(value) if isinstance(value, str) else value
        if not isinstance(raw, list):
            raise ValueError
        selected, ids = [], set()
        for item in raw:
            if not isinstance(item, dict) or set(item) - {"policy_id", "options"}:
                raise ValueError
            policy_id = item["policy_id"]
            policy_service_id(policy_id)
            if policy_id in ids or not isinstance(item.get("options", {}), dict):
                raise ValueError
            ids.add(policy_id)
            selected.append(PolicySelection(policy_id, _json_copy(item.get("options", {}))))
        return tuple(selected)
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise ValueError("execution_policy_configuration_invalid") from None


class ExecutionPolicyRejected(Exception):
    def __init__(self, policy_id, target, reason, *, decision=False, phase="before",
                 execution_status="not_started", scope="target_call", retryable=False,
                 execution_receipt=None):
        self.details = {"policy_id": policy_id, "phase": phase, "target": target,
            "reason": reason, "execution_status": execution_status, "scope": scope,
            "retryable": bool(retryable)}
        if execution_receipt is not None:
            self.details["execution_receipt"] = execution_receipt
        self.status = "policy_rejected" if decision else "policy_failed"
        super().__init__("execution_policy_rejected" if decision else "execution_policy_failed")


class ExecutionPolicyPipeline:
    def __init__(self, selections=(), *, source=None):
        self.selections = tuple(selections)
        self.source = source
        self._diagnostics = deque(maxlen=128)
        self._lock = threading.Lock()

    def _record(self, policy_id, phase, target, reason):
        # Diagnostic identifiers only: no arguments, results, options or exceptions.
        reason = _public_reason(reason, "execution_policy_callback_failed")
        with self._lock:
            self._diagnostics.append({"policy_id": policy_id, "phase": phase, "target": target, "reason": reason})

    def snapshot(self):
        policies = []
        for selected in self.selections:
            try:
                methods = self._resolve(selected, "")
                stages, reason = list(methods), ""
            except ExecutionPolicyRejected as exc:
                stages, reason = [], exc.details["reason"]
            policies.append({"policy_id": selected.policy_id, "service_id": selected.dependency.service_id,
                "version": 1, "order": len(policies), "stages": stages,
                "status": "available" if not reason else "unavailable", "reason": reason})
        with self._lock:
            return {"policies": policies, "diagnostics": [dict(item) for item in self._diagnostics]}

    def _resolve(self, selected, target):
        try:
            return self._resolve_service(selected, target)
        except ExecutionPolicyRejected:
            raise
        except Exception:
            raise ExecutionPolicyRejected(selected.policy_id, target, "execution_policy_unavailable") from None

    def _resolve_service(self, selected, target):
        if self.source is None:
            raise ExecutionPolicyRejected(selected.policy_id, target, "execution_policy_unavailable")
        catalog = self.source.list_services(selected.dependency.service_id)
        contract = next((item for item in catalog if item["version"] == 1), None)
        if not contract or contract["status"] != "available":
            raise ExecutionPolicyRejected(selected.policy_id, target, "execution_policy_unavailable")
        provider = next(item for item in contract["providers"] if item["plugin_id"] == contract["selected_provider"])
        declared = {item["name"] for item in provider["methods"]}
        if not declared or declared - {"before", "wrap", "transform", "present", "observe"}:
            raise ExecutionPolicyRejected(selected.policy_id, target, "execution_policy_contract_invalid")
        methods = {}
        for stage in ("before", "wrap", "transform", "present", "observe"):
            if stage not in declared:
                continue
            handler, _ = self.source.resolve_service_handler(service_target(selected.dependency.service_id, stage))
            if handler is None or not valid_policy_descriptor(handler.descriptor):
                raise ExecutionPolicyRejected(selected.policy_id, target, "execution_policy_contract_invalid")
            methods[stage] = handler
        return methods

    @staticmethod
    def _call(handler, request):
        from .tool_handlers.core import ToolExecutionContext
        token = policy_evaluation.set(True)
        try:
            # Evaluation has no user/conversation authority. Dependency callbacks
            # retain the existing global-scope pure-service admission.
            context = ToolExecutionContext("", "", int(time.time()), {}, result_consumer="program", global_scope=True)
            call = handler.normalize_call({"type": handler.tool_type, "arguments": _json_copy(request)})
            result = handler.execute(call=call, context=context).capability_result
            return result
        finally:
            policy_evaluation.reset(token)

    @staticmethod
    def _outcome(raw):
        capability = raw if isinstance(raw, CapabilityResult) else getattr(raw, "capability_result", None)
        if isinstance(capability, CapabilityResult):
            return {"status": capability.status, "reason": capability.reason or "",
                    **({"value": _json_copy(capability.value)} if capability.has_value else {})}
        from .tool_handlers.core import ToolExecutionResult
        if isinstance(raw, ToolExecutionResult):
            for event in raw.stream_events:
                if event.get("type") == "capability_approval_required":
                    return {"status": "approval_required", "reason": "capability_approval_required"}
                if event.get("type") == "capability_execution_result":
                    return {"status": str(event.get("status") or "returned"), "reason": str(event.get("reason") or "")}
            # A legacy presentation/request is not proof of a completed action.
            return {"status": "returned", "reason": ""}
        if isinstance(raw, dict):
            return {"status": str(raw.get("status") or ("failed" if raw.get("ok") is False else "completed")),
                    "reason": str(raw.get("reason") or "")}
        return {"status": str(getattr(raw, "status", "execution_unknown")), "reason": str(getattr(raw, "reason", ""))}

    def _transform_result(self, entries, request, tool_id, spec, original):
        if tool_id != request["tool_id"] or original.is_error or not original.has_value:
            return original
        current = original
        for selected, methods in entries:
            if "transform" not in methods:
                continue
            reason = "execution_policy_transform_failed"
            try:
                response = self._call(methods["transform"], {**request, "options": selected.options,
                    "outcome": self._outcome(current)})
                if response is None or response.is_error or not response.has_value:
                    reason = _public_reason(getattr(response, "reason", None), reason)
                    raise ValueError
                decision = response.value
                if decision["action"] == "keep":
                    continue
                value = _json_copy(decision["value"])
                if spec is None or not validate_tool_spec_result(spec, value).ok:
                    reason = "execution_policy_result_schema_mismatch"
                    raise ValueError
                # Rebuild presentation from the new value. Producer prose may
                # describe the old value; policy data cannot mint artifact metadata.
                content = {"value": value}
                if isinstance(original.content, dict) and isinstance(original.content.get("managed_artifacts"), list):
                    content["managed_artifacts"] = _json_copy(original.content["managed_artifacts"])
                current = replace(current, value=value, content=content)
            except Exception:
                details = {"policy_id": selected.policy_id, "phase": "transform", "target": tool_id,
                    "invocation_id": request["invocation_id"], "reason": reason,
                    "execution_status": original.status or "ok", "scope": "result_processing", "retryable": False}
                self._record(selected.policy_id, "transform", tool_id, reason)
                return CapabilityResult(is_error=True, status="result_processing_failed",
                    reason="execution_policy_result_processing_failed",
                    content={"policy_failure": details, "execution_receipt": original.as_dict()})
        return current

    @staticmethod
    def _execution_receipt(raw):
        """Return a bounded, JSON-safe receipt when a post-dispatch policy fails."""
        capability = raw if isinstance(raw, CapabilityResult) else getattr(raw, "capability_result", None)
        if isinstance(capability, CapabilityResult):
            return {"capability_result": capability.as_dict()}
        if isinstance(raw, dict):
            return {key: raw.get(key) for key in ("status", "reason") if key in raw}
        return {key: getattr(raw, key) for key in ("status", "reason") if hasattr(raw, key)}

    @staticmethod
    def _retryable_outcome(raw, outcome):
        capability = raw if isinstance(raw, CapabilityResult) else getattr(raw, "capability_result", None)
        if isinstance(capability, CapabilityResult):
            if capability.is_error:
                return True
        status = str((outcome or {}).get("status") or "").strip().lower()
        return status in {"error", "failed", "execution_unknown", "timed_out", "timeout",
                          "unavailable", "unavailable_before_dispatch", "resource_exhausted"}

    @staticmethod
    def _wrap_response(result, *, phase):
        if result is None or result.is_error or not result.has_value or not isinstance(result.value, dict):
            return None
        action = result.value.get("action")
        if phase == "before":
            if action != "continue":
                return None
            max_attempts = result.value.get("max_attempts", 1)
            if type(max_attempts) is not int or not 1 <= max_attempts <= 8:
                return None
            return max_attempts
        if action not in {"return", "retry"}:
            return None
        return action

    def _wrap_before(self, entries, request, tool_id, attempt, previous_outcome):
        max_attempts = 8
        for selected, methods in entries:
            handler = methods.get("wrap")
            if handler is None:
                continue
            payload = {**request, "options": selected.options, "phase": "before", "attempt": attempt}
            if previous_outcome is not None:
                payload["outcome"] = _json_copy(previous_outcome)
            try:
                result = self._call(handler, payload)
                value = self._wrap_response(result, phase="before")
                if value is None:
                    reason = _public_reason(getattr(result, "reason", None), "execution_policy_wrapper_failed")
                    raise ExecutionPolicyRejected(selected.policy_id, tool_id, reason, phase="wrap")
                max_attempts = min(max_attempts, value)
            except ExecutionPolicyRejected:
                raise
            except Exception:
                raise ExecutionPolicyRejected(selected.policy_id, tool_id, "execution_policy_wrapper_failed", phase="wrap") from None
        return max_attempts

    def _wrap_after(self, entries, request, tool_id, attempt, outcome, raw):
        has_wrapper = False
        retry_votes = True
        for selected, methods in entries:
            handler = methods.get("wrap")
            if handler is None:
                continue
            has_wrapper = True
            try:
                result = self._call(handler, {**request, "options": selected.options,
                    "phase": "after", "attempt": attempt, "outcome": _json_copy(outcome)})
                action = self._wrap_response(result, phase="after")
                if action is None:
                    reason = _public_reason(getattr(result, "reason", None), "execution_policy_wrapper_failed")
                    raise ExecutionPolicyRejected(selected.policy_id, tool_id, reason, phase="wrap",
                        execution_status=str((outcome or {}).get("status") or "execution_unknown"),
                        scope="lifecycle", execution_receipt=self._execution_receipt(raw))
                retry_votes = retry_votes and action == "retry"
            except ExecutionPolicyRejected:
                raise
            except Exception:
                raise ExecutionPolicyRejected(selected.policy_id, tool_id, "execution_policy_wrapper_failed", phase="wrap",
                    execution_status=str((outcome or {}).get("status") or "execution_unknown"),
                    scope="lifecycle", execution_receipt=self._execution_receipt(raw)) from None
        return has_wrapper and retry_votes

    def _present_result(self, entries, request, tool_id, spec, original, rendered):
        if tool_id != request["tool_id"] or original.is_error or not rendered:
            return rendered
        current = rendered
        for selected, methods in entries:
            if "present" not in methods:
                continue
            try:
                response = self._call(methods["present"], {**request, "options": selected.options,
                    "outcome": self._outcome(original), "rendered": current})
                if response is None or response.is_error or not response.has_value:
                    raise ValueError
                decision = response.value
                if decision["action"] == "replace":
                    current = _json_copy(decision["text"])
            except Exception:
                self._record(selected.policy_id, "present", tool_id, "execution_policy_presenter_failed")
        return current

    def execute(self, *, tool_id, invocation_id, arguments, dispatch, executor="server_local", spec=None, client_mode=""):
        if not self.selections or policy_evaluation.get():
            return dispatch()
        if self.source is None:
            raise ExecutionPolicyRejected(self.selections[0].policy_id, tool_id, "execution_policy_unavailable")
        with self.source.service_scope():
            entries = [(selected, self._resolve(selected, tool_id)) for selected in self.selections]
            try:
                policy_arguments = _json_copy(arguments)
            except (TypeError, ValueError, UnicodeError):
                raise ExecutionPolicyRejected(entries[0][0].policy_id, tool_id, "execution_policy_input_invalid") from None
            request = {"tool_id": tool_id, "invocation_id": invocation_id, "arguments": policy_arguments,
                "executor": executor, "effects": list(getattr(spec, "effects", ()) or ()),
                "risk": str(getattr(spec, "risk", "") or ""), "client_mode": str(client_mode or "")}
            outcome = {"status": "execution_unknown", "reason": "execution_outcome_unavailable"}
            raw = None
            try:
                for selected, methods in entries:
                    if "before" not in methods:
                        continue
                    try:
                        result = self._call(methods["before"], {**request, "options": selected.options})
                        if result is None or result.is_error or not result.has_value:
                            raise ExecutionPolicyRejected(selected.policy_id, tool_id,
                                _public_reason(getattr(result, "reason", None), "execution_policy_callback_failed"))
                        decision = result.value
                        if decision["decision"] == "deny":
                            raise ExecutionPolicyRejected(selected.policy_id, tool_id,
                                decision.get("reason", "deployment_policy_denied"), decision=True)
                    except ExecutionPolicyRejected:
                        raise
                    except Exception:
                        raise ExecutionPolicyRejected(selected.policy_id, tool_id, "execution_policy_callback_failed") from None

                has_wrapper = any("wrap" in methods for _, methods in entries)
                max_attempts = 8 if has_wrapper else 1
                attempt = 1
                token = _result_transform.set(lambda target, target_spec, result:
                    self._transform_result(entries, request, target, target_spec, result))
                presentation_token = _result_presentation.set(lambda target, target_spec, result, rendered:
                    self._present_result(entries, request, target, target_spec, result, rendered))
                try:
                    while True:
                        previous_outcome = outcome if attempt > 1 else None
                        if has_wrapper:
                            max_attempts = min(max_attempts,
                                self._wrap_before(entries, request, tool_id, attempt, previous_outcome))
                        for selected, methods in entries:
                            if any(not handler.adapter.is_live(handler.tool_type) for handler in methods.values()):
                                raise ExecutionPolicyRejected(selected.policy_id, tool_id, "execution_policy_revoked")
                        dispatch_error = None
                        try:
                            raw = dispatch()
                        except Exception as exc:
                            dispatch_error = exc
                            raw = None
                            outcome = {"status": "execution_unknown", "reason": "execution_outcome_unavailable"}
                        else:
                            try:
                                outcome = self._outcome(raw)
                            except Exception:
                                # An observer projection cannot rewrite a completed action.
                                self._record(entries[0][0].policy_id, "observe", tool_id,
                                    "execution_policy_observation_unavailable")
                                outcome = {"status": "execution_unknown", "reason": "execution_outcome_unavailable"}
                        retry_requested = self._wrap_after(entries, request, tool_id, attempt, outcome, raw) if has_wrapper else False
                        if retry_requested and attempt < max_attempts:
                            if request["effects"]:
                                self._record(entries[0][0].policy_id, "wrap", tool_id,
                                    "execution_policy_retry_forbidden_for_effectful_tool")
                            elif not self._retryable_outcome(raw, outcome):
                                self._record(entries[0][0].policy_id, "wrap", tool_id,
                                    "execution_policy_retry_outcome_not_retryable")
                            else:
                                revoked = any(
                                    not handler.adapter.is_live(handler.tool_type)
                                    for _, methods in entries for handler in methods.values()
                                )
                                if not revoked:
                                    attempt += 1
                                    continue
                                self._record(entries[0][0].policy_id, "wrap", tool_id,
                                    "execution_policy_retry_revoked")
                        elif retry_requested and attempt >= max_attempts:
                            self._record(entries[0][0].policy_id, "wrap", tool_id,
                                "execution_policy_retry_exhausted")
                        if dispatch_error is not None:
                            raise dispatch_error
                        return raw
                finally:
                    _result_transform.reset(token)
                    _result_presentation.reset(presentation_token)
            except ExecutionPolicyRejected as exc:
                outcome = {"status": exc.status, "reason": str(exc)}
                self._record(exc.details["policy_id"], exc.details.get("phase", "before"), tool_id, exc.details["reason"])
                raise
            finally:
                for selected, methods in entries:
                    if "observe" not in methods:
                        continue
                    try:
                        result = self._call(methods["observe"], {**request, "options": selected.options, "outcome": outcome})
                        if result is None or result.is_error:
                            self._record(selected.policy_id, "observe", tool_id,
                                _public_reason(getattr(result, "reason", None), "execution_policy_observer_failed"))
                    except Exception:
                        self._record(selected.policy_id, "observe", tool_id, "execution_policy_observer_failed")


def policy_failure_tool_result(tool_id, status, reason, details):
    from .tool_handlers.core import ToolExecutionResult
    result = CapabilityResult(is_error=True, status=status, reason=reason, content=details)
    return ToolExecutionResult(tool_type=tool_id, capability_result=result,
        followup_context=json.dumps(result.as_dict(), ensure_ascii=False),
        stream_events=[{"type": "capability_execution_result", "tool_type": tool_id,
                        "status": status, "reason": reason, "policy_failure": details}])
