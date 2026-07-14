"""Restart-only host for explicitly allowlisted, trusted in-process plugins."""

from __future__ import annotations

import asyncio
import inspect
import re
from copy import deepcopy
from dataclasses import dataclass, replace
from importlib import metadata as importlib_metadata
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping

from capcore import (
    CapabilityAdapter,
    CapabilityDescriptor,
    CapabilityResult,
    HealthStatus,
    InvocationContext,
    validate_invocation_args,
)

from .distribution_artifacts import audit_distribution_artifact
from .instance_profile import PluginSelection
from .plugin_contribution_policy import ContributionPolicyDecision, PluginContributionPolicy
from .plugin_api import (
    AKANE_PLUGIN_API_VERSION,
    AKANE_PLUGIN_ENTRYPOINT_GROUP,
    PluginManifest,
    PluginRegistrar,
    is_valid_capability_id,
    is_valid_permission_id,
    is_valid_plugin_id,
)
from .plugin_result_projection import sanitize_capability_result


_SAFE_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}$")
_HOST_AVAILABLE_STATES = frozenset({"active", "degraded"})
_MAX_ADAPTERS_PER_PLUGIN = 16
_MAX_CAPABILITIES_PER_PLUGIN = 64


@dataclass(frozen=True, slots=True)
class PluginStatus:
    plugin_id: str
    enabled: bool
    status: str
    reason: str = ""
    plugin_version: str = ""
    stage: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "plugin_id": self.plugin_id,
            "enabled": self.enabled,
            "status": self.status,
            "reason": self.reason,
        }
        if self.plugin_version:
            payload["plugin_version"] = self.plugin_version
        if self.stage:
            payload["stage"] = self.stage
        return payload


@dataclass(frozen=True, slots=True)
class _CapabilityRegistration:
    plugin_id: str
    adapter: CapabilityAdapter
    descriptor: CapabilityDescriptor


@dataclass(frozen=True, slots=True)
class _ActivePlugin:
    plugin: Any
    adapters: tuple[CapabilityAdapter, ...]
    capability_ids: tuple[str, ...]


class _ActivationFailure(RuntimeError):
    def __init__(self, reason: str, *, status: str = "failed", stage: str = "") -> None:
        self.reason = reason
        self.status = status
        self.stage = stage
        super().__init__(reason)


class _StagedRegistrar(PluginRegistrar):
    def __init__(self) -> None:
        self._adapters: list[CapabilityAdapter] = []
        self._sealed = False

    @property
    def adapters(self) -> tuple[CapabilityAdapter, ...]:
        return tuple(self._adapters)

    def add_capability_adapter(self, adapter: CapabilityAdapter) -> None:
        if self._sealed:
            raise RuntimeError("plugin_registrar_sealed")
        self._adapters.append(adapter)

    def seal(self) -> None:
        self._sealed = True


