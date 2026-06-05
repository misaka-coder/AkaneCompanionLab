from __future__ import annotations

import time
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ..desktop_pet_contract import build_desktop_pet_error_payload
from ..store import normalize_character_pack_id


ResolveIdentity = Callable[[dict[str, Any]], tuple[str, str]]
ResolveQueryIdentity = Callable[[Request], tuple[str, str]]
LogEvent = Callable[..., None]


def build_sessions_router(
    *,
    engine: Any,
    runtime_metrics: Any,
    log_event: LogEvent,
    resolve_identity_from_query: ResolveQueryIdentity,
    resolve_identity_from_payload: ResolveIdentity,
) -> APIRouter:
    router = APIRouter()

    def resolve_character_pack_id_from_payload(payload: dict[str, Any]) -> str | None:
        for key in ("character_pack_id", "characterPackId", "character_pack"):
            if key in payload:
                return normalize_character_pack_id(payload.get(key))
        current_visual = payload.get("current_visual")
        if isinstance(current_visual, dict):
            for key in ("character_pack_id", "characterPackId", "character_pack"):
                if key in current_visual:
                    return normalize_character_pack_id(current_visual.get(key))
        return None

    def resolve_character_pack_id_from_query(request: Request) -> str | None:
        for key in ("character_pack_id", "characterPackId", "character_pack"):
            if key in request.query_params:
                return normalize_character_pack_id(request.query_params.get(key))
        return None

    def build_session_state_payload(
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str | None = None,
        ensure: bool,
        display_title: str | None = None,
    ) -> dict[str, object]:
        session = (
            engine.store.ensure_session(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id or "",
                display_title=display_title,
            )
            if ensure
            else (
                engine.store.get_character_session(
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    character_pack_id=character_pack_id or "",
                )
                if character_pack_id is not None
                else engine.store.get_session(profile_user_id, session_id)
            )
        )
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")

        sessions = engine.store.list_sessions(
            profile_user_id=profile_user_id,
            limit=50,
            character_pack_id=character_pack_id,
        )
        messages = engine.store.get_session_messages(
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            limit=120,
        )
        latest_eval = engine.store.get_latest_eval_turn_for_session(
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        latest_final_json = latest_eval.get("final_json") if latest_eval else None
        if not isinstance(latest_final_json, dict):
            latest_final_json = None

        return {
            "session": session,
            "sessions": sessions,
            "messages": messages,
            "latest_final_json": latest_final_json,
        }

    @router.get("/sessions")
    async def sessions_list(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        session_id, profile_user_id = resolve_identity_from_query(request)
        character_pack_id = resolve_character_pack_id_from_query(request)
        try:
            sessions = engine.store.list_sessions(
                profile_user_id=profile_user_id,
                limit=50,
                character_pack_id=character_pack_id,
            )
        except Exception as exc:
            duration_ms = (time.perf_counter() - started_at) * 1000
            runtime_metrics.observe_request("sessions_list", duration_ms=duration_ms, ok=False)
            log_event("sessions_list_error", session_id=session_id, profile_user_id=profile_user_id, message=str(exc))
            raise

        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("sessions_list", duration_ms=duration_ms, ok=True)
        return JSONResponse(
            {
                "sessions": sessions,
                "current_session_id": session_id,
            }
        )

    @router.post("/sessions/ensure")
    async def sessions_ensure(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        try:
            payload = await request.json()
        except Exception as exc:
            runtime_metrics.observe_request(
                "sessions_ensure",
                duration_ms=(time.perf_counter() - started_at) * 1000,
                ok=False,
            )
            return JSONResponse(
                build_desktop_pet_error_payload(
                    error="invalid_json",
                    message=f"无法读取会话请求：{str(exc)[:160]}",
                    retryable=False,
                ),
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )
        if not isinstance(payload, dict):
            runtime_metrics.observe_request(
                "sessions_ensure",
                duration_ms=(time.perf_counter() - started_at) * 1000,
                ok=False,
            )
            return JSONResponse(
                build_desktop_pet_error_payload(
                    error="invalid_payload",
                    message="/sessions/ensure payload must be a JSON object",
                    retryable=False,
                ),
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )
        session_id, profile_user_id = resolve_identity_from_payload(payload)
        character_pack_id = resolve_character_pack_id_from_payload(payload)
        display_title = str(payload.get("display_title") or "").strip() or None

        try:
            response_payload = build_session_state_payload(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                ensure=True,
                display_title=display_title,
            )
        except Exception as exc:
            duration_ms = (time.perf_counter() - started_at) * 1000
            runtime_metrics.observe_request("sessions_ensure", duration_ms=duration_ms, ok=False)
            log_event("sessions_ensure_error", session_id=session_id, profile_user_id=profile_user_id, message=str(exc))
            raise

        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("sessions_ensure", duration_ms=duration_ms, ok=True)
        return JSONResponse(response_payload)

    @router.post("/sessions/rename")
    async def sessions_rename(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        payload = await request.json()
        session_id, profile_user_id = resolve_identity_from_payload(payload)
        character_pack_id = resolve_character_pack_id_from_payload(payload)
        display_title = str(payload.get("display_title") or "").strip()
        if not display_title:
            duration_ms = (time.perf_counter() - started_at) * 1000
            runtime_metrics.observe_request("sessions_rename", duration_ms=duration_ms, ok=False)
            raise HTTPException(status_code=400, detail="display_title is required")

        renamed = engine.store.rename_session(
            profile_user_id=profile_user_id,
            session_id=session_id,
            display_title=display_title,
        )
        if renamed is None:
            duration_ms = (time.perf_counter() - started_at) * 1000
            runtime_metrics.observe_request("sessions_rename", duration_ms=duration_ms, ok=False)
            raise HTTPException(status_code=404, detail="session not found")

        sessions = engine.store.list_sessions(
            profile_user_id=profile_user_id,
            limit=50,
            character_pack_id=character_pack_id,
        )
        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("sessions_rename", duration_ms=duration_ms, ok=True)
        return JSONResponse({"session": renamed, "sessions": sessions})

    return router
