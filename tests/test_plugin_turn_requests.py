"""Public SDK requests through signed context, the real inbox and desktop adapter."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import threading
import shutil
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import unittest

from akane_plugin import Plugin, ToolContext, TurnReceipt, EventBinding, ObservationReceipt
from akane_plugin import PluginInvocationContext as InvocationContext
from companion_v01.durable_session_queue import DurableSessionWorkQueue
from companion_v01.desktop_context_engine import build_turn_extra_user_context
from companion_v01.plugin_agent_events import HostAgentEventRouter
from companion_v01.plugin_conversation_refs import PluginConversationReferenceAuthority
from companion_v01.plugin_resources import ResourceInvocation
from companion_v01.routes.think import build_think_router
from companion_v01.session_inbox import SessionInboxStore
from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_active_generation import ActivePluginGeneration, PluginGenerationSnapshot
from companion_v01.plugin_generation import PluginGenerationProcess
from companion_v01.plugin_managed_artifacts import GeneratedFileManagedArtifactSink
from companion_v01.turn_coordination import TurnCoordinator
from tests import test_plugin_events_v2 as event_tests


def board_plugin(permissions=("agent.turn.request", "context.observe")):
    plugin = Plugin("example.board", permissions=permissions)

    @plugin.tool
    async def request(data: Any, ctx: ToolContext, versions: dict[str, int] = {},
                      stale: str = "latest", key: str = "") -> TurnReceipt:
        return await ctx.request_turn("Choose a move", data, observations=versions, stale=stale, coalesce_key=key or None)

    @plugin.tool
    async def observe(data: Any, ctx: ToolContext) -> ObservationReceipt:
        return await ctx.observe("board", data)

    @plugin.tool
    async def status(request_id: str, ctx: ToolContext) -> TurnReceipt:
        return await ctx.turn_status(request_id)

    @plugin.tool
    async def cancel(request_id: str, ctx: ToolContext) -> TurnReceipt:
        return await ctx.cancel_turn(request_id)

    @plugin.tool
    async def bind(ctx: ToolContext) -> EventBinding:
        return await ctx.events.bind("example.board.changed")

    @plugin.tool
    async def unbind(scope_id: str, ctx: ToolContext) -> EventBinding:
        return await ctx.events.unbind(scope_id)

    @plugin.on("board.changed", name="changed", sources=("@host",), scope="conversation")
    async def changed(event, ctx):
        observation = await ctx.observe("board", event.data)
        return await ctx.request_turn("Choose a move", event.data, observations={"board": observation.version})

    return plugin


class TurnRequestTests(unittest.IsolatedAsyncioTestCase):
    start_host = event_tests.PluginEventV2Tests.start
    terminal = event_tests.PluginEventV2Tests.terminal

    async def start(self, plugin=None):
        def configure(host):
            self.coordinator = TurnCoordinator()
            self.queue = DurableSessionWorkQueue(SessionInboxStore(self.root / "inbox.db"))
            self.refs = PluginConversationReferenceAuthority(self.root / "refs.key", instance_id="test")
            self.router = HostAgentEventRouter(self.refs.resolve)
            self.router.bind_runtime(self.queue, self.coordinator)
            host.bind_turn_router(self.router)
        await self.start_host(plugin or board_plugin(), before_start=configure)
        self.context = InvocationContext("owner", "session", "desktop_pet", character_pack_id="akane",
            conversation_ref=self.refs.issue_desktop(profile_user_id="owner", session_id="session", character_pack_id="akane"))
        self.calls, self.frames = [], []
        self.entered, self.release = threading.Event(), threading.Event()
        self.release.set()
        self.failure = False
        self.model_frame = {"speech": "B2", "speech_segments": ["B2"], "emotion": "thinking", "activity": {"action": "pause"}}

        def process(payload):
            self.entered.set()
            if not self.release.wait(5):
                raise RuntimeError("test_model_barrier_timeout")
            observation = self.router.prompt_context(profile_user_id=payload["real_user_id"],
                session_id=payload["session_id"], character_pack_id=payload["character_pack_id"])
            self.calls.append((dict(payload), observation))
            return dict(self.model_frame)

        async def deliver(frame):
            if self.failure:
                return {"ok": False, "status": "failed", "reason": "test_delivery_failed"}
            self.frames.append(frame)
            return {"ok": True, "status": "queued"}

        self.http = build_think_router(engine=SimpleNamespace(process_turn=process), public_guard=SimpleNamespace(),
            runtime_metrics=SimpleNamespace(), log_event=lambda *args, **kwargs: None,
            turn_coordinator=self.coordinator, session_work_queue=self.queue,
            plugin_turn_router=self.router, plugin_agent_event_handler_registrar=self.router.register_channel,
            desktop_agent_frame_delivery=deliver, desktop_agent_event_available=lambda: True,
            plugin_event_broker_provider=lambda: self.broker, plugin_conversation_ref_issuer=self.refs.issue_desktop)
        async def cleanup():
            self.release.set()
            self.router.request_shutdown()
            await self.queue.close()
            await self.router.aclose()
        self.addAsyncCleanup(cleanup)

    async def call(self, name, arguments=None, context=None):
        result = await self.host.invoke("example.board." + name, arguments or {}, context=context or self.context)
        self.assertIsInstance(result.content, dict, result)
        return result.content

    async def settled(self, request_id):
        async with asyncio.timeout(5):
            while True:
                result = await self.call("status", {"request_id": request_id})
                if result["complete"]:
                    return result
                await asyncio.sleep(0.01)

    async def test_typed_request_uses_queue_and_real_frame_delivery(self):
        await self.start()
        data = {"move": "A1", "nested": [False, None, 0, 0.5]}
        observation = await self.call("observe", {"data": data})
        self.assertEqual(self.calls, [])
        receipt = await self.call("request", {"data": data, "versions": {"board": observation["version"]}})
        final = await self.settled(receipt["request_id"])
        self.assertEqual((final["status"], final["model_status"], final["delivery_status"]), ("completed", "completed", "queued"))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][0]["plugin_external_event"]["data"], data)
        # Follow the engine's actual payload-to-context adapter, not merely a
        # lookalike field on the queued envelope.
        self.assertIn('"nested": [', build_turn_extra_user_context(None, self.calls[0][0], None))
        self.assertIn('"move": "A1"', self.calls[0][1])
        self.assertEqual(self.frames, [self.model_frame])

    async def test_busy_session_coalesces_latest_before_one_model_decision(self):
        await self.start()
        async with self.coordinator.hold("owner", "session", actor_id="desktop:owner", channel="desktop_pet"):
            observed = await self.call("observe", {"data": {"move": "A1"}})
            first = await self.call("request", {"data": 1, "versions": {"board": observed["version"]}, "key": "move"})
            latest = await self.call("observe", {"data": {"move": "B2"}})
            second = await self.call("request", {"data": [False, None, 2], "versions": {"board": latest["version"]}, "key": "move"})
            self.assertEqual(first["request_id"], second["request_id"])
            self.assertEqual(second["status"], "merged")
            self.assertEqual(self.calls, [])
        final = await self.settled(first["request_id"])
        self.assertEqual(final["observation_versions"], {"board": latest["version"]})
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][0]["plugin_external_event"]["data"], [False, None, 2])
        self.assertNotIn("A1", self.calls[0][1])

    async def test_stale_reject_before_model_and_latest_updates_at_decision(self):
        await self.start()
        async with self.coordinator.hold("owner", "session", actor_id="desktop:owner", channel="desktop_pet"):
            observed = await self.call("observe", {"data": "A1"})
            rejected = await self.call("request", {"data": 0, "versions": {"board": observed["version"]}, "stale": "reject"})
            accepted = await self.call("request", {"data": False, "versions": {"board": observed["version"]}})
            latest = await self.call("observe", {"data": "B2"})
        final = await self.settled(rejected["request_id"])
        self.assertEqual(final["reason"], "observation_version_stale")
        self.assertEqual(final["model_status"], "not_started")
        final = await self.settled(accepted["request_id"])
        self.assertEqual(final["observation_versions"], {"board": latest["version"]})
        self.assertEqual(len(self.calls), 1)

    async def test_observation_change_between_queue_claim_and_model_input_is_checked(self):
        await self.start()
        self.release.clear()
        observed = await self.call("observe", {"data": "A1"})
        receipt = await self.call("request", {"data": None, "versions": {"board": observed["version"]}, "stale": "reject"})
        self.assertTrue(await asyncio.to_thread(self.entered.wait, 3))
        await self.call("observe", {"data": "B2"})
        self.release.set()
        final = await self.settled(receipt["request_id"])
        self.assertEqual(final["reason"], "observation_version_stale")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.frames, [])

    async def test_event_receipt_waits_for_turn_and_unbind_cancels_pending(self):
        await self.start()
        binding = await self.call("bind")
        async with self.coordinator.hold("owner", "session", actor_id="desktop:owner", channel="desktop_pet"):
            event = await self.broker.emit("board.changed", {"move": "A1"}, context=self.context)
            async with asyncio.timeout(5):
                while True:
                    receipt = await self.broker.receipt(event.dispatch_id)
                    if receipt.deliveries[0].linked_turn_requests:
                        break
                    await asyncio.sleep(0.01)
            self.assertFalse(receipt.complete)
            self.assertEqual(receipt.deliveries[0].status, "waiting")
            request_id = receipt.deliveries[0].linked_turn_requests[0]
            await self.call("unbind", {"scope_id": binding["scope_id"]})
        final = await self.settled(request_id)
        self.assertEqual(final["status"], "cancelled")
        self.assertEqual((await self.terminal(event.dispatch_id)).status, "cancelled")
        self.assertEqual(self.calls, [])

    async def test_event_completion_contains_actual_model_and_delivery_receipt(self):
        await self.start()
        await self.call("bind")
        event = await self.broker.emit("board.changed", {"move": "A1"}, context=self.context)
        final = await self.terminal(event.dispatch_id)
        self.assertEqual(final.status, "completed", final)
        self.assertEqual(final.deliveries[0].value["model_status"], "completed")
        self.assertEqual(final.deliveries[0].value["delivery_status"], "queued")
        self.assertEqual(len(self.frames), 1)

    async def test_cancel_queued_and_old_stop_scope_cannot_resurrect_work(self):
        await self.start()
        old = ResourceInvocation("example.board", self.context, generation_id=str(self.host._generation), can_request_turn=True)
        self.broker.initialize_scope(old)
        async with self.coordinator.hold("owner", "session", actor_id="desktop:owner", channel="desktop_pet"):
            first = await self.call("request", {"data": 1})
            final = await self.call("cancel", {"request_id": first["request_id"]})
            self.assertEqual(final["status"], "cancelled")
            self.coordinator.request_stop(profile_user_id="owner", session_id="session", actor_id="desktop:owner")
            reply = await self.broker.request("request_turn", {"reason": "old", "data": 2}, invocation=old)
            self.assertEqual(reply["reason"], "agent_turn_scope_expired")
        fresh = await self.call("request", {"data": 3})
        self.assertEqual((await self.settled(fresh["request_id"]))["status"], "completed")
        self.assertEqual(len(self.calls), 1)

    async def test_permissions_identity_generation_and_private_data(self):
        await self.start()
        for context in (InvocationContext(), replace(self.context, session_id="forged")):
            result = await self.call("request", {"data": {"session_id": "session"}}, context=context)
            self.assertEqual(result["status"], "rejected")
        invocation = ResourceInvocation("example.board", self.context, generation_id=str(self.host._generation),
            can_request_turn=True, private_values={"fixture-secret"})
        self.broker.initialize_scope(invocation)
        result = await self.broker.request("request_turn", {"reason": "request", "data": {"value": "fixture-secret"}}, invocation=invocation)
        self.assertEqual(result["reason"], "plugin_result_private_data")
        self.assertEqual(self.calls, [])

    async def test_cancelling_running_request_waits_for_real_stop(self):
        await self.start()
        self.release.clear()
        request = await self.call("request", {"data": "A1"})
        self.assertTrue(await asyncio.to_thread(self.entered.wait, 3))
        receipt = await self.call("cancel", {"request_id": request["request_id"]})
        self.assertEqual(receipt["status"], "cancelling")
        self.assertFalse(receipt["complete"])
        self.release.set()
        final = await self.settled(request["request_id"])
        self.assertEqual(final["status"], "cancelled")
        self.assertEqual(final["model_status"], "stopped")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.frames, [])

    async def test_large_request_reaches_real_context_with_readable_full_material(self):
        await self.start()
        data = {"board": "完整棋盘" * 9000, "tail": [False, None, 0]}
        failed = await self.call("request", {"data": data})
        self.assertEqual(failed["reason"], "plugin_result_storage_unavailable")
        self.assertEqual(self.calls, [])
        self.broker.observations.sink = GeneratedFileManagedArtifactSink(self.files)
        request = await self.call("request", {"data": data})
        self.assertEqual((await self.settled(request["request_id"]))["status"], "completed")
        text = build_turn_extra_user_context(None, self.calls[0][0], None)
        handle = re.search(r"同次执行的完整 JSON 已保存为 (\S+)，", text).group(1)
        artifact = self.files.resolve_generated_artifact(profile_user_id="owner", session_id="session", target=handle)
        self.assertEqual(json.loads(Path(artifact["absolute_path"]).read_text(encoding="utf-8")), data)
        self.assertEqual(artifact["delivery_status"], "not_requested")
        self.assertTrue(self.files.inspect_generated_file(profile_user_id="owner", session_id="session", target=handle, section="content")["ok"])
        self.assertNotIn(str(self.root), text)

    async def test_event_cannot_link_an_unrelated_request_id(self):
        plugin = board_plugin()
        saved = {}
        @plugin.on("board.forged", name="forged", scope="global")
        async def forged(event, ctx):
            return TurnReceipt(saved["id"], "completed", True)
        await self.start(plugin)
        async with self.coordinator.hold("owner", "session", actor_id="desktop:owner", channel="desktop_pet"):
            request = await self.call("request", {"data": 0})
            saved["id"] = request["request_id"]
            event = await self.broker.emit("board.forged", None, context=self.context)
            final = await self.terminal(event.dispatch_id)
            self.assertEqual(final.status, "failed")
            self.assertEqual(final.deliveries[0].reason, "agent_turn_link_invalid")
        self.assertEqual((await self.settled(request["request_id"]))["status"], "completed")

    async def test_delivery_failure_keeps_successful_model_result_distinct(self):
        await self.start()
        self.failure = True
        receipt = await self.call("request", {"data": 1})
        final = await self.settled(receipt["request_id"])
        self.assertEqual(final["status"], "failed")
        self.assertEqual(final["model_status"], "completed")
        self.assertEqual(final["delivery_status"], "failed")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.frames, [])

    async def test_independent_board_sample_in_real_worker_and_generation_revocation(self):
        await self.start()
        site = self.root / "site"
        project = Path(__file__).resolve().parents[1]
        shutil.copytree(project / "examples/plugins/akane_sdk_board/src/akane_sdk_board", site / "akane_sdk_board")
        metadata = site / "akane_sdk_board_example-0.1.0.dist-info"
        metadata.mkdir()
        (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: akane-sdk-board-example\nVersion: 0.1.0\n", encoding="utf-8")
        (metadata / "entry_points.txt").write_text("[akane.plugins.v1]\nexample.board = akane_sdk_board:create_plugin\n", encoding="utf-8")
        active = ActivePluginGeneration()
        broker = active.build_event_broker()
        broker.bind_turn_router(self.router)
        process = PluginGenerationProcess(project_root=project, site_dir=site, plugin_id="example.board", work_dir=self.root / "worker")
        process.bind_events_provider(broker)
        try:
            await asyncio.to_thread(process.start)
            await active.publish(PluginGenerationSnapshot((PluginSelection("example.board", True),), (process,)))
            self.host, self.broker = active, broker
            observation = await self.call("update", {"state": {"move": "A1", "done": False, "weight": 0.5}})
            self.assertEqual(observation["status"], "observed")
            self.assertEqual(self.calls, [])
            request = await self.call("decide", {"version": observation["version"]})
            self.assertEqual((await self.settled(request["request_id"]))["status"], "completed")
            self.assertIn("A1", self.calls[0][1])
            binding = await self.call("enable")
            self.assertEqual(binding["status"], "bound")
            event = await self.call("publish", {"state": {"move": "B2", "done": False}})
            final = await self.terminal(event["dispatch_id"])
            self.assertEqual(final.status, "completed", final)
            self.assertEqual(final.deliveries[0].value["model_status"], "completed")
            async with self.coordinator.hold("owner", "session", actor_id="desktop:owner", channel="desktop_pet"):
                pending = await self.call("decide", {"version": observation["version"]})
                await active.publish(PluginGenerationSnapshot((), ()))
                self.router.reconcile()
                invocation = ResourceInvocation("example.board", self.context, generation_id=process.generation_id)
                receipt = self.router.receipt(pending["request_id"], invocation=invocation)
                self.assertEqual(receipt.status, "cancelled", receipt)
            self.assertEqual(len(self.calls), 2)
        finally:
            await active.stop()
            if process.running:
                await asyncio.to_thread(process.stop)
