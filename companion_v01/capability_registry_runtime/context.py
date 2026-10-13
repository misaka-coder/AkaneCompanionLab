"""Attachment classification and host-frozen context gates for capability selection."""
from __future__ import annotations

from typing import Any

from ..client_protocol import ClientMode
from .models import CapabilitySnapshot


DOCUMENT_ATTACHMENT_FORMATS = {
    "txt",
    "md",
    "markdown",
    "log",
    "lrc",
    "srt",
    "vtt",
    "json",
    "toml",
    "yaml",
    "yml",
    "csv",
    "ini",
    "cfg",
    "conf",
    "py",
    "js",
    "ts",
    "tsx",
    "jsx",
    "html",
    "css",
    "xml",
    "sql",
    "java",
    "c",
    "cpp",
    "h",
    "hpp",
    "cs",
    "go",
    "rs",
    "pdf",
    "docx",
    "xlsx",
}

DOCUMENT_GENERATED_FORMATS = {"txt", "md", "docx", "xlsx", "pdf", "json", "csv", "html"}
MEDIA_FORMATS = {"mp3", "wav", "flac", "m4a", "aac", "ogg", "opus", "mp4", "mov", "mkv", "webm", "avi"}
IMAGE_GENERATED_FORMATS = {"png", "jpg", "jpeg", "webp", "gif"}


def _always(_: CapabilitySnapshot) -> bool:
    return True


def _has_any_attachment(snapshot: CapabilitySnapshot) -> bool:
    return snapshot.has_any_attachment


def _has_document_context(snapshot: CapabilitySnapshot) -> bool:
    return (
        snapshot.has_document_attachment or snapshot.has_document_generated_file or snapshot.has_document_workspace_file
    )


def _has_media_context(snapshot: CapabilitySnapshot) -> bool:
    return snapshot.has_media_attachment or snapshot.has_media_generated_file or snapshot.has_media_workspace_file


def _execution_enabled(snapshot: CapabilitySnapshot) -> bool:
    return snapshot.execution_enabled


def _execution_qq_enabled(snapshot: CapabilitySnapshot) -> bool:
    return snapshot.execution_qq_enabled


def _execution_available_in_mode(snapshot: CapabilitySnapshot) -> bool:
    """Host-frozen execution availability for the current client mode.

    QQ relies on the owner-opened Shell flag; desktop relies on the configured
    execution provider. Neither depends on transient provider readiness, so the
    capability schema stays byte-identical while readiness fluctuates.
    """
    if snapshot.client_mode == ClientMode.QQ_TEXT:
        return snapshot.execution_qq_enabled
    return snapshot.execution_enabled


def _has_image_context(snapshot: CapabilitySnapshot) -> bool:
    return snapshot.has_image_attachment or snapshot.has_image_generated_file or snapshot.has_image_workspace_file


def _has_generated_file(snapshot: CapabilitySnapshot) -> bool:
    return snapshot.has_generated_file


def _has_deliverable_file(snapshot: CapabilitySnapshot) -> bool:
    return snapshot.has_any_attachment or snapshot.has_generated_file


def is_document_attachment(item: dict) -> bool:
    kind = str(item.get("kind") or "").strip().lower()
    if kind == "document":
        return True
    detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
    file_kind = str(detail.get("file_kind") or item.get("file_ext") or "").strip().lower().lstrip(".")
    mime_type = str(item.get("mime_type") or detail.get("mime_type") or "").strip().lower()
    return file_kind in DOCUMENT_ATTACHMENT_FORMATS or mime_type.startswith("text/")


def is_media_attachment(item: dict) -> bool:
    kind = str(item.get("kind") or "").strip().lower()
    if kind == "audio":
        return True
    detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
    file_kind = str(detail.get("file_kind") or item.get("file_ext") or "").strip().lower().lstrip(".")
    mime_type = str(item.get("mime_type") or detail.get("mime_type") or "").strip().lower()
    return bool(detail.get("media_info")) or file_kind in MEDIA_FORMATS or mime_type.startswith(("audio/", "video/"))


def is_image_attachment(item: dict) -> bool:
    kind = str(item.get("kind") or "").strip().lower()
    mime_type = str(item.get("mime_type") or "").strip().lower()
    return kind == "image" or mime_type.startswith("image/")


def is_document_generated_file(item: dict) -> bool:
    output_format = str(item.get("output_format") or item.get("file_ext") or "").strip().lower().lstrip(".")
    return output_format in DOCUMENT_GENERATED_FORMATS


def is_media_generated_file(item: dict) -> bool:
    output_format = str(item.get("output_format") or item.get("file_ext") or "").strip().lower().lstrip(".")
    return output_format in MEDIA_FORMATS


def is_image_generated_file(item: dict) -> bool:
    output_format = str(item.get("output_format") or item.get("file_ext") or "").strip().lower().lstrip(".")
    mime_type = str(item.get("mime_type") or "").strip().lower()
    return output_format in IMAGE_GENERATED_FORMATS or mime_type.startswith("image/")
