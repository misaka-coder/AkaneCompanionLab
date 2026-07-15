"""Tests for PluginQQCommandBroker and PluginHost QQ command integration.

Covers:
  1. handles() — True for registered command, False for unknown
  2. dispatch() routes to correct handler and returns its result
  3. Handler exception → PluginQQCommandResult(handled=True, reason="handler_exception")
  4. Handler timeout → handled=True, reason="handler_timeout"
  5. reply_text longer than MAX_COMMAND_REPLY_CHARS is truncated
  6. dispatch() on unknown command → handled=False
  7. PluginHost: plugin with qq.command.register permission → commands visible in broker
  8. Plugin without qq.command.register tries add_qq_command() → activation fails
  9. build_qq_command_broker() empty when no plugins have commands
 10. TrustedStatefulPluginContributionPolicy accepts qq.command.register as optional permission
"""

from __future__ import annotations

import asyncio
import unittest
from typing import Any, Callable

from capcore import CapabilityDescriptor, CapabilityResult, HealthStatus, InvocationContext

from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_api import (
    AKANE_PLUGIN_API_VERSION,
    CAPABILITY_PROMPT_INVOKE_PERMISSION,
    NETWORK_READ_PERMISSION,
    PLUGIN_QQ_COMMAND_PERMISSION,
    PluginManifest,
    PluginQQCommandRequest,
    PluginQQCommandResult,
)
from companion_v01.plugin_contribution_policy import (
    TrustedReadNetworkContributionPolicy,
    TrustedStatefulPluginContributionPolicy,
)
from companion_v01.plugin_host import PluginHost
from companion_v01.plugin_qq_commands import (
    COMMAND_FAILURE_REPLY,
    MAX_COMMAND_ARGS_CHARS,
    MAX_COMMAND_REPLY_CHARS,
    PluginQQCommandBroker,
    _PluginCommandRegistration,
)


PLUGIN_ID = "akane.test.qq.commands"
CAP_ID = f"{PLUGIN_ID}.query.v1"

# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------


def _descriptor() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        id=CAP_ID,
        display_name="QQ command test",
        short_hint="Test capability.",
        visible_in=("base", "web", "desktop", "qq"),
        prompt_exposed=True,
        risk="low",
        confirm="never",
        effects=("network",),
        trigger=None,
        inputs=(),
        outputs=(),
        raw={},
    )


class _FakeAdapter:
    provider_id = "provider.test.qq.commands"

    async def health(self) -> HealthStatus:
        return HealthStatus(ok=True, status="ok")

    async def list_capabilities(self) -> tuple[CapabilityDescriptor, ...]:
        return (_descriptor(),)

    async def invoke(
        self,
        capability_id: str,
        args: Any,
        ctx: InvocationContext,
    ) -> CapabilityResult:
        return CapabilityResult(is_error=False, status="ok", content={})

    async def aclose(self) -> None:
        pass


class _SimpleHandler:
    """Returns a fixed PluginQQCommandResult on every call."""

    def __init__(self, result: PluginQQCommandResult) -> None:
        self._result = result
        self.received: list[PluginQQCommandRequest] = []

    async def handle(self, request: PluginQQCommandRequest) -> PluginQQCommandResult:
        self.received.append(request)
        return self._result


class _ExceptionHandler:
    async def handle(self, request: PluginQQCommandRequest) -> PluginQQCommandResult:
        raise ValueError("deliberate handler error")


class _TimeoutHandler:
    """Never returns; used only when asyncio.wait_for is mocked to raise TimeoutError."""

    async def handle(self, request: PluginQQCommandRequest) -> PluginQQCommandResult:
        await asyncio.sleep(999)
        return PluginQQCommandResult(handled=True)  # pragma: no cover


def _reg(command: str, handler: Any) -> _PluginCommandRegistration:
    return _PluginCommandRegistration(
        plugin_id=PLUGIN_ID,
        command=command.lower(),
        handler=handler,
    )


