from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..local_capability_catalog import build_local_capability_catalog, probe_known_local_services


LogEvent = Callable[..., None]


def build_capabilities_router(
    *,
    engine: Any,
    config_module: Any = None,
    tts_client: Any = None,
    runtime_metrics: Any = None,
    log_event: LogEvent | None = None,
    resolve_identity_from_query: Callable[[Request], tuple[str, str]] | None = None,
    local_environment_probe: Callable[[], dict[str, Any]] | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/capabilities")
    async def read_capabilities(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        payload = build_local_capability_catalog(
            engine=engine,
            config_module=config_module,
            tts_client=tts_client,
            profile_user_id=profile_user_id,
        )
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
