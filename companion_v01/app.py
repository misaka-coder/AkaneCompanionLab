from __future__ import annotations

import json
import logging
import asyncio
import importlib.util
import os
import sys
import threading
import time
import tracemalloc
from pathlib import Path
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

import config
from services.tts_client import EdgeTTSClient
from .engine import AkaneMemoryEngine
from .public_guard import PublicThinkGuard
from .qq_gateway import NapCatQQGateway
from .resource_manifest import ResourceManifest

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


def _print_debug(payload: dict, frame: dict) -> None:
    debug = frame.get("_debug") or {}
    retrieval_debug = debug.get("retrieval_result") or {}
    memory_snippets = list(retrieval_debug.get("memory_snippets") or [])
    selected_memory_snippets = list(retrieval_debug.get("selected_memory_snippets") or [])
    print("")
    print("=" * 18, "哀鸿同人 V0.1", "=" * 18)
    print(f"session_id: {payload.get('user_id', '')}")
    print(f"user_text: {payload.get('message', '')}")
    print("")
    print("=== 前置路由输出 ===")
    print(json.dumps(debug.get("router_output", {}), ensure_ascii=False, indent=2))
    print("")
    print("=== 前置路由耗时 ===")
    print(json.dumps(debug.get("router_timing", {}), ensure_ascii=False, indent=2))
    print("")
    print("=== 检索结果摘要 ===")
    print(json.dumps(debug.get("retrieval_result", {}), ensure_ascii=False, indent=2))
    print("")
    print("=== 检索校验输出 ===")
    print(json.dumps(debug.get("verifier_output", {}), ensure_ascii=False, indent=2))
    print("")
    print("=== 检索校验耗时 ===")
    print(json.dumps(debug.get("verifier_timing", {}), ensure_ascii=False, indent=2))
    print("")
    print("=== 检索片段（按编号） ===")
    if memory_snippets:
        for index, snippet in enumerate(memory_snippets, start=1):
            print(f"[{index}]")
            print(str(snippet))
            print("")
    else:
        print("(无)")
    print("=== 被选中的记忆片段 ===")
    if selected_memory_snippets:
        for item in selected_memory_snippets:
            label = f"[{item.get('index')}]" if item.get("index") is not None else "[fallback]"
            print(label)
            print(str(item.get("snippet") or ""))
            print("")
    else:
        print("(无)")
    print("")
    print("=== 最终回复 JSON ===")
    visible: dict[str, object] = {}
    for key in (
        "thought",
        "memory_tags",
        "speech",
        "speech_segments",
        "code_snippet",
        "status",
        "emotion",
        "score",
        "tool_call",
        "choices",
        "npc_turns",
        "client_mode",
        "client",
        "character",
        "scene",
        "persona",
    ):
        if key == "thought" and key not in frame:
            continue
        visible[key] = frame.get(key)
    print(json.dumps(visible, ensure_ascii=False, indent=2))
    print("=" * 48)


def _log_event(event: str, **fields: object) -> None:
    payload = {
        "event": event,
        "timestamp": round(time.time(), 3),
        **fields,
    }
    logger.info(json.dumps(payload, ensure_ascii=False))


def _log_turn_result(route: str, payload: dict, frame: dict, duration_ms: float) -> None:
    debug = frame.get("_debug") or {}
    _log_event(
        "turn_complete",
        route=route,
        session_id=str(payload.get("user_id") or ""),
        trace_id=str(frame.get("trace_id") or ""),
        duration_ms=round(float(duration_ms), 1),
        router_ready_at_ms=debug.get("router_timing", {}).get("ready_at_ms"),
        verifier_mode=debug.get("verifier_timing", {}).get("mode"),
        final_status=str(frame.get("status") or ""),
        emotion=str(frame.get("emotion") or ""),
    )


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "pid": os.getpid(),
        "python": sys.executable,
        "yt_dlp": importlib.util.find_spec("yt_dlp") is not None,
    }


