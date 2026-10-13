"""Host-authorized working directories, mount aliases, and shared file-tool authority."""
from __future__ import annotations

import re
from pathlib import Path

from ..execution_specs import EXEC_CWD_MAX_CHARS


_ALIAS_PREFIX = "alias:"


_ALIAS_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


class ExecutionPathError(Exception):
    """Raised when a requested cwd cannot be resolved inside the workspace."""


class LocalPathsMixin:
    def bind_authorized_mount(self, name: str, path: str | Path) -> None:
        """Bind a host-authorized directory without exposing it to tool arguments."""

        clean_name = str(name or "").strip()
        if not _ALIAS_RE.fullmatch(clean_name):
            raise ValueError("invalid_execution_mount_alias")
        target = Path(path).expanduser().resolve(strict=True)
        if not target.is_dir() or target.is_symlink():
            raise ValueError("execution_mount_directory_missing")
        with self._lock:
            existing = self.mounts.get(clean_name)
            if existing is not None and existing != target:
                raise ValueError("execution_mount_alias_conflict")
            self.mounts[clean_name] = target

    def resolve_workdir(self, value: str = "") -> Path:
        """Resolve a model-visible cwd through the executor's real path authority.

        File tools use this same entry point so Shell and source editing agree on
        relative roots, authorized aliases, absolute host directories, and
        structured path failures.
        """

        return self._resolve_workdir(value)

    def _resolve_workdir(self, value: str) -> Path:
        raw = str(value or "").strip()
        if not raw:
            return self.workspace_root
        if len(raw) > EXEC_CWD_MAX_CHARS:
            raise ExecutionPathError("cwd_too_long")
        if raw.startswith(_ALIAS_PREFIX):
            alias_value = raw[len(_ALIAS_PREFIX) :].strip().replace("\\", "/")
            name, _, suffix = alias_value.partition("/")
            if not name or name not in self.mounts:
                raise ExecutionPathError("unknown_mount_alias")
            resolved = self.mounts[name]
            if not resolved.is_dir():
                raise ExecutionPathError("mount_directory_missing")
            if not suffix:
                return resolved
            suffix_path = Path(suffix)
            if suffix_path.is_absolute() or ".." in suffix_path.parts:
                raise ExecutionPathError("path_traversal_not_allowed")
            candidate = (resolved / suffix_path).resolve(strict=False)
            try:
                candidate.relative_to(resolved)
            except ValueError:
                raise ExecutionPathError("path_escapes_mount") from None
            if not candidate.is_dir():
                raise ExecutionPathError("cwd_not_found")
            return candidate
        candidate_path = Path(raw)
        if candidate_path.is_absolute():
            try:
                candidate = candidate_path.expanduser().resolve(strict=True)
            except (OSError, RuntimeError):
                raise ExecutionPathError("cwd_not_found") from None
            if not candidate.is_dir():
                raise ExecutionPathError("cwd_not_found")
            return candidate
        if ".." in candidate_path.parts:
            raise ExecutionPathError("path_traversal_not_allowed")
        candidate = (self.workspace_root / candidate_path).resolve(strict=False)
        try:
            candidate.relative_to(self.workspace_root)
        except ValueError:
            raise ExecutionPathError("path_escapes_workspace") from None
        if not candidate.is_dir():
            raise ExecutionPathError("cwd_not_found")
        return candidate
