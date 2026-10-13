"""Plugin generation services and bindings to the existing engine."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..engine import AkaneMemoryEngine
from ..extension_management import ExtensionManagementService, PLUGIN_SELECTION_STATE_FILENAME, PluginSelectionStore
from ..plugin_managed_artifacts import GeneratedFileManagedArtifactSink
from ..plugin_resources import GeneratedFileResourceProvider
from ..plugin_capability_calls import EnginePluginCapabilityProvider
from ..plugin_connections import ModelServicePluginConnectionProvider
from ..plugin_connection_settings import PluginConnectionSettings
from ..execution_resource_policy import ExecutionResourcePolicy
from ..execution_policies import ExecutionPolicyPipeline, PolicySelection
from ..instance_profile import InstanceContext
from ..instance_runtime import InstanceRuntimeLayout
from ..plugin_agent_events import HostAgentEventRouter
from ..plugin_conversation_refs import PluginConversationReferenceAuthority
from ..plugin_generation_candidate import PluginGenerationCandidateBuilder
from ..plugin_generation_runtime import PluginGenerationRuntime
from ..plugin_installation import ManagedPluginArtifactStore
from ..plugin_market import StaticPluginMarket
from ..plugin_tool_bridge import PluginCapabilityToolBridge


@dataclass(frozen=True, slots=True)
class PluginServices:
    plugin_runtime: PluginGenerationRuntime
    plugin_agent_event_router: HostAgentEventRouter
    plugin_conversation_refs: PluginConversationReferenceAuthority
    extension_management_service: ExtensionManagementService
    plugin_capability_source: PluginCapabilityToolBridge


def build_plugin_services(
    *,
    runtime_layout: InstanceRuntimeLayout,
    instance_context: InstanceContext,
    runtime_config: Any,
    execution_policies: tuple[PolicySelection, ...],
) -> PluginServices:
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
        dependency_index_url=str(getattr(runtime_config, "AKANE_PLUGIN_DEPENDENCY_INDEX_URL", "") or "").strip(),
    )
    plugin_candidate_builder = PluginGenerationCandidateBuilder(
        source_resolver=plugin_artifact_store,
        service_bindings_provider=plugin_selection_store.load_service_bindings,
        bootstrap_services=tuple(item.dependency for item in execution_policies),
        project_root=Path(__file__).resolve().parents[2],
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
            or Path(__file__).resolve().parents[2] / ".plugin-market" / "index.json"
        ),
    )
    plugin_capability_source = PluginCapabilityToolBridge(
        plugin_runtime,
        config_base_dir=runtime_layout.users_data_dir,
        conversation_ref_issuer=plugin_conversation_refs.issue,
        result_preview_chars=int(getattr(runtime_config, "AKANE_PLUGIN_RESULT_PREVIEW_CHARS", 16_000)),
    )
    return PluginServices(
        plugin_runtime=plugin_runtime,
        plugin_agent_event_router=plugin_agent_event_router,
        plugin_conversation_refs=plugin_conversation_refs,
        extension_management_service=extension_management_service,
        plugin_capability_source=plugin_capability_source,
    )


def bind_plugin_engine(
    *,
    engine: AkaneMemoryEngine,
    plugins: PluginServices,
    runtime_layout: InstanceRuntimeLayout,
    runtime_config: Any,
    execution_resource_policy: ExecutionResourcePolicy,
    execution_policies: tuple[PolicySelection, ...],
) -> None:
    engine.plugin_observations_provider = plugins.plugin_agent_event_router.prompt_context
    engine.executor_broker.resource_policy = execution_resource_policy
    engine.executor_broker.execution_policies = ExecutionPolicyPipeline(
        execution_policies, source=plugins.plugin_capability_source
    )
    engine.plugin_steering_observer = plugins.plugin_agent_event_router.steering_results
    engine.plugin_skill_roots_provider = plugins.plugin_runtime.skill_roots
    plugins.plugin_runtime.bind_capability_provider(EnginePluginCapabilityProvider(engine))
    plugins.plugin_capability_source.bind_approval_store(engine._get_approval_store())
    plugins.plugin_runtime.bind_connection_provider(
        ModelServicePluginConnectionProvider(
            engine, runtime_config, connection_settings=plugins.extension_management_service.connection_settings
        )
    )
    generated_file_service = engine._get_generated_file_service()
    if generated_file_service is not None:
        plugins.plugin_capability_source.bind_result_sink(GeneratedFileManagedArtifactSink(generated_file_service))
        plugins.plugin_runtime.bind_managed_artifact_sink(GeneratedFileManagedArtifactSink(generated_file_service))
        plugins.plugin_runtime.bind_resource_provider(
            GeneratedFileResourceProvider(generated_file_service, work_root=runtime_layout.run_dir / "plugin-inputs")
        )