@app.get("/metrics")
async def metrics() -> Response:
    current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    llm_metrics = engine.llm.snapshot_metrics()
    vector_entries = engine.vector_store.count_entries()
    reindex_metrics = engine.snapshot_embedding_reindex_status()
    counters = runtime_metrics.snapshot()
    guard_metrics = public_guard.snapshot()

    lines = [
        "# TYPE akane_runtime gauge",
        f"akane_tracemalloc_current_bytes {int(current_bytes)}",
        f"akane_tracemalloc_peak_bytes {int(peak_bytes)}",
        f"akane_vector_entries {int(vector_entries)}",
        f"akane_embedding_reindex_total {int(reindex_metrics.get('total') or 0)}",
        f"akane_embedding_reindex_processed {int(reindex_metrics.get('processed') or 0)}",
        f"akane_embedding_reindex_running {1 if str(reindex_metrics.get('state') or '') == 'running' else 0}",
        f"akane_public_guard_enabled {1 if guard_metrics.get('enabled') else 0}",
        f"akane_public_guard_max_concurrent_thinks {int(guard_metrics.get('max_concurrent_thinks') or 0)}",
        f"akane_public_guard_daily_think_limit {int(guard_metrics.get('daily_think_limit') or 0)}",
        f"akane_public_guard_active_thinks {int(guard_metrics.get('active_thinks') or 0)}",
        f"akane_public_guard_used_today {int(guard_metrics.get('used_today') or 0)}",
    ]
    for key, value in sorted(counters.items()):
        lines.append(f"akane_{key} {value}")
    for key, value in sorted(llm_metrics.items()):
        lines.append(f"akane_llm_{key} {int(value)}")
    return Response(content="\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


@app.get("/api/qq/napcat/status")
async def qq_napcat_status() -> JSONResponse:
    return JSONResponse({"status": "ok", "data": qq_gateway.status()})


@app.post("/api/qq/napcat/event")
async def qq_napcat_event(request: Request) -> JSONResponse:
    started_at = time.perf_counter()
    event: dict = {}
    context = None
    try:
        event = await request.json()
        if not bool(getattr(config, "QQ_BRIDGE_ENABLED", False)):
            runtime_metrics.observe_request("qq_napcat_event", duration_ms=(time.perf_counter() - started_at) * 1000, ok=True)
            return JSONResponse({"status": "disabled", "message": "QQ bridge is disabled"})

        context = qq_gateway.build_message_context(event)
        if not context.should_respond:
            runtime_metrics.observe_request("qq_napcat_event", duration_ms=(time.perf_counter() - started_at) * 1000, ok=True)
            return JSONResponse({"status": "ignored", "reason": context.reason})

        attachments_registered = []
        if context.attachments:
            attachments_registered = await asyncio.to_thread(
                engine.ingest_qq_attachments,
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                attachments=list(context.attachments),
                timestamp=int(event.get("time") or time.time()),
            )
            attachment_ids = [
                str(item.get("attachment_id") or "").strip()
                for item in attachments_registered
                if isinstance(item, dict) and str(item.get("attachment_id") or "").strip()
            ]
            debounce_token = qq_gateway.register_attachment_debounce(context, attachment_ids=attachment_ids)
            if bool(debounce_token.get("enabled")):
                await asyncio.sleep(float(debounce_token.get("delay_seconds") or 0.0))
                debounce_result = qq_gateway.consume_attachment_debounce(debounce_token)
                if not bool(debounce_result.get("process")):
                    duration_ms = (time.perf_counter() - started_at) * 1000
                    runtime_metrics.observe_request("qq_napcat_event", duration_ms=duration_ms, ok=True)
                    _log_event(
                        "qq_napcat_event_buffered",
                        session_id=context.session_id,
                        profile_user_id=context.profile_user_id,
                        reason=str(debounce_result.get("reason") or "attachment_debounce"),
                        attachment_count=len(context.attachments or []),
                        attachments_registered=len(attachments_registered),
                        duration_ms=round(duration_ms, 1),
                    )
                    return JSONResponse(
                        {
                            "status": "buffered",
                            "reason": str(debounce_result.get("reason") or "attachment_debounce"),
                            "session_id": context.session_id,
                            "profile_user_id": context.profile_user_id,
                            "attachments_registered": len(attachments_registered),
                        }
                    )
                attachment_ids = [
                    str(item or "").strip()
                    for item in list(debounce_result.get("attachment_ids") or attachment_ids)
                    if str(item or "").strip()
                ]
            if attachment_ids:
                await asyncio.to_thread(
                    engine.wait_for_qq_attachments_settled,
                    profile_user_id=context.profile_user_id,
                    session_id=context.session_id,
                    attachment_ids=attachment_ids,
                    timeout_seconds=float(getattr(config, "QQ_ATTACHMENT_READY_WAIT_SECONDS", 8.0) or 0.0),
                )

        turn_payload = context.to_turn_payload()
        remote_prefetch_result = await asyncio.to_thread(
            engine.prefetch_remote_media_links_for_message,
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            message=context.clean_message or context.raw_message,
            timestamp=int(event.get("time") or time.time()),
        )
        if isinstance(remote_prefetch_result, dict) and str(remote_prefetch_result.get("followup_context") or "").strip():
            prefetch_context = str(remote_prefetch_result.get("followup_context") or "").strip()
            original_extra_context = str(turn_payload.get("extra_context") or "").strip()
            turn_payload["extra_context"] = "\n\n".join(
                part
                for part in (
                    original_extra_context,
                    "【链接素材预处理结果】\n" + prefetch_context,
                )
                if part
            )
        frame = await asyncio.to_thread(engine.process_turn, turn_payload)
        reply_messages = qq_gateway.render_reply_messages(frame)
        reply_text = "\n".join(reply_messages).strip()
        send_result = await asyncio.to_thread(qq_gateway.send_replies, context, reply_messages)
        file_send_result = await asyncio.to_thread(
            qq_gateway.send_generated_files,
            context,
            list(frame.get("tool_events") or []),
        )
        for item in list(file_send_result.get("results") or []):
            generated_id = str(item.get("generated_id") or "").strip()
            if not generated_id:
                continue
            await asyncio.to_thread(
                engine.mark_generated_file_delivery,
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                generated_id=generated_id,
                delivery_status="sent" if item.get("ok") else "failed",
                timestamp=int(time.time()),
            )
    except Exception as exc:
        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("qq_napcat_event", duration_ms=duration_ms, ok=False)
        logger.exception("qq_napcat_event failed")
        _log_event(
            "qq_napcat_event_error",
            session_id=getattr(context, "session_id", ""),
            profile_user_id=getattr(context, "profile_user_id", ""),
            post_type=str(event.get("post_type") or "") if isinstance(event, dict) else "",
            message_type=str(event.get("message_type") or "") if isinstance(event, dict) else "",
            reason=str(exc),
            duration_ms=round(duration_ms, 1),
        )
        return JSONResponse(
            {
                "status": "error",
                "reason": "qq_event_processing_failed",
                "message": str(exc)[:500],
            }
        )

    duration_ms = (time.perf_counter() - started_at) * 1000
    runtime_metrics.observe_request("qq_napcat_event", duration_ms=duration_ms, ok=bool(send_result.get("ok")))
    _log_event(
        "qq_napcat_event",
        session_id=context.session_id,
        profile_user_id=context.profile_user_id,
        reason=context.reason,
        sent=bool(send_result.get("ok")),
        attachment_count=len(context.attachments or []),
        attachments_registered=len(attachments_registered),
        duration_ms=round(duration_ms, 1),
    )
    return JSONResponse(
        {
            "status": "ok" if send_result.get("ok") else "send_failed",
            "reason": context.reason,
            "session_id": context.session_id,
            "profile_user_id": context.profile_user_id,
            # NapCat / OneBot HTTP report supports "quick operation" replies.
            # Do not include a "reply" field here, or it may send a second
            # aggregated message after our explicit send_replies() call.
            "sent_count": len(reply_messages),
            "attachment_count": len(context.attachments or []),
            "attachments_registered": len(attachments_registered),
            "send_result": send_result,
            "file_send_result": file_send_result,
        }
    )


@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/preview")
async def preview():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/resource-preview")
async def resource_preview():
    return FileResponse(WEB_DIR / "preview.html")


@app.get("/live2d-preview")
async def live2d_preview():
    return FileResponse(WEB_DIR / "live2d-preview.html")


@app.get("/resource-manifest")
async def resource_manifest(request: Request):
    _, profile_user_id = _resolve_identity_from_query(request)
    return JSONResponse(engine.build_resource_manifest(profile_user_id=profile_user_id))


@app.get("/app-config")
async def app_config():
    return JSONResponse(
        {
            "streaming_tts_enabled": bool(getattr(config, "STREAMING_TTS_ENABLED", True)),
            "web_identity_mode": str(getattr(config, "WEB_IDENTITY_MODE", "owner") or "owner"),
            "web_owner_profile_user_id": str(getattr(config, "WEB_OWNER_PROFILE_USER_ID", "master") or "master"),
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/styles.css")
async def styles():
    return FileResponse(WEB_DIR / "styles.css")


@app.get("/app.js")
async def app_js():
    return FileResponse(
        WEB_DIR / "app.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/modules/{module_path:path}")
async def module_js(module_path: str):
    if not MODULES_DIR.exists():
        raise HTTPException(status_code=404, detail="module directory not found")

    requested = (MODULES_DIR / unquote(module_path)).resolve()
    try:
        requested.relative_to(MODULES_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=404, detail="module not found")
    if not requested.is_file():
        raise HTTPException(status_code=404, detail="module not found")

    media_type = "application/javascript" if requested.suffix.lower() == ".js" else "application/octet-stream"
    return FileResponse(
        requested,
        media_type=media_type,
        headers={"Cache-Control": "no-store"},
    )


@app.get("/vendor/{vendor_path:path}")
async def vendor_file(vendor_path: str):
    if not VENDOR_DIR.exists():
        raise HTTPException(status_code=404, detail="vendor directory not found")

    requested = (VENDOR_DIR / unquote(vendor_path)).resolve()
    try:
        requested.relative_to(VENDOR_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=404, detail="vendor file not found")
    if not requested.is_file():
        raise HTTPException(status_code=404, detail="vendor file not found")

    media_type = "application/javascript" if requested.suffix.lower() == ".js" else "application/octet-stream"
    return FileResponse(
        requested,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.get("/preview.css")
async def preview_css():
    return FileResponse(WEB_DIR / "preview.css")


@app.get("/preview.js")
async def preview_js():
    return FileResponse(WEB_DIR / "preview.js", media_type="application/javascript")


@app.get("/live2d-preview.css")
async def live2d_preview_css():
    return FileResponse(WEB_DIR / "live2d-preview.css")


@app.get("/live2d-preview.js")
async def live2d_preview_js():
    return FileResponse(WEB_DIR / "live2d-preview.js", media_type="application/javascript")


def _build_gift_assistant_line(*, action: str, asset: dict[str, object]) -> str:
    asset_type = str(asset.get("asset_type") or "").strip().lower()
    display_name = str(asset.get("display_name") or asset.get("origin_name") or "这份礼物").strip() or "这份礼物"
    display_label = f"《{display_name}》" if asset_type == "audio" else display_name
    normalized_action = str(action or "").strip().lower()
    if normalized_action == "upload":
        if asset_type == "audio":
            return f"我先把 {display_label} 放在手边啦。你想让我先留着，还是直接吃掉它呢？"
        if asset_type == "image":
            return f"这张图我先放在手边啦。你想让我先收进相册、直接吃掉它当成自己的场景，还是只让我看看就好？"
        return f"这份礼物我先放在手边啦。你想让我先留着，还是直接吃掉它呢？"
    if normalized_action == "observe":
        return f"{display_label} 我已经看过啦。只是这样被主人分享一下日常，我也会很开心。"
    if normalized_action in {"save", "keep"}:
        if asset_type == "audio":
            return f"那我先把 {display_label} 放进自己的歌匣里，想听的时候再拿出来。"
        if asset_type == "image":
            return f"那我先把 {display_label} 收进自己的回忆相册里，想看的时候再拿出来。"
        return f"那我先把 {display_label} 留在自己这边。"
    if normalized_action == "internalize":
        if asset_type == "audio":
            return f"{display_label} 我吃掉啦，以后我也能把它当成自己的 BGM 放出来。"
        if asset_type == "image":
            return f"{display_label} 我吃掉啦，以后也能把它当成自己的场景拿出来。"
        return f"{display_label} 我已经吃掉啦。"
    if normalized_action == "reject":
        return f"{display_label} 我先不留下，不过我会记得你把它递到我手边过。"
    if normalized_action == "remove":
        return f"{display_label} 我先从自己的收藏里放下啦。"
    if normalized_action == "purge":
        return f"{display_label} 我已经把它彻底删掉啦。"
    return f"{display_label} 我已经收到啦。"


@app.get("/gifts")
async def gifts_list(request: Request):
    started_at = time.perf_counter()
    session_id, profile_user_id = _resolve_identity_from_query(request)
    media_kind = str(request.query_params.get("media_kind") or "all").strip().lower() or "all"
    limit = max(1, int(request.query_params.get("limit") or 50))
    try:
        items = engine.list_gift_assets(
            profile_user_id=profile_user_id,
            media_kind=media_kind,
            limit=limit,
        )
    except Exception as exc:
        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("gifts_list", duration_ms=duration_ms, ok=False)
        _log_event("gifts_list_error", session_id=session_id, profile_user_id=profile_user_id, message=str(exc))
        raise

    duration_ms = (time.perf_counter() - started_at) * 1000
    runtime_metrics.observe_request("gifts_list", duration_ms=duration_ms, ok=True)
    return JSONResponse({"items": items})


@app.get("/gifts/inventory")
async def gifts_inventory(request: Request):
    started_at = time.perf_counter()
    session_id, profile_user_id = _resolve_identity_from_query(request)
    scope = str(request.query_params.get("scope") or "pending_recent").strip().lower() or "pending_recent"
    limit = max(1, min(20, int(request.query_params.get("limit") or 3)))
    try:
        payload = engine.list_gift_inventory(
            profile_user_id=profile_user_id,
            session_id=session_id,
            scope=scope,
            limit=limit,
        )
    except Exception as exc:
        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("gifts_inventory", duration_ms=duration_ms, ok=False)
        _log_event("gifts_inventory_error", session_id=session_id, profile_user_id=profile_user_id, message=str(exc))
        raise

    duration_ms = (time.perf_counter() - started_at) * 1000
    runtime_metrics.observe_request("gifts_inventory", duration_ms=duration_ms, ok=True)
    return JSONResponse(payload)


@app.get("/artifacts/containers")
async def artifact_containers(request: Request):
    started_at = time.perf_counter()
    session_id, profile_user_id = _resolve_identity_from_query(request)
    preview_limit = max(1, min(12, int(request.query_params.get("preview_limit") or 3)))
    include_empty = str(request.query_params.get("include_empty") or "true").strip().lower() not in {"0", "false", "no", "off"}
    try:
        containers = engine.list_artifact_containers(
            profile_user_id=profile_user_id,
            preview_limit=preview_limit,
            include_empty=include_empty,
        )
    except Exception as exc:
        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("artifact_containers", duration_ms=duration_ms, ok=False)
        _log_event("artifact_containers_error", session_id=session_id, profile_user_id=profile_user_id, message=str(exc))
        raise

    duration_ms = (time.perf_counter() - started_at) * 1000
    runtime_metrics.observe_request("artifact_containers", duration_ms=duration_ms, ok=True)
    return JSONResponse({"containers": containers})


@app.get("/artifacts/container")
async def artifact_container_items(request: Request):
    started_at = time.perf_counter()
    session_id, profile_user_id = _resolve_identity_from_query(request)
    container_type = str(request.query_params.get("container_type") or request.query_params.get("type") or "").strip().lower()
    container_key = str(request.query_params.get("container_key") or "").strip()
    limit = max(1, min(200, int(request.query_params.get("limit") or 50)))
    if not container_type:
        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("artifact_container_items", duration_ms=duration_ms, ok=False)
        raise HTTPException(status_code=400, detail="container_type is required")

    try:
        payload = engine.list_artifacts_in_container(
            profile_user_id=profile_user_id,
            container_type=container_type,
            container_key=container_key,
            limit=limit,
        )
    except ValueError as exc:
        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("artifact_container_items", duration_ms=duration_ms, ok=False)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("artifact_container_items", duration_ms=duration_ms, ok=False)
        _log_event("artifact_container_items_error", session_id=session_id, profile_user_id=profile_user_id, message=str(exc))
        raise

    duration_ms = (time.perf_counter() - started_at) * 1000
    runtime_metrics.observe_request("artifact_container_items", duration_ms=duration_ms, ok=True)
    return JSONResponse(payload)


@app.post("/gifts/upload")
async def gifts_upload(request: Request):
    started_at = time.perf_counter()
    session_id, profile_user_id = _resolve_identity_from_query(request)
    filename = unquote(str(request.headers.get("X-Akane-Filename") or "").strip())
    content_type = str(request.headers.get("content-type") or "").strip()
    if not filename:
        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("gifts_upload", duration_ms=duration_ms, ok=False)
        raise HTTPException(status_code=400, detail="missing gift filename")

    try:
        content = await request.body()
        asset = engine.upload_gift_asset(
            profile_user_id=profile_user_id,
            session_id=session_id,
            filename=filename,
            content_type=content_type,
            content=content,
        )
    except ValueError as exc:
        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("gifts_upload", duration_ms=duration_ms, ok=False)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("gifts_upload", duration_ms=duration_ms, ok=False)
        _log_event("gifts_upload_error", session_id=session_id, profile_user_id=profile_user_id, message=str(exc))
        raise

    duration_ms = (time.perf_counter() - started_at) * 1000
    runtime_metrics.observe_request("gifts_upload", duration_ms=duration_ms, ok=True)
    _log_event(
        "gifts_upload",
        session_id=session_id,
        profile_user_id=profile_user_id,
        asset_id=asset.get("asset_id"),
        display_name=asset.get("display_name"),
    )
    return JSONResponse(
        {
            "asset": asset,
            "assistant_line": _build_gift_assistant_line(action="upload", asset=asset),
        }
    )


@app.post("/gifts/action")
async def gifts_action(request: Request):
    started_at = time.perf_counter()
    payload = await request.json()
    session_id, profile_user_id = _resolve_identity_from_payload(payload)
    asset_id = str(payload.get("asset_id") or "").strip()
    action = str(payload.get("action") or "").strip().lower()
    if not asset_id:
        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("gifts_action", duration_ms=duration_ms, ok=False)
        raise HTTPException(status_code=400, detail="asset_id is required")

    try:
        asset = engine.apply_gift_action(
            profile_user_id=profile_user_id,
            session_id=session_id,
            asset_id=asset_id,
            action=action,
        )
    except ValueError as exc:
        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("gifts_action", duration_ms=duration_ms, ok=False)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("gifts_action", duration_ms=duration_ms, ok=False)
        _log_event("gifts_action_error", session_id=session_id, profile_user_id=profile_user_id, message=str(exc))
        raise

    duration_ms = (time.perf_counter() - started_at) * 1000
    runtime_metrics.observe_request("gifts_action", duration_ms=duration_ms, ok=asset is not None)
    if asset is None:
        raise HTTPException(status_code=404, detail="gift asset not found")
    _log_event(
        "gifts_action",
        session_id=session_id,
        profile_user_id=profile_user_id,
        asset_id=asset_id,
        action=action,
        status=asset.get("status"),
    )
    return JSONResponse(
        {
            "asset": asset,
            "assistant_line": _build_gift_assistant_line(action=action, asset=asset),
            "manifest_refresh_required": action in {"internalize", "remove", "purge"},
        }
    )


@app.post("/gifts/observe")
async def gifts_observe(request: Request):
    started_at = time.perf_counter()
    payload = await request.json()
    session_id, profile_user_id = _resolve_identity_from_payload(payload)
    asset_id = str(payload.get("asset_id") or "").strip()
    if not asset_id:
        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("gifts_observe", duration_ms=duration_ms, ok=False)
        raise HTTPException(status_code=400, detail="asset_id is required")

    try:
        result = engine.observe_gift_image_once(
            profile_user_id=profile_user_id,
            session_id=session_id,
            asset_id=asset_id,
        )
    except ValueError as exc:
        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("gifts_observe", duration_ms=duration_ms, ok=False)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("gifts_observe", duration_ms=duration_ms, ok=False)
        _log_event("gifts_observe_error", session_id=session_id, profile_user_id=profile_user_id, message=str(exc))
        raise

    duration_ms = (time.perf_counter() - started_at) * 1000
    runtime_metrics.observe_request("gifts_observe", duration_ms=duration_ms, ok=result is not None)
    if result is None:
        raise HTTPException(status_code=404, detail="gift asset not found")
    _log_event(
        "gifts_observe",
        session_id=session_id,
        profile_user_id=profile_user_id,
        asset_id=asset_id,
    )
    return JSONResponse(result)


@app.post("/reset")
async def reset() -> dict[str, str]:
    started_at = time.perf_counter()
    engine.reset()
    runtime_metrics.observe_request("reset", duration_ms=(time.perf_counter() - started_at) * 1000, ok=True)
    _log_event("reset_complete", duration_ms=round((time.perf_counter() - started_at) * 1000, 1))
    return {"status": "reset"}


@app.post("/tts")
async def tts(request: Request):
    started_at = time.perf_counter()
    payload = await request.json()
    text = str(payload.get("text") or "").strip()
    if not text:
        runtime_metrics.observe_request("tts", duration_ms=(time.perf_counter() - started_at) * 1000, ok=False)
        raise HTTPException(status_code=400, detail="text is required")

    try:
        audio = await tts_client.synthesize(text)
    except ValueError as exc:
        runtime_metrics.observe_request("tts", duration_ms=(time.perf_counter() - started_at) * 1000, ok=False)
        _log_event("tts_error", message=str(exc), text_length=len(text))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        runtime_metrics.observe_request("tts", duration_ms=(time.perf_counter() - started_at) * 1000, ok=False)
        _log_event("tts_error", message=str(exc), text_length=len(text))
        raise HTTPException(status_code=502, detail=f"TTS failed: {exc}") from exc

    duration_ms = (time.perf_counter() - started_at) * 1000
    runtime_metrics.observe_request("tts", duration_ms=duration_ms, ok=True)
    _log_event("tts_complete", duration_ms=round(duration_ms, 1), text_length=len(text))
    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store"},
    )


@app.post("/think")
async def think(request: Request):
    payload = await request.json()
    guard_decision = public_guard.try_acquire()
    if not guard_decision.allowed:
        runtime_metrics.incr("public_guard_blocked_total", 1)
        runtime_metrics.incr(f"public_guard_blocked_{guard_decision.reason}_total", 1)
        _log_event(
            "public_guard_blocked",
            route="think",
            reason=guard_decision.reason,
            message=guard_decision.message,
            session_id=str(payload.get("user_id") or ""),
        )
        raise HTTPException(status_code=429, detail=guard_decision.message)

    def _stream():
        started_at = time.perf_counter()
        partial = {
            "emotion": "",
            "speech": "",
            "event_count": 0,
        }
        ok = False
        yield json.dumps({"type": "stream_start"}, ensure_ascii=False) + "\n"
        try:
            for event in engine.process_turn_stream(payload):
                partial["event_count"] = int(partial.get("event_count", 0)) + 1
                event_type = str(event.get("type") or "")
                if event_type == "ui":
                    partial["emotion"] = str(event.get("emotion") or partial.get("emotion") or "")
                elif event_type == "speech_chunk":
                    partial["speech"] = str(partial.get("speech") or "") + str(event.get("text") or "")
                if str(event.get("type") or "") == "final":
                    final_payload = event.get("payload")
                    if isinstance(final_payload, dict):
                        _print_debug(payload, final_payload)
                        _log_turn_result("think", payload, final_payload, (time.perf_counter() - started_at) * 1000)
                        partial["emotion"] = str(final_payload.get("emotion") or partial.get("emotion") or "")
                        partial["speech"] = str(final_payload.get("speech") or partial.get("speech") or "")
                yield json.dumps(event, ensure_ascii=False) + "\n"
            ok = True
        except Exception as exc:
            yield json.dumps(
                {
                    "type": "stream_error",
                    "message": f"stream failed: {exc}",
                    "partial": partial,
                },
                ensure_ascii=False,
            ) + "\n"
            _log_event("think_stream_error", session_id=str(payload.get("user_id") or ""), message=str(exc), partial=partial)
        finally:
            if guard_decision.acquired:
                public_guard.release()
            duration_ms = (time.perf_counter() - started_at) * 1000
            runtime_metrics.observe_request("think_stream", duration_ms=duration_ms, ok=ok)
            yield json.dumps(
                {
                    "type": "stream_end",
                    "status": "ok" if ok else "error",
                    "partial": partial,
                },
                ensure_ascii=False,
            ) + "\n"

    return StreamingResponse(
        _stream(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-store",
            # Tell nginx not to buffer streamed NDJSON chunks; otherwise UI
            # state like BGM / choices may appear only after a refresh.
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/think_once")
async def think_once(request: Request):
    started_at = time.perf_counter()
    payload = await request.json()
    guard_decision = public_guard.try_acquire()
    if not guard_decision.allowed:
        runtime_metrics.incr("public_guard_blocked_total", 1)
        runtime_metrics.incr(f"public_guard_blocked_{guard_decision.reason}_total", 1)
        _log_event(
            "public_guard_blocked",
            route="think_once",
            reason=guard_decision.reason,
            message=guard_decision.message,
            session_id=str(payload.get("user_id") or ""),
        )
        raise HTTPException(status_code=429, detail=guard_decision.message)
    try:
        frame = engine.process_turn(payload)
    except Exception as exc:
        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("think_once", duration_ms=duration_ms, ok=False)
        _log_event("think_once_error", session_id=str(payload.get("user_id") or ""), message=str(exc))
        raise
    finally:
        if guard_decision.acquired:
            public_guard.release()
    _print_debug(payload, frame)
    duration_ms = (time.perf_counter() - started_at) * 1000
    runtime_metrics.observe_request("think_once", duration_ms=duration_ms, ok=True)
    _log_turn_result("think_once", payload, frame, duration_ms)
    return JSONResponse(frame)


def _resolve_identity_from_query(request: Request) -> tuple[str, str]:
    session_id = str(request.query_params.get("user_id") or request.query_params.get("session_id") or "default_session")
    profile_user_id = str(request.query_params.get("real_user_id") or session_id)
    return session_id, profile_user_id


def _resolve_identity_from_payload(payload: dict) -> tuple[str, str]:
    session_id = str(payload.get("user_id") or payload.get("session_id") or "default_session")
    profile_user_id = str(payload.get("real_user_id") or session_id)
    return session_id, profile_user_id


def _build_session_state_payload(
    *,
    profile_user_id: str,
    session_id: str,
    ensure: bool,
    display_title: str | None = None,
) -> dict[str, object]:
    session = (
        engine.store.ensure_session(
            profile_user_id=profile_user_id,
            session_id=session_id,
            display_title=display_title,
        )
        if ensure
        else engine.store.get_session(profile_user_id, session_id)
    )
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")

    sessions = engine.store.list_sessions(profile_user_id=profile_user_id, limit=50)
    messages = engine.store.get_session_messages(
        profile_user_id=profile_user_id,
        session_id=session_id,
        limit=120,
    )
    latest_eval = engine.store.get_latest_eval_turn_for_session(
        profile_user_id=profile_user_id,
        session_id=session_id,
    )
    latest_final_json = latest_eval.get("final_json") if latest_eval else None
    if not isinstance(latest_final_json, dict):
        latest_final_json = None

    return {
        "session": session,
        "sessions": sessions,
        "messages": messages,
        "latest_final_json": latest_final_json,
    }


@app.get("/sessions")
async def sessions_list(request: Request):
    started_at = time.perf_counter()
    session_id, profile_user_id = _resolve_identity_from_query(request)
    try:
        sessions = engine.store.list_sessions(profile_user_id=profile_user_id, limit=50)
    except Exception as exc:
        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("sessions_list", duration_ms=duration_ms, ok=False)
        _log_event("sessions_list_error", session_id=session_id, profile_user_id=profile_user_id, message=str(exc))
        raise

    duration_ms = (time.perf_counter() - started_at) * 1000
    runtime_metrics.observe_request("sessions_list", duration_ms=duration_ms, ok=True)
    return JSONResponse(
        {
            "sessions": sessions,
            "current_session_id": session_id,
        }
    )


@app.post("/sessions/ensure")
async def sessions_ensure(request: Request):
    started_at = time.perf_counter()
    payload = await request.json()
    session_id, profile_user_id = _resolve_identity_from_payload(payload)
    display_title = str(payload.get("display_title") or "").strip() or None

    try:
        response_payload = _build_session_state_payload(
            profile_user_id=profile_user_id,
            session_id=session_id,
            ensure=True,
            display_title=display_title,
        )
    except Exception as exc:
        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("sessions_ensure", duration_ms=duration_ms, ok=False)
        _log_event("sessions_ensure_error", session_id=session_id, profile_user_id=profile_user_id, message=str(exc))
        raise

    duration_ms = (time.perf_counter() - started_at) * 1000
    runtime_metrics.observe_request("sessions_ensure", duration_ms=duration_ms, ok=True)
    return JSONResponse(response_payload)


@app.post("/sessions/rename")
async def sessions_rename(request: Request):
    started_at = time.perf_counter()
    payload = await request.json()
    session_id, profile_user_id = _resolve_identity_from_payload(payload)
    display_title = str(payload.get("display_title") or "").strip()
    if not display_title:
        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("sessions_rename", duration_ms=duration_ms, ok=False)
        raise HTTPException(status_code=400, detail="display_title is required")

    renamed = engine.store.rename_session(
        profile_user_id=profile_user_id,
        session_id=session_id,
        display_title=display_title,
    )
    if renamed is None:
        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("sessions_rename", duration_ms=duration_ms, ok=False)
        raise HTTPException(status_code=404, detail="session not found")

    sessions = engine.store.list_sessions(profile_user_id=profile_user_id, limit=50)
    duration_ms = (time.perf_counter() - started_at) * 1000
    runtime_metrics.observe_request("sessions_rename", duration_ms=duration_ms, ok=True)
    return JSONResponse({"session": renamed, "sessions": sessions})


@app.get("/reminders/due")
async def reminders_due(request: Request):
    started_at = time.perf_counter()
    session_id, profile_user_id = _resolve_identity_from_query(request)
    now_ts = int(request.query_params.get("timestamp") or time.time())
    limit = max(1, int(request.query_params.get("limit") or 3))

    try:
        notifications = engine.consume_due_reminders(
            profile_user_id=profile_user_id,
            session_id=session_id,
            now_ts=now_ts,
            limit=limit,
        )
    except Exception as exc:
        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("reminders_due", duration_ms=duration_ms, ok=False)
        _log_event(
            "reminders_due_error",
            session_id=session_id,
            profile_user_id=profile_user_id,
            message=str(exc),
        )
        raise

    duration_ms = (time.perf_counter() - started_at) * 1000
    runtime_metrics.observe_request("reminders_due", duration_ms=duration_ms, ok=True)
    if notifications:
        _log_event(
            "reminders_due",
            session_id=session_id,
            profile_user_id=profile_user_id,
            count=len(notifications),
            reminder_ids=[item.get("reminder_id") for item in notifications],
        )
    return JSONResponse({"notifications": notifications})


@app.get("/reminders")
async def reminders_list(request: Request):
    started_at = time.perf_counter()
    session_id, profile_user_id = _resolve_identity_from_query(request)
    status = str(request.query_params.get("status") or "pending").strip().lower() or "pending"
    limit = max(1, int(request.query_params.get("limit") or 10))

    try:
        reminders = engine.store.list_reminders(
            profile_user_id=profile_user_id,
            session_id=session_id,
            status=status,
            limit=limit,
        )
    except Exception as exc:
        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("reminders_list", duration_ms=duration_ms, ok=False)
        _log_event(
            "reminders_list_error",
            session_id=session_id,
            profile_user_id=profile_user_id,
            message=str(exc),
        )
        raise

    duration_ms = (time.perf_counter() - started_at) * 1000
    runtime_metrics.observe_request("reminders_list", duration_ms=duration_ms, ok=True)
    return JSONResponse({"reminders": reminders})


@app.post("/reminders/cancel")
async def reminders_cancel(request: Request):
    started_at = time.perf_counter()
    payload = await request.json()
    session_id, profile_user_id = _resolve_identity_from_payload(payload)
    reminder_id = str(payload.get("reminder_id") or "").strip()
    if not reminder_id:
        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("reminders_cancel", duration_ms=duration_ms, ok=False)
        raise HTTPException(status_code=400, detail="reminder_id is required")

    cancelled = engine.store.cancel_reminder(
        profile_user_id=profile_user_id,
        session_id=session_id,
        reminder_id=reminder_id,
    )
    duration_ms = (time.perf_counter() - started_at) * 1000
    runtime_metrics.observe_request("reminders_cancel", duration_ms=duration_ms, ok=cancelled is not None)
    if cancelled is None:
        raise HTTPException(status_code=404, detail="pending reminder not found")
    _log_event(
        "reminders_cancel",
        session_id=session_id,
        profile_user_id=profile_user_id,
        reminder_id=reminder_id,
    )
    return JSONResponse({"reminder": cancelled})
