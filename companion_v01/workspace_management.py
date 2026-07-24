from __future__ import annotations

import logging
import time
from typing import Any


logger = logging.getLogger("akane.workspace_management")

ACTIVE_ATTACHMENT_STATUSES = ("ready", "pending_observation", "failed")
ACTIVE_GENERATED_STATUSES = ("ready", "failed")
ATTACHMENT_HANDLE_PREFIXES = (
    "attachment::",
    "file_",
    "img_",
    "image_",
    "audio_",
    "video_",
)
GENERATED_HANDLE_PREFIXES = ("generated::", "gen_")
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "bmp"}
AUDIO_EXTENSIONS = {"mp3", "wav", "flac", "m4a", "aac", "ogg", "opus"}
VIDEO_EXTENSIONS = {"mp4", "mov", "mkv", "webm", "avi"}


def list_workspace_files(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str,
    limit: int = 200,
) -> dict[str, Any]:
    """Return the two user-visible file shelves behind Akane's workbench.

    Attachments and generated files keep their own storage authorities.  This
    facade only gives channels one coherent view, so QQ and desktop code do not
    need to guess which table a visible file came from.
    """

    bounded_limit = max(1, min(500, int(limit or 200)))
    attachment_service = _engine_service(engine, "_get_attachment_inbox_service")
    generated_service = _engine_service(engine, "_get_generated_file_service")
    attachments: list[dict[str, Any]] = []
    generated_files: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    if attachment_service is not None:
        try:
            attachments = [
                dict(item)
                for item in attachment_service.store.list_attachment_inbox_items(
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    statuses=list(ACTIVE_ATTACHMENT_STATUSES),
                    limit=bounded_limit,
                )
                if isinstance(item, dict)
            ]
        except Exception:
            logger.exception("Failed to list attachment workbench items")
            failures.append(_failure("attachments", "list_failed", "收到的材料暂时无法读取。"))

    if generated_service is not None:
        try:
            generated_files = [
                dict(item)
                for item in generated_service.store.list_generated_files(
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    statuses=list(ACTIVE_GENERATED_STATUSES),
                    limit=bounded_limit,
                )
                if isinstance(item, dict)
            ]
        except Exception:
            logger.exception("Failed to list generated workbench items")
            failures.append(_failure("generated_files", "list_failed", "生成结果暂时无法读取。"))

    available = attachment_service is not None or generated_service is not None
    status = "partial" if failures and available else "unavailable" if not available else "ready"
    return {
        "ok": available and status != "unavailable",
        "status": status,
        "attachments": attachments,
        "generated_files": generated_files,
        "failures": failures,
    }


