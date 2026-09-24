from __future__ import annotations

import asyncio
import tempfile
import unittest
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import httpx
from capcore import CapabilityResult
from fastapi import FastAPI

from akane_plugin import DIRECT_CONVERSATION_EVENT, EventSubscription, GROUP_CONVERSATION_EVENT, Plugin
from companion_v01.instance_profile import PluginSelection
from companion_v01.deployment_security import QQChannelRuntimeConfig
from companion_v01.plugin_api import PluginInvocationContext
from companion_v01.plugin_contribution_policy import TrustedStatefulPluginContributionPolicy
from companion_v01.plugin_events import PluginEventBroker, _PluginEventRegistration
from companion_v01.plugin_host import PluginHost
from companion_v01.qq_gateway import NapCatQQGateway
from companion_v01.routes.qq import build_qq_router
from companion_v01.routes.think import build_think_router
from tests.test_plugin_host import FakeEntryPoint


PLUGIN_ID = "akane.test.events"


def _subscription(event_type: str, *, persistence: str = "none", name: str = "observer") -> EventSubscription:
    return EventSubscription(f"{PLUGIN_ID}.{name}", event_type, persistence=persistence)


class _Recorder:
    """Minimal V2 executor: record the delivered event, never touch a model."""

    def __init__(self) -> None:
        self.events: list[Any] = []
        self.scopes: list[str] = []

    async def __call__(self, registration, event, *, context, scope_id, delivery_id,
                       origin_context, invocation_out=None, keep_scope_open=False):
        del registration, context, delivery_id, origin_context, keep_scope_open
        self.events.append(event)
        self.scopes.append(scope_id)
        return CapabilityResult(is_error=False, status="ok", content=event.data)


async def _settle(broker: PluginEventBroker, receipt):
    async with asyncio.timeout(5):
        while not receipt.complete:
            receipt = await broker.receipt(receipt.dispatch_id)
            await asyncio.sleep(0.01)
    return receipt


class PluginEventBrokerTests(unittest.IsolatedAsyncioTestCase):
    async def test_delivered_event_keeps_the_public_payload_and_host_identity(self) -> None:
        recorder = _Recorder()
        payload = {"desktop": "hello"}
        broker = PluginEventBroker(
            (_PluginEventRegistration(PLUGIN_ID, DIRECT_CONVERSATION_EVENT, _subscription(DIRECT_CONVERSATION_EVENT)),),
            executor=recorder,
        )

        receipt = await broker.emit(DIRECT_CONVERSATION_EVENT, payload, event_key="desktop-event-1")
        receipt = await _settle(broker, receipt)

        self.assertEqual(receipt.status, "completed", receipt)
        event = recorder.events[0]
        self.assertEqual(event.data, payload)
        self.assertTrue(event.event_id)
        self.assertNotEqual(event.event_id, "desktop-event-1")
        self.assertEqual(event.source, "@host")
        self.assertEqual(event.version, 1)
        self.assertTrue(event.received_at_ms)

    async def test_subscription_declared_timeline_records_once_per_publication(self) -> None:
        recorded: list[dict[str, Any]] = []
        recorder = _Recorder()
        broker = PluginEventBroker(
            (
                _PluginEventRegistration(PLUGIN_ID, GROUP_CONVERSATION_EVENT,
                                         _subscription(GROUP_CONVERSATION_EVENT, persistence="timeline", name="first")),
                _PluginEventRegistration(PLUGIN_ID, GROUP_CONVERSATION_EVENT,
                                         _subscription(GROUP_CONVERSATION_EVENT, persistence="timeline", name="second")),
            ),
            executor=recorder,
        )
        broker.bind_timeline_recorder(
            lambda payload: recorded.append(payload) or {"ok": True, "status": "recorded"}
        )

        receipt = await broker.emit(
            GROUP_CONVERSATION_EVENT, {"text": "hi"},
            context=PluginInvocationContext("master", "session-1", "qq", conversation_ref="ref-1"),
        )
        receipt = await _settle(broker, receipt)

        self.assertEqual(receipt.status, "completed", receipt)
        self.assertEqual(len(recorder.events), 2, recorder.events)
        self.assertEqual(len(recorded), 1, recorded)
        self.assertEqual(recorded[0]["event"]["event_type"], GROUP_CONVERSATION_EVENT)
        self.assertEqual(recorded[0]["user_id"], "session-1")
        self.assertEqual(recorded[0]["real_user_id"], "master")

    async def test_failed_timeline_record_reports_the_real_reason(self) -> None:
        recorder = _Recorder()
        broker = PluginEventBroker(
            (_PluginEventRegistration(PLUGIN_ID, GROUP_CONVERSATION_EVENT,
                                      _subscription(GROUP_CONVERSATION_EVENT, persistence="timeline")),),
            executor=recorder,
        )
        broker.bind_timeline_recorder(lambda payload: {"ok": False, "reason": "memcore_not_enabled"})

        receipt = await broker.emit(
            GROUP_CONVERSATION_EVENT, {"text": "hi"},
            context=PluginInvocationContext("master", "session-1", "qq", conversation_ref="ref-1"),
        )
        receipt = await _settle(broker, receipt)

        self.assertEqual(receipt.status, "failed", receipt)
        self.assertEqual(receipt.deliveries[0].reason, "memcore_not_enabled")


