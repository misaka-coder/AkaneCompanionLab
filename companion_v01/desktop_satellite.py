from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from capcore import CapabilityToolSpec

from .capability_registry import BrokerExecutionResult, ExecutionReceipt, OPEN_BROWSER_TOOL_SPEC
from .desktop_satellite_specs import desktop_satellite_spec


SATELLITE_PROTOCOL_VERSION = 1
SATELLITE_HEARTBEAT_SECONDS = 10
SATELLITE_LEASE_TTL_SECONDS = 30
SATELLITE_REGISTER_TIMEOUT_SECONDS = 8
_SAFE_DEVICE_SCOPE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass
class _PendingInvocation:
    connection_id: str = ""
    lease_epoch: str = ""
    offer_id: str = ""
    tool_id: str = ""
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

    def for_bot(self, *, bot_id: str, memory_space_id: str) -> "BotScopedDesktopSatelliteOfferSource":
        return BotScopedDesktopSatelliteOfferSource(
            service=self,
            bot_id=bot_id,
            memory_space_id=memory_space_id,
        )

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
                "offer_ids": {tool_id: connection.offer_id for tool_id in sorted(connection.tool_ids)},
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
                tool_id=spec.capability_id,
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
                    model_feedback="本地能力的执行结果暂时无法确认，请不要声称操作已经完成。",
                )
            return BrokerExecutionResult(
                status="unavailable_before_dispatch",
                reason="executor_ack_timeout",
                model_feedback="桌面执行器没有接受这次操作，请直接说明本地能力没有执行。",
            )
        with self._lock:
            self._pending.pop(invocation_id, None)
        return pending.result or BrokerExecutionResult(
            status="execution_unknown",
            reason="executor_result_missing",
            model_feedback="本地能力的执行结果暂时无法确认，请不要声称操作已经完成。",
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
                or str(message.get("tool_id") or "").strip() != pending.tool_id
            ):
                return
            if message_type in {"accepted", "running"}:
                pending.acknowledged = True
                return
            if message_type != "result":
                return
            raw_status = str(message.get("status") or "failed").strip().lower()
            reason = self._safe_reason(message.get("reason"))
            data = self._safe_result_data(message.get("data"))
            tool_id = str(message.get("tool_id") or "").strip()
            if raw_status == "succeeded":
                pending.result = BrokerExecutionResult(
                    status="succeeded",
                    model_feedback=self._success_feedback(tool_id),
                    data=data,
                )
            elif raw_status == "execution_unknown":
                pending.result = BrokerExecutionResult(
                    status="execution_unknown",
                    reason=reason or "execution_unknown",
                    model_feedback=self._unknown_feedback(tool_id),
                    data=data,
                )
            elif raw_status == "rejected":
                pending.result = BrokerExecutionResult(
                    status="failed",
                    reason=reason or "executor_rejected",
                    model_feedback=self._failure_feedback(tool_id, rejected=True),
                    data=data,
                )
            else:
                pending.result = BrokerExecutionResult(
                    status="failed",
                    reason=reason or "executor_failed",
                    model_feedback=self._failure_feedback(tool_id, rejected=False),
                    data=data,
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
            tool_id = str(raw.get("tool_id") or "").strip()
            spec = (
                OPEN_BROWSER_TOOL_SPEC
                if tool_id == OPEN_BROWSER_TOOL_SPEC.capability_id
                else desktop_satellite_spec(tool_id)
            )
            if spec is None:
                continue
            if (
                str(raw.get("spec_version") or "").strip() == spec.spec_version
                and int(raw.get("schema_version") or 0) == spec.schema_version
                and str(raw.get("schema_hash") or "").strip().lower() == spec.schema_hash
            ):
                supported.add(spec.capability_id)
        return supported

    @staticmethod
    def _success_feedback(tool_id: str) -> str:
        if tool_id == OPEN_BROWSER_TOOL_SPEC.capability_id:
            return "已在用户绑定的电脑上真实打开该公开网页；不要声称读取了页面内容。"
        spec = desktop_satellite_spec(tool_id)
        if spec is not None:
            return f"已从用户绑定电脑真实完成：{spec.display_name}。以下是执行器返回的实际结果。"
        return "已从用户绑定电脑真实完成这次本地能力调用。"

    @staticmethod
    def _failure_feedback(tool_id: str, *, rejected: bool) -> str:
        if tool_id == OPEN_BROWSER_TOOL_SPEC.capability_id:
            return (
                "用户的电脑拒绝了这次打开网页操作，请自然说明没有打开。"
                if rejected
                else "用户的电脑没有成功打开网页，请自然说明这次操作失败。"
            )
        spec = desktop_satellite_spec(tool_id)
        operation = spec.display_name if spec is not None else "本地能力调用"
        if rejected:
            return f"用户的电脑拒绝了这次{operation}，请如实说明没有执行。"
        return f"用户的电脑没有成功完成{operation}，请如实说明这次操作失败。"

    @staticmethod
    def _unknown_feedback(tool_id: str) -> str:
        spec = desktop_satellite_spec(tool_id)
        operation = spec.display_name if spec is not None else "本地能力调用"
        return (
            f"已向用户绑定电脑发送{operation}指令，但执行器没有确认到实际状态变化；"
            "请如实说明结果未确认，不要声称操作已经完成。"
        )

    @staticmethod
    def _safe_result_data(value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            return {}
        blocked = {"path", "absolutepath", "localpath", "token", "secret", "password", "authorization"}

        def safe_key(value: Any) -> bool:
            normalized = "".join(character for character in str(value).lower() if character.isalnum())
            return not any(normalized == item or normalized.endswith(item) for item in blocked)

        def clean_text(value: Any) -> str:
            text = str(value or "")[:1000]
            text = re.sub(r"(?i)\bbearer\s+[^\s]+", "Bearer [redacted]", text)
            text = re.sub(
                r"(?i)\b(api[_-]?key|password|secret|token|authorization)\s*[:=]\s*[^\s,;]+",
                r"\1=[redacted]",
                text,
            )
            text = re.sub(
                r"(?P<quote>[\"'])(?:[A-Za-z]:[\\/]|\\\\)[^\"'\r\n]+(?P=quote)",
                "[local_path]",
                text,
            )
            text = re.sub(r"(?<![\w/])(?:[A-Za-z]:[\\/]|\\\\)[^\r\n,;|<>]*", "[local_path]", text)
            text = re.sub(
                r"(?<![\w/])/(?:users|home|root|var|tmp|mnt|Volumes)/[^\r\n,;|<>\s]+",
                "[local_path]",
                text,
            )
            return text

        def clean(item: Any, depth: int = 0, field_name: str = "") -> Any:
            if depth > 4:
                return None
            if isinstance(item, Mapping):
                return {
                    str(key): clean(raw, depth + 1, str(key))
                    for key, raw in list(item.items())[:64]
                    if safe_key(key)
                }
            if isinstance(item, list):
                limit = 128 if field_name == "processes" else 32
                return [clean(raw, depth + 1) for raw in item[:limit]]
            if isinstance(item, (str, int, float, bool)) or item is None:
                return item if not isinstance(item, str) else clean_text(item)
            return clean_text(item)[:200]

        result = clean(value)
        return result if isinstance(result, dict) else {}

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
                            model_feedback="桌面执行器在操作过程中断开，结果无法确认；请不要声称本地操作已经完成。",
                        )
                    else:
                        pending.result = BrokerExecutionResult(
                            status="unavailable_before_dispatch",
                            reason="executor_disconnected_before_accept",
                            model_feedback="桌面执行器在接受操作前断开，这次没有执行本地能力。",
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
                            model_feedback="桌面执行器当前无法接受新操作，这次没有执行本地能力。",
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
            "invalid_action",
            "result_serialization_failed",
            "media_control_failed",
            "media_state_not_confirmed",
            "unsupported_platform",
            "no_active_session",
            "control_failed",
            "read_failed",
            "join_failed",
            "process_enumeration_failed",
            "terminate_failed",
            "invalid_pid",
            "protected_process",
            "termination_unconfirmed",
            "volume_read_failed",
            "volume_set_failed",
            "invocation_id_tool_conflict",
            "unknown_tool",
        }:
            return reason
        return "executor_failed"


class BotScopedDesktopSatelliteOfferSource:
    """Bot-isolated receipt and invocation view over one Host device connection."""

    def __init__(
        self,
        *,
        service: DesktopSatelliteService,
        bot_id: str,
        memory_space_id: str,
    ) -> None:
        self.service = service
        self.bot_id = self._safe_scope_id(bot_id, field="bot_id")
        self.memory_space_id = self._safe_scope_id(memory_space_id, field="memory_space_id")
        self.instance_id = f"{service.instance_id}:bot:{self.bot_id}:memory:{self.memory_space_id}"

    def resolve_receipt(self, spec: CapabilityToolSpec) -> ExecutionReceipt | None:
        receipt = self.service.resolve_receipt(spec)
        if receipt is None:
            return None
        return ExecutionReceipt(
            instance_id=self.instance_id,
            tool_id=receipt.tool_id,
            offer_id=receipt.offer_id,
            lease_epoch=receipt.lease_epoch,
            offer_expires_at=receipt.offer_expires_at,
            spec_version=receipt.spec_version,
            schema_version=receipt.schema_version,
            schema_hash=receipt.schema_hash,
        )

    def validate_receipt(self, spec: CapabilityToolSpec, receipt: ExecutionReceipt) -> str:
        if receipt.instance_id != self.instance_id:
            return "receipt_instance_mismatch"
        return self.service.validate_receipt(spec, self._host_receipt(receipt))

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
        return self.service.dispatch(
            spec=spec,
            receipt=self._host_receipt(receipt),
            invocation_id=self._wire_invocation_id(invocation_id),
            arguments=arguments,
            timeout_seconds=timeout_seconds,
        )

    def _wire_invocation_id(self, invocation_id: str) -> str:
        digest = hashlib.sha256(
            f"{self.bot_id}\0{self.memory_space_id}\0{str(invocation_id or '').strip()}".encode("utf-8")
        ).hexdigest()
        return f"inv_{digest}"

    def _host_receipt(self, receipt: ExecutionReceipt) -> ExecutionReceipt:
        return ExecutionReceipt(
            instance_id=self.service.instance_id,
            tool_id=receipt.tool_id,
            offer_id=receipt.offer_id,
            lease_epoch=receipt.lease_epoch,
            offer_expires_at=receipt.offer_expires_at,
            spec_version=receipt.spec_version,
            schema_version=receipt.schema_version,
            schema_hash=receipt.schema_hash,
        )

    @staticmethod
    def _safe_scope_id(value: Any, *, field: str) -> str:
        normalized = str(value or "").strip()
        if _SAFE_DEVICE_SCOPE_ID.fullmatch(normalized) is None:
            raise ValueError(f"invalid_{field}")
        return normalized
