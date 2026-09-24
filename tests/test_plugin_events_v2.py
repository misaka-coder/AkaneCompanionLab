"""Public event declarations through real Host admission, scopes and lifecycle."""

from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import Mock

from capcore import CapabilityResult, InvocationContext
from akane_plugin import EventBinding, EventReceipt, Plugin, PluginInvocationContext, ToolContext
from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_capability_calls import EnginePluginCapabilityProvider
from companion_v01.plugin_contribution_policy import TrustedStatefulPluginContributionPolicy
from companion_v01.plugin_host import PluginHost
from companion_v01.plugin_resources import GeneratedFileResourceProvider
from companion_v01.plugin_tool_bridge import PluginCapabilityToolBridge
from tests.test_plugin_engine_bridge import EngineFacade
from tests.test_plugin_host import FakeEntryPoint
from tests.test_plugin_resources import services, add_attachment


def publisher():
    plugin = Plugin("example.publisher", permissions=("event.emit",))

    @plugin.tool
    async def send(event_type: str, data: Any, ctx: ToolContext) -> dict[str, Any]:
        return (await ctx.events.emit(event_type, data)).as_dict()

    @plugin.tool
    async def send_keyed(event_type: str, data: Any, ctx: ToolContext, event_key: str = "",
                         occurred_at_ms: int | None = None) -> dict[str, Any]:
        return (await ctx.events.emit(event_type, data, event_key=event_key,
                                      occurred_at_ms=occurred_at_ms)).as_dict()

    @plugin.tool
    async def status(dispatch_id: str, ctx: ToolContext) -> dict[str, Any]:
        return (await ctx.events.status(dispatch_id)).as_dict()

    return plugin


