"""Generation contribution inventory from accepted registrations."""

from __future__ import annotations

from typing import Mapping

from akane_plugin.service_contracts import service_method_info

from .constants import _public_plugin_id
from .descriptors import _copy_descriptor_snapshot
from .models import PluginContributionSnapshot, _ActivePlugin, _CapabilityRegistration


def _build_contribution_snapshots(
    active_plugins: Mapping[str, _ActivePlugin],
    capabilities: Mapping[str, _CapabilityRegistration],
    *,
    generation: int,
) -> tuple[PluginContributionSnapshot, ...]:
    snapshots: list[PluginContributionSnapshot] = []
    for plugin_id, active in active_plugins.items():
        prompt_blocks = tuple(
            sorted(active.prompt_block_registrations, key=lambda item: item.block_id)
        )
        surfaces = {
            surface
            for capability_id in active.capability_ids
            for surface in tuple(
                getattr(
                    getattr(capabilities.get(capability_id), "descriptor", None),
                    "visible_in",
                    (),
                )
                or ()
            )
            if surface in {"desktop", "qq"}
        }
        if active.qq_command_registrations:
            surfaces.add("qq")
        if active.prompt_block_registrations or active.skill_registrations:
            surfaces.update(("desktop", "qq"))
        snapshots.append(
            PluginContributionSnapshot(
                plugin_id=_public_plugin_id(plugin_id),
                generation=generation,
                requires_services=active.requires_services,
                connections=active.connections,
                capability_ids=tuple(sorted(key for key in active.capability_ids
                    if service_method_info(capabilities[key].descriptor) is None)),
                service_methods=tuple(_copy_descriptor_snapshot(capabilities[key].descriptor)
                    for key in sorted(active.capability_ids) if service_method_info(capabilities[key].descriptor) is not None),
                qq_commands=tuple(
                    sorted(item.command for item in active.qq_command_registrations)
                ),
                event_types=tuple(
                    sorted(item.event_type for item in active.event_registrations if item.subscription is None)
                ),
                event_subscriptions=tuple(item.subscription for item in active.event_registrations if item.subscription),
                hook_types=tuple(
                    sorted(item.hook_type for item in active.hook_registrations)
                ),
                background_service_ids=tuple(
                    sorted(item.service_id for item in active.background_services)
                ),
                prompt_block_ids=tuple(item.block_id for item in prompt_blocks),
                prompt_character_count=sum(len(item.text) for item in prompt_blocks),
                skill_names=tuple(
                    sorted(item.name for item in active.skill_registrations)
                ),
                surfaces=tuple(sorted(surfaces)),
            )
        )
    snapshots.sort(key=lambda item: item.plugin_id)
    return tuple(snapshots)
