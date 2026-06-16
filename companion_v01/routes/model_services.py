from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..model_service_config import (
    ModelServiceConfigStore,
    effective_settings_from_config,
    probe_model_ids,
    public_model_service_snapshot,
    redact_provider_error,
    settings_from_mapping,
    test_model_service,
    apply_model_service_settings,
)


def build_model_services_router(
    *,
    store: ModelServiceConfigStore,
    config_module: Any,
    engine: Any,
    runtime_metrics: Any = None,
    log_event: Callable[..., Any] | None = None,
    model_probe: Callable[..., list[str]] = probe_model_ids,
    connection_tester: Callable[..., str] = test_model_service,
) -> APIRouter:
    router = APIRouter()

    @router.get("/control-center/model-service")
    async def read_model_service() -> JSONResponse:
        started_at = time.perf_counter()
        settings, source, load_status = _load_effective_settings(store, config_module)
        _observe(runtime_metrics, "model_service.read", started_at, True)
        return _json(
            public_model_service_snapshot(
                settings,
                source=source,
                load_status=load_status,
            )
        )

    @router.post("/control-center/model-service")
    async def save_model_service(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        if not _is_local_request(request):
            _observe(runtime_metrics, "model_service.save", started_at, False)
            return _json({"ok": False, "status": "forbidden", "reason": "local_request_required"}, 403)
        payload = await _request_mapping(request)
        existing = _load_existing_secret(store, config_module, payload)
        try:
            settings = settings_from_mapping(payload, existing_api_key=existing)
            store.save(settings)
            apply_model_service_settings(config_module, settings)
            reload_result = engine.reload_model_services()
        except Exception as exc:
            _observe(runtime_metrics, "model_service.save", started_at, False)
            _log(log_event, "model_service_save", status="failed", reason=exc.__class__.__name__)
            return _json(
                {
                    "ok": False,
                    "status": "invalid_config",
                    "reason": redact_provider_error(exc, api_key=existing),
                }
            )

        _observe(runtime_metrics, "model_service.save", started_at, True)
        _log(
            log_event,
            "model_service_save",
            status="saved",
            provider_id=settings.provider_id,
            protocol=settings.protocol,
            chat_model=settings.chat_model,
        )
        return _json(
            {
                **public_model_service_snapshot(settings, source="local_file"),
                "refresh": True,
                "runtime": reload_result or {"status": "reloaded"},
            }
        )

    @router.post("/control-center/model-service/models")
    async def list_models(request: Request) -> JSONResponse:
        return await _run_candidate_action(
            request=request,
            action_name="model_service.models",
            store=store,
            config_module=config_module,
            runtime_metrics=runtime_metrics,
            log_event=log_event,
            runner=model_probe,
            require_model=False,
            result_builder=lambda result: {
                "ok": True,
                "status": "available",
                "models": list(result or []),
                "count": len(list(result or [])),
            },
        )

    @router.post("/control-center/model-service/test")
    async def test_connection(request: Request) -> JSONResponse:
        return await _run_candidate_action(
            request=request,
            action_name="model_service.test",
            store=store,
            config_module=config_module,
            runtime_metrics=runtime_metrics,
            log_event=log_event,
            runner=connection_tester,
            require_model=True,
            result_builder=lambda result: {
                "ok": True,
                "status": "connected",
                "message": str(result or "OK")[:120],
            },
        )

    return router


async def _run_candidate_action(
    *,
    request: Request,
    action_name: str,
    store: ModelServiceConfigStore,
    config_module: Any,
    runtime_metrics: Any,
    log_event: Callable[..., Any] | None,
    runner: Callable[..., Any],
    require_model: bool,
    result_builder: Callable[[Any], dict[str, Any]],
) -> JSONResponse:
    started_at = time.perf_counter()
    if not _is_local_request(request):
        _observe(runtime_metrics, action_name, started_at, False)
        return _json({"ok": False, "status": "forbidden", "reason": "local_request_required"}, 403)
    payload = await _request_mapping(request)
    existing = _load_existing_secret(store, config_module, payload)
    try:
        settings = settings_from_mapping(
            payload,
            existing_api_key=existing,
            require_model=require_model,
        )
        result = await asyncio.to_thread(runner, settings)
    except Exception as exc:
        _observe(runtime_metrics, action_name, started_at, False)
        _log(log_event, action_name.replace(".", "_"), status="failed", reason=exc.__class__.__name__)
        return _json(
            {
                "ok": False,
                "status": "request_failed",
                "reason": redact_provider_error(exc, api_key=existing),
            }
        )

    _observe(runtime_metrics, action_name, started_at, True)
    _log(
        log_event,
        action_name.replace(".", "_"),
        status="ok",
        provider_id=settings.provider_id,
        protocol=settings.protocol,
    )
    return _json(result_builder(result))


def _load_effective_settings(
    store: ModelServiceConfigStore,
    config_module: Any,
) -> tuple[Any, str, str]:
    try:
        saved = store.load()
    except Exception:
        return effective_settings_from_config(config_module), "environment", "invalid_config"
    if saved is not None:
        return saved, "local_file", "ok"
    return effective_settings_from_config(config_module), "environment", "ok"


def _load_existing_secret(
    store: ModelServiceConfigStore,
    config_module: Any,
    payload: dict[str, Any],
) -> str:
    try:
        saved = store.load()
    except Exception:
        saved = None
    existing = saved or effective_settings_from_config(config_module)
    requested_provider_id = str(
        payload.get("providerId") or payload.get("provider_id") or existing.provider_id
    ).strip()
    if requested_provider_id != existing.provider_id:
        return ""
    return existing.api_key


def _is_local_request(request: Request) -> bool:
    host = str(getattr(getattr(request, "client", None), "host", "") or "").strip().lower()
    return host in {"127.0.0.1", "::1", "localhost", "testclient"}


async def _request_mapping(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _observe(runtime_metrics: Any, name: str, started_at: float, ok: bool) -> None:
    if runtime_metrics is None or not hasattr(runtime_metrics, "observe_request"):
        return
    try:
        runtime_metrics.observe_request(
            name,
            duration_ms=(time.perf_counter() - started_at) * 1000,
            ok=ok,
        )
    except Exception:
        pass


def _log(log_event: Callable[..., Any] | None, event: str, **fields: Any) -> None:
    if log_event is None:
        return
    try:
        log_event(event, **fields)
    except Exception:
        pass


def _json(payload: dict[str, Any], status_code: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status_code, headers={"Cache-Control": "no-store"})
