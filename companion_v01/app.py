from __future__ import annotations

import json
import logging
import time
import tracemalloc
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import config
from .bot_registry import BotRegistry
from .bot_runtime import BotRuntimeFactory, log_runtime_start_status
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
from .routes.qq import build_qq_router
from .routes.reminders import build_reminders_router
from .routes.sessions import build_sessions_router
from .routes.satellite import build_satellite_router
from .routes.system import build_system_router
from .routes.think import build_think_router
from .routes.voice import build_voice_router
from .routes.web_static import build_web_static_router

tracemalloc.start()
logger = logging.getLogger("akane.app")


app = FastAPI(title="Aihong Companion V0.1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://127.0.0.1",
        "http://tauri.localhost",
        "https://tauri.localhost",
        "tauri://localhost",
        "null",
    ],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Accel-Buffering"],
    max_age=600,
)

APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent

WEB_DIR = PROJECT_DIR / "web"
ASSETS_DIR = WEB_DIR / "assets"
MODULES_DIR = WEB_DIR / "modules"
VENDOR_DIR = WEB_DIR / "vendor"
bot_runtime_factory = BotRuntimeFactory(
    config_module=config,
    assets_dir=ASSETS_DIR,
    logger=logger,
)
bot_runtime = bot_runtime_factory.create(
    data_root=Path(config.DATA_ROOT),
    selected_instance_id=getattr(config, "AKANE_INSTANCE_ID", ""),
    explicit_data_root=bool(getattr(config, "AKANE_DATA_ROOT_EXPLICIT", False)),
)
bot_registry = BotRegistry(default_bot_id=bot_runtime.bot_id)
bot_registry.add(bot_runtime, default=True)
bot_runtime.bind_app_state(app)
app.state.akane_bot_registry = bot_registry

# Compatibility aliases: existing routers and deployment smoke tests still use
# these names during Slice 1. They reference the single BotRuntime and are not
# a second construction path.
instance_context = bot_runtime.instance_context
instance_runtime = bot_runtime.instance_runtime
runtime_layout = bot_runtime.runtime_layout
CREATOR_KIT_CHARACTERS_DIR = runtime_layout.characters_dir
resources = bot_runtime.resources
desktop_pet_character_resources = bot_runtime.desktop_pet_character_resources
model_service_config_store = bot_runtime.model_service_config_store
settings_override_store = bot_runtime.settings_override_store
deployment_security = bot_runtime.deployment_security
qq_channel_config = bot_runtime.qq_channel_config
admin_write_auth = bot_runtime.admin_write_auth
desktop_satellite_service = bot_runtime.desktop_satellite_service
plugin_host = bot_runtime.plugin_host
plugin_capability_source = bot_runtime.plugin_capability_source
engine = bot_runtime.engine
USER_ASSETS_DIR = bot_runtime.user_assets_dir
tts_client = bot_runtime.tts_client
runtime_metrics = bot_runtime.runtime_metrics
public_guard = bot_runtime.public_guard
qq_gateway = bot_runtime.qq_gateway
qq_followup_tasks = bot_runtime.qq_followup_tasks


@app.on_event("startup")
async def startup_event() -> None:
    startup_status = await bot_runtime.start()
    log_runtime_start_status(bot_runtime, startup_status)
    app.state.akane_plugin_command_broker = bot_runtime.plugin_command_broker


if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")
if CREATOR_KIT_CHARACTERS_DIR.exists():
    app.mount(
        "/desktop-pet-character-packs",
        StaticFiles(directory=str(CREATOR_KIT_CHARACTERS_DIR)),
        name="desktop_pet_character_packs",
    )
    app.mount(
        "/petdesk-character-packs",
        StaticFiles(directory=str(CREATOR_KIT_CHARACTERS_DIR)),
        name="petdesk_character_packs",
    )
if USER_ASSETS_DIR.exists():
    app.mount("/user-assets", StaticFiles(directory=str(USER_ASSETS_DIR)), name="user-assets")


@app.on_event("shutdown")
async def shutdown_event() -> None:
    shutdown_status = await bot_runtime.stop()
    if shutdown_status.get("status") != "stopped":
        logger.warning(
            "Bot runtime shutdown incomplete: %s",
            json.dumps(shutdown_status, ensure_ascii=False, sort_keys=True),
        )
    app.state.akane_plugin_command_broker = None
    # Keep the root lease until process exit. Some legacy stores still release
    # native handles only when the interpreter exits; dropping the lock here
    # would let a replacement process overlap those final writers/handles.


def _log_event(event: str, **fields: object) -> None:
    payload = {
        "event": event,
        "timestamp": round(time.time(), 3),
        **fields,
    }
    logger.info(json.dumps(payload, ensure_ascii=False))


def _resolve_identity_from_query(request: Request) -> tuple[str, str]:
    session_id = str(request.query_params.get("user_id") or request.query_params.get("session_id") or "default_session")
    profile_user_id = str(request.query_params.get("real_user_id") or session_id)
    return session_id, profile_user_id


