from __future__ import annotations

import unittest
import time
from types import SimpleNamespace
from typing import Any, Callable
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from channelcore_onebot import normalize_inbound_event

from companion_v01.instance_profile import PluginSelection
from companion_v01.deployment_security import QQChannelRuntimeConfig
from companion_v01.plugin_api import (
    AKANE_PLUGIN_API_VERSION,
    EVENT_SUBSCRIBE_PERMISSION,
    PluginEventEnvelope,
    PluginEventResult,
    PluginExternalEvent,
    PluginManifest,
)
from companion_v01.plugin_contribution_policy import TrustedStatefulPluginContributionPolicy
from companion_v01.plugin_events import PluginEventBroker, _PluginEventRegistration
from companion_v01.plugin_host import PluginHost
from companion_v01.qq_gateway import NapCatQQGateway
from companion_v01.routes.qq import build_qq_router


PLUGIN_ID = "akane.test.events"


class _Handler:
    def __init__(self, result: PluginEventResult) -> None:
        self.result = result
        self.received: list[PluginEventEnvelope] = []

    async def handle_event(self, event: PluginEventEnvelope) -> PluginEventResult:
        self.received.append(event)
        return self.result


def _envelope() -> PluginEventEnvelope:
    inbound = normalize_inbound_event(
        {
            "post_type": "message",
            "message_type": "group",
            "self_id": "100",
            "group_id": "200",
            "user_id": "300",
            "message_id": "event-1",
            "sender": {"nickname": "Olivia"},
            "message": [
                {"type": "reply", "data": {"id": "quoted-1"}},
                {"type": "at", "data": {"qq": "100"}},
                {"type": "text", "data": {"text": "看这个"}},
                {"type": "image", "data": {"file": "image.jpg", "url": "https://example.invalid/a"}},
            ],
            "time": int(time.time()),
        },
        bot_account_id="100",
    ).message
    assert inbound is not None
    return PluginEventEnvelope(
        event_id=inbound.event_id,
        event_type="channel.qq.inbound",
        source="channelcore-onebot",
        occurred_at=int(inbound.timestamp),
        subject="qq-group:200",
        channel_message=inbound,
    )


class PluginEventBrokerTests(unittest.IsolatedAsyncioTestCase):
    async def test_internal_observer_keeps_full_channelcore_chain_and_emits_nothing(self) -> None:
        handler = _Handler(PluginEventResult())
        broker = PluginEventBroker(
            (_PluginEventRegistration(PLUGIN_ID, "channel.qq.inbound", handler),)
        )

        result = await broker.dispatch(_envelope())

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "observed")
        self.assertEqual(result.current_turn_events, ())
        self.assertEqual(result.timeline_events, ())
        self.assertFalse(result.request_agent_turn)
        inbound = handler.received[0].channel_message
        self.assertIsNotNone(inbound)
        assert inbound is not None
        self.assertEqual([part.kind for part in inbound.chain.parts], ["reply", "mention", "text", "attachment"])
        self.assertEqual(inbound.reply_to.message_id, "quoted-1")
        self.assertTrue(inbound.mentioned_bot)
        self.assertEqual(inbound.attachments[0].kind, "image")

    async def test_current_turn_and_timeline_are_distinct_typed_outputs(self) -> None:
        current = _Handler(
            PluginEventResult(
                delivery="current_turn",
                event=PluginExternalEvent(
                    event_type="game.hp_changed",
                    fields=(("hp", "17"),),
                    source="plugin.game",
                ),
            )
        )
        timeline = _Handler(
            PluginEventResult(
                delivery="timeline",
                event=PluginExternalEvent(
                    event_type="game.boss_defeated",
                    fields=(("name", "Scarlet"),),
                    source="plugin.game",
                ),
            )
        )
        broker = PluginEventBroker(
            (
                _PluginEventRegistration("plugin.current", "channel.qq.inbound", current),
                _PluginEventRegistration("plugin.timeline", "channel.qq.inbound", timeline),
            )
        )

        result = await broker.dispatch(_envelope())

        self.assertTrue(result.ok)
        self.assertTrue(result.request_agent_turn)
        self.assertEqual(result.current_turn_events[0].event_type, "game.hp_changed")
        self.assertEqual(result.timeline_events[0].event_type, "game.boss_defeated")

    async def test_handler_failure_isolated_and_does_not_discard_sibling_result(self) -> None:
        class Broken:
            async def handle_event(self, event: PluginEventEnvelope) -> PluginEventResult:
                del event
                raise RuntimeError("boom")

        working = _Handler(
            PluginEventResult(
                delivery="timeline",
                event=PluginExternalEvent(event_type="calendar.done", fields=(("task", "x"),)),
            )
        )
        broker = PluginEventBroker(
            (
                _PluginEventRegistration("broken", "channel.qq.inbound", Broken()),
                _PluginEventRegistration("working", "channel.qq.inbound", working),
            )
        )

        result = await broker.dispatch(_envelope())

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "partially_observed")
        self.assertEqual(result.failures, (("broken", "handler_exception"),))
        self.assertEqual(result.timeline_events[0].event_type, "calendar.done")


