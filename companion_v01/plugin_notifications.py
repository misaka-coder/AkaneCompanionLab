"""Host-owned notification port for proactive delivery from supervised plugin jobs.

A plugin job obtains this port via registrar.get_notification_port() during
registration and calls send() to deliver messages to users without holding a
reference to the QQ gateway or any other concrete channel implementation.

Channel encoding for recipient_id:
  "group:<group_id>"   — QQ group message
  "user:<qq_number>"   — QQ private message

The NullNotificationPort is used when no real gateway is bound (tests,
public-mode, gateway disabled).  The QQTextNotificationPort wraps NapCatQQGateway.
"""

from __future__ import annotations

import asyncio
import re
from collections import OrderedDict
from typing import Any, Callable

from .plugin_api import NotificationIntent, NotificationResult


MAX_NOTIFICATION_TEXT_CHARS = 4000
MAX_NOTIFICATION_IDEMPOTENCY_KEY_CHARS = 128
MAX_NOTIFICATION_DELIVERY_HISTORY = 4096
_RECIPIENT_PATTERN = re.compile(r"^(group|user):(\d{1,20})$")


class _NotificationDeliveryLedger:
    """Process-lifetime, plugin-scoped deduplication for notification delivery."""

    def __init__(self, *, max_delivered: int = MAX_NOTIFICATION_DELIVERY_HISTORY) -> None:
        self._max_delivered = max(1, int(max_delivered))
        self._lock = asyncio.Lock()
        self._delivered: OrderedDict[tuple[str, str], None] = OrderedDict()
        self._inflight: dict[tuple[str, str], asyncio.Future[NotificationResult]] = {}

    async def reserve(
        self,
        plugin_id: str,
        idempotency_key: str,
    ) -> tuple[str, asyncio.Future[NotificationResult] | None]:
        scoped_key = (plugin_id, idempotency_key)
        async with self._lock:
            if scoped_key in self._delivered:
                self._delivered.move_to_end(scoped_key)
                return "delivered", None
            existing = self._inflight.get(scoped_key)
            if existing is not None:
                return "inflight", existing
            future = asyncio.get_running_loop().create_future()
            self._inflight[scoped_key] = future
            return "owner", future

    async def finish(
        self,
        plugin_id: str,
        idempotency_key: str,
        result: NotificationResult,
    ) -> None:
        scoped_key = (plugin_id, idempotency_key)
        async with self._lock:
            future = self._inflight.pop(scoped_key, None)
            if result.ok:
                self._delivered[scoped_key] = None
                self._delivered.move_to_end(scoped_key)
                while len(self._delivered) > self._max_delivered:
                    self._delivered.popitem(last=False)
            if future is not None and not future.done():
                future.set_result(result)


class _PluginScopedNotificationPort:
    """Namespace one host notification port to a single activated plugin."""

    def __init__(
        self,
        *,
        plugin_id: str,
        delegate: Any,
        ledger: _NotificationDeliveryLedger,
        availability_provider: Callable[[], bool],
    ) -> None:
        self._plugin_id = plugin_id
        self._delegate = delegate
        self._ledger = ledger
        self._availability_provider = availability_provider

    async def send(self, intent: NotificationIntent) -> NotificationResult:
        try:
            available = bool(self._availability_provider())
        except Exception:
            available = False
        if not available:
            return NotificationResult(ok=False, status="host_unavailable", reason="host_unavailable")
        if not isinstance(intent, NotificationIntent):
            return NotificationResult(ok=False, status="rejected", reason="invalid_notification_intent")
        idempotency_key = str(intent.idempotency_key or "").strip()
        if not idempotency_key or len(idempotency_key) > MAX_NOTIFICATION_IDEMPOTENCY_KEY_CHARS:
            return NotificationResult(ok=False, status="rejected", reason="invalid_idempotency_key")

        reservation, future = await self._ledger.reserve(self._plugin_id, idempotency_key)
        if reservation == "delivered":
            return NotificationResult(ok=True, status="already_delivered")
        if reservation == "inflight" and future is not None:
            prior_result = await asyncio.shield(future)
            if prior_result.ok:
                return NotificationResult(ok=True, status="already_delivered")
            return prior_result

        try:
            result = await self._delegate.send(intent)
            if not isinstance(result, NotificationResult):
                result = NotificationResult(ok=False, status="error", reason="invalid_notification_result")
        except asyncio.CancelledError:
            cancelled_result = NotificationResult(ok=False, status="error", reason="delivery_cancelled")
            await asyncio.shield(self._ledger.finish(self._plugin_id, idempotency_key, cancelled_result))
            raise
        except Exception:
            result = NotificationResult(ok=False, status="error", reason="delivery_exception")

        await asyncio.shield(self._ledger.finish(self._plugin_id, idempotency_key, result))
        return result


