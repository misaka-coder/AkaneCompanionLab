"""Shared filesystem authority, safe paths, execution aliases, and output roots."""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .definitions import (
    ProjectWorkspaceError, ProjectWorkspaceScope, _ROOT_KIND_MANAGED, _ROOT_KIND_HOST_BOUND,
)


class ProjectPathsMixin:
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
    def _relative_path(value: str) -> PurePosixPath:
        raw = str(value or "").replace("\\", "/").strip()
        path = PurePosixPath(raw)
        if not raw or path.is_absolute() or ".." in path.parts:
            raise ProjectWorkspaceError("path_outside_workspace")
        if not path.parts:
            raise ProjectWorkspaceError("invalid_relative_path")
        if ":" in path.parts[0]:
            raise ProjectWorkspaceError("path_outside_workspace")
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
