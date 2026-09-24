"""Atomic publication for a complete set of isolated plugin generations.

The process client owns one plugin.  This module freezes several ready clients
into the one immutable snapshot a Bot will eventually consume.  Publication
changes one pointer; requests that already leased the previous pointer finish
before its processes are stopped.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from copy import deepcopy
from types import MappingProxyType
from typing import Any, Mapping, Protocol
from contextlib import contextmanager

from capcore import CapabilityDescriptor, CapabilityResult, InvocationContext
from akane_plugin.events import Event, EventSubscription
from akane_plugin.service_contracts import service_method_info

from .instance_profile import PluginSelection
from .plugin_api import (
    PluginHookEnvelope,
    PluginQQCommandResult,
    is_valid_plugin_id,
)
from .plugin_events import PluginEventBroker, _PluginEventRegistration
from .plugin_hooks import PluginHookDispatchResult
from .plugin_qq_commands import COMMAND_FAILURE_REPLY, PluginQQCommandBroker
from .skill_runtime import ContributedSkillRoot
from .plugin_invocation_scope import current_generation_scope, generation_scopes, use_generation_scopes
from .plugin_subprocess import drain


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


@dataclass(eq=False)
class _ProcessLifetime:
    process: PluginGenerationEndpoint
    calls: int = 0
    scopes: set = field(default_factory=set)
    withdrawn: bool = False
    retirement: asyncio.Task | None = None
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    loop: asyncio.AbstractEventLoop = field(default_factory=asyncio.get_running_loop)
    service_scope_open: bool = False


@dataclass(eq=False)
class _CapabilityGrant:
    capability_id: str
    plugin_id: str
    revoked: bool = False
    pending: dict[asyncio.Task, asyncio.AbstractEventLoop] = field(default_factory=dict)


class PluginGenerationScope:
    """Pin existing plugin implementations; newly installed plugins may join once."""

    def __init__(self, runtime, *, allow_new_plugins=True):
        self.runtime = runtime
        self.allow_new_plugins = allow_new_plugins
        self.closed = False
        self._entries = {}
        self._capabilities = {}
        self._seen_plugins = set()
        with runtime._condition:
            self.service_bindings = runtime._active.service_bindings if runtime._active is not None else MappingProxyType({})
            self._refresh()

    def _refresh(self):
        if self.closed or self.runtime._active is None or self.runtime._state != "active":
            return
        snapshot = self.runtime._active
        for process in snapshot.processes:
            if process.plugin_id in self._seen_plugins:
                continue
            self._seen_plugins.add(process.plugin_id)
            life = self.runtime._lifetimes[id(process)]
            self._entries[process.plugin_id] = life
            life.scopes.add(self)
            for key, descriptor in process.capability_descriptors.items():
                # A removed name cannot obtain fresh authority in this scope.
                self._capabilities.setdefault(key, (life, self.runtime._capability_grants[key], descriptor))

    def bindings(self):
        with self.runtime._condition:
            if self.allow_new_plugins:
                self._refresh()
            if self.closed:
                return {}
            return {key: PluginCapabilityBinding(self.runtime, grant, descriptor, life=life, scope=self)
                    for key, (life, grant, descriptor) in self._capabilities.items()}

    def processes(self):
        with self.runtime._condition:
            if self.allow_new_plugins:
                self._refresh()
            return tuple(life.process for life in self._entries.values()
                         if not life.withdrawn and life.process.running) if not self.closed else ()

    def holds(self, life):
        return not self.closed and not life.withdrawn and self._entries.get(life.process.plugin_id) is life

    def fork(self):
        with self.runtime._condition:
            child = object.__new__(PluginGenerationScope)
            child.runtime, child.closed = self.runtime, self.closed
            child.allow_new_plugins = self.allow_new_plugins
            child.service_bindings = self.service_bindings
            child._entries, child._capabilities = dict(self._entries), dict(self._capabilities)
            child._seen_plugins = set(self._seen_plugins)
            if not child.closed:
                for life in child._entries.values():
                    life.scopes.add(child)
            return child

    @contextmanager
    def activate(self):
        scopes = tuple(scope for scope in generation_scopes.get() if scope.runtime is not self.runtime)
        with use_generation_scopes((*scopes, self)):
            yield self

    def close(self):
        with self.runtime._condition:
            if self.closed:
                return
            self.closed = True
            for life in self._entries.values():
                life.scopes.discard(self)
                self.runtime._notify_lifetime(life)

    def __enter__(self):
        self._activation = self.activate()
        return self._activation.__enter__()

    def __exit__(self, *args):
        try:
            return self._activation.__exit__(*args)
        finally:
            self.close()


class PluginCapabilityBinding:
    """A host-owned invocation grant captured with its published descriptor."""

    type = "plugin"

    def __init__(self, runtime: ActivePluginGeneration, grant: _CapabilityGrant,
                 descriptor: CapabilityDescriptor, *, life: _ProcessLifetime, scope=None, owns_scope=False) -> None:
        self._runtime = runtime
        self._grant = grant
        self.descriptor = descriptor
        self._life, self._scope, self._owns_scope = life, scope, owns_scope

    @property
    def plugin_id(self):
        return self._grant.plugin_id

    @property
    def contract_revision(self):
        """Host-observed implementation identity, independent of schema shape."""
        return self._life.process.generation_id

    def is_live(self, capability_id: str = "") -> bool:
        with self._runtime._condition:
            return (capability_id == self._grant.capability_id
                    and self._runtime._binding_live(self._grant, self._life, self._scope))

    def delivery_allowed(self) -> bool:
        """Check the captured grant after its invocation scope has closed.

        Ordinary publication may retire a worker while its completed output is
        being read. Explicit removal/revocation still withdraws that output.
        This check confers no permission to make another invocation.
        """
        with self._runtime._condition:
            return self._runtime._grant_current(self._grant)

    async def invoke(self, capability_id: str, args: Mapping[str, Any], ctx: InvocationContext) -> CapabilityResult:
        if capability_id != self._grant.capability_id:
            return CapabilityResult(is_error=True, status="unavailable", reason="plugin_capability_revoked")
        from .capcore_runtime import tool_identity_rejection

        reason = tool_identity_rejection(self.descriptor, ctx)
        if reason:
            return CapabilityResult(is_error=True, status="blocked", reason=reason)
        return await self._runtime._invoke_with_grant(capability_id, args, context=ctx, grant=self._grant,
                                                     life=self._life, scope=self._scope)

    def retain(self):
        with self._runtime._condition:
            if not self._runtime._binding_live(self._grant, self._life, self._scope):
                return self
            scope = self._scope.fork() if self._scope is not None else self._runtime.freeze_invocation_scope()
            return PluginCapabilityBinding(self._runtime, self._grant, self.descriptor,
                life=self._life, scope=scope, owns_scope=True)

    def close(self):
        if self._owns_scope:
            self._scope.close()


class ActivePluginGeneration:
    """Atomically route work to one published full-plugin snapshot."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._publication_lock = asyncio.Lock()
        self._active: PluginGenerationSnapshot | None = None
        self._state = "created"
        self._generation = 0
        self._capability_grants: dict[str, _CapabilityGrant] = {}
        self._lifetimes: dict[int, _ProcessLifetime] = {}
        self._cleanup_failures: dict[int, tuple[str, str]] = {}
        self._runtime_loop: asyncio.AbstractEventLoop | None = None
        self._last_cleanup_failures: tuple[tuple[str, str], ...] = ()
        self._reconcile_failure = ""
        self._capability_revocation_listener = None
        self._task_revocation_listener = None
        self._events = PluginEventBroker(
            (), availability_provider=lambda: self.state == "active",
            registrations_provider=self._event_registrations,
            owners_provider=self._event_owners,
            runtime_loop_provider=lambda: self.runtime_loop,
            executor=self._invoke_event_registration,
        )
        self._event_broker = ActiveGenerationEventBroker(self)

    @property
    def state(self) -> str:
        with self._condition:
            return self._state

    @property
    def runtime_loop(self) -> asyncio.AbstractEventLoop | None:
        with self._condition:
            return self._runtime_loop

    @property
    def selections(self) -> tuple[PluginSelection, ...]:
        snapshot = self._current_snapshot()
        return snapshot.selections if snapshot is not None else ()

    @property
    def capability_ids(self) -> tuple[str, ...]:
        snapshot = self._current_snapshot()
        return snapshot.capability_ids if snapshot is not None else ()

    @property
    def capability_descriptors(self) -> Mapping[str, CapabilityDescriptor]:
        snapshot = self._current_snapshot()
        return snapshot.capability_descriptors if snapshot is not None else MappingProxyType({})

    def capture_capability_bindings(self) -> Mapping[str, PluginCapabilityBinding]:
        with self._condition:
            scope = current_generation_scope(self)
            if scope is not None:
                return scope.bindings()
            if self._active is None or self._state != "active":
                return {}
            return {key: PluginCapabilityBinding(self, self._capability_grants[key], descriptor,
                    life=self._lifetimes[id(self._active.process_for_capability(key))])
                    for key, descriptor in self._active.capability_descriptors.items()}

    def freeze_invocation_scope(self, *, allow_new_plugins=True):
        return PluginGenerationScope(self, allow_new_plugins=allow_new_plugins)

    def capture_service_bindings(self):
        with self._condition:
            scope = current_generation_scope(self)
            if scope is not None:
                return scope.service_bindings
            return self._active.service_bindings if self._active is not None else MappingProxyType({})

    def retained_site_dirs(self):
        """Host-only installer retention; never include these paths in status."""
        with self._condition:
            return tuple(life.process.site_dir for life in self._lifetimes.values()
                         if getattr(life.process, "site_dir", None) is not None)

    def _binding_live(self, grant, life, scope):
        return (self._grant_current(grant) and not life.withdrawn and life.process.running
                and (scope.holds(life) if scope is not None else life.retirement is None))

    def _grant_current(self, grant: _CapabilityGrant) -> bool:
        return (self._state == "active" and not grant.revoked
                and self._capability_grants.get(grant.capability_id) is grant)

    def stable_system_prompt_blocks(self) -> tuple[str, ...]:
        scope = current_generation_scope(self)
        if scope is not None:
            return tuple(block for process in scope.processes() for block in process.stable_system_prompt_blocks())
        snapshot = self._current_snapshot()
        return snapshot.stable_system_prompt_blocks() if snapshot is not None else ()

    def skill_roots(self) -> tuple[ContributedSkillRoot, ...]:
        scope = current_generation_scope(self)
        if scope is not None:
            return tuple(root for process in scope.processes() for root in process.skill_roots())
        snapshot = self._current_snapshot()
        return snapshot.skill_roots() if snapshot is not None else ()

    def build_event_broker(self) -> "ActiveGenerationEventBroker":
        return self._event_broker

    def bind_capability_revocation_listener(self, listener):
        self._capability_revocation_listener = listener

    def bind_task_revocation_listener(self, listener):
        """Bind the host-owned task authority for generation withdrawal."""

        self._task_revocation_listener = listener

    def _revoke_generation_tasks(self, processes) -> None:
        listener = self._task_revocation_listener
        if listener is None:
            return
        for process in tuple(processes):
            listener(process.plugin_id, process.generation_id)

    def _event_owners(self):
        snapshot = self._current_snapshot()
        return {process.plugin_id: process.generation_id for process in snapshot.processes} if snapshot else {}

    def _event_registrations(self):
        snapshot = self._current_snapshot()
        if snapshot is None:
            return ()
        return tuple(_PluginEventRegistration(process.plugin_id, subscription.event_type,
                                              subscription, generation_id=process.generation_id)
                     for process in snapshot.processes for subscription in getattr(process, "event_subscriptions", ()))

    async def _invoke_event_registration(self, registration, event, *, context, scope_id, delivery_id, origin_context,
                                         invocation_out=None, keep_scope_open=False):
        leased = self._lease_snapshot()
        if leased is None:
            return CapabilityResult(is_error=True, status="unavailable", reason="event_subscription_unavailable")
        record, snapshot = leased
        try:
            process = next((item for item in snapshot.processes if item.plugin_id == registration.plugin_id and
                            item.generation_id == registration.generation_id), None)
            if process is None:
                return CapabilityResult(is_error=True, status="unavailable", reason="event_subscription_unavailable")
            with self.freeze_invocation_scope():
                return await process.invoke_event(registration.subscription.subscription_id, event, context=context,
                                                  scope_id=scope_id, delivery_id=delivery_id, origin_context=origin_context)
        finally:
            self._release(record)

    def build_hook_broker(self) -> "ActiveGenerationHookBroker":
        return ActiveGenerationHookBroker(self)

    def build_qq_command_broker(
        self,
        host_registrations: tuple = (),
    ) -> "ActiveGenerationQQCommandBroker":
        return ActiveGenerationQQCommandBroker(
            self,
            host_registrations=host_registrations,
        )

    async def publish(self, candidate: PluginGenerationSnapshot) -> dict[str, Any]:
        """Publish immediately; retire old workers after their consumers finish."""

        async with self._publication_lock:
            result, cancelled = await drain(asyncio.create_task(self._publish_once(candidate)))
            if cancelled:
                raise asyncio.CancelledError()
            return result

    async def _publish_once(self, candidate: PluginGenerationSnapshot) -> dict[str, Any]:

        if not isinstance(candidate, PluginGenerationSnapshot):
            raise PluginActiveGenerationError("plugin_generation_candidate_invalid")
        if not candidate.ready:
            raise PluginActiveGenerationError("plugin_generation_not_ready")
        loop = asyncio.get_running_loop()
        with self._condition:
            if self._state in {"stopping", "stopped"}:
                raise PluginActiveGenerationError("plugin_runtime_stopped")
            previous = self._active
            candidate_generation_keys = {
                (process.plugin_id, process.generation_id)
                for process in candidate.processes
            }
            withdrawn_task_processes = (
                tuple(
                    process
                    for process in previous.processes
                    if (process.plugin_id, process.generation_id) not in candidate_generation_keys
                )
                if previous is not None
                else ()
            )
            self._revoke_generation_tasks(withdrawn_task_processes)
            if any(id(process) in self._lifetimes and self._lifetimes[id(process)].retirement is not None
                   for process in candidate.processes):
                raise PluginActiveGenerationError("plugin_generation_already_retiring")
            removed = {key for key, grant in self._capability_grants.items()
                       if grant.revoked or candidate.process_for_capability(key) is None
                       or candidate.process_for_capability(key).plugin_id != grant.plugin_id}
            for key in removed:
                grant = self._capability_grants[key]
                grant.revoked = True
                for task, task_loop in tuple(grant.pending.items()):
                    task_loop.call_soon_threadsafe(task.cancel)
            listener = getattr(self, "_capability_revocation_listener", None)
            if removed and listener is not None:
                listener(tuple(sorted(removed)))
            self._capability_grants = {
                key: self._capability_grants[key] if key in self._capability_grants and key not in removed
                else _CapabilityGrant(key, candidate.process_for_capability(key).plugin_id)
                for key in candidate.capability_ids}
            enabled_plugins = {process.plugin_id for process in candidate.processes}
            for life in self._lifetimes.values():
                if life.process.plugin_id not in enabled_plugins:
                    life.withdrawn = True
                    self._notify_lifetime(life)
            for process in candidate.processes:
                life = self._lifetimes.setdefault(id(process), _ProcessLifetime(process))
                begin_service = getattr(process, "begin_service_invocation", None)
                if life.service_scope_open is False and callable(begin_service):
                    # Give this generation's supervised services one invocation
                    # that the worker's callback lane can use for their lifetime.
                    begin_service()
                    life.service_scope_open = True
            self._active = candidate
            self._generation += 1
            self._state = "active"
            self._runtime_loop = loop
            generation = self._generation
            retained = {id(process) for process in candidate.processes}
            immediate = []
            for key, life in tuple(self._lifetimes.items()):
                if key not in retained and life.retirement is None:
                    ready = self._lifetime_ready(life)
                    life.retirement = asyncio.create_task(self._retire_lifetime(life))
                    if ready:
                        immediate.append(life.retirement)

        try:
            await self._events.reconcile()
            self._reconcile_failure = ""
        except Exception:
            # The pointer is already public. Keep its workers owned and report
            # the failure instead of treating them as a rejected candidate.
            self._reconcile_failure = "plugin_event_reconcile_failed"
        # Event reconciliation can release the last lease after the
        # retirement tasks were created. Join only lifetimes that are now at
        # a safe no-calls/no-scopes boundary; a long-running capability call
        # must still be allowed to finish without making publication block.
        with self._condition:
            newly_ready = tuple(
                life.retirement
                for key, life in self._lifetimes.items()
                if key not in retained
                and life.retirement is not None
                and self._lifetime_ready(life)
            )
        if newly_ready:
            await asyncio.shield(asyncio.gather(*newly_ready))
        if immediate:
            await asyncio.shield(asyncio.gather(*immediate))
        payload = self.status_snapshot()
        payload["generation"] = generation
        return payload

    async def invoke(
        self,
        capability_id: str,
        args: Mapping[str, Any],
        *,
        context: InvocationContext,
    ) -> CapabilityResult:
        binding = self.capture_capability_bindings().get(capability_id)
        if binding is None:
            return CapabilityResult(is_error=True, status="not_found" if self.state == "active" else "host_unavailable",
                reason="unknown_capability" if self.state == "active" else "host_unavailable")
        return await binding.invoke(capability_id, args, context)

    async def _invoke_with_grant(self, capability_id: str, args: Mapping[str, Any], *,
                                 context: InvocationContext, grant: _CapabilityGrant,
                                 life: _ProcessLifetime, scope=None) -> CapabilityResult:
        owned_scope = None
        with self._condition:
            if not self._binding_live(grant, life, scope):
                return CapabilityResult(is_error=True, status="unavailable", reason="plugin_capability_revoked")
            if scope is None:
                owned_scope = scope = self.freeze_invocation_scope()
            lease = self._lease_processes((life.process,))
            try:
                with scope.activate():
                    task = asyncio.create_task(life.process.invoke(capability_id, args, context=context))
            except BaseException:
                self._release(lease)
                if owned_scope is not None:
                    owned_scope.close()
                raise
            grant.pending[task] = asyncio.get_running_loop()
        try:
            return await task
        except asyncio.CancelledError:
            if grant.revoked and not asyncio.current_task().cancelling():
                return CapabilityResult(is_error=True, status="cancelled", reason="plugin_capability_revoked")
            raise
        finally:
            with self._condition:
                grant.pending.pop(task, None)
                self._release(lease)
                if owned_scope is not None:
                    owned_scope.close()

    async def invoke_from_consumer(
        self,
        capability_id: str,
        args: Mapping[str, Any],
        *,
        context: InvocationContext,
    ) -> CapabilityResult:
        return await self.invoke(capability_id, args, context=context)

    async def dispatch_hook(
        self,
        hook: PluginHookEnvelope,
    ) -> PluginHookDispatchResult:
        if not isinstance(hook, PluginHookEnvelope):
            return PluginHookDispatchResult(
                False,
                "invalid_hook",
                failures=(("host", "invalid_hook"),),
            )
        leased = self._lease_hook(hook.hook_type)
        if leased is None:
            return PluginHookDispatchResult(False, "host_unavailable")
        record, processes = leased
        try:
            if not processes:
                return PluginHookDispatchResult(True, "unobserved")
            results = await asyncio.gather(
                *(process.dispatch(hook) for process in processes),
                return_exceptions=True,
            )
            diagnostics: list[tuple[str, str]] = []
            failures: list[tuple[str, str]] = []
            decorations = []
            for process, result in zip(processes, results):
                if isinstance(result, BaseException):
                    failures.append((process.plugin_id, "hook_dispatch_failed"))
                    continue
                if not isinstance(result, PluginHookDispatchResult):
                    failures.append((process.plugin_id, "invalid_handler_result"))
                    continue
                diagnostics.extend(result.diagnostics)
                decorations.extend(result.outbound_decorations)
                failures.extend(
                    (process.plugin_id if owner == "host" else owner, reason)
                    for owner, reason in result.failures
                )
                if not result.ok and not result.failures:
                    failures.append((process.plugin_id, result.status or "hook_dispatch_failed"))
            return PluginHookDispatchResult(
                ok=not failures,
                status="observed" if not failures else "partially_observed",
                diagnostics=tuple(diagnostics),
                failures=tuple(failures),
                outbound_decorations=tuple(decorations),
            )
        finally:
            self._release(record)

    async def dispatch_qq_command(self, **command_args: Any) -> PluginQQCommandResult:
        command = str(command_args.get("command") or "")
        leased = self._lease_qq_command(command)
        if leased is None:
            with self._condition:
                available = self._active is not None and self._state == "active"
            if available:
                return PluginQQCommandResult(handled=False, reason="no_matching_command")
            return PluginQQCommandResult(
                handled=True,
                reply_text=COMMAND_FAILURE_REPLY,
                reason="host_unavailable",
            )
        record, process = leased
        try:
            try:
                with self.freeze_invocation_scope():
                    result = await process.dispatch_qq_command(**command_args)
            except asyncio.CancelledError:
                raise
            except Exception:
                return PluginQQCommandResult(
                    handled=True,
                    reply_text=COMMAND_FAILURE_REPLY,
                    reason="plugin_command_dispatch_failed",
                )
            if not isinstance(result, PluginQQCommandResult):
                return PluginQQCommandResult(
                    handled=True,
                    reply_text=COMMAND_FAILURE_REPLY,
                    reason="invalid_handler_result",
                )
            return result
        finally:
            self._release(record)

    async def stop(self) -> dict[str, Any]:
        async with self._publication_lock:
            result, cancelled = await drain(asyncio.create_task(self._stop_once()))
            if cancelled:
                raise asyncio.CancelledError()
            return result

    async def _stop_once(self) -> dict[str, Any]:
        with self._condition:
            if self._state == "stopped":
                return self.status_snapshot()
            previous = self._active
            self._revoke_generation_tasks(previous.processes if previous is not None else ())
            self._active = None
            self._state = "stopping"
            for grant in self._capability_grants.values():
                grant.revoked = True
                for task, loop in tuple(grant.pending.items()):
                    loop.call_soon_threadsafe(task.cancel)
            for life in self._lifetimes.values():
                life.withdrawn = True
                self._notify_lifetime(life)
                if life.retirement is None:
                    life.retirement = asyncio.create_task(self._retire_lifetime(life))
        try:
            await self._events.reconcile()
        except Exception:
            self._reconcile_failure = "plugin_event_reconcile_failed"
        await self.drain_retired()
        with self._condition:
            self._state = "stopped"
            self._runtime_loop = None
        return self.status_snapshot()

    def status_snapshot(self) -> dict[str, Any]:
        with self._condition:
            snapshot = self._active
            state = self._state
            generation = self._generation
            retiring_count = sum(life.retirement is not None and not life.retirement.done()
                                 for life in self._lifetimes.values())
            failures = self._last_cleanup_failures
        selections = snapshot.selections if snapshot is not None else ()
        active_ids = {
            process.plugin_id for process in snapshot.processes
        } if snapshot is not None else set()
        active_statuses = {
            process.plugin_id: process.public_status_snapshot()
            for process in snapshot.processes
        } if snapshot is not None else {}
        unavailable = any(
            str(item.get("status") or "") not in {"active", "degraded"}
            for item in active_statuses.values()
        )
        public_state = "degraded" if state == "active" and unavailable else state
        reason = ""
        if unavailable:
            reason = "plugin_runtime_failed"
        elif self._reconcile_failure:
            reason = self._reconcile_failure
        elif failures:
            reason = "old_generation_stop_failed"
        elif snapshot is not None and snapshot.waiting:
            reason = "service_dependencies_pending"
        return {
            "ok": public_state == "active" and not failures and not self._reconcile_failure,
            "status": public_state,
            "reason": reason,
            "generation": generation,
            "cleanup_status": "draining" if retiring_count else "failed" if failures else "completed",
            "retiring_process_count": retiring_count,
            "configured_plugin_count": len(selections),
            "plugin_count": len(active_ids),
            **({"service_bindings": [{"service_id": key[0], "version": key[1], "plugin_id": value}
                for key, value in sorted(snapshot.service_bindings.items())]} if snapshot is not None and snapshot.service_bindings else {}),
            **({"waiting_dependency_count": len(snapshot.waiting)} if snapshot is not None and snapshot.waiting else {}),
            "capability_count": sum(service_method_info(item) is None for item in snapshot.capability_descriptors.values())
                if snapshot is not None else 0,
            "prompt_block_count": len(snapshot.stable_system_prompt_blocks()) if snapshot is not None else 0,
            "event_handler_count": sum(
                len(process.registered_event_types) + len(getattr(process, "event_subscriptions", ())) for process in snapshot.processes
            ) if snapshot is not None else 0,
            "event_runtime": self._events.status_snapshot(),
            "hook_handler_count": sum(
                len(process.registered_hook_types) for process in snapshot.processes
            ) if snapshot is not None else 0,
            "skill_count": len(snapshot.skill_roots()) if snapshot is not None else 0,
            "background_service_count": len(snapshot.registered_background_service_ids) if snapshot is not None else 0,
            "cleanup_failures": [
                {"plugin_id": plugin_id, "reason": reason}
                for plugin_id, reason in failures
            ],
            "plugins": [
                dict(active_statuses[item.plugin_id])
                if item.plugin_id in active_ids
                else deepcopy(snapshot.waiting[item.plugin_id]) if snapshot is not None and item.plugin_id in snapshot.waiting
                else {
                    "plugin_id": item.plugin_id,
                    "enabled": False,
                    "status": "disabled",
                    "reason": "instance_disabled",
                }
                for item in selections
            ],
        }

    def _current_snapshot(self) -> PluginGenerationSnapshot | None:
        with self._condition:
            return self._active

    def _lease_processes(self, processes):
        lease = tuple(self._lifetimes[id(process)] for process in processes)
        for life in lease:
            life.calls += 1
        return lease

    def _lease_snapshot(self):
        with self._condition:
            record = self._active
            if record is None or self._state != "active":
                return None
            return self._lease_processes(record.processes), record

    def _lease_hook(self, hook_type):
        with self._condition:
            if self._active is None or self._state != "active":
                return None
            scope = current_generation_scope(self)
            processes = scope.processes() if scope is not None else self._active.processes
            selected = tuple(process for process in processes if hook_type in process.registered_hook_types)
            return self._lease_processes(selected), selected

    def _lease_qq_command(self, command):
        with self._condition:
            record = self._active
            if record is None or self._state != "active":
                return None
            process = record.process_for_qq_command(command)
            if process is None:
                return None
            return self._lease_processes((process,)), process

    def _release(self, lease):
        with self._condition:
            for life in lease:
                life.calls -= 1
                self._notify_lifetime(life)

    def _notify_lifetime(self, life):
        if not life.loop.is_closed():
            life.loop.call_soon_threadsafe(life.changed.set)

    def _lifetime_ready(self, life):
        return life.calls == 0 and not any(scope.holds(life) for scope in life.scopes)

    async def _retire_lifetime(self, life):
        while True:
            with self._condition:
                if self._lifetime_ready(life):
                    break
                life.changed.clear()
            await life.changed.wait()
        try:
            result = await asyncio.to_thread(life.process.stop)
            reason = "plugin_generation_stop_failed"
            if isinstance(result, Mapping):
                reason = "" if result.get("ok") else str(result.get("reason") or reason)
        except Exception:
            reason = "plugin_generation_stop_failed"
        if life.service_scope_open:
            end_service = getattr(life.process, "end_service_invocation", None)
            if callable(end_service):
                try:
                    await end_service()
                except Exception:
                    pass
            life.service_scope_open = False
        with self._condition:
            if reason:
                self._cleanup_failures[id(life.process)] = (life.process.plugin_id, reason)
            else:
                self._lifetimes.pop(id(life.process), None)
            self._last_cleanup_failures = tuple(self._cleanup_failures.values())
        return reason

    async def drain_retired(self):
        with self._condition:
            pending = tuple(life.retirement for life in self._lifetimes.values() if life.retirement is not None)
        if pending:
            await asyncio.shield(asyncio.gather(*pending))
        return self.status_snapshot()


