from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from capcore import CapabilityToolSpec

import config
from companion_v01 import tool_orchestration_engine as orchestration
from companion_v01.capability_contracts import ContractBoundHandler, contract_snapshot
from companion_v01.tool_handlers.core import ToolExecutionResult
from companion_v01.tool_invocation import (
    TOOL_CAPABILITY_SELECTION_FIELD,
    TOOL_INVOCATION_ID_FIELD,
    TOOL_PARSE_ERROR_FIELD,
    TOOL_RAW_ARGUMENTS_FIELD,
    ToolInvocation,
)


class NativePlanAdmissionTests(unittest.TestCase):
    def test_missing_or_invalid_handlers_return_an_observable_disabled_plan(self):
        with patch.object(config, "ENABLE_NATIVE_TOOL_DECISION", True):
            for handlers in (None, [], ["not-a-handler-map"], "not-a-handler-map"):
                with self.subTest(handlers=handlers):
                    plan = orchestration.build_native_tool_decision_plan(
                        handlers,
                        allow_tool_call=True,
                        provider_supports_native_tools=True,
                    )
                    self.assertEqual((plan.status, plan.reason), ("disabled", "native_tool_handlers_missing"))
                    self.assertEqual(plan.tools, [])
                    self.assertEqual(plan.legacy_prompt_exclusions, set())

    def test_disabled_native_decisions_do_not_inspect_handlers(self):
        for enabled, allowed, reason in (
            (True, False, "tool_call_not_allowed"),
            (False, True, "native_tool_decision_disabled"),
        ):
            with self.subTest(enabled=enabled, allowed=allowed):
                with patch.object(config, "ENABLE_NATIVE_TOOL_DECISION", enabled):
                    plan = orchestration.build_native_tool_decision_plan(
                        None,
                        allow_tool_call=allowed,
                        provider_supports_native_tools=True,
                    )
                self.assertEqual((plan.status, plan.reason), ("disabled", reason))
                self.assertEqual(plan.tools, [])


class LegacyAdmissionTests(unittest.TestCase):
    def test_legacy_validation_resolves_the_selected_handlers_once(self):
        handler = SimpleNamespace(normalize_call=lambda call: dict(call))
        engine = SimpleNamespace(_resolve_tool_handlers=Mock(return_value={"demo": handler}))
        selection = SimpleNamespace()
        call = {"type": "demo", "text": "hello", TOOL_CAPABILITY_SELECTION_FIELD: selection}

        result = orchestration.validate_legacy_tool_call(
            engine, call, profile_user_id="alice", session_id="session", domain_profile_id="domain"
        )

        self.assertTrue(result.ok)
        engine._resolve_tool_handlers.assert_called_once_with(
            client_context=None,
            profile_user_id="alice",
            session_id="session",
            domain_profile_id="domain",
            capability_selection=selection,
        )

    def test_malformed_wire_arguments_do_not_probe_tool_handlers(self):
        for tool in ("demo", "computer_use"):
            with self.subTest(tool=tool):
                engine = SimpleNamespace(_resolve_tool_handlers=Mock(return_value={}))
                result = orchestration.validate_legacy_tool_call(
                    engine,
                    {
                        "type": tool,
                        TOOL_PARSE_ERROR_FIELD: "invalid_tool_arguments_json",
                        TOOL_RAW_ARGUMENTS_FIELD: '{"action":',
                    },
                )
                self.assertFalse(result.ok)
                self.assertEqual(result.code, "invalid_tool_arguments_json")
                engine._resolve_tool_handlers.assert_not_called()


class ExecutionAdmissionTests(unittest.TestCase):
    def engine_with_revoked_binding(self):
        state = {"available": True}

        class Handler:
            tool_type = "demo"

            def __init__(self):
                self.normalized_inputs = []
                self.executed_calls = []

            def tool_spec(self):
                return CapabilityToolSpec(
                    capability_id=self.tool_type,
                    display_name="Demo",
                    description="Demo tool",
                    input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
                    risk="low",
                    confirm="never",
                    effects=(),
                    visible_in=("desktop",),
                )

            def normalize_call(self, call):
                self.normalized_inputs.append(dict(call))
                state["available"] = False
                return {"type": self.tool_type, "text": str(call.get("text") or "").strip()}

            def execute(self, *, call, context):
                self.executed_calls.append(dict(call))
                return ToolExecutionResult(tool_type=self.tool_type, followup_context="executed")

        handler = Handler()
        snapshot = contract_snapshot(handler, scope="test-scope")
        bound = ContractBoundHandler(
            handler,
            snapshot,
            current_handler=lambda tool_name: handler if tool_name == "demo" and state["available"] else None,
        )
        engine = SimpleNamespace(
            _resolve_tool_handlers=Mock(return_value={"demo": bound}),
        )
        return engine, handler

    def test_handler_loss_after_validation_returns_a_paired_unavailable_result(self):
        engine, handler = self.engine_with_revoked_binding()
        result, envelope = orchestration.execute_tool_invocation(
            engine,
            invocation=ToolInvocation(name="demo", id="call-disappeared", arguments={"text": "hello"}),
            profile_user_id="alice",
            session_id="session",
            visual_payload={},
            now_ts=1,
        )

        self.assertIsNotNone(result)
        self.assertEqual(envelope.invocation_id, "call-disappeared")
        self.assertEqual(envelope.status, "unavailable")
        self.assertEqual(envelope.data["code"], "capability_not_available")
        self.assertEqual(result.stream_events, envelope.events)
        self.assertIn("capability_not_available", result.followup_context)
        self.assertEqual(len(handler.normalized_inputs), 1)
        self.assertEqual(handler.executed_calls, [])
        engine._resolve_tool_handlers.assert_called_once()

    def test_legacy_execution_does_not_drop_handler_loss_feedback(self):
        engine, handler = self.engine_with_revoked_binding()
        result = orchestration.execute_tool_call(
            engine,
            tool_call={"type": "demo", "text": "hello", TOOL_INVOCATION_ID_FIELD: "legacy-disappeared"},
            profile_user_id="alice",
            session_id="session",
            visual_payload={},
            now_ts=1,
        )

        self.assertIsNotNone(result)
        self.assertIn("capability_not_available", result.followup_context)
        self.assertEqual(result.stream_events[0]["reason"], "capability_not_available")
        self.assertEqual(len(handler.normalized_inputs), 1)
        self.assertEqual(handler.executed_calls, [])
        engine._resolve_tool_handlers.assert_called_once()


if __name__ == "__main__":
    unittest.main()
