"""One authoritative lifecycle service for installed Akane extensions.

V1 manages trusted Python plugins already present on the Host.  The persisted
selection overlay is deliberately independent from marketplace distribution so
the later installer can reuse this lifecycle without becoming a second runtime.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

from capcore import CapabilityResult, InvocationContext

from .instance_profile import PluginSelection
from .plugin_api import is_valid_plugin_id
from .plugin_installation import ManagedPluginArtifactStore, PluginInstallationError


PLUGIN_SELECTION_STATE_SCHEMA_VERSION = 1
PLUGIN_SELECTION_STATE_FILENAME = "plugin-selections.json"


class PluginManagementRuntime(Protocol):
    """The live plugin surface consumed by management and diagnostics.

    ``PluginHost`` satisfies this contract today. Keeping the contract here
    lets a generation-backed runtime replace it without teaching HTTP routes or
    model tools about a second lifecycle authority.
    """

    @property
    def selections(self) -> tuple[PluginSelection, ...]: ...

    @property
    def runtime_loop(self) -> asyncio.AbstractEventLoop | None: ...

    def status_snapshot(self) -> dict[str, Any]: ...

    async def restart(self) -> dict[str, Any]: ...

    async def reconfigure(
        self,
        selections: tuple[PluginSelection, ...],
    ) -> dict[str, Any]: ...

    async def invoke(
        self,
        capability_id: str,
        args: Mapping[str, Any],
        *,
        context: InvocationContext,
    ) -> CapabilityResult: ...


class PluginSelectionStore:
    """Persist instance plugin enablement as a small atomic JSON overlay."""

    def __init__(
        self,
        path: Path,
        *,
        defaults: tuple[PluginSelection, ...],
        instance_id: str,
    ) -> None:
        self.path = Path(path)
        self.defaults = _normalize_selections(defaults)
        self.instance_id = str(instance_id or "").strip()
        self._lock = threading.RLock()
        self.load_reason = ""

    def load(self) -> tuple[PluginSelection, ...]:
        with self._lock:
            if not self.path.exists():
                self.load_reason = ""
                return self.defaults
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                overrides = self._parse_payload(payload)
            except Exception:
                self.load_reason = "plugin_selection_state_invalid"
                return self.defaults
            self.load_reason = ""
            return _merge_selection_overrides(self.defaults, overrides)

    def save(self, selections: tuple[PluginSelection, ...]) -> None:
        normalized = _normalize_selections(selections)
        default_by_id = {item.plugin_id: item.enabled for item in self.defaults}
        overrides = {
            item.plugin_id: item.enabled
            for item in normalized
            if default_by_id.get(item.plugin_id) is None
            or default_by_id[item.plugin_id] != item.enabled
        }
        payload = {
            "schema_version": PLUGIN_SELECTION_STATE_SCHEMA_VERSION,
            "instance_id": self.instance_id,
            "overrides": {key: overrides[key] for key in sorted(overrides)},
        }
        data = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
            try:
                with temp_path.open("xb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, self.path)
                self.load_reason = ""
            finally:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _parse_payload(self, payload: Any) -> dict[str, bool]:
        if not isinstance(payload, Mapping):
            raise ValueError("plugin_selection_state_root_invalid")
        if payload.get("schema_version") != PLUGIN_SELECTION_STATE_SCHEMA_VERSION:
            raise ValueError("plugin_selection_state_schema_unsupported")
        stored_instance_id = str(payload.get("instance_id") or "").strip()
        if stored_instance_id != self.instance_id:
            raise ValueError("plugin_selection_state_instance_mismatch")
        raw_overrides = payload.get("overrides")
        if not isinstance(raw_overrides, Mapping):
            raise ValueError("plugin_selection_state_overrides_invalid")
        overrides: dict[str, bool] = {}
        for raw_plugin_id, raw_enabled in raw_overrides.items():
            plugin_id = str(raw_plugin_id or "").strip()
            if not is_valid_plugin_id(plugin_id) or not isinstance(raw_enabled, bool):
                raise ValueError("plugin_selection_state_entry_invalid")
            overrides[plugin_id] = raw_enabled
        return overrides


class ExtensionManagementService:
    """Coordinate persisted selection and the one live plugin runtime."""

    def __init__(
        self,
        *,
        plugin_runtime: PluginManagementRuntime,
        selection_store: PluginSelectionStore,
        artifact_store: ManagedPluginArtifactStore | None = None,
        sync_timeout_seconds: float | None = None,
    ) -> None:
        self.plugin_runtime = plugin_runtime
        self.selection_store = selection_store
        self.artifact_store = artifact_store
        self.sync_timeout_seconds = (
            None if sync_timeout_seconds is None else max(1.0, float(sync_timeout_seconds))
        )
        self._operation_lock = asyncio.Lock()

    def snapshot(self) -> dict[str, Any]:
        payload = dict(self.plugin_runtime.status_snapshot())
        payload["kind"] = "plugin"
        artifact_snapshot: dict[str, Any] = {"status": "not_configured", "plugins": [], "stages": []}
        if self.artifact_store is not None:
            try:
                artifact_snapshot = self.artifact_store.snapshot()
            except PluginInstallationError as exc:
                artifact_snapshot = {"status": exc.status, "reason": exc.reason, "plugins": [], "stages": []}
        payload["artifacts"] = artifact_snapshot
        payload["management"] = {
            "status": "ready",
            "persistence": "instance_overlay",
            "load_reason": self.selection_store.load_reason,
            "supports": [
                "list",
                "enable",
                "disable",
                "restart",
                *(
                    [
                        "stage_wheel",
                        "stage_source",
                        "publish",
                        "discard_stage",
                        "rollback",
                        "uninstall",
                    ]
                    if self.artifact_store is not None
                    else []
                ),
            ],
            "code_reload": "process_restart_required",
        }
        return payload

    async def restart(self, *, requested_plugin_id: str = "") -> dict[str, Any]:
        plugin_id = str(requested_plugin_id or "").strip()
        if plugin_id and plugin_id not in {item.plugin_id for item in self.plugin_runtime.selections}:
            return _failure("not_found", "plugin_not_configured", plugin_id=plugin_id)
        if self.artifact_store is not None:
            try:
                pending_ids = self.artifact_store.pending_process_restart_plugin_ids()
            except PluginInstallationError as exc:
                return _failure(exc.status, exc.reason, plugin_id=plugin_id)
            except Exception:
                return _failure(
                    "unavailable",
                    "plugin_artifact_catalog_unavailable",
                    plugin_id=plugin_id,
                )
            if pending_ids:
                return {
                    "ok": False,
                    "status": "restart_required",
                    "reason": "bot_process_restart_required",
                    "plugin_id": plugin_id,
                    "pending_plugin_ids": list(pending_ids),
                }
        async with self._operation_lock:
            result = dict(await self.plugin_runtime.restart())
        result.update(
            {
                "action": "restart",
                "scope": "host",
                "requested_plugin_id": plugin_id,
            }
        )
        return result

    async def set_enabled(self, *, plugin_id: str, enabled: bool) -> dict[str, Any]:
        normalized_id = str(plugin_id or "").strip()
        if not is_valid_plugin_id(normalized_id):
            return _failure("invalid_request", "invalid_plugin_id", plugin_id=normalized_id)
        if enabled and self.artifact_store is not None:
            try:
                restart_pending = self.artifact_store.has_pending_process_restart(normalized_id)
            except PluginInstallationError as exc:
                return _failure(exc.status, exc.reason, plugin_id=normalized_id)
            except Exception:
                return _failure(
                    "unavailable",
                    "plugin_artifact_catalog_unavailable",
                    plugin_id=normalized_id,
                )
            if restart_pending:
                return _failure(
                    "restart_required",
                    "plugin_process_restart_required",
                    plugin_id=normalized_id,
                )
        async with self._operation_lock:
            previous = self.plugin_runtime.selections
            if normalized_id not in {item.plugin_id for item in previous}:
                return _failure("not_found", "plugin_not_configured", plugin_id=normalized_id)
            previous_enabled = next(item.enabled for item in previous if item.plugin_id == normalized_id)
            if previous_enabled == bool(enabled):
                payload = self.snapshot()
                payload["host_status"] = str(payload.get("status") or "unknown")
                payload.update(
                    {
                        "ok": True,
                        "status": "enabled" if enabled else "disabled",
                        "action": "enable" if enabled else "disable",
                        "plugin_id": normalized_id,
                        "unchanged": True,
                    }
                )
                return payload

            candidate = tuple(
                PluginSelection(item.plugin_id, bool(enabled) if item.plugin_id == normalized_id else item.enabled)
                for item in previous
            )
            candidate_status = dict(await self.plugin_runtime.reconfigure(candidate))
            target = _plugin_status(candidate_status, normalized_id)
            expected_status = "active" if enabled else "disabled"
            if target.get("status") != expected_status:
                rollback = dict(await self.plugin_runtime.reconfigure(previous))
                return {
                    "ok": False,
                    "status": "activation_failed",
                    "reason": str(target.get("reason") or "plugin_state_change_failed"),
                    "action": "enable" if enabled else "disable",
                    "plugin_id": normalized_id,
                    "candidate": target,
                    "rollback_status": str(rollback.get("status") or "unknown"),
                }
            try:
                self.selection_store.save(candidate)
            except Exception:
                rollback = dict(await self.plugin_runtime.reconfigure(previous))
                return {
                    "ok": False,
                    "status": "persist_failed",
                    "reason": "plugin_selection_persist_failed",
                    "action": "enable" if enabled else "disable",
                    "plugin_id": normalized_id,
                    "rollback_status": str(rollback.get("status") or "unknown"),
                }
            payload = self.snapshot()
            payload["host_status"] = str(payload.get("status") or "unknown")
            payload.update(
                {
                    "ok": True,
                    "status": "enabled" if enabled else "disabled",
                    "action": "enable" if enabled else "disable",
                    "plugin_id": normalized_id,
                    "unchanged": False,
                }
            )
            return payload

    async def stage_wheel(self, *, wheel_path: str) -> dict[str, Any]:
        if self.artifact_store is None:
            return _failure("unavailable", "plugin_artifact_store_unavailable")
        normalized = str(wheel_path or "").strip()
        if not normalized:
            return _failure("invalid_request", "plugin_wheel_path_required")
        async with self._operation_lock:
            try:
                return dict(
                    await asyncio.to_thread(
                        self.artifact_store.stage_wheel,
                        Path(normalized),
                    )
                )
            except PluginInstallationError as exc:
                return _failure(exc.status, exc.reason)
            except Exception:
                return _failure("error", "plugin_stage_failed")

    async def stage_source(self, *, source_path: str) -> dict[str, Any]:
        if self.artifact_store is None:
            return _failure("unavailable", "plugin_artifact_store_unavailable")
        normalized = str(source_path or "").strip()
        if not normalized:
            return _failure("invalid_request", "plugin_source_path_required")
        async with self._operation_lock:
            try:
                return dict(
                    await asyncio.to_thread(
                        self.artifact_store.stage_source,
                        Path(normalized),
                    )
                )
            except PluginInstallationError as exc:
                return _failure(exc.status, exc.reason)
            except Exception:
                return _failure("error", "plugin_source_stage_failed")

    async def publish_stage(
        self,
        *,
        stage_id: str,
        approved_permissions: Iterable[str],
    ) -> dict[str, Any]:
        if self.artifact_store is None:
            return _failure("unavailable", "plugin_artifact_store_unavailable")
        async with self._operation_lock:
            try:
                result = dict(
                    await asyncio.to_thread(
                        self.artifact_store.publish_stage,
                        stage_id,
                        approved_permissions=tuple(approved_permissions),
                    )
                )
            except PluginInstallationError as exc:
                return _failure(exc.status, exc.reason)
            except Exception:
                return _failure("error", "plugin_publish_failed")
            if not result.get("ok"):
                return result
            plugin_id = str(result.get("plugin_id") or "")
            persisted = self.selection_store.load()
            if plugin_id not in {item.plugin_id for item in persisted}:
                try:
                    self.selection_store.save((*persisted, PluginSelection(plugin_id, False)))
                except Exception:
                    result.update(
                        {
                            "ok": False,
                            "status": "persist_failed",
                            "reason": "plugin_selection_persist_failed",
                        }
                    )
            return result

    async def discard_stage(self, *, stage_id: str) -> dict[str, Any]:
        if self.artifact_store is None:
            return _failure("unavailable", "plugin_artifact_store_unavailable")
        async with self._operation_lock:
            try:
                return dict(await asyncio.to_thread(self.artifact_store.discard_stage, stage_id))
            except PluginInstallationError as exc:
                return _failure(exc.status, exc.reason)
            except Exception:
                return _failure("error", "plugin_stage_discard_failed")

    async def rollback(self, *, plugin_id: str) -> dict[str, Any]:
        if self.artifact_store is None:
            return _failure("unavailable", "plugin_artifact_store_unavailable", plugin_id=plugin_id)
        async with self._operation_lock:
            try:
                return dict(
                    await asyncio.to_thread(self.artifact_store.rollback_to_last_good, plugin_id)
                )
            except PluginInstallationError as exc:
                return _failure(exc.status, exc.reason, plugin_id=plugin_id)
            except Exception:
                return _failure("error", "plugin_rollback_failed", plugin_id=plugin_id)

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
        async with self._operation_lock:
            current = self.plugin_runtime.selections
            if normalized_id in {item.plugin_id for item in current}:
                candidate = tuple(
                    PluginSelection(item.plugin_id, False if item.plugin_id == normalized_id else item.enabled)
                    for item in current
                )
                status = dict(await self.plugin_runtime.reconfigure(candidate))
                target = _plugin_status(status, normalized_id)
                if target.get("status") != "disabled":
                    rollback = dict(await self.plugin_runtime.reconfigure(current))
                    return {
                        "ok": False,
                        "status": "deactivation_failed",
                        "reason": str(target.get("reason") or "plugin_disable_failed"),
                        "plugin_id": normalized_id,
                        "rollback_status": str(rollback.get("status") or "unknown"),
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
                if current:
                    await self.plugin_runtime.reconfigure(current)
                return _failure(
                    "persist_failed",
                    "plugin_selection_persist_failed",
                    plugin_id=normalized_id,
                )
            try:
                result = dict(
                    await asyncio.to_thread(self.artifact_store.remove_plugin, normalized_id)
                )
            except PluginInstallationError as exc:
                try:
                    self.selection_store.save(persisted)
                    await self.plugin_runtime.reconfigure(current)
                except Exception:
                    return _failure(
                        "rollback_failed",
                        "plugin_uninstall_rollback_failed",
                        plugin_id=normalized_id,
                    )
                return _failure(exc.status, exc.reason, plugin_id=normalized_id)
            except Exception:
                try:
                    self.selection_store.save(persisted)
                    await self.plugin_runtime.reconfigure(current)
                except Exception:
                    return _failure(
                        "rollback_failed",
                        "plugin_uninstall_rollback_failed",
                        plugin_id=normalized_id,
                    )
                return _failure("error", "plugin_uninstall_failed", plugin_id=normalized_id)
            runtime_status = dict(await self.plugin_runtime.reconfigure(next_selections))
            if runtime_status.get("status") == "degraded":
                result.update(
                    {
                        "ok": False,
                        "status": "removed_runtime_degraded",
                        "reason": "plugin_runtime_reconfigure_failed",
                        "restart_required": True,
                    }
                )
            return result

    def reconcile_runtime(self, plugin_status: Mapping[str, Any]) -> dict[str, Any]:
        if self.artifact_store is None:
            return {"status": "not_configured", "restart_required": False}
        try:
            return self.artifact_store.reconcile_runtime(plugin_status.get("plugins", ()))
        except PluginInstallationError as exc:
            return {"status": exc.status, "reason": exc.reason, "restart_required": False}
        except Exception:
            return {"status": "error", "reason": "plugin_artifact_reconcile_failed", "restart_required": False}

    async def invoke_capability(
        self,
        capability_id: str,
        args: Mapping[str, Any],
        *,
        context: InvocationContext,
    ) -> CapabilityResult:
        """Invoke diagnostics through the same runtime used by lifecycle work."""

        return await self.plugin_runtime.invoke(
            capability_id,
            args,
            context=context,
        )

    def execute_sync(self, *, action: str, plugin_id: str = "") -> dict[str, Any]:
        normalized_action = str(action or "").strip().lower()
        if normalized_action == "list":
            payload = self.snapshot()
            payload["action"] = "list"
            return payload
        if normalized_action == "enable":
            coroutine = self.set_enabled(plugin_id=plugin_id, enabled=True)
        elif normalized_action == "disable":
            coroutine = self.set_enabled(plugin_id=plugin_id, enabled=False)
        elif normalized_action == "restart":
            coroutine = self.restart(requested_plugin_id=plugin_id)
        else:
            return _failure("invalid_request", "extension_action_invalid", plugin_id=plugin_id)
        runtime_loop = self.plugin_runtime.runtime_loop
        if runtime_loop is None or not runtime_loop.is_running():
            coroutine.close()
            return _failure("unavailable", "plugin_host_loop_unavailable", plugin_id=plugin_id)
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is runtime_loop:
            coroutine.close()
            return _failure("unavailable", "plugin_management_requires_worker_thread", plugin_id=plugin_id)
        future = asyncio.run_coroutine_threadsafe(coroutine, runtime_loop)
        try:
            return dict(future.result(timeout=self.sync_timeout_seconds))
        except TimeoutError:
            future.cancel()
            return _failure("timeout", "plugin_management_timeout", plugin_id=plugin_id)
        except Exception:
            return _failure("error", "plugin_management_failed", plugin_id=plugin_id)


def _normalize_selections(selections: Iterable[PluginSelection]) -> tuple[PluginSelection, ...]:
    normalized: list[PluginSelection] = []
    seen: set[str] = set()
    for item in tuple(selections):
        if (
            not isinstance(item, PluginSelection)
            or not is_valid_plugin_id(item.plugin_id)
            or not isinstance(item.enabled, bool)
        ):
            raise ValueError("plugin_selection_invalid")
        if item.plugin_id in seen:
            raise ValueError("duplicate_plugin_selection")
        seen.add(item.plugin_id)
        normalized.append(PluginSelection(item.plugin_id, item.enabled))
    return tuple(normalized)


def _merge_selection_overrides(
    defaults: tuple[PluginSelection, ...],
    overrides: Mapping[str, bool],
) -> tuple[PluginSelection, ...]:
    merged = [PluginSelection(item.plugin_id, overrides.get(item.plugin_id, item.enabled)) for item in defaults]
    known = {item.plugin_id for item in defaults}
    merged.extend(PluginSelection(plugin_id, overrides[plugin_id]) for plugin_id in sorted(overrides) if plugin_id not in known)
    return tuple(merged)


def _plugin_status(snapshot: Mapping[str, Any], plugin_id: str) -> dict[str, Any]:
    for item in list(snapshot.get("plugins") or []):
        if isinstance(item, Mapping) and str(item.get("plugin_id") or "") == plugin_id:
            return dict(item)
    return {}


def _failure(status: str, reason: str, *, plugin_id: str = "") -> dict[str, Any]:
    return {
        "ok": False,
        "status": status,
        "reason": reason,
        "plugin_id": str(plugin_id or ""),
    }


__all__ = [
    "ExtensionManagementService",
    "PLUGIN_SELECTION_STATE_FILENAME",
    "PLUGIN_SELECTION_STATE_SCHEMA_VERSION",
    "PluginManagementRuntime",
    "PluginSelectionStore",
]
