"""Validated, immutable Akane instance identity for host composition.

M65-A intentionally stops at read-only composition metadata.  It does not
change storage roots, activate feature switches, or load plugins.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .plugin_api import is_valid_plugin_id


INSTANCE_MANIFEST_SCHEMA_VERSION = 1
LOCAL_DEFAULT_INSTANCE_ID = "local-default"
_SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_ROOT_FIELDS = frozenset({"schema_version", "instance_id", "character_pack_id", "features", "plugins"})
_FEATURE_FIELDS = frozenset({"care"})
_PLUGIN_FIELDS = frozenset({"id", "enabled"})
_MAX_PLUGIN_SELECTIONS = 32


class InstanceProfileError(ValueError):
    """Structured startup/request-context failure without leaking local paths."""

    def __init__(self, *, status: str, reason: str, field: str = "") -> None:
        self.status = str(status)
        self.reason = str(reason)
        self.field = str(field)
        super().__init__(json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True))

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": False,
            "status": self.status,
            "reason": self.reason,
        }
        if self.field:
            payload["field"] = self.field
        return payload


@dataclass(frozen=True, slots=True)
class FeatureSnapshot:
    """Resolved feature decisions for one immutable host startup snapshot."""

    care: bool

    def as_dict(self) -> dict[str, bool]:
        return {"care": self.care}


@dataclass(frozen=True, slots=True)
class PluginSelection:
    """One restart-only plugin decision from the instance allowlist."""

    plugin_id: str
    enabled: bool

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.plugin_id, "enabled": self.enabled}


@dataclass(frozen=True, slots=True)
class InstanceManifest:
    schema_version: int
    instance_id: str
    character_pack_id: str
    features: FeatureSnapshot
    plugins: tuple[PluginSelection, ...]


@dataclass(frozen=True, slots=True)
class InstanceContext:
    """Safe instance metadata exposed to the host and request boundary."""

    manifest: InstanceManifest
    source: str

    @property
    def instance_id(self) -> str:
        return self.manifest.instance_id

    @property
    def character_pack_id(self) -> str:
        return self.manifest.character_pack_id

    @property
    def features(self) -> FeatureSnapshot:
        return self.manifest.features

    @property
    def plugins(self) -> tuple[PluginSelection, ...]:
        return self.manifest.plugins

    @property
    def is_compatibility_default(self) -> bool:
        return self.source == "compatibility_default"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.manifest.schema_version,
            "instance_id": self.instance_id,
            "character_pack_id": self.character_pack_id,
            "features": self.features.as_dict(),
            "source": self.source,
        }


def build_local_default_instance_context() -> InstanceContext:
    """Preserve the pre-M65 single-instance behavior without reading a file."""

    return InstanceContext(
        manifest=InstanceManifest(
            schema_version=INSTANCE_MANIFEST_SCHEMA_VERSION,
            instance_id=LOCAL_DEFAULT_INSTANCE_ID,
            character_pack_id="",
            features=FeatureSnapshot(care=True),
            plugins=(),
        ),
        source="compatibility_default",
    )


def _fail(reason: str, *, field: str = "", status: str = "invalid_config") -> None:
    raise InstanceProfileError(status=status, reason=reason, field=field)


def _require_safe_id(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        _fail("invalid_safe_id", field=field)
    normalized = value.strip()
    if normalized != value or not _SAFE_ID_PATTERN.fullmatch(normalized):
        _fail("invalid_safe_id", field=field)
    return normalized


def _require_plugin_id(value: Any, *, field: str) -> str:
    if not is_valid_plugin_id(value):
        _fail("invalid_plugin_id", field=field)
    return value


def _reject_unknown_fields(payload: Mapping[str, Any], allowed: frozenset[str], *, field: str) -> None:
    unknown = sorted(str(key) for key in payload if key not in allowed)
    if unknown:
        unknown_field = f"{field}.{unknown[0]}" if field else unknown[0]
        reason = "unknown_feature" if field == "features" else "unsupported_manifest_field"
        _fail(reason, field=unknown_field)


def parse_instance_manifest(payload: Any, *, selected_instance_id: str) -> InstanceManifest:
    """Validate the M65-A subset and reject future-only manifest fields."""

    if not isinstance(payload, Mapping):
        _fail("manifest_root_must_be_table")
    _reject_unknown_fields(payload, _ROOT_FIELDS, field="")

    schema_version = payload.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        _fail("schema_version_must_be_integer", field="schema_version")
    if schema_version != INSTANCE_MANIFEST_SCHEMA_VERSION:
        _fail("unsupported_schema_version", field="schema_version", status="incompatible")

    manifest_instance_id = _require_safe_id(payload.get("instance_id"), field="instance_id")
    if manifest_instance_id != selected_instance_id:
        _fail("selected_instance_id_mismatch", field="instance_id")
    character_pack_id = _require_safe_id(payload.get("character_pack_id"), field="character_pack_id")

    features_payload = payload.get("features")
    if not isinstance(features_payload, Mapping):
        _fail("features_must_be_table", field="features")
    _reject_unknown_fields(features_payload, _FEATURE_FIELDS, field="features")
    care = features_payload.get("care")
    if not isinstance(care, bool):
        _fail("feature_must_be_boolean", field="features.care")

    plugins_payload = payload.get("plugins", [])
    if not isinstance(plugins_payload, list):
        _fail("plugins_must_be_array", field="plugins")
    if len(plugins_payload) > _MAX_PLUGIN_SELECTIONS:
        _fail("too_many_plugins", field="plugins")
    plugins: list[PluginSelection] = []
    seen_plugin_ids: set[str] = set()
    for index, raw_plugin in enumerate(plugins_payload):
        field_prefix = f"plugins.{index}"
        if not isinstance(raw_plugin, Mapping):
            _fail("plugin_must_be_table", field=field_prefix)
        _reject_unknown_fields(raw_plugin, _PLUGIN_FIELDS, field=field_prefix)
        plugin_id = _require_plugin_id(raw_plugin.get("id"), field=f"{field_prefix}.id")
        enabled = raw_plugin.get("enabled")
        if not isinstance(enabled, bool):
            _fail("plugin_enabled_must_be_boolean", field=f"{field_prefix}.enabled")
        if plugin_id in seen_plugin_ids:
            _fail("duplicate_plugin_id", field=f"{field_prefix}.id")
        seen_plugin_ids.add(plugin_id)
        plugins.append(PluginSelection(plugin_id=plugin_id, enabled=enabled))

    return InstanceManifest(
        schema_version=schema_version,
        instance_id=manifest_instance_id,
        character_pack_id=character_pack_id,
        features=FeatureSnapshot(care=care),
        plugins=tuple(plugins),
    )


def load_instance_manifest(path: Path, *, selected_instance_id: str) -> InstanceManifest:
    try:
        with Path(path).open("rb") as manifest_file:
            payload = tomllib.load(manifest_file)
    except FileNotFoundError:
        _fail("instance_manifest_not_found", status="unavailable")
    except (OSError, tomllib.TOMLDecodeError):
        _fail("instance_manifest_unreadable")
    return parse_instance_manifest(payload, selected_instance_id=selected_instance_id)


def _resolve_manifest_path(data_root: Path, instance_id: str) -> Path:
    try:
        resolved_root = Path(data_root).expanduser().resolve()
        instances_root = (resolved_root / "instances").resolve()
        manifest_path = (instances_root / instance_id / "instance.toml").resolve()
        instances_root.relative_to(resolved_root)
        manifest_path.relative_to(instances_root)
    except (OSError, ValueError):
        _fail("unsafe_instance_manifest_path")
    return manifest_path


def resolve_instance_context(*, data_root: Path, selected_instance_id: str | None) -> InstanceContext:
    """Resolve one startup snapshot without creating or moving instance data."""

    raw_selector = "" if selected_instance_id is None else str(selected_instance_id)
    if not raw_selector.strip():
        return build_local_default_instance_context()
    instance_id = _require_safe_id(raw_selector, field="AKANE_INSTANCE_ID")
    manifest_path = _resolve_manifest_path(Path(data_root), instance_id)
    manifest = load_instance_manifest(manifest_path, selected_instance_id=instance_id)
    return InstanceContext(manifest=manifest, source="manifest")


def instance_context_from_request(request: Any) -> InstanceContext:
    """Read the host-bound context through a real ASGI request boundary."""

    app = getattr(request, "app", None)
    state = getattr(app, "state", None)
    context = getattr(state, "akane_instance_context", None)
    if not isinstance(context, InstanceContext):
        _fail("instance_context_not_bound", status="unavailable")
    return context
