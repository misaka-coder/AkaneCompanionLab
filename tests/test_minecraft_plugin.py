from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins/akane_minecraft/src"))
from akane_plugin import PluginInvocationContext
from akane_minecraft.bridge import MinecraftBridge, EventBatch
from akane_minecraft.plugin import create_plugin
from akane_minecraft.transport import GameConnection, MinecraftError, NumenClient, active_task
from companion_v01.plugin_generation_codec import invocation_context_from_wire, invocation_context_to_wire
from companion_v01.plugin_tool_bridge import PluginCapabilityToolHandler
from companion_v01.tool_handlers.core import ToolExecutionContext


VALUES = dict(endpoint="http://127.0.0.1:8765/mcp", token="test-secret-token", companion="Reimu",
              session_minutes=30)
REIMU_ID = "11111111-1111-4111-8111-111111111111"
CECILIA_ID = "22222222-2222-4222-8222-222222222222"


def result(value):
    return {"content": [{"type": "text", "text": json.dumps(value)}]}


class FakeClient:
    def __init__(self, connection):
        self.calls = []
        self.task = ""
        self.events = "(no new events)"
        self.fail_action = False
        self.companions = {REIMU_ID: "Reimu"}

    async def list_tools(self):
        return [{"name": n} for n in ("get_self_status", "get_events", "say", "task_status", "task_stop")]

    async def call(self, name, arguments, **kwargs):
        self.calls.append((name, arguments, kwargs))
        if name == "list_companions":
            return result("Live companions:\n" + "\n".join(
                f"- {name}  (id: {identity})" for identity, name in self.companions.items()))
        if name == "get_events":
            event, self.events = self.events, "(no new events)"
            return result(event)
        if name == "task_status":
            return result({"success": True, "data": {"task_id": self.task, "state": "running" if self.task else "idle"}})
        if kwargs.get("effectful") and self.fail_action:
            raise MinecraftError("minecraft_timeout", uncertain=True)
        if name == "task_stop":
            self.task = ""
        if name == "follow":
            self.task = "t42"
            return result({"success": True, "data": {"task_id": self.task}})
        return result({"success": True, "data": {"health": 20}})

    async def aclose(self):
        pass


def context(actor="master", profile="master", session="desktop"):
    return NS(invocation=PluginInvocationContext(profile, session, "desktop_pet",
                  conversation_ref="signed-ref", authorization_profile_user_id=actor),
        connections=NS(resolve=AsyncMock(return_value=NS(ok=True, options=dict(VALUES)))),
        events=NS(bind=AsyncMock(return_value=NS(status="bound", scope_id=profile+session)), unbind=AsyncMock()))


