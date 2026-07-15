"""Host-owned scoped storage root for installed plugins.

Each enabled plugin receives a logically scoped directory under:
    AKANE_DATA_ROOT/instances/<instance_id>/plugins/<plugin_id>/

The host owns path construction and validation and returns only the plugin's
resolved directory through this API.  This is namespace ownership for trusted
in-process plugins, not a filesystem sandbox; ordinary Python code can still
inspect parent paths or access other process-visible files.

Design constraints:
- Directory is created lazily on first call to get_plugin_data_dir().
- Path traversal is rejected by resolving against the expected plugins root.
- Invalid plugin IDs (non-safe IDs) raise ValueError before any filesystem op.
- OSError from mkdir propagates to the caller; the host maps it to a structured
  activation failure rather than silently returning a non-existent path.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol


_SAFE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def _is_safe_id(value: object) -> bool:
    return isinstance(value, str) and _SAFE_ID_PATTERN.fullmatch(value) is not None


class PluginStorageService(Protocol):
    """Resolve and create a safe, scoped data directory for one installed plugin."""

    def get_plugin_data_dir(self, plugin_id: str) -> Path:
        """Return the plugin's scoped data directory, creating it if needed.

        Raises ValueError for an invalid plugin_id.
        Raises OSError if directory creation fails.
        """
        ...


class InstancePluginStorageService:
    """Concrete storage service scoped to one Akane instance.

    Resolves each plugin's data directory under:
        <data_root>/instances/<instance_id>/plugins/<plugin_id>/

    The data_root is resolved to an absolute path at construction time.
    Each call to get_plugin_data_dir() resolves the candidate path, verifies
    it stays within the expected plugins subtree, then creates it if needed.
    """

    def __init__(self, data_root: Path, instance_id: str) -> None:
        if not isinstance(data_root, Path):
            raise TypeError("data_root_must_be_path")
        if not _is_safe_id(instance_id):
            raise ValueError(f"invalid_instance_id: {instance_id!r}")
        self._data_root = data_root.resolve()
        self._instance_id = instance_id

    @property
    def instance_id(self) -> str:
        return self._instance_id

    def get_plugin_data_dir(self, plugin_id: str) -> Path:
        """Return the scoped directory for plugin_id, creating it if needed."""
        if not _is_safe_id(plugin_id):
            raise ValueError(f"invalid_plugin_id: {plugin_id!r}")

        plugins_root = self._data_root / "instances" / self._instance_id / "plugins"
        candidate = plugins_root / plugin_id
        resolved = candidate.resolve()

        # Reject any path that escapes the plugins subtree.
        # We resolve plugins_root after constructing it, not at __init__,
        # because the directory may not exist yet.
        try:
            resolved.relative_to(plugins_root.resolve())
        except ValueError:
            raise ValueError(
                f"unsafe_plugin_storage_path for plugin_id={plugin_id!r}"
            ) from None

        resolved.mkdir(parents=True, exist_ok=True)
        return resolved


__all__ = ["InstancePluginStorageService", "PluginStorageService"]
