"""Serialized host start, prepare, activate, restart and stop."""

from __future__ import annotations

from dataclasses import replace
from typing import Any
import asyncio

from capcore import CapabilityResult, HealthStatus

from ..instance_profile import PluginSelection
from ..plugin_api import is_valid_plugin_id
from ..plugin_result_projection import sanitize_capability_result

from .cleanup import abort_startup, complete_cleanup, stop_registered_plugins
from .constants import _HOST_AVAILABLE_STATES
from .dependencies import resolve_startup_dependencies
from .models import _ActivationFailure
from .startup import StartupPlan, publish_startup, stage_plugins
from .timing import _bounded_adapter_call


class PluginLifecycleMixin:
    async def start(self) -> dict[str, Any]:
        async with self._management_lock:
            return await self._start_once()

    async def _start_once(self, *, allow_stopped: bool = False, prepare_only: bool = False) -> dict[str, Any]:
        async with self._lifecycle_lock:
            if self._state in _HOST_AVAILABLE_STATES or self._state in {"starting", "prepared", "stopping"}:
                return self.status_snapshot()
            if self._state == "stopped" and not allow_stopped:
                return self.status_snapshot()
            # A failed prepare can still own accepted registrations. Retire them
            # before replacing the maps with another generation's instances.
            if self._active_plugins or self._background_service_tasks:
                await complete_cleanup(stop_registered_plugins(self))
            self._runtime_loop = asyncio.get_running_loop()
            self._state = "starting"
            self._closed_adapters = []
            plan = StartupPlan()
            try:
                await stage_plugins(self, plan, prepare_only=prepare_only)
                if not prepare_only:
                    await resolve_startup_dependencies(self, plan)
                publish_startup(self, plan, prepare_only=prepare_only)
                if prepare_only:
                    return self.status_snapshot()
                return await self._run_registered_plugins()
            except asyncio.CancelledError:
                await abort_startup(self, plan=plan, reason="plugin_activation_cancelled")
                raise
            except Exception:
                await abort_startup(self, plan=plan, reason="plugin_activation_failed")
                raise

    async def prepare(self) -> dict[str, Any]:
        """Register and validate declarations without health checks or jobs."""
        async with self._management_lock:
            return await self._start_once(prepare_only=True)

    async def activate_prepared(self) -> dict[str, Any]:
        """Activate after the caller has checked the full generation's dependencies."""
        async with self._management_lock:
            async with self._lifecycle_lock:
                if self._state != "prepared":
                    return self.status_snapshot()
                try:
                    for active in self._active_plugins.values():
                        for adapter in active.adapters:
                            await self._check_adapter_health(adapter)
                except asyncio.CancelledError:
                    await abort_startup(self, reason="plugin_activation_cancelled")
                    raise
                except _ActivationFailure as exc:
                    self._state = "failed"
                    self._plugin_statuses = tuple(replace(item, status="failed", reason=exc.reason)
                                                  if item.enabled else item for item in self._plugin_statuses)
                    return self.status_snapshot()
                self._plugin_statuses = tuple(replace(item, status="active") if item.status == "prepared" else item
                                              for item in self._plugin_statuses)
                try:
                    return await self._run_registered_plugins()
                except asyncio.CancelledError:
                    await abort_startup(self, reason="plugin_activation_cancelled")
                    raise

    async def _check_adapter_health(self, adapter):
        health = await _bounded_adapter_call(
            adapter.health(),
            timeout_seconds=self._activation_timeout_seconds,
            failure_reason="plugin_health_failed",
        )
        if not isinstance(health, HealthStatus):
            raise _ActivationFailure("invalid_plugin_health_result")
        if not health.ok:
            safe_health = sanitize_capability_result(
                CapabilityResult(is_error=True, status="unavailable", reason=health.reason)
            )
            raise _ActivationFailure(safe_health.reason or "plugin_health_unavailable")

    async def stop(self) -> dict[str, Any]:
        async with self._management_lock:
            return await self._stop_once()

    async def restart(self) -> dict[str, Any]:
        """Recreate installed plugin instances without claiming code hot reload.

        The current selection snapshot remains authoritative.  This operation
        drains active calls, stops jobs, closes adapters, then re-runs discovery
        and activation in this worker's process. Python modules can remain in
        its import cache; process replacement is owned by PluginGenerationRuntime.
        """

        async with self._management_lock:
            await self._stop_once()
            return await self._start_once(allow_stopped=True)

    async def reconfigure(self, selections: tuple[PluginSelection, ...]) -> dict[str, Any]:
        """Apply one validated selection snapshot through the normal lifecycle."""

        if not isinstance(selections, tuple) or any(not isinstance(item, PluginSelection) for item in selections):
            raise TypeError("plugin_selections_must_be_snapshot")
        if any(not is_valid_plugin_id(item.plugin_id) or not isinstance(item.enabled, bool) for item in selections):
            raise ValueError("invalid_plugin_selection")
        if len({item.plugin_id for item in selections}) != len(selections):
            raise ValueError("duplicate_plugin_selection")
        async with self._management_lock:
            await self._stop_once()
            self._selections = tuple(selections)
            return await self._start_once(allow_stopped=True)

    async def _stop_once(self) -> dict[str, Any]:
        async with self._lifecycle_lock:
            if self._state == "stopped":
                return self.status_snapshot()
            if self._state == "created":
                self._state = "stopped"
                return self.status_snapshot()
            await complete_cleanup(stop_registered_plugins(self))
            return self.status_snapshot()
