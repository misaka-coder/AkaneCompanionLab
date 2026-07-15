"""Host-owned job controller for supervised plugin background tasks.

PluginHost creates one _HostJobController per plugin that registers a job.
The controller is armed (event created) on the lifecycle event loop in
PluginHost.start(), then passed to PluginBackgroundJob.start(controller).

The host signals shutdown by calling controller.signal_shutdown() and then
awaiting the job task with a bounded timeout before proceeding to close adapters.
"""

from __future__ import annotations

import asyncio
from typing import Any


class _HostJobController:
    """Concrete PluginJobController provided to a running PluginBackgroundJob."""

    def __init__(self) -> None:
        self._shutdown_event: asyncio.Event | None = None

    def _arm(self) -> None:
        """Create the asyncio Event on the currently running loop.

        Called from PluginHost.start() so the event is bound to the
        FastAPI lifecycle loop where plugin adapters were activated.
        """
        self._shutdown_event = asyncio.Event()

    @property
    def shutdown_requested(self) -> bool:
        return self._shutdown_event is not None and self._shutdown_event.is_set()

    async def wait_for_shutdown(self, timeout: float | None = None) -> bool:
        """Block until shutdown is signalled.

        Returns True if shutdown was signalled, False if timeout elapsed.
        """
        if self._shutdown_event is None:
            return False
        if self._shutdown_event.is_set():
            return True
        try:
            await asyncio.wait_for(self._shutdown_event.wait(), timeout=timeout)
            return True
        except (asyncio.TimeoutError, TimeoutError):
            return False

    def signal_shutdown(self) -> None:
        """Signal the job to stop.  Idempotent."""
        if self._shutdown_event is not None:
            self._shutdown_event.set()


async def run_supervised_job(job: Any, controller: _HostJobController) -> None:
    """Run a plugin job while leaving its outcome observable to PluginHost."""

    await job.start(controller)


__all__ = ["_HostJobController", "run_supervised_job"]
