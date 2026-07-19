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
from .bot_http_routes import build_bot_runtime_routers
from .bot_runtime import BotRuntimeFactory
from .host_bot_bootstrap import build_host_bot_registry
from .routes.bots import build_bots_router
from .routes.qq import build_qq_router
from .routes.satellite import build_satellite_router
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
host_bot_bootstrap = build_host_bot_registry(
    factory=bot_runtime_factory,
    host_data_root=Path(config.DATA_ROOT),
    selected_instance_id=getattr(config, "AKANE_INSTANCE_ID", ""),
    explicit_data_root=bool(getattr(config, "AKANE_DATA_ROOT_EXPLICIT", False)),
)
bot_registry = host_bot_bootstrap.registry
bot_runtime = host_bot_bootstrap.default_runtime
bot_runtime.bind_app_state(app)
app.state.akane_bot_registry = bot_registry
app.state.akane_default_bot_id = bot_registry.default_bot_id
app.state.akane_host_bot_bootstrap = host_bot_bootstrap.public_snapshot()

# Compatibility aliases remain references to the Registry default runtime.
# Route construction below no longer depends on them.
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
runtime_config = bot_runtime.config_module


@app.on_event("startup")
async def startup_event() -> None:
    startup_status = await bot_registry.start_all()
    if startup_status.get("status") == "degraded":
        logger.warning(
            "Bot registry startup degraded: %s",
            json.dumps(startup_status, ensure_ascii=False, sort_keys=True),
        )
    app.state.akane_plugin_command_broker = bot_runtime.plugin_command_broker


if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")
for static_bot_runtime in bot_registry.values():
    static_prefix = f"/api/bots/{static_bot_runtime.bot_id}"
    characters_dir = static_bot_runtime.runtime_layout.characters_dir
    if characters_dir.exists():
        app.mount(
            f"{static_prefix}/desktop-pet-character-packs",
            StaticFiles(directory=str(characters_dir)),
            name=f"bot_{static_bot_runtime.bot_id}_desktop_pet_character_packs",
        )
        app.mount(
            f"{static_prefix}/petdesk-character-packs",
            StaticFiles(directory=str(characters_dir)),
            name=f"bot_{static_bot_runtime.bot_id}_petdesk_character_packs",
        )
    user_assets_dir = static_bot_runtime.user_assets_dir
    if user_assets_dir.exists():
        app.mount(
            f"{static_prefix}/user-assets",
            StaticFiles(directory=str(user_assets_dir)),
            name=f"bot_{static_bot_runtime.bot_id}_user_assets",
        )
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
    shutdown_status = await bot_registry.stop_all()
    if shutdown_status.get("status") != "stopped":
        logger.warning(
            "Bot registry shutdown incomplete: %s",
            json.dumps(shutdown_status, ensure_ascii=False, sort_keys=True),
        )
    app.state.akane_plugin_command_broker = None
    # Keep root leases until process exit. Some legacy stores still release
    # native handles only when the interpreter exits; dropping the lock here
    # would let a replacement process overlap those final writers/handles.


def _log_event(event: str, **fields: object) -> None:
    payload = {
        "event": event,
        "timestamp": round(time.time(), 3),
        **fields,
    }
    logger.info(json.dumps(payload, ensure_ascii=False))


def _bot_log_event(bot_id: str):
    def emit(event: str, **fields: object) -> None:
        _log_event(event, bot_id=bot_id, **fields)

    return emit


def _resolve_identity_from_query(request: Request) -> tuple[str, str]:
    session_id = str(request.query_params.get("user_id") or request.query_params.get("session_id") or "default_session")
    profile_user_id = str(request.query_params.get("real_user_id") or session_id)
    return session_id, profile_user_id


def _resolve_identity_from_payload(payload: dict) -> tuple[str, str]:
    session_id = str(payload.get("user_id") or payload.get("session_id") or "default_session")
    profile_user_id = str(payload.get("real_user_id") or session_id)
    return session_id, profile_user_id


app.include_router(build_bots_router(bot_registry=bot_registry))
for http_bot_runtime in bot_registry.values():
    route_prefix = f"/api/bots/{http_bot_runtime.bot_id}"
    runtime_routers = build_bot_runtime_routers(
        runtime=http_bot_runtime,
        route_prefix=route_prefix,
        resolve_identity_from_query=_resolve_identity_from_query,
        resolve_identity_from_payload=_resolve_identity_from_payload,
        log_event=_bot_log_event(http_bot_runtime.bot_id),
    )
    for runtime_router in runtime_routers:
        app.include_router(runtime_router, prefix=route_prefix)
        if http_bot_runtime.bot_id == bot_registry.default_bot_id:
            app.include_router(runtime_router)

for qq_bot_runtime in bot_registry.values():
    if qq_bot_runtime.qq_gateway is None:
        continue
    qq_route_kwargs = {
        "engine": qq_bot_runtime.engine,
        "config_module": qq_bot_runtime.config_module,
        "qq_gateway": qq_bot_runtime.qq_gateway,
        "runtime_metrics": qq_bot_runtime.runtime_metrics,
        "logger": logger,
        "log_event": _bot_log_event(qq_bot_runtime.bot_id),
        "tts_client": qq_bot_runtime.tts_client,
        "settings": qq_bot_runtime.settings,
        "async_task_supervisor": qq_bot_runtime.qq_followup_tasks,
        "channel_config": qq_bot_runtime.qq_channel_config,
        "admin_auth": qq_bot_runtime.admin_write_auth,
        "plugin_command_broker_provider": lambda runtime=qq_bot_runtime: runtime.plugin_command_broker,
    }
    app.include_router(
        build_qq_router(
            **qq_route_kwargs,
            route_base=f"/api/bots/{qq_bot_runtime.bot_id}/qq",
        )
    )
    if qq_bot_runtime.bot_id == bot_registry.default_bot_id:
        app.include_router(
            build_qq_router(
                **qq_route_kwargs,
                route_base="/api/qq",
            )
        )
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
