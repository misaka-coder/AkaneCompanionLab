"""Deployment resource limits and one shared budget per execution chain."""

from contextvars import ContextVar
from dataclasses import dataclass, field
import json
import threading

from capcore import CapabilityResult


current_resource_budget = ContextVar("current_resource_budget", default=None)


@dataclass(frozen=True)
class ExecutionResourcePolicy:
    max_input_bytes: int | None = None
    max_dependency_depth: int | None = 32
    max_dependency_calls: int | None = 256
    configured_fields: tuple[str, ...] = field(default=(), repr=False)

    def __post_init__(self):
        for name in self.fields():
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 1):
                raise ValueError("execution_resource_policy_invalid")

    @staticmethod
    def fields():
        return ("max_input_bytes", "max_dependency_depth", "max_dependency_calls")

    @classmethod
    def from_config(cls, value):
        try:
            raw = json.loads(value) if isinstance(value, str) else value
        except (TypeError, ValueError):
            raise ValueError("execution_resource_policy_invalid") from None
        if raw is None:
            raw = {}
        if not isinstance(raw, dict) or set(raw) - set(cls.fields()):
            raise ValueError("execution_resource_policy_invalid")
        return cls(**raw, configured_fields=tuple(sorted(raw)))

    def as_dict(self):
        return {"policy_id": "deployment.execution_resources.v1", "limits": {
            name: {"value": getattr(self, name), "source": "deployment_config" if name in self.configured_fields else "deployment_default"}
            for name in self.fields()}}


class ExecutionResourceBudget:
    def __init__(self, policy):
        self.policy = policy
        self._calls = 0
        self._lock = threading.Lock()

    def snapshot(self, *, depth):
        with self._lock:
            return {**self.policy.as_dict(), "used_dependency_calls": self._calls, "dependency_depth": depth,
                "remaining_dependency_calls": None if self.policy.max_dependency_calls is None else max(0, self.policy.max_dependency_calls - self._calls),
                "remaining_dependency_depth": None if self.policy.max_dependency_depth is None else max(0, self.policy.max_dependency_depth - depth)}

    def _exceeded(self, name, actual):
        limit = getattr(self.policy, name)
        if limit is None or actual <= limit:
            return None
        return CapabilityResult(is_error=True, status="resource_exhausted", reason="execution_resource_limit_exceeded",
            content={"policy_id": self.policy.as_dict()["policy_id"], "phase": "before_dispatch",
                     "budget": name, "actual": actual, "limit": limit,
                     "source": self.policy.as_dict()["limits"][name]["source"], "execution_status": "not_started", "scope": "target_call",
                     "retryable": False, "recovery": "reduce_work_or_change_deployment_resource_policy"})

    def input_failure(self, arguments):
        if self.policy.max_input_bytes is None:
            return None
        try:
            size = len(json.dumps(arguments, allow_nan=False, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        except (TypeError, ValueError, UnicodeError):
            # Argument types remain owned by the normal schema/transport gate.
            return None
        return self._exceeded("max_input_bytes", size)

    def reserve_dependency(self, *, depth):
        with self._lock:
            failed = self._exceeded("max_dependency_depth", depth) or self._exceeded("max_dependency_calls", self._calls + 1)
            if failed is None:
                self._calls += 1
            return failed


class ExecutionResourceLimitExceeded(Exception):
    def __init__(self, result):
        self.result = result
        super().__init__(result.reason)


def resource_limit_tool_result(tool_id, details):
    from .tool_handlers.core import ToolExecutionResult
    result = CapabilityResult(is_error=True, status="resource_exhausted", reason="execution_resource_limit_exceeded", content=details)
    return ToolExecutionResult(tool_type=tool_id, capability_result=result,
        followup_context=json.dumps(result.as_dict(), ensure_ascii=False),
        stream_events=[{"type": "capability_execution_result", "tool_type": tool_id,
                        "status": result.status, "reason": result.reason, "resource_limit": details}])


def tool_input_arguments(handler, call):
    project = getattr(handler, "call_arguments", None)
    if callable(project):
        return project(call)
    return {key: value for key, value in call.items() if key != "type"}


def dispatch_with_resource_policy(policy, tool_id, arguments, dispatch):
    """The broker's single input gate for model, program and job dispatch."""
    budget = current_resource_budget.get() or ExecutionResourceBudget(policy)
    token = current_resource_budget.set(budget)
    try:
        failed = budget.input_failure(arguments)
        if failed is not None:
            failed.content["target"] = tool_id
            raise ExecutionResourceLimitExceeded(failed)
        return dispatch()
    finally:
        current_resource_budget.reset(token)
