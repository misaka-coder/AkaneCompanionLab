from __future__ import annotations

import importlib.util
import os
import sys
from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from ..desktop_pet_contract import (
    DESKTOP_PET_CONTRACT_VERSION,
    DESKTOP_PET_DEFAULT_EMOTION,
    DESKTOP_PET_DEFAULT_OUTFIT,
    build_desktop_pet_diagnostics_payload,
    build_desktop_pet_health_payload,
    decorate_resource_manifest_for_desktop_pet,
)


ResolveIdentity = Callable[[Request], tuple[str, str]]


def build_core_router(
    *,
    engine: Any,
    config_module: Any,
    resolve_identity_from_query: ResolveIdentity,
    runtime_metrics: Any = None,
    public_guard: Any = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    async def health() -> dict[str, object]:
        yt_dlp_available = importlib.util.find_spec("yt_dlp") is not None
        return {
            "status": "ok",
            "pid": os.getpid(),
            "python": sys.executable,
            "yt_dlp": yt_dlp_available,
            "contracts": {
                "desktop_pet": {
                    "version": DESKTOP_PET_CONTRACT_VERSION,
                    "health": "/desktop-pet/health",
                    "resource_manifest": "/resource-manifest",
                    "think": "/think",
                    "tts": "/tts",
                }
            },
        }

    @router.get("/desktop-pet/health")
    async def desktop_pet_health(request: Request) -> JSONResponse:
        session_id, profile_user_id = resolve_identity_from_query(request)
        return JSONResponse(
            build_desktop_pet_health_payload(
                profile_user_id=profile_user_id,
                session_id=session_id,
                streaming_tts_enabled=bool(getattr(config_module, "STREAMING_TTS_ENABLED", True)),
                yt_dlp_available=importlib.util.find_spec("yt_dlp") is not None,
            ),
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/desktop-pet/diagnostics")
    async def desktop_pet_diagnostics(request: Request) -> JSONResponse:
        session_id, profile_user_id = resolve_identity_from_query(request)
        character_pack_id = str(
            request.query_params.get("character_pack_id")
            or request.query_params.get("characterPackId")
            or ""
        )
        preferred_outfit = str(request.query_params.get("outfit") or "")
        preferred_emotion = str(request.query_params.get("emotion") or "")
        guard_snapshot: dict[str, Any] | None = None
        if public_guard is not None and hasattr(public_guard, "snapshot"):
            try:
                guard_snapshot = dict(public_guard.snapshot())
            except Exception:
                guard_snapshot = None
        return JSONResponse(
            build_desktop_pet_diagnostics_payload(
                engine=engine,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                preferred_outfit=preferred_outfit,
                preferred_emotion=preferred_emotion,
                runtime_metrics=runtime_metrics.snapshot() if hasattr(runtime_metrics, "snapshot") else None,
                public_guard_snapshot=guard_snapshot,
            ),
            headers={"Cache-Control": "no-store"},
        )

    @router.options("/{rest_of_path:path}", include_in_schema=False)
    async def options_preflight(rest_of_path: str) -> Response:
        return Response(status_code=204, headers={"Cache-Control": "no-store"})

    @router.get("/resource-manifest")
    async def resource_manifest(request: Request) -> JSONResponse:
        session_id, profile_user_id = resolve_identity_from_query(request)
        client_mode = str(
            request.query_params.get("client")
            or request.query_params.get("client_mode")
            or ""
        )
        character_pack_id = str(
            request.query_params.get("character_pack_id")
            or request.query_params.get("characterPackId")
            or ""
        )
        preferred_outfit = str(request.query_params.get("outfit") or "")
        preferred_emotion = str(request.query_params.get("emotion") or "")
        manifest = engine.build_resource_manifest(
            profile_user_id=profile_user_id,
            client_mode=client_mode,
            character_pack_id=character_pack_id,
        )
        return JSONResponse(
            decorate_resource_manifest_for_desktop_pet(
                manifest,
                profile_user_id=profile_user_id,
                session_id=session_id,
                preferred_outfit=preferred_outfit or DESKTOP_PET_DEFAULT_OUTFIT,
                preferred_emotion=preferred_emotion or DESKTOP_PET_DEFAULT_EMOTION,
            ),
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/app-config")
    async def app_config() -> JSONResponse:
        return JSONResponse(
            {
                "streaming_tts_enabled": bool(getattr(config_module, "STREAMING_TTS_ENABLED", True)),
                "web_identity_mode": str(getattr(config_module, "WEB_IDENTITY_MODE", "owner") or "owner"),
                "web_owner_profile_user_id": str(
                    getattr(config_module, "WEB_OWNER_PROFILE_USER_ID", "master") or "master"
                ),
                "desktop_pet_contract_version": DESKTOP_PET_CONTRACT_VERSION,
                "desktop_pet_health_url": "/desktop-pet/health",
            },
            headers={"Cache-Control": "no-store"},
        )

    return router
