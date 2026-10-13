"""Capability HTTP workflows responsibilities."""
from __future__ import annotations
import time
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from ...host_jobs import HostJobOwner
from ...local_capability_config import (
    load_capability_config,
    preflight_workflow_execution,
    save_workflow_file,
    save_workflow_config,
    validate_workflow_config,
)
from ...local_workflow_execution import normalize_workflow_asset_handle
from ...local_capability_catalog import build_local_workflow_catalog
from .context import CapabilityRouteContext
from .workflow_support import project_workflow_readiness
from .common import _read_json_object, _resolve_identity, _observe_request, _log_best_effort
from .workflow_support import (
    _with_bound_workflow_runner,
    _extract_workflow_input_assets,
    _safe_workflow_job_id,
)


def register_workflows_routes(router: APIRouter, *, context: CapabilityRouteContext) -> None:
    runtime_metrics = context.runtime_metrics
    log_event = context.log_event
    resolve_identity_from_query = context.resolve_identity_from_query
    provider_config_base_dir = context.provider_config_base_dir
    workflow_runner = context.workflow_runner
    background_tasks = context.background_tasks
    workflow_job_runtime = context.workflow_job_runtime
    executor_broker = context.executor_broker

    @router.get("/capabilities/workflows")
    async def read_capability_workflows(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        provider_config = load_capability_config(
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
        )
        payload = build_local_workflow_catalog(
            profile_user_id=profile_user_id,
            provider_configs=provider_config.get("providers", {}),
            workflow_configs=provider_config.get("workflows", {}),
            approval_policy=provider_config.get("approvalPolicy", {}),
        )
        project_workflow_readiness(payload, context=context, profile_user_id=profile_user_id)
        payload["providerConfigStatus"] = provider_config.get("configStatus") or "available"
        payload["providerConfigWarnings"] = list(provider_config.get("warnings") or [])
        _observe_request(runtime_metrics, "capabilities.workflows", started_at, True)
        _log_best_effort(
            log_event,
            "capabilities_workflows",
            status=payload.get("status"),
            total=payload.get("summary", {}).get("total"),
        )
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @router.post("/capabilities/workflows/{workflow_id}/config")
    async def write_capability_workflow_config(workflow_id: str, request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        payload = await _read_json_object(request)
        result = save_workflow_config(
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
            workflow_id=workflow_id,
            payload=payload,
        )
        _observe_request(runtime_metrics, "capabilities.workflow_config", started_at, bool(result.get("ok")))
        _log_best_effort(
            log_event,
            "capabilities_workflow_config",
            status=result.get("status"),
            workflowId=result.get("workflowId"),
        )
        status_code = 404 if result.get("status") == "unknown_workflow" else 200
        return JSONResponse(result, status_code=status_code, headers={"Cache-Control": "no-store"})

    @router.post("/capabilities/workflows/{workflow_id}/validate")
    async def validate_capability_workflow_config(workflow_id: str, request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        result = validate_workflow_config(
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
            workflow_id=workflow_id,
        )
        _observe_request(runtime_metrics, "capabilities.workflow_validate", started_at, bool(result.get("ok")))
        _log_best_effort(
            log_event,
            "capabilities_workflow_validate",
            status=result.get("status"),
            workflowId=result.get("workflowId"),
        )
        status_code = 404 if result.get("status") == "unknown_workflow" else 200
        return JSONResponse(result, status_code=status_code, headers={"Cache-Control": "no-store"})

    @router.post("/capabilities/workflows/{workflow_id}/file")
    async def import_capability_workflow_file(workflow_id: str, request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        payload = await _read_json_object(request)
        result = save_workflow_file(
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
            workflow_id=workflow_id,
            payload=payload,
        )
        _observe_request(runtime_metrics, "capabilities.workflow_file", started_at, bool(result.get("ok")))
        _log_best_effort(
            log_event,
            "capabilities_workflow_file",
            status=result.get("status"),
            workflowId=result.get("workflowId"),
        )
        status_code = 404 if result.get("status") == "unknown_workflow" else 200
        return JSONResponse(result, status_code=status_code, headers={"Cache-Control": "no-store"})

    @router.post("/capabilities/workflows/{workflow_id}/preflight")
    async def preflight_capability_workflow_execution(workflow_id: str, request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        payload = await _read_json_object(request)
        result = preflight_workflow_execution(
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
            workflow_id=workflow_id,
            payload=payload,
        )
        result = _with_bound_workflow_runner(
            result,
            workflow_runner=workflow_runner,
            background_tasks=background_tasks,
            executor_broker=executor_broker,
            workflow_job_runtime=workflow_job_runtime,
        )
        _observe_request(runtime_metrics, "capabilities.workflow_preflight", started_at, bool(result.get("ok")))
        _log_best_effort(
            log_event,
            "capabilities_workflow_preflight",
            status=result.get("status"),
            workflowId=result.get("workflowId"),
        )
        status_code = 404 if result.get("status") == "unknown_workflow" else 200
        return JSONResponse(result, status_code=status_code, headers={"Cache-Control": "no-store"})

    @router.post("/capabilities/workflows/{workflow_id}/jobs")
    async def start_capability_workflow_job(workflow_id: str, request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        payload = await _read_json_object(request)
        result = preflight_workflow_execution(
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
            workflow_id=workflow_id,
            payload=payload,
        )
        result = _with_bound_workflow_runner(
            result,
            workflow_runner=workflow_runner,
            background_tasks=background_tasks,
            executor_broker=executor_broker,
            workflow_job_runtime=workflow_job_runtime,
        )
        if result.get("ok") and result.get("status") == "ready":
            started = workflow_job_runtime.start(
                preflight=result,
                profile_user_id=profile_user_id,
                session_id=session_id,
                input_assets=_extract_workflow_input_assets(preflight=result, payload=payload),
            )
            result = {**result, **started}
        _observe_request(runtime_metrics, "capabilities.workflow_job_start", started_at, bool(result.get("ok")))
        _log_best_effort(
            log_event,
            "capabilities_workflow_job_start",
            status=result.get("status"),
            workflowId=result.get("workflowId"),
            jobStatus=result.get("jobStatus"),
        )
        status_code = 404 if result.get("status") == "unknown_workflow" else 200
        return JSONResponse(result, status_code=status_code, headers={"Cache-Control": "no-store"})

    @router.get("/capabilities/workflow-jobs/{job_id}")
    async def read_capability_workflow_job(job_id: str, request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        safe_job_id = _safe_workflow_job_id(job_id)
        job = (
            workflow_job_runtime.get(
                safe_job_id,
                owner=HostJobOwner(profile_user_id, _session_id),
            )
            if safe_job_id and workflow_job_runtime is not None
            else None
        )
        if job is None:
            result = {
                "ok": False,
                "status": "unknown_workflow_job",
                "reason": "workflow_job_not_found",
                "executionReady": False,
                "canRun": False,
            }
            _observe_request(runtime_metrics, "capabilities.workflow_job_status", started_at, False)
            _log_best_effort(log_event, "capabilities_workflow_job_status", status=result["status"])
            return JSONResponse(result, status_code=404, headers={"Cache-Control": "no-store"})

        public_job = workflow_job_runtime.public(job)
        result = {
            "ok": True,
            "status": public_job["status"],
            "reason": public_job["reason"],
            "jobId": public_job["jobId"],
            "workflowId": public_job["workflowId"],
            "capabilityId": public_job["capabilityId"],
            "executionReady": False,
            "canRun": False,
            "job": public_job,
        }
        _observe_request(runtime_metrics, "capabilities.workflow_job_status", started_at, True)
        _log_best_effort(
            log_event,
            "capabilities_workflow_job_status",
            status=result.get("status"),
            workflowId=result.get("workflowId"),
            jobStatus=public_job.get("status"),
        )
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @router.get("/capabilities/workflow-jobs/{job_id}/outputs/{output_handle}")
    async def read_capability_workflow_job_output(job_id: str, output_handle: str, request: Request) -> Response:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        safe_job_id = _safe_workflow_job_id(job_id)
        safe_output_handle = normalize_workflow_asset_handle(output_handle)
        job = (
            workflow_job_runtime.get(
                safe_job_id,
                owner=HostJobOwner(profile_user_id, _session_id),
            )
            if safe_job_id and safe_output_handle.get("ok") and workflow_job_runtime is not None
            else None
        )
        if job is None:
            _observe_request(runtime_metrics, "capabilities.workflow_job_output", started_at, False)
            return JSONResponse(
                {
                    "ok": False,
                    "status": "unknown_workflow_job",
                    "reason": "workflow_job_not_found",
                },
                status_code=404,
                headers={"Cache-Control": "no-store"},
            )
        if job.status != "succeeded":
            _observe_request(runtime_metrics, "capabilities.workflow_job_output", started_at, False)
            return JSONResponse(
                {
                    "ok": False,
                    "status": "not-ready",
                    "reason": "workflow_job_output_not_ready",
                },
                status_code=409,
                headers={"Cache-Control": "no-store"},
            )
        output_asset = workflow_job_runtime.output(job, str(safe_output_handle.get("handle") or ""))
        if output_asset is None:
            _observe_request(runtime_metrics, "capabilities.workflow_job_output", started_at, False)
            return JSONResponse(
                {
                    "ok": False,
                    "status": "unknown_workflow_output",
                    "reason": "workflow_output_not_found",
                },
                status_code=404,
                headers={"Cache-Control": "no-store"},
            )
        _observe_request(runtime_metrics, "capabilities.workflow_job_output", started_at, True)
        return Response(
            content=output_asset.data,
            media_type=output_asset.content_type or "application/octet-stream",
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