class FakeDistribution:
    def __init__(self, *, version: str = "0.1.0") -> None:
        self.version = version
        self.metadata = {"Name": "akane-test-qq-commands-plugin"}

    def read_text(self, filename: str) -> str | None:
        return None


class FakeEntryPoint:
    def __init__(
        self,
        name: str,
        factory: Callable[..., Any],
        *,
        version: str = "0.1.0",
    ) -> None:
        self.name = name
        self.dist = FakeDistribution(version=version)
        self._factory = factory

    def load(self) -> Callable[..., Any]:
        return self._factory


def _entry_points(factory: Callable) -> Callable[[], tuple[FakeEntryPoint, ...]]:
    return lambda: (FakeEntryPoint(PLUGIN_ID, factory),)


# ---------------------------------------------------------------------------
# 1. handles() — sync
# ---------------------------------------------------------------------------


class BrokerHandlesTests(unittest.TestCase):
    def _broker(self, *commands: str) -> PluginQQCommandBroker:
        handler = _SimpleHandler(PluginQQCommandResult(handled=True))
        regs = tuple(_reg(cmd, handler) for cmd in commands)
        return PluginQQCommandBroker(regs)

    def test_handles_returns_true_for_registered_command(self) -> None:
        broker = self._broker("/balance", "/help")
        self.assertTrue(broker.handles("/balance"))
        self.assertTrue(broker.handles("/help"))

    def test_handles_returns_false_for_unknown_command(self) -> None:
        broker = self._broker("/balance")
        self.assertFalse(broker.handles("/unknown"))
        self.assertFalse(broker.handles(""))

    def test_handles_is_case_insensitive(self) -> None:
        broker = self._broker("/Balance")
        self.assertTrue(broker.handles("/balance"))
        self.assertTrue(broker.handles("/BALANCE"))

    def test_registered_commands_reflects_index(self) -> None:
        broker = self._broker("/balance", "/help")
        self.assertIn("/balance", broker.registered_commands)
        self.assertIn("/help", broker.registered_commands)


# ---------------------------------------------------------------------------
# 2–5. dispatch() — async
# ---------------------------------------------------------------------------


