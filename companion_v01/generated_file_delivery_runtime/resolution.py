"""Resolve scoped generated files, attachments and media sources."""
from __future__ import annotations

from pathlib import Path
from typing import Any



def resolve_generated_file(
    service: Any,
    *,
    profile_user_id: str,
    session_id: str,
    target: str,
) -> dict[str, Any] | None:
    normalized = str(target or "").strip()
    if not normalized or normalized.lower() in {"latest", "current", "最近", "当前"}:
        items = service.store.list_generated_files(
            profile_user_id=profile_user_id,
            session_id=session_id,
            statuses=["ready"],
            limit=1,
        )
        return items[0] if items else None
    return service.store.find_generated_file(
        profile_user_id=profile_user_id,
        session_id=session_id,
        query=normalized,
        statuses=["ready"],
    )


def resolve_generated_file_targets(
    service: Any,
    *,
    profile_user_id: str,
    session_id: str,
    targets: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    if not targets:
        targets = ["latest"]
    lowered = {target.lower() for target in targets}
    if lowered & {"all", "全部", "*"}:
        return (
            service.store.list_generated_files(
                profile_user_id=profile_user_id,
                session_id=session_id,
                statuses=["ready", "failed"],
                limit=200,
            ),
            [],
        )

    resolved: list[dict[str, Any]] = []
    unresolved: list[str] = []
    seen: set[str] = set()
    for target in targets:
        item = service._resolve_generated_file_any_status(
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
        )
        if item is None:
            unresolved.append(target)
            continue
        item_id = str(item.get("generated_id") or "").strip()
        if item_id and item_id not in seen:
            seen.add(item_id)
            resolved.append(item)
    return resolved, unresolved


def resolve_generated_file_any_status(
    service: Any,
    *,
    profile_user_id: str,
    session_id: str,
    target: str,
) -> dict[str, Any] | None:
    normalized = str(target or "").strip()
    if not normalized or normalized.lower() in {"latest", "current", "最近", "当前"}:
        items = service.store.list_generated_files(
            profile_user_id=profile_user_id,
            session_id=session_id,
            statuses=["ready", "failed"],
            limit=1,
        )
        return items[0] if items else None
    return service.store.find_generated_file(
        profile_user_id=profile_user_id,
        session_id=session_id,
        query=normalized,
        statuses=["ready", "failed"],
    )


def find_generated_file_ambiguity_candidates(
    service: Any,
    *,
    profile_user_id: str,
    session_id: str,
    target: str,
    statuses: list[str] | tuple[str, ...] | set[str] | None,
) -> list[dict[str, Any]]:
    normalized = str(target or "").strip()
    if not normalized:
        return []
    matches = service.store.find_generated_file_matches(
        profile_user_id=profile_user_id,
        session_id=session_id,
        query=normalized,
        statuses=statuses,
        limit=8,
    )
    if not matches:
        return []
    best_rank = int(matches[0].get("_match_rank") or 99)
    if best_rank <= 2:
        return []
    candidates = [
        {key: value for key, value in item.items() if key != "_match_rank"}
        for item in matches
        if int(item.get("_match_rank") or 99) == best_rank
    ]
    return candidates if len(candidates) > 1 else []


def format_generated_file_ambiguity(
    service: Any,
    *,
    profile_user_id: str,
    session_id: str,
    target: str,
    statuses: list[str] | tuple[str, ...] | set[str] | None,
) -> str:
    candidates = find_generated_file_ambiguity_candidates(
        service,
        profile_user_id=profile_user_id,
        session_id=session_id,
        target=target,
        statuses=statuses,
    )
    if not candidates:
        return ""
    labels = [
        service._generated_display_name(item)
        for item in candidates[:4]
    ]
    return f"{target} -> {'、'.join(label for label in labels if label)}"


def normalize_targets(service: Any, value: list[str] | tuple[str, ...] | set[str] | str | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = [item.strip() for item in value.replace("，", ",").replace("、", ",").split(",")]
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = [value]
    targets: list[str] = []
    for item in raw_items:
        text = str(item or "").strip()
        if text and text not in targets:
            targets.append(text[:120])
    return targets[:50]


def resolve_sendable_file(
    service: Any,
    *,
    profile_user_id: str,
    session_id: str,
    target: str,
    timestamp: int | None,
) -> tuple[dict[str, Any] | None, str]:
    normalized = str(target or "").strip() or "latest"
    lowered = normalized.lower()
    if lowered in {
        "latest_generated",
        "latest-generated",
        "generated_latest",
        "最近生成物",
        "最新生成物",
    }:
        return service._resolve_generated_sendable_file(
            profile_user_id=profile_user_id,
            session_id=session_id,
            target="latest",
            timestamp=timestamp,
        )
    if lowered in {
        "latest_attachment",
        "latest-attachment",
        "attachment_latest",
        "最近附件",
        "最新附件",
    }:
        return service._resolve_attachment_sendable_file(
            profile_user_id=profile_user_id,
            session_id=session_id,
            target="latest",
        )
    if lowered in {"latest", "current", "最近", "当前"}:
        return service._resolve_latest_sendable_file(
            profile_user_id=profile_user_id,
            session_id=session_id,
            timestamp=timestamp,
        )

    prefer_generated = lowered.startswith(("gen_", "generated::"))
    prefer_attachment = lowered.startswith(("file_", "img_", "image_", "audio_", "video_", "attachment::"))
    if prefer_generated:
        return service._resolve_generated_sendable_file(
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=normalized,
            timestamp=timestamp,
        )
    if prefer_attachment:
        return service._resolve_attachment_sendable_file(
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=normalized,
        )

    generated, generated_error = service._resolve_generated_sendable_file(
        profile_user_id=profile_user_id,
        session_id=session_id,
        target=normalized,
        timestamp=timestamp,
    )
    if generated is not None or generated_error in {"missing_on_disk", "ambiguous_generated_file"}:
        return generated, generated_error
    return service._resolve_attachment_sendable_file(
        profile_user_id=profile_user_id,
        session_id=session_id,
        target=normalized,
    )


def resolve_latest_sendable_file(
    service: Any,
    *,
    profile_user_id: str,
    session_id: str,
    timestamp: int | None,
) -> tuple[dict[str, Any] | None, str]:
    generated_items = service.store.list_generated_files(
        profile_user_id=profile_user_id,
        session_id=session_id,
        statuses=["ready"],
        limit=1,
    )
    attachment_items = service.store.list_attachment_inbox_items(
        profile_user_id=profile_user_id,
        session_id=session_id,
        statuses=["ready"],
        limit=1,
    )
    generated = generated_items[0] if generated_items else None
    attachment = attachment_items[0] if attachment_items else None
    if generated is None and attachment is None:
        return None, "not_found"
    if generated is not None and attachment is not None:
        generated_ts = int(generated.get("updated_at") or generated.get("created_at") or 0)
        attachment_ts = int(attachment.get("updated_at") or attachment.get("created_at") or 0)
        if generated_ts >= attachment_ts:
            return service._generated_item_to_sendable_file(generated, timestamp=timestamp)
        return service._attachment_item_to_sendable_file(attachment)
    if generated is not None:
        return service._generated_item_to_sendable_file(generated, timestamp=timestamp)
    return service._attachment_item_to_sendable_file(attachment or {})


def resolve_generated_sendable_file(
    service: Any,
    *,
    profile_user_id: str,
    session_id: str,
    target: str,
    timestamp: int | None,
) -> tuple[dict[str, Any] | None, str]:
    generated = service._resolve_generated_file(
        profile_user_id=profile_user_id,
        session_id=session_id,
        target=target,
    )
    if generated is None:
        if find_generated_file_ambiguity_candidates(
            service,
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
            statuses=["ready"],
        ):
            return None, "ambiguous_generated_file"
        return None, "not_found"
    return service._generated_item_to_sendable_file(generated, timestamp=timestamp)


def resolve_attachment_sendable_file(
    service: Any,
    *,
    profile_user_id: str,
    session_id: str,
    target: str,
) -> tuple[dict[str, Any] | None, str]:
    attachment = service.attachment_service.resolve_attachment(
        profile_user_id=profile_user_id,
        session_id=session_id,
        target=target,
        kind="any",
    )
    if attachment is None:
        ambiguity = service.attachment_service.format_attachment_ambiguity(
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
            kind="any",
            statuses=["ready"],
        )
        if ambiguity:
            return None, "ambiguous_attachment_file"
        return None, "not_found"
    return service._attachment_item_to_sendable_file(attachment)


def generated_item_to_sendable_file(
    service: Any,
    generated: dict[str, Any],
    *,
    timestamp: int | None,
) -> tuple[dict[str, Any] | None, str]:
    try:
        absolute_path = service.absolute_path(generated)
        if not absolute_path.exists() or not absolute_path.is_file():
            return None, "missing_on_disk"
        file_size = int(generated.get("file_size") or absolute_path.stat().st_size)
    except (OSError, ValueError, RuntimeError):
        return None, "missing_on_disk"
    # This resolver also serves read-only resource consumers. Delivery state
    # changes belong to send_file, never lookup, parsing or lineage validation.
    updated = dict(generated)
    updated["absolute_path"] = str(absolute_path)
    output_format = str(updated.get("output_format") or absolute_path.suffix.lstrip(".")).strip().lower()
    title = str(updated.get("output_title") or updated.get("generated_handle") or absolute_path.stem).strip()
    name = f"{title}.{output_format}" if output_format and not title.lower().endswith(f".{output_format}") else title
    return (
        {
            "source_type": "generated",
            "source_id": str(updated.get("generated_id") or "").strip(),
            "generated_id": str(updated.get("generated_id") or "").strip(),
            "handle": str(updated.get("generated_handle") or "").strip(),
            "title": title,
            "name": name or absolute_path.name,
            "absolute_path": str(absolute_path),
            "file_ext": output_format,
            "mime_type": str(updated.get("mime_type") or service._mime_type_for_format(output_format)),
            "file_size": file_size,
            "generated_file": updated,
        },
        "",
    )


def attachment_item_to_sendable_file(service: Any, attachment: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    if not attachment:
        return None, "not_found"
    try:
        absolute_path = service.attachment_service.resolve_storage_path(attachment)
        if absolute_path is None or not absolute_path.exists() or not absolute_path.is_file():
            return None, "missing_on_disk"
        file_size = int(attachment.get("file_size") or absolute_path.stat().st_size)
    except (OSError, ValueError, RuntimeError):
        return None, "missing_on_disk"
    handle = str(attachment.get("attachment_handle") or attachment.get("attachment_id") or "").strip()
    origin_name = str(attachment.get("origin_name") or "").strip()
    ext = str(attachment.get("file_ext") or absolute_path.suffix.lstrip(".")).strip().lstrip(".")
    title = origin_name or handle or absolute_path.stem
    if ext and title and not title.lower().endswith(f".{ext.lower()}"):
        name = f"{title}.{ext}"
    else:
        name = title or absolute_path.name
    return (
        {
            "source_type": "attachment",
            "source_id": str(attachment.get("attachment_id") or "").strip(),
            "attachment_id": str(attachment.get("attachment_id") or "").strip(),
            "handle": handle,
            "title": title,
            "name": name or absolute_path.name,
            "absolute_path": str(absolute_path),
            "file_ext": ext,
            "mime_type": str(attachment.get("mime_type") or ""),
            "file_size": file_size,
            "attachment": attachment,
        },
        "",
    )


def resolve_generated_media_source(
    service: Any,
    *,
    profile_user_id: str,
    session_id: str,
    target: str,
) -> dict[str, Any] | None:
    generated = service._resolve_generated_file(
        profile_user_id=profile_user_id,
        session_id=session_id,
        target=target,
    )
    if generated is None:
        return None
    content_card = generated.get("content_card") if isinstance(generated.get("content_card"), dict) else {}
    media_info = content_card.get("media_info") if isinstance(content_card.get("media_info"), dict) else {}
    absolute_path = service.absolute_path(generated)
    return {
        "source_type": "generated",
        "source_id": str(generated.get("generated_id") or "").strip(),
        "handle": str(generated.get("generated_handle") or "").strip(),
        "title": str(generated.get("output_title") or "").strip() or "生成文件",
        "summary": str(generated.get("summary") or content_card.get("summary") or "").strip(),
        "preview": str(content_card.get("content_preview") or "").strip(),
        "output_format": str(generated.get("output_format") or absolute_path.suffix.lstrip(".")).strip().lower(),
        "absolute_path": absolute_path,
        "extra_source_ids": list(generated.get("source_ids") or []),
        "version_no": int(generated.get("version_no") or 1),
        "media_info": media_info,
    }


def resolve_attachment_media_source(
    service: Any,
    *,
    profile_user_id: str,
    session_id: str,
    target: str,
) -> dict[str, Any] | None:
    attachment = service.attachment_service.resolve_attachment(
        profile_user_id=profile_user_id,
        session_id=session_id,
        target=target,
        kind="any",
    )
    if attachment is None:
        return None
    source_path = None
    if hasattr(service.attachment_service, "resolve_storage_path"):
        try:
            source_path = service.attachment_service.resolve_storage_path(attachment)  # type: ignore[attr-defined]
        except Exception:
            source_path = None
    detail = attachment.get("detail") if isinstance(attachment.get("detail"), dict) else {}
    attachment_status = str(attachment.get("status") or "").strip()
    raw_failure = detail.get("failure") if isinstance(detail.get("failure"), dict) else {}
    failure_code = str(raw_failure.get("code") or attachment.get("error_message") or "").strip()
    failure_reason = " ".join(str(raw_failure.get("reason") or attachment.get("short_hint") or "").split()).strip()
    failure = (
        {
            "code": failure_code[:120],
            "reason": failure_reason[:500],
        }
        if attachment_status.lower() == "failed" or failure_code or raw_failure
        else {}
    )
    for field in ("observed_bytes", "limit_bytes"):
        try:
            value = max(0, int(raw_failure.get(field) or 0))
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            failure[field] = value
    media_info = detail.get("media_info") if isinstance(detail.get("media_info"), dict) else {}
    title = (
        str(attachment.get("summary_title") or "").strip()
        or str(attachment.get("origin_name") or "").strip()
        or str(attachment.get("attachment_handle") or "").strip()
        or "工作台材料"
    )
    output_format = ""
    if isinstance(source_path, Path):
        output_format = source_path.suffix.lower().lstrip(".")
    if not output_format:
        output_format = str(attachment.get("file_ext") or detail.get("file_kind") or "").strip().lower().lstrip(".")
    return {
        "source_type": "attachment",
        "source_id": str(attachment.get("attachment_id") or "").strip(),
        "handle": str(attachment.get("attachment_handle") or "").strip(),
        "title": title,
        "summary": str(attachment.get("short_hint") or detail.get("summary") or "").strip(),
        "preview": str(detail.get("text_preview") or detail.get("content_preview") or "").strip(),
        "output_format": output_format,
        "absolute_path": source_path,
        "extra_source_ids": [],
        "version_no": 0,
        "media_info": media_info,
        "status": attachment_status,
        "failure": failure,
    }
