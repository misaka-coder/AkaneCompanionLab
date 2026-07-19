"""Safe Host-level discovery for configured Bot runtimes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse


def build_bots_router(*, bot_registry: Any) -> APIRouter:
    router = APIRouter()

    @router.get("/api/bots")
    async def list_bots() -> JSONResponse:
        snapshot = bot_registry.public_snapshot()
        bots = [
            {
                "botId": str(item.get("bot_id") or ""),
                "displayName": str(item.get("display_name") or item.get("bot_id") or ""),
                "default": bool(item.get("default")),
                "available": str(item.get("status") or "") != "unavailable",
                "state": str(item.get("state") or "unavailable"),
                "status": str(item.get("status") or "unavailable"),
                "reason": str(item.get("reason") or ""),
            }
            for item in snapshot.get("bots", [])
            if isinstance(item, dict) and str(item.get("bot_id") or "")
        ]
        return JSONResponse(
            {
                "ok": True,
                "status": "available",
                "defaultBotId": str(snapshot.get("default_bot_id") or ""),
                "bots": bots,
            },
            headers={"Cache-Control": "no-store"},
        )

    return router


__all__ = ["build_bots_router"]
