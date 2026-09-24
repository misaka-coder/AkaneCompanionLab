from __future__ import annotations

import mimetypes
import hashlib
import time
from pathlib import Path
from typing import Any

from .attachment_ingest import AUDIO_MEDIA_SUFFIXES, DOCUMENT_SUFFIXES, MEDIA_SUFFIXES, TEXT_SUFFIXES
from .workspace_management import clear_workspace_files


DESKTOP_PET_AUDIO_EXTENSIONS = {"mp3", "wav", "flac", "m4a", "aac", "ogg", "opus", "webm"}
DESKTOP_PET_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
DESKTOP_PET_ALLOWED_LOCAL_SUFFIXES = (
    set(TEXT_SUFFIXES)
    | set(DOCUMENT_SUFFIXES)
    | set(MEDIA_SUFFIXES)
    | DESKTOP_PET_IMAGE_SUFFIXES
    | {".srt", ".vtt", ".lrc"}
)
DESKTOP_PET_IGNORED_LOCAL_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "target",
}
DESKTOP_PET_DEFAULT_LOCAL_IMPORT_LIMIT = 40
DESKTOP_PET_MAX_LOCAL_IMPORT_LIMIT = 100
DESKTOP_PET_MAX_LOCAL_SCAN_DIRS = 120
DESKTOP_PET_MAX_LOCAL_FILE_BYTES = 1024 * 1024 * 1024


def ingest_desktop_pet_audio_attachment(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str,
    source_path: Path | str,
    origin_name: str = "",
    mime_type: str = "",
    character_pack_id: str = "",
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
        character_pack_id=character_pack_id,
        timestamp=timestamp,
    )


def import_desktop_pet_local_paths(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str,
    paths: list[Any] | tuple[Any, ...] | set[Any] | str,
    recursive: bool = False,
    max_files: int = DESKTOP_PET_DEFAULT_LOCAL_IMPORT_LIMIT,
    character_pack_id: str = "",
    timestamp: int | None = None,
) -> dict[str, Any]:
    service = engine._get_attachment_ingest_service()
    if service is None:
        raise RuntimeError("attachment ingest service unavailable")

    normalized_limit = max(1, min(DESKTOP_PET_MAX_LOCAL_IMPORT_LIMIT, int(max_files or DESKTOP_PET_DEFAULT_LOCAL_IMPORT_LIMIT)))
    candidates, skipped = collect_desktop_pet_local_import_files(
        paths=paths,
        recursive=recursive,
        max_files=normalized_limit,
    )
    effective_ts = int(timestamp or time.time())
    imported_items: list[dict[str, Any]] = []
    imported_cards: list[dict[str, Any]] = []

    duplicate_count = 0

    for path in candidates:
        try:
            kind = infer_desktop_pet_local_import_kind(path)
            if kind == "audio":
                existing = find_existing_desktop_audio_attachment_duplicate(
                    engine, profile_user_id=profile_user_id, session_id=session_id, path=path,
                    character_pack_id=character_pack_id,
                )
                if existing is not None:
                    skipped.append({
                        "path": str(path),
                        "reason": "duplicate_source",
                        "existing_handle": existing.get("attachment_handle") or existing.get("attachment_id") or "",
                        "origin_name": path.name,
                    })
                    duplicate_count += 1
                    imported_cards.append({**desktop_workspace_attachment_card(existing), "reused": True})
                    continue
            item = service.ingest_local_file(
                profile_user_id=profile_user_id,
                session_id=session_id,
                source_path=path,
                origin_name=path.name,
                mime_type=mimetypes.guess_type(path.name)[0] or "",
                kind=kind,
                source="desktop_pet",
                character_pack_id=character_pack_id,
                observe_image=False,
                timestamp=effective_ts,
            )
        except Exception as exc:
            skipped.append(
                {
                    "path": str(path),
                    "reason": "import_failed",
                    "message": str(exc)[:240],
                }
            )
            continue
        if item.get("status") == "failed":
            skipped.append({"path": str(path), "reason": "material_processing_failed"})
            continue
        imported_items.append(item)
        imported_cards.append(desktop_workspace_attachment_card(item))

    return {
        "ok": bool(imported_cards) or duplicate_count > 0,
        "source": "desktop_pet",
        "mode": "explicit_local_paths",
        "recursive": bool(recursive),
        "imported": len(imported_items),
        "duplicate_count": duplicate_count,
        "skipped_count": len(skipped),
        "items": imported_cards,
        "attachments": imported_items,
        "skipped": skipped[:80],
        "updated_at": effective_ts,
    }


