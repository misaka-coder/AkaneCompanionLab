"""Exercise HTTP admission and the public failure boundary without external services."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from companion_v01.routes.qq import build_qq_router


class QQInboundRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.metrics = []
        self.events = []
        self.errors = []
        self.context_calls = []

        def fail_context(event):
            self.context_calls.append(event)
            raise RuntimeError("private-token-sentinel F:/private-storage-sentinel/config.json")

        gateway = SimpleNamespace(build_message_context=fail_context)
        self.app = FastAPI()
        self.app.include_router(build_qq_router(
            engine=SimpleNamespace(), config_module=SimpleNamespace(QQ_BRIDGE_ENABLED=True),
            qq_gateway=gateway,
            runtime_metrics=SimpleNamespace(observe_request=lambda name, **data: self.metrics.append((name, data))),
            logger=SimpleNamespace(error=lambda *args: self.errors.append(args)),
            log_event=lambda name, **data: self.events.append((name, data)),
        ))

    def test_unexpected_failure_keeps_cause_type_and_redacts_private_details(self):
        with TestClient(self.app) as client:
            response = client.post("/api/qq/napcat/event", json={"post_type": "message", "message_type": "private"})
        data = response.json()
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["reason"], "qq_event_processing_failed")
        self.assertEqual(data["error_type"], "RuntimeError")
        self.assertEqual(len(self.context_calls), 1)
        self.assertFalse(self.metrics[-1][1]["ok"])
        self.assertEqual(self.events[-1][1]["reason"], "RuntimeError")
        public_output = repr((data, self.events, self.errors))
        self.assertNotIn("private-token-sentinel", public_output)
        self.assertNotIn("private-storage-sentinel", public_output)

    def test_malformed_and_non_object_events_do_not_enter_conversation_processing(self):
        with TestClient(self.app) as client:
            malformed = client.post("/api/qq/napcat/event", content="{", headers={"Content-Type": "application/json"})
            non_object = client.post("/api/qq/napcat/event", json=[])
        self.assertEqual(malformed.json()["reason"], "qq_event_processing_failed")
        self.assertEqual(malformed.json()["error_type"], "JSONDecodeError")
        self.assertEqual(non_object.status_code, 400)
        self.assertEqual(non_object.json()["reason"], "qq_event_must_be_object")
        self.assertEqual(self.context_calls, [])
        self.assertTrue(all(not data["ok"] for _, data in self.metrics))


if __name__ == "__main__":
    unittest.main()
