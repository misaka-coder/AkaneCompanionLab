from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from companion_v01.engine import AkaneMemoryEngine
from companion_v01.plugin_api import (
    PluginExternalEvent,
    PluginReasoningRequest,
    PluginReasoningResult,
)
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
            external_event=PluginExternalEvent(
                event_type="finance",
                source="东方财富",
                fields=(
                    ("url", "https://finance.eastmoney.com/example.html"),
                    ("summary", "公开快讯摘要。"),
                    ("title", "科创债ETF规模出现新变化"),
                    ("published_at", "2026-07-14T14:30:00+08:00"),
                ),
            ),
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
        self.assertEqual(
            payload["message"],
            "source: 东方财富\n"
            "published_at: 2026-07-14T14:30:00+08:00\n"
            "title: 科创债ETF规模出现新变化\n"
            "summary: 公开快讯摘要。\n"
            "url: https://finance.eastmoney.com/example.html",
        )
        self.assertEqual(payload["plugin_external_event"]["event_type"], "finance")
        self.assertEqual(payload["plugin_external_event"]["source"], "东方财富")

    async def test_legacy_unstructured_request_keeps_bounded_proactive_wrapper(self) -> None:
        engine = FakeEngine()
        result = await EnginePluginReasoningPort(engine).analyze(
            PluginReasoningRequest(
                trace_id="legacy:1",
                profile_user_id="qq-user",
                session_id="qq-session",
                message="旧式主动事件",
            )
        )

        self.assertTrue(result.ok)
        self.assertEqual(
            engine.payloads[0]["message"],
            "【当前待处理的插件主动事件（不是用户发言）】\n"
            "旧式主动事件\n"
            "请按系统约定的 JSON 最终答复格式完成本次处理。",
        )
        self.assertNotIn("plugin_external_event", engine.payloads[0])

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
            ({"external_event": "invalid"}, "invalid_external_event"),
            (
                {
                    "external_event": PluginExternalEvent(
                        event_type="Finance",
                        fields=(("title", "event"),),
                    )
                },
                "invalid_external_event_type",
            ),
            (
                {
                    "external_event": PluginExternalEvent(
                        event_type="finance",
                        fields=(("title", "event"), ("title", "duplicate")),
                    )
                },
                "invalid_external_event_fields",
            ),
            (
                {
                    "external_event": PluginExternalEvent(
                        event_type="finance",
                        fields=(("file_path", "C:/private/event.txt"),),
                    )
                },
                "invalid_external_event_fields",
            ),
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

    def _build_engine_stopped_after_external_event_write(
        self,
    ) -> tuple[AkaneMemoryEngine, list[dict[str, Any]]]:
        engine, _store, _prepared_payloads = self._build_engine_stopped_after_user_write()
        event_calls: list[dict[str, Any]] = []
        engine._load_turn_visible_memory = lambda **kwargs: ([kwargs["user_record"]], [], [])
        engine._run_pre_retrieval_pipeline = lambda **_kwargs: SimpleNamespace(
            router_output={},
            router_timing={},
            retrieval_result={"fused_hits": []},
            verifier_output={},
            confirmed_snippets=[],
            verifier_timing={},
        )
        engine._apply_user_vector_index_policy = lambda *, user_record, **_kwargs: user_record
        engine._upsert_raw_record = lambda _record: None

        def stop_after_external_event_write(**kwargs: Any) -> dict[str, Any]:
            event_calls.append(dict(kwargs))
            raise self._StopAfterUserWrite()

        engine._record_memcore_input_turn = stop_after_external_event_write
        return engine, event_calls

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
            "plugin_stable_system_context": "stable finance principles",
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
        self.assertTrue(all("plugin_stable_system_context" not in item for item in prepared_payloads))

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

    def test_structured_event_source_id_is_separate_from_legacy_user_role(self) -> None:
        payload = {"memory_idempotency_key": "delivery:event-1"}
        legacy = AkaneMemoryEngine._pop_user_memory_source_id(
            dict(payload),
            profile_user_id="owner",
            session_id="session",
            character_pack_id="akane_v1",
        )
        structured = AkaneMemoryEngine._pop_user_memory_source_id(
            dict(payload),
            profile_user_id="owner",
            session_id="session",
            character_pack_id="akane_v1",
            memory_role="event.finance",
        )

        self.assertNotEqual(legacy, structured)

    def test_sync_and_stream_paths_store_structured_event_role_without_leaking_control_payload(self) -> None:
        engine, store, prepared_payloads = self._build_engine_stopped_after_user_write()
        message = (
            "source: 东方财富\n"
            "published_at: 2026-07-14T14:30:00+08:00\n"
            "title: 科创债ETF规模出现新变化\n"
            "summary: 公开快讯摘要。\n"
            "url: https://finance.eastmoney.com/example.html"
        )
        payload = {
            "user_id": "qq-session",
            "real_user_id": "qq-user",
            "character_pack_id": "akane_v1",
            "message": message,
            "timestamp": 1_784_016_000,
            "turn_kind": "plugin_proactive",
            "memory_idempotency_key": "delivery:structured-event",
            "plugin_external_event": {
                "event_type": "finance",
                "source": "东方财富",
                "fields": {
                    "published_at": "2026-07-14T14:30:00+08:00",
                    "title": "科创债ETF规模出现新变化",
                    "summary": "公开快讯摘要。",
                    "url": "https://finance.eastmoney.com/example.html",
                },
            },
        }

        with self.assertRaises(self._StopAfterUserWrite):
            engine.process_turn(payload)
        with self.assertRaises(self._StopAfterUserWrite):
            next(engine.process_turn_stream(payload))

        self.assertEqual([call["role"] for call in store.calls], ["event.finance", "event.finance"])
        self.assertTrue(
            all(call["memory_metadata"]["categories"] == ["event_trace"] for call in store.calls)
        )
        self.assertTrue(all(call["content"] == message for call in store.calls))
        self.assertTrue(all("plugin_external_event" not in item for item in prepared_payloads))

    def test_sync_and_stream_paths_open_structured_event_as_memcore_v2_input(self) -> None:
        payload = {
            "user_id": "qq-session",
            "real_user_id": "qq-user",
            "character_pack_id": "akane_v1",
            "message": "source: 东方财富\ntitle: 结构化事件",
            "timestamp": 1_784_016_000,
            "turn_kind": "plugin_proactive",
            "memory_idempotency_key": "delivery:structured-event",
            "plugin_external_event": {
                "event_type": "finance",
                "source": "东方财富",
                "fields": {"title": "结构化事件"},
            },
        }

        for use_stream in (False, True):
            with self.subTest(use_stream=use_stream):
                engine, event_calls = self._build_engine_stopped_after_external_event_write()
                with self.assertRaises(self._StopAfterUserWrite):
                    if use_stream:
                        next(engine.process_turn_stream(payload))
                    else:
                        engine.process_turn(payload)

                self.assertEqual(len(event_calls), 1)
                self.assertEqual(event_calls[0]["external_event"]["event_type"], "finance")
                self.assertEqual(event_calls[0]["external_event"]["fields"], {"title": "结构化事件"})

    def test_transient_final_failure_is_not_persisted_as_an_assistant_turn(self) -> None:
        self.assertFalse(
            AkaneMemoryEngine._should_persist_completed_assistant(
                True,
                {"speech": "我在认真听你说", "_transient_final_failure": True},
            )
        )
        self.assertTrue(
            AkaneMemoryEngine._should_persist_completed_assistant(
                True,
                {"speech": "完成的真实分析"},
            )
        )


if __name__ == "__main__":
    unittest.main()
