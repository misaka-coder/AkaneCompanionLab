"""Validated capability and event execution within supervised resource scopes."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping
import asyncio

from akane_plugin.events import Event
from capcore import CapabilityResult, InvocationContext, validate_tool_spec_args

from ..plugin_resources import ResourceInvocation
from ..plugin_result_projection import sanitize_capability_result

from .constants import _HOST_AVAILABLE_STATES
from .models import _CapabilityRegistration
from .scopes import capability_scope, event_scope, invocation_scope
from .timing import _current_task_is_cancelling


async def invoke_registered_capability(host: Any, registration: _CapabilityRegistration,
                                       args: Mapping[str, Any], context: InvocationContext,
                                       resource_invocation: ResourceInvocation) -> CapabilityResult:
    validation = validate_tool_spec_args(registration.tool_spec, args)
    if not validation.ok:
        first_reason = validation.errors[0].code if validation.errors else "invalid_arguments"
        return CapabilityResult(
            is_error=True,
            status="validation_error",
            reason=first_reason,
            content={"errors": [error.as_dict() for error in validation.errors]},
        )
    try:
        resource_invocation.arguments = deepcopy(validation.normalized_args)
        result = await resource_invocation.run(
            registration.adapter.invoke(
                registration.descriptor.id,
                validation.normalized_args,
                context,
            ),
            timeout_seconds=host._invoke_timeout_seconds,
        )
    except asyncio.CancelledError:
        if _current_task_is_cancelling():
            raise
        return CapabilityResult(
            is_error=True,
            status="error",
            reason="plugin_invoke_failed",
        )
    except (TimeoutError, asyncio.TimeoutError):
        return CapabilityResult(
            is_error=True,
            status="error",
            reason="plugin_invoke_timeout",
        )
    except Exception:
        return CapabilityResult(
            is_error=True,
            status="error",
            reason="plugin_invoke_failed",
        )
    if not isinstance(result, CapabilityResult):
        return CapabilityResult(
            is_error=True,
            status="error",
            reason="plugin_invoke_invalid_result",
        )
    return await host._finalize_capability_result(
        result,
        registration=registration,
        context=context,
    )


class PluginInvocationMixin:
    async def invoke(
        self,
        capability_id: str,
        args: Mapping[str, Any],
        *,
        context: InvocationContext,
    ) -> CapabilityResult:
        async with self._invoke_lock:
            if self._state not in _HOST_AVAILABLE_STATES:
                return CapabilityResult(
                    is_error=True,
                    status="host_unavailable",
                    reason="host_unavailable",
                )
            registration = self._capabilities.get(str(capability_id or ""))
            if registration is None:
                return CapabilityResult(
                    is_error=True,
                    status="not_found",
                    reason="unknown_capability",
                )
            if not isinstance(context, InvocationContext):
                return CapabilityResult(
                    is_error=True,
                    status="validation_error",
                    reason="invalid_invocation_context",
                )
            from ..capcore_runtime import tool_identity_rejection

            reason = tool_identity_rejection(registration.descriptor, context)
            if reason:
                return CapabilityResult(is_error=True, status="blocked", reason=reason)
            self._inflight_count += 1
            self._inflight_zero.clear()

        try:
            async with invocation_scope(self, lambda: capability_scope(self, registration, context)) as scope:
                return await invoke_registered_capability(self, registration, args, context, scope)
        except asyncio.CancelledError:
            raise
        except Exception:
            return CapabilityResult(is_error=True, status="error", reason="plugin_invoke_failed")

    async def invoke_event(self, subscription_id, event: Event, *, context: InvocationContext, scope_id: str,
                           delivery_id: str) -> CapabilityResult:
        registration = next((item for item in self._event_registrations_snapshot() if item.subscription and
                             item.subscription.subscription_id == subscription_id), None)
        if registration is None:
            return CapabilityResult(is_error=True, status="unavailable", reason="event_subscription_unavailable")
        return await self._invoke_event_registration(registration, event, context=context,
                                                      scope_id=scope_id, delivery_id=delivery_id)

    async def _invoke_event_registration(self, registration, event, *, context, scope_id, delivery_id,
                                         origin_context=None, invocation_out=None, keep_scope_open=False):
        async with self._invoke_lock:
            if self._state not in _HOST_AVAILABLE_STATES or registration.generation_id != str(self._generation):
                return CapabilityResult(is_error=True, status="unavailable", reason="event_subscription_unavailable")
            self._inflight_count += 1
            self._inflight_zero.clear()
        try:
            async with invocation_scope(
                self, lambda: event_scope(self, registration, context, delivery_id=delivery_id,
                                          origin_context=origin_context),
                keep_scope_open=keep_scope_open,
            ) as scope:
                # The broker uses this live scope for subscription-declared turns.
                if invocation_out is not None:
                    invocation_out[0] = scope
                result = await scope.run(registration.handler.handle_event(event, scope_id))
                return sanitize_capability_result(result)
        except asyncio.CancelledError:
            raise
        except Exception:
            return CapabilityResult(is_error=True, status="error", reason="event_handler_exception")
