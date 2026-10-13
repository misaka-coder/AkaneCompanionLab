"""Permission-approved publication, last-good rollback and retained release cleanup."""

from __future__ import annotations


import os
import shutil
import time
from pathlib import Path
from typing import Any, Iterable, Mapping
from ..plugin_api import is_valid_plugin_id
from .contracts import PluginInstallationError, _normalize_permissions


class ArtifactPublication:
    __slots__ = ()

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
            # Read before consuming the reviewable stage. A corrupt catalog
            # must leave the previous release and the candidate untouched.
            catalog = self._read_catalog_locked()
            stage_dir = self._resolve_stage_dir(staged.stage_id)
            release_dir = self._artifact_release_dir(
                {
                    "plugin_id": staged.plugin_id,
                    "relative_root": f"releases/{staged.plugin_id}/{staged.digest}",
                }
            )
            release_parent = release_dir.parent
            release_parent.mkdir(parents=True, exist_ok=True)
            reused = release_dir.exists()
            if reused and not (release_dir / "site").is_dir():
                raise PluginInstallationError("plugin_artifact_unavailable")
            if reused and (release_dir / "site").resolve() != release_dir / "site":
                raise PluginInstallationError("plugin_artifact_invalid")
            if not reused:
                os.replace(stage_dir, release_dir)

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
                    str(previous.get("last_good") or previous_current) if isinstance(previous, Mapping) else ""
                ),
                "pending_activation": activation_pending,
            }
            try:
                self._write_catalog_locked(catalog)
            except Exception as exc:
                if not reused:
                    try:
                        os.replace(release_dir, stage_dir)
                    except OSError:
                        # Keep the release's stage.json as recovery evidence.
                        # Never claim that an uncommitted release is installed.
                        raise PluginInstallationError("plugin_publication_restore_failed") from exc
                raise PluginInstallationError("plugin_catalog_write_failed") from exc
            cleanup_pending = False
            if reused:
                try:
                    shutil.rmtree(stage_dir)
                except OSError:
                    cleanup_pending = True
            return {
                "ok": True,
                "status": "installed" if not previous_current else "updated",
                "plugin_id": staged.plugin_id,
                "version": staged.version,
                "digest": staged.digest,
                "permissions": list(staged.permissions),
                "activation_pending": activation_pending,
                "unchanged": previous_current == staged.digest,
                **(
                    {"stage_cleanup_pending": True, "cleanup_reason": "plugin_stage_cleanup_failed"}
                    if cleanup_pending
                    else {}
                ),
            }

    def discard_stage(self, stage_id: str) -> dict[str, Any]:
        with self._lock:
            stage_dir = self._resolve_stage_dir(stage_id)
            if not stage_dir.exists():
                return {"ok": False, "status": "not_found", "reason": "plugin_stage_not_found"}
            shutil.rmtree(stage_dir)
            return {"ok": True, "status": "discarded", "stage_id": stage_id}

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
            if not self._last_good_release_available(catalog, normalized, last_good):
                return {"ok": False, "status": "unavailable", "reason": "plugin_last_good_unavailable"}
            if current == last_good:
                return {
                    "ok": True,
                    "status": "unchanged",
                    "plugin_id": normalized,
                    "activation_pending": bool(pointer.get("pending_activation")),
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
            owned = any(
                isinstance(item, Mapping) and item.get("plugin_id") == normalized
                for item in catalog["artifacts"].values()
            )
            if pointer is None and not owned:
                return {"ok": False, "status": "not_found", "reason": "plugin_not_installed"}
            # Withdraw the pointer durably before touching release contents.
            # Failed/held cleanup retains its original artifact record.
            if pointer is not None:
                self._write_catalog_locked(catalog)
            cleanup = self._cleanup_catalog_artifacts_locked(
                catalog, retained_site_dirs=retained_site_dirs, plugin_id=normalized
            )
            cleanup_failed = cleanup.get("cleanup_status") == "failed"
            return {
                "ok": not cleanup_failed,
                "status": "removed_cleanup_pending" if cleanup else "removed",
                "reason": "plugin_artifact_cleanup_failed"
                if cleanup_failed
                else "plugin_workers_draining"
                if cleanup
                else "",
                "plugin_id": normalized,
            }

    def reconcile_runtime(
        self, plugin_statuses: Iterable[Mapping[str, Any]], *, retained_site_dirs=()
    ) -> dict[str, Any]:
        """Record successful activation or schedule last-good rollback.

        This runs after one fresh process generation has attempted activation.
        Failed candidates never replace the active generation; the catalog is
        pointed back to last-good. Generation-aware hosts can settle that
        rollback without restarting the Bot process.
        """

        statuses = {str(item.get("plugin_id") or ""): item for item in plugin_statuses if isinstance(item, Mapping)}
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
                if last_good != current and self._last_good_release_available(catalog, plugin_id, last_good):
                    pointer["current"] = last_good
                    pointer["pending_activation"] = True
                    rollbacks.append(plugin_id)
                    changed = True
                else:
                    failed.append(plugin_id)
            if changed:
                self._write_catalog_locked(catalog)
            cleanup = self._cleanup_catalog_artifacts_locked(catalog, retained_site_dirs=retained_site_dirs)
            return {
                "status": (
                    "rollback_scheduled"
                    if rollbacks
                    else (
                        "activation_failed"
                        if failed
                        else "waiting_dependency"
                        if waiting
                        else "cleanup_pending"
                        if cleanup.get("cleanup_status") == "failed"
                        else "ready"
                    )
                ),
                "rollback_plugin_ids": rollbacks,
                "failed_plugin_ids": failed,
                **({"waiting_plugin_ids": waiting} if waiting else {}),
                "reload_required": bool(rollbacks),
                **cleanup,
            }

    def _drop_unreferenced_artifacts_locked(
        self, catalog: dict[str, Any], *, retained_site_dirs=()
    ) -> tuple[Path, ...]:
        referenced = _referenced_artifacts(catalog)
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
                if release.name != digest:
                    raise PluginInstallationError("plugin_catalog_invalid")
                if (release / "site").resolve() in retained:
                    continue
                if release.exists():
                    shutil.rmtree(release)
                releases.append(release)
                artifacts.pop(digest, None)
            except (OSError, RuntimeError, PluginInstallationError):
                continue
        return tuple(releases)

    def _last_good_release_available(self, catalog: Mapping[str, Any], plugin_id: str, digest: str) -> bool:
        artifact = catalog["artifacts"].get(digest)
        if not digest or not isinstance(artifact, Mapping) or artifact.get("plugin_id") != plugin_id:
            return False
        try:
            release = self._artifact_release_dir(artifact)
            site = release / "site"
            return release.name == digest and site.resolve() == site and site.is_dir()
        except (OSError, RuntimeError, PluginInstallationError):
            return False

    def _cleanup_catalog_artifacts_locked(
        self, catalog: dict[str, Any], *, retained_site_dirs: Iterable[Path | str] = (), plugin_id: str | None = None
    ) -> dict[str, Any]:
        """Delete exact unreferenced releases, then retire confirmed records.

        A failed ledger write retains the disk's original retry evidence. No
        selected release is deleted before its pointer change is committed.
        """
        retained = tuple(Path(site).resolve() for site in retained_site_dirs)
        original = {
            digest: item
            for digest, item in catalog["artifacts"].items()
            if plugin_id is None or isinstance(item, Mapping) and item.get("plugin_id") == plugin_id
        }
        work = {"plugins": catalog["plugins"], "artifacts": dict(original)}
        self._drop_unreferenced_artifacts_locked(work, retained_site_dirs=retained)
        removed = original.keys() - work["artifacts"].keys()
        if removed:
            for digest in removed:
                catalog["artifacts"].pop(digest)
            try:
                self._write_catalog_locked(catalog)
            except OSError:
                for digest in removed:
                    catalog["artifacts"][digest] = original[digest]
                    work["artifacts"][digest] = original[digest]
        referenced = _referenced_artifacts(catalog)
        pending, failed = set(), set()
        for digest, artifact in work["artifacts"].items():
            if digest in referenced:
                continue
            owner = str(artifact.get("plugin_id") or "") if isinstance(artifact, Mapping) else ""
            owner = owner if is_valid_plugin_id(owner) else "unknown"
            pending.add(owner)
            try:
                release = self._artifact_release_dir(artifact)
                if release.name != digest or (release / "site").resolve() not in retained:
                    failed.add(owner)
            except (OSError, RuntimeError, PluginInstallationError):
                failed.add(owner)
        if not pending:
            return {}
        return {
            "cleanup_status": "failed" if failed else "draining",
            "cleanup_pending_plugin_ids": sorted(pending),
            **(
                {"cleanup_failed_plugin_ids": sorted(failed), "cleanup_reason": "plugin_artifact_cleanup_failed"}
                if failed
                else {}
            ),
        }


def _referenced_artifacts(catalog: Mapping[str, Any]) -> set[str]:
    return {
        str(item.get(key) or "")
        for item in catalog.get("plugins", {}).values()
        if isinstance(item, Mapping)
        for key in ("current", "last_good")
        if str(item.get(key) or "")
    }
