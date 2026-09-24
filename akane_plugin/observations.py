"""Latest context facts; observing never requests a model turn."""

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ObservationReceipt:
    key: str
    version: int
    status: str
    reason: str = ""

    def as_dict(self):
        return asdict(self)

    @staticmethod
    def json_schema():
        return {"type": "object", "additionalProperties": False, "properties": {
            "key": {"type": "string"}, "version": {"type": "integer"},
            "status": {"type": "string"}, "reason": {"type": "string"},
        }, "required": ["key", "version", "status", "reason"]}


class ObservationContext:
    async def observe(self, key: str, data: Any) -> ObservationReceipt:
        """Replace this plugin's key in the host-bound conversation context."""
        events = getattr(self, "events", None)
        port = getattr(events, "_port", None)
        if port is None:
            return ObservationReceipt(key, 0, "rejected", "context_observe_permission_required")
        return ObservationReceipt(**await port.request("observe", {"key": key, "data": data}))
