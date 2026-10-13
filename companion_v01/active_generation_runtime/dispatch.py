"""Leased capability, event, Hook and QQ invocation paths."""

from __future__ import annotations

import asyncio
from typing import Any, Mapping
from capcore import CapabilityResult, InvocationContext
from ..plugin_api import PluginHookEnvelope, PluginQQCommandResult
from ..plugin_events import _PluginEventRegistration
from ..plugin_hooks import PluginHookDispatchResult
from ..plugin_qq_commands import COMMAND_FAILURE_REPLY
from .bindings import _ProcessLifetime, _CapabilityGrant


class GenerationDispatch:
    """Capability, event, Hook and QQ dispatch using runtime leases."""

    __slots__ = ()

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
