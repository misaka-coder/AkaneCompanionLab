from __future__ import annotations

import time
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ..desktop_pet_contract import build_desktop_pet_error_payload
from ..desktop_pet_engine import desktop_message_attachment_cards
from ..store import normalize_character_pack_id


ResolveIdentity = Callable[[dict[str, Any]], tuple[str, str]]
ResolveQueryIdentity = Callable[[Request], tuple[str, str]]
LogEvent = Callable[..., None]
DEFAULT_SESSION_MESSAGE_LIMIT = 120
MAX_SESSION_MESSAGE_LIMIT = 120


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
        normalized_character_pack_id = character_pack_id or ""
        if ensure and normalized_character_pack_id:
            existing_session = engine.store.get_session(profile_user_id, session_id)
            existing_character_pack_id = str((existing_session or {}).get("character_pack_id") or "").strip()
            if existing_character_pack_id and existing_character_pack_id != normalized_character_pack_id:
                raise ValueError(
                    "session_character_mismatch:"
                    f" session_id={session_id}"
                    f" existing={existing_character_pack_id}"
                    f" requested={normalized_character_pack_id}"
                )
        session = (
            engine.store.ensure_session(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=normalized_character_pack_id,
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
        messages, message_page = read_message_page(
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            limit=DEFAULT_SESSION_MESSAGE_LIMIT,
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
            "message_page": message_page,
            "latest_final_json": latest_final_json,
        }

    def read_message_page(
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str | None,
        limit: int,
        before_seq: int | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, object]]:
        page_limit = max(1, min(MAX_SESSION_MESSAGE_LIMIT, int(limit)))
        rows = engine.store.get_session_messages(
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            limit=page_limit + 1,
            before_seq=before_seq,
        )
        has_more = len(rows) > page_limit
        messages = rows[-page_limit:]
        messages = [{**message, "attachments": desktop_message_attachment_cards(
            engine, message=message, profile_user_id=profile_user_id, session_id=session_id,
            character_pack_id=character_pack_id if character_pack_id is not None else str(message.get("character_pack_id") or ""),
        )} for message in messages]
        next_before_seq = int(messages[0].get("seq_no") or 0) if has_more and messages else None
        return messages, {
            "limit": page_limit,
            "has_more": has_more,
            "next_before_seq": next_before_seq,
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

    @router.get("/sessions/messages")
    async def sessions_messages(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        session_id, profile_user_id = resolve_identity_from_query(request)
        character_pack_id = resolve_character_pack_id_from_query(request)
        try:
            limit = int(request.query_params.get("limit") or 60)
            raw_before_seq = str(request.query_params.get("before_seq") or "").strip()
            before_seq = int(raw_before_seq) if raw_before_seq else None
            if before_seq is not None and before_seq <= 0:
                raise ValueError("before_seq must be positive")
        except (TypeError, ValueError) as exc:
            runtime_metrics.observe_request(
                "sessions_messages",
                duration_ms=(time.perf_counter() - started_at) * 1000,
                ok=False,
            )
            return JSONResponse(
                build_desktop_pet_error_payload(
                    error="invalid_pagination",
                    message=str(exc)[:160] or "无效的历史分页参数",
                    retryable=False,
                ),
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )

        session = (
            engine.store.get_character_session(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id or "",
            )
            if character_pack_id is not None
            else engine.store.get_session(profile_user_id, session_id)
        )
        if session is None:
            runtime_metrics.observe_request(
                "sessions_messages",
                duration_ms=(time.perf_counter() - started_at) * 1000,
                ok=False,
            )
            raise HTTPException(status_code=404, detail="session not found")

        try:
            messages, message_page = read_message_page(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                limit=limit,
                before_seq=before_seq,
            )
        except Exception as exc:
            runtime_metrics.observe_request(
                "sessions_messages",
                duration_ms=(time.perf_counter() - started_at) * 1000,
                ok=False,
            )
            log_event(
                "sessions_messages_error",
                session_id=session_id,
                profile_user_id=profile_user_id,
                message=str(exc),
            )
            raise

        runtime_metrics.observe_request(
            "sessions_messages",
            duration_ms=(time.perf_counter() - started_at) * 1000,
            ok=True,
        )
        return JSONResponse(
            {
                "session_id": session_id,
                "character_pack_id": character_pack_id or "",
                "messages": messages,
                "message_page": message_page,
            },
            headers={"Cache-Control": "no-store"},
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
            if str(exc).startswith("session_character_mismatch:"):
                return JSONResponse(
                    build_desktop_pet_error_payload(
                        error="session_character_mismatch",
                        message="当前会话属于另一个角色，请为当前角色创建新会话。",
                        retryable=True,
                        details={
                            "session_id": session_id,
                            "character_pack_id": character_pack_id or "",
                        },
                    ),
                    status_code=409,
                    headers={"Cache-Control": "no-store"},
                )
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
