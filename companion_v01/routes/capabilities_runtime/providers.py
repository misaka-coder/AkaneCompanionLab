"""Capability HTTP providers responsibilities."""
from __future__ import annotations
import asyncio
import time
from typing import Mapping
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from ...local_capability_config import (
    check_provider_health,
    inspect_gpt_sovits_voice_model_folder,
    list_provider_configs,
    list_voice_profile_configs,
    save_provider_config,
    save_voice_profile_config,
)
from .context import CapabilityRouteContext
from .common import _read_json_object, _resolve_identity, _observe_request, _log_best_effort
from .tts_probe import _run_provider_tts_test


def register_providers_routes(router: APIRouter, *, context: CapabilityRouteContext) -> None:
    engine = context.engine
    config_module = context.config_module
    settings = context.settings
    runtime_metrics = context.runtime_metrics
    log_event = context.log_event
    resolve_identity_from_query = context.resolve_identity_from_query
    provider_config_base_dir = context.provider_config_base_dir
    provider_health_checker = context.provider_health_checker
    provider_tts_test_runner = context.provider_tts_test_runner

    @router.get("/capabilities/providers")
    async def read_capability_providers(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        payload = list_provider_configs(
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
        )
        _observe_request(runtime_metrics, "capabilities.providers", started_at, True)
        _log_best_effort(
            log_event,
            "capabilities_providers",
            status=payload.get("status"),
            total=payload.get("summary", {}).get("total"),
        )
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @router.get("/capabilities/voice-profiles")
    async def read_capability_voice_profiles(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        payload = list_voice_profile_configs(
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
        )
        _observe_request(runtime_metrics, "capabilities.voice_profiles", started_at, True)
        _log_best_effort(
            log_event,
            "capabilities_voice_profiles",
            status=payload.get("status"),
            total=payload.get("summary", {}).get("total"),
        )
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @router.post("/capabilities/providers/{provider_id}/config")
    async def write_capability_provider_config(provider_id: str, request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        payload = await _read_json_object(request)
        result = save_provider_config(
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
            provider_id=provider_id,
            payload=payload,
        )
        _observe_request(runtime_metrics, "capabilities.provider_config", started_at, bool(result.get("ok")))
        _log_best_effort(
            log_event,
            "capabilities_provider_config",
            status=result.get("status"),
            providerId=result.get("providerId"),
        )
        status_code = 404 if result.get("status") == "unknown_provider" else 200
        return JSONResponse(result, status_code=status_code, headers={"Cache-Control": "no-store"})

    @router.post("/capabilities/providers/{provider_id}/health-check")
    async def check_capability_provider_health(provider_id: str, request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        payload = await _read_json_object(request)
        result = await asyncio.to_thread(
            check_provider_health,
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
            provider_id=provider_id,
            payload=payload,
            health_checker=provider_health_checker,
        )
        _observe_request(runtime_metrics, "capabilities.provider_health_check", started_at, result.get("status") == "ready")
        _log_best_effort(
            log_event,
            "capabilities_provider_health_check",
            status=result.get("status"),
            providerId=result.get("providerId"),
        )
        status_code = 404 if result.get("status") == "unknown_provider" else 200
        return JSONResponse(result, status_code=status_code, headers={"Cache-Control": "no-store"})

    @router.post("/capabilities/providers/{provider_id}/tts-test")
    async def test_capability_provider_tts(provider_id: str, request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        payload = await _read_json_object(request)
        result = await _run_provider_tts_test(
            engine=engine,
            session_id=_session_id,
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
            provider_id=provider_id,
            payload=payload,
            config_module=config_module,
            settings=settings,
            runner=provider_tts_test_runner,
        )
        ok = bool(result.get("ok"))
        _observe_request(runtime_metrics, "capabilities.provider_tts_test", started_at, ok)
        _log_best_effort(
            log_event,
            "capabilities_provider_tts_test",
            status=result.get("status"),
            providerId=result.get("providerId"),
            reason=result.get("reason"),
            audioBytes=result.get("audioBytes"),
        )
        status_code = 404 if result.get("status") == "unknown_provider" else 200
        return JSONResponse(result, status_code=status_code, headers={"Cache-Control": "no-store"})

    @router.post("/capabilities/providers/{provider_id}/voice-profiles/inspect-folder")
    async def inspect_capability_provider_voice_profile_folder(provider_id: str, request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, _profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        payload = await _read_json_object(request)
        result = await asyncio.to_thread(
            inspect_gpt_sovits_voice_model_folder,
            provider_id=provider_id,
            payload=payload,
        )
        ok = bool(result.get("ok"))
        _observe_request(runtime_metrics, "capabilities.voice_profile_folder_inspect", started_at, ok)
        _log_best_effort(
            log_event,
            "capabilities_voice_profile_folder_inspect",
            status=result.get("status"),
            providerId=provider_id,
            voiceProfileId=result.get("suggestedProfile", {}).get("voiceProfileId")
            if isinstance(result.get("suggestedProfile"), Mapping)
            else "",
            reason=result.get("reason"),
        )
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @router.post("/capabilities/providers/{provider_id}/voice-profiles/{voice_profile_id}/config")
    async def write_capability_provider_voice_profile(
        provider_id: str,
        voice_profile_id: str,
        request: Request,
    ) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        payload = await _read_json_object(request)
        payload = {**payload, "providerId": provider_id}
        result = save_voice_profile_config(
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
            voice_profile_id=voice_profile_id,
            payload=payload,
        )
        ok = bool(result.get("ok"))
        _observe_request(runtime_metrics, "capabilities.voice_profile_config", started_at, ok)
        _log_best_effort(
            log_event,
            "capabilities_voice_profile_config",
            status=result.get("status"),
            providerId=provider_id,
            voiceProfileId=result.get("voiceProfileId"),
        )
        status_code = 404 if result.get("status") == "unknown_provider" else 200
        return JSONResponse(result, status_code=status_code, headers={"Cache-Control": "no-store"})
