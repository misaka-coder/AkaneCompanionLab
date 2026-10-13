"""Shared artifact inventory, validated locators and atomic catalog I/O."""

from __future__ import annotations


import hashlib
import json
import os
import uuid
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Mapping
from ..distribution_artifacts import audit_distribution_artifact
from ..plugin_api import AKANE_PLUGIN_ENTRYPOINT_GROUP, is_valid_plugin_id
from .contracts import (
    PluginInstallationError,
    PluginGenerationSource,
    StagedPluginArtifact,
    PLUGIN_ARTIFACT_CATALOG_SCHEMA_VERSION,
    PLUGIN_STAGE_METADATA_FILENAME,
    _normalize_permissions,
)


class ArtifactCatalog:
    __slots__ = ()

    def snapshot(self) -> dict[str, Any]:
        """Return a path-free artifact inventory suitable for status/UI."""

        with self._lock:
            catalog = self._read_catalog_locked()
            plugins: list[dict[str, Any]] = []
            for plugin_id, pointer in sorted(catalog["plugins"].items()):
                if not isinstance(pointer, Mapping):
                    continue
                current = str(pointer.get("current") or "")
                artifact = catalog["artifacts"].get(current, {})
                if not isinstance(artifact, Mapping):
                    artifact = {}
                plugins.append(
                    {
                        "plugin_id": plugin_id,
                        "version": str(artifact.get("version") or ""),
                        "distribution_name": str(artifact.get("distribution_name") or ""),
                        "digest": current,
                        "permissions": list(artifact.get("permissions") or ()),
                        "contribution_snapshot": dict(artifact.get("contribution_snapshot") or {}),
                        "last_good_digest": str(pointer.get("last_good") or ""),
                        "pending_activation": bool(pointer.get("pending_activation")),
                    }
                )
            stages: list[dict[str, Any]] = []
            if self.staging_root.is_dir():
                for stage_dir in sorted(self.staging_root.iterdir(), key=lambda item: item.name):
                    try:
                        staged = self._read_stage_locked(stage_dir.name)
                    except PluginInstallationError as exc:
                        if (
                            stage_dir.is_dir()
                            and len(stage_dir.name) == 32
                            and all(char in "0123456789abcdef" for char in stage_dir.name.lower())
                        ):
                            stages.append(
                                {
                                    "ok": False,
                                    "status": "invalid",
                                    "reason": exc.reason,
                                    "stage_id": stage_dir.name,
                                }
                            )
                        continue
                    stages.append(staged.as_dict())
            return {
                "status": "ready",
                "schema_version": PLUGIN_ARTIFACT_CATALOG_SCHEMA_VERSION,
                "dependency_supply": self._dependency_supply_snapshot(),
                "plugin_count": len(plugins),
                "staged_count": len(stages),
                "plugins": plugins,
                "stages": stages,
            }

    def resolve_generation_source(self, plugin_id: str) -> PluginGenerationSource:
        """Resolve the exact selected artifact without importing it.

        A managed catalog pointer is authoritative when present.  Corrupt or
        missing managed content is never hidden by falling back to an older
        process-installed distribution with the same plugin id.
        """

        normalized = str(plugin_id or "").strip()
        if not is_valid_plugin_id(normalized):
            raise PluginInstallationError("invalid_plugin_id", status="invalid_request")
        with self._lock:
            catalog = self._read_catalog_locked()
            pointer = catalog["plugins"].get(normalized)
            if pointer is not None:
                if not isinstance(pointer, Mapping):
                    raise PluginInstallationError("plugin_catalog_invalid")
                digest = str(pointer.get("current") or "")
                artifact = catalog["artifacts"].get(digest)
                if (
                    not digest
                    or not isinstance(artifact, Mapping)
                    or str(artifact.get("plugin_id") or "") != normalized
                ):
                    raise PluginInstallationError("plugin_catalog_invalid")
                release_dir = self._artifact_release_dir(artifact)
                if release_dir.name != digest:
                    raise PluginInstallationError("plugin_catalog_invalid")
                site_dir = release_dir / "site"
                if not site_dir.is_dir():
                    raise PluginInstallationError("plugin_artifact_unavailable")
                if site_dir.resolve() != site_dir:
                    raise PluginInstallationError("plugin_artifact_invalid")
                if not _site_has_plugin_entry_point(site_dir, normalized):
                    raise PluginInstallationError("plugin_artifact_invalid")
                return PluginGenerationSource(
                    normalized, site_dir.resolve(), digest, _normalize_permissions(artifact.get("permissions"))
                )

        matches = tuple(
            item
            for item in _process_plugin_entry_points()
            if str(getattr(item, "name", "") or "").strip() == normalized
        )
        if not matches:
            raise PluginInstallationError("plugin_not_installed", status="not_found")
        if len(matches) != 1:
            raise PluginInstallationError("duplicate_plugin_entry_point")
        distribution = getattr(matches[0], "dist", None)
        locate_file = getattr(distribution, "locate_file", None)
        if not callable(locate_file):
            raise PluginInstallationError("plugin_distribution_invalid")
        try:
            site_dir = Path(locate_file("")).resolve()
        except (OSError, TypeError, ValueError):
            raise PluginInstallationError("plugin_distribution_invalid") from None
        if not site_dir.is_dir():
            raise PluginInstallationError("plugin_distribution_unavailable")
        from ..plugin_implementation_revision import distribution_revision

        try:
            revision = distribution_revision(matches[0])
        except (OSError, ValueError, TypeError):
            # Without verified source identity, keep conservative runtime binding.
            revision = ""
        return PluginGenerationSource(normalized, site_dir, revision)

    def _read_stage_locked(self, stage_id: str) -> StagedPluginArtifact:
        stage_dir = self._resolve_stage_dir(stage_id)
        metadata_path = stage_dir / PLUGIN_STAGE_METADATA_FILENAME
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PluginInstallationError("plugin_stage_invalid") from exc
        if not isinstance(payload, Mapping) or payload.get("status") != "staged":
            raise PluginInstallationError("plugin_stage_invalid")
        plugin_id = str(payload.get("plugin_id") or "")
        digest = str(payload.get("digest") or "")
        if (
            not is_valid_plugin_id(plugin_id)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest.lower())
        ):
            raise PluginInstallationError("plugin_stage_invalid")
        permissions = _normalize_permissions(payload.get("permissions"))
        contribution = payload.get("contribution_snapshot")
        if not isinstance(contribution, Mapping):
            raise PluginInstallationError("plugin_stage_invalid")
        return StagedPluginArtifact(
            stage_id=stage_dir.name,
            plugin_id=plugin_id,
            distribution_name=str(payload.get("distribution_name") or ""),
            version=str(payload.get("version") or ""),
            digest=digest,
            permissions=permissions,
            contribution_snapshot=dict(contribution),
        )

    def _resolve_stage_dir(self, stage_id: str) -> Path:
        normalized = str(stage_id or "").strip().lower()
        if len(normalized) != 32 or any(char not in "0123456789abcdef" for char in normalized):
            raise PluginInstallationError("plugin_stage_id_invalid", status="invalid_request")
        try:
            resolved = (self.staging_root / normalized).resolve()
            resolved.relative_to(self.staging_root)
            if resolved != self.staging_root / normalized:
                raise ValueError("plugin_stage_alias")
        except (OSError, RuntimeError, ValueError):
            raise PluginInstallationError("plugin_stage_id_invalid", status="invalid_request") from None
        return resolved

    def _artifact_release_dir(self, artifact: Mapping[str, Any]) -> Path:
        if not isinstance(artifact, Mapping):
            raise PluginInstallationError("plugin_catalog_invalid")
        relative = Path(str(artifact.get("relative_root") or ""))
        plugin_id = str(artifact.get("plugin_id") or "")
        if (
            relative.is_absolute()
            or len(relative.parts) != 3
            or relative.parts[:2] != ("releases", plugin_id)
            or not is_valid_plugin_id(plugin_id)
            or len(relative.name) != 64
            or any(char not in "0123456789abcdef" for char in relative.name.lower())
        ):
            raise PluginInstallationError("plugin_catalog_invalid")
        try:
            resolved = (self.root / relative).resolve()
            resolved.relative_to(self.releases_root)
            if resolved != self.releases_root / plugin_id / relative.name:
                raise ValueError("plugin_release_alias")
        except (OSError, RuntimeError, ValueError):
            raise PluginInstallationError("plugin_catalog_invalid") from None
        return resolved

    def _read_catalog_locked(self) -> dict[str, Any]:
        if not self.catalog_path.exists():
            return {
                "schema_version": PLUGIN_ARTIFACT_CATALOG_SCHEMA_VERSION,
                "instance_id": self.instance_id,
                "plugins": {},
                "artifacts": {},
            }
        try:
            payload = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PluginInstallationError("plugin_catalog_invalid") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("instance_id") != self.instance_id
            or not isinstance(payload.get("plugins"), dict)
            or not isinstance(payload.get("artifacts"), dict)
        ):
            raise PluginInstallationError("plugin_catalog_invalid")
        schema_version = payload.get("schema_version")
        if schema_version == 1:
            for pointer in payload["plugins"].values():
                if not isinstance(pointer, dict):
                    raise PluginInstallationError("plugin_catalog_invalid")
                pointer["pending_activation"] = bool(pointer.pop("pending_process_restart", False))
            payload["schema_version"] = PLUGIN_ARTIFACT_CATALOG_SCHEMA_VERSION
            self._write_catalog_locked(payload)
        elif schema_version != PLUGIN_ARTIFACT_CATALOG_SCHEMA_VERSION:
            raise PluginInstallationError("plugin_catalog_invalid")
        return payload

    def _write_catalog_locked(self, catalog: Mapping[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(self.catalog_path, catalog)


def _copy_and_hash(source: Path, target: Path) -> str:
    digest = hashlib.sha256()
    with source.open("rb") as reader, target.open("xb") as writer:
        while True:
            chunk = reader.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    return digest.hexdigest()


def _inspect_installed_site(site_dir: Path) -> tuple[str, str, str]:
    entries: list[tuple[Any, Any]] = []
    for distribution in importlib_metadata.distributions(path=[str(site_dir)]):
        for entry_point in distribution.entry_points:
            if entry_point.group == AKANE_PLUGIN_ENTRYPOINT_GROUP:
                entries.append((distribution, entry_point))
    if not entries:
        raise PluginInstallationError("plugin_entry_point_missing")
    if len(entries) != 1:
        raise PluginInstallationError("plugin_wheel_must_contain_one_plugin")
    distribution, entry_point = entries[0]
    plugin_id = str(entry_point.name or "").strip()
    if not is_valid_plugin_id(plugin_id):
        raise PluginInstallationError("invalid_plugin_id")
    audit = audit_distribution_artifact(distribution)
    if not audit.ok:
        raise PluginInstallationError(audit.reason)
    return plugin_id, audit.distribution_name, audit.version


def _process_plugin_entry_points() -> tuple[Any, ...]:
    discovered = importlib_metadata.entry_points()
    if hasattr(discovered, "select"):
        return tuple(discovered.select(group=AKANE_PLUGIN_ENTRYPOINT_GROUP))
    return tuple(discovered.get(AKANE_PLUGIN_ENTRYPOINT_GROUP, ()))


def _site_has_plugin_entry_point(site_dir: Path, plugin_id: str) -> bool:
    matches = 0
    for distribution in importlib_metadata.distributions(path=[str(site_dir)]):
        matches += sum(
            1
            for entry_point in distribution.entry_points
            if entry_point.group == AKANE_PLUGIN_ENTRYPOINT_GROUP and entry_point.name == plugin_id
        )
    return matches == 1


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
