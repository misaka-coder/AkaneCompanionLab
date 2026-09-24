from __future__ import annotations

from dataclasses import dataclass
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import regex as regex_engine
import sys
import threading
import time
import uuid
from typing import Any, Iterable, Mapping

from .store import MemoryStore


PROJECT_WRITE_MAX_CHARS = 256 * 1024
PROJECT_PATCH_MAX_CHARS = 256 * 1024
PROJECT_INSPECT_MAX_TEXT_BYTES = 64 * 1024 * 1024
PROJECT_INSPECT_MAX_ENTRIES = 20_000
PROJECT_INSPECT_MAX_FILES = 5_000
PROJECT_INSPECT_MAX_SCAN_BYTES = 64 * 1024 * 1024
PROJECT_INSPECT_MAX_MATCHES = 20_000
PROJECT_INSPECT_SEARCH_PREVIEW_CHARS = 800
PROJECT_INSPECT_SEARCH_TIMEOUT_SECONDS = 8.0
_PROJECT_ID_RE = re.compile(r"^proj_[a-f0-9]{32}$")
_PATCH_BEGIN = "*** Begin Patch"
_PATCH_END = "*** End Patch"
_PATCH_FILE_PREFIXES = ("*** Add File:", "*** Delete File:", "*** Update File:")
_ROOT_KIND_MANAGED = "managed"
_ROOT_KIND_HOST_BOUND = "host_bound"
_INSPECTION_SKIPPED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".bzr",
        ".jj",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".next",
        "coverage",
        "dist",
        "build",
        "target",
    }
)


class ProjectWorkspaceError(ValueError):
    def __init__(self, reason: str, **details: Any) -> None:
        super().__init__(reason)
        self.reason = str(reason)
        self.details = dict(details)


@dataclass(frozen=True)
class ProjectWorkspaceScope:
    owner_kind: str
    owner_id: str
    actor_scope: str
    selection_scope: str = ""

    @property
    def shared_group(self) -> str:
        return self.selection_scope if self.selection_scope.startswith("qq_group_shared_") else ""

    @property
    def selection_key(self) -> str:
        if self.shared_group:
            return "project-selection:group:" + hashlib.sha256(self.shared_group.encode("utf-8")).hexdigest()
        base = f"{self.owner_kind}\x1f{self.owner_id}\x1f{self.actor_scope}"
        payload = (base if not self.selection_scope else f"{base}\x1f{self.selection_scope}").encode("utf-8")
        return "project-selection:" + hashlib.sha256(payload).hexdigest()


