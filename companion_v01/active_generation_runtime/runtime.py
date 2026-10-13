"""Shared active generation state and stable Bot-facing invocation bridges."""

from __future__ import annotations

import asyncio
import threading
from types import MappingProxyType
from typing import Any, Mapping
from capcore import CapabilityDescriptor, CapabilityResult, InvocationContext
from ..instance_profile import PluginSelection
from ..plugin_api import PluginHookEnvelope, PluginQQCommandResult
from ..plugin_events import PluginEventBroker
from ..plugin_hooks import PluginHookDispatchResult
from ..plugin_qq_commands import PluginQQCommandBroker
from ..plugin_invocation_scope import current_generation_scope
from .snapshot import PluginGenerationSnapshot
from .bindings import _ProcessLifetime, _CapabilityGrant
from .bindings import GenerationBindings
from .dispatch import GenerationDispatch
from .lifecycle import GenerationLifecycle


class ActivePluginGeneration(GenerationBindings, GenerationDispatch, GenerationLifecycle):
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

    def build_event_broker(self) -> "ActiveGenerationEventBroker":
        return self._event_broker

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

    def _current_snapshot(self) -> PluginGenerationSnapshot | None:
        with self._condition:
            return self._active


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
        """Stable verified artifact identity; unverified providers stay conservative."""
        return getattr(self._life.process, "implementation_revision", "") or self._life.process.generation_id

    @property
    def runtime_revision(self):
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
        from ..capcore_runtime import tool_identity_rejection

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
