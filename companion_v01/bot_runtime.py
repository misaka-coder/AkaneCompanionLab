"""Repeatable construction and lifecycle ownership for one Akane Bot."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping


from .async_task_supervisor import AsyncTaskSupervisor
from .bot_profile import BotConfig, bot_config_from_instance_context
from .deployment_security import InstanceDeploymentSecurity, resolve_instance_deployment_security
from .capability_diagnosis import build_host_command_registrations
from .desktop_pet_character_resources import DesktopPetCharacterResourceService
from .desktop_satellite import DesktopSatelliteService
from .durable_session_queue import DurableSessionWorkQueue
from .session_inbox import SessionInboxStore
from .host_jobs import HostJob, HostJobOwner, HostJobStore
from .host_execution_jobs import HostExecutionJobRuntime
from .host_tool_jobs import HostToolJobRuntime
from .plugin_tasks import HostTaskProvider
from .host_subagent_jobs import HostSubagentJobRuntime
from .subagent_runtime import InProcessSubagentProvider, SubagentProviderRegistry
from .subagent_engine import EngineSubagentDriver
from .tool_handlers.subagent import SpawnSubagentToolHandler
from .host_workflow_jobs import HostWorkflowJobRuntime, WorkflowJobAssetStore
from .engine import AkaneMemoryEngine
from .instance_profile import InstanceContext, instance_context_from_bot_config, resolve_instance_context
from .instance_runtime import InstanceRuntimeLease, bind_instance_runtime
from .model_service_config import (
    ModelServiceConfigStore,
    ModelServiceSettings,
)
from .extension_management import (
    ExtensionManagementService,
    PLUGIN_SELECTION_STATE_FILENAME,
    PluginSelectionStore,
)
from .plugin_managed_artifacts import GeneratedFileManagedArtifactSink
from .plugin_resources import GeneratedFileResourceProvider
from .plugin_capability_calls import EnginePluginCapabilityProvider
from .plugin_connections import ModelServicePluginConnectionProvider
from .plugin_connection_settings import PluginConnectionSettings
from .execution_resource_policy import ExecutionResourcePolicy
from .execution_policies import ExecutionPolicyPipeline, load_execution_policies
from .plugin_notifications import NullNotificationPort, QQTextNotificationPort
from .plugin_agent_events import HostAgentEventRouter
from .plugin_turn_intents import HostJobTurnIntent, HostTurnResult
from .plugin_conversation_refs import PluginConversationReferenceAuthority
from .plugin_generation_candidate import PluginGenerationCandidateBuilder
from .plugin_generation_runtime import PluginGenerationRuntime
from .plugin_installation import ManagedPluginArtifactStore
from .plugin_market import StaticPluginMarket
from .plugin_tool_bridge import PluginCapabilityToolBridge
from .public_guard import PublicThinkGuard
from .local_workflow_runners.comfyui import ComfyUiWorkflowRunner
from .qq_channel_profiles import QQChannelDeploymentProfile
from .qq_gateway import NapCatQQGateway
from .qq_tool_delivery import QQToolDeliveryPort
from .resource_manifest import ResourceManifest
from .runtime_settings import BotSettingsView, normalize_thinking_mode
from .settings_overrides import (
    RuntimeConfigView,
    SettingsOverrideStore,
    load_and_apply_saved_overrides,
    load_saved_overrides,
    set_override,
)
from .tts_provider_runtime import resolve_character_tts_client
from .turn_coordination import TurnCoordinator
from .voice_runtime import AkaneVoiceRuntimeService


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
class BotRuntime:
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
            recover = getattr({
                "tool": self.host_tool_jobs,
                "workflow": self.host_workflow_jobs,
                "execution": self.host_execution_jobs,
            }.get(job.capability_source), "recover", None)
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
        self.settings = self.settings.with_model_service(model_service_settings)
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

    async def start(self) -> dict[str, Any]:
        if self._stop_status is not None:
            return {"status": "unavailable", "reason": "bot_runtime_stopped", "bot_id": self.bot_id}
        if self._started:
            return {"status": "active", "reason": "already_started", "bot_id": self.bot_id}

        plugin_status = await self.plugin_runtime.start()
        reconcile_runtime = getattr(
            self.extension_management_service,
            "reconcile_runtime",
            None,
        )
        artifact_status = (
            reconcile_runtime(plugin_status)
            if callable(reconcile_runtime)
            else {"status": "not_configured", "reload_required": False}
        )
        if artifact_status.get("reload_required"):
            first_artifact_status = dict(artifact_status)
            fallback_status = await self.plugin_runtime.restart()
            fallback_artifact_status = (
                reconcile_runtime(fallback_status)
                if callable(reconcile_runtime)
                else {"status": "not_configured", "reload_required": False}
            )
            artifact_status = {
                **dict(fallback_artifact_status),
                "fallback_attempted": True,
                "initial_status": str(first_artifact_status.get("status") or ""),
            }
            if fallback_status.get("published") is True:
                plugin_status = fallback_status
        host_commands = build_host_command_registrations(
            engine=self.engine,
            qq_gateway=self.qq_gateway,
            config_module=self.config_module,
            satellite_service=self.desktop_satellite_service,
            bot_label=str(getattr(self, "display_name", "") or ""),
        )
        self.plugin_command_broker = self.plugin_runtime.build_qq_command_broker(
            host_registrations=host_commands
        )
        self.plugin_event_broker = self.plugin_runtime.build_event_broker()
        bind_timeline = getattr(self.plugin_runtime, "bind_timeline_recorder", None)
        if callable(bind_timeline):
            # Reuse the engine's existing public MemCore timeline port.
            bind_timeline(self.engine.record_plugin_timeline_event)
        build_hook_broker = getattr(self.plugin_runtime, "build_hook_broker", None)
        self.plugin_hook_broker = build_hook_broker() if callable(build_hook_broker) else None
        recover_queue = getattr(self.session_work_queue, "recover", None)
        if callable(recover_queue):
            await recover_queue()
        recover_jobs = getattr(self.job_store, "recover_abandoned_claims", None)
        if callable(recover_jobs):
            await asyncio.to_thread(recover_jobs)
        if self.host_workflow_jobs is not None:
            self.host_workflow_jobs.recover()
        if self.host_tool_jobs is not None:
            host_loop = asyncio.get_running_loop()

            def submit_completion(job: HostJob) -> Any:
                future = asyncio.run_coroutine_threadsafe(
                    _dispatch_host_job_completion(self.engine, self.plugin_agent_event_router, job),
                    host_loop,
                )
                try:
                    return future.result(timeout=900.0)
                except Exception:
                    future.cancel()
                    raise

            self.host_tool_jobs.bind_terminal_callback(submit_completion)
            if self.host_execution_jobs is not None:
                await asyncio.to_thread(self.host_execution_jobs.recover)
            if self.host_subagent_jobs is not None:
                self.host_subagent_jobs.recover()
            self.host_tool_jobs.recover()
        bind_hook_broker = getattr(self.engine, "bind_plugin_hook_broker", None)
        if callable(bind_hook_broker):
            bind_hook_broker(self.plugin_hook_broker)
        bind_gateway_hook_broker = getattr(self.qq_gateway, "bind_plugin_hook_broker", None)
        if callable(bind_gateway_hook_broker):
            bind_gateway_hook_broker(self.plugin_hook_broker)
        self._started = True
        status = "degraded" if plugin_status.get("status") == "degraded" else "active"
        return {
            "status": status,
            "reason": "plugin_runtime_degraded" if status == "degraded" else "",
            "bot_id": self.bot_id,
            "plugin_status": plugin_status,
            "plugin_artifact_status": artifact_status,
        }

    async def stop(self) -> dict[str, Any]:
        if self._stop_status is not None:
            return dict(self._stop_status)

        self.plugin_agent_event_router.request_shutdown()

        queue_status: dict[str, Any] = {"status": "not_configured"}
        if self.session_work_queue is not None:
            try:
                queue_status = await self.session_work_queue.close(timeout=10.0)
            except Exception:
                queue_status = {"status": "error", "reason": "session_queue_shutdown_failed"}
            if queue_status.get("status") != "stopped":
                # Do not cancel a to_thread model turn or close the services it
                # still uses. A later stop may retry after the turn settles.
                return {"status": "degraded", "reason": "session_queue_shutdown_incomplete",
                        "bot_id": self.bot_id, "queue_status": queue_status,
                        "engine_status": {"status": "deferred", "reason": "session_queue_not_drained"}}

        failures: list[str] = []
        await self.plugin_agent_event_router.aclose()
        followup_status: dict[str, Any] = {"status": "not_configured"}
        plugin_status: dict[str, Any] = {"status": "not_started"}
        voice_status: dict[str, Any] = {"status": "not_configured"}
        engine_status: dict[str, Any] = {"status": "not_started"}

        if self.host_tool_jobs is not None:
            self.host_tool_jobs.bind_terminal_callback(None)

        request_engine_shutdown = getattr(self.engine, "request_shutdown", None)
        if callable(request_engine_shutdown):
            try:
                request_engine_shutdown()
            except Exception:
                failures.append("engine_shutdown_signal_failed")

        if self.qq_followup_tasks is not None:
            try:
                followup_status = await self.qq_followup_tasks.close(timeout=10.0)
                if followup_status.get("status") != "stopped":
                    failures.append("qq_followup_shutdown_incomplete")
            except Exception:
                failures.append("qq_followup_shutdown_failed")
                followup_status = {"status": "error", "reason": "qq_followup_shutdown_failed"}

        try:
            plugin_status = await self.plugin_runtime.stop()
            if int(plugin_status.get("close_failure_count") or 0) > 0 or plugin_status.get(
                "cleanup_failures"
            ):
                failures.append("plugin_runtime_close_incomplete")
        except Exception:
            failures.append("plugin_runtime_shutdown_failed")
            plugin_status = {"status": "error", "reason": "plugin_runtime_shutdown_failed"}

        self.plugin_command_broker = None
        self.plugin_event_broker = None
        unbind_gateway_hook_broker = getattr(self.qq_gateway, "bind_plugin_hook_broker", None)
        if callable(unbind_gateway_hook_broker):
            try:
                unbind_gateway_hook_broker(None)
            except Exception:
                failures.append("qq_gateway_hook_unbind_failed")
        self.plugin_hook_broker = None
        if self.voice_runtime_service is not None:
            try:
                # Runtime cleanup owns blocking worker/thread joins. Keep those
                # joins off the Host lifecycle loop so sibling Bots can stop
                # concurrently under the same shutdown deadline.
                voice_status = await asyncio.to_thread(self.voice_runtime_service.close)
                if voice_status.get("status") != "stopped":
                    failures.append("voice_runtime_shutdown_incomplete")
            except Exception:
                failures.append("voice_runtime_shutdown_failed")
                voice_status = {"status": "error", "reason": "voice_runtime_shutdown_failed"}
        try:
            engine_status = await asyncio.to_thread(self.engine.close)
            if engine_status.get("status") != "stopped":
                failures.append("engine_shutdown_incomplete")
        except Exception:
            failures.append("engine_shutdown_failed")
            engine_status = {"status": "error", "reason": "engine_shutdown_failed"}

        self._stop_status = {
            "status": "degraded" if failures else "stopped",
            "reason": failures[0] if failures else "",
            "bot_id": self.bot_id,
            "failures": failures,
            "queue_status": queue_status,
            "followup_status": followup_status,
            "plugin_status": plugin_status,
            "voice_status": voice_status,
            "engine_status": engine_status,
        }
        return dict(self._stop_status)

class BotRuntimeFactory:
    """Construct one BotRuntime from the current process defaults."""

    def __init__(
        self,
        *,
        config_module: Any,
        assets_dir: Path,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config_module = config_module
        self.assets_dir = Path(assets_dir)
        self.logger = logger or logging.getLogger("akane.bot_runtime")

    def create(
        self,
        *,
        data_root: Path,
        bot_config: BotConfig | None = None,
        qq_channel_profile: QQChannelDeploymentProfile | None = None,
        desktop_satellite_service: DesktopSatelliteService | None = None,
        selected_instance_id: str = "",
        explicit_data_root: bool = False,
        settings_overrides: Mapping[str, Any] | None = None,
    ) -> BotRuntime:
        if bot_config is None:
            instance_context = resolve_instance_context(
                data_root=Path(data_root),
                selected_instance_id=selected_instance_id,
            )
            effective_bot_config = bot_config_from_instance_context(instance_context)
        else:
            if selected_instance_id:
                raise ValueError("bot_config_and_selected_instance_id_are_mutually_exclusive")
            instance_context = instance_context_from_bot_config(bot_config)
            effective_bot_config = bot_config
        instance_runtime: InstanceRuntimeLease | None = None
        engine: AkaneMemoryEngine | None = None
        try:
            instance_runtime = bind_instance_runtime(
                instance_context,
                data_root=Path(data_root),
                explicit_data_root=explicit_data_root,
            )
            runtime_layout = instance_runtime.layout
            resources = ResourceManifest(self.assets_dir)
            route_prefix = f"/api/bots/{effective_bot_config.bot_id}"
            character_resources = DesktopPetCharacterResourceService(
                characters_dir=runtime_layout.characters_dir,
                public_prefix=f"{route_prefix}/desktop-pet-character-packs",
            )
            model_store = ModelServiceConfigStore(runtime_layout.config_dir / "model_service.json")
            try:
                saved_model_settings = model_store.load()
            except Exception as exc:
                saved_model_settings = None
                self.logger.warning("Model service config ignored: %s", type(exc).__name__)
            settings_store = SettingsOverrideStore(runtime_layout.config_dir / "settings_overrides.json")
            if bot_config is None:
                load_and_apply_saved_overrides(
                    self.config_module,
                    settings_store,
                    on_error=lambda exc: self.logger.warning("Settings override ignored: %s", type(exc).__name__),
                )
                runtime_config = self.config_module
            else:
                saved_overrides = load_saved_overrides(
                    settings_store,
                    on_error=lambda exc: self.logger.warning("Settings override ignored: %s", type(exc).__name__),
                )
                runtime_config = RuntimeConfigView(
                    self.config_module,
                    {
                        **saved_overrides,
                        "DATA_DIR": str(runtime_layout.data_root),
                        "DATA_ROOT": str(runtime_layout.data_root),
                    },
                )
            settings = BotSettingsView.from_config(runtime_config)
            if saved_model_settings is not None:
                settings = settings.with_model_service(saved_model_settings)
            settings = settings.overlay(settings_overrides)
            if bot_config is not None:
                settings = settings.overlay(
                    {"prompt_cache_namespace": (f"{settings.prompt_cache_namespace}:bot:{effective_bot_config.bot_id}")}
                )

            deployment_security = resolve_instance_deployment_security(
                instance_context,
                runtime_config,
                qq_channel_profile=qq_channel_profile,
            )
            if deployment_security.qq.enabled and deployment_security.qq.onebot_shared_data_root:
                deployment_security = replace(
                    deployment_security,
                    qq=replace(
                        deployment_security.qq,
                        local_data_root=str(runtime_layout.data_root),
                    ),
                )
            satellite_service = (
                desktop_satellite_service
                if desktop_satellite_service is not None
                else DesktopSatelliteService(
                    instance_id=instance_context.instance_id,
                    token=deployment_security.satellite.token,
                )
            )
            satellite_offer_source = satellite_service.for_bot(
                bot_id=effective_bot_config.bot_id,
                memory_space_id=effective_bot_config.memory_space_id,
            )
            execution_resource_policy = ExecutionResourcePolicy.from_config(
                getattr(runtime_config, "AKANE_EXECUTION_RESOURCE_POLICY", "{}"))
            execution_policies = load_execution_policies(getattr(runtime_config, "AKANE_EXECUTION_POLICIES", "[]"))
            plugin_selection_store = PluginSelectionStore(
                runtime_layout.config_dir / PLUGIN_SELECTION_STATE_FILENAME,
                defaults=instance_context.plugins,
                instance_id=instance_context.instance_id,
            )
            plugin_artifact_store = ManagedPluginArtifactStore(
                runtime_layout.data_root / "extensions" / "plugins",
                instance_id=instance_context.instance_id,
                dependency_wheelhouse=(
                    Path(str(getattr(runtime_config, "AKANE_PLUGIN_DEPENDENCY_WHEELHOUSE", "") or "").strip())
                    if str(getattr(runtime_config, "AKANE_PLUGIN_DEPENDENCY_WHEELHOUSE", "") or "").strip()
                    else None
                ),
                dependency_index_url=str(
                    getattr(runtime_config, "AKANE_PLUGIN_DEPENDENCY_INDEX_URL", "") or ""
                ).strip(),
            )
            plugin_candidate_builder = PluginGenerationCandidateBuilder(
                source_resolver=plugin_artifact_store,
                service_bindings_provider=plugin_selection_store.load_service_bindings,
                bootstrap_services=tuple(item.dependency for item in execution_policies),
                project_root=Path(__file__).resolve().parents[1],
                work_root=runtime_layout.run_dir / "plugin-generations",
                plugin_storage_data_root=runtime_layout.data_root,
                plugin_storage_instance_id=instance_context.instance_id,
            )
            plugin_runtime = PluginGenerationRuntime(
                plugin_selection_store.load(),
                candidate_builder=plugin_candidate_builder,
            )
            plugin_conversation_refs = PluginConversationReferenceAuthority(
                runtime_layout.state_dir / "plugin_conversation_ref.key",
                instance_id=instance_context.instance_id,
            )
            plugin_agent_event_router = HostAgentEventRouter(plugin_conversation_refs.resolve)
            plugin_runtime.bind_turn_router(plugin_agent_event_router)
            extension_management_service = ExtensionManagementService(
                plugin_runtime=plugin_runtime,
                selection_store=plugin_selection_store,
                artifact_store=plugin_artifact_store,
                connection_settings=PluginConnectionSettings(runtime_layout.users_data_dir),
                market=StaticPluginMarket(
                    str(getattr(runtime_config, "AKANE_PLUGIN_MARKET_INDEX", "") or "").strip()
                    or Path(__file__).resolve().parents[1] / ".plugin-market" / "index.json"
                ),
            )
            plugin_capability_source = PluginCapabilityToolBridge(
                plugin_runtime,
                config_base_dir=runtime_layout.users_data_dir,
                conversation_ref_issuer=plugin_conversation_refs.issue,
                result_preview_chars=int(getattr(runtime_config, "AKANE_PLUGIN_RESULT_PREVIEW_CHARS", 16_000)),
            )
            engine = AkaneMemoryEngine(
                runtime_layout.engine_dir,
                resource_manifest=resources,
                desktop_pet_character_resources=character_resources,
                instance_context=instance_context,
                runtime_layout=runtime_layout,
                plugin_capability_source=plugin_capability_source,
                extension_management_service=extension_management_service,
                stable_system_blocks_provider=plugin_runtime.stable_system_prompt_blocks,
                qq_channel_config=deployment_security.qq,
                capability_offer_source=satellite_offer_source,
                settings=settings,
                user_assets_public_prefix=f"{route_prefix}/user-assets",
            )
            engine.plugin_observations_provider = plugin_agent_event_router.prompt_context
            engine.executor_broker.resource_policy = execution_resource_policy
            engine.executor_broker.execution_policies = ExecutionPolicyPipeline(execution_policies, source=plugin_capability_source)
            engine.plugin_steering_observer = plugin_agent_event_router.steering_results
            engine.plugin_skill_roots_provider = plugin_runtime.skill_roots
            plugin_runtime.bind_capability_provider(EnginePluginCapabilityProvider(engine))
            plugin_capability_source.bind_approval_store(engine._get_approval_store())
            plugin_runtime.bind_connection_provider(ModelServicePluginConnectionProvider(
                engine, runtime_config, connection_settings=extension_management_service.connection_settings))
            generated_file_service = engine._get_generated_file_service()
            if generated_file_service is not None:
                plugin_capability_source.bind_result_sink(GeneratedFileManagedArtifactSink(generated_file_service))
                plugin_runtime.bind_managed_artifact_sink(
                    GeneratedFileManagedArtifactSink(generated_file_service)
                )
                plugin_runtime.bind_resource_provider(GeneratedFileResourceProvider(
                    generated_file_service, work_root=runtime_layout.run_dir / "plugin-inputs",
                ))

            qq_gateway: NapCatQQGateway | None
            qq_followup_tasks: AsyncTaskSupervisor | None
            if deployment_security.qq.enabled:
                qq_gateway = NapCatQQGateway(
                    state_path=runtime_layout.state_dir / "qq_gateway_state.json",
                    channel_config=deployment_security.qq,
                    default_character_pack_id=instance_context.character_pack_id,
                    wake_words=effective_bot_config.wake_words,
                )
                engine.bind_qq_delivery_port(QQToolDeliveryPort(qq_gateway))
                qq_followup_tasks = AsyncTaskSupervisor(name=f"qq-followups:{instance_context.instance_id}")
                plugin_runtime.bind_notification_port(QQTextNotificationPort(qq_gateway))
            else:
                qq_gateway = None
                qq_followup_tasks = None
                plugin_runtime.bind_notification_port(NullNotificationPort())

            turn_coordinator = TurnCoordinator()
            engine.turn_coordinator = turn_coordinator
            session_inbox_store = SessionInboxStore(engine.store.db_path)
            engine.session_inbox_store = session_inbox_store
            session_work_queue = DurableSessionWorkQueue(session_inbox_store)
            plugin_agent_event_router.bind_runtime(session_work_queue, turn_coordinator)
            job_store = HostJobStore(engine.store.db_path)
            engine.job_store = job_store
            plugin_runtime.bind_task_provider(
                HostTaskProvider(job_store, generation_active=plugin_runtime.generation_active)
            )
            host_tool_jobs = HostToolJobRuntime(
                engine=engine,
                store=job_store,
                background_tasks=engine.background_tasks,
                conversation_ref_issuer=plugin_conversation_refs.issue,
            )
            engine.host_tool_jobs = host_tool_jobs
            bind_revocation = getattr(plugin_runtime, "bind_capability_revocation_listener", None)
            if callable(bind_revocation):
                bind_revocation(host_tool_jobs.revoke_capabilities)
            subagent_providers = SubagentProviderRegistry()
            subagent_driver = EngineSubagentDriver(engine)
            subagent_providers.register(InProcessSubagentProvider(subagent_driver))
            host_subagent_jobs = HostSubagentJobRuntime(
                store=job_store, providers=subagent_providers, provider_name="in_process",
                background_tasks=engine.background_tasks, completion_publisher=host_tool_jobs.publish_completion,
                task_controller=subagent_driver.control,
            )
            engine.host_subagent_jobs = host_subagent_jobs
            engine.tool_handlers["spawn_subagent"] = SpawnSubagentToolHandler(
                engine=engine, runtime=host_subagent_jobs, conversation_ref_issuer=plugin_conversation_refs.issue,
            )
            host_execution_jobs = HostExecutionJobRuntime(
                store=job_store,
                conversation_ref_issuer=plugin_conversation_refs.issue,
                completion_publisher=host_tool_jobs.publish_completion,
            )
            exec_run_handler = engine.tool_handlers.get("exec_run")
            if exec_run_handler is not None:
                exec_run_handler.bind_job_runtime(host_execution_jobs)
                host_execution_jobs.bind_provider(exec_run_handler.execution_provider)
            engine.host_execution_jobs = host_execution_jobs
            host_workflow_jobs = HostWorkflowJobRuntime(
                store=job_store,
                asset_store=WorkflowJobAssetStore(
                    runtime_layout.users_data_dir / "_runtime" / "workflow_jobs"
                ),
                workflow_runner=ComfyUiWorkflowRunner(config_base_dir=runtime_layout.users_data_dir),
                background_tasks=engine.background_tasks,
                executor_broker=engine.executor_broker,
            )
            engine.host_workflow_jobs = host_workflow_jobs
            runtime = BotRuntime(
                bot_config=effective_bot_config,
                instance_context=instance_context,
                instance_runtime=instance_runtime,
                deployment_security=deployment_security,
                resources=resources,
                desktop_pet_character_resources=character_resources,
                model_service_config_store=model_store,
                settings_override_store=settings_store,
                desktop_satellite_service=satellite_service,
                plugin_runtime=plugin_runtime,
                plugin_agent_event_router=plugin_agent_event_router,
                plugin_conversation_refs=plugin_conversation_refs,
                extension_management_service=extension_management_service,
                plugin_capability_source=plugin_capability_source,
                engine=engine,
                settings=settings,
                tts_client=None,
                runtime_metrics=RuntimeMetrics(),
                public_guard=PublicThinkGuard(
                    enabled=bool(getattr(runtime_config, "PUBLIC_GUARD_ENABLED", False)),
                    max_concurrent_thinks=int(getattr(runtime_config, "MAX_CONCURRENT_THINKS", 2)),
                    daily_think_limit=int(getattr(runtime_config, "DAILY_THINK_LIMIT", 200)),
                    busy_message=str(
                        getattr(
                            runtime_config,
                            "PUBLIC_BUSY_MESSAGE",
                            "当前体验人数较多，请稍后再试。",
                        )
                    ),
                    daily_limit_message=str(
                        getattr(
                            runtime_config,
                            "PUBLIC_DAILY_LIMIT_MESSAGE",
                            "今日体验名额已满，明天再来看看吧。",
                        )
                    ),
                ),
                turn_coordinator=turn_coordinator,
                session_inbox_store=session_inbox_store,
                session_work_queue=session_work_queue,
                job_store=job_store,
                host_tool_jobs=host_tool_jobs,
                host_subagent_jobs=host_subagent_jobs,
                host_execution_jobs=host_execution_jobs,
                host_workflow_jobs=host_workflow_jobs,
                qq_gateway=qq_gateway,
                qq_followup_tasks=qq_followup_tasks,
                config_module=runtime_config,
                logger=self.logger,
            )
            runtime.voice_runtime_service = AkaneVoiceRuntimeService(
                engine=engine,
                settings=settings,
                state_dir=runtime_layout.state_dir,
                instance_id=instance_context.instance_id,
                bot_id=effective_bot_config.bot_id,
                default_character_pack_id=instance_context.character_pack_id,
                tts_client=runtime.tts_client,
                tts_client_resolver=(
                    lambda *, profile_user_id, session_id, character_pack_id: resolve_character_tts_client(
                        engine=engine,
                        profile_user_id=profile_user_id,
                        session_id=session_id,
                        character_pack_id=character_pack_id,
                    )
                ),
                runtime_metrics=runtime.runtime_metrics,
            )
            return runtime
        except Exception:
            if engine is not None:
                try:
                    engine.close()
                except Exception:
                    self.logger.warning("Engine cleanup failed during BotRuntime construction")
            if instance_runtime is not None:
                instance_runtime.release()
            raise


async def _dispatch_host_job_completion(engine: Any, router: Any, job: HostJob) -> HostTurnResult:
    from .tool_continuation import job_followup
    from .host_completion_batch import job_delivery_events

    if not job_followup(job).requires_model:
        recorded = await asyncio.to_thread(_record_host_job_completion, engine, job)
        if not recorded.ok or not job_delivery_events(job):
            return recorded
    return await router.submit(_host_job_completion_request(job))


def _record_host_job_completion(engine: Any, job: HostJob) -> HostTurnResult:
    """Persist an opted-in terminal fact without creating a model/delivery turn."""
    if job.memory_mode != "timeline":
        return HostTurnResult(True, "silent", "")
    request = _host_job_completion_request(job)
    result = engine.record_plugin_timeline_event({
        "source_id": job.completion_event_id,
        "event": {"event_type": request.event_type, "source": request.source,
                  "fields": dict(request.data)},
        "timestamp": int(job.finished_at),
        "user_id": job.owner.session_id,
        "real_user_id": job.owner.profile_user_id,
        "character_pack_id": job.character_pack_id,
    })
    if not isinstance(result, dict) or result.get("ok") is not True:
        return HostTurnResult(False, "failed", str(
            result.get("reason", "timeline_record_failed") if isinstance(result, dict) else "timeline_record_failed"
        ))
    return HostTurnResult(True, "recorded", "")


def _host_job_completion_request(job: HostJob) -> HostJobTurnIntent:
    """Project one durable Job terminal fact into the normal Agent turn path."""

    status = str(job.status or "failed").strip().lower()
    succeeded = status == "succeeded"
    summary = str(job.result_summary or "").strip()[:3_000]
    error = str(job.last_error or "").strip()[:500]
    handles = [
        str(item.get("handle") or "").strip()
        for item in job.artifacts
        if isinstance(item, dict) and str(item.get("handle") or "").strip()
    ]
    handle_text = ", ".join(handles)[:2_000]
    delivery_requests = ", ".join(
        f"{item['handle']}={item['delivery_mode']}"
        for item in job.artifacts
        if isinstance(item, dict) and item.get("handle") and item.get("send_to_user") is True
        and item.get("delivery_mode") in ("file", "voice", "both")
    )[:2_000]
    fields: list[tuple[str, str]] = [
        ("job_id", job.job_id),
        ("capability_id", job.capability_id),
        ("status", status),
        ("finished_at", str(job.finished_at)),
    ]
    if job.turn_id:
        fields.append(("origin_turn_id", job.turn_id))
    if job.tool_call_id:
        fields.append(("tool_call_id", job.tool_call_id))
    task_directory = str(job.payload.get("working_directory") or "").strip()
    if task_directory:
        fields.append(("task_working_directory", task_directory))
    if summary:
        fields.append(("result_summary", summary))
    if handle_text:
        fields.append(("artifact_handles", handle_text))
        fields.append(("artifact_delivery_status", "available_not_delivered"))
        if delivery_requests:
            fields.append(("artifact_delivery_requests", delivery_requests))
        hashes = ", ".join(f"{item['handle']}={item['sha256']}" for item in job.artifacts
                           if isinstance(item, dict) and item.get("handle") and item.get("sha256"))[:2_000]
        if hashes:
            fields.append(("artifact_sha256", hashes))
    if error:
        fields.append(("error", error))
    lines = [
        f"后台任务 {job.capability_id} 已{'完成' if succeeded else '结束'}。",
        f"job_id: {job.job_id}",
        f"status: {status}",
    ]
    if summary:
        lines.append(f"结果摘要：{summary}")
    if task_directory:
        lines.append(f"该任务的执行目录：{task_directory}（不是切换当前会话的工作目录）。")
    if handle_text:
        lines.append(f"可用产物：{handle_text}")
        lines.append("这些产物已登记但尚未由本完成事件确认发送；需要交付时使用当前渠道的正常发送能力。")
        if delivery_requests:
            lines.append(f"原任务请求的交付形式：{delivery_requests}。这是发送意图，不是已发送状态；通过正常渠道发送能力完成交付。")
    if error:
        lines.append(f"失败原因：{error}")
    return HostJobTurnIntent(
        message="\n".join(lines),
        data={key: value for key, value in fields},
        source="host.jobs",
        event_type=f"job.{status}",
        conversation_ref=job.delivery_target,
        trace_id=job.completion_event_id,
        idempotency_key=job.completion_event_id,
        memory_mode=job.memory_mode,
    )


def log_runtime_start_status(runtime: BotRuntime, status: dict[str, Any]) -> None:
    if status.get("status") != "degraded":
        return
    runtime.logger.warning(
        "Bot runtime degraded at startup: %s",
        json.dumps(status, ensure_ascii=False, sort_keys=True),
    )


__all__ = [
    "BotRuntime",
    "BotRuntimeFactory",
    "RuntimeMetrics",
    "log_runtime_start_status",
]
