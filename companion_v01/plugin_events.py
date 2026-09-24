"""Host-owned event broker for supervised plugin contributions.

The broker observes immutable event snapshots and returns typed delivery
intentions.  It never writes MemCore, starts an Agent turn, or sends to a
channel itself; the owning channel/runtime applies those decisions through its
existing authoritative paths.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from collections import OrderedDict, deque
from contextvars import Context
from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Callable

from capcore import CapabilityResult, InvocationContext
from akane_plugin.events import Event, EventBinding, EventDelivery, EventReceipt, EventSubscription

from .plugin_api import (
    PluginInvocationContext,
    is_valid_capability_id,
    is_valid_plugin_id,
)
from .plugin_result_projection import sanitize_capability_result
from .plugin_resources import ResourceInvocation, current_resource_invocation
from .plugin_observations import PluginObservations
from akane_plugin.observations import ObservationReceipt
from akane_plugin.turns import TurnReceipt
from .plugin_subprocess import drain


DEFAULT_EVENT_HANDLER_TIMEOUT_SECONDS = 2.0
_EVENT_TYPE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,119}$")
_SAFE_REASON_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


@dataclass(frozen=True, slots=True)
class _PluginEventRegistration:
    plugin_id: str
    event_type: str
    subscription: EventSubscription
    handler: Any = field(default=None, repr=False, compare=False)
    generation_id: str = ""
    permissions: tuple[str, ...] = ()
    private_values: set[str] = field(default_factory=set, repr=False, compare=False)
    connections: tuple = ()

    @property
    def key(self):
        return (self.plugin_id, self.generation_id, self.subscription.subscription_id)


@dataclass
class _EventBinding:
    scope_id: str
    registration_key: tuple[str, str, str]
    context: InvocationContext


@dataclass
class _EventDelivery:
    registration: _PluginEventRegistration
    event: Event
    context: InvocationContext
    scope_id: str
    origin_context: InvocationContext
    turn_epoch: int = 0
    origin_request_id: str = ""
    delivery_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "queued"
    reason: str = ""
    value: Any = None
    has_value: bool = False
    cancel_requested: bool = False
    linked_dispatches: tuple[str, ...] = ()
    linked_turn_requests: tuple[str, ...] = ()
    task: asyncio.Task | None = None
    invocation: Any = None

    @property
    def lane(self):
        return (*self.registration.key, self.scope_id)

    @property
    def complete(self):
        return self.status in {"completed", "failed", "cancelled", "superseded"}

    def snapshot(self):
        return EventDelivery(self.registration.subscription.subscription_id, self.scope_id, self.status,
                             self.reason, deepcopy(self.value), self.has_value, self.cancel_requested,
                             self.linked_dispatches, self.linked_turn_requests)


@dataclass
class _EventDispatch:
    dispatch_id: str
    event: Event
    owner: str
    context_key: tuple[str, str, str]
    deliveries: tuple[_EventDelivery, ...]
    parent_delivery_id: str = ""
    event_key: str = ""
    timeline_recorded: bool = False
    done: asyncio.Event = field(default_factory=asyncio.Event)

    def snapshot(self):
        states = {item.status for item in self.deliveries}
        complete = all(item.complete for item in self.deliveries)
        if not states:
            status = "unobserved"
        elif not complete:
            status = "accepted" if states == {"queued"} else "running"
        elif states == {"completed"}:
            status = "completed"
        elif len(states) == 1:
            status = next(iter(states))
        else:
            status = "partially_failed"
        if complete:
            self.done.set()
        return EventReceipt(self.dispatch_id, self.event.event_id, status, complete,
                            deliveries=tuple(item.snapshot() for item in self.deliveries))


def validate_event_subscription(subscription: EventSubscription) -> str:
    if not isinstance(subscription, EventSubscription):
        return "event_subscription_invalid"
    if not is_valid_capability_id(subscription.subscription_id):
        return "event_subscription_id_invalid"
    if not isinstance(subscription.event_type, str) or not _normalize_event_type(subscription.event_type):
        return "event_type_invalid"
    if not isinstance(subscription.sources, tuple) or any(
        not isinstance(source, str) or (source != "@host" and not is_valid_plugin_id(source))
        for source in subscription.sources
    ):
        return "event_sources_invalid"
    if not isinstance(subscription.scope, str) or subscription.scope not in {"global", "conversation"}:
        return "event_scope_invalid"
    if not isinstance(subscription.coalesce, str) or subscription.coalesce not in {"queue", "latest"}:
        return "event_coalesce_invalid"
    if subscription.persistence not in {"none", "timeline"}:
        return "event_persistence_invalid"
    return ""


class PluginEventBroker:
    """Publish events and run declared subscriptions; the host owns the effects."""

    def __init__(
        self,
        registrations: tuple[_PluginEventRegistration, ...],
        *,
        handler_timeout_seconds: float = DEFAULT_EVENT_HANDLER_TIMEOUT_SECONDS,
        availability_provider: Callable[[], bool] | None = None,
        registrations_provider: Callable[[], tuple[_PluginEventRegistration, ...]] | None = None,
        executor: Callable | None = None,
        owners_provider: Callable[[], dict[str, str]] | None = None,
        runtime_loop_provider: Callable | None = None,
        receipt_limit: int = 1_000,
    ) -> None:
        self._handler_timeout_seconds = max(0.01, float(handler_timeout_seconds))
        self._availability_provider = availability_provider or (lambda: True)
        self._static_registrations = tuple(registrations)
        self._registrations_provider = registrations_provider
        self._executor = executor
        self._owners_provider = owners_provider
        self._runtime_loop_provider = runtime_loop_provider
        self._receipt_limit = max(1, int(receipt_limit))
        self._dispatches: OrderedDict[str, _EventDispatch] = OrderedDict()
        self._bindings: dict[str, _EventBinding] = {}
        self._lanes: dict[tuple, deque] = {}
        self._workers: dict[tuple, asyncio.Task] = {}
        self.observations = PluginObservations(self._observation_live)
        self.turn_router = None
        self._timeline_recorder = None
        # Host-signed event stream version: (generation, scope, event_type) -> count.
        # Reset whenever the active plugin generation changes; never persisted.
        self._event_version_epoch = ""
        self._event_versions: dict[tuple[str, str], int] = {}

    async def _on_loop(self, function, *args, **kwargs):
        loop = self._runtime_loop_provider() if self._runtime_loop_provider else None
        if loop is not None and loop is not asyncio.get_running_loop():
            if loop.is_closed() or not loop.is_running():
                raise RuntimeError("event_runtime_unavailable")
            return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(function(*args, **kwargs), loop))
        return await function(*args, **kwargs)

    def bind_turn_router(self, router):
        if callable(getattr(router, "request_turn", None)):
            self.turn_router = router
            router.observations = self.observations

    def bind_timeline_recorder(self, recorder) -> None:
        """Bind the host's existing public timeline port; never a second store."""

        self._timeline_recorder = recorder if callable(recorder) else None

    def generation_active(self, plugin_id, generation_id):
        return self._available() and (not self._owners_provider or self._owners_provider().get(plugin_id) == generation_id)

    def capture_scope(self, context, delivery_id=""):
        if delivery_id:
            delivery = next((item for record in self._dispatches.values() for item in record.deliveries
                             if item.delivery_id == delivery_id), None)
            if delivery is not None:
                return delivery.turn_epoch, delivery.origin_request_id
        return self.turn_router.capture_scope(context) if self.turn_router else (0, "")

    def initialize_scope(self, invocation):
        parent = current_resource_invocation.get()
        if parent is not None and parent.turn_epoch is not None:
            invocation.turn_epoch, invocation.origin_request_id = parent.turn_epoch, parent.origin_request_id
        else:
            invocation.turn_epoch, invocation.origin_request_id = self.capture_scope(invocation.context, invocation.event_delivery_id)

    async def request(self, operation: str, payload: dict, *, invocation: ResourceInvocation) -> dict:
        return await self._on_loop(self._request, operation, payload, invocation=invocation)

    async def _request(self, operation, payload, *, invocation):
        if not isinstance(operation, str) or operation not in {"emit", "status", "bind", "unbind", "observe", "timeline_append", "request_turn", "turn_status", "cancel_turn"} or not isinstance(payload, dict):
            return event_rejection("status", "event_request_invalid")
        if not invocation.active:
            return event_rejection(operation, "event_invocation_expired")
        event_context = invocation.event_origin_context or invocation.context
        if operation == "status":
            record = self._dispatches.get(payload.get("dispatch_id"))
            if record is None:
                return event_rejection(operation, "event_dispatch_not_found")
            if record.owner != invocation.plugin_id or record.context_key != _context_key(event_context):
                return event_rejection(operation, "event_dispatch_access_denied")
            return record.snapshot().as_dict()
        if operation in {"turn_status", "cancel_turn"}:
            if not invocation.can_request_turn:
                return event_rejection(operation, "agent_turn_request_permission_required")
            if self.turn_router is None:
                return event_rejection(operation, "agent_turn_provider_unavailable")
            return self.turn_router.receipt(payload.get("request_id"), invocation=invocation,
                                            cancel=operation == "cancel_turn").as_dict()
        if not self._available():
            return event_rejection(operation, "event_host_unavailable")
        if self._owners_provider and self._owners_provider().get(invocation.plugin_id) != invocation.generation_id:
            return event_rejection(operation, "event_publisher_expired")
        self._revoke_stale()
        if operation == "request_turn":
            if not invocation.can_request_turn:
                return event_rejection(operation, "agent_turn_request_permission_required")
            if self.turn_router is None:
                return event_rejection(operation, "agent_turn_provider_unavailable")
            binding = ""
            if invocation.event_delivery_id:
                delivery = next((item for record in self._dispatches.values() for item in record.deliveries
                                 if item.delivery_id == invocation.event_delivery_id), None)
                if delivery is None:
                    return event_rejection(operation, "agent_turn_scope_expired")
                binding = delivery.scope_id
            plugin_id, generation_id = invocation.plugin_id, invocation.generation_id
            def live():
                return self.generation_active(plugin_id, generation_id) and (not binding or binding in self._bindings)
            return (await self.turn_router.request_turn(payload, invocation=invocation, live=live, binding=binding)).as_dict()
        if operation == "observe":
            if not invocation.can_observe_context:
                return event_rejection(operation, "context_observe_permission_required")
            binding = ""
            if invocation.event_delivery_id:
                delivery = next((item for record in self._dispatches.values() for item in record.deliveries
                                 if item.delivery_id == invocation.event_delivery_id), None)
                if delivery is None:
                    return event_rejection(operation, "observation_scope_expired")
                binding = delivery.scope_id
            return await self.observations.observe(payload, invocation=invocation, binding=binding)
        if operation == "emit":
            event_key = payload.get("event_key", "")
            if not isinstance(event_key, str) or "\x00" in event_key:
                return event_rejection(operation, "event_key_invalid")
            if "event_id" in payload:
                return event_rejection(operation, "event_id_unsupported")
            occurred_at_ms = payload.get("occurred_at_ms")
            if occurred_at_ms is not None and (isinstance(occurred_at_ms, bool) or not isinstance(occurred_at_ms, int)):
                return event_rejection(operation, "occurred_at_ms_invalid")
            return self._emit(payload, owner=invocation.plugin_id, context=event_context,
                              parent_delivery_id=invocation.event_delivery_id,
                              origin_request_id=invocation.origin_request_id).as_dict()
        if operation == "timeline_append":
            return await self._timeline_append(payload, invocation=invocation)
        if operation == "bind":
            return self._bind(payload, invocation).as_dict()
        if operation == "unbind":
            return await self._unbind(payload, invocation)
        return event_rejection(operation, "event_operation_invalid")

    def _observation_live(self, item):
        return (self._available() and
                (not self._owners_provider or self._owners_provider().get(item.owner) == item.generation) and
                (not item.binding or item.binding in self._bindings))

    async def emit(self, event_type: str, data: Any, *, context: InvocationContext | None = None,
                   event_key: str = "", occurred_at_ms: int | None = None,
                   ) -> EventReceipt:
        """Trusted host publication; plugins emit through their scoped port."""
        async def publish():
            if not self._available():
                return EventReceipt("", "", "rejected", True, "event_host_unavailable")
            self._revoke_stale()
            return self._emit({"event_type": event_type, "data": data,
                               "event_key": event_key, "occurred_at_ms": occurred_at_ms},
                              owner="@host", context=context or InvocationContext())
        return await self._on_loop(publish)

    async def receipt(self, dispatch_id: str) -> EventReceipt:
        """Host inspection; plugin inspection also verifies owner and scope."""
        async def read():
            record = self._dispatches.get(dispatch_id)
            return record.snapshot() if record else EventReceipt(dispatch_id, "", "rejected", True, "event_dispatch_not_found")
        return await self._on_loop(read)

    def _event_version(self, scope: str, event_type: str) -> int:
        """Next host-signed version for one (generation, scope, event_type) stream."""

        owners = self._owners_provider() if self._owners_provider else {}
        epoch = "|".join(f"{key}={value}" for key, value in sorted(owners.items()))
        if epoch != self._event_version_epoch:
            self._event_version_epoch = epoch
            self._event_versions.clear()
        key = (scope, event_type)
        self._event_versions[key] = self._event_versions.get(key, 0) + 1
        return self._event_versions[key]

    @staticmethod
    def _occurred_at_ms(value: Any, fallback_ms: int) -> tuple[int, bool]:
        """Accept any integer timestamp; never reject an event because of it."""

        if isinstance(value, bool) or not isinstance(value, int):
            return fallback_ms, False
        return value, True

    def _routing_targets(self, event_type: str, owner: str, context) -> list[tuple[Any, str, Any]]:
        """Resolve every subscriber target for one publication, in order."""

        targets: list[tuple[Any, str, Any]] = []
        for registration in self._current_registrations():
            subscription = registration.subscription
            if subscription is None or registration.event_type != event_type:
                continue
            if subscription.sources and owner not in subscription.sources:
                continue
            if subscription.scope == "global":
                targets.append((registration, uuid.uuid5(uuid.NAMESPACE_URL, ":".join(registration.key)).hex,
                                PluginInvocationContext(global_scope=True)))
            else:
                targets.extend(
                    (registration, binding.scope_id, binding.context) for binding in self._bindings.values()
                    if binding.registration_key == registration.key and (
                        not _is_bound(context) or _context_key(context) == _context_key(binding.context))
                )
        return targets

    def _emit(self, payload, *, owner, context, parent_delivery_id="", origin_request_id=""):
        if any(_context_key(context)) and not _is_bound(context):
            return EventReceipt("", "", "rejected", True, "event_context_invalid")
        raw_type = payload.get("event_type")
        event_type = _normalize_event_type(raw_type) if isinstance(raw_type, str) else ""
        if not event_type:
            return EventReceipt("", "", "rejected", True, "event_type_invalid")
        event_key = payload.get("event_key", "")
        if not isinstance(event_key, str) or "\x00" in event_key:
            return EventReceipt("", "", "rejected", True, "event_key_invalid")
        data = sanitize_capability_result(CapabilityResult(is_error=False, status="ok", content=payload.get("data")))
        if data.is_error:
            return EventReceipt("", "", "rejected", True, data.reason)
        received_at_ms = int(time.time() * 1000)
        occurred_at_ms, occurred_at_trusted = self._occurred_at_ms(payload.get("occurred_at_ms"), received_at_ms)
        event = Event(uuid.uuid4().hex, event_type, owner, data.content)
        targets = self._routing_targets(event_type, owner, context)
        if event_key:
            for record in self._dispatches.values():
                if (record.owner == owner and record.event_key == event_key
                        and record.context_key == _context_key(context)
                        and record.event.event_type == event.event_type):
                    if json.dumps(record.event.data, sort_keys=True) != json.dumps(event.data, sort_keys=True):
                        return EventReceipt("", record.event.event_id, "rejected", True, "event_key_conflict")
                    return record.snapshot()
        # One host-signed identity and version per published event, shared by
        # every subscriber; the scope is the host's routing scope, never payload.
        scope = targets[0][1] if targets else (_context_key(context)[0] or "global")
        version = self._event_version(scope, event_type)
        event = replace(event, scope=scope, version=version, occurred_at_ms=occurred_at_ms,
                        received_at_ms=received_at_ms)
        deliveries = []
        for registration, scope_id, target_context in targets:
            # Each subscriber gets its own JSON snapshot, including nested values.
            snapshot = sanitize_capability_result(CapabilityResult(is_error=False, status="ok", content=event.data))
            # A conversation binding narrows the route. A global handler
            # preserves the incoming route without acquiring its identity.
            origin = target_context if _is_bound(target_context) else context
            deliveries.append(_EventDelivery(registration, replace(event, data=snapshot.content), target_context, scope_id, origin,
                                             self.capture_scope(origin)[0], origin_request_id))
        if deliveries and self._executor is None:
            return EventReceipt("", event.event_id, "rejected", True, "event_executor_unavailable")
        record = _EventDispatch(uuid.uuid4().hex, event, owner, _context_key(context), tuple(deliveries),
                                parent_delivery_id, event_key)
        self._dispatches[record.dispatch_id] = record
        for delivery in deliveries:
            lane = self._lanes.setdefault(delivery.lane, deque())
            if delivery.registration.subscription.coalesce == "latest":
                for pending in lane:
                    if pending.status == "queued":
                        pending.status, pending.reason = "superseded", "event_replaced_by_latest"
            lane.append(delivery)
            if delivery.lane not in self._workers:
                self._workers[delivery.lane] = asyncio.create_task(self._run_lane(delivery.lane),
                                                                  name="plugin-event-subscription", context=Context())
        self._refresh_receipts()
        return record.snapshot()

    async def _timeline_append(self, payload, *, invocation) -> dict:
        """Persist one delivered event through the host's public timeline port.

        Identity, scope and idempotency stay host-owned: the plugin supplies only
        the event it received, and the durable source id is derived from the
        host-signed event id.
        """

        context = invocation.event_origin_context or invocation.context
        if getattr(context, "global_scope", False) or not context.profile_user_id or not context.session_id:
            return event_rejection("timeline_append", "context_unbound")
        event_id = payload.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            return event_rejection("timeline_append", "timeline_event_required")
        record = next((item for item in self._dispatches.values()
                       if item.event.event_id == event_id), None)
        if record is None:
            return event_rejection("timeline_append", "timeline_event_not_delivered")
        # Only a plugin this event was actually delivered to may record it; the
        # publisher itself and unrelated plugins get the same rejection.
        if not any(item.registration.plugin_id == invocation.plugin_id for item in record.deliveries):
            return event_rejection("timeline_append", "timeline_event_not_delivered")
        if self._timeline_recorder is None:
            return event_rejection("timeline_append", "timeline_recorder_unavailable")
        event = record.event
        source_id = f"plugin-event:{event.event_id}"
        return await self._record_timeline(event, context, source_id=source_id, operation="timeline_append")

    async def _record_timeline(self, event: Event, context, *, source_id: str, operation: str) -> dict:
        """Persist one event through the host's public timeline port."""

        try:
            result = await asyncio.to_thread(
                self._timeline_recorder,
                {
                    "source_id": source_id,
                    "event": {
                        "event_type": event.event_type,
                        "source": event.source,
                        "fields": {"data": event.data},
                    },
                    "timestamp": int(event.occurred_at_ms / 1000) or int(time.time()),
                    "user_id": str(context.session_id),
                    "real_user_id": str(context.profile_user_id),
                    "character_pack_id": str(getattr(context, "character_pack_id", "") or ""),
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return event_rejection(operation, "timeline_record_exception")
        if not isinstance(result, dict) or result.get("ok") is not True:
            reason = result.get("reason") if isinstance(result, dict) else ""
            return event_rejection(operation, str(reason or "timeline_record_failed"))
        return {"event_id": event.event_id, "status": "recorded", "reason": "", "source_id": source_id}

    async def _apply_subscription_persistence(self, delivery) -> str:
        """Record a timeline-declaring subscription's event exactly once.

        Returns an empty string on success, or the real failure reason. The host
        never reports a completed delivery when the declared durable record was
        not written.
        """

        subscription = delivery.registration.subscription
        if subscription is None or getattr(subscription, "persistence", "none") != "timeline":
            return ""
        record = next((item for item in self._dispatches.values()
                       if item.event.event_id == delivery.event.event_id), None)
        if record is None or record.timeline_recorded:
            return ""
        record.timeline_recorded = True
        context = delivery.origin_context if _is_bound(delivery.origin_context) else delivery.context
        if getattr(context, "global_scope", False) or not context.profile_user_id or not context.session_id:
            return "context_unbound"
        if self._timeline_recorder is None:
            return "timeline_recorder_unavailable"
        outcome = await self._record_timeline(
            delivery.event, context, source_id=f"plugin-event:{delivery.event.event_id}",
            operation="timeline_append",
        )
        return "" if outcome.get("status") == "recorded" else str(outcome.get("reason") or "timeline_record_failed")

    def _bind(self, payload, invocation):
        subscription_id = payload.get("subscription_id")
        registration = next((item for item in self._current_registrations() if item.subscription and
                             item.plugin_id == invocation.plugin_id and item.subscription.subscription_id == subscription_id), None)
        if registration is None:
            return EventBinding("", "", "rejected", "event_subscription_unavailable")
        if registration.subscription.scope != "conversation":
            return EventBinding(subscription_id, "", "rejected", "event_binding_not_required")
        if not _is_bound(invocation.context):
            return EventBinding(subscription_id, "", "rejected", "context_unbound")
        for binding in self._bindings.values():
            if binding.registration_key == registration.key and _context_key(binding.context) == _context_key(invocation.context):
                return EventBinding(subscription_id, binding.scope_id, "bound")
        binding = _EventBinding(uuid.uuid4().hex, registration.key, invocation.context)
        self._bindings[binding.scope_id] = binding
        return EventBinding(subscription_id, binding.scope_id, "bound")

    async def _unbind(self, payload, invocation):
        binding = self._bindings.get(payload.get("scope_id"))
        if binding is None or binding.registration_key[0] != invocation.plugin_id or _context_key(binding.context) != _context_key(invocation.context):
            return asdict(EventBinding("", "", "rejected", "event_binding_unavailable"))
        self._bindings.pop(binding.scope_id)
        self.observations.prune()
        if self.turn_router:
            self.turn_router.reconcile()
        tasks = self._cancel_matching(lambda item: item.scope_id == binding.scope_id, "event_binding_revoked")
        own_task = next((item.task for record in self._dispatches.values() for item in record.deliveries
                         if item.delivery_id == invocation.event_delivery_id), None)
        tasks = [task for task in tasks if task is not own_task]
        if tasks:
            await drain(asyncio.ensure_future(asyncio.gather(*tasks, return_exceptions=True)))
        return asdict(EventBinding(binding.registration_key[2], binding.scope_id, "unbound"))

    async def _run_lane(self, key):
        try:
            lane = self._lanes[key]
            while lane:
                delivery = lane.popleft()
                if delivery.status != "queued":
                    continue
                delivery.task = asyncio.create_task(self._run_delivery(delivery), name="plugin-event-handler")
                try:
                    await delivery.task
                except asyncio.CancelledError:
                    # Cancellation can arrive before the handler task starts.
                    delivery.status = "cancelled"
                    delivery.reason = delivery.reason or "event_handler_cancelled"
                    self._refresh_receipts()
        finally:
            self._workers.pop(key, None)
            self._lanes.pop(key, None)
            self._refresh_receipts()

    async def _run_delivery(self, delivery):
        delivery.status = "running"
        invocation = None
        invocation_out: list = [None]
        try:
            # The executor keeps its ResourceInvocation open for the handler; keep
            # a reference so a declared `request_turn` can use the same scope.
            result = await self._executor(delivery.registration, delivery.event, context=delivery.context,
                                          scope_id=delivery.scope_id, delivery_id=delivery.delivery_id,
                                          origin_context=delivery.origin_context,
                                          invocation_out=invocation_out,
                                          keep_scope_open=self._subscription_requests_turn(delivery))
            invocation = invocation_out[0]
            delivery.invocation = invocation
            if not isinstance(result, CapabilityResult):
                raise TypeError("event_handler_result_invalid")
            result = sanitize_capability_result(result)
            if result.status == "turn_deferred" and not result.is_error:
                request_id = result.content.get("request_id") if isinstance(result.content, dict) else None
                if self.turn_router is None:
                    result = CapabilityResult(is_error=True, status="error", reason="agent_turn_router_unavailable")
                else:
                    delivery.status = "waiting"
                    delivery.linked_turn_requests = (request_id,) if isinstance(request_id, str) else ()
                    receipt = await self.turn_router.wait_request(request_id, delivery=delivery)
                    result = CapabilityResult(is_error=receipt.status != "completed", status=receipt.status,
                                              reason=receipt.reason, content=receipt.as_dict())
            if result.status == "event_deferred" and not result.is_error:
                child_id = result.content.get("dispatch_id") if isinstance(result.content, dict) else None
                child = self._dispatches.get(child_id)
                if child is None or child.parent_delivery_id != delivery.delivery_id:
                    result = CapabilityResult(is_error=True, status="error", reason="event_link_invalid")
                elif self._link_would_cycle(delivery, child):
                    child_ids = {item.delivery_id for item in child.deliveries}
                    tasks = self._cancel_matching(lambda item: item.delivery_id in child_ids, "event_link_cycle")
                    if tasks:
                        await drain(asyncio.ensure_future(asyncio.gather(*tasks, return_exceptions=True)))
                    result = CapabilityResult(is_error=True, status="error", reason="event_link_cycle")
                else:
                    delivery.linked_dispatches = (child_id,)
                    delivery.status = "waiting"
                    await child.done.wait()
                    receipt = child.snapshot()
                    result = CapabilityResult(is_error=receipt.status not in {"completed", "unobserved"},
                                              status="ok", reason="" if receipt.status in {"completed", "unobserved"} else "event_link_failed",
                                              content=receipt.as_dict())
            if not result.is_error and self._subscription_requests_turn(delivery):
                result = await self._request_subscription_turn(delivery, result, invocation=invocation)
            if not result.is_error:
                persistence_failure = await self._apply_subscription_persistence(delivery)
                if persistence_failure:
                    result = CapabilityResult(is_error=True, status="error", reason=persistence_failure)
            delivery.status = "failed" if result.is_error else "completed"
            delivery.reason = result.reason
            delivery.value = result.value if result.has_value else result.content
            delivery.has_value = True
        except asyncio.CancelledError:
            tasks = []
            if self.turn_router:
                self.turn_router.cancel_delivery(delivery.delivery_id)
                for request_id in delivery.linked_turn_requests:
                    await drain(asyncio.create_task(self.turn_router.wait_request(request_id, delivery=delivery)))
            for child_id in delivery.linked_dispatches:
                child = self._dispatches.get(child_id)
                if child:
                    tasks.extend(self._cancel_matching(lambda item: item in child.deliveries, "event_parent_cancelled"))
            if tasks:
                await drain(asyncio.ensure_future(asyncio.gather(*tasks, return_exceptions=True)))
            delivery.status = "cancelled"
            delivery.reason = delivery.reason or "event_handler_cancelled"
        except Exception:
            delivery.status, delivery.reason = "failed", "event_handler_exception"
        finally:
            if invocation is not None:
                close_scope = getattr(invocation, "aclose", None)
                if callable(close_scope):
                    await close_scope()
            if self.turn_router:
                self.turn_router.release_delivery(delivery.delivery_id, cancel=delivery.status == "cancelled")
            self._refresh_receipts()

    @staticmethod
    def _subscription_requests_turn(delivery) -> bool:
        subscription = delivery.registration.subscription
        return bool(subscription is not None and getattr(subscription, "request_turn", False))

    async def _request_subscription_turn(self, delivery, result, invocation=None):
        """Apply a subscription's declared ``request_turn`` through the normal queue.

        The handler did not ask for a turn itself, so the host asks on its behalf
        and waits for the real terminal receipt. This never writes chat by itself
        and never replaces a receipt the handler already returned.
        """

        if self.turn_router is None:
            return CapabilityResult(is_error=True, status="error", reason="agent_turn_router_unavailable")
        if invocation is None or not getattr(invocation, "active", False):
            return CapabilityResult(is_error=True, status="error", reason="agent_turn_scope_expired")
        event = delivery.event
        subscription_id = delivery.registration.subscription.subscription_id
        receipt = await self.turn_router.request_turn(
            {
                "reason": f"event:{event.event_type}",
                "data": event.data,
                "observations": {},
                "stale": "latest",
                "coalesce_key": f"{subscription_id}:{delivery.scope_id}",
            },
            invocation=invocation,
            live=lambda: True,
            binding=delivery.scope_id,
        )
        if receipt.request_id:
            delivery.linked_turn_requests = (*delivery.linked_turn_requests, receipt.request_id)
        if receipt.status == "rejected":
            return CapabilityResult(is_error=True, status=receipt.status, reason=receipt.reason,
                                    content=receipt.as_dict())
        delivery.status = "waiting"
        terminal = await self.turn_router.wait_request(receipt.request_id, delivery=delivery)
        return CapabilityResult(is_error=terminal.status != "completed", status=terminal.status,
                                reason=terminal.reason, content=terminal.as_dict())

    def _cancel_matching(self, predicate, reason):
        tasks = []
        for record in tuple(self._dispatches.values()):
            for item in record.deliveries:
                if not item.complete and predicate(item):
                    item.cancel_requested = True
                    item.reason = reason
                    if item.task is None:
                        item.status = "cancelled"
                    elif not item.task.done():
                        item.task.cancel()
                        tasks.append(item.task)
        self._refresh_receipts()
        return tasks

    def _link_would_cycle(self, current, child):
        deliveries = [item for record in self._dispatches.values() for item in record.deliveries]
        seen = set()
        def waits_for_current(item):
            if item.complete:
                return False
            if item.delivery_id == current.delivery_id:
                return True
            if item.delivery_id in seen:
                return False
            seen.add(item.delivery_id)
            if item.status == "queued":
                running = next((other for other in deliveries if other.lane == item.lane and
                                other.status in {"running", "waiting"}), None)
                if running and waits_for_current(running):
                    return True
            for linked in item.linked_dispatches:
                record = self._dispatches.get(linked)
                if record and any(waits_for_current(other) for other in record.deliveries):
                    return True
            return False
        return any(waits_for_current(item) for item in child.deliveries)

    def status_snapshot(self):
        records = tuple(self._dispatches.values())
        return {
            "mode": "ephemeral", "handler_timeout_seconds": None,
            "terminal_receipt_limit": self._receipt_limit,
            "retained_receipts": len(records),
            "conversation_bindings": len(self._bindings),
            "pending_deliveries": sum(item.status == "queued" for record in records for item in record.deliveries),
            "running_deliveries": sum(item.status in {"running", "waiting"} for record in records for item in record.deliveries),
        }

    def _revoke_stale(self):
        keys = {item.key for item in self._current_registrations() if item.subscription}
        self._bindings = {key: value for key, value in self._bindings.items() if value.registration_key in keys}
        self.observations.prune()
        if self.turn_router:
            self.turn_router.reconcile()
        tasks = self._cancel_matching(lambda item: item.registration.key not in keys, "event_subscription_revoked")
        tasks.extend(worker for lane, worker in self._workers.items() if lane[:3] not in keys)
        return tasks

    async def reconcile(self):
        """Withdraw removed generations before their process drain begins."""
        async def reconcile():
            tasks = self._revoke_stale()
            if tasks:
                await drain(asyncio.ensure_future(asyncio.gather(*tasks, return_exceptions=True)))
        await self._on_loop(reconcile)

    def _refresh_receipts(self):
        for record in self._dispatches.values():
            if all(item.complete for item in record.deliveries):
                record.done.set()
        excess = len(self._dispatches) - self._receipt_limit
        if excess > 0:
            pinned = {child for record in self._dispatches.values() for item in record.deliveries
                      if not item.complete for child in item.linked_dispatches}
            active_deliveries = {item.delivery_id for record in self._dispatches.values()
                                 for item in record.deliveries if not item.complete}
            # A quick child may finish before the worker has returned its
            # receipt to link it. Retain it through that parent's execution.
            pinned.update(key for key, record in self._dispatches.items()
                          if record.parent_delivery_id in active_deliveries)
            for key, record in tuple(self._dispatches.items()):
                if excess <= 0:
                    break
                if record.done.is_set() and key not in pinned:
                    self._dispatches.pop(key)
                    excess -= 1

    def _available(self):
        try:
            return bool(self._availability_provider())
        except Exception:
            return False

    @property
    def registered_event_types(self) -> tuple[str, ...]:
        return tuple(sorted({item.event_type for item in self._current_registrations()}))

    def observes(self, event_type: str) -> bool:
        normalized = _normalize_event_type(event_type)
        return any(item.event_type == normalized for item in self._current_registrations())

    def _current_registrations(self) -> tuple[_PluginEventRegistration, ...]:
        if not self._available():
            return ()
        if self._registrations_provider is not None:
            try:
                provided = self._registrations_provider()
            except Exception:
                return ()
            return provided if isinstance(provided, tuple) else ()
        return self._static_registrations


def _context_key(context):
    return (context.profile_user_id, context.session_id, getattr(context, "character_pack_id", ""))


def _is_bound(context):
    return bool(context.profile_user_id and context.session_id)


def event_rejection(operation, reason):
    if isinstance(operation, str) and operation in {"request_turn", "turn_status", "cancel_turn"}:
        return TurnReceipt("", "rejected", True, reason).as_dict()
    if operation == "observe":
        return ObservationReceipt("", 0, "rejected", reason).as_dict()
    if isinstance(operation, str) and operation in {"bind", "unbind"}:
        return asdict(EventBinding("", "", "rejected", reason))
    return EventReceipt("", "", "rejected", True, reason).as_dict()


def _normalize_event_type(value: object) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if _EVENT_TYPE_PATTERN.fullmatch(normalized) is not None else ""


def _safe_reason(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return ""
    return normalized if _SAFE_REASON_PATTERN.fullmatch(normalized) is not None else "plugin_reported_error"


__all__ = [
    "DEFAULT_EVENT_HANDLER_TIMEOUT_SECONDS",
    "PluginEventBroker",
    "_PluginEventRegistration",
]
