"""Staged generation discovery, registration ownership and publication."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any

from capcore import CapabilityAdapter

from .constants import _public_plugin_id
from .contributions import _build_contribution_snapshots
from .models import PluginStatus, _ActivePlugin, _CapabilityRegistration


@dataclass
class StartupPlan:
    """Own all staged resources until one complete generation is published."""

    plugins: dict[str, _ActivePlugin] = field(default_factory=dict)
    capabilities: dict[str, _CapabilityRegistration] = field(default_factory=dict)
    qq_commands: set[str] = field(default_factory=set)
    skill_names: set[str] = field(default_factory=set)
    adapters: list[CapabilityAdapter] = field(default_factory=list)
    statuses: list[PluginStatus] = field(default_factory=list)


def discover_entry_points(host: Any) -> tuple[dict[str, list[Any]], bool]:
    enabled_ids = {selection.plugin_id for selection in host._selections if selection.enabled}
    entry_points_by_name: dict[str, list[Any]] = {}
    discovery_failed = False
    if enabled_ids:
        try:
            discovered = tuple(host._entry_points_provider())
            for entry_point in discovered:
                name = str(getattr(entry_point, "name", "") or "")
                if name in enabled_ids:
                    entry_points_by_name.setdefault(name, []).append(entry_point)
        except Exception:
            discovery_failed = True
    return entry_points_by_name, discovery_failed


async def stage_plugins(host: Any, plan: StartupPlan, *, prepare_only: bool) -> None:
    entry_points_by_name, discovery_failed = discover_entry_points(host)
    for selection in host._selections:
        if not selection.enabled:
            plan.statuses.append(
                PluginStatus(
                    plugin_id=_public_plugin_id(selection.plugin_id),
                    enabled=False,
                    status="disabled",
                    reason="instance_disabled",
                )
            )
            continue
        if discovery_failed:
            plan.statuses.append(
                PluginStatus(
                    plugin_id=_public_plugin_id(selection.plugin_id),
                    enabled=True,
                    status="unavailable",
                    reason="plugin_discovery_failed",
                )
            )
            continue

        status, active, registrations = await host._activate_plugin(
            selection,
            entry_points_by_name.get(selection.plugin_id, []),
            reserved_capability_ids=frozenset(plan.capabilities),
            reserved_qq_commands=frozenset(plan.qq_commands),
            reserved_skill_names=frozenset(plan.skill_names),
            check_health=not prepare_only,
        )
        plan.statuses.append(status)
        if active is None:
            continue
        plan.plugins[selection.plugin_id] = active
        for registration in registrations:
            plan.capabilities[registration.descriptor.id] = registration
        plan.qq_commands.update(
            registration.command for registration in active.qq_command_registrations
        )
        plan.skill_names.update(
            registration.name for registration in active.skill_registrations
        )
        plan.adapters.extend(active.adapters)


def publish_startup(host: Any, plan: StartupPlan, *, prepare_only: bool) -> None:
    host._plugin_statuses = tuple(plan.statuses)
    host._active_plugins = MappingProxyType(dict(plan.plugins))
    host._capabilities = MappingProxyType(dict(plan.capabilities))
    host._activation_order = tuple(plan.adapters)
    host._generation += 1
    host._contribution_snapshots = _build_contribution_snapshots(
        plan.plugins,
        plan.capabilities,
        generation=host._generation,
    )
    if prepare_only:
        host._plugin_statuses = tuple(replace(item, status="prepared") if item.status == "active" else item
                                      for item in host._plugin_statuses)
        host._state = "prepared" if all(not item.enabled or item.status == "prepared" for item in host._plugin_statuses) else "failed"
