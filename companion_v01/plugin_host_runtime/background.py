"""Independent background service supervision and generation-safe outcomes."""

from __future__ import annotations

from typing import Any
import asyncio

from ..plugin_jobs import _HostJobController, run_supervised_job

from .constants import _HOST_AVAILABLE_STATES
from .timing import _current_task_is_cancelling


class PluginBackgroundMixin:
    async def _run_registered_plugins(self):
        # Publish one independently supervised task for every registered
        # service.  The tuple key avoids ambiguous string concatenation and
        # lets sibling services keep running when one exits.
        service_tasks: dict[
            tuple[str, str], tuple[Any, _HostJobController, asyncio.Task]
        ] = {}
        self._background_service_statuses = {}
        for plugin_id, active_plugin in self._active_plugins.items():
            for registration in active_plugin.background_services:
                service_key = (plugin_id, registration.service_id)
                controller = _HostJobController()
                controller._arm()
                task = asyncio.create_task(
                    run_supervised_job(registration.service, controller),
                    name=f"plugin-service:{plugin_id}:{registration.service_id}",
                )
                self._background_service_statuses[service_key] = {
                    "status": "running",
                    "reason": "",
                }
                task.add_done_callback(
                    lambda done, key=service_key, ctl=controller: self._on_background_service_done(
                        key,
                        ctl,
                        done,
                    )
                )
                service_tasks[service_key] = (registration.service, controller, task)
        self._background_service_tasks = service_tasks
        enabled_failures = any(status.enabled and status.status != "active" for status in self._plugin_statuses)
        async with self._invoke_lock:
            self._state = "degraded" if enabled_failures else "active"
        if service_tasks:
            # Give every service one scheduling opportunity so an immediate exit
            # is reflected in the startup snapshot instead of fake readiness.
            await asyncio.sleep(0)
            for service_key, (_service, controller, task) in service_tasks.items():
                if (
                    task.done()
                    and self._background_service_statuses.get(service_key, {}).get("status")
                    == "running"
                ):
                    self._on_background_service_done(service_key, controller, task)
        return self.status_snapshot()

    def _on_background_service_done(
        self,
        service_key: tuple[str, str],
        controller: _HostJobController,
        task: asyncio.Task,
    ) -> None:
        """Consume every task outcome; only the current service may change status."""
        try:
            exception = task.exception()
        except asyncio.CancelledError:
            exception = None
        current = self._background_service_tasks.get(service_key)
        if current is None or current[1] is not controller or current[2] is not task:
            return
        if controller.shutdown_requested or self._state in {"stopping", "stopped"}:
            previous = self._background_service_statuses.get(service_key, {})
            if previous.get("status") != "degraded":
                self._background_service_statuses[service_key] = {
                    "status": "stopped", "reason": str(previous.get("reason") or ""),
                }
            return
        reason = "job_cancelled" if task.cancelled() else "job_failed" if exception is not None else "job_exited"
        self._background_service_statuses[service_key] = {"status": "failed", "reason": reason}
        if self._state in _HOST_AVAILABLE_STATES:
            self._state = "degraded"

    async def _stop_background_services(self) -> None:
        """Signal every supervised service and await independent shutdown."""
        if not self._background_service_tasks:
            return
        for _service, controller, _task in self._background_service_tasks.values():
            controller.signal_shutdown()
        for service_key, (service, _controller, _task) in reversed(
            tuple(self._background_service_tasks.items())
        ):
            try:
                await asyncio.wait_for(service.stop(), timeout=self._close_timeout_seconds)
            except asyncio.CancelledError:
                if _current_task_is_cancelling():
                    raise
                self._record_background_service_stop_failure(service_key, "service_stop_cancelled")
            except Exception:
                self._record_background_service_stop_failure(service_key, "service_stop_failed")
        active_tasks = {
            service_key: task
            for service_key, (_service, _controller, task) in self._background_service_tasks.items()
            if not task.done()
        }
        if active_tasks:
            _done, pending = await asyncio.wait(
                tuple(active_tasks.values()),
                timeout=self._background_service_stop_timeout_seconds,
            )
            if pending:
                pending_set = set(pending)
                for service_key, task in active_tasks.items():
                    if task in pending_set:
                        self._record_background_service_stop_failure(
                            service_key,
                            "service_stop_timeout",
                        )
                for task in pending:
                    task.cancel()
                await asyncio.wait(pending, timeout=self._close_timeout_seconds)

    def _record_background_service_stop_failure(
        self,
        service_key: tuple[str, str],
        reason: str,
    ) -> None:
        self._background_service_stop_failure_count += 1
        self._background_service_statuses[service_key] = {
            "status": "degraded",
            "reason": reason,
        }
