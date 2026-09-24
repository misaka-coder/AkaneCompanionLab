"""Instance-owned staging and publication for trusted plugin wheels.

This module owns plugin *artifacts*, while the generation runtime remains the only
runtime contribution authority. Source tests can run in a child process against
the current release SDK without changing installation state. A wheel is installed
into an isolated staging directory, audited without importing it in the host
process, and then prepared once in a short-lived probe process. Preparation
validates declarations without adapter health checks or supervised jobs. After the caller
confirms the exact permission set, the management service publishes the artifact
and switches the isolated generation as one operation.

Python code is never reloaded in the host process. Managed artifacts enter only
through the isolated generation runtime; there is no second installer.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass
from email.parser import Parser
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Iterable, Mapping

from .distribution_artifacts import audit_distribution_artifact
from .plugin_api import AKANE_PLUGIN_ENTRYPOINT_GROUP, is_valid_permission_id, is_valid_plugin_id
from .plugin_generation import PluginGenerationError, PluginGenerationProcess

try:
    from packaging.requirements import InvalidRequirement, Requirement
except ImportError:  # pragma: no cover - packaging is supplied by the host toolchain
    InvalidRequirement = ValueError
    Requirement = None


PLUGIN_ARTIFACT_CATALOG_SCHEMA_VERSION = 2
PLUGIN_ARTIFACT_CATALOG_FILENAME = "plugin-artifacts.json"
PLUGIN_STAGE_METADATA_FILENAME = "stage.json"
PLUGIN_INSTALL_TIMEOUT_SECONDS = 600.0
PLUGIN_PROBE_TIMEOUT_SECONDS = 45.0
PLUGIN_SOURCE_BUILD_TIMEOUT_SECONDS = 600.0
PLUGIN_SOURCE_TEST_TIMEOUT_SECONDS = 300.0
PLUGIN_SOURCE_TEST_OUTPUT_BYTES = 24 * 1024
PLUGIN_PIP_DIAGNOSTIC_BYTES = 12 * 1024
_SENSITIVE_ENV_NAME_RE = re.compile(r"KEY|PASSWORD|SECRET|TOKEN", re.IGNORECASE)
_HOST_PROVIDED_DISTRIBUTIONS = frozenset({"akane-plugin", "capcore"})
_PIP_MISSING_REQUIREMENT_RE = re.compile(
    r"(?:No matching distribution found for|Could not find a version that satisfies the requirement)\s+([^\s]+)",
    re.IGNORECASE,
)


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


class ManagedPluginArtifactStore:
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
        self.project_root = Path(project_root or Path(__file__).resolve().parents[1]).resolve()
        raw_wheelhouse = str(dependency_wheelhouse or "").strip()
        raw_index_url = str(dependency_index_url or "").strip()
        if raw_wheelhouse and raw_index_url:
            raise ValueError("plugin_dependency_sources_ambiguous")
        self.dependency_wheelhouse = Path(raw_wheelhouse).resolve() if raw_wheelhouse else None
        self.dependency_index_url = raw_index_url
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
                        "contribution_snapshot": dict(
                            artifact.get("contribution_snapshot") or {}
                        ),
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
        env = _scrubbed_plugin_subprocess_env()
        env.update(
            {
                "PIP_NO_INPUT": "1",
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            }
        )
        build_args = [
            self.python_executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--disable-pip-version-check",
            "--wheel-dir",
            str(wheelhouse),
        ]
        if self.dependency_wheelhouse is None and not self.dependency_index_url:
            build_args.extend(("--no-index", "--no-build-isolation"))
        else:
            build_args.extend(self._pip_dependency_source_args())
        try:
            try:
                completed = subprocess.run(
                    [*build_args, str(source)],
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=PLUGIN_SOURCE_BUILD_TIMEOUT_SECONDS,
                    env=env,
                )
            except subprocess.TimeoutExpired as exc:
                raise PluginInstallationError(
                    "plugin_source_build_timeout",
                    diagnostics=(_simple_diagnostic("build", "timeout"),),
                ) from exc
            except OSError as exc:
                raise PluginInstallationError(
                    "plugin_source_builder_unavailable",
                    diagnostics=(_simple_diagnostic("build", "installer_unavailable"),),
                ) from exc
            if completed.returncode != 0:
                raise _pip_failure(
                    "plugin_source_build_failed",
                    stage="build",
                    output=completed.stdout,
                )
            wheels = tuple(wheelhouse.glob("*.whl"))
            if len(wheels) != 1:
                raise PluginInstallationError("plugin_source_build_result_invalid")
            return self.stage_wheel(wheels[0])
        finally:
            shutil.rmtree(build_root, ignore_errors=True)

    def test_source(self, source_path: Path) -> dict[str, Any]:
        """Run source logic tests against this release's real public SDK.

        The project remains an ordinary source tree and is neither staged nor
        activated.  Tests run in a child process using the same interpreter
        and public contracts as the plugin probe, so projects never need to
        fake ``capcore`` or ``companion_v01.plugin_api`` in ``sys.modules``.
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
        tests_dir = source / "tests"
        if not tests_dir.is_dir():
            raise PluginInstallationError(
                "plugin_source_tests_required",
                status="invalid_request",
            )

        env = _scrubbed_plugin_subprocess_env()
        python_paths = [source / "src", source, self.project_root]
        packaged_dependencies = self.project_root / ".packages"
        if packaged_dependencies.is_dir():
            python_paths.append(packaged_dependencies)
        inherited_pythonpath = str(env.get("PYTHONPATH") or "").strip()
        if inherited_pythonpath:
            python_paths.extend(Path(item) for item in inherited_pythonpath.split(os.pathsep) if item)
        env["PYTHONPATH"] = os.pathsep.join(str(item) for item in python_paths)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        started_at = time.monotonic()
        try:
            with tempfile.TemporaryFile() as output:
                try:
                    completed = subprocess.run(
                        [
                            self.python_executable,
                            "-m",
                            "unittest",
                            "discover",
                            "-s",
                            "tests",
                            "-v",
                        ],
                        cwd=str(source),
                        env=env,
                        stdin=subprocess.DEVNULL,
                        stdout=output,
                        stderr=subprocess.STDOUT,
                        check=False,
                        timeout=PLUGIN_SOURCE_TEST_TIMEOUT_SECONDS,
                    )
                except subprocess.TimeoutExpired as exc:
                    raise PluginInstallationError("plugin_source_test_timeout", status="timeout") from exc
                except OSError as exc:
                    raise PluginInstallationError("plugin_source_test_runner_unavailable") from exc
                output.flush()
                output.seek(0, os.SEEK_END)
                output_size = output.tell()
                output_text, output_truncated = _read_test_output(output, output_size)
        except PluginInstallationError:
            raise
        except OSError as exc:
            raise PluginInstallationError("plugin_source_test_output_failed") from exc

        passed = completed.returncode == 0
        return {
            "ok": passed,
            "status": "passed" if passed else "failed",
            "reason": "" if passed else "plugin_source_tests_failed",
            "test_framework": "unittest",
            "exit_code": int(completed.returncode),
            "duration_ms": round((time.monotonic() - started_at) * 1000, 1),
            "output": output_text,
            "output_bytes": output_size,
            "output_truncated": output_truncated,
        }

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

    def remove_plugin(self, plugin_id: str, *, retained_site_dirs=()) -> dict[str, Any]:
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
            retained = {Path(site).resolve() for site in retained_site_dirs}
            releases, held = [], []
            for digest in removable:
                release = self._artifact_release_dir(catalog["artifacts"][digest])
                if (release / "site").resolve() in retained:
                    held.append(release)
                else:
                    releases.append(release)
                    catalog["artifacts"].pop(digest, None)
            self._write_catalog_locked(catalog)
            cleanup_failed = False
            plugin_release_root = (self.releases_root / normalized).resolve()
            plugin_release_root.relative_to(self.releases_root)
            try:
                if held:
                    for release in releases:
                        if release.exists():
                            shutil.rmtree(release)
                elif plugin_release_root.exists():
                    shutil.rmtree(plugin_release_root)
            except OSError:
                cleanup_failed = True
            return {
                "ok": not cleanup_failed,
                "status": "removed_cleanup_pending" if held or cleanup_failed else "removed",
                "reason": "plugin_artifact_cleanup_failed" if cleanup_failed else "plugin_workers_draining" if held else "",
                "plugin_id": normalized,
            }

    def reconcile_runtime(self, plugin_statuses: Iterable[Mapping[str, Any]], *, retained_site_dirs=()) -> dict[str, Any]:
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
            waiting: list[str] = []
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
                if state == "waiting_dependency":
                    waiting.append(plugin_id)
                    continue
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
            stale_releases = self._drop_unreferenced_artifacts_locked(catalog, retained_site_dirs=retained_site_dirs)
            if changed or stale_releases:
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
                    else ("activation_failed" if failed else "waiting_dependency" if waiting else "ready")
                ),
                "rollback_plugin_ids": rollbacks,
                "failed_plugin_ids": failed,
                **({"waiting_plugin_ids": waiting} if waiting else {}),
                "reload_required": bool(rollbacks),
            }

    def _drop_unreferenced_artifacts_locked(self, catalog: dict[str, Any], *, retained_site_dirs=()) -> tuple[Path, ...]:
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
        retained = {Path(site).resolve() for site in retained_site_dirs}
        for digest, artifact in tuple(artifacts.items()):
            if digest in referenced or not isinstance(artifact, Mapping):
                continue
            try:
                release = self._artifact_release_dir(artifact)
                if (release / "site").resolve() in retained:
                    continue
                releases.append(release)
                artifacts.pop(digest, None)
            except PluginInstallationError:
                continue
        return tuple(releases)

    def _dependency_supply_snapshot(self) -> dict[str, Any]:
        if self.dependency_wheelhouse is not None:
            return {
                "kind": "wheelhouse",
                "configured": self.dependency_wheelhouse.is_dir(),
                "status": "ready" if self.dependency_wheelhouse.is_dir() else "unavailable",
            }
        if self.dependency_index_url:
            return {"kind": "package_index", "configured": True, "status": "configured"}
        return {"kind": "host_only", "configured": False, "status": "unconfigured"}

    def _pip_dependency_source_args(self) -> list[str]:
        if self.dependency_wheelhouse is not None:
            if not self.dependency_wheelhouse.is_dir():
                raise PluginInstallationError(
                    "plugin_dependency_wheelhouse_unavailable",
                    status="dependency_error",
                    diagnostics=(
                        {
                            "stage": "dependency_preflight",
                            "code": "wheelhouse_unavailable",
                            "diagnostic_id": _diagnostic_id("wheelhouse_unavailable", str(self.dependency_wheelhouse)),
                        },
                    ),
                )
            return ["--no-index", "--find-links", str(self.dependency_wheelhouse)]
        if self.dependency_index_url:
            return ["--index-url", self.dependency_index_url]
        return []

    def _install_wheel(self, wheel_path: Path, site_dir: Path) -> None:
        # Let pip retain its canonical wheel error for non-archives.  Dependency
        # metadata diagnostics apply only after the artifact is structurally a
        # wheel and can be inspected safely.
        requirements = _wheel_runtime_requirements(wheel_path) if zipfile.is_zipfile(wheel_path) else ()
        external = [item for item in requirements if item["host_provided"] is False]
        if external and self.dependency_wheelhouse is None and not self.dependency_index_url:
            raise PluginInstallationError(
                "plugin_dependencies_unconfigured",
                status="dependency_error",
                diagnostics=tuple(
                    {
                        "stage": "dependency_preflight",
                        "code": "dependency_source_required",
                        "package": item["name"],
                        "specifier": item["specifier"],
                        "diagnostic_id": _diagnostic_id("dependency_source_required", item["name"], item["specifier"]),
                    }
                    for item in external
                ),
            )
        source_args = self._pip_dependency_source_args() if external else ["--no-index"]
        env = _scrubbed_plugin_subprocess_env()
        env.update(
            {
                "PIP_NO_INPUT": "1",
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            }
        )
        try:
            # Install the plugin payload itself without asking pip to resolve
            # the host SDK. Runtime dependencies are installed separately into
            # the same isolated target after the preflight above.
            completed = subprocess.run(
                [
                    self.python_executable,
                    "-m",
                    "pip",
                    "install",
                    "--no-deps",
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
            raise PluginInstallationError(
                "plugin_wheel_install_timeout",
                diagnostics=(_simple_diagnostic("plugin_install", "timeout"),),
            ) from exc
        except OSError as exc:
            raise PluginInstallationError(
                "plugin_wheel_installer_unavailable",
                diagnostics=(_simple_diagnostic("plugin_install", "installer_unavailable"),),
            ) from exc
        if completed.returncode != 0:
            raise _pip_failure(
                "plugin_wheel_install_failed",
                stage="plugin_install",
                output=completed.stdout,
            )
        if not external:
            return
        requirement_args = [item["raw"] for item in external]
        try:
            completed = subprocess.run(
                [
                    self.python_executable,
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "--no-compile",
                    "--target",
                    str(site_dir),
                    *source_args,
                    *requirement_args,
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=PLUGIN_INSTALL_TIMEOUT_SECONDS,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise PluginInstallationError(
                "plugin_dependency_install_timeout",
                status="dependency_error",
                diagnostics=(_simple_diagnostic("dependency_install", "timeout"),),
            ) from exc
        except OSError as exc:
            raise PluginInstallationError(
                "plugin_dependency_installer_unavailable",
                status="dependency_error",
                diagnostics=(_simple_diagnostic("dependency_install", "installer_unavailable"),),
            ) from exc
        if completed.returncode != 0:
            raise _pip_failure(
                "plugin_dependency_install_failed",
                stage="dependency_install",
                output=completed.stdout,
                status="dependency_error",
                requirements=external,
            )

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
            return generation.prepare()
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
            raise PluginInstallationError(reason, schema_errors=exc.schema_errors) from exc
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


def _wheel_runtime_requirements(wheel_path: Path) -> tuple[dict[str, Any], ...]:
    """Read applicable Requires-Dist entries without importing plugin code."""

    try:
        with zipfile.ZipFile(wheel_path) as archive:
            metadata_names = tuple(
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/METADATA") and name.count("/") == 1
            )
            if len(metadata_names) != 1:
                raise PluginInstallationError("plugin_dependency_metadata_invalid", status="dependency_error")
            metadata = Parser().parsestr(archive.read(metadata_names[0]).decode("utf-8"))
    except PluginInstallationError:
        raise
    except (OSError, UnicodeError, zipfile.BadZipFile, KeyError) as exc:
        raise PluginInstallationError("plugin_dependency_metadata_invalid", status="dependency_error") from exc
    raw_requirements = tuple(metadata.get_all("Requires-Dist") or ())
    result: list[dict[str, Any]] = []
    for raw in raw_requirements:
        text = str(raw or "").strip()
        if not text or Requirement is None:
            raise PluginInstallationError(
                "plugin_dependency_metadata_invalid",
                status="dependency_error",
                diagnostics=(
                    {
                        "stage": "dependency_preflight",
                        "code": "requirement_parser_unavailable" if Requirement is None else "requirement_empty",
                        "diagnostic_id": _diagnostic_id("requirement_parser_unavailable" if Requirement is None else "requirement_empty"),
                    },
                ),
            )
        try:
            requirement = Requirement(text)
        except (InvalidRequirement, TypeError, ValueError) as exc:
            raise PluginInstallationError(
                "plugin_dependency_metadata_invalid",
                status="dependency_error",
                diagnostics=(
                    {
                        "stage": "dependency_preflight",
                        "code": "requirement_invalid",
                        "requirement": text[:256],
                        "diagnostic_id": _diagnostic_id("requirement_invalid", text),
                    },
                ),
            ) from exc
        if requirement.url:
            raise PluginInstallationError(
                "plugin_dependency_requirement_unsupported",
                status="dependency_error",
                diagnostics=(
                    {
                        "stage": "dependency_preflight",
                        "code": "direct_url_requirement_not_allowed",
                        "package": requirement.name.lower().replace("_", "-"),
                        "diagnostic_id": _diagnostic_id("direct_url_requirement_not_allowed", text),
                    },
                ),
            )
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        normalized_name = requirement.name.lower().replace("_", "-")
        result.append(
            {
                "raw": text,
                "name": normalized_name,
                "specifier": str(requirement.specifier),
                "host_provided": normalized_name in _HOST_PROVIDED_DISTRIBUTIONS,
            }
        )
    return tuple(result)


def _diagnostic_id(*parts: object) -> str:
    payload = "|".join(str(item or "") for item in parts).encode("utf-8", errors="replace")
    return "plugin-diag-" + hashlib.sha256(payload).hexdigest()[:12]


def _simple_diagnostic(stage: str, code: str) -> dict[str, str]:
    return {
        "stage": str(stage),
        "code": str(code),
        "diagnostic_id": _diagnostic_id(stage, code),
    }


def _pip_failure(
    reason: str,
    *,
    stage: str,
    output: object,
    status: str = "failed",
    requirements: Iterable[Mapping[str, Any]] = (),
) -> PluginInstallationError:
    text = output.decode("utf-8", errors="replace") if isinstance(output, bytes) else str(output or "")
    text = text[-PLUGIN_PIP_DIAGNOSTIC_BYTES:]
    missing = tuple(_PIP_MISSING_REQUIREMENT_RE.findall(text))
    declared = tuple(
        {
            "name": str(item.get("name") or ""),
            "specifier": str(item.get("specifier") or ""),
        }
        for item in requirements
        if isinstance(item, Mapping)
    )
    diagnostics: list[dict[str, Any]] = [
        {
            "stage": stage,
            "code": "pip_command_failed",
            "diagnostic_id": _diagnostic_id(stage, reason, text),
            "declared_requirements": list(declared),
        }
    ]
    for item in dict.fromkeys(missing):
        diagnostics.append(
            {
                "stage": stage,
                "code": "requirement_unavailable",
                "package": item[:160],
                "diagnostic_id": _diagnostic_id(stage, "requirement_unavailable", item),
            }
        )
    return PluginInstallationError(reason, status=status, diagnostics=diagnostics)


def _read_test_output(handle: Any, output_size: int) -> tuple[str, bool]:
    """Return useful bounded test evidence, preserving both setup and failure tail."""

    size = max(0, int(output_size))
    if size <= PLUGIN_SOURCE_TEST_OUTPUT_BYTES:
        handle.seek(0)
        return handle.read().decode("utf-8", errors="replace"), False
    head_bytes = 4 * 1024
    tail_bytes = PLUGIN_SOURCE_TEST_OUTPUT_BYTES - head_bytes
    handle.seek(0)
    head = handle.read(head_bytes)
    handle.seek(max(0, size - tail_bytes))
    tail = handle.read(tail_bytes)
    marker = f"\n... test output omitted ({size - len(head) - len(tail)} bytes) ...\n".encode()
    return (head + marker + tail).decode("utf-8", errors="replace"), True


def _scrubbed_plugin_subprocess_env() -> dict[str, str]:
    """Keep normal toolchain discovery without exposing host credentials."""

    return {
        str(name): str(value)
        for name, value in os.environ.items()
        if not _SENSITIVE_ENV_NAME_RE.search(str(name))
        and not str(name).upper().startswith("AKANE_")
    }


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
    "PLUGIN_SOURCE_TEST_OUTPUT_BYTES",
    "PLUGIN_SOURCE_TEST_TIMEOUT_SECONDS",
    "ManagedPluginArtifactStore",
    "PluginGenerationSource",
    "PluginInstallationError",
    "StagedPluginArtifact",
]
