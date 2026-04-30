from __future__ import annotations

import time
from pathlib import Path
from typing import Any


DESKTOP_PET_AUDIO_EXTENSIONS = {"mp3", "wav", "flac", "m4a", "aac", "ogg", "opus", "webm"}


def ingest_desktop_pet_audio_attachment(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str,
    source_path: Path | str,
    origin_name: str = "",
    mime_type: str = "",
    timestamp: int | None = None,
) -> dict[str, Any]:
    service = engine._get_attachment_ingest_service()
    if service is None:
        raise RuntimeError("attachment ingest service unavailable")
    return service.ingest_local_file(
        profile_user_id=profile_user_id,
        session_id=session_id,
        source_path=source_path,
        origin_name=origin_name,
        mime_type=mime_type,
        kind="audio",
        source="desktop_pet",
        timestamp=timestamp,
    )


def resolve_desktop_pet_audio_attachment(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str,
    target: str,
) -> tuple[dict[str, Any], Path] | None:
    service = engine._get_attachment_inbox_service()
    if service is None:
        return None
    item = service.resolve_attachment(
        profile_user_id=profile_user_id,
        session_id=session_id,
        target=target,
        kind="audio",
    )
    if not item or str(item.get("status") or "") != "ready" or str(item.get("kind") or "") != "audio":
        return None
    source_path = service.resolve_storage_path(item)
    if source_path is None:
        return None
    return item, source_path


def resolve_desktop_pet_generated_audio(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str,
    target: str,
) -> tuple[dict[str, Any], Path] | None:
    service = engine._get_generated_file_service()
    if service is None:
        return None
    item = service._resolve_generated_file(
        profile_user_id=profile_user_id,
        session_id=session_id,
        target=target,
    )
    if not item or str(item.get("status") or "") != "ready":
        return None
    path = service.absolute_path(item)
    if not path.exists() or not path.is_file():
        return None
    ext = str(item.get("file_ext") or item.get("output_format") or path.suffix.lstrip(".")).strip().lower().lstrip(".")
    mime_type = str(item.get("mime_type") or "").strip().lower()
    if ext not in DESKTOP_PET_AUDIO_EXTENSIONS and not mime_type.startswith("audio/"):
        return None
    return item, path


def resolve_desktop_pet_attachment_file(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str,
    target: str,
) -> tuple[dict[str, Any], Path] | None:
    service = engine._get_attachment_inbox_service()
    if service is None:
        return None
    item = service.resolve_attachment(
        profile_user_id=profile_user_id,
        session_id=session_id,
        target=target,
        kind="any",
    )
    if not item or str(item.get("status") or "") != "ready":
        return None
    source_path = service.resolve_storage_path(item)
    if source_path is None or not source_path.exists() or not source_path.is_file():
        return None
    return item, source_path


def resolve_desktop_pet_generated_file(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str,
    target: str,
) -> tuple[dict[str, Any], Path] | None:
    service = engine._get_generated_file_service()
    if service is None:
        return None
    item = service._resolve_generated_file(
        profile_user_id=profile_user_id,
        session_id=session_id,
        target=target,
    )
    if not item or str(item.get("status") or "") != "ready":
        return None
    path = service.absolute_path(item)
    if not path.exists() or not path.is_file():
        return None
    return item, path


def build_desktop_pet_workspace_panel(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str,
    limit: int = 24,
) -> dict[str, Any]:
    max_items = max(1, min(60, int(limit or 24)))
    attachment_service = engine._get_attachment_inbox_service()
    generated_service = engine._get_generated_file_service()
    task_service = engine._get_task_workspace_service()

    files: list[dict[str, Any]] = []
    if attachment_service is not None:
        attachments = engine.store.list_attachment_inbox_items(
            profile_user_id=profile_user_id,
            session_id=session_id,
            statuses=["ready", "pending_observation", "failed"],
            limit=max_items,
        )
        files = [desktop_workspace_attachment_card(item) for item in attachments]

    outputs: list[dict[str, Any]] = []
    if generated_service is not None:
        generated = engine.store.list_generated_files(
            profile_user_id=profile_user_id,
            session_id=session_id,
            statuses=["ready", "failed"],
            limit=max_items,
        )
        outputs = [desktop_workspace_generated_card(item) for item in generated]

    tasks: list[dict[str, Any]] = []
    if task_service is not None:
        task_items = task_service.list_status_summaries(
            profile_user_id=profile_user_id,
            session_id=session_id,
            limit=min(12, max_items),
        )
        tasks = [desktop_workspace_task_card(item) for item in task_items]

    return {
        "ok": True,
        "updated_at": int(time.time()),
        "sections": {
            "files": files,
            "outputs": outputs,
            "tasks": tasks,
        },
        "counts": {
            "files": len(files),
            "outputs": len(outputs),
            "tasks": len(tasks),
        },
    }


