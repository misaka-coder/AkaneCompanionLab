"""Reverse callbacks used by one PluginHost generation process.

The parent router owns host event-loop dispatch.  Worker-side ports only
project public PluginHost values onto the existing generation control lane.
"""

from __future__ import annotations

from dataclasses import replace

import asyncio
import threading
import uuid
from collections.abc import Callable
from typing import Any, Mapping

from .plugin_api import (
    NotificationIntent,
    NotificationResult,
    PluginResourceResult,
    CAPABILITY_INVOKE_PERMISSION,
    RESOURCE_READ_PERMISSION,
    EVENT_EMIT_PERMISSION,
    CONTEXT_OBSERVE_PERMISSION,
    AGENT_TURN_REQUEST_PERMISSION,
    EVENT_SUBSCRIBE_PERMISSION,
)
from pathlib import Path
from capcore import CapabilityResult, InvocationContext
from .plugin_resources import ResourceInvocation, current_resource_invocation, generation_request_id
from .plugin_generation_codec import (
    PluginGenerationCodecError,
    notification_intent_from_wire,
    notification_intent_to_wire,
    notification_result_from_wire,
    notification_result_to_wire,
    capability_result_to_wire,
    capability_result_from_wire,
)
from .plugin_result_projection import sanitize_capability_result
from .plugin_result_forwarding import remember_dependency_result, forwarded_dependency_result
from .plugin_subprocess import drain
from .plugin_connections import (
    permitted_connections, connection_result_to_wire, connection_result_from_wire, rejected as connection_rejected,
    resolve_connection_snapshot,
)
from .plugin_generation_protocol import PLUGIN_GENERATION_PROTOCOL
from .plugin_events import event_rejection
from .plugin_tasks import task_rejection


