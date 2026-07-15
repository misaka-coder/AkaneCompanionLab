"""Tests for PluginHost background job port: _HostJobController + PluginHost integration."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any, Callable

from capcore import CapabilityDescriptor, CapabilityResult, HealthStatus, InvocationContext

from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_api import (
    AKANE_PLUGIN_API_VERSION,
    BACKGROUND_JOB_PERMISSION,
    CAPABILITY_PROMPT_INVOKE_PERMISSION,
    NETWORK_READ_PERMISSION,
    PluginManifest,
)
from companion_v01.plugin_contribution_policy import (
    TrustedReadNetworkContributionPolicy,
    TrustedStatefulPluginContributionPolicy,
)
from companion_v01.plugin_host import PluginHost
from companion_v01.plugin_jobs import _HostJobController


PLUGIN_ID = "akane.test.jobs"
CAPABILITY_ID = f"{PLUGIN_ID}.query.v1"


# ---------------------------------------------------------------------------
# Shared fake infrastructure (mirrors test_plugin_storage.py pattern)
# ---------------------------------------------------------------------------

class FakeDistribution:
    def __init__(self, *, version: str = "0.1.0") -> None:
        self.version = version
        self.metadata = {"Name": "akane-test-jobs-plugin"}

    def read_text(self, filename: str) -> str | None:
        return None


class FakeEntryPoint:
    def __init__(self, name: str, factory: Callable[..., Any], *, version: str = "0.1.0") -> None:
        self.name = name
        self.dist = FakeDistribution(version=version)
        self._factory = factory

    def load(self) -> Callable[..., Any]:
        return self._factory


def _descriptor() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        id=CAPABILITY_ID,
        display_name="Jobs query",
        short_hint="Query job-owned data.",
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


class FakeAdapter:
    provider_id = "provider.test.jobs"

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


def _plugin_entry_points(factory: Callable) -> Callable[[], tuple[FakeEntryPoint, ...]]:
    return lambda: (FakeEntryPoint(PLUGIN_ID, factory),)


# ---------------------------------------------------------------------------
# Concrete job implementations used across tests
# ---------------------------------------------------------------------------

class _CooperativeJob:
    """A well-behaved job that exits cleanly when the controller signals shutdown."""

    def __init__(self) -> None:
        self.started = False

    async def start(self, controller: Any) -> None:
        self.started = True
        await controller.wait_for_shutdown()

    async def stop(self) -> None:
        pass


class _HangingJob:
    """A misbehaving job that ignores the shutdown signal and never exits voluntarily."""

    def __init__(self) -> None:
        self.stop_called = False

    async def start(self, controller: Any) -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            raise  # re-raise so the task is properly cancelled

    async def stop(self) -> None:
        self.stop_called = True


class _SlowCancellationJob:
    """Takes longer than the host's final cancellation grace period to exit."""

    async def start(self, controller: Any) -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await asyncio.sleep(0.3)

    async def stop(self) -> None:
        pass


class _CrashingJob:
    async def start(self, controller: Any) -> None:
        raise RuntimeError("private plugin details must not leak")

    async def stop(self) -> None:
        pass


