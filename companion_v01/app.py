from __future__ import annotations

import json
import logging
import threading
import time
import tracemalloc
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import config
from services.tts_client import EdgeTTSClient
from .engine import AkaneMemoryEngine
from .public_guard import PublicThinkGuard
from .qq_gateway import NapCatQQGateway
from .resource_manifest import ResourceManifest
from .routes.core import build_core_router
from .routes.desktop_pet import build_desktop_pet_router
from .routes.gifts import build_gifts_router
from .routes.qq import build_qq_router
from .routes.reminders import build_reminders_router
from .routes.sessions import build_sessions_router
from .routes.system import build_system_router
from .routes.think import build_think_router
from .routes.voice import build_voice_router
from .routes.web_static import build_web_static_router

tracemalloc.start()
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("akane.app")


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
resources = ResourceManifest(ASSETS_DIR)
engine = AkaneMemoryEngine(
    Path(config.DATA_DIR) / "akane_memory_v01",
    resource_manifest=resources,
)
USER_ASSETS_DIR = engine.gift_assets.base_dir
tts_client = EdgeTTSClient(
    voice=getattr(config, "TTS_VOICE", "zh-CN-XiaoxiaoNeural"),
    rate=getattr(config, "TTS_RATE", "+0%"),
    volume=getattr(config, "TTS_VOLUME", "+0%"),
    pitch=getattr(config, "TTS_PITCH", "+4Hz"),
)
runtime_metrics = RuntimeMetrics()
public_guard = PublicThinkGuard(
    enabled=bool(getattr(config, "PUBLIC_GUARD_ENABLED", False)),
    max_concurrent_thinks=int(getattr(config, "MAX_CONCURRENT_THINKS", 2)),
    daily_think_limit=int(getattr(config, "DAILY_THINK_LIMIT", 200)),
    busy_message=str(getattr(config, "PUBLIC_BUSY_MESSAGE", "当前体验人数较多，请稍后再试。")),
    daily_limit_message=str(
        getattr(config, "PUBLIC_DAILY_LIMIT_MESSAGE", "今日体验名额已满，明天再来看看 Akane 吧。")
    ),
)
qq_gateway = NapCatQQGateway()

if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")
if USER_ASSETS_DIR.exists():
    app.mount("/user-assets", StaticFiles(directory=str(USER_ASSETS_DIR)), name="user-assets")


@app.on_event("shutdown")
async def shutdown_event() -> None:
    engine.close()


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
        resolve_identity_from_query=_resolve_identity_from_query,
    )
)
app.include_router(
    build_system_router(
        engine=engine,
        runtime_metrics=runtime_metrics,
        public_guard=public_guard,
        log_event=_log_event,
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
    build_gifts_router(
        engine=engine,
        runtime_metrics=runtime_metrics,
        log_event=_log_event,
        resolve_identity_from_query=_resolve_identity_from_query,
        resolve_identity_from_payload=_resolve_identity_from_payload,
    )
)
app.include_router(
    build_qq_router(
        engine=engine,
        config_module=config,
        qq_gateway=qq_gateway,
        runtime_metrics=runtime_metrics,
        logger=logger,
        log_event=_log_event,
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
