"""Capability HTTP catalog responsibilities."""
from __future__ import annotations
import asyncio
import time
from typing import Any
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from ...local_capability_config import (
    get_approval_policy_config,
    get_effective_mcp_server_configs,
    load_capability_config,
)
from ...local_capability_catalog import build_local_capability_catalog, probe_known_local_services
from .context import CapabilityRouteContext
from .workflow_support import project_workflow_readiness
from .common import (
    _read_json_object,
    _maybe_thread,
    _resolve_identity,
    _resolve_character_voice_preference,
    _observe_request,
    _log_best_effort,
)


def _build_skill_catalog_payload(engine: Any) -> dict[str, Any]:
    registry = getattr(engine, "skill_registry", None)
    if registry is None:
        factory = getattr(engine, "_get_skill_registry", None)
        if callable(factory):
            try:
                registry = factory()
            except Exception:
                registry = None
    snapshot = None
    if registry is not None and callable(getattr(registry, "snapshot", None)):
        try:
            snapshot = registry.snapshot()
        except Exception:
            pass
    if snapshot is None:
        return {
            "status": "unavailable",
            "catalogRevision": "",
            "summary": {"total": 0, "bundled": 0, "managed": 0, "diagnostics": 0},
            "entries": [],
            "diagnostics": [],
        }
    entries = [
        {
            "name": str(entry.name),
            "description": str(entry.description),
            "source": str(entry.source),
            "revision": str(entry.revision),
            "requiredTools": list(entry.required_tools),
            "resourceCount": len(entry.files),
        }
        for entry in snapshot.entries
    ]
    diagnostics = [
        {
            "name": str(item.get("name") or ""),
            "source": str(item.get("source") or ""),
            "reason": str(item.get("reason") or "skill_invalid"),
            "fallback": str(item.get("fallback") or "ignored"),
        }
        for item in snapshot.diagnostics
    ]
    bundled = sum(entry["source"] == "bundled" for entry in entries)
    managed = sum(entry["source"] == "managed" for entry in entries)
    return {
        "status": "degraded" if diagnostics else "ready",
        "catalogRevision": str(snapshot.catalog_revision),
        "summary": {
            "total": len(entries),
            "bundled": bundled,
            "managed": managed,
            "diagnostics": len(diagnostics),
        },
        "entries": entries,
        "diagnostics": diagnostics,
    }


def register_catalog_routes(router: APIRouter, *, context: CapabilityRouteContext) -> None:
    engine = context.engine
    config_module = context.config_module
    tts_client = context.tts_client
    settings = context.settings
    runtime_metrics = context.runtime_metrics
    log_event = context.log_event
    resolve_identity_from_query = context.resolve_identity_from_query
    local_environment_probe = context.local_environment_probe
    provider_config_base_dir = context.provider_config_base_dir

    def exposure_scope(request):
        session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        return dict(base_dir=provider_config_base_dir, profile_user_id=profile_user_id,
                    session_id=session_id, character_pack_id=str(request.query_params.get("character_pack_id") or ""),
                    config_module=config_module)

    @router.get("/capabilities/tool-exposure")
    async def read_tool_exposure(request: Request) -> JSONResponse:
        from ...capability_exposure_service import read_exposure_state
        result = await asyncio.to_thread(read_exposure_state, engine, **exposure_scope(request))
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @router.post("/capabilities/tool-exposure")
    async def write_tool_exposure(request: Request) -> JSONResponse:
        from ...capability_exposure_service import save_exposure_state
        payload = await _read_json_object(request)
        result = await asyncio.to_thread(save_exposure_state, engine, payload=payload, **exposure_scope(request))
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @router.get("/capabilities")
    async def read_capabilities(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        character_voice = _resolve_character_voice_preference(engine, request)
        provider_config = load_capability_config(
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
        )
        payload = build_local_capability_catalog(
            engine=engine,
            config_module=config_module,
            tts_client=tts_client,
            settings=settings,
            profile_user_id=profile_user_id,
            provider_configs=provider_config.get("providers", {}),
            workflow_configs=provider_config.get("workflows", {}),
            mcp_server_configs=get_effective_mcp_server_configs(
                base_dir=provider_config_base_dir,
                profile_user_id=profile_user_id,
                profile_config=provider_config,
            ),
            approval_policy=provider_config.get("approvalPolicy", {}),
            character_voice=character_voice,
        )
        project_workflow_readiness(payload, context=context, profile_user_id=profile_user_id)
        payload["providerConfigStatus"] = provider_config.get("configStatus") or "available"
        payload["providerConfigWarnings"] = list(provider_config.get("warnings") or [])
        payload["approvalPolicy"] = get_approval_policy_config(
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
        )["approvalPolicy"]
        payload["skills"] = _build_skill_catalog_payload(engine)
        _observe_request(runtime_metrics, "capabilities.catalog", started_at, True)
        _log_best_effort(
            log_event,
            "capabilities_catalog",
            status=payload.get("status"),
            total=payload.get("summary", {}).get("total"),
        )
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @router.post("/capabilities/local-environment-check")
    async def check_local_environment() -> JSONResponse:
        started_at = time.perf_counter()
        try:
            if local_environment_probe is not None:
                payload = await _maybe_thread(local_environment_probe)
            else:
                payload = await asyncio.to_thread(probe_known_local_services)
            _observe_request(runtime_metrics, "capabilities.local_environment_check", started_at, True)
            _log_best_effort(
                log_event,
                "capabilities_local_environment_check",
                status=payload.get("status"),
                total=payload.get("summary", {}).get("total"),
            )
            return JSONResponse(payload, headers={"Cache-Control": "no-store"})
        except Exception as exc:
            _observe_request(runtime_metrics, "capabilities.local_environment_check", started_at, False)
            _log_best_effort(
                log_event,
                "capabilities_local_environment_check",
                status="error",
                error=type(exc).__name__,
            )
            return JSONResponse(
                {
                    "ok": False,
                    "status": "error",
                    "reason": "local_environment_check_failed",
                    "error": type(exc).__name__,
                    "autoEnable": False,
                    "services": [],
                },
                headers={"Cache-Control": "no-store"},
            )
