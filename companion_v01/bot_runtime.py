"""Repeatable construction and lifecycle ownership for one Akane Bot."""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

from services.tts_client import EdgeTTSClient

from .async_task_supervisor import AsyncTaskSupervisor
from .bot_profile import BotConfig, bot_config_from_instance_context
from .deployment_security import InstanceDeploymentSecurity, resolve_instance_deployment_security
from .desktop_pet_character_resources import DesktopPetCharacterResourceService
from .desktop_satellite import DesktopSatelliteService
from .engine import AkaneMemoryEngine
from .instance_profile import InstanceContext, instance_context_from_bot_config, resolve_instance_context
from .instance_runtime import InstanceRuntimeLease, bind_instance_runtime
from .model_service_config import (
    ModelServiceConfigStore,
    ModelServiceSettings,
)
from .plugin_contribution_policy import TrustedStatefulPluginContributionPolicy
from .plugin_host import PluginHost
from .plugin_managed_artifacts import GeneratedFileManagedArtifactSink
from .plugin_notifications import NullNotificationPort, QQTextNotificationPort
from .plugin_reasoning import EnginePluginReasoningPort
from .plugin_storage import InstancePluginStorageService
from .plugin_tool_bridge import PluginCapabilityToolBridge
from .public_guard import PublicThinkGuard
from .qq_channel_profiles import QQChannelDeploymentProfile
from .qq_gateway import NapCatQQGateway
from .resource_manifest import ResourceManifest
from .runtime_settings import BotSettingsView
from .settings_overrides import (
    RuntimeConfigView,
    SettingsOverrideStore,
    load_and_apply_saved_overrides,
    load_saved_overrides,
)
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
    plugin_host: PluginHost
    plugin_capability_source: PluginCapabilityToolBridge
    engine: AkaneMemoryEngine
    settings: BotSettingsView
    tts_client: EdgeTTSClient
    runtime_metrics: RuntimeMetrics
    public_guard: PublicThinkGuard
    qq_gateway: NapCatQQGateway | None
    qq_followup_tasks: AsyncTaskSupervisor | None
    config_module: Any = field(repr=False)
    logger: logging.Logger = field(repr=False)
    plugin_command_broker: Any = field(default=None, init=False, repr=False)
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

    def bind_app_state(self, app: Any) -> None:
        app.state.akane_bot_runtime = self
        app.state.akane_instance_context = self.instance_context
        app.state.akane_instance_runtime = self.instance_runtime
        app.state.akane_deployment_security = self.deployment_security
        app.state.akane_desktop_satellite = self.desktop_satellite_service
        app.state.akane_plugin_host = self.plugin_host

    async def start(self) -> dict[str, Any]:
        if self._stop_status is not None:
            return {"status": "unavailable", "reason": "bot_runtime_stopped", "bot_id": self.bot_id}
        if self._started:
            return {"status": "active", "reason": "already_started", "bot_id": self.bot_id}

        plugin_status = await self.plugin_host.start()
        self.plugin_command_broker = self.plugin_host.build_qq_command_broker()
        self._started = True
        status = "degraded" if plugin_status.get("status") == "degraded" else "active"
        return {
            "status": status,
            "reason": "plugin_host_degraded" if status == "degraded" else "",
            "bot_id": self.bot_id,
            "plugin_status": plugin_status,
        }

    async def stop(self) -> dict[str, Any]:
        if self._stop_status is not None:
            return dict(self._stop_status)

        failures: list[str] = []
        followup_status: dict[str, Any] = {"status": "not_configured"}
        plugin_status: dict[str, Any] = {"status": "not_started"}
        voice_status: dict[str, Any] = {"status": "not_configured"}
        engine_status: dict[str, Any] = {"status": "not_started"}

        if self.qq_followup_tasks is not None:
            try:
                followup_status = await self.qq_followup_tasks.close(timeout=10.0)
                if followup_status.get("status") != "stopped":
                    failures.append("qq_followup_shutdown_incomplete")
            except Exception:
                failures.append("qq_followup_shutdown_failed")
                followup_status = {"status": "error", "reason": "qq_followup_shutdown_failed"}

        try:
            plugin_status = await self.plugin_host.stop()
            if int(plugin_status.get("close_failure_count") or 0) > 0:
                failures.append("plugin_host_close_incomplete")
        except Exception:
            failures.append("plugin_host_shutdown_failed")
            plugin_status = {"status": "error", "reason": "plugin_host_shutdown_failed"}

        self.plugin_command_broker = None
        if self.voice_runtime_service is not None:
            try:
                voice_status = self.voice_runtime_service.close()
                if voice_status.get("status") != "stopped":
                    failures.append("voice_runtime_shutdown_incomplete")
            except Exception:
                failures.append("voice_runtime_shutdown_failed")
                voice_status = {"status": "error", "reason": "voice_runtime_shutdown_failed"}
        try:
            engine_status = self.engine.close()
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
            "followup_status": followup_status,
            "plugin_status": plugin_status,
            "voice_status": voice_status,
            "engine_status": engine_status,
        }
        return dict(self._stop_status)

    def install_qq_task_completion_notifications(self) -> None:
        if self.qq_gateway is None:
            return
        task_worker = getattr(self.engine, "task_worker_service", None)
        if task_worker is None:
            return
        task_worker.on_task_completed = self._handle_qq_task_completion

    def _handle_qq_task_completion(
        self,
        *,
        task_id: str,
        profile_user_id: str,
        session_id: str,
        task: dict[str, Any],
        handoff: dict[str, Any],
    ) -> None:
        if self.qq_gateway is None:
            return
        if not bool(getattr(self.config_module, "QQ_BACKGROUND_COMPLETION_NOTIFY_ENABLED", True)):
            return
        task_service = getattr(self.engine, "task_workspace_service", None)
        current_task = task_service.get_task(task_id) if task_service is not None else task
        metadata = dict((current_task or task).get("metadata") or {})
        delivery = metadata.get("delivery") if isinstance(metadata.get("delivery"), dict) else {}
        if str(delivery.get("client") or "").strip() != "qq_text":
            return
        if delivery.get("completed_notified_at"):
            return
        context = self.qq_gateway.context_from_delivery_context(delivery)
        if context is None:
            return

        delivery["completed_notified_at"] = int(time.time())
        metadata["delivery"] = delivery
        if task_service is not None:
            task_service.update_task(task_id=task_id, metadata=metadata, timestamp=int(time.time()))

        artifact_targets = self._qq_completion_artifact_targets(current_task or task, handoff)
        should_send = str((handoff or {}).get("next_action") or "").strip().lower() == "send_to_user"
        if should_send and artifact_targets:
            sent_count = self._send_qq_completion_files(
                context=context,
                profile_user_id=profile_user_id,
                session_id=session_id,
                targets=artifact_targets,
            )
            if sent_count > 0:
                self.qq_gateway.send_reply(context, "做好啦，我把结果发给你了。")
                return

        labels = self._qq_completion_artifact_labels(current_task or task, handoff)
        if labels:
            self.qq_gateway.send_reply(
                context,
                "做好啦。现在有这些结果可以发给你："
                + "、".join(labels[:6])
                + "。你要哪份就直接说“发给我”或告诉我编号。",
            )
        else:
            self.qq_gateway.send_reply(context, "做好啦，后台任务已经处理完了。")

    @staticmethod
    def _qq_completion_artifact_targets(task: dict[str, Any], handoff: dict[str, Any]) -> list[str]:
        targets: list[str] = []
        raw_items = (handoff or {}).get("artifacts") if isinstance(handoff, dict) else []
        if not isinstance(raw_items, list) or not raw_items:
            raw_items = task.get("artifacts") if isinstance(task.get("artifacts"), list) else []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            for key in ("generated_handle", "generated_id", "id", "handle"):
                value = str(item.get(key) or "").strip()
                if value and value not in targets:
                    targets.append(value)
                    break
        return targets[:8]

    @staticmethod
    def _qq_completion_artifact_labels(task: dict[str, Any], handoff: dict[str, Any]) -> list[str]:
        labels: list[str] = []
        raw_items = (handoff or {}).get("artifacts") if isinstance(handoff, dict) else []
        if not isinstance(raw_items, list) or not raw_items:
            raw_items = task.get("artifacts") if isinstance(task.get("artifacts"), list) else []
        for item in raw_items:
            if isinstance(item, dict):
                artifact_id = str(item.get("id") or item.get("generated_handle") or item.get("handle") or "").strip()
                title = str(item.get("title") or "").strip()
                kind = str(item.get("kind") or "").strip()
                label = artifact_id or title
                if title and artifact_id and title != artifact_id:
                    label = f"{artifact_id}({title})"
                if kind and label:
                    label = f"{label}/{kind}"
            else:
                label = str(item or "").strip()
            if label and label not in labels:
                labels.append(label[:160])
        return labels[:8]

    def _send_qq_completion_files(
        self,
        *,
        context: Any,
        profile_user_id: str,
        session_id: str,
        targets: list[str],
    ) -> int:
        if self.qq_gateway is None:
            return 0
        generated_file_service = self.engine._get_generated_file_service()
        if generated_file_service is None:
            return 0
        result = generated_file_service.send_file(
            profile_user_id=profile_user_id,
            session_id=session_id,
            targets=targets,
            timestamp=int(time.time()),
        )
        if not bool(result.get("ok")):
            return 0
        sent_count = 0
        for file_ref in list(result.get("files") or []):
            if not isinstance(file_ref, dict):
                continue
            send_result = self.qq_gateway.send_file(
                context,
                file_path=str(file_ref.get("absolute_path") or ""),
                name=str(file_ref.get("name") or file_ref.get("title") or ""),
            )
            generated_id = str(file_ref.get("generated_id") or "").strip()
            if generated_id:
                self.engine.mark_generated_file_delivery(
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    generated_id=generated_id,
                    delivery_status="sent" if send_result.get("ok") else "failed",
                    timestamp=int(time.time()),
                )
            if send_result.get("ok"):
                sent_count += 1
        return sent_count


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
            plugin_host = PluginHost(
                instance_context.plugins,
                contribution_policy=TrustedStatefulPluginContributionPolicy(),
            )
            plugin_capability_source = PluginCapabilityToolBridge(
                plugin_host,
                config_base_dir=runtime_layout.users_data_dir,
            )
            engine = AkaneMemoryEngine(
                runtime_layout.engine_dir,
                resource_manifest=resources,
                desktop_pet_character_resources=character_resources,
                instance_context=instance_context,
                runtime_layout=runtime_layout,
                plugin_capability_source=plugin_capability_source,
                stable_system_blocks_provider=plugin_host.stable_system_prompt_blocks,
                qq_channel_config=deployment_security.qq,
                capability_offer_source=satellite_offer_source,
                settings=settings,
                user_assets_public_prefix=f"{route_prefix}/user-assets",
            )
            plugin_host.bind_reasoning_port(EnginePluginReasoningPort(engine))
            generated_file_service = engine._get_generated_file_service()
            if generated_file_service is not None:
                plugin_host.bind_managed_artifact_sink(GeneratedFileManagedArtifactSink(generated_file_service))
            plugin_host.bind_plugin_storage_service(
                InstancePluginStorageService(
                    data_root=runtime_layout.data_root,
                    instance_id=instance_context.instance_id,
                )
            )

            qq_gateway: NapCatQQGateway | None
            qq_followup_tasks: AsyncTaskSupervisor | None
            if deployment_security.qq.enabled:
                qq_gateway = NapCatQQGateway(
                    state_path=runtime_layout.state_dir / "qq_gateway_state.json",
                    channel_config=deployment_security.qq,
                    default_character_pack_id=instance_context.character_pack_id,
                    wake_words=effective_bot_config.wake_words,
                )
                qq_followup_tasks = AsyncTaskSupervisor(name=f"qq-followups:{instance_context.instance_id}")
                plugin_host.bind_notification_port(QQTextNotificationPort(qq_gateway))
            else:
                qq_gateway = None
                qq_followup_tasks = None
                plugin_host.bind_notification_port(NullNotificationPort())

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
                plugin_host=plugin_host,
                plugin_capability_source=plugin_capability_source,
                engine=engine,
                settings=settings,
                tts_client=EdgeTTSClient(
                    voice=settings.tts_voice,
                    rate=settings.tts_rate,
                    volume=settings.tts_volume,
                    pitch=settings.tts_pitch,
                ),
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
                runtime_metrics=runtime.runtime_metrics,
            )
            runtime.install_qq_task_completion_notifications()
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