class _EarlyExitJob:
    async def start(self, controller: Any) -> None:
        return None

    async def stop(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Plugin factories
# ---------------------------------------------------------------------------

def _make_job_plugin(
    job: Any,
    permissions: tuple[str, ...] = (
        CAPABILITY_PROMPT_INVOKE_PERMISSION,
        NETWORK_READ_PERMISSION,
        BACKGROUND_JOB_PERMISSION,
    ),
) -> Callable:
    """Return a zero-parameter factory for a plugin that registers one background job."""
    def factory():
        class Plugin:
            manifest = PluginManifest(
                plugin_id=PLUGIN_ID,
                plugin_version="0.1.0",
                plugin_api_version=AKANE_PLUGIN_API_VERSION,
                permissions=permissions,
            )

            def register(self, registrar: Any) -> None:
                registrar.add_background_job(job)
                registrar.add_capability_adapter(FakeAdapter())

        return Plugin()

    return factory


def _make_double_job_plugin(job: Any) -> Callable:
    """Return a factory for a plugin that calls add_background_job() twice (duplicate)."""
    def factory():
        class Plugin:
            manifest = PluginManifest(
                plugin_id=PLUGIN_ID,
                plugin_version="0.1.0",
                plugin_api_version=AKANE_PLUGIN_API_VERSION,
                permissions=(
                    CAPABILITY_PROMPT_INVOKE_PERMISSION,
                    NETWORK_READ_PERMISSION,
                    BACKGROUND_JOB_PERMISSION,
                ),
            )

            def register(self, registrar: Any) -> None:
                registrar.add_background_job(job)
                registrar.add_background_job(job)  # second call -> RuntimeError duplicate_plugin_job
                registrar.add_capability_adapter(FakeAdapter())

        return Plugin()

    return factory


# ---------------------------------------------------------------------------
# _HostJobController unit tests
# ---------------------------------------------------------------------------

class HostJobControllerTests(unittest.IsolatedAsyncioTestCase):
    """Unit tests for _HostJobController in isolation."""

    async def test_shutdown_requested_false_before_signal(self) -> None:
        controller = _HostJobController()
        controller._arm()
        self.assertFalse(controller.shutdown_requested)

    async def test_shutdown_signal_sets_requested(self) -> None:
        controller = _HostJobController()
        controller._arm()
        controller.signal_shutdown()
        self.assertTrue(controller.shutdown_requested)

    async def test_wait_for_shutdown_returns_true_after_signal(self) -> None:
        """wait_for_shutdown returns True when shutdown is already signalled."""
        controller = _HostJobController()
        controller._arm()
        controller.signal_shutdown()
        result = await controller.wait_for_shutdown(timeout=1.0)
        self.assertTrue(result)

    async def test_wait_for_shutdown_returns_false_on_timeout(self) -> None:
        """wait_for_shutdown returns False when timeout elapses without a signal."""
        controller = _HostJobController()
        controller._arm()
        result = await controller.wait_for_shutdown(timeout=0.01)
        self.assertFalse(result)

    async def test_signal_shutdown_is_idempotent(self) -> None:
        """signal_shutdown may be called multiple times without raising."""
        controller = _HostJobController()
        controller._arm()
        controller.signal_shutdown()
        controller.signal_shutdown()
        self.assertTrue(controller.shutdown_requested)

    async def test_wait_for_shutdown_without_arm_returns_false(self) -> None:
        """wait_for_shutdown returns False when the controller has never been armed."""
        controller = _HostJobController()
        result = await controller.wait_for_shutdown(timeout=0.01)
        self.assertFalse(result)

    async def test_wait_for_shutdown_resolves_after_concurrent_signal(self) -> None:
        """wait_for_shutdown unblocks when signal_shutdown is called concurrently."""
        controller = _HostJobController()
        controller._arm()

        async def _signal_after_delay() -> None:
            await asyncio.sleep(0.02)
            controller.signal_shutdown()

        task = asyncio.ensure_future(_signal_after_delay())
        result = await controller.wait_for_shutdown(timeout=1.0)
        await task
        self.assertTrue(result)


# ---------------------------------------------------------------------------
# PluginHost integration tests for background job port
# ---------------------------------------------------------------------------

class PluginHostJobIntegrationTests(unittest.IsolatedAsyncioTestCase):

    async def test_plugin_with_job_run_starts_and_stops_cleanly(self) -> None:
        """A plugin with job.run permission has its job started and stopped cleanly."""
        job = _CooperativeJob()
        factory = _make_job_plugin(job)
        host = PluginHost(
            (PluginSelection(plugin_id=PLUGIN_ID, enabled=True),),
            contribution_policy=TrustedStatefulPluginContributionPolicy(),
            entry_points_provider=_plugin_entry_points(factory),
        )

        status = await host.start()

        self.assertEqual(status["status"], "active")
        self.assertEqual(status["job_count"], 1)
        self.assertEqual(status["running_job_count"], 1)

        # Yield so the job task can actually begin executing start().
        await asyncio.sleep(0)
        self.assertTrue(job.started)

        await host.stop()

        self.assertEqual(host.state, "stopped")

    async def test_crashed_job_degrades_host_with_safe_diagnostics(self) -> None:
        host = PluginHost(
            (PluginSelection(plugin_id=PLUGIN_ID, enabled=True),),
            contribution_policy=TrustedStatefulPluginContributionPolicy(),
            entry_points_provider=_plugin_entry_points(_make_job_plugin(_CrashingJob())),
        )

        status = await host.start()

        self.assertEqual(status["status"], "degraded")
        self.assertEqual(status["reason"], "plugin_runtime_failed")
        self.assertEqual(status["running_job_count"], 0)
        self.assertEqual(status["jobs"][0]["status"], "failed")
        self.assertEqual(status["jobs"][0]["reason"], "job_failed")
        self.assertNotIn("private plugin details", repr(status))
        await host.stop()

    async def test_unexpected_clean_job_exit_degrades_host(self) -> None:
        host = PluginHost(
            (PluginSelection(plugin_id=PLUGIN_ID, enabled=True),),
            contribution_policy=TrustedStatefulPluginContributionPolicy(),
            entry_points_provider=_plugin_entry_points(_make_job_plugin(_EarlyExitJob())),
        )

        status = await host.start()

        self.assertEqual(status["status"], "degraded")
        self.assertEqual(status["jobs"][0]["reason"], "job_exited")
        await host.stop()

    async def test_plugin_without_job_run_add_background_job_fails_activation(self) -> None:
        """Plugin without job.run that calls add_background_job() fails with plugin_registration_failed."""
        job = _CooperativeJob()
        # Permissions deliberately omit BACKGROUND_JOB_PERMISSION
        factory = _make_job_plugin(
            job,
            permissions=(
                CAPABILITY_PROMPT_INVOKE_PERMISSION,
                NETWORK_READ_PERMISSION,
            ),
        )
        host = PluginHost(
            (PluginSelection(plugin_id=PLUGIN_ID, enabled=True),),
            contribution_policy=TrustedReadNetworkContributionPolicy(),
            entry_points_provider=_plugin_entry_points(factory),
        )

        status = await host.start()
        await host.stop()

        plugin_info = status["plugins"][0]
        self.assertEqual(plugin_info["status"], "failed")
        self.assertEqual(plugin_info["reason"], "plugin_registration_failed")

    async def test_duplicate_plugin_job_fails_activation(self) -> None:
        """Registering the same job twice raises duplicate_plugin_job -> plugin_registration_failed."""
        job = _CooperativeJob()
        factory = _make_double_job_plugin(job)
        host = PluginHost(
            (PluginSelection(plugin_id=PLUGIN_ID, enabled=True),),
            contribution_policy=TrustedStatefulPluginContributionPolicy(),
            entry_points_provider=_plugin_entry_points(factory),
        )

        status = await host.start()
        await host.stop()

        plugin_info = status["plugins"][0]
        self.assertEqual(plugin_info["status"], "failed")
        self.assertEqual(plugin_info["reason"], "plugin_registration_failed")

    async def test_job_task_cancelled_gracefully_when_stop_times_out(self) -> None:
        """A job that ignores shutdown is force-cancelled after the bounded stop timeout."""
        job = _HangingJob()
        factory = _make_job_plugin(job)
        host = PluginHost(
            (PluginSelection(plugin_id=PLUGIN_ID, enabled=True),),
            contribution_policy=TrustedStatefulPluginContributionPolicy(),
            entry_points_provider=_plugin_entry_points(factory),
        )
        # Use a very short stop timeout so the test does not take seconds.
        host._job_stop_timeout_seconds = 0.05

        status = await host.start()
        self.assertEqual(status["status"], "active")

        # Yield so the job task starts executing inside asyncio.sleep(3600).
        await asyncio.sleep(0)

        # stop() must complete even though the job never voluntarily exits.
        await host.stop()

        self.assertEqual(host.state, "stopped")
        # The host issued a secondary stop signal.
        self.assertTrue(job.stop_called)

    async def test_shutdown_remains_bounded_when_job_delays_cancellation(self) -> None:
        host = PluginHost(
            (PluginSelection(plugin_id=PLUGIN_ID, enabled=True),),
            contribution_policy=TrustedStatefulPluginContributionPolicy(),
            entry_points_provider=_plugin_entry_points(_make_job_plugin(_SlowCancellationJob())),
            close_timeout_seconds=0.01,
        )
        host._job_stop_timeout_seconds = 0.01
        await host.start()

        started_at = asyncio.get_running_loop().time()
        stopped = await host.stop()
        elapsed = asyncio.get_running_loop().time() - started_at

        self.assertLess(elapsed, 0.25)
        self.assertEqual(stopped["status"], "stopped")
        self.assertGreaterEqual(stopped["job_stop_failure_count"], 1)
        await asyncio.sleep(0.32)

    async def test_bind_notification_port_after_start_raises(self) -> None:
        """bind_notification_port must be called before start; calling it after raises RuntimeError."""
        host = PluginHost(
            (),
            contribution_policy=TrustedStatefulPluginContributionPolicy(),
        )
        await host.start()

        class FakePort:
            async def send(self, intent: Any) -> Any:
                return None

        with self.assertRaises(RuntimeError):
            host.bind_notification_port(FakePort())

        await host.stop()

    def test_bind_notification_port_with_invalid_object_raises_type_error(self) -> None:
        """bind_notification_port rejects any object that lacks a callable send attribute."""
        host = PluginHost(
            (),
            contribution_policy=TrustedStatefulPluginContributionPolicy(),
        )
        with self.assertRaises(TypeError):
            host.bind_notification_port(object())  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
