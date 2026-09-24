"""One continuation decision for model results; delivery and storage stay separate."""

from __future__ import annotations

from typing import Any, Mapping, Sequence
from dataclasses import asdict, dataclass

from akane_plugin.results import validate_followup


FINISH_TURN_PARAMETER = {
    "type": "boolean",
    "description": (
        "Set true only when this action is your last step and no further reply is needed. "
        "Success may finish the turn; errors always return for follow-up. Defaults to false."
    ),
}


@dataclass(frozen=True)
class FollowupDecision:
    mode: str
    requires_model: bool
    reason: str

    def as_dict(self):
        return asdict(self)


def declared_followup(raw: Mapping[str, Any]) -> str:
    """Legacy declaration fields are read only at this boundary."""
    if "followup" in raw:
        return validate_followup(raw["followup"])
    if str(raw.get("execution_class") or "sync") == "long_task":
        return "none" if raw.get("completion_mode") == "silent" else "required"
    return "auto" if raw.get("model_followup") == "optional" else "required"


def resolve_followup(*, default="required", override=None, consumer="model", required=False,
                     allow_end=False, delivery_managed=False, failed=False, revoked=False):
    mode = validate_followup(override, optional=True) or validate_followup(default)
    if revoked:
        return FollowupDecision(mode, False, "scope_revoked")
    if consumer == "program":
        return FollowupDecision(mode, False, "program_consumer")
    if required:
        return FollowupDecision(mode, True, "consumer_requires_result")
    if failed:
        return FollowupDecision(mode, True, "unhandled_failure")
    if mode == "none":
        return FollowupDecision(mode, False, "result_does_not_request_model")
    if mode == "auto" and allow_end and delivery_managed:
        return FollowupDecision(mode, False, "direct_completion_allowed")
    return FollowupDecision(mode, True, "required" if mode == "required" else "consumer_demand_unknown")


def result_failed(result):
    capability = getattr(result, "capability_result", None)
    return bool(getattr(capability, "is_error", False)) or any(
        event.get("type") in {"tool_execution_failed", "approval_required", "background_job_rejected"}
        or event.get("is_error") is True
        for event in getattr(result, "stream_events", ()) if isinstance(event, Mapping)
    )


def bind_result_followup(result, *, context, default="required", allow_end=False, delivery_managed=False, revoked=False):
    capability = getattr(result, "capability_result", None)
    result.followup = resolve_followup(default=default, override=getattr(capability, "followup", None),
        consumer=context.result_consumer, required=context.model_result_required or context.execution_scope is not None,
        allow_end=allow_end, delivery_managed=delivery_managed, failed=result_failed(result),
        revoked=revoked or bool(context.cancel_requested and context.cancel_requested()))
    return result


def job_followup(job):
    contract = job.payload.get("followup") or {}
    stored = job.result.get("followup") or {}
    mode = stored.get("mode") or contract.get("default") or ("none" if job.completion_mode == "silent" else "required")
    return resolve_followup(default=mode, consumer=contract.get("consumer", "model"),
        required=contract.get("required") is True,
        allow_end=stored.get("reason") == "direct_completion_allowed", delivery_managed=True,
        failed=job.status == "failed", revoked=bool(job.scope_revoked_reason) or job.cancel_requested or job.status == "cancelled"
            or stored.get("reason") == "scope_revoked")


def can_finish_tool_batch(results: Sequence[Any]) -> bool:
    """Only finish after every result has released its implicit model consumer."""
    return bool(results) and all(
        isinstance(getattr(result, "followup", None), FollowupDecision)
        and not result.followup.requires_model
        and (not result_failed(result) or result.followup.reason == "scope_revoked")
        for result in results
    )