def clear_workspace_files(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str,
    character_pack_id: str = "",
    target: str = "current",
    kind: str = "any",
    delete_storage: bool = False,
    reason: str = "",
    timestamp: int | None = None,
) -> dict[str, Any]:
    """Clear input materials and generated results through one channel facade.

    ``current``/``all`` means both shelves.  A typed handle is routed to its
    owning service; a human title may be offered to both services because both
    already implement ambiguity-aware resolution.  The facade never edits
    either store directly.
    """

    effective_ts = int(timestamp or time.time())
    normalized_target = str(target or "current").strip() or "current"
    lowered_target = normalized_target.lower()
    normalized_kind = _normalize_kind(kind)
    attachment_service = _engine_service(engine, "_get_attachment_inbox_service")
    generated_service = _engine_service(engine, "_get_generated_file_service")
    task_service = _engine_service(engine, "_get_task_workspace_service")

    clear_all = lowered_target in {"current", "all", "全部", "当前", "工作台", "*"}
    latest_attachment = lowered_target in {"latest", "最近", "最新"}
    generated_only = lowered_target.startswith(GENERATED_HANDLE_PREFIXES)
    attachment_only = latest_attachment or lowered_target.startswith(ATTACHMENT_HANDLE_PREFIXES)
    include_attachments = attachment_service is not None and not generated_only
    include_generated = generated_service is not None and not attachment_only

    attachment_result: dict[str, Any] = {
        "ok": False,
        "cleared": [],
        "purged_files": [],
        "purge_failures": [],
        "already_absent_files": [],
        "unresolved": [],
    }
    generated_result: dict[str, Any] = {
        "ok": False,
        "status": "empty",
        "managed": [],
        "failures": [],
        "unresolved": [],
        "action": "purge" if delete_storage else "archive",
    }
    failures: list[dict[str, str]] = []

    if include_attachments:
        try:
            attachment_result = attachment_service.clear_focus(
                profile_user_id=profile_user_id,
                session_id=session_id,
                target="current" if clear_all else "latest" if latest_attachment else normalized_target,
                kind=normalized_kind,
                reason=reason,
                delete_storage=bool(delete_storage),
                timestamp=effective_ts,
            )
        except Exception:
            logger.exception("Failed to clear attachment workbench items")
            failures.append(_failure("attachments", "clear_failed", "收到的材料未能完成清理。"))

    if include_generated:
        try:
            generated_targets = _generated_targets(
                generated_service,
                profile_user_id=profile_user_id,
                session_id=session_id,
                target=normalized_target,
                kind=normalized_kind,
                clear_all=clear_all,
            )
            if generated_targets:
                generated_result = generated_service.manage_generated_files(
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    action="purge" if delete_storage else "archive",
                    targets=generated_targets,
                    reason=reason,
                    timestamp=effective_ts,
                )
        except Exception:
            logger.exception("Failed to clear generated workbench items")
            generated_failure = _failure("generated_files", "clear_failed", "生成结果未能完成清理。")
            generated_result = {
                **generated_result,
                "ok": False,
                "status": "failed",
                "failures": [generated_failure],
            }
            failures.append(generated_failure)

    cleared_attachments = [
        dict(item) for item in list(attachment_result.get("cleared") or []) if isinstance(item, dict)
    ]
    managed_generated = [dict(item) for item in list(generated_result.get("managed") or []) if isinstance(item, dict)]
    generated_failures = [
        dict(item) for item in list(generated_result.get("failures") or []) if isinstance(item, dict)
    ]
    failures.extend(
        _public_failure("attachments", item)
        for item in list(attachment_result.get("purge_failures") or [])
        if isinstance(item, dict)
    )
    failures.extend(
        _public_failure("generated_files", item)
        for item in generated_failures
        if item not in failures
    )
    generated_unresolved = [
        str(item or "").strip()
        for item in list(generated_result.get("unresolved") or [])
        if str(item or "").strip()
    ]
    generated_event_unresolved = generated_unresolved if not cleared_attachments else []

    generated_timeline_event = _record_generated_cleanup_event(
        engine,
        profile_user_id=profile_user_id,
        session_id=session_id,
        character_pack_id=character_pack_id,
        action=str(generated_result.get("action") or ("purge" if delete_storage else "archive")),
        status=str(generated_result.get("status") or "empty"),
        managed=managed_generated,
        failures=generated_failures,
        unresolved=generated_event_unresolved,
        reason=reason,
        timestamp=effective_ts,
        record_empty_attempt=include_generated and not clear_all and not cleared_attachments,
    )

    artifact_ids = _artifact_ids(cleared_attachments, managed_generated)
    cleaned_tasks: list[dict[str, Any]] = []
    if task_service is not None and artifact_ids:
        try:
            cleaned_tasks = [
                dict(item)
                for item in task_service.cleanup_tasks_for_artifacts(
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    artifact_ids=artifact_ids,
                    reason=str(reason or "").strip() or "关联文件已退出当前工作台。",
                    timestamp=effective_ts,
                )
                if isinstance(item, dict)
            ]
        except Exception:
            logger.exception("Failed to clean task workspaces linked to cleared files")
            failures.append(_failure("tasks", "linked_cleanup_failed", "关联任务白板未能完成清理。"))

    changed = bool(cleared_attachments or managed_generated)
    available = attachment_service is not None or generated_service is not None
    if not available:
        status = "unavailable"
    elif failures and changed:
        status = "partial"
    elif failures:
        status = "failed"
    elif changed:
        status = "cleared"
    else:
        status = "empty"

    unresolved = _combined_unresolved(
        attachment_result=attachment_result,
        generated_result=generated_result,
        changed=changed,
    )
    return {
        "ok": status in {"cleared", "empty", "partial"},
        "complete": status in {"cleared", "empty"},
        "status": status,
        "attachments": attachment_result,
        "generated_files": generated_result,
        "cleaned_tasks": cleaned_tasks,
        "failures": failures,
        "unresolved": unresolved,
        "delete_storage": bool(delete_storage),
        "generated_timeline_event": generated_timeline_event,
    }


def _engine_service(engine: Any, method_name: str) -> Any:
    factory = getattr(engine, method_name, None)
    if not callable(factory):
        return None
    try:
        return factory()
    except Exception:
        logger.exception("Workspace service factory failed: %s", method_name)
        return None


