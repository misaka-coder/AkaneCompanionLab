"""Artifact approval, activation and restoration of persisted and live selections."""

from __future__ import annotations


import asyncio
from typing import Any, Iterable
from ..instance_profile import PluginSelection
from ..plugin_installation import PluginInstallationError
from .operations import _serialized_mutation
from .contracts import _failure, _plugin_status, _runtime_matches, _selection_enabled, _upsert_selection


class ManagementInstallation:
    __slots__ = ()

    @_serialized_mutation
    async def install_stage(
        self,
        *,
        stage_id: str,
        approved_permissions: Iterable[str],
    ) -> dict[str, Any]:
        """Publish one immutable stage and make it effective in one operation.

        Staging is the review boundary.  Once the exact permission set is
        approved, callers should not have to coordinate the artifact catalog,
        persisted selections and live generation themselves.
        """

        if self.artifact_store is None:
            return _failure("unavailable", "plugin_artifact_store_unavailable")
        desired_before = self.selection_store.load()
        live_before = self.plugin_runtime.selections
        try:
            published = dict(
                await asyncio.to_thread(
                    self.artifact_store.publish_stage,
                    stage_id,
                    approved_permissions=tuple(approved_permissions),
                )
            )
        except PluginInstallationError as exc:
            return _failure(exc.status, exc.reason, schema_errors=exc.schema_errors, diagnostics=exc.diagnostics)
        except Exception:
            return _failure("error", "plugin_install_failed")
        if not published.get("ok"):
            return published

        plugin_id = str(published.get("plugin_id") or "")
        existed = plugin_id in {item.plugin_id for item in desired_before}
        enabled = _selection_enabled(desired_before, plugin_id) if existed else True
        candidate = _upsert_selection(desired_before, plugin_id, enabled=enabled)
        candidate_status = await self._reconfigure(candidate)
        target = _plugin_status(candidate_status, plugin_id)
        expected_status = "active" if enabled else "disabled"
        if candidate_status.get("published") is False or target.get("status") not in (
            {"active", "waiting_dependency"} if enabled else {"disabled"}
        ):
            artifact_status = self.reconcile_runtime(candidate_status)
            rollback_ok = await self._restore_install_state(
                plugin_id=plugin_id,
                existed=existed,
                desired_before=desired_before,
                live_before=live_before,
                restore_runtime=candidate_status.get("published") is not False,
            )
            return {
                "ok": False,
                "status": "activation_failed" if rollback_ok else "rollback_failed",
                "reason": str(
                    (candidate_status.get("reason") or target.get("reason") or "plugin_activation_failed")
                    if rollback_ok
                    else "plugin_install_rollback_failed"
                ),
                "action": "install",
                "plugin_id": plugin_id,
                "candidate": target,
                "artifact_status": artifact_status,
            }

        try:
            self.selection_store.save(candidate)
        except Exception:
            rollback_ok = await self._restore_install_state(
                plugin_id=plugin_id,
                existed=existed,
                desired_before=desired_before,
                live_before=live_before,
            )
            return {
                "ok": False,
                "status": "persist_failed" if rollback_ok else "rollback_failed",
                "reason": ("plugin_selection_persist_failed" if rollback_ok else "plugin_install_rollback_failed"),
                "action": "install",
                "plugin_id": plugin_id,
            }

        artifact_status = self.reconcile_runtime(candidate_status)
        return {
            "ok": True,
            "status": target.get("status", expected_status),
            "action": "install",
            "plugin_id": plugin_id,
            "version": str(published.get("version") or ""),
            "digest": str(published.get("digest") or ""),
            "permissions": list(published.get("permissions") or ()),
            "installed_status": str(published.get("status") or "installed"),
            "runtime": target,
            "artifact_status": artifact_status,
            "unchanged": bool(published.get("unchanged")),
            **(
                {"stage_cleanup_pending": True, "cleanup_reason": str(published.get("cleanup_reason") or "")}
                if published.get("stage_cleanup_pending")
                else {}
            ),
        }

    async def _restore_install_state(
        self,
        *,
        plugin_id: str,
        existed: bool,
        desired_before: tuple[PluginSelection, ...],
        live_before: tuple[PluginSelection, ...],
        restore_runtime: bool = True,
    ) -> bool:
        try:
            if existed:
                rollback = await asyncio.to_thread(
                    self.artifact_store.rollback_to_last_good,
                    plugin_id,
                )
                if not rollback.get("ok") and rollback.get("status") != "unchanged":
                    return False
            else:
                removed = await asyncio.to_thread(
                    self.artifact_store.remove_plugin, plugin_id, **self._artifact_retention()
                )
                if not removed.get("ok"):
                    return False
            self.selection_store.save(desired_before)
            if restore_runtime:
                rollback_ok, restored = await self._restore_runtime(live_before)
                if not rollback_ok:
                    return False
            else:
                # Rejected candidates leave the published generation running.
                # Starting it again can fail on the same changed dependency.
                restored = dict(self.plugin_runtime.status_snapshot())
                if not _runtime_matches(restored, live_before):
                    return False
            self.reconcile_runtime(restored)
            return True
        except Exception:
            return False
