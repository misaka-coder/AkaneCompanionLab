"""Actual desktop and OneBot routes publish to the public scoped event API."""

import asyncio
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from akane_plugin import EventBinding, Plugin, PluginInvocationContext, ToolContext
from companion_v01.plugin_api import (
    DIRECT_CONVERSATION_EVENT,
    GROUP_CONVERSATION_EVENT,
    POKE_CONVERSATION_EVENT,
)
from companion_v01.plugin_conversation_refs import PluginConversationReferenceAuthority
from companion_v01.deployment_security import QQChannelRuntimeConfig
from companion_v01.qq_gateway import NapCatQQGateway
from companion_v01.routes.think import build_think_router
from companion_v01.routes.qq import build_qq_router
from tests import test_plugin_events as legacy_tests
from tests import test_plugin_events_v2 as event_tests
from tests import test_plugin_turn_requests as turn_tests


class ChannelEventTests(unittest.IsolatedAsyncioTestCase):
    start = event_tests.PluginEventV2Tests.start

    async def observer(self, event_type):
        self.received = []
        self.done = asyncio.Event()
        plugin = Plugin("example.channel-observer", permissions=("context.observe",))

        @plugin.tool
        async def bind(ctx: ToolContext) -> EventBinding:
            return await ctx.events.bind("example.channel-observer.inbound")

        @plugin.on(event_type, name="inbound", sources=("@host",), scope="conversation")
        async def inbound(event, ctx):
            self.received.append(event)
            result = await ctx.observe("latest-message", event.data)
            self.done.set()
            return result

        await self.start(plugin)
        self.refs = PluginConversationReferenceAuthority(self.root / "refs.key", instance_id="test")

    async def bind(self, context):
        result = await self.host.invoke("example.channel-observer.bind", {}, context=context)
        self.assertFalse(result.is_error, result)
        self.assertEqual(result.content["status"], "bound")

    async def post(self, app, route, payload):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(route, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        await asyncio.wait_for(self.done.wait(), 3)

    async def test_desktop_route_public_on_receives_real_message_without_extra_turn(self):
        await self.observer(DIRECT_CONVERSATION_EVENT)
        context = PluginInvocationContext("owner", "session", "desktop_pet", character_pack_id="akane",
            conversation_ref=self.refs.issue_desktop(profile_user_id="owner", session_id="session", character_pack_id="akane"))
        await self.bind(context)
        engine = legacy_tests._RouteEngine()
        app = FastAPI()
        app.include_router(build_think_router(engine=engine, public_guard=legacy_tests._Guard(),
            runtime_metrics=legacy_tests._Metrics(), log_event=lambda *a, **k: None,
            plugin_event_broker_provider=lambda: self.broker, plugin_conversation_ref_issuer=self.refs.issue_desktop))
        await self.post(app, "/think_once", {"user_id": "session", "real_user_id": "owner", "character_pack_id": "akane",
            "actor_stable_id": "desktop:owner", "client_mode": "desktop_pet", "message": "外面下雨了", "source_message_id": "desk-1"})
        event, = self.received
        self.assertEqual(event.source, "@host")
        self.assertEqual(event.data["text"], "外面下雨了")
        self.assertEqual(event.data["channel"], "desktop_pet")
        self.assertEqual(len(engine.turns), 1)
        self.assertEqual(engine.timeline, [])

        self.assertIn("外面下雨了", self.broker.observations.prompt_context(profile_user_id="owner", session_id="session", character_pack_id="akane"))

    async def test_qq_route_preserves_public_message_chain_and_typed_data(self):
        await self.observer(GROUP_CONVERSATION_EVENT)
        channel = QQChannelRuntimeConfig(enabled=True, profile_ref="qq.test", bot_id="100", onebot_http_url="http://127.0.0.1:3001",
            webhook_secret="", onebot_access_token="", require_webhook_auth=False, require_self_id=True)
        gateway = NapCatQQGateway(channel_config=channel, wake_words=("Akane",))
        session, profile = gateway.resolve_identity(user_id=300, group_id=200)
        character = gateway.resolve_character_pack_id(session)
        context = PluginInvocationContext(profile, session, "qq", character_pack_id=character,
            conversation_ref=self.refs.issue_qq(profile_user_id=profile, session_id=session, character_pack_id=character,
                user_id=300, group_id=200, actor_stable_id="qq:300"))
        await self.bind(context)
        engine = legacy_tests._RouteEngine()
        app = FastAPI()
        app.include_router(build_qq_router(engine=engine, config_module=SimpleNamespace(QQ_BRIDGE_ENABLED=True, QQ_STREAM_REPLIES_ENABLED=False),
            qq_gateway=gateway, runtime_metrics=legacy_tests._Metrics(), channel_config=channel,
            logger=SimpleNamespace(exception=lambda *a, **k: None), log_event=lambda *a, **k: None,
            plugin_event_broker_provider=lambda: self.broker, plugin_conversation_ref_issuer=self.refs.issue_qq))
        payload = {"post_type": "message", "message_type": "group", "self_id": "100", "group_id": "200", "user_id": "300",
            "message_id": "qq-1", "sender": {"nickname": "Olivia"}, "time": int(time.time()), "message": [
                {"type": "reply", "data": {"id": "quoted-1"}}, {"type": "at", "data": {"qq": "100"}},
                {"type": "text", "data": {"text": "Akane 晚上好"}},
            ]}
        with patch("companion_v01.onebot_transport.requests.Session.request", return_value=legacy_tests._Response()):
            await self.post(app, "/api/qq/napcat/event", payload)
        event, = self.received
        self.assertEqual(event.source, "@host")
        self.assertEqual(event.data["actor"]["id"], "300")
        self.assertEqual(event.data["reply_to"], "quoted-1")
        self.assertIsInstance(event.data["has_text_content"], bool)
        self.assertEqual([part["kind"] for part in event.data["parts"]], ["reply", "mention", "text"])
        self.assertEqual(len(engine.turns), 1)
        self.assertEqual(engine.timeline, [])

    async def test_qq_poke_notice_reaches_public_subscribers_as_a_typed_event(self):
        await self.observer(POKE_CONVERSATION_EVENT)
        channel = QQChannelRuntimeConfig(enabled=True, profile_ref="qq.test", bot_id="100", onebot_http_url="http://127.0.0.1:3001",
            webhook_secret="", onebot_access_token="", require_webhook_auth=False, require_self_id=True)
        gateway = NapCatQQGateway(channel_config=channel, wake_words=("Akane",))
        session, profile = gateway.resolve_identity(user_id=300, group_id=200)
        character = gateway.resolve_character_pack_id(session)
        context = PluginInvocationContext(profile, session, "qq", character_pack_id=character,
            conversation_ref=self.refs.issue_qq(profile_user_id=profile, session_id=session, character_pack_id=character,
                user_id=300, group_id=200, actor_stable_id="qq:300"))
        await self.bind(context)
        engine = legacy_tests._RouteEngine()
        app = FastAPI()
        app.include_router(build_qq_router(engine=engine, config_module=SimpleNamespace(QQ_BRIDGE_ENABLED=True, QQ_STREAM_REPLIES_ENABLED=False),
            qq_gateway=gateway, runtime_metrics=legacy_tests._Metrics(), channel_config=channel,
            logger=SimpleNamespace(exception=lambda *a, **k: None), log_event=lambda *a, **k: None,
            plugin_event_broker_provider=lambda: self.broker, plugin_conversation_ref_issuer=self.refs.issue_qq))
        payload = {"post_type": "notice", "notice_type": "poke", "sub_type": "poke", "self_id": "100",
            "group_id": "200", "user_id": "300", "target_id": "100", "time": int(time.time()),
            "sender": {"nickname": "Olivia"}}
        with patch("companion_v01.onebot_transport.requests.Session.request", return_value=legacy_tests._Response()):
            await self.post(app, "/api/qq/napcat/event", payload)
        event, = self.received
        self.assertEqual(event.source, "@host")
        self.assertEqual(event.data["event_kind"], "poke")
        self.assertEqual(event.data["channel"], "qq")
        self.assertEqual(event.data["conversation_kind"], "group")
        self.assertEqual(event.data["conversation_id"], "200")
        self.assertEqual(event.data["actor_id"], "300")
        self.assertEqual(event.data["actor_label"], "Olivia")
        self.assertIsInstance(event.data["outcome_kind"], str)
        self.assertIsInstance(event.data["status"], str)
        # The host already applied its own care effect; publishing the event
        # must not create an extra model turn by itself.
        self.assertEqual(len(engine.turns), 1)
        self.assertEqual(engine.timeline, [])


class QQTurnRequestTests(unittest.IsolatedAsyncioTestCase):
    start_host = event_tests.PluginEventV2Tests.start
    start = turn_tests.TurnRequestTests.start
    call = turn_tests.TurnRequestTests.call
    settled = turn_tests.TurnRequestTests.settled

    async def test_public_request_reaches_normal_qq_stream_and_send(self):
        await self.start()
        channel = QQChannelRuntimeConfig(enabled=True, profile_ref="qq.test", bot_id="100", onebot_http_url="http://127.0.0.1:3001",
            webhook_secret="", onebot_access_token="", require_webhook_auth=False, require_self_id=True)
        gateway = NapCatQQGateway(channel_config=channel)
        session, profile = gateway.resolve_identity(user_id=300)
        character = gateway.resolve_character_pack_id(session)
        self.context = PluginInvocationContext(profile, session, "qq", character_pack_id=character,
            conversation_ref=self.refs.issue_qq(profile_user_id=profile, session_id=session, character_pack_id=character, user_id=300))
        engine = legacy_tests._RouteEngine()
        build_qq_router(engine=engine, config_module=SimpleNamespace(QQ_BRIDGE_ENABLED=True, QQ_STREAM_REPLIES_ENABLED=False),
            qq_gateway=gateway, runtime_metrics=legacy_tests._Metrics(), channel_config=channel,
            logger=SimpleNamespace(exception=lambda *a, **k: None), log_event=lambda *a, **k: None,
            turn_coordinator=self.coordinator, session_work_queue=self.queue, plugin_turn_router=self.router,
            plugin_agent_event_handler_registrar=self.router.register_channel)
        data = {"board": [False, None, 0, 0.5]}
        with patch("companion_v01.onebot_transport.requests.Session.request", return_value=legacy_tests._Response()) as network:
            request = await self.call("request", {"data": data})
            final = await self.settled(request["request_id"])
        self.assertEqual(final["status"], "completed", final)
        self.assertEqual(final["model_status"], "completed")
        self.assertEqual(final["delivery_status"], "sent")
        self.assertEqual(len(engine.turns), 1)
        self.assertEqual(engine.turns[0]["plugin_external_event"]["data"], data)
        self.assertIn('"board": [', engine.turns[0]["extra_context"])
        self.assertGreaterEqual(network.call_count, 1)
