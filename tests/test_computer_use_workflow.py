"""Workflow contract and the existing host approval/transport integration."""
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from companion_v01.computer_use.contracts import normalize_arguments, COMPUTER_USE_TOOL_SPEC
from companion_v01.computer_use.dispatch import dispatch
from companion_v01.computer_use.media import sanitize_device_result
from companion_v01.computer_use.session import dispatch_control
from companion_v01.tool_invocation import ToolInvocation
from companion_v01.tool_handlers.core import ToolExecutionResult


class ComputerUseWorkflowTests(unittest.TestCase):
    @staticmethod
    def plan():
        return dict(action="run_steps", control_session_id="session", device_epoch="epoch", observation_id="observation",
                    steps=[dict(action="click", target={"name": "Search", "role": 50004},
                                expect={"kind": "focused", "target": {"name": "Search", "role": 50004}}),
                           dict(action="type_text", text="example", expect={"kind": "value_equals", "target": {"focused": True}, "value": "example"})])

    def test_bounded_plan_rejects_ambiguous_contracts_and_embedded_grants(self):
        valid = self.plan()
        self.assertIsNotNone(normalize_arguments(valid))
        for mutate in [lambda v: v.update(steps=v["steps"] * 7),
                       lambda v: v["steps"][0].update(approved=True),
                       lambda v: v["steps"][0].pop("expect"),
                       lambda v: v["steps"][0].update(target={}),
                       lambda v: v["steps"][0].update(wait_ms=50000),
                       lambda v: v["steps"][0].update(action="run_steps"),
                       lambda v: v["steps"][0]["expect"].update(kind="whatever"),
                       lambda v: v["steps"][1].update(text="submit\n"),
                       lambda v: v["steps"][0].update(click_count=True)]:
            bad = copy.deepcopy(valid); mutate(bad)
            self.assertIsNone(normalize_arguments(bad), str(bad))

    def test_setting_and_mouse_contracts(self):
        scope = dict(control_session_id="c", device_epoch="e", observation_id="o")
        for args in [dict(action="set_value", element_id="el", text=""),
                     dict(action="set_checked", element_id="el", checked=False),
                     dict(action="click", element_id="el", button="right", click_count=2),
                     dict(action="drag", screenshot_id="s", x=0, y=1, to_x=12, to_y=15, duration_ms=300),
                     dict(action="resume_steps", workflow_id="w")]:
            self.assertIsNotNone(normalize_arguments({**scope, **args}))
        self.assertIsNone(normalize_arguments({**scope, "action": "set_checked", "element_id": "el", "checked": 1}))

    def test_workflow_progress_survives_wire_cleaning(self):
        data = sanitize_device_result(dict(ok=True, workflow_id="w", workflow_state="paused", next_step=1, completed_steps=1,
                                          steps=[{"index": 0, "state": "verified"}, {"index": 1, "state": "executed"}], secret="omit"))
        self.assertEqual(data["steps"][1]["state"], "executed")
        self.assertNotIn("secret", data)

    def test_future_step_approval_retains_completed_progress_and_does_not_reexecute(self):
        phases = []
        def execute(**kwargs):
            phases.append(dict(dispatch_control.get()))
            self.assertEqual(kwargs["timeout_seconds"], 30.0)
            if phases[-1]["phase"] == "prepare":
                return SimpleNamespace(status="succeeded", data={"authorization": {"required": False, "binding": "a" * 64}})
            return SimpleNamespace(status="succeeded", data=dict(ok=True, action_state="executed", observation_state="complete",
                workflow_id="workflow", workflow_state="awaiting_approval", next_step=1, completed_steps=1,
                steps=[{"state":"verified"},{"state":"pending"}],
                authorization={"required":True,"binding":"b"*64,"preview":{"action":"press_key","key":"Enter","workflow":{"id":"workflow","step":1}}}))
        engine = SimpleNamespace(approval_store=None)
        decision = SimpleNamespace(allowed=False, requires_user_decision=True, reason="confirmation")
        # Real decision type is immutable; the test only substitutes policy storage/UI.
        from dataclasses import dataclass
        @dataclass(frozen=True)
        class Decision:
            allowed: bool = False
            requires_user_decision: bool = True
            mode: str = "ask_each_time"
        ask = (ToolExecutionResult(tool_type="computer_use", followup_context="approval"), SimpleNamespace(data={}))
        with patch("companion_v01.capcore_runtime.resolve_permission_for_profile",
                   side_effect=lambda request, **_: Decision(allowed=not request.required, requires_user_decision=request.required)), \
             patch("companion_v01.tool_orchestration_engine._satellite_ask_result", return_value=ask):
            result, stopped = dispatch(engine, broker=SimpleNamespace(execute=execute), spec=COMPUTER_USE_TOOL_SPEC,
                invocation=ToolInvocation(id="batch", name="computer_use", arguments=self.plan()),
                profile_user_id="owner", session_id="s", client_context=None, request_context={})
        self.assertIsNone(result)
        self.assertEqual([p["phase"] for p in phases], ["prepare", "execute"])
        self.assertEqual(stopped[1].data["result"]["completed_steps"], 1)
        self.assertIn("resume_steps", stopped[0].followup_context)
        self.assertIsNone(dispatch_control.get())


if __name__ == "__main__":
    unittest.main()
