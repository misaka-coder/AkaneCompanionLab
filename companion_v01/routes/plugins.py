"""Loopback-only diagnostics and lifecycle surface for trusted plugins."""

from __future__ import annotations

import json
import asyncio
from collections.abc import Mapping
from typing import Any, Callable

from capcore import InvocationContext
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..deployment_security import AdminWriteAuth
from ..extension_management import ExtensionManagementService
from ..plugin_result_projection import MAX_PLUGIN_RESPONSE_BYTES, project_capability_result
from ..host_jobs import HostJobOwner, HostJobStore, JOB_TERMINAL_STATUSES
from ..tool_continuation import job_followup


MAX_PLUGIN_REQUEST_BYTES = 16 * 1024


def build_plugins_router(
    *,
    extension_management_service: ExtensionManagementService,
    admin_auth: AdminWriteAuth | None = None,
    job_store: HostJobStore | None = None,
    job_control: Callable[..., dict[str, Any]] | None = None,
) -> APIRouter:
    router = APIRouter()
    management_auth = admin_auth or AdminWriteAuth.local_compatibility()

    @router.get("/admin/plugins/{plugin_id}/connections/{name}")
    async def read_connection_config(plugin_id: str, name: str, request: Request, profile_user_id: str = ""):
        authorization = management_auth.authorize(request)
        if not authorization.ok:
            return _response({"ok": False, "status": "forbidden", "reason": authorization.reason},
                             status_code=authorization.status_code)
        result = await asyncio.to_thread(extension_management_service.connection_config,
            plugin_id=plugin_id, name=name, profile_user_id=profile_user_id)
        return _response(result, status_code=_management_status_code(result))

    @router.put("/admin/plugins/{plugin_id}/connections/{name}")
    async def save_connection_config(plugin_id: str, name: str, request: Request, profile_user_id: str = ""):
        authorization = management_auth.authorize(request)
        if not authorization.ok:
            return _response({"ok": False, "status": "forbidden", "reason": authorization.reason},
                             status_code=authorization.status_code)
        payload, error = await _read_bounded_json_object(request)
        if error is not None:
            return _response(error, status_code=400)
        result = await asyncio.to_thread(extension_management_service.connection_config,
            plugin_id=plugin_id, name=name, profile_user_id=profile_user_id, payload=payload)
        return _response(result, status_code=_management_status_code(result))

    @router.get("/admin/plugins/jobs/{job_id}")
    async def read_plugin_job(job_id: str, request: Request, profile_user_id: str = "", session_id: str = "") -> JSONResponse:
        authorization = management_auth.authorize(request)
        if not authorization.ok:
            return _response({"ok": False, "status": "forbidden", "reason": authorization.reason},
                             status_code=authorization.status_code)
        if job_store is None:
            return _response({"ok": False, "status": "unavailable", "reason": "host_job_store_unavailable"}, status_code=503)
        if not profile_user_id.strip() or not session_id.strip():
            return _response(_request_error("host_job_owner_required"), status_code=400)
        job = await asyncio.to_thread(job_store.get, job_id, owner=HostJobOwner(profile_user_id, session_id))
        if job is None:
            return _response({"ok": False, "status": "not_found", "reason": "host_job_not_found"}, status_code=404)
        return _response({"ok": True, "job_id": job.job_id, "status": job.status, "reason": job.last_error,
            "capability_id": job.capability_id, "created_at": job.created_at, "finished_at": job.finished_at,
            "result_summary": job.result_summary, "result": job.result.get("capability_result"),
            "followup": job_followup(job).as_dict() if job.status in JOB_TERMINAL_STATUSES else None,
            "delivery_receipt": job.delivery_receipt, "scope_revoked_reason": job.scope_revoked_reason,
            "control_state": job.control_state})

    @router.post("/admin/plugins/jobs/{job_id}/control")
    async def control_plugin_job(job_id: str, request: Request, profile_user_id: str = "", session_id: str = "") -> JSONResponse:
        authorization = management_auth.authorize(request)
        if not authorization.ok:
            return _response({"ok": False, "status": "forbidden", "reason": authorization.reason},
                             status_code=authorization.status_code)
        if job_store is None:
            return _response({"ok": False, "status": "unavailable", "reason": "host_job_store_unavailable"}, status_code=503)
        if not profile_user_id.strip() or not session_id.strip():
            return _response(_request_error("host_job_owner_required"), status_code=400)
        payload, error = await _read_bounded_json_object(request)
        if error is not None:
            return _response(error, status_code=400)
        action = str(payload.get("action") or "").strip().lower()
        if action not in {"pause", "resume", "stop"}:
            return _response(_request_error("host_job_control_action_invalid"), status_code=400)
        owner = HostJobOwner(profile_user_id, session_id)
        if callable(job_control):
            result = await asyncio.to_thread(job_control, job_id, owner=owner, action=action)
        else:
            result = await asyncio.to_thread(job_store.control, job_id, owner=owner, action=action)
        if not result.get("ok"):
            return _response(result, status_code=_job_control_status_code(result))
        current = await asyncio.to_thread(job_store.get, job_id, owner=owner)
        return _response({
            "ok": True,
            "status": str(result.get("status") or getattr(current, "status", "")),
            "reason": str(result.get("reason") or "host_job_control_applied"),
            "job_id": job_id,
            "job_status": getattr(current, "status", result.get("status")),
            "control_state": getattr(current, "control_state", result.get("status")),
        })

    @router.get("/plugins/catalog")
    async def read_public_plugin_catalog() -> JSONResponse:
        """Return the safe, read-only plugin inventory used by clients."""

        return _response(extension_management_service.public_snapshot())

    @router.get("/admin/plugins/status")
    async def read_plugin_status(request: Request) -> JSONResponse:
        authorization = management_auth.authorize(request)
        if not authorization.ok:
            return _response(
                {"ok": False, "status": "forbidden", "reason": authorization.reason},
                status_code=authorization.status_code,
            )
        return _response(extension_management_service.snapshot())

    @router.get("/plugins/market")
    async def read_plugin_market() -> JSONResponse:
        result = await extension_management_service.browse_market()
        return _response(result, status_code=_management_status_code(result))

    @router.post("/admin/plugins/market/{plugin_id}/stage")
    async def stage_market_plugin(plugin_id: str, request: Request) -> JSONResponse:
        authorization = management_auth.authorize(request)
        if not authorization.ok:
            return _response({"ok": False, "status": "forbidden", "reason": authorization.reason},
                             status_code=authorization.status_code)
        payload, error = await _read_bounded_json_object(request)
        if error is not None:
            return _response(error, status_code=400)
        result = await extension_management_service.stage_market(plugin_id=plugin_id, digest=payload.get("digest"))
        return _response(result, status_code=_management_status_code(result))

    @router.post("/admin/plugins/restart")
    async def restart_plugin_runtime(request: Request) -> JSONResponse:
        """Recreate installed plugin instances using the persisted selection snapshot."""

        authorization = management_auth.authorize(request)
        if not authorization.ok:
            return _response(
                {"ok": False, "status": "forbidden", "reason": authorization.reason},
                status_code=authorization.status_code,
            )
        result = await extension_management_service.restart()
        status_code = 200 if result.get("status") == "active" else 503
        return _response(result, status_code=status_code)

    @router.post("/admin/plugins/{plugin_id}/enabled")
    async def set_plugin_enabled(plugin_id: str, request: Request) -> JSONResponse:
        authorization = management_auth.authorize(request)
        if not authorization.ok:
            return _response(
                {"ok": False, "status": "forbidden", "reason": authorization.reason},
                status_code=authorization.status_code,
            )
        payload, error = await _read_bounded_json_object(request)
        if error is not None:
            return _response(error, status_code=400)
        if not isinstance(payload.get("enabled"), bool):
            return _response(_request_error("enabled_boolean_required"), status_code=400)
        result = await extension_management_service.set_enabled(
            plugin_id=plugin_id,
            enabled=bool(payload["enabled"]),
        )
        status_code = 200 if result.get("ok") else (404 if result.get("status") == "not_found" else 409)
        return _response(result, status_code=status_code)

    @router.post("/admin/plugins/stages")
    async def stage_plugin_wheel(request: Request) -> JSONResponse:
        """Install and probe a local wheel without changing the active artifact."""

        authorization = management_auth.authorize(request)
        if not authorization.ok:
            return _response(
                {"ok": False, "status": "forbidden", "reason": authorization.reason},
                status_code=authorization.status_code,
            )
        payload, error = await _read_bounded_json_object(request)
        if error is not None:
            return _response(error, status_code=400)
        wheel_path = str(payload.get("wheel_path") or "").strip()
        if not wheel_path:
            return _response(_request_error("plugin_wheel_path_required"), status_code=400)
        result = await extension_management_service.stage_wheel(wheel_path=wheel_path)
        return _response(result, status_code=_management_status_code(result, success=201))

    @router.post("/admin/plugins/stages/source")
    async def stage_plugin_source(request: Request) -> JSONResponse:
        """Build a local plugin project, then run the ordinary wheel probe."""

        authorization = management_auth.authorize(request)
        if not authorization.ok:
            return _response(
                {"ok": False, "status": "forbidden", "reason": authorization.reason},
                status_code=authorization.status_code,
            )
        payload, error = await _read_bounded_json_object(request)
        if error is not None:
            return _response(error, status_code=400)
        source_path = str(payload.get("source_path") or "").strip()
        if not source_path:
            return _response(_request_error("plugin_source_path_required"), status_code=400)
        result = await extension_management_service.stage_source(source_path=source_path)
        return _response(result, status_code=_management_status_code(result, success=201))

    @router.post("/admin/plugins/stages/{stage_id}/install")
    async def install_plugin_stage(stage_id: str, request: Request) -> JSONResponse:
        """Publish and activate after the caller echoes the reviewed permissions."""

        authorization = management_auth.authorize(request)
        if not authorization.ok:
            return _response(
                {"ok": False, "status": "forbidden", "reason": authorization.reason},
                status_code=authorization.status_code,
            )
        payload, error = await _read_bounded_json_object(request)
        if error is not None:
            return _response(error, status_code=400)
        permissions = payload.get("approved_permissions")
        if not isinstance(permissions, list) or any(not isinstance(item, str) for item in permissions):
            return _response(_request_error("approved_permissions_array_required"), status_code=400)
        result = await extension_management_service.install_stage(
            stage_id=stage_id,
            approved_permissions=tuple(permissions),
        )
        return _response(result, status_code=_management_status_code(result))

    @router.delete("/admin/plugins/stages/{stage_id}")
    async def discard_plugin_stage(stage_id: str, request: Request) -> JSONResponse:
        authorization = management_auth.authorize(request)
        if not authorization.ok:
            return _response(
                {"ok": False, "status": "forbidden", "reason": authorization.reason},
                status_code=authorization.status_code,
            )
        result = await extension_management_service.discard_stage(stage_id=stage_id)
        return _response(result, status_code=_management_status_code(result))

    @router.post("/admin/plugins/{plugin_id}/rollback")
    async def rollback_plugin(plugin_id: str, request: Request) -> JSONResponse:
        authorization = management_auth.authorize(request)
        if not authorization.ok:
            return _response(
                {"ok": False, "status": "forbidden", "reason": authorization.reason},
                status_code=authorization.status_code,
            )
        result = await extension_management_service.rollback(plugin_id=plugin_id)
        return _response(result, status_code=_management_status_code(result))

    @router.delete("/admin/plugins/{plugin_id}")
    async def uninstall_plugin(plugin_id: str, request: Request) -> JSONResponse:
        authorization = management_auth.authorize(request)
        if not authorization.ok:
            return _response(
                {"ok": False, "status": "forbidden", "reason": authorization.reason},
                status_code=authorization.status_code,
            )
        result = await extension_management_service.uninstall(plugin_id=plugin_id)
        return _response(result, status_code=_management_status_code(result))

    @router.post("/admin/plugins/capabilities/{capability_id:path}/invoke")
    async def invoke_plugin_capability(capability_id: str, request: Request) -> JSONResponse:
        authorization = management_auth.authorize(request)
        if not authorization.ok:
            return _response(
                {"ok": False, "status": "forbidden", "reason": authorization.reason},
                status_code=authorization.status_code,
            )
        payload, error = await _read_bounded_json_object(request)
        if error is not None:
            return _response(error, status_code=400)

        result = await extension_management_service.invoke_capability(
            capability_id,
            payload,
            context=InvocationContext(client_mode="plugin_admin"),
        )
        projected = project_capability_result(result)
        status_code = _status_code_for_result(projected)
        return _response(projected, status_code=status_code)

    return router