class PluginEventV2Tests(unittest.IsolatedAsyncioTestCase):
    async def start(self, *plugins, before_start=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        def entry(plugin):
            def factory():
                return plugin
            return FakeEntryPoint(plugin.manifest.plugin_id, factory)
        self.host = PluginHost(
            tuple(PluginSelection(plugin.manifest.plugin_id, True) for plugin in plugins),
            entry_points_provider=lambda: tuple(entry(plugin) for plugin in plugins),
            contribution_policy=TrustedStatefulPluginContributionPolicy(),
        )
        self.engine = EngineFacade(PluginCapabilityToolBridge(self.host, config_base_dir=self.root))
        self.engine.store, self.attachments, self.files = services(self.root)
        self.engine.llm = Mock(side_effect=AssertionError("events must not invoke a model"))
        self.host.bind_capability_provider(EnginePluginCapabilityProvider(self.engine))
        self.host.bind_resource_provider(GeneratedFileResourceProvider(self.files, work_root=self.root / "copies"))
        self.addAsyncCleanup(self.host.stop)
        if before_start:
            before_start(self.host)
        status = await self.host.start()
        self.assertEqual(self.host.state, "active", status)
        self.broker = self.host.build_event_broker()

    async def invoke(self, name, arguments=None, *, owner="owner", session="session"):
        result = await self.host.invoke(name, arguments or {}, context=InvocationContext(owner, session, "web"))
        self.assertFalse(result.is_error, result)
        return result.value

    async def terminal(self, dispatch_id):
        async with asyncio.timeout(5):
            while True:
                result = await self.broker.receipt(dispatch_id)
                if result.complete:
                    return result
                await asyncio.sleep(0.01)

    async def test_public_emit_global_calculation_and_linked_async_dispatch_without_model(self):
        producer = Plugin("example.producer", permissions=("event.emit",))
        consumer = Plugin("example.consumer", permissions=("capability.invoke", "event.emit"))
        entered, release = asyncio.Event(), asyncio.Event()
        downstream_entered, downstream_release = asyncio.Event(), asyncio.Event()
        observed, contexts = [], []

        @producer.tool
        async def publish(data: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
            return (await ctx.events.emit("example.numbers", data)).as_dict()

        @producer.on("example.sum", sources=("example.consumer",))
        async def accept(event, ctx):
            downstream_entered.set()
            await downstream_release.wait()
            observed.append(event.data)
            return event.data

        @consumer.tool
        async def add(a: int, b: int, ctx: ToolContext) -> int:
            contexts.append((ctx.invocation.profile_user_id, ctx.invocation.session_id, ctx.invocation.global_scope))
            return a + b

        @consumer.on("example.numbers", sources=("example.producer",))
        async def calculate(event, ctx):
            self.assertEqual(event.source, "example.producer")
            self.assertFalse(hasattr(ctx, "invocation"))
            entered.set()
            await release.wait()
            before_budget = await ctx.tools.budget()
            value = await ctx.tools.call("example.consumer.add", {"a": event.data["a"], "b": event.data["b"]})
            after_budget = await ctx.tools.budget()
            self.assertEqual(after_budget["used_dependency_calls"], before_budget["used_dependency_calls"] + 1)
            return await ctx.events.emit("example.sum", {"sum": value, "nested": event.data["nested"]})

        await self.start(producer, consumer)
        data = {"a": 2, "b": 40, "nested": [False, None, {"weight": 0.5}]}
        receipt = await self.invoke("example.producer.publish", {"data": data})
        self.assertEqual(receipt["status"], "accepted")
        self.assertFalse(receipt["complete"])
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.sleep(2.1)  # A real business handler outlives the V1 two-second observer budget.
        self.assertFalse((await self.broker.receipt(receipt["dispatch_id"])).complete)
        release.set()
        try:
            await asyncio.wait_for(downstream_entered.wait(), 3)
        except TimeoutError:
            self.fail(str(await self.broker.receipt(receipt["dispatch_id"])))
        pending = await self.broker.receipt(receipt["dispatch_id"])
        self.assertFalse(pending.complete)
        self.assertEqual(pending.deliveries[0].status, "waiting")
        self.assertTrue(pending.deliveries[0].linked_dispatches)
        downstream_release.set()
        terminal = await self.terminal(receipt["dispatch_id"])
        self.assertEqual(terminal.status, "completed", terminal)
        self.assertEqual(observed, [{"sum": 42, "nested": data["nested"]}])
        self.assertEqual(contexts, [("", "", True)])
        self.engine.llm.assert_not_called()

    async def test_host_signs_event_identity_and_version_and_accepts_any_timestamp(self):
        seen = []
        subscriber = Plugin("example.signed.subscriber")

        @subscriber.on("example.signed")
        async def observe(event, ctx):
            seen.append(event)
            return event.data

        await self.start(publisher(), subscriber)
        receipt = await self.invoke("example.publisher.send_keyed", {
            "event_type": "example.signed", "data": {"hp": 8, "flag": False, "nested": [1, None]},
            "occurred_at_ms": 1_000,
        })
        self.assertEqual(receipt["status"], "accepted", receipt)
        terminal = await self.terminal(receipt["dispatch_id"])
        self.assertEqual(terminal.status, "completed", terminal)
        event, = seen
        # Host-signed identity: the plugin supplied only type/data/occurred_at_ms.
        self.assertTrue(event.event_id)
        self.assertEqual(event.event_type, "example.signed")
        self.assertEqual(event.source, "example.publisher")
        self.assertTrue(event.scope)
        self.assertEqual(event.version, 1)
        self.assertEqual(event.occurred_at_ms, 1_000)
        self.assertGreater(event.received_at_ms, 0)
        self.assertEqual(event.data, {"hp": 8, "flag": False, "nested": [1, None]})

        # A wildly out-of-range reported time is recorded, never rejected.
        receipt = await self.invoke("example.publisher.send_keyed", {
            "event_type": "example.signed", "data": {"hp": 7}, "occurred_at_ms": 1,
        })
        self.assertEqual(receipt["status"], "accepted", receipt)
        await self.terminal(receipt["dispatch_id"])
        self.assertEqual(seen[-1].occurred_at_ms, 1)
        self.assertEqual(seen[-1].version, 2)
        self.engine.llm.assert_not_called()

    async def test_event_version_is_per_scope_and_type_and_never_business_state(self):
        seen = []
        subscriber = Plugin("example.versioned.subscriber")

        @subscriber.on("example.versioned")
        async def observe(event, ctx):
            seen.append(event)
            return event.data

        @subscriber.on("example.other")
        async def observe_other(event, ctx):
            seen.append(event)
            return event.data

        await self.start(publisher(), subscriber)
        for event_type in ("example.versioned", "example.versioned", "example.other", "example.versioned"):
            receipt = await self.invoke("example.publisher.send_keyed", {"event_type": event_type, "data": {}})
            self.assertEqual(receipt["status"], "accepted", receipt)
            await self.terminal(receipt["dispatch_id"])
        versioned = [event.version for event in seen if event.event_type == "example.versioned"]
        other = [event.version for event in seen if event.event_type == "example.other"]
        # Each (scope, event_type) stream counts from 1 independently.
        self.assertEqual(versioned, [1, 2, 3])
        self.assertEqual(other, [1])

    async def test_timeline_append_uses_the_host_port_with_host_owned_identity(self):
        recorded, receipts = [], []
        subscriber = Plugin("example.timeline.subscriber", permissions=("context.observe",))

        @subscriber.tool
        async def enable(ctx: ToolContext) -> dict[str, Any]:
            return (await ctx.events.bind("example.timeline.subscriber.keep")).as_dict()

        @subscriber.on("example.timeline", name="keep", scope="conversation")
        async def keep(event, ctx):
            receipts.append(await ctx.timeline.append(event))
            return event.data

        await self.start(publisher(), subscriber)
        self.broker.bind_timeline_recorder(lambda payload: recorded.append(payload) or {"ok": True, "status": "recorded"})
        self.assertEqual((await self.invoke("example.timeline.subscriber.enable"))["status"], "bound")
        receipt = await self.invoke("example.publisher.send_keyed", {
            "event_type": "example.timeline", "data": {"hp": 8}, "occurred_at_ms": 1_700_000_000_000,
        })
        self.assertEqual(receipt["status"], "accepted", receipt)
        await self.terminal(receipt["dispatch_id"])
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0].status, "recorded")
        self.assertEqual(receipts[0].event_id, receipt["event_id"])
        payload, = recorded
        # Identity and scope are host-owned; the plugin supplied only the event.
        self.assertEqual(payload["source_id"], f"plugin-event:{receipt['event_id']}")
        self.assertEqual(payload["event"]["event_type"], "example.timeline")
        self.assertEqual(payload["event"]["source"], "example.publisher")
        self.assertEqual(payload["event"]["fields"], {"data": {"hp": 8}})
        self.assertEqual(payload["user_id"], "session")
        self.assertEqual(payload["real_user_id"], "owner")
        self.assertEqual(payload["timestamp"], 1_700_000_000)
        self.engine.llm.assert_not_called()

    async def test_timeline_append_reports_real_failures_and_refuses_unbound_scope(self):
        receipts, global_receipts = [], []
        subscriber = Plugin("example.timeline.failure", permissions=("context.observe",))
        global_subscriber = Plugin("example.timeline.global", permissions=("context.observe",))

        @subscriber.tool
        async def enable(ctx: ToolContext) -> dict[str, Any]:
            return (await ctx.events.bind("example.timeline.failure.keep")).as_dict()

        @subscriber.on("example.timeline.fail", name="keep", scope="conversation")
        async def keep(event, ctx):
            receipts.append(await ctx.timeline.append(event))
            return event.data

        @global_subscriber.on("example.timeline.fail")
        async def global_keep(event, ctx):
            global_receipts.append(await ctx.timeline.append(event))
            return event.data

        await self.start(publisher(), subscriber, global_subscriber)
        self.broker.bind_timeline_recorder(lambda payload: {"ok": False, "reason": "memcore_not_enabled"})
        self.assertEqual((await self.invoke("example.timeline.failure.enable"))["status"], "bound")
        receipt = await self.invoke("example.publisher.send_keyed", {
            "event_type": "example.timeline.fail", "data": {"hp": 1},
        })
        await self.terminal(receipt["dispatch_id"])
        self.assertEqual(receipts[0].status, "rejected")
        self.assertEqual(receipts[0].reason, "memcore_not_enabled")
        # A global handler records to the conversation that published the event.
        self.assertEqual(global_receipts[0].status, "rejected")
        self.assertEqual(global_receipts[0].reason, "memcore_not_enabled")
        # With no conversation identity at all, the host refuses instead of
        # guessing a target conversation.
        from_host = await self.broker.emit("example.timeline.fail", {"hp": 2})
        await self.terminal(from_host.dispatch_id)
        self.assertEqual(global_receipts[-1].status, "rejected")
        self.assertEqual(global_receipts[-1].reason, "context_unbound")

    async def test_event_key_deduplicates_within_scope_and_rejects_conflicting_content(self):
        seen = []
        subscriber = Plugin("example.keyed.subscriber")

        @subscriber.on("example.keyed")
        async def observe(event, ctx):
            seen.append(event)
            return event.data

        await self.start(publisher(), subscriber)
        first = await self.invoke("example.publisher.send_keyed", {
            "event_type": "example.keyed", "data": {"turn": 4}, "event_key": "turn:4",
        })
        self.assertEqual(first["status"], "accepted", first)
        await self.terminal(first["dispatch_id"])
        again = await self.invoke("example.publisher.send_keyed", {
            "event_type": "example.keyed", "data": {"turn": 4}, "event_key": "turn:4",
        })
        # The duplicate returns the original dispatch receipt, not a new one.
        self.assertEqual(again["dispatch_id"], first["dispatch_id"])
        self.assertEqual(again["event_id"], first["event_id"])
        conflict = await self.invoke("example.publisher.send_keyed", {
            "event_type": "example.keyed", "data": {"turn": 5}, "event_key": "turn:4",
        })
        self.assertEqual(conflict["status"], "rejected")
        self.assertEqual(conflict["reason"], "event_key_conflict")
        # The same business key in another scope is a different event.
        other = await self.invoke("example.publisher.send_keyed", {
            "event_type": "example.keyed", "data": {"turn": 4}, "event_key": "turn:4",
        }, session="other-session")
        self.assertEqual(other["status"], "accepted", other)
        self.assertNotEqual(other["dispatch_id"], first["dispatch_id"])
        await self.terminal(other["dispatch_id"])
        self.assertEqual([event.data for event in seen], [{"turn": 4}, {"turn": 4}])
        self.engine.llm.assert_not_called()

    async def test_serial_and_latest_are_per_subscription_and_failures_are_individual(self):
        plugin = Plugin("example.stream")
        entered = [asyncio.Event(), asyncio.Event()]
        releases = [asyncio.Event(), asyncio.Event()]
        queued, latest = [], []

        @plugin.on("example.state", name="ordered")
        async def ordered(event, ctx):
            if event.data["number"] == 1:
                entered[0].set()
                await releases[0].wait()
            queued.append(event.data.copy())
            return event.data

        @plugin.on("example.state", name="latest", coalesce="latest")
        async def newest(event, ctx):
            if event.data["number"] == 1:
                entered[1].set()
                await releases[1].wait()
            latest.append(event.data["number"])
            event.data["subscriber_mutation"] = True
            return None

        @plugin.on("example.state", name="failed")
        async def fails(event, ctx):
            raise RuntimeError("implementation-private exception details")

        await self.start(plugin)
        original = {"number": 1, "nested": [True, 1.5, None]}
        first = await self.broker.emit("example.state", original)
        original["number"] = 99
        await asyncio.wait_for(asyncio.gather(*(item.wait() for item in entered)), 2)
        second = await self.broker.emit("example.state", {"number": 2})
        third = await self.broker.emit("example.state", {"number": 3})
        interim = await self.broker.receipt(second.dispatch_id)
        outcomes = {item.subscription_id.rsplit(".", 1)[-1]: item.status for item in interim.deliveries}
        self.assertEqual(outcomes["latest"], "superseded")
        self.assertEqual(outcomes["ordered"], "queued")
        self.assertFalse(interim.complete)
        for release in releases:
            release.set()
        for receipt in (first, second, third):
            terminal = await self.terminal(receipt.dispatch_id)
            self.assertEqual(terminal.status, "partially_failed")
            failure = next(item for item in terminal.deliveries if item.subscription_id.endswith("failed"))
            self.assertEqual(failure.reason, "event_handler_exception")
            self.assertNotIn("implementation-private", str(terminal))
        self.assertEqual([item["number"] for item in queued], [1, 2, 3])
        self.assertFalse(any("subscriber_mutation" in item for item in queued))
        self.assertEqual(latest, [1, 3])
        again = await self.broker.receipt(first.dispatch_id)
        success = next(item for item in again.deliveries if item.subscription_id.endswith("ordered"))
        success.value["number"] = 7
        self.assertEqual(next(item for item in (await self.broker.receipt(first.dispatch_id)).deliveries
                              if item.subscription_id.endswith("ordered")).value["number"], 1)

    async def test_binding_uses_subscriber_conversation_and_payload_cannot_choose_identity(self):
        consumer = Plugin("example.reader", permissions=("resource.read",))
        seen = []

        @consumer.tool
        async def enable(ctx: ToolContext) -> dict[str, Any]:
            return (await ctx.events.bind("example.reader.read")).as_dict()

        @consumer.tool
        async def disable(scope_id: str, ctx: ToolContext) -> dict[str, Any]:
            return (await ctx.events.unbind(scope_id)).as_dict()

        @consumer.on("example.read", name="read", sources=("example.publisher", "@host"), scope="conversation")
        async def read(event, ctx):
            seen.append(ctx.scope_id)
            result = await ctx.resources.open(event.data["target"])
            if not result.ok:
                return CapabilityResult(is_error=True, status="error", reason=result.reason)
            return result.path.read_text()

        await self.start(publisher(), consumer)
        _, attachment = add_attachment(self.root, self.attachments)
        data = {"target": attachment["attachment_handle"], "profile_user_id": "owner", "session_id": "session"}
        before = await self.invoke("example.publisher.send", {"event_type": "example.read", "data": data})
        self.assertEqual(before["status"], "unobserved")
        a = await self.invoke("example.reader.enable")
        b = await self.invoke("example.reader.enable", owner="other")
        self.assertEqual(a["status"], "bound")
        self.assertNotEqual(a["scope_id"], b["scope_id"])
        self.assertEqual((await self.invoke("example.reader.enable"))["scope_id"], a["scope_id"])
        sent_a = await self.invoke("example.publisher.send", {"event_type": "example.read", "data": data})
        done_a = await self.terminal(sent_a["dispatch_id"])
        self.assertEqual([(item.scope_id, item.value) for item in done_a.deliveries], [(a["scope_id"], "original resource")])
        sent_b = await self.invoke("example.publisher.send", {"event_type": "example.read", "data": data}, owner="other")
        done_b = await self.terminal(sent_b["dispatch_id"])
        self.assertEqual(len(done_b.deliveries), 1)
        self.assertEqual(done_b.deliveries[0].scope_id, b["scope_id"])
        self.assertEqual(done_b.deliveries[0].reason, "resource_not_found")
        denied = await self.invoke("example.publisher.status", {"dispatch_id": sent_a["dispatch_id"]}, owner="other")
        self.assertEqual(denied["reason"], "event_dispatch_access_denied")
        from_host = await self.broker.emit("example.read", data)
        self.assertEqual((await self.terminal(from_host.dispatch_id)).status, "partially_failed")
        self.assertEqual((await self.invoke("example.reader.disable", {"scope_id": a["scope_id"]}, owner="other"))["status"], "rejected")
        self.assertEqual((await self.invoke("example.reader.disable", {"scope_id": a["scope_id"]}))["status"], "unbound")
        self.assertEqual((await self.invoke("example.publisher.send", {"event_type": "example.read", "data": data}))["status"], "unobserved")
        self.assertEqual(list((self.root / "copies").iterdir()), [])
        self.assertTrue((await self.host.restart())["ok"])
        self.assertEqual((await self.invoke("example.publisher.send", {"event_type": "example.read", "data": data}, owner="other"))["status"], "unobserved")
        self.assertNotEqual((await self.invoke("example.reader.enable"))["scope_id"], a["scope_id"])

    async def test_event_version_restarts_after_generation_switch_and_is_not_persisted(self):
        seen = []
        subscriber = Plugin("example.reset.subscriber")

        @subscriber.on("example.reset")
        async def observe(event, ctx):
            seen.append(event)
            return event.data

        await self.start(publisher(), subscriber)
        first = await self.invoke("example.publisher.send_keyed", {"event_type": "example.reset", "data": {}})
        await self.terminal(first["dispatch_id"])
        second = await self.invoke("example.publisher.send_keyed", {"event_type": "example.reset", "data": {}})
        await self.terminal(second["dispatch_id"])
        self.assertEqual([event.version for event in seen], [1, 2])
        self.assertTrue((await self.host.restart())["ok"])
        after = await self.invoke("example.publisher.send_keyed", {"event_type": "example.reset", "data": {}})
        self.assertEqual(after["status"], "accepted", after)
        await self.terminal(after["dispatch_id"])
        # A new generation restarts the stream; the version is not durable state.
        self.assertEqual(seen[-1].version, 1)
        self.assertEqual([event.version for event in seen], [1, 2, 1])

    async def test_rejections_source_and_event_key_conflicts_are_observable(self):
        listener = Plugin("example.listener")
        calls = []

        @listener.on("example.value", sources=("example.publisher",))
        async def receive(event, ctx):
            calls.append(event.data)
            return event.data

        await self.start(publisher(), listener)
        self.assertEqual((await self.broker.emit("example.value", {"source": "example.publisher"})).status, "unobserved")
        for value in (False, 0, None, [1, {"nested": 2.5}]):
            sent = await self.invoke("example.publisher.send", {"event_type": "example.value", "data": value})
            delivery = (await self.terminal(sent["dispatch_id"])).deliveries[0]
            self.assertTrue(delivery.has_value)
            self.assertEqual(delivery.value, value)
        args = {"event_type": "example.value", "data": True, "event_key": "external-event"}
        first = await self.invoke("example.publisher.send_keyed", args)
        duplicate = await self.invoke("example.publisher.send_keyed", args)
        self.assertEqual(first["dispatch_id"], duplicate["dispatch_id"])
        conflict = await self.invoke("example.publisher.send_keyed", {**args, "data": 1})
        self.assertEqual(conflict["reason"], "event_key_conflict")
        await self.terminal(first["dispatch_id"])
        self.assertEqual(len(calls), 5)
        invalid = await self.broker.emit("example.value", {"number": float("nan")})
        self.assertEqual(invalid.reason, "plugin_result_non_finite_number")
        cycle = []
        cycle.append(cycle)
        self.assertEqual((await self.broker.emit("example.value", cycle)).reason, "plugin_result_cycle")
        self.assertEqual((await self.broker.receipt("unknown")).reason, "event_dispatch_not_found")

    async def test_stop_revokes_running_and_queued_work(self):
        listener = Plugin("example.stoppable", permissions=("capability.invoke", "event.emit"))
        entered, attempts = asyncio.Event(), []
        executed = []

        @listener.tool
        async def after() -> int:
            executed.append("unexpected followup")
            return 1

        @listener.on("example.stop")
        async def handle(event, ctx):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                asyncio.current_task().uncancel()
                result = await asyncio.create_task(ctx.tools.call_result("example.stoppable.after", {}))
                emission = await asyncio.create_task(ctx.events.emit("example.stop", "must not revive"))
                attempts.append((result.reason, emission.reason))
                raise asyncio.CancelledError()

        await self.start(listener)
        running = await self.broker.emit("example.stop", 1)
        await asyncio.wait_for(entered.wait(), 2)
        queued = await self.broker.emit("example.stop", 2)
        await asyncio.wait_for(self.host.stop(), 3)
        self.assertEqual((await self.broker.receipt(running.dispatch_id)).status, "cancelled")
        self.assertEqual((await self.broker.receipt(queued.dispatch_id)).status, "cancelled")
        self.assertEqual(attempts, [("capability_invocation_required", "event_invocation_required")])
        self.assertEqual(executed, [])
        self.assertEqual(self.broker._workers, {})
        self.assertEqual((await self.broker.emit("example.stop", 3)).reason, "event_host_unavailable")

    async def test_deferred_cycle_fails_and_cancels_new_work_instead_of_deadlocking(self):
        plugin = Plugin("example.cycle", permissions=("event.emit",))

        @plugin.on("example.first")
        async def first(event, ctx):
            return await ctx.events.emit("example.second", event.data)

        @plugin.on("example.second")
        async def second(event, ctx):
            return await ctx.events.emit("example.first", event.data)

        await self.start(plugin)
        receipt = await self.broker.emit("example.first", {"step": 1})
        terminal = await self.terminal(receipt.dispatch_id)
        self.assertEqual(terminal.status, "failed")
        self.assertIn(terminal.deliveries[0].reason, {"event_link_cycle", "event_link_failed"})
        await asyncio.sleep(0)
        self.assertEqual(self.broker._workers, {})
        self.assertEqual(self.broker.status_snapshot()["pending_deliveries"], 0)

    async def test_global_scope_denies_effects_and_resources_but_validates_pure_tool_output(self):
        plugin = Plugin("example.scope", permissions=("capability.invoke", "resource.read", "network.read"))
        executed = []

        @plugin.tool(effects=("network",))
        async def network() -> int:
            executed.append("network")
            return 1

        @plugin.tool(confirm="always")
        async def approval() -> int:
            executed.append("approval")
            return 1

        @plugin.tool
        async def invalid() -> int:
            executed.append("invalid")
            return "bad output"

        @plugin.on("example.check")
        async def check(event, ctx):
            values = {}
            for name in ("network", "approval", "invalid"):
                result = await ctx.tools.call_result("example.scope." + name, {})
                values[name] = result.reason
            resource = await ctx.resources.open("attachment")
            values["resource"] = resource.reason
            try:
                await ctx.resources.work_directory()
            except RuntimeError as error:
                values["work_directory"] = str(error)
            return values

        await self.start(plugin)
        receipt = await self.terminal((await self.broker.emit("example.check", {})).dispatch_id)
        self.assertEqual(receipt.status, "completed", receipt)
        values = receipt.deliveries[0].value
        self.assertEqual(values["network"], "capability_context_required")
        self.assertEqual(values["approval"], "capability_context_required")
        self.assertEqual(values["invalid"], "plugin_result_schema_mismatch")
        self.assertEqual(values["resource"], "resource_context_required")
        self.assertEqual(values["work_directory"], "resource_context_required")
        self.assertEqual(executed, ["invalid"])

    async def test_receipt_retention_pins_fast_child_until_parent_links_it(self):
        plugin = Plugin("example.retention", permissions=("event.emit",))
        started, release = asyncio.Event(), asyncio.Event()

        @plugin.on("example.parent")
        async def parent(event, ctx):
            child = await ctx.events.emit("example.unobserved", None)
            started.set()
            await release.wait()
            return child

        await self.start(plugin)
        self.broker._receipt_limit = 1
        first = await self.broker.emit("example.parent", {})
        await asyncio.wait_for(started.wait(), 2)
        self.assertEqual(len(self.broker._dispatches), 2)
        record = self.broker._dispatches[first.dispatch_id]
        release.set()
        await asyncio.wait_for(record.done.wait(), 2)
        done = record.snapshot()
        self.assertEqual(done.status, "completed", done)
        self.assertEqual(done.deliveries[0].value["status"], "unobserved")
        await self.broker.emit("example.unobserved", 2)
        self.assertEqual((await self.broker.receipt(first.dispatch_id)).reason, "event_dispatch_not_found")

    async def test_invalid_subscription_declarations_report_specific_activation_reason(self):
        # ``persistence`` is rejected at declaration time; the remaining options
        # are host-validated, so they must fail activation with a real reason.
        for options, reason in (
            ({"scope": []}, "event_scope_invalid"),
            ({"coalesce": {}}, "event_coalesce_invalid"),
            ({"sources": (42,)}, "event_sources_invalid"),
        ):
            with self.subTest(options=options):
                plugin = Plugin("example.invalid-event")

                @plugin.on("example.check", **options)
                async def check(event, ctx):
                    self.fail("invalid declaration must not execute")

                host = PluginHost(
                    (PluginSelection(plugin.manifest.plugin_id, True),),
                    entry_points_provider=lambda: (FakeEntryPoint(plugin.manifest.plugin_id, lambda: plugin),),
                    contribution_policy=TrustedStatefulPluginContributionPolicy(),
                )
                try:
                    status = await host.start()
                    self.assertEqual(status["plugins"][0]["reason"], reason, status)
                    self.assertEqual(status["event_handler_count"], 0)
                finally:
                    await host.stop()

    async def test_global_relay_preserves_routing_scope_through_a_normal_tool(self):
        relay = Plugin("example.relay", permissions=("event.emit", "capability.invoke"))
        receiver = Plugin("example.bound", permissions=("event.emit",))
        observed_contexts = []

        @relay.tool
        async def forward(data: Any, ctx: ToolContext) -> EventReceipt:
            observed_contexts.append((ctx.invocation.profile_user_id, ctx.invocation.session_id))
            return await ctx.events.emit("example.forwarded", data)

        @relay.on("example.original", sources=("example.publisher", "@host"))
        async def receive(event, ctx):
            result = await ctx.tools.call("example.relay.forward", {"data": event.data})
            return result

        @receiver.tool
        async def enable(ctx: ToolContext) -> EventBinding:
            await ctx.events.bind("example.bound.final")
            return await ctx.events.bind("example.bound.receive")

        @receiver.on("example.forwarded", name="receive", sources=("example.relay",), scope="conversation")
        async def receive_forwarded(event, ctx):
            return await ctx.events.emit("example.final", {"scope": ctx.scope_id, "data": event.data})

        @receiver.on("example.final", name="final", sources=("example.bound",), scope="conversation")
        async def final(event, ctx):
            return event.data

        await self.start(publisher(), relay, receiver)
        owner = await self.invoke("example.bound.enable")
        other = await self.invoke("example.bound.enable", owner="other")
        initial = await self.invoke("example.publisher.send", {"event_type": "example.original", "data": {"private": "owner data"}})
        done = await self.terminal(initial["dispatch_id"])
        child = await self.terminal(done.deliveries[0].value["dispatch_id"])
        self.assertEqual([item.scope_id for item in child.deliveries], [owner["scope_id"]])
        host = await self.terminal((await self.broker.emit("example.original", {"public": "host feed"})).dispatch_id)
        host_child = await self.terminal(host.deliveries[0].value["dispatch_id"])
        self.assertEqual({item.scope_id for item in host_child.deliveries}, {owner["scope_id"], other["scope_id"]})
        # Each conversation-bound receiver narrows the global feed before it
        # republishes data; neither downstream publication reaches both users.
        for item in host_child.deliveries:
            linked = item.value["deliveries"]
            self.assertEqual(len(linked), 1)
            self.assertEqual(linked[0]["value"]["scope"], item.scope_id)
        self.assertEqual(observed_contexts, [("", ""), ("", "")])


