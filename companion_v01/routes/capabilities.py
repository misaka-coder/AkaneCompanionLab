from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..local_capability_config import (
    check_provider_health,
    list_provider_configs,
    load_capability_config,
    preflight_workflow_execution,
    save_workflow_config,
    save_provider_config,
    validate_workflow_config,
)
from ..local_capability_catalog import (
    build_local_capability_catalog,
    build_local_workflow_catalog,
    probe_known_local_services,
)


LogEvent = Callable[..., None]
ProviderHealthChecker = Callable[[str, int, float], tuple[bool, str]]


def build_capabilities_router(
    *,
    engine: Any,
    config_module: Any = None,
    tts_client: Any = None,
    runtime_metrics: Any = None,
    log_event: LogEvent | None = None,
    resolve_identity_from_query: Callable[[Request], tuple[str, str]] | None = None,
    local_environment_probe: Callable[[], dict[str, Any]] | None = None,
    capability_config_base_dir: str | Path | None = None,
    provider_health_checker: ProviderHealthChecker | None = None,
) -> APIRouter:
    router = APIRouter()
    provider_config_base_dir = _resolve_provider_config_base_dir(
        capability_config_base_dir=capability_config_base_dir,
        config_module=config_module,
    )

    @router.get("/capabilities")
    async def read_capabilities(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        provider_config = load_capability_config(
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
        )
        payload = build_local_capability_catalog(
            engine=engine,
            config_module=config_module,
            tts_client=tts_client,
            profile_user_id=profile_user_id,
            provider_configs=provider_config.get("providers", {}),
            workflow_configs=provider_config.get("workflows", {}),
        )
        payload["providerConfigStatus"] = provider_config.get("configStatus") or "available"
        payload["providerConfigWarnings"] = list(provider_config.get("warnings") or [])
        _observe_request(runtime_metrics, "capabilities.catalog", started_at, True)
        _log_best_effort(
            log_event,
            "capabilities_catalog",
            status=payload.get("status"),
            total=payload.get("summary", {}).get("total"),
        )
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @router.get("/capabilities/providers")
    async def read_capability_providers(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        payload = list_provider_configs(
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
        )
        _observe_request(runtime_metrics, "capabilities.providers", started_at, True)
        _log_best_effort(
            log_event,
            "capabilities_providers",
            status=payload.get("status"),
            total=payload.get("summary", {}).get("total"),
        )
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

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
        )
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
        _observe_request(runtime_metrics, "capabilities.workflow_preflight", started_at, bool(result.get("ok")))
        _log_best_effort(
            log_event,
            "capabilities_workflow_preflight",
            status=result.get("status"),
            workflowId=result.get("workflowId"),
        )
        status_code = 404 if result.get("status") == "unknown_workflow" else 200
        return JSONResponse(result, status_code=status_code, headers={"Cache-Control": "no-store"})

    @router.post("/capabilities/providers/{provider_id}/config")
    async def write_capability_provider_config(provider_id: str, request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        payload = await _read_json_object(request)
        result = save_provider_config(
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
            provider_id=provider_id,
            payload=payload,
        )
        _observe_request(runtime_metrics, "capabilities.provider_config", started_at, bool(result.get("ok")))
        _log_best_effort(
            log_event,
            "capabilities_provider_config",
            status=result.get("status"),
            providerId=result.get("providerId"),
        )
        status_code = 404 if result.get("status") == "unknown_provider" else 200
        return JSONResponse(result, status_code=status_code, headers={"Cache-Control": "no-store"})

    @router.post("/capabilities/providers/{provider_id}/health-check")
    async def check_capability_provider_health(provider_id: str, request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        payload = await _read_json_object(request)
        result = await asyncio.to_thread(
            check_provider_health,
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
            provider_id=provider_id,
            payload=payload,
            health_checker=provider_health_checker,
        )
        _observe_request(runtime_metrics, "capabilities.provider_health_check", started_at, result.get("status") == "ready")
        _log_best_effort(
            log_event,
            "capabilities_provider_health_check",
            status=result.get("status"),
            providerId=result.get("providerId"),
        )
        status_code = 404 if result.get("status") == "unknown_provider" else 200
        return JSONResponse(result, status_code=status_code, headers={"Cache-Control": "no-store"})

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
                error=str(exc)[:160],
            )
            return JSONResponse(
                {
                    "ok": False,
                    "status": "error",
                    "reason": str(exc)[:200] or "local_environment_check_failed",
                    "autoEnable": False,
                    "services": [],
                },
                headers={"Cache-Control": "no-store"},
            )

    return router


async def _read_json_object(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


async def _maybe_thread(callback: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    value = callback()
    if hasattr(value, "__await__"):
        return await value
    return value


def _resolve_identity(
    request: Request,
    resolve_identity_from_query: Callable[[Request], tuple[str, str]] | None,
) -> tuple[str, str]:
    if resolve_identity_from_query is not None:
        return resolve_identity_from_query(request)
    session_id = str(request.query_params.get("user_id") or request.query_params.get("session_id") or "default_session")
    profile_user_id = str(request.query_params.get("real_user_id") or request.query_params.get("profileUserId") or session_id)
    return session_id, profile_user_id


def _resolve_provider_config_base_dir(
    *,
    capability_config_base_dir: str | Path | None,
    config_module: Any = None,
) -> Path | None:
    if capability_config_base_dir is not None:
        return Path(capability_config_base_dir)
    data_dir = getattr(config_module, "DATA_DIR", None)
    if data_dir:
        return Path(data_dir)
    return None


def _observe_request(runtime_metrics: Any, name: str, started_at: float, ok: bool) -> None:
    if runtime_metrics is None:
        return
    try:
        runtime_metrics.observe_request(name, duration_ms=(time.perf_counter() - started_at) * 1000, ok=ok)
    except Exception:
        pass


def _log_best_effort(log_event: LogEvent | None, event: str, **fields: Any) -> None:
    if log_event is None:
        return
    try:
        log_event(event, **fields)
    except Exception:
        pass
