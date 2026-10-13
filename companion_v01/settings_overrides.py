"""Typed, atomic per-Bot overrides, without editing environment files.

Runtime keys apply live. The two explicitly allowed private-memory restart
keys persist desired values and apply only during Bot construction. Secrets,
other restart keys, and settings owned by other surfaces remain read-only.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Callable

import config

from . import settings_catalog

SCHEMA_VERSION = 1

_TRUE = {"true", "1", "yes", "on", "开", "是"}
_FALSE = {"false", "0", "no", "off", "关", "否"}


class SettingOverrideError(ValueError):
    """Structured failure for a rejected override (never write on failure)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class SettingsOverrideStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def update(self, key: str, value: Any) -> None:
        # Serialize read/modify/replace so simultaneous settings saves cannot
        # lose each other's keys or share an in-flight temporary file.
        with self._lock:
            overrides = self.load()
            overrides[key] = value
            self.save(overrides)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        overrides = data.get("overrides") if isinstance(data, dict) else None
        return dict(overrides) if isinstance(overrides, dict) else {}

    def save(self, overrides: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schemaVersion": SCHEMA_VERSION, "overrides": dict(overrides)}
        temp_path = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temp_path, self.path)


class RuntimeConfigView:
    """Per-Bot config overlay with process defaults as a read-only fallback."""

    def __init__(self, base_config: Any, overrides: dict[str, Any] | None = None) -> None:
        object.__setattr__(self, "_base_config", base_config)
        object.__setattr__(self, "_overrides", dict(overrides or {}))

    def __getattr__(self, name: str) -> Any:
        overrides = object.__getattribute__(self, "_overrides")
        if name in overrides:
            return overrides[name]
        return getattr(object.__getattribute__(self, "_base_config"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        object.__getattribute__(self, "_overrides")[name] = value

    def local_overrides(self) -> dict[str, Any]:
        return dict(object.__getattribute__(self, "_overrides"))


def _base_kind(key: str) -> tuple[str, bool]:
    """(kind, optional) for a key from its pydantic annotation.
    kind in {bool,int,float,str}; raises SettingOverrideError for anything else."""
    field = config.Settings.model_fields.get(key)
    annotation = getattr(field, "annotation", None)
    text = str(annotation)
    optional = ("None" in text) or ("Optional" in text)
    if annotation is bool or "bool" in text:
        return "bool", optional
    if annotation is int or ("int" in text and "float" not in text):
        return "int", optional
    if annotation is float or "float" in text:
        return "float", optional
    if annotation is str or "str" in text:
        return "str", optional
    raise SettingOverrideError("unsupported_type")


def coerce_value(key: str, raw: Any) -> Any:
    """Coerce raw input to the field's declared type, or raise
    SettingOverrideError("invalid_value")."""
    kind, optional = _base_kind(key)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        if kind == "str" and raw is not None:
            return ""
        if optional:
            return None
        if kind == "str":
            return ""
        raise SettingOverrideError("invalid_value")
    if kind == "bool":
        if isinstance(raw, bool):
            return raw
        token = str(raw).strip().lower()
        if token in _TRUE:
            return True
        if token in _FALSE:
            return False
        raise SettingOverrideError("invalid_value")
    if kind == "int":
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise SettingOverrideError("invalid_value")
    if kind == "float":
        try:
            return float(raw)
        except (TypeError, ValueError):
            raise SettingOverrideError("invalid_value")
    return str(raw)


def set_override(
    config_module: Any,
    store: SettingsOverrideStore,
    *,
    key: str,
    raw_value: Any,
) -> Any:
    """Validate and persist; apply live only for runtime keys. Returns desired value.
    Raises SettingOverrideError (not_editable / invalid_value / unsupported_type)
    without writing anything on failure."""
    if not settings_catalog.is_setting_editable(key):
        raise SettingOverrideError("not_editable")
    value = coerce_value(key, raw_value)
    store.update(key, value)
    if settings_catalog.is_runtime_editable(key):
        setattr(config_module, key, value)
    return value


def load_saved_overrides(
    store: SettingsOverrideStore,
    *,
    on_error: Callable[[Exception], None] | None = None,
) -> dict[str, Any]:
    """Load and validate saved overrides without mutating a config module."""

    try:
        overrides = store.load()
    except Exception as exc:  # noqa: BLE001 - a bad store must not crash startup
        if on_error is not None:
            on_error(exc)
        return {}
    validated: dict[str, Any] = {}
    for key, value in overrides.items():
        if not settings_catalog.is_setting_editable(key):
            continue
        try:
            validated[key] = coerce_value(key, value)
        except SettingOverrideError as exc:
            if on_error is not None:
                on_error(exc)
    return validated
