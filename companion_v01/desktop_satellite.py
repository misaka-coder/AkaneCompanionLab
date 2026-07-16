from __future__ import annotations

import asyncio
import hmac
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from capcore import CapabilityToolSpec

from .capability_registry import BrokerExecutionResult, ExecutionReceipt, OPEN_BROWSER_TOOL_SPEC


SATELLITE_PROTOCOL_VERSION = 1
SATELLITE_HEARTBEAT_SECONDS = 10
SATELLITE_LEASE_TTL_SECONDS = 30
SATELLITE_REGISTER_TIMEOUT_SECONDS = 8


@dataclass
class _PendingInvocation:
    connection_id: str = ""
    lease_epoch: str = ""
    offer_id: str = ""
    event: threading.Event = field(default_factory=threading.Event)
    acknowledged: bool = False
    result: BrokerExecutionResult | None = None


@dataclass
class _SatelliteConnection:
    connection_id: str
    lease_epoch: str
    offer_id: str
    loop: asyncio.AbstractEventLoop
    outbound: asyncio.Queue[dict[str, Any]]
    expires_at: float
    connected_at: float
    tool_ids: frozenset[str]


class DesktopSatelliteService:
    """Instance-bound WSS session and offer source for desktop execution."""

    def __init__(
        self,
        *,
        instance_id: str,
        token: str,
        clock=time.time,
        lease_ttl_seconds: int = SATELLITE_LEASE_TTL_SECONDS,
    ) -> None:
        self.instance_id = str(instance_id or "").strip()
        self._token = str(token or "").strip()
        self._clock = clock
        self._lease_ttl_seconds = max(5, min(120, int(lease_ttl_seconds)))
        self._lock = threading.RLock()
        self._connection: _SatelliteConnection | None = None
        self._pending: dict[str, _PendingInvocation] = {}

    @property
    def enabled(self) -> bool:
        return bool(self._token)

    async def handle_websocket(self, websocket: WebSocket) -> None:
        if not self._authorize_headers(websocket.headers):
            await websocket.close(code=4401, reason="satellite_auth_required")
            return
        await websocket.accept()
        await websocket.send_json(
            {
                "type": "hello",
                "protocol_version": SATELLITE_PROTOCOL_VERSION,
                "instance_id": self.instance_id,
                "heartbeat_seconds": SATELLITE_HEARTBEAT_SECONDS,
                "lease_ttl_seconds": self._lease_ttl_seconds,
            }
        )
        try:
            registration = await asyncio.wait_for(
                websocket.receive_json(),
                timeout=SATELLITE_REGISTER_TIMEOUT_SECONDS,
            )
        except (TimeoutError, WebSocketDisconnect, ValueError):
            await websocket.close(code=4408, reason="satellite_registration_required")
            return
        supported = self._validate_registration(registration)
        if not supported:
            await websocket.close(code=4403, reason="satellite_registration_rejected")
            return

        now = float(self._clock())
        connection = _SatelliteConnection(
            connection_id=f"satconn_{uuid.uuid4().hex}",
            lease_epoch=f"lease_{uuid.uuid4().hex}",
            offer_id=f"offer_{uuid.uuid4().hex}",
            loop=asyncio.get_running_loop(),
            outbound=asyncio.Queue(maxsize=64),
            expires_at=now + self._lease_ttl_seconds,
            connected_at=now,
            tool_ids=frozenset(supported),
        )
        if not self._install_connection(connection):
            await websocket.close(code=4429, reason="satellite_already_connected")
            return
        await websocket.send_json(
            {
                "type": "registered",
                "protocol_version": SATELLITE_PROTOCOL_VERSION,
                "instance_id": self.instance_id,
                "lease_epoch": connection.lease_epoch,
                "offer_ids": {OPEN_BROWSER_TOOL_SPEC.capability_id: connection.offer_id},
                "expires_at": connection.expires_at,
            }
        )
        sender = asyncio.create_task(self._send_loop(websocket, connection))
        try:
            while True:
                message = await websocket.receive_json()
                await self._handle_client_message(connection, message)
        except (WebSocketDisconnect, ValueError, RuntimeError):
            pass
        finally:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
            self._remove_connection(connection.connection_id)

    def resolve_receipt(self, spec: CapabilityToolSpec) -> ExecutionReceipt | None:
        with self._lock:
            connection = self._active_connection_locked(spec)
            if connection is None:
                return None
            return ExecutionReceipt(
                instance_id=self.instance_id,
                tool_id=spec.capability_id,
                offer_id=connection.offer_id,
                lease_epoch=connection.lease_epoch,
                offer_expires_at=connection.expires_at,
                spec_version=spec.spec_version,
                schema_version=spec.schema_version,
                schema_hash=spec.schema_hash,
            )

    def validate_receipt(self, spec: CapabilityToolSpec, receipt: ExecutionReceipt) -> str:
        with self._lock:
            connection = self._active_connection_locked(spec)
            if connection is None:
                return "executor_unavailable"
            if receipt.instance_id != self.instance_id or receipt.tool_id != spec.capability_id:
                return "receipt_instance_mismatch"
            if receipt.offer_id != connection.offer_id or receipt.lease_epoch != connection.lease_epoch:
                return "receipt_lease_mismatch"
            if receipt.offer_expires_at <= float(self._clock()):
                return "offer_expired"
            if (
                receipt.spec_version != spec.spec_version
                or receipt.schema_version != spec.schema_version
                or receipt.schema_hash != spec.schema_hash
            ):
                return "receipt_schema_mismatch"
        return ""

    def dispatch(
        self,
        *,
        spec: CapabilityToolSpec,
        receipt: ExecutionReceipt,
        invocation_id: str,
        arguments: Mapping[str, Any],
        timeout_seconds: float,
    ) -> BrokerExecutionResult:
        reason = self.validate_receipt(spec, receipt)
        if reason:
            return BrokerExecutionResult(status="unavailable_before_dispatch", reason=reason)
        with self._lock:
            connection = self._active_connection_locked(spec)
            if connection is None:
                return BrokerExecutionResult(status="unavailable_before_dispatch", reason="executor_unavailable")
            pending = _PendingInvocation(
                connection_id=connection.connection_id,
                lease_epoch=connection.lease_epoch,
                offer_id=connection.offer_id,
            )
            self._pending[invocation_id] = pending
            payload = {
                "type": "invoke",
                "protocol_version": SATELLITE_PROTOCOL_VERSION,
                "instance_id": self.instance_id,
                "lease_epoch": receipt.lease_epoch,
                "offer_id": receipt.offer_id,
                "invocation_id": invocation_id,
                "tool_id": spec.capability_id,
                "spec_version": spec.spec_version,
                "schema_version": spec.schema_version,
                "schema_hash": spec.schema_hash,
                "arguments": dict(arguments),
            }
            queued = self._queue_message_locked(connection, payload)
            if not queued:
                self._pending.pop(invocation_id, None)
                return BrokerExecutionResult(status="unavailable_before_dispatch", reason="executor_queue_unavailable")
        if not pending.event.wait(max(1.0, min(30.0, float(timeout_seconds)))):
            with self._lock:
                current = self._pending.pop(invocation_id, None)
            if current is not None and current.result is not None:
                return current.result
            if pending.acknowledged:
                return BrokerExecutionResult(
                    status="execution_unknown",
                    reason="executor_result_timeout",
                    model_feedback="桌面动作的执行结果暂时无法确认，请不要声称网页已经打开。",
                )
            return BrokerExecutionResult(
                status="unavailable_before_dispatch",
                reason="executor_ack_timeout",
                model_feedback="桌面执行器没有接受这次操作，请直接说明网页没有打开。",
            )
        with self._lock:
            self._pending.pop(invocation_id, None)
        return pending.result or BrokerExecutionResult(
            status="execution_unknown",
            reason="executor_result_missing",
            model_feedback="桌面动作的执行结果暂时无法确认，请不要声称网页已经打开。",
        )

    def diagnostics(self) -> dict[str, Any]:
        now = float(self._clock())
        with self._lock:
            connection = self._connection
            active = connection is not None and connection.expires_at > now
            return {
                "ok": True,
                "status": "online" if active else ("disabled" if not self.enabled else "offline"),
                "connected": active,
                "activeOfferCount": len(connection.tool_ids) if active and connection is not None else 0,
                "pendingInvocationCount": len(self._pending),
            }

    async def _send_loop(self, websocket: WebSocket, connection: _SatelliteConnection) -> None:
        while True:
            payload = await connection.outbound.get()
            await websocket.send_json(payload)

    async def _handle_client_message(self, connection: _SatelliteConnection, message: Any) -> None:
        if not isinstance(message, dict):
            return
        message_type = str(message.get("type") or "").strip()
        if message_type == "heartbeat":
            if str(message.get("lease_epoch") or "").strip() != connection.lease_epoch:
                return
            with self._lock:
                current = self._connection
                if current is not None and current.connection_id == connection.connection_id:
                    current.expires_at = float(self._clock()) + self._lease_ttl_seconds
                    expires_at = current.expires_at
                else:
                    return
            await connection.outbound.put(
                {
                    "type": "heartbeat_ack",
                    "lease_epoch": connection.lease_epoch,
                    "expires_at": expires_at,
                }
            )
            return
        invocation_id = str(message.get("invocation_id") or "").strip()
        if not invocation_id:
            return
        with self._lock:
            pending = self._pending.get(invocation_id)
            if pending is None:
                return
            if (
                pending.connection_id != connection.connection_id
                or str(message.get("instance_id") or "").strip() != self.instance_id
                or str(message.get("lease_epoch") or "").strip() != pending.lease_epoch
                or str(message.get("offer_id") or "").strip() != pending.offer_id
            ):
                return
            if message_type in {"accepted", "running"}:
                pending.acknowledged = True
                return
            if message_type != "result":
                return
            raw_status = str(message.get("status") or "failed").strip().lower()
            reason = self._safe_reason(message.get("reason"))
            if raw_status == "succeeded":
                pending.result = BrokerExecutionResult(
                    status="succeeded",
                    model_feedback="已在用户绑定的电脑上真实打开该公开网页；不要声称读取了页面内容。",
                )
            elif raw_status == "rejected":
                pending.result = BrokerExecutionResult(
                    status="failed",
                    reason=reason or "executor_rejected",
                    model_feedback="用户的电脑拒绝了这次打开网页操作，请自然说明没有打开。",
                )
            else:
                pending.result = BrokerExecutionResult(
                    status="failed",
                    reason=reason or "executor_failed",
                    model_feedback="用户的电脑没有成功打开网页，请自然说明这次操作失败。",
                )
            pending.event.set()

    def _validate_registration(self, value: Any) -> set[str]:
        if not isinstance(value, dict) or str(value.get("type") or "") != "register":
            return set()
        if int(value.get("protocol_version") or 0) != SATELLITE_PROTOCOL_VERSION:
            return set()
        if str(value.get("instance_id") or "").strip() != self.instance_id:
            return set()
        raw_offers = value.get("offers")
        if not isinstance(raw_offers, list):
            return set()
        supported: set[str] = set()
        for raw in raw_offers[:16]:
            if not isinstance(raw, dict):
                continue
            if (
                str(raw.get("tool_id") or "").strip() == OPEN_BROWSER_TOOL_SPEC.capability_id
                and str(raw.get("spec_version") or "").strip() == OPEN_BROWSER_TOOL_SPEC.spec_version
                and int(raw.get("schema_version") or 0) == OPEN_BROWSER_TOOL_SPEC.schema_version
                and str(raw.get("schema_hash") or "").strip().lower() == OPEN_BROWSER_TOOL_SPEC.schema_hash
            ):
                supported.add(OPEN_BROWSER_TOOL_SPEC.capability_id)
        return supported

    def _install_connection(self, connection: _SatelliteConnection) -> bool:
        now = float(self._clock())
        with self._lock:
            current = self._connection
            if current is not None and current.expires_at > now:
                return False
            self._connection = connection
        return True

    def _remove_connection(self, connection_id: str) -> None:
        with self._lock:
            current = self._connection
            if current is not None and current.connection_id == connection_id:
                self._connection = None
            for pending in self._pending.values():
                if pending.connection_id == connection_id and pending.result is None:
                    if pending.acknowledged:
                        pending.result = BrokerExecutionResult(
                            status="execution_unknown",
                            reason="executor_disconnected_after_accept",
                            model_feedback="桌面执行器在操作过程中断开，结果无法确认；请不要声称网页已经打开。",
                        )
                    else:
                        pending.result = BrokerExecutionResult(
                            status="unavailable_before_dispatch",
                            reason="executor_disconnected_before_accept",
                            model_feedback="桌面执行器在接受操作前断开，这次没有执行网页打开。",
                        )
                    pending.event.set()

    def _active_connection_locked(self, spec: CapabilityToolSpec) -> _SatelliteConnection | None:
        connection = self._connection
        if connection is None or connection.expires_at <= float(self._clock()):
            return None
        if spec.capability_id not in connection.tool_ids:
            return None
        return connection

    def _queue_message_locked(self, connection: _SatelliteConnection, payload: dict[str, Any]) -> bool:
        invocation_id = str(payload.get("invocation_id") or "").strip()

        def enqueue() -> None:
            try:
                connection.outbound.put_nowait(payload)
            except asyncio.QueueFull:
                with self._lock:
                    pending = self._pending.get(invocation_id)
                    if pending is not None and pending.result is None:
                        pending.result = BrokerExecutionResult(
                            status="unavailable_before_dispatch",
                            reason="executor_queue_unavailable",
                            model_feedback="桌面执行器当前无法接受新操作，这次没有打开网页。",
                        )
                        pending.event.set()

        try:
            connection.loop.call_soon_threadsafe(enqueue)
        except RuntimeError:
            return False
        return True

    def _authorize_headers(self, headers: Mapping[str, Any]) -> bool:
        if not self.enabled:
            return False
        authorization = str(headers.get("authorization") or "").strip()
        scheme, separator, supplied = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer":
            supplied = str(headers.get("x-akane-satellite-token") or "").strip()
        return bool(supplied) and hmac.compare_digest(supplied, self._token)

    @staticmethod
    def _safe_reason(value: Any) -> str:
        reason = str(value or "").strip().lower()
        if reason in {
            "invalid_url",
            "unsafe_url",
            "os_open_failed",
            "permission_denied",
            "instance_mismatch",
            "lease_mismatch",
            "schema_mismatch",
        }:
            return reason
        return "executor_failed"
