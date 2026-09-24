"""Tests for companion_v01.plugin_notifications and PluginHost notification port integration."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from capcore import CapabilityDescriptor, CapabilityResult, HealthStatus

from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_api import (
    AKANE_PLUGIN_API_VERSION,
    CAPABILITY_PROMPT_INVOKE_PERMISSION,
    NETWORK_READ_PERMISSION,
    NOTIFICATION_SEND_PERMISSION,
    NotificationIntent,
    NotificationResult,
    PluginManifest,
)
from companion_v01.plugin_contribution_policy import TrustedStatefulPluginContributionPolicy
from companion_v01.plugin_host import PluginHost
from companion_v01.plugin_notifications import (
    MAX_NOTIFICATION_TEXT_CHARS,
    NullNotificationPort,
    QQTextNotificationPort,
)


# ---------------------------------------------------------------------------
# Shared test-plugin infrastructure for PluginHost integration tests
# ---------------------------------------------------------------------------

_PLUGIN_ID = "akane.test.stateful"
_CAPABILITY_ID = f"{_PLUGIN_ID}.query.v1"


class _FakeDistribution:
    def __init__(self, *, version: str = "0.1.0") -> None:
        self.version = version
        self.metadata = {"Name": "akane-test-stateful-plugin"}

    def read_text(self, filename: str) -> str | None:
        return None


class _FakeEntryPoint:
    def __init__(self, name: str, factory: Any, *, distribution: Any | None = None) -> None:
        self.name = name
        self.dist = distribution or _FakeDistribution()
        self._factory = factory

    def load(self) -> Any:
        return self._factory


class _FakeAdapter:
    """Minimal CapabilityAdapter that satisfies TrustedStatefulPluginContributionPolicy."""

    provider_id = "provider.test.stateful"

    async def health(self) -> HealthStatus:
        return HealthStatus(ok=True, status="ready")

    async def list_capabilities(self) -> tuple[CapabilityDescriptor, ...]:
        return (
            CapabilityDescriptor(
                id=_CAPABILITY_ID,
                display_name="Test Query",
                short_hint="A test stateful query.",
                visible_in=("diagnostics",),
                prompt_exposed=True,
                risk="low",
                confirm="never",
                effects=("network",),
                trigger=None,
                inputs=(),
                outputs=(),
                raw={},
            ),
        )

    async def invoke(self, capability_id: str, args: dict, ctx: Any) -> CapabilityResult:
        return CapabilityResult(is_error=False, status="ok", content={"result": "ok"})

    async def aclose(self) -> None:
        pass


class _PluginWithNotificationPort:
    """Plugin that attempts get_notification_port() during register().

    When declare_permission=True, the manifest declares notification.send and
    the captured port will be a plugin-scoped wrapper around the host port.  When False, the
    manifest omits the permission and get_notification_port() raises, causing
    activation to fail.
    """

    def __init__(self, *, declare_permission: bool) -> None:
        self.captured_port: Any = None
        if declare_permission:
            permissions: tuple[str, ...] = (
                CAPABILITY_PROMPT_INVOKE_PERMISSION,
                NETWORK_READ_PERMISSION,
                NOTIFICATION_SEND_PERMISSION,
            )
        else:
            permissions = (
                CAPABILITY_PROMPT_INVOKE_PERMISSION,
                NETWORK_READ_PERMISSION,
            )
        self.manifest = PluginManifest(
            plugin_id=_PLUGIN_ID,
            plugin_version="0.1.0",
            plugin_api_version=AKANE_PLUGIN_API_VERSION,
            permissions=permissions,
        )

    def register(self, registrar: Any) -> None:
        registrar.add_capability_adapter(_FakeAdapter())
        # This call succeeds when the permission is declared and the host has
        # a port bound; raises RuntimeError otherwise, failing activation.
        self.captured_port = registrar.get_notification_port()


def _entry_point_for(plugin: Any) -> _FakeEntryPoint:
    def factory() -> Any:
        return plugin

    return _FakeEntryPoint(_PLUGIN_ID, factory)


def _stateful_host(
    plugin: Any,
    *,
    notification_port: Any | None = None,
) -> PluginHost:
    selection = PluginSelection(plugin_id=_PLUGIN_ID, enabled=True)
    host = PluginHost(
        (selection,),
        contribution_policy=TrustedStatefulPluginContributionPolicy(),
        entry_points_provider=lambda: [_entry_point_for(plugin)],
    )
    if notification_port is not None:
        host.bind_notification_port(notification_port)
    return host


# ---------------------------------------------------------------------------
# Helper factories for QQTextNotificationPort tests
# ---------------------------------------------------------------------------

def _make_gateway(*, raises: bool = False) -> MagicMock:
    gw = MagicMock()
    if raises:
        gw.send_replies.side_effect = RuntimeError("gateway_down")
    else:
        gw.send_replies.return_value = {"ok": True}
    return gw


def _intent(
    *,
    channel: str = "qq_text",
    recipient_id: str = "group:100200300",
    text: str = "hello world",
    idempotency_key: str = "test-key-1",
) -> NotificationIntent:
    return NotificationIntent(
        channel=channel,
        recipient_id=recipient_id,
        text=text,
        idempotency_key=idempotency_key,
    )


# ---------------------------------------------------------------------------
# Test 1: NullNotificationPort.send() returns not_configured
# ---------------------------------------------------------------------------

class NullNotificationPortTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_returns_not_configured(self) -> None:
        port = NullNotificationPort()
        result = await port.send(
            NotificationIntent(
                channel="qq_text",
                recipient_id="user:123456789",
                text="hello",
                idempotency_key="null-key-1",
            )
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "not_configured")


# ---------------------------------------------------------------------------
# Tests 2–8: QQTextNotificationPort
# ---------------------------------------------------------------------------

class QQTextNotificationPortTests(unittest.IsolatedAsyncioTestCase):
    # Test 2: invalid channel returns rejected
    async def test_invalid_channel_returns_rejected(self) -> None:
        port = QQTextNotificationPort(_make_gateway())
        result = await port.send(_intent(channel="sms"))
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.reason, "unsupported_channel")

    # Test 3: invalid recipient_id returns rejected
    async def test_invalid_recipient_id_returns_rejected(self) -> None:
        port = QQTextNotificationPort(_make_gateway())
        result = await port.send(_intent(recipient_id="badformat"))
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.reason, "invalid_recipient_id")

        zero_result = await port.send(_intent(recipient_id="user:0"))
        self.assertFalse(zero_result.ok)
        self.assertEqual(zero_result.reason, "invalid_recipient_id")

    # Test 4: empty text returns rejected
    async def test_empty_text_returns_rejected(self) -> None:
        port = QQTextNotificationPort(_make_gateway())
        result = await port.send(_intent(text="   "))
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.reason, "empty_text")

    # Test 5: group:<id> routes correctly
    async def test_group_recipient_routes_correctly(self) -> None:
        gw = _make_gateway()
        port = QQTextNotificationPort(gw)
        with patch(
            "companion_v01.plugin_notifications._build_proactive_context",
            return_value=object(),
        ) as mock_build:
            result = await port.send(_intent(recipient_id="group:100200300"))
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "delivered")
        mock_build.assert_called_once_with("group", 100200300)
        gw.send_replies.assert_called_once()

    # Test 6: user:<id> routes correctly
    async def test_user_recipient_routes_correctly(self) -> None:
        gw = _make_gateway()
        port = QQTextNotificationPort(gw)
        with patch(
            "companion_v01.plugin_notifications._build_proactive_context",
            return_value=object(),
        ) as mock_build:
            result = await port.send(_intent(recipient_id="user:999888777"))
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "delivered")
        mock_build.assert_called_once_with("user", 999888777)
        gw.send_replies.assert_called_once()

    # Test 7: gateway exception returns error result
    async def test_gateway_exception_returns_error(self) -> None:
        port = QQTextNotificationPort(_make_gateway(raises=True))
        with patch(
            "companion_v01.plugin_notifications._build_proactive_context",
            return_value=object(),
        ):
            result = await port.send(_intent())
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "error")
        self.assertEqual(result.reason, "delivery_exception")

    # Test 8: text over MAX_NOTIFICATION_TEXT_CHARS is truncated before delivery
    async def test_long_text_is_truncated_before_delivery(self) -> None:
        gw = _make_gateway()
        port = QQTextNotificationPort(gw)
        long_text = "a" * (MAX_NOTIFICATION_TEXT_CHARS + 500)
        with patch(
            "companion_v01.plugin_notifications._build_proactive_context",
            return_value=object(),
        ):
            result = await port.send(_intent(text=long_text))
        self.assertTrue(result.ok)
        # Verify the text actually delivered to the gateway was truncated
        call_args = gw.send_replies.call_args
        delivered_messages: list[str] = call_args[0][1]
        self.assertEqual(len(delivered_messages[0]), MAX_NOTIFICATION_TEXT_CHARS)
        self.assertEqual(result.reason, "text_truncated")


# ---------------------------------------------------------------------------
# Tests 9–10: PluginHost notification port integration
# ---------------------------------------------------------------------------

class PluginHostNotificationPortTests(unittest.IsolatedAsyncioTestCase):
    # Test 9: plugin with notification.send permission receives notification port
    async def test_plugin_with_permission_receives_notification_port(self) -> None:
        plugin = _PluginWithNotificationPort(declare_permission=True)
        port = NullNotificationPort()
        host = _stateful_host(plugin, notification_port=port)
        try:
            snapshot = await host.start()
            plugin_status = snapshot["plugins"][0]
            self.assertEqual(plugin_status["status"], "active", plugin_status.get("reason"))
            self.assertIsNot(plugin.captured_port, port)
            first = await plugin.captured_port.send(_intent(idempotency_key="same-delivery"))
            second = await plugin.captured_port.send(_intent(idempotency_key="same-delivery"))
            self.assertEqual(first.status, "not_configured")
            self.assertEqual(second.status, "not_configured")
        finally:
            await host.stop()

    async def test_successful_delivery_is_deduplicated_per_plugin(self) -> None:
        plugin = _PluginWithNotificationPort(declare_permission=True)
        gateway = _make_gateway()
        host = _stateful_host(plugin, notification_port=QQTextNotificationPort(gateway))
        try:
            snapshot = await host.start()
            self.assertEqual(snapshot["plugins"][0]["status"], "active")

            first = await plugin.captured_port.send(_intent(idempotency_key="delivery-1"))
            duplicate = await plugin.captured_port.send(_intent(idempotency_key="delivery-1"))

            self.assertEqual(first.status, "delivered")
            self.assertTrue(duplicate.ok)
            self.assertEqual(duplicate.status, "already_delivered")
            gateway.send_replies.assert_called_once()

            await host.stop()
            unavailable = await plugin.captured_port.send(_intent(idempotency_key="after-stop"))
            self.assertFalse(unavailable.ok)
            self.assertEqual(unavailable.status, "host_unavailable")
            gateway.send_replies.assert_called_once()
        finally:
            await host.stop()

    async def test_failed_delivery_can_be_retried_with_same_key(self) -> None:
        plugin = _PluginWithNotificationPort(declare_permission=True)
        gateway = _make_gateway()
        gateway.send_replies.side_effect = [
            {"ok": False, "reason": "temporary_failure"},
            {"ok": True},
        ]
        host = _stateful_host(plugin, notification_port=QQTextNotificationPort(gateway))
        try:
            await host.start()
            first = await plugin.captured_port.send(_intent(idempotency_key="retryable"))
            retry = await plugin.captured_port.send(_intent(idempotency_key="retryable"))

            self.assertFalse(first.ok)
            self.assertTrue(retry.ok)
            self.assertEqual(gateway.send_replies.call_count, 2)
        finally:
            await host.stop()

    async def test_concurrent_duplicate_waits_for_single_delivery(self) -> None:
        plugin = _PluginWithNotificationPort(declare_permission=True)

        class SlowPort:
            def __init__(self) -> None:
                self.calls = 0
                self.started = asyncio.Event()
                self.release = asyncio.Event()

            async def send(self, intent: NotificationIntent) -> NotificationResult:
                self.calls += 1
                self.started.set()
                await self.release.wait()
                return NotificationResult(ok=True, status="delivered")

        delegate = SlowPort()
        host = _stateful_host(plugin, notification_port=delegate)
        try:
            await host.start()
            first_task = asyncio.create_task(
                plugin.captured_port.send(_intent(idempotency_key="concurrent"))
            )
            await delegate.started.wait()
            duplicate_task = asyncio.create_task(
                plugin.captured_port.send(_intent(idempotency_key="concurrent"))
            )
            await asyncio.sleep(0)
            delegate.release.set()
            first, duplicate = await asyncio.gather(first_task, duplicate_task)

            self.assertEqual(first.status, "delivered")
            self.assertEqual(duplicate.status, "already_delivered")
            self.assertEqual(delegate.calls, 1)
        finally:
            await host.stop()

    async def test_empty_idempotency_key_is_rejected_before_delivery(self) -> None:
        plugin = _PluginWithNotificationPort(declare_permission=True)
        gateway = _make_gateway()
        host = _stateful_host(plugin, notification_port=QQTextNotificationPort(gateway))
        try:
            await host.start()
            result = await plugin.captured_port.send(_intent(idempotency_key=""))
            self.assertFalse(result.ok)
            self.assertEqual(result.reason, "invalid_idempotency_key")
            gateway.send_replies.assert_not_called()
        finally:
            await host.stop()

    # Test 10: plugin without notification.send that calls get_notification_port() fails activation
    async def test_plugin_without_permission_activation_fails(self) -> None:
        plugin = _PluginWithNotificationPort(declare_permission=False)
        # No notification port bound to host; plugin still calls get_notification_port()
        host = _stateful_host(plugin)
        try:
            snapshot = await host.start()
            plugin_status = snapshot["plugins"][0]
            # Activation must fail because registrar raises RuntimeError
            # when notification_permission is not set
            self.assertNotEqual(plugin_status["status"], "active")
            self.assertEqual(plugin_status["reason"], "plugin_registration_failed")
        finally:
            await host.stop()


if __name__ == "__main__":
    unittest.main()