def _record_generated_cleanup_event(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str,
    character_pack_id: str,
    action: str,
    status: str,
    managed: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    unresolved: list[str],
    reason: str,
    timestamp: int,
    record_empty_attempt: bool,
) -> dict[str, Any]:
    if not managed and not failures and not unresolved and not record_empty_attempt:
        return {
            "ok": True,
            "status": "skipped",
            "reason": "no_generated_cleanup_fact",
        }
    recorder = getattr(engine, "_record_generated_workspace_cleanup", None)
    if not callable(recorder):
        return {
            "ok": True,
            "status": "skipped",
            "reason": "recorder_unavailable",
        }
    try:
        result = recorder(
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            action=action,
            status=status,
            managed=managed,
            failures=failures,
            unresolved=unresolved,
            reason=reason,
            timestamp=timestamp,
        )
        if isinstance(result, dict):
            return dict(result)
        return {
            "ok": False,
            "status": "failed",
            "reason": "invalid_recorder_result",
        }
    except Exception as exc:
        logger.warning("Failed to record generated workbench cleanup event: %s", exc)
        return {
            "ok": False,
            "status": "failed",
            "reason": str(exc) or exc.__class__.__name__,
        }


def _generated_targets(
    service: Any,
    *,
    profile_user_id: str,
    session_id: str,
    target: str,
    kind: str,
    clear_all: bool,
) -> list[str]:
    if not clear_all:
        return [target]
    if kind == "any":
        return ["all"]
    items = service.store.list_generated_files(
        profile_user_id=profile_user_id,
        session_id=session_id,
        statuses=list(ACTIVE_GENERATED_STATUSES),
        limit=200,
    )
    targets: list[str] = []
    for item in items:
        if not isinstance(item, dict) or not _generated_kind_matches(item, kind):
            continue
        handle = str(item.get("generated_handle") or item.get("generated_id") or "").strip()
        if handle:
            targets.append(handle)
    return targets


def _generated_kind_matches(item: dict[str, Any], kind: str) -> bool:
    if kind == "any":
        return True
    extension = str(item.get("file_ext") or item.get("output_format") or "").strip().lower().lstrip(".")
    mime_type = str(item.get("mime_type") or "").strip().lower()
    if kind == "image":
        return extension in IMAGE_EXTENSIONS or mime_type.startswith("image/")
    if kind == "audio":
        return extension in AUDIO_EXTENSIONS or mime_type.startswith("audio/")
    if kind == "video":
        return extension in VIDEO_EXTENSIONS or mime_type.startswith("video/")
    if kind in {"document", "file"}:
        return not (
            extension in IMAGE_EXTENSIONS | AUDIO_EXTENSIONS | VIDEO_EXTENSIONS
            or mime_type.startswith(("image/", "audio/", "video/"))
        )
    return True


def _normalize_kind(value: Any) -> str:
    kind = str(value or "any").strip().lower()
    aliases = {
        "photo": "image",
        "picture": "image",
        "pic": "image",
        "img": "image",
        "doc": "document",
        "text": "document",
        "txt": "document",
        "pdf": "document",
        "music": "audio",
        "song": "audio",
        "voice": "audio",
    }
    normalized = aliases.get(kind, kind)
    return normalized if normalized in {"any", "image", "file", "document", "audio", "video"} else "any"


def _artifact_ids(
    attachments: list[dict[str, Any]],
    generated_files: list[dict[str, Any]],
) -> set[str]:
    values: set[str] = set()
    for item in [*attachments, *generated_files]:
        for key in ("attachment_id", "attachment_handle", "generated_id", "generated_handle"):
            value = str(item.get(key) or "").strip()
            if value:
                values.add(value)
    return values


def _combined_unresolved(
    *,
    attachment_result: dict[str, Any],
    generated_result: dict[str, Any],
    changed: bool,
) -> list[str]:
    if changed:
        return []
    unresolved: list[str] = []
    for raw in [
        *list(attachment_result.get("unresolved") or []),
        *list(generated_result.get("unresolved") or []),
    ]:
        value = str(raw or "").strip()
        if value and value not in unresolved:
            unresolved.append(value)
    return unresolved


def _failure(domain: str, code: str, reason: str) -> dict[str, str]:
    return {
        "domain": str(domain or "workspace")[:40],
        "code": str(code or "operation_failed")[:80],
        "reason": " ".join(str(reason or "工作台操作失败。").split())[:300],
    }


def _public_failure(domain: str, item: dict[str, Any]) -> dict[str, str]:
    failure = _failure(
        domain,
        str(item.get("code") or item.get("error") or "operation_failed"),
        str(item.get("reason") or "工作台文件未能完成处理。"),
    )
    target = str(
        item.get("target")
        or item.get("attachment_handle")
        or item.get("generated_handle")
        or item.get("attachment_id")
        or item.get("generated_id")
        or ""
    ).strip()
    if target:
        failure["target"] = target[:120]
    return failure
