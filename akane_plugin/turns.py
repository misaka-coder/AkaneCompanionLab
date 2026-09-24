"""Explicit requests for a host-owned model turn and its delivery receipt."""

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class TurnReceipt:
    request_id: str
    status: str
    complete: bool = False
    reason: str = ""
    model_status: str = "not_started"
    delivery_status: str = "not_sent"
    cancel_requested: bool = False
    observation_versions: dict[str, int] = field(default_factory=dict)

    def as_dict(self):
        return asdict(self)

    @staticmethod
    def json_schema():
        return {"type": "object", "additionalProperties": False, "properties": {
            **{name: {"type": "string"} for name in
               ("request_id", "status", "reason", "model_status", "delivery_status")},
            "complete": {"type": "boolean"}, "cancel_requested": {"type": "boolean"},
            "observation_versions": {"type": "object", "additionalProperties": {"type": "integer"}},
        }, "required": ["request_id", "status", "complete", "reason", "model_status", "delivery_status",
                         "cancel_requested", "observation_versions"]}


class TurnContext:
    async def request_turn(self, reason: str, data: Any = None, *, observations: dict[str, int] | None = None,
                           stale: str = "latest", coalesce_key: str | None = None,
                           conversation_ref: str | None = None) -> TurnReceipt:
        """Queue work in this context; optionally replace an owned pending request.

        ``conversation_ref`` is only for a caller that has no conversation
        identity of its own, such as a supervised background service acting on a
        reference the host issued to it earlier. The host verifies the reference
        and rejects one that was not issued for the same conversation.
        """
        return await self._turn_request("request_turn", {
            "reason": reason, "data": data, "observations": observations or {},
            "stale": stale, "coalesce_key": coalesce_key, "conversation_ref": conversation_ref,
        })

    async def turn_status(self, request_id: str) -> TurnReceipt:
        return await self._turn_request("turn_status", {"request_id": request_id})

    async def cancel_turn(self, request_id: str) -> TurnReceipt:
        return await self._turn_request("cancel_turn", {"request_id": request_id})

    async def _turn_request(self, operation, payload):
        port = getattr(getattr(self, "events", None), "_port", None)
        if port is None:
            return TurnReceipt("", "rejected", True, "agent_turn_request_permission_required")
        return TurnReceipt(**await port.request(operation, payload))