class ProjectWorkspaceService:
    """Persistent project identity plus one atomic source-operation authority.

    This is separate from the attachment ``workspace:/`` and TaskWorkspace
    ledger. Registered projects use a workspace id and ``alias:project``;
    unregistered task directories may reuse a cwd already authorized and
    resolved by the execution provider.
    """

    def __init__(
        self,
        *,
        store: MemoryStore,
        execution_workspace_root: str | Path,
        protected_roots: Iterable[str | Path] = (),
    ) -> None:
        self.store = store
        self.execution_workspace_root = Path(execution_workspace_root).resolve()
        self.projects_root = (self.execution_workspace_root / "Projects").resolve()
        default_protected = (
            Path(sys.prefix).resolve(),
            Path(self.store.base_dir).resolve(),
        )
        self.protected_roots = tuple(
            dict.fromkeys(
                Path(item).expanduser().resolve(strict=False)
                for item in (*default_protected, *protected_roots)
                if str(item or "").strip()
            )
        )
        self._lock = threading.RLock()
        self._ensure_root()

    def scope_for(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        client_mode: str,
        actor_stable_id: str = "",
        actor_profile_user_id: str = "",
    ) -> ProjectWorkspaceScope:
        profile = str(profile_user_id or "").strip()
        session = str(session_id or "").strip()
        actor = str(actor_stable_id or "").strip()
        actor_profile = str(actor_profile_user_id or "").strip()
        mode = str(client_mode or "").strip().lower()
        if not profile:
            raise ProjectWorkspaceError("workspace_owner_required")
        if mode.startswith("qq"):
            if session.startswith("qq_group_shared_"):
                if not actor:
                    raise ProjectWorkspaceError("group_actor_required")
                if not actor_profile:
                    raise ProjectWorkspaceError("group_actor_profile_required")
                return ProjectWorkspaceScope("qq_user", actor_profile, "", session)
            return ProjectWorkspaceScope("qq_user", actor_profile or profile, "", session)
        if session.startswith("qq_group_shared_"):
            if not actor:
                raise ProjectWorkspaceError("group_actor_required")
            return ProjectWorkspaceScope("group", session, actor)
        if mode == "desktop_pet":
            return ProjectWorkspaceScope("desktop", profile, "")
        return ProjectWorkspaceScope("private", profile, "")

    def create(self, *, scope: ProjectWorkspaceScope, display_name: str) -> dict[str, Any]:
        self._prepare_scope(scope)
        name = self._display_name(display_name)
        workspace_id = "proj_" + uuid.uuid4().hex
        root_relpath = f"Projects/{workspace_id}"
        final_root = self._root_from_relpath(root_relpath)
        temp_root = self.projects_root / f".{workspace_id}.{uuid.uuid4().hex}.tmp"
        with self._lock:
            temp_root.mkdir(parents=False, exist_ok=False)
            try:
                os.replace(temp_root, final_root)
                record = self.store.add_project_workspace(
                    workspace_id=workspace_id,
                    display_name=name,
                    owner_kind=scope.owner_kind,
                    owner_id=scope.owner_id,
                    actor_scope=scope.actor_scope,
                    root_relpath=root_relpath,
                )
                self.store.set_project_workspace_selection(
                    selection_key=scope.selection_key,
                    workspace_id=workspace_id,
                )
            except Exception:
                if temp_root.exists():
                    temp_root.rmdir()
                if final_root.exists() and not any(final_root.iterdir()):
                    final_root.rmdir()
                raise
        return self._public_record(record, selected=True)

    def bind_existing(
        self,
        *,
        scope: ProjectWorkspaceScope,
        host_directory: str | Path,
        display_name: str = "",
    ) -> dict[str, Any]:
        self._prepare_scope(scope)
        root = self._validate_host_binding_root(host_directory)
        name = self._display_name(display_name or root.name)
        canonical = os.path.normcase(str(root))
        with self._lock:
            existing_records = self.store.list_project_workspaces(
                owner_kind=scope.owner_kind,
                owner_id=scope.owner_id,
                actor_scope=scope.actor_scope,
                include_archived=True,
            )
            for existing in existing_records:
                if str(existing.get("root_kind") or _ROOT_KIND_MANAGED) != _ROOT_KIND_HOST_BOUND:
                    continue
                existing_path = os.path.normcase(str(existing.get("host_root_path") or ""))
                if existing_path != canonical:
                    continue
                if str(existing.get("state") or "") != "active":
                    existing = self.store.update_project_workspace_state(
                        workspace_id=str(existing["workspace_id"]),
                        state="active",
                    ) or existing
                self.store.set_project_workspace_selection(
                    selection_key=scope.selection_key,
                    workspace_id=str(existing["workspace_id"]),
                )
                result = self._public_record(existing, selected=True)
                result["already_bound"] = True
                return result
            workspace_id = "proj_" + uuid.uuid4().hex
            record = self.store.add_project_workspace(
                workspace_id=workspace_id,
                display_name=name,
                owner_kind=scope.owner_kind,
                owner_id=scope.owner_id,
                actor_scope=scope.actor_scope,
                root_relpath="",
                root_kind=_ROOT_KIND_HOST_BOUND,
                host_root_path=str(root),
            )
            self.store.set_project_workspace_selection(
                selection_key=scope.selection_key,
                workspace_id=workspace_id,
            )
        return self._public_record(record, selected=True)

    def list(self, *, scope: ProjectWorkspaceScope, include_archived: bool = False) -> dict[str, Any]:
        self._prepare_scope(scope)
        current = self.current(scope=scope)
        selected_id = str((current or {}).get("workspace_id") or "")
        records = self.store.list_project_workspaces(
            owner_kind=scope.owner_kind,
            owner_id=scope.owner_id,
            actor_scope=scope.actor_scope,
            include_archived=include_archived,
        )
        public_records = [
            self._public_record(item, selected=item["workspace_id"] == selected_id)
            for item in records
        ]
        # A member sees the group's selected project, not the owner's private catalog.
        if current and not any(item["selected"] for item in public_records):
            public_records.insert(0, current)
        return {
            "status": "ok",
            "selected_workspace_id": selected_id,
            "workspaces": public_records,
        }

    def select(self, *, scope: ProjectWorkspaceScope, workspace_id: str) -> dict[str, Any]:
        self._prepare_scope(scope)
        record = self._owned_record(scope=scope, workspace_id=workspace_id, require_active=True)
        self._root_from_record(record, require_exists=True)
        self.store.set_project_workspace_selection(
            selection_key=scope.selection_key,
            workspace_id=str(record["workspace_id"]),
        )
        return self._public_record(record, selected=True)

    def close(self, *, scope: ProjectWorkspaceScope) -> dict[str, Any]:
        """Stop using the selected project as this conversation's default cwd.

        Closing changes only the conversation selection.  It does not archive
        the project, delete files, or change any filesystem permission.
        """

        self._prepare_scope(scope)
        workspace_id = self.store.get_project_workspace_selection(selection_key=scope.selection_key)
        if workspace_id:
            self.store.clear_project_workspace_selection(
                selection_key=scope.selection_key,
                workspace_id=workspace_id,
            )
        return {
            "closed": bool(workspace_id),
            "previous_workspace_id": str(workspace_id or ""),
            "working_directory": str(self.execution_workspace_root),
        }

    def archive(self, *, scope: ProjectWorkspaceScope, workspace_id: str) -> dict[str, Any]:
        self._prepare_scope(scope)
        record = self._owned_record(scope=scope, workspace_id=workspace_id, require_active=False)
        if str(record.get("state") or "") == "archived":
            return self._public_record(record, selected=False)
        updated = self.store.update_project_workspace_state(
            workspace_id=str(record["workspace_id"]),
            state="archived",
        )
        self.store.clear_project_workspace_selection(
            selection_key=scope.selection_key,
            workspace_id=str(record["workspace_id"]),
        )
        return self._public_record(updated or record, selected=False)

    def current(self, *, scope: ProjectWorkspaceScope) -> dict[str, Any] | None:
        self._prepare_scope(scope)
        workspace_id = self.store.get_project_workspace_selection(selection_key=scope.selection_key)
        if not workspace_id:
            return None
        try:
            record = self._owned_record(scope=scope, workspace_id=workspace_id, require_active=True)
            self._root_from_record(record, require_exists=True)
        except ProjectWorkspaceError:
            self.store.clear_project_workspace_selection(
                selection_key=scope.selection_key, workspace_id=workspace_id,
            )
            return None
        return self._public_record(record, selected=True)

    def output_directory(self, *, scope: ProjectWorkspaceScope, cwd: str | Path) -> Path:
        """Authorize an output directory without changing the selected project."""
        alias = str(cwd).strip()
        if alias == "alias:project" or alias.startswith("alias:project/"):
            _record, project_root = self._resolve_project(scope=scope, workspace_id="")
            suffix = alias[len("alias:project"):].lstrip("/")
            _relative, target = self._inspection_target(project_root, suffix or ".", allow_root=True)
            if not target.is_dir():
                raise ProjectWorkspaceError("invalid_output_cwd")
            return target
        raw = Path(cwd)
        candidate = (raw if raw.is_absolute() else self.execution_workspace_root / raw).absolute()
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ProjectWorkspaceError("invalid_output_cwd") from exc
        if candidate != resolved or not resolved.is_dir():
            raise ProjectWorkspaceError("invalid_output_cwd")
        if self._path_is_within(resolved, self.execution_workspace_root):
            return resolved
        self._prepare_scope(scope)
        for record in self.store.list_project_workspaces(
            owner_kind=scope.owner_kind, owner_id=scope.owner_id, actor_scope=scope.actor_scope,
            include_archived=False,
        ):
            try:
                root = self._root_from_record(record, require_exists=True)
            except ProjectWorkspaceError:
                continue
            if self._path_is_within(resolved, root):
                return resolved
        raise ProjectWorkspaceError("output_cwd_not_registered")

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

    def execution_cwd(
        self,
        *,
        scope: ProjectWorkspaceScope,
        alias_value: str,
        execution_provider: Any | None = None,
    ) -> str:
        raw = str(alias_value or "").strip()
        if raw != "alias:project" and not raw.startswith("alias:project/"):
            raise ProjectWorkspaceError("invalid_project_alias")
        selected = self.current(scope=scope)
        if selected is None:
            raise ProjectWorkspaceError("workspace_not_selected")
        record = self._owned_record(
            scope=scope,
            workspace_id=str(selected["workspace_id"]),
            require_active=True,
        )
        suffix = raw[len("alias:project") :].lstrip("/")
        root_kind = str(record.get("root_kind") or _ROOT_KIND_MANAGED)
        if root_kind == _ROOT_KIND_HOST_BOUND:
            root = self._root_from_record(record, require_exists=True)
            binder = getattr(execution_provider, "bind_authorized_mount", None)
            if not callable(binder):
                raise ProjectWorkspaceError("host_bound_execution_unavailable")
            mount_name = f"project_{str(record['workspace_id'])[5:29]}"
            try:
                binder(mount_name, root)
            except (OSError, ValueError) as exc:
                raise ProjectWorkspaceError("host_bound_execution_unavailable") from exc
            if suffix:
                suffix = self._relative_path(suffix).as_posix()
                target = self._safe_child(root, suffix)
                if not target.is_dir():
                    raise ProjectWorkspaceError("cwd_not_found")
                return f"alias:{mount_name}/{suffix}"
            return f"alias:{mount_name}"
        root_relpath = str(record["root_relpath"])
        if suffix:
            suffix = self._relative_path(suffix).as_posix()
            target = self._safe_child(self._root_from_record(record, require_exists=True), suffix)
            if not target.is_dir():
                raise ProjectWorkspaceError("cwd_not_found")
            return f"{root_relpath}/{suffix}"
        return root_relpath

    def write(
        self,
        *,
        scope: ProjectWorkspaceScope | None,
        path: str,
        content: str,
        workspace_id: str = "",
        operation_root: str | Path | None = None,
        expected_sha256: str = "",
        mode: str = "create_or_replace",
    ) -> dict[str, Any]:
        text = str(content)
        if len(text) > PROJECT_WRITE_MAX_CHARS:
            raise ProjectWorkspaceError(
                "content_too_large",
                max_chars=PROJECT_WRITE_MAX_CHARS,
                actual_chars=len(text),
                recommended_action="split_source_file_or_use_smaller_patch",
            )
        clean_mode = str(mode or "create_or_replace").strip()
        if clean_mode not in {"create", "replace", "create_or_replace"}:
            raise ProjectWorkspaceError("invalid_write_mode")
        record, root = self._resolve_operation_root(
            scope=scope,
            workspace_id=workspace_id,
            operation_root=operation_root,
        )
        relative = self._relative_path(path)
        target = self._safe_child(root, relative.as_posix())
        with self._lock:
            exists = target.exists()
            if exists and (target.is_symlink() or not target.is_file()):
                raise ProjectWorkspaceError("path_conflict")
            if clean_mode == "create" and exists:
                raise ProjectWorkspaceError("path_conflict")
            if clean_mode == "replace" and not exists:
                raise ProjectWorkspaceError("source_missing")
            current = target.read_bytes() if exists else b""
            self._check_expected_hash(current=current, expected_sha256=expected_sha256, exists=exists)
            data = text.encode("utf-8")
            self._atomic_write(target, data)
        return {
            "status": "succeeded",
            "workspace_id": str(record["workspace_id"]),
            "effective_cwd": str(root),
            "path": relative.as_posix(),
            "created": not exists,
            "replaced": exists,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    def patch(
        self,
        *,
        scope: ProjectWorkspaceScope | None,
        patch_text: str,
        workspace_id: str = "",
        operation_root: str | Path | None = None,
        expected_files: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        text = str(patch_text or "")
        if not text.strip():
            raise ProjectWorkspaceError("patch_required")
        if len(text) > PROJECT_PATCH_MAX_CHARS:
            raise ProjectWorkspaceError(
                "content_too_large",
                max_chars=PROJECT_PATCH_MAX_CHARS,
                actual_chars=len(text),
                recommended_action="split_patch_by_file",
            )
        record, root = self._resolve_operation_root(
            scope=scope,
            workspace_id=workspace_id,
            operation_root=operation_root,
        )
        file_patches = self._parse_model_patch(text)
        expected = {
            self._relative_path(key).as_posix(): str(value or "").lower()
            for key, value in dict(expected_files or {}).items()
        }
        prepared: list[dict[str, Any]] = []
        affected: dict[str, tuple[Path, bool, bytes]] = {}
        with self._lock:
            for file_patch in file_patches:
                operation = str(file_patch.get("operation") or "update")
                old_relative = self._relative_path(file_patch["old_path"]) if file_patch.get("old_path") else None
                relative = self._relative_path(file_patch["path"])
                source_relative = old_relative or relative
                source = self._safe_child(root, source_relative.as_posix())
                target = self._safe_child(root, relative.as_posix())
                source_exists = source.exists()
                target_exists = target.exists()
                if source_exists and (source.is_symlink() or not source.is_file()):
                    raise ProjectWorkspaceError("path_conflict", path=source_relative.as_posix())
                if operation == "create" and target_exists:
                    raise ProjectWorkspaceError("path_conflict", path=relative.as_posix())
                if operation in {"update", "delete", "rename"} and not source_exists:
                    raise ProjectWorkspaceError("source_missing", path=source_relative.as_posix())
                if operation == "rename" and target_exists:
                    raise ProjectWorkspaceError("path_conflict", path=relative.as_posix())
                original = source.read_bytes() if source_exists else b""
                self._check_expected_hash(
                    current=original,
                    expected_sha256=expected.get(source_relative.as_posix(), expected.get(relative.as_posix(), "")),
                    exists=source_exists,
                )
                try:
                    original_text = original.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ProjectWorkspaceError("source_not_utf8", path=source_relative.as_posix()) from exc
                if operation == "create":
                    updated = str(file_patch.get("content") or "").encode("utf-8")
                elif operation == "delete":
                    updated = b""
                else:
                    updated = self._apply_hunks(
                        original_text,
                        file_patch["hunks"],
                        path=source_relative.as_posix(),
                    ).encode("utf-8")
                for item_path, item_relative in ((source, source_relative), (target, relative)):
                    key = item_relative.as_posix()
                    if key not in affected:
                        affected[key] = (
                            item_path,
                            item_path.exists(),
                            item_path.read_bytes() if item_path.is_file() else b"",
                        )
                prepared.append(
                    {
                        "operation": operation,
                        "source": source,
                        "target": target,
                        "source_relative": source_relative,
                        "relative": relative,
                        "updated": updated,
                    }
                )
            mutated: list[str] = []
            try:
                for item in prepared:
                    if item["operation"] != "delete":
                        self._atomic_write(item["target"], item["updated"])
                        mutated.append(item["relative"].as_posix())
                    if item["operation"] in {"delete", "rename"}:
                        item["source"].unlink()
                        mutated.append(item["source_relative"].as_posix())
            except Exception as exc:
                rollback_failed: list[str] = []
                for key in reversed(tuple(dict.fromkeys(mutated))):
                    path, existed, original = affected[key]
                    try:
                        if existed:
                            self._atomic_write(path, original)
                        elif path.exists() or path.is_symlink():
                            path.unlink()
                            self._prune_empty_parents(path.parent, root=root)
                    except Exception:
                        rollback_failed.append(key)
                if rollback_failed:
                    original_reason = exc.reason if isinstance(exc, ProjectWorkspaceError) else "write_failed"
                    raise ProjectWorkspaceError(
                        "rollback_failed",
                        original_reason=original_reason,
                        failed_paths=rollback_failed,
                    ) from exc
                if isinstance(exc, ProjectWorkspaceError):
                    raise
                raise ProjectWorkspaceError("write_failed") from exc
        return {
            "status": "succeeded",
            "workspace_id": str(record["workspace_id"]),
            "effective_cwd": str(root),
            "files": [
                {
                    "path": item["relative"].as_posix(),
                    "operation": item["operation"],
                    "bytes": len(item["updated"]),
                    "sha256": "" if item["operation"] == "delete" else hashlib.sha256(item["updated"]).hexdigest(),
                    **({"source_path": item["source_relative"].as_posix()} if item["operation"] == "rename" else {}),
                }
                for item in prepared
            ],
        }

    def _resolve_project(
        self,
        *,
        scope: ProjectWorkspaceScope,
        workspace_id: str,
    ) -> tuple[dict[str, Any], Path]:
        self._prepare_scope(scope)
        resolved_id = str(workspace_id or "").strip()
        if not resolved_id:
            resolved_id = self.store.get_project_workspace_selection(selection_key=scope.selection_key)
        if not resolved_id:
            raise ProjectWorkspaceError("workspace_not_selected")
        record = self._owned_record(scope=scope, workspace_id=resolved_id, require_active=True)
        return record, self._root_from_record(record, require_exists=True)

    def _resolve_operation_root(
        self,
        *,
        scope: ProjectWorkspaceScope | None,
        workspace_id: str,
        operation_root: str | Path | None,
    ) -> tuple[dict[str, Any], Path]:
        """Choose one filesystem root without creating a second file authority.

        Registered projects retain their identity and selection semantics.  A
        caller that already resolved a cwd through the execution provider may
        instead supply that real directory; the same inspect/write/patch
        implementation and atomicity checks then operate relative to it.
        """

        if operation_root is None:
            if scope is None:
                raise ProjectWorkspaceError("workspace_owner_required")
            return self._resolve_project(scope=scope, workspace_id=workspace_id)
        if str(workspace_id or "").strip():
            raise ProjectWorkspaceError("workspace_and_cwd_conflict")
        try:
            root = Path(operation_root).expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            raise ProjectWorkspaceError("cwd_not_found") from None
        if not root.is_dir():
            raise ProjectWorkspaceError("cwd_not_found")
        return {"workspace_id": ""}, root

    def _prepare_scope(self, scope: ProjectWorkspaceScope) -> None:
        if scope.owner_kind != "qq_user":
            return
        # Early Project Workspace releases stored private QQ catalogs as
        # ``private/<profile>``. That identity is unambiguous and can be moved
        # safely. Group-wide legacy catalogs are deliberately not guessed:
        # their original creator was not stored by the broken runtime path.
        self.store.reassign_project_workspaces(
            from_owner_kind="private",
            from_owner_id=scope.owner_id,
            from_actor_scope="",
            to_owner_kind=scope.owner_kind,
            to_owner_id=scope.owner_id,
            to_actor_scope=scope.actor_scope,
        )

    def _owned_record(
        self,
        *,
        scope: ProjectWorkspaceScope,
        workspace_id: str,
        require_active: bool,
    ) -> dict[str, Any]:
        clean_id = str(workspace_id or "").strip()
        if not _PROJECT_ID_RE.fullmatch(clean_id):
            raise ProjectWorkspaceError("workspace_not_found")
        record = self.store.get_project_workspace(clean_id)
        if record is None:
            raise ProjectWorkspaceError("workspace_not_found")
        owned = (
            str(record.get("owner_kind") or "") == scope.owner_kind
            and str(record.get("owner_id") or "") == scope.owner_id
            and str(record.get("actor_scope") or "") == scope.actor_scope
        )
        shared_selected = bool(scope.shared_group) and (
            self.store.get_project_workspace_selection(selection_key=scope.selection_key) == clean_id
        )
        # This resolves project visibility only. Tool authorization still uses
        # the requesting actor at dispatch; sharing a cwd grants no permission.
        if not owned and not shared_selected:
            raise ProjectWorkspaceError("workspace_not_found")
        if require_active and str(record.get("state") or "") != "active":
            raise ProjectWorkspaceError("workspace_archived")
        return record

    def _root_from_record(self, record: Mapping[str, Any], *, require_exists: bool) -> Path:
        root_kind = str(record.get("root_kind") or _ROOT_KIND_MANAGED)
        if root_kind == _ROOT_KIND_HOST_BOUND:
            raw = str(record.get("host_root_path") or "").strip()
            source = Path(raw).expanduser()
            root = source.resolve(strict=False)
            source_absolute = source.absolute()
            if (
                not raw
                or not root.is_absolute()
                or source.is_symlink()
                or os.path.normcase(str(root)) != os.path.normcase(str(source_absolute))
            ):
                raise ProjectWorkspaceError("invalid_workspace_root")
        elif root_kind == _ROOT_KIND_MANAGED:
            root = self._root_from_relpath(str(record.get("root_relpath") or ""))
        else:
            raise ProjectWorkspaceError("invalid_workspace_root")
        if require_exists and (not root.is_dir() or root.is_symlink()):
            raise ProjectWorkspaceError("workspace_missing")
        return root

    def _validate_host_binding_root(self, value: str | Path) -> Path:
        raw = str(value or "").strip()
        if not raw:
            raise ProjectWorkspaceError("host_directory_required")
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            raise ProjectWorkspaceError("host_directory_must_be_absolute")
        try:
            root = candidate.resolve(strict=True)
        except OSError as exc:
            raise ProjectWorkspaceError("host_directory_missing") from exc
        if not root.is_dir() or root.is_symlink():
            raise ProjectWorkspaceError("host_directory_invalid")
        if self._path_is_within(root, self.execution_workspace_root):
            raise ProjectWorkspaceError("host_directory_managed_by_runtime")
        if any(self._path_is_within(root, protected) for protected in self.protected_roots):
            raise ProjectWorkspaceError("host_directory_protected")
        return root

    @staticmethod
    def _path_is_within(candidate: Path, root: Path) -> bool:
        try:
            candidate.relative_to(root)
            return True
        except ValueError:
            return False

    def _root_from_relpath(self, value: str) -> Path:
        relative = PurePosixPath(str(value or ""))
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ProjectWorkspaceError("invalid_workspace_root")
        candidate = (self.execution_workspace_root / Path(*relative.parts)).resolve(strict=False)
        try:
            candidate.relative_to(self.projects_root)
        except ValueError as exc:
            raise ProjectWorkspaceError("invalid_workspace_root") from exc
        return candidate

    def _safe_child(self, root: Path, relative: str) -> Path:
        candidate = (root / Path(*PurePosixPath(relative).parts)).resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ProjectWorkspaceError("path_outside_workspace") from exc
        current = root
        for part in PurePosixPath(relative).parts[:-1]:
            current = current / part
            if current.exists() and current.is_symlink():
                raise ProjectWorkspaceError("path_outside_workspace")
        return candidate

    def _inspection_target(self, root: Path, value: str, *, allow_root: bool) -> tuple[str, Path]:
        raw = str(value or ".").replace("\\", "/").strip() or "."
        relative = PurePosixPath(raw)
        if relative.is_absolute() or ".." in relative.parts or (relative.parts and ":" in relative.parts[0]):
            raise ProjectWorkspaceError("path_outside_workspace")
        if any(part == "" for part in relative.parts):
            raise ProjectWorkspaceError("invalid_relative_path")
        clean = relative.as_posix()
        if clean == ".":
            if not allow_root:
                raise ProjectWorkspaceError("file_path_required")
            return ".", root
        unresolved = root / Path(*relative.parts)
        if unresolved.is_symlink():
            raise ProjectWorkspaceError("path_outside_workspace")
        target = self._safe_child(root, clean)
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ProjectWorkspaceError("path_outside_workspace") from exc
        if not target.exists():
            raise ProjectWorkspaceError("source_missing", path=clean)
        return clean, target

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

    @staticmethod
    def _relative_path(value: str) -> PurePosixPath:
        raw = str(value or "").replace("\\", "/").strip()
        path = PurePosixPath(raw)
        if not raw or path.is_absolute() or ".." in path.parts or ":" in path.parts[0]:
            raise ProjectWorkspaceError("path_outside_workspace")
        if any(part in {"", "."} for part in path.parts):
            raise ProjectWorkspaceError("invalid_relative_path")
        return path

    def _ensure_root(self) -> None:
        if self.execution_workspace_root.exists() and not self.execution_workspace_root.is_dir():
            raise ProjectWorkspaceError("execution_workspace_missing")
        self.execution_workspace_root.mkdir(parents=True, exist_ok=True)
        if self.execution_workspace_root.is_symlink():
            raise ProjectWorkspaceError("execution_workspace_linked")
        self.projects_root.mkdir(parents=True, exist_ok=True)
        if self.projects_root.is_symlink():
            raise ProjectWorkspaceError("projects_root_linked")

    @staticmethod
    def _display_name(value: str) -> str:
        name = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or ""))
        name = re.sub(r"\s+", " ", name).strip()
        if not name:
            raise ProjectWorkspaceError("display_name_required")
        if len(name) > 80:
            raise ProjectWorkspaceError("display_name_too_long", max_chars=80, actual_chars=len(name))
        return name

    def _public_record(self, record: Mapping[str, Any], *, selected: bool) -> dict[str, Any]:
        root_kind = str(record.get("root_kind") or _ROOT_KIND_MANAGED)
        root: Path | None = None
        try:
            root = self._root_from_record(record, require_exists=True)
            available = root.is_dir()
        except ProjectWorkspaceError:
            available = False
        workspace_id = str(record.get("workspace_id") or "")
        is_current = bool(selected and available and str(record.get("state") or "") == "active")
        return {
            "workspace_id": workspace_id,
            "display_name": str(record.get("display_name") or ""),
            "owner_kind": str(record.get("owner_kind") or ""),
            "state": str(record.get("state") or ""),
            "root_kind": root_kind,
            "available": available,
            "selected": bool(selected and available),
            "alias": "alias:project" if is_current else "",
            "working_directory": str(root) if is_current and root is not None else "",
            "created_at": int(record.get("created_at") or 0),
            "updated_at": int(record.get("updated_at") or 0),
        }

    @staticmethod
    def _check_expected_hash(*, current: bytes, expected_sha256: str, exists: bool) -> None:
        expected = str(expected_sha256 or "").strip().lower()
        if not expected:
            return
        if not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise ProjectWorkspaceError("invalid_expected_sha256")
        actual = hashlib.sha256(current).hexdigest() if exists else ""
        if actual != expected:
            raise ProjectWorkspaceError("base_hash_mismatch", actual_sha256=actual)

    @staticmethod
    def _atomic_write(target: Path, data: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.parent.is_symlink():
            raise ProjectWorkspaceError("path_outside_workspace")
        temp = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
        try:
            with temp.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
        except Exception as exc:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            if isinstance(exc, ProjectWorkspaceError):
                raise
            raise ProjectWorkspaceError("write_failed") from exc

    @staticmethod
    def _prune_empty_parents(directory: Path, *, root: Path) -> None:
        current = directory
        while current != root:
            try:
                current.rmdir()
            except OSError:
                return
            current = current.parent

    @staticmethod
    def _parse_model_patch(text: str) -> list[dict[str, Any]]:
        """Parse the count-free patch language exposed to the model.

        The model states file operations and supplies old/new context, while
        the host derives locations and line counts.  This deliberately avoids
        traditional unified-diff range bookkeeping such as
        ``@@ -12,7 +12,8 @@``: those numbers are useful to ``patch(1)`` but
        are brittle, non-semantic work for a language model.
        """

        lines = text.splitlines(keepends=True)
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines or lines[0].rstrip("\r\n") != _PATCH_BEGIN or lines[-1].rstrip("\r\n") != _PATCH_END:
            raise ProjectWorkspaceError(
                "patch_format_invalid",
                expected="*** Begin Patch ... *** End Patch",
                recommended_action="use_the_count_free_workspace_patch_format",
            )

        patches: list[dict[str, Any]] = []
        index = 1
        end_index = len(lines) - 1
        while index < end_index:
            line = lines[index].rstrip("\r\n")
            if not line.strip():
                index += 1
                continue
            operation = ""
            source_path: str | None = None
            path = ""
            if line.startswith("*** Add File:"):
                operation = "create"
                path = line[len("*** Add File:") :].strip()
            elif line.startswith("*** Delete File:"):
                operation = "delete"
                source_path = line[len("*** Delete File:") :].strip()
                path = source_path
            elif line.startswith("*** Update File:"):
                operation = "update"
                source_path = line[len("*** Update File:") :].strip()
                path = source_path
            else:
                raise ProjectWorkspaceError(
                    "patch_file_header_expected",
                    line=index + 1,
                    found=line[:160],
                    expected="*** Add File, *** Delete File, or *** Update File",
                    recommended_action="start_each_file_change_with_a_patch_file_header",
                )
            if not path:
                raise ProjectWorkspaceError("patch_path_missing", line=index + 1)
            hunks: list[dict[str, Any]] = []
            content = ""
            index += 1

            if operation == "update" and index < end_index and lines[index].startswith("*** Move to:"):
                path = lines[index].rstrip("\r\n")[len("*** Move to:") :].strip()
                if not path:
                    raise ProjectWorkspaceError("patch_path_missing", line=index + 1)
                operation = "rename"
                index += 1

            if operation == "create":
                content_lines: list[str] = []
                while index < end_index and not lines[index].startswith(_PATCH_FILE_PREFIXES):
                    raw = lines[index]
                    if not raw.startswith("+"):
                        raise ProjectWorkspaceError(
                            "patch_add_line_prefix_missing",
                            path=path,
                            line=index + 1,
                            found=raw.rstrip("\r\n")[:160],
                            expected="prefix every new file line with +",
                            recommended_action="prefix_each_added_line_with_plus",
                        )
                    content_lines.append(raw[1:])
                    index += 1
                content = "".join(content_lines)
            elif operation == "delete":
                if index < end_index and not lines[index].startswith(_PATCH_FILE_PREFIXES):
                    raise ProjectWorkspaceError(
                        "patch_delete_has_body",
                        path=path,
                        line=index + 1,
                        recommended_action="delete_file_headers_do_not_need_file_contents",
                    )
            else:
                while index < end_index and not lines[index].startswith(_PATCH_FILE_PREFIXES):
                    header = lines[index].rstrip("\r\n")
                    if not header.startswith("@@"):
                        raise ProjectWorkspaceError(
                            "patch_hunk_header_expected",
                            path=source_path,
                            line=index + 1,
                            found=header[:160],
                            expected="@@ or @@ <unique anchor line>",
                            recommended_action="start_each_update_hunk_with_at_at",
                        )
                    if re.match(r"^@@\s+-\d", header):
                        raise ProjectWorkspaceError(
                            "patch_numeric_range_not_allowed",
                            path=source_path,
                            line=index + 1,
                            found=header[:160],
                            expected="@@ or @@ <unique anchor line>",
                            recommended_action="remove_the_old_and_new_line_ranges_from_the_hunk_header",
                        )
                    anchor = header[2:].strip()
                    hunk = {"anchor": anchor, "lines": []}
                    index += 1
                    while (
                        index < end_index
                        and not lines[index].startswith(_PATCH_FILE_PREFIXES)
                        and not lines[index].startswith("@@")
                    ):
                        raw = lines[index]
                        if not raw.startswith((" ", "+", "-")):
                            raise ProjectWorkspaceError(
                                "patch_line_prefix_invalid",
                                path=source_path,
                                line=index + 1,
                                found=raw.rstrip("\r\n")[:160],
                                expected="space for context, - for removed text, or + for added text",
                                recommended_action="prefix_every_hunk_line_with_space_minus_or_plus",
                            )
                        hunk["lines"].append(raw)
                        index += 1
                    if not hunk["lines"]:
                        raise ProjectWorkspaceError(
                            "patch_hunk_empty",
                            path=source_path,
                            line=index + 1,
                            recommended_action="include_context_and_changed_lines_after_the_hunk_header",
                        )
                    if not any(raw.startswith(("+", "-")) for raw in hunk["lines"]):
                        raise ProjectWorkspaceError(
                            "patch_hunk_has_no_changes",
                            path=source_path,
                            hunk=len(hunks) + 1,
                            recommended_action="include_at_least_one_added_or_removed_line",
                        )
                    hunks.append(hunk)
                if operation == "update" and not hunks:
                    raise ProjectWorkspaceError(
                        "patch_update_missing_hunk",
                        path=source_path,
                        recommended_action="add_an_at_at_hunk_or_use_a_move_for_a_content_preserving_rename",
                    )
            patches.append(
                {
                    "operation": operation,
                    "old_path": source_path,
                    "path": path,
                    "hunks": hunks,
                    **({"content": content} if operation == "create" else {}),
                }
            )
        if not patches:
            raise ProjectWorkspaceError(
                "patch_has_no_file_changes",
                recommended_action="add_at_least_one_file_operation_between_the_patch_markers",
            )
        paths = [item["path"] for item in patches]
        paths.extend(
            item["old_path"]
            for item in patches
            if item.get("old_path") and item.get("old_path") != item.get("path")
        )
        if len(paths) != len(set(paths)):
            raise ProjectWorkspaceError("duplicate_patch_target")
        return patches

    @staticmethod
    def _apply_hunks(original: str, hunks: list[dict[str, Any]], *, path: str) -> str:
        source = original.splitlines(keepends=True)
        newline = "\r\n" if "\r\n" in original else "\n"
        output: list[str] = []
        cursor = 0

        def matches_at(start: int, expected_lines: list[str]) -> bool:
            if start < cursor or start + len(expected_lines) > len(source):
                return False
            return all(
                source[start + offset].rstrip("\r\n") == expected
                for offset, expected in enumerate(expected_lines)
            )

        for hunk_index, hunk in enumerate(hunks, start=1):
            old_lines = [
                raw[1:].rstrip("\r\n")
                for raw in hunk["lines"]
                if raw[0] in {" ", "-"}
            ]
            anchor = str(hunk.get("anchor") or "")
            search_start = cursor
            if anchor:
                anchor_candidates = [
                    candidate
                    for candidate in range(cursor, len(source))
                    if source[candidate].rstrip("\r\n") == anchor
                ]
                if len(anchor_candidates) != 1:
                    raise ProjectWorkspaceError(
                        "hunk_not_applicable",
                        path=path,
                        hunk=hunk_index,
                        mismatch="anchor_not_found" if not anchor_candidates else "anchor_ambiguous",
                        anchor=anchor,
                        candidate_lines=[candidate + 1 for candidate in anchor_candidates[:8]],
                        recommended_action=(
                            "reread_the_target_region_and_use_an_existing_anchor"
                            if not anchor_candidates
                            else "use_a_more_specific_anchor_or_add_unique_unchanged_context"
                        ),
                    )
                search_start = anchor_candidates[0] + 1

            if old_lines:
                candidates = [
                    candidate
                    for candidate in range(search_start, len(source) - len(old_lines) + 1)
                    if matches_at(candidate, old_lines)
                ]
                if len(candidates) != 1:
                    raise ProjectWorkspaceError(
                        "hunk_not_applicable",
                        path=path,
                        hunk=hunk_index,
                        mismatch="context_not_found" if not candidates else "context_ambiguous",
                        candidate_lines=[candidate + 1 for candidate in candidates[:8]],
                        recommended_action=(
                            "reread_the_target_region_and_copy_the_current_text_exactly"
                            if not candidates
                            else "add_unchanged_context_or_an_at_at_anchor_to_make_the_hunk_unique"
                        ),
                    )
                start = candidates[0]
            else:
                start = search_start if anchor else len(source)
            if start < cursor or start > len(source):
                raise ProjectWorkspaceError(
                    "hunk_not_applicable",
                    path=path,
                    hunk=hunk_index,
                    mismatch="invalid_insertion_point",
                    recommended_action="reread_the_target_region_and_regenerate_the_hunk",
                )
            output.extend(source[cursor:start])
            cursor = start
            for raw in hunk["lines"]:
                marker = raw[0]
                body = raw[1:]
                body_without_eol = body.rstrip("\r\n")
                if marker in {" ", "-"}:
                    if marker == " ":
                        output.append(source[cursor])
                    cursor += 1
                elif marker == "+":
                    has_eol = body.endswith(("\n", "\r"))
                    output.append(body_without_eol + (newline if has_eol else ""))
        output.extend(source[cursor:])
        return "".join(output)


__all__ = [
    "PROJECT_INSPECT_MAX_ENTRIES",
    "PROJECT_INSPECT_MAX_FILES",
    "PROJECT_INSPECT_MAX_MATCHES",
    "PROJECT_INSPECT_MAX_SCAN_BYTES",
    "PROJECT_INSPECT_MAX_TEXT_BYTES",
    "PROJECT_INSPECT_SEARCH_TIMEOUT_SECONDS",
    "PROJECT_PATCH_MAX_CHARS",
    "PROJECT_WRITE_MAX_CHARS",
    "ProjectWorkspaceError",
    "ProjectWorkspaceScope",
    "ProjectWorkspaceService",
]
