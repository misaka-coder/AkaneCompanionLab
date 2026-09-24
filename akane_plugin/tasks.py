"""Public host-owned Task lifecycle contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


TASK_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "stale"})


@dataclass(frozen=True, slots=True)
class TaskReceipt:
    """A real host receipt for one plugin-owned long task.

    ``complete`` describes the task lifecycle only. ``scope_expired`` reports
    that this invocation's generation or scope no longer owns the request, so
    the caller must recover through an explicit ``open(..., recover=True)`` in
    an active scope instead of treating the task as finished.
    """

    task_id: str
    status: str
    reason: str = ""
    complete: bool = False
    scope_expired: bool = False
    cancel_requested: bool = False
    result: Any = None
    checkpoint: Any = None
    checkpoint_version: int = 0
    checkpoint_fingerprint: str = ""
    checkpoint_generation_id: str = ""
    checkpoint_updated_at: float = 0.0
    recovery_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def json_schema():
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "task_id": {"type": "string"},
                "status": {"type": "string"},
                "reason": {"type": "string"},
                "complete": {"type": "boolean"},
                "scope_expired": {"type": "boolean"},
                "cancel_requested": {"type": "boolean"},
                "result": {},
                "checkpoint": {},
                "checkpoint_version": {"type": "integer", "minimum": 0},
                "checkpoint_fingerprint": {"type": "string"},
                "checkpoint_generation_id": {"type": "string"},
                "checkpoint_updated_at": {"type": "number"},
                "recovery_reason": {"type": "string"},
            },
            "required": [
                "task_id", "status", "reason", "complete", "scope_expired", "cancel_requested",
                "result", "checkpoint", "checkpoint_version", "checkpoint_fingerprint",
                "checkpoint_generation_id", "checkpoint_updated_at", "recovery_reason",
            ],
        }

    @staticmethod
    def from_dict(value: dict[str, Any]) -> "TaskReceipt":
        return TaskReceipt(
            task_id=str(value.get("task_id") or ""),
            status=str(value.get("status") or "rejected"),
            reason=str(value.get("reason") or ""),
            complete=bool(value.get("complete", False)),
            scope_expired=bool(value.get("scope_expired", False)),
            cancel_requested=bool(value.get("cancel_requested", False)),
            result=value.get("result"),
            checkpoint=value.get("checkpoint"),
            checkpoint_version=int(value.get("checkpoint_version", 0) or 0),
            checkpoint_fingerprint=str(value.get("checkpoint_fingerprint") or ""),
            checkpoint_generation_id=str(value.get("checkpoint_generation_id") or ""),
            checkpoint_updated_at=float(value.get("checkpoint_updated_at", 0) or 0),
            recovery_reason=str(value.get("recovery_reason") or ""),
        )


@dataclass(frozen=True, slots=True)
class TaskHandle:
    """A worker-local reference to a host-owned task."""

    _port: Any = field(repr=False, compare=False)
    task_id: str
    initial: TaskReceipt = field(repr=False)

    async def status(self) -> TaskReceipt:
        return TaskReceipt.from_dict(await self._port.request("status", {"task_id": self.task_id}))

    async def update(self, *, status: str, result: Any = None) -> TaskReceipt:
        payload = {"task_id": self.task_id, "status": status}
        if result is not None:
            payload["result"] = result
        return TaskReceipt.from_dict(await self._port.request("update", payload))

    async def checkpoint(self, value: Any, *, version: int) -> TaskReceipt:
        return TaskReceipt.from_dict(await self._port.request("checkpoint", {
            "task_id": self.task_id,
            "value": value,
            "version": version,
        }))

    async def checkpoint_status(self) -> TaskReceipt:
        return TaskReceipt.from_dict(await self._port.request("checkpoint_status", {"task_id": self.task_id}))

    async def pause(self) -> TaskReceipt:
        return TaskReceipt.from_dict(await self._port.request("pause", {"task_id": self.task_id}))

    async def pause_at_boundary(self) -> TaskReceipt:
        """Confirm a cooperative pause after the plugin reached a safe boundary."""
        return TaskReceipt.from_dict(await self._port.request("pause_boundary", {"task_id": self.task_id}))

    async def resume(self) -> TaskReceipt:
        return TaskReceipt.from_dict(await self._port.request("resume", {"task_id": self.task_id}))

    async def cancel(self) -> TaskReceipt:
        return TaskReceipt.from_dict(await self._port.request("cancel", {"task_id": self.task_id}))


class TaskContext:
    """Create and inspect tasks through the host-owned Task port."""

    def __init__(self, port: Any = None) -> None:
        self._port = port

    async def create(
        self,
        task_type: str,
        payload: dict[str, Any] | None = None,
        *,
        idempotency_key: str = "",
        pause_mode: str = "unavailable",
    ) -> TaskHandle:
        if self._port is None:
            receipt = TaskReceipt("", "rejected", "task_port_unavailable", True)
            return TaskHandle(self._port, "", receipt)
        value = await self._port.request(
            "create",
            {
                "task_type": task_type,
                "payload": payload or {},
                "idempotency_key": idempotency_key,
                "pause_mode": pause_mode,
            },
        )
        receipt = TaskReceipt.from_dict(value)
        return TaskHandle(self._port, receipt.task_id, receipt)

    async def open(self, task_id: str, *, recover: bool = False) -> TaskHandle:
        """Open an existing task in the current plugin invocation scope.

        ``recover`` is explicit and only reloads host-persisted checkpoint
        state. It never causes the host to replay a lost side effect or model
        turn.
        """

        if self._port is None:
            receipt = TaskReceipt("", "rejected", "task_port_unavailable", True)
            return TaskHandle(self._port, "", receipt)
        value = await self._port.request("open", {"task_id": task_id, "recover": bool(recover)})
        receipt = TaskReceipt.from_dict(value)
        return TaskHandle(self._port, receipt.task_id, receipt)


__all__ = ["TASK_TERMINAL_STATUSES", "TaskContext", "TaskHandle", "TaskReceipt"]
