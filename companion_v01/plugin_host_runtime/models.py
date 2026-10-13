"""Immutable host status, contributions and internal registrations."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from akane_plugin.events import EventSubscription
from akane_plugin.service_contracts import service_catalog
from capcore import CapabilityAdapter, CapabilityDescriptor, CapabilityToolSpec

from ..plugin_events import _PluginEventRegistration
from ..plugin_hooks import _PluginHookRegistration
from ..plugin_qq_commands import _PluginCommandRegistration


@dataclass(frozen=True, slots=True)
class PluginStatus:
    plugin_id: str
    enabled: bool
    status: str
    reason: str = ""
    plugin_version: str = ""
    stage: str = ""
    permissions: tuple[str, ...] = ()
    capability_ids: tuple[str, ...] = ()
    qq_commands: tuple[str, ...] = ()
    event_types: tuple[str, ...] = ()
    hook_types: tuple[str, ...] = ()
    skill_names: tuple[str, ...] = ()
    schema_errors: tuple[Mapping[str, str], ...] = ()

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
        if self.schema_errors:
            payload["schema_errors"] = [dict(error) for error in self.schema_errors]
        if self.permissions:
            payload["permissions"] = list(self.permissions)
        if self.capability_ids:
            payload["capability_ids"] = list(self.capability_ids)
        if self.qq_commands:
            payload["qq_commands"] = list(self.qq_commands)
        if self.event_types:
            payload["event_types"] = list(self.event_types)
        if self.hook_types:
            payload["hook_types"] = list(self.hook_types)
        if self.skill_names:
            payload["skill_names"] = list(self.skill_names)
        return payload


@dataclass(frozen=True, slots=True)
class PluginContributionSnapshot:
    """Immutable inventory of one active plugin's real host contributions.

    Only contribution kinds that PluginHost can execute today are listed.
    Providers and UI pages are added only when their runtime
    contracts exist; empty future placeholders are never advertised.
    """

    plugin_id: str
    generation: int
    capability_ids: tuple[str, ...] = ()
    qq_commands: tuple[str, ...] = ()
    event_types: tuple[str, ...] = ()
    hook_types: tuple[str, ...] = ()
    background_service_ids: tuple[str, ...] = ()
    prompt_block_ids: tuple[str, ...] = ()
    skill_names: tuple[str, ...] = ()
    surfaces: tuple[str, ...] = ()
    prompt_character_count: int = 0
    event_subscriptions: tuple[EventSubscription, ...] = ()
    service_methods: tuple[CapabilityDescriptor, ...] = ()
    requires_services: tuple = ()
    connections: tuple = ()

    @property
    def contribution_types(self) -> tuple[str, ...]:
        kinds: list[str] = []
        if self.capability_ids:
            kinds.append("capabilities")
        if self.service_methods:
            kinds.append("services")
        if self.connections:
            kinds.append("connections")
        if self.qq_commands:
            kinds.append("commands")
        if self.event_subscriptions:
            kinds.append("event_handlers")
        if self.hook_types:
            kinds.append("hooks")
        if self.background_service_ids:
            kinds.append("background_services")
        if self.prompt_block_ids:
            kinds.append("prompt_blocks")
        if self.skill_names:
            kinds.append("skills")
        return tuple(kinds)

    def as_dict(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "generation": self.generation,
            "types": list(self.contribution_types),
            "capabilities": list(self.capability_ids),
            **({"connections": [spec.as_dict() for spec in self.connections]} if self.connections else {}),
            **({"services": service_catalog(self.service_methods)} if self.service_methods else {}),
            **({"requires_services": [{"service_id": item.service_id, "version": item.version}
                for item in self.requires_services]} if self.requires_services else {}),
            "commands": list(self.qq_commands),
            "event_handlers": list(self.event_types),
            **({"event_subscriptions": [
                {"subscription_id": item.subscription_id, "event_type": item.event_type, "sources": list(item.sources),
                 "scope": item.scope, "coalesce": item.coalesce, "persistence": item.persistence,
                 "request_turn": item.request_turn}
                for item in self.event_subscriptions
            ]} if self.event_subscriptions else {}),
            "hooks": list(self.hook_types),
            "background_services": list(self.background_service_ids),
            "prompt_blocks": list(self.prompt_block_ids),
            "skills": list(self.skill_names),
            "surfaces": list(self.surfaces),
            "prompt_character_count": self.prompt_character_count,
        }


@dataclass(frozen=True, slots=True)
class _CapabilityRegistration:
    plugin_id: str
    adapter: CapabilityAdapter
    descriptor: CapabilityDescriptor
    permissions: tuple[str, ...]
    tool_spec: CapabilityToolSpec
    private_result_values: set[str] = field(default_factory=set, repr=False, compare=False)
    connections: tuple = ()


@dataclass(frozen=True, slots=True)
class _PromptBlockRegistration:
    block_id: str
    text: str


@dataclass(frozen=True, slots=True)
class _SkillRegistration:
    name: str
    root: Path
    mount_name: str


@dataclass(frozen=True, slots=True)
class _BackgroundServiceRegistration:
    service_id: str
    service: Any  # PluginBackgroundJob


@dataclass(frozen=True, slots=True)
class _ActivePlugin:
    plugin: Any
    adapters: tuple[CapabilityAdapter, ...]
    capability_ids: tuple[str, ...]
    requires_services: tuple = ()
    connections: tuple = ()
    background_services: tuple[_BackgroundServiceRegistration, ...] = ()
    qq_command_registrations: tuple[_PluginCommandRegistration, ...] = ()
    event_registrations: tuple[_PluginEventRegistration, ...] = ()
    hook_registrations: tuple[_PluginHookRegistration, ...] = ()
    prompt_block_registrations: tuple[_PromptBlockRegistration, ...] = ()
    skill_registrations: tuple[_SkillRegistration, ...] = ()


class _ActivationFailure(RuntimeError):
    def __init__(self, reason: str, *, status: str = "failed", stage: str = "", schema_errors=()) -> None:
        self.reason = reason
        self.status = status
        self.stage = stage
        self.schema_errors = tuple(schema_errors)
        super().__init__(reason)
