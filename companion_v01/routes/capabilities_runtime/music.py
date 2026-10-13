"""Capability HTTP music responsibilities."""
from __future__ import annotations
import asyncio
import time
from typing import Any
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from .context import CapabilityRouteContext
from .common import (
    _read_json_object,
    _resolve_identity,
    _resolve_identity_with_payload,
    _observe_request,
    _log_best_effort,
)


_CONTROL_NAMES = ("pause", "next", "prev", "recommend")


def _coerce_control_flag(value: Any) -> bool:
    if isinstance(value, str) and value.strip().lower() in {"0", "false", "no", "off", "disabled"}:
        return False
    return bool(value)


def _coerce_recent_limit(value: Any) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError, OverflowError):
        return 5


def register_music_routes(router: APIRouter, *, context: CapabilityRouteContext) -> None:
    engine = context.engine
    runtime_metrics = context.runtime_metrics
    log_event = context.log_event
    resolve_identity_from_query = context.resolve_identity_from_query
    lyrics_service = context.lyrics_service

    @router.post("/capabilities/music/lyrics")
    async def resolve_music_lyrics(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        payload = await _read_json_object(request)
        _session_id, profile_user_id = _resolve_identity_with_payload(
            request,
            payload,
            resolve_identity_from_query,
        )
        result = await asyncio.to_thread(
            lyrics_service.resolve_lyrics,
            profile_user_id=profile_user_id,
            payload=payload,
        )
        ok = bool(result.get("ok"))
        _observe_request(runtime_metrics, "capabilities.music_lyrics", started_at, ok)
        _log_best_effort(
            log_event,
            "capabilities_music_lyrics",
            status=result.get("status"),
            reason=result.get("reason"),
            cached=bool(result.get("cached")),
            lineCount=int(result.get("lineCount") or 0),
        )
        status_code = 400 if result.get("status") == "invalid_request" else 200
        return JSONResponse(result, status_code=status_code, headers={"Cache-Control": "no-store"})

    @router.post("/capabilities/music/co_listen_summary")
    async def resolve_co_listen_summary(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        payload = await _read_json_object(request)
        _session_id, profile_user_id = _resolve_identity_with_payload(
            request,
            payload,
            resolve_identity_from_query,
        )
        from ...music_context import summarize_listening_together
        from ... import music_control_store

        store = getattr(engine, "store", None)
        if store is None:
            result = {
                "ok": False,
                "status": "store_unavailable",
                "now": None,
                "recent": [],
                "enabled_music_controls": sorted(music_control_store.ALLOWED_CONTROL_NAMES),
            }
            _observe_request(runtime_metrics, "capabilities.music_co_listen_summary", started_at, False)
            return JSONResponse(result, status_code=503, headers={"Cache-Control": "no-store"})

        def _fetch_summary_with_controls():
            r = summarize_listening_together(
                store=store,
                profile_user_id=profile_user_id,
                title=str(payload.get("title") or ""),
                artist=str(payload.get("artist") or ""),
                album=str(payload.get("album") or ""),
                source_kind=str(payload.get("source_kind") or payload.get("sourceKind") or ""),
                source_app=str(payload.get("source_app") or payload.get("sourceApp") or ""),
                system_media=bool(payload.get("system_media") or payload.get("systemMedia") or False),
                recent_limit=_coerce_recent_limit(payload.get("recent_limit") or payload.get("recentLimit") or 5),
            )
            try:
                connect_fn = getattr(store, "_connect", None)
                if connect_fn is not None:
                    with connect_fn() as conn:
                        music_control_store.ensure_schema(conn)
                        enabled = music_control_store.get_enabled_controls(
                            conn, profile_user_id=profile_user_id
                        )
                    r["enabled_music_controls"] = sorted(enabled)
                else:
                    r["enabled_music_controls"] = sorted(music_control_store.ALLOWED_CONTROL_NAMES)
            except Exception:
                r["enabled_music_controls"] = sorted(music_control_store.ALLOWED_CONTROL_NAMES)
            return r

        result = await asyncio.to_thread(_fetch_summary_with_controls)
        ok = bool(result.get("ok"))
        _observe_request(runtime_metrics, "capabilities.music_co_listen_summary", started_at, ok)
        _log_best_effort(
            log_event,
            "capabilities_music_co_listen_summary",
            status=result.get("status"),
            has_now=bool(result.get("now")),
            recent=len(result.get("recent") or []),
        )
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @router.get("/capabilities/music/control_permissions")
    async def get_music_control_permissions(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        from ... import music_control_store

        store = getattr(engine, "store", None)
        if store is None:
            _observe_request(runtime_metrics, "capabilities.music_control_permissions_get", started_at, False)
            return JSONResponse(
                {"ok": False, "status": "store_unavailable",
                 "controls": dict.fromkeys(_CONTROL_NAMES, True)},
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )
        if not callable(getattr(store, "_connect", None)):
            _observe_request(runtime_metrics, "capabilities.music_control_permissions_get", started_at, False)
            return JSONResponse(
                {"ok": False, "status": "store_unavailable", "reason": "music_control_store_unavailable",
                 "controls": dict.fromkeys(_CONTROL_NAMES, True)},
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )

        def _get_controls():
            connect_fn = getattr(store, "_connect", None)
            with connect_fn() as conn:
                music_control_store.ensure_schema(conn)
                enabled = music_control_store.get_enabled_controls(conn, profile_user_id=profile_user_id)
            return {c: (c in enabled) for c in _CONTROL_NAMES}

        try:
            controls = await asyncio.to_thread(_get_controls)
        except Exception as exc:
            _observe_request(runtime_metrics, "capabilities.music_control_permissions_get", started_at, False)
            return JSONResponse(
                {"ok": False, "status": "query_failed", "reason": "music_control_query_failed",
                 "error": type(exc).__name__, "controls": dict.fromkeys(_CONTROL_NAMES, True)},
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )

        _observe_request(runtime_metrics, "capabilities.music_control_permissions_get", started_at, True)
        return JSONResponse(
            {"ok": True, "status": "ready", "controls": controls},
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/capabilities/music/control_permissions")
    async def set_music_control_permissions(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        payload = await _read_json_object(request)
        _session_id, profile_user_id = _resolve_identity_with_payload(
            request, payload, resolve_identity_from_query
        )
        from ... import music_control_store

        store = getattr(engine, "store", None)
        if store is None:
            _observe_request(runtime_metrics, "capabilities.music_control_permissions_set", started_at, False)
            return JSONResponse(
                {"ok": False, "status": "store_unavailable",
                 "controls": dict.fromkeys(_CONTROL_NAMES, True)},
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )
        if not callable(getattr(store, "_connect", None)):
            _observe_request(runtime_metrics, "capabilities.music_control_permissions_set", started_at, False)
            return JSONResponse(
                {"ok": False, "status": "store_unavailable", "reason": "music_control_store_unavailable",
                 "controls": dict.fromkeys(_CONTROL_NAMES, True)},
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )

        raw_controls = payload.get("controls")
        coerced = {k: _coerce_control_flag(v) for k, v in raw_controls.items()} if isinstance(raw_controls, dict) else {}

        def _set_and_get() -> dict:
            connect_fn = getattr(store, "_connect", None)
            with connect_fn() as conn:
                music_control_store.ensure_schema(conn)
                music_control_store.bulk_set_controls(
                    conn,
                    profile_user_id=profile_user_id,
                    controls=coerced,
                    now_ts=int(time.time()),
                )
                enabled = music_control_store.get_enabled_controls(conn, profile_user_id=profile_user_id)
            return {c: (c in enabled) for c in _CONTROL_NAMES}

        try:
            controls = await asyncio.to_thread(_set_and_get)
        except Exception as exc:
            _observe_request(runtime_metrics, "capabilities.music_control_permissions_set", started_at, False)
            return JSONResponse(
                {"ok": False, "status": "update_failed", "reason": "music_control_update_failed",
                 "error": type(exc).__name__, "controls": dict.fromkeys(_CONTROL_NAMES, True)},
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )

        _observe_request(runtime_metrics, "capabilities.music_control_permissions_set", started_at, True)
        return JSONResponse(
            {"ok": True, "status": "ready", "controls": controls},
            headers={"Cache-Control": "no-store"},
        )
