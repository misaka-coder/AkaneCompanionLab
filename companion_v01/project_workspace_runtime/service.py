"""Project service composition and its shared store, roots, and operation lock."""
from __future__ import annotations

from pathlib import Path
import sys
import threading
from typing import Iterable

from ..store import MemoryStore
from .catalog import ProjectCatalogMixin
from .paths import ProjectPathsMixin
from .inspection import ProjectInspectionMixin
from .writing import ProjectWritingMixin
from .patching import ProjectPatchSyntaxMixin


class ProjectWorkspaceService(
    ProjectCatalogMixin,
    ProjectPathsMixin,
    ProjectInspectionMixin,
    ProjectWritingMixin,
    ProjectPatchSyntaxMixin,
):
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
