"""Installed entry-point audits, factory/manifest loading and validation."""

from __future__ import annotations

from dataclasses import replace
from importlib import metadata as importlib_metadata
from typing import Any
import inspect

from akane_plugin.connections import ConnectionSpec, validate_connection_specs
from akane_plugin.service_contracts import validate_service_dependencies

from ..distribution_artifacts import audit_distribution_artifact
from ..instance_profile import PluginSelection
from ..plugin_api import (
    AKANE_PLUGIN_API_VERSION,
    AKANE_PLUGIN_ENTRYPOINT_GROUP,
    PluginManifest,
    is_valid_permission_id,
    is_valid_plugin_id,
)
from ..plugin_contribution_policy import PluginContributionPolicy

from .constants import _MAX_PERMISSIONS_PER_PLUGIN, _is_safe_version
from .contracts import _require_policy_acceptance
from .models import _ActivationFailure


def _installed_plugin_entry_points() -> tuple[Any, ...]:
    discovered = importlib_metadata.entry_points()
    if hasattr(discovered, "select"):
        return tuple(discovered.select(group=AKANE_PLUGIN_ENTRYPOINT_GROUP))
    return tuple(discovered.get(AKANE_PLUGIN_ENTRYPOINT_GROUP, ()))


def _validate_zero_parameter_factory(factory: Any) -> None:
    if not callable(factory):
        raise _ActivationFailure("invalid_plugin_factory")
    try:
        parameters = inspect.signature(factory).parameters
    except (TypeError, ValueError):
        raise _ActivationFailure("invalid_plugin_factory") from None
    if parameters:
        raise _ActivationFailure("plugin_factory_must_be_zero_parameter")


def _validate_manifest(manifest: Any, *, expected_plugin_id: str, artifact_version: str) -> None:
    if not isinstance(manifest, PluginManifest):
        raise _ActivationFailure("invalid_plugin_manifest")
    if manifest.plugin_id != expected_plugin_id:
        raise _ActivationFailure("plugin_id_mismatch")
    try:
        validate_service_dependencies(manifest.requires_services)
        validate_connection_specs(manifest.connections, manifest.permissions)
    except ValueError as exc:
        raise _ActivationFailure(str(exc)) from None
    if not is_valid_plugin_id(manifest.plugin_id):
        raise _ActivationFailure("invalid_plugin_id")
    if manifest.plugin_api_version != AKANE_PLUGIN_API_VERSION:
        raise _ActivationFailure("plugin_api_version_mismatch")
    if not _is_safe_version(manifest.plugin_version):
        raise _ActivationFailure("invalid_plugin_version")
    if manifest.plugin_version != artifact_version:
        raise _ActivationFailure("plugin_version_mismatch")
    if (
        not isinstance(manifest.permissions, tuple)
        or len(manifest.permissions) > _MAX_PERMISSIONS_PER_PLUGIN
        or len(set(manifest.permissions)) != len(manifest.permissions)
        or any(not is_valid_permission_id(permission) for permission in manifest.permissions)
    ):
        raise _ActivationFailure("invalid_plugin_manifest")


def audited_entry_point(selection: PluginSelection, entry_points: list[Any]) -> tuple[Any, str]:
    if not is_valid_plugin_id(selection.plugin_id):
        raise _ActivationFailure("invalid_plugin_id")
    if not entry_points:
        raise _ActivationFailure("plugin_not_installed", status="unavailable")
    if len(entry_points) != 1:
        raise _ActivationFailure("duplicate_plugin_artifact")
    entry_point = entry_points[0]
    if str(getattr(entry_point, "name", "") or "") != selection.plugin_id:
        raise _ActivationFailure("entry_point_name_mismatch")

    try:
        distribution = entry_point.dist
    except Exception:
        raise _ActivationFailure("distribution_metadata_unavailable") from None
    artifact = audit_distribution_artifact(distribution)
    if not artifact.ok:
        raise _ActivationFailure(artifact.reason)
    artifact_version = artifact.version
    return entry_point, artifact_version


def load_plugin(entry_point: Any, *, expected_plugin_id: str, artifact_version: str,
                contribution_policy: PluginContributionPolicy) -> tuple[Any, PluginManifest]:
    try:
        factory = entry_point.load()
    except Exception:
        raise _ActivationFailure("plugin_load_failed") from None
    _validate_zero_parameter_factory(factory)
    try:
        plugin = factory()
    except Exception:
        raise _ActivationFailure("plugin_factory_failed") from None

    manifest = getattr(plugin, "manifest", None)
    _validate_manifest(
        manifest,
        expected_plugin_id=expected_plugin_id,
        artifact_version=artifact_version,
    )
    manifest = replace(manifest, connections=tuple(ConnectionSpec.from_dict(spec.as_dict()) for spec in manifest.connections))
    _require_policy_acceptance(
        lambda: contribution_policy.validate_manifest(manifest),
        stage="manifest_contributions",
    )
    return plugin, manifest
