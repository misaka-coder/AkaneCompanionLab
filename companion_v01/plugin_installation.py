"""Instance-owned staging and publication for trusted plugin wheels.

This module owns plugin *artifacts*, while the generation runtime remains the only
runtime contribution authority.  A wheel is installed into an isolated
staging directory, audited without importing it in the host process, and then
activated once in a short-lived probe process. After the caller confirms the
exact permission set, the management service publishes the artifact and
switches the isolated generation as one operation.

Python code is never reloaded in the host process. Managed artifacts enter only
through the isolated generation runtime; there is no second installer.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Iterable, Mapping

from .distribution_artifacts import audit_distribution_artifact
from .plugin_api import AKANE_PLUGIN_ENTRYPOINT_GROUP, is_valid_permission_id, is_valid_plugin_id
from .plugin_generation import PluginGenerationError, PluginGenerationProcess


PLUGIN_ARTIFACT_CATALOG_SCHEMA_VERSION = 2
PLUGIN_ARTIFACT_CATALOG_FILENAME = "plugin-artifacts.json"
PLUGIN_STAGE_METADATA_FILENAME = "stage.json"
PLUGIN_INSTALL_TIMEOUT_SECONDS = 600.0
PLUGIN_PROBE_TIMEOUT_SECONDS = 45.0
PLUGIN_SOURCE_BUILD_TIMEOUT_SECONDS = 600.0


class PluginInstallationError(RuntimeError):
    """Structured artifact failure without a local path or subprocess output."""

    def __init__(self, reason: str, *, status: str = "failed") -> None:
        self.status = str(status or "failed")
        self.reason = str(reason or "plugin_installation_failed")
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


class ManagedPluginArtifactStore:
    """One instance's authoritative staged/selected plugin artifact catalog."""

    def __init__(
        self,
        root: Path,
        *,
        instance_id: str,
        python_executable: str | None = None,
        project_root: Path | None = None,
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
        self.project_root = Path(project_root or Path(__file__).resolve().parents[1]).resolve()
        self._lock = threading.RLock()

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
                "plugin_count": len(plugins),
                "staged_count": len(stages),
                "plugins": plugins,
                "stages": stages,
            }

    def stage_wheel(self, wheel_path: Path) -> dict[str, Any]:
        """Install and fully probe one local wheel without publishing it."""

        source = Path(wheel_path)
        try:
            before = source.stat()
        except OSError as exc:
            raise PluginInstallationError("plugin_wheel_unavailable") from exc
        if not source.is_file() or source.suffix.lower() != ".whl":
            raise PluginInstallationError("plugin_wheel_required", status="invalid_request")
        if before.st_size <= 0:
            raise PluginInstallationError("plugin_wheel_empty", status="invalid_request")
        stage_id = uuid.uuid4().hex
        stage_dir = (self.staging_root / stage_id).resolve()
        stage_dir.relative_to(self.staging_root)
        site_dir = stage_dir / "site"
        # pip validates wheel compatibility from the standardized archive
        # filename. Preserve the basename inside the host-owned staging
        # directory; it is never projected in public status.
        staged_wheel = stage_dir / source.name
        with self._lock:
            self.staging_root.mkdir(parents=True, exist_ok=True)
            stage_dir.mkdir(parents=False, exist_ok=False)
        try:
            digest = _copy_and_hash(source, staged_wheel)
            after = source.stat()
            if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
                raise PluginInstallationError("plugin_wheel_changed_during_stage")
            self._install_wheel(staged_wheel, site_dir)
            plugin_id, distribution_name, version = _inspect_installed_site(site_dir)
            probe = self._probe(site_dir, plugin_id, stage_dir / "probe-data")
            permissions = _normalize_permissions(probe.get("permissions"))
            contribution = probe.get("contribution_snapshot")
            if not isinstance(contribution, Mapping):
                raise PluginInstallationError("plugin_probe_result_invalid")
            staged = StagedPluginArtifact(
                stage_id=stage_id,
                plugin_id=plugin_id,
                distribution_name=distribution_name,
                version=version,
                digest=digest,
                permissions=permissions,
                contribution_snapshot=dict(contribution),
            )
            _write_json_atomic(stage_dir / PLUGIN_STAGE_METADATA_FILENAME, staged.as_dict())
            return staged.as_dict()
        except Exception:
            shutil.rmtree(stage_dir, ignore_errors=True)
            raise

    def stage_source(self, source_path: Path) -> dict[str, Any]:
        """Build one local Python project into an immutable wheel, then stage it.

        This is the development-mode path.  It still enters the same wheel
        audit and activation probe as a downloaded artifact; the source tree is
        never added to ``sys.path`` and is not a second runtime authority.
        """

        source = Path(source_path)
        try:
            source = source.resolve(strict=True)
        except OSError as exc:
            raise PluginInstallationError("plugin_source_unavailable") from exc
        if not source.is_dir() or not (source / "pyproject.toml").is_file():
            raise PluginInstallationError(
                "plugin_source_project_required",
                status="invalid_request",
            )
        build_id = uuid.uuid4().hex
        build_root = (self.root / "source-builds" / build_id).resolve()
        build_root.relative_to(self.root)
        wheelhouse = build_root / "wheelhouse"
        wheelhouse.mkdir(parents=True, exist_ok=False)
        env = dict(os.environ)
        env.update(
            {
                "PIP_NO_INDEX": "1",
                "PIP_NO_INPUT": "1",
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            }
        )
        try:
            try:
                completed = subprocess.run(
                    [
                        self.python_executable,
                        "-m",
                        "pip",
                        "wheel",
                        "--no-deps",
                        "--no-index",
                        "--no-build-isolation",
                        "--disable-pip-version-check",
                        "--wheel-dir",
                        str(wheelhouse),
                        str(source),
                    ],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=PLUGIN_SOURCE_BUILD_TIMEOUT_SECONDS,
                    env=env,
                )
            except subprocess.TimeoutExpired as exc:
                raise PluginInstallationError("plugin_source_build_timeout") from exc
            except OSError as exc:
                raise PluginInstallationError("plugin_source_builder_unavailable") from exc
            if completed.returncode != 0:
                raise PluginInstallationError("plugin_source_build_failed")
            wheels = tuple(wheelhouse.glob("*.whl"))
            if len(wheels) != 1:
                raise PluginInstallationError("plugin_source_build_result_invalid")
            return self.stage_wheel(wheels[0])
        finally:
            shutil.rmtree(build_root, ignore_errors=True)

    def publish_stage(
        self,
        stage_id: str,
        *,
        approved_permissions: Iterable[str],
    ) -> dict[str, Any]:
        """Atomically select a staged release after exact permission approval."""

        with self._lock:
            staged = self._read_stage_locked(stage_id)
            approved = _normalize_permissions(tuple(approved_permissions))
            if approved != staged.permissions:
                return {
                    "ok": False,
                    "status": "approval_required",
                    "reason": "plugin_permissions_not_approved",
                    "stage_id": staged.stage_id,
                    "plugin_id": staged.plugin_id,
                    "required_permissions": list(staged.permissions),
                }
            stage_dir = (self.staging_root / staged.stage_id).resolve()
            stage_dir.relative_to(self.staging_root)
            release_parent = (self.releases_root / staged.plugin_id).resolve()
            release_parent.relative_to(self.releases_root)
            release_dir = (release_parent / staged.digest).resolve()
            release_dir.relative_to(release_parent)
            release_parent.mkdir(parents=True, exist_ok=True)
            if release_dir.exists():
                shutil.rmtree(stage_dir)
            else:
                os.replace(stage_dir, release_dir)

            catalog = self._read_catalog_locked()
            previous = catalog["plugins"].get(staged.plugin_id, {})
            previous_current = str(previous.get("current") or "") if isinstance(previous, Mapping) else ""
            activation_pending = previous_current != staged.digest or bool(
                isinstance(previous, Mapping) and previous.get("pending_activation")
            )
            catalog["artifacts"][staged.digest] = {
                "plugin_id": staged.plugin_id,
                "distribution_name": staged.distribution_name,
                "version": staged.version,
                "permissions": list(staged.permissions),
                "contribution_snapshot": dict(staged.contribution_snapshot),
                "relative_root": f"releases/{staged.plugin_id}/{staged.digest}",
                "published_at": int(time.time()),
            }
            catalog["plugins"][staged.plugin_id] = {
                "current": staged.digest,
                "last_good": (
                    str(previous.get("last_good") or previous_current)
                    if isinstance(previous, Mapping)
                    else ""
                ),
                "pending_activation": activation_pending,
            }
            self._write_catalog_locked(catalog)
            return {
                "ok": True,
                "status": "installed" if not previous_current else "updated",
                "plugin_id": staged.plugin_id,
                "version": staged.version,
                "digest": staged.digest,
                "permissions": list(staged.permissions),
                "activation_pending": activation_pending,
                "unchanged": previous_current == staged.digest,
            }

    def discard_stage(self, stage_id: str) -> dict[str, Any]:
        with self._lock:
            stage_dir = self._resolve_stage_dir(stage_id)
            if not stage_dir.exists():
                return {"ok": False, "status": "not_found", "reason": "plugin_stage_not_found"}
            shutil.rmtree(stage_dir)
            return {"ok": True, "status": "discarded", "stage_id": stage_id}

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
                site_dir = self._artifact_release_dir(artifact) / "site"
                if not site_dir.is_dir():
                    raise PluginInstallationError("plugin_artifact_unavailable")
                if not _site_has_plugin_entry_point(site_dir, normalized):
                    raise PluginInstallationError("plugin_artifact_invalid")
                return PluginGenerationSource(normalized, site_dir.resolve(), digest)

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
        return PluginGenerationSource(normalized, site_dir)

    def rollback_to_last_good(self, plugin_id: str) -> dict[str, Any]:
        normalized = str(plugin_id or "").strip()
        if not is_valid_plugin_id(normalized):
            raise PluginInstallationError("invalid_plugin_id", status="invalid_request")
        with self._lock:
            catalog = self._read_catalog_locked()
            pointer = catalog["plugins"].get(normalized)
            if not isinstance(pointer, dict):
                return {"ok": False, "status": "not_found", "reason": "plugin_not_installed"}
            current = str(pointer.get("current") or "")
            last_good = str(pointer.get("last_good") or "")
            if not last_good or last_good not in catalog["artifacts"]:
                return {"ok": False, "status": "unavailable", "reason": "plugin_last_good_unavailable"}
            if current == last_good:
                return {
                    "ok": True,
                    "status": "unchanged",
                    "plugin_id": normalized,
                    "activation_pending": False,
                }
            pointer["current"] = last_good
            pointer["pending_activation"] = True
            self._write_catalog_locked(catalog)
            return {
                "ok": True,
                "status": "rollback_scheduled",
                "plugin_id": normalized,
                "activation_pending": True,
            }

    def remove_plugin(self, plugin_id: str) -> dict[str, Any]:
        """Withdraw one artifact pointer and clean only its managed releases."""

        normalized = str(plugin_id or "").strip()
        if not is_valid_plugin_id(normalized):
            raise PluginInstallationError("invalid_plugin_id", status="invalid_request")
        with self._lock:
            catalog = self._read_catalog_locked()
            pointer = catalog["plugins"].pop(normalized, None)
            if not isinstance(pointer, Mapping):
                return {"ok": False, "status": "not_found", "reason": "plugin_not_installed"}
            referenced = {
                str(item.get(key) or "")
                for item in catalog["plugins"].values()
                if isinstance(item, Mapping)
                for key in ("current", "last_good")
                if str(item.get(key) or "")
            }
            removable = [
                digest
                for digest, artifact in tuple(catalog["artifacts"].items())
                if digest not in referenced
                and isinstance(artifact, Mapping)
                and str(artifact.get("plugin_id") or "") == normalized
            ]
            for digest in removable:
                catalog["artifacts"].pop(digest, None)
            self._write_catalog_locked(catalog)
            cleanup_failed = False
            plugin_release_root = (self.releases_root / normalized).resolve()
            plugin_release_root.relative_to(self.releases_root)
            try:
                if plugin_release_root.exists():
                    shutil.rmtree(plugin_release_root)
            except OSError:
                cleanup_failed = True
            return {
                "ok": not cleanup_failed,
                "status": "removed" if not cleanup_failed else "removed_cleanup_pending",
                "reason": "" if not cleanup_failed else "plugin_artifact_cleanup_failed",
                "plugin_id": normalized,
            }

    def reconcile_runtime(self, plugin_statuses: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        """Record successful activation or schedule last-good rollback.

        This runs after one fresh process generation has attempted activation.
        Failed candidates never replace the active generation; the catalog is
        pointed back to last-good. Generation-aware hosts can settle that
        rollback without restarting the Bot process.
        """

        statuses = {
            str(item.get("plugin_id") or ""): item
            for item in plugin_statuses
            if isinstance(item, Mapping)
        }
        with self._lock:
            catalog = self._read_catalog_locked()
            changed = False
            rollbacks: list[str] = []
            failed: list[str] = []
            for plugin_id, pointer in catalog["plugins"].items():
                if not isinstance(pointer, dict) or not pointer.get("pending_activation"):
                    continue
                status = statuses.get(plugin_id)
                # A runtime snapshot can legitimately omit an artifact that
                # was not part of that generation.  Absence is not evidence of
                # a failed activation; only an explicit per-plugin result may
                # settle or roll back a pending artifact.
                if status is None:
                    continue
                state = str(status.get("status") or "")
                if state in {"active", "disabled"}:
                    pointer["last_good"] = str(pointer.get("current") or "")
                    pointer["pending_activation"] = False
                    changed = True
                    continue
                last_good = str(pointer.get("last_good") or "")
                current = str(pointer.get("current") or "")
                if last_good and last_good != current and last_good in catalog["artifacts"]:
                    pointer["current"] = last_good
                    pointer["pending_activation"] = True
                    rollbacks.append(plugin_id)
                    changed = True
                else:
                    failed.append(plugin_id)
            if changed:
                stale_releases = self._drop_unreferenced_artifacts_locked(catalog)
                self._write_catalog_locked(catalog)
                for release in stale_releases:
                    try:
                        if release.exists():
                            shutil.rmtree(release)
                    except OSError:
                        continue
            return {
                "status": (
                    "rollback_scheduled"
                    if rollbacks
                    else ("activation_failed" if failed else "ready")
                ),
                "rollback_plugin_ids": rollbacks,
                "failed_plugin_ids": failed,
                "reload_required": bool(rollbacks),
            }

    def _drop_unreferenced_artifacts_locked(self, catalog: dict[str, Any]) -> tuple[Path, ...]:
        referenced = {
            str(item.get(key) or "")
            for item in catalog.get("plugins", {}).values()
            if isinstance(item, Mapping)
            for key in ("current", "last_good")
            if str(item.get(key) or "")
        }
        artifacts = catalog.get("artifacts", {})
        if not isinstance(artifacts, dict):
            return ()
        releases: list[Path] = []
        for digest, artifact in tuple(artifacts.items()):
            if digest in referenced or not isinstance(artifact, Mapping):
                continue
            try:
                releases.append(self._artifact_release_dir(artifact))
                artifacts.pop(digest, None)
            except PluginInstallationError:
                continue
        return tuple(releases)

    def _install_wheel(self, wheel_path: Path, site_dir: Path) -> None:
        env = dict(os.environ)
        env.update(
            {
                "PIP_NO_INDEX": "1",
                "PIP_NO_INPUT": "1",
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            }
        )
        try:
            completed = subprocess.run(
                [
                    self.python_executable,
                    "-m",
                    "pip",
                    "install",
                    "--no-deps",
                    "--no-index",
                    "--disable-pip-version-check",
                    "--no-compile",
                    "--target",
                    str(site_dir),
                    str(wheel_path),
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=PLUGIN_INSTALL_TIMEOUT_SECONDS,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise PluginInstallationError("plugin_wheel_install_timeout") from exc
        except OSError as exc:
            raise PluginInstallationError("plugin_wheel_installer_unavailable") from exc
        if completed.returncode != 0:
            raise PluginInstallationError("plugin_wheel_install_failed")

    def _probe(self, site_dir: Path, plugin_id: str, work_dir: Path) -> dict[str, Any]:
        generation = PluginGenerationProcess(
            project_root=self.project_root,
            site_dir=site_dir,
            plugin_id=plugin_id,
            work_dir=work_dir,
            python_executable=self.python_executable,
            start_timeout_seconds=PLUGIN_PROBE_TIMEOUT_SECONDS,
        )
        try:
            return generation.start()
        except PluginGenerationError as exc:
            reason = exc.reason
            if reason == "plugin_generation_timeout":
                reason = "plugin_probe_timeout"
            elif reason in {
                "plugin_generation_unavailable",
                "plugin_generation_exited",
            }:
                reason = "plugin_probe_unavailable"
            elif reason.startswith("plugin_generation_"):
                reason = "plugin_probe_failed"
            raise PluginInstallationError(reason) from exc
        finally:
            generation.stop()
            shutil.rmtree(work_dir, ignore_errors=True)

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
        resolved = (self.staging_root / normalized).resolve()
        resolved.relative_to(self.staging_root)
        return resolved

    def _artifact_release_dir(self, artifact: Mapping[str, Any]) -> Path:
        relative = Path(str(artifact.get("relative_root") or ""))
        if relative.is_absolute():
            raise PluginInstallationError("plugin_catalog_invalid")
        resolved = (self.root / relative).resolve()
        try:
            resolved.relative_to(self.releases_root)
        except ValueError:
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


def _normalize_permissions(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, (tuple, list)):
        raise PluginInstallationError("plugin_permissions_invalid")
    normalized = tuple(sorted({str(item or "").strip() for item in raw}))
    if any(not is_valid_permission_id(item) for item in normalized):
        raise PluginInstallationError("plugin_permissions_invalid")
    return normalized


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
            if entry_point.group == AKANE_PLUGIN_ENTRYPOINT_GROUP
            and entry_point.name == plugin_id
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


__all__ = [
    "PLUGIN_ARTIFACT_CATALOG_FILENAME",
    "PLUGIN_ARTIFACT_CATALOG_SCHEMA_VERSION",
    "PLUGIN_INSTALL_TIMEOUT_SECONDS",
    "PLUGIN_PROBE_TIMEOUT_SECONDS",
    "PLUGIN_SOURCE_BUILD_TIMEOUT_SECONDS",
    "ManagedPluginArtifactStore",
    "PluginGenerationSource",
    "PluginInstallationError",
    "StagedPluginArtifact",
]
