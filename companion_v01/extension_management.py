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
from typing import Any, Iterable, Mapping

from .instance_profile import PluginSelection
from .plugin_api import is_valid_plugin_id
from .plugin_host import PluginHost


PLUGIN_SELECTION_STATE_SCHEMA_VERSION = 1
PLUGIN_SELECTION_STATE_FILENAME = "plugin-selections.json"


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
    """Coordinate persisted plugin selection and the live PluginHost."""

    def __init__(
        self,
        *,
        plugin_host: PluginHost,
        selection_store: PluginSelectionStore,
        sync_timeout_seconds: float | None = None,
    ) -> None:
        self.plugin_host = plugin_host
        self.selection_store = selection_store
        self.sync_timeout_seconds = (
            None if sync_timeout_seconds is None else max(1.0, float(sync_timeout_seconds))
        )
        self._operation_lock = asyncio.Lock()

    def snapshot(self) -> dict[str, Any]:
        payload = dict(self.plugin_host.status_snapshot())
        payload["kind"] = "plugin"
        payload["management"] = {
            "status": "ready",
            "persistence": "instance_overlay",
            "load_reason": self.selection_store.load_reason,
            "supports": ["list", "enable", "disable", "restart"],
        }
        return payload

    async def restart(self, *, requested_plugin_id: str = "") -> dict[str, Any]:
        plugin_id = str(requested_plugin_id or "").strip()
        if plugin_id and plugin_id not in {item.plugin_id for item in self.plugin_host.selections}:
            return _failure("not_found", "plugin_not_configured", plugin_id=plugin_id)
        async with self._operation_lock:
            result = dict(await self.plugin_host.restart())
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
        async with self._operation_lock:
            previous = self.plugin_host.selections
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
            candidate_status = dict(await self.plugin_host.reconfigure(candidate))
            target = _plugin_status(candidate_status, normalized_id)
            expected_status = "active" if enabled else "disabled"
            if target.get("status") != expected_status:
                rollback = dict(await self.plugin_host.reconfigure(previous))
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
                rollback = dict(await self.plugin_host.reconfigure(previous))
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
        runtime_loop = self.plugin_host.runtime_loop
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
    "PluginSelectionStore",
]
