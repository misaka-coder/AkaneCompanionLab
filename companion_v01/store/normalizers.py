"""Status/type normalizer helpers extracted from store/core.py."""

from __future__ import annotations

import re
from typing import Any


def normalize_gift_status(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return raw if raw in {"active", "archived", "consumed", "pending"} else "pending"


def normalize_persona_status(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return raw if raw in {"active", "archived", "draft"} else "draft"


def normalize_attachment_status(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return raw if raw in {"pending", "ready", "pending_observation", "failed", "stale"} else "pending"


def normalize_desktop_music_timeline_status(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return raw if raw in {"pending", "ready", "failed"} else "pending"


def normalize_attachment_kind(value: Any) -> str:
    raw = str(value or "").strip().lower() or "unknown"
    valid = {"audio", "document", "image", "video", "archive", "unknown"}
    return raw if raw in valid else "unknown"


def normalize_generated_status(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return raw if raw in {"pending", "ready", "failed"} else "pending"


def normalize_generated_format(value: Any) -> str:
    raw = str(value or "").strip().lower()
    valid = {"txt", "md", "json", "csv", "html", "png", "jpg", "webp", "svg", "mp3", "wav", "flac", "mp4", "webm"}
    return raw if raw in valid else "txt"


def normalize_gift_status_list(values: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    valid = {"active", "archived", "consumed", "pending"}
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        raw = str(v or "").strip().lower()
        if raw in valid and raw not in seen:
            seen.add(raw)
            out.append(raw)
    return out


def normalize_attachment_status_list(normalize_fn, values: Any) -> list[str]:
    valid = {"pending", "ready", "pending_observation", "failed", "stale"}
    seen: set[str] = set()
    out: list[str] = []
    for v in values if isinstance(values, (list, tuple, set)) else []:
        raw = normalize_fn(v)
        if raw in valid and raw not in seen:
            seen.add(raw)
            out.append(raw)
    return out


def normalize_generated_status_list(normalize_fn, values: Any) -> list[str]:
    valid = {"pending", "ready", "failed"}
    seen: set[str] = set()
    out: list[str] = []
    for v in values if isinstance(values, (list, tuple, set)) else []:
        raw = normalize_fn(v)
        if raw in valid and raw not in seen:
            seen.add(raw)
            out.append(raw)
    return out


def normalize_task_workspace_status(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return raw if raw in {"pending", "running", "completed", "failed", "cancelled"} else "pending"


def normalize_task_workspace_status_list(normalize_fn, values: Any) -> list[str]:
    valid = {"pending", "running", "completed", "failed", "cancelled"}
    seen: set[str] = set()
    out: list[str] = []
    for v in values if isinstance(values, (list, tuple, set)) else []:
        raw = normalize_fn(v)
        if raw in valid and raw not in seen:
            seen.add(raw)
            out.append(raw)
    return out


def normalize_task_event_status(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return raw if raw in {"pending", "handled", "failed"} else "pending"


def normalize_task_event_priority(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return raw if raw in {"high", "normal", "low"} else "normal"


def normalize_string_list(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def normalize_container_type(value: Any) -> str:
    raw = str(value or "").strip().lower()
    valid = {"gift", "artifact", "card", "badge", "trophy", "collection", "box", "folder"}
    return raw if raw in valid else "box"


def infer_asset_type_from_media_kind(media_kind: Any) -> str:
    _MEDIA_KIND_MAP = {
        "image": "image",
        "gif": "image",
        "png": "image",
        "jpg": "image",
        "jpeg": "image",
        "webp": "image",
        "audio": "audio",
        "mp3": "audio",
        "wav": "audio",
        "flac": "audio",
        "ogg": "audio",
        "m4a": "audio",
        "video": "video",
        "mp4": "video",
        "webm": "video",
        "mov": "video",
        "document": "document",
        "pdf": "document",
        "doc": "document",
        "docx": "document",
        "archive": "archive",
        "zip": "archive",
        "rar": "archive",
        "7z": "archive",
    }
    key = str(media_kind or "").strip().lower()
    return _MEDIA_KIND_MAP.get(key, "unknown")


def attachment_handle_prefix(kind: Any) -> str:
    _PREFIX_MAP = {
        "audio": "aud",
        "document": "doc",
        "image": "img",
        "video": "vid",
        "archive": "arc",
        "unknown": "unk",
    }
    return _PREFIX_MAP.get(str(kind or "").strip().lower(), "unk")


def sequence_no_from_generated_handle(value: Any) -> int:
    match = re.search(r"gen_(\d+)", str(value or ""))
    return int(match.group(1)) if match else 0


def sequence_no_from_attachment_handle(value: Any) -> int:
    match = re.search(r"(?:aud|doc|img|vid|arc|unk)_(\d+)", str(value or ""))
    return int(match.group(1)) if match else 0
