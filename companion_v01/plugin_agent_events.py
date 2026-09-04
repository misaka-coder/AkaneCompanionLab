"""Host-owned bridge for contextual Agent events from plugin workers.

The bridge only namespaces and validates the public request. The bound host
port owns session resolution, MemCore policy, model execution, and delivery.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from dataclasses import replace
from typing import Any, Awaitable, Callable, Mapping

from .plugin_api import PluginAgentEventRequest, PluginAgentEventResult, PluginExternalEvent


MAX_AGENT_EVENT_TRACE_CHARS = 160
MAX_AGENT_EVENT_REFERENCE_CHARS = 4096
MAX_AGENT_EVENT_MESSAGE_CHARS = 12_000
MAX_AGENT_EVENT_IDEMPOTENCY_KEY_CHARS = 240
MAX_AGENT_EVENT_TEXT_AFFIX_CHARS = 2000
MAX_AGENT_EVENT_STRIP_ADDRESSES = 8
MAX_AGENT_EVENT_STRIP_ADDRESS_CHARS = 40
MAX_AGENT_EVENT_FIELDS = 16
MAX_AGENT_EVENT_FIELD_CHARS = 4000
MAX_AGENT_EVENT_FIELD_TOTAL_CHARS = 12_000
MAX_AGENT_EVENT_TASKS = 256
AGENT_EVENT_RESULT_REUSE_SECONDS = 600.0
_EVENT_TYPE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_EVENT_FIELD_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_RESERVED_EVENT_FIELDS = frozenset(
    {
        "api_key",
        "apikey",
        "access_token",
        "authorization",
        "cached_path",
        "file_path",
        "password",
        "path",
        "secret",
        "storage_relpath",
        "token",
    }
)


class _PluginScopedAgentEventPort:
    """Namespace one host Agent-event port to a single activated plugin."""

    def __init__(
        self,
        *,
        plugin_id: str,
        delegate: Any,
        availability_provider: Callable[[], bool],
    ) -> None:
        self._plugin_id = str(plugin_id or "").strip()
        self._delegate = delegate
        self._availability_provider = availability_provider
        self._task_lock = asyncio.Lock()
        self._tasks: dict[str, tuple[asyncio.Task[PluginAgentEventResult], float]] = {}

    async def submit(self, request: PluginAgentEventRequest) -> PluginAgentEventResult:
        try:
            available = bool(self._availability_provider())
        except Exception:
            available = False
        if not available:
            return PluginAgentEventResult(False, "host_unavailable", "host_unavailable")
        if not isinstance(request, PluginAgentEventRequest):
            return PluginAgentEventResult(False, "rejected", "invalid_agent_event_request")
        reason = _validate_agent_event_request(request)
        if reason:
            return PluginAgentEventResult(False, "rejected", reason)
        scoped_request = replace(
            request,
            event=PluginExternalEvent(
                event_type=request.event.event_type,
                source=self._plugin_id,
                fields=request.event.fields,
            ),
        )
        task_key = _agent_event_task_key(scoped_request)
        if task_key:
            task = await self._get_or_create_task(task_key, scoped_request)
            try:
                result = await asyncio.shield(task)
            except asyncio.CancelledError:
                raise
            except Exception:
                result = PluginAgentEventResult(False, "error", "agent_event_delegate_failed")
            if not result.ok:
                async with self._task_lock:
                    if self._tasks.get(task_key, (None, 0.0))[0] is task:
                        self._tasks.pop(task_key, None)
            return result
        try:
            result = await self._delegate.submit(scoped_request)
        except Exception:
            return PluginAgentEventResult(False, "error", "agent_event_delegate_failed")
        if not isinstance(result, PluginAgentEventResult):
            return PluginAgentEventResult(False, "error", "invalid_agent_event_result")
        return result

    async def _get_or_create_task(
        self,
        task_key: str,
        request: PluginAgentEventRequest,
    ) -> asyncio.Task[PluginAgentEventResult]:
        async with self._task_lock:
            self._prune_tasks_locked()
            existing = self._tasks.get(task_key)
            if existing is not None:
                return existing[0]
            task = asyncio.create_task(self._submit_scoped(request))
            self._tasks[task_key] = (task, time.monotonic())
            self._prune_tasks_locked()
            return task

    async def _submit_scoped(self, request: PluginAgentEventRequest) -> PluginAgentEventResult:
        try:
            result = await self._delegate.submit(request)
        except asyncio.CancelledError:
            raise
        except Exception:
            return PluginAgentEventResult(False, "error", "agent_event_delegate_failed")
        if not isinstance(result, PluginAgentEventResult):
            return PluginAgentEventResult(False, "error", "invalid_agent_event_result")
        return result

    def _prune_tasks_locked(self) -> None:
        now = time.monotonic()
        for key, (task, created_at) in tuple(self._tasks.items()):
            if task.done() and now - created_at >= AGENT_EVENT_RESULT_REUSE_SECONDS:
                self._tasks.pop(key, None)
        overflow = max(0, len(self._tasks) - MAX_AGENT_EVENT_TASKS)
        for key, (task, _created_at) in tuple(self._tasks.items()):
            if overflow <= 0:
                break
            if task.done():
                self._tasks.pop(key, None)
                overflow -= 1


AgentEventChannelHandler = Callable[
    [PluginAgentEventRequest, Mapping[str, str]],
    Awaitable[PluginAgentEventResult],
]


class HostAgentEventRouter:
    """Resolve one opaque conversation reference and route by source channel.

    Plugin workers bind to this single instance-owned port. Channel adapters
    register their ordinary delivery path here; they never replace the plugin
    port or create a second plugin runtime.
    """

    def __init__(self, resolver: Callable[[str], Mapping[str, str] | None]) -> None:
        if not callable(resolver):
            raise TypeError("invalid_conversation_reference_resolver")
        self._resolver = resolver
        self._handlers: dict[str, AgentEventChannelHandler] = {}

    def register_channel(self, channel: str, handler: AgentEventChannelHandler) -> None:
        normalized = str(channel or "").strip().lower()
        if not normalized or not callable(handler):
            raise ValueError("invalid_agent_event_channel_handler")
        self._handlers[normalized] = handler

    async def submit(self, request: PluginAgentEventRequest) -> PluginAgentEventResult:
        if not isinstance(request, PluginAgentEventRequest):
            return PluginAgentEventResult(False, "rejected", "invalid_agent_event_request")
        reason = _validate_agent_event_request(request)
        if reason:
            return PluginAgentEventResult(False, "rejected", reason)
        try:
            resolved = self._resolver(str(request.conversation_ref or ""))
        except Exception:
            resolved = None
        if not isinstance(resolved, Mapping):
            return PluginAgentEventResult(False, "rejected", "event_context_unresolved")
        channel = str(resolved.get("channel") or "").strip().lower()
        handler = self._handlers.get(channel)
        if handler is None:
            return PluginAgentEventResult(False, "host_unavailable", "agent_event_channel_unavailable")
        try:
            result = await handler(request, resolved)
        except asyncio.CancelledError:
            raise
        except Exception:
            return PluginAgentEventResult(False, "error", "agent_event_channel_failed")
        if not isinstance(result, PluginAgentEventResult):
            return PluginAgentEventResult(False, "error", "invalid_agent_event_result")
        return result


def _validate_agent_event_request(request: PluginAgentEventRequest) -> str:
    fields = (
        ("trace_id", request.trace_id, MAX_AGENT_EVENT_TRACE_CHARS, True),
        ("conversation_ref", request.conversation_ref, MAX_AGENT_EVENT_REFERENCE_CHARS, True),
        ("message", request.message, MAX_AGENT_EVENT_MESSAGE_CHARS, True),
        (
            "memory_idempotency_key",
            request.memory_idempotency_key,
            MAX_AGENT_EVENT_IDEMPOTENCY_KEY_CHARS,
            False,
        ),
        ("text_prefix", request.text_prefix, MAX_AGENT_EVENT_TEXT_AFFIX_CHARS, False),
        ("text_suffix", request.text_suffix, MAX_AGENT_EVENT_TEXT_AFFIX_CHARS, False),
    )
    for name, value, maximum, required in fields:
        if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
            return f"invalid_{name}"
        if required and not value.strip():
            return f"{name}_required"
    if not isinstance(request.delivery, str) or request.delivery not in {"current_turn", "timeline"}:
        return "unsupported_event_delivery"
    if not isinstance(request.text_delivery, str) or request.text_delivery not in {"default", "single_message"}:
        return "unsupported_text_delivery"
    addresses = request.text_strip_leading_addresses
    if not isinstance(addresses, tuple) or len(addresses) > MAX_AGENT_EVENT_STRIP_ADDRESSES:
        return "invalid_text_strip_leading_addresses"
    seen_addresses: set[str] = set()
    for address in addresses:
        if (
            not isinstance(address, str)
            or not address.strip()
            or len(address) > MAX_AGENT_EVENT_STRIP_ADDRESS_CHARS
            or "\x00" in address
            or address in seen_addresses
        ):
            return "invalid_text_strip_leading_addresses"
        seen_addresses.add(address)
    if addresses and request.text_delivery != "single_message":
        return "text_strip_leading_addresses_requires_single_message"
    event = request.event
    if not isinstance(event, PluginExternalEvent):
        return "invalid_external_event"
    if not isinstance(event.event_type, str) or _EVENT_TYPE_PATTERN.fullmatch(event.event_type) is None:
        return "invalid_external_event_type"
    if not isinstance(event.fields, tuple) or not event.fields or len(event.fields) > MAX_AGENT_EVENT_FIELDS:
        return "invalid_external_event_fields"
    total_chars = len(event.event_type)
    seen: set[str] = set()
    for item in event.fields:
        if not isinstance(item, tuple) or len(item) != 2:
            return "invalid_external_event_fields"
        key, value = item
        if (
            not isinstance(key, str)
            or key in seen
            or key.lower() in _RESERVED_EVENT_FIELDS
            or _EVENT_FIELD_PATTERN.fullmatch(key) is None
            or not isinstance(value, str)
            or not value
            or len(value) > MAX_AGENT_EVENT_FIELD_CHARS
            or "\x00" in value
        ):
            return "invalid_external_event_fields"
        seen.add(key)
        total_chars += len(key) + len(value)
    if total_chars > MAX_AGENT_EVENT_FIELD_TOTAL_CHARS:
        return "invalid_external_event_fields"
    return ""


def _agent_event_task_key(request: PluginAgentEventRequest) -> str:
    key = str(request.memory_idempotency_key or "").strip()
    if not key:
        return ""
    material = "\x00".join((request.conversation_ref, request.event.event_type, key))
    return hashlib.sha256(material.encode("utf-8", errors="strict")).hexdigest()


__all__ = ["HostAgentEventRouter", "_PluginScopedAgentEventPort"]
