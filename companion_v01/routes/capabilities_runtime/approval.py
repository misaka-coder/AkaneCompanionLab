"""Capability HTTP approval responsibilities."""
from __future__ import annotations
import time
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from ...local_capability_config import get_approval_policy_config, save_capability_approval_mode
from .context import CapabilityRouteContext
from .common import (
    _read_json_object,
    _safe_bool_query,
    _safe_positive_int,
    _resolve_identity,
    _observe_request,
    _log_best_effort,
)


def register_approval_routes(router: APIRouter, *, context: CapabilityRouteContext) -> None:
    runtime_metrics = context.runtime_metrics
    log_event = context.log_event
    resolve_identity_from_query = context.resolve_identity_from_query
    provider_config_base_dir = context.provider_config_base_dir
    approval_store = context.approval_store

    @router.get("/capabilities/approval-policy")
    async def read_capability_approval_policy(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        payload = get_approval_policy_config(
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
        )
        _observe_request(runtime_metrics, "capabilities.approval_policy", started_at, True)
        _log_best_effort(
            log_event,
            "capabilities_approval_policy",
            status=payload.get("status"),
            familyCount=len(payload.get("approvalPolicy", {}).get("families") or []),
        )
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @router.post("/capabilities/approval-policy")
    async def write_capability_approval_policy(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        payload = await _read_json_object(request)
        family_id = str(payload.get("familyId") or payload.get("family_id") or "").strip()
        family_mode = str(payload.get("mode") or "").strip()
        if family_id in {"ops", "extensions"} and family_mode:
            result = save_capability_approval_mode(
                base_dir=provider_config_base_dir,
                profile_user_id=profile_user_id,
                capability_id=family_id,
                mode=family_mode,
            )
        else:
            result = {
                "ok": False,
                "status": "invalid_config",
                "reason": "approval_policy_family_and_mode_required",
            }
        ok = bool(result.get("ok"))
        _observe_request(runtime_metrics, "capabilities.approval_policy_save", started_at, ok)
        _log_best_effort(
            log_event,
            "capabilities_approval_policy_save",
            status=result.get("status"),
            reason=result.get("reason"),
            familyId=family_id,
            mode=family_mode,
        )
        status_code = 400 if result.get("status") == "invalid_config" else 200
        return JSONResponse(result, status_code=status_code, headers={"Cache-Control": "no-store"})

    @router.get("/capabilities/approval-requests")
    async def read_capability_approval_requests(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        include_resolved = _safe_bool_query(request.query_params.get("include_resolved"))
        limit = _safe_positive_int(request.query_params.get("limit"), default=20, maximum=50)
        payload = approval_store.list_requests(
            profile_user_id=profile_user_id,
            include_resolved=include_resolved,
            limit=limit,
        )
        _observe_request(runtime_metrics, "capabilities.approval_requests", started_at, True)
        _log_best_effort(
            log_event,
            "capabilities_approval_requests",
            status=payload.get("status"),
            pendingCount=payload.get("pendingCount"),
        )
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @router.post("/capabilities/approval-requests")
    async def create_capability_approval_request(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        payload = await _read_json_object(request)
        result = approval_store.create_request(
            profile_user_id=profile_user_id,
            session_id=session_id,
            payload=payload,
        )
        ok = bool(result.get("ok"))
        _observe_request(runtime_metrics, "capabilities.approval_request_create", started_at, ok)
        _log_best_effort(
            log_event,
            "capabilities_approval_request_create",
            status=result.get("status"),
            requestId=result.get("requestId"),
            reason=result.get("reason"),
        )
        status_code = 400 if result.get("status") in {"invalid_request", "not_required", "disabled"} else 200
        return JSONResponse(result, status_code=status_code, headers={"Cache-Control": "no-store"})

    @router.get("/capabilities/approval-requests/{request_id}")
    async def read_capability_approval_request(request_id: str, request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        result = approval_store.get_request(profile_user_id=profile_user_id, request_id=request_id)
        _observe_request(runtime_metrics, "capabilities.approval_request_read", started_at, bool(result.get("ok")))
        _log_best_effort(
            log_event,
            "capabilities_approval_request_read",
            status=result.get("status"),
            reason=result.get("reason"),
        )
        status_code = 404 if result.get("status") == "not_found" else 400 if result.get("status") == "invalid_request" else 200
        return JSONResponse(result, status_code=status_code, headers={"Cache-Control": "no-store"})

    @router.post("/capabilities/approval-requests/{request_id}/decision")
    async def decide_capability_approval_request(request_id: str, request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        payload = await _read_json_object(request)
        result = approval_store.decide_request(
            profile_user_id=profile_user_id,
            request_id=request_id,
            payload=payload,
        )
        ok = bool(result.get("ok"))
        _observe_request(runtime_metrics, "capabilities.approval_request_decision", started_at, ok)
        _log_best_effort(
            log_event,
            "capabilities_approval_request_decision",
            status=result.get("status"),
            requestId=result.get("requestId"),
            reason=result.get("reason"),
        )
        if result.get("status") == "not_found":
            status_code = 404
        elif result.get("status") == "invalid_request":
            status_code = 400
        elif result.get("reason") == "approval_request_already_resolved":
            status_code = 409
        else:
            status_code = 200
        return JSONResponse(result, status_code=status_code, headers={"Cache-Control": "no-store"})
