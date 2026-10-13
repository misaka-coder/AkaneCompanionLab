"""Real host port calls under the existing invocation authority."""

from __future__ import annotations

import asyncio
from typing import Any
from ..plugin_api import NotificationIntent, NotificationResult, PluginResourceResult
from capcore import CapabilityResult
from ..plugin_resources import ResourceInvocation
from ..plugin_generation_codec import capability_result_to_wire
from ..plugin_result_projection import sanitize_capability_result
from ..plugin_result_forwarding import remember_dependency_result
from ..plugin_connections import connection_result_to_wire, rejected as connection_rejected, resolve_connection_snapshot
from ..plugin_events import event_rejection
from ..plugin_tasks import task_rejection


class CallbackHostPorts:
    """Real host port calls under the existing invocation authority."""

    __slots__ = ()

    def _register_callback_task(self, callback_id, invocation):
        task = asyncio.current_task()
        with self._lock:
            control = self._capability_callbacks.get(callback_id)
            cancelled = control is None
            if control is not None:
                control["task"] = task
                cancelled = control["cancelled"]
            invocation.pending.add(task)
        return task, cancelled

    async def _request_event(self, callback_id, request, invocation):
        task, cancelled = self._register_callback_task(callback_id, invocation)
        operation = request.get("operation")
        try:
            if cancelled or not invocation.active:
                result = event_rejection(operation, "event_invocation_expired")
            elif not isinstance(request.get("payload"), dict):
                result = event_rejection(operation, "event_request_invalid")
            else:
                result = await self._events_provider.request(operation, request["payload"], invocation=invocation)
        except asyncio.CancelledError:
            result = event_rejection(operation, "event_operation_cancelled")
        except Exception:
            result = event_rejection(operation, "event_provider_failed")
        try:
            self._send_wire_result(callback_id, result)
        finally:
            invocation.pending.discard(task)
            with self._lock:
                self._capability_callbacks.pop(callback_id, None)

    async def _request_task(self, callback_id, request, invocation):
        task, cancelled = self._register_callback_task(callback_id, invocation)
        operation = request.get("operation")
        try:
            if cancelled or not invocation.active:
                result = task_rejection(str(operation or ""), "task_invocation_expired")
            elif not isinstance(request.get("payload"), dict):
                result = task_rejection(str(operation or ""), "task_request_invalid")
            else:
                result = await self._tasks_provider.request(operation, request["payload"], invocation=invocation)
        except asyncio.CancelledError:
            result = task_rejection(str(operation or ""), "task_operation_cancelled")
        except Exception:
            result = task_rejection(str(operation or ""), "task_provider_failed")
        try:
            self._send_wire_result(callback_id, result)
        finally:
            invocation.pending.discard(task)
            with self._lock:
                self._capability_callbacks.pop(callback_id, None)

    async def _resolve_connection(self, callback_id, name, invocation):
        task = asyncio.current_task()
        invocation.pending.add(task)
        try:
            if not invocation.active:
                result = connection_rejected("connection_invocation_expired")
            else:
                result = await resolve_connection_snapshot(self._connection_provider, name, invocation)
            if not invocation.active:
                result = connection_rejected("connection_invocation_expired")
            wire = connection_result_to_wire(result)
        except asyncio.CancelledError:
            raise
        except Exception:
            wire = connection_result_to_wire(connection_rejected("connection_resolution_failed"))
        finally:
            invocation.pending.discard(task)
        # Private callback lane only: never a public capability result or log.
        self._send_wire_result(callback_id, wire)

    async def _invoke_capability(self, callback_id, request, invocation):
        task, cancelled = self._register_callback_task(callback_id, invocation)
        try:
            if cancelled:
                result = CapabilityResult(is_error=True, status="cancelled", reason="invocation_cancelled")
            elif not invocation.active:
                result = CapabilityResult(is_error=True, status="rejected", reason="capability_invocation_expired")
            else:
                result = await self._capability_provider.invoke(
                    request.get("capability_id"), request.get("arguments"), invocation=invocation,
                )
                if not isinstance(result, CapabilityResult):
                    result = CapabilityResult(is_error=True, status="error", reason="capability_dependency_result_invalid")
        except asyncio.CancelledError:
            result = CapabilityResult(is_error=True, status="cancelled", reason="invocation_cancelled")
        except Exception:
            result = CapabilityResult(is_error=True, status="error", reason="capability_dependency_failed")
        try:
            result = sanitize_capability_result(result)
            remember_dependency_result(invocation, result)
            self._send_wire_result(callback_id, capability_result_to_wire(result))
        finally:
            invocation.pending.discard(task)
            with self._lock:
                self._capability_callbacks.pop(callback_id, None)

    async def _open_resource(self, callback_id: str, target: Any, invocation: ResourceInvocation,
                             representation: str = "original") -> None:
        task = asyncio.current_task()
        invocation.pending.add(task)
        try:
            try:
                if not invocation.active:
                    result = PluginResourceResult(False, "rejected", "resource_invocation_expired")
                elif representation not in ("original", "document"):
                    result = PluginResourceResult(False, "rejected", "resource_representation_invalid")
                else:
                    options = {} if representation == "original" else {"representation": representation}
                    result = await self._resource_provider.open(target, invocation=invocation, **options)
                if not isinstance(result, PluginResourceResult):
                    result = PluginResourceResult(False, "error", "resource_result_invalid")
                if result.ok and result.representation != representation:
                    result = PluginResourceResult(False, "error", "resource_representation_mismatch")
                if not invocation.active:
                    result = PluginResourceResult(False, "rejected", "resource_invocation_expired")
            except asyncio.CancelledError:
                raise
            except Exception:
                result = PluginResourceResult(False, "error", "resource_open_failed")
            self._send_wire_result(callback_id, {
                "ok": result.ok, "status": result.status, "reason": result.reason,
                "path": str(result.path) if result.ok and result.path else "",
                "handle": result.handle, "name": result.name, "file_size": result.file_size,
                "representation": result.representation,
            })
        finally:
            invocation.pending.discard(task)

    async def _deliver_notification(
        self,
        callback_id: str,
        intent: NotificationIntent,
    ) -> None:
        try:
            result = await self._notification_port.send(intent)
            if not isinstance(result, NotificationResult):
                result = NotificationResult(
                    ok=False,
                    status="error",
                    reason="invalid_notification_result",
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            result = NotificationResult(
                ok=False,
                status="error",
                reason="delivery_exception",
            )
        self._send_notification_result(callback_id, result)
