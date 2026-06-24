from __future__ import annotations

import asyncio
import base64
import copy
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from ..capability_approval import CapabilityApprovalStore
from ..local_capability_config import (
    check_provider_health,
    get_approval_policy_config,
    get_mcp_server_runtime_config,
    get_voice_profile_runtime_config,
    inspect_gpt_sovits_voice_model_folder,
    list_mcp_server_configs,
    list_provider_configs,
    list_voice_profile_configs,
    load_capability_config,
    preflight_workflow_execution,
    save_workflow_file,
    save_workflow_config,
    save_provider_config,
    save_mcp_server_config,
    save_mcp_server_discovery,
    save_approval_policy_config,
    save_voice_profile_config,
    validate_workflow_config,
    validate_workflow_runtime_binding,
)
from ..local_workflow_execution import (
    WorkflowExecutionAsset,
    WorkflowExecutionRequest,
    call_workflow_execution_runner,
    normalize_workflow_asset,
    normalize_workflow_asset_handle,
)
from ..music_lyrics import LyricsSearchFunc, OnlineLyricsService
from services.tts_client import GptSovitsTTSClient, SynthesizedAudio
from ..local_capability_catalog import (
    build_local_capability_catalog,
    build_local_workflow_catalog,
    probe_known_local_services,
)


LogEvent = Callable[..., None]
ProviderHealthChecker = Callable[[str, int, float], tuple[bool, str]]
ProviderTtsTestRunner = Callable[..., Any]
McpToolDiscoverer = Callable[..., Any]
WORKFLOW_JOB_ID_RE = re.compile(r"^workflowjob_[a-f0-9]{32}$")
GPT_SOVITS_PROVIDER_ID = "provider.tts.gpt_sovits.local"
PROVIDER_TTS_TEST_DEFAULT_TEXT = "你好，主人，本地语音服务已经接通。"
PROVIDER_TTS_TEST_TEXT_MAX_CHARS = 120
PROVIDER_TTS_TEST_AUDIO_MAX_BYTES = 2 * 1024 * 1024


