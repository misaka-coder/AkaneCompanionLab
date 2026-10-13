"""Durable business results; images remain private provider inputs."""
from __future__ import annotations

import copy
import json
from dataclasses import asdict

from capcore import CapabilityResult

from ..tool_continuation import FollowupDecision
from ..tool_handlers.core import ToolExecutionResult, ToolFollowupEnvelope
from ..plugin_result_projection import sanitize_capability_result


def stored_tool_result(result):
    fields = {
        "raw_turns": result.raw_turns,
        "stream_events": result.stream_events,
        "state_updates": result.state_updates,
        "trace_receipt": result.trace_receipt,
        "model_image_inputs": result.model_image_inputs,
        "execution_timing": result.execution_timing,
    }
    data = {"tool_result": {key: value for key, value in fields.items() if value}}
    if result.followup is not None:
        data["followup"] = result.followup.as_dict()
    if result.followup_envelope is not None:
        envelope = asdict(result.followup_envelope)
        envelope.pop("content")  # The complete body lives in result_summary.
        data["model_presentation"] = envelope
    if isinstance(result.capability_result, CapabilityResult):
        data["capability_result"] = sanitize_capability_result(result.capability_result).as_dict()
    # Fail visibly if an adapter broke the public result contract; never lose
    # business state silently or stringify arbitrary execution objects.
    json.dumps(data, ensure_ascii=False, allow_nan=False)
    return data


def completion_result(job, *, identity=True):
    fields = copy.deepcopy(job.result.get("tool_result") or {})
    result = ToolExecutionResult(tool_type=job.capability_id,
        followup_context=job.result_summary or job.last_error or "(no output)", **fields)
    metadata = job.result.get("model_presentation")
    if isinstance(metadata, dict):
        result.followup_envelope = ToolFollowupEnvelope(content=result.followup_context,
            producer_bounded=bool(metadata.get("producer_bounded")), complete=bool(metadata.get("complete", True)),
            continuation=metadata.get("continuation"), diagnostics=metadata.get("diagnostics"))
    followup = job.result.get("followup")
    if isinstance(followup, dict):
        result.followup = FollowupDecision(**followup)
    capability = job.result.get("capability_result")
    if isinstance(capability, dict):
        result.capability_result = CapabilityResult(**capability)
    if job.capability_source == "subagent":
        references = [str(item.get("handle") or "") +
            (f" sha256={item['sha256']}" if item.get("sha256") else "") for item in job.artifacts if item.get("handle")]
        if references:
            result.followup_context += "\nartifacts (available_not_delivered): " + "; ".join(references)
    if identity:
        header = f"job_id={job.job_id}; tool={job.capability_id}; status={job.status}"
        if job.capability_source == "subagent":
            header += "; agent_id=" + str(job.payload.get("child_session_id") or "")
        if job.last_error and job.last_error not in result.followup_context:
            header += f"; reason={job.last_error}"
        result.followup_context = header + "\n" + result.followup_context
        if result.followup_envelope is not None:
            result.followup_envelope = result.followup_envelope.with_content(result.followup_context)
    return result
