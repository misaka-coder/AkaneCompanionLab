"""Workspace file service; stable entry point for tools and engine services."""

from __future__ import annotations

import importlib.util  # Preserve the optional-reader availability patch point.
from pathlib import Path

from .store import MemoryStore
from .workspace_files_runtime.reading import WorkspaceReadingMixin
from .workspace_files_runtime.documents import WorkspaceDocumentReadersMixin
from .workspace_files_runtime.file_paging import WorkspaceFilePagingMixin
from .workspace_files_runtime.page_source import WorkspacePageSourceMixin
from .workspace_files_runtime.read_cache import WorkspaceReadCache
from .workspace_files_runtime.focus import WorkspaceFocusMixin
from .workspace_files_runtime.context import WorkspaceContextMixin
from .workspace_files_runtime.definitions import (
    ARCHIVE_EXTENSIONS,
    DOCUMENT_EXTENSIONS,
    IMAGE_EXTENSIONS,
    MEDIA_EXTENSIONS,
    TEXT_EXTENSIONS,
    WORKSPACE_LAYER_NAMES,
    WorkspacePathError,
    WorkspaceReaderUnavailable,
    WorkspaceResolvedFile,
)
from .workspace_files_runtime.paths import WorkspacePathsMixin
from .workspace_files_runtime.traversal import WorkspaceTraversalMixin
from .workspace_files_runtime.listing import WorkspaceListingMixin
from .workspace_files_runtime.listing_paging import WorkspaceListingPagingMixin
from .workspace_files_runtime.targets import WorkspaceTargetsMixin


def default_workspace_root() -> Path:
    desktop = Path.home() / "Desktop"
    parent = desktop if desktop.exists() else Path.home()
    return parent / "Akane Workspace"


class WorkspaceFileService(
    WorkspacePathsMixin,
    WorkspaceTraversalMixin,
    WorkspaceListingMixin,
    WorkspaceListingPagingMixin,
    WorkspaceTargetsMixin,
    WorkspaceReadingMixin,
    WorkspaceDocumentReadersMixin,
    WorkspaceFilePagingMixin,
    WorkspacePageSourceMixin,
    WorkspaceFocusMixin,
    WorkspaceContextMixin,
):
    def __init__(
        self,
        *,
        root_dir: str | Path | None,
        store: MemoryStore,
        max_read_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        configured_root = str(root_dir or "").strip()
        root = Path(configured_root).expanduser() if configured_root else default_workspace_root()
        self.root_dir = root.resolve()
        self.uses_default_root = not bool(configured_root)
        self.store = store
        self.max_read_bytes = max(1024, int(max_read_bytes or 0))
        self._read_cache = WorkspaceReadCache()
        self.ensure_layout()
