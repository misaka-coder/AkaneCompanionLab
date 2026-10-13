"""Invocation scope construction, parent provenance and guaranteed admission release."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Callable

from capcore import InvocationContext

from ..plugin_api import (
    EVENT_SUBSCRIBE_PERMISSION,
    EVENT_EMIT_PERMISSION,
    CONTEXT_OBSERVE_PERMISSION,
    AGENT_TURN_REQUEST_PERMISSION,
    RESOURCE_READ_PERMISSION,
    CAPABILITY_INVOKE_PERMISSION,
)
from ..plugin_connections import permitted_connections
from ..plugin_resources import ResourceInvocation, current_resource_invocation

from .cleanup import complete_cleanup
from .models import _CapabilityRegistration


def capability_scope(host: Any, registration: _CapabilityRegistration,
                     context: InvocationContext) -> ResourceInvocation:
    return ResourceInvocation(
        registration.plugin_id, context, capability_id=registration.descriptor.id,
        can_invoke_capabilities=CAPABILITY_INVOKE_PERMISSION in registration.permissions,
        connection_names=permitted_connections(registration.permissions),
        connection_specs=registration.connections,
        private_values=registration.private_result_values,
        generation_id=str(host._generation),
        event_origin_context=getattr(current_resource_invocation.get(), "event_origin_context", None),
        event_delivery_id=getattr(current_resource_invocation.get(), "event_delivery_id", ""),
        can_emit_events=EVENT_EMIT_PERMISSION in registration.permissions,
        can_bind_events=EVENT_SUBSCRIBE_PERMISSION in registration.permissions,
        can_observe_context=CONTEXT_OBSERVE_PERMISSION in registration.permissions,
        can_request_turn=AGENT_TURN_REQUEST_PERMISSION in registration.permissions,
    )


def event_scope(host: Any, registration: Any, context: InvocationContext, *,
                delivery_id: str, origin_context: InvocationContext | None) -> ResourceInvocation:
    return ResourceInvocation(
        registration.plugin_id, context, capability_id=registration.subscription.subscription_id,
        can_invoke_capabilities=CAPABILITY_INVOKE_PERMISSION in registration.permissions,
        can_read_resources=RESOURCE_READ_PERMISSION in registration.permissions,
        connection_names=permitted_connections(registration.permissions),
        connection_specs=registration.connections,
        private_values=registration.private_values, generation_id=str(host._generation),
        event_delivery_id=delivery_id, can_emit_events=EVENT_EMIT_PERMISSION in registration.permissions,
        event_origin_context=origin_context,
        can_bind_events=EVENT_SUBSCRIBE_PERMISSION in registration.permissions,
        can_observe_context=CONTEXT_OBSERVE_PERMISSION in registration.permissions,
        can_request_turn=AGENT_TURN_REQUEST_PERMISSION in registration.permissions,
    )


async def close_invocation(host: Any, scope: ResourceInvocation | None, *, keep_scope_open: bool) -> None:
    try:
        if scope is not None and not keep_scope_open:
            await scope.aclose()
    finally:
        async with host._invoke_lock:
            host._inflight_count = max(0, host._inflight_count - 1)
            if host._inflight_count == 0:
                host._inflight_zero.set()


@asynccontextmanager
async def invocation_scope(host: Any, factory: Callable[[], ResourceInvocation], *, keep_scope_open: bool = False):
    """Pair admission with cleanup, including failure before handler entry."""
    scope = None
    token = None
    opened = False
    try:
        scope = factory()
        initializer = getattr(host._events_provider or host.build_event_broker(), "initialize_scope", None)
        if callable(initializer):
            # Initialize while the parent invocation is still current so nested
            # calls retain its captured origin and turn epoch.
            initializer(scope)
        token = current_resource_invocation.set(scope)
        opened = True
        yield scope
    finally:
        if token is not None:
            current_resource_invocation.reset(token)
        await complete_cleanup(
            close_invocation(host, scope, keep_scope_open=keep_scope_open and opened),
            propagate_cancel=False,
        )
