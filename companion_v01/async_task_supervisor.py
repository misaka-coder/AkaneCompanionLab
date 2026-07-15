"""Bounded ownership for request-spawned asyncio follow-up tasks."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any


logger = logging.getLogger("akane.async_tasks")


class AsyncTaskSupervisor:
    def __init__(self, *, name: str) -> None:
        self.name = str(name or "async-tasks")
        self._tasks: set[asyncio.Task[Any]] = set()
        self._closing = False

    def create_task(self, coroutine: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        if self._closing:
            coroutine.close()
            raise RuntimeError(f"{self.name} is stopping")
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._on_done)
        return task

    async def close(self, *, timeout: float = 10.0) -> dict[str, Any]:
        self._closing = True
        tasks = [task for task in self._tasks if not task.done()]
        if not tasks:
            return {"status": "stopped", "cancelled": 0, "remaining": 0}
        for task in tasks:
            task.cancel()
        done, pending = await asyncio.wait(tasks, timeout=max(0.1, float(timeout)))
        if done:
            await asyncio.gather(*done, return_exceptions=True)
        return {
            "status": "stopped" if not pending else "degraded",
            "cancelled": len(done),
            "remaining": len(pending),
        }

    def _on_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        try:
            error = task.exception()
        except (asyncio.CancelledError, asyncio.InvalidStateError):
            return
        if error is not None:
            logger.warning("supervised async task failed: group=%s type=%s", self.name, type(error).__name__)


__all__ = ["AsyncTaskSupervisor"]
