"""Archive generated records and remove only managed files."""
from __future__ import annotations

import time
from typing import Any



def manage_generated_files(
    service: Any,
    *,
    profile_user_id: str,
    session_id: str,
    action: str,
    targets: list[str] | tuple[str, ...] | set[str] | str | None = None,
    reason: str = "",
    timestamp: int | None = None,
) -> dict[str, Any]:
    effective_ts = int(timestamp or time.time())
    normalized_action = service._normalize_generated_file_action(action)
    if not normalized_action:
        return {
            "ok": False,
            "managed": [],
            "unresolved": [],
            "action": "",
            "followup_context": (
                "你刚刚想管理生成文件，但 action 不明确。"
                "可用 action 是 archive、delete 或 purge；请自然向用户确认。"
            ),
        }

    normalized_targets = service._normalize_targets(targets)
    resolved, unresolved = service._resolve_generated_file_targets(
        profile_user_id=profile_user_id,
        session_id=session_id,
        targets=normalized_targets,
    )
    if not resolved:
        missing = f"未找到：{', '.join(unresolved[:5])}。" if unresolved else ""
        return {
            "ok": False,
            "status": "empty",
            "managed": [],
            "failures": [],
            "unresolved": unresolved,
            "action": normalized_action,
            "followup_context": (
                "你刚刚想清理生成文件，但没有找到可管理的生成物。"
                f"{missing}请自然向用户确认要处理哪一份，不要重复调用 manage_generated_file。"
            ),
        }

    managed: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for item in resolved:
        item_id = str(item.get("generated_id") or "").strip()
        if not item_id:
            continue
        deleted_file = False
        delete_error = ""
        if normalized_action in {"delete", "purge"}:
            deleted_file, delete_error = service._delete_generated_file_on_disk(item)
            if delete_error not in {"", "missing"}:
                failures.append(_management_failure(item, delete_error))
                continue

        update_kwargs: dict[str, Any] = {
            "profile_user_id": profile_user_id,
            "session_id": session_id,
            "generated_id": item_id,
            "status": "removed",
            "delivery_status": normalized_action,
            "updated_at": effective_ts,
        }
        if normalized_action == "purge":
            update_kwargs.update(
                {
                    "summary": "",
                    "content_card": {
                        "summary": "生成文件内容已清理。",
                        "purged": True,
                        "purged_at": effective_ts,
                        "reason": str(reason or "").strip()[:200],
                    },
                    "storage_relpath": "",
                    "file_size": 0,
                }
            )
        update_failure = "record_update_not_confirmed"
        try:
            updated = service.store.update_generated_file(**update_kwargs)
        except Exception as exc:
            updated = None
            update_failure = type(exc).__name__
        if not updated or updated.get("status") != "removed":
            failure = _management_failure(item, "record_update_failed", file_deleted=deleted_file)
            failure["cause"] = update_failure
            failures.append(failure)
            continue
        updated["file_deleted"] = deleted_file
        if delete_error == "missing":
            updated["file_already_absent"] = True
        elif delete_error:
            updated["delete_error"] = delete_error
        managed.append(updated)

    names = "、".join(service._generated_display_name(item) for item in managed[:3])
    if len(managed) > 3:
        names += f" 等 {len(managed)} 个"
    missing = f"未找到：{', '.join(unresolved[:5])}。" if unresolved else ""
    reason_text = f"原因：{str(reason).strip()}。" if str(reason or "").strip() else ""
    action_label = {
        "archive": "归档隐藏",
        "delete": "删除本地文件并归档",
        "purge": "彻底清理内容并归档",
    }.get(normalized_action, normalized_action)
    if managed and failures:
        status = "partial"
    elif managed:
        status = "completed"
    elif failures:
        status = "failed"
    else:
        status = "empty"
    managed_text = (
        f"你刚刚已经对 {len(managed)} 个生成文件执行了「{action_label}」"
        f"（{names}）。{missing}{reason_text}"
        "这些生成物不会继续显示在生成文件工作台里；用户原始附件不会被删除。"
        if managed
        else ""
    )
    failure_text = (
        f"另有 {len(failures)} 个生成文件未能安全完成操作；"
        "请依据返回的失败原因如实告诉用户，"
        + (
            "不要把未完成的步骤说成成功。"
            if any(item.get("file_deleted") for item in failures)
            else "不要声称已经删除。"
        )
        if failures
        else ""
    )
    return {
        "ok": bool(managed),
        "status": status,
        "managed": managed,
        "failures": failures,
        "unresolved": unresolved,
        "action": normalized_action,
        "followup_context": (
            managed_text
            + failure_text
            + (missing if not managed and missing else "")
            + "请基于这个既成事实自然回应，不要重复调用 manage_generated_file。"
        ),
    }


def normalize_generated_file_action(service: Any, value: Any) -> str:
    action = str(value or "").strip().lower()
    aliases = {
        "hide": "archive",
        "archive": "archive",
        "remove": "archive",
        "clear": "archive",
        "收起": "archive",
        "归档": "archive",
        "隐藏": "archive",
        "delete": "delete",
        "unlink": "delete",
        "删除": "delete",
        "删掉": "delete",
        "purge": "purge",
        "destroy": "purge",
        "彻底删除": "purge",
        "彻底清理": "purge",
    }
    return aliases.get(action, "")


def delete_generated_file_on_disk(service: Any, item: dict[str, Any]) -> tuple[bool, str]:
    try:
        path = service.absolute_path(item)
        if not service.is_managed_storage_path(path):
            return False, "unsafe_path"
        if not path.exists():
            return False, "missing"
        if not path.is_file():
            return False, "not_file"
        path.unlink()
        return True, ""
    except FileNotFoundError:
        return False, "missing"
    except (OSError, ValueError, RuntimeError):
        return False, "delete_failed"


def _management_failure(item: dict[str, Any], code: str, *, file_deleted: bool = False) -> dict[str, Any]:
    reason = {
        "unsafe_path": "生成文件不在 Akane 管理的目录内，系统没有删除或隐藏它。",
        "not_file": "生成文件对应的位置不是普通文件，系统没有删除或隐藏它。",
        "delete_failed": "删除生成文件时发生文件系统错误，系统保留了工作台记录以便重试。",
        "record_update_failed": (
            "本地文件已删除，但未能确认工作台记录更新；请核实记录状态后重试，不能声称已经完成归档。"
            if file_deleted else "未能确认工作台记录更新；请核实记录状态后重试，不能声称已经完成归档。"
        ),
    }.get(code, "生成文件未能安全删除，系统保留了工作台记录以便重试。")
    item_id = str(item.get("generated_id") or "").strip()
    return {
        "generated_id": item_id,
        "generated_handle": str(item.get("generated_handle") or "").strip(),
        "target": str(item.get("generated_handle") or item_id).strip(),
        "code": code,
        "file_deleted": file_deleted,
        "reason": reason,
    }
