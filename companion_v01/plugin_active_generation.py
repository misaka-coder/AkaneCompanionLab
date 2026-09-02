"""Atomic publication for a complete set of isolated plugin generations.

The process client owns one plugin.  This module freezes several ready clients
into the one immutable snapshot a Bot will eventually consume.  Publication
changes one pointer; requests that already leased the previous pointer finish
before its processes are stopped.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from capcore import CapabilityDescriptor, CapabilityResult, InvocationContext

from .instance_profile import PluginSelection
from .plugin_api import is_valid_plugin_id
from .skill_runtime import ContributedSkillRoot


class PluginActiveGenerationError(RuntimeError):
    """A candidate cannot become the active plugin generation."""

    def __init__(self, reason: str) -> None:
        self.reason = str(reason or "plugin_active_generation_invalid")
        super().__init__(self.reason)


class PluginGenerationEndpoint(Protocol):
    """Public parent-side surface of one ready plugin process."""

    plugin_id: str
    generation_id: str

    @property
    def running(self) -> bool: ...

    @property
    def capability_descriptors(self) -> Mapping[str, CapabilityDescriptor]: ...

    @property
    def registered_event_types(self) -> tuple[str, ...]: ...

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

    def stop(self) -> dict[str, Any]: ...


class PluginGenerationSnapshot:
    """One validated and immutable full-Bot plugin snapshot."""

    def __init__(
        self,
        selections: tuple[PluginSelection, ...],
        processes: tuple[PluginGenerationEndpoint, ...],
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
        if set(process_by_plugin) != expected_enabled:
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

    def processes_for_event(self, event_type: str) -> tuple[PluginGenerationEndpoint, ...]:
        normalized = str(event_type or "").strip().lower()
        return tuple(
            process for process in self._processes if normalized in process.registered_event_types
        )

    def processes_for_hook(self, hook_type: str) -> tuple[PluginGenerationEndpoint, ...]:
        normalized = str(hook_type or "").strip().lower()
        return tuple(
            process for process in self._processes if normalized in process.registered_hook_types
        )


@dataclass(slots=True)
class _PublishedGeneration:
    snapshot: PluginGenerationSnapshot
    inflight: int = 0
    accepting: bool = True


class ActivePluginGeneration:
    """Atomically route work to one published full-plugin snapshot."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._publication_lock = asyncio.Lock()
        self._active: _PublishedGeneration | None = None
        self._state = "created"
        self._generation = 0
        self._runtime_loop: asyncio.AbstractEventLoop | None = None
        self._last_cleanup_failures: tuple[tuple[str, str], ...] = ()

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

    def stable_system_prompt_blocks(self) -> tuple[str, ...]:
        snapshot = self._current_snapshot()
        return snapshot.stable_system_prompt_blocks() if snapshot is not None else ()

    def skill_roots(self) -> tuple[ContributedSkillRoot, ...]:
        snapshot = self._current_snapshot()
        return snapshot.skill_roots() if snapshot is not None else ()

    async def publish(self, candidate: PluginGenerationSnapshot) -> dict[str, Any]:
        """Publish a ready candidate, then drain and stop the previous set."""

        async with self._publication_lock:
            return await self._publish_once(candidate)

    async def _publish_once(self, candidate: PluginGenerationSnapshot) -> dict[str, Any]:

        if not isinstance(candidate, PluginGenerationSnapshot):
            raise PluginActiveGenerationError("plugin_generation_candidate_invalid")
        if not candidate.ready:
            raise PluginActiveGenerationError("plugin_generation_not_ready")
        loop = asyncio.get_running_loop()
        replacement = _PublishedGeneration(candidate)
        with self._condition:
            if self._state == "stopped":
                raise PluginActiveGenerationError("plugin_runtime_stopped")
            previous = self._active
            self._active = replacement
            self._generation += 1
            self._state = "active"
            self._runtime_loop = loop
            if previous is not None:
                previous.accepting = False
            generation = self._generation

        failures = await self._finish_cleanup(previous, replacement)
        with self._condition:
            self._last_cleanup_failures = failures
        payload = self.status_snapshot()
        payload["generation"] = generation
        if failures:
            payload["ok"] = False
            payload["reason"] = "old_generation_stop_failed"
        return payload

    async def invoke(
        self,
        capability_id: str,
        args: Mapping[str, Any],
        *,
        context: InvocationContext,
    ) -> CapabilityResult:
        leased = self._lease_capability(capability_id)
        if leased is None:
            with self._condition:
                available = self._active is not None and self._state == "active"
            return CapabilityResult(
                is_error=True,
                status="not_found" if available else "host_unavailable",
                reason="unknown_capability" if available else "host_unavailable",
            )
        record, process = leased
        try:
            return await process.invoke(capability_id, args, context=context)
        finally:
            self._release(record)

    async def invoke_from_consumer(
        self,
        capability_id: str,
        args: Mapping[str, Any],
        *,
        context: InvocationContext,
    ) -> CapabilityResult:
        return await self.invoke(capability_id, args, context=context)

    async def stop(self) -> dict[str, Any]:
        async with self._publication_lock:
            return await self._stop_once()

    async def _stop_once(self) -> dict[str, Any]:
        with self._condition:
            if self._state == "stopped":
                return self.status_snapshot()
            previous = self._active
            self._active = None
            self._state = "stopping"
            if previous is not None:
                previous.accepting = False
        try:
            failures = await self._finish_cleanup(previous, None)
        except asyncio.CancelledError:
            with self._condition:
                self._state = "stopped"
                self._runtime_loop = None
            raise
        with self._condition:
            self._state = "stopped"
            self._runtime_loop = None
            self._last_cleanup_failures = failures
        return self.status_snapshot()

    def status_snapshot(self) -> dict[str, Any]:
        with self._condition:
            record = self._active
            state = self._state
            generation = self._generation
            failures = self._last_cleanup_failures
        snapshot = record.snapshot if record is not None else None
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
        elif failures:
            reason = "old_generation_stop_failed"
        return {
            "ok": public_state == "active" and not failures,
            "status": public_state,
            "reason": reason,
            "generation": generation,
            "configured_plugin_count": len(selections),
            "plugin_count": len(active_ids),
            "capability_count": len(snapshot.capability_ids) if snapshot is not None else 0,
            "prompt_block_count": len(snapshot.stable_system_prompt_blocks()) if snapshot is not None else 0,
            "event_handler_count": sum(
                len(process.registered_event_types) for process in snapshot.processes
            ) if snapshot is not None else 0,
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
            return self._active.snapshot if self._active is not None else None

    def _lease_capability(
        self,
        capability_id: str,
    ) -> tuple[_PublishedGeneration, PluginGenerationEndpoint] | None:
        with self._condition:
            record = self._active
            if record is None or not record.accepting or self._state != "active":
                return None
            process = record.snapshot.process_for_capability(capability_id)
            if process is None:
                return None
            record.inflight += 1
            return record, process

    def _release(self, record: _PublishedGeneration) -> None:
        with self._condition:
            record.inflight = max(0, record.inflight - 1)
            if record.inflight == 0:
                self._condition.notify_all()

    async def _drain_and_stop(
        self,
        previous: _PublishedGeneration | None,
        replacement: _PublishedGeneration | None,
    ) -> tuple[tuple[str, str], ...]:
        if previous is None:
            return ()
        await asyncio.to_thread(self._wait_for_drain, previous)
        retained = {
            id(process)
            for process in replacement.snapshot.processes
        } if replacement is not None else set()
        processes = tuple(
            process for process in previous.snapshot.processes if id(process) not in retained
        )
        if not processes:
            return ()
        results = await asyncio.gather(
            *(asyncio.to_thread(process.stop) for process in processes),
            return_exceptions=True,
        )
        failures: list[tuple[str, str]] = []
        for process, result in zip(processes, results):
            if isinstance(result, BaseException) or not isinstance(result, Mapping):
                failures.append((process.plugin_id, "plugin_generation_stop_failed"))
            elif not bool(result.get("ok")):
                failures.append(
                    (
                        process.plugin_id,
                        str(result.get("reason") or "plugin_generation_stop_failed"),
                    )
                )
        return tuple(failures)

    async def _finish_cleanup(
        self,
        previous: _PublishedGeneration | None,
        replacement: _PublishedGeneration | None,
    ) -> tuple[tuple[str, str], ...]:
        cleanup = asyncio.create_task(self._drain_and_stop(previous, replacement))
        try:
            return await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            failures = await cleanup
            with self._condition:
                self._last_cleanup_failures = failures
            raise

    def _wait_for_drain(self, record: _PublishedGeneration) -> None:
        with self._condition:
            self._condition.wait_for(lambda: record.inflight == 0)


__all__ = [
    "ActivePluginGeneration",
    "PluginActiveGenerationError",
    "PluginGenerationEndpoint",
    "PluginGenerationSnapshot",
]
