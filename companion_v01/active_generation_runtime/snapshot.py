"""Validated full-Bot generation snapshots and endpoint contracts."""

from __future__ import annotations

from copy import deepcopy
from types import MappingProxyType
from typing import Any, Mapping, Protocol
from capcore import CapabilityDescriptor, CapabilityResult, InvocationContext
from akane_plugin.events import Event, EventSubscription
from ..instance_profile import PluginSelection
from ..plugin_api import PluginHookEnvelope, PluginQQCommandResult, is_valid_plugin_id
from ..plugin_hooks import PluginHookDispatchResult
from ..skill_runtime import ContributedSkillRoot


class PluginActiveGenerationError(RuntimeError):
    """A candidate cannot become the active plugin generation."""

    def __init__(self, reason: str) -> None:
        self.reason = str(reason or "plugin_active_generation_invalid")
        super().__init__(self.reason)


class PluginGenerationEndpoint(Protocol):
    """Public parent-side surface of one ready plugin process."""

    plugin_id: str
    generation_id: str

    def prepare(self) -> dict[str, Any]: ...

    def activate(self) -> dict[str, Any]: ...

    @property
    def running(self) -> bool: ...

    @property
    def capability_descriptors(self) -> Mapping[str, CapabilityDescriptor]: ...

    @property
    def registered_event_types(self) -> tuple[str, ...]: ...

    @property
    def event_subscriptions(self) -> tuple[EventSubscription, ...]: ...

    @property
    def registered_hook_types(self) -> tuple[str, ...]: ...

    @property
    def registered_background_service_ids(self) -> tuple[str, ...]: ...

    @property
    def registered_qq_commands(self) -> tuple[str, ...]: ...

    def public_status_snapshot(self) -> dict[str, Any]: ...

    def stable_system_prompt_blocks(self) -> tuple[str, ...]: ...

    def skill_roots(self) -> tuple[ContributedSkillRoot, ...]: ...

    async def invoke(
        self,
        capability_id: str,
        args: Mapping[str, Any],
        *,
        context: InvocationContext,
    ) -> CapabilityResult: ...

    async def dispatch(
        self,
        hook: PluginHookEnvelope,
    ) -> PluginHookDispatchResult: ...

    async def invoke_event(self, subscription_id: str, event: Event, *, context: InvocationContext,
                           scope_id: str, delivery_id: str, origin_context: InvocationContext | None = None) -> CapabilityResult: ...

    async def dispatch_qq_command(self, **command_args: Any) -> PluginQQCommandResult: ...

    def stop(self) -> dict[str, Any]: ...

    def bind_tasks_provider(self, provider: Any) -> None: ...


