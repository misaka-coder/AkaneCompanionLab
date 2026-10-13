"""Workspace file kinds and public result/error types."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


TEXT_EXTENSIONS = {
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
    "tsv",
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
    "sh",
    "ps1",
    "bat",
}


DOCUMENT_EXTENSIONS = {"docx", "xlsx", "pdf"}


ARCHIVE_EXTENSIONS = {"zip"}


MEDIA_EXTENSIONS = {"mp3", "wav", "flac", "m4a", "aac", "ogg", "opus", "mp4", "mov", "mkv", "webm", "avi"}


IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif", "bmp", "tif", "tiff"}


WORKSPACE_LAYER_NAMES = ("Inbox", "Outputs", "Archive")


class WorkspacePathError(ValueError):
    pass


class WorkspaceReaderUnavailable(RuntimeError):
    pass


class WorkspaceReadLimitExceeded(WorkspaceReaderUnavailable):
    pass


DOCUMENT_EXTRACTION_CHAR_LIMIT = 4_000_000


@dataclass(frozen=True)
class WorkspaceResolvedFile:
    path: Path
    uri: str
