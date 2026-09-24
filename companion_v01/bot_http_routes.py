"""One reusable HTTP surface for every configured BotRuntime."""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Request

from .mcp_stdio_discoverer import McpToolDiscoverer
from .routes.capabilities import build_capabilities_router
from .routes.control_center import build_control_center_router, build_control_center_snapshot_runtime_providers
from .routes.core import build_core_router
from .routes.desktop_pet import build_desktop_pet_router
from .routes.gifts import build_gifts_router
from .routes.model_services import build_model_services_router
from .routes.petdesk import build_petdesk_router
from .routes.plugins import build_plugins_router
from .routes.sessions import build_sessions_router
from .routes.system import build_system_router
from .routes.think import build_think_router
from .routes.voice import build_voice_router
from .routes.scene import build_scene_router
from .scene.composition import compose_room


def build_bot_runtime_routers(
    *,
    runtime: Any,
    route_prefix: str,
    resolve_identity_from_query: Callable[[Request], tuple[str, str]],
    resolve_identity_from_payload: Callable[[dict], tuple[str, str]],
    log_event: Callable[..., None],
) -> tuple[APIRouter, ...]:
    """Build the same route set for any runtime; configuration supplies differences."""

    engine = runtime.engine
    config_module = runtime.config_module
    runtime_metrics = runtime.runtime_metrics
    layout = runtime.runtime_layout
    satellite_service = getattr(runtime, "desktop_satellite_service", None)
    agent_event_router = getattr(runtime, "plugin_agent_event_router", None)
    workflow_job_runtime = getattr(runtime, "host_workflow_jobs", None)
    desktop_delivery = (
        (
            lambda frame, service=satellite_service, bot_id=runtime.bot_id: service.deliver_agent_frame(
                frame,
                bot_id=bot_id,
            )
        )
        if satellite_service is not None
        and callable(getattr(satellite_service, "deliver_agent_frame", None))
        else None
    )

    def desktop_agent_event_available() -> bool:
        snapshot = satellite_service.diagnostics()
        return (
            bool(snapshot.get("desktopUiConnected"))
            and str(snapshot.get("desktopUiBotId") or snapshot.get("activeBotId") or "") == runtime.bot_id
        )

    return (
        build_scene_router(service=compose_room(runtime, route_prefix), admin_auth=runtime.admin_write_auth),
        build_core_router(
            engine=engine,
            config_module=config_module,
            instance_runtime=runtime.instance_runtime,
            resolve_identity_from_query=resolve_identity_from_query,
            runtime_metrics=runtime_metrics,
            public_guard=runtime.public_guard,
            route_prefix=route_prefix,
        ),
        build_system_router(
            engine=engine,
            runtime_metrics=runtime_metrics,
            public_guard=runtime.public_guard,
            log_event=log_event,
            admin_auth=runtime.admin_write_auth,
        ),
        build_think_router(
            engine=engine,
            public_guard=runtime.public_guard,
            runtime_metrics=runtime_metrics,
            log_event=log_event,
            turn_coordinator=runtime.turn_coordinator,
            session_work_queue=runtime.session_work_queue,
            plugin_event_broker_provider=lambda runtime=runtime: runtime.plugin_event_broker,
            plugin_agent_event_handler_registrar=(
                agent_event_router.register_channel
                if desktop_delivery is not None
                and callable(getattr(agent_event_router, "register_channel", None))
                else None
            ),
            plugin_turn_router=agent_event_router,
            plugin_conversation_ref_issuer=runtime.plugin_conversation_refs.issue_desktop,
            desktop_agent_frame_delivery=desktop_delivery,
            desktop_agent_event_available=(
                desktop_agent_event_available
                if desktop_delivery is not None
                else None
            ),
        ),
        build_desktop_pet_router(
            engine=engine,
            config_module=config_module,
            runtime_metrics=runtime_metrics,
            log_event=log_event,
            resolve_identity_from_query=resolve_identity_from_query,
            resolve_identity_from_payload=resolve_identity_from_payload,
            admin_auth=runtime.admin_write_auth,
        ),
        build_petdesk_router(
            engine=engine,
            config_module=config_module,
            tts_client=runtime.tts_client,
            settings=runtime.settings,
            character_resources=runtime.desktop_pet_character_resources,
            runtime_metrics=runtime_metrics,
            public_guard=runtime.public_guard,
            log_event=log_event,
            capability_config_base_dir=layout.users_data_dir,
            route_prefix=route_prefix,
        ),
        build_gifts_router(
            engine=engine,
            runtime_metrics=runtime_metrics,
            log_event=log_event,
            resolve_identity_from_query=resolve_identity_from_query,
            resolve_identity_from_payload=resolve_identity_from_payload,
        ),
        build_sessions_router(
            engine=engine,
            runtime_metrics=runtime_metrics,
            log_event=log_event,
            resolve_identity_from_query=resolve_identity_from_query,
            resolve_identity_from_payload=resolve_identity_from_payload,
        ),
        build_voice_router(
            engine=engine,
            config_module=config_module,
            tts_client=runtime.tts_client,
            settings=runtime.settings,
            runtime_metrics=runtime_metrics,
            log_event=log_event,
            capability_config_base_dir=layout.users_data_dir,
            realtime_asr_coordinator_factory=(
                runtime.voice_runtime_service.create_coordinator
                if getattr(runtime, "voice_runtime_service", None) is not None
                else None
            ),
            realtime_asr_call_factory=(
                getattr(runtime.voice_runtime_service, "open_call", None)
                if getattr(runtime, "voice_runtime_service", None) is not None
                else None
            ),
        ),
        build_control_center_router(
            runtime_metrics=runtime_metrics,
            log_event=log_event,
            resolve_identity_from_query=resolve_identity_from_query,
            snapshot_runtime_providers=build_control_center_snapshot_runtime_providers(
                engine=engine,
                config_module=config_module,
                runtime_metrics=runtime_metrics,
                public_guard=runtime.public_guard,
            ),
            settings_override_store=runtime.settings_override_store,
            config_module=config_module,
            admin_auth=runtime.admin_write_auth,
        ),
        build_model_services_router(
            store=runtime.model_service_config_store,
            config_module=config_module,
            engine=engine,
            reload_model_services=runtime.reload_model_services,
            runtime_metrics=runtime_metrics,
            log_event=log_event,
            admin_auth=runtime.admin_write_auth,
        ),
        build_capabilities_router(
            engine=engine,
            config_module=config_module,
            tts_client=runtime.tts_client,
            settings=runtime.settings,
            runtime_metrics=runtime_metrics,
            log_event=log_event,
            resolve_identity_from_query=resolve_identity_from_query,
            background_tasks=getattr(engine, "background_tasks", None),
            workflow_job_runtime=workflow_job_runtime,
            mcp_tool_discoverer=McpToolDiscoverer(),
            capability_config_base_dir=layout.users_data_dir,
            workflow_runner=getattr(workflow_job_runtime, "workflow_runner", None),
        ),
        build_plugins_router(
            extension_management_service=runtime.extension_management_service,
            admin_auth=runtime.admin_write_auth,
            job_store=getattr(runtime, "job_store", None),
            job_control=getattr(runtime, "control_job", None),
        ),
    )


__all__ = ["build_bot_runtime_routers"]
