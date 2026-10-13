"""Services and public controls owned by one Akane Bot."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time as time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ..async_task_supervisor import AsyncTaskSupervisor
from ..bot_profile import BotConfig
from ..deployment_security import InstanceDeploymentSecurity
from ..desktop_pet_character_resources import DesktopPetCharacterResourceService
from ..desktop_satellite import DesktopSatelliteService
from ..durable_session_queue import DurableSessionWorkQueue
from ..session_inbox import SessionInboxStore
from ..host_jobs import HostJobOwner, HostJobStore
from ..host_execution_jobs import HostExecutionJobRuntime
from ..host_tool_jobs import HostToolJobRuntime
from ..host_subagent_jobs import HostSubagentJobRuntime
from ..host_workflow_jobs import HostWorkflowJobRuntime
from ..engine import AkaneMemoryEngine
from ..instance_profile import InstanceContext
from ..instance_runtime import InstanceRuntimeLease
from ..model_service_config import ModelServiceConfigStore, ModelServiceSettings
from ..extension_management import ExtensionManagementService
from ..plugin_agent_events import HostAgentEventRouter
from ..plugin_conversation_refs import PluginConversationReferenceAuthority
from ..plugin_generation_runtime import PluginGenerationRuntime
from ..plugin_tool_bridge import PluginCapabilityToolBridge
from ..public_guard import PublicThinkGuard
from ..qq_gateway import NapCatQQGateway
from ..resource_manifest import ResourceManifest
from ..runtime_settings import BotSettingsView, normalize_thinking_mode
from ..settings_overrides import SettingsOverrideStore, set_override
from ..turn_coordination import TurnCoordinator
from ..voice_runtime import AkaneVoiceRuntimeService

from .lifecycle import BotLifecycle


class RuntimeMetrics:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._counters: dict[str, float] = {}

    def incr(self, key: str, amount: float = 1.0) -> None:
        with self._lock:
            self._counters[key] = float(self._counters.get(key, 0.0)) + float(amount)

    def observe_request(self, name: str, *, duration_ms: float, ok: bool) -> None:
        status = "ok" if ok else "error"
        self.incr(f"{name}_requests_total", 1)
        self.incr(f"{name}_{status}_total", 1)
        self.incr(f"{name}_duration_ms_total", float(duration_ms))

    def snapshot(self) -> dict[str, float]:
        with self._lock:
            return dict(self._counters)


@dataclass(slots=True)
class BotRuntime(BotLifecycle):
    bot_config: BotConfig
    instance_context: InstanceContext
    instance_runtime: InstanceRuntimeLease
    deployment_security: InstanceDeploymentSecurity
    resources: ResourceManifest
    desktop_pet_character_resources: DesktopPetCharacterResourceService
    model_service_config_store: ModelServiceConfigStore
    settings_override_store: SettingsOverrideStore
    desktop_satellite_service: DesktopSatelliteService
    plugin_runtime: PluginGenerationRuntime
    plugin_agent_event_router: HostAgentEventRouter
    extension_management_service: ExtensionManagementService
    plugin_capability_source: PluginCapabilityToolBridge
    engine: AkaneMemoryEngine
    settings: BotSettingsView
    tts_client: Any
    runtime_metrics: RuntimeMetrics
    public_guard: PublicThinkGuard
    turn_coordinator: TurnCoordinator
    qq_gateway: NapCatQQGateway | None
    qq_followup_tasks: AsyncTaskSupervisor | None
    config_module: Any = field(repr=False)
    logger: logging.Logger = field(repr=False)
    session_inbox_store: SessionInboxStore | None = field(default=None, repr=False)
    session_work_queue: DurableSessionWorkQueue | None = field(default=None, repr=False)
    job_store: HostJobStore | None = field(default=None, repr=False)
    host_tool_jobs: HostToolJobRuntime | None = field(default=None, repr=False)
    host_subagent_jobs: HostSubagentJobRuntime | None = field(default=None, repr=False)
    host_execution_jobs: HostExecutionJobRuntime | None = field(default=None, repr=False)
    host_workflow_jobs: HostWorkflowJobRuntime | None = field(default=None, repr=False)
    plugin_conversation_refs: PluginConversationReferenceAuthority | None = field(default=None, repr=False)
    plugin_command_broker: Any = field(default=None, init=False, repr=False)
    plugin_event_broker: Any = field(default=None, init=False, repr=False)
    plugin_hook_broker: Any = field(default=None, init=False, repr=False)
    voice_runtime_service: AkaneVoiceRuntimeService | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _started: bool = field(default=False, init=False, repr=False)
    _start_status: dict[str, Any] | None = field(default=None, init=False, repr=False)
    _stopping: bool = field(default=False, init=False, repr=False)
    _lifecycle_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _stop_status: dict[str, Any] | None = field(default=None, init=False, repr=False)

    @property
    def bot_id(self) -> str:
        return self.bot_config.bot_id

    @property
    def display_name(self) -> str:
        return self.bot_config.display_name

    def control_job(self, job_id: str, *, owner: HostJobOwner, action: str) -> dict[str, Any]:
        """Route public task controls to the source-specific live runtime."""

        if self.job_store is None:
            return {"ok": False, "status": "unavailable", "reason": "host_job_store_unavailable"}
        job = self.job_store.get(job_id, owner=owner)
        if job is None:
            return {"ok": False, "status": "unknown", "reason": "host_job_not_found"}
        runtime = {
            "subagent": self.host_subagent_jobs,
        }.get(job.capability_source)
        if runtime is not None and callable(getattr(runtime, "control", None)):
            return runtime.control(job_id, owner=owner, action=action)
        result = self.job_store.control(job_id, owner=owner, action=action)
        if result.get("ok") and str(action or "").strip().lower() == "resume" and result.get("status") == "queued":
            recover = getattr(
                {
                    "tool": self.host_tool_jobs,
                    "workflow": self.host_workflow_jobs,
                    "execution": self.host_execution_jobs,
                }.get(job.capability_source),
                "recover",
                None,
            )
            if callable(recover):
                recover()
        return result

    @property
    def runtime_layout(self) -> Any:
        return self.instance_runtime.layout

    @property
    def qq_channel_config(self) -> Any:
        return self.deployment_security.qq

    @property
    def admin_write_auth(self) -> Any:
        return self.deployment_security.admin

    @property
    def user_assets_dir(self) -> Path:
        return self.engine.gift_assets.base_dir

    def reload_model_services(self, model_service_settings: ModelServiceSettings) -> dict[str, Any]:
        previous_provider = (
            str(self.settings.chat_api_protocol or "").strip().lower(),
            str(self.settings.chat_base_url or "").strip().rstrip("/").lower(),
            str(self.settings.chat_api_key or ""),
        )
        # Re-resolve "default" from deployment settings, not the previous
        # model profile's explicit override (for example, disabled -> default).
        default_thinking_mode = normalize_thinking_mode(
            getattr(self.config_module, "LLM_THINKING_MODE", "disabled")
        ) or "disabled"
        self.settings = replace(
            self.settings, llm_thinking_mode=default_thinking_mode
        ).with_model_service(model_service_settings)
        current_provider = (
            str(self.settings.chat_api_protocol or "").strip().lower(),
            str(self.settings.chat_base_url or "").strip().rstrip("/").lower(),
            str(self.settings.chat_api_key or ""),
        )
        result = dict(self.engine.reload_model_services(settings=self.settings))
        if previous_provider == current_provider or self.qq_gateway is None:
            result["chat_model_overrides_status"] = "unchanged"
            return result
        persisted = self.qq_gateway.clear_all_chat_model_overrides()
        result["chat_model_overrides_status"] = "cleared" if persisted else "persist_failed"
        return result

    def set_llm_thinking_mode(self, mode: str) -> str:
        normalized = normalize_thinking_mode(mode)
        if normalized not in {"enabled", "disabled"}:
            raise ValueError("llm_thinking_mode_invalid")
        applied = str(
            set_override(
                self.config_module,
                self.settings_override_store,
                key="LLM_THINKING_MODE",
                raw_value=normalized,
            )
            or ""
        )
        self.settings = self.settings.overlay({"llm_thinking_mode": applied})
        self.engine.settings = self.settings
        self.engine.llm.settings = self.settings
        return self.settings.llm_thinking_mode

    def bind_app_state(self, app: Any) -> None:
        app.state.akane_bot_runtime = self
        app.state.akane_instance_context = self.instance_context
        app.state.akane_instance_runtime = self.instance_runtime
        app.state.akane_deployment_security = self.deployment_security
        app.state.akane_desktop_satellite = self.desktop_satellite_service
        app.state.akane_plugin_runtime = self.plugin_runtime


def log_runtime_start_status(runtime: BotRuntime, status: dict[str, Any]) -> None:
    if status.get("status") != "degraded":
        return
    runtime.logger.warning(
        "Bot runtime degraded at startup: %s",
        json.dumps(status, ensure_ascii=False, sort_keys=True),
    )
