"""Immutable instance bindings shared by capability route groups."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from fastapi import Request

from ...capability_approval import CapabilityApprovalStore
from ...capability_registry import ExecutorBroker
from ...host_workflow_jobs import HostWorkflowJobRuntime
from ...music_lyrics import OnlineLyricsService
from .definitions import LogEvent, ProviderHealthChecker, ProviderTtsTestRunner, McpToolDiscoverer


@dataclass(frozen=True)
class CapabilityRouteContext:
    engine: Any
    config_module: Any
    tts_client: Any
    settings: Any
    runtime_metrics: Any
    log_event: LogEvent | None
    resolve_identity_from_query: Callable[[Request], tuple[str, str]] | None
    local_environment_probe: Callable[[], Any] | None
    provider_config_base_dir: Path | None
    provider_health_checker: ProviderHealthChecker | None
    provider_tts_test_runner: ProviderTtsTestRunner | None
    mcp_tool_discoverer: McpToolDiscoverer | None
    workflow_runner: Any
    background_tasks: Any
    workflow_job_runtime: HostWorkflowJobRuntime | None
    executor_broker: ExecutorBroker
    approval_store: CapabilityApprovalStore
    lyrics_service: OnlineLyricsService
