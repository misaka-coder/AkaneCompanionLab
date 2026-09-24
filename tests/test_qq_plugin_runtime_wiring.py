"""Exercise app.py's real QQ bootstrap without starting the desktop/services."""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from akane_plugin import Plugin, PluginInvocationContext, ToolContext

from companion_v01.durable_session_queue import DurableSessionWorkQueue
from companion_v01.routes import qq as qq_routes
from companion_v01.session_inbox import SessionInboxStore
from companion_v01.turn_coordination import TurnCoordinator
from tests import test_plugin_agent_events as fixtures
from tests import test_plugin_events_v2 as event_fixtures
from tests import test_minecraft_plugin as minecraft


class QQRuntimeWiringTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SessionInboxStore(Path(self.temp.name) / "inbox.db")
        self.queue = DurableSessionWorkQueue(self.store)
        self.coordinator = TurnCoordinator()
        captured = {}
        build = qq_routes.build_qq_router

        def capture(**kwargs):
            captured.update(kwargs)
            return build(**kwargs)

        with patch.object(qq_routes, "build_qq_router", side_effect=capture):
            self.turns = fixtures.QQTurnRouterTests()._build(session_work_queue=self.queue)
        self.turns.bind_runtime(self.queue, self.coordinator)
        runtime = NS(bot_id="test", engine=captured["engine"], config_module=captured["config_module"],
                     qq_gateway=captured["qq_gateway"], runtime_metrics=captured["runtime_metrics"],
                     tts_client=None, settings=None, qq_followup_tasks=None, qq_channel_config=None,
                     admin_write_auth=None, plugin_command_broker=None, plugin_event_broker=None,
                     plugin_conversation_refs=NS(issue_qq=lambda **kwargs: "ref-group"),
                     set_llm_thinking_mode=lambda value: value,
                     turn_coordinator=self.coordinator, session_work_queue=self.queue,
                     plugin_agent_event_router=self.turns)
        self.app = FastAPI()
        source = Path(__file__).resolve().parents[1] / "companion_v01/app.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        bootstrap = next(node for node in tree.body if isinstance(node, ast.For)
                         and isinstance(node.target, ast.Name) and node.target.id == "qq_bot_runtime")
        # Execute the production assembly, not a second hand-written copy of
        # its arguments: the two URL registrations caused the original bug.
        exec(compile(ast.Module(body=[bootstrap], type_ignores=[]), str(source), "exec"), {
            "app": self.app, "bot_registry": NS(values=lambda: [runtime], default_bot_id="test"),
            "build_qq_router": build, "logger": captured["logger"],
            "_bot_log_event": lambda bot_id: captured["log_event"],
        })
        self.payloads = []

        async def cleanup():
            self.turns.request_shutdown()
            await self.queue.close()
            await self.turns.aclose()
        self.addAsyncCleanup(cleanup)

    def model(self, **kwargs):
        self.payloads.append(kwargs["turn_payload"])
        return {"send_result": {"ok": True, "status": "sent"},
                "final_frame_received": True, "frame": {"speech": "done"}}

    async def request(self, data=None, coalesce_key=None):
        intent = fixtures.turn_intent(reference="ref-group", source="example.audit", requires_queue=True,
                             data=data or {"value": 1})
        return await self.turns._admit(intent, self.turns._resolve("ref-group"),
                                       generation_id="g1", live=lambda: True, epoch=0,
                                       coalesce_key=coalesce_key)

    async def drain(self):
        await asyncio.wait_for(asyncio.gather(*list(self.queue._workers.values())), 5)

    async def test_both_url_aliases_keep_plugin_receipt_owner(self):
        paths = {route.path for route in self.app.routes}
        self.assertIn("/api/qq/napcat/event", paths)
        self.assertIn("/api/bots/test/qq/napcat/event", paths)
        with patch.object(qq_routes, "_process_qq_turn_streaming", side_effect=self.model):
            receipt = await self.request()
            await self.drain()
        record = self.turns._records[receipt.request_id]
        self.assertEqual(record.status, "completed")
        self.assertTrue(record.snapshot().complete)
        self.assertEqual(self.store.get(record.item_id).status, "committed")
        self.assertEqual(len(self.payloads), 1)

    async def test_busy_alias_queue_coalesces_then_runs_latest_intent_once(self):
        with patch.object(qq_routes, "_process_qq_turn_streaming", side_effect=self.model):
            async with self.coordinator.hold("qq_group_shared_87", "qq_group_shared_87", actor_id="qq:123"):
                first = await self.request({"value": 1}, "game")
                second = await self.request({"value": 2}, "game")
                self.assertEqual(first.request_id, second.request_id)
                self.assertEqual(second.status, "merged")
                self.assertEqual(self.payloads, [])
            await self.drain()
        self.assertEqual(len(self.payloads), 1)
        self.assertEqual(self.payloads[0]["plugin_external_event"]["data"], {"value": 2})
        self.assertEqual(self.turns._records[first.request_id].status, "completed")

    async def test_alias_queue_failure_settles_plugin_receipt(self):
        with patch.object(qq_routes, "_process_qq_turn_streaming", side_effect=RuntimeError("fixture_failure")):
            receipt = await self.request()
            await self.drain()
        record = self.turns._records[receipt.request_id]
        self.assertEqual(record.status, "failed")
        self.assertTrue(record.snapshot().complete)
        self.assertEqual(self.store.get(record.item_id).status, "failed")

    async def test_preempted_plugin_receipt_keeps_reason_distinct_from_explicit_stop(self):
        for explicit in (False, True):
            with self.subTest(explicit=explicit):
                def stop_model(**kwargs):
                    if explicit:
                        self.coordinator.request_stop(profile_user_id="qq_group_shared_87",
                                                      session_id="qq_group_shared_87", actor_id="qq:123")
                    else:
                        self.coordinator.offer_steer(profile_user_id="qq_group_shared_87",
                            session_id="qq_group_shared_87", actor_id="qq:123", content="new message")
                    control = self.coordinator.drain(kwargs["turn_payload"]["_turn_control_id"])
                    return {"send_result": {"ok": True, "status": "stopped"}, "final_frame_received": True,
                            "frame": {"status": "stopped", "reason": control["stop_reason"], "speech": ""}}
                with patch.object(qq_routes, "_process_qq_turn_streaming", side_effect=stop_model):
                    receipt = await self.request()
                    await self.drain()
                record = self.turns._records[receipt.request_id]
                self.assertEqual(record.status, "cancelled")
                self.assertEqual(record.reason, "user_stopped" if explicit else "addressed_input_preempts_optional_turn")

    async def test_minecraft_events_remain_deliverable_while_real_qq_model_request_is_queued(self):
        bridge = minecraft.MinecraftBridge(client_factory=minecraft.FakeClient)
        fake_context = minecraft.context(profile="qq_group_shared_87", session="qq_group_shared_87")
        fake_context.invocation = minecraft.replace(fake_context.invocation, character_pack_id="reimu")
        await bridge.start({"goal": "finish two steps"}, fake_context)
        plugin = Plugin("akane.minecraft", permissions=("event.emit", "agent.turn.request", "context.observe"))

        @plugin.tool
        async def bind(ctx: ToolContext):
            return (await ctx.events.bind("akane.minecraft.game_events")).scope_id

        @plugin.tool
        async def publish(ctx: ToolContext):
            return (await ctx.events.emit("minecraft.numen.events", {"control_id": bridge.active.control_id})).as_dict()

        @plugin.on("minecraft.numen.events", name="game_events", sources=("akane.minecraft",), scope="conversation")
        async def game_events(event, ctx):
            return await bridge.game_event(event, ctx)

        await event_fixtures.PluginEventV2Tests.start(self, plugin, before_start=lambda host: host.bind_turn_router(self.turns))
        context = PluginInvocationContext("qq_group_shared_87", "qq_group_shared_87", "qq",
                                           character_pack_id="reimu", conversation_ref="ref-group")
        bound = await self.host.invoke("akane.minecraft.bind", {}, context=context)
        self.assertFalse(bound.is_error, bound)
        bridge.active.scope_id = bound.value

        async def publish_and_settle():
            sent = await self.host.invoke("akane.minecraft.publish", {}, context=context)
            self.assertFalse(sent.is_error, sent)
            delivery = await event_fixtures.PluginEventV2Tests.terminal(self, sent.value["dispatch_id"])
            self.assertEqual(delivery.status, "completed", delivery)

        with patch.object(qq_routes, "_process_qq_turn_streaming", side_effect=self.model):
            async with self.coordinator.hold("qq_group_shared_87", "qq_group_shared_87", actor_id="qq:123"):
                bridge.active.pending.append(minecraft.EventBatch("first task ended"))
                await publish_and_settle()
                first_id = bridge.active.turn_request_id
                self.assertEqual(self.turns._records[first_id].status, "queued")
                bridge.active.pending.append(minecraft.EventBatch("second task ended"))
                await publish_and_settle()
                self.assertEqual(bridge.active.turn_request_id, first_id)
                self.assertEqual(bridge.active.turns_coalesced, 1)
                self.assertEqual(len(self.payloads), 0)
            await self.drain()
            self.assertEqual(len(self.payloads), 1)
            self.assertEqual(self.turns._records[first_id].actual_versions, {"game": 2})
            await publish_and_settle()
        self.assertFalse(bridge.active.pending)
        self.assertFalse(bridge.active.turn_request_id)
        self.assertEqual(self.broker.status_snapshot()["running_deliveries"], 0)


if __name__ == "__main__":
    unittest.main()
