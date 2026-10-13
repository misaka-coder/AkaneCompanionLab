"""Shared tool-job identity, safe snapshots, artifacts and result projections."""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Mapping

from ..tool_handlers.core import ToolExecutionResult
from ..host_jobs import HostJob


logger = logging.getLogger("akane.host_tool_jobs")


HOST_TOOL_JOB_SOURCE = "tool"


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
        followup_context=f"job_id={job_id}; status={status}" + ("; duplicate=true" if duplicate else ""),
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


def _registered_failure_result(
    capability_id: str, *, job_id: str, job: HostJob | None, reason: str,
) -> ToolExecutionResult:
    """Keep registration identity and observable state after admission failed."""
    status = job.status if job is not None else "unknown"
    control = job.control_state if job is not None else "unknown"
    safe_reason = str(reason or "host_job_schedule_failed")[:160]
    fields = {"job_id": job_id, "job_status": status, "control_state": control,
              "reason": safe_reason}
    return ToolExecutionResult(
        tool_type=capability_id,
        stream_events=[{"type": "background_job_rejected", "tool_type": capability_id,
                        "status": "failed", **fields}],
        followup_context=(
            f"<tool_use_error>本次后台任务未能确认调度（{safe_reason}）。"
            f"已返回任务编号 {job_id}，当前任务状态：{status}。"
            "请依据任务记录处理后续，不要重复启动已有任务。</tool_use_error>"
        ),
        state_updates={"capability_execution": {"tool_type": capability_id, "status": "failed", **fields}},
    )