async def _read_bounded_json_object(request: Request) -> tuple[dict[str, Any], dict[str, Any] | None]:
    raw_content_length = str(request.headers.get("content-length") or "").strip()
    if raw_content_length:
        try:
            if int(raw_content_length) > MAX_PLUGIN_REQUEST_BYTES:
                return {}, _request_error("request_too_large")
        except ValueError:
            return {}, _request_error("invalid_content_length")
    try:
        body = await request.body()
    except Exception:
        return {}, _request_error("request_body_unreadable")
    if len(body) > MAX_PLUGIN_REQUEST_BYTES:
        return {}, _request_error("request_too_large")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}, _request_error("invalid_json")
    if not isinstance(payload, dict):
        return {}, _request_error("json_object_required")
    return payload, None


def _status_code_for_result(payload: Mapping[str, Any]) -> int:
    if payload.get("ok"):
        return 200
    status = str(payload.get("status") or "")
    reason = str(payload.get("reason") or "")
    if status == "host_unavailable":
        return 503
    if status == "not_found" or reason == "unknown_capability":
        return 404
    if status == "validation_error":
        return 400
    return 502


def _management_status_code(payload: Mapping[str, Any], *, success: int = 200) -> int:
    if payload.get("ok"):
        return success
    status = str(payload.get("status") or "")
    if status == "not_found":
        return 404
    if status in {"invalid_request", "invalid_config"}:
        return 400
    if status in {"unavailable", "timeout"}:
        return 503
    if status in {"approval_required", "activation_failed", "deactivation_failed", "conflict", "dependency_error"}:
        return 409
    return 500


def _job_control_status_code(payload: Mapping[str, Any]) -> int:
    if payload.get("ok"):
        return 200
    status = str(payload.get("status") or "")
    if status in {"unknown", "not_running"}:
        return 404
    if status in {"invalid", "pause_unavailable", "resume_unavailable"}:
        return 409 if status != "invalid" else 400
    if status == "unavailable":
        return 503
    return 409


def _request_error(reason: str) -> dict[str, Any]:
    return {"ok": False, "status": "invalid_request", "reason": reason}


def _response(payload: Mapping[str, Any], *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(dict(payload), status_code=status_code, headers={"Cache-Control": "no-store"})


__all__ = [
    "MAX_PLUGIN_REQUEST_BYTES",
    "MAX_PLUGIN_RESPONSE_BYTES",
    "build_plugins_router",
    "project_capability_result",
]
