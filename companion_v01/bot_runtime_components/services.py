"""Conversation admission and background job services over one engine store."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..async_task_supervisor import AsyncTaskSupervisor
from ..bot_profile import BotConfig
from ..desktop_satellite import DesktopSatelliteService
from ..durable_session_queue import DurableSessionWorkQueue
from ..session_inbox import SessionInboxStore
from ..host_jobs import HostJobStore
from ..host_execution_jobs import HostExecutionJobRuntime
from ..host_tool_jobs import HostToolJobRuntime
from ..plugin_tasks import HostTaskProvider
from ..host_subagent_jobs import HostSubagentJobRuntime
from ..subagent_runtime import InProcessSubagentProvider, SubagentProviderRegistry
from ..subagent_engine import EngineSubagentDriver
from ..tool_handlers.subagent import SpawnSubagentToolHandler, ManageSubagentsToolHandler
from ..host_workflow_jobs import HostWorkflowJobRuntime, WorkflowJobAssetStore
from ..engine import AkaneMemoryEngine
from ..instance_profile import InstanceContext
from ..instance_runtime import InstanceRuntimeLayout
from ..deployment_security import InstanceDeploymentSecurity
from ..plugin_notifications import NullNotificationPort, QQTextNotificationPort
from ..local_workflow_runners.comfyui import ComfyUiWorkflowRunner
from companion_v01.channels.qq.gateway import NapCatQQGateway
from companion_v01.channels.qq.delivery.tool_port import QQToolDeliveryPort
from ..turn_coordination import TurnCoordinator

from .plugins import PluginServices


@dataclass(frozen=True, slots=True)
class ConversationServices:
    qq_gateway: NapCatQQGateway | None
    qq_followup_tasks: AsyncTaskSupervisor | None
    turn_coordinator: TurnCoordinator
    session_inbox_store: SessionInboxStore
    session_work_queue: DurableSessionWorkQueue


@dataclass(frozen=True, slots=True)
class HostJobServices:
    job_store: HostJobStore
    host_tool_jobs: HostToolJobRuntime
    host_subagent_jobs: HostSubagentJobRuntime
    host_execution_jobs: HostExecutionJobRuntime
    host_workflow_jobs: HostWorkflowJobRuntime


def build_conversation_services(
    *,
    engine: AkaneMemoryEngine,
    plugins: PluginServices,
    runtime_layout: InstanceRuntimeLayout,
    instance_context: InstanceContext,
    effective_bot_config: BotConfig,
    runtime_config: Any,
    deployment_security: InstanceDeploymentSecurity,
    satellite_service: DesktopSatelliteService,
) -> ConversationServices:
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
        plugins.plugin_runtime.bind_notification_port(QQTextNotificationPort(qq_gateway))
    else:
        qq_gateway = None
        qq_followup_tasks = None
        plugins.plugin_runtime.bind_notification_port(NullNotificationPort())
    from ..memory_read_policy import MemoryReadPolicy
    from ..owner_private_session import OwnerPrivateSessionPolicy

    engine.owner_private_policy = OwnerPrivateSessionPolicy(
        enabled=bool(getattr(runtime_config, "OWNER_PRIVATE_SESSION_SHARED", False)),
        master_qq=qq_gateway.master_qq
        if qq_gateway is not None
        else str(getattr(runtime_config, "MASTER_QQ", "") or ""),
        references=plugins.plugin_conversation_refs,
        manager=engine.memcore_manager,
        store=engine.store,
    )
    engine.memory_read_policy = MemoryReadPolicy(
        store=engine.store,
        bot_id=effective_bot_config.bot_id,
        master_qq=qq_gateway.master_qq
        if qq_gateway is not None
        else str(getattr(runtime_config, "MASTER_QQ", "") or ""),
        enabled=lambda config_view=runtime_config: bool(
            getattr(config_view, "MEMORY_CROSS_CONVERSATION_ENABLED", True)
        ),
        resolve_origin=plugins.plugin_conversation_refs.resolve,
        key_path=runtime_layout.state_dir / "memory_read_cursor.key",
        private_policy=engine.owner_private_policy,
    )
    turn_coordinator = TurnCoordinator()
    engine.turn_coordinator = turn_coordinator
    if qq_gateway is not None:
        from ..owner_private_group_memory import OwnerPrivateGroupMemory

        engine.owner_private_group_memory = OwnerPrivateGroupMemory(
            engine=engine,
            gateway=qq_gateway,
            coordinator=turn_coordinator,
            bot_id=effective_bot_config.bot_id,
            enabled=bool(getattr(runtime_config, "OWNER_PRIVATE_GROUP_MEMORY_MIRROR", False)),
        )
        engine.memcore_manager.owner_private_group_memory = engine.owner_private_group_memory
    from ..local_music_control import LocalMusicControlService

    engine.local_music_control = LocalMusicControlService(satellite_service, effective_bot_config.bot_id)
    session_inbox_store = SessionInboxStore(engine.store.db_path)
    engine.session_inbox_store = session_inbox_store
    session_work_queue = DurableSessionWorkQueue(session_inbox_store)
    plugins.plugin_agent_event_router.bind_runtime(session_work_queue, turn_coordinator)
    return ConversationServices(
        qq_gateway=qq_gateway,
        qq_followup_tasks=qq_followup_tasks,
        turn_coordinator=turn_coordinator,
        session_inbox_store=session_inbox_store,
        session_work_queue=session_work_queue,
    )


def build_host_job_services(
    *,
    engine: AkaneMemoryEngine,
    plugins: PluginServices,
    runtime_layout: InstanceRuntimeLayout,
) -> HostJobServices:
    job_store = HostJobStore(engine.store.db_path)
    engine.job_store = job_store
    plugins.plugin_runtime.bind_task_provider(
        HostTaskProvider(job_store, generation_active=plugins.plugin_runtime.generation_active)
    )
    host_tool_jobs = HostToolJobRuntime(
        engine=engine,
        store=job_store,
        background_tasks=engine.background_tasks,
        conversation_ref_issuer=plugins.plugin_conversation_refs.issue,
    )
    engine.host_tool_jobs = host_tool_jobs
    bind_revocation = getattr(plugins.plugin_runtime, "bind_capability_revocation_listener", None)
    if callable(bind_revocation):
        bind_revocation(host_tool_jobs.revoke_capabilities)
    subagent_providers = SubagentProviderRegistry()
    subagent_driver = EngineSubagentDriver(engine)
    subagent_providers.register(InProcessSubagentProvider(subagent_driver))
    host_subagent_jobs = HostSubagentJobRuntime(
        store=job_store,
        providers=subagent_providers,
        provider_name="in_process",
        background_tasks=engine.background_tasks,
        completion_publisher=host_tool_jobs.publish_completion,
        task_controller=subagent_driver.control,
        completion_work=host_tool_jobs.completion_work,
        memory_deleter=getattr(getattr(engine, "memcore_manager", None), "forget_subagent", None),
    )
    engine.host_subagent_jobs = host_subagent_jobs
    engine.tool_handlers["spawn_subagent"] = SpawnSubagentToolHandler(
        engine=engine, runtime=host_subagent_jobs, conversation_ref_issuer=plugins.plugin_conversation_refs.issue
    )
    engine.tool_handlers["manage_subagents"] = ManageSubagentsToolHandler(runtime=host_subagent_jobs)
    host_execution_jobs = HostExecutionJobRuntime(
        store=job_store,
        conversation_ref_issuer=plugins.plugin_conversation_refs.issue,
        completion_publisher=host_tool_jobs.publish_completion,
        completion_work=host_tool_jobs.completion_work,
        turn_coordinator=getattr(engine, "turn_coordinator", None),
    )
    exec_run_handler = engine.tool_handlers.get("exec_run")
    for name in ("exec_run", "exec_status", "exec_cancel"):
        handler = engine.tool_handlers.get(name)
        if handler is not None:
            handler.bind_job_runtime(host_execution_jobs)
    if exec_run_handler is not None:
        host_execution_jobs.bind_provider(exec_run_handler.execution_provider)
    engine.host_execution_jobs = host_execution_jobs
    host_workflow_jobs = HostWorkflowJobRuntime(
        store=job_store,
        asset_store=WorkflowJobAssetStore(runtime_layout.users_data_dir / "_runtime" / "workflow_jobs"),
        workflow_runner=ComfyUiWorkflowRunner(config_base_dir=runtime_layout.users_data_dir),
        background_tasks=engine.background_tasks,
        executor_broker=engine.executor_broker,
    )
    engine.host_workflow_jobs = host_workflow_jobs
    return HostJobServices(
        job_store=job_store,
        host_tool_jobs=host_tool_jobs,
        host_subagent_jobs=host_subagent_jobs,
        host_execution_jobs=host_execution_jobs,
        host_workflow_jobs=host_workflow_jobs,
    )
