"""Cancellation-safe host shutdown and staged-resource retirement."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Iterable
import asyncio

from capcore import CapabilityAdapter

from ..plugin_subprocess import drain

from .constants import _public_plugin_id
from .models import PluginStatus
from .startup import StartupPlan
from .timing import (
    _await_with_optional_timeout,
    _combined_invocation_timeout,
    _current_task_is_cancelling,
)


async def complete_cleanup(awaitable: Any, *, propagate_cancel: bool = True) -> Any:
    """Drain real cleanup even if its caller receives another cancellation."""
    result, cancelled = await drain(asyncio.ensure_future(awaitable))
    if cancelled and propagate_cancel:
        raise asyncio.CancelledError()
    return result


async def stop_registered_plugins(host: Any, *, staged_adapters: Iterable[CapabilityAdapter] = ()) -> None:
    """Withdraw admission, drain calls, stop services and close owned adapters."""
    async with host._invoke_lock:
        host._state = "stopping"
    if host._event_broker is not None:
        await host._event_broker.reconcile()
    try:
        await _await_with_optional_timeout(
            host._inflight_zero.wait(),
            timeout_seconds=_combined_invocation_timeout(
                host._invoke_timeout_seconds, host._managed_artifact_timeout_seconds,
            ),
        )
    except (TimeoutError, asyncio.TimeoutError):
        pass
    await host._stop_background_services()
    adapters = (*staged_adapters, *host._activation_order)
    await host._close_adapters(reversed(adapters))
    host._active_plugins = MappingProxyType({})
    host._capabilities = MappingProxyType({})
    host._contribution_snapshots = ()
    host._activation_order = ()
    host._background_service_tasks = {}
    host._state = "stopped"
    host._runtime_loop = None


async def abort_startup(host: Any, *, plan: StartupPlan | None = None, reason: str) -> None:
    await complete_cleanup(
        stop_registered_plugins(host, staged_adapters=plan.adapters if plan is not None else ()),
        propagate_cancel=False,
    )
    host._plugin_statuses = tuple(
        PluginStatus(
            plugin_id=_public_plugin_id(selection.plugin_id), enabled=selection.enabled,
            status=("cancelled" if reason == "plugin_activation_cancelled" else "failed")
            if selection.enabled else "disabled",
            reason=reason if selection.enabled else "instance_disabled",
        )
        for selection in host._selections
    )


class PluginCleanupMixin:
    async def _close_adapters(self, adapters: Iterable[CapabilityAdapter]) -> None:
        for adapter in adapters:
            if any(closed is adapter for closed in self._closed_adapters):
                continue
            self._closed_adapters.append(adapter)
            try:
                await asyncio.wait_for(
                    adapter.aclose(),
                    timeout=self._close_timeout_seconds,
                )
            except asyncio.CancelledError:
                if _current_task_is_cancelling():
                    raise
                self._close_failure_count += 1
                continue
            except Exception:
                self._close_failure_count += 1
                continue
