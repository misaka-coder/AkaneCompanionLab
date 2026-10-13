"""Live, bounded capability probing and directory traversal."""

from __future__ import annotations

from collections import deque
import mimetypes
from pathlib import Path
from typing import Any

from .definitions import DOCUMENT_EXTENSIONS, IMAGE_EXTENSIONS, MEDIA_EXTENSIONS, TEXT_EXTENSIONS


class WorkspaceTraversalMixin:
    def capability_inventory(self, *, max_files: int = 1024) -> dict[str, Any]:
        """Return file-presence facts, stopping when all classes are confirmed.

        This is a bounded disclosure probe, not a file count. Every call reads
        the live directory and retains the same path/link checks.
        """

        file_limit = max(1, min(10000, int(max_files or 1024)))
        pending_dirs = deque([self.root_dir])
        files_scanned = 0
        has_document = False
        has_media = False
        has_image = False
        truncated = False

        while pending_dirs:
            current = pending_dirs.popleft()
            try:
                children = sorted(current.iterdir(), key=lambda item: item.name.casefold())
            except OSError:
                continue
            for child in children:
                try:
                    resolved = child.resolve(strict=False)
                    resolved.relative_to(self.root_dir)
                    if child.is_symlink():
                        continue
                    if child.is_dir():
                        pending_dirs.append(child)
                        continue
                    if not child.is_file():
                        continue
                except (OSError, ValueError):
                    continue

                files_scanned += 1
                suffix = child.suffix.lower().lstrip(".")
                mime_type = (mimetypes.guess_type(child.name)[0] or "").lower()
                has_document = (
                    has_document or suffix in TEXT_EXTENSIONS | DOCUMENT_EXTENSIONS or mime_type.startswith("text/")
                )
                has_media = has_media or suffix in MEDIA_EXTENSIONS or mime_type.startswith(("audio/", "video/"))
                has_image = has_image or suffix in IMAGE_EXTENSIONS or mime_type.startswith("image/")
                if files_scanned >= file_limit or (has_document and has_media and has_image):
                    truncated = bool(pending_dirs) or child != children[-1]
                    pending_dirs.clear()
                    break

        return {
            "has_any_file": files_scanned > 0,
            "has_document_file": has_document,
            "has_media_file": has_media,
            "has_image_file": has_image,
            "files_scanned": files_scanned,
            "truncated": truncated,
        }

    def _walk_directory(self, directory: Path, *, depth: int) -> list[Path]:
        if depth <= 0:
            return []
        collected: list[tuple[int, Path]] = []
        stack: list[tuple[int, Path]] = [(0, directory)]
        while stack:
            current_depth, current = stack.pop()
            if current_depth >= depth:
                continue
            try:
                children = sorted(
                    current.iterdir(),
                    key=lambda item: (not item.is_dir(), item.name.casefold()),
                )
            except OSError:
                continue
            for child in children:
                try:
                    resolved = child.resolve(strict=False)
                    resolved.relative_to(self.root_dir)
                except (OSError, ValueError):
                    continue
                collected.append((current_depth + 1, child))
                if child.is_dir() and not child.is_symlink():
                    stack.append((current_depth + 1, child))
        collected.sort(key=lambda item: (item[0], self.to_uri(item[1]).casefold()))
        return [item[1] for item in collected]