class PluginHost:
    """Own plugin discovery, activation, immutable registration, and shutdown.

    Construction only captures an immutable instance selection and callables.
    Discovery, artifact audit, imports, factories, registration, health checks,
    and capability enumeration happen exclusively in :meth:`start`.
    """

    def __init__(
        self,
        selections: tuple[PluginSelection, ...],
        *,
        contribution_policy: PluginContributionPolicy,
        entry_points_provider: Callable[[], Iterable[Any]] | None = None,
        activation_timeout_seconds: float = 5.0,
        invoke_timeout_seconds: float = 5.0,
        close_timeout_seconds: float = 2.0,
    ) -> None:
        if not isinstance(selections, tuple) or any(
            not isinstance(selection, PluginSelection) for selection in selections
        ):
            raise TypeError("plugin_selections_must_be_snapshot")
        contribution_policy_id = str(getattr(contribution_policy, "policy_id", "") or "").strip()
        if (
            not is_valid_capability_id(contribution_policy_id)
            or not callable(getattr(contribution_policy, "validate_manifest", None))
            or not callable(getattr(contribution_policy, "validate_capability", None))
        ):
            raise TypeError("invalid_plugin_contribution_policy")
        self._selections = selections
        self._contribution_policy = contribution_policy
        self._contribution_policy_id = contribution_policy_id
        self._entry_points_provider = entry_points_provider or _installed_plugin_entry_points
        self._activation_timeout_seconds = max(0.1, float(activation_timeout_seconds))
        self._invoke_timeout_seconds = max(0.1, float(invoke_timeout_seconds))
        self._close_timeout_seconds = max(0.1, float(close_timeout_seconds))

        self._state = "created"
        self._plugin_statuses: tuple[PluginStatus, ...] = tuple(
            PluginStatus(
                plugin_id=_public_plugin_id(selection.plugin_id),
                enabled=selection.enabled,
                status="pending" if selection.enabled else "disabled",
                reason="host_not_started" if selection.enabled else "instance_disabled",
            )
            for selection in self._selections
        )
        self._active_plugins: Mapping[str, _ActivePlugin] = MappingProxyType({})
        self._capabilities: Mapping[str, _CapabilityRegistration] = MappingProxyType({})
        self._activation_order: tuple[CapabilityAdapter, ...] = ()
        self._closed_adapters: list[CapabilityAdapter] = []
        self._close_failure_count = 0

        self._lifecycle_lock = asyncio.Lock()
        self._invoke_lock = asyncio.Lock()
        self._inflight_count = 0
        self._inflight_zero = asyncio.Event()
        self._inflight_zero.set()

    @property
    def state(self) -> str:
        return self._state

    @property
    def capability_ids(self) -> tuple[str, ...]:
        return tuple(self._capabilities)

    @property
    def capability_descriptors(self) -> Mapping[str, CapabilityDescriptor]:
        """Return an immutable point-in-time view without exposing adapters."""

        return MappingProxyType(
            {
                capability_id: _copy_descriptor_snapshot(registration.descriptor)
                for capability_id, registration in self._capabilities.items()
            }
        )

    def status_snapshot(self) -> dict[str, Any]:
        reason = ""
        if self._state == "degraded":
            reason = "plugin_activation_failed"
        elif self._state not in _HOST_AVAILABLE_STATES:
            reason = "host_unavailable"
        return {
            "ok": self._state == "active",
            "status": self._state,
            "reason": reason,
            "contribution_policy": self._contribution_policy_id,
            "plugin_count": len(self._active_plugins),
            "capability_count": len(self._capabilities),
            "close_failure_count": self._close_failure_count,
            "plugins": [status.as_dict() for status in self._plugin_statuses],
        }

    async def start(self) -> dict[str, Any]:
        async with self._lifecycle_lock:
            if self._state in _HOST_AVAILABLE_STATES or self._state in {"starting", "stopping", "stopped"}:
                return self.status_snapshot()

            self._state = "starting"
            working_plugins: dict[str, _ActivePlugin] = {}
            working_capabilities: dict[str, _CapabilityRegistration] = {}
            activation_order: list[CapabilityAdapter] = []
            statuses: list[PluginStatus] = []

            enabled_ids = {selection.plugin_id for selection in self._selections if selection.enabled}
            entry_points_by_name: dict[str, list[Any]] = {}
            discovery_failed = False
            if enabled_ids:
                try:
                    discovered = tuple(self._entry_points_provider())
                    for entry_point in discovered:
                        name = str(getattr(entry_point, "name", "") or "")
                        if name in enabled_ids:
                            entry_points_by_name.setdefault(name, []).append(entry_point)
                except Exception:
                    discovery_failed = True

            for selection in self._selections:
                if not selection.enabled:
                    statuses.append(
                        PluginStatus(
                            plugin_id=_public_plugin_id(selection.plugin_id),
                            enabled=False,
                            status="disabled",
                            reason="instance_disabled",
                        )
                    )
                    continue
                if discovery_failed:
                    statuses.append(
                        PluginStatus(
                            plugin_id=_public_plugin_id(selection.plugin_id),
                            enabled=True,
                            status="unavailable",
                            reason="plugin_discovery_failed",
                        )
                    )
                    continue

                status, active, registrations = await self._activate_plugin(
                    selection,
                    entry_points_by_name.get(selection.plugin_id, []),
                    reserved_capability_ids=frozenset(working_capabilities),
                )
                statuses.append(status)
                if active is None:
                    continue
                working_plugins[selection.plugin_id] = active
                for registration in registrations:
                    working_capabilities[registration.descriptor.id] = registration
                activation_order.extend(active.adapters)

            self._plugin_statuses = tuple(statuses)
            self._active_plugins = MappingProxyType(dict(working_plugins))
            self._capabilities = MappingProxyType(dict(working_capabilities))
            self._activation_order = tuple(activation_order)
            enabled_failures = any(status.enabled and status.status != "active" for status in statuses)
            async with self._invoke_lock:
                self._state = "degraded" if enabled_failures else "active"
            return self.status_snapshot()

    async def stop(self) -> dict[str, Any]:
        async with self._lifecycle_lock:
            if self._state == "stopped":
                return self.status_snapshot()
            if self._state == "created":
                self._state = "stopped"
                return self.status_snapshot()

            async with self._invoke_lock:
                self._state = "stopping"

            try:
                await asyncio.wait_for(
                    self._inflight_zero.wait(),
                    timeout=self._invoke_timeout_seconds + 0.5,
                )
            except (TimeoutError, asyncio.TimeoutError):
                pass

            await self._close_adapters(reversed(self._activation_order))
            self._active_plugins = MappingProxyType({})
            self._capabilities = MappingProxyType({})
            self._activation_order = ()
            self._state = "stopped"
            return self.status_snapshot()

    async def invoke(
        self,
        capability_id: str,
        args: Mapping[str, Any],
        *,
        context: InvocationContext,
    ) -> CapabilityResult:
        async with self._invoke_lock:
            if self._state not in _HOST_AVAILABLE_STATES:
                return CapabilityResult(
                    is_error=True,
                    status="host_unavailable",
                    reason="host_unavailable",
                )
            registration = self._capabilities.get(str(capability_id or ""))
            if registration is None:
                return CapabilityResult(
                    is_error=True,
                    status="not_found",
                    reason="unknown_capability",
                )
            if not isinstance(context, InvocationContext):
                return CapabilityResult(
                    is_error=True,
                    status="validation_error",
                    reason="invalid_invocation_context",
                )
            self._inflight_count += 1
            self._inflight_zero.clear()

        try:
            validation = validate_invocation_args(registration.descriptor, args)
            if not validation.ok:
                first_reason = validation.errors[0].code if validation.errors else "invalid_arguments"
                return CapabilityResult(
                    is_error=True,
                    status="validation_error",
                    reason=first_reason,
                    content={"errors": [error.as_dict() for error in validation.errors]},
                )
            try:
                result = await asyncio.wait_for(
                    registration.adapter.invoke(
                        registration.descriptor.id,
                        validation.normalized_args,
                        context,
                    ),
                    timeout=self._invoke_timeout_seconds,
                )
            except asyncio.CancelledError:
                if _current_task_is_cancelling():
                    raise
                return CapabilityResult(
                    is_error=True,
                    status="error",
                    reason="plugin_invoke_failed",
                )
            except (TimeoutError, asyncio.TimeoutError):
                return CapabilityResult(
                    is_error=True,
                    status="error",
                    reason="plugin_invoke_timeout",
                )
            except Exception:
                return CapabilityResult(
                    is_error=True,
                    status="error",
                    reason="plugin_invoke_failed",
                )
            if not isinstance(result, CapabilityResult):
                return CapabilityResult(
                    is_error=True,
                    status="error",
                    reason="plugin_invoke_invalid_result",
                )
            return sanitize_capability_result(result)
        finally:
            async with self._invoke_lock:
                self._inflight_count = max(0, self._inflight_count - 1)
                if self._inflight_count == 0:
                    self._inflight_zero.set()

    async def _activate_plugin(
        self,
        selection: PluginSelection,
        entry_points: list[Any],
        *,
        reserved_capability_ids: frozenset[str],
    ) -> tuple[PluginStatus, _ActivePlugin | None, tuple[_CapabilityRegistration, ...]]:
        registrar = _StagedRegistrar()
        artifact_version = ""
        try:
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
                expected_plugin_id=selection.plugin_id,
                artifact_version=artifact_version,
            )
            _require_policy_acceptance(
                lambda: self._contribution_policy.validate_manifest(manifest),
                stage="manifest_contributions",
            )
            register = getattr(plugin, "register", None)
            if not callable(register):
                raise _ActivationFailure("invalid_plugin_contract")
            try:
                register_result = register(registrar)
            except Exception:
                raise _ActivationFailure("plugin_registration_failed") from None
            registrar.seal()
            if inspect.isawaitable(register_result):
                close_awaitable = getattr(register_result, "close", None)
                cancel_awaitable = getattr(register_result, "cancel", None)
                if callable(close_awaitable):
                    close_awaitable()
                elif callable(cancel_awaitable):
                    cancel_awaitable()
                raise _ActivationFailure("invalid_plugin_registration_result")
            if register_result is not None:
                raise _ActivationFailure("invalid_plugin_registration_result")
            if not registrar.adapters:
                raise _ActivationFailure("plugin_registered_no_adapters")
            if len(registrar.adapters) > _MAX_ADAPTERS_PER_PLUGIN:
                raise _ActivationFailure("too_many_plugin_adapters")
            if len({id(adapter) for adapter in registrar.adapters}) != len(registrar.adapters):
                raise _ActivationFailure("duplicate_plugin_adapter")

            registrations: list[_CapabilityRegistration] = []
            local_capability_ids: set[str] = set()
            for adapter in registrar.adapters:
                _validate_adapter_contract(adapter)
                health = await _bounded_adapter_call(
                    adapter.health(),
                    timeout_seconds=self._activation_timeout_seconds,
                    failure_reason="plugin_health_failed",
                )
                if not isinstance(health, HealthStatus):
                    raise _ActivationFailure("invalid_plugin_health_result")
                if not health.ok:
                    raise _ActivationFailure("plugin_health_unavailable")
                descriptors = await _bounded_adapter_call(
                    adapter.list_capabilities(),
                    timeout_seconds=self._activation_timeout_seconds,
                    failure_reason="plugin_capability_enumeration_failed",
                )
                if not isinstance(descriptors, tuple):
                    raise _ActivationFailure("invalid_capability_enumeration_result")
                for descriptor in descriptors:
                    _validate_descriptor_contract(descriptor, plugin_id=selection.plugin_id)
                    _require_policy_acceptance(
                        lambda: self._contribution_policy.validate_capability(
                            plugin_id=selection.plugin_id,
                            descriptor=descriptor,
                        ),
                        stage="capability_contributions",
                    )
                    descriptor_snapshot = _copy_descriptor_snapshot(descriptor)
                    capability_id = descriptor_snapshot.id
                    if capability_id in local_capability_ids:
                        raise _ActivationFailure("duplicate_plugin_capability")
                    if capability_id in reserved_capability_ids:
                        raise _ActivationFailure("capability_id_conflict")
                    local_capability_ids.add(capability_id)
                    registrations.append(
                        _CapabilityRegistration(
                            plugin_id=selection.plugin_id,
                            adapter=adapter,
                            descriptor=descriptor_snapshot,
                        )
                    )
                    if len(registrations) > _MAX_CAPABILITIES_PER_PLUGIN:
                        raise _ActivationFailure("too_many_plugin_capabilities")
            if not registrations:
                raise _ActivationFailure("plugin_registered_no_capabilities")

            active = _ActivePlugin(
                plugin=plugin,
                adapters=registrar.adapters,
                capability_ids=tuple(registration.descriptor.id for registration in registrations),
            )
            return (
                PluginStatus(
                    plugin_id=_public_plugin_id(selection.plugin_id),
                    enabled=True,
                    status="active",
                    plugin_version=manifest.plugin_version,
                ),
                active,
                tuple(registrations),
            )
        except _ActivationFailure as exc:
            registrar.seal()
            await self._close_adapters(reversed(registrar.adapters))
            return (
                PluginStatus(
                    plugin_id=_public_plugin_id(selection.plugin_id),
                    enabled=True,
                    status=exc.status,
                    reason=exc.reason,
                    plugin_version=artifact_version if _is_safe_version(artifact_version) else "",
                    stage=exc.stage,
                ),
                None,
                (),
            )
        except Exception:
            registrar.seal()
            await self._close_adapters(reversed(registrar.adapters))
            return (
                PluginStatus(
                    plugin_id=_public_plugin_id(selection.plugin_id),
                    enabled=True,
                    status="failed",
                    reason="plugin_activation_failed",
                ),
                None,
                (),
            )

    async def _close_adapters(self, adapters: Iterable[CapabilityAdapter]) -> None:
        for adapter in adapters:
            if any(closed is adapter for closed in self._closed_adapters):
                continue
            self._closed_adapters.append(adapter)
            try:
                await asyncio.wait_for(
                    adapter.aclose(),
                    timeout=self._close_timeout_seconds,
                )
            except asyncio.CancelledError:
                if _current_task_is_cancelling():
                    raise
                self._close_failure_count += 1
                continue
            except Exception:
                self._close_failure_count += 1
                continue


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
        or len(manifest.permissions) > 32
        or len(set(manifest.permissions)) != len(manifest.permissions)
        or any(not is_valid_permission_id(permission) for permission in manifest.permissions)
    ):
        raise _ActivationFailure("invalid_plugin_manifest")


