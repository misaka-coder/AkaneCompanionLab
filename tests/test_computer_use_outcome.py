"""Transport failures must remain observable without inventing input outcomes."""
import json
import unittest
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import patch

from companion_v01.computer_use.contracts import COMPUTER_USE_TOOL_SPEC
from companion_v01.computer_use.dispatch import dispatch
from companion_v01.computer_use.outcome import complete_outcome
from companion_v01.executor_broker import BrokerExecutionResult
from companion_v01.tool_invocation import ToolInvocation
from companion_v01.tool_orchestration_engine import _execute_satellite_with_broker
from companion_v01.computer_use.media import sanitize_device_result
from tests.test_computer_use import ComputerUseTests


class ComputerUseOutcomeTests(unittest.TestCase):
    def test_empty_transport_failure_reaches_actual_model_feedback(self):
        for status, reason in (("unavailable_before_dispatch", "offer_expired"),
                               ("execution_unknown", "executor_result_timeout")):
            engine = SimpleNamespace(executor_broker=SimpleNamespace(execute=lambda **_: BrokerExecutionResult(
                status=status, reason=reason)))
            result, envelope = _execute_satellite_with_broker(engine, spec=COMPUTER_USE_TOOL_SPEC,
                invocation=ToolInvocation(id="read-operation", name="computer_use", arguments={"action": "list_windows"},
                                          execution_receipt={"lease_epoch": "epoch"}),
                profile_user_id="p", session_id="s")
            data = json.loads(result.followup_context.split("实际返回数据：", 1)[1])
            self.assertFalse(data["ok"])
            self.assertEqual(data["reason"], reason)
            self.assertEqual(data["execution_status"], status)
            self.assertEqual(data["action_state"], "not_started")
            self.assertEqual(data["operation_id"], "read-operation")
            self.assertEqual(data["device_epoch"], "epoch")
            self.assertEqual(envelope.status, "error")

    def test_unknown_effect_never_becomes_not_started_or_automatic_retry(self):
        data, status, _ = complete_outcome({}, status="execution_unknown",
            reason="executor_disconnected_after_accept", action="run_actions")
        self.assertEqual(status, "execution_unknown")
        self.assertEqual(data["action_state"], "unknown")
        self.assertEqual(data["next_action"], "status_or_observe_do_not_replay")

    def test_preflight_failure_names_the_real_operation_and_never_dispatches_input(self):
        calls = []
        def execute(**kwargs):
            calls.append(kwargs["invocation_id"])
            return BrokerExecutionResult(status="execution_unknown", reason="executor_result_timeout")
        invocation = ToolInvocation(id="effect", name="computer_use", arguments={"action": "run_actions"},
                                    execution_receipt={"lease_epoch": "epoch"})
        with patch("companion_v01.capcore_runtime.approval_policy_for_capability",
                   return_value=SimpleNamespace(default_mode="trusted_auto_allow")):
            result, stopped = dispatch(SimpleNamespace(), broker=SimpleNamespace(execute=execute),
                spec=COMPUTER_USE_TOOL_SPEC, invocation=invocation, profile_user_id="p", session_id="s",
                client_context=None, request_context={})
        self.assertIsNone(stopped)
        self.assertEqual(calls, ["prepare_" + sha256(b"effect").hexdigest()])
        self.assertEqual(result.data["operation_id"], calls[0])
        data, _, _ = complete_outcome(result.data, status=result.status, reason=result.reason, action="run_actions")
        self.assertEqual(data["action_state"], "not_started")
        self.assertEqual(data["dispatch_phase"], "prepare")

    def test_device_execution_and_checkpoint_facts_survive_failure(self):
        native = dict(ok=False, action_state="executed", observation_state="failed", reason="capture_timeout",
                      workflow_id="flow", completed_steps=2, next_action="observe")
        data, _, _ = complete_outcome(native, status="failed", reason="capture_timeout", action="run_actions")
        for key, value in native.items():
            self.assertEqual(data[key], value)

    def test_success_without_device_contract_is_not_reported_as_completed(self):
        data, status, reason = complete_outcome({}, status="succeeded", reason="", action="click")
        self.assertFalse(data["ok"])
        self.assertEqual(data["action_state"], "unknown")
        self.assertEqual((status, reason), ("execution_unknown", "desktop_result_incomplete"))

    def test_failed_preflight_delivers_new_pixels_and_reference_without_dispatching_input(self):
        data = ComputerUseTests.image_data()
        data.update(ok=False, reason="window_layout_changed", context_ref="ctx-new",
                    control_session_id="control", window_id="window", next_action="review_current_observation_then_replan")
        data["token"] = "must-not-project"
        calls = []
        def execute(**kwargs):
            calls.append(kwargs)
            return BrokerExecutionResult(status="failed", reason="window_layout_changed",
                                         data=sanitize_device_result(data))
        engine = SimpleNamespace(executor_broker=SimpleNamespace(execute=execute))
        invocation = ToolInvocation(id="stale-input", name="computer_use",
            arguments={"action":"click", "context_ref":"ctx-old", "x":1, "y":1},
            execution_receipt={"lease_epoch":"epoch"})
        with patch("companion_v01.capcore_runtime.approval_policy_for_capability",
                   return_value=SimpleNamespace(default_mode="trusted_auto_allow")):
            result, envelope = _execute_satellite_with_broker(engine, spec=COMPUTER_USE_TOOL_SPEC,
                invocation=invocation, profile_user_id="p", session_id="s",
                request_context={"_model_execution_target":SimpleNamespace(reason="chat_supports_images")})
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["invocation_id"].startswith("prepare_"))
        self.assertEqual(invocation.arguments["context_ref"], "ctx-old")
        self.assertEqual(envelope.status, "error")
        actual = envelope.data["result"]
        self.assertFalse(actual["ok"])
        self.assertEqual(actual["action_state"], "not_started")
        self.assertEqual(actual["observation_state"], "complete")
        self.assertEqual(actual["reason"], "window_layout_changed")
        self.assertEqual(actual["context_ref"], "ctx-new")
        self.assertEqual(actual["model_image_count"], 1)
        self.assertEqual(len(result.model_image_inputs), 1)
        self.assertTrue(result.model_image_inputs[0]["data_url"].startswith("data:image/png;base64,"))
        self.assertNotIn("imageBase64", result.followup_context)
        self.assertNotIn("must-not-project", result.followup_context)

    def test_waiting_observation_gets_transport_headroom_without_preflight(self):
        for wait_ms, timeout in ((0, 15.0), (15000, 30.0)):
            with self.subTest(wait_ms=wait_ms):
                calls = []
                def execute(**kwargs):
                    calls.append(kwargs)
                    return BrokerExecutionResult(status="succeeded", data=ComputerUseTests.image_data())
                result, stopped = dispatch(SimpleNamespace(), broker=SimpleNamespace(execute=execute),
                    spec=COMPUTER_USE_TOOL_SPEC, invocation=ToolInvocation(id="wait", name="computer_use",
                        arguments={"action":"observe", "context_ref":"ctx", "wait_ms":wait_ms}),
                    profile_user_id="p", session_id="s", client_context=None, request_context={})
                self.assertIsNone(stopped)
                self.assertEqual(result.status, "succeeded")
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0]["invocation_id"], "wait")
                self.assertEqual(calls[0].get("timeout_seconds", 15.0), timeout)


if __name__ == "__main__":
    unittest.main()
