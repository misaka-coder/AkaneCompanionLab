"""Resolve checked workspace targets without loading file contents."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .definitions import WorkspacePathError, WorkspaceResolvedFile


class WorkspaceTargetsMixin:
    def resolve_file_targets(
        self,
        *,
        targets: list[str],
        recursive: bool = True,
        max_files: int = 500,
    ) -> tuple[list[WorkspaceResolvedFile], list[dict[str, Any]], bool]:
        requested_targets = self._normalize_target_list(targets)
        file_limit = max(1, min(5000, int(max_files or 500)))
        resolved_files: list[WorkspaceResolvedFile] = []
        item_results: list[dict[str, Any]] = []
        seen_uris: set[str] = set()
        truncated = False

        for requested in requested_targets:
            try:
                path, uri = self.resolve_uri(requested)
            except WorkspacePathError as exc:
                item_results.append(
                    {
                        "requested": "(invalid workspace path)",
                        "status": "denied",
                        "reason": str(exc),
                    }
                )
                continue
            if not path.exists():
                item_results.append({"uri": uri, "status": "missing"})
                continue
            if path.is_file():
                if uri not in seen_uris:
                    if len(resolved_files) >= file_limit:
                        truncated = True
                        item_results.append({"uri": uri, "status": "limit_reached", "file_count": 0})
                        continue
                    resolved_files.append(WorkspaceResolvedFile(path=path, uri=uri))
                    seen_uris.add(uri)
                item_results.append({"uri": uri, "status": "resolved", "file_count": 1})
            elif path.is_dir():
                candidates = self._walk_directory(path, depth=64 if recursive else 1)
                directory_files = [candidate for candidate in candidates if candidate.is_file()]
                added_count = 0
                for candidate in directory_files:
                    candidate_uri = self.to_uri(candidate)
                    if candidate_uri in seen_uris:
                        continue
                    if len(resolved_files) >= file_limit:
                        truncated = True
                        break
                    resolved_files.append(WorkspaceResolvedFile(path=candidate, uri=candidate_uri))
                    seen_uris.add(candidate_uri)
                    added_count += 1
                item_results.append(
                    {
                        "uri": uri,
                        "status": "resolved",
                        "file_count": added_count,
                    }
                )
            else:
                item_results.append({"uri": uri, "status": "unsupported"})

        return resolved_files, item_results, truncated

    def resolve_file_uri(self, value: str) -> Path | None:
        try:
            path, _ = self.resolve_uri(value)
        except WorkspacePathError:
            return None
        if path.exists() and path.is_file():
            return path
        return None