class NullNotificationPort:
    """No-op port used when the QQ gateway is unavailable or disabled."""

    async def send(self, intent: NotificationIntent) -> NotificationResult:
        return NotificationResult(
            ok=False,
            status="not_configured",
            reason="no_notification_port_bound",
        )


class QQTextNotificationPort:
    """Deliver text notifications to QQ groups or users via NapCatQQGateway.

    Construction does not touch the gateway.  Each send() call dispatches
    synchronously through the gateway on a thread-pool worker so the caller's
    asyncio event loop is not blocked.

    recipient_id format:
        "group:<group_id>"  — send_replies to a group (is_group=True)
        "user:<qq_number>"  — send_replies to a private user (is_group=False)

    Error paths always return a NotificationResult rather than raising so that
    a job's delivery loop can continue without catching exceptions.
    """

    def __init__(self, gateway: Any) -> None:
        if not callable(getattr(gateway, "send_replies", None)):
            raise TypeError("gateway_must_have_send_replies")
        self._gateway = gateway

    async def send(self, intent: NotificationIntent) -> NotificationResult:
        if intent.channel != "qq_text":
            return NotificationResult(ok=False, status="rejected", reason="unsupported_channel")

        match = _RECIPIENT_PATTERN.fullmatch(str(intent.recipient_id or "").strip())
        if match is None:
            return NotificationResult(ok=False, status="rejected", reason="invalid_recipient_id")

        recipient_type = match.group(1)
        recipient_num = int(match.group(2))
        if recipient_num <= 0:
            return NotificationResult(ok=False, status="rejected", reason="invalid_recipient_id")

        text = str(intent.text or "").strip()
        if not text:
            return NotificationResult(ok=False, status="rejected", reason="empty_text")
        # Bound text length before delivery
        text_truncated = len(text) > MAX_NOTIFICATION_TEXT_CHARS
        if text_truncated:
            text = text[:MAX_NOTIFICATION_TEXT_CHARS]

        try:
            context = _build_proactive_context(recipient_type, recipient_num)
            result = await asyncio.to_thread(self._gateway.send_replies, context, [text])
            ok = bool(result.get("ok", False)) if isinstance(result, dict) else False
            reason = str(result.get("reason") or "") if isinstance(result, dict) else ""
            if ok and text_truncated and not reason:
                reason = "text_truncated"
            return NotificationResult(
                ok=ok,
                status="delivered" if ok else "error",
                reason=reason,
            )
        except Exception:
            return NotificationResult(ok=False, status="error", reason="delivery_exception")


def _build_proactive_context(recipient_type: str, recipient_num: int) -> Any:
    """Return a minimal QQMessageContext for a proactive (not-reply) delivery.

    Only the fields used by send_reply / send_replies for routing are set.
    All other fields keep their defaults.
    """
    from .qq_gateway import QQMessageContext

    is_group = recipient_type == "group"
    return QQMessageContext(
        should_respond=True,
        reason="proactive_plugin_notification",
        should_record=False,
        is_group=is_group,
        target_id=recipient_num,
        user_id=0 if is_group else recipient_num,
        group_id=recipient_num if is_group else 0,
    )


__all__ = [
    "MAX_NOTIFICATION_IDEMPOTENCY_KEY_CHARS",
    "MAX_NOTIFICATION_TEXT_CHARS",
    "NullNotificationPort",
    "QQTextNotificationPort",
]