class GenerationHostCallbackRouter:
    """Dispatch worker callbacks to the one host-owned runtime port."""

    def __init__(
        self,
        *,
        generation_id: str,
        start_timeout_seconds: float,
        write_response: Callable[[Mapping[str, Any]], None],
    ) -> None:
        self._generation_id = str(generation_id or "")
        self._start_timeout_seconds = max(0.1, float(start_timeout_seconds))
        self._write_response = write_response
        self._notification_port: Any = None
        self._notification_loop: asyncio.AbstractEventLoop | None = None
        self._fallback_loop: asyncio.AbstractEventLoop | None = None
        self._fallback_thread: threading.Thread | None = None
        self._fallback_loop_ready = threading.Event()
        self._lock = threading.Lock()
        self._futures: dict[str, Any] = {}
        self._resource_provider: Any = None
        self._capability_provider: Any = None
        self._connection_provider: Any = None
        self._events_provider: Any = None
        self._tasks_provider: Any = None
        self._capability_callbacks: dict[str, dict] = {}
        self._invocations: dict[str, ResourceInvocation] = {}

    def bind_resource_provider(self, provider: Any) -> None:
        if not callable(getattr(provider, "open", None)):
            raise TypeError("invalid_resource_provider")
        self._resource_provider = provider

    def bind_capability_provider(self, provider: Any) -> None:
        if not callable(getattr(provider, "invoke", None)):
            raise TypeError("invalid_capability_provider")
        self._capability_provider = provider

    def bind_connection_provider(self, provider: Any) -> None:
        if not callable(getattr(provider, "resolve", None)):
            raise TypeError("invalid_connection_provider")
        self._connection_provider = provider

    def begin_invocation(self, request_id: str, *, plugin_id: str, context: InvocationContext,
                         capability_id: str = "", permissions=(), connection_specs=(), event_delivery_id="", event_origin_context=None,
                         arguments=None) -> None:
        with self._lock:
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
            invocation = self._invocations.pop(request_id, None)
        if invocation is not None:
            await invocation.aclose()

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

    def bind_events_provider(self, provider):
        if not callable(getattr(provider, "request", None)):
            raise TypeError("event_provider_invalid")
        self._events_provider = provider

    def bind_tasks_provider(self, provider):
        if not callable(getattr(provider, "request", None)):
            raise TypeError("task_provider_invalid")
        self._tasks_provider = provider

    def bind_notification_port(self, port: Any) -> None:
        if not callable(getattr(port, "send", None)):
            raise TypeError("invalid_notification_port")
        self._notification_port = port
        try:
            self._notification_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._notification_loop = None

    def dispatch(self, request: Mapping[str, Any]) -> None:
        callback_id = str(request.get("callback_id") or "")
        if (
            request.get("protocol") != PLUGIN_GENERATION_PROTOCOL
            or request.get("generation_id") != self._generation_id
            or len(callback_id) != 32
            or any(char not in "0123456789abcdef" for char in callback_id)
        ):
            self._send_failure(callback_id, "callback_protocol_invalid")
            return
        callback = str(request.get("callback") or "")
        if callback == "notification.send":
            self._dispatch_notification(callback_id, request)
            return
        if callback == "resource.open":
            with self._lock:
                invocation = self._invocations.get(str(request.get("invocation_id") or ""))
            if invocation is None or not invocation.active:
                self._send_failure(callback_id, "resource_invocation_expired")
                return
            if not invocation.can_read_resources:
                self._send_failure(callback_id, "resource_read_permission_required")
                return
            if self._resource_provider is None:
                self._send_failure(callback_id, "resource_provider_unavailable")
                return
            self._schedule(
                callback_id, self._open_resource(callback_id, request.get("target"), invocation,
                                                request.get("representation", "original")),
                loop=invocation.loop, unavailable_reason="resource_host_unavailable",
            )
            return
        if callback == "capability.invoke":
            with self._lock:
                invocation = self._invocations.get(str(request.get("invocation_id") or ""))
            if invocation is None or not invocation.active:
                self._send_failure(callback_id, "capability_invocation_expired")
                return
            if not invocation.can_invoke_capabilities:
                self._send_failure(callback_id, "capability_invoke_permission_required")
                return
            if self._capability_provider is None:
                self._send_failure(callback_id, "capability_provider_unavailable")
                return
            with self._lock:
                if callback_id in self._capability_callbacks:
                    return
                self._capability_callbacks[callback_id] = {"task": None, "cancelled": False}
            self._schedule(callback_id, self._invoke_capability(callback_id, request, invocation),
                           loop=invocation.loop, unavailable_reason="capability_host_unavailable")
            return
        if callback == "connection.resolve":
            with self._lock:
                invocation = self._invocations.get(str(request.get("invocation_id") or ""))
            name = request.get("name")
            if invocation is None or not invocation.active:
                self._send_failure(callback_id, "connection_invocation_required")
                return
            if getattr(invocation.context, "global_scope", False):
                self._send_failure(callback_id, "connection_context_required")
                return
            if not isinstance(name, str) or name not in invocation.connection_names:
                self._send_failure(callback_id, "connection_permission_required")
                return
            if self._connection_provider is None:
                self._send_failure(callback_id, "connection_provider_unavailable")
                return
            self._schedule(callback_id, self._resolve_connection(callback_id, name, invocation),
                           loop=invocation.loop, unavailable_reason="connection_host_unavailable")
            return
        if callback == "events.request":
            with self._lock:
                invocation = self._invocations.get(str(request.get("invocation_id") or ""))
            operation = request.get("operation")
            if not isinstance(operation, str) or operation not in {"emit", "status", "bind", "unbind", "observe", "timeline_append", "request_turn", "turn_status", "cancel_turn"}:
                self._send_wire_result(callback_id, event_rejection("status", "event_request_invalid"))
                return
            if invocation is None or not invocation.active:
                self._send_wire_result(callback_id, event_rejection(operation, "event_invocation_expired"))
                return
            allowed = invocation.can_emit_events if operation == "emit" else (
                invocation.can_bind_events if operation in {"bind", "unbind"} else invocation.can_emit_events or invocation.can_bind_events
            )
            if operation in {"observe", "timeline_append"}:
                allowed = invocation.can_observe_context
            if operation in {"request_turn", "turn_status", "cancel_turn"}:
                allowed = invocation.can_request_turn
            if not allowed or self._events_provider is None:
                self._send_wire_result(callback_id, event_rejection(operation, "event_permission_required" if not allowed else "event_provider_unavailable"))
                return
            with self._lock:
                if callback_id in self._capability_callbacks:
                    return
                self._capability_callbacks[callback_id] = {"task": None, "cancelled": False}
            self._schedule(callback_id, self._request_event(callback_id, request, invocation),
                           loop=invocation.loop, unavailable_reason="event_host_unavailable")
            return
        if callback == "tasks.request":
            with self._lock:
                invocation = self._invocations.get(str(request.get("invocation_id") or ""))
            if invocation is None or not invocation.active:
                self._send_wire_result(callback_id, task_rejection(str(request.get("operation") or ""), "task_invocation_expired"))
                return
            if self._tasks_provider is None:
                self._send_wire_result(callback_id, task_rejection(str(request.get("operation") or ""), "task_provider_unavailable"))
                return
            with self._lock:
                if callback_id in self._capability_callbacks:
                    return
                self._capability_callbacks[callback_id] = {"task": None, "cancelled": False}
            self._schedule(callback_id, self._request_task(callback_id, request, invocation),
                           loop=invocation.loop, unavailable_reason="task_host_unavailable")
            return
        self._send_failure(callback_id, "callback_protocol_invalid")

    async def _request_event(self, callback_id, request, invocation):
        task = asyncio.current_task()
        invocation.pending.add(task)
        operation = request.get("operation")
        with self._lock:
            control = self._capability_callbacks[callback_id]
            control["task"] = task
            cancelled = control["cancelled"]
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
        task = asyncio.current_task()
        invocation.pending.add(task)
        operation = request.get("operation")
        with self._lock:
            control = self._capability_callbacks[callback_id]
            control["task"] = task
            cancelled = control["cancelled"]
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
        task = asyncio.current_task()
        invocation.pending.add(task)
        with self._lock:
            control = self._capability_callbacks[callback_id]
            control["task"] = task
            cancelled = control["cancelled"]
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

    def _dispatch_notification(
        self,
        callback_id: str,
        request: Mapping[str, Any],
    ) -> None:
        try:
            intent = notification_intent_from_wire(request.get("intent"))
        except PluginGenerationCodecError:
            self._send_failure(callback_id, "notification_protocol_invalid")
            return
        if self._notification_port is None:
            self._send_notification_result(
                callback_id,
                NotificationResult(
                    ok=False,
                    status="not_configured",
                    reason="no_notification_port_bound",
                ),
            )
            return
        self._schedule(
            callback_id,
            self._deliver_notification(callback_id, intent),
            loop=self._notification_loop,
            unavailable_reason="notification_host_unavailable",
        )

    def _schedule(
        self,
        callback_id: str,
        coroutine: Any,
        *,
        loop: asyncio.AbstractEventLoop | None,
        unavailable_reason: str,
    ) -> None:
        if loop is None or loop.is_closed() or not loop.is_running():
            loop = self._ensure_fallback_loop()
        if loop is None:
            coroutine.close()
            self._send_failure(callback_id, unavailable_reason)
            return
        with self._lock:
            if callback_id in self._futures:
                coroutine.close()
                self._send_failure(callback_id, "duplicate_callback_id")
                return
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        except RuntimeError:
            coroutine.close()
            self._send_failure(callback_id, unavailable_reason)
            return
        with self._lock:
            self._futures[callback_id] = future
        future.add_done_callback(
            lambda done, cid=callback_id: self._discard(cid, done)
        )

    def cancel(self, request: Mapping[str, Any]) -> None:
        if (
            request.get("protocol") != PLUGIN_GENERATION_PROTOCOL
            or request.get("generation_id") != self._generation_id
        ):
            return
        callback_id = str(request.get("callback_id") or "")
        with self._lock:
            control = self._capability_callbacks.get(callback_id)
            if control is not None:
                control["cancelled"] = True
                task = control["task"]
                if task is not None:
                    task.get_loop().call_soon_threadsafe(task.cancel)
                return
            future = self._futures.get(callback_id)
        if future is not None:
            future.cancel()

    def close(self) -> None:
        with self._lock:
            futures = tuple(self._futures.values())
            loop = self._fallback_loop
            thread = self._fallback_thread
        for future in futures:
            future.cancel()
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        with self._lock:
            self._futures.clear()
            self._fallback_loop = None
            self._fallback_thread = None

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

    def _send_notification_result(
        self,
        callback_id: str,
        result: NotificationResult,
    ) -> None:
        try:
            wire_result = notification_result_to_wire(result)
        except PluginGenerationCodecError:
            wire_result = notification_result_to_wire(
                NotificationResult(
                    ok=False,
                    status="error",
                    reason="invalid_notification_result",
                )
            )
        self._send_wire_result(callback_id, wire_result)

    def _send_wire_result(
        self,
        callback_id: str,
        wire_result: Mapping[str, Any],
    ) -> None:
        self._write_response(
            {
                "protocol": PLUGIN_GENERATION_PROTOCOL,
                "type": "callback_response",
                "generation_id": self._generation_id,
                "callback_id": callback_id,
                "ok": True,
                "result": wire_result,
            }
        )

    def _send_failure(self, callback_id: str, reason: str) -> None:
        if not callback_id:
            return
        self._write_response(
            {
                "protocol": PLUGIN_GENERATION_PROTOCOL,
                "type": "callback_response",
                "generation_id": self._generation_id,
                "callback_id": callback_id,
                "ok": False,
                "reason": str(reason or "notification_callback_failed"),
            }
        )

    def _discard(self, callback_id: str, future: Any) -> None:
        with self._lock:
            if self._futures.get(callback_id) is future:
                self._futures.pop(callback_id, None)

    def _ensure_fallback_loop(self) -> asyncio.AbstractEventLoop | None:
        with self._lock:
            loop = self._fallback_loop
            thread = self._fallback_thread
            if loop is not None and thread is not None and thread.is_alive():
                return loop
            self._fallback_loop_ready.clear()
            thread = threading.Thread(
                target=self._run_fallback_loop,
                name=f"plugin-generation-callback:{self._generation_id[:8]}",
                daemon=True,
            )
            self._fallback_thread = thread
            thread.start()
        if not self._fallback_loop_ready.wait(timeout=self._start_timeout_seconds):
            return None
        with self._lock:
            return self._fallback_loop

    def _run_fallback_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with self._lock:
            self._fallback_loop = loop
        self._fallback_loop_ready.set()
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.close()


