"""Desktop channel publication tests for the structured event contract."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from akane_plugin import DIRECT_CONVERSATION_EVENT, EventReceipt
from companion_v01.routes.think import build_think_router
from tests.test_plugin_events import _Guard, _Metrics


class _Broker:
    def __init__(self, *, fail=False):
        self.events = []
        self.fail = fail

    async def emit(self, event_type, data, *, context=None, event_key=None, **kwargs):
        if self.fail:
            raise RuntimeError("event_publish_failed")
        self.events.append({
            "event_type": event_type,
            "data": data,
            "context": context,
            "event_key": event_key,
            "kwargs": kwargs,
        })
        return EventReceipt("dispatch-1", "host-event", "unobserved", True)


class InboundTurnsTests(unittest.IsolatedAsyncioTestCase):
    async def _request(self, *, broker, route="/think_once"):
        calls = []

        def process(payload):
            calls.append(dict(payload))
            return {"status": "ok", "speech": "收到", "emotion": "normal", "speech_segments": ["收到"]}

        router = build_think_router(
            engine=SimpleNamespace(process_turn=process),
            public_guard=_Guard(),
            runtime_metrics=_Metrics(),
            log_event=lambda *args, **kwargs: None,
            plugin_event_broker_provider=lambda: broker,
            desktop_agent_event_available=lambda: True,
        )
        payload = {
            "user_id": "session",
            "session_id": "session",
            "real_user_id": "owner",
            "character_pack_id": "akane",
            "message": "hello",
            "source_message_id": "input-1",
            "current_attachment_ids": ["attachment-1"],
        }

        async def body():
            return payload

        endpoint = next(item.endpoint for item in router.routes if item.path == route)
        response = await endpoint(SimpleNamespace(json=body))
        return response, calls

    async def test_once_publishes_structured_event_and_keeps_model_input_normal(self):
        broker = _Broker()
        response, calls = await self._request(broker=broker)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.body)["speech"], "收到")
        self.assertEqual(len(broker.events), 1)
        event = broker.events[0]
        self.assertEqual(event["event_type"], DIRECT_CONVERSATION_EVENT)
        self.assertEqual(event["data"]["text"], "hello")
        self.assertEqual(event["context"].profile_user_id, "owner")
        self.assertEqual(len(calls), 1)
        self.assertNotIn("plugin_external_event", calls[0])

    async def test_publication_failure_is_logged_without_faking_a_second_delivery_path(self):
        logs = []
        broker = _Broker(fail=True)
        calls = []

        def process(payload):
            calls.append(payload)
            return {"status": "ok", "speech": "继续处理", "emotion": "normal"}

        router = build_think_router(
            engine=SimpleNamespace(process_turn=process),
            public_guard=_Guard(),
            runtime_metrics=_Metrics(),
            log_event=lambda name, **data: logs.append((name, data)),
            plugin_event_broker_provider=lambda: broker,
        )

        async def body():
            return {"user_id": "session", "real_user_id": "owner", "message": "hello"}

        endpoint = next(item.endpoint for item in router.routes if item.path == "/think_once")
        response = await endpoint(SimpleNamespace(json=body))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(calls), 1)
        self.assertTrue(any(name == "desktop_plugin_event_degraded" for name, _ in logs))


if __name__ == "__main__":
    unittest.main()
