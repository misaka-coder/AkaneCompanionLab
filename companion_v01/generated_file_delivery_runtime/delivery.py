"""Prepare selected files and truthful delivery receipts."""
from __future__ import annotations

from typing import Any

from .resolution import format_generated_file_ambiguity



def send_file(
    service: Any,
    *,
    profile_user_id: str,
    session_id: str,
    target: str = "latest",
    targets: list[str] | tuple[str, ...] | None = None,
    timestamp: int | None = None,
) -> dict[str, Any]:
    resolved_targets = service._normalize_send_targets(target=target, targets=targets)
    files: list[dict[str, Any]] = []
    unresolved: list[str] = []
    missing_on_disk: list[str] = []
    ambiguous_targets: list[str] = []
    seen_file_ids: set[tuple[str, str]] = set()

    for item in resolved_targets:
        file_ref, error = service._resolve_sendable_file(
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=item,
            timestamp=timestamp,
        )
        if file_ref is None:
            if error == "missing_on_disk":
                missing_on_disk.append(item)
            elif error == "ambiguous_generated_file":
                ambiguous_label = format_generated_file_ambiguity(
                    service,
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    target=item,
                    statuses=["ready"],
                )
                ambiguous_targets.append(ambiguous_label or item)
            elif error == "ambiguous_attachment_file":
                ambiguity = service.attachment_service.format_attachment_ambiguity(
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    target=item,
                    kind="any",
                    statuses=["ready"],
                )
                ambiguous_targets.append(ambiguity or item)
            else:
                unresolved.append(item)
            continue
        source_type = str(file_ref.get("source_type") or "").strip().lower()
        source_id = str(
            file_ref.get("source_id")
            or file_ref.get("generated_id")
            or file_ref.get("attachment_id")
            or file_ref.get("handle")
            or file_ref.get("absolute_path")
            or ""
        ).strip()
        file_identity = (source_type, source_id)
        if source_id and file_identity in seen_file_ids:
            continue
        if source_id:
            seen_file_ids.add(file_identity)
        if source_type == "generated":
            updated = service.store.update_generated_file(
                profile_user_id=profile_user_id,
                session_id=session_id,
                generated_id=source_id,
                delivery_status="pending",
                updated_at=timestamp,
            )
            if updated:
                file_ref["generated_file"] = {**updated, "absolute_path": file_ref["absolute_path"]}
        files.append(file_ref)

    if not files:
        return {
            "ok": False,
            "files": [],
            "unresolved": unresolved,
            "missing_on_disk": missing_on_disk,
            "ambiguous_targets": ambiguous_targets,
            "send_to_user": False,
            "error": (
                "file_target_ambiguous"
                if ambiguous_targets
                else "file_not_found"
                if unresolved
                else "file_missing_on_disk"
            ),
            "followup_context": service._build_send_file_followup_missing(
                requested_targets=resolved_targets,
                unresolved=unresolved,
                missing_on_disk=missing_on_disk,
                ambiguous_targets=ambiguous_targets,
            ),
        }

    return {
        "ok": True,
        "files": files,
        "unresolved": unresolved,
        "missing_on_disk": missing_on_disk,
        "ambiguous_targets": ambiguous_targets,
        "send_to_user": True,
        "followup_context": service._build_send_file_followup_batch(
            files=files,
            unresolved=unresolved,
            missing_on_disk=missing_on_disk,
            ambiguous_targets=ambiguous_targets,
        ),
    }


def sendable_file_label(service: Any, file_ref: dict[str, Any]) -> str:
    source_type = str(file_ref.get("source_type") or "").strip()
    handle = str(file_ref.get("handle") or "").strip()
    title = str(file_ref.get("title") or file_ref.get("name") or "").strip()
    ext = str(file_ref.get("file_ext") or "").strip()
    type_label = "生成文件" if source_type == "generated" else "工作台材料" if source_type == "attachment" else "文件"
    label = f"{type_label} {handle}" if handle else type_label
    if title:
        label += f"《{title}》"
    if ext:
        label += f"（{ext}）"
    return label


def normalize_send_targets(
    service: Any,
    *,
    target: str = "latest",
    targets: list[str] | tuple[str, ...] | None = None,
) -> list[str]:
    raw_items: list[Any] = []
    if targets:
        raw_items.extend(list(targets))
    elif target:
        raw_items.append(target)
    else:
        raw_items.append("latest")

    normalized: list[str] = []
    for item in raw_items:
        if isinstance(item, str):
            chunks = item.replace("，", ",").replace("、", ",").split(",")
        else:
            chunks = [str(item or "")]
        for chunk in chunks:
            text = str(chunk or "").strip()
            if not text:
                continue
            if text not in normalized:
                normalized.append(text[:120])
    return normalized or ["latest"]
