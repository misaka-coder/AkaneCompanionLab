"""Worker-side scoped projection for the public SDK Task port."""

from __future__ import annotations

import asyncio

from .plugin_resources import current_resource_invocation
from .plugin_result_projection import sanitize_capability_result
from .plugin_tasks import task_rejection
from capcore import CapabilityResult


class ScopedPluginTasksPort:
    def __init__(self, plugin_id, provider):
        self._plugin_id, self._provider = plugin_id, provider

    async def request(self, operation, payload):
        if operation not in {"create", "open", "status", "update", "checkpoint", "checkpoint_status", "pause", "pause_boundary", "resume", "cancel"} or not isinstance(payload, dict):
            return task_rejection(operation, "task_request_invalid")
        scope = current_resource_invocation.get()
        if scope is None or not scope.active or scope.plugin_id != self._plugin_id:
            return task_rejection(operation, "task_invocation_required")
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            scope.revoke()
            raise asyncio.CancelledError()
        snapshot = sanitize_capability_result(CapabilityResult(is_error=False, status="ok", content=payload))
        if snapshot.is_error:
            return task_rejection(operation, snapshot.reason)
        scope.pending.add(task)
        try:
            return await self._provider.request(operation, snapshot.content, invocation=scope)
        except asyncio.CancelledError:
            raise
        except Exception:
            return task_rejection(operation, "task_provider_failed")
        finally:
            scope.pending.discard(task)


__all__ = ["ScopedPluginTasksPort"]
