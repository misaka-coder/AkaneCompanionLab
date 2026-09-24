"""Visual plans share single-input validation and preserve observable outcomes."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from companion_v01.computer_use.contracts import COMPUTER_USE_TOOL_SPEC, normalize_arguments
from companion_v01.computer_use.dispatch import dispatch
from companion_v01.computer_use.media import sanitize_device_result
from companion_v01.tool_invocation import ToolInvocation


class VisualBatchContractTests(unittest.TestCase):
    def plan(self):
        return dict(action="run_actions", control_session_id="session", device_epoch="epoch",
                    observation_id="observation", screenshot_id="shot", actions=[
                        dict(action="click", x=120, y=40), dict(action="press_key", key="Ctrl+A"),
                        dict(action="type_text", text="晴天"), dict(action="press_key", key="Enter")])

    def test_visual_plan_needs_no_uia_or_future_observation(self):
        plan = self.plan()
        self.assertEqual(normalize_arguments(plan), plan)
        for action in (dict(action="scroll", x=2, y=3, direction="down"),
                       dict(action="drag", x=2, y=3, to_x=200, to_y=100)):
            plan["actions"] = [action]
            self.assertEqual(normalize_arguments(plan), plan)

    def test_rejects_injected_metadata_and_invalid_partial_actions(self):
        changes = [lambda p: p.pop("screenshot_id"), lambda p: p.update(actions=[]),
                   lambda p: p.update(actions=p["actions"] * 2),
                   lambda p: p["actions"][0].update(_permission_mode="trusted_auto_allow"),
                   lambda p: p["actions"][0].update(observation_id="future"),
                   lambda p: p["actions"][0].update(x=True), lambda p: p["actions"][0].pop("y"),
                   lambda p: p["actions"][2].update(text="hello\n"),
                   lambda p: p["actions"][1].update(action="run_actions"),
                   lambda p: p["actions"][1].update(key="x" * 41)]
        for change in changes:
            plan = copy.deepcopy(self.plan())
            change(plan)
            with self.subTest(plan=plan):
                self.assertIsNone(normalize_arguments(plan))

    def test_window_actions_have_separate_screen_coordinates(self):
        base = dict(action="manage_window", window_id="window", device_epoch="epoch")
        for action in ("activate", "restore", "minimize"):
            self.assertIsNotNone(normalize_arguments(dict(base, window_action=action)))
            self.assertIsNone(normalize_arguments(dict(base, window_action=action, desktop_x=10)))
        self.assertIsNotNone(normalize_arguments(dict(base, window_action="move", desktop_x=-900, desktop_y=0)))
        self.assertIsNone(normalize_arguments(dict(base, window_action="move", desktop_x=10)))
        self.assertIsNone(normalize_arguments(dict(base, window_action="move", desktop_x=True, desktop_y=0)))

    def test_media_projection_preserves_partial_execution_and_occluder(self):
        data = dict(ok=False, action_state="executed", observation_state="failed",
                    observation_reason="capture_timeout", workflow_kind="visual_actions", task_verified=False,
                    steps=[dict(index=0, state="executed", action_state="executed"),
                           dict(index=1, state="pending", action_state="not_started")],
                    occluder=dict(window_id="other", relationship="owned_popup", minimized=False),
                    window_state=dict(minimized=True))
        self.assertEqual(sanitize_device_result(data), data)

    def test_busy_recovery_survives_projection_without_owner_identity(self):
        data = dict(ok=False, reason="desktop_owned_by_other_session", retry_after_ms=42000,
                    next_action="wait_then_reselect", recovery_hint="wait then select")
        self.assertEqual(sanitize_device_result(dict(data, owner_scope="private", owner_profile="private")), data)

    def test_visual_resume_requires_images_but_semantic_resume_does_not(self):
        for kind in ("visual_actions", "verified_steps"):
            for vision in (False, True):
                phases = []
                def execute(**kwargs):
                    phases.append(kwargs["invocation_id"])
                    return SimpleNamespace(status="succeeded", data={"workflow_kind": kind,
                        "authorization": {"required": False, "binding": "a" * 64, "preview": {}}})
                call = ToolInvocation(id="resume", name="computer_use", arguments={"action": "resume_steps"}, execution_receipt={})
                with patch("companion_v01.capcore_runtime.approval_policy_for_capability", return_value=SimpleNamespace(default_mode="trusted_auto_allow")), \
                     patch("companion_v01.capcore_runtime.resolve_permission_for_profile", return_value=SimpleNamespace(allowed=True, mode="trusted_auto_allow")), \
                     patch("companion_v01.tool_orchestration_engine._satellite_blocked_result", side_effect=lambda spec, call, reason: ("blocked", reason)):
                    result, stopped = dispatch(SimpleNamespace(capability_config_base_dir=None), broker=SimpleNamespace(execute=execute),
                        spec=COMPUTER_USE_TOOL_SPEC, invocation=call, profile_user_id="master", session_id="s", client_context=None,
                        request_context={"_model_execution_target": SimpleNamespace(role="vision" if vision else "chat", reason="")})
                if kind == "visual_actions" and not vision:
                    self.assertIsNone(result)
                    self.assertEqual(stopped, ("blocked", "coordinate_requires_visual_model"))
                    self.assertEqual(len(phases), 1)
                else:
                    self.assertIsNone(stopped)
                    self.assertEqual(len(phases), 2)


if __name__ == "__main__":
    unittest.main()
