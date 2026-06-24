from __future__ import annotations

import json
import logging
import threading
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
from services.tts_client import EdgeTTSClient
from .engine import AkaneMemoryEngine
from .desktop_pet_character_resources import DesktopPetCharacterResourceService
from .local_workflow_runners.comfyui import ComfyUiWorkflowRunner
from .mcp_stdio_discoverer import McpStdioToolDiscoverer
from .model_service_config import ModelServiceConfigStore, load_and_apply_saved_model_service
from .settings_overrides import SettingsOverrideStore, load_and_apply_saved_overrides
from .public_guard import PublicThinkGuard
from .qq_gateway import NapCatQQGateway
from .resource_manifest import ResourceManifest
from .routes.capabilities import build_capabilities_router
from .routes.control_center import build_control_center_router, build_control_center_snapshot_runtime_providers
from .routes.core import build_core_router
from .routes.desktop_pet import build_desktop_pet_router
from .routes.gifts import build_gifts_router
from .routes.model_services import build_model_services_router
from .routes.qq import build_qq_router
from .routes.reminders import build_reminders_router
from .routes.sessions import build_sessions_router
from .routes.system import build_system_router
from .routes.think import build_think_router
from .routes.voice import build_voice_router
from .routes.web_static import build_web_static_router

tracemalloc.start()
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
CREATOR_KIT_CHARACTERS_DIR = Path(config.CHARACTERS_DIR)
MODULES_DIR = WEB_DIR / "modules"
VENDOR_DIR = WEB_DIR / "vendor"
resources = ResourceManifest(ASSETS_DIR)
desktop_pet_character_resources = DesktopPetCharacterResourceService(
    characters_dir=CREATOR_KIT_CHARACTERS_DIR,
)
model_service_config_store = ModelServiceConfigStore(
    Path(config.DATA_DIR) / "_local" / "model_service.json"
)
load_and_apply_saved_model_service(
    store=model_service_config_store,
    config_module=config,
    on_error=lambda exc: logger.warning("Model service config ignored: %s", exc),
)
settings_override_store = SettingsOverrideStore(
    Path(config.DATA_DIR) / "_local" / "settings_overrides.json"
)
load_and_apply_saved_overrides(
    config,
    settings_override_store,
    on_error=lambda exc: logger.warning("Settings override ignored: %s", exc),
)
engine = AkaneMemoryEngine(
    Path(config.DATA_DIR) / "akane_memory_v01",
    resource_manifest=resources,
    desktop_pet_character_resources=desktop_pet_character_resources,
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
        getattr(config, "PUBLIC_DAILY_LIMIT_MESSAGE", "今日体验名额已满，明天再来看看吧。")
    ),
)
if getattr(config, "QQ_BRIDGE_ENABLED", False):
    qq_gateway: NapCatQQGateway | None = NapCatQQGateway(
        state_path=Path(config.STATE_DIR) / "qq_gateway_state.json",
    )
else:
    qq_gateway = None


def _install_qq_task_completion_notifications() -> None:
    task_worker = getattr(engine, "task_worker_service", None)
    if task_worker is None:
        return

    def _handle_completion(
        *,
        task_id: str,
        profile_user_id: str,
        session_id: str,
        task: dict,
        handoff: dict,
    ) -> None:
        if not bool(getattr(config, "QQ_BACKGROUND_COMPLETION_NOTIFY_ENABLED", True)):
            return
        task_service = getattr(engine, "task_workspace_service", None)
        current_task = task_service.get_task(task_id) if task_service is not None else task
        metadata = dict((current_task or task).get("metadata") or {})
        delivery = metadata.get("delivery") if isinstance(metadata.get("delivery"), dict) else {}
        if str(delivery.get("client") or "").strip() != "qq_text":
            return
        if delivery.get("completed_notified_at"):
            return
        context = qq_gateway.context_from_delivery_context(delivery)
        if context is None:
            return

        delivery["completed_notified_at"] = int(time.time())
        metadata["delivery"] = delivery
        if task_service is not None:
            task_service.update_task(task_id=task_id, metadata=metadata, timestamp=int(time.time()))

        artifact_targets = _qq_completion_artifact_targets(current_task or task, handoff)
        original_message = str(delivery.get("clean_message") or delivery.get("raw_message") or "")
        should_send = str((handoff or {}).get("next_action") or "").strip().lower() == "send_to_user"
        should_send = should_send or qq_gateway.message_requests_file_delivery(original_message)
        if should_send and artifact_targets:
            sent_count = _send_qq_completion_files(
                context=context,
                profile_user_id=profile_user_id,
                session_id=session_id,
                targets=artifact_targets,
            )
            if sent_count > 0:
                qq_gateway.send_reply(context, f"做好啦，我把结果发给你了。")
                return

        labels = _qq_completion_artifact_labels(current_task or task, handoff)
        if labels:
            qq_gateway.send_reply(
                context,
                "做好啦。现在有这些结果可以发给你："
                + "、".join(labels[:6])
                + "。你要哪份就直接说“发给我”或告诉我编号。",
            )
        else:
            qq_gateway.send_reply(context, "做好啦，后台任务已经处理完了。")

    task_worker.on_task_completed = _handle_completion


def _qq_completion_artifact_targets(task: dict, handoff: dict) -> list[str]:
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


def _qq_completion_artifact_labels(task: dict, handoff: dict) -> list[str]:
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
    *,
    context,
    profile_user_id: str,
    session_id: str,
    targets: list[str],
) -> int:
    generated_file_service = engine._get_generated_file_service()
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
        send_result = qq_gateway.send_file(
            context,
            file_path=str(file_ref.get("absolute_path") or ""),
            name=str(file_ref.get("name") or file_ref.get("title") or ""),
        )
        generated_id = str(file_ref.get("generated_id") or "").strip()
        if generated_id:
            engine.mark_generated_file_delivery(
                profile_user_id=profile_user_id,
                session_id=session_id,
                generated_id=generated_id,
                delivery_status="sent" if send_result.get("ok") else "failed",
                timestamp=int(time.time()),
            )
        if send_result.get("ok"):
            sent_count += 1
    return sent_count


if qq_gateway is not None:
    _install_qq_task_completion_notifications()

if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")
if CREATOR_KIT_CHARACTERS_DIR.exists():
    app.mount(
        "/desktop-pet-character-packs",
        StaticFiles(directory=str(CREATOR_KIT_CHARACTERS_DIR)),
        name="desktop_pet_character_packs",
    )
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
    )
)
app.include_router(
    build_model_services_router(
        store=model_service_config_store,
        config_module=config,
        engine=engine,
        runtime_metrics=runtime_metrics,
        log_event=_log_event,
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
        workflow_runner=ComfyUiWorkflowRunner(config_base_dir=Path(config.DATA_DIR)),
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