class GenerationCapabilityProvider:
    """Worker callback with cancellation acknowledgement, using the existing lane."""

    def __init__(self, *, generation_id, emit, pending):
        self._generation_id, self._emit, self._pending = generation_id, emit, pending

    async def invoke(self, capability_id, arguments, *, invocation):
        request_id = generation_request_id.get()
        if not request_id or not invocation.active:
            return CapabilityResult(is_error=True, status="rejected", reason="capability_invocation_required")
        callback_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._pending[callback_id] = future
        self._emit({
            "protocol": PLUGIN_GENERATION_PROTOCOL, "type": "callback_request",
            "generation_id": self._generation_id, "callback_id": callback_id,
            "callback": "capability.invoke", "invocation_id": request_id,
            "capability_id": capability_id, "arguments": arguments,
        })
        try:
            try:
                response = await asyncio.shield(future)
            except asyncio.CancelledError:
                self._emit({
                    "protocol": PLUGIN_GENERATION_PROTOCOL, "type": "callback_cancel",
                    "generation_id": self._generation_id, "callback_id": callback_id,
                })
                # Parent cancellation only acknowledges after the real target
                # has drained. Do not abandon a reverse call still doing work.
                response, _ = await drain(future)
            if not response.get("ok"):
                return CapabilityResult(is_error=True, status="error", reason=str(response.get("reason") or "capability_callback_failed"))
            return capability_result_from_wire(response.get("result"))
        except PluginGenerationCodecError:
            return CapabilityResult(is_error=True, status="error", reason="capability_dependency_result_invalid")
        finally:
            self._pending.pop(callback_id, None)


