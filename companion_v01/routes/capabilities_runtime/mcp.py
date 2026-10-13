"""Capability HTTP mcp responsibilities."""
from __future__ import annotations
import asyncio
import time
from pathlib import Path
from typing import Any, Mapping
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from ...local_capability_config import (
    get_mcp_server_runtime_config,
    list_mcp_server_configs,
    save_mcp_server_config,
    save_mcp_server_discovery,
)
from .context import CapabilityRouteContext
from .common import _maybe_thread
from .definitions import McpToolDiscoverer
from .common import _read_json_object, _resolve_identity, _observe_request, _log_best_effort


async def _discover_mcp_server_tools(
    *,
    base_dir: Path | None,
    profile_user_id: str,
    server_id: str,
    discoverer: McpToolDiscoverer | None,
) -> dict[str, Any]:
    server = get_mcp_server_runtime_config(
        base_dir=base_dir,
        profile_user_id=profile_user_id,
        server_id=server_id,
    )
    safe_server_id = str(server.get("serverId") or server_id or "").strip()
    if not server:
        return {
            "ok": False,
            "status": "missing_config",
            "serverId": safe_server_id,
            "reason": "mcp_server_config_missing",
            "refresh": False,
        }
    if not server.get("enabled"):
        return {
            "ok": False,
            "status": "disabled",
            "serverId": safe_server_id,
            "reason": "mcp_server_disabled",
            "refresh": False,
        }
    if discoverer is None:
        return {
            "ok": False,
            "status": "not-implemented",
            "serverId": safe_server_id,
            "reason": "mcp_discoverer_not_bound",
            "refresh": False,
        }
    try:
        discovered = await _maybe_thread(lambda: discoverer(server=server))
    except Exception:
        return {
            "ok": False,
            "status": "discovery-failed",
            "serverId": safe_server_id,
            "reason": "mcp_discovery_failed",
            "refresh": False,
        }
    payload = discovered if isinstance(discovered, Mapping) else {"tools": discovered if isinstance(discovered, list) else []}
    return save_mcp_server_discovery(
        base_dir=base_dir,
        profile_user_id=profile_user_id,
        server_id=safe_server_id,
        payload=payload,
    )


def register_mcp_routes(router: APIRouter, *, context: CapabilityRouteContext) -> None:
    engine = context.engine
    runtime_metrics = context.runtime_metrics
    log_event = context.log_event
    resolve_identity_from_query = context.resolve_identity_from_query
    provider_config_base_dir = context.provider_config_base_dir
    mcp_tool_discoverer = context.mcp_tool_discoverer

    @router.get("/capabilities/mcp-servers")
    async def read_capability_mcp_servers(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        service = getattr(engine, "mcp_management_service", None)
        if service is not None:
            payload = await asyncio.to_thread(service.list, profile_user_id=profile_user_id)
        else:
            payload = list_mcp_server_configs(
                base_dir=provider_config_base_dir,
                profile_user_id=profile_user_id,
            )
        _observe_request(runtime_metrics, "capabilities.mcp_servers", started_at, True)
        _log_best_effort(
            log_event,
            "capabilities_mcp_servers",
            status=payload.get("status"),
            total=payload.get("summary", {}).get("total"),
        )
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @router.post("/capabilities/mcp-servers/{server_id}/config")
    async def write_capability_mcp_server_config(server_id: str, request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        payload = await _read_json_object(request)
        service = getattr(engine, "mcp_management_service", None)
        if service is not None:
            result = await asyncio.to_thread(
                service.configure,
                profile_user_id=profile_user_id,
                server_id=server_id,
                payload=payload,
            )
        else:
            result = save_mcp_server_config(
                base_dir=provider_config_base_dir,
                profile_user_id=profile_user_id,
                server_id=server_id,
                payload=payload,
            )
        _observe_request(runtime_metrics, "capabilities.mcp_server_config", started_at, bool(result.get("ok")))
        _log_best_effort(
            log_event,
            "capabilities_mcp_server_config",
            status=result.get("status"),
            serverId=result.get("serverId"),
        )
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @router.post("/capabilities/mcp-servers/{server_id}/discover")
    async def discover_capability_mcp_server_tools(server_id: str, request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        service = getattr(engine, "mcp_management_service", None)
        if service is not None:
            result = await asyncio.to_thread(
                service.discover,
                profile_user_id=profile_user_id,
                server_id=server_id,
            )
        else:
            result = await _discover_mcp_server_tools(
                base_dir=provider_config_base_dir,
                profile_user_id=profile_user_id,
                server_id=server_id,
                discoverer=mcp_tool_discoverer,
            )
        _observe_request(runtime_metrics, "capabilities.mcp_server_discover", started_at, bool(result.get("ok")))
        _log_best_effort(
            log_event,
            "capabilities_mcp_server_discover",
            status=result.get("status"),
            serverId=result.get("serverId"),
            toolCount=result.get("toolCount"),
            reason=result.get("reason"),
        )
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    async def _change_mcp_lifecycle(server_id: str, request: Request, action: str) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        service = getattr(engine, "mcp_management_service", None)
        if service is None:
            result = {
                "ok": False,
                "status": "unavailable",
                "serverId": server_id,
                "reason": "mcp_management_service_unavailable",
            }
        elif action == "restart":
            result = await asyncio.to_thread(
                service.restart, profile_user_id=profile_user_id, server_id=server_id
            )
        elif action == "remove":
            result = await asyncio.to_thread(
                service.remove, profile_user_id=profile_user_id, server_id=server_id
            )
        else:
            result = await asyncio.to_thread(
                service.set_enabled,
                profile_user_id=profile_user_id,
                server_id=server_id,
                enabled=action == "enable",
            )
        _observe_request(
            runtime_metrics,
            f"capabilities.mcp_server_{action}",
            started_at,
            bool(result.get("ok")),
        )
        _log_best_effort(
            log_event,
            f"capabilities_mcp_server_{action}",
            status=result.get("status"),
            serverId=result.get("serverId"),
            reason=result.get("reason"),
        )
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @router.post("/capabilities/mcp-servers/{server_id}/enable")
    async def enable_capability_mcp_server(server_id: str, request: Request) -> JSONResponse:
        return await _change_mcp_lifecycle(server_id, request, "enable")

    @router.post("/capabilities/mcp-servers/{server_id}/disable")
    async def disable_capability_mcp_server(server_id: str, request: Request) -> JSONResponse:
        return await _change_mcp_lifecycle(server_id, request, "disable")

    @router.post("/capabilities/mcp-servers/{server_id}/restart")
    async def restart_capability_mcp_server(server_id: str, request: Request) -> JSONResponse:
        return await _change_mcp_lifecycle(server_id, request, "restart")

    @router.delete("/capabilities/mcp-servers/{server_id}")
    async def remove_capability_mcp_server(server_id: str, request: Request) -> JSONResponse:
        return await _change_mcp_lifecycle(server_id, request, "remove")