class ActiveGenerationEventBroker:
    """Stable Bot-facing event broker backed by the current atomic snapshot."""

    def __init__(self, runtime: ActivePluginGeneration) -> None:
        self._runtime = runtime

    @property
    def registered_event_types(self) -> tuple[str, ...]:
        return self._runtime._events.registered_event_types

    def observes(self, event_type: str) -> bool:
        return str(event_type or "").strip().lower() in self.registered_event_types

    async def emit(self, event_type, data, **kwargs):
        return await self._runtime._events.emit(event_type, data, **kwargs)

    async def receipt(self, dispatch_id):
        return await self._runtime._events.receipt(dispatch_id)

    def bind_turn_router(self, router):
        self._runtime._events.bind_turn_router(router)

    def bind_timeline_recorder(self, recorder):
        self._runtime._events.bind_timeline_recorder(recorder)

    def generation_active(self, plugin_id, generation_id):
        return self._runtime._events.generation_active(plugin_id, generation_id)

    def initialize_scope(self, invocation):
        self._runtime._events.initialize_scope(invocation)

    @property
    def observations(self):
        return self._runtime._events.observations

    async def request(self, operation, payload, *, invocation):
        return await self._runtime._events.request(operation, payload, invocation=invocation)


class ActiveGenerationHookBroker:
    """Stable synchronous/async Hook boundary across atomic generation swaps."""

    def __init__(self, runtime: ActivePluginGeneration) -> None:
        self._runtime = runtime

    @property
    def registered_hook_types(self) -> tuple[str, ...]:
        snapshot = self._runtime._current_snapshot()
        return snapshot.registered_hook_types if snapshot is not None else ()

    def observes(self, hook_type: str) -> bool:
        return str(hook_type or "").strip().lower() in self.registered_hook_types

    async def dispatch(self, hook: PluginHookEnvelope) -> PluginHookDispatchResult:
        return await self._runtime.dispatch_hook(hook)

    def dispatch_from_consumer(self, hook: PluginHookEnvelope) -> PluginHookDispatchResult:
        loop = self._runtime.runtime_loop
        if loop is None or loop.is_closed() or not loop.is_running():
            return PluginHookDispatchResult(False, "host_unavailable")
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is loop:
            return PluginHookDispatchResult(
                False,
                "wrong_execution_context",
                failures=(("host", "hook_requires_worker_thread"),),
            )
        try:
            future = asyncio.run_coroutine_threadsafe(self.dispatch(hook), loop)
            return future.result()
        except Exception:
            return PluginHookDispatchResult(
                False,
                "dispatch_failed",
                failures=(("host", "dispatch_failed"),),
            )


