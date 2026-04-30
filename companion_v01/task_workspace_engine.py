from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("akane.engine")


def record_tool_result_artifacts_in_task_workspace(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str,
    tool_result: Any,
    now_ts: int,
) -> tuple[list[dict[str, Any]], str]:
    tool_type = str(getattr(tool_result, "tool_type", "") or "").strip()
    if not tool_type or tool_type == "manage_task_workspace":
        return [], ""
    artifacts = extract_task_workspace_artifacts_from_tool_events(
        tool_type=tool_type,
        stream_events=list(getattr(tool_result, "stream_events", []) or []),
    )
    if not artifacts:
        return [], ""
    service = engine._get_task_workspace_service()
    if service is None:
        return [], ""
    try:
        tasks = service.list_tasks(
            profile_user_id=profile_user_id,
            session_id=session_id,
            statuses=["running", "waiting_user", "queued"],
            limit=1,
        )
        if not tasks:
            return [], ""
        task = tasks[0]
        task_id = str(task.get("task_id") or "").strip()
        if not task_id:
            return [], ""
        existing_artifacts = [dict(item) for item in list(task.get("artifacts") or []) if isinstance(item, dict)]
        merged_artifacts, added_artifacts = merge_task_workspace_artifacts(
            existing=existing_artifacts,
            additions=artifacts,
        )
        if not added_artifacts:
            return [], ""
        status_update = "running" if str(task.get("status") or "") == "queued" else None
        updated = service.update_task(
            task_id=task_id,
            status=status_update,
            artifacts=merged_artifacts,
            timestamp=now_ts,
        )
        service.append_event(
            task_id=task_id,
            event_type="tool_artifacts_recorded",
            from_actor=f"tool:{tool_type}",
            message=f"{tool_type} 产出了 {len(added_artifacts)} 个可继续使用的产物。",
            payload={"tool_type": tool_type, "artifacts": added_artifacts},
            status="handled",
            timestamp=now_ts,
        )
    except Exception:
        logger.exception("Failed to record tool artifacts in task workspace")
        return [], ""

    compact_task = compact_task_workspace_for_event(updated or task)
    labels = "、".join(str(item.get("id") or item.get("title") or "").strip() for item in added_artifacts[:6])
    followup = (
        f"系统已把这次工具产物登记到当前任务工作区 {compact_task.get('task_id') or task_id}"
        f"：{labels or '新产物'}。后续可以继续引用这些产物，不需要重复登记。"
    )
    return [
        {
            "type": "task_workspace_artifacts_recorded",
            "task": compact_task,
            "artifacts": added_artifacts,
            "tool_type": tool_type,
        }
    ], followup


def extract_task_workspace_artifacts_from_tool_events(
    *,
    tool_type: str,
    stream_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for event in stream_events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "").strip()
        if event_type == "generated_file_ready":
            artifact = task_workspace_artifact_from_generated_file(
                generated=event.get("generated_file"),
                tool_type=tool_type,
                send_to_user=bool(event.get("send_to_user")),
            )
            if artifact:
                artifacts.append(artifact)
        elif event_type == "attachment_remote_media_ready":
            artifact = task_workspace_artifact_from_attachment_item(
                item=event.get("item"),
                tool_type=tool_type,
            )
            if artifact:
                artifacts.append(artifact)
    return artifacts


def task_workspace_artifact_from_generated_file(
    *,
    generated: Any,
    tool_type: str,
    send_to_user: bool,
) -> dict[str, Any] | None:
    if not isinstance(generated, dict):
        return None
    handle = str(generated.get("generated_handle") or "").strip()
    generated_id = str(generated.get("generated_id") or "").strip()
    artifact_id = handle or generated_id
    if not artifact_id:
        return None
    title = str(generated.get("output_title") or handle or "生成文件").strip()
    output_format = str(generated.get("output_format") or generated.get("file_ext") or "file").strip().lower()
    artifact = {
        "id": artifact_id,
        "kind": output_format or "file",
        "title": title[:120],
        "status": str(generated.get("status") or "ready").strip() or "ready",
        "source": "generated_file",
        "tool": tool_type,
        "send_to_user": bool(send_to_user),
        "delivery_role": "requested_output" if send_to_user else "workspace_material",
    }
    if generated_id:
        artifact["generated_id"] = generated_id
    if handle:
        artifact["generated_handle"] = handle
    content_card = generated.get("content_card") if isinstance(generated.get("content_card"), dict) else {}
    separation = content_card.get("separation") if isinstance(content_card.get("separation"), dict) else {}
    stem_role = str(separation.get("stem_role") or "").strip()
    if stem_role:
        artifact["stem_role"] = stem_role[:40]
    for key in ("file_ext", "file_size", "created_by_tool", "version_of_generated_id", "version_no"):
        value = generated.get(key)
        if value not in (None, "", [], {}):
            artifact[key] = value
    return artifact


def task_workspace_artifact_from_attachment_item(
    *,
    item: Any,
    tool_type: str,
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    handle = str(item.get("attachment_handle") or "").strip()
    attachment_id = str(item.get("attachment_id") or "").strip()
    artifact_id = handle or attachment_id
    if not artifact_id:
        return None
    title = str(item.get("summary_title") or item.get("origin_name") or handle or "临时素材").strip()
    kind = str(item.get("kind") or item.get("file_ext") or "file").strip().lower()
    artifact = {
        "id": artifact_id,
        "kind": kind or "file",
        "title": title[:120],
        "status": str(item.get("status") or "ready").strip() or "ready",
        "source": "attachment_inbox",
        "tool": tool_type,
        "source_type": str(item.get("source") or "").strip(),
        "delivery_role": "workspace_material",
    }
    if attachment_id:
        artifact["attachment_id"] = attachment_id
    if handle:
        artifact["attachment_handle"] = handle
    for key in ("origin_name", "file_ext", "file_size", "mime_type"):
        value = item.get(key)
        if value not in (None, "", [], {}):
            artifact[key] = value
    return artifact


def merge_task_workspace_artifacts(
    *,
    existing: list[dict[str, Any]],
    additions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    merged = [dict(item) for item in existing if isinstance(item, dict)]
    seen: set[str] = set()
    for item in merged:
        identity = task_workspace_artifact_identity(item)
        if identity:
            seen.add(identity)
    added: list[dict[str, Any]] = []
    for artifact in additions:
        if not isinstance(artifact, dict):
            continue
        identity = task_workspace_artifact_identity(artifact)
        if not identity or identity in seen:
            continue
        compact = dict(artifact)
        merged.append(compact)
        added.append(compact)
        seen.add(identity)
    return merged, added


def task_workspace_artifact_identity(artifact: dict[str, Any]) -> str:
    for key in ("id", "generated_handle", "generated_id", "attachment_handle", "attachment_id"):
        value = str(artifact.get(key) or "").strip()
        if value:
            return value
    title = str(artifact.get("title") or "").strip()
    kind = str(artifact.get("kind") or "").strip()
    return f"title:{kind}:{title}" if title else ""


def compact_task_workspace_for_event(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": str(task.get("task_id") or ""),
        "status": str(task.get("status") or ""),
        "normalized_goal": str(task.get("normalized_goal") or "")[:200],
        "artifact_count": len(list(task.get("artifacts") or [])),
    }
