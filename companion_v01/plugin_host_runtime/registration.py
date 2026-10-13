"""Synchronous contributions and accepted plugin publication."""

from __future__ import annotations

from dataclasses import replace
from typing import Any
import inspect

from akane_plugin.service_contracts import service_method_info

from ..plugin_api import PluginManifest
from ..plugin_contribution_policy import PluginContributionPolicy
from ..plugin_events import _PluginEventRegistration
from ..plugin_hooks import _PluginHookRegistration
from ..plugin_qq_commands import _PluginCommandRegistration

from .constants import _MAX_ADAPTERS_PER_PLUGIN, _plugin_skill_mount_name, _public_plugin_id
from .contracts import _require_policy_acceptance
from .models import PluginStatus, _ActivationFailure, _ActivePlugin, _CapabilityRegistration
from .registrar import _StagedRegistrar


def register_contributions(plugin: Any, registrar: _StagedRegistrar, *, plugin_id: str,
                           reserved_qq_commands: frozenset[str],
                           reserved_skill_names: frozenset[str]) -> None:
    register = getattr(plugin, "register", None)
    if not callable(register):
        raise _ActivationFailure("invalid_plugin_contract")
    try:
        register_result = register(registrar)
    except _ActivationFailure:
        raise
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
    if any(reg.subscription and not reg.subscription.subscription_id.startswith(plugin_id + ".")
           for reg in registrar.event_registrations):
        raise _ActivationFailure("event_subscription_id_invalid")
    if any(reg.command in reserved_qq_commands for reg in registrar.qq_commands):
        raise _ActivationFailure("qq_command_conflict")
    if any(reg.name in reserved_skill_names for reg in registrar.skills):
        raise _ActivationFailure("skill_name_conflict")
    if len(registrar.adapters) > _MAX_ADAPTERS_PER_PLUGIN:
        raise _ActivationFailure("too_many_plugin_adapters")
    if len({id(adapter) for adapter in registrar.adapters}) != len(registrar.adapters):
        raise _ActivationFailure("duplicate_plugin_adapter")


def validate_contributions(contribution_policy: PluginContributionPolicy,
                           manifest: PluginManifest, registrar: _StagedRegistrar,
                           registrations: tuple[_CapabilityRegistration, ...]) -> None:
    validate_registration = getattr(contribution_policy, "validate_registration", None)
    if callable(validate_registration):
        _require_policy_acceptance(
            lambda: validate_registration(
                manifest=manifest,
                capability_count=sum(service_method_info(item.descriptor) is None for item in registrations),
                qq_command_count=len(registrar.qq_commands),
                event_subscription_count=len(registrar.event_registrations),
                hook_handler_count=len(registrar.hook_registrations),
                has_background_job=bool(registrar.background_services),
                prompt_block_count=len(registrar.prompt_blocks),
                skill_count=len(registrar.skills),
            ),
            stage="registration_contributions",
        )
    if not (
        registrations
        or registrar.qq_commands
        or registrar.event_registrations
        or registrar.hook_registrations
        or registrar.background_services
        or registrar.prompt_blocks
        or registrar.skills
    ):
        raise _ActivationFailure("plugin_registered_no_contributions")


def accepted_plugin(plugin: Any, manifest: PluginManifest, registrar: _StagedRegistrar,
                    registrations: tuple[_CapabilityRegistration, ...], *, plugin_id: str
                    ) -> tuple[PluginStatus, _ActivePlugin, tuple[_CapabilityRegistration, ...]]:
    active = _ActivePlugin(
        plugin=plugin,
        adapters=registrar.adapters,
        capability_ids=tuple(registration.descriptor.id for registration in registrations),
        requires_services=manifest.requires_services,
        connections=manifest.connections,
        background_services=registrar.background_services,
        qq_command_registrations=tuple(
            _PluginCommandRegistration(
                plugin_id=plugin_id,
                command=reg.command,
                handler=reg.handler,
            )
            for reg in registrar.qq_commands
        ),
        event_registrations=tuple(
            _PluginEventRegistration(
                plugin_id=plugin_id,
                event_type=reg.event_type,
                handler=reg.handler,
                subscription=reg.subscription,
                permissions=tuple(manifest.permissions),
                private_values=registrar._private_result_values,
                connections=manifest.connections,
            )
            for reg in registrar.event_registrations
        ),
        hook_registrations=tuple(
            _PluginHookRegistration(
                plugin_id=plugin_id,
                hook_type=reg.hook_type,
                handler=reg.handler,
            )
            for reg in registrar.hook_registrations
        ),
        prompt_block_registrations=tuple(registrar.prompt_blocks),
        skill_registrations=tuple(
            replace(
                registration,
                mount_name=_plugin_skill_mount_name(plugin_id, registration.name),
            )
            for registration in registrar.skills
        ),
    )
    return (
        PluginStatus(
            plugin_id=_public_plugin_id(plugin_id),
            enabled=True,
            status="active",
            plugin_version=manifest.plugin_version,
            permissions=tuple(manifest.permissions),
            capability_ids=tuple(registration.descriptor.id for registration in registrations),
            qq_commands=tuple(reg.command for reg in registrar.qq_commands),
            event_types=tuple(reg.event_type for reg in registrar.event_registrations),
            hook_types=tuple(reg.hook_type for reg in registrar.hook_registrations),
            skill_names=tuple(reg.name for reg in registrar.skills),
        ),
        active,
        tuple(registrations),
    )
