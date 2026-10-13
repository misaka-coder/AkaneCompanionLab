"""Path-free host status and detached generation contribution views."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Mapping
import asyncio

from capcore import CapabilityDescriptor

from ..instance_profile import PluginSelection
from ..plugin_hooks import SUPPORTED_HOOK_TYPES
from ..skill_runtime import ContributedSkillRoot

from .constants import (
    _HOST_AVAILABLE_STATES,
    _MAX_ADAPTERS_PER_PLUGIN,
    _MAX_BACKGROUND_SERVICE_ID_CHARS,
    _MAX_CAPABILITIES_PER_PLUGIN,
    _MAX_PERMISSIONS_PER_PLUGIN,
    _MAX_PROMPT_BLOCKS_PER_PLUGIN,
    _MAX_PROMPT_BLOCK_CHARS,
    _MAX_PROMPT_BLOCK_TOTAL_CHARS,
    _MAX_QQ_COMMANDS_PER_PLUGIN,
    _public_plugin_id,
)
from .descriptors import _copy_descriptor_snapshot
from .models import PluginContributionSnapshot


class PluginSnapshotMixin:
    @property
    def state(self) -> str:
        return self._state

    @property
    def selections(self) -> tuple[PluginSelection, ...]:
        return tuple(self._selections)

    @property
    def runtime_loop(self) -> asyncio.AbstractEventLoop | None:
        return self._runtime_loop

    @property
    def capability_ids(self) -> tuple[str, ...]:
        return tuple(self._capabilities)

    @property
    def capability_owners(self) -> Mapping[str, str]:
        return MappingProxyType({key: item.plugin_id for key, item in self._capabilities.items()})

    @property
    def capability_descriptors(self) -> Mapping[str, CapabilityDescriptor]:
        """Return an immutable point-in-time view without exposing adapters."""

        return MappingProxyType(
            {
                capability_id: _copy_descriptor_snapshot(registration.descriptor)
                for capability_id, registration in self._capabilities.items()
            }
        )

    @property
    def contribution_snapshots(self) -> tuple[PluginContributionSnapshot, ...]:
        """Return the immutable contribution inventory for this generation."""

        return self._contribution_snapshots

    def stable_system_prompt_blocks(self) -> tuple[str, ...]:
        """Return the active restart-only prompt snapshot in stable key order."""

        registrations = [
            (plugin_id, registration.block_id, registration.text)
            for plugin_id, active in self._active_plugins.items()
            for registration in active.prompt_block_registrations
        ]
        registrations.sort(key=lambda item: (item[0], item[1]))
        return tuple(text for _plugin_id, _block_id, text in registrations)

    def skill_roots(self) -> tuple[ContributedSkillRoot, ...]:
        """Return the active generation's immutable plugin Skill mounts."""

        roots = [
            ContributedSkillRoot(
                source=f"plugin:{plugin_id}",
                root=registration.root,
                mount_name=registration.mount_name,
            )
            for plugin_id, active in self._active_plugins.items()
            for registration in active.skill_registrations
        ]
        roots.sort(key=lambda item: (item.source, item.root.name.casefold()))
        return tuple(roots)

    def status_snapshot(self) -> dict[str, Any]:
        reason = ""
        if self._state == "degraded":
            reason = (
                "plugin_runtime_failed"
                if any(
                    item.get("status") in {"degraded", "failed"}
                    for item in self._background_service_statuses.values()
                )
                else "plugin_activation_failed"
            )
        elif self._state not in _HOST_AVAILABLE_STATES:
            reason = "host_unavailable"
        background_service_statuses = [
            {
                "plugin_id": _public_plugin_id(plugin_id),
                "service_id": service_id,
                "status": item.get("status", "unknown"),
                "reason": item.get("reason", ""),
            }
            for (plugin_id, service_id), item in sorted(self._background_service_statuses.items())
        ]
        contribution_by_plugin = {
            item.plugin_id: item for item in self._contribution_snapshots
        }
        plugin_statuses: list[dict[str, Any]] = []
        for status in self._plugin_statuses:
            payload = status.as_dict()
            contribution = contribution_by_plugin.get(status.plugin_id)
            if contribution is not None:
                payload["contribution_snapshot"] = contribution.as_dict()
            plugin_statuses.append(payload)
        return {
            "ok": self._state == "active",
            "status": self._state,
            "reason": reason,
            "contribution_policy": self._contribution_policy_id,
            "generation": self._generation,
            "configured_plugin_count": len(self._plugin_statuses),
            "plugin_count": len(self._active_plugins),
            "capability_count": sum(
                len(item.capability_ids) for item in self._contribution_snapshots
            ),
            "prompt_block_count": sum(
                len(item.prompt_block_ids) for item in self._contribution_snapshots
            ),
            "event_handler_count": sum(
                len(item.event_types) + len(item.event_subscriptions) for item in self._contribution_snapshots
            ),
            **({"event_runtime": self._event_broker.status_snapshot()} if self._event_broker is not None else {}),
            "hook_handler_count": sum(
                len(item.hook_types) for item in self._contribution_snapshots
            ),
            "skill_count": sum(
                len(item.skill_names) for item in self._contribution_snapshots
            ),
            "hook_runtime": (
                self._hook_broker.status_snapshot()
                if self._hook_broker is not None
                else {
                    "dispatch_count": 0,
                    "dispatch_failure_count": 0,
                    "diagnostic_count": 0,
                    "last_diagnostics": [],
                    "last_failures": [],
                }
            ),
            "background_service_count": len(self._background_service_statuses),
            "running_background_service_count": sum(
                1
                for item in self._background_service_statuses.values()
                if item.get("status") == "running"
            ),
            "background_service_stop_failure_count": self._background_service_stop_failure_count,
            "background_services": background_service_statuses,
            "close_failure_count": self._close_failure_count,
            "contract": {
                "supported_hook_types": sorted(SUPPORTED_HOOK_TYPES),
                "registration_limits": {
                    "adapters_per_plugin": _MAX_ADAPTERS_PER_PLUGIN,
                    "capabilities_per_plugin": _MAX_CAPABILITIES_PER_PLUGIN,
                    "qq_commands_per_plugin": _MAX_QQ_COMMANDS_PER_PLUGIN,
                    "prompt_blocks_per_plugin": _MAX_PROMPT_BLOCKS_PER_PLUGIN,
                    "prompt_block_chars": _MAX_PROMPT_BLOCK_CHARS,
                    "prompt_block_total_chars": _MAX_PROMPT_BLOCK_TOTAL_CHARS,
                    "background_service_id_chars": _MAX_BACKGROUND_SERVICE_ID_CHARS,
                    "permissions_per_plugin": _MAX_PERMISSIONS_PER_PLUGIN,
                },
                "timeouts": {
                    "activation_step_seconds": self._activation_timeout_seconds,
                    "invoke_seconds": self._invoke_timeout_seconds,
                    "managed_artifact_seconds": self._managed_artifact_timeout_seconds,
                    "adapter_close_seconds": self._close_timeout_seconds,
                    "background_service_stop_seconds": self._background_service_stop_timeout_seconds,
                    "legacy_event_handler_seconds": self._event_handler_timeout_seconds,
                    "hook_handler_seconds": self._hook_handler_timeout_seconds,
                },
            },
            "plugins": plugin_statuses,
        }
