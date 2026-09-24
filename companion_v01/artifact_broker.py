from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


CANONICAL_ARTIFACT_PREFIXES = ("audio_", "file_", "gen_")
LEGACY_ATTACHMENT_PREFIXES = ("arc_", "aud_", "doc_", "img_", "unk_", "vid_")
_ARTIFACT_HANDLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    instance_id: str
    handle: str
    kind: str
    path: Path
    file_name: str
    media_type: str
    size_bytes: int
    sha256: str

    def transfer_headers(self) -> dict[str, str]:
        return {
            "Cache-Control": "no-store",
            "X-Akane-Artifact-Instance": self.instance_id,
            "X-Akane-Artifact-Size": str(self.size_bytes),
            "X-Akane-Artifact-Sha256": self.sha256,
            "X-Akane-Artifact-Mime": self.media_type,
        }


class ArtifactBroker:
    """Instance-owned authority for materializing handle-backed file transfers."""

    def __init__(self, *, instance_id: str, data_root: Path) -> None:
        clean_instance = str(instance_id or "").strip()
        if not clean_instance:
            raise ValueError("artifact_instance_id_required")
        self.instance_id = clean_instance
        self.data_root = Path(data_root).expanduser().resolve()
        self._hash_cache: dict[tuple[str, int, int], str] = {}
        self._lock = threading.RLock()

    def record(
        self,
        *,
        handle: str,
        kind: str,
        path: Path,
        file_name: str,
        media_type: str,
        item: Mapping[str, Any] | None = None,
    ) -> ArtifactRecord:
        clean_handle = str(handle or "").strip()
        if (
            not _ARTIFACT_HANDLE_RE.fullmatch(clean_handle)
            or not clean_handle.startswith((*CANONICAL_ARTIFACT_PREFIXES, *LEGACY_ATTACHMENT_PREFIXES))
        ):
            raise ValueError("artifact_handle_invalid")
        clean_kind = str(kind or "").strip().lower()
        if clean_kind not in {"attachment", "generated"}:
            raise ValueError("artifact_kind_invalid")

        resolved_path = Path(path).expanduser().resolve(strict=True)
        if not resolved_path.is_file():
            raise ValueError("artifact_file_missing")
        if not resolved_path.is_relative_to(self.data_root):
            raise ValueError("artifact_outside_instance_root")

        stat = resolved_path.stat()
        size_bytes = int(stat.st_size)
        if size_bytes <= 0:
            raise ValueError("artifact_file_empty")
        declared_size = int((item or {}).get("file_size") or (item or {}).get("size_bytes") or 0)
        if declared_size > 0 and declared_size != size_bytes:
            raise ValueError("artifact_size_record_mismatch")

        clean_name = _safe_file_name(file_name, fallback=clean_handle)
        clean_mime = str(media_type or "application/octet-stream").split(";", 1)[0].strip().lower()
        if not clean_mime or "/" not in clean_mime:
            clean_mime = "application/octet-stream"
        sha256 = self._sha256(resolved_path, size_bytes=size_bytes, mtime_ns=int(stat.st_mtime_ns))
        return ArtifactRecord(
            instance_id=self.instance_id,
            handle=clean_handle,
            kind=clean_kind,
            path=resolved_path,
            file_name=clean_name,
            media_type=clean_mime,
            size_bytes=size_bytes,
            sha256=sha256,
        )

    def _sha256(self, path: Path, *, size_bytes: int, mtime_ns: int) -> str:
        key = (str(path), int(size_bytes), int(mtime_ns))
        with self._lock:
            cached = self._hash_cache.get(key)
            if cached:
                return cached
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        value = digest.hexdigest()
        with self._lock:
            self._hash_cache = {key: value}
        return value


def _safe_file_name(value: str, *, fallback: str) -> str:
    name = Path(str(value or "").replace("\\", "/")).name.strip().strip(".")
    clean = "".join(
        "_" if ch in {'<', '>', ':', '"', '/', "\\", '|', '?', '*'} or ord(ch) < 32 else ch
        for ch in name
    ).strip().strip(".")
    if not clean:
        clean = str(fallback or "artifact").strip() or "artifact"
    return clean[:180]


__all__ = [
    "ArtifactBroker",
    "ArtifactRecord",
    "CANONICAL_ARTIFACT_PREFIXES",
    "LEGACY_ATTACHMENT_PREFIXES",
]