class PluginGenerationSnapshot:
    """One validated and immutable full-Bot plugin snapshot."""

    def __init__(
        self,
        selections: tuple[PluginSelection, ...],
        processes: tuple[PluginGenerationEndpoint, ...],
        *, waiting=None, service_bindings=None,
    ) -> None:
        if not isinstance(selections, tuple) or any(
            not isinstance(item, PluginSelection)
            or not is_valid_plugin_id(item.plugin_id)
            or not isinstance(item.enabled, bool)
            for item in selections
        ):
            raise PluginActiveGenerationError("plugin_generation_selections_invalid")
        selection_ids = tuple(item.plugin_id for item in selections)
        if len(set(selection_ids)) != len(selection_ids):
            raise PluginActiveGenerationError("duplicate_plugin_selection")

        process_by_plugin: dict[str, PluginGenerationEndpoint] = {}
        for process in tuple(processes):
            plugin_id = str(getattr(process, "plugin_id", "") or "").strip()
            if not is_valid_plugin_id(plugin_id):
                raise PluginActiveGenerationError("plugin_generation_process_invalid")
            if plugin_id in process_by_plugin:
                raise PluginActiveGenerationError("duplicate_plugin_generation")
            if not bool(getattr(process, "running", False)):
                raise PluginActiveGenerationError("plugin_generation_not_ready")
            process_by_plugin[plugin_id] = process

        expected_enabled = {item.plugin_id for item in selections if item.enabled}
        waiting = dict(waiting or {})
        if any(not isinstance(value, dict) or value.get("status") != "waiting_dependency"
               or value.get("enabled") is not True or value.get("plugin_id") != key for key, value in waiting.items()):
            raise PluginActiveGenerationError("plugin_generation_waiting_invalid")
        if set(process_by_plugin).intersection(waiting) or set(process_by_plugin).union(waiting) != expected_enabled:
            raise PluginActiveGenerationError("plugin_generation_selection_mismatch")

        capabilities: dict[str, CapabilityDescriptor] = {}
        capability_owners: dict[str, PluginGenerationEndpoint] = {}
        command_owners: dict[str, PluginGenerationEndpoint] = {}
        skill_names: set[str] = set()
        skill_roots: list[ContributedSkillRoot] = []
        for plugin_id in sorted(process_by_plugin):
            process = process_by_plugin[plugin_id]
            for capability_id, descriptor in process.capability_descriptors.items():
                if not isinstance(descriptor, CapabilityDescriptor) or descriptor.id != capability_id:
                    raise PluginActiveGenerationError("plugin_generation_capability_invalid")
                if capability_id in capability_owners:
                    raise PluginActiveGenerationError("duplicate_plugin_capability")
                capabilities[capability_id] = descriptor
                capability_owners[capability_id] = process
            for command in process.registered_qq_commands:
                normalized = str(command or "").strip().lower()
                if not normalized or normalized in command_owners:
                    raise PluginActiveGenerationError("duplicate_plugin_qq_command")
                command_owners[normalized] = process
            for root in process.skill_roots():
                if not isinstance(root, ContributedSkillRoot):
                    raise PluginActiveGenerationError("plugin_generation_skill_invalid")
                name = root.root.name
                if name in skill_names:
                    raise PluginActiveGenerationError("duplicate_plugin_skill")
                skill_names.add(name)
                skill_roots.append(root)

        self._selections = tuple(selections)
        self.waiting = MappingProxyType(deepcopy(waiting))
        self.service_bindings = MappingProxyType(dict(service_bindings or {}))
        self._processes = tuple(process_by_plugin[key] for key in sorted(process_by_plugin))
        self._process_by_plugin = MappingProxyType(dict(process_by_plugin))
        self._capabilities = MappingProxyType(dict(sorted(capabilities.items())))
        self._capability_owners = MappingProxyType(dict(capability_owners))
        self._command_owners = MappingProxyType(dict(command_owners))
        self._skill_roots = tuple(
            sorted(skill_roots, key=lambda item: (item.source, item.root.name.casefold()))
        )

    @property
    def selections(self) -> tuple[PluginSelection, ...]:
        return self._selections

    @property
    def processes(self) -> tuple[PluginGenerationEndpoint, ...]:
        return self._processes

    @property
    def ready(self) -> bool:
        return all(process.running for process in self._processes)

    @property
    def capability_descriptors(self) -> Mapping[str, CapabilityDescriptor]:
        return self._capabilities

    @property
    def capability_ids(self) -> tuple[str, ...]:
        return tuple(self._capabilities)

    @property
    def registered_event_types(self) -> tuple[str, ...]:
        return tuple(
            sorted({item for process in self._processes for item in process.registered_event_types})
        )

    @property
    def registered_hook_types(self) -> tuple[str, ...]:
        return tuple(
            sorted({item for process in self._processes for item in process.registered_hook_types})
        )

    @property
    def registered_background_service_ids(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (process.plugin_id, service_id)
            for process in self._processes
            for service_id in process.registered_background_service_ids
        )

    @property
    def registered_qq_commands(self) -> tuple[str, ...]:
        return tuple(sorted(self._command_owners))

    def stable_system_prompt_blocks(self) -> tuple[str, ...]:
        return tuple(
            block
            for process in self._processes
            for block in process.stable_system_prompt_blocks()
        )

    def skill_roots(self) -> tuple[ContributedSkillRoot, ...]:
        return self._skill_roots

    def process_for_capability(self, capability_id: str) -> PluginGenerationEndpoint | None:
        return self._capability_owners.get(str(capability_id or ""))

    def process_for_qq_command(self, command: str) -> PluginGenerationEndpoint | None:
        return self._command_owners.get(str(command or "").strip().lower())

    def processes_for_hook(self, hook_type: str) -> tuple[PluginGenerationEndpoint, ...]:
        normalized = str(hook_type or "").strip().lower()
        return tuple(
            process for process in self._processes if normalized in process.registered_hook_types
        )
