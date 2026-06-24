from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import time
import tracemalloc
from inspect import Parameter, isawaitable, signature
from typing import Any, Callable, Mapping

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..desktop_pet_contract import (
    DESKTOP_PET_CONTRACT_VERSION,
    DESKTOP_PET_DEFAULT_EMOTION,
    DESKTOP_PET_DEFAULT_OUTFIT,
    build_desktop_pet_diagnostics_payload,
    decorate_resource_manifest_for_desktop_pet,
)
from .desktop_pet import decorate_desktop_workspace_urls


def _is_local_request(request: Request) -> bool:
    host = str(getattr(getattr(request, "client", None), "host", "") or "").strip().lower()
    return host in {"127.0.0.1", "::1", "localhost", "testclient"}


def build_control_center_router(
    *,
    runtime_metrics: Any = None,
    log_event: Callable[..., Any] | None = None,
    resolve_identity_from_query: Callable[[Request], tuple[str, str]] | None = None,
    snapshot_runtime_providers: Mapping[str, Callable[..., Any]] | None = None,
    settings_override_store: Any = None,
    config_module: Any = None,
) -> APIRouter:
    router = APIRouter()
    runtime_providers = dict(snapshot_runtime_providers or {})

    @router.get("/control-center/actions")
    async def describe_control_center_actions() -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "status": "available",
                "contractVersion": 1,
                "actionsEndpoint": "/control-center/actions/{actionId}",
                "execution": "not-implemented",
                "defaultResult": {
                    "ok": False,
                    "status": "not-implemented",
                    "refresh": False,
                },
            },
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/control-center/actions/{action_id}")
    async def run_control_center_action(action_id: str, request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception:
            payload = {}

        if not isinstance(payload, dict):
            payload = {}

        if runtime_metrics is not None:
            try:
                runtime_metrics.observe_request(f"control_center.action.{action_id}", duration_ms=0, ok=False)
            except Exception:
                pass

        if log_event is not None:
            try:
                log_event(
                    "control_center_action",
                    action_id=action_id,
                    status="not-implemented",
                    payload_keys=sorted(str(key) for key in payload.keys()),
                )
            except Exception:
                pass

        return JSONResponse(
            {
                "ok": False,
                "status": "not-implemented",
                "actionId": action_id,
                "refresh": False,
            },
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/control-center/snapshot")
    async def read_control_center_snapshot(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        context = _build_snapshot_context(request, resolve_identity_from_query)
        runtime = {
            field: await _read_snapshot_runtime_field(field, runtime_providers, context)
            for field in ("health", "diagnostics", "workspace", "resourceManifest", "metrics")
        }
        _observe_request(runtime_metrics, "control_center.snapshot", started_at, True)
        _log_best_effort(log_event, "control_center_snapshot", runtime_fields=sorted(runtime.keys()))
        return JSONResponse(
            {
                "ok": True,
                "status": "available",
                "schemaVersion": 1,
                "sourceKind": "backend",
                "generatedAt": _now_iso(),
                "runtime": runtime,
            },
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/control-center/settings-catalog")
    async def read_settings_catalog(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        # Reveals current (non-secret) config values, so gate to local requests
        # like the model-service route — never expose deployment config publicly.
        if not _is_local_request(request):
            _observe_request(runtime_metrics, "control_center.settings_catalog", started_at, False)
            return JSONResponse(
                {"ok": False, "status": "forbidden", "reason": "local_request_required"},
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
        from ..settings_catalog import build_settings_catalog

        catalog = build_settings_catalog()
        _observe_request(runtime_metrics, "control_center.settings_catalog", started_at, True)
        _log_best_effort(
            log_event,
            "control_center_settings_catalog",
            categories=len(catalog.get("categories", [])),
        )
        return JSONResponse(
            {
                "ok": True,
                "status": "available",
                "schemaVersion": catalog.get("schemaVersion", 1),
                "sourceKind": "backend",
                "generatedAt": _now_iso(),
                "scopeLegend": catalog.get("scopeLegend", {}),
                "categories": catalog.get("categories", []),
            },
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/control-center/settings-catalog/{key}")
    async def update_setting(key: str, request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        if not _is_local_request(request):
            _observe_request(runtime_metrics, "control_center.settings_update", started_at, False)
            return JSONResponse(
                {"ok": False, "status": "forbidden", "reason": "local_request_required"},
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
        if settings_override_store is None or config_module is None:
            return JSONResponse(
                {"ok": False, "status": "not-available", "key": key},
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        raw_value = payload.get("value") if isinstance(payload, dict) else None
        from ..settings_overrides import SettingOverrideError, set_override

        try:
            applied = set_override(config_module, settings_override_store, key=key, raw_value=raw_value)
        except SettingOverrideError as exc:
            _observe_request(runtime_metrics, "control_center.settings_update", started_at, False)
            return JSONResponse(
                {"ok": False, "status": exc.reason, "key": key},
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )
        except Exception:
            _observe_request(runtime_metrics, "control_center.settings_update", started_at, False)
            return JSONResponse(
                {"ok": False, "status": "error", "key": key},
                status_code=500,
                headers={"Cache-Control": "no-store"},
            )
        _observe_request(runtime_metrics, "control_center.settings_update", started_at, True)
        _log_best_effort(log_event, "control_center_settings_update", key=key)
        return JSONResponse(
            {"ok": True, "status": "applied", "key": key, "value": applied},
            headers={"Cache-Control": "no-store"},
        )

    return router


def build_control_center_snapshot_runtime_providers(
    *,
    engine: Any,
    config_module: Any,
    runtime_metrics: Any = None,
    public_guard: Any = None,
) -> dict[str, Callable[[dict[str, Any]], Any]]:
    return {
        "health": lambda context: _build_snapshot_health(config_module),
        "diagnostics": lambda context: _build_snapshot_diagnostics(
            context,
            engine=engine,
            runtime_metrics=runtime_metrics,
            public_guard=public_guard,
        ),
        "workspace": lambda context: _build_snapshot_workspace(context, engine=engine),
        "resourceManifest": lambda context: _build_snapshot_resource_manifest(
            context,
            engine=engine,
        ),
        "metrics": lambda context: _build_snapshot_metrics_text(
            engine=engine,
            runtime_metrics=runtime_metrics,
            public_guard=public_guard,
        ),
    }


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _unavailable(reason: str = "placeholder") -> dict[str, Any]:
    return {"ok": False, "status": "unavailable", "error": reason}


async def _read_snapshot_runtime_field(
    field: str,
    providers: Mapping[str, Callable[..., Any]],
    context: dict[str, Any],
) -> Any:
    provider = providers.get(field)
    if provider is None:
        return _unavailable("placeholder")

    try:
        value = _call_snapshot_provider(provider, context)
        if isawaitable(value):
            value = await value
        if value is None:
            return _unavailable("empty")
        return value
    except Exception as exc:
        return _unavailable(str(exc) or "provider-failed")


def _call_snapshot_provider(provider: Callable[..., Any], context: dict[str, Any]) -> Any:
    if _provider_accepts_context(provider):
        return provider(context)
    return provider()


def _provider_accepts_context(provider: Callable[..., Any]) -> bool:
    try:
        provider_signature = signature(provider)
    except (TypeError, ValueError):
        return True

    for parameter in provider_signature.parameters.values():
        if parameter.kind in (Parameter.VAR_POSITIONAL, Parameter.VAR_KEYWORD):
            return True
        if parameter.kind in (Parameter.POSITIONAL_ONLY, Parameter.POSITIONAL_OR_KEYWORD):
            return True
    return False


def _build_snapshot_context(
    request: Request,
    resolve_identity_from_query: Callable[[Request], tuple[str, str]] | None,
) -> dict[str, Any]:
    if resolve_identity_from_query is not None:
        session_id, profile_user_id = resolve_identity_from_query(request)
    else:
        session_id = str(request.query_params.get("user_id") or request.query_params.get("session_id") or "default_session")
        profile_user_id = str(request.query_params.get("real_user_id") or session_id)
    return {
        "request": request,
        "session_id": session_id,
        "profile_user_id": profile_user_id,
        "client": str(request.query_params.get("client") or request.query_params.get("client_mode") or "desktop_pet"),
        "character_pack_id": str(request.query_params.get("character_pack_id") or request.query_params.get("characterPackId") or ""),
        "outfit": str(request.query_params.get("outfit") or ""),
        "emotion": str(request.query_params.get("emotion") or ""),
        "limit": _bounded_int(request.query_params.get("limit"), default=24, minimum=1, maximum=60),
    }


def _build_snapshot_health(config_module: Any) -> dict[str, Any]:
    return {
        "status": "ok",
        "pid": os.getpid(),
        "python": sys.executable,
        "yt_dlp": importlib.util.find_spec("yt_dlp") is not None,
        "contracts": {
            "desktop_pet": {
                "version": DESKTOP_PET_CONTRACT_VERSION,
                "health": "/desktop-pet/health",
                "resource_manifest": "/resource-manifest",
                "think": "/think",
                "tts": "/tts",
                "streaming_tts": bool(getattr(config_module, "STREAMING_TTS_ENABLED", True)),
            }
        },
    }


def _build_snapshot_diagnostics(
    context: dict[str, Any],
    *,
    engine: Any,
    runtime_metrics: Any,
    public_guard: Any,
) -> dict[str, Any]:
    guard_snapshot: dict[str, Any] | None = None
    if public_guard is not None and hasattr(public_guard, "snapshot"):
        guard_snapshot = _safe_dict(public_guard.snapshot)
    return build_desktop_pet_diagnostics_payload(
        engine=engine,
        profile_user_id=str(context.get("profile_user_id") or ""),
        session_id=str(context.get("session_id") or ""),
        character_pack_id=str(context.get("character_pack_id") or ""),
        preferred_outfit=str(context.get("outfit") or ""),
        preferred_emotion=str(context.get("emotion") or ""),
        runtime_metrics=_safe_dict(runtime_metrics.snapshot) if hasattr(runtime_metrics, "snapshot") else None,
        public_guard_snapshot=guard_snapshot,
    )


async def _build_snapshot_workspace(context: dict[str, Any], *, engine: Any) -> dict[str, Any]:
    payload = await asyncio.to_thread(
        engine.build_desktop_pet_workspace_panel,
        profile_user_id=str(context.get("profile_user_id") or ""),
        session_id=str(context.get("session_id") or ""),
        limit=int(context.get("limit") or 24),
    )
    if isinstance(payload, dict):
        decorate_desktop_workspace_urls(
            payload,
            session_id=str(context.get("session_id") or ""),
            profile_user_id=str(context.get("profile_user_id") or ""),
        )
    return payload if isinstance(payload, dict) else {}


def _build_snapshot_resource_manifest(context: dict[str, Any], *, engine: Any) -> dict[str, Any]:
    manifest = engine.build_resource_manifest(
        profile_user_id=str(context.get("profile_user_id") or ""),
        client_mode=str(context.get("client") or "desktop_pet"),
        character_pack_id=str(context.get("character_pack_id") or ""),
    )
    return decorate_resource_manifest_for_desktop_pet(
        manifest if isinstance(manifest, dict) else {},
        profile_user_id=str(context.get("profile_user_id") or ""),
        session_id=str(context.get("session_id") or ""),
        preferred_outfit=str(context.get("outfit") or "") or DESKTOP_PET_DEFAULT_OUTFIT,
        preferred_emotion=str(context.get("emotion") or "") or DESKTOP_PET_DEFAULT_EMOTION,
    )


def _build_snapshot_metrics_text(*, engine: Any, runtime_metrics: Any, public_guard: Any) -> str:
    current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    llm_metrics = _safe_dict(lambda: engine.llm.snapshot_metrics())
    vector_entries = _safe_number(lambda: engine.vector_store.count_entries())
    reindex_metrics = _safe_dict(engine.snapshot_embedding_reindex_status)
    counters = _safe_dict(runtime_metrics.snapshot) if hasattr(runtime_metrics, "snapshot") else {}
    guard_metrics = _safe_dict(public_guard.snapshot) if hasattr(public_guard, "snapshot") else {}

    lines = [
        "# TYPE akane_runtime gauge",
        f"akane_tracemalloc_current_bytes {int(current_bytes)}",
        f"akane_tracemalloc_peak_bytes {int(peak_bytes)}",
        f"akane_vector_entries {int(vector_entries)}",
        f"akane_embedding_reindex_total {int(reindex_metrics.get('total') or 0)}",
        f"akane_embedding_reindex_processed {int(reindex_metrics.get('processed') or 0)}",
        f"akane_embedding_reindex_running {1 if str(reindex_metrics.get('state') or '') == 'running' else 0}",
        f"akane_public_guard_enabled {1 if guard_metrics.get('enabled') else 0}",
        f"akane_public_guard_max_concurrent_thinks {int(guard_metrics.get('max_concurrent_thinks') or 0)}",
        f"akane_public_guard_daily_think_limit {int(guard_metrics.get('daily_think_limit') or 0)}",
        f"akane_public_guard_active_thinks {int(guard_metrics.get('active_thinks') or 0)}",
        f"akane_public_guard_used_today {int(guard_metrics.get('used_today') or 0)}",
    ]
    for key, value in sorted(counters.items()):
        lines.append(f"akane_{key} {_metric_number(value)}")
    for key, value in sorted(llm_metrics.items()):
        lines.append(f"akane_llm_{key} {int(_metric_number(value))}")
    return "\n".join(lines) + "\n"


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _safe_dict(factory: Callable[[], Any]) -> dict[str, Any]:
    try:
        value = factory()
        return dict(value or {}) if isinstance(value, Mapping) else {}
    except Exception:
        return {}


def _safe_number(factory: Callable[[], Any]) -> float:
    try:
        return _metric_number(factory())
    except Exception:
        return 0.0


def _metric_number(value: Any) -> float:
    try:
        number = float(value)
        return number if number == number else 0.0
    except (TypeError, ValueError):
        return 0.0


def _observe_request(runtime_metrics: Any, name: str, started_at: float, ok: bool) -> None:
    if runtime_metrics is None:
        return
    try:
        runtime_metrics.observe_request(name, duration_ms=(time.perf_counter() - started_at) * 1000, ok=ok)
    except Exception:
        pass


def _log_best_effort(log_event: Callable[..., Any] | None, event: str, **fields: Any) -> None:
    if log_event is None:
        return
    try:
        log_event(event, **fields)
    except Exception:
        pass
