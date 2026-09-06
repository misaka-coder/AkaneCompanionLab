"""Bot-facing lifecycle facade for isolated plugin generations.

One facade owns candidate construction and the one atomic active-generation
slot.  A failed candidate never mutates the published selection or routing
surface; a successful candidate changes every plugin consumer together.
"""

from __future__ import annotations

import asyncio
from types import MappingProxyType
from typing import Any, Mapping

from capcore import CapabilityDescriptor, CapabilityResult, InvocationContext

from .instance_profile import PluginSelection
from .plugin_active_generation import ActivePluginGeneration
from .plugin_generation_candidate import (
    PluginGenerationCandidateBuilder,
    PluginGenerationCandidateError,
)
from .skill_runtime import ContributedSkillRoot


class PluginGenerationRuntime:
    """Build, validate and atomically publish complete plugin generations."""

    code_reload_mode = "atomic_generation_switch"

    def __init__(
        self,
        selections: tuple[PluginSelection, ...],
        *,
        candidate_builder: PluginGenerationCandidateBuilder,
        active_generation: ActivePluginGeneration | None = None,
    ) -> None:
        if not callable(getattr(candidate_builder, "build", None)):
            raise TypeError("invalid_plugin_generation_candidate_builder")
        self._selections = tuple(selections)
        self._builder = candidate_builder
        self._active = active_generation or ActivePluginGeneration()
        self._lifecycle_lock = asyncio.Lock()
        self._runtime_loop: asyncio.AbstractEventLoop | None = None
        self._state = "created"
        self._last_candidate_failure: dict[str, str] | None = None

    @property
    def state(self) -> str:
        active_state = self._active.state
        if active_state == "active":
            return active_state
        return self._state

    @property
    def selections(self) -> tuple[PluginSelection, ...]:
        return self._selections

    @property
    def runtime_loop(self) -> asyncio.AbstractEventLoop | None:
        return self._runtime_loop

    @property
    def capability_ids(self) -> tuple[str, ...]:
        return self._active.capability_ids

    @property
    def capability_descriptors(self) -> Mapping[str, CapabilityDescriptor]:
        descriptors = self._active.capability_descriptors
        return descriptors if descriptors else MappingProxyType({})

    def stable_system_prompt_blocks(self) -> tuple[str, ...]:
        return self._active.stable_system_prompt_blocks()

    def skill_roots(self) -> tuple[ContributedSkillRoot, ...]:
        return self._active.skill_roots()

    def build_event_broker(self) -> Any:
        return self._active.build_event_broker()

    def build_hook_broker(self) -> Any:
        return self._active.build_hook_broker()

    def build_qq_command_broker(self, host_registrations: tuple = ()) -> Any:
        return self._active.build_qq_command_broker(host_registrations=host_registrations)

    def bind_managed_artifact_sink(self, sink: Any) -> None:
        self._builder.managed_artifact_sink = sink

    def bind_resource_provider(self, provider: Any) -> None:
        self._builder.resource_provider = provider

    def bind_notification_port(self, port: Any) -> None:
        self._builder.notification_port = port

    def bind_agent_event_port(self, port: Any) -> None:
        self._builder.agent_event_port = port

    async def start(self) -> dict[str, Any]:
        async with self._lifecycle_lock:
            if self._state == "stopped":
                return self._failure_payload(
                    "plugin_runtime_stopped",
                    selections=self._selections,
                )
            if self._active.state == "active":
                payload = self.status_snapshot()
                payload.update({"published": True, "unchanged": True})
                return payload
            self._runtime_loop = asyncio.get_running_loop()
            return await self._replace(self._selections)

    async def restart(self) -> dict[str, Any]:
        async with self._lifecycle_lock:
            if self._state == "stopped":
                return self._failure_payload(
                    "plugin_runtime_stopped",
                    selections=self._selections,
                )
            self._runtime_loop = asyncio.get_running_loop()
            return await self._replace(self._selections)

    async def reconfigure(
        self,
        selections: tuple[PluginSelection, ...],
    ) -> dict[str, Any]:
        async with self._lifecycle_lock:
            if self._state == "stopped":
                return self._failure_payload(
                    "plugin_runtime_stopped",
                    selections=tuple(selections),
                )
            self._runtime_loop = asyncio.get_running_loop()
            return await self._replace(tuple(selections))

    async def _replace(self, selections: tuple[PluginSelection, ...]) -> dict[str, Any]:
        try:
            candidate = await self._builder.build(selections)
        except PluginGenerationCandidateError as exc:
            self._state = "active" if self._active.state == "active" else "degraded"
            self._last_candidate_failure = {
                "plugin_id": exc.plugin_id,
                "reason": exc.reason,
            }
            return self._failure_payload(
                exc.reason,
                plugin_id=exc.plugin_id,
                selections=selections,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            self._state = "active" if self._active.state == "active" else "degraded"
            self._last_candidate_failure = {
                "plugin_id": "",
                "reason": "plugin_generation_candidate_failed",
            }
            return self._failure_payload(
                "plugin_generation_candidate_failed",
                selections=selections,
            )

        publish_task = asyncio.create_task(self._active.publish(candidate))
        cancelled = False
        try:
            payload = dict(await asyncio.shield(publish_task))
        except asyncio.CancelledError:
            cancelled = True
            payload = dict(await publish_task)
        except Exception:
            await asyncio.gather(
                *(asyncio.to_thread(process.stop) for process in candidate.processes),
                return_exceptions=True,
            )
            self._state = "active" if self._active.state == "active" else "degraded"
            self._last_candidate_failure = {
                "plugin_id": "",
                "reason": "plugin_generation_publish_failed",
            }
            return self._failure_payload(
                "plugin_generation_publish_failed",
                selections=selections,
            )

        self._selections = selections
        self._state = "active"
        self._last_candidate_failure = None
        payload.update(
            {
                "published": True,
                "code_reload": self.code_reload_mode,
            }
        )
        if cancelled:
            raise asyncio.CancelledError()
        return payload

    async def invoke(
        self,
        capability_id: str,
        args: Mapping[str, Any],
        *,
        context: InvocationContext,
    ) -> CapabilityResult:
        return await self._active.invoke(capability_id, args, context=context)

    async def invoke_from_consumer(
        self,
        capability_id: str,
        args: Mapping[str, Any],
        *,
        context: InvocationContext,
    ) -> CapabilityResult:
        return await self._active.invoke_from_consumer(capability_id, args, context=context)

    async def stop(self) -> dict[str, Any]:
        async with self._lifecycle_lock:
            if self._state == "stopped":
                return self.status_snapshot()
            try:
                payload = dict(await self._active.stop())
            except asyncio.CancelledError:
                self._state = "stopped"
                self._runtime_loop = None
                raise
            self._state = "stopped"
            self._runtime_loop = None
            payload.update(self._selection_counts(payload))
            return payload

    def status_snapshot(self) -> dict[str, Any]:
        payload = dict(self._active.status_snapshot())
        if self._active.state != "active":
            payload.update(
                {
                    "ok": False,
                    "status": self._state,
                    "reason": (
                        str((self._last_candidate_failure or {}).get("reason") or "")
                        if self._state == "degraded"
                        else str(payload.get("reason") or "")
                    ),
                    "plugins": self._selection_statuses(
                        self._selections,
                        failed_plugin_id=str(
                            (self._last_candidate_failure or {}).get("plugin_id") or ""
                        ),
                        failure_reason=str(
                            (self._last_candidate_failure or {}).get("reason") or ""
                        ),
                    ),
                }
            )
        elif self._last_candidate_failure is not None:
            payload["last_candidate_failure"] = dict(self._last_candidate_failure)
        payload.update(self._selection_counts(payload))
        payload["code_reload"] = self.code_reload_mode
        return payload

    def _failure_payload(
        self,
        reason: str,
        *,
        plugin_id: str = "",
        selections: tuple[PluginSelection, ...],
    ) -> dict[str, Any]:
        current = self._active.status_snapshot()
        return {
            "ok": False,
            "status": "degraded" if self._active.state != "active" else "candidate_rejected",
            "reason": str(reason or "plugin_generation_candidate_failed"),
            "plugin_id": str(plugin_id or ""),
            "published": False,
            "active_generation": int(current.get("generation") or 0),
            "active_status": str(current.get("status") or self._state),
            "plugins": self._selection_statuses(
                selections,
                failed_plugin_id=plugin_id,
                failure_reason=reason,
                candidate_rejected=True,
            ),
            "code_reload": self.code_reload_mode,
        }

    def _selection_counts(self, payload: Mapping[str, Any]) -> dict[str, int]:
        return {
            "configured_plugin_count": len(self._selections),
            "plugin_count": int(payload.get("plugin_count") or 0),
        }

    @staticmethod
    def _selection_statuses(
        selections: tuple[PluginSelection, ...],
        *,
        failed_plugin_id: str = "",
        failure_reason: str = "",
        candidate_rejected: bool = False,
    ) -> list[dict[str, Any]]:
        statuses: list[dict[str, Any]] = []
        for selection in selections:
            if not selection.enabled and not candidate_rejected:
                statuses.append(
                    {
                        "plugin_id": selection.plugin_id,
                        "enabled": False,
                        "status": "disabled",
                        "reason": "instance_disabled",
                    }
                )
                continue
            statuses.append(
                {
                    "plugin_id": selection.plugin_id,
                    "enabled": selection.enabled,
                    "status": "unavailable",
                    "reason": (
                        str(failure_reason or "plugin_generation_candidate_rejected")
                        if not failed_plugin_id or selection.plugin_id == failed_plugin_id
                        else "plugin_generation_candidate_rejected"
                    ),
                }
            )
        return statuses


__all__ = ["PluginGenerationRuntime"]
