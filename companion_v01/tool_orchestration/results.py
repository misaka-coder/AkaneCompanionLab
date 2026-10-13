"""Provider-neutral receipts, with transport and capability outcomes kept distinct."""

from __future__ import annotations

from typing import Any, Mapping

from ..execution_specs import EXEC_TOOL_SPEC_BY_ID
from ..tool_handlers.core import ToolExecutionResult, ToolFollowupEnvelope
from ..tool_invocation import ToolInvocation, ToolResultEnvelope, ValidationResult


def _final_exec_envelope(
    *,
    invocation: ToolInvocation,
    result: ToolExecutionResult,
) -> ToolResultEnvelope:
    """Build the execution envelope with an honest status mapping.

    The generic builder labels every non-None result ``ok``; for the execution
    tools the command's own outcome (failed / timed_out / cancelled /
    execution_unknown) and the approval / unavailable cases must surface on the
    envelope so nothing downstream mistakes a failed command for success.
    """
    base = tool_execution_result_to_envelope(invocation=invocation, result=result)
    capability_result = result.capability_result
    if (
        capability_result is not None
        and capability_result.is_error
        and capability_result.status
        in {"resource_exhausted", "policy_rejected", "policy_failed", "result_processing_failed"}
    ):
        detail_key = "resource_limit" if capability_result.status == "resource_exhausted" else "policy_failure"
        detail = {detail_key: capability_result.content}
        if capability_result.status == "result_processing_failed" and isinstance(capability_result.content, Mapping):
            detail = {key: capability_result.content.get(key) for key in ("policy_failure", "execution_receipt")}
        return ToolResultEnvelope(
            invocation_id=base.invocation_id,
            status="error",
            model_feedback=base.model_feedback,
            data={**dict(base.data or {}), **detail},
            events=list(base.events or []),
        )
    if capability_result is not None and capability_result.is_error:
        reason = str(capability_result.reason or capability_result.status or "tool_execution_failed")
        status = "unavailable" if reason in {
            "capability_not_available", "executor_runtime_changed", "tool_not_declared"
        } else "error"
        return ToolResultEnvelope(
            invocation_id=base.invocation_id,
            status=status,
            model_feedback=base.model_feedback,
            data={**dict(base.data or {}), "code": reason},
            events=list(base.events or []),
        )
    if invocation.name == "open_music_search":
        execution = result.state_updates.get("capability_execution") or {}
        return ToolResultEnvelope(
            invocation_id=base.invocation_id,
            status="ok" if execution.get("status") == "succeeded" else "error",
            model_feedback=base.model_feedback,
            data={**dict(base.data or {}), "status": execution.get("status"), "code": execution.get("reason")},
            events=list(base.events or []),
        )
    if str(invocation.name or "").strip() not in EXEC_TOOL_SPEC_BY_ID:
        return base
    status = "ok"
    for event in result.stream_events:
        if not isinstance(event, Mapping):
            continue
        event_type = str(event.get("type") or "").strip()
        if event_type == "capability_approval_required":
            status = "ask"
            break
        if event_type == "capability_execution_result":
            inner = str(event.get("status") or "").strip()
            if inner == "unavailable":
                status = "unavailable"
            elif inner in {"completed", "running"}:
                status = "ok"
            elif str(invocation.name or "").strip() == "exec_cancel" and inner in {"cancelled", "already_ended"}:
                status = "ok"
            elif str(invocation.name or "").strip() == "exec_input" and inner in {"idle", "pending", "written"}:
                status = "ok"
            else:
                status = "error"
            break
    return ToolResultEnvelope(
        invocation_id=base.invocation_id,
        status=status,
        model_feedback=base.model_feedback,
        data=dict(base.data or {}),
        events=list(base.events or []),
    )


def validation_result_to_envelope(
    *,
    invocation: ToolInvocation,
    validation: ValidationResult,
) -> ToolResultEnvelope:
    message = str(validation.message or validation.code or "tool_validation_failed").strip()
    return ToolResultEnvelope(
        invocation_id=invocation.id,
        status="error",
        model_feedback=f"<tool_use_error>{message}</tool_use_error>",
        data={
            "code": str(validation.code or "validation_failed"),
            "tool": str(invocation.name or ""),
            **({"validation": validation.details} if validation.details else {}),
        },
    )


