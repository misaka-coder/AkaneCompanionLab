"""Worker adapters for the existing generation control lane."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from typing import Any, Mapping
from ..plugin_api import NotificationIntent, NotificationResult, PluginResourceResult
from pathlib import Path
from capcore import CapabilityResult
from ..plugin_resources import ResourceInvocation, generation_request_id
from ..plugin_generation_codec import (
    PluginGenerationCodecError,
    notification_intent_to_wire,
    notification_result_from_wire,
    capability_result_from_wire,
)
from ..plugin_subprocess import drain
from ..plugin_connections import connection_result_from_wire, rejected as connection_rejected
from ..plugin_generation_protocol import PLUGIN_GENERATION_PROTOCOL
from ..plugin_events import event_rejection
from ..plugin_tasks import task_rejection


async def _request_callback(port, payload, *, acknowledge_cancel=False):
    """Own one pending frame, including failures before the first await."""
    callback_id = uuid.uuid4().hex
    future = asyncio.get_running_loop().create_future()
    port._pending[callback_id] = future
    try:
        port._emit(
            {
                "protocol": PLUGIN_GENERATION_PROTOCOL,
                "type": "callback_request",
                "generation_id": port._generation_id,
                "callback_id": callback_id,
                **payload,
            }
        )
        try:
            if acknowledge_cancel:
                return await asyncio.shield(future)
            return await future
        except asyncio.CancelledError:
            port._emit(
                {
                    "protocol": PLUGIN_GENERATION_PROTOCOL,
                    "type": "callback_cancel",
                    "generation_id": port._generation_id,
                    "callback_id": callback_id,
                }
            )
            if not acknowledge_cancel:
                raise
            # Capability/event/task cancellation waits for the real parent
            # outcome, including repeated cancellation during that wait.
            response, _ = await drain(future)
            return response
    finally:
        port._pending.pop(callback_id, None)


class GenerationCapabilityProvider:
    """Worker callback with cancellation acknowledgement, using the existing lane."""

    def __init__(self, *, generation_id, emit, pending):
        self._generation_id, self._emit, self._pending = generation_id, emit, pending

    async def invoke(self, capability_id, arguments, *, invocation):
        request_id = generation_request_id.get()
        if not request_id or not invocation.active:
            return CapabilityResult(is_error=True, status="rejected", reason="capability_invocation_required")
        try:
            response = await _request_callback(
                self,
                {
                    "callback": "capability.invoke",
                    "invocation_id": request_id,
                    "capability_id": capability_id,
                    "arguments": arguments,
                },
                acknowledge_cancel=True,
            )
            if not response.get("ok"):
                return CapabilityResult(
                    is_error=True, status="error", reason=str(response.get("reason") or "capability_callback_failed")
                )
            return capability_result_from_wire(response.get("result"))
        except PluginGenerationCodecError:
            return CapabilityResult(is_error=True, status="error", reason="capability_dependency_result_invalid")


class GenerationEventsProvider:
    def __init__(self, *, generation_id, emit, pending):
        self._generation_id, self._emit, self._pending = generation_id, emit, pending

    async def request(self, operation, payload, *, invocation):
        request_id = generation_request_id.get()
        if not request_id or not invocation.active:
            return event_rejection(operation, "event_invocation_required")
        response = await _request_callback(
            self,
            {"callback": "events.request", "invocation_id": request_id, "operation": operation, "payload": payload},
            acknowledge_cancel=True,
        )
        if not response.get("ok") or not isinstance(response.get("result"), dict):
            return event_rejection(operation, "event_callback_failed")
        return response["result"]


class GenerationTaskProvider:
    def __init__(self, *, generation_id, emit, pending):
        self._generation_id, self._emit, self._pending = generation_id, emit, pending

    async def request(self, operation, payload, *, invocation):
        request_id = generation_request_id.get()
        if not request_id or not invocation.active:
            return task_rejection(operation, "task_invocation_required")
        response = await _request_callback(
            self,
            {"callback": "tasks.request", "invocation_id": request_id, "operation": operation, "payload": payload},
            acknowledge_cancel=True,
        )
        if not response.get("ok") or not isinstance(response.get("result"), dict):
            return task_rejection(operation, "task_callback_failed")
        return response["result"]


class GenerationConnectionProvider:
    def __init__(self, *, generation_id, emit, pending):
        self._generation_id, self._emit, self._pending = generation_id, emit, pending

    async def resolve(self, name, *, invocation):
        request_id = generation_request_id.get()
        if not request_id or not invocation.active:
            return connection_rejected("connection_invocation_required")
        try:
            response = await _request_callback(
                self, {"callback": "connection.resolve", "invocation_id": request_id, "name": name}
            )
            if not response.get("ok"):
                return connection_rejected(str(response.get("reason") or "connection_callback_failed"))
            return connection_result_from_wire(response.get("result"))
        except (ValueError, TypeError):
            return connection_rejected("connection_result_invalid")


class GenerationResourceProvider:
    """Private callback; request identity is set by the worker, not the plugin."""

    def __init__(self, *, generation_id: str, emit: Callable, pending: dict) -> None:
        self._generation_id = generation_id
        self._emit = emit
        self._pending = pending

    async def open(
        self, target: str, *, invocation: ResourceInvocation, representation: str = "original"
    ) -> PluginResourceResult:
        request_id = generation_request_id.get()
        if not request_id or not invocation.active:
            return PluginResourceResult(False, "rejected", "resource_invocation_required")
        if not isinstance(target, str) or not target.strip() or len(target) > 512:
            return PluginResourceResult(False, "rejected", "resource_target_invalid")
        if representation not in ("original", "document"):
            return PluginResourceResult(False, "rejected", "resource_representation_invalid")
        response = await _request_callback(
            self,
            {
                "callback": "resource.open",
                "invocation_id": request_id,
                "target": target,
                "representation": representation,
            },
        )
        if not response.get("ok"):
            return PluginResourceResult(False, "error", str(response.get("reason") or "resource_callback_failed"))
        result = response.get("result")
        if not isinstance(result, Mapping) or not isinstance(result.get("ok"), bool):
            return PluginResourceResult(False, "error", "resource_result_invalid")
        if not result["ok"]:
            return PluginResourceResult(
                False, str(result.get("status") or "error"), str(result.get("reason") or "resource_open_failed")
            )
        if result.get("representation", "original") != representation:
            return PluginResourceResult(False, "error", "resource_representation_mismatch")
        path, size = (result.get("path"), result.get("file_size"))
        if (
            not isinstance(path, str)
            or not Path(path).is_absolute()
            or isinstance(size, bool)
            or (not isinstance(size, int))
            or (size < 0)
        ):
            return PluginResourceResult(False, "error", "resource_result_invalid")
        return PluginResourceResult(
            True,
            "ready",
            path=Path(path),
            file_size=size,
            handle=str(result.get("handle") or ""),
            name=str(result.get("name") or ""),
            representation=representation,
        )


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
            return NotificationResult(ok=False, status="rejected", reason="invalid_notification_intent")
        response = await _request_callback(self, {"callback": "notification.send", "intent": wire_intent})
        if not response.get("ok"):
            return NotificationResult(
                ok=False, status="error", reason=str(response.get("reason") or "notification_callback_failed")
            )
        try:
            return notification_result_from_wire(response.get("result"))
        except PluginGenerationCodecError:
            return NotificationResult(ok=False, status="error", reason="invalid_notification_result")