class PluginEventHostTests(unittest.IsolatedAsyncioTestCase):
    async def test_sdk_subscription_activates_and_publishes_a_real_snapshot(self) -> None:
        plugin = Plugin(PLUGIN_ID, permissions=("event.subscribe",))

        @plugin.on(GROUP_CONVERSATION_EVENT)
        async def observe(event, ctx):
            return event.data

        host = PluginHost(
            (PluginSelection(plugin_id=PLUGIN_ID, enabled=True),),
            contribution_policy=TrustedStatefulPluginContributionPolicy(),
            entry_points_provider=lambda: (FakeEntryPoint(PLUGIN_ID, lambda: plugin),),
        )
        try:
            status = await host.start()
            broker = host.build_event_broker()

            self.assertEqual(status["status"], "active", status)
            self.assertEqual(status["capability_count"], 0)
            self.assertEqual(broker.registered_event_types, (GROUP_CONVERSATION_EVENT,))
            snapshot = status["plugins"][0]["contribution_snapshot"]
            self.assertEqual(snapshot["types"], ["event_handlers"])
            self.assertEqual(snapshot["event_subscriptions"][0]["subscription_id"], f"{PLUGIN_ID}.observe")
        finally:
            await host.stop()


class _Metrics:
    def observe_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def incr(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _Guard:
    def __init__(self) -> None:
        self.released = 0

    def try_acquire(self) -> Any:
        return SimpleNamespace(allowed=True, acquired=True, reason="", message="")

    def release(self) -> None:
        self.released += 1


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"status": "ok"}


