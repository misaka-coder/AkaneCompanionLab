"""Assemble capability routes while keeping existing helper imports available."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Request

from ..capability_approval import CapabilityApprovalStore
from ..capability_registry import ExecutorBroker
from ..host_workflow_jobs import HostWorkflowJobRuntime
from ..music_lyrics import LyricsSearchFunc, OnlineLyricsService

# Keep the original helper imports available to existing callers of this facade.
from .capabilities_runtime.approval import register_approval_routes
from .capabilities_runtime.catalog import _build_skill_catalog_payload, register_catalog_routes
from .capabilities_runtime.common import (
    _read_json_object,
    _safe_bool_query,
    _safe_positive_int,
    _maybe_thread,
    _resolve_identity,
    _resolve_character_voice_preference,
    _resolve_identity_with_payload,
    _resolve_provider_config_base_dir,
    _observe_request,
    _log_best_effort,
)
from .capabilities_runtime.context import CapabilityRouteContext
from .capabilities_runtime.definitions import (
    LogEvent,
    ProviderHealthChecker,
    ProviderTtsTestRunner,
    McpToolDiscoverer,
    WORKFLOW_JOB_ID_RE,
    GPT_SOVITS_PROVIDER_ID,
    PROVIDER_TTS_TEST_DEFAULT_TEXT,
    PROVIDER_TTS_TEST_TEXT_MAX_CHARS,
    PROVIDER_TTS_TEST_AUDIO_MAX_BYTES,
)
from .capabilities_runtime.mcp import _discover_mcp_server_tools, register_mcp_routes
from .capabilities_runtime.music import register_music_routes
from .capabilities_runtime.providers import register_providers_routes
from .capabilities_runtime.tts_probe import _run_provider_tts_test
from .capabilities_runtime.voice_payloads import (
    _resolve_provider_tts_test_endpoint,
    _safe_provider_tts_test_text,
    _provider_tts_test_profile_payload,
    _provider_tts_test_profile_checks,
    _safe_provider_tts_profile_id,
    _safe_optional_bool,
    _safe_optional_int,
    _safe_optional_float,
    _safe_provider_prompt_text,
    _safe_provider_private_path,
    _coerce_provider_tts_test_audio,
)
from .capabilities_runtime.workflow_support import (
    _with_bound_workflow_runner,
    _extract_workflow_input_assets,
    _mark_workflows_execution_ready,
    _payload_workflow_entries,
    _summarize_payload_entries,
    _increment_summary_count,
    _safe_workflow_job_id,
)
from .capabilities_runtime.workflows import register_workflows_routes


_ROUTE_ORDER = {name: index for index, name in enumerate((
    "read_tool_exposure",
    "write_tool_exposure",
    "read_capabilities",
    "read_capability_approval_policy",
    "write_capability_approval_policy",
    "resolve_music_lyrics",
    "resolve_co_listen_summary",
    "get_music_control_permissions",
    "set_music_control_permissions",
    "read_capability_providers",
    "read_capability_voice_profiles",
    "read_capability_mcp_servers",
    "write_capability_mcp_server_config",
    "discover_capability_mcp_server_tools",
    "enable_capability_mcp_server",
    "disable_capability_mcp_server",
    "restart_capability_mcp_server",
    "remove_capability_mcp_server",
    "read_capability_approval_requests",
    "create_capability_approval_request",
    "read_capability_approval_request",
    "decide_capability_approval_request",
    "read_capability_workflows",
    "write_capability_workflow_config",
    "validate_capability_workflow_config",
    "import_capability_workflow_file",
    "preflight_capability_workflow_execution",
    "start_capability_workflow_job",
    "read_capability_workflow_job",
    "read_capability_workflow_job_output",
    "write_capability_provider_config",
    "check_capability_provider_health",
    "test_capability_provider_tts",
    "inspect_capability_provider_voice_profile_folder",
    "write_capability_provider_voice_profile",
    "check_local_environment",
))}


def build_capabilities_router(
    *,
    engine: Any,
    config_module: Any = None,
    tts_client: Any = None,
    settings: Any = None,
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
    workflow_job_runtime: HostWorkflowJobRuntime | None = None,
    lyrics_searcher: LyricsSearchFunc | None = None,
) -> APIRouter:
    router = APIRouter()
    executor_broker = getattr(engine, "executor_broker", None)
    if executor_broker is None:
        executor_broker = ExecutorBroker(None)
        try:
            setattr(engine, "executor_broker", executor_broker)
        except Exception:
            pass
    # Share the engine-owned approval store so exec-handler-created requests and
    # their grants (redemption) live in the same instance the routes resolve.
    approval_store = getattr(engine, "approval_store", None)
    if approval_store is None:
        approval_store = CapabilityApprovalStore()
        try:
            setattr(engine, "approval_store", approval_store)
        except Exception:
            pass
    provider_config_base_dir = _resolve_provider_config_base_dir(
        capability_config_base_dir=capability_config_base_dir,
        config_module=config_module,
    )
    lyrics_service = OnlineLyricsService(
        base_dir=provider_config_base_dir,
        config_module=config_module,
        search_func=lyrics_searcher,
    )
    context = CapabilityRouteContext(
        engine=engine,
        config_module=config_module,
        tts_client=tts_client,
        settings=settings,
        runtime_metrics=runtime_metrics,
        log_event=log_event,
        resolve_identity_from_query=resolve_identity_from_query,
        local_environment_probe=local_environment_probe,
        provider_config_base_dir=provider_config_base_dir,
        provider_health_checker=provider_health_checker,
        provider_tts_test_runner=provider_tts_test_runner,
        mcp_tool_discoverer=mcp_tool_discoverer,
        workflow_runner=workflow_runner,
        background_tasks=background_tasks,
        workflow_job_runtime=workflow_job_runtime,
        executor_broker=executor_broker,
        approval_store=approval_store,
        lyrics_service=lyrics_service,
    )

    register_providers_routes(router, context=context)
    register_mcp_routes(router, context=context)
    register_workflows_routes(router, context=context)
    register_catalog_routes(router, context=context)
    register_approval_routes(router, context=context)
    register_music_routes(router, context=context)
    router.routes.sort(key=lambda route: _ROUTE_ORDER[route.name])
    return router
