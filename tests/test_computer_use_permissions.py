"""The desktop gate must honor the same persisted owner policy as other tools."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from companion_v01.computer_use.contracts import COMPUTER_USE_TOOL_SPEC
from companion_v01.computer_use.dispatch import dispatch
from companion_v01.computer_use.session import dispatch_control
from companion_v01.local_capability_config import save_capability_approval_mode
from companion_v01.tool_invocation import ToolInvocation


class ComputerUsePermissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Mock()
        self.store.resolve_grant.return_value = None
        self.engine = SimpleNamespace(capability_config_base_dir=self.root, approval_store=self.store)
        self.mode("master", "trusted_auto_allow")
        self.mode("qq_group_shared_fixture", "ask_each_time")

    def mode(self, profile, mode, capability="ops"):
        self.assertTrue(save_capability_approval_mode(base_dir=self.root, profile_user_id=profile,
            capability_id=capability, mode=mode)["ok"])

    def invoke(self, *, actor="master", action="press_key", required=True, grant=None, after_prepare=None):
        phases = []
        self.store.resolve_grant.return_value = grant
        def execute(**kwargs):
            control = dict(dispatch_control.get())
            phases.append(control)
            self.assertNotIn("permission_mode", kwargs["arguments"])
            if control["phase"] == "prepare":
                if after_prepare:
                    after_prepare()
                return SimpleNamespace(status="succeeded", data={"authorization": {
                    "required": required, "binding": "a" * 64, "preview": {"action": action, "draft": "fixture"}}})
            return SimpleNamespace(status="succeeded", data={"ok": True, "action_state": "executed"})
        invocation = ToolInvocation(id="fixture", name="computer_use", arguments={"action": action},
                                    execution_receipt={"instance_id": "fixture-device"})
        with patch("companion_v01.tool_orchestration_engine._create_satellite_approval_request", return_value="fixture-approval") as create, \
             patch("companion_v01.tool_orchestration_engine._satellite_ask_result", return_value=("ask", "envelope")) as ask, \
             patch("companion_v01.tool_orchestration_engine._satellite_blocked_result", side_effect=lambda spec, call, reason: ("blocked", reason)):
            result, stopped = dispatch(self.engine, broker=SimpleNamespace(execute=execute), spec=COMPUTER_USE_TOOL_SPEC,
                invocation=invocation, profile_user_id="qq_group_shared_fixture", session_id="s", client_context=None,
                request_context={} if actor is None else {"actor_profile_user_id": actor})
        self.assertIsNone(dispatch_control.get())
        return result, stopped, phases, create.call_count, ask.call_count

    def test_full_access_does_not_create_second_approval_for_any_effect_path(self):
        for action in ("click", "press_key", "type_text", "set_value", "set_checked", "scroll", "drag", "launch_app", "manage_window", "run_actions", "run_steps", "resume_steps"):
            with self.subTest(action=action):
                result, stopped, phases, created, asked = self.invoke(action=action)
                self.assertIsNone(stopped)
                self.assertEqual(result.data["action_state"], "executed")
                self.assertEqual(result.data["permission_mode"], "trusted_auto_allow")
                self.assertEqual([p["phase"] for p in phases], ["prepare", "execute"])
                self.assertTrue(all(p["permission_mode"] == "trusted_auto_allow" for p in phases))
                self.assertEqual(phases[1]["approval_binding"], "a" * 64)
                self.assertEqual((created, asked), (0, 0))
        self.store.resolve_grant.assert_not_called()

    def test_group_storage_profile_cannot_replace_actor_policy(self):
        result, stopped, phases, created, asked = self.invoke(actor="qq_group_shared_fixture")
        self.assertIsNone(result)
        self.assertEqual(stopped[0], "ask")
        self.assertEqual(len(phases), 1)
        self.assertEqual((created, asked), (1, 1))
        self.assertEqual(phases[0]["permission_mode"], "ask_each_time")

    def test_explicit_capability_override_wins_over_family_full_access(self):
        self.mode("master", "ask_each_time", "computer_use")
        result, stopped, phases, _, asked = self.invoke()
        self.assertIsNone(result)
        self.assertEqual(stopped[0], "ask")
        self.assertEqual(len(phases), 1)
        self.assertEqual(asked, 1)

    def test_ask_mode_existing_exact_grant_executes_once(self):
        self.mode("master", "ask_each_time")
        result, stopped, phases, created, asked = self.invoke(grant={"scope": "exact-fixture"})
        self.assertIsNone(stopped)
        self.assertEqual(result.data["action_state"], "executed")
        self.assertEqual([p["phase"] for p in phases], ["prepare", "execute"])
        self.assertEqual((created, asked), (0, 0))

    def test_disabled_blocks_before_preflight_even_with_old_grant(self):
        self.mode("master", "disabled", "computer_use")
        result, stopped, phases, created, asked = self.invoke(grant={"old": True}, required=False)
        self.assertIsNone(result)
        self.assertEqual(stopped, ("blocked", "capability_disabled_by_policy"))
        self.assertEqual(phases, [])
        self.assertEqual((created, asked), (0, 0))

    def test_policy_change_during_preflight_does_not_execute_stale_full_access(self):
        result, stopped, phases, _, asked = self.invoke(required=False,
            after_prepare=lambda: self.mode("master", "ask_each_time"))
        self.assertIsNone(result)
        self.assertEqual(stopped, ("blocked", "desktop_permission_changed"))
        self.assertEqual(len(phases), 1)
        self.assertEqual(asked, 0)


if __name__ == "__main__":
    unittest.main()
