"""Frozen acceptance cases and model-visible errors through the actual host path."""
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from companion_v01.computer_use import contracts
from companion_v01.capability_contracts import ContractBoundHandler, contract_snapshot
from companion_v01.native_tool_schema import build_openai_native_tool_from_spec
from companion_v01.tool_handlers.computer_use import ComputerUseToolHandler
from companion_v01.tool_invocation import NATIVE_OPENAI, TOOL_SOURCE_FIELD
from companion_v01.tool_orchestration_engine import normalize_tool_invocation, execute_tool_invocation

FIXTURE = Path(__file__).parent / "fixtures/computer_use_contract_cases.json"


class ComputerUseContractDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.handler = ComputerUseToolHandler()
        self.broker = Mock()
        self.broker.execute.side_effect = AssertionError("invalid calls must not dispatch")
        self.engine = SimpleNamespace(tool_handlers={"computer_use": self.handler}, executor_broker=self.broker)
        self.engine._resolve_tool_handlers = lambda **_: self.engine.tool_handlers

    def execute(self, arguments, **metadata):
        call = {"type": "computer_use", **arguments, TOOL_SOURCE_FIELD: NATIVE_OPENAI, **metadata}
        invocation = normalize_tool_invocation(self.engine, call)
        self.assertIsNotNone(invocation)
        result, envelope = execute_tool_invocation(self.engine, invocation=invocation,
            profile_user_id="test-owner", session_id="test-session", visual_payload={}, now_ts=1)
        return invocation, result, envelope

    def test_frozen_acceptance_and_error_paths(self):
        rows = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
        for row in rows:
            with self.subTest(case=row["id"]):
                issue = contracts.argument_error(row["arguments"])
                self.assertEqual(issue is None, row["accepted"])
                normalized = contracts.normalize_arguments(row["arguments"])
                if row["accepted"]:
                    self.assertEqual(normalized, row["arguments"])
                else:
                    self.assertIsNone(normalized)
                    self.assertEqual(issue["path"], row["error_path"])
                    self.assertTrue(issue["reason"])
                    self.assertTrue(issue["expected"])

    def test_all_invalid_objects_survive_native_normalization_with_precise_error(self):
        for row in json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]:
            if row["accepted"] or not isinstance(row["arguments"], dict):
                continue
            with self.subTest(case=row["id"]):
                invocation, result, envelope = self.execute(row["arguments"])
                self.assertEqual(invocation.arguments, row["arguments"])
                self.assertEqual(envelope.data["code"], "bad_args")
                details = envelope.data["validation"]
                self.assertEqual(details["error"]["path"], row["error_path"])
                self.assertEqual(details["action_state"], "not_started")
                self.assertFalse(details["dispatched"])
                self.assertIn(row["error_path"], result.followup_context)
        self.broker.execute.assert_not_called()

    def test_internal_failure_is_not_bad_args_and_does_not_echo_exception_payload(self):
        with patch.object(contracts, "argument_error", side_effect=RuntimeError("synthetic-sensitive-value")) as validator:
            _, result, envelope = self.execute({"action": "status"})
        self.assertEqual(validator.call_count, 1)
        self.assertEqual(envelope.data["code"], "validator_internal_error")
        self.assertEqual(envelope.data["validation"]["exception_type"], "RuntimeError")
        self.assertNotIn("synthetic-sensitive-value", result.followup_context)
        self.broker.execute.assert_not_called()

    def test_error_does_not_echo_values_or_unrecognized_field_names(self):
        _, result, _ = self.execute({"action": "status", "synthetic-secret-key": "synthetic-secret-value"})
        self.assertNotIn("synthetic-secret", result.followup_context)
        _, result, _ = self.execute({"action": "type_text", "control_session_id": "c", "device_epoch": "e",
            "observation_id": "o", "text": "synthetic-secret-value\n"})
        self.assertNotIn("synthetic-secret", result.followup_context)

    def test_malformed_provider_arguments_are_not_echoed(self):
        from companion_v01.tool_invocation import TOOL_PARSE_ERROR_FIELD, TOOL_RAW_ARGUMENTS_FIELD
        _, result, envelope = self.execute({}, **{TOOL_PARSE_ERROR_FIELD: "invalid_json",
            TOOL_RAW_ARGUMENTS_FIELD: '{"text":"synthetic-secret-value"'})
        self.assertEqual(envelope.data["validation"]["error"]["path"], "$")
        self.assertNotIn("synthetic-secret", result.followup_context)
        self.broker.execute.assert_not_called()

    def test_offline_valid_call_is_not_a_parameter_error(self):
        _, result, envelope = self.execute({"action": "list_windows"})
        self.assertEqual(envelope.data["code"], "not_available")
        self.assertNotIn("bad_args", result.followup_context)
        self.broker.execute.assert_not_called()

    def test_nested_known_but_disallowed_field_keeps_its_public_name(self):
        _, _, envelope = self.execute(dict(action="run_actions", control_session_id="c", device_epoch="e",
            observation_id="o", screenshot_id="s", actions=[dict(action="type_text",text="test",element_id="private-value")]))
        self.assertEqual(envelope.data["validation"]["error"]["path"], "actions[0].element_id")
        self.assertNotIn("private-value", envelope.model_feedback)

    def test_stale_contract_precedes_argument_validation(self):
        current = contract_snapshot(self.handler, scope="synthetic-scope")
        old = {**current, "contract_ref": "synthetic-old-contract"}
        self.engine.tool_handlers["computer_use"] = ContractBoundHandler(self.handler, old, current_contract=lambda _: current)
        with patch.object(contracts, "argument_error", side_effect=AssertionError("must not interpret old arguments")) as validator:
            _, result, envelope = self.execute({"action": "old-action"})
        self.assertEqual(envelope.data["code"], "capability_contract_stale")
        self.assertIn("capability_load", result.followup_context)
        validator.assert_not_called()
        self.broker.execute.assert_not_called()

    def test_provider_schema_preserves_action_rules_without_conditional_dialect(self):
        schema = build_openai_native_tool_from_spec(contracts.COMPUTER_USE_TOOL_SPEC)["function"]["parameters"]
        self.assertEqual(schema, contracts.INPUT_SCHEMA)
        for action in contracts.ACTIONS:
            self.assertIn(action + ":", schema["properties"]["action"]["description"])
        self.assertIn("CR", schema["properties"]["actions"]["items"]["properties"]["text"]["description"])
        self.assertIn("window_title", schema["properties"]["steps"]["items"]["properties"]["expect"]["properties"]["kind"]["description"])
        serialized = json.dumps(schema)
        for unsupported in ('"oneOf"', '"if"', '"then"', '"allOf"'):
            self.assertNotIn(unsupported, serialized)

    def test_error_survives_memcore_and_provider_request_serialization(self):
        from companion_v01.engine import AkaneMemoryEngine
        from companion_v01.llm_runtime import LLMRuntime
        from companion_v01.memcore_integration.manager import MemcoreManager
        from companion_v01.tool_invocation import TOOL_INVOCATION_ID_FIELD
        from services.llm_client import _build_anthropic_payload
        from services.gemini_native_client import _build_gemini_payload
        from tests.test_memcore_integration import _FakeLLM, _FakeEmbeddingProvider

        args = dict(action="run_actions", control_session_id="c", device_epoch="e", observation_id="o",
                    screenshot_id="s", actions=[dict(action="click",x=1,y=1),dict(action="type_text",text="synthetic\ntext")])
        invocation, result, _ = self.execute(args)
        identity = dict(profile_user_id="test-owner", session_id="test-session", character_pack_id="test-character")
        with tempfile.TemporaryDirectory() as directory:
            manager = MemcoreManager(backend="memcore", storage_path=Path(directory)/"memory.db", visible_scope="conversation",
                enable_flavor=True, shadow_compare=False, llm=_FakeLLM(), embedding_provider=_FakeEmbeddingProvider())
            try:
                engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
                engine.memcore_manager = manager
                opened = manager.begin_input_turn({"source_id":"test-user", "content":"操作合成窗口", "timestamp":1}, **identity)
                batch = manager.record_tool_batch(exchanges=[{"tool_name":"computer_use", "tool_call_id":invocation.id,
                    "tool_input":args, "result":result.followup_context, "source":"computer_use", "timestamp":2,
                    "source_id_prefix":"test-error", "result_status":"error"}], turn_id=opened["turn_id"], **identity)
                trace = [source for entry in batch["exchanges"] for source in (entry["tool_use_source_id"], entry["tool_result_source_id"])]
                history = []
                call = {"type":"computer_use", **args, TOOL_SOURCE_FIELD:NATIVE_OPENAI, TOOL_INVOCATION_ID_FIELD:invocation.id}
                projection = engine._append_tool_history_batch(tool_history_turns=history,
                    items=[(call,result,result.followup_context)], trace_source_ids=trace, provider_profile="responses",
                    memcore_turn_id=opened["turn_id"],current_user_source_id=opened["source_id"], **identity)
                self.assertTrue(projection["ok"], projection)
                schema = build_openai_native_tool_from_spec(contracts.COMPUTER_USE_TOOL_SPEC)
                schema.pop("_akane_capability_id", None)
                payload = {"model":"test-no-network", "max_tokens":1024, "messages":[{"role":"user","content":"操作合成窗口"},*history], "tools":[schema]}
                runtime = LLMRuntime.__new__(LLMRuntime)
                for name, wire in (("chat",payload), ("responses",runtime._responses_payload_from_chat(payload)),
                                   ("anthropic",_build_anthropic_payload(payload)), ("gemini",_build_gemini_payload(payload))):
                    with self.subTest(provider=name):
                        text = json.dumps(wire,ensure_ascii=False)
                        self.assertIn("actions[1].text", text)
                        self.assertIn("unsupported_text_character_LF", text)
                        self.assertIn("not_started", text)
                        self.assertIn("desktop_x/desktop_y", text)
            finally:
                manager.close()


if __name__ == "__main__":
    unittest.main()
