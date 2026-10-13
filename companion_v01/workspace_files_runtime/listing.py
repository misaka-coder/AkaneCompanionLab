"""Live file descriptions and recent-file metadata."""

from __future__ import annotations

from datetime import datetime
import mimetypes
from pathlib import Path
from typing import Any

from .definitions import ARCHIVE_EXTENSIONS, DOCUMENT_EXTENSIONS, TEXT_EXTENSIONS, WorkspacePathError


class WorkspaceListingMixin:
    def list_items(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        paths: list[str] | None = None,
        depth: int = 1,
        max_entries: int = 10000,
    ) -> dict[str, Any]:
        requested_paths = self._normalize_target_list(paths or ["workspace:/"])
        effective_depth = max(0, min(8, int(depth or 0)))
        entry_limit = max(1, min(50000, int(max_entries or 10000)))
        state_map = self._state_map(profile_user_id=profile_user_id, session_id=session_id)
        results: list[dict[str, Any]] = []
        total_entries = 0
        truncated = False

        for requested in requested_paths:
            try:
                target_path, target_uri = self.resolve_uri(requested)
            except WorkspacePathError as exc:
                results.append(
                    {
                        "requested": "(invalid workspace path)",
                        "status": "denied",
                        "reason": str(exc),
                        "entries": [],
                    }
                )
                continue
            if not target_path.exists():
                results.append(
                    {
                        "requested": target_uri,
                        "status": "missing",
                        "reason": "workspace item does not exist",
                        "entries": [],
                    }
                )
                continue

            entries: list[dict[str, Any]] = []
            if target_path.is_file():
                entries.append(self._describe_path(target_path, state_map=state_map))
            elif target_path.is_dir():
                for path in self._walk_directory(target_path, depth=effective_depth):
                    if total_entries >= entry_limit:
                        truncated = True
                        break
                    entries.append(self._describe_path(path, state_map=state_map))
                    total_entries += 1
            else:
                entries.append(self._describe_path(target_path, state_map=state_map))
            results.append(
                {
                    "requested": target_uri,
                    "status": "ok",
                    "entries": entries,
                }
            )
            if truncated:
                break

        return {
            "status": "ok" if any(item["status"] == "ok" for item in results) else "failed",
            "root": "workspace:/",
            "results": results,
            "truncated": truncated,
        }

    def _describe_path(
        self,
        path: Path,
        *,
        state_map: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        uri = self.to_uri(path)
        state = state_map.get(uri)
        try:
            stat = path.stat()
            size = int(stat.st_size) if path.is_file() else 0
            modified_at = int(stat.st_mtime)
        except OSError:
            size = 0
            modified_at = 0
        suffix = path.suffix.lower().lstrip(".")
        if path.is_symlink():
            kind = "symlink"
        elif path.is_dir():
            kind = "directory"
        elif path.is_file():
            kind = "file"
        else:
            kind = "other"
        focus_rank = int((state or {}).get("focus_rank") or 0)
        if focus_rank > 0:
            workspace_status = "focused"
        elif state is not None:
            workspace_status = "hidden"
        else:
            workspace_status = "present"
        return {
            "uri": uri,
            "name": path.name or "Akane Workspace",
            "kind": kind,
            "status": "ok",
            "workspace_status": workspace_status,
            "focus_rank": focus_rank,
            "file_ext": suffix,
            "mime_type": mimetypes.guess_type(path.name)[0] or "",
            "size": size,
            "modified_at": modified_at,
            "readable": suffix in TEXT_EXTENSIONS | DOCUMENT_EXTENSIONS | ARCHIVE_EXTENSIONS,
        }

    def _recent_file_manifest(self, *, limit: int) -> list[dict[str, Any]]:
        candidates: list[tuple[int, str, int]] = []
        for path in self._walk_directory(self.root_dir, depth=64):
            if not path.is_file():
                continue
            try:
                stat = path.stat()
                modified_at = int(stat.st_mtime)
                size = int(stat.st_size)
                uri = self.to_uri(path)
            except (OSError, WorkspacePathError):
                continue
            candidates.append((modified_at, uri, size))
        candidates.sort(key=lambda item: (-item[0], item[1].casefold()))
        return [
            {
                "uri": uri,
                "size": size,
                "modified_at": modified_at,
                "modified_label": datetime.fromtimestamp(modified_at).strftime("%Y-%m-%d %H:%M"),
            }
            for modified_at, uri, size in candidates[: max(1, int(limit or 1))]
        ]
