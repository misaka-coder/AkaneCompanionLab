"""Project-relative listing, bounded text search, reads, and content fingerprints."""
from __future__ import annotations

import fnmatch
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Iterable

import regex as regex_engine

from .definitions import (
    ProjectWorkspaceError, ProjectWorkspaceScope, PROJECT_INSPECT_MAX_TEXT_BYTES,
    PROJECT_INSPECT_MAX_ENTRIES, PROJECT_INSPECT_MAX_FILES, PROJECT_INSPECT_MAX_SCAN_BYTES,
    PROJECT_INSPECT_MAX_MATCHES, PROJECT_INSPECT_SEARCH_PREVIEW_CHARS,
    PROJECT_INSPECT_SEARCH_TIMEOUT_SECONDS, _INSPECTION_SKIPPED_DIRECTORIES,
)


class ProjectInspectionMixin:
    def inspect_list(
        self,
        *,
        scope: ProjectWorkspaceScope | None,
        workspace_id: str = "",
        operation_root: str | Path | None = None,
        path: str = ".",
        pattern: str = "*",
        max_depth: int = 2,
        include_hidden: bool = False,
    ) -> dict[str, Any]:
        """List project-relative entries without creating another filesystem authority."""
        record, root = self._resolve_operation_root(
            scope=scope,
            workspace_id=workspace_id,
            operation_root=operation_root,
        )
        base_relative, base = self._inspection_target(root, path, allow_root=True)
        if not base.is_dir() or base.is_symlink():
            raise ProjectWorkspaceError("not_a_directory", path=base_relative)
        clean_pattern = str(pattern or "*").strip() or "*"
        depth_limit = max(0, min(20, int(max_depth)))
        entries: list[dict[str, Any]] = []
        scan_complete = True
        visited_entries = 0
        pruned_directories = 0
        stack: list[tuple[Path, int]] = [(base, 0)]
        while stack:
            directory, depth = stack.pop()
            try:
                children = sorted(directory.iterdir(), key=lambda item: item.name.casefold(), reverse=True)
            except OSError as exc:
                raise ProjectWorkspaceError("read_failed", path=self._project_relative(root, directory)) from exc
            for child in children:
                visited_entries += 1
                if visited_entries > PROJECT_INSPECT_MAX_ENTRIES:
                    scan_complete = False
                    stack.clear()
                    break
                name = child.name
                if not include_hidden and name.startswith("."):
                    continue
                relative = self._project_relative(root, child)
                is_link = child.is_symlink()
                try:
                    is_dir = child.is_dir() if not is_link else False
                    is_file = child.is_file() if not is_link else False
                    stat = child.stat(follow_symlinks=False)
                except OSError:
                    continue
                kind = "symlink" if is_link else "directory" if is_dir else "file" if is_file else "other"
                if self._inspection_pattern_matches(relative, name, clean_pattern):
                    entries.append(
                        {
                            "path": relative + ("/" if is_dir else ""),
                            "kind": kind,
                            "bytes": int(stat.st_size) if is_file else 0,
                            "modified_ns": int(stat.st_mtime_ns),
                        }
                    )
                if is_dir and depth < depth_limit and name in _INSPECTION_SKIPPED_DIRECTORIES:
                    pruned_directories += 1
                if is_dir and depth < depth_limit and name not in _INSPECTION_SKIPPED_DIRECTORIES:
                    stack.append((child, depth + 1))
        entries.sort(key=lambda item: str(item["path"]).casefold())
        fingerprint = self._inspection_fingerprint(
            {"entries": entries, "scan_complete": scan_complete, "base": base_relative}
        )
        return {
            "workspace_id": str(record["workspace_id"]),
            "effective_cwd": str(root),
            "path": base_relative,
            "pattern": clean_pattern,
            "max_depth": depth_limit,
            "include_hidden": bool(include_hidden),
            "entries": entries,
            "scan_complete": scan_complete,
            "visited_entries": visited_entries,
            "pruned_directories": pruned_directories,
            "fingerprint": fingerprint,
        }

    def inspect_search(
        self,
        *,
        scope: ProjectWorkspaceScope | None,
        workspace_id: str = "",
        operation_root: str | Path | None = None,
        path: str = ".",
        query: str,
        include: str = "*",
        regex: bool = False,
        case_sensitive: bool = True,
        include_hidden: bool = False,
    ) -> dict[str, Any]:
        """Search UTF-8 project files and return stable, line-addressable matches."""
        record, root = self._resolve_operation_root(
            scope=scope,
            workspace_id=workspace_id,
            operation_root=operation_root,
        )
        base_relative, base = self._inspection_target(root, path, allow_root=True)
        clean_query = str(query or "")
        if not clean_query:
            raise ProjectWorkspaceError("query_required")
        clean_include = str(include or "*").strip() or "*"
        flags = 0 if case_sensitive else regex_engine.IGNORECASE
        try:
            expression = regex_engine.compile(clean_query if regex else regex_engine.escape(clean_query), flags)
        except regex_engine.error as exc:
            raise ProjectWorkspaceError("invalid_regex", detail=str(exc)) from exc

        matches: list[dict[str, Any]] = []
        scanned_files = 0
        visited_files = 0
        scanned_bytes = 0
        skipped_binary = 0
        skipped_too_large = 0
        scan_complete = True
        stop = False
        deadline = time.monotonic() + PROJECT_INSPECT_SEARCH_TIMEOUT_SECONDS
        for file_path in self._inspection_files(base, include_hidden=include_hidden):
            if time.monotonic() >= deadline:
                raise ProjectWorkspaceError("search_timeout")
            visited_files += 1
            if visited_files > PROJECT_INSPECT_MAX_FILES:
                scan_complete = False
                break
            relative = self._project_relative(root, file_path)
            if not self._inspection_pattern_matches(relative, file_path.name, clean_include):
                continue
            try:
                size = file_path.stat().st_size
            except OSError:
                continue
            if size > PROJECT_INSPECT_MAX_TEXT_BYTES:
                skipped_too_large += 1
                scan_complete = False
                continue
            if scanned_bytes + size > PROJECT_INSPECT_MAX_SCAN_BYTES:
                scan_complete = False
                break
            try:
                raw = file_path.read_bytes()
                if len(raw) > PROJECT_INSPECT_MAX_TEXT_BYTES or scanned_bytes + len(raw) > PROJECT_INSPECT_MAX_SCAN_BYTES:
                    skipped_too_large += 1
                    scan_complete = False
                    continue
                if self._inspection_bytes_are_binary(raw):
                    skipped_binary += 1
                    continue
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                skipped_binary += 1
                continue
            except OSError:
                scan_complete = False
                continue
            scanned_files += 1
            scanned_bytes += len(raw)
            for line_number, line in enumerate(text.splitlines(), start=1):
                if time.monotonic() >= deadline:
                    raise ProjectWorkspaceError("search_timeout", path=relative, line=line_number)
                try:
                    found = expression.search(line, timeout=0.05)
                except TimeoutError as exc:
                    raise ProjectWorkspaceError(
                        "search_timeout",
                        path=relative,
                        line=line_number,
                    ) from exc
                if found is None:
                    continue
                preview_truncated = len(line) > PROJECT_INSPECT_SEARCH_PREVIEW_CHARS
                preview_start = 0
                if preview_truncated:
                    preview_start = max(0, found.start() - PROJECT_INSPECT_SEARCH_PREVIEW_CHARS // 4)
                    preview_start = min(preview_start, len(line) - PROJECT_INSPECT_SEARCH_PREVIEW_CHARS)
                preview_end = min(len(line), preview_start + PROJECT_INSPECT_SEARCH_PREVIEW_CHARS)
                preview = line[preview_start:preview_end]
                matches.append(
                    {
                        "path": relative,
                        "line": line_number,
                        "column": found.start() + 1,
                        "text": preview,
                        "preview_truncated": preview_truncated,
                        "preview_start_column": preview_start + 1,
                        "preview_end_column": preview_end,
                        "line_chars": len(line),
                    }
                )
                if len(matches) >= PROJECT_INSPECT_MAX_MATCHES:
                    scan_complete = False
                    stop = True
                    break
            if stop:
                break
        fingerprint = self._inspection_fingerprint(
            {
                "matches": matches,
                "scan_complete": scan_complete,
                "scanned_files": scanned_files,
                "scanned_bytes": scanned_bytes,
                "visited_files": visited_files,
            }
        )
        return {
            "workspace_id": str(record["workspace_id"]),
            "effective_cwd": str(root),
            "path": base_relative,
            "query": clean_query,
            "include": clean_include,
            "regex": bool(regex),
            "case_sensitive": bool(case_sensitive),
            "include_hidden": bool(include_hidden),
            "matches": matches,
            "scan_complete": scan_complete,
            "scanned_files": scanned_files,
            "visited_files": visited_files,
            "scanned_bytes": scanned_bytes,
            "skipped_binary": skipped_binary,
            "skipped_too_large": skipped_too_large,
            "fingerprint": fingerprint,
        }

    def inspect_read(
        self,
        *,
        scope: ProjectWorkspaceScope | None,
        workspace_id: str = "",
        operation_root: str | Path | None = None,
        path: str,
    ) -> dict[str, Any]:
        """Read one UTF-8 source as logical lines and expose a content fingerprint."""
        record, root = self._resolve_operation_root(
            scope=scope,
            workspace_id=workspace_id,
            operation_root=operation_root,
        )
        relative, target = self._inspection_target(root, path, allow_root=False)
        if target.is_symlink() or not target.is_file():
            raise ProjectWorkspaceError("source_missing", path=relative)
        try:
            size = target.stat().st_size
            if size > PROJECT_INSPECT_MAX_TEXT_BYTES:
                raise ProjectWorkspaceError(
                    "file_too_large",
                    path=relative,
                    bytes=size,
                    max_bytes=PROJECT_INSPECT_MAX_TEXT_BYTES,
                )
            raw = target.read_bytes()
            if len(raw) > PROJECT_INSPECT_MAX_TEXT_BYTES:
                raise ProjectWorkspaceError(
                    "file_too_large",
                    path=relative,
                    bytes=len(raw),
                    max_bytes=PROJECT_INSPECT_MAX_TEXT_BYTES,
                )
            if self._inspection_bytes_are_binary(raw):
                raise ProjectWorkspaceError("source_not_text", path=relative)
            text = raw.decode("utf-8")
        except ProjectWorkspaceError:
            raise
        except UnicodeDecodeError as exc:
            raise ProjectWorkspaceError("source_not_utf8", path=relative) from exc
        except OSError as exc:
            raise ProjectWorkspaceError("read_failed", path=relative) from exc
        return {
            "workspace_id": str(record["workspace_id"]),
            "effective_cwd": str(root),
            "path": relative,
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "lines": text.splitlines(),
        }

    @staticmethod
    def _project_relative(root: Path, target: Path) -> str:
        return target.relative_to(root).as_posix()

    @staticmethod
    def _inspection_pattern_matches(relative: str, name: str, pattern: str) -> bool:
        return fnmatch.fnmatchcase(relative, pattern) or fnmatch.fnmatchcase(name, pattern)

    def _inspection_files(self, base: Path, *, include_hidden: bool) -> Iterable[Path]:
        if base.is_symlink():
            return
        if base.is_file():
            yield base
            return
        if not base.is_dir():
            return
        stack = [base]
        while stack:
            directory = stack.pop()
            try:
                children = sorted(directory.iterdir(), key=lambda item: item.name.casefold(), reverse=True)
            except OSError:
                continue
            directories: list[Path] = []
            for child in children:
                if not include_hidden and child.name.startswith("."):
                    continue
                if child.is_symlink():
                    continue
                try:
                    if child.is_file():
                        yield child
                    elif child.is_dir() and child.name not in _INSPECTION_SKIPPED_DIRECTORIES:
                        directories.append(child)
                except OSError:
                    continue
            stack.extend(directories)

    @staticmethod
    def _inspection_fingerprint(value: Any) -> str:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _inspection_bytes_are_binary(value: bytes) -> bool:
        sample = value[:4096]
        if b"\x00" in sample:
            return True
        if not sample:
            return False
        control = sum(1 for item in sample if item < 9 or 13 < item < 32)
        return control / len(sample) > 0.3
