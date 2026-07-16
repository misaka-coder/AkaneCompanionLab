from __future__ import annotations

import unittest
from typing import Any

from companion_v01.plugin_api import PluginReasoningRequest, PluginReasoningResult
from companion_v01.plugin_reasoning import EnginePluginReasoningPort, PluginScopedReasoningPort


class FakeEngine:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def process_turn(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.payloads.append(payload)
        return {
            "speech": "核验后的分析",
            "tool_events": [
                {
                    "type": "adapter_capability_completed",
                    "status": "ok",
                    "capabilityId": "akane.finance.quote_snapshot.v1",
                    "absolute_path": "C:/secret/local.db",
                    "token": "must-not-leak",
                }
            ],
        }


class EnginePluginReasoningPortTests(unittest.IsolatedAsyncioTestCase):
    async def test_runs_transient_proactive_turn_and_projects_safe_evidence(self) -> None:
        engine = FakeEngine()
        port = EnginePluginReasoningPort(engine)
        request = PluginReasoningRequest(
            trace_id="finance:1",
            profile_user_id="qq-user",
            session_id="qq-session",
            character_pack_id="akane_v1",
            message="外部市场事件",
            extra_context="先核验再分析",
            timestamp=1_784_016_000,
        )

        result = await port.analyze(request)

        self.assertTrue(result.ok)
        self.assertEqual(result.text, "核验后的分析")
        self.assertEqual(result.evidence_events[0]["capabilityId"], "akane.finance.quote_snapshot.v1")
        self.assertNotIn("absolute_path", result.evidence_events[0])
        self.assertNotIn("token", result.evidence_events[0])
        payload = engine.payloads[0]
        self.assertTrue(payload["transient_user_message"])
        self.assertTrue(payload["transient_assistant_message"])
        self.assertTrue(payload["pre_retrieval_enabled"])
        self.assertEqual(payload["character_pack_id"], "akane_v1")

    async def test_invalid_request_fails_without_calling_engine(self) -> None:
        engine = FakeEngine()
        port = EnginePluginReasoningPort(engine)

        result = await port.analyze(
            PluginReasoningRequest(
                trace_id="",
                profile_user_id="qq-user",
                session_id="qq-session",
                message="event",
            )
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "reasoning_identity_required")
        self.assertEqual(engine.payloads, [])

    async def test_scoped_port_rejects_after_host_becomes_unavailable(self) -> None:
        class Delegate:
            async def analyze(self, request: PluginReasoningRequest) -> PluginReasoningResult:
                del request
                return PluginReasoningResult(ok=True, status="completed", text="should-not-run")

        port = PluginScopedReasoningPort(delegate=Delegate(), availability_provider=lambda: False)
        result = await port.analyze(
            PluginReasoningRequest(
                trace_id="finance:1",
                profile_user_id="qq-user",
                session_id="qq-session",
                message="event",
            )
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "host_unavailable")


if __name__ == "__main__":
    unittest.main()
