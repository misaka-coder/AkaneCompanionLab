from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..petdesk_bridge import (
    build_akane_turn_payload,
    build_petdesk_display_envelope,
    build_petdesk_health_payload,
    build_petdesk_resource_bundle,
    clean_text,
    serialize_petdesk_sse,
)


LogEvent = Callable[..., None]


def build_petdesk_router(
    *,
    engine: Any,
    character_resources: Any = None,
    runtime_metrics: Any = None,
    public_guard: Any = None,
    log_event: LogEvent | None = None,
) -> APIRouter:
    # Transitional bridge for petdesk-runtime; see docs/petdesk_akane_bridge_m32.md before extending it.
    router = APIRouter(prefix="/pet")

    def _character_resources() -> Any:
        return (
            character_resources
            if character_resources is not None
            else getattr(engine, "desktop_pet_character_resources", None)
        )

    @router.get("/health")
    async def pet_health(request: Request) -> JSONResponse:
        character_pack_id = _character_pack_id_from_request(request)
        payload = build_petdesk_health_payload(_character_resources(), character_pack_id)
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @router.get("/snapshot")
    async def pet_snapshot(request: Request) -> JSONResponse:
        character_pack_id = _character_pack_id_from_request(request)
        bundle = build_petdesk_resource_bundle(_character_resources(), character_pack_id)
        manifest = _manifest_for_bundle(bundle.character_pack_id)
        envelope = build_petdesk_display_envelope(
            {},
            bundle=bundle,
            resource_manifest=manifest,
            fallback_speech="我在，petdesk runtime 已连接。",
        )
        return JSONResponse(envelope, headers={"Cache-Control": "no-store"})

    @router.post("/turn")
    async def pet_turn(request: Request):
        started_at = time.perf_counter()
        try:
            payload = await request.json()
        except Exception:
            _observe("pet_turn", started_at=started_at, ok=False)
            return JSONResponse(
                {
                    "ok": False,
                    "status": "error",
                    "reason": "invalid_json",
                    "message": "/pet/turn payload must be valid JSON.",
                },
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )
        if not isinstance(payload, dict):
            _observe("pet_turn", started_at=started_at, ok=False)
            return JSONResponse(
                {
                    "ok": False,
                    "status": "error",
                    "reason": "invalid_payload",
                    "message": "/pet/turn payload must be a JSON object.",
                },
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )
        if not clean_text(payload.get("text")):
            _observe("pet_turn", started_at=started_at, ok=False)
            return JSONResponse(
                {
                    "ok": False,
                    "status": "error",
                    "reason": "empty_text",
                    "message": "/pet/turn text is required.",
                },
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )

        guard_decision = _try_acquire_guard()
        if not bool(getattr(guard_decision, "allowed", True)):
            _observe("pet_turn", started_at=started_at, ok=False)
            reason = clean_text(getattr(guard_decision, "reason", "")) or "busy"
            message = clean_text(getattr(guard_decision, "message", "")) or "当前体验人数较多，请稍后再试。"
            _log("petdesk_guard_blocked", reason=reason)
            return JSONResponse(
                {
                    "ok": False,
                    "status": "error",
                    "reason": reason,
                    "message": message,
                },
                status_code=429,
                headers={"Cache-Control": "no-store"},
            )

        query_params = dict(request.query_params)
        akane_payload = build_akane_turn_payload(
            payload,
            query_params=query_params,
            character_resources=_character_resources(),
        )
        bundle = build_petdesk_resource_bundle(_character_resources(), akane_payload.get("character_pack_id"))
        manifest = _manifest_for_bundle(bundle.character_pack_id)
        turn_id = clean_text(payload.get("turnId"))

        async def _stream():
            ok = False
            try:
                yield serialize_petdesk_sse("resource_manifest", bundle.runtime_manifest)
                frame = await asyncio.to_thread(engine.process_turn, akane_payload)
                envelope = build_petdesk_display_envelope(
                    frame if isinstance(frame, dict) else {},
                    bundle=bundle,
                    resource_manifest=manifest,
                    turn_id=turn_id,
                    fallback_speech="我这边暂时没有可以显示的回复。",
                )
                yield serialize_petdesk_sse("display", envelope)
                ok = True
                _log(
                    "petdesk_turn_complete",
                    session_id=str(akane_payload.get("user_id") or ""),
                    character_pack_id=str(akane_payload.get("character_pack_id") or ""),
                )
            except Exception:
                yield serialize_petdesk_sse(
                    "error",
                    {
                        "reason": "akane_turn_failed",
                        "payload": {"retryable": True},
                    },
                )
                _log("petdesk_turn_error", session_id=str(akane_payload.get("user_id") or ""))
            finally:
                _release_guard(guard_decision)
                _observe("pet_turn", started_at=started_at, ok=ok)
                yield "event: done\ndata: {}\n\n"

        return StreamingResponse(
            _stream(),
            media_type="text/event-stream; charset=utf-8",
            headers={
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
            },
        )

    def _manifest_for_bundle(character_pack_id: str) -> Any:
        resources = _character_resources()
        getter = getattr(resources, "get_manifest", None)
        if not callable(getter) or not character_pack_id:
            return None
        try:
            return getter(character_pack_id)
        except Exception:
            return None

    def _try_acquire_guard() -> Any:
        if public_guard is None or not hasattr(public_guard, "try_acquire"):
            return _AllowedGuardDecision()
        try:
            return public_guard.try_acquire()
        except Exception:
            return _AllowedGuardDecision()

    def _release_guard(decision: Any) -> None:
        if not bool(getattr(decision, "acquired", False)):
            return
        if public_guard is None or not hasattr(public_guard, "release"):
            return
        try:
            public_guard.release()
        except Exception:
            return

    def _observe(name: str, *, started_at: float, ok: bool) -> None:
        observer = getattr(runtime_metrics, "observe_request", None)
        if not callable(observer):
            return
        try:
            observer(name, duration_ms=(time.perf_counter() - started_at) * 1000, ok=ok)
        except Exception:
            return

    def _log(event: str, **fields: object) -> None:
        if log_event is None:
            return
        try:
            log_event(event, **fields)
        except Exception:
            return

    return router


class _AllowedGuardDecision:
    allowed = True
    acquired = False
    reason = ""
    message = ""


def _character_pack_id_from_request(request: Request) -> str:
    return (
        clean_text(request.query_params.get("character_pack_id"))
        or clean_text(request.query_params.get("characterPackId"))
        or clean_text(request.query_params.get("packId"))
    )
