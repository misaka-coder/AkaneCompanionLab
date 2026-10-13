"""One shared instance catalog, lock and artifact-store composition."""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from ..plugin_api import is_valid_plugin_id
from .contracts import PLUGIN_ARTIFACT_CATALOG_FILENAME
from .storage import ArtifactCatalog
from .staging import ArtifactStaging
from .installation import ArtifactInstaller
from .publication import ArtifactPublication


class ManagedPluginArtifactStore(ArtifactCatalog, ArtifactStaging, ArtifactInstaller, ArtifactPublication):
    """One instance's authoritative staged/selected plugin artifact catalog."""

    def __init__(
        self,
        root: Path,
        *,
        instance_id: str,
        python_executable: str | None = None,
        project_root: Path | None = None,
        dependency_wheelhouse: Path | None = None,
        dependency_index_url: str | None = None,
    ) -> None:
        if not isinstance(root, Path):
            raise TypeError("plugin_artifact_root_must_be_path")
        normalized_instance_id = str(instance_id or "").strip()
        if not is_valid_plugin_id(normalized_instance_id):
            raise ValueError("invalid_instance_id")
        self.root = root.resolve()
        self.instance_id = normalized_instance_id
        self.staging_root = (self.root / "staging").resolve()
        self.releases_root = (self.root / "releases").resolve()
        self.catalog_path = (self.root / PLUGIN_ARTIFACT_CATALOG_FILENAME).resolve()
        for child in (self.staging_root, self.releases_root, self.catalog_path):
            child.relative_to(self.root)
        self.python_executable = str(python_executable or sys.executable)
        self.project_root = Path(project_root or Path(__file__).resolve().parents[2]).resolve()
        raw_wheelhouse = str(dependency_wheelhouse or "").strip()
        raw_index_url = str(dependency_index_url or "").strip()
        if raw_wheelhouse and raw_index_url:
            raise ValueError("plugin_dependency_sources_ambiguous")
        self.dependency_wheelhouse = Path(raw_wheelhouse).resolve() if raw_wheelhouse else None
        self.dependency_index_url = raw_index_url
        self._lock = threading.RLock()