def tool_execution_result_to_envelope(
    *,
    invocation: ToolInvocation,
    result: Any,
) -> ToolResultEnvelope:
    if result is None:
        return ToolResultEnvelope(
            invocation_id=invocation.id,
            status="error",
            model_feedback="<tool_use_error>工具执行没有返回结果。</tool_use_error>",
            data={"code": "empty_result", "tool": str(invocation.name or "")},
        )
    followup_envelope = getattr(result, "followup_envelope", None)
    followup = str(getattr(followup_envelope, "content", "") or getattr(result, "followup_context", "") or "").strip()
    followup_data: dict[str, Any] = {}
    if isinstance(followup_envelope, ToolFollowupEnvelope):
        followup_data = {
            "producer_bounded": bool(followup_envelope.producer_bounded),
            "complete": bool(followup_envelope.complete),
            "continuation": dict(followup_envelope.continuation or {}),
            "diagnostics": dict(followup_envelope.diagnostics or {}),
        }
    return ToolResultEnvelope(
        invocation_id=invocation.id,
        status="ok",
        model_feedback=followup,
        data={
            "tool_type": str(getattr(result, "tool_type", "") or invocation.name),
            "state_updates": dict(getattr(result, "state_updates", {}) or {}),
            **({"followup": followup_data} if followup_data else {}),
        },
        events=list(getattr(result, "stream_events", []) or []),
    )


def validation_failure_result(
    *,
    invocation: ToolInvocation,
    validation: ValidationResult,
) -> tuple[ToolExecutionResult, ToolResultEnvelope]:
    """Pair a failed admission with feedback for both legacy and native callers."""
    envelope = validation_result_to_envelope(invocation=invocation, validation=validation)
    status = "unavailable" if validation.code in {"not_available", "unknown_tool"} else "rejected"
    event = {
        "type": "capability_execution_result",
        "tool_type": invocation.name,
        "status": status,
        "reason": str(validation.code or "validation_failed"),
    }
    return (
        ToolExecutionResult(
            tool_type=invocation.name,
            stream_events=[event],
            followup_context=envelope.model_feedback,
            state_updates={
                "capability_execution": {
                    "tool_type": invocation.name,
                    "status": status,
                    "reason": str(validation.code or "validation_failed"),
                }
            },
        ),
        ToolResultEnvelope(
            invocation_id=envelope.invocation_id,
            status=envelope.status,
            model_feedback=envelope.model_feedback,
            data=dict(envelope.data or {}),
            events=[event],
        ),
    )


def broker_constraint_result(
    *,
    invocation: ToolInvocation,
    broker_result: Any,
    server_local: bool = False,
) -> tuple[ToolExecutionResult, ToolResultEnvelope] | None:
    """Map shared policy/resource failures from either broker result contract."""
    if server_local:
        policy_failure = getattr(broker_result, "policy_failure", None)
        resource_limit = getattr(broker_result, "resource_limit", None)
    else:
        data = getattr(broker_result, "data", {}) or {}
        policy_failure = data.get("policy_failure")
        resource_limit = data.get("resource_limit")
        policy_failure = policy_failure if isinstance(policy_failure, dict) else None
        resource_limit = resource_limit if isinstance(resource_limit, dict) else None
    if policy_failure is not None:
        from ..execution_policies import policy_failure_tool_result

        result = policy_failure_tool_result(invocation.name, broker_result.status, broker_result.reason, policy_failure)
    elif resource_limit is not None:
        from ..execution_resource_policy import resource_limit_tool_result

        result = resource_limit_tool_result(invocation.name, resource_limit)
    else:
        return None
    return result, _final_exec_envelope(invocation=invocation, result=result)


def server_local_broker_result(
    *,
    invocation: ToolInvocation,
    broker_result: Any,
) -> tuple[Any | None, ToolResultEnvelope]:
    """Preserve ordinary capability outcomes and expose broker failures honestly."""
    constrained = broker_constraint_result(invocation=invocation, broker_result=broker_result, server_local=True)
    if constrained is not None:
        return constrained
    result = broker_result.result
    if broker_result.status != "succeeded" or result is None:
        reason = str(broker_result.reason or broker_result.status or "server_local_execution_failed")
        feedback = (
            f"<tool_use_error>工具执行没有得到可确认的结果（{reason}）。"
            "请明确说明这次没有完成，不要重试可能产生重复副作用的动作。</tool_use_error>"
        )
        event = {
            "type": "capability_execution_result",
            "tool_type": invocation.name,
            "status": broker_result.status,
            "reason": reason,
        }
        failure_result = ToolExecutionResult(
            tool_type=invocation.name,
            stream_events=[event],
            followup_context=feedback,
            state_updates={
                "capability_execution": {
                    "tool_type": invocation.name,
                    "status": broker_result.status,
                    "reason": reason,
                }
            },
        )
        return failure_result, ToolResultEnvelope(
            invocation_id=invocation.id,
            status="error",
            model_feedback=feedback,
            data={"code": reason, "tool": invocation.name, "status": broker_result.status},
            events=[event],
        )
    return result, _final_exec_envelope(invocation=invocation, result=result)