def manage_desktop_pet_workspace_panel(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str,
    action: str,
    item_type: str = "",
    target: str = "",
) -> dict[str, Any]:
    normalized_action = str(action or "").strip().lower()
    normalized_type = str(item_type or "").strip().lower()
    normalized_target = str(target or "").strip()
    now_ts = int(time.time())

    if normalized_action not in {"clear", "hide", "archive", "clear_completed_tasks", "clear_files", "clear_workspace_files"}:
        return {"ok": False, "error": "unsupported_action", "managed": []}

    if normalized_action in {"clear_files", "clear_workspace_files"}:
        return clear_desktop_workspace_files(
            engine,
            profile_user_id=profile_user_id,
            session_id=session_id,
            timestamp=now_ts,
        )

    if normalized_action == "clear_completed_tasks":
        return clear_desktop_workspace_completed_tasks(
            engine,
            profile_user_id=profile_user_id,
            session_id=session_id,
            timestamp=now_ts,
        )

    if normalized_type in {"attachment", "file", "source"}:
        if not normalized_target:
            return {"ok": False, "error": "missing_target", "managed": []}
        cleared = engine.store.clear_attachment_inbox_items(
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=normalized_target,
            timestamp=now_ts,
        )
        return {
            "ok": bool(cleared),
            "managed": [desktop_workspace_attachment_card(item) for item in cleared],
            "action": "clear",
            "item_type": "attachment",
        }

    if normalized_type in {"generated", "output"}:
        if not normalized_target:
            return {"ok": False, "error": "missing_target", "managed": []}
        service = engine._get_generated_file_service()
        if service is None:
            return {"ok": False, "error": "generated_service_unavailable", "managed": []}
        result = service.manage_generated_files(
            profile_user_id=profile_user_id,
            session_id=session_id,
            action="archive",
            targets=[normalized_target],
            reason="用户从桌宠手边物品面板收起。",
            timestamp=now_ts,
        )
        return {
            "ok": bool(result.get("ok")),
            "managed": [desktop_workspace_generated_card(item) for item in list(result.get("managed") or [])],
            "unresolved": list(result.get("unresolved") or []),
            "action": "archive",
            "item_type": "generated",
        }

    if normalized_type == "task":
        if not normalized_target:
            return {"ok": False, "error": "missing_target", "managed": []}
        service = engine._get_task_workspace_service()
        if service is None:
            return {"ok": False, "error": "task_service_unavailable", "managed": []}
        task = service.get_task(normalized_target)
        if not task:
            return {"ok": False, "error": "task_not_found", "managed": []}
        status = str(task.get("status") or "").strip().lower()
        if status not in {"completed", "failed", "canceled", "waiting_user"}:
            return {"ok": False, "error": "task_not_clearable", "managed": []}
        cleaned = service.cleanup_task(
            task_id=normalized_target,
            mode="desktop_panel",
            reason="用户从桌宠手边物品面板清理。",
            timestamp=now_ts,
        )
        return {
            "ok": bool(cleaned),
            "managed": [desktop_workspace_task_card(cleaned)] if cleaned else [],
            "action": "clear",
            "item_type": "task",
        }

    return {"ok": False, "error": "unsupported_item_type", "managed": []}


def prepare_desktop_music_timeline(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str,
    activity: dict[str, Any] | None,
) -> dict[str, Any]:
    service = engine._get_desktop_music_timeline_service()
    if service is None:
        return {"ok": False, "error": "timeline_service_unavailable", "timeline": None}
    return service.prepare_timeline(
        profile_user_id=profile_user_id,
        session_id=session_id,
        activity=activity,
    )


def clear_desktop_workspace_files(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str,
    timestamp: int,
) -> dict[str, Any]:
    managed: list[dict[str, Any]] = []
    cleared_attachments = engine.store.clear_attachment_inbox_items(
        profile_user_id=profile_user_id,
        session_id=session_id,
        target="all",
        timestamp=timestamp,
    )
    managed.extend(desktop_workspace_attachment_card(item) for item in cleared_attachments)

    generated_service = engine._get_generated_file_service()
    generated_result: dict[str, Any] = {}
    if generated_service is not None:
        generated_result = generated_service.manage_generated_files(
            profile_user_id=profile_user_id,
            session_id=session_id,
            action="archive",
            targets=["all"],
            reason="用户从桌宠手边物品面板一键清理文件筐。",
            timestamp=timestamp,
        )
        managed.extend(
            desktop_workspace_generated_card(item)
            for item in list(generated_result.get("managed") or [])
        )

    return {
        "ok": True,
        "managed": managed,
        "unresolved": list(generated_result.get("unresolved") or []) if generated_result else [],
        "action": "clear_files",
        "item_type": "workspace_files",
    }


