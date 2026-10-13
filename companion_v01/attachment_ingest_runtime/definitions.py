"""Attachment classifications, limits and materialization contracts."""

from __future__ import annotations

from dataclasses import dataclass, field


TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".markdown",
    ".log",
    ".lrc",
    ".srt",
    ".vtt",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".csv",
    ".ini",
    ".cfg",
    ".conf",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".html",
    ".css",
    ".xml",
    ".sql",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".cs",
    ".go",
    ".rs",
}

DOCUMENT_SUFFIXES = {
    ".pdf",
    ".docx",
    ".xlsx",
}

MEDIA_SUFFIXES = {
    ".mp3",
    ".wav",
    ".flac",
    ".m4a",
    ".aac",
    ".ogg",
    ".opus",
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
    ".avi",
}

AUDIO_MEDIA_SUFFIXES = {
    ".mp3",
    ".wav",
    ".flac",
    ".m4a",
    ".aac",
    ".ogg",
    ".opus",
}

IMAGE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
}

REMOTE_MEDIA_DEFAULT_TIMEOUT = 180.0

REMOTE_MEDIA_DEFAULT_MAX_BYTES = 1024 * 1024 * 1024

REMOTE_MEDIA_DEFAULT_MAX_URLS = 8

QQ_ATTACHMENT_DEFAULT_MAX_BYTES = 64 * 1024 * 1024

REMOTE_MEDIA_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)

REMOTE_MEDIA_DEFAULT_REFERER = "https://www.bilibili.com/"

REMOTE_DOWNLOAD_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

REMOTE_DOWNLOAD_MAX_REDIRECTS = 3

REMOTE_MEDIA_YTDLP_ALLOWED_EXTRACTORS = (
    "BiliBili",
    "youtube",
    "Douyin",
    "Ixigua",
    "Kuaishou",
)

class AttachmentMaterializationError(RuntimeError):
    """Stable materialization failure that never contains a locator or raw exception."""

    def __init__(self, code: str) -> None:
        self.code = str(code or "attachment_materialization_failed")
        super().__init__(self.code)

@dataclass(frozen=True)
class RemoteMediaDescriptor:
    source_url: str = field(repr=False)
    title: str
    ext: str
    mime_type: str
    kind: str
    download_mode: str
    webpage_url: str = field(default="", repr=False)
    extractor: str = ""
    extractor_key: str = ""
    uploader: str = ""
    channel: str = ""
    duration_seconds: float | None = None
    thumbnail_url: str = field(default="", repr=False)
    description: str = ""
    file_size_hint: int = 0
    media_urls: tuple[str, ...] = field(default_factory=tuple, repr=False)

    @property
    def origin_name(self) -> str:
        suffix = f".{self.ext.lstrip('.')}" if str(self.ext).strip() else ""
        title = str(self.title or "remote_media").strip() or "remote_media"
        return f"{title}{suffix}"
