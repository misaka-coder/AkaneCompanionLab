from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import JSONResponse

from ..desktop_satellite import DesktopSatelliteService


def build_satellite_router(
    *,
    satellite_service: DesktopSatelliteService,
    admin_auth: Any,
) -> APIRouter:
    router = APIRouter()

    @router.websocket("/capabilities/satellite/ws")
    async def satellite_websocket(websocket: WebSocket) -> None:
        await satellite_service.handle_websocket(websocket)

    @router.get("/capabilities/satellite/status")
    async def satellite_status(request: Request) -> JSONResponse:
        decision = admin_auth.authorize(request)
        if not decision.ok:
            return JSONResponse(
                {
                    "ok": False,
                    "status": "unauthorized",
                    "reason": decision.reason,
                },
                status_code=decision.status_code,
                headers={"Cache-Control": "no-store"},
            )
        return JSONResponse(satellite_service.diagnostics(), headers={"Cache-Control": "no-store"})

    return router