def clear_desktop_workspace_completed_tasks(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str,
    timestamp: int,
) -> dict[str, Any]:
    service = engine._get_task_workspace_service()
    if service is None:
        return {"ok": False, "error": "task_service_unavailable", "managed": []}
    tasks = engine.store.list_task_workspaces(
        profile_user_id=profile_user_id,
        session_id=session_id,
        statuses=["completed", "failed", "canceled"],
        limit=50,
    )
    managed: list[dict[str, Any]] = []
    for task in tasks:
        task_id = str(task.get("task_id") or "").strip()
        if not task_id:
            continue
        cleaned = service.cleanup_task(
            task_id=task_id,
            mode="desktop_panel_batch",
            reason="用户从桌宠手边物品面板清理已完成任务。",
            timestamp=timestamp,
        )
        if cleaned:
            managed.append(desktop_workspace_task_card(cleaned))
    return {
        "ok": True,
        "managed": managed,
        "action": "clear_completed_tasks",
        "item_type": "task",
    }


def desktop_workspace_attachment_card(item: dict[str, Any]) -> dict[str, Any]:
    detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
    media_info = detail.get("media_info") if isinstance(detail.get("media_info"), dict) else {}
    handle = str(item.get("attachment_handle") or item.get("attachment_id") or "").strip()
    title = clip_desktop_workspace_text(
        item.get("summary_title") or item.get("origin_name") or handle or "手边文件",
        80,
    )
    kind = str(item.get("kind") or "file").strip().lower()
    ext = str(item.get("file_ext") or "").strip().lower().lstrip(".")
    status = str(item.get("status") or "").strip().lower()
    return {
        "item_type": "attachment",
        "id": handle,
        "handle": handle,
        "title": title,
        "subtitle": desktop_workspace_attachment_subtitle(kind, ext),
        "kind": kind,
        "format": ext,
        "status": status,
        "status_label": desktop_workspace_status_label(status),
        "size_bytes": int(item.get("file_size") or 0),
        "duration_seconds": media_info.get("duration_seconds"),
        "updated_at": int(item.get("updated_at") or item.get("created_at") or 0),
        "can_open": status == "ready",
        "can_clear": status in {"ready", "pending_observation", "failed"},
    }


def desktop_workspace_generated_card(item: dict[str, Any]) -> dict[str, Any]:
    handle = str(item.get("generated_handle") or item.get("generated_id") or "").strip()
    output_format = str(item.get("output_format") or item.get("file_ext") or "").strip().lower().lstrip(".")
    status = str(item.get("status") or "").strip().lower()
    title = clip_desktop_workspace_text(item.get("output_title") or handle or "做好的东西", 80)
    return {
        "item_type": "generated",
        "id": handle,
        "handle": handle,
        "title": title,
        "subtitle": f"{desktop_workspace_format_label(output_format)} · 做好的东西",
        "format": output_format,
        "status": status,
        "status_label": desktop_workspace_status_label(status),
        "size_bytes": int(item.get("file_size") or 0),
        "updated_at": int(item.get("updated_at") or item.get("created_at") or 0),
        "can_open": status == "ready",
        "can_clear": status in {"ready", "failed"},
    }


def desktop_workspace_task_card(item: dict[str, Any] | None) -> dict[str, Any]:
    task = item if isinstance(item, dict) else {}
    task_id = str(task.get("task_id") or "").strip()
    status = str(task.get("status") or "").strip().lower()
    title = clip_desktop_workspace_text(task.get("title") or "后台任务", 80)
    summary = clip_desktop_workspace_text(task.get("summary") or "", 120)
    return {
        "item_type": "task",
        "id": task_id,
        "handle": task_id,
        "title": title,
        "subtitle": summary or "后台任务",
        "status": status,
        "status_label": desktop_workspace_status_label(status),
        "updated_at": int(task.get("updated_at") or 0),
        "can_open": False,
        "can_clear": status in {"completed", "failed", "canceled"},
    }


def desktop_workspace_attachment_subtitle(kind: str, ext: str) -> str:
    kind_label = {
        "image": "图片",
        "audio": "音频",
        "document": "文档",
        "file": "文件",
    }.get(kind, "文件")
    format_label = desktop_workspace_format_label(ext)
    return f"{kind_label} · {format_label}" if format_label else kind_label


def desktop_workspace_format_label(value: str) -> str:
    text = str(value or "").strip().lower().lstrip(".")
    if not text:
        return ""
    labels = {
        "md": "Markdown",
        "txt": "文本",
        "docx": "Word",
        "xlsx": "Excel",
        "pdf": "PDF",
        "json": "JSON",
        "csv": "CSV",
        "html": "HTML",
        "zip": "压缩包",
        "mp3": "MP3",
        "wav": "WAV",
        "flac": "FLAC",
        "m4a": "M4A",
        "aac": "AAC",
        "ogg": "OGG",
        "opus": "OPUS",
    }
    return labels.get(text, text.upper())


def desktop_workspace_status_label(status: str) -> str:
    return {
        "ready": "已放好",
        "pending_observation": "整理中",
        "failed": "失败",
        "queued": "排队中",
        "running": "进行中",
        "waiting_user": "等确认",
        "blocked": "等确认",
        "partial": "部分完成",
        "completed": "已完成",
        "canceled": "已取消",
        "cleaned": "已收起",
    }.get(str(status or "").strip().lower(), str(status or "").strip() or "未知")


def clip_desktop_workspace_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split()).strip()
    max_len = max(1, int(limit or 1))
    return text[:max_len]