def _resolve_identity_from_payload(payload: dict) -> tuple[str, str]:
    session_id = str(payload.get("user_id") or payload.get("session_id") or "default_session")
    profile_user_id = str(payload.get("real_user_id") or session_id)
    return session_id, profile_user_id


app.include_router(
    build_core_router(
        engine=engine,
        config_module=config,
        instance_runtime=instance_runtime,
        resolve_identity_from_query=_resolve_identity_from_query,
        runtime_metrics=runtime_metrics,
        public_guard=public_guard,
    )
)
app.include_router(
    build_system_router(
        engine=engine,
        runtime_metrics=runtime_metrics,
        public_guard=public_guard,
        log_event=_log_event,
        admin_auth=admin_write_auth,
    )
)
app.include_router(
    build_think_router(
        engine=engine,
        public_guard=public_guard,
        runtime_metrics=runtime_metrics,
        log_event=_log_event,
    )
)
app.include_router(
    build_desktop_pet_router(
        engine=engine,
        config_module=config,
        runtime_metrics=runtime_metrics,
        log_event=_log_event,
        resolve_identity_from_query=_resolve_identity_from_query,
        resolve_identity_from_payload=_resolve_identity_from_payload,
    )
)
app.include_router(
    build_petdesk_router(
        engine=engine,
        config_module=config,
        tts_client=tts_client,
        character_resources=desktop_pet_character_resources,
        runtime_metrics=runtime_metrics,
        public_guard=public_guard,
        log_event=_log_event,
        capability_config_base_dir=runtime_layout.users_data_dir,
    )
)
app.include_router(
    build_gifts_router(
        engine=engine,
        runtime_metrics=runtime_metrics,
        log_event=_log_event,
        resolve_identity_from_query=_resolve_identity_from_query,
        resolve_identity_from_payload=_resolve_identity_from_payload,
    )
)
if qq_gateway is not None:
    app.include_router(
        build_qq_router(
            engine=engine,
            config_module=config,
            qq_gateway=qq_gateway,
            runtime_metrics=runtime_metrics,
            logger=logger,
            log_event=_log_event,
            tts_client=tts_client,
            async_task_supervisor=qq_followup_tasks,
            channel_config=qq_channel_config,
            admin_auth=admin_write_auth,
        )
    )
app.include_router(
    build_sessions_router(
        engine=engine,
        runtime_metrics=runtime_metrics,
        log_event=_log_event,
        resolve_identity_from_query=_resolve_identity_from_query,
        resolve_identity_from_payload=_resolve_identity_from_payload,
    )
)
app.include_router(
    build_voice_router(
        engine=engine,
        config_module=config,
        tts_client=tts_client,
        runtime_metrics=runtime_metrics,
        log_event=_log_event,
        capability_config_base_dir=runtime_layout.users_data_dir,
    )
)
app.include_router(
    build_control_center_router(
        runtime_metrics=runtime_metrics,
        log_event=_log_event,
        resolve_identity_from_query=_resolve_identity_from_query,
        snapshot_runtime_providers=build_control_center_snapshot_runtime_providers(
            engine=engine,
            config_module=config,
            runtime_metrics=runtime_metrics,
            public_guard=public_guard,
        ),
        settings_override_store=settings_override_store,
        config_module=config,
        admin_auth=admin_write_auth,
    )
)
app.include_router(
    build_model_services_router(
        store=model_service_config_store,
        config_module=config,
        engine=engine,
        reload_model_services=bot_runtime.reload_model_services,
        runtime_metrics=runtime_metrics,
        log_event=_log_event,
        admin_auth=admin_write_auth,
    )
)
app.include_router(
    build_capabilities_router(
        engine=engine,
        config_module=config,
        tts_client=tts_client,
        runtime_metrics=runtime_metrics,
        log_event=_log_event,
        resolve_identity_from_query=_resolve_identity_from_query,
        background_tasks=getattr(engine, "background_tasks", None),
        mcp_tool_discoverer=McpStdioToolDiscoverer(),
        capability_config_base_dir=runtime_layout.users_data_dir,
        workflow_runner=ComfyUiWorkflowRunner(config_base_dir=runtime_layout.users_data_dir),
    )
)
app.include_router(build_plugins_router(plugin_host=plugin_host, admin_auth=admin_write_auth))
app.include_router(
    build_satellite_router(
        satellite_service=desktop_satellite_service,
        admin_auth=admin_write_auth,
    )
)
app.include_router(
    build_web_static_router(
        web_dir=WEB_DIR,
        modules_dir=MODULES_DIR,
        vendor_dir=VENDOR_DIR,
    )
)
app.include_router(
    build_reminders_router(
        engine=engine,
        runtime_metrics=runtime_metrics,
        log_event=_log_event,
        resolve_identity_from_query=_resolve_identity_from_query,
        resolve_identity_from_payload=_resolve_identity_from_payload,
    )
)