class BrokerDispatchTests(unittest.IsolatedAsyncioTestCase):
    def _broker(self, *regs: _PluginCommandRegistration) -> PluginQQCommandBroker:
        return PluginQQCommandBroker(regs)

    # 2. correct handler receives the request and result is returned
    async def test_dispatch_routes_to_registered_handler(self) -> None:
        expected = PluginQQCommandResult(handled=True, reply_text="pong")
        handler = _SimpleHandler(expected)
        broker = self._broker(_reg("/ping", handler))

        result = await broker.dispatch(
            command="/ping",
            args="world",
            qq_number=100,
            group_id=200,
            is_group=True,
            sender_role="ADMIN",
        )

        self.assertTrue(result.handled)
        self.assertEqual(result.reply_text, "pong")
        self.assertEqual(len(handler.received), 1)
        req = handler.received[0]
        self.assertEqual(req.command, "/ping")
        self.assertEqual(req.args, "world")
        self.assertEqual(req.qq_number, 100)
        self.assertEqual(req.group_id, 200)
        self.assertTrue(req.is_group)
        self.assertEqual(req.sender_role, "admin")
        self.assertEqual(len(req.idempotency_key), 32)

    # 3. handler exception → handled=True, reason="handler_exception"
    async def test_dispatch_handler_exception_returns_handler_exception(self) -> None:
        broker = self._broker(_reg("/crash", _ExceptionHandler()))

        result = await broker.dispatch(
            command="/crash",
            args="",
            qq_number=0,
            group_id=0,
            is_group=False,
        )

        self.assertTrue(result.handled)
        self.assertEqual(result.reason, "handler_exception")
        self.assertEqual(result.reply_text, COMMAND_FAILURE_REPLY)

    # 4. handler timeout → handled=True, reason="handler_timeout"
    async def test_dispatch_handler_timeout_returns_handler_timeout(self) -> None:
        broker = PluginQQCommandBroker(
            (_reg("/slow", _TimeoutHandler()),),
            handler_timeout_seconds=0.01,
        )

        result = await broker.dispatch(
            command="/slow",
            args="",
            qq_number=0,
            group_id=0,
            is_group=False,
        )

        self.assertTrue(result.handled)
        self.assertEqual(result.reason, "handler_timeout")
        self.assertEqual(result.reply_text, COMMAND_FAILURE_REPLY)

    # 5. reply_text truncated at MAX_COMMAND_REPLY_CHARS
    async def test_dispatch_truncates_long_reply_text(self) -> None:
        long_reply = "x" * (MAX_COMMAND_REPLY_CHARS + 50)
        handler = _SimpleHandler(PluginQQCommandResult(handled=True, reply_text=long_reply))
        broker = self._broker(_reg("/long", handler))

        result = await broker.dispatch(
            command="/long",
            args="",
            qq_number=0,
            group_id=0,
            is_group=False,
        )

        self.assertTrue(result.handled)
        self.assertEqual(len(result.reply_text), MAX_COMMAND_REPLY_CHARS)

    async def test_dispatch_does_not_truncate_at_limit(self) -> None:
        exact_reply = "y" * MAX_COMMAND_REPLY_CHARS
        handler = _SimpleHandler(PluginQQCommandResult(handled=True, reply_text=exact_reply))
        broker = self._broker(_reg("/exact", handler))

        result = await broker.dispatch(
            command="/exact",
            args="",
            qq_number=0,
            group_id=0,
            is_group=False,
        )

        self.assertEqual(len(result.reply_text), MAX_COMMAND_REPLY_CHARS)

    async def test_dispatch_sanitizes_plugin_reason_and_hashes_source_event_id(self) -> None:
        handler = _SimpleHandler(
            PluginQQCommandResult(
                handled=True,
                reply_text="safe reply",
                reason="C:\\private\\secret.txt",
            )
        )
        broker = self._broker(_reg("/safe", handler))

        first = await broker.dispatch(
            command="/safe",
            args="",
            qq_number=100,
            group_id=0,
            is_group=False,
            idempotency_key="raw-message-id",
        )
        await broker.dispatch(
            command="/safe",
            args="",
            qq_number=100,
            group_id=0,
            is_group=False,
            idempotency_key="raw-message-id",
        )

        self.assertEqual(first.reason, "plugin_reported_error")
        self.assertEqual(handler.received[0].idempotency_key, handler.received[1].idempotency_key)
        self.assertNotEqual(handler.received[0].idempotency_key, "raw-message-id")

    async def test_dispatch_rejects_oversized_args_before_handler(self) -> None:
        handler = _SimpleHandler(PluginQQCommandResult(handled=True, reply_text="should not run"))
        broker = self._broker(_reg("/bounded", handler))

        result = await broker.dispatch(
            command="/bounded",
            args="x" * (MAX_COMMAND_ARGS_CHARS + 1),
            qq_number=100,
            group_id=0,
            is_group=False,
        )

        self.assertEqual(result.reason, "command_args_too_large")
        self.assertEqual(result.reply_text, COMMAND_FAILURE_REPLY)
        self.assertEqual(handler.received, [])

    # 6. unknown command → handled=False
    async def test_dispatch_unknown_command_returns_not_handled(self) -> None:
        handler = _SimpleHandler(PluginQQCommandResult(handled=True))
        broker = self._broker(_reg("/known", handler))

        result = await broker.dispatch(
            command="/unknown",
            args="",
            qq_number=0,
            group_id=0,
            is_group=False,
        )

        self.assertFalse(result.handled)

    async def test_dispatch_empty_broker_returns_not_handled(self) -> None:
        broker = PluginQQCommandBroker(())

        result = await broker.dispatch(
            command="/anything",
            args="",
            qq_number=0,
            group_id=0,
            is_group=False,
        )

        self.assertFalse(result.handled)