class EventContext:
    """Model scheduling is simulated; bridge state and MCP client are real test objects."""
    def __init__(self, scope_id):
        self.scope_id = scope_id
        self.events = NS(unbind=AsyncMock())
        self.snapshots, self.requests, self.records = [], [], {}

    async def observe(self, key, data):
        self.snapshots.append(json.loads(json.dumps(data)))
        return NS(status="observed", version=len(self.snapshots), reason="")

    async def request_turn(self, reason, data, **kwargs):
        self.requests.append((data, kwargs))
        queued = next((value for value in self.records.values() if value.status == "queued"), None)
        if queued:
            return NS(request_id=queued.request_id, status="merged", reason="")
        request_id = "request-" + str(len(self.records) + 1)
        receipt = NS(request_id=request_id, status="queued", complete=False, reason="", observation_versions={})
        self.records[request_id] = receipt
        return receipt

    async def turn_status(self, request_id):
        return self.records[request_id]


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bridge = MinecraftBridge(client_factory=FakeClient)
        self.ctx = context()

    async def begin(self, ctx=None, **payload):
        started = await self.bridge.start({"goal": "follow owner", **payload}, ctx or self.ctx)
        self.assertFalse(started.is_error, started)
        return started.content["control_id"]

    async def test_host_authorized_group_keeps_its_game_session_scope(self):
        group = context(profile="qq_group_shared_test", session="qq-group")
        await self.begin(group)
        self.assertEqual(self.bridge.active.context[:2], ("qq_group_shared_test", "qq-group"))

    async def test_control_lease_busy_stop_and_stale_calls(self):
        lease = await self.begin()
        action = await self.bridge.invoke("follow", {"control_id": lease}, self.ctx)
        self.assertEqual(action.status, "accepted")
        self.assertFalse(action.content["task_completed"])
        busy = await self.bridge.invoke("mine", {"control_id": lease}, self.ctx)
        self.assertEqual(busy.reason, "minecraft_body_busy")
        other = context(session="other")
        refused = await self.bridge.start({"goal": "new"}, other)
        self.assertEqual(refused.reason, "minecraft_controller_busy")
        await self.begin(other, take_over=True)
        old = await self.bridge.invoke("say", {"text": "bad", "control_id": lease}, self.ctx)
        self.assertEqual(old.reason, "minecraft_controller_changed")
        stopped = await self.bridge.stop(other)
        self.assertTrue(stopped.content["body_stopped"])
        other.events.unbind.assert_not_awaited()
        self.assertIsNone(self.bridge.active)
        old = await self.bridge.invoke("say", {"text": "bad", "control_id": lease}, self.ctx)
        self.assertEqual(old.reason, "minecraft_start_session_required")

    async def test_uncertain_write_blocks_retry_and_missing_read_target_is_explicit(self):
        lease = await self.begin()
        client = self.bridge.active.client
        client.fail_action = True
        first = await self.bridge.invoke("say", {"control_id": lease, "text": "x"}, self.ctx)
        self.assertFalse(first.content["outcome_known"])
        count = len(client.calls)
        second = await self.bridge.invoke("say", {"control_id": lease, "text": "x"}, self.ctx)
        self.assertEqual(second.reason, "minecraft_previous_action_uncertain_stop_and_reconnect")
        self.assertEqual(sum(c[0] == "say" for c in client.calls), 1)
        override = await self.bridge.invoke("get_self_status", {"companion": "Other"}, self.ctx)
        self.assertEqual(override.reason, "minecraft_companion_not_found")
        self.assertFalse(override.content["retryable"])

    async def test_new_world_single_companion_can_start_from_qq(self):
        client = self.bridge.client(GameConnection.parse(VALUES))
        client.companions = {CECILIA_ID: "Cecilia"}
        qq = context(profile="qq_group_shared_test", session="qq-group")
        qq.invocation = replace(qq.invocation, client_mode="qq")
        lease = await self.begin(qq)
        self.assertEqual(self.bridge.active.connection.companion, CECILIA_ID)
        self.assertEqual(self.bridge.active.context[:2], ("qq_group_shared_test", "qq-group"))
        action = await self.bridge.invoke("follow", {"control_id": lease}, qq)
        self.assertEqual(action.status, "accepted")
        self.assertEqual(next(c for c in client.calls if c[0] == "follow")[1]["companion"], CECILIA_ID)

    async def test_recreated_same_name_invalidates_old_controller_and_queued_writes(self):
        old = await self.begin()
        client = self.bridge.active.client
        client.companions = {CECILIA_ID: "Reimu"}
        refused = await self.bridge.invoke("say", {"control_id": old, "text": "stale"}, self.ctx)
        self.assertEqual(refused.reason, "minecraft_world_or_companion_changed")
        self.assertFalse(refused.content["retryable"])
        self.assertFalse(any(c[0] == "say" for c in client.calls))
        new = await self.begin()
        self.assertNotEqual(new, old)
        self.assertEqual(self.bridge.active.connection.companion, CECILIA_ID)
        refused = await self.bridge.invoke("follow", {"control_id": old}, self.ctx)
        self.assertEqual(refused.reason, "minecraft_controller_changed")

    async def test_ambiguous_or_absent_companions_do_not_guess(self):
        client = self.bridge.client(GameConnection.parse(VALUES))
        client.companions = {REIMU_ID: "Other", CECILIA_ID: "Cecilia"}
        result = await self.bridge.start({"goal": "follow"}, self.ctx)
        self.assertEqual(result.reason, "minecraft_companion_selection_required")
        listing = await self.bridge.companions(self.ctx)
        self.assertEqual(len(listing.content["companions"]), 2)
        await self.begin(companion="Cecilia")
        self.assertEqual(self.bridge.active.connection.companion, CECILIA_ID)
        client.companions = {}
        result = await self.bridge.start({"goal": "follow"}, self.ctx)
        self.assertEqual(result.reason, "minecraft_no_live_companions")

    async def test_stop_after_world_change_does_not_stop_new_body(self):
        lease = await self.begin()
        await self.bridge.invoke("follow", {"control_id": lease}, self.ctx)
        client = self.bridge.active.client
        client.companions = {CECILIA_ID: "Reimu"}
        stopped = await self.bridge.stop(self.ctx)
        self.assertFalse(stopped.content["body_stopped"])
        self.assertFalse(stopped.content["listening"])
        self.assertFalse(any(c[0] == "task_stop" for c in client.calls))

    async def test_explicit_start_renews_paused_session_and_target_clients_are_reused(self):
        old = await self.begin()
        self.bridge.active.paused = True
        new = await self.begin()
        self.assertNotEqual(old, new)
        self.assertFalse(self.bridge.active.paused)
        self.assertIs(self.bridge.client(GameConnection.parse(VALUES)),
                      self.bridge.client(replace(GameConnection.parse(VALUES), companion="Cecilia")))

    async def test_goal_update_preserves_running_task_and_unlimited_session(self):
        self.ctx.connections.resolve.return_value.options["session_minutes"] = 0
        lease = await self.begin()
        await self.bridge.invoke("follow", {"control_id": lease}, self.ctx)
        result = await self.bridge.start({"goal": "完成整座神社，子任务后继续"}, self.ctx)
        self.assertEqual(result.content["control_id"], lease)
        self.assertEqual(result.content["status"], "goal_updated")
        self.assertIsNone(result.content["remaining_seconds"])
        self.assertEqual(self.bridge.active.owned_task_id, "t42")
        self.assertEqual(self.bridge.active.goal, "完成整座神社，子任务后继续")

    async def test_blueprint_listing_and_game_skill_are_readable_without_start(self):
        listing = await self.bridge.invoke("blueprint", {"action": "list"}, self.ctx)
        self.assertFalse(listing.is_error)
        skill = await self.bridge.invoke("load_skill", {"name": "building_design", "file": ""}, self.ctx)
        self.assertFalse(skill.is_error)
        building = await self.bridge.invoke("blueprint", {"action": "build", "file": "hut"}, self.ctx)
        self.assertEqual(building.reason, "minecraft_start_session_required")

    async def test_completion_event_requests_next_step_with_full_goal(self):
        await self.begin(goal="完成地基、墙体和屋顶")
        session = self.bridge.active
        session.pending.append(EventBatch('task_finished id=t42 status=done'))
        event = NS(data={"control_id": session.control_id, "batch_id": session.pending[0].batch_id})
        captured = []

        async def next_turn(stimulus, data, **kwargs):
            captured.append(data)
            # A completion-triggered turn can issue the next action with the same grant.
            action = await self.bridge.invoke("follow", {"control_id": data["control_id"]}, self.ctx)
            self.assertEqual(action.status, "accepted")
            return NS(request_id="next-step", status="queued", reason="")

        event_ctx = NS(scope_id=session.scope_id, request_turn=next_turn,
                       observe=AsyncMock(return_value=NS(status="observed", version=1)))
        await self.bridge.game_event(event, event_ctx)
        self.assertEqual(captured[0]["goal"], "完成地基、墙体和屋顶")
        self.assertIn("task_finished", captured[0]["game_events"])
        self.assertIn("自主执行下一步", captured[0]["instructions"])
        self.assertEqual(session.turns_requested, 1)

    async def test_mcp_task_end_without_native_event_wakes_once_without_claiming_success(self):
        lease = await self.begin()
        await self.bridge.invoke("follow", {"control_id": lease}, self.ctx)
        session = self.bridge.active
        await self.bridge.observe_task_end(session)
        self.assertEqual(len(session.pending), 0)
        session.client.task = ""
        session.last_task_probe_at = 0
        await self.bridge.observe_task_end(session)
        await self.bridge.observe_task_end(session)
        self.assertEqual(len(session.pending), 1)
        notice = json.loads(session.pending[0].text)
        self.assertEqual(notice["task_id"], "t42")
        self.assertEqual(notice["state"], "no_longer_running")
        self.assertEqual(notice["outcome"], "unverified")
        self.assertEqual(session.watched_task_id, "")
        self.assertEqual(session.observed_completions, 1)

    async def test_native_end_event_and_stop_do_not_generate_duplicate_completion(self):
        lease = await self.begin()
        await self.bridge.invoke("follow", {"control_id": lease}, self.ctx)
        session = self.bridge.active
        session.client.task = ""
        await self.bridge.observe_task_end(session, native_events="<event>task_finished id=t42 status=done</event>")
        self.assertEqual(len(session.pending), 0)
        await self.bridge.invoke("follow", {"control_id": lease}, self.ctx)
        await self.bridge.invoke("task_stop", {"control_id": lease}, self.ctx)
        await self.bridge.observe_task_end(session)
        self.assertEqual(len(session.pending), 0)

    async def test_async_stop_acceptance_is_observed_until_actual_retirement(self):
        lease = await self.begin()
        await self.bridge.invoke("follow", {"control_id": lease}, self.ctx)
        session = self.bridge.active
        original = session.client.call
        async def queued_stop(name, args, **kwargs):
            if name == "task_stop":
                return result({"success": True, "data": {"task_id": "t42"}, "message": "stop requested"})
            if name == "task_status" and session.client.task:
                return result({"success": True, "data": {"task_id": "t42", "state": "queued"}})
            return await original(name, args, **kwargs)
        session.client.call = queued_stop
        receipt = await self.bridge.invoke("task_stop", {"control_id": lease}, self.ctx)
        self.assertFalse(receipt.content["task_completed"])
        self.assertEqual(session.watched_task_id, "t42")
        await self.bridge.observe_task_end(session)
        self.assertFalse(session.pending)
        status = await self.bridge.invoke("task_status", {}, self.ctx)
        self.assertIn("保持世界运行", status.content["next_action"])
        session.client.task = ""
        session.last_task_probe_at = 0
        await self.bridge.observe_task_end(session)
        self.assertEqual(len(session.pending), 1)
        self.assertEqual(json.loads(session.pending[0].text)["outcome"], "unverified")

    async def test_old_task_observation_cannot_clear_newly_started_task(self):
        lease = await self.begin()
        await self.bridge.invoke("follow", {"control_id": lease}, self.ctx)
        session = self.bridge.active
        original = session.client.call

        async def switch_while_waiting(name, args, **kwargs):
            if name == "task_status":
                session.watched_task_id = "t43"
                return result({"success": True, "data": {"state": "idle"}})
            return await original(name, args, **kwargs)

        session.client.call = switch_while_waiting
        await self.bridge.observe_task_end(session)
        self.assertEqual(session.watched_task_id, "t43")
        self.assertEqual(len(session.pending), 0)

    async def test_empty_polls_do_not_request_turns_and_only_matching_binding_sees_events(self):
        lease = await self.begin()
        session = self.bridge.active
        bg = NS(events=NS(emit=AsyncMock(return_value=NS(status="accepted", dispatch_id="dispatch")),
                         status=AsyncMock(return_value=NS(complete=False))))
        await self.bridge.tick(bg)
        bg.events.emit.reset_mock()
        await self.bridge.tick(bg)
        bg.events.emit.assert_not_awaited()
        session.client.events = "task t42 completed"
        await self.bridge.tick(bg)
        await self.bridge.tick(bg)
        sent = bg.events.emit.call_args
        self.assertNotIn("task t42", str(sent))
        event = NS(data=sent.args[1])
        ctx = NS(scope_id="old", events=NS(unbind=AsyncMock()), request_turn=AsyncMock(),
                 observe=AsyncMock(return_value=NS(status="observed", version=1)),
                 turn_status=AsyncMock(return_value=NS(status="queued", complete=False)))
        await self.bridge.game_event(event, ctx)
        ctx.request_turn.assert_not_awaited()
        ctx.scope_id = session.scope_id
        ctx.request_turn.return_value = NS(status="queued", request_id="turn", reason="")
        await self.bridge.game_event(event, ctx)
        await self.bridge.game_event(event, ctx)
        self.assertEqual(ctx.request_turn.await_count, 1)
        self.assertEqual(ctx.request_turn.call_args.args[1]["control_id"], lease)
        self.assertTrue(session.pending[0].acknowledged)

    async def test_queued_events_merge_and_running_turn_observes_updates_without_extra_turn(self):
        await self.begin()
        session = self.bridge.active
        ctx = EventContext(session.scope_id)
        event = NS(data={"control_id": session.control_id})
        session.pending.append(EventBatch("first completed"))
        delivered = await self.bridge.game_event(event, ctx)
        self.assertIsInstance(delivered, dict)  # Do not hold the host event lane on a TurnReceipt.
        request_id = session.turn_request_id
        session.pending.append(EventBatch("second completed"))
        await self.begin(goal="new user goal")
        await self.bridge.game_event(event, ctx)
        self.assertEqual(session.turn_request_id, request_id)
        self.assertEqual(session.turns_coalesced, 1)
        self.assertEqual(ctx.snapshots[-1]["goal"], "new user goal")
        self.assertIn("first completed", ctx.snapshots[-1]["game_events"])
        self.assertIn("second completed", ctx.snapshots[-1]["game_events"])
        ctx.records[request_id].status = "running"
        session.pending.append(EventBatch("third completed"))
        await self.bridge.game_event(event, ctx)
        self.assertEqual(len(ctx.requests), 2)  # One initial request plus its merge, no second turn.
        self.assertIn("third completed", ctx.snapshots[-1]["game_events"])

        # The model finalized using version 2, before it saw the third event.
        ctx.records[request_id].status, ctx.records[request_id].complete = "completed", True
        ctx.records[request_id].observation_versions = {"game": 2}
        await self.bridge.game_event(event, ctx)
        self.assertEqual([item.text for item in session.pending], ["third completed"])
        self.assertEqual(ctx.snapshots[-1]["game_events"], "third completed")
        second = session.turn_request_id
        self.assertNotEqual(second, request_id)
        ctx.records[second].status, ctx.records[second].complete = "completed", True
        ctx.records[second].observation_versions = {"game": len(ctx.snapshots)}
        await self.bridge.game_event(event, ctx)
        await self.bridge.game_event(event, ctx)
        self.assertFalse(session.pending)
        self.assertFalse(session.turn_request_id)
        self.assertEqual(len(ctx.records), 2)

    async def test_preemption_resumes_but_stop_failure_and_uncertainty_do_not(self):
        for reason, uncertain, resumes in (
            ("addressed_input_preempts_optional_turn", False, True),
            ("user_stopped", False, False),
            ("agent_turn_model_incomplete", False, False),
            ("addressed_input_preempts_optional_turn", True, False),
        ):
            with self.subTest(reason=reason, uncertain=uncertain):
                self.bridge = MinecraftBridge(client_factory=FakeClient)
                await self.begin()
                session = self.bridge.active
                ctx = EventContext(session.scope_id)
                event = NS(data={"control_id": session.control_id})
                session.pending.append(EventBatch("task end; verify result"))
                await self.bridge.game_event(event, ctx)
                current = ctx.records[session.turn_request_id]
                current.status, current.complete, current.reason = "cancelled", True, reason
                session.uncertain = uncertain
                await self.bridge.game_event(event, ctx)
                self.assertEqual(session.paused, not resumes)
                self.assertEqual(len(ctx.records), 2 if resumes else 1)
                self.assertFalse(any(call[0] == "task_stop" for call in session.client.calls))

    async def test_full_event_buffer_can_still_settle_model_receipt_and_resume_polling(self):
        await self.begin()
        session = self.bridge.active
        ctx = EventContext(session.scope_id)
        event = NS(data={"control_id": session.control_id})
        session.pending.extend(EventBatch(f"event-{index}") for index in range(16))
        await self.bridge.game_event(event, ctx)
        self.bridge.cleanup_needed = False
        bg = NS(events=NS(emit=AsyncMock(return_value=NS(status="accepted", dispatch_id="dispatch")),
                         status=AsyncMock(return_value=NS(status="completed", complete=True, reason=""))))
        session.last_dispatch_at = 0
        await self.bridge.tick(bg)
        bg.events.emit.assert_awaited()  # The buffer limit must not block receipt checks.
        current = ctx.records[session.turn_request_id]
        current.status, current.complete, current.observation_versions = "completed", True, {"game": 1}
        await self.bridge.game_event(event, ctx)
        self.assertEqual(len(session.pending), 0)
        session.client.calls.clear()
        await self.bridge.tick(bg)
        self.assertTrue(any(call[0] == "get_events" for call in session.client.calls))

    async def test_game_semantic_rejection_is_not_advertised_as_retryable(self):
        lease = await self.begin()
        client = self.bridge.active.client
        original = client.call
        async def reject(name, args, **kwargs):
            if name == "build":
                return {"isError": True, "content": [{"type": "text", "text": "not a placeable block"}]}
            return await original(name, args, **kwargs)
        client.call = reject
        rejected = await self.bridge.invoke("build", {"control_id": lease}, self.ctx)
        self.assertTrue(rejected.is_error)
        self.assertFalse(rejected.content["retryable"])
        self.assertIn("not a placeable block", str(rejected.content["game_result"]))

    async def test_short_event_lane_does_not_revoke_scope_before_remote_stop_finishes(self):
        await self.begin()
        session = self.bridge.active
        entered, release = asyncio.Event(), asyncio.Event()
        async def stop_body(*args, **kwargs):
            entered.set()
            await release.wait()
            return {"body_stopped": True, "status": "stopped"}
        self.bridge._stop_body = stop_body
        stopping = asyncio.create_task(self.bridge.stop(self.ctx))
        await entered.wait()
        event = NS(data={"control_id": session.control_id})
        ctx = EventContext(session.scope_id)
        await self.bridge.game_event(event, ctx)
        ctx.events.unbind.assert_not_awaited()
        release.set()
        stopped = await stopping
        self.assertTrue(stopped.content["body_stopped"])
        await self.bridge.game_event(event, ctx)
        ctx.events.unbind.assert_awaited_once()

    async def test_poll_failure_pauses_without_fabricated_success(self):
        await self.begin()
        session = self.bridge.active
        session.client.call = AsyncMock(side_effect=MinecraftError("minecraft_unreachable"))
        bg = NS(events=NS(emit=AsyncMock()))
        await self.bridge.tick(bg)
        self.assertTrue(session.paused)
        self.assertEqual(session.error, "minecraft_unreachable")

    async def test_expiry_only_stops_task_owned_by_session(self):
        await self.begin()
        session = self.bridge.active
        session.owned_task_id = "t1"
        session.client.task = "t2"
        session.deadline = 0
        await self.bridge.tick(NS(events=NS(emit=AsyncMock())))
        self.assertIsNone(self.bridge.active)
        self.assertEqual(session.client.task, "t2")
        self.assertFalse(self.bridge.last_stop["body_stopped"])

    async def test_takeover_retains_owned_task_for_expiry_and_uncertain_state(self):
        lease = await self.begin()
        await self.bridge.invoke("follow", {"control_id": lease}, self.ctx)
        self.bridge.active.uncertain = True
        await self.begin(context(session="other"), take_over=True)
        session = self.bridge.active
        self.assertEqual(session.owned_task_id, "t42")
        self.assertTrue(session.uncertain)
        session.deadline = 0
        await self.bridge.tick(NS(events=NS(emit=AsyncMock())))
        self.assertEqual(session.client.task, "")


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_authenticated_handshake_redaction_and_no_write_retry(self):
        calls = []
        def handle(request):
            body = json.loads(request.content)
            calls.append(body)
            self.assertEqual(request.headers["authorization"], "Bearer test-secret-token")
            method = body["method"]
            if method == "initialize":
                response = {"serverInfo": {"name": "numen-mcp"}, "protocolVersion": "2024-11-05"}
            elif method == "notifications/initialized":
                return httpx.Response(202)
            elif body["params"]["name"] == "say":
                raise httpx.ReadTimeout("secret must not surface")
            else:
                response = result({"success": True, "message": "test-secret-token"})
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": response})
        client = NumenClient(GameConnection.parse(VALUES), transport=httpx.MockTransport(handle))
        try:
            value = await client.call("get_self_status", {"companion": "Reimu"})
            self.assertNotIn("test-secret-token", str(value))
            with self.assertRaises(MinecraftError) as error:
                await client.call("say", {"text": "x"}, effectful=True)
            self.assertTrue(error.exception.uncertain)
            self.assertEqual(sum(c.get("params", {}).get("name") == "say" for c in calls), 1)
        finally:
            await client.aclose()

    def test_connection_rejects_remote_credentials_in_url_and_redirect_targets(self):
        for endpoint in ("http://example.com:8765/mcp", "http://u:p@localhost:8765/mcp",
                         "http://localhost:8765/mcp?key=x", "https://localhost:8765/mcp"):
            with self.subTest(endpoint=endpoint), self.assertRaises(MinecraftError):
                GameConnection.parse({**VALUES, "endpoint": endpoint})

    def test_sdk_principal_roundtrip_and_catalog_guards(self):
        original = context(profile="qq_group_shared_test").invocation
        self.assertEqual(invocation_context_from_wire(invocation_context_to_wire(original)), original)
        plugin = create_plugin()
        self.assertEqual(len(plugin._functions), 44)
        ids = {item.descriptor.id for item in plugin._functions}
        self.assertNotIn("akane.minecraft.get_events", ids)
        self.assertIn("akane.minecraft.create_companion", ids)
        upstream = json.loads((Path(__file__).resolve().parents[1] /
            "plugins/akane_minecraft/src/akane_minecraft/numen-tools.json").read_text(encoding="utf-8"))
        self.assertEqual({"akane.minecraft." + item["name"] for item in upstream} -
                         {"akane.minecraft.get_events"}, ids - {
                             "akane.minecraft.status", "akane.minecraft.start_session", "akane.minecraft.stop_session"})
        for item in plugin._functions:
            self.assertIs(item.descriptor.raw["owner_only"], True)
            schema = item.descriptor.input_schema
            if "control_id" in schema["properties"]:
                self.assertNotIn("companion", schema["properties"])
        self.assertIn("akane.minecraft.list_companions", ids)
        self.assertNotIn("owner_profile", plugin.manifest.connections[0].schema["properties"])
        self.assertNotIn(VALUES["token"], str(plugin.manifest))

    def test_host_bridge_derives_group_sender_and_never_gives_global_authority(self):
        handler = object.__new__(PluginCapabilityToolHandler)
        handler._conversation_ref_issuer = lambda ctx: "signed-reference"
        execution = ToolExecutionContext("qq_group_shared_test", "group", 0, {},
            client_mode="qq", request_context={"actor_profile_user_id": "master"})
        invocation = handler._invocation_context(execution)
        self.assertEqual(invocation.profile_user_id, "qq_group_shared_test")
        self.assertEqual(invocation.authorization_profile_user_id, "master")
        execution.request_context["actor_profile_user_id"] = "qq_stranger"
        self.assertEqual(handler._invocation_context(execution).authorization_profile_user_id, "qq_stranger")
        execution = replace(execution, global_scope=True)
        self.assertEqual(handler._invocation_context(execution).authorization_profile_user_id, "")


if __name__ == "__main__":
    unittest.main()