class GenerationEventsProvider:
    def __init__(self, *, generation_id, emit, pending):
        self._generation_id, self._emit, self._pending = generation_id, emit, pending

    async def request(self, operation, payload, *, invocation):
        request_id = generation_request_id.get()
        if not request_id or not invocation.active:
            return event_rejection(operation, "event_invocation_required")
        callback_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._pending[callback_id] = future
        self._emit({
            "protocol": PLUGIN_GENERATION_PROTOCOL, "type": "callback_request", "generation_id": self._generation_id,
            "callback_id": callback_id, "callback": "events.request", "invocation_id": request_id,
            "operation": operation, "payload": payload,
        })
        try:
            try:
                response = await asyncio.shield(future)
            except asyncio.CancelledError:
                self._emit({"protocol": PLUGIN_GENERATION_PROTOCOL, "type": "callback_cancel",
                            "generation_id": self._generation_id, "callback_id": callback_id})
                response, _ = await drain(future)
            if not response.get("ok") or not isinstance(response.get("result"), dict):
                return event_rejection(operation, "event_callback_failed")
            return response["result"]
        finally:
            self._pending.pop(callback_id, None)


class GenerationTaskProvider:
    def __init__(self, *, generation_id, emit, pending):
        self._generation_id, self._emit, self._pending = generation_id, emit, pending

    async def request(self, operation, payload, *, invocation):
        request_id = generation_request_id.get()
        if not request_id or not invocation.active:
            return task_rejection(operation, "task_invocation_required")
        callback_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._pending[callback_id] = future
        self._emit({
            "protocol": PLUGIN_GENERATION_PROTOCOL, "type": "callback_request", "generation_id": self._generation_id,
            "callback_id": callback_id, "callback": "tasks.request", "invocation_id": request_id,
            "operation": operation, "payload": payload,
        })
        try:
            try:
                response = await asyncio.shield(future)
            except asyncio.CancelledError:
                self._emit({"protocol": PLUGIN_GENERATION_PROTOCOL, "type": "callback_cancel",
                            "generation_id": self._generation_id, "callback_id": callback_id})
                response, _ = await drain(future)
            if not response.get("ok") or not isinstance(response.get("result"), dict):
                return task_rejection(operation, "task_callback_failed")
            return response["result"]
        finally:
            self._pending.pop(callback_id, None)


