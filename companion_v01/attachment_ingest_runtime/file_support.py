"""File names, kinds, bounded labels and managed storage operations."""

from __future__ import annotations

import mimetypes
import shutil
import time
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
import config
from .definitions import (
    AUDIO_MEDIA_SUFFIXES,
    AttachmentMaterializationError,
    DOCUMENT_SUFFIXES,
    IMAGE_SUFFIXES,
    QQ_ATTACHMENT_DEFAULT_MAX_BYTES,
    TEXT_SUFFIXES,
)


class AttachmentFileSupportMixin:
    """Methods use the state owned by AttachmentIngestService."""

    def _copy_trusted_file(
        self,
        *,
        source: Path,
        target_path: Path,
        enforce_max_bytes: bool = True,
    ) -> None:
        max_bytes = int(getattr(config, "QQ_ATTACHMENT_MAX_BYTES", QQ_ATTACHMENT_DEFAULT_MAX_BYTES) or 0)
        try:
            file_size = source.stat().st_size
        except OSError as exc:
            raise AttachmentMaterializationError("attachment_trusted_local_missing") from exc
        if enforce_max_bytes and max_bytes > 0 and file_size > max_bytes:
            raise AttachmentMaterializationError("attachment_too_large")
        temp_path = target_path.with_name(f".{target_path.name}.part")
        try:
            temp_path.unlink(missing_ok=True)
            shutil.copyfile(source, temp_path)
            temp_path.replace(target_path)
        except Exception as exc:
            temp_path.unlink(missing_ok=True)
            if isinstance(exc, AttachmentMaterializationError):
                raise
            raise AttachmentMaterializationError("attachment_materialization_failed") from exc

    def _write_bytes_atomically(self, *, target_path: Path, payload: bytes) -> None:
        temp_path = target_path.with_name(f".{target_path.name}.part")
        try:
            temp_path.unlink(missing_ok=True)
            temp_path.write_bytes(payload)
            temp_path.replace(target_path)
        except Exception as exc:
            temp_path.unlink(missing_ok=True)
            raise AttachmentMaterializationError("attachment_materialization_failed") from exc

    def _safe_prompt_label(self, value: Any) -> str:
        text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or ""))
        text = re.sub(r"\s+", " ", text).strip()
        return text[:48]

    def _safe_float(self, value: Any) -> float | None:
        try:
            number = float(str(value).strip())
        except Exception:
            return None
        return number if number >= 0 else None

    def _storage_relpath(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.base_dir)).replace("\\", "/")
        except ValueError:
            return str(path)

    def _workspace_date_dir(self, item: dict[str, Any]) -> Path:
        timestamp = self._safe_int(item.get("created_at")) or int(time.time())
        date_slug = time.strftime("%Y-%m-%d", time.localtime(timestamp))
        target_dir = self.base_dir / date_slug
        try:
            target_dir.resolve(strict=False).relative_to(self.base_dir.resolve())
        except Exception:
            raise RuntimeError("attachment destination escaped the managed workspace") from None
        return target_dir

    def _available_attachment_path(
        self,
        *,
        target_dir: Path,
        handle: str,
        origin_name: str,
        suffix: str,
    ) -> Path:
        stem = self._available_attachment_stem(
            target_dir=target_dir,
            handle=handle,
            origin_name=origin_name,
            suffix=suffix,
        )
        return target_dir / f"{stem}{suffix}"

    def _available_attachment_stem(
        self,
        *,
        target_dir: Path,
        handle: str,
        origin_name: str,
        suffix: str = "",
    ) -> str:
        clean_suffix = str(suffix or "").lower()
        safe_origin_name = self._safe_path_part(origin_name) if origin_name else ""
        origin_stem = Path(safe_origin_name).stem if safe_origin_name else ""
        base_stem = f"{handle}__{origin_stem}" if origin_stem else handle
        candidate_stem = base_stem
        sequence = 2
        while any(target_dir.glob(f"{candidate_stem}.*")) or (
            clean_suffix and (target_dir / f"{candidate_stem}{clean_suffix}").exists()
        ):
            candidate_stem = f"{base_stem}_{sequence}"
            sequence += 1
        return candidate_stem

    def _guess_suffix(self, *, origin_name: str, payload: dict[str, Any], kind: str) -> str:
        suffix = Path(origin_name).suffix.lower()
        if suffix:
            return suffix
        url = str(payload.get("url") or "").strip()
        if url:
            path = unquote(urlparse(url).path)
            suffix = Path(path).suffix.lower()
            if suffix:
                return suffix
        mime_type = str(payload.get("mime_type") or "").strip()
        guessed = mimetypes.guess_extension(mime_type) if mime_type else ""
        if guessed:
            return guessed
        return ".png" if kind == "image" else ".bin"

    def _normalize_kind(self, value: Any) -> str:
        kind = str(value or "").strip().lower()
        if kind in {"photo", "picture", "pic", "img"}:
            return "image"
        if kind in {"doc", "text", "txt", "pdf"}:
            return "document"
        if kind in {"music", "song", "voice", "record"}:
            return "audio"
        if kind in {"image", "document", "audio", "file"}:
            return kind
        return "file"

    def _infer_local_kind(self, *, source_path: Path, mime_type: str) -> str:
        suffix = source_path.suffix.lower()
        normalized_mime = str(mime_type or "").strip().lower()
        if suffix in IMAGE_SUFFIXES or normalized_mime.startswith("image/"):
            return "image"
        if suffix in AUDIO_MEDIA_SUFFIXES or normalized_mime.startswith("audio/"):
            return "audio"
        if suffix in TEXT_SUFFIXES or suffix in DOCUMENT_SUFFIXES or normalized_mime.startswith("text/"):
            return "document"
        return "file"

    def _file_kind_from_suffix(self, suffix: str) -> str:
        return {
            ".md": "markdown",
            ".markdown": "markdown",
            ".json": "json",
            ".toml": "toml",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".csv": "csv",
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".log": "log",
        }.get(suffix, suffix.lstrip(".") or "text")

    def _clean_filename(self, value: Any) -> str:
        raw = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or "")).strip().replace("\\", "/")
        parsed = urlparse(raw)
        text = unquote(parsed.path or "").replace("\\", "/").split("/")[-1].strip()
        return text[:160]

    def _safe_path_part(self, value: Any) -> str:
        text = str(value or "").strip() or "unknown"
        safe = "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in text)
        return safe[:80] or "unknown"

    def _safe_int(self, value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except Exception:
            return 0
