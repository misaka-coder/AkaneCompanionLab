"""Configuration and ordered assembly of one BotRuntime."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from ..bot_profile import BotConfig, bot_config_from_instance_context
from ..deployment_security import resolve_instance_deployment_security
from ..desktop_pet_character_resources import DesktopPetCharacterResourceService
from ..desktop_satellite import DesktopSatelliteService
from ..engine import AkaneMemoryEngine
from ..instance_profile import instance_context_from_bot_config, resolve_instance_context
from ..instance_runtime import InstanceRuntimeLayout, InstanceRuntimeLease, bind_instance_runtime
from ..model_service_config import ModelServiceConfigStore
from ..execution_resource_policy import ExecutionResourcePolicy
from ..execution_policies import load_execution_policies
from ..public_guard import PublicThinkGuard
from companion_v01.channels.qq.profiles import QQChannelDeploymentProfile
from ..resource_manifest import ResourceManifest
from ..runtime_settings import BotSettingsView
from ..settings_overrides import (
    RuntimeConfigView,
    SettingsOverrideStore,
    load_saved_overrides,
)
from ..tts_provider_runtime import resolve_character_tts_client
from ..voice_runtime import AkaneVoiceRuntimeService
from .runtime import BotRuntime, RuntimeMetrics

from .plugins import build_plugin_services, bind_plugin_engine
from .services import build_conversation_services, build_host_job_services


def _load_runtime_settings(
    *,
    config_module: Any,
    logger: logging.Logger,
    runtime_layout: InstanceRuntimeLayout,
    bot_config: BotConfig | None,
    effective_bot_config: BotConfig,
    settings_overrides: Mapping[str, Any] | None,
) -> tuple[Any, BotSettingsView, ModelServiceConfigStore, SettingsOverrideStore]:
    model_store = ModelServiceConfigStore(runtime_layout.config_dir / "model_service.json")
    try:
        saved_model_settings = model_store.load()
    except Exception as exc:
        saved_model_settings = None
        logger.warning("Model service config ignored: %s", type(exc).__name__)
    settings_store = SettingsOverrideStore(runtime_layout.config_dir / "settings_overrides.json")
    saved_overrides = load_saved_overrides(
        settings_store,
        on_error=lambda exc: logger.warning("Settings override ignored: %s", type(exc).__name__),
    )
    if bot_config is None:
        # The implicit single Bot still has process-level config readers.
        # Explicit Bots keep their overrides in the view below.
        for key, value in saved_overrides.items():
            setattr(config_module, key, value)
        runtime_config = config_module
    else:
        runtime_config = RuntimeConfigView(
            config_module,
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
    return runtime_config, settings, model_store, settings_store


def _bind_voice_runtime(runtime: BotRuntime) -> None:
    engine = runtime.engine
    runtime.voice_runtime_service = AkaneVoiceRuntimeService(
        engine=engine,
        settings=runtime.settings,
        state_dir=runtime.runtime_layout.state_dir,
        instance_id=runtime.instance_context.instance_id,
        bot_id=runtime.bot_config.bot_id,
        default_character_pack_id=runtime.instance_context.character_pack_id,
        tts_client=runtime.tts_client,
        tts_client_resolver=lambda *, profile_user_id, session_id, character_pack_id: resolve_character_tts_client(
            engine=engine,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        ),
        runtime_metrics=runtime.runtime_metrics,
    )


class BotRuntimeFactory:
    """Construct one BotRuntime from the current process defaults."""

    def __init__(self, *, config_module: Any, assets_dir: Path, logger: logging.Logger | None = None) -> None:
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
                data_root=Path(data_root), selected_instance_id=selected_instance_id
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
                instance_context, data_root=Path(data_root), explicit_data_root=explicit_data_root
            )
            runtime_layout = instance_runtime.layout
            resources = ResourceManifest(self.assets_dir)
            route_prefix = f"/api/bots/{effective_bot_config.bot_id}"
            character_resources = DesktopPetCharacterResourceService(
                characters_dir=runtime_layout.characters_dir,
                public_prefix=f"{route_prefix}/desktop-pet-character-packs",
            )
            runtime_config, settings, model_store, settings_store = _load_runtime_settings(
                config_module=self.config_module,
                logger=self.logger,
                runtime_layout=runtime_layout,
                bot_config=bot_config,
                effective_bot_config=effective_bot_config,
                settings_overrides=settings_overrides,
            )
            deployment_security = resolve_instance_deployment_security(
                instance_context, runtime_config, qq_channel_profile=qq_channel_profile
            )
            if deployment_security.qq.enabled and deployment_security.qq.onebot_shared_data_root:
                deployment_security = replace(
                    deployment_security,
                    qq=replace(deployment_security.qq, local_data_root=str(runtime_layout.data_root)),
                )
            satellite_service = (
                desktop_satellite_service
                if desktop_satellite_service is not None
                else DesktopSatelliteService(
                    instance_id=instance_context.instance_id, token=deployment_security.satellite.token
                )
            )
            satellite_offer_source = satellite_service.for_bot(
                bot_id=effective_bot_config.bot_id, memory_space_id=effective_bot_config.memory_space_id
            )
            execution_resource_policy = ExecutionResourcePolicy.from_config(
                getattr(runtime_config, "AKANE_EXECUTION_RESOURCE_POLICY", "{}")
            )
            execution_policies = load_execution_policies(getattr(runtime_config, "AKANE_EXECUTION_POLICIES", "[]"))
            plugins = build_plugin_services(
                runtime_layout=runtime_layout,
                instance_context=instance_context,
                runtime_config=runtime_config,
                execution_policies=execution_policies,
            )
            engine = AkaneMemoryEngine(
                runtime_layout.engine_dir,
                resource_manifest=resources,
                desktop_pet_character_resources=character_resources,
                instance_context=instance_context,
                runtime_layout=runtime_layout,
                plugin_capability_source=plugins.plugin_capability_source,
                extension_management_service=plugins.extension_management_service,
                stable_system_blocks_provider=plugins.plugin_runtime.stable_system_prompt_blocks,
                qq_channel_config=deployment_security.qq,
                capability_offer_source=satellite_offer_source,
                settings=settings,
                user_assets_public_prefix=f"{route_prefix}/user-assets",
            )
            bind_plugin_engine(
                engine=engine,
                plugins=plugins,
                runtime_layout=runtime_layout,
                runtime_config=runtime_config,
                execution_resource_policy=execution_resource_policy,
                execution_policies=execution_policies,
            )
            conversations = build_conversation_services(
                engine=engine,
                plugins=plugins,
                runtime_layout=runtime_layout,
                instance_context=instance_context,
                effective_bot_config=effective_bot_config,
                runtime_config=runtime_config,
                deployment_security=deployment_security,
                satellite_service=satellite_service,
            )
            jobs = build_host_job_services(engine=engine, plugins=plugins, runtime_layout=runtime_layout)
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
                plugin_runtime=plugins.plugin_runtime,
                plugin_agent_event_router=plugins.plugin_agent_event_router,
                plugin_conversation_refs=plugins.plugin_conversation_refs,
                extension_management_service=plugins.extension_management_service,
                plugin_capability_source=plugins.plugin_capability_source,
                engine=engine,
                settings=settings,
                tts_client=None,
                runtime_metrics=RuntimeMetrics(),
                public_guard=PublicThinkGuard(
                    enabled=bool(getattr(runtime_config, "PUBLIC_GUARD_ENABLED", False)),
                    max_concurrent_thinks=int(getattr(runtime_config, "MAX_CONCURRENT_THINKS", 2)),
                    daily_think_limit=int(getattr(runtime_config, "DAILY_THINK_LIMIT", 200)),
                    busy_message=str(getattr(runtime_config, "PUBLIC_BUSY_MESSAGE", "当前体验人数较多，请稍后再试。")),
                    daily_limit_message=str(
                        getattr(runtime_config, "PUBLIC_DAILY_LIMIT_MESSAGE", "今日体验名额已满，明天再来看看吧。")
                    ),
                ),
                turn_coordinator=conversations.turn_coordinator,
                session_inbox_store=conversations.session_inbox_store,
                session_work_queue=conversations.session_work_queue,
                job_store=jobs.job_store,
                host_tool_jobs=jobs.host_tool_jobs,
                host_subagent_jobs=jobs.host_subagent_jobs,
                host_execution_jobs=jobs.host_execution_jobs,
                host_workflow_jobs=jobs.host_workflow_jobs,
                qq_gateway=conversations.qq_gateway,
                qq_followup_tasks=conversations.qq_followup_tasks,
                config_module=runtime_config,
                logger=self.logger,
            )
            _bind_voice_runtime(runtime)
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
