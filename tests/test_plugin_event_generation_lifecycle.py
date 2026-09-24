"""Event cancellation across actual workers and parent callback authority."""

from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import textwrap
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from capcore import InvocationContext
from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_active_generation import ActivePluginGeneration, PluginGenerationSnapshot
from companion_v01.plugin_capability_calls import EnginePluginCapabilityProvider
from companion_v01.plugin_generation import PluginGenerationProcess
from companion_v01.plugin_generation_callbacks import GenerationHostCallbackRouter
from companion_v01.plugin_generation_protocol import PLUGIN_GENERATION_PROTOCOL
from companion_v01.plugin_tool_bridge import PluginCapabilityToolBridge
from tests.test_plugin_engine_bridge import EngineFacade


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = "test.event-lifecycle"


class EventGenerationLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_disable_drains_running_worker_revokes_parent_and_cancels_queue(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = root / "site"
            package = site / "event_lifecycle"
            metadata = site / "event_lifecycle-0.1.0.dist-info"
            package.mkdir(parents=True)
            metadata.mkdir()
            (package / "__init__.py").write_text(textwrap.dedent('''
                import asyncio
                from dataclasses import replace
                from pathlib import Path
                from typing import Any
                from akane_plugin import EventBinding, EventReceipt, Plugin, ToolContext
                from companion_v01.plugin_resources import current_resource_invocation
                plugin = Plugin("test.event-lifecycle", permissions=("event.emit", "capability.invoke"))
                @plugin.tool
                async def enable(ctx: ToolContext) -> EventBinding:
                    return await ctx.events.bind("test.event-lifecycle.bound")
                @plugin.tool
                async def forward(data: dict[str, Any], ctx: ToolContext) -> EventReceipt:
                    assert not ctx.invocation.profile_user_id and not ctx.invocation.session_id
                    return await ctx.events.emit("test.forwarded", data)
                @plugin.on("test.relay")
                async def relay(event, ctx):
                    return await ctx.tools.call("test.event-lifecycle.forward", {"data": event.data})
                @plugin.on("test.forwarded", name="bound", scope="conversation")
                async def bound(event, ctx): return event.data
                @plugin.on("test.wait")
                async def wait(event, ctx):
                    if event.data.get("quick"): return event.data
                    Path(event.data["marker"]).write_text("ready")
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        asyncio.current_task().uncancel()
                        local = await asyncio.create_task(ctx.events.emit("test.wait", {}))
                        call = await asyncio.create_task(ctx.tools.call_result("test.peer.run", {}))
                        # Deliberately bypass the local port to verify the
                        # parent still rejects this revoked opaque request ID.
                        forged = replace(current_resource_invocation.get(), active=True)
                        wire = await ctx.events._port._provider.request("emit", {
                            "event_type": "test.wait", "data": {}, "source": "@host",
                        }, invocation=forged)
                        return {"local": local.reason, "wire": wire["reason"], "call": call.reason}
                def create_plugin(): return plugin
            '''), encoding="utf-8")
            (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: event-lifecycle\nVersion: 0.1.0\n", encoding="utf-8")
            (metadata / "entry_points.txt").write_text(
                "[akane.plugins.v1]\ntest.event-lifecycle = event_lifecycle:create_plugin\n", encoding="utf-8")
            active = ActivePluginGeneration()
            broker = active.build_event_broker()
            provider = SimpleNamespace(invoke=AsyncMock(side_effect=AssertionError("revoked followup executed")))
            processes = []

            async def enable(number):
                process = PluginGenerationProcess(project_root=ROOT, site_dir=site, plugin_id=PLUGIN,
                                                  work_dir=root / f"worker-{number}")
                processes.append(process)
                process.bind_events_provider(broker)
                process.bind_capability_provider(provider)
                await asyncio.to_thread(process.start)
                await active.publish(PluginGenerationSnapshot((PluginSelection(PLUGIN, True),), (process,)))
                return process

            try:
                process = await enable(1)
                self.assertTrue(broker.observes("test.wait"))
                self.assertIn("test.wait", broker.registered_event_types)
                first = await broker.emit("test.wait", {"marker": str(root / "ready")})
                async with asyncio.timeout(10):
                    while not (root / "ready").exists():
                        receipt = await broker.receipt(first.dispatch_id)
                        self.assertFalse(receipt.complete, receipt)
                        await asyncio.sleep(0.01)
                queued = await broker.emit("test.wait", {"marker": str(root / "must-not-run")})
                await asyncio.wait_for(active.publish(PluginGenerationSnapshot((), ())), 10)
                done = await broker.receipt(first.dispatch_id)
                self.assertTrue(done.complete, done)
                self.assertTrue(done.deliveries[0].cancel_requested)
                self.assertEqual(done.deliveries[0].value, {
                    "local": "event_invocation_required", "wire": "event_invocation_expired",
                    "call": "capability_invocation_required",
                })
                self.assertEqual((await broker.receipt(queued.dispatch_id)).status, "cancelled")
                self.assertFalse((root / "must-not-run").exists())
                self.assertFalse(process.running)
                self.assertEqual(process._callback_router._invocations, {})
                provider.invoke.assert_not_called()
                await enable(2)
                resumed = await broker.emit("test.wait", {"quick": True})
                async with asyncio.timeout(5):
                    while not (await broker.receipt(resumed.dispatch_id)).complete:
                        await asyncio.sleep(0.01)
                self.assertEqual((await broker.receipt(resumed.dispatch_id)).deliveries[0].value, {"quick": True})
                engine = EngineFacade(PluginCapabilityToolBridge(active, config_base_dir=root))
                provider.invoke = EnginePluginCapabilityProvider(engine).invoke
                bindings = []
                for owner in ("owner", "other"):
                    bound = await active.invoke(PLUGIN + ".enable", {}, context=InvocationContext(owner, "session", "web"))
                    self.assertFalse(bound.is_error, bound)
                    bindings.append(bound.value["scope_id"])

                async def terminal(dispatch_id):
                    async with asyncio.timeout(5):
                        while True:
                            result = await broker.receipt(dispatch_id)
                            if result.complete: return result
                            await asyncio.sleep(0.01)

                origin = InvocationContext("owner", "session", "web")
                routed = await terminal((await broker.emit("test.relay", {"value": 42}, context=origin)).dispatch_id)
                self.assertEqual(routed.status, "completed", routed)
                forwarded = await terminal(routed.deliveries[0].value["dispatch_id"])
                self.assertEqual([item.scope_id for item in forwarded.deliveries], [bindings[0]])
                public = await terminal((await broker.emit("test.relay", {"public": True})).dispatch_id)
                public_forwarded = await terminal(public.deliveries[0].value["dispatch_id"])
                self.assertEqual({item.scope_id for item in public_forwarded.deliveries}, set(bindings))
            finally:
                await active.stop()
                for process in processes:
                    await asyncio.to_thread(process.stop)

    async def test_parent_checks_permission_and_revocation_before_queued_event_callback(self):
        output, scheduled = [], []
        provider = SimpleNamespace(request=AsyncMock())
        router = GenerationHostCallbackRouter(generation_id="generation", start_timeout_seconds=1,
                                              write_response=output.append)
        router.bind_events_provider(provider)
        router._schedule = lambda callback, coroutine, **_: scheduled.append(coroutine)
        context = InvocationContext("owner", "session", "web")
        router.begin_invocation("denied", plugin_id=PLUGIN, context=context, permissions=())
        request = {"protocol": PLUGIN_GENERATION_PROTOCOL, "generation_id": "generation",
                   "callback_id": "1" * 32, "callback": "events.request", "invocation_id": "denied",
                   "operation": "emit", "payload": {"event_type": "test.wait", "data": {},
                                                     "permissions": ["event.emit"], "source": "@host"}}
        router.dispatch(request)
        self.assertEqual(output[-1]["result"]["reason"], "event_permission_required")
        self.assertEqual(scheduled, [])
        router.begin_invocation("allowed", plugin_id=PLUGIN, context=context, permissions=("event.emit",))
        router.dispatch({**request, "callback_id": "2" * 32, "invocation_id": "allowed"})
        self.assertEqual(len(scheduled), 1)
        router.revoke_invocation("allowed")
        await scheduled[0]
        self.assertEqual(output[-1]["result"]["reason"], "event_invocation_expired")
        provider.request.assert_not_called()
        await router.finish_invocation("allowed")
        await router.finish_invocation("denied")
        router.close()


if __name__ == "__main__":
    unittest.main()
