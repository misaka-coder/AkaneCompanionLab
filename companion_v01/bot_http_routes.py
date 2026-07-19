"""One reusable HTTP surface for every configured BotRuntime."""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Request

from .local_workflow_runners.comfyui import ComfyUiWorkflowRunner
from .mcp_stdio_discoverer import McpStdioToolDiscoverer
from .routes.capabilities import build_capabilities_router
from .routes.control_center import build_control_center_router, build_control_center_snapshot_runtime_providers
from .routes.core import build_core_router
from .routes.desktop_pet import build_desktop_pet_router
from .routes.gifts import build_gifts_router
from .routes.model_services import build_model_services_router
from .routes.petdesk import build_petdesk_router
from .routes.plugins import build_plugins_router
from .routes.reminders import build_reminders_router
from .routes.sessions import build_sessions_router
from .routes.system import build_system_router
from .routes.think import build_think_router
from .routes.voice import build_voice_router


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
    return (
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
        ),
        build_desktop_pet_router(
            engine=engine,
            config_module=config_module,
            runtime_metrics=runtime_metrics,
            log_event=log_event,
            resolve_identity_from_query=resolve_identity_from_query,
            resolve_identity_from_payload=resolve_identity_from_payload,
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
            mcp_tool_discoverer=McpStdioToolDiscoverer(),
            capability_config_base_dir=layout.users_data_dir,
            workflow_runner=ComfyUiWorkflowRunner(config_base_dir=layout.users_data_dir),
        ),
        build_plugins_router(plugin_host=runtime.plugin_host, admin_auth=runtime.admin_write_auth),
        build_reminders_router(
            engine=engine,
            runtime_metrics=runtime_metrics,
            log_event=log_event,
            resolve_identity_from_query=resolve_identity_from_query,
            resolve_identity_from_payload=resolve_identity_from_payload,
        ),
    )


__all__ = ["build_bot_runtime_routers"]
