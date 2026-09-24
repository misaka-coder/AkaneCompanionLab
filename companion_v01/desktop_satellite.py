from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import hmac
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from capcore import CapabilityToolSpec

from .capability_registry import BrokerExecutionResult, ExecutionReceipt, OPEN_BROWSER_TOOL_SPEC
from .desktop_satellite_specs import desktop_satellite_spec
from .turn_coordination import current_cancellation_check, cancellation_requested


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
    cancelled: Callable[[], bool] | None = field(default=None, repr=False)
    event: threading.Event = field(default_factory=threading.Event)
    send_started: bool = False
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
    bot_id: str
    tool_ids: frozenset[str]
    desktop_ui: bool = True
    presentation_only: bool = False


@dataclass
class _PendingAgentFrame:
    connection_id: str
    lease_epoch: str
    cancelled: Callable[[], bool] | None = field(default=None, repr=False)
    result: concurrent.futures.Future[dict[str, Any]] = field(
        default_factory=concurrent.futures.Future
    )


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
        self._ui_connection: _SatelliteConnection | None = None
        self._pending: dict[str, _PendingInvocation] = {}
        self._pending_agent_frames: dict[str, _PendingAgentFrame] = {}

    @property
    def enabled(self) -> bool:
        return bool(self._token)

    def for_bot(self, *, bot_id: str, memory_space_id: str) -> "BotScopedDesktopSatelliteOfferSource":
        return BotScopedDesktopSatelliteOfferSource(
            service=self,
            bot_id=bot_id,
            memory_space_id=memory_space_id,
        )

    async def handle_websocket(self, websocket: WebSocket, *, presentation_only: bool = False) -> None:
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
        registration_result = self._validate_registration(registration, presentation_only=presentation_only)
        if registration_result is None:
            await websocket.close(code=4403, reason="satellite_registration_rejected")
            return
        supported, bot_id = registration_result

        now = float(self._clock())
        connection = _SatelliteConnection(
            connection_id=f"satconn_{uuid.uuid4().hex}",
            lease_epoch=f"lease_{uuid.uuid4().hex}",
            offer_id=f"offer_{uuid.uuid4().hex}",
            loop=asyncio.get_running_loop(),
            outbound=asyncio.Queue(maxsize=64),
            expires_at=now + self._lease_ttl_seconds,
            connected_at=now,
            bot_id=bot_id,
            tool_ids=frozenset(supported),
            desktop_ui=registration.get("desktop_ui", True) is True,
            presentation_only=presentation_only,
        )
        if not self._install_connection(connection):
            await websocket.close(code=4429, reason="satellite_already_connected")
            return
        await websocket.send_json(
            {
                "type": "registered",
                "protocol_version": SATELLITE_PROTOCOL_VERSION,
                "instance_id": self.instance_id,
                "bot_id": connection.bot_id,
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
            self._remove_connection(connection.connection_id)
            await asyncio.gather(sender, return_exceptions=True)

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
        cancelled = current_cancellation_check()
        reason = self._cancellation_reason(cancelled)
        if reason:
            return BrokerExecutionResult(status="unavailable_before_dispatch", reason=reason)
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
                cancelled=cancelled,
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
            if spec.capability_id in {"computer_use", "browser_page_personal"}:
                from .computer_use.session import dispatch_scope, dispatch_control
                payload["control_scope"] = dispatch_scope.get()
                payload["control_dispatch"] = dict(dispatch_control.get() or {})
            queued = self._queue_message_locked(connection, payload)
            if not queued:
                self._pending.pop(invocation_id, None)
                return BrokerExecutionResult(status="unavailable_before_dispatch", reason="executor_queue_unavailable")
        if not pending.event.wait(max(1.0, min(30.0, float(timeout_seconds)))):
            with self._lock:
                current = self._pending.pop(invocation_id, None)
            if current is not None and current.result is not None:
                return current.result
            if pending.send_started or pending.acknowledged:
                return BrokerExecutionResult(
                    status="execution_unknown",
                    reason="executor_result_timeout" if pending.acknowledged else "executor_ack_timeout",
                    model_feedback="本地能力的执行结果暂时无法确认，请不要声称操作已经完成。",
                )
            return BrokerExecutionResult(
                status="unavailable_before_dispatch",
                reason="executor_ack_timeout",
                model_feedback="这次操作在发给桌面执行器前已超时，本地能力没有执行。",
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
            ui_connection = self._active_ui_connection_locked()
            return {
                "ok": True,
                "status": "online" if active else ("disabled" if not self.enabled else "offline"),
                "connected": active,
                "activeOfferCount": len(connection.tool_ids) if active and connection is not None else 0,
                "pendingInvocationCount": len(self._pending),
                "pendingAgentFrameCount": len(self._pending_agent_frames),
                "activeBotId": connection.bot_id if active and connection is not None else "",
                "desktopUiConnected": ui_connection is not None,
                "desktopUiBotId": ui_connection.bot_id if ui_connection is not None else "",
            }

    async def deliver_agent_frame(
        self,
        frame: Mapping[str, Any],
        *,
        bot_id: str,
        timeout_seconds: float = 5.0,
    ) -> dict[str, Any]:
        """Queue one ordinary desktop reply frame on the active app session."""

        if not isinstance(frame, Mapping):
            return {"ok": False, "status": "failed", "reason": "agent_frame_invalid"}
        expected_bot_id = str(bot_id or "").strip()
        if _SAFE_DEVICE_SCOPE_ID.fullmatch(expected_bot_id) is None:
            return {"ok": False, "status": "failed", "reason": "desktop_bot_invalid"}
        if cancellation_requested():
            return {"ok": False, "status": "cancelled", "reason": "turn_scope_revoked"}
        delivery_id = f"agent_{uuid.uuid4().hex}"
        with self._lock:
            connection = self._active_ui_connection_locked()
            if connection is None:
                return {"ok": False, "status": "unavailable", "reason": "desktop_ui_unavailable"}
            if connection.bot_id != expected_bot_id:
                return {"ok": False, "status": "unavailable", "reason": "desktop_bot_unavailable"}
            pending = _PendingAgentFrame(
                connection_id=connection.connection_id,
                lease_epoch=connection.lease_epoch,
                cancelled=current_cancellation_check(),
            )
            self._pending_agent_frames[delivery_id] = pending
            queued = self._queue_message_locked(
                connection,
                {
                    "type": "agent_event_frame",
                    "protocol_version": SATELLITE_PROTOCOL_VERSION,
                    "instance_id": self.instance_id,
                    "lease_epoch": connection.lease_epoch,
                    "delivery_id": delivery_id,
                    "payload": dict(frame),
                },
            )
            if not queued:
                self._pending_agent_frames.pop(delivery_id, None)
                return {"ok": False, "status": "unavailable", "reason": "desktop_queue_unavailable"}
        try:
            result = await asyncio.wait_for(
                asyncio.wrap_future(pending.result),
                timeout=max(1.0, min(15.0, float(timeout_seconds))),
            )
        except TimeoutError:
            return {"ok": False, "status": "unavailable", "reason": "desktop_delivery_ack_timeout"}
        finally:
            with self._lock:
                self._pending_agent_frames.pop(delivery_id, None)
        return result

    @staticmethod
    def _cancellation_reason(check: Callable[[], bool] | None) -> str:
        if check is None:
            return ""
        try:
            return "turn_scope_revoked" if check() else ""
        except Exception:
            return "turn_scope_check_failed"

    async def _send_loop(self, websocket: WebSocket, connection: _SatelliteConnection) -> None:
        while True:
            payload = await connection.outbound.get()
            if payload.get("type") == "invoke":
                invocation_id = str(payload.get("invocation_id") or "")
                with self._lock:
                    invocation = self._pending.get(invocation_id)
                if invocation is None:
                    continue
                reason = self._cancellation_reason(invocation.cancelled)
                with self._lock:
                    # The dispatch owner may time out while the scope check runs.
                    if (self._pending.get(invocation_id) is not invocation
                            or invocation.result is not None or invocation.send_started
                            or invocation.connection_id != connection.connection_id):
                        continue
                    if not reason and (self._connection is not connection
                                       or connection.expires_at <= float(self._clock())):
                        reason = "executor_unavailable"
                    if reason:
                        invocation.result = BrokerExecutionResult(
                            status="unavailable_before_dispatch", reason=reason)
                        invocation.event.set()
                        continue
                    # Once sending starts, no ack cannot prove no execution.
                    invocation.send_started = True
            if payload.get("type") == "agent_event_frame":
                delivery_id = str(payload.get("delivery_id") or "")
                with self._lock:
                    pending = self._pending_agent_frames.get(delivery_id)
                if pending is None:
                    continue
                reason = self._cancellation_reason(pending.cancelled)
                with self._lock:
                    if (self._pending_agent_frames.get(delivery_id) is not pending
                            or pending.result.done() or pending.connection_id != connection.connection_id):
                        continue
                    if not reason and (self._current_connection_locked(connection) is not connection
                                       or connection.expires_at <= float(self._clock())):
                        reason = "desktop_client_unavailable"
                if reason:
                    try:
                        pending.result.set_result({"ok": False, "status": "cancelled", "reason": reason})
                    except concurrent.futures.InvalidStateError:
                        # Delivery timed out/cancelled on its owning loop
                        # while the scope check ran; it has no live receipt.
                        pass
                    continue
            await websocket.send_json(payload)

    async def _handle_client_message(self, connection: _SatelliteConnection, message: Any) -> None:
        if not isinstance(message, dict):
            return
        message_type = str(message.get("type") or "").strip()
        if message_type == "heartbeat":
            if str(message.get("lease_epoch") or "").strip() != connection.lease_epoch:
                return
            with self._lock:
                current = self._current_connection_locked(connection)
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
        if message_type == "agent_event_result":
            delivery_id = str(message.get("delivery_id") or "").strip()
            with self._lock:
                pending_frame = self._pending_agent_frames.get(delivery_id)
                if (
                    pending_frame is None
                    or pending_frame.connection_id != connection.connection_id
                    or str(message.get("instance_id") or "").strip() != self.instance_id
                    or str(message.get("lease_epoch") or "").strip() != pending_frame.lease_epoch
                    or pending_frame.result.done()
                ):
                    return
                status = str(message.get("status") or "failed").strip().lower()
                reason = str(message.get("reason") or "").strip().lower()
                if status == "queued":
                    pending_frame.result.set_result({"ok": True, "status": "queued", "reason": ""})
                else:
                    pending_frame.result.set_result(
                        {"ok": False, "status": "failed", "reason": reason or "desktop_event_rejected"}
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
            tool_id = str(message.get("tool_id") or "").strip()
            data = self._safe_result_data(message.get("data"))
            if tool_id in {"computer_use", "browser_page_personal"}:
                from .computer_use.media import sanitize_device_result
                data = sanitize_device_result(message.get("data"))
                raw_reason = str(message.get("reason") or "")
                if re.fullmatch(r"[a-z][a-z0-9_]{0,79}", raw_reason):
                    reason = raw_reason
            # Image bytes are private transport material, consumed by the artifact
            # bridge before any model feedback or public event is constructed.
            if tool_id == "desktop_screenshot" and isinstance(message.get("data"), Mapping):
                encoded = message["data"].get("imageBase64")
                data.pop("imageBase64", None)
                if isinstance(encoded, str) and len(encoded) <= 12 * 1024 * 1024:
                    data["imageBase64"] = encoded
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

    def _validate_registration(self, value: Any, *, presentation_only: bool = False) -> tuple[set[str], str] | None:
        if not isinstance(value, dict) or str(value.get("type") or "") != "register":
            return None
        if int(value.get("protocol_version") or 0) != SATELLITE_PROTOCOL_VERSION:
            return None
        if str(value.get("instance_id") or "").strip() != self.instance_id:
            return None
        bot_id = str(value.get("bot_id") or "").strip()
        if _SAFE_DEVICE_SCOPE_ID.fullmatch(bot_id) is None:
            return None
        raw_offers = value.get("offers")
        if not isinstance(raw_offers, list):
            return None
        if presentation_only:
            # A presentation connection never contributes execution offers.
            return (set(), bot_id) if value.get("desktop_ui") is True and not raw_offers else None
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
        return (supported, bot_id) if supported else None

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
            current = self._current_connection_locked(connection)
            if current is not None and current.expires_at > now:
                return False
            if connection.presentation_only:
                self._ui_connection = connection
            else:
                self._connection = connection
        return True

    def _remove_connection(self, connection_id: str) -> None:
        with self._lock:
            current = self._connection
            if current is not None and current.connection_id == connection_id:
                self._connection = None
            if self._ui_connection is not None and self._ui_connection.connection_id == connection_id:
                self._ui_connection = None
            for pending in self._pending.values():
                if pending.connection_id == connection_id and pending.result is None:
                    if pending.send_started or pending.acknowledged:
                        pending.result = BrokerExecutionResult(
                            status="execution_unknown",
                            reason=("executor_disconnected_after_accept" if pending.acknowledged
                                    else "executor_disconnected_after_dispatch"),
                            model_feedback="桌面执行器在操作过程中断开，结果无法确认；请不要声称本地操作已经完成。",
                        )
                    else:
                        pending.result = BrokerExecutionResult(
                            status="unavailable_before_dispatch",
                            reason="executor_disconnected_before_accept",
                            model_feedback="桌面执行器在接受操作前断开，这次没有执行本地能力。",
                        )
                    pending.event.set()
            for pending in self._pending_agent_frames.values():
                if pending.connection_id == connection_id and not pending.result.done():
                    pending.result.set_result(
                        {"ok": False, "status": "unavailable", "reason": "desktop_client_disconnected"}
                    )

    def _active_connection_locked(self, spec: CapabilityToolSpec) -> _SatelliteConnection | None:
        connection = self._active_connection_any_locked()
        if connection is None:
            return None
        if spec.capability_id not in connection.tool_ids:
            return None
        return connection

    def _active_connection_any_locked(self) -> _SatelliteConnection | None:
        connection = self._connection
        if connection is None or connection.expires_at <= float(self._clock()):
            return None
        return connection

    def _current_connection_locked(self, connection: _SatelliteConnection) -> _SatelliteConnection | None:
        return self._ui_connection if connection.presentation_only else self._connection

    def _active_ui_connection_locked(self) -> _SatelliteConnection | None:
        ui = self._ui_connection
        if ui is not None and ui.expires_at > float(self._clock()):
            return ui
        connection = self._active_connection_any_locked()
        return connection if connection is not None and connection.desktop_ui else None

    def _queue_message_locked(self, connection: _SatelliteConnection, payload: dict[str, Any]) -> bool:
        invocation_id = str(payload.get("invocation_id") or "").strip()
        delivery_id = str(payload.get("delivery_id") or "").strip()

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
                    pending_frame = self._pending_agent_frames.get(delivery_id)
                    if pending_frame is not None and not pending_frame.result.done():
                        pending_frame.result.set_result(
                            {"ok": False, "status": "unavailable", "reason": "desktop_queue_unavailable"}
                        )

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
            "foreground_is_desktop_pet",
            "foreground_unavailable",
            "no_active_session",
            "control_failed",
            "read_failed",
            "join_failed",
            "process_enumeration_failed",
            "screen_dimensions_unavailable",
            "screen_capture_unavailable",
            "screen_capture_failed",
            "screen_encoding_failed",
            "screen_image_too_large",
            "permission_denied",
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
