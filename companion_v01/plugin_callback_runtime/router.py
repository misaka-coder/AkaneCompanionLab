"""Shared callback router, validated dispatch and control-lane responses."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Any, Mapping
from ..plugin_api import NotificationResult
from ..plugin_resources import ResourceInvocation
from ..plugin_generation_codec import (
    PluginGenerationCodecError,
    notification_intent_from_wire,
    notification_result_to_wire,
)
from ..plugin_generation_protocol import PLUGIN_GENERATION_PROTOCOL
from ..plugin_events import event_rejection
from ..plugin_tasks import task_rejection
from .scopes import CallbackInvocationScopes
from .host_ports import CallbackHostPorts
from .scheduling import CallbackScheduling


class GenerationHostCallbackRouter(CallbackInvocationScopes, CallbackHostPorts, CallbackScheduling):
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
        self._closed = False
        self._fallback_stop_requested = False
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
        with self._lock:
            closed = self._closed
        if closed:
            self._send_failure(callback_id, "callback_router_closed")
            return
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