def _validate_adapter_contract(adapter: Any) -> None:
    if not str(getattr(adapter, "provider_id", "") or "").strip():
        raise _ActivationFailure("invalid_capability_adapter")
    for method_name in ("health", "list_capabilities", "invoke", "aclose"):
        if not callable(getattr(adapter, method_name, None)):
            raise _ActivationFailure("invalid_capability_adapter")


def _validate_descriptor_contract(descriptor: Any, *, plugin_id: str) -> None:
    if not isinstance(descriptor, CapabilityDescriptor):
        raise _ActivationFailure("invalid_capability_descriptor")
    if not is_valid_capability_id(descriptor.id):
        raise _ActivationFailure("invalid_capability_id")
    if not descriptor.id.startswith(f"{plugin_id}."):
        raise _ActivationFailure("capability_prefix_mismatch")


def _copy_descriptor_snapshot(descriptor: CapabilityDescriptor) -> CapabilityDescriptor:
    try:
        copied_trigger = (
            replace(descriptor.trigger, raw=_copy_descriptor_value(descriptor.trigger.raw))
            if descriptor.trigger is not None
            else None
        )
        copied_inputs = tuple(replace(slot, raw=_copy_descriptor_value(slot.raw)) for slot in descriptor.inputs)
        copied_outputs = tuple(replace(slot, raw=_copy_descriptor_value(slot.raw)) for slot in descriptor.outputs)
        copied = replace(
            descriptor,
            trigger=copied_trigger,
            inputs=copied_inputs,
            outputs=copied_outputs,
            raw=_copy_descriptor_value(descriptor.raw),
        )
    except Exception:
        raise _ActivationFailure("invalid_capability_descriptor_snapshot") from None
    if not isinstance(copied, CapabilityDescriptor):
        raise _ActivationFailure("invalid_capability_descriptor_snapshot")
    return copied


