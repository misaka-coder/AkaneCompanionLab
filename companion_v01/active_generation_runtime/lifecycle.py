"""Atomic generation publication, withdrawal and cleanup status."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any, Mapping
from akane_plugin.service_contracts import service_method_info
from ..plugin_subprocess import drain
from .snapshot import PluginActiveGenerationError, PluginGenerationSnapshot
from .bindings import _ProcessLifetime, _CapabilityGrant


class GenerationLifecycle:
    """Atomic publication, withdrawal and retired-worker cleanup."""

    __slots__ = ()

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
        opened = []
        published = False
        try:
            with self._condition:
                if self._state in {"stopping", "stopped"}:
                    raise PluginActiveGenerationError("plugin_runtime_stopped")
                if any(id(process) in self._lifetimes and (
                    self._lifetimes[id(process)].retirement is not None
                    or self._lifetimes[id(process)].withdrawn
                ) for process in candidate.processes):
                    raise PluginActiveGenerationError("plugin_generation_already_retiring")
                # Prepare every new service scope before withdrawing any active
                # task or capability authority. Keep tentative lifetimes local.
                prepared = self._prepare_lifetimes(candidate, opened)
                if not candidate.ready:
                    raise PluginActiveGenerationError("plugin_generation_not_ready")
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
                self._lifetimes.update(prepared)
                self._active = candidate
                published = True
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
        except BaseException:
            if not published:
                await self._rollback_service_scopes(opened)
            raise

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

    def _notify_lifetime(self, life):
        if not life.loop.is_closed():
            life.loop.call_soon_threadsafe(life.changed.set)

    def _lifetime_ready(self, life):
        return life.calls == 0 and not any(scope.holds(life) for scope in life.scopes)

    def _prepare_lifetimes(self, candidate, opened):
        prepared = {}
        for process in candidate.processes:
            key = id(process)
            life = self._lifetimes.get(key)
            if life is None:
                life = _ProcessLifetime(process)
            prepared[key] = life
            begin_service = getattr(process, "begin_service_invocation", None)
            if not life.service_scope_open and callable(begin_service):
                # Begin may fail after opening resources; include that attempt
                # in rollback without closing an existing generation's scope.
                opened.append(life)
                life.service_scope_open = True
                begin_service()
        return prepared

    async def _close_service_scope(self, life):
        if not life.service_scope_open:
            return ""
        try:
            end_service = getattr(life.process, "end_service_invocation", None)
            if not callable(end_service):
                return "plugin_service_scope_close_failed"
            await end_service()
        except (Exception, asyncio.CancelledError):
            return "plugin_service_scope_close_failed"
        life.service_scope_open = False
        return ""

    async def _rollback_service_scopes(self, opened):
        for life in reversed(opened):
            reason = await self._close_service_scope(life)
            if reason:
                with self._condition:
                    key = id(life.process)
                    if key not in self._lifetimes:
                        life.withdrawn = True
                        self._lifetimes[key] = life
                    # Retain unconfirmed resources for later retirement; never
                    # replace the original publication exception with cleanup.
                    self._cleanup_failures[key] = (life.process.plugin_id, reason)
                    self._last_cleanup_failures = tuple(self._cleanup_failures.values())

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
                if not reason and (result.get("status") != "stopped" or life.process.running):
                    reason = "plugin_generation_stop_incomplete"
        except Exception:
            reason = "plugin_generation_stop_failed"
        scope_failure = await self._close_service_scope(life)
        reason = reason or scope_failure
        with self._condition:
            if reason:
                self._cleanup_failures[id(life.process)] = (life.process.plugin_id, reason)
            else:
                self._cleanup_failures.pop(id(life.process), None)
                self._lifetimes.pop(id(life.process), None)
            self._last_cleanup_failures = tuple(self._cleanup_failures.values())
        return reason

    async def drain_retired(self):
        with self._condition:
            pending = tuple(life.retirement for life in self._lifetimes.values() if life.retirement is not None)
        if pending:
            await asyncio.shield(asyncio.gather(*pending))
        return self.status_snapshot()