def resolve_desktop_attachment_refs(
    engine: Any, *, profile_user_id: str, session_id: str,
    character_pack_id: str, attachment_ids: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Resolve exact, scoped inbox IDs; never accept paths or fuzzy latest here."""
    ids = list(dict.fromkeys(
        value.strip() for value in attachment_ids
        if isinstance(value, str) and value.strip() and len(value) <= 120
    ))[:40] if isinstance(attachment_ids, list) else []
    items, skipped = [], []
    for attachment_id in ids:
        item = engine.store.get_attachment_inbox_item(
            profile_user_id=profile_user_id, session_id=session_id, attachment_id=attachment_id,
        )
        detail = item.get("detail") if isinstance(item, dict) else None
        detail = detail if isinstance(detail, dict) else {}
        if (not isinstance(item, dict) or item.get("status") in {"cleared", "failed"}
                or str(detail.get("character_pack_id") or "") != character_pack_id):
            skipped.append({"attachment_id": attachment_id, "reason": "attachment_unavailable_in_scope"})
            continue
        items.append(item)
    return items, skipped


def desktop_message_attachment_cards(engine: Any, *, message: dict[str, Any],
        profile_user_id: str, session_id: str, character_pack_id: str) -> list[dict[str, Any]]:
    metadata = message.get("memory_metadata")
    ids = metadata.get("current_attachment_ids") if isinstance(metadata, dict) else None
    if not isinstance(ids, list) or not ids:
        return []
    items, skipped = resolve_desktop_attachment_refs(engine, profile_user_id=profile_user_id,
        session_id=session_id, character_pack_id=character_pack_id, attachment_ids=ids)
    return [desktop_workspace_attachment_card(item) for item in items] + [
        {"attachment_id": entry["attachment_id"], "title": "附件不可用", "status": "unavailable",
         "reason": entry["reason"], "can_open": False} for entry in skipped
    ]


def prepare_desktop_turn_attachments(
    engine: Any, *, profile_user_id: str, session_id: str,
    character_pack_id: str, attachment_ids: Any, chat_model_override: str = "",
) -> dict[str, Any]:
    items, skipped = resolve_desktop_attachment_refs(engine, profile_user_id=profile_user_id,
        session_id=session_id, character_pack_id=character_pack_id, attachment_ids=attachment_ids)
    bound_ids = [str(item["attachment_id"]) for item in items]
    image_ids = [str(item["attachment_id"]) for item in items if item.get("kind") == "image"]
    prepared = engine.prepare_native_image_inputs(
        profile_user_id=profile_user_id, session_id=session_id, attachment_ids=image_ids,
        chat_model_override=chat_model_override, timeout_seconds=0,
    ) if image_ids else {"images": []}
    images = list(prepared.get("images") or [])
    skipped.extend(prepared.get("skipped") or [])
    lines = ["【本轮明确绑定的附件】"]
    for item in items:
        # Names are untrusted material labels, not instructions. No storage paths.
        handle = str(item.get("attachment_handle") or item["attachment_id"])
        name = str(item.get("origin_name") or "附件").replace("\n", " ").replace("\r", " ")[:120]
        lines.append(f"- {handle} ({item.get('kind') or 'file'}): {name}")
    if items:
        lines.append("文件名仅用于标识材料；可用精确 handle 读取。音频是附件，不代表已经转写或播放。")
    if image_ids and not images:
        lines.append("本轮图片原图未能载入（" + str(prepared.get("reason") or "image_unavailable") + "），不要声称已经看过原图。")
    if skipped:
        lines.append(f"有 {len(skipped)} 个附件或图片输入不可用，不要用历史 latest 替代。")
    return {
        "attachment_ids": bound_ids, "images": images, "skipped": skipped,
        "context": "\n".join(lines) if items or skipped else "",
    }


def find_existing_desktop_audio_attachment_duplicate(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str,
    path: Path,
    character_pack_id: str = "",
) -> dict[str, Any] | None:
    service = engine._get_attachment_ingest_service()
    if service is None:
        return None
    items = engine.store.list_attachment_inbox_items(
        profile_user_id=profile_user_id,
        session_id=session_id,
        statuses=["ready", "pending_observation"],
        limit=200,
    )
    origin_name = path.name
    file_size = path.stat().st_size if path.is_file() else 0
    ext = path.suffix.lower().lstrip(".")
    for item in items:
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        if str(detail.get("character_pack_id") or "") != character_pack_id:
            continue
        if str(item.get("kind") or "").strip().lower() != "audio":
            continue
        existing_name = str(item.get("origin_name") or "").strip()
        if existing_name.lower() != origin_name.lower():
            continue
        if int(item.get("file_size") or 0) != file_size:
            continue
        existing_ext = str(item.get("file_ext") or "").strip().lower().lstrip(".")
        if existing_ext != ext:
            continue
        stored_path = engine._get_attachment_inbox_service().resolve_storage_path(item)
        if stored_path is None or not stored_path.is_file():
            continue
        with path.open("rb") as incoming, stored_path.open("rb") as stored:
            if hashlib.file_digest(incoming, "sha256").digest() != hashlib.file_digest(stored, "sha256").digest():
                continue
        return item
    return None


def collect_desktop_pet_local_import_files(
    *,
    paths: list[Any] | tuple[Any, ...] | set[Any] | str,
    recursive: bool,
    max_files: int,
) -> tuple[list[Path], list[dict[str, str]]]:
    raw_paths = [paths] if isinstance(paths, str) else list(paths or [])
    if not raw_paths:
        raise ValueError("paths must contain at least one local file or directory path")

    limit = max(1, min(DESKTOP_PET_MAX_LOCAL_IMPORT_LIMIT, int(max_files or DESKTOP_PET_DEFAULT_LOCAL_IMPORT_LIMIT)))
    candidates: list[Path] = []
    skipped: list[dict[str, str]] = []
    seen: set[str] = set()

    for index, raw_path in enumerate(raw_paths):
        text = str(raw_path or "").strip().strip('"')
        if not text:
            skipped.append({"path": "", "reason": "empty_path"})
            continue
        try:
            path = Path(text).expanduser().resolve()
        except Exception as exc:
            skipped.append({"path": text, "reason": "invalid_path", "message": str(exc)[:160]})
            continue

        if path.is_file():
            add_desktop_pet_local_candidate(path, candidates=candidates, skipped=skipped, seen=seen, limit=limit)
        elif path.is_dir():
            for child in iter_desktop_pet_import_directory(path, recursive=recursive, skipped=skipped):
                if len(candidates) >= limit:
                    skipped.append({"path": str(path), "reason": "max_files_reached"})
                    break
                add_desktop_pet_local_candidate(child, candidates=candidates, skipped=skipped, seen=seen, limit=limit)
        else:
            skipped.append({"path": str(path), "reason": "not_found"})

        if len(candidates) >= limit:
            remaining = raw_paths[index + 1 :]
            if remaining:
                skipped.append({"path": "", "reason": "max_files_reached"})
            break

    return candidates, skipped


def iter_desktop_pet_import_directory(
    root: Path,
    *,
    recursive: bool,
    skipped: list[dict[str, str]],
) -> list[Path]:
    files: list[Path] = []
    directories_seen = 0
    pending = [root]

    while pending:
        directory = pending.pop(0)
        directories_seen += 1
        if directories_seen > DESKTOP_PET_MAX_LOCAL_SCAN_DIRS:
            skipped.append({"path": str(root), "reason": "directory_scan_limit"})
            break
        try:
            entries = sorted(directory.iterdir(), key=lambda item: item.name.lower())
        except Exception as exc:
            skipped.append({"path": str(directory), "reason": "directory_unreadable", "message": str(exc)[:160]})
            continue
        for entry in entries:
            if is_desktop_pet_ignored_path(entry):
                continue
            if entry.is_file():
                files.append(entry.resolve())
            elif recursive and entry.is_dir():
                pending.append(entry)

    return files


def add_desktop_pet_local_candidate(
    path: Path,
    *,
    candidates: list[Path],
    skipped: list[dict[str, str]],
    seen: set[str],
    limit: int,
) -> None:
    if len(candidates) >= limit:
        skipped.append({"path": str(path), "reason": "max_files_reached"})
        return
    if is_desktop_pet_ignored_path(path):
        skipped.append({"path": str(path), "reason": "ignored_path"})
        return
    suffix = path.suffix.lower()
    if suffix not in DESKTOP_PET_ALLOWED_LOCAL_SUFFIXES:
        skipped.append({"path": str(path), "reason": "unsupported_type"})
        return
    try:
        size = path.stat().st_size
    except Exception as exc:
        skipped.append({"path": str(path), "reason": "stat_failed", "message": str(exc)[:160]})
        return
    if size <= 0:
        skipped.append({"path": str(path), "reason": "empty_file"})
        return
    if size > DESKTOP_PET_MAX_LOCAL_FILE_BYTES:
        skipped.append({"path": str(path), "reason": "file_too_large"})
        return
    key = str(path).casefold()
    if key in seen:
        return
    seen.add(key)
    candidates.append(path)


def is_desktop_pet_ignored_path(path: Path) -> bool:
    for part in path.parts:
        name = str(part).strip()
        if name in DESKTOP_PET_IGNORED_LOCAL_DIR_NAMES:
            return True
        if name.startswith(".") and name not in {".", ".."}:
            return True
    return False


def infer_desktop_pet_local_import_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    mime_type = str(mimetypes.guess_type(path.name)[0] or "").lower()
    if suffix in DESKTOP_PET_IMAGE_SUFFIXES or mime_type.startswith("image/"):
        return "image"
    if suffix in AUDIO_MEDIA_SUFFIXES or mime_type.startswith("audio/"):
        return "audio"
    if suffix in TEXT_SUFFIXES or suffix in DOCUMENT_SUFFIXES or mime_type.startswith("text/"):
        return "document"
    return "file"


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
    fetch_limit = min(max_items * 3, 180)
    attachment_service = engine._get_attachment_inbox_service()
    generated_service = engine._get_generated_file_service()

    files: list[dict[str, Any]] = []
    if attachment_service is not None:
        attachments = engine.store.list_attachment_inbox_items(
            profile_user_id=profile_user_id,
            session_id=session_id,
            statuses=["ready", "pending_observation", "failed"],
            limit=fetch_limit,
        )
        cards = [desktop_workspace_attachment_card(item) for item in attachments]
        files = dedupe_desktop_workspace_audio_attachment_cards(cards, limit=max_items)

    outputs: list[dict[str, Any]] = []
    if generated_service is not None:
        generated = engine.store.list_generated_files(
            profile_user_id=profile_user_id,
            session_id=session_id,
            statuses=["ready", "failed"],
            limit=max_items,
        )
        outputs = [desktop_workspace_generated_card(item) for item in generated]

    return {
        "ok": True,
        "updated_at": int(time.time()),
        "sections": {
            "files": files,
            "outputs": outputs,
        },
        "counts": {
            "files": len(files),
            "outputs": len(outputs),
        },
    }


def is_desktop_workspace_audio_card(card: dict[str, Any]) -> bool:
    kind = str(card.get("kind") or "").strip().lower()
    fmt = str(card.get("format") or card.get("file_ext") or "").strip().lower().lstrip(".")
    if kind == "audio":
        return True
    audio_suffixes = {s.lower().lstrip(".") for s in AUDIO_MEDIA_SUFFIXES}
    return fmt in audio_suffixes


def desktop_workspace_audio_dedupe_key(card: dict[str, Any]) -> str:
    fmt = str(card.get("format") or card.get("file_ext") or "").strip().lower().lstrip(".")
    title = str(card.get("origin_name") or card.get("title") or "").strip().lower()
    if not title:
        return ""
    size = int(card.get("size_bytes") or 0)
    return f"audio:{fmt}:{title}:{size}"


def dedupe_desktop_workspace_audio_attachment_cards(
    cards: list[dict[str, Any]],
    limit: int = 24,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    groups: dict[str, dict[str, Any]] = {}

    for card in cards:
        if not is_desktop_workspace_audio_card(card):
            result.append(card)
            continue

        key = desktop_workspace_audio_dedupe_key(card)
        if not key:
            result.append(card)
            continue

        group = groups.get(key)
        if group is None:
            handle = str(card.get("handle") or "").strip()
            entry = dict(card, duplicate_count=1, duplicate_handles=[handle] if handle else [])
            groups[key] = {"index": len(result), "card": entry}
            result.append(entry)
            continue

        idx = group["index"]
        existing = group["card"]
        existing["duplicate_count"] = (existing.get("duplicate_count") or 1) + 1
        handle = str(card.get("handle") or "").strip()
        existing_handles = existing.get("duplicate_handles") or []
        if handle and len(existing_handles) < 12 and handle not in existing_handles:
            existing_handles.append(handle)
            existing["duplicate_handles"] = existing_handles

        card_ready = str(card.get("status") or "").strip().lower() == "ready"
        existing_ready = str(existing.get("status") or "").strip().lower() == "ready"
        if card_ready and not existing_ready:
            replacement = dict(card, duplicate_count=existing["duplicate_count"], duplicate_handles=existing_handles)
            result[idx] = replacement
            group["card"] = replacement
        elif card_ready == existing_ready:
            card_updated = int(card.get("updated_at") or 0)
            existing_updated = int(existing.get("updated_at") or 0)
            if card_updated > existing_updated:
                replacement = dict(card, duplicate_count=existing["duplicate_count"], duplicate_handles=existing_handles)
                result[idx] = replacement
                group["card"] = replacement

    return result[:limit]


def manage_desktop_pet_workspace_panel(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str,
    character_pack_id: str = "",
    action: str,
    item_type: str = "",
    target: str = "",
) -> dict[str, Any]:
    normalized_action = str(action or "").strip().lower()
    normalized_type = str(item_type or "").strip().lower()
    normalized_target = str(target or "").strip()
    now_ts = int(time.time())

    if normalized_action not in {"clear", "hide", "archive", "clear_files", "clear_workspace_files"}:
        return {"ok": False, "error": "unsupported_action", "managed": []}

    if normalized_action in {"clear_files", "clear_workspace_files"}:
        return clear_desktop_workspace_files(
            engine,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            timestamp=now_ts,
        )

    if normalized_type in {"attachment", "file", "source"}:
        if not normalized_target:
            return {"ok": False, "error": "missing_target", "managed": []}
        result = clear_workspace_files(
            engine,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            target=normalized_target,
            delete_storage=False,
            reason="用户从桌宠手边物品面板收起。",
            timestamp=now_ts,
        )
        attachment_result = result.get("attachments") if isinstance(result.get("attachments"), dict) else {}
        return {
            "ok": bool(result.get("ok")) and bool(attachment_result.get("cleared")),
            "status": str(result.get("status") or ""),
            "managed": [
                desktop_workspace_attachment_card(item)
                for item in list(attachment_result.get("cleared") or [])
                if isinstance(item, dict)
            ],
            "unresolved": list(result.get("unresolved") or []),
            "failures": list(result.get("failures") or []),
            "action": "clear",
            "item_type": "attachment",
        }

    if normalized_type in {"generated", "output"}:
        if not normalized_target:
            return {"ok": False, "error": "missing_target", "managed": []}
        result = clear_workspace_files(
            engine,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            target=normalized_target,
            delete_storage=False,
            reason="用户从桌宠手边物品面板收起。",
            timestamp=now_ts,
        )
        generated_result = result.get("generated_files") if isinstance(result.get("generated_files"), dict) else {}
        return {
            "ok": bool(result.get("ok")) and bool(generated_result.get("managed")),
            "status": str(result.get("status") or ""),
            "managed": [
                desktop_workspace_generated_card(item)
                for item in list(generated_result.get("managed") or [])
                if isinstance(item, dict)
            ],
            "unresolved": list(result.get("unresolved") or []),
            "failures": list(result.get("failures") or []),
            "action": "archive",
            "item_type": "generated",
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
    character_pack_id: str = "",
    timestamp: int,
) -> dict[str, Any]:
    result = clear_workspace_files(
        engine,
        profile_user_id=profile_user_id,
        session_id=session_id,
        character_pack_id=character_pack_id,
        target="all",
        delete_storage=False,
        reason="用户从桌宠手边物品面板一键清理工作台文件。",
        timestamp=timestamp,
    )
    attachment_result = result.get("attachments") if isinstance(result.get("attachments"), dict) else {}
    generated_result = result.get("generated_files") if isinstance(result.get("generated_files"), dict) else {}
    managed = [
        *[
            desktop_workspace_attachment_card(item)
            for item in list(attachment_result.get("cleared") or [])
            if isinstance(item, dict)
        ],
        *[
            desktop_workspace_generated_card(item)
            for item in list(generated_result.get("managed") or [])
            if isinstance(item, dict)
        ],
    ]

    return {
        "ok": bool(result.get("ok")),
        "status": str(result.get("status") or ""),
        "managed": managed,
        "unresolved": list(result.get("unresolved") or []),
        "failures": list(result.get("failures") or []),
        "action": "clear_files",
        "item_type": "workspace_files",
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
        "attachment_id": str(item.get("attachment_id") or ""),
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
        "created_at": int(item.get("created_at") or 0),
        "updated_at": int(item.get("updated_at") or item.get("created_at") or 0),
        "can_open": status == "ready",
        "can_clear": status in {"ready", "failed"},
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