# ---------------------------------------------------------------------------
# 7–9. PluginHost integration
# ---------------------------------------------------------------------------


def _make_plugin_with_qq_command(
    command: str = "/balance",
    permissions: tuple[str, ...] = (
        CAPABILITY_PROMPT_INVOKE_PERMISSION,
        NETWORK_READ_PERMISSION,
        PLUGIN_QQ_COMMAND_PERMISSION,
    ),
) -> Callable[[], Any]:
    """Return a factory for a plugin that registers a QQ command and a capability adapter."""

    def factory() -> Any:
        class _Handler:
            async def handle(self, req: PluginQQCommandRequest) -> PluginQQCommandResult:
                return PluginQQCommandResult(handled=True, reply_text="ok")

        class Plugin:
            manifest = PluginManifest(
                plugin_id=PLUGIN_ID,
                plugin_version="0.1.0",
                plugin_api_version=AKANE_PLUGIN_API_VERSION,
                permissions=permissions,
            )

            def register(self, registrar: Any) -> None:
                registrar.add_capability_adapter(_FakeAdapter())
                registrar.add_qq_command(command, _Handler())

        return Plugin()

    return factory


def _make_plugin_without_qq_command(
    permissions: tuple[str, ...] = (
        CAPABILITY_PROMPT_INVOKE_PERMISSION,
        NETWORK_READ_PERMISSION,
    ),
) -> Callable[[], Any]:
    """Return a factory for a plugin that tries add_qq_command without permission."""

    def factory() -> Any:
        class _Handler:
            async def handle(self, req: PluginQQCommandRequest) -> PluginQQCommandResult:
                return PluginQQCommandResult(handled=True)  # pragma: no cover

        class Plugin:
            manifest = PluginManifest(
                plugin_id=PLUGIN_ID,
                plugin_version="0.1.0",
                plugin_api_version=AKANE_PLUGIN_API_VERSION,
                permissions=permissions,
            )

            def register(self, registrar: Any) -> None:
                # No capability adapter; tries qq command without permission first
                registrar.add_qq_command("/balance", _Handler())

        return Plugin()

    return factory


