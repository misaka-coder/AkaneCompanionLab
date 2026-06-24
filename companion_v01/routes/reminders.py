from __future__ import annotations

import time
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse


LogEvent = Callable[..., None]
ResolveIdentityFromQuery = Callable[[Request], tuple[str, str]]
ResolveIdentityFromPayload = Callable[[dict], tuple[str, str]]


def build_reminders_router(
    *,
    engine: Any,
    runtime_metrics: Any,
    log_event: LogEvent,
    resolve_identity_from_query: ResolveIdentityFromQuery,
    resolve_identity_from_payload: ResolveIdentityFromPayload,
) -> APIRouter:
    router = APIRouter()

    @router.get("/reminders/due")
    async def reminders_due(request: Request):
        started_at = time.perf_counter()
        session_id, profile_user_id = resolve_identity_from_query(request)
        now_ts = int(request.query_params.get("timestamp") or time.time())
        limit = max(1, int(request.query_params.get("limit") or 3))

        try:
            notifications = engine.consume_due_reminders(
                profile_user_id=profile_user_id,
                session_id=session_id,
                now_ts=now_ts,
                limit=limit,
            )
        except Exception as exc:
            duration_ms = (time.perf_counter() - started_at) * 1000
            runtime_metrics.observe_request("reminders_due", duration_ms=duration_ms, ok=False)
            log_event(
                "reminders_due_error",
                session_id=session_id,
                profile_user_id=profile_user_id,
                message=str(exc),
            )
            raise

        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("reminders_due", duration_ms=duration_ms, ok=True)
        if notifications:
            log_event(
                "reminders_due",
                session_id=session_id,
                profile_user_id=profile_user_id,
                count=len(notifications),
                reminder_ids=[item.get("reminder_id") for item in notifications],
            )
        return JSONResponse({"notifications": notifications})

    @router.get("/reminders")
    async def reminders_list(request: Request):
        started_at = time.perf_counter()
        session_id, profile_user_id = resolve_identity_from_query(request)
        status = str(request.query_params.get("status") or "pending").strip().lower() or "pending"
        limit = max(1, int(request.query_params.get("limit") or 10))

        try:
            reminders = engine.store.list_reminders(
                profile_user_id=profile_user_id,
                session_id=session_id,
                status=status,
                limit=limit,
            )
        except Exception as exc:
            duration_ms = (time.perf_counter() - started_at) * 1000
            runtime_metrics.observe_request("reminders_list", duration_ms=duration_ms, ok=False)
            log_event(
                "reminders_list_error",
                session_id=session_id,
                profile_user_id=profile_user_id,
                message=str(exc),
            )
            raise

        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("reminders_list", duration_ms=duration_ms, ok=True)
        return JSONResponse({"reminders": reminders})

    @router.post("/reminders/cancel")
    async def reminders_cancel(request: Request):
        started_at = time.perf_counter()
        payload = await request.json()
        session_id, profile_user_id = resolve_identity_from_payload(payload)
        reminder_id = str(payload.get("reminder_id") or "").strip()
        if not reminder_id:
            duration_ms = (time.perf_counter() - started_at) * 1000
            runtime_metrics.observe_request("reminders_cancel", duration_ms=duration_ms, ok=False)
            raise HTTPException(status_code=400, detail="reminder_id is required")

        cancelled = engine.store.cancel_reminder(
            profile_user_id=profile_user_id,
            session_id=session_id,
            reminder_id=reminder_id,
        )
        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("reminders_cancel", duration_ms=duration_ms, ok=cancelled is not None)
        if cancelled is None:
            raise HTTPException(status_code=404, detail="pending reminder not found")
        log_event(
            "reminders_cancel",
            session_id=session_id,
            profile_user_id=profile_user_id,
            reminder_id=reminder_id,
        )
        return JSONResponse({"reminder": cancelled})

    return router
