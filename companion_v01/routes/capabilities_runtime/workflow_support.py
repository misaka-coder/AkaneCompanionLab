"""Capability HTTP workflow support responsibilities."""
from __future__ import annotations
import copy
from pathlib import Path
from typing import Any
from ...host_workflow_jobs import HostWorkflowJobRuntime
from ...local_capability_config import validate_workflow_runtime_binding
from ...local_workflow_execution import WorkflowExecutionAsset, normalize_workflow_asset
from .definitions import WORKFLOW_JOB_ID_RE
from .context import CapabilityRouteContext


def _with_bound_workflow_runner(
    result: dict[str, Any],
    *,
    workflow_runner: Any,
    background_tasks: Any,
    executor_broker: Any,
    workflow_job_runtime: HostWorkflowJobRuntime | None,
) -> dict[str, Any]:
    if (
        workflow_runner is None
        or not callable(getattr(executor_broker, "execute_server_local", None))
        or result.get("status") != "not-implemented"
        or result.get("reason") != "workflow_runner_not_bound"
    ):
        return result
    next_result = copy.deepcopy(result)
    checks = dict(next_result.get("checks") or {})
    checks["runnerBound"] = True
    next_result["checks"] = checks
    if workflow_job_runtime is None:
        next_result["ok"] = False
        next_result["status"] = "not-implemented"
        next_result["reason"] = "workflow_scheduler_not_bound"
        next_result["executionReady"] = False
        next_result["canRun"] = False
        return next_result
    next_result["ok"] = True
    next_result["status"] = "ready"
    next_result["reason"] = ""
    next_result["executionReady"] = True
    next_result["canRun"] = True
    return next_result

def _extract_workflow_input_assets(
    *,
    preflight: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, WorkflowExecutionAsset]:
    accepted_inputs = preflight.get("acceptedInputs") if isinstance(preflight.get("acceptedInputs"), dict) else {}
    input_handle = str(accepted_inputs.get("inputImageHandle") or "")
    if not input_handle:
        return {}
    raw_asset = {
        "handle": input_handle,
        "bytes": payload.get("inputImageBytes") or payload.get("imageBytes"),
        "contentType": payload.get("inputImageContentType") or payload.get("mimeType") or payload.get("contentType"),
    }
    asset = normalize_workflow_asset(raw_asset)
    return {asset.handle: asset} if asset is not None else {}

def _mark_workflows_execution_ready(
    payload: dict[str, Any],
    *,
    workflow_runner: Any,
    background_tasks: Any,
    executor_broker: Any,
    base_dir: Path | None,
    profile_user_id: str,
) -> None:
    if (
        workflow_runner is None
        or background_tasks is None
        or not hasattr(background_tasks, "submit")
        or not callable(getattr(executor_broker, "execute_server_local", None))
    ):
        return
    workflows = _payload_workflow_entries(payload)
    for workflow in workflows:
        if not isinstance(workflow, dict):
            continue
        if not workflow.get("configured") or not workflow.get("enabled"):
            continue
        if str(workflow.get("status") or "") not in {"configured", "validated_config", "ready"}:
            continue
        runtime = validate_workflow_runtime_binding(
            base_dir=base_dir,
            profile_user_id=profile_user_id,
            workflow_id=str(workflow.get("id") or workflow.get("workflowId") or ""),
        )
        if runtime.get("ok"):
            workflow["status"] = "ready"
            workflow["reason"] = ""
            workflow["executionReady"] = True
        else:
            workflow["status"] = runtime.get("status") or "invalid_workflow_config"
            workflow["reason"] = runtime.get("reason") or "workflow_runtime_config_invalid"
            workflow["executionReady"] = False
    if isinstance(payload.get("summary"), dict):
        payload["summary"].update(_summarize_payload_entries(payload))
        payload["summary"]["executionReady"] = sum(1 for workflow in workflows if workflow.get("executionReady"))

def _payload_workflow_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(payload.get("workflows"), list):
        return [item for item in payload["workflows"] if isinstance(item, dict)]
    if isinstance(payload.get("capabilities"), list):
        return [
            item
            for item in payload["capabilities"]
            if isinstance(item, dict) and str(item.get("kind") or "") == "workflow"
        ]
    return []

def _summarize_payload_entries(payload: dict[str, Any]) -> dict[str, Any]:
    entries = payload.get("workflows") if isinstance(payload.get("workflows"), list) else payload.get("capabilities")
    entries = entries if isinstance(entries, list) else []
    by_kind: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_source: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        _increment_summary_count(by_kind, str(entry.get("kind") or "unknown"))
        _increment_summary_count(by_status, str(entry.get("status") or "unknown"))
        _increment_summary_count(by_source, str(entry.get("source") or "unknown"))
        _increment_summary_count(by_type, str(entry.get("type") or "unknown"))
    return {
        "total": sum(by_kind.values()),
        "byKind": by_kind,
        "byStatus": by_status,
        "bySource": by_source,
        "byType": by_type,
    }

def _increment_summary_count(target: dict[str, int], key: str) -> None:
    target[key] = target.get(key, 0) + 1

def _safe_workflow_job_id(value: Any) -> str:
    text = str(value or "").strip()
    return text if WORKFLOW_JOB_ID_RE.match(text) else ""


def project_workflow_readiness(
    payload: dict[str, Any], *, context: CapabilityRouteContext, profile_user_id: str,
) -> None:
    """Catalog readiness must agree with the real job admission path."""
    if context.workflow_job_runtime is None:
        return
    _mark_workflows_execution_ready(
        payload,
        workflow_runner=context.workflow_runner,
        background_tasks=context.background_tasks,
        executor_broker=context.executor_broker,
        base_dir=context.provider_config_base_dir,
        profile_user_id=profile_user_id,
    )
