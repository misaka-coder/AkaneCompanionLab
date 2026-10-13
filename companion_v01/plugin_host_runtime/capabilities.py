"""Capability enumeration, policy checks and canonical schema snapshots."""

from __future__ import annotations

from typing import Any

from capcore import SchemaDefinitionError, build_tool_spec

from ..plugin_api import PluginManifest

from .constants import _MAX_CAPABILITIES_PER_PLUGIN
from .contracts import (
    _require_policy_acceptance,
    _validate_adapter_contract,
    _validate_descriptor_contract,
    _validate_managed_artifact_descriptor_contract,
)
from .descriptors import _copy_descriptor_snapshot
from .models import _ActivationFailure, _CapabilityRegistration
from .registrar import _StagedRegistrar
from .timing import _bounded_adapter_call


async def collect_capabilities(host: Any, registrar: _StagedRegistrar, *,
                               plugin_id: str, manifest: PluginManifest,
                               reserved_capability_ids: frozenset[str],
                               check_health: bool) -> tuple[_CapabilityRegistration, ...]:
    registrations: list[_CapabilityRegistration] = []
    local_capability_ids: set[str] = set()
    for adapter in registrar.adapters:
        _validate_adapter_contract(adapter)
        if check_health and not manifest.requires_services:
            await host._check_adapter_health(adapter)
        descriptors = await _bounded_adapter_call(
            adapter.list_capabilities(),
            timeout_seconds=host._activation_timeout_seconds,
            failure_reason="plugin_capability_enumeration_failed",
        )
        if not isinstance(descriptors, tuple):
            raise _ActivationFailure("invalid_capability_enumeration_result")
        for descriptor in descriptors:
            _validate_descriptor_contract(descriptor, plugin_id=plugin_id)
            _validate_managed_artifact_descriptor_contract(descriptor, manifest=manifest)
            _require_policy_acceptance(
                lambda: host._contribution_policy.validate_capability(
                    plugin_id=plugin_id,
                    descriptor=descriptor,
                ),
                stage="capability_contributions",
            )
            validate_permissions = getattr(
                host._contribution_policy,
                "validate_capability_permissions",
                None,
            )
            if callable(validate_permissions):
                _require_policy_acceptance(
                    lambda: validate_permissions(
                        manifest=manifest,
                        descriptor=descriptor,
                    ),
                    stage="capability_permissions",
                )
            descriptor_snapshot = _copy_descriptor_snapshot(descriptor)
            try:
                canonical_spec = build_tool_spec(descriptor_snapshot)
            except SchemaDefinitionError as exc:
                raise _ActivationFailure(
                    exc.code, stage="capability_schema", schema_errors=({
                        "code": exc.code, "field": exc.path, "message": exc.detail,
                    },),
                ) from None
            capability_id = descriptor_snapshot.id
            if capability_id in local_capability_ids:
                raise _ActivationFailure("duplicate_plugin_capability")
            if capability_id in reserved_capability_ids:
                raise _ActivationFailure("capability_id_conflict")
            local_capability_ids.add(capability_id)
            registrations.append(
                _CapabilityRegistration(
                    plugin_id=plugin_id,
                    adapter=adapter,
                    descriptor=descriptor_snapshot,
                    permissions=manifest.permissions,
                    tool_spec=canonical_spec,
                    private_result_values=registrar._private_result_values,
                    connections=manifest.connections,
                )
            )
            if len(registrations) > _MAX_CAPABILITIES_PER_PLUGIN:
                raise _ActivationFailure("too_many_plugin_capabilities")
    return tuple(registrations)
