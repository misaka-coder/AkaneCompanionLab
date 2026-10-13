"""Frozen invocation scopes and shared process lifetime leases."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import MappingProxyType
from contextlib import contextmanager
from ..skill_runtime import ContributedSkillRoot
from ..plugin_invocation_scope import current_generation_scope, generation_scopes, use_generation_scopes
from .snapshot import PluginGenerationEndpoint


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
        from .runtime import PluginCapabilityBinding

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


class GenerationBindings:
    """Version scopes and process leases on the shared runtime."""

    __slots__ = ()

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
