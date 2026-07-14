"""Loopback-only diagnostics surface for the restart-only plugin host."""

from __future__ import annotations

import ipaddress
import json
from collections.abc import Mapping
from typing import Any

from capcore import InvocationContext
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..plugin_host import PluginHost
from ..plugin_result_projection import MAX_PLUGIN_RESPONSE_BYTES, project_capability_result


MAX_PLUGIN_REQUEST_BYTES = 16 * 1024


def build_plugins_router(*, plugin_host: PluginHost) -> APIRouter:
    router = APIRouter()

    @router.get("/admin/plugins/status")
    async def read_plugin_status(request: Request) -> JSONResponse:
        if not _is_loopback_socket_peer(request):
            return _response(
                {"ok": False, "status": "forbidden", "reason": "local_request_required"},
                status_code=403,
            )
        return _response(plugin_host.status_snapshot())

    @router.post("/admin/plugins/capabilities/{capability_id:path}/invoke")
    async def invoke_plugin_capability(capability_id: str, request: Request) -> JSONResponse:
        if not _is_loopback_socket_peer(request):
            return _response(
                {"ok": False, "status": "forbidden", "reason": "local_request_required"},
                status_code=403,
            )
        payload, error = await _read_bounded_json_object(request)
        if error is not None:
            return _response(error, status_code=400)

        result = await plugin_host.invoke(
            capability_id,
            payload,
            context=InvocationContext(client_mode="plugin_admin"),
        )
        projected = project_capability_result(result)
        status_code = _status_code_for_result(projected)
        return _response(projected, status_code=status_code)

    return router


def _is_loopback_socket_peer(request: Request) -> bool:
    host = str(getattr(getattr(request, "client", None), "host", "") or "").strip()
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


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
