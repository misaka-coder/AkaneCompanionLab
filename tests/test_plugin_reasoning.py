from __future__ import annotations

import unittest
from typing import Any

from companion_v01.engine import AkaneMemoryEngine
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
    async def test_runs_persistent_proactive_turn_and_projects_safe_evidence(self) -> None:
        engine = FakeEngine()
        port = EnginePluginReasoningPort(engine)
        request = PluginReasoningRequest(
            trace_id="finance:1",
            profile_user_id="qq-user",
            session_id="qq-session",
            character_pack_id="akane_v1",
            message="外部市场事件",
            extra_context="先核验再分析",
            stable_system_context="长期稳定的金融分析原则",
            memory_idempotency_key="delivery:stable-event-1",
            timestamp=1_784_016_000,
        )

        result = await port.analyze(request)

        self.assertTrue(result.ok)
        self.assertEqual(result.text, "核验后的分析")
        self.assertEqual(result.evidence_events[0]["capabilityId"], "akane.finance.quote_snapshot.v1")
        self.assertNotIn("absolute_path", result.evidence_events[0])
        self.assertNotIn("token", result.evidence_events[0])
        payload = engine.payloads[0]
        self.assertNotIn("transient_user_message", payload)
        self.assertNotIn("transient_assistant_message", payload)
        self.assertNotIn("pre_retrieval_enabled", payload)
        self.assertEqual(payload["plugin_stable_system_context"], "长期稳定的金融分析原则")
        self.assertEqual(payload["memory_idempotency_key"], "delivery:stable-event-1")
        self.assertEqual(payload["character_pack_id"], "akane_v1")

    async def test_transient_final_failure_is_structured_instead_of_returned_as_analysis(self) -> None:
        class IncompleteEngine:
            def process_turn(self, payload: dict[str, Any]) -> dict[str, Any]:
                del payload
                return {
                    "speech": "我在认真听你说，要不要再多告诉我一点？",
                    "_transient_final_failure": True,
                }

        result = await EnginePluginReasoningPort(IncompleteEngine()).analyze(
            PluginReasoningRequest(
                trace_id="finance:incomplete",
                profile_user_id="qq-user",
                session_id="qq-session",
                message="event",
            )
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "incomplete_reasoning_result")
        self.assertEqual(result.text, "")

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

    async def test_rejects_invalid_stable_context_and_idempotency_key(self) -> None:
        engine = FakeEngine()
        port = EnginePluginReasoningPort(engine)
        invalid_cases = (
            ({"stable_system_context": 123}, "invalid_stable_system_context"),
            ({"stable_system_context": "x" * 12_001}, "invalid_stable_system_context"),
            ({"stable_system_context": "stable\x00context"}, "invalid_stable_system_context"),
            ({"memory_idempotency_key": []}, "invalid_memory_idempotency_key"),
            ({"memory_idempotency_key": "x" * 241}, "invalid_memory_idempotency_key"),
            ({"memory_idempotency_key": "event\x00key"}, "invalid_memory_idempotency_key"),
        )

        for overrides, expected_reason in invalid_cases:
            with self.subTest(expected_reason=expected_reason):
                result = await port.analyze(
                    PluginReasoningRequest(
                        trace_id="finance:invalid",
                        profile_user_id="qq-user",
                        session_id="qq-session",
                        message="event",
                        **overrides,
                    )
                )
                self.assertFalse(result.ok)
                self.assertEqual(result.reason, expected_reason)
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


class PluginReasoningMemoryPathTests(unittest.TestCase):
    class _StopAfterUserWrite(RuntimeError):
        pass

    class _RecordingStore:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def add_message(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(dict(kwargs))
            return {
                **kwargs,
                "seq_no": len(self.calls),
                "source_id": str(kwargs.get("source_id") or f"generated:{len(self.calls)}"),
                "memory_metadata": {},
                "index_in_vector": True,
            }

    def _build_engine_stopped_after_user_write(self) -> tuple[AkaneMemoryEngine, _RecordingStore, list[dict[str, Any]]]:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        store = self._RecordingStore()
        prepared_payloads: list[dict[str, Any]] = []
        engine.store = store
        engine._resolve_client_protocol_context = lambda payload: object()
        engine._resolve_payload_character_pack_id = lambda payload: "akane_v1"
        engine._resolve_turn_actor = lambda payload: ("", "")
        engine._resolve_turn_domain_profile = lambda payload: ""
        engine._resolve_turn_resource_manifest = lambda payload, context: None
        engine._prepare_care_context_for_turn = (
            lambda payload, context, **kwargs: prepared_payloads.append(dict(payload)) or payload
        )
        engine._build_turn_extra_user_context = lambda payload, context: ""
        engine._extract_native_user_images = lambda payload: []
        engine._extract_desktop_screen_frame_images = lambda payload: []
        engine.consume_due_reminders = lambda **kwargs: None
        engine._memcore_owns_compaction = lambda: True

        def stop_after_user_write(**kwargs: Any) -> tuple[Any, Any, Any]:
            del kwargs
            raise self._StopAfterUserWrite()

        engine._load_turn_visible_memory = stop_after_user_write
        return engine, store, prepared_payloads

    def test_sync_and_stream_paths_use_same_hashed_user_source_id_and_consume_raw_key(self) -> None:
        engine, store, prepared_payloads = self._build_engine_stopped_after_user_write()
        payload = {
            "user_id": "qq-session",
            "real_user_id": "qq-user",
            "character_pack_id": "akane_v1",
            "message": "外部财经事件",
            "timestamp": 1_784_016_000,
            "turn_kind": "plugin_proactive",
            "memory_idempotency_key": "delivery:raw-secret-event-key",
        }

        with self.assertRaises(self._StopAfterUserWrite):
            engine.process_turn(payload)
        with self.assertRaises(self._StopAfterUserWrite):
            next(engine.process_turn_stream(payload))

        self.assertEqual(len(store.calls), 2)
        sync_source_id = store.calls[0]["source_id"]
        stream_source_id = store.calls[1]["source_id"]
        self.assertEqual(sync_source_id, stream_source_id)
        self.assertRegex(sync_source_id, r"^plugin-event:[0-9a-f]{64}$")
        self.assertNotIn("delivery:raw-secret-event-key", sync_source_id)
        self.assertEqual([call["role"] for call in store.calls], ["user", "user"])
        self.assertTrue(all("memory_idempotency_key" not in item for item in prepared_payloads))

    def test_hashed_user_source_id_changes_with_owner_scope(self) -> None:
        base_payload = {"memory_idempotency_key": "delivery:event-1"}
        first = AkaneMemoryEngine._pop_user_memory_source_id(
            dict(base_payload),
            profile_user_id="owner-a",
            session_id="session",
            character_pack_id="akane_v1",
        )
        second = AkaneMemoryEngine._pop_user_memory_source_id(
            dict(base_payload),
            profile_user_id="owner-b",
            session_id="session",
            character_pack_id="akane_v1",
        )

        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