class GenerationConnectionProvider:
    def __init__(self, *, generation_id, emit, pending):
        self._generation_id, self._emit, self._pending = generation_id, emit, pending

    async def resolve(self, name, *, invocation):
        request_id = generation_request_id.get()
        if not request_id or not invocation.active:
            return connection_rejected("connection_invocation_required")
        callback_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._pending[callback_id] = future
        self._emit({
            "protocol": PLUGIN_GENERATION_PROTOCOL, "type": "callback_request",
            "generation_id": self._generation_id, "callback_id": callback_id,
            "callback": "connection.resolve", "invocation_id": request_id, "name": name,
        })
        try:
            response = await future
            if not response.get("ok"):
                return connection_rejected(str(response.get("reason") or "connection_callback_failed"))
            return connection_result_from_wire(response.get("result"))
        except asyncio.CancelledError:
            self._emit({"protocol": PLUGIN_GENERATION_PROTOCOL, "type": "callback_cancel",
                        "generation_id": self._generation_id, "callback_id": callback_id})
            raise
        except (ValueError, TypeError):
            return connection_rejected("connection_result_invalid")
        finally:
            self._pending.pop(callback_id, None)


class GenerationResourceProvider:
    """Private callback; request identity is set by the worker, not the plugin."""

    def __init__(self, *, generation_id: str, emit: Callable, pending: dict) -> None:
        self._generation_id = generation_id
        self._emit = emit
        self._pending = pending

    async def open(self, target: str, *, invocation: ResourceInvocation,
                   representation: str = "original") -> PluginResourceResult:
        request_id = generation_request_id.get()
        if not request_id or not invocation.active:
            return PluginResourceResult(False, "rejected", "resource_invocation_required")
        if not isinstance(target, str) or not target.strip() or len(target) > 512:
            return PluginResourceResult(False, "rejected", "resource_target_invalid")
        if representation not in ("original", "document"):
            return PluginResourceResult(False, "rejected", "resource_representation_invalid")
        callback_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._pending[callback_id] = future
        self._emit({
            "protocol": PLUGIN_GENERATION_PROTOCOL, "type": "callback_request",
            "generation_id": self._generation_id, "callback_id": callback_id,
            "callback": "resource.open", "invocation_id": request_id, "target": target,
            "representation": representation,
        })
        try:
            response = await future
        except asyncio.CancelledError:
            self._emit({
                "protocol": PLUGIN_GENERATION_PROTOCOL, "type": "callback_cancel",
                "generation_id": self._generation_id, "callback_id": callback_id,
            })
            raise
        finally:
            self._pending.pop(callback_id, None)
        if not response.get("ok"):
            return PluginResourceResult(False, "error", str(response.get("reason") or "resource_callback_failed"))
        result = response.get("result")
        if not isinstance(result, Mapping) or not isinstance(result.get("ok"), bool):
            return PluginResourceResult(False, "error", "resource_result_invalid")
        if not result["ok"]:
            return PluginResourceResult(False, str(result.get("status") or "error"), str(result.get("reason") or "resource_open_failed"))
        if result.get("representation", "original") != representation:
            return PluginResourceResult(False, "error", "resource_representation_mismatch")
        path, size = result.get("path"), result.get("file_size")
        if not isinstance(path, str) or not Path(path).is_absolute() or isinstance(size, bool) or not isinstance(size, int) or size < 0:
            return PluginResourceResult(False, "error", "resource_result_invalid")
        return PluginResourceResult(True, "ready", path=Path(path), file_size=size,
                                    handle=str(result.get("handle") or ""), name=str(result.get("name") or ""),
                                    representation=representation)


