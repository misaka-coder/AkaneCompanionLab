from __future__ import annotations

import unittest

from companion_v01 import tool_orchestration_engine
from companion_v01.tool_invocation import (
    LEGACY_JSON,
    NATIVE_OPENAI,
    TOOL_INVOCATION_ID_FIELD,
    TOOL_SOURCE_FIELD,
    ToolResultEnvelope,
    ToolInvocation,
    ValidationResult,
    invocation_to_legacy_tool_call,
    legacy_tool_call_to_invocation,
    round_trip_legacy_tool_call,
)
from companion_v01.tool_runtime import TOOL_METADATA_BY_TYPE, ToolExecutionResult, ToolMetadata


class ToolInvocationTests(unittest.TestCase):
    def test_legacy_dict_becomes_invocation(self) -> None:
        inv = legacy_tool_call_to_invocation({"type": "web_search", "action": "search", "query": "天气"})
        assert inv is not None
        self.assertEqual(inv.name, "web_search")
        self.assertEqual(inv.arguments, {"action": "search", "query": "天气"})
        self.assertEqual(inv.source, LEGACY_JSON)
        self.assertTrue(inv.id)  # auto-generated

    def test_non_dict_or_empty_type_is_none(self) -> None:
        # Must behave exactly like "no tool call" so the live path is unaffected.
        self.assertIsNone(legacy_tool_call_to_invocation(None))
        self.assertIsNone(legacy_tool_call_to_invocation("web_search"))
        self.assertIsNone(legacy_tool_call_to_invocation({}))
        self.assertIsNone(legacy_tool_call_to_invocation({"type": ""}))
        self.assertIsNone(legacy_tool_call_to_invocation({"type": "   "}))

    def test_round_trip_is_identity(self) -> None:
        # The safety property that makes live-path wiring behaviour-preserving
        # for already-normalized legacy tool calls.
        for tc in [
            {"type": "retrieve_memory", "query": "我的生日"},
            {"type": "web_search", "action": "batch_search", "queries": ["a", "b"], "max_results": 5},
            {"type": "send_sticker", "sticker": "haoxingfu"},
            {"type": "manage_task_workspace", "action": "create", "title": "t", "steps": []},
        ]:
            inv = legacy_tool_call_to_invocation(tc)
            assert inv is not None
            self.assertEqual(invocation_to_legacy_tool_call(inv), tc)

    def test_native_metadata_can_cross_legacy_bridge(self) -> None:
        inv = legacy_tool_call_to_invocation(
            {
                "type": "web_search",
                "action": "search",
                "query": "天气",
                TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                TOOL_INVOCATION_ID_FIELD: "call_native_1",
            }
        )
        assert inv is not None
        self.assertEqual(inv.source, NATIVE_OPENAI)
        self.assertEqual(inv.id, "call_native_1")
        self.assertEqual(inv.arguments, {"action": "search", "query": "天气"})
        self.assertEqual(
            invocation_to_legacy_tool_call(inv, include_metadata=True),
            {
                "type": "web_search",
                "action": "search",
                "query": "天气",
                TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                TOOL_INVOCATION_ID_FIELD: "call_native_1",
            },
        )
        self.assertEqual(
            invocation_to_legacy_tool_call(inv),
            {"type": "web_search", "action": "search", "query": "天气"},
        )

    def test_round_trip_bridge_returns_legacy_shape(self) -> None:
        self.assertEqual(
            round_trip_legacy_tool_call({"type": "web_search", "query": "天气"}),
            {"type": "web_search", "query": "天气"},
        )
        self.assertIsNone(round_trip_legacy_tool_call({"type": ""}))

    def test_dirty_type_is_normalized_not_identity(self) -> None:
        # Callers should use the identity guarantee after handler.normalize_call.
        self.assertEqual(
            round_trip_legacy_tool_call({"type": " web_search ", "query": "天气"}),
            {"type": "web_search", "query": "天气"},
        )

    def test_explicit_id_preserved(self) -> None:
        inv = ToolInvocation(name="x", id="call_fixed")
        self.assertEqual(inv.id, "call_fixed")

    def test_distinct_invocations_get_distinct_ids(self) -> None:
        a = ToolInvocation(name="x")
        b = ToolInvocation(name="x")
        self.assertNotEqual(a.id, b.id)

    def test_validation_result_helpers(self) -> None:
        ok = ValidationResult.success()
        self.assertTrue(ok.ok)
        bad = ValidationResult.fail("unknown_tool", "工具不存在")
        self.assertFalse(bad.ok)
        self.assertEqual(bad.code, "unknown_tool")
        self.assertIn("不存在", bad.message)

    def test_arguments_default_is_not_shared(self) -> None:
        a = ToolInvocation(name="x")
        a.arguments["k"] = 1
        b = ToolInvocation(name="y")
        self.assertEqual(b.arguments, {})  # no shared mutable default

    def test_result_envelope_events_default_is_not_shared(self) -> None:
        a = ToolResultEnvelope(invocation_id="call_a", status="ok", model_feedback="done")
        a.events.append({"type": "tool_done"})
        b = ToolResultEnvelope(invocation_id="call_b", status="ok", model_feedback="done")
        self.assertEqual(b.events, [])

    def test_tool_metadata_contract_fields_are_descriptive_only_for_now(self) -> None:
        read_metadata = TOOL_METADATA_BY_TYPE["retrieve_memory"]
        write_metadata = TOOL_METADATA_BY_TYPE["compose_file"]

        self.assertTrue(read_metadata.is_read_only)
        self.assertFalse(write_metadata.is_read_only)
        self.assertFalse(read_metadata.requires_confirmation)
        self.assertTrue(write_metadata.requires_confirmation)
        self.assertEqual(ToolMetadata().aliases, ())
        self.assertIsNone(ToolMetadata().input_schema)

        metadata_dict = tool_orchestration_engine.tool_metadata_dict(
            SimpleMetadataHandler(write_metadata),
            tool_type="compose_file",
        )

        self.assertNotIn("requires_confirmation", metadata_dict)
        self.assertNotIn("input_schema", metadata_dict)
        self.assertNotIn("aliases", metadata_dict)

    def test_live_legacy_normalize_path_crosses_invocation_boundary(self) -> None:
        handler = RecordingHandler()
        engine = FakeEngine(handler)

        normalized = tool_orchestration_engine.normalize_tool_call(
            engine,
            {"type": "web_search", "query": "天气", "ignored": ""},
            profile_user_id="alice",
            session_id="s1",
        )

        self.assertEqual(normalized, {"type": "web_search", "query": "天气", "limit": 2})
        self.assertEqual(handler.normalized_inputs, [{"type": "web_search", "query": "天气", "ignored": ""}])

    def test_live_native_source_survives_normalize_but_not_execute_args(self) -> None:
        handler = RecordingHandler()
        engine = FakeEngine(handler)

        normalized = tool_orchestration_engine.normalize_tool_call(
            engine,
            {
                "type": "web_search",
                "query": "天气",
                TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                TOOL_INVOCATION_ID_FIELD: "call_native_1",
            },
            profile_user_id="alice",
            session_id="s1",
        )

        self.assertEqual(normalized[TOOL_SOURCE_FIELD], NATIVE_OPENAI)
        self.assertEqual(normalized[TOOL_INVOCATION_ID_FIELD], "call_native_1")

        result = tool_orchestration_engine.execute_tool_call(
            engine,
            profile_user_id="alice",
            session_id="s1",
            tool_call=normalized,
            visual_payload={"speech": "我查一下"},
            now_ts=123,
        )

        self.assertIsInstance(result, ToolExecutionResult)
        self.assertEqual(handler.executed_call, {"type": "web_search", "query": "天气", "limit": 2})

    def test_live_execute_path_still_executes_legacy_dict(self) -> None:
        handler = RecordingHandler()
        engine = FakeEngine(handler)

        result = tool_orchestration_engine.execute_tool_call(
            engine,
            profile_user_id="alice",
            session_id="s1",
            tool_call={"type": "web_search", "query": "天气"},
            visual_payload={"speech": "我查一下"},
            now_ts=123,
        )

        self.assertIsInstance(result, ToolExecutionResult)
        self.assertEqual(result.tool_type, "web_search")
        self.assertEqual(handler.executed_call, {"type": "web_search", "query": "天气", "limit": 2})

    def test_validation_reports_unknown_tool(self) -> None:
        engine = FakeEngine(RecordingHandler())

        validation = tool_orchestration_engine.validate_legacy_tool_call(
            engine,
            {"type": "make_coffee", "size": "large"},
            profile_user_id="alice",
            session_id="s1",
        )

        self.assertFalse(validation.ok)
        self.assertEqual(validation.code, "unknown_tool")
        self.assertIn("make_coffee", validation.message)
        self.assertIn("web_search", validation.message)

    def test_validation_reports_bad_args(self) -> None:
        engine = FakeEngine(RecordingHandler())

        validation = tool_orchestration_engine.validate_legacy_tool_call(
            engine,
            {"type": "web_search"},
            profile_user_id="alice",
            session_id="s1",
        )

        self.assertFalse(validation.ok)
        self.assertEqual(validation.code, "bad_args")
        self.assertIn("参数", validation.message)

    def test_rejection_message_still_comes_from_validation(self) -> None:
        engine = FakeEngine(RecordingHandler())

        reason = tool_orchestration_engine.classify_tool_call_rejection(
            engine,
            {"type": "web_search"},
            profile_user_id="alice",
            session_id="s1",
        )

        self.assertIn("web_search", reason)
        self.assertIn("参数", reason)

    def test_execute_invocation_returns_result_envelope(self) -> None:
        handler = RecordingHandler()
        engine = FakeEngine(handler)
        invocation = ToolInvocation(name="web_search", arguments={"query": "天气", "limit": 2}, id="call_fixed")

        result, envelope = tool_orchestration_engine.execute_tool_invocation(
            engine,
            invocation=invocation,
            profile_user_id="alice",
            session_id="s1",
            visual_payload={"speech": "我查一下"},
            now_ts=123,
        )

        self.assertIsInstance(result, ToolExecutionResult)
        self.assertEqual(envelope.invocation_id, "call_fixed")
        self.assertEqual(envelope.status, "ok")
        self.assertEqual(envelope.model_feedback, "query=天气")
        self.assertEqual(envelope.data["tool_type"], "web_search")

    def test_validation_failure_can_be_shaped_as_error_envelope(self) -> None:
        invocation = ToolInvocation(name="make_coffee", id="call_bad")
        validation = ValidationResult.fail("unknown_tool", "工具不存在")

        envelope = tool_orchestration_engine.validation_result_to_envelope(
            invocation=invocation,
            validation=validation,
        )

        self.assertEqual(envelope.invocation_id, "call_bad")
        self.assertEqual(envelope.status, "error")
        self.assertIn("<tool_use_error>", envelope.model_feedback)
        self.assertEqual(envelope.data["code"], "unknown_tool")