class SubscriptionPolicyTests(unittest.IsolatedAsyncioTestCase):
    """Declared subscription persistence and request_turn through the real host."""

    start = PluginEventV2Tests.start
    invoke = PluginEventV2Tests.invoke
    terminal = PluginEventV2Tests.terminal

    async def _start_with_turns(self, *plugins):
        """Start the real host with the real turn router attached before start."""

        from companion_v01.durable_session_queue import DurableSessionWorkQueue
        from companion_v01.plugin_agent_events import HostAgentEventRouter
        from companion_v01.plugin_conversation_refs import PluginConversationReferenceAuthority
        from companion_v01.session_inbox import SessionInboxStore
        from companion_v01.turn_coordination import TurnCoordinator

        root = Path(tempfile.mkdtemp(prefix="akane-subscription-"))
        self.addCleanup(lambda: None)
        self.coordinator = TurnCoordinator()
        self.queue = DurableSessionWorkQueue(SessionInboxStore(root / "inbox.db"))
        self.refs = PluginConversationReferenceAuthority(root / "refs.key", instance_id="test")
        self.router = HostAgentEventRouter(self.refs.resolve)
        self.router.bind_runtime(self.queue, self.coordinator)

        def configure(host):
            host.bind_turn_router(self.router)

        await self.start(*plugins, before_start=configure)
        self.broker.bind_turn_router(self.router)
        self.calls, self.frames = [], []
        self.context = PluginInvocationContext(
            "owner", "session", "desktop_pet", character_pack_id="akane",
            conversation_ref=self.refs.issue_desktop(
                profile_user_id="owner", session_id="session", character_pack_id="akane"),
        )

        def process(payload):
            self.calls.append(dict(payload))
            return {"status": "ok", "speech": "收到事件", "emotion": "normal", "speech_segments": ["收到事件"]}

        async def deliver(frame):
            self.frames.append(frame)
            return {"ok": True, "status": "queued"}

        from companion_v01.routes.think import build_think_router

        self.http = build_think_router(
            engine=SimpleNamespace(process_turn=process), public_guard=SimpleNamespace(),
            runtime_metrics=SimpleNamespace(), log_event=lambda *args, **kwargs: None,
            turn_coordinator=self.coordinator, session_work_queue=self.queue,
            plugin_turn_router=self.router, plugin_agent_event_handler_registrar=self.router.register_channel,
            desktop_agent_frame_delivery=deliver, desktop_agent_event_available=lambda: True,
            plugin_event_broker_provider=lambda: self.broker, plugin_conversation_ref_issuer=self.refs.issue_desktop,
        )

        async def cleanup():
            self.router.request_shutdown()
            await self.queue.close()
            await self.router.aclose()

        self.addAsyncCleanup(cleanup)

    async def _enable(self, tool: str):
        result = await self.host.invoke(tool, {}, context=self.context)
        self.assertFalse(result.is_error, result)
        return result.value

    async def _send(self, event_type: str, data: Any):
        result = await self.host.invoke("example.publisher.send_keyed",
                                        {"event_type": event_type, "data": data}, context=self.context)
        self.assertFalse(result.is_error, result)
        return result.value

    async def test_declared_request_turn_uses_the_normal_queue_and_reports_real_receipt(self):
        received = []
        subscriber = Plugin("example.policy.subscriber")

        @subscriber.tool
        async def enable(ctx: ToolContext) -> dict[str, Any]:
            return (await ctx.events.bind("example.policy.subscriber.decide")).as_dict()

        @subscriber.on("example.policy", name="decide", scope="conversation", request_turn=True)
        async def decide(event, ctx):
            received.append(event.data)
            return event.data

        await self._start_with_turns(publisher(), subscriber)
        self.assertEqual((await self._enable("example.policy.subscriber.enable"))["status"], "bound")
        receipt = await self._send("example.policy", {"hp": 8})
        done = await self.terminal(receipt["dispatch_id"])
        self.assertEqual(done.status, "completed", done)
        self.assertEqual(received, [{"hp": 8}])
        self.assertTrue(done.deliveries[0].linked_turn_requests)
        self.assertEqual(done.deliveries[0].value["model_status"], "completed")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.frames), 1)

    async def test_declared_timeline_persistence_records_once_for_two_subscribers(self):
        recorded = []
        first = Plugin("example.policy.first")
        second = Plugin("example.policy.second")
        seen = []

        @first.tool
        async def enable(ctx: ToolContext) -> dict[str, Any]:
            return (await ctx.events.bind("example.policy.first.keep")).as_dict()

        @second.tool
        async def enable(ctx: ToolContext) -> dict[str, Any]:
            return (await ctx.events.bind("example.policy.second.keep")).as_dict()

        @first.on("example.keep", name="keep", scope="conversation", persistence="timeline")
        async def first_keep(event, ctx):
            seen.append("first")
            return event.data

        @second.on("example.keep", name="keep", scope="conversation", persistence="timeline")
        async def second_keep(event, ctx):
            seen.append("second")
            return event.data

        await self._start_with_turns(publisher(), first, second)
        self.broker.bind_timeline_recorder(lambda payload: recorded.append(payload) or {"ok": True, "status": "recorded"})
        self.assertEqual((await self._enable("example.policy.first.enable"))["status"], "bound")
        self.assertEqual((await self._enable("example.policy.second.enable"))["status"], "bound")
        receipt = await self._send("example.keep", {"turn": 4})
        done = await self.terminal(receipt["dispatch_id"])
        self.assertEqual(done.status, "completed", done)
        self.assertEqual(sorted(seen), ["first", "second"])
        # Two subscribers, one durable record.
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0]["event"]["fields"], {"data": {"turn": 4}})
        self.engine.llm.assert_not_called()

    async def test_declared_persistence_never_fakes_success_when_storage_fails(self):
        first = Plugin("example.policy.fail")

        @first.tool
        async def enable(ctx: ToolContext) -> dict[str, Any]:
            return (await ctx.events.bind("example.policy.fail.keep")).as_dict()

        @first.on("example.fail", name="keep", scope="conversation", persistence="timeline")
        async def keep(event, ctx):
            return event.data

        await self._start_with_turns(publisher(), first)
        self.broker.bind_timeline_recorder(lambda payload: {"ok": False, "reason": "memcore_not_enabled"})
        self.assertEqual((await self._enable("example.policy.fail.enable"))["status"], "bound")
        receipt = await self._send("example.fail", {"hp": 1})
        done = await self.terminal(receipt["dispatch_id"])
        # The handler succeeded, but the declared durable record did not exist, so
        # the delivery reports the real failure instead of a fake completion.
        self.assertEqual(done.status, "failed", done)
        self.assertEqual(done.deliveries[0].reason, "memcore_not_enabled")


if __name__ == "__main__":
    unittest.main()
