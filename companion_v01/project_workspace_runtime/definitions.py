"""Project scopes, errors, inspection limits, and patch grammar defaults."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any

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