class PluginHostQQCommandIntegrationTests(unittest.IsolatedAsyncioTestCase):
    # 7. Plugin with qq.command.register permission → commands visible in broker
    async def test_plugin_with_permission_commands_visible_in_broker(self) -> None:
        factory = _make_plugin_with_qq_command(command="/balance")
        host = PluginHost(
            (PluginSelection(plugin_id=PLUGIN_ID, enabled=True),),
            contribution_policy=TrustedStatefulPluginContributionPolicy(),
            entry_points_provider=_entry_points(factory),
        )
        status = await host.start()

        broker = host.build_qq_command_broker()
        await host.stop()

        self.assertEqual(status["status"], "active")
        self.assertTrue(broker.handles("/balance"))
        self.assertIn("/balance", broker.registered_commands)
        unavailable = await broker.dispatch(
            command="/balance",
            args="",
            qq_number=100,
            group_id=0,
            is_group=False,
        )
        self.assertTrue(unavailable.handled)
        self.assertEqual(unavailable.reason, "host_unavailable")
        self.assertEqual(unavailable.reply_text, COMMAND_FAILURE_REPLY)

    # 8. Plugin without qq.command.register tries add_qq_command() → activation fails
    async def test_plugin_without_permission_add_qq_command_fails_activation(self) -> None:
        factory = _make_plugin_without_qq_command(
            permissions=(
                CAPABILITY_PROMPT_INVOKE_PERMISSION,
                NETWORK_READ_PERMISSION,
            )
        )
        host = PluginHost(
            (PluginSelection(plugin_id=PLUGIN_ID, enabled=True),),
            contribution_policy=TrustedReadNetworkContributionPolicy(),
            entry_points_provider=_entry_points(factory),
        )
        status = await host.start()
        await host.stop()

        plugin_info = status["plugins"][0]
        self.assertNotEqual(plugin_info["status"], "active")

    async def test_plugin_command_without_slash_fails_activation(self) -> None:
        host = PluginHost(
            (PluginSelection(plugin_id=PLUGIN_ID, enabled=True),),
            contribution_policy=TrustedStatefulPluginContributionPolicy(),
            entry_points_provider=_entry_points(_make_plugin_with_qq_command(command="balance")),
        )

        status = await host.start()
        await host.stop()

        self.assertEqual(status["plugins"][0]["status"], "failed")
        self.assertEqual(status["plugins"][0]["reason"], "plugin_registration_failed")

    async def test_duplicate_command_across_plugins_fails_second_activation(self) -> None:
        second_plugin_id = "akane.test.qq.commands.second"

        def make_factory(plugin_id: str) -> Callable[[], Any]:
            capability_id = f"{plugin_id}.query.v1"

            class Adapter:
                provider_id = f"provider.{plugin_id}"

                async def health(self) -> HealthStatus:
                    return HealthStatus(ok=True, status="ok")

                async def list_capabilities(self) -> tuple[CapabilityDescriptor, ...]:
                    return (
                        CapabilityDescriptor(
                            id=capability_id,
                            display_name="QQ command conflict test",
                            short_hint="Test command conflict handling.",
                            visible_in=("qq",),
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

                async def invoke(self, *args: Any, **kwargs: Any) -> CapabilityResult:
                    return CapabilityResult(is_error=False, status="ok", content={})

                async def aclose(self) -> None:
                    pass

            def factory() -> Any:
                class Plugin:
                    manifest = PluginManifest(
                        plugin_id=plugin_id,
                        plugin_version="0.1.0",
                        plugin_api_version=AKANE_PLUGIN_API_VERSION,
                        permissions=(
                            CAPABILITY_PROMPT_INVOKE_PERMISSION,
                            NETWORK_READ_PERMISSION,
                            PLUGIN_QQ_COMMAND_PERMISSION,
                        ),
                    )

                    def register(self, registrar: Any) -> None:
                        registrar.add_capability_adapter(Adapter())
                        registrar.add_qq_command(
                            "/balance",
                            _SimpleHandler(PluginQQCommandResult(handled=True, reply_text="ok")),
                        )

                return Plugin()

            return factory

        host = PluginHost(
            (
                PluginSelection(plugin_id=PLUGIN_ID, enabled=True),
                PluginSelection(plugin_id=second_plugin_id, enabled=True),
            ),
            contribution_policy=TrustedStatefulPluginContributionPolicy(),
            entry_points_provider=lambda: (
                FakeEntryPoint(PLUGIN_ID, make_factory(PLUGIN_ID)),
                FakeEntryPoint(second_plugin_id, make_factory(second_plugin_id)),
            ),
        )

        status = await host.start()
        self.addAsyncCleanup(host.stop)

        self.assertEqual(status["status"], "degraded")
        self.assertEqual(status["plugins"][0]["status"], "active")
        self.assertEqual(status["plugins"][1]["reason"], "qq_command_conflict")
        self.assertEqual(host.build_qq_command_broker().registered_commands, ("/balance",))

    # 9. build_qq_command_broker() empty when no plugins have commands
    async def test_build_qq_command_broker_empty_when_no_plugins(self) -> None:
        host = PluginHost(
            (),
            contribution_policy=TrustedStatefulPluginContributionPolicy(),
        )
        await host.start()
        broker = host.build_qq_command_broker()
        await host.stop()

        self.assertEqual(broker.registered_commands, ())
        self.assertFalse(broker.handles("/anything"))

    async def test_build_qq_command_broker_empty_when_plugin_has_no_commands(self) -> None:
        """A plugin that only registers a capability (no QQ commands) yields an empty broker."""

        def factory() -> Any:
            class Plugin:
                manifest = PluginManifest(
                    plugin_id=PLUGIN_ID,
                    plugin_version="0.1.0",
                    plugin_api_version=AKANE_PLUGIN_API_VERSION,
                    permissions=(
                        CAPABILITY_PROMPT_INVOKE_PERMISSION,
                        NETWORK_READ_PERMISSION,
                    ),
                )

                def register(self, registrar: Any) -> None:
                    registrar.add_capability_adapter(_FakeAdapter())

            return Plugin()

        host = PluginHost(
            (PluginSelection(plugin_id=PLUGIN_ID, enabled=True),),
            contribution_policy=TrustedReadNetworkContributionPolicy(),
            entry_points_provider=_entry_points(factory),
        )
        status = await host.start()
        broker = host.build_qq_command_broker()
        await host.stop()

        self.assertEqual(status["status"], "active")
        self.assertEqual(broker.registered_commands, ())


# ---------------------------------------------------------------------------
# 10. TrustedStatefulPluginContributionPolicy accepts qq.command.register
# ---------------------------------------------------------------------------


class TrustedStatefulPolicyQQCommandTests(unittest.TestCase):
    def _policy(self) -> TrustedStatefulPluginContributionPolicy:
        return TrustedStatefulPluginContributionPolicy()

    def _manifest(self, permissions: tuple[str, ...]) -> PluginManifest:
        return PluginManifest(
            plugin_id=PLUGIN_ID,
            plugin_version="0.1.0",
            plugin_api_version=AKANE_PLUGIN_API_VERSION,
            permissions=permissions,
        )

    def test_accepts_base_plus_qq_command_permission(self) -> None:
        policy = self._policy()
        manifest = self._manifest((
            CAPABILITY_PROMPT_INVOKE_PERMISSION,
            NETWORK_READ_PERMISSION,
            PLUGIN_QQ_COMMAND_PERMISSION,
        ))
        self.assertTrue(policy.validate_manifest(manifest).accepted)

    def test_accepts_base_permissions_without_qq_command(self) -> None:
        """qq.command.register is optional; base-only is also accepted."""
        policy = self._policy()
        manifest = self._manifest((
            CAPABILITY_PROMPT_INVOKE_PERMISSION,
            NETWORK_READ_PERMISSION,
        ))
        self.assertTrue(policy.validate_manifest(manifest).accepted)

    def test_accepts_all_optional_permissions_including_qq(self) -> None:
        from companion_v01.plugin_api import (
            BACKGROUND_JOB_PERMISSION,
            MANAGED_ARTIFACT_WRITE_PERMISSION,
            NOTIFICATION_SEND_PERMISSION,
            PLUGIN_STORAGE_WRITE_PERMISSION,
        )
        policy = self._policy()
        manifest = self._manifest((
            CAPABILITY_PROMPT_INVOKE_PERMISSION,
            NETWORK_READ_PERMISSION,
            PLUGIN_STORAGE_WRITE_PERMISSION,
            BACKGROUND_JOB_PERMISSION,
            NOTIFICATION_SEND_PERMISSION,
            MANAGED_ARTIFACT_WRITE_PERMISSION,
            PLUGIN_QQ_COMMAND_PERMISSION,
        ))
        self.assertTrue(policy.validate_manifest(manifest).accepted)

    def test_rejects_qq_command_without_base_permissions(self) -> None:
        policy = self._policy()
        manifest = self._manifest((PLUGIN_QQ_COMMAND_PERMISSION,))
        self.assertFalse(policy.validate_manifest(manifest).accepted)

    def test_rejects_unknown_permission(self) -> None:
        policy = self._policy()
        manifest = self._manifest((
            CAPABILITY_PROMPT_INVOKE_PERMISSION,
            NETWORK_READ_PERMISSION,
            "unknown.custom.permission",
        ))
        self.assertFalse(policy.validate_manifest(manifest).accepted)


if __name__ == "__main__":
    unittest.main()