class ActiveGenerationQQCommandBroker:
    """Compose host commands with the current full plugin generation."""

    def __init__(
        self,
        runtime: ActivePluginGeneration,
        *,
        host_registrations: tuple = (),
    ) -> None:
        self._runtime = runtime
        self._host_broker = PluginQQCommandBroker(
            (),
            host_registrations=tuple(host_registrations or ()),
        )

    @property
    def registered_commands(self) -> tuple[str, ...]:
        snapshot = self._runtime._current_snapshot()
        plugin_commands = snapshot.registered_qq_commands if snapshot is not None else ()
        return tuple(dict.fromkeys((*self._host_broker.registered_commands, *plugin_commands)))

    def handles(self, command: str) -> bool:
        normalized = str(command or "").strip().lower()
        return self._host_broker.handles(normalized) or normalized in self.registered_commands

    async def dispatch(self, **command_args: Any) -> PluginQQCommandResult:
        command = str(command_args.get("command") or "")
        if self._host_broker.handles(command):
            return await self._host_broker.dispatch(**command_args)
        return await self._runtime.dispatch_qq_command(**command_args)


__all__ = [
    "ActiveGenerationEventBroker",
    "ActiveGenerationHookBroker",
    "ActiveGenerationQQCommandBroker",
    "ActivePluginGeneration",
    "PluginActiveGenerationError",
    "PluginGenerationEndpoint",
    "PluginGenerationSnapshot",
]
