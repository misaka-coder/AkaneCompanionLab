from __future__ import annotations

import asyncio
import mimetypes
import tempfile
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse


LogEvent = Callable[..., None]
ResolveIdentityFromQuery = Callable[[Request], tuple[str, str]]
ResolveIdentityFromPayload = Callable[[dict], tuple[str, str]]

DESKTOP_PET_AUDIO_SUFFIXES = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus"}


def build_desktop_pet_router(
    *,
    engine: Any,
    config_module: Any,
    runtime_metrics: Any,
    log_event: LogEvent,
    resolve_identity_from_query: ResolveIdentityFromQuery,
    resolve_identity_from_payload: ResolveIdentityFromPayload,
) -> APIRouter:
    router = APIRouter()

    @router.get("/task-workspace/status")
    async def task_workspace_status(request: Request):
        started_at = time.perf_counter()
        session_id, profile_user_id = resolve_identity_from_query(request)
        scope = str(request.query_params.get("scope") or "profile").strip().lower() or "profile"
        limit = max(1, min(50, int(request.query_params.get("limit") or 20)))
        query_session_id = session_id if scope == "session" else None

        try:
            service = getattr(engine, "task_workspace_service", None)
            if service is None and hasattr(engine, "_get_task_workspace_service"):
                service = engine._get_task_workspace_service()
            items = (
                service.list_status_summaries(
                    profile_user_id=profile_user_id,
                    session_id=query_session_id,
                    limit=limit,
                )
                if service is not None
                else []
            )
        except Exception as exc:
            duration_ms = (time.perf_counter() - started_at) * 1000
            runtime_metrics.observe_request("task_workspace_status", duration_ms=duration_ms, ok=False)
            log_event(
                "task_workspace_status_error",
                session_id=session_id,
                profile_user_id=profile_user_id,
                message=str(exc),
            )
            raise

        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("task_workspace_status", duration_ms=duration_ms, ok=True)
        return JSONResponse({"items": items}, headers={"Cache-Control": "no-store"})

    @router.get("/desktop-pet/workspace/summary")
    async def desktop_pet_workspace_summary(request: Request):
        started_at = time.perf_counter()
        session_id, profile_user_id = resolve_identity_from_query(request)
        limit = max(1, min(60, int(request.query_params.get("limit") or 24)))
        try:
            payload = await asyncio.to_thread(
                engine.build_desktop_pet_workspace_panel,
                profile_user_id=profile_user_id,
                session_id=session_id,
                limit=limit,
            )
            decorate_desktop_workspace_urls(
                payload,
                session_id=session_id,
                profile_user_id=profile_user_id,
            )
        except Exception as exc:
            duration_ms = (time.perf_counter() - started_at) * 1000
            runtime_metrics.observe_request("desktop_pet_workspace_summary", duration_ms=duration_ms, ok=False)
            log_event(
                "desktop_pet_workspace_summary_error",
                session_id=session_id,
                profile_user_id=profile_user_id,
                message=str(exc),
            )
            raise HTTPException(status_code=500, detail=f"Workspace summary failed: {exc}") from exc

        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("desktop_pet_workspace_summary", duration_ms=duration_ms, ok=True)
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @router.post("/desktop-pet/workspace/action")
    async def desktop_pet_workspace_action(request: Request):
        started_at = time.perf_counter()
        try:
            payload = await request.json()
        except Exception as exc:
            runtime_metrics.observe_request(
                "desktop_pet_workspace_action",
                duration_ms=(time.perf_counter() - started_at) * 1000,
                ok=False,
            )
            raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {exc}") from exc

        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Payload must be an object")

        session_id, profile_user_id = resolve_identity_from_payload(payload)
        action = str(payload.get("action") or "").strip()
        item_type = str(payload.get("item_type") or payload.get("type") or "").strip()
        target = str(payload.get("target") or payload.get("id") or payload.get("handle") or "").strip()
        try:
            result = await asyncio.to_thread(
                engine.manage_desktop_pet_workspace_panel,
                profile_user_id=profile_user_id,
                session_id=session_id,
                action=action,
                item_type=item_type,
                target=target,
            )
        except Exception as exc:
            duration_ms = (time.perf_counter() - started_at) * 1000
            runtime_metrics.observe_request("desktop_pet_workspace_action", duration_ms=duration_ms, ok=False)
            log_event(
                "desktop_pet_workspace_action_error",
                session_id=session_id,
                profile_user_id=profile_user_id,
                message=str(exc),
            )
            raise HTTPException(status_code=500, detail=f"Workspace action failed: {exc}") from exc

        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request(
            "desktop_pet_workspace_action",
            duration_ms=duration_ms,
            ok=bool(result.get("ok")),
        )
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @router.post("/desktop-pet/workspace/import-local")
    async def desktop_pet_workspace_import_local(request: Request):
        started_at = time.perf_counter()
        try:
            payload = await request.json()
        except Exception as exc:
            runtime_metrics.observe_request(
                "desktop_pet_workspace_import_local",
                duration_ms=(time.perf_counter() - started_at) * 1000,
                ok=False,
            )
            raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {exc}") from exc

        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Payload must be an object")

        session_id, profile_user_id = resolve_identity_from_payload(payload)
        raw_paths = payload.get("paths")
        if raw_paths is None and payload.get("path") is not None:
            raw_paths = [payload.get("path")]
        recursive = str(payload.get("recursive") or "").strip().lower() in {"1", "true", "yes"}
        max_files = coerce_optional_int(payload.get("max_files") or payload.get("limit")) or 40

        try:
            result = await asyncio.to_thread(
                engine.import_desktop_pet_local_paths,
                profile_user_id=profile_user_id,
                session_id=session_id,
                paths=raw_paths,
                recursive=recursive,
                max_files=max_files,
                timestamp=int(time.time()),
            )
            decorate_desktop_workspace_attachment_urls(
                list(result.get("items") or []),
                session_id=session_id,
                profile_user_id=profile_user_id,
            )
        except ValueError as exc:
            runtime_metrics.observe_request(
                "desktop_pet_workspace_import_local",
                duration_ms=(time.perf_counter() - started_at) * 1000,
                ok=False,
            )
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            duration_ms = (time.perf_counter() - started_at) * 1000
            runtime_metrics.observe_request("desktop_pet_workspace_import_local", duration_ms=duration_ms, ok=False)
            log_event(
                "desktop_pet_workspace_import_local_error",
                session_id=session_id,
                profile_user_id=profile_user_id,
                message=str(exc),
            )
            raise HTTPException(status_code=500, detail=f"Workspace import failed: {exc}") from exc

        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request(
            "desktop_pet_workspace_import_local",
            duration_ms=duration_ms,
            ok=bool(result.get("ok")),
        )
        log_event(
            "desktop_pet_workspace_import_local",
            session_id=session_id,
            profile_user_id=profile_user_id,
            imported=int(result.get("imported") or 0),
            skipped=int(result.get("skipped_count") or 0),
            duration_ms=round(duration_ms, 1),
        )
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @router.post("/desktop-pet/attachments/audio")
    async def desktop_pet_upload_audio(request: Request):
        started_at = time.perf_counter()
        try:
            form = await request.form()
        except Exception as exc:
            runtime_metrics.observe_request(
                "desktop_pet_audio_upload",
                duration_ms=(time.perf_counter() - started_at) * 1000,
                ok=False,
            )
            raise HTTPException(status_code=400, detail=f"Invalid multipart form: {exc}") from exc

        upload = form.get("file") or form.get("audio")
        if upload is None or not hasattr(upload, "read"):
            runtime_metrics.observe_request(
                "desktop_pet_audio_upload",
                duration_ms=(time.perf_counter() - started_at) * 1000,
                ok=False,
            )
            raise HTTPException(status_code=400, detail="Missing audio file")

        session_id, profile_user_id = resolve_identity_from_form_or_query(request, form)
        filename = safe_upload_filename(str(getattr(upload, "filename", "") or "akane_audio.mp3"))
        content_type = str(getattr(upload, "content_type", "") or mimetypes.guess_type(filename)[0] or "").strip()
        suffix = Path(filename).suffix.lower()
        if suffix not in DESKTOP_PET_AUDIO_SUFFIXES and not content_type.startswith("audio/"):
            runtime_metrics.observe_request(
                "desktop_pet_audio_upload",
                duration_ms=(time.perf_counter() - started_at) * 1000,
                ok=False,
            )
            raise HTTPException(status_code=400, detail="Only audio files are supported")

        audio_bytes = await upload.read()
        max_bytes = int(
            getattr(config_module, "DESKTOP_PET_AUDIO_UPLOAD_MAX_BYTES", 200 * 1024 * 1024)
            or (200 * 1024 * 1024)
        )
        if not audio_bytes:
            runtime_metrics.observe_request(
                "desktop_pet_audio_upload",
                duration_ms=(time.perf_counter() - started_at) * 1000,
                ok=False,
            )
            raise HTTPException(status_code=400, detail="Audio file is empty")
        if len(audio_bytes) > max_bytes:
            runtime_metrics.observe_request(
                "desktop_pet_audio_upload",
                duration_ms=(time.perf_counter() - started_at) * 1000,
                ok=False,
            )
            raise HTTPException(status_code=413, detail=f"Audio file is too large, limit is {max_bytes} bytes")

        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix or ".audio") as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name
            item = await asyncio.to_thread(
                engine.ingest_desktop_pet_audio_attachment,
                profile_user_id=profile_user_id,
                session_id=session_id,
                source_path=tmp_path,
                origin_name=filename,
                mime_type=content_type,
                timestamp=int(time.time()),
            )
        except Exception as exc:
            runtime_metrics.observe_request(
                "desktop_pet_audio_upload",
                duration_ms=(time.perf_counter() - started_at) * 1000,
                ok=False,
            )
            log_event(
                "desktop_pet_audio_upload_error",
                session_id=session_id,
                profile_user_id=profile_user_id,
                message=str(exc),
            )
            raise HTTPException(status_code=500, detail=f"Audio upload failed: {exc}") from exc
        finally:
            if tmp_path:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except Exception:
                    pass

        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("desktop_pet_audio_upload", duration_ms=duration_ms, ok=True)
        log_event(
            "desktop_pet_audio_uploaded",
            session_id=session_id,
            profile_user_id=profile_user_id,
            handle=str(item.get("attachment_handle") or ""),
            filename=filename,
            size=len(audio_bytes),
            duration_ms=round(duration_ms, 1),
        )
        return JSONResponse(
            {
                "ok": True,
                "attachment": build_desktop_audio_attachment_payload(
                    item,
                    session_id=session_id,
                    profile_user_id=profile_user_id,
                ),
            }
        )

    @router.get("/desktop-pet/attachments/{attachment_handle}/content")
    async def desktop_pet_attachment_content(request: Request, attachment_handle: str):
        session_id, profile_user_id = resolve_identity_from_query(request)
        resolved = engine.resolve_desktop_pet_audio_attachment(
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=attachment_handle,
        )
        if resolved is None:
            raise HTTPException(status_code=404, detail="Audio attachment not found")
        item, path = resolved
        media_type = str(item.get("mime_type") or mimetypes.guess_type(str(path))[0] or "application/octet-stream")
        return FileResponse(
            path,
            media_type=media_type,
            filename=str(item.get("origin_name") or item.get("summary_title") or path.name),
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/desktop-pet/generated/{generated_handle}/content")
    async def desktop_pet_generated_audio_content(request: Request, generated_handle: str):
        session_id, profile_user_id = resolve_identity_from_query(request)
        resolved = engine.resolve_desktop_pet_generated_audio(
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=generated_handle,
        )
        if resolved is None:
            raise HTTPException(status_code=404, detail="Generated audio not found")
        item, path = resolved
        media_type = str(item.get("mime_type") or mimetypes.guess_type(str(path))[0] or "application/octet-stream")
        return FileResponse(
            path,
            media_type=media_type,
            filename=str(item.get("output_title") or item.get("generated_handle") or path.name),
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/desktop-pet/workspace/attachments/{attachment_handle}/content")
    async def desktop_pet_workspace_attachment_content(request: Request, attachment_handle: str):
        session_id, profile_user_id = resolve_identity_from_query(request)
        resolved = engine.resolve_desktop_pet_attachment_file(
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=attachment_handle,
        )
        if resolved is None:
            raise HTTPException(status_code=404, detail="Attachment not found")
        item, path = resolved
        media_type = str(item.get("mime_type") or mimetypes.guess_type(str(path))[0] or "application/octet-stream")
        return FileResponse(
            path,
            media_type=media_type,
            filename=str(item.get("origin_name") or item.get("summary_title") or path.name),
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/desktop-pet/workspace/attachments/{attachment_handle}/location")
    async def desktop_pet_workspace_attachment_location(request: Request, attachment_handle: str):
        session_id, profile_user_id = resolve_identity_from_query(request)
        resolved = engine.resolve_desktop_pet_attachment_file(
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=attachment_handle,
        )
        if resolved is None:
            raise HTTPException(status_code=404, detail="Attachment not found")
        _item, path = resolved
        return JSONResponse(
            {"ok": True, "path": str(Path(path).resolve())},
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/desktop-pet/workspace/generated/{generated_handle}/content")
    async def desktop_pet_workspace_generated_content(request: Request, generated_handle: str):
        session_id, profile_user_id = resolve_identity_from_query(request)
        resolved = engine.resolve_desktop_pet_generated_file(
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=generated_handle,
        )
        if resolved is None:
            raise HTTPException(status_code=404, detail="Generated file not found")
        item, path = resolved
        media_type = str(item.get("mime_type") or mimetypes.guess_type(str(path))[0] or "application/octet-stream")
        filename = str(item.get("output_title") or item.get("generated_handle") or path.stem)
        file_ext = str(item.get("file_ext") or item.get("output_format") or path.suffix.lstrip(".")).strip().lstrip(".")
        if file_ext and not filename.lower().endswith(f".{file_ext.lower()}"):
            filename = f"{filename}.{file_ext}"
        return FileResponse(
            path,
            media_type=media_type,
            filename=filename,
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/desktop-pet/workspace/generated/{generated_handle}/location")
    async def desktop_pet_workspace_generated_location(request: Request, generated_handle: str):
        session_id, profile_user_id = resolve_identity_from_query(request)
        resolved = engine.resolve_desktop_pet_generated_file(
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=generated_handle,
        )
        if resolved is None:
            raise HTTPException(status_code=404, detail="Generated file not found")
        _item, path = resolved
        return JSONResponse(
            {"ok": True, "path": str(Path(path).resolve())},
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/desktop-pet/music-timeline/prepare")
    async def desktop_pet_prepare_music_timeline(request: Request):
        started_at = time.perf_counter()
        try:
            payload = await request.json()
        except Exception as exc:
            runtime_metrics.observe_request(
                "desktop_pet_music_timeline_prepare",
                duration_ms=(time.perf_counter() - started_at) * 1000,
                ok=False,
            )
            raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {exc}") from exc

        session_id, profile_user_id = resolve_identity_from_payload(payload if isinstance(payload, dict) else {})
        activity = None
        if isinstance(payload, dict):
            activity = payload.get("activity") or payload.get("desktop_activity") or payload.get("current_activity")
        try:
            result = await asyncio.to_thread(
                engine.prepare_desktop_music_timeline,
                profile_user_id=profile_user_id,
                session_id=session_id,
                activity=activity if isinstance(activity, dict) else None,
            )
        except Exception as exc:
            runtime_metrics.observe_request(
                "desktop_pet_music_timeline_prepare",
                duration_ms=(time.perf_counter() - started_at) * 1000,
                ok=False,
            )
            log_event(
                "desktop_pet_music_timeline_prepare_error",
                session_id=session_id,
                profile_user_id=profile_user_id,
                message=str(exc),
            )
            raise HTTPException(status_code=500, detail=f"Timeline prepare failed: {exc}") from exc

        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request(
            "desktop_pet_music_timeline_prepare",
            duration_ms=duration_ms,
            ok=bool(result.get("ok")),
        )
        log_event(
            "desktop_pet_music_timeline_prepare",
            session_id=session_id,
            profile_user_id=profile_user_id,
            ok=bool(result.get("ok")),
            status=str((result.get("timeline") or {}).get("status") or ""),
            duration_ms=round(duration_ms, 1),
        )
        return JSONResponse(result)

    @router.post("/desktop-pet/vision/clip")
    async def desktop_pet_submit_screen_vision_clip(request: Request):
        started_at = time.perf_counter()
        try:
            payload = await request.json()
        except Exception as exc:
            runtime_metrics.observe_request(
                "desktop_pet_screen_vision_clip",
                duration_ms=(time.perf_counter() - started_at) * 1000,
                ok=False,
            )
            raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {exc}") from exc

        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Payload must be an object")

        session_id, profile_user_id = resolve_identity_from_payload(payload)
        frames = payload.get("frames")
        if not isinstance(frames, list) or not frames:
            runtime_metrics.observe_request(
                "desktop_pet_screen_vision_clip",
                duration_ms=(time.perf_counter() - started_at) * 1000,
                ok=False,
            )
            raise HTTPException(status_code=400, detail="frames must be a non-empty list")

        try:
            result = await asyncio.to_thread(
                engine.submit_desktop_screen_vision_clip,
                profile_user_id=profile_user_id,
                session_id=session_id,
                frames=frames,
                foreground=payload.get("foreground") if isinstance(payload.get("foreground"), dict) else None,
                captured_start_ts=coerce_optional_int(payload.get("captured_start_ts") or payload.get("capturedStartTs")),
                captured_end_ts=coerce_optional_int(payload.get("captured_end_ts") or payload.get("capturedEndTs")),
                mode=str(payload.get("mode") or ""),
            )
        except ValueError as exc:
            runtime_metrics.observe_request(
                "desktop_pet_screen_vision_clip",
                duration_ms=(time.perf_counter() - started_at) * 1000,
                ok=False,
            )
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            duration_ms = (time.perf_counter() - started_at) * 1000
            runtime_metrics.observe_request("desktop_pet_screen_vision_clip", duration_ms=duration_ms, ok=False)
            log_event(
                "desktop_pet_screen_vision_clip_error",
                session_id=session_id,
                profile_user_id=profile_user_id,
                message=str(exc),
            )
            raise HTTPException(status_code=500, detail=f"Screen vision clip failed: {exc}") from exc

        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("desktop_pet_screen_vision_clip", duration_ms=duration_ms, ok=True)
        return JSONResponse({"ok": True, "clip": result}, headers={"Cache-Control": "no-store"})

    @router.get("/desktop-pet/vision/latest")
    async def desktop_pet_latest_screen_vision(request: Request):
        started_at = time.perf_counter()
        session_id, profile_user_id = resolve_identity_from_query(request)
        limit = max(1, min(5, int(request.query_params.get("limit") or 3)))
        include_pending = str(request.query_params.get("include_pending") or "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        clip_id = str(request.query_params.get("clip_id") or "").strip()
        try:
            if clip_id:
                clip = await asyncio.to_thread(
                    engine.get_desktop_screen_vision_clip,
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    clip_id=clip_id,
                )
                items = [clip] if clip else []
            else:
                items = await asyncio.to_thread(
                    engine.list_desktop_screen_vision_observations,
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    limit=limit,
                    include_pending=include_pending,
                )
        except Exception as exc:
            duration_ms = (time.perf_counter() - started_at) * 1000
            runtime_metrics.observe_request("desktop_pet_screen_vision_latest", duration_ms=duration_ms, ok=False)
            log_event(
                "desktop_pet_screen_vision_latest_error",
                session_id=session_id,
                profile_user_id=profile_user_id,
                message=str(exc),
            )
            raise HTTPException(status_code=500, detail=f"Screen vision latest failed: {exc}") from exc

        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("desktop_pet_screen_vision_latest", duration_ms=duration_ms, ok=True)
        return JSONResponse({"items": items}, headers={"Cache-Control": "no-store"})

    @router.post("/desktop-pet/vision/reaction")
    async def desktop_pet_screen_vision_reaction(request: Request):
        started_at = time.perf_counter()
        try:
            payload = await request.json()
        except Exception as exc:
            runtime_metrics.observe_request(
                "desktop_pet_screen_vision_reaction",
                duration_ms=(time.perf_counter() - started_at) * 1000,
                ok=False,
            )
            raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {exc}") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Payload must be an object")

        session_id, profile_user_id = resolve_identity_from_payload(payload)
        clip_id = str(payload.get("clip_id") or payload.get("clipId") or "").strip()
        if not clip_id:
            raise HTTPException(status_code=400, detail="clip_id is required")

        try:
            result = await asyncio.to_thread(
                engine.build_desktop_screen_vision_reaction,
                profile_user_id=profile_user_id,
                session_id=session_id,
                clip_id=clip_id,
            )
        except Exception as exc:
            duration_ms = (time.perf_counter() - started_at) * 1000
            runtime_metrics.observe_request("desktop_pet_screen_vision_reaction", duration_ms=duration_ms, ok=False)
            log_event(
                "desktop_pet_screen_vision_reaction_error",
                session_id=session_id,
                profile_user_id=profile_user_id,
                clip_id=clip_id,
                message=str(exc),
            )
            raise HTTPException(status_code=500, detail=f"Screen vision reaction failed: {exc}") from exc

        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request(
            "desktop_pet_screen_vision_reaction",
            duration_ms=duration_ms,
            ok=bool(result.get("ok", True)),
        )
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @router.post("/desktop-pet/vision/clear")
    async def desktop_pet_clear_screen_vision(request: Request):
        started_at = time.perf_counter()
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        session_id, profile_user_id = resolve_identity_from_payload(payload)
        scope = str(payload.get("scope") or "session").strip().lower()
        try:
            result = await asyncio.to_thread(
                engine.clear_desktop_screen_vision_observations,
                profile_user_id=profile_user_id,
                session_id=session_id if scope != "profile" else None,
            )
        except Exception as exc:
            duration_ms = (time.perf_counter() - started_at) * 1000
            runtime_metrics.observe_request("desktop_pet_screen_vision_clear", duration_ms=duration_ms, ok=False)
            log_event(
                "desktop_pet_screen_vision_clear_error",
                session_id=session_id,
                profile_user_id=profile_user_id,
                message=str(exc),
            )
            raise HTTPException(status_code=500, detail=f"Screen vision clear failed: {exc}") from exc

        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("desktop_pet_screen_vision_clear", duration_ms=duration_ms, ok=True)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    return router


def resolve_identity_from_form_or_query(request: Request, form) -> tuple[str, str]:
    session_id = str(
        form.get("user_id")
        or form.get("session_id")
        or request.query_params.get("user_id")
        or request.query_params.get("session_id")
        or "default_session"
    )
    profile_user_id = str(form.get("real_user_id") or request.query_params.get("real_user_id") or session_id)
    return session_id, profile_user_id


def safe_upload_filename(value: str) -> str:
    name = Path(str(value or "").replace("\\", "/")).name.strip()
    if not name:
        return "akane_audio.mp3"
    cleaned = "".join(ch for ch in name if ch not in {"\x00", "\r", "\n"}).strip()
    return cleaned[:180] or "akane_audio.mp3"


def coerce_optional_int(value) -> int | None:
    try:
        number = int(float(value))
    except Exception:
        return None
    return number if number > 0 else None


def build_desktop_audio_attachment_payload(
    item: dict,
    *,
    session_id: str,
    profile_user_id: str,
) -> dict:
    detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
    media_info = detail.get("media_info") if isinstance(detail.get("media_info"), dict) else {}
    handle = str(item.get("attachment_handle") or item.get("attachment_id") or "").strip()
    title = str(item.get("summary_title") or item.get("origin_name") or handle or "未命名音频").strip()
    path_handle = quote(handle, safe="")
    query = (
        f"user_id={quote(str(session_id), safe='')}"
        f"&real_user_id={quote(str(profile_user_id), safe='')}"
    )
    return {
        "attachment_id": str(item.get("attachment_id") or ""),
        "handle": handle,
        "source_id": handle,
        "title": title,
        "origin_name": str(item.get("origin_name") or ""),
        "mime_type": str(item.get("mime_type") or ""),
        "file_ext": str(item.get("file_ext") or ""),
        "size_bytes": int(item.get("file_size") or 0),
        "duration_seconds": media_info.get("duration_seconds"),
        "status": str(item.get("status") or ""),
        "url": f"/desktop-pet/attachments/{path_handle}/content?{query}",
    }


def decorate_desktop_workspace_urls(
    payload: dict,
    *,
    session_id: str,
    profile_user_id: str,
) -> None:
    if not isinstance(payload, dict):
        return
    sections = payload.get("sections") if isinstance(payload.get("sections"), dict) else {}
    decorate_desktop_workspace_attachment_urls(
        list(sections.get("files") or []),
        session_id=session_id,
        profile_user_id=profile_user_id,
    )
    decorate_desktop_workspace_generated_urls(
        list(sections.get("outputs") or []),
        session_id=session_id,
        profile_user_id=profile_user_id,
    )


def decorate_desktop_workspace_attachment_urls(
    items: list,
    *,
    session_id: str,
    profile_user_id: str,
) -> None:
    query = (
        f"user_id={quote(str(session_id), safe='')}"
        f"&real_user_id={quote(str(profile_user_id), safe='')}"
    )
    for item in list(items or []):
        if not isinstance(item, dict) or not item.get("can_open"):
            continue
        handle = str(item.get("handle") or item.get("id") or "").strip()
        if handle:
            item["url"] = f"/desktop-pet/workspace/attachments/{quote(handle, safe='')}/content?{query}"


def decorate_desktop_workspace_generated_urls(
    items: list,
    *,
    session_id: str,
    profile_user_id: str,
) -> None:
    query = (
        f"user_id={quote(str(session_id), safe='')}"
        f"&real_user_id={quote(str(profile_user_id), safe='')}"
    )
    for item in list(items or []):
        if not isinstance(item, dict) or not item.get("can_open"):
            continue
        handle = str(item.get("handle") or item.get("id") or "").strip()
        if handle:
            item["url"] = f"/desktop-pet/workspace/generated/{quote(handle, safe='')}/content?{query}"
