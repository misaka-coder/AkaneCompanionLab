from __future__ import annotations

import json

import unittest

from capcore import CapabilityToolSpec

from companion_v01 import tool_orchestration_engine
from companion_v01.client_protocol import ClientMode, ClientProtocolContext
from companion_v01.tool_invocation import (
    LEGACY_JSON,
    NATIVE_OPENAI,
    TOOL_INVOCATION_ID_FIELD,
    TOOL_PARSE_ERROR_FIELD,
    TOOL_RAW_ARGUMENTS_FIELD,
    TOOL_SOURCE_FIELD,
    ToolResultEnvelope,
    ToolInvocation,
    ValidationResult,
    invocation_to_legacy_tool_call,
    legacy_tool_call_to_invocation,
    round_trip_legacy_tool_call,
)
from companion_v01.tool_runtime import (
    TOOL_METADATA_BY_TYPE,
    ToolExecutionResult,
    ToolFollowupEnvelope,
    ToolMetadata,
)


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
            {"type": "manage_project_workspace", "action": "inspect", "workspace_id": "project_1"},
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

    def test_malformed_provider_arguments_survive_bridge_and_become_tool_error(self) -> None:
        raw_arguments = '{"query":"weather"'
        inv = legacy_tool_call_to_invocation(
            {
                "type": "web_search",
                TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                TOOL_INVOCATION_ID_FIELD: "call_bad_json",
                TOOL_PARSE_ERROR_FIELD: "invalid_tool_arguments_json",
                TOOL_RAW_ARGUMENTS_FIELD: raw_arguments,
            }
        )
        assert inv is not None

        bridged = invocation_to_legacy_tool_call(inv, include_metadata=True)
        validation = tool_orchestration_engine.validate_tool_invocation(
            object(),
            inv,
        )

        self.assertEqual(bridged[TOOL_PARSE_ERROR_FIELD], "invalid_tool_arguments_json")
        self.assertEqual(bridged[TOOL_RAW_ARGUMENTS_FIELD], raw_arguments)
        self.assertFalse(validation.ok)
        self.assertEqual(validation.code, "invalid_tool_arguments_json")
        self.assertIn(raw_arguments, validation.message)
        self.assertIn("工具仍然可用", validation.message)

        result, envelope = tool_orchestration_engine.execute_tool_invocation(
            object(),
            invocation=inv,
            profile_user_id="alice",
            session_id="s1",
            visual_payload={},
            now_ts=1,
        )
        self.assertIsNotNone(result)
        self.assertEqual(envelope.invocation_id, "call_bad_json")
        self.assertEqual(envelope.status, "error")
        self.assertIn(raw_arguments, envelope.model_feedback)

    def test_unknown_named_call_becomes_result_instead_of_disappearing(self) -> None:
        engine = FakeEngine(RecordingHandler())
        normalized = tool_orchestration_engine.normalize_tool_call(
            engine,
            {
                "type": "not_installed_tool",
                "value": "still-preserved",
                TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                TOOL_INVOCATION_ID_FIELD: "call_unknown",
            },
            profile_user_id="alice",
            session_id="s1",
        )
        assert normalized is not None
        invocation = legacy_tool_call_to_invocation(normalized)
        assert invocation is not None

        result, envelope = tool_orchestration_engine.execute_tool_invocation(
            engine,
            invocation=invocation,
            profile_user_id="alice",
            session_id="s1",
            visual_payload={},
            now_ts=1,
        )

        self.assertIsNotNone(result)
        self.assertEqual(envelope.invocation_id, "call_unknown")
        self.assertEqual(envelope.status, "error")
        self.assertIn("not_installed_tool", envelope.model_feedback)

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
        write_metadata = ToolMetadata(
            family="file_workspace", operation="control", risk="medium", requires_confirmation=True
        )

        self.assertTrue(read_metadata.is_read_only)
        self.assertFalse(write_metadata.is_read_only)
        self.assertFalse(read_metadata.requires_confirmation)
        self.assertTrue(write_metadata.requires_confirmation)
        self.assertEqual(ToolMetadata().aliases, ())
        self.assertIsNone(ToolMetadata().input_schema)

        metadata_dict = tool_orchestration_engine.tool_metadata_dict(
            SimpleMetadataHandler(write_metadata),
            tool_type="manage_generated_file",
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

    def test_tool_spec_bad_args_return_precise_schema_diagnostics(self) -> None:
        class SchemaHandler:
            tool_type = "manage_project_workspace"

            def tool_spec(self):
                return CapabilityToolSpec(
                    capability_id=self.tool_type,
                    display_name="Manage project workspace",
                    description="Manage project workspace.",
                    input_schema={
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "action": {"type": "string", "enum": ["create", "current"]},
                            "display_name": {
                                "type": "string",
                                "minLength": 1,
                                "description": "Required for create; do not use name.",
                            },
                        },
                        "required": ["action"],
                    },
                    risk="medium",
                    confirm="never",
                    effects=("filesystem_write",),
                    visible_in=("desktop",),
                )

            def normalize_call(self, value):
                if value.get("action") == "create" and value.get("display_name"):
                    return dict(value)
                return None

        handler = SchemaHandler()
        engine = FakeEngine(RecordingHandler())
        engine._resolve_tool_handlers = lambda **_kwargs: {handler.tool_type: handler}

        validation = tool_orchestration_engine.validate_legacy_tool_call(
            engine,
            {
                "type": handler.tool_type,
                "action": "create",
                "name": "Demo",
                "goal": "build it",
            },
            profile_user_id="alice",
            session_id="s1",
        )

        self.assertFalse(validation.ok)
        self.assertEqual(validation.code, "bad_args")
        self.assertIn('"code":"unknown_argument"', validation.message)
        self.assertIn('"field":"name"', validation.message)
        self.assertIn('"field":"goal"', validation.message)
        diagnostic = json.JSONDecoder().raw_decode(validation.message.split("结构化诊断：", 1)[1])[0]
        self.assertEqual(diagnostic["input_schema"], handler.tool_spec().input_schema)

    def test_schema_valid_but_action_invalid_reports_conditional_rule(self) -> None:
        class ConditionalHandler:
            tool_type = "conditional_tool"

            def tool_spec(self):
                return CapabilityToolSpec(
                    capability_id=self.tool_type,
                    display_name="Conditional",
                    description="Conditional fields.",
                    input_schema={
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "action": {"type": "string", "enum": ["create"]},
                            "display_name": {"type": "string"},
                        },
                        "required": ["action"],
                    },
                    risk="low",
                    confirm="never",
                    effects=(),
                    visible_in=("desktop",),
                )

            def normalize_call(self, _value):
                return None

        handler = ConditionalHandler()
        engine = FakeEngine(RecordingHandler())
        engine._resolve_tool_handlers = lambda **_kwargs: {handler.tool_type: handler}

        validation = tool_orchestration_engine.validate_legacy_tool_call(
            engine,
            {"type": handler.tool_type, "action": "create"},
            profile_user_id="alice",
            session_id="s1",
        )

        self.assertFalse(validation.ok)
        self.assertIn('"reason":"conditional_fields_invalid"', validation.message)
        diagnostic = json.JSONDecoder().raw_decode(validation.message.split("结构化诊断：", 1)[1])[0]
        self.assertEqual(diagnostic["input_schema"], handler.tool_spec().input_schema)

    def test_rejected_call_retains_complete_schema_and_all_field_errors(self) -> None:
        schema = {
            "type": "object",
            "properties": {f"field_{index:02}": {
                "type": "string", "description": "说明  保留空白\n" * 50 + f"tail_{index}",
            } for index in range(40)},
            "required": [f"field_{index:02}" for index in range(40)],
        }

        class Handler:
            tool_type = "many_fields"

            def tool_spec(self):
                return CapabilityToolSpec(
                    capability_id=self.tool_type, display_name="Many fields", description="Full contract",
                    input_schema=schema, risk="low", confirm="never", effects=(), visible_in=("base",),
                )

            def normalize_call(self, _value):
                return None

        handler = Handler()
        engine = FakeEngine(RecordingHandler())
        engine._resolve_tool_handlers = lambda **_kwargs: {handler.tool_type: handler}
        validation = tool_orchestration_engine.validate_legacy_tool_call(engine, {"type": handler.tool_type})
        self.assertFalse(validation.ok)
        diagnostic = json.JSONDecoder().raw_decode(validation.message.split("结构化诊断：", 1)[1])[0]
        self.assertEqual(diagnostic["input_schema"], schema)
        self.assertEqual(len(diagnostic["errors"]), 40)
        self.assertEqual(diagnostic["errors"][-1]["field"], "field_39")

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

    def test_long_task_is_acknowledged_by_host_job_runtime_before_handler_execution(self) -> None:
        class LongHandler:
            tool_type = "generate_image"

            @staticmethod
            def tool_spec():
                return CapabilityToolSpec(
                    capability_id="generate_image",
                    display_name="Long-task fixture",
                    description="Test-only long operation.",
                    input_schema={"type": "object", "properties": {"prompt": {"type": "string"}}},
                    execution_class="long_task",
                    risk="low",
                    confirm="never",
                    effects=("test",),
                )

            @staticmethod
            def normalize_call(value):
                return dict(value) if str(value.get("prompt") or "").strip() else None

            @staticmethod
            def execute(**_kwargs):
                raise AssertionError("long handler must not execute in the foreground turn")

        class JobRuntime:
            def __init__(self) -> None:
                self.submissions = []

            @staticmethod
            def accepts(**_kwargs):
                return True

            def submit(self, **kwargs):
                self.submissions.append(kwargs)
                return ToolExecutionResult(
                    tool_type="generate_image",
                    stream_events=[{"type": "background_job_accepted", "status": "accepted", "job_id": "job-1"}],
                    followup_context="accepted job-1",
                )

        handler = LongHandler()
        engine = FakeEngine(RecordingHandler())
        engine._resolve_tool_handlers = lambda **_kwargs: {handler.tool_type: handler}
        engine.host_tool_jobs = JobRuntime()

        result, envelope = tool_orchestration_engine.execute_tool_invocation(
            engine,
            invocation=ToolInvocation(
                name="generate_image",
                arguments={"prompt": "moon"},
                id="call-long",
            ),
            profile_user_id="alice",
            session_id="s1",
            character_pack_id="reimu",
            visual_payload={},
            now_ts=123,
        )

        self.assertEqual(result.stream_events[0]["job_id"], "job-1")
        self.assertEqual(envelope.status, "ok")
        self.assertEqual(engine.host_tool_jobs.submissions[0]["invocation_id"], "call-long")

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
            shaped = tool_orchestration_engine.shape_tool_followup(empty, tool_type="inspect_media_info")
            self.assertIn("inspect_media_info", shaped)
            self.assertIn("没有返回可展示的内容", shaped)

    def test_normal_result_passes_through_unchanged(self) -> None:
        text = "媒体时长：183 秒"
        self.assertEqual(
            tool_orchestration_engine.shape_tool_followup(text, tool_type="inspect_media_info"),
            text,
        )

    def test_oversize_internal_result_passes_through_without_shared_character_cut(self) -> None:
        big = "\n".join(f"line {i} " + "x" * 50 for i in range(2000))
        shaped = tool_orchestration_engine.shape_tool_followup(big, tool_type="web_search")
        self.assertEqual(shaped, big)

    def test_producer_bounded_envelope_is_not_truncated_again(self) -> None:
        content = "完整逻辑单元\n" + ("证据" * 6000)
        envelope = ToolFollowupEnvelope(
            content=content,
            producer_bounded=True,
            complete=False,
            continuation={"cursor": "timeline-v1:next"},
            diagnostics={"projection": "conversation"},
        )

        shaped = tool_orchestration_engine.shape_tool_followup(
            envelope,
            tool_type="read_memory_timeline",
        )

        self.assertEqual(shaped, content)
        self.assertNotIn("已截断", shaped)

    def test_unbounded_envelope_is_not_treated_as_permission_to_truncate(self) -> None:
        content = "x" * 5000
        envelope = ToolFollowupEnvelope(content=content)

        shaped = tool_orchestration_engine.shape_tool_followup(
            envelope,
            tool_type="web_search",
        )

        self.assertEqual(shaped, content)
        self.assertNotIn("已截断", shaped)

    def test_generated_file_event_adds_handle_receipt_only_when_missing(self) -> None:
        events = [
            {
                "type": "generated_file_ready",
                "generated_file": {
                    "generated_handle": "gen_007",
                    "output_title": "会议纪要",
                    "output_format": "docx",
                },
            }
        ]

        enriched = tool_orchestration_engine.append_structured_artifact_receipts(
            "文件已经生成。",
            stream_events=events,
        )
        existing = tool_orchestration_engine.append_structured_artifact_receipts(
            "已经生成 gen_007《会议纪要》。",
            stream_events=events,
        )

        self.assertIn("handle=gen_007", enriched)
        self.assertIn("直接调用 send_file", enriched)
        self.assertEqual(existing, "已经生成 gen_007《会议纪要》。")


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
    tool_type = "manage_generated_file"

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
