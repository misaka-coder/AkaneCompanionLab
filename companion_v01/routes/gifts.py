from __future__ import annotations

import time
from typing import Any, Callable
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse


LogEvent = Callable[..., None]
ResolveIdentityFromQuery = Callable[[Request], tuple[str, str]]
ResolveIdentityFromPayload = Callable[[dict], tuple[str, str]]


def build_gifts_router(
    *,
    engine: Any,
    runtime_metrics: Any,
    log_event: LogEvent,
    resolve_identity_from_query: ResolveIdentityFromQuery,
    resolve_identity_from_payload: ResolveIdentityFromPayload,
) -> APIRouter:
    router = APIRouter()

    @router.get("/gifts")
    async def gifts_list(request: Request):
        started_at = time.perf_counter()
        session_id, profile_user_id = resolve_identity_from_query(request)
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
            log_event("gifts_list_error", session_id=session_id, profile_user_id=profile_user_id, message=str(exc))
            raise

        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("gifts_list", duration_ms=duration_ms, ok=True)
        return JSONResponse({"items": items})

    @router.get("/gifts/inventory")
    async def gifts_inventory(request: Request):
        started_at = time.perf_counter()
        session_id, profile_user_id = resolve_identity_from_query(request)
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
            log_event("gifts_inventory_error", session_id=session_id, profile_user_id=profile_user_id, message=str(exc))
            raise

        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("gifts_inventory", duration_ms=duration_ms, ok=True)
        return JSONResponse(payload)

    @router.get("/artifacts/containers")
    async def artifact_containers(request: Request):
        started_at = time.perf_counter()
        session_id, profile_user_id = resolve_identity_from_query(request)
        preview_limit = max(1, min(12, int(request.query_params.get("preview_limit") or 3)))
        include_empty = (
            str(request.query_params.get("include_empty") or "true").strip().lower()
            not in {"0", "false", "no", "off"}
        )
        try:
            containers = engine.list_artifact_containers(
                profile_user_id=profile_user_id,
                preview_limit=preview_limit,
                include_empty=include_empty,
            )
        except Exception as exc:
            duration_ms = (time.perf_counter() - started_at) * 1000
            runtime_metrics.observe_request("artifact_containers", duration_ms=duration_ms, ok=False)
            log_event(
                "artifact_containers_error",
                session_id=session_id,
                profile_user_id=profile_user_id,
                message=str(exc),
            )
            raise

        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("artifact_containers", duration_ms=duration_ms, ok=True)
        return JSONResponse({"containers": containers})

    @router.get("/artifacts/container")
    async def artifact_container_items(request: Request):
        started_at = time.perf_counter()
        session_id, profile_user_id = resolve_identity_from_query(request)
        container_type = str(
            request.query_params.get("container_type") or request.query_params.get("type") or ""
        ).strip().lower()
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
            log_event(
                "artifact_container_items_error",
                session_id=session_id,
                profile_user_id=profile_user_id,
                message=str(exc),
            )
            raise

        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("artifact_container_items", duration_ms=duration_ms, ok=True)
        return JSONResponse(payload)

    @router.post("/gifts/upload")
    async def gifts_upload(request: Request):
        started_at = time.perf_counter()
        session_id, profile_user_id = resolve_identity_from_query(request)
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
            log_event("gifts_upload_error", session_id=session_id, profile_user_id=profile_user_id, message=str(exc))
            raise

        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("gifts_upload", duration_ms=duration_ms, ok=True)
        log_event(
            "gifts_upload",
            session_id=session_id,
            profile_user_id=profile_user_id,
            asset_id=asset.get("asset_id"),
            display_name=asset.get("display_name"),
        )
        return JSONResponse(
            {
                "asset": asset,
                "assistant_line": build_gift_assistant_line(action="upload", asset=asset),
            }
        )

    @router.post("/gifts/action")
    async def gifts_action(request: Request):
        started_at = time.perf_counter()
        payload = await request.json()
        session_id, profile_user_id = resolve_identity_from_payload(payload)
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
            log_event("gifts_action_error", session_id=session_id, profile_user_id=profile_user_id, message=str(exc))
            raise

        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("gifts_action", duration_ms=duration_ms, ok=asset is not None)
        if asset is None:
            raise HTTPException(status_code=404, detail="gift asset not found")
        log_event(
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
                "assistant_line": build_gift_assistant_line(action=action, asset=asset),
                "manifest_refresh_required": action in {"internalize", "remove", "purge"},
            }
        )

    @router.post("/gifts/observe")
    async def gifts_observe(request: Request):
        started_at = time.perf_counter()
        payload = await request.json()
        session_id, profile_user_id = resolve_identity_from_payload(payload)
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
            log_event("gifts_observe_error", session_id=session_id, profile_user_id=profile_user_id, message=str(exc))
            raise

        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("gifts_observe", duration_ms=duration_ms, ok=result is not None)
        if result is None:
            raise HTTPException(status_code=404, detail="gift asset not found")
        log_event(
            "gifts_observe",
            session_id=session_id,
            profile_user_id=profile_user_id,
            asset_id=asset_id,
        )
        return JSONResponse(result)

    return router


def build_gift_assistant_line(*, action: str, asset: dict[str, object]) -> str:
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