class _RouteEngine:
    desktop_pet_character_resources = None

    def __init__(self) -> None:
        self.turns: list[dict[str, Any]] = []
        self.timeline: list[dict[str, Any]] = []

    def record_plugin_timeline_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.timeline.append(dict(payload))
        return {"ok": True, "status": "recorded"}

    def prefetch_remote_media_links_for_message(self, **_kwargs: Any) -> dict[str, Any]:
        return {}

    def process_turn_stream(self, payload: dict[str, Any]):
        self.turns.append(dict(payload))
        yield {
            "type": "final_ui",
            "payload": {
                "reply_medium": "text",
                "speech": "收到事件",
                "speech_segments": ["收到事件"],
                "tool_events": [],
            },
        }

    def process_turn(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.turns.append(dict(payload))
        return {"status": "ok", "emotion": "normal", "speech": "收到事件", "_debug": {}}

    def mark_generated_file_delivery(self, **_kwargs: Any) -> dict[str, Any]:
        return {"ok": True}


class _EventPluginHarness(unittest.IsolatedAsyncioTestCase):
    """Real SDK subscription over a real broker, with the route engine recording."""

    event_type = GROUP_CONVERSATION_EVENT
    persistence = "none"

    async def start_host(self) -> None:
        self.seen: list[Any] = []
        plugin = Plugin(PLUGIN_ID, permissions=("event.subscribe",))

        @plugin.on(self.event_type, persistence=self.persistence)
        async def observe(event, ctx):
            self.seen.append(event)
            return event.data

        self.engine = _RouteEngine()
        self.host = PluginHost(
            (PluginSelection(plugin_id=PLUGIN_ID, enabled=True),),
            contribution_policy=TrustedStatefulPluginContributionPolicy(),
            entry_points_provider=lambda: (FakeEntryPoint(PLUGIN_ID, lambda: plugin),),
        )
        status = await self.host.start()
        self.assertEqual(status["status"], "active", status)
        self.broker = self.host.build_event_broker()
        self.broker.bind_timeline_recorder(self.engine.record_plugin_timeline_event)
        self.addAsyncCleanup(self.host.stop)

    async def wait_for(self, predicate) -> None:
        async with asyncio.timeout(5):
            while not predicate():
                await asyncio.sleep(0.01)

    async def _post(self, app: FastAPI, path: str, payload: dict[str, Any]):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(path, json=payload)


class PluginEventQQBridgeTests(_EventPluginHarness):
    def _channel(self) -> QQChannelRuntimeConfig:
        return QQChannelRuntimeConfig(
            enabled=True,
            profile_ref="qq.test",
            bot_id="100",
            onebot_http_url="http://127.0.0.1:3001",
            webhook_secret="",
            onebot_access_token="",
            require_webhook_auth=False,
            require_self_id=True,
        )

    def _client(self, **config: Any) -> FastAPI:
        channel = self._channel()
        app = FastAPI()
        app.include_router(
            build_qq_router(
                engine=self.engine,
                config_module=SimpleNamespace(QQ_BRIDGE_ENABLED=True, QQ_STREAM_REPLIES_ENABLED=False, **config),
                qq_gateway=NapCatQQGateway(channel_config=channel, wake_words=("Akane",)),
                runtime_metrics=_Metrics(),
                channel_config=channel,
                logger=SimpleNamespace(exception=lambda *_args, **_kwargs: None),
                log_event=lambda *_args, **_kwargs: None,
                plugin_event_broker_provider=lambda: self.broker,
            )
        )
        return app

    async def test_private_message_publishes_a_direct_event_with_channel_facts(self) -> None:
        self.event_type = DIRECT_CONVERSATION_EVENT
        await self.start_host()
        app = self._client()
        event = {
            "post_type": "message",
            "message_type": "private",
            "self_id": "100",
            "user_id": "300",
            "message_id": "private-event-1",
            "sender": {"nickname": "Olivia"},
            "raw_message": "晚上好",
            "time": int(time.time()),
        }

        with patch("companion_v01.onebot_transport.requests.Session.request", return_value=_Response()):
            response = await self._post(app, "/api/qq/napcat/event", event)

        self.assertEqual(response.status_code, 200)
        await self.wait_for(lambda: bool(self.seen))
        self.assertEqual(self.seen[0].event_type, DIRECT_CONVERSATION_EVENT)
        self.assertEqual(self.seen[0].source, "@host")
        data = self.seen[0].data
        self.assertEqual(data["conversation"], {"kind": "private", "id": "300"})
        self.assertEqual(data["actor"]["id"], "300")
        self.assertEqual(data["text"], "晚上好")

    async def test_group_message_without_turn_request_stays_silent_and_records_timeline(self) -> None:
        self.persistence = "timeline"
        await self.start_host()
        app = self._client(QQ_GROUP_PASSIVE_MEMORY_MODE="all")
        event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": "100",
            "group_id": "200",
            "user_id": "300",
            "message_id": "bridge-event-1",
            "sender": {"nickname": "Olivia"},
            "raw_message": "今晚风很大",
            "time": int(time.time()),
        }

        with patch("companion_v01.onebot_transport.requests.Session.request", return_value=_Response()):
            response = await self._post(app, "/api/qq/napcat/event", event)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.engine.turns, [], response.json())
        self.assertEqual(response.json()["reason"], "group_passive_observed")
        await self.wait_for(lambda: bool(self.engine.timeline))
        self.assertEqual(self.engine.timeline[0]["event"]["event_type"], GROUP_CONVERSATION_EVENT)
        self.assertEqual(self.engine.timeline[0]["event"]["source"], "@host")


class PluginEventDesktopBridgeTests(_EventPluginHarness):
    async def test_direct_message_publishes_a_direct_event_and_records_the_declared_timeline(self) -> None:
        self.event_type = DIRECT_CONVERSATION_EVENT
        self.persistence = "timeline"
        await self.start_host()
        guard = _Guard()
        logs: list[tuple[str, dict[str, Any]]] = []
        app = FastAPI()
        app.include_router(
            build_think_router(
                engine=self.engine,
                public_guard=guard,
                runtime_metrics=_Metrics(),
                log_event=lambda name, **data: logs.append((name, data)),
                plugin_event_broker_provider=lambda: self.broker,
            )
        )

        response = await self._post(
            app,
            "/think_once",
            {
                "user_id": "desktop",
                "real_user_id": "master",
                "actor_stable_id": "desktop:master",
                "actor_display_name": "伙伴",
                "character_pack_id": "reimu",
                "client_mode": "desktop_pet",
                "message": "外面下雨了",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(guard.released, 1)
        await self.wait_for(lambda: bool(self.seen))
        self.assertEqual(self.seen[0].event_type, DIRECT_CONVERSATION_EVENT)
        data = self.seen[0].data
        self.assertEqual(data["conversation_kind"], "direct")
        self.assertEqual(data["conversation_id"], "desktop")
        self.assertEqual(data["actor_id"], "desktop:master")
        self.assertEqual(data["text"], "外面下雨了")
        self.assertEqual(self.engine.turns[0]["message"], "外面下雨了")
        await self.wait_for(lambda: bool(self.engine.timeline))
        self.assertEqual(self.engine.timeline[0]["user_id"], "desktop")
        self.assertEqual(self.engine.timeline[0]["real_user_id"], "master")
        self.assertEqual(self.engine.timeline[0]["character_pack_id"], "reimu")
        self.assertEqual(self.engine.timeline[0]["event"]["event_type"], DIRECT_CONVERSATION_EVENT)


if __name__ == "__main__":
    unittest.main()
