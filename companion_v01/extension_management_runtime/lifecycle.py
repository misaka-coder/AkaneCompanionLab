"""Serialized restart, enablement, withdrawal and runtime artifact reconciliation."""

from __future__ import annotations


import asyncio
from typing import Any, Mapping
from ..instance_profile import PluginSelection
from ..plugin_api import is_valid_plugin_id
from ..plugin_installation import PluginInstallationError
from .operations import _serialized_mutation
from .contracts import _failure, _plugin_status, _runtime_matches, _selection_enabled


class ManagementLifecycle:
    __slots__ = ()

    @_serialized_mutation
    async def restart(self, *, requested_plugin_id: str = "") -> dict[str, Any]:
        plugin_id = str(requested_plugin_id or "").strip()
        desired = self.selection_store.load()
        if plugin_id and plugin_id not in {item.plugin_id for item in desired}:
            return _failure("not_found", "plugin_not_configured", plugin_id=plugin_id)
        result = await self._reconfigure(desired)
        artifact_status = self.reconcile_runtime(result)
        if plugin_id and result.get("published") is not False:
            target = _plugin_status(result, plugin_id)
            expected = "active" if _selection_enabled(desired, plugin_id) else "disabled"
            if target.get("status") not in ({"active", "waiting_dependency"} if expected == "active" else {expected}):
                return {
                    "ok": False,
                    "status": "reload_failed",
                    "reason": str(result.get("reason") or "plugin_reload_target_missing"),
                    "action": "restart",
                    "plugin_id": plugin_id,
                    "candidate": target,
                    "artifact_status": artifact_status,
                }
        result.update(
            {
                "action": "restart",
                "scope": "plugin_runtime",
                "requested_plugin_id": plugin_id,
                "artifact_status": artifact_status,
            }
        )
        return result

    @_serialized_mutation
    async def set_enabled(self, *, plugin_id: str, enabled: bool) -> dict[str, Any]:
        normalized_id = str(plugin_id or "").strip()
        if not is_valid_plugin_id(normalized_id):
            return _failure("invalid_request", "invalid_plugin_id", plugin_id=normalized_id)
        desired = self.selection_store.load()
        if normalized_id not in {item.plugin_id for item in desired}:
            return _failure("not_found", "plugin_not_configured", plugin_id=normalized_id)
        previous_live = self.plugin_runtime.selections
        previous_enabled = _selection_enabled(desired, normalized_id)
        live_enabled = _selection_enabled(previous_live, normalized_id, missing=None)
        selections_match = previous_enabled == bool(enabled) and live_enabled == bool(enabled)
        current_target = (
            _plugin_status(self.plugin_runtime.status_snapshot(), normalized_id) if selections_match else {}
        )
        if selections_match and current_target.get("status") in (
            {"active", "waiting_dependency"} if enabled else {"disabled"}
        ):
            payload = self.snapshot()
            payload["runtime_status"] = str(payload.get("status") or "unknown")
            payload.update(
                {
                    "ok": True,
                    "status": "waiting_dependency"
                    if current_target.get("status") == "waiting_dependency"
                    else "enabled"
                    if enabled
                    else "disabled",
                    "action": "enable" if enabled else "disable",
                    "plugin_id": normalized_id,
                    "unchanged": True,
                }
            )
            return payload

        candidate = tuple(
            PluginSelection(item.plugin_id, bool(enabled) if item.plugin_id == normalized_id else item.enabled)
            for item in desired
        )
        candidate_status = await self._reconfigure(candidate)
        target = _plugin_status(candidate_status, normalized_id)
        if candidate_status.get("published") is False or target.get("status") not in (
            {"active", "waiting_dependency"} if enabled else {"disabled"}
        ):
            rollback_status = "not_required"
            rollback_ok = True
            runtime_proof = candidate_status
            if candidate_status.get("published") is not False:
                rollback_ok, rollback = await self._restore_runtime(previous_live)
                rollback_status = str(rollback.get("status") or "unknown")
                if rollback_ok:
                    runtime_proof = rollback
            artifact_status = self.reconcile_runtime(runtime_proof)
            return {
                "ok": False,
                "status": "activation_failed" if rollback_ok else "rollback_failed",
                "reason": str(
                    (candidate_status.get("reason") or target.get("reason") or "plugin_state_change_failed")
                    if rollback_ok
                    else "plugin_state_rollback_failed"
                ),
                "action": "enable" if enabled else "disable",
                "plugin_id": normalized_id,
                "candidate": target,
                "rollback_status": rollback_status,
                "artifact_status": artifact_status,
            }
        try:
            self.selection_store.save(candidate)
        except Exception:
            rollback_ok, rollback = await self._restore_runtime(previous_live)
            return {
                "ok": False,
                "status": "persist_failed" if rollback_ok else "rollback_failed",
                "reason": "plugin_selection_persist_failed" if rollback_ok else "plugin_state_rollback_failed",
                "action": "enable" if enabled else "disable",
                "plugin_id": normalized_id,
                "rollback_status": str(rollback.get("status") or "unknown"),
                "artifact_status": self.reconcile_runtime(rollback),
            }
        artifact_status = self.reconcile_runtime(candidate_status)
        payload = self.snapshot()
        payload["artifact_status"] = artifact_status
        payload["runtime_status"] = str(payload.get("status") or "unknown")
        payload.update(
            {
                "ok": True,
                "status": "waiting_dependency"
                if target.get("status") == "waiting_dependency"
                else "enabled"
                if enabled
                else "disabled",
                "action": "enable" if enabled else "disable",
                "plugin_id": normalized_id,
                "unchanged": False,
            }
        )
        return payload

    @_serialized_mutation
    async def rollback(self, *, plugin_id: str) -> dict[str, Any]:
        if self.artifact_store is None:
            return _failure("unavailable", "plugin_artifact_store_unavailable", plugin_id=plugin_id)
        try:
            result = dict(await asyncio.to_thread(self.artifact_store.rollback_to_last_good, plugin_id))
            if result.get("ok"):
                result["reload_required"] = bool(result.pop("activation_pending", False))
                result["reload_scope"] = "plugin_generation"
            return result
        except PluginInstallationError as exc:
            return _failure(
                exc.status,
                exc.reason,
                plugin_id=plugin_id,
                schema_errors=exc.schema_errors,
                diagnostics=exc.diagnostics,
            )
        except Exception:
            return _failure("error", "plugin_rollback_failed", plugin_id=plugin_id)

    @_serialized_mutation
    async def uninstall(self, *, plugin_id: str) -> dict[str, Any]:
        normalized_id = str(plugin_id or "").strip()
        if not is_valid_plugin_id(normalized_id):
            return _failure("invalid_request", "invalid_plugin_id", plugin_id=normalized_id)
        if self.artifact_store is None:
            return _failure("unavailable", "plugin_artifact_store_unavailable", plugin_id=normalized_id)
        try:
            artifact_snapshot = self.artifact_store.snapshot()
        except PluginInstallationError as exc:
            return _failure(exc.status, exc.reason, plugin_id=normalized_id)
        except Exception:
            return _failure(
                "unavailable",
                "plugin_artifact_catalog_unavailable",
                plugin_id=normalized_id,
            )
        installed_ids = {
            str(item.get("plugin_id") or "")
            for item in artifact_snapshot.get("plugins", ())
            if isinstance(item, Mapping)
        }
        if normalized_id not in installed_ids:
            return _failure("not_found", "plugin_not_installed", plugin_id=normalized_id)
        current = self.plugin_runtime.selections
        if normalized_id in {item.plugin_id for item in current}:
            candidate = tuple(
                PluginSelection(item.plugin_id, False if item.plugin_id == normalized_id else item.enabled)
                for item in current
            )
            status = await self._reconfigure(candidate)
            target = _plugin_status(status, normalized_id)
            if status.get("published") is False or target.get("status") != "disabled":
                rollback_status = "not_required"
                rollback_ok = True
                if status.get("published") is not False:
                    rollback_ok, rollback = await self._restore_runtime(current)
                    rollback_status = str(rollback.get("status") or "unknown")
                return {
                    "ok": False,
                    "status": "deactivation_failed" if rollback_ok else "rollback_failed",
                    "reason": str(status.get("reason") or target.get("reason") or "plugin_disable_failed")
                    if rollback_ok
                    else "plugin_uninstall_rollback_failed",
                    "plugin_id": normalized_id,
                    "rollback_status": rollback_status,
                }
        persisted = self.selection_store.load()
        defaults = {item.plugin_id for item in self.selection_store.defaults}
        next_selections = tuple(
            PluginSelection(item.plugin_id, False)
            if item.plugin_id == normalized_id and normalized_id in defaults
            else item
            for item in persisted
            if item.plugin_id != normalized_id or normalized_id in defaults
        )
        try:
            self.selection_store.save(next_selections)
        except Exception:
            rollback_ok = True
            if current:
                rollback_ok, _rollback = await self._restore_runtime(current)
            return _failure(
                "persist_failed" if rollback_ok else "rollback_failed",
                "plugin_selection_persist_failed" if rollback_ok else "plugin_uninstall_rollback_failed",
                plugin_id=normalized_id,
            )
        try:
            result = dict(
                await asyncio.to_thread(self.artifact_store.remove_plugin, normalized_id, **self._artifact_retention())
            )
        except Exception as exc:
            try:
                self.selection_store.save(persisted)
            except Exception:
                return _failure(
                    "rollback_failed",
                    "plugin_uninstall_rollback_failed",
                    plugin_id=normalized_id,
                )
            rollback_ok, _rollback = await self._restore_runtime(current)
            if not rollback_ok:
                return _failure(
                    "rollback_failed",
                    "plugin_uninstall_rollback_failed",
                    plugin_id=normalized_id,
                )
            if isinstance(exc, PluginInstallationError):
                return _failure(
                    exc.status,
                    exc.reason,
                    plugin_id=normalized_id,
                    schema_errors=exc.schema_errors,
                    diagnostics=exc.diagnostics,
                )
            return _failure("error", "plugin_uninstall_failed", plugin_id=normalized_id)
        runtime_status = await self._reconfigure(next_selections)
        if not _runtime_matches(runtime_status, next_selections):
            result.update(
                {
                    "ok": False,
                    "status": "removed_runtime_degraded",
                    "reason": "plugin_runtime_reconfigure_failed",
                    "reload_required": True,
                    "reload_scope": "plugin_generation",
                }
            )
        else:
            result["reload_required"] = False
            result["reload_scope"] = "plugin_generation"
        return result

    async def _reconfigure(self, selections: tuple[PluginSelection, ...]) -> dict[str, Any]:
        try:
            return dict(await self.plugin_runtime.reconfigure(selections))
        except Exception:
            # An exceptional port gives no publication proof. The caller must
            # restore its previous state rather than assume the old set survived.
            return _failure("error", "plugin_runtime_reconfigure_failed")

    async def _restore_runtime(self, selections: tuple[PluginSelection, ...]) -> tuple[bool, dict[str, Any]]:
        restored = await self._reconfigure(selections)
        return _runtime_matches(restored, selections), restored

    def _artifact_retention(self):
        retained = getattr(self.plugin_runtime, "retained_site_dirs", None)
        return {"retained_site_dirs": retained()} if callable(retained) else {}

    def reconcile_runtime(self, plugin_status: Mapping[str, Any]) -> dict[str, Any]:
        if self.artifact_store is None:
            return {"status": "not_configured", "reload_required": False}
        try:
            result = dict(
                self.artifact_store.reconcile_runtime(plugin_status.get("plugins", ()), **self._artifact_retention())
            )
            if (
                result.get("reload_required")
                and plugin_status.get("published") is False
                and str(plugin_status.get("active_status") or "") == "active"
            ):
                # The failed candidate never replaced the old process set. The
                # catalog now points back to that last-good artifact, so the
                # still-active snapshot is already the correct runtime proof.
                settled = dict(
                    self.artifact_store.reconcile_runtime(
                        self.plugin_runtime.status_snapshot().get("plugins", ()), **self._artifact_retention()
                    )
                )
                result["active_generation_preserved"] = True
                result["reload_required"] = bool(settled.get("reload_required"))
                result["settled_status"] = str(settled.get("status") or "")
            return result
        except PluginInstallationError as exc:
            return {"status": exc.status, "reason": exc.reason, "reload_required": False}
        except Exception:
            return {"status": "error", "reason": "plugin_artifact_reconcile_failed", "reload_required": False}
