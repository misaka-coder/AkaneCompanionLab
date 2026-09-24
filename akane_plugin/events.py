"""Public transient event contracts and context ports; no scheduler or storage."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
from .observations import ObservationContext
from .timeline import TimelineContext
from .turns import TurnContext
from .tasks import TaskContext


@dataclass(frozen=True, slots=True)
class EventInput:
    """What a plugin may supply when publishing an event.

    ``event_key`` is a business idempotency key: the host binds it to the real
    plugin, generation, source, routing scope and event type, so the same key in
    another scope is a different event. ``occurred_at_ms`` is optional; the host
    always records its own ``received_at_ms`` and never rejects an event because
    of the reported time.
    """

    event_type: str
    data: Any
    event_key: str = ""
    occurred_at_ms: int | None = None


@dataclass(frozen=True, slots=True)
class Event:
    """An immutable event as delivered to one subscriber.

    ``event_id``, ``source``, ``scope``, ``version`` and ``received_at_ms`` are
    signed by the host. ``scope`` is an opaque host identifier: read it, log it,
    echo it back in a query, but never parse it, construct it or use it as a
    business key. ``version`` counts events in one
    ``(generation, scope, event_type)`` stream starting at 1; it is not a
    business state version and does not survive a restart.
    """

    event_id: str
    event_type: str
    source: str
    data: Any
    scope: str = ""
    version: int = 0
    occurred_at_ms: int = 0
    received_at_ms: int = 0


@dataclass(frozen=True, slots=True)
class EventSubscription:
    subscription_id: str
    event_type: str
    sources: tuple[str, ...] = ()
    scope: str = "global"
    coalesce: str = "queue"
    persistence: str = "none"
    request_turn: bool = False


@dataclass(frozen=True, slots=True)
class EventDelivery:
    subscription_id: str
    scope_id: str
    status: str
    reason: str = ""
    value: Any = None
    has_value: bool = False
    cancel_requested: bool = False
    linked_dispatches: tuple[str, ...] = ()
    linked_turn_requests: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EventReceipt:
    dispatch_id: str
    event_id: str
    status: str
    complete: bool = False
    reason: str = ""
    deliveries: tuple[EventDelivery, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def json_schema():
        delivery = {
            "type": "object", "additionalProperties": False,
            "properties": {
                **{name: {"type": "string"} for name in ("subscription_id", "scope_id", "status", "reason")},
                "value": {}, "has_value": {"type": "boolean"}, "cancel_requested": {"type": "boolean"},
                "linked_dispatches": {"type": "array", "items": {"type": "string"}},
                "linked_turn_requests": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["subscription_id", "scope_id", "status", "reason", "value", "has_value", "cancel_requested", "linked_dispatches", "linked_turn_requests"],
        }
        return {"type": "object", "additionalProperties": False, "properties": {
            **{name: {"type": "string"} for name in ("dispatch_id", "event_id", "status", "reason")},
            "complete": {"type": "boolean"}, "deliveries": {"type": "array", "items": delivery},
        }, "required": ["dispatch_id", "event_id", "status", "reason", "complete", "deliveries"]}


@dataclass(frozen=True, slots=True)
class EventBinding:
    subscription_id: str
    scope_id: str
    status: str
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def json_schema():
        names = ("subscription_id", "scope_id", "status", "reason")
        return {"type": "object", "additionalProperties": False,
                "properties": {name: {"type": "string"} for name in names}, "required": list(names)}


def receipt_from_dict(value: dict) -> EventReceipt:
    return EventReceipt(
        dispatch_id=value["dispatch_id"], event_id=value["event_id"], status=value["status"],
        complete=value["complete"], reason=value.get("reason", ""),
        deliveries=tuple(EventDelivery(
            **{**item, "linked_dispatches": tuple(item.get("linked_dispatches", ())),
               "linked_turn_requests": tuple(item.get("linked_turn_requests", ()))},
        ) for item in value.get("deliveries", ())),
    )


class Events:
    """Emit once, inspect receipts, or bind an owned conversation subscription.

    Returning an accepted EventReceipt from an event handler explicitly links
    that child dispatch. The parent remains pending until the child finishes.
    Merely emitting another event does not make it a blocking dependency.
    """

    def __init__(self, port):
        self._port = port

    async def emit(self, event_type: str, data: Any, *, event_key: str = "",
                   occurred_at_ms: int | None = None) -> EventReceipt:
        if self._port is None:
            return EventReceipt("", "", "rejected", True, "event_emit_permission_required")
        return receipt_from_dict(await self._port.request("emit", {
            "event_type": event_type, "data": data, "event_key": event_key,
            "occurred_at_ms": occurred_at_ms,
        }))

    async def status(self, dispatch_id: str) -> EventReceipt:
        if self._port is None:
            return EventReceipt(dispatch_id, "", "rejected", True, "event_port_unavailable")
        return receipt_from_dict(await self._port.request("status", {"dispatch_id": dispatch_id}))

    async def bind(self, subscription_id: str) -> EventBinding:
        if self._port is None:
            return EventBinding(subscription_id, "", "rejected", "event_subscribe_permission_required")
        return EventBinding(**await self._port.request("bind", {"subscription_id": subscription_id}))

    async def unbind(self, scope_id: str) -> EventBinding:
        if self._port is None:
            return EventBinding("", scope_id, "rejected", "event_subscribe_permission_required")
        return EventBinding(**await self._port.request("unbind", {"scope_id": scope_id}))


@dataclass(frozen=True, slots=True)
class EventContext(ObservationContext, TurnContext):
    """Ports and an opaque execution scope; identity is held by the host."""

    tools: Any
    events: Events
    scope_id: str
    _resources: Any = None
    _connections: Any = None
    _tasks: Any = None

    @property
    def context(self) -> "EventContext":
        """Explicit namespace form of the observation ports."""
        return self

    @property
    def timeline(self) -> TimelineContext:
        """Explicit namespace form of the timeline port."""
        return TimelineContext(getattr(self.events, "_port", None))

    @property
    def connections(self):
        from .connections import Connections
        return Connections(self._connections)

    @property
    def services(self):
        from .tools import Services
        return Services(self.tools._port)

    @property
    def task(self) -> TaskContext:
        return TaskContext(self._tasks)

    @property
    def resources(self):
        if self._resources is None:
            raise RuntimeError("resource_read_permission_required")
        return self._resources


__all__ = ["Event", "EventInput", "EventSubscription", "EventDelivery", "EventReceipt", "EventBinding", "Events", "EventContext"]
