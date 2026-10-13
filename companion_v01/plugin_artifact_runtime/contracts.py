"""Artifact contracts, limits, structured failures and permission validation."""

from __future__ import annotations


from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from ..plugin_api import is_valid_permission_id


PLUGIN_ARTIFACT_CATALOG_SCHEMA_VERSION = 2


PLUGIN_ARTIFACT_CATALOG_FILENAME = "plugin-artifacts.json"


PLUGIN_STAGE_METADATA_FILENAME = "stage.json"


PLUGIN_INSTALL_TIMEOUT_SECONDS = 600.0


PLUGIN_PROBE_TIMEOUT_SECONDS = 45.0


PLUGIN_SOURCE_BUILD_TIMEOUT_SECONDS = 600.0


PLUGIN_SOURCE_TEST_TIMEOUT_SECONDS = 300.0


PLUGIN_SOURCE_TEST_OUTPUT_BYTES = 24 * 1024


PLUGIN_PIP_DIAGNOSTIC_BYTES = 12 * 1024


class PluginInstallationError(RuntimeError):
    """Structured artifact failure without a local path or subprocess output."""

    def __init__(self, reason: str, *, status: str = "failed", schema_errors=(), diagnostics=()) -> None:
        self.status = str(status or "failed")
        self.reason = str(reason or "plugin_installation_failed")
        self.schema_errors = tuple(dict(error) for error in schema_errors)
        self.diagnostics = tuple(dict(item) for item in diagnostics)
        super().__init__(self.reason)


@dataclass(frozen=True, slots=True)
class StagedPluginArtifact:
    stage_id: str
    plugin_id: str
    distribution_name: str
    version: str
    digest: str
    permissions: tuple[str, ...]
    contribution_snapshot: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "status": "staged",
            "stage_id": self.stage_id,
            "plugin_id": self.plugin_id,
            "distribution_name": self.distribution_name,
            "version": self.version,
            "digest": self.digest,
            "permissions": list(self.permissions),
            "contribution_snapshot": dict(self.contribution_snapshot),
        }


@dataclass(frozen=True, slots=True)
class PluginGenerationSource:
    """Trusted internal locator for one selected plugin release.

    The physical site directory is consumed only by the process launcher.  It
    is intentionally absent from artifact snapshots and management responses.
    """

    plugin_id: str
    site_dir: Path
    digest: str = ""
    approved_permissions: tuple[str, ...] | None = None


def _normalize_permissions(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, (tuple, list)):
        raise PluginInstallationError("plugin_permissions_invalid")
    normalized = tuple(sorted({str(item or "").strip() for item in raw}))
    if any(not is_valid_permission_id(item) for item in normalized):
        raise PluginInstallationError("plugin_permissions_invalid")
    return normalized