class _Distribution:
    version = "0.1.0"
    metadata = {"Name": "akane-test-events"}

    def read_text(self, _filename: str) -> str | None:
        return None


class _EntryPoint:
    name = PLUGIN_ID
    dist = _Distribution()

    def __init__(self, factory: Callable[[], Any]) -> None:
        self._factory = factory

    def load(self) -> Callable[[], Any]:
        return self._factory


class PluginEventHostTests(unittest.IsolatedAsyncioTestCase):
    async def test_event_only_plugin_activates_and_publishes_real_snapshot(self) -> None:
        def factory() -> Any:
            class Plugin:
                manifest = PluginManifest(
                    plugin_id=PLUGIN_ID,
                    plugin_version="0.1.0",
                    plugin_api_version=AKANE_PLUGIN_API_VERSION,
                    permissions=(EVENT_SUBSCRIBE_PERMISSION,),
                )

                def register(self, registrar: Any) -> None:
                    registrar.add_event_handler("channel.qq.inbound", _Handler(PluginEventResult()))

            return Plugin()

        host = PluginHost(
            (PluginSelection(plugin_id=PLUGIN_ID, enabled=True),),
            contribution_policy=TrustedStatefulPluginContributionPolicy(),
            entry_points_provider=lambda: (_EntryPoint(factory),),
        )

        status = await host.start()
        broker = host.build_event_broker()

        self.assertEqual(status["status"], "active")
        self.assertEqual(status["capability_count"], 0)
        self.assertEqual(broker.registered_event_types, ("channel.qq.inbound",))
        self.assertEqual(
            status["plugins"][0]["contribution_snapshot"]["types"],
            ["event_handlers"],
        )
        self.assertEqual(
            status["plugins"][0]["contribution_snapshot"]["event_handlers"],
            ["channel.qq.inbound"],
        )
        await host.stop()


class _Metrics:
    def observe_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


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

    def mark_generated_file_delivery(self, **_kwargs: Any) -> dict[str, Any]:
        return {"ok": True}


class PluginEventQQBridgeTests(unittest.TestCase):
    def test_current_turn_wakes_passive_message_and_timeline_uses_memcore_port(self) -> None:
        current = _Handler(
            PluginEventResult(
                delivery="current_turn",
                event=PluginExternalEvent(
                    event_type="companion.scene",
                    fields=(("mood", "tense"),),
                    source="plugin.scene",
                ),
            )
        )
        timeline = _Handler(
            PluginEventResult(
                delivery="timeline",
                event=PluginExternalEvent(
                    event_type="companion.shared_moment",
                    fields=(("summary", "一起看了夜景"),),
                    source="plugin.scene",
                ),
            )
        )
        broker = PluginEventBroker(
            (
                _PluginEventRegistration("current", "channel.qq.inbound", current),
                _PluginEventRegistration("timeline", "channel.qq.inbound", timeline),
            )
        )
        engine = _RouteEngine()
        channel = QQChannelRuntimeConfig(
            enabled=True,
            profile_ref="qq.test",
            bot_id="100",
            onebot_http_url="http://127.0.0.1:3001",
            webhook_secret="",
            onebot_access_token="",
            require_webhook_auth=False,
            require_self_id=True,
        )
        gateway = NapCatQQGateway(channel_config=channel, wake_words=("Akane",))
        app = FastAPI()
        app.include_router(
            build_qq_router(
                engine=engine,
                config_module=SimpleNamespace(
                    QQ_BRIDGE_ENABLED=True,
                    QQ_STREAM_REPLIES_ENABLED=False,
                    QQ_GROUP_PASSIVE_MEMORY_MODE="all",
                ),
                qq_gateway=gateway,
                runtime_metrics=_Metrics(),
                channel_config=channel,
                logger=SimpleNamespace(exception=lambda *_args, **_kwargs: None),
                log_event=lambda *_args, **_kwargs: None,
                plugin_event_broker_provider=lambda: broker,
            )
        )
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
            response = TestClient(app).post("/api/qq/napcat/event", json=event)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(engine.turns), 1, response.json())
        self.assertIn("plugin.current_turn", engine.turns[0]["extra_context"])
        self.assertIn("companion.scene", engine.turns[0]["extra_context"])
        self.assertEqual(len(engine.timeline), 1)
        self.assertEqual(engine.timeline[0]["event"]["event_type"], "companion.shared_moment")
        self.assertNotIn("plugin_current_turn_events", engine.turns[0])


if __name__ == "__main__":
    unittest.main()