def build_capabilities_router(
    *,
    engine: Any,
    config_module: Any = None,
    tts_client: Any = None,
    runtime_metrics: Any = None,
    log_event: LogEvent | None = None,
    resolve_identity_from_query: Callable[[Request], tuple[str, str]] | None = None,
    local_environment_probe: Callable[[], dict[str, Any]] | None = None,
    capability_config_base_dir: str | Path | None = None,
    provider_health_checker: ProviderHealthChecker | None = None,
    provider_tts_test_runner: ProviderTtsTestRunner | None = None,
    mcp_tool_discoverer: McpToolDiscoverer | None = None,
    workflow_runner: Any = None,
    background_tasks: Any = None,
    lyrics_searcher: LyricsSearchFunc | None = None,
) -> APIRouter:
    router = APIRouter()
    workflow_jobs: dict[str, dict[str, Any]] = {}
    workflow_jobs_lock = threading.RLock()
    approval_store = CapabilityApprovalStore()
    provider_config_base_dir = _resolve_provider_config_base_dir(
        capability_config_base_dir=capability_config_base_dir,
        config_module=config_module,
    )
    lyrics_service = OnlineLyricsService(
        base_dir=provider_config_base_dir,
        config_module=config_module,
        search_func=lyrics_searcher,
    )

    @router.get("/capabilities")
    async def read_capabilities(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        character_voice = _resolve_character_voice_preference(engine, request)
        provider_config = load_capability_config(
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
        )
        payload = build_local_capability_catalog(
            engine=engine,
            config_module=config_module,
            tts_client=tts_client,
            profile_user_id=profile_user_id,
            provider_configs=provider_config.get("providers", {}),
            workflow_configs=provider_config.get("workflows", {}),
            mcp_server_configs=provider_config.get("mcpServers", {}),
            approval_policy=provider_config.get("approvalPolicy", {}),
            character_voice=character_voice,
        )
        _mark_workflows_execution_ready(
            payload,
            workflow_runner=workflow_runner,
            background_tasks=background_tasks,
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
        )
        payload["providerConfigStatus"] = provider_config.get("configStatus") or "available"
        payload["providerConfigWarnings"] = list(provider_config.get("warnings") or [])
        payload["approvalPolicy"] = get_approval_policy_config(
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
        )["approvalPolicy"]
        _observe_request(runtime_metrics, "capabilities.catalog", started_at, True)
        _log_best_effort(
            log_event,
            "capabilities_catalog",
            status=payload.get("status"),
            total=payload.get("summary", {}).get("total"),
        )
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @router.get("/capabilities/approval-policy")
    async def read_capability_approval_policy(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        payload = get_approval_policy_config(
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
        )
        _observe_request(runtime_metrics, "capabilities.approval_policy", started_at, True)
        _log_best_effort(
            log_event,
            "capabilities_approval_policy",
            status=payload.get("status"),
            defaultMode=payload.get("approvalPolicy", {}).get("defaultMode"),
        )
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @router.post("/capabilities/approval-policy")
    async def write_capability_approval_policy(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        payload = await _read_json_object(request)
        result = save_approval_policy_config(
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
            payload=payload,
        )
        ok = bool(result.get("ok"))
        _observe_request(runtime_metrics, "capabilities.approval_policy_save", started_at, ok)
        _log_best_effort(
            log_event,
            "capabilities_approval_policy_save",
            status=result.get("status"),
            reason=result.get("reason"),
            defaultMode=result.get("approvalPolicy", {}).get("defaultMode"),
        )
        status_code = 400 if result.get("status") == "invalid_config" else 200
        return JSONResponse(result, status_code=status_code, headers={"Cache-Control": "no-store"})

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
        from ..music_context import summarize_listening_together
        from .. import music_control_store

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
                recent_limit=int(payload.get("recent_limit") or payload.get("recentLimit") or 5),
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
        from .. import music_control_store

        store = getattr(engine, "store", None)
        if store is None:
            _observe_request(runtime_metrics, "capabilities.music_control_permissions_get", started_at, False)
            return JSONResponse(
                {"ok": False, "status": "store_unavailable",
                 "controls": {c: True for c in ("pause", "next", "prev", "recommend")}},
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )
        if not callable(getattr(store, "_connect", None)):
            _observe_request(runtime_metrics, "capabilities.music_control_permissions_get", started_at, False)
            return JSONResponse(
                {"ok": False, "status": "store_unavailable", "reason": "music_control_store_unavailable",
                 "controls": {c: True for c in ("pause", "next", "prev", "recommend")}},
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )

        def _get_controls():
            connect_fn = getattr(store, "_connect", None)
            with connect_fn() as conn:
                music_control_store.ensure_schema(conn)
                enabled = music_control_store.get_enabled_controls(conn, profile_user_id=profile_user_id)
            return {c: (c in enabled) for c in ("pause", "next", "prev", "recommend")}

        try:
            controls = await asyncio.to_thread(_get_controls)
        except Exception as exc:
            _observe_request(runtime_metrics, "capabilities.music_control_permissions_get", started_at, False)
            return JSONResponse(
                {"ok": False, "status": "query_failed", "reason": str(exc)[:200],
                 "controls": {c: True for c in ("pause", "next", "prev", "recommend")}},
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
        from .. import music_control_store

        store = getattr(engine, "store", None)
        if store is None:
            _observe_request(runtime_metrics, "capabilities.music_control_permissions_set", started_at, False)
            return JSONResponse(
                {"ok": False, "status": "store_unavailable",
                 "controls": {c: True for c in ("pause", "next", "prev", "recommend")}},
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )
        if not callable(getattr(store, "_connect", None)):
            _observe_request(runtime_metrics, "capabilities.music_control_permissions_set", started_at, False)
            return JSONResponse(
                {"ok": False, "status": "store_unavailable", "reason": "music_control_store_unavailable",
                 "controls": {c: True for c in ("pause", "next", "prev", "recommend")}},
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )

        raw_controls = payload.get("controls")
        coerced = {k: bool(v) for k, v in raw_controls.items()} if isinstance(raw_controls, dict) else {}

        def _set_and_get() -> dict:
            import time as _time
            connect_fn = getattr(store, "_connect", None)
            with connect_fn() as conn:
                music_control_store.ensure_schema(conn)
                music_control_store.bulk_set_controls(
                    conn,
                    profile_user_id=profile_user_id,
                    controls=coerced,
                    now_ts=int(_time.time()),
                )
                enabled = music_control_store.get_enabled_controls(conn, profile_user_id=profile_user_id)
            return {c: (c in enabled) for c in ("pause", "next", "prev", "recommend")}

        try:
            controls = await asyncio.to_thread(_set_and_get)
        except Exception as exc:
            _observe_request(runtime_metrics, "capabilities.music_control_permissions_set", started_at, False)
            return JSONResponse(
                {"ok": False, "status": "update_failed", "reason": str(exc)[:200],
                 "controls": {c: True for c in ("pause", "next", "prev", "recommend")}},
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )

        _observe_request(runtime_metrics, "capabilities.music_control_permissions_set", started_at, True)
        return JSONResponse(
            {"ok": True, "status": "ready", "controls": controls},
            headers={"Cache-Control": "no-store"},
        )

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

    @router.get("/capabilities/mcp-servers")
    async def read_capability_mcp_servers(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        payload = list_mcp_server_configs(
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
        )
        _observe_request(runtime_metrics, "capabilities.mcp_servers", started_at, True)
        _log_best_effort(
            log_event,
            "capabilities_mcp_servers",
            status=payload.get("status"),
            total=payload.get("summary", {}).get("total"),
        )
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @router.post("/capabilities/mcp-servers/{server_id}/config")
    async def write_capability_mcp_server_config(server_id: str, request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        payload = await _read_json_object(request)
        result = save_mcp_server_config(
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
            server_id=server_id,
            payload=payload,
        )
        _observe_request(runtime_metrics, "capabilities.mcp_server_config", started_at, bool(result.get("ok")))
        _log_best_effort(
            log_event,
            "capabilities_mcp_server_config",
            status=result.get("status"),
            serverId=result.get("serverId"),
        )
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @router.post("/capabilities/mcp-servers/{server_id}/discover")
    async def discover_capability_mcp_server_tools(server_id: str, request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        result = await _discover_mcp_server_tools(
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
            server_id=server_id,
            discoverer=mcp_tool_discoverer,
        )
        _observe_request(runtime_metrics, "capabilities.mcp_server_discover", started_at, bool(result.get("ok")))
        _log_best_effort(
            log_event,
            "capabilities_mcp_server_discover",
            status=result.get("status"),
            serverId=result.get("serverId"),
            toolCount=result.get("toolCount"),
            reason=result.get("reason"),
        )
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @router.get("/capabilities/approval-requests")
    async def read_capability_approval_requests(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        include_resolved = _safe_bool_query(request.query_params.get("include_resolved"))
        limit = _safe_positive_int(request.query_params.get("limit"), default=20, maximum=50)
        payload = approval_store.list_requests(
            profile_user_id=profile_user_id,
            include_resolved=include_resolved,
            limit=limit,
        )
        _observe_request(runtime_metrics, "capabilities.approval_requests", started_at, True)
        _log_best_effort(
            log_event,
            "capabilities_approval_requests",
            status=payload.get("status"),
            pendingCount=payload.get("pendingCount"),
        )
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @router.post("/capabilities/approval-requests")
    async def create_capability_approval_request(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        payload = await _read_json_object(request)
        result = approval_store.create_request(
            profile_user_id=profile_user_id,
            session_id=session_id,
            payload=payload,
        )
        ok = bool(result.get("ok"))
        _observe_request(runtime_metrics, "capabilities.approval_request_create", started_at, ok)
        _log_best_effort(
            log_event,
            "capabilities_approval_request_create",
            status=result.get("status"),
            requestId=result.get("requestId"),
            reason=result.get("reason"),
        )
        status_code = 400 if result.get("status") in {"invalid_request", "not_required", "disabled"} else 200
        return JSONResponse(result, status_code=status_code, headers={"Cache-Control": "no-store"})

    @router.get("/capabilities/approval-requests/{request_id}")
    async def read_capability_approval_request(request_id: str, request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        result = approval_store.get_request(profile_user_id=profile_user_id, request_id=request_id)
        _observe_request(runtime_metrics, "capabilities.approval_request_read", started_at, bool(result.get("ok")))
        _log_best_effort(
            log_event,
            "capabilities_approval_request_read",
            status=result.get("status"),
            reason=result.get("reason"),
        )
        status_code = 404 if result.get("status") == "not_found" else 400 if result.get("status") == "invalid_request" else 200
        return JSONResponse(result, status_code=status_code, headers={"Cache-Control": "no-store"})

    @router.post("/capabilities/approval-requests/{request_id}/decision")
    async def decide_capability_approval_request(request_id: str, request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        payload = await _read_json_object(request)
        result = approval_store.decide_request(
            profile_user_id=profile_user_id,
            request_id=request_id,
            payload=payload,
        )
        ok = bool(result.get("ok"))
        _observe_request(runtime_metrics, "capabilities.approval_request_decision", started_at, ok)
        _log_best_effort(
            log_event,
            "capabilities_approval_request_decision",
            status=result.get("status"),
            requestId=result.get("requestId"),
            reason=result.get("reason"),
        )
        if result.get("status") == "not_found":
            status_code = 404
        elif result.get("status") == "invalid_request":
            status_code = 400
        elif result.get("reason") == "approval_request_already_resolved":
            status_code = 409
        else:
            status_code = 200
        return JSONResponse(result, status_code=status_code, headers={"Cache-Control": "no-store"})

    @router.get("/capabilities/workflows")
    async def read_capability_workflows(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        provider_config = load_capability_config(
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
        )
        payload = build_local_workflow_catalog(
            profile_user_id=profile_user_id,
            provider_configs=provider_config.get("providers", {}),
            workflow_configs=provider_config.get("workflows", {}),
            approval_policy=provider_config.get("approvalPolicy", {}),
        )
        _mark_workflows_execution_ready(
            payload,
            workflow_runner=workflow_runner,
            background_tasks=background_tasks,
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
        )
        payload["providerConfigStatus"] = provider_config.get("configStatus") or "available"
        payload["providerConfigWarnings"] = list(provider_config.get("warnings") or [])
        _observe_request(runtime_metrics, "capabilities.workflows", started_at, True)
        _log_best_effort(
            log_event,
            "capabilities_workflows",
            status=payload.get("status"),
            total=payload.get("summary", {}).get("total"),
        )
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @router.post("/capabilities/workflows/{workflow_id}/config")
    async def write_capability_workflow_config(workflow_id: str, request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        payload = await _read_json_object(request)
        result = save_workflow_config(
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
            workflow_id=workflow_id,
            payload=payload,
        )
        _observe_request(runtime_metrics, "capabilities.workflow_config", started_at, bool(result.get("ok")))
        _log_best_effort(
            log_event,
            "capabilities_workflow_config",
            status=result.get("status"),
            workflowId=result.get("workflowId"),
        )
        status_code = 404 if result.get("status") == "unknown_workflow" else 200
        return JSONResponse(result, status_code=status_code, headers={"Cache-Control": "no-store"})

    @router.post("/capabilities/workflows/{workflow_id}/validate")
    async def validate_capability_workflow_config(workflow_id: str, request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        result = validate_workflow_config(
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
            workflow_id=workflow_id,
        )
        _observe_request(runtime_metrics, "capabilities.workflow_validate", started_at, bool(result.get("ok")))
        _log_best_effort(
            log_event,
            "capabilities_workflow_validate",
            status=result.get("status"),
            workflowId=result.get("workflowId"),
        )
        status_code = 404 if result.get("status") == "unknown_workflow" else 200
        return JSONResponse(result, status_code=status_code, headers={"Cache-Control": "no-store"})

    @router.post("/capabilities/workflows/{workflow_id}/file")
    async def import_capability_workflow_file(workflow_id: str, request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        payload = await _read_json_object(request)
        result = save_workflow_file(
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
            workflow_id=workflow_id,
            payload=payload,
        )
        _observe_request(runtime_metrics, "capabilities.workflow_file", started_at, bool(result.get("ok")))
        _log_best_effort(
            log_event,
            "capabilities_workflow_file",
            status=result.get("status"),
            workflowId=result.get("workflowId"),
        )
        status_code = 404 if result.get("status") == "unknown_workflow" else 200
        return JSONResponse(result, status_code=status_code, headers={"Cache-Control": "no-store"})

    @router.post("/capabilities/workflows/{workflow_id}/preflight")
    async def preflight_capability_workflow_execution(workflow_id: str, request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        payload = await _read_json_object(request)
        result = preflight_workflow_execution(
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
            workflow_id=workflow_id,
            payload=payload,
        )
        result = _with_bound_workflow_runner(
            result,
            workflow_runner=workflow_runner,
            background_tasks=background_tasks,
        )
        _observe_request(runtime_metrics, "capabilities.workflow_preflight", started_at, bool(result.get("ok")))
        _log_best_effort(
            log_event,
            "capabilities_workflow_preflight",
            status=result.get("status"),
            workflowId=result.get("workflowId"),
        )
        status_code = 404 if result.get("status") == "unknown_workflow" else 200
        return JSONResponse(result, status_code=status_code, headers={"Cache-Control": "no-store"})

    @router.post("/capabilities/workflows/{workflow_id}/jobs")
    async def start_capability_workflow_job(workflow_id: str, request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        payload = await _read_json_object(request)
        result = preflight_workflow_execution(
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
            workflow_id=workflow_id,
            payload=payload,
        )
        result = _with_bound_workflow_runner(
            result,
            workflow_runner=workflow_runner,
            background_tasks=background_tasks,
        )
        if result.get("ok") and result.get("status") == "ready":
            result = _start_bound_workflow_job(
                preflight=result,
                profile_user_id=profile_user_id,
                session_id=session_id,
                workflow_runner=workflow_runner,
                background_tasks=background_tasks,
                payload=payload,
                workflow_jobs=workflow_jobs,
                workflow_jobs_lock=workflow_jobs_lock,
            )
        elif result.get("status") == "not-implemented" and result.get("reason") == "workflow_runner_not_bound":
            job = _build_inert_workflow_job(
                preflight=result,
                profile_user_id=profile_user_id,
                session_id=session_id,
            )
            with workflow_jobs_lock:
                workflow_jobs[job["jobId"]] = job
            public_job = _public_workflow_job(job)
            result = {
                **result,
                "jobId": job["jobId"],
                "jobStatus": job["status"],
                "job": public_job,
            }
        _observe_request(runtime_metrics, "capabilities.workflow_job_start", started_at, bool(result.get("ok")))
        _log_best_effort(
            log_event,
            "capabilities_workflow_job_start",
            status=result.get("status"),
            workflowId=result.get("workflowId"),
            jobStatus=result.get("jobStatus"),
        )
        status_code = 404 if result.get("status") == "unknown_workflow" else 200
        return JSONResponse(result, status_code=status_code, headers={"Cache-Control": "no-store"})

    @router.get("/capabilities/workflow-jobs/{job_id}")
    async def read_capability_workflow_job(job_id: str, request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        safe_job_id = _safe_workflow_job_id(job_id)
        job: dict[str, Any] | None = None
        if safe_job_id:
            with workflow_jobs_lock:
                stored = workflow_jobs.get(safe_job_id)
                job = copy.deepcopy(stored) if stored else None
        if job is None or job.get("_profileUserId") != profile_user_id:
            result = {
                "ok": False,
                "status": "unknown_workflow_job",
                "reason": "workflow_job_not_found",
                "executionReady": False,
                "canRun": False,
            }
            _observe_request(runtime_metrics, "capabilities.workflow_job_status", started_at, False)
            _log_best_effort(log_event, "capabilities_workflow_job_status", status=result["status"])
            return JSONResponse(result, status_code=404, headers={"Cache-Control": "no-store"})

        public_job = _public_workflow_job(job)
        result = {
            "ok": True,
            "status": job["status"],
            "reason": job["reason"],
            "jobId": job["jobId"],
            "workflowId": job["workflowId"],
            "capabilityId": job["capabilityId"],
            "executionReady": False,
            "canRun": False,
            "job": public_job,
        }
        _observe_request(runtime_metrics, "capabilities.workflow_job_status", started_at, True)
        _log_best_effort(
            log_event,
            "capabilities_workflow_job_status",
            status=result.get("status"),
            workflowId=result.get("workflowId"),
            jobStatus=job.get("status"),
        )
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @router.get("/capabilities/workflow-jobs/{job_id}/outputs/{output_handle}")
    async def read_capability_workflow_job_output(job_id: str, output_handle: str, request: Request) -> Response:
        started_at = time.perf_counter()
        _session_id, profile_user_id = _resolve_identity(request, resolve_identity_from_query)
        safe_job_id = _safe_workflow_job_id(job_id)
        safe_output_handle = normalize_workflow_asset_handle(output_handle)
        job: dict[str, Any] | None = None
        if safe_job_id and safe_output_handle.get("ok"):
            with workflow_jobs_lock:
                stored = workflow_jobs.get(safe_job_id)
                job = copy.deepcopy(stored) if stored else None
        if job is None or job.get("_profileUserId") != profile_user_id:
            _observe_request(runtime_metrics, "capabilities.workflow_job_output", started_at, False)
            return JSONResponse(
                {
                    "ok": False,
                    "status": "unknown_workflow_job",
                    "reason": "workflow_job_not_found",
                },
                status_code=404,
                headers={"Cache-Control": "no-store"},
            )
        if str(job.get("status") or "") != "completed":
            _observe_request(runtime_metrics, "capabilities.workflow_job_output", started_at, False)
            return JSONResponse(
                {
                    "ok": False,
                    "status": "not-ready",
                    "reason": "workflow_job_output_not_ready",
                },
                status_code=409,
                headers={"Cache-Control": "no-store"},
            )
        output_asset = _find_workflow_job_output_asset(job, str(safe_output_handle.get("handle") or ""))
        if output_asset is None:
            _observe_request(runtime_metrics, "capabilities.workflow_job_output", started_at, False)
            return JSONResponse(
                {
                    "ok": False,
                    "status": "unknown_workflow_output",
                    "reason": "workflow_output_not_found",
                },
                status_code=404,
                headers={"Cache-Control": "no-store"},
            )
        _observe_request(runtime_metrics, "capabilities.workflow_job_output", started_at, True)
        return Response(
            content=output_asset.data,
            media_type=output_asset.content_type or "application/octet-stream",
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

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
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
            provider_id=provider_id,
            payload=payload,
            config_module=config_module,
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

    @router.post("/capabilities/local-environment-check")
    async def check_local_environment() -> JSONResponse:
        started_at = time.perf_counter()
        try:
            if local_environment_probe is not None:
                payload = await _maybe_thread(local_environment_probe)
            else:
                payload = await asyncio.to_thread(probe_known_local_services)
            _observe_request(runtime_metrics, "capabilities.local_environment_check", started_at, True)
            _log_best_effort(
                log_event,
                "capabilities_local_environment_check",
                status=payload.get("status"),
                total=payload.get("summary", {}).get("total"),
            )
            return JSONResponse(payload, headers={"Cache-Control": "no-store"})
        except Exception as exc:
            _observe_request(runtime_metrics, "capabilities.local_environment_check", started_at, False)
            _log_best_effort(
                log_event,
                "capabilities_local_environment_check",
                status="error",
                error=str(exc)[:160],
            )
            return JSONResponse(
                {
                    "ok": False,
                    "status": "error",
                    "reason": str(exc)[:200] or "local_environment_check_failed",
                    "autoEnable": False,
                    "services": [],
                },
                headers={"Cache-Control": "no-store"},
            )

    @router.post("/capabilities/adapter-registry/reload")
    async def reload_capability_adapter_registry(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        payload = await _read_json_object(request)
        provider_id = _safe_adapter_registry_provider_id(payload.get("providerId") or payload.get("provider_id"))
        registry = getattr(engine, "capability_adapter_registry", None)
        reload_fn = getattr(registry, "reload", None)
        if not callable(reload_fn):
            _observe_request(runtime_metrics, "capabilities.adapter_registry_reload", started_at, False)
            return JSONResponse(
                {
                    "ok": False,
                    "status": "not_available",
                    "reason": "capability_adapter_registry_unavailable",
                    "refresh": False,
                },
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )
        try:
            await asyncio.to_thread(reload_fn, provider_id)
            manifests = list(getattr(registry, "list_manifests")())
            invalid = list(getattr(registry, "list_invalid")())
        except Exception:
            _observe_request(runtime_metrics, "capabilities.adapter_registry_reload", started_at, False)
            return JSONResponse(
                {
                    "ok": False,
                    "status": "reload_failed",
                    "reason": "capability_adapter_registry_reload_failed",
                    "refresh": False,
                },
                status_code=500,
                headers={"Cache-Control": "no-store"},
            )
        result = {
            "ok": True,
            "status": "reloaded",
            "providerId": provider_id,
            "validCount": len(manifests),
            "invalidCount": len(invalid),
            "providers": sorted(str(item.provider_id or "") for item in manifests if str(item.provider_id or "")),
            "invalid": [_safe_invalid_manifest_summary(item) for item in invalid],
            "refresh": True,
        }
        _observe_request(runtime_metrics, "capabilities.adapter_registry_reload", started_at, True)
        _log_best_effort(
            log_event,
            "capabilities_adapter_registry_reload",
            providerId=provider_id,
            validCount=result["validCount"],
            invalidCount=result["invalidCount"],
        )
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    return router


async def _discover_mcp_server_tools(
    *,
    base_dir: Path | None,
    profile_user_id: str,
    server_id: str,
    discoverer: McpToolDiscoverer | None,
) -> dict[str, Any]:
    server = get_mcp_server_runtime_config(
        base_dir=base_dir,
        profile_user_id=profile_user_id,
        server_id=server_id,
    )
    safe_server_id = str(server.get("serverId") or server_id or "").strip()
    if not server:
        return {
            "ok": False,
            "status": "missing_config",
            "serverId": safe_server_id,
            "reason": "mcp_server_config_missing",
            "refresh": False,
        }
    if not server.get("enabled"):
        return {
            "ok": False,
            "status": "disabled",
            "serverId": safe_server_id,
            "reason": "mcp_server_disabled",
            "refresh": False,
        }
    if discoverer is None:
        return {
            "ok": False,
            "status": "not-implemented",
            "serverId": safe_server_id,
            "reason": "mcp_discoverer_not_bound",
            "refresh": False,
        }
    try:
        discovered = discoverer(server=server)
        if hasattr(discovered, "__await__"):
            discovered = await discovered
    except Exception:
        return {
            "ok": False,
            "status": "discovery-failed",
            "serverId": safe_server_id,
            "reason": "mcp_discovery_failed",
            "refresh": False,
        }
    payload = discovered if isinstance(discovered, Mapping) else {"tools": discovered if isinstance(discovered, list) else []}
    return save_mcp_server_discovery(
        base_dir=base_dir,
        profile_user_id=profile_user_id,
        server_id=safe_server_id,
        payload=payload,
    )


def _safe_adapter_registry_provider_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", text):
        return ""
    return text


def _safe_invalid_manifest_summary(item: Any) -> dict[str, str]:
    source_name = ""
    source_path = getattr(item, "source_path", None)
    if source_path is not None:
        source_name = Path(str(source_path)).name[:120]
    return {
        "source": source_name,
        "sourceLayer": str(getattr(item, "source_layer", "") or "")[:40],
        "providerId": _safe_adapter_registry_provider_id(getattr(item, "provider_id", "")),
        "reason": _safe_adapter_registry_text(getattr(item, "reason", ""), limit=120),
        "detail": _safe_adapter_registry_text(getattr(item, "detail", ""), limit=160),
    }


def _safe_adapter_registry_text(value: Any, *, limit: int) -> str:
    text = str(value or "").strip()
    text = re.sub(r"(?i)(token|secret|password|api[_-]?key)=([^\s&]+)", r"\1=redacted", text)
    text = re.sub(r"[A-Za-z]:[\\/][^\s]+", "[local_path]", text)
    return text[:limit]


async def _run_provider_tts_test(
    *,
    base_dir: Path | None,
    profile_user_id: str,
    provider_id: str,
    payload: dict[str, Any],
    config_module: Any = None,
    runner: ProviderTtsTestRunner | None = None,
) -> dict[str, Any]:
    provider_id = str(provider_id or "").strip()
    if provider_id != GPT_SOVITS_PROVIDER_ID:
        return {
            "ok": False,
            "status": "unsupported_provider",
            "providerId": provider_id,
            "reason": "provider_tts_test_not_supported",
            "refresh": False,
        }

    endpoint_result = _resolve_provider_tts_test_endpoint(
        base_dir=base_dir,
        profile_user_id=profile_user_id,
        payload=payload,
    )
    if not endpoint_result.get("ok"):
        return {
            "ok": False,
            "status": endpoint_result.get("status") or "missing_config",
            "providerId": provider_id,
            "reason": endpoint_result.get("reason") or "provider_endpoint_missing",
            "refresh": False,
        }

    text = _safe_provider_tts_test_text(payload.get("text"))
    voice_profile_id = _safe_provider_tts_profile_id(
        payload.get("voiceProfileId") or payload.get("voice_profile_id") or payload.get("profileId")
    )
    voice_profile = _provider_tts_test_profile_payload(payload)
    profile_source = "payload" if voice_profile else "none"
    if voice_profile_id:
        saved_profile = get_voice_profile_runtime_config(
            base_dir=base_dir,
            profile_user_id=profile_user_id,
            voice_profile_id=voice_profile_id,
        )
        if saved_profile.get("providerId") == GPT_SOVITS_PROVIDER_ID:
            voice_profile = {
                **saved_profile,
                **voice_profile,
            }
            profile_source = "saved+payload" if profile_source == "payload" else "saved"
        elif profile_source == "none":
            profile_source = "missing"
    endpoint = str(endpoint_result.get("endpoint") or "")
    profile_checks = _provider_tts_test_profile_checks(
        endpoint=endpoint,
        voice_profile_id=voice_profile_id,
        voice_profile=voice_profile,
        profile_source=profile_source,
    )
    try:
        if runner is not None:
            result = runner(endpoint=endpoint, text=text, voice_profile_id=voice_profile_id)
            if hasattr(result, "__await__"):
                result = await result
        else:
            timeout_seconds = float(getattr(config_module, "GPT_SOVITS_TTS_TIMEOUT_SECONDS", 45.0) or 45.0)
            text_lang = str(getattr(config_module, "GPT_SOVITS_TEXT_LANG", "zh") or "zh")
            media_type = str(getattr(config_module, "GPT_SOVITS_MEDIA_TYPE", "wav") or "wav")
            client = GptSovitsTTSClient(
                endpoint,
                timeout_seconds=timeout_seconds,
                text_lang=text_lang,
                media_type=media_type,
                streaming_mode=bool(getattr(config_module, "GPT_SOVITS_STREAMING_MODE", False)),
                parallel_infer=getattr(config_module, "GPT_SOVITS_PARALLEL_INFER", None),
                split_bucket=getattr(config_module, "GPT_SOVITS_SPLIT_BUCKET", None),
                batch_size=getattr(config_module, "GPT_SOVITS_BATCH_SIZE", None),
                speed_factor=getattr(config_module, "GPT_SOVITS_SPEED_FACTOR", None),
                fragment_interval=getattr(config_module, "GPT_SOVITS_FRAGMENT_INTERVAL", None),
                text_split_method=str(getattr(config_module, "GPT_SOVITS_TEXT_SPLIT_METHOD", "") or ""),
            )
            result = await client.synthesize(text, voice_profile_id=voice_profile_id, profile=voice_profile)
        audio, media_type = _coerce_provider_tts_test_audio(result)
    except ValueError:
        return {
            "ok": False,
            "status": "invalid_config",
            "providerId": provider_id,
            "reason": "provider_tts_test_invalid_config",
            "voiceProfileId": voice_profile_id,
            "profileApplied": bool(voice_profile),
            "profileSource": profile_source,
            "checks": profile_checks,
            "refresh": False,
        }
    except RuntimeError as exc:
        reason = str(exc) if str(exc) == "provider_tts_test_empty_audio" else "provider_tts_test_failed"
        return {
            "ok": False,
            "status": "tts-test-failed",
            "providerId": provider_id,
            "reason": reason,
            "voiceProfileId": voice_profile_id,
            "profileApplied": bool(voice_profile),
            "profileSource": profile_source,
            "checks": profile_checks,
            "refresh": False,
        }
    except Exception:
        return {
            "ok": False,
            "status": "tts-test-failed",
            "providerId": provider_id,
            "reason": "provider_tts_test_failed",
            "voiceProfileId": voice_profile_id,
            "profileApplied": bool(voice_profile),
            "profileSource": profile_source,
            "checks": profile_checks,
            "refresh": False,
        }

    if len(audio) > PROVIDER_TTS_TEST_AUDIO_MAX_BYTES:
        return {
            "ok": False,
            "status": "tts-test-too-large",
            "providerId": provider_id,
            "reason": "provider_tts_test_audio_too_large",
            "refresh": False,
        }
    return {
        "ok": True,
        "status": "tts-test-ready",
        "providerId": provider_id,
        "mediaType": media_type,
        "audioBase64": base64.b64encode(audio).decode("ascii"),
        "audioBytes": len(audio),
        "textLength": len(text),
        "voiceProfileId": voice_profile_id,
        "profileApplied": bool(voice_profile),
        "profileSource": profile_source,
        "checks": profile_checks,
        "refresh": False,
    }


def _resolve_provider_tts_test_endpoint(
    *,
    base_dir: Path | None,
    profile_user_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    endpoint_value = str(payload.get("endpoint") or "").strip()
    if endpoint_value:
        from ..local_capability_config import normalize_local_http_endpoint

        normalized = normalize_local_http_endpoint(endpoint_value)
        if not normalized.get("ok"):
            return normalized
        return {"ok": True, "status": "valid", "endpoint": normalized["endpoint"]}

    config = load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
    saved = config.get("providers", {}).get(GPT_SOVITS_PROVIDER_ID)
    endpoint = str((saved or {}).get("endpoint") or "").strip() if isinstance(saved, dict) else ""
    if not endpoint:
        return {"ok": False, "status": "missing_config", "reason": "provider_endpoint_missing"}
    return {"ok": True, "status": "valid", "endpoint": endpoint}


def _safe_provider_tts_test_text(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return PROVIDER_TTS_TEST_DEFAULT_TEXT
    return text[:PROVIDER_TTS_TEST_TEXT_MAX_CHARS]


def _provider_tts_test_profile_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    profile: dict[str, Any] = {}
    text_lang = _safe_provider_tts_profile_id(payload.get("textLang") or payload.get("text_lang"))
    prompt_lang = _safe_provider_tts_profile_id(payload.get("promptLang") or payload.get("prompt_lang"))
    media_type = _safe_provider_tts_profile_id(payload.get("mediaType") or payload.get("media_type"))
    ref_audio_path = _safe_provider_private_path(payload.get("refAudioPath") or payload.get("ref_audio_path"))
    prompt_text = _safe_provider_prompt_text(payload.get("promptText") or payload.get("prompt_text"))
    if text_lang:
        profile["textLang"] = text_lang
    if prompt_lang:
        profile["promptLang"] = prompt_lang
    if media_type:
        profile["mediaType"] = media_type
    if ref_audio_path:
        profile["refAudioPath"] = ref_audio_path
    if prompt_text:
        profile["promptText"] = prompt_text
    streaming_mode = _safe_optional_bool(payload.get("streamingMode") if "streamingMode" in payload else payload.get("streaming_mode"))
    if streaming_mode is not None:
        profile["streamingMode"] = streaming_mode
    parallel_infer = _safe_optional_bool(payload.get("parallelInfer") if "parallelInfer" in payload else payload.get("parallel_infer"))
    if parallel_infer is not None:
        profile["parallelInfer"] = parallel_infer
    split_bucket = _safe_optional_bool(payload.get("splitBucket") if "splitBucket" in payload else payload.get("split_bucket"))
    if split_bucket is not None:
        profile["splitBucket"] = split_bucket
    batch_size = _safe_optional_int(payload.get("batchSize") if "batchSize" in payload else payload.get("batch_size"), minimum=1, maximum=32)
    if batch_size is not None:
        profile["batchSize"] = batch_size
    speed_factor = _safe_optional_float(
        payload.get("speedFactor") if "speedFactor" in payload else payload.get("speed_factor"),
        minimum=0.5,
        maximum=2.0,
    )
    if speed_factor is not None:
        profile["speedFactor"] = speed_factor
    fragment_interval = _safe_optional_float(
        payload.get("fragmentInterval") if "fragmentInterval" in payload else payload.get("fragment_interval"),
        minimum=0.0,
        maximum=2.0,
    )
    if fragment_interval is not None:
        profile["fragmentInterval"] = fragment_interval
    text_split_method = _safe_provider_tts_profile_id(
        payload.get("textSplitMethod") if "textSplitMethod" in payload else payload.get("text_split_method")
    )
    if text_split_method:
        profile["textSplitMethod"] = text_split_method
    return profile


def _provider_tts_test_profile_checks(
    *,
    endpoint: str,
    voice_profile_id: str,
    voice_profile: Mapping[str, Any],
    profile_source: str,
) -> dict[str, bool | str]:
    return {
        "endpoint": bool(str(endpoint or "").strip()),
        "voiceProfileId": bool(str(voice_profile_id or "").strip()),
        "profileApplied": bool(voice_profile),
        "profileSource": str(profile_source or "none")[:40],
        "refAudio": bool(str(voice_profile.get("refAudioPath") or "").strip()),
        "promptText": bool(str(voice_profile.get("promptText") or "").strip()),
    }


def _safe_provider_tts_profile_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 120:
        return ""
    lowered = text.lower()
    if (
        "://" in text
        or "/" in text
        or "\\" in text
        or ":" in text
        or ".." in text
        or "token" in lowered
        or "secret" in lowered
        or "password" in lowered
        or "api_key" in lowered
    ):
        return ""
    return text


def _safe_optional_bool(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return None


def _safe_optional_int(value: Any, *, minimum: int, maximum: int) -> int | None:
    if value in (None, ""):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return max(minimum, min(maximum, number))


def _safe_optional_float(value: Any, *, minimum: float, maximum: float) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(minimum, min(maximum, number))


def _safe_provider_prompt_text(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    lowered = text.lower()
    if any(marker in lowered for marker in ("api_key", "password", "secret", "token")):
        return ""
    return text[:300]


def _safe_provider_private_path(value: Any) -> str:
    text = str(value or "").strip().replace("\r", "").replace("\n", "")
    if not text:
        return ""
    lowered = text.lower()
    if "://" in text or any(marker in lowered for marker in ("api_key", "password", "secret", "token")):
        return ""
    return text[:500]


def _coerce_provider_tts_test_audio(result: Any) -> tuple[bytes, str]:
    if isinstance(result, bytes):
        audio = result
        media_type = "audio/wav"
    elif isinstance(result, SynthesizedAudio):
        audio = result.audio
        media_type = result.media_type or "audio/wav"
    else:
        audio = bytes(getattr(result, "audio", b"") or b"")
        media_type = str(getattr(result, "media_type", "") or "audio/wav")
    if not audio:
        raise RuntimeError("provider_tts_test_empty_audio")
    media_type = str(media_type or "audio/wav").split(";", 1)[0].strip().lower()
    if "/" not in media_type or any(ch in media_type for ch in "\r\n"):
        media_type = "audio/wav"
    if not media_type.startswith("audio/"):
        media_type = "audio/wav"
    return audio, media_type[:80]


async def _read_json_object(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _safe_bool_query(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "on", "include", "resolved"}


def _safe_positive_int(value: Any, *, default: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(1, min(maximum, number))


async def _maybe_thread(callback: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    value = callback()
    if hasattr(value, "__await__"):
        return await value
    return value


def _resolve_identity(
    request: Request,
    resolve_identity_from_query: Callable[[Request], tuple[str, str]] | None,
) -> tuple[str, str]:
    if resolve_identity_from_query is not None:
        return resolve_identity_from_query(request)
    session_id = str(request.query_params.get("user_id") or request.query_params.get("session_id") or "default_session")
    profile_user_id = str(request.query_params.get("real_user_id") or request.query_params.get("profileUserId") or session_id)
    return session_id, profile_user_id


def _resolve_character_voice_preference(engine: Any, request: Request) -> dict[str, Any]:
    pack_id = str(
        request.query_params.get("character_pack_id")
        or request.query_params.get("characterPackId")
        or request.query_params.get("character_pack")
        or ""
    ).strip()
    if not pack_id:
        return {}
    service = getattr(engine, "desktop_pet_character_resources", None)
    builder = getattr(service, "build_character_voice_preference", None)
    if not callable(builder):
        return {}
    try:
        result = builder(pack_id)
    except Exception:
        return {}
    return result if isinstance(result, dict) else {}


def _resolve_identity_with_payload(
    request: Request,
    payload: dict[str, Any],
    resolve_identity_from_query: Callable[[Request], tuple[str, str]] | None,
) -> tuple[str, str]:
    base_session_id, base_profile_user_id = _resolve_identity(request, resolve_identity_from_query)
    query_session = str(request.query_params.get("user_id") or request.query_params.get("session_id") or "").strip()
    query_profile = str(request.query_params.get("real_user_id") or request.query_params.get("profileUserId") or "").strip()
    body = payload if isinstance(payload, dict) else {}
    body_session = str(body.get("user_id") or body.get("session_id") or "").strip()
    body_profile = str(
        body.get("real_user_id")
        or body.get("profileUserId")
        or body.get("profile_user_id")
        or ""
    ).strip()
    session_id = str(query_session or body_session or base_session_id or "default_session").strip() or "default_session"
    profile_user_id = (
        str(query_profile or body_profile or base_profile_user_id or session_id)
        .strip()
        or session_id
    )
    return session_id, profile_user_id


def _resolve_provider_config_base_dir(
    *,
    capability_config_base_dir: str | Path | None,
    config_module: Any = None,
) -> Path | None:
    if capability_config_base_dir is not None:
        return Path(capability_config_base_dir)
    data_dir = getattr(config_module, "DATA_DIR", None)
    if data_dir:
        return Path(data_dir)
    return None


def _build_inert_workflow_job(
    *,
    preflight: dict[str, Any],
    profile_user_id: str,
    session_id: str,
) -> dict[str, Any]:
    now = _now_iso()
    accepted_inputs = preflight.get("acceptedInputs") if isinstance(preflight.get("acceptedInputs"), dict) else {}
    checks = preflight.get("checks") if isinstance(preflight.get("checks"), dict) else {}
    return {
        "_profileUserId": str(profile_user_id or ""),
        "_sessionId": str(session_id or ""),
        "_workflow": copy.deepcopy(preflight.get("workflow")) if isinstance(preflight.get("workflow"), dict) else {},
        "jobId": f"workflowjob_{uuid.uuid4().hex}",
        "kind": "workflow_job",
        "workflowId": str(preflight.get("workflowId") or ""),
        "capabilityId": str(preflight.get("capabilityId") or ""),
        "status": "queued-but-inert",
        "reason": str(preflight.get("reason") or "workflow_runner_not_bound")[:160],
        "executionReady": False,
        "canRun": False,
        "createdAt": now,
        "updatedAt": now,
        "inputs": {
            "inputImageHandle": str(accepted_inputs.get("inputImageHandle") or ""),
            "outputImageHandle": str(accepted_inputs.get("outputImageHandle") or ""),
        },
        "checks": {
            "providerConfigured": bool(checks.get("providerConfigured")),
            "workflowConfigured": bool(checks.get("workflowConfigured")),
            "inputImageHandle": bool(checks.get("inputImageHandle")),
            "outputImageHandle": bool(checks.get("outputImageHandle")),
            "runnerBound": False,
        },
        "runner": {
            "bound": False,
            "lane": "workflow",
            "backgroundTaskId": "",
            "reason": "workflow_runner_not_bound",
        },
        "outputs": [],
        "events": [
            {
                "status": "blocked",
                "reason": "workflow_runner_not_bound",
                "createdAt": now,
            }
        ],
    }


def _start_bound_workflow_job(
    *,
    preflight: dict[str, Any],
    profile_user_id: str,
    session_id: str,
    workflow_runner: Any,
    background_tasks: Any,
    payload: dict[str, Any],
    workflow_jobs: dict[str, dict[str, Any]],
    workflow_jobs_lock: threading.RLock,
) -> dict[str, Any]:
    if workflow_runner is None or background_tasks is None or not hasattr(background_tasks, "submit"):
        return {
            **preflight,
            "ok": False,
            "status": "not-implemented",
            "reason": "workflow_scheduler_not_bound",
            "executionReady": False,
            "canRun": False,
        }

    job = _build_bound_workflow_job(
        preflight=preflight,
        profile_user_id=profile_user_id,
        session_id=session_id,
        input_assets=_extract_workflow_input_assets(preflight=preflight, payload=payload),
    )
    with workflow_jobs_lock:
        workflow_jobs[job["jobId"]] = job
    try:
        handle = background_tasks.submit(
            lane="workflow",
            name="capability-workflow",
            fn=_run_bound_workflow_job,
            args=(job["jobId"], workflow_runner, workflow_jobs, workflow_jobs_lock),
        )
    except Exception:
        _update_workflow_job(
            job["jobId"],
            workflow_jobs=workflow_jobs,
            workflow_jobs_lock=workflow_jobs_lock,
            status="failed",
            reason="workflow_scheduler_failed",
            outputs=[],
        )
        with workflow_jobs_lock:
            failed_job = copy.deepcopy(workflow_jobs[job["jobId"]])
        return {
            **preflight,
            "ok": False,
            "status": "failed",
            "reason": "workflow_scheduler_failed",
            "executionReady": False,
            "canRun": False,
            "jobId": job["jobId"],
            "jobStatus": "failed",
            "job": _public_workflow_job(failed_job),
        }

    with workflow_jobs_lock:
        stored = workflow_jobs.get(job["jobId"])
        if stored is not None:
            stored["runner"]["backgroundTaskId"] = str(getattr(handle, "task_id", "") or "")
            job = copy.deepcopy(stored)
    return {
        **preflight,
        "ok": True,
        "status": "queued",
        "reason": "workflow_job_submitted",
        "executionReady": True,
        "canRun": False,
        "jobId": job["jobId"],
        "jobStatus": job["status"],
        "job": _public_workflow_job(job),
    }


def _build_bound_workflow_job(
    *,
    preflight: dict[str, Any],
    profile_user_id: str,
    session_id: str,
    input_assets: dict[str, WorkflowExecutionAsset] | None = None,
) -> dict[str, Any]:
    now = _now_iso()
    accepted_inputs = preflight.get("acceptedInputs") if isinstance(preflight.get("acceptedInputs"), dict) else {}
    checks = preflight.get("checks") if isinstance(preflight.get("checks"), dict) else {}
    return {
        "_profileUserId": str(profile_user_id or ""),
        "_sessionId": str(session_id or ""),
        "_workflow": copy.deepcopy(preflight.get("workflow")) if isinstance(preflight.get("workflow"), dict) else {},
        "_inputAssets": dict(input_assets or {}),
        "_outputAssets": {},
        "jobId": f"workflowjob_{uuid.uuid4().hex}",
        "kind": "workflow_job",
        "workflowId": str(preflight.get("workflowId") or ""),
        "capabilityId": str(preflight.get("capabilityId") or ""),
        "status": "queued",
        "reason": "workflow_job_submitted",
        "executionReady": True,
        "canRun": False,
        "createdAt": now,
        "updatedAt": now,
        "inputs": {
            "inputImageHandle": str(accepted_inputs.get("inputImageHandle") or ""),
            "outputImageHandle": str(accepted_inputs.get("outputImageHandle") or ""),
        },
        "checks": {
            "providerConfigured": bool(checks.get("providerConfigured")),
            "workflowConfigured": bool(checks.get("workflowConfigured")),
            "inputImageHandle": bool(checks.get("inputImageHandle")),
            "outputImageHandle": bool(checks.get("outputImageHandle")),
            "runnerBound": True,
        },
        "runner": {
            "bound": True,
            "lane": "workflow",
            "backgroundTaskId": "",
            "reason": "",
        },
        "outputs": [],
        "events": [
            {
                "status": "queued",
                "reason": "workflow_job_submitted",
                "createdAt": now,
            }
        ],
    }


def _run_bound_workflow_job(
    job_id: str,
    workflow_runner: Any,
    workflow_jobs: dict[str, dict[str, Any]],
    workflow_jobs_lock: threading.RLock,
) -> None:
    _update_workflow_job(
        job_id,
        workflow_jobs=workflow_jobs,
        workflow_jobs_lock=workflow_jobs_lock,
        status="running",
        reason="workflow_running",
        outputs=[],
    )
    with workflow_jobs_lock:
        job = copy.deepcopy(workflow_jobs.get(job_id))
    if not job:
        return

    request = WorkflowExecutionRequest(
        job_id=str(job.get("jobId") or ""),
        workflow_id=str(job.get("workflowId") or ""),
        capability_id=str(job.get("capabilityId") or ""),
        profile_user_id=str(job.get("_profileUserId") or ""),
        session_id=str(job.get("_sessionId") or ""),
        inputs=dict(job.get("inputs") or {}),
        input_assets=dict(job.get("_inputAssets") or {}),
        workflow=copy.deepcopy(job.get("_workflow")) if isinstance(job.get("_workflow"), dict) else {},
    )
    try:
        runner_result = call_workflow_execution_runner(workflow_runner, request)
    except Exception:
        runner_result = {
            "ok": False,
            "status": "failed",
            "reason": "workflow_runner_failed",
            "outputs": [],
        }

    if runner_result.get("ok"):
        _update_workflow_job(
            job_id,
            workflow_jobs=workflow_jobs,
            workflow_jobs_lock=workflow_jobs_lock,
            status="completed",
            reason=str(runner_result.get("reason") or "workflow_completed"),
            outputs=list(runner_result.get("outputs") or []),
            output_assets=list(runner_result.get("outputAssets") or []),
        )
    else:
        _update_workflow_job(
            job_id,
            workflow_jobs=workflow_jobs,
            workflow_jobs_lock=workflow_jobs_lock,
            status="failed",
            reason=str(runner_result.get("reason") or "workflow_runner_failed"),
            outputs=[],
            output_assets=[],
        )


def _update_workflow_job(
    job_id: str,
    *,
    workflow_jobs: dict[str, dict[str, Any]],
    workflow_jobs_lock: threading.RLock,
    status: str,
    reason: str,
    outputs: list[dict[str, Any]],
    output_assets: list[WorkflowExecutionAsset] | None = None,
) -> None:
    now = _now_iso()
    with workflow_jobs_lock:
        job = workflow_jobs.get(job_id)
        if job is None:
            return
        job["status"] = str(status or "failed")[:80]
        job["reason"] = str(reason or "")[:160]
        job["updatedAt"] = now
        job["executionReady"] = False
        job["canRun"] = False
        job["outputs"] = copy.deepcopy(outputs)
        if output_assets is not None:
            job["_outputAssets"] = {
                asset.handle: asset
                for asset in output_assets
                if isinstance(asset, WorkflowExecutionAsset)
            }
        events = list(job.get("events") or [])
        events.append({"status": job["status"], "reason": job["reason"], "createdAt": now})
        job["events"] = events[-20:]


def _with_bound_workflow_runner(
    result: dict[str, Any],
    *,
    workflow_runner: Any,
    background_tasks: Any,
) -> dict[str, Any]:
    if (
        workflow_runner is None
        or result.get("status") != "not-implemented"
        or result.get("reason") != "workflow_runner_not_bound"
    ):
        return result
    next_result = copy.deepcopy(result)
    checks = dict(next_result.get("checks") or {})
    checks["runnerBound"] = True
    next_result["checks"] = checks
    if background_tasks is None or not hasattr(background_tasks, "submit"):
        next_result["ok"] = False
        next_result["status"] = "not-implemented"
        next_result["reason"] = "workflow_scheduler_not_bound"
        next_result["executionReady"] = False
        next_result["canRun"] = False
        return next_result
    next_result["ok"] = True
    next_result["status"] = "ready"
    next_result["reason"] = ""
    next_result["executionReady"] = True
    next_result["canRun"] = True
    return next_result


def _extract_workflow_input_assets(
    *,
    preflight: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, WorkflowExecutionAsset]:
    accepted_inputs = preflight.get("acceptedInputs") if isinstance(preflight.get("acceptedInputs"), dict) else {}
    input_handle = str(accepted_inputs.get("inputImageHandle") or "")
    if not input_handle:
        return {}
    raw_asset = {
        "handle": input_handle,
        "bytes": payload.get("inputImageBytes") or payload.get("imageBytes"),
        "contentType": payload.get("inputImageContentType") or payload.get("mimeType") or payload.get("contentType"),
    }
    asset = normalize_workflow_asset(raw_asset)
    return {asset.handle: asset} if asset is not None else {}


def _find_workflow_job_output_asset(job: dict[str, Any], output_handle: str) -> WorkflowExecutionAsset | None:
    output_assets = job.get("_outputAssets") if isinstance(job.get("_outputAssets"), dict) else {}
    asset = output_assets.get(output_handle)
    return asset if isinstance(asset, WorkflowExecutionAsset) else None


def _mark_workflows_execution_ready(
    payload: dict[str, Any],
    *,
    workflow_runner: Any,
    background_tasks: Any,
    base_dir: Path | None,
    profile_user_id: str,
) -> None:
    if workflow_runner is None or background_tasks is None or not hasattr(background_tasks, "submit"):
        return
    workflows = _payload_workflow_entries(payload)
    for workflow in workflows:
        if not isinstance(workflow, dict):
            continue
        if not workflow.get("configured") or not workflow.get("enabled"):
            continue
        if str(workflow.get("status") or "") not in {"configured", "validated_config", "ready"}:
            continue
        runtime = validate_workflow_runtime_binding(
            base_dir=base_dir,
            profile_user_id=profile_user_id,
            workflow_id=str(workflow.get("id") or workflow.get("workflowId") or ""),
        )
        if runtime.get("ok"):
            workflow["status"] = "ready"
            workflow["reason"] = ""
            workflow["executionReady"] = True
        else:
            workflow["status"] = runtime.get("status") or "invalid_workflow_config"
            workflow["reason"] = runtime.get("reason") or "workflow_runtime_config_invalid"
            workflow["executionReady"] = False
    if isinstance(payload.get("summary"), dict):
        payload["summary"].update(_summarize_payload_entries(payload))
        payload["summary"]["executionReady"] = sum(1 for workflow in workflows if workflow.get("executionReady"))


def _payload_workflow_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(payload.get("workflows"), list):
        return [item for item in payload["workflows"] if isinstance(item, dict)]
    if isinstance(payload.get("capabilities"), list):
        return [
            item
            for item in payload["capabilities"]
            if isinstance(item, dict) and str(item.get("kind") or "") == "workflow"
        ]
    return []


def _summarize_payload_entries(payload: dict[str, Any]) -> dict[str, Any]:
    entries = payload.get("workflows") if isinstance(payload.get("workflows"), list) else payload.get("capabilities")
    entries = entries if isinstance(entries, list) else []
    by_kind: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_source: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        _increment_summary_count(by_kind, str(entry.get("kind") or "unknown"))
        _increment_summary_count(by_status, str(entry.get("status") or "unknown"))
        _increment_summary_count(by_source, str(entry.get("source") or "unknown"))
        _increment_summary_count(by_type, str(entry.get("type") or "unknown"))
    return {
        "total": sum(by_kind.values()),
        "byKind": by_kind,
        "byStatus": by_status,
        "bySource": by_source,
        "byType": by_type,
    }


def _increment_summary_count(target: dict[str, int], key: str) -> None:
    target[key] = target.get(key, 0) + 1


def _public_workflow_job(job: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(value)
        for key, value in job.items()
        if not str(key).startswith("_")
    }


def _safe_workflow_job_id(value: Any) -> str:
    text = str(value or "").strip()
    return text if WORKFLOW_JOB_ID_RE.match(text) else ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _observe_request(runtime_metrics: Any, name: str, started_at: float, ok: bool) -> None:
    if runtime_metrics is None:
        return
    try:
        runtime_metrics.observe_request(name, duration_ms=(time.perf_counter() - started_at) * 1000, ok=ok)
    except Exception:
        pass


def _log_best_effort(log_event: LogEvent | None, event: str, **fields: Any) -> None:
    if log_event is None:
        return
    try:
        log_event(event, **fields)
    except Exception:
        pass
