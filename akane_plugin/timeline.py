"""Durable conversation records contributed by plugins; never requests a model turn."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only; avoids an import cycle
    from .events import Event


def _is_event(value: object) -> bool:
    """Duck-check the public event shape without importing the events module."""

    return all(hasattr(value, name) for name in ("event_id", "event_type", "source", "data"))


@dataclass(frozen=True, slots=True)
class TimelineReceipt:
    """Outcome of one timeline append; ``recorded`` is the only success status."""

    event_id: str
    status: str
    reason: str = ""
    source_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def json_schema():
        names = ("event_id", "status", "reason", "source_id")
        return {"type": "object", "additionalProperties": False,
                "properties": {name: {"type": "string"} for name in names}, "required": list(names)}


class TimelineContext:
    """Append one delivered event to the conversation's durable history.

    The host owns identity, scope and idempotency: the plugin supplies the
    event it received, never a user, session, character or storage key.
    """

    def __init__(self, port: Any = None) -> None:
        self._port = port

    async def append(self, event: "Event") -> TimelineReceipt:
        port = self._port if self._port is not None else getattr(getattr(self, "events", None), "_port", None)
        if port is None:
            return TimelineReceipt("", "rejected", "context_observe_permission_required")
        if not _is_event(event):
            return TimelineReceipt("", "rejected", "timeline_event_required")
        result = await port.request("timeline_append", {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "source": event.source,
            "data": event.data,
            "occurred_at_ms": event.occurred_at_ms,
        })
        if not isinstance(result, dict):
            return TimelineReceipt("", "rejected", "timeline_response_invalid")
        if "event_id" not in result or "source_id" not in result:
            # The host rejected the operation before it reached the timeline
            # port; keep its real reason instead of fabricating a receipt.
            return TimelineReceipt("", "rejected", str(result.get("reason") or "timeline_request_failed"))
        return TimelineReceipt(**{key: result[key] for key in ("event_id", "status", "reason", "source_id")})
