"""Runtime overrides for editable config switches (Settings Catalog · slice B1).

Lets the control center change a *runtime-scope, non-secret* switch without
editing .env, following the model-service pattern (store + apply + load on
startup + local-request gate). The store is a small JSON file under
DATA_DIR/_local; on save we both persist the override and apply it live by
setting the attribute on the config module (runtime-scope switches are read
per-use via getattr/config.X, so the change takes effect immediately).

Hard gates (mirrors settings_catalog.is_runtime_editable):
- only scope=runtime, non-sensitive keys owned by the settings catalog may be
  set — restart / restart_client switches, secrets, and fields managed by
  another surface are rejected;
- values are coerced to the field's declared type, invalid input is rejected
  with a structured reason rather than written.

Editing is intentionally NOT for secrets, restart-scoped settings, or fields
managed elsewhere; those stay read-only in the catalog with a link to the owner.
"""
from __future__ import annotations

import json
import os
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
    """Validate, persist and apply one override. Returns the applied value.
    Raises SettingOverrideError (not_editable / invalid_value / unsupported_type)
    without writing anything on failure."""
    if not settings_catalog.is_runtime_editable(key):
        raise SettingOverrideError("not_editable")
    value = coerce_value(key, raw_value)
    overrides = store.load()
    overrides[key] = value
    store.save(overrides)
    setattr(config_module, key, value)
    return value


def load_and_apply_saved_overrides(
    config_module: Any,
    store: SettingsOverrideStore,
    *,
    on_error: Callable[[Exception], None] | None = None,
) -> dict[str, Any]:
    """At startup, re-apply saved overrides over the .env-loaded config. Skips
    any key that is no longer runtime-editable (catalog may have changed) or
    fails coercion — those are reported via on_error, never crash startup."""
    try:
        overrides = store.load()
    except Exception as exc:  # noqa: BLE001 - never let a bad store crash startup
        if on_error is not None:
            on_error(exc)
        return {}
    applied: dict[str, Any] = {}
    for key, value in overrides.items():
        if not settings_catalog.is_runtime_editable(key):
            continue
        try:
            coerced = coerce_value(key, value)
        except SettingOverrideError as exc:
            if on_error is not None:
                on_error(exc)
            continue
        setattr(config_module, key, coerced)
        applied[key] = coerced
    return applied