class ShapeToolFollowupTests(unittest.TestCase):
    def test_empty_result_becomes_stable_placeholder(self) -> None:
        # Claude Code's empty-tool-result guard: a successful-but-silent tool
        # must still feed the model something, never an empty result.
        for empty in ["", "   ", "\n\t", None]:
            shaped = tool_orchestration_engine.shape_tool_followup(empty, tool_type="list_reminders")
            self.assertIn("list_reminders", shaped)
            self.assertIn("没有返回可展示的内容", shaped)

    def test_normal_result_passes_through_unchanged(self) -> None:
        text = "当前待处理提醒如下：\n1. 明天买牛奶"
        self.assertEqual(
            tool_orchestration_engine.shape_tool_followup(text, tool_type="list_reminders"),
            text,
        )

    def test_oversize_result_is_truncated_with_marker(self) -> None:
        big = "\n".join(f"line {i} " + "x" * 50 for i in range(2000))
        shaped = tool_orchestration_engine.shape_tool_followup(
            big, tool_type="web_search", max_chars=1000
        )
        self.assertLess(len(shaped), len(big))
        self.assertIn("已截断", shaped)
        self.assertIn("web_search", shaped)
        # The marker is honest about magnitude: it reports the full size so the
        # model can gauge how far to narrow its next call, not just what was shown.
        self.assertIn(f"共约 {len(big)} 字", shaped)
        self.assertIn("省略约", shaped)
        # Truncation prefers a newline boundary, so no line is cut mid-way.
        body = shaped.split("\n…（")[0]
        self.assertTrue(big.startswith(body))

    def test_floor_protects_against_tiny_limits(self) -> None:
        big = "y" * 5000
        shaped = tool_orchestration_engine.shape_tool_followup(
            big, tool_type="t", max_chars=10
        )
        # limit is floored at 500, so we keep a usable preview, not 10 chars.
        self.assertGreater(len(shaped), 400)


class RecordingHandler:
    def __init__(self) -> None:
        self.normalized_inputs: list[dict] = []
        self.executed_call: dict | None = None

    def normalize_call(self, value):
        self.normalized_inputs.append(dict(value or {}))
        if not isinstance(value, dict) or value.get("type") != "web_search":
            return None
        query = str(value.get("query") or "").strip()
        if not query:
            return None
        return {"type": "web_search", "query": query, "limit": 2}

    def execute(self, *, call: dict, context) -> ToolExecutionResult:
        self.executed_call = dict(call)
        return ToolExecutionResult(tool_type="web_search", followup_context=f"query={call.get('query')}")


class SimpleMetadataHandler:
    tool_type = "compose_file"

    def __init__(self, metadata: ToolMetadata) -> None:
        self._metadata = metadata

    def tool_metadata(self) -> ToolMetadata:
        return self._metadata


class FakeEngine:
    def __init__(self, handler: RecordingHandler) -> None:
        self.handler = handler

    def _resolve_tool_handlers(self, **_kwargs):
        return {"web_search": self.handler}


if __name__ == "__main__":
    unittest.main()
