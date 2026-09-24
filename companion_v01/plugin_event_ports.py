"""Scoped event operations over the existing host/runtime provider lane."""

from __future__ import annotations

import asyncio
from capcore import CapabilityResult

from .plugin_events import event_rejection
from .plugin_resources import current_resource_invocation
from .plugin_result_projection import sanitize_capability_result


class ScopedPluginEventsPort:
    def __init__(self, plugin_id, provider):
        self._plugin_id, self._provider = plugin_id, provider

    async def request(self, operation, payload):
        if not isinstance(operation, str) or operation not in {"emit", "status", "bind", "unbind", "observe", "timeline_append", "request_turn", "turn_status", "cancel_turn"} or not isinstance(payload, dict):
            return event_rejection("status", "event_request_invalid")
        scope = current_resource_invocation.get()
        if scope is None or not scope.active or scope.plugin_id != self._plugin_id:
            return event_rejection(operation, "event_invocation_required")
        allowed = scope.can_emit_events if operation == "emit" else (
            scope.can_bind_events if operation in {"bind", "unbind"} else scope.can_emit_events or scope.can_bind_events
        )
        if operation in {"observe", "timeline_append"}:
            allowed = scope.can_observe_context
        if operation in {"request_turn", "turn_status", "cancel_turn"}:
            allowed = scope.can_request_turn
        if not allowed:
            if operation in {"request_turn", "turn_status", "cancel_turn"}:
                return event_rejection(operation, "agent_turn_request_permission_required")
            if operation in {"observe", "timeline_append"}:
                return event_rejection(operation, "context_observe_permission_required")
            return event_rejection(operation, "event_emit_permission_required" if operation == "emit" else "event_subscribe_permission_required")
        task = asyncio.current_task()
        if task.cancelling():
            scope.revoke()
            raise asyncio.CancelledError()
        snapshot = sanitize_capability_result(CapabilityResult(is_error=False, status="ok", content=payload))
        if snapshot.is_error:
            return event_rejection(operation, snapshot.reason)
        scope.pending.add(task)
        try:
            return await self._provider.request(operation, snapshot.content, invocation=scope)
        except asyncio.CancelledError:
            raise
        except Exception:
            return event_rejection(operation, "event_provider_failed")
        finally:
            scope.pending.discard(task)
