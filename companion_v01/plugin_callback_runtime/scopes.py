"""Host-owned callback invocation registration and authority."""

from __future__ import annotations

from ..plugin_api import (
    CAPABILITY_INVOKE_PERMISSION,
    RESOURCE_READ_PERMISSION,
    EVENT_EMIT_PERMISSION,
    CONTEXT_OBSERVE_PERMISSION,
    AGENT_TURN_REQUEST_PERMISSION,
    EVENT_SUBSCRIBE_PERMISSION,
)
from capcore import InvocationContext
from ..plugin_resources import ResourceInvocation, current_resource_invocation
from ..plugin_result_forwarding import forwarded_dependency_result
from ..plugin_connections import permitted_connections


class CallbackInvocationScopes:
    """Host-owned callback invocation registration and authority."""

    __slots__ = ()

    def begin_invocation(self, request_id: str, *, plugin_id: str, context: InvocationContext,
                         capability_id: str = "", permissions=(), connection_specs=(), event_delivery_id="", event_origin_context=None,
                         arguments=None) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("callback_router_closed")
            self._invocations[request_id] = ResourceInvocation(
                plugin_id, context, capability_id=capability_id,
                can_invoke_capabilities=CAPABILITY_INVOKE_PERMISSION in permissions,
                connection_names=permitted_connections(permissions),
                connection_specs=connection_specs,
                arguments=dict(arguments or {}),
                can_read_resources=RESOURCE_READ_PERMISSION in permissions,
                generation_id=self._generation_id,
                event_delivery_id=event_delivery_id or getattr(current_resource_invocation.get(), "event_delivery_id", ""),
                event_origin_context=event_origin_context or getattr(current_resource_invocation.get(), "event_origin_context", None),
                can_emit_events=EVENT_EMIT_PERMISSION in permissions,
                can_bind_events=EVENT_SUBSCRIBE_PERMISSION in permissions,
                can_observe_context=CONTEXT_OBSERVE_PERMISSION in permissions,
                can_request_turn=AGENT_TURN_REQUEST_PERMISSION in permissions,
            )

            initializer = getattr(self._events_provider, "initialize_scope", None)
            if callable(initializer):
                initializer(self._invocations[request_id])

    def begin_service_invocation(self, request_id: str, *, plugin_id: str, context: InvocationContext,
                                 permissions=(), connection_specs=(), service_id: str = "") -> None:
        """Register one host invocation for a supervised background service.

        A service runs between turns, so it has no per-command request id. The
        worker sets the same id as its callback lane key, letting the service use
        host ports (``request_turn``, tools, events) for its whole lifetime.
        """

        with self._lock:
            if self._closed:
                raise RuntimeError("callback_router_closed")
            self._invocations[request_id] = ResourceInvocation(
                plugin_id, context, capability_id=service_id,
                can_invoke_capabilities=CAPABILITY_INVOKE_PERMISSION in permissions,
                connection_names=permitted_connections(permissions),
                connection_specs=connection_specs,
                can_read_resources=RESOURCE_READ_PERMISSION in permissions,
                generation_id=self._generation_id,
                can_emit_events=EVENT_EMIT_PERMISSION in permissions,
                can_bind_events=EVENT_SUBSCRIBE_PERMISSION in permissions,
                can_observe_context=CONTEXT_OBSERVE_PERMISSION in permissions,
                can_request_turn=AGENT_TURN_REQUEST_PERMISSION in permissions,
            )

    async def finish_invocation(self, request_id: str) -> None:
        with self._lock:
            invocation = self._invocations.get(request_id)
            if invocation is not None:
                invocation.revoke()
        if invocation is not None:
            await invocation.aclose()
            with self._lock:
                if self._invocations.get(request_id) is invocation:
                    self._invocations.pop(request_id)

    def revoke_invocation(self, request_id: str) -> None:
        # Called before notifying the worker. A worker can catch cancellation,
        # but cannot restore the parent-owned callback grant.
        with self._lock:
            invocation = self._invocations.get(request_id)
            if invocation is not None:
                invocation.revoke()

    def forwarded_result(self, request_id, result):
        with self._lock:
            invocation = self._invocations.get(request_id)
            return forwarded_dependency_result(invocation, result)
