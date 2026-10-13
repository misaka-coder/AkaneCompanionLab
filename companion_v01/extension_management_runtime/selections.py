"""Instance-owned selection overlay and reviewed service-binding persistence."""

from __future__ import annotations


import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Mapping
from ..instance_profile import PluginSelection
from ..plugin_api import is_valid_plugin_id
from ..plugin_service_dependencies import provider_bindings
from .contracts import PLUGIN_SELECTION_STATE_SCHEMA_VERSION, _normalize_selections, _merge_selection_overrides


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

    def load_service_bindings(self):
        with self._lock:
            if not self.path.exists():
                return ()
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            self._parse_payload(payload)
            bindings = provider_bindings(payload.get("service_bindings", ()))
            return tuple(
                {"service_id": key[0], "version": key[1], "plugin_id": value} for key, value in sorted(bindings.items())
            )

    def save(self, selections: tuple[PluginSelection, ...], *, service_bindings=None) -> None:
        normalized = _normalize_selections(selections)
        default_by_id = {item.plugin_id: item.enabled for item in self.defaults}
        overrides = {
            item.plugin_id: item.enabled
            for item in normalized
            if default_by_id.get(item.plugin_id) is None or default_by_id[item.plugin_id] != item.enabled
        }
        payload = {
            "schema_version": PLUGIN_SELECTION_STATE_SCHEMA_VERSION,
            "instance_id": self.instance_id,
            "overrides": {key: overrides[key] for key in sorted(overrides)},
        }
        with self._lock:
            if service_bindings is None:
                service_bindings = self.load_service_bindings()
            bindings = provider_bindings(service_bindings)
            if bindings:
                payload["service_bindings"] = [
                    {"service_id": key[0], "version": key[1], "plugin_id": value}
                    for key, value in sorted(bindings.items())
                ]
            data = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
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

    def save_service_binding(self, *, service_id: str, version: int, plugin_id: str, expected_sha256: str):
        """Update one binding in a reviewed file; activation remains explicit.

        Preserve unknown fields and all other selections. Check the exact bytes
        again immediately before replacement to reject intervening edits.
        """
        import hashlib

        binding = {"service_id": service_id, "version": version, "plugin_id": plugin_id}
        provider_bindings((binding,))
        with self._lock:
            original = self.path.read_bytes()
            if hashlib.sha256(original).hexdigest() != expected_sha256:
                raise ValueError("plugin_selection_conflict")
            payload = json.loads(original)
            self._parse_payload(payload)
            bindings = dict(provider_bindings(payload.get("service_bindings", ())))
            bindings[(service_id, version)] = plugin_id
            payload["service_bindings"] = [
                {"service_id": key[0], "version": key[1], "plugin_id": value} for key, value in sorted(bindings.items())
            ]
            data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
            try:
                with temporary.open("xb") as output:
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
                if self.path.read_bytes() != original:
                    raise ValueError("plugin_selection_conflict")
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)
            return {
                "ok": True,
                "status": "saved",
                "activated": False,
                "sha256": hashlib.sha256(data).hexdigest(),
                "binding": binding,
            }