class GenerationNotificationPort:
    """Worker-side notification port projected through the control lane."""

    def __init__(
        self,
        *,
        generation_id: str,
        emit: Callable[[Mapping[str, Any]], None],
        pending: dict[str, asyncio.Future[Mapping[str, Any]]],
    ) -> None:
        self._generation_id = generation_id
        self._emit = emit
        self._pending = pending

    async def send(self, intent: NotificationIntent) -> NotificationResult:
        try:
            wire_intent = notification_intent_to_wire(intent)
        except PluginGenerationCodecError:
            return NotificationResult(
                ok=False,
                status="rejected",
                reason="invalid_notification_intent",
            )
        callback_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._pending[callback_id] = future
        self._emit(
            {
                "protocol": PLUGIN_GENERATION_PROTOCOL,
                "type": "callback_request",
                "generation_id": self._generation_id,
                "callback_id": callback_id,
                "callback": "notification.send",
                "intent": wire_intent,
            }
        )
        try:
            response = await future
        except asyncio.CancelledError:
            self._emit(
                {
                    "protocol": PLUGIN_GENERATION_PROTOCOL,
                    "type": "callback_cancel",
                    "generation_id": self._generation_id,
                    "callback_id": callback_id,
                }
            )
            raise
        finally:
            self._pending.pop(callback_id, None)
        if not response.get("ok"):
            return NotificationResult(
                ok=False,
                status="error",
                reason=str(
                    response.get("reason") or "notification_callback_failed"
                ),
            )
        try:
            return notification_result_from_wire(response.get("result"))
        except PluginGenerationCodecError:
            return NotificationResult(
                ok=False,
                status="error",
                reason="invalid_notification_result",
            )


__all__ = [
    "GenerationHostCallbackRouter",
    "GenerationNotificationPort",
]