def _copy_descriptor_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {deepcopy(raw_key): _copy_descriptor_value(raw_value) for raw_key, raw_value in value.items()}
    if isinstance(value, list):
        return [_copy_descriptor_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_copy_descriptor_value(item) for item in value)
    return deepcopy(value)


def _require_policy_acceptance(evaluate: Callable[[], Any], *, stage: str) -> None:
    try:
        decision = evaluate()
    except Exception:
        raise _ActivationFailure("contribution_policy_failed", stage=stage) from None
    if not isinstance(decision, ContributionPolicyDecision):
        raise _ActivationFailure("contribution_policy_failed", stage=stage)
    if not decision.accepted:
        raise _ActivationFailure("contribution_policy_rejected", stage=stage)


async def _bounded_adapter_call(awaitable: Any, *, timeout_seconds: float, failure_reason: str) -> Any:
    try:
        return await asyncio.wait_for(awaitable, timeout=timeout_seconds)
    except asyncio.CancelledError:
        if _current_task_is_cancelling():
            raise
        raise _ActivationFailure(failure_reason) from None
    except (TimeoutError, asyncio.TimeoutError):
        raise _ActivationFailure(failure_reason) from None
    except Exception:
        raise _ActivationFailure(failure_reason) from None


def _is_safe_version(version: Any) -> bool:
    return isinstance(version, str) and _SAFE_VERSION_PATTERN.fullmatch(version) is not None


def _public_plugin_id(plugin_id: Any) -> str:
    return plugin_id if is_valid_plugin_id(plugin_id) else "invalid-plugin-id"


def _current_task_is_cancelling() -> bool:
    task = asyncio.current_task()
    return bool(task is not None and task.cancelling())


__all__ = ["PluginHost", "PluginStatus"]
