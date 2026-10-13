"""Failures before handler entry must release admission and resource scopes."""
from __future__ import annotations

import asyncio
import unittest

from akane_plugin import Plugin
from akane_plugin.events import Event
from capcore import CapabilityResult, InvocationContext
from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_contribution_policy import TrustedStatefulPluginContributionPolicy
from companion_v01.plugin_host import PluginHost
from companion_v01.plugin_resources import current_resource_invocation
from tests.test_plugin_host import CAPABILITY_ID, PLUGIN_ID, FakeAdapter, FakeEntryPoint, FakePlugin, _entry_point_for, _host


class ScopeProvider:
    def __init__(self):
        self.fail = True
        self.invocations = []

    def initialize_scope(self, invocation):
        self.invocations.append(invocation)
        if self.fail:
            raise RuntimeError("private initializer details")

    async def request(self, operation, payload, *, invocation):
        return {}


class PluginInvocationBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_scope_initialization_releases_capability_and_allows_next_call(self):
        provider, adapter = ScopeProvider(), FakeAdapter()
        host = _host((PluginSelection(PLUGIN_ID, True),),
                     entry_points_provider=lambda: [_entry_point_for(FakePlugin((adapter,)))],
                     invoke_timeout_seconds=0.1, managed_artifact_timeout_seconds=0.1)
        host.bind_events_provider(provider)
        self.addAsyncCleanup(host.stop)
        await host.start()
        failed = await host.invoke(CAPABILITY_ID, {}, context=InvocationContext())
        self.assertTrue(failed.is_error)
        self.assertEqual(failed.reason, "plugin_invoke_failed")
        self.assertFalse(provider.invocations[-1].active)
        self.assertEqual((adapter.invoke_count, host._inflight_count), (0, 0))
        self.assertIsNone(current_resource_invocation.get())
        provider.fail = False
        completed = await host.invoke(CAPABILITY_ID, {}, context=InvocationContext())
        self.assertFalse(completed.is_error)
        self.assertEqual((adapter.invoke_count, host._inflight_count), (1, 0))
        self.assertEqual((await asyncio.wait_for(host.stop(), timeout=1))["status"], "stopped")

    async def event_host(self):
        provider = ScopeProvider()
        plugin = Plugin("example.scope", permissions=("event.subscribe",))
        handled = []

        @plugin.on("example.failure", sources=("example.publisher",))
        async def receive(event, ctx):
            handled.append(event.event_id)
            return {"received": event.event_id}

        host = PluginHost((PluginSelection(plugin.manifest.plugin_id, True),),
                          contribution_policy=TrustedStatefulPluginContributionPolicy(),
                          entry_points_provider=lambda: [FakeEntryPoint(plugin.manifest.plugin_id, lambda: plugin)],
                          invoke_timeout_seconds=0.1, managed_artifact_timeout_seconds=0.1)
        host.bind_events_provider(provider)
        self.addAsyncCleanup(host.stop)
        self.assertEqual((await host.start())["status"], "active")
        return host, provider, handled, host._event_registrations_snapshot()[0]

    async def test_failed_event_initialization_closes_scope_even_when_handoff_was_requested(self):
        host, provider, handled, registration = await self.event_host()
        invocation_out = [None]
        result = await host._invoke_event_registration(
            registration, Event("event-1", "example.failure", "example.publisher", {}),
            context=InvocationContext("owner", "session", "web"), scope_id="scope", delivery_id="delivery",
            invocation_out=invocation_out, keep_scope_open=True,
        )
        self.assertTrue(result.is_error)
        self.assertEqual(result.reason, "event_handler_exception")
        self.assertEqual(handled, [])
        self.assertIsNone(invocation_out[0])
        self.assertFalse(provider.invocations[-1].active)
        self.assertEqual(host._inflight_count, 0)
        self.assertIsNone(current_resource_invocation.get())
        self.assertEqual((await asyncio.wait_for(host.stop(), timeout=1))["status"], "stopped")

    async def test_successful_event_handoff_keeps_scope_alive_until_broker_closes_it(self):
        host, provider, handled, registration = await self.event_host()
        provider.fail = False
        invocation_out = [None]
        result = await host._invoke_event_registration(
            registration, Event("event-2", "example.failure", "example.publisher", {}),
            context=InvocationContext("owner", "session", "web"), scope_id="scope", delivery_id="delivery",
            invocation_out=invocation_out, keep_scope_open=True,
        )
        self.assertFalse(result.is_error)
        self.assertEqual(handled, ["event-2"])
        self.assertTrue(invocation_out[0].active)
        self.assertEqual(host._inflight_count, 0)
        self.assertIsNone(current_resource_invocation.get())
        await invocation_out[0].aclose()
        self.assertFalse(invocation_out[0].active)


class PluginConsumerBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_consumer_thread_runs_adapter_on_the_lifecycle_loop(self):
        runtime_loop = asyncio.get_running_loop()
        adapter_loops = []

        class LoopAdapter(FakeAdapter):
            async def invoke(self, *args):
                adapter_loops.append(asyncio.get_running_loop())
                return await super().invoke(*args)

        adapter = LoopAdapter()
        host = _host((PluginSelection(PLUGIN_ID, True),),
                     entry_points_provider=lambda: [_entry_point_for(FakePlugin((adapter,)))])
        self.addAsyncCleanup(host.stop)
        await host.start()
        result = await asyncio.to_thread(lambda: asyncio.run(host.invoke_from_consumer(
            CAPABILITY_ID, {}, context=InvocationContext("owner", "session", "web"),
        )))
        self.assertFalse(result.is_error)
        self.assertEqual(adapter_loops, [runtime_loop])
        self.assertEqual(host._inflight_count, 0)

    async def test_cross_loop_cancellation_waits_for_real_terminal_result_and_cleanup(self):
        entered, cancelled, release, finished = (asyncio.Event() for _ in range(4))
        consumer = {}
        provider = ScopeProvider()
        provider.fail = False

        class TerminalAdapter(FakeAdapter):
            async def invoke(self, *args):
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    await release.wait()
                    return CapabilityResult(is_error=False, content={"terminal": "completed"})
                finally:
                    finished.set()

        adapter = TerminalAdapter()
        host = _host((PluginSelection(PLUGIN_ID, True),),
                     entry_points_provider=lambda: [_entry_point_for(FakePlugin((adapter,)))])
        host.bind_events_provider(provider)
        self.addAsyncCleanup(host.stop)
        self.addCleanup(release.set)
        await host.start()

        async def consume():
            consumer["loop"] = asyncio.get_running_loop()
            consumer["task"] = asyncio.create_task(host.invoke_from_consumer(
                CAPABILITY_ID, {}, context=InvocationContext("owner", "session", "web"),
            ))
            return await consumer["task"]

        waiting = asyncio.create_task(asyncio.to_thread(lambda: asyncio.run(consume())))
        await asyncio.wait_for(entered.wait(), timeout=2)
        consumer["loop"].call_soon_threadsafe(consumer["task"].cancel)
        await asyncio.wait_for(cancelled.wait(), timeout=2)
        self.assertFalse(waiting.done())
        self.assertEqual(host._inflight_count, 1)
        release.set()
        result = await asyncio.wait_for(waiting, timeout=2)
        self.assertEqual(result.content, {"terminal": "completed"})
        self.assertTrue(finished.is_set())
        self.assertFalse(provider.invocations[-1].active)
        self.assertEqual(host._inflight_count, 0)
