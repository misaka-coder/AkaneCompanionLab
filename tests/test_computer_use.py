import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image
from companion_v01.computer_use.contracts import COMPUTER_USE_TOOL_SPEC, normalize_arguments, GUIDE, GUIDE_VERSION
from companion_v01.computer_use.media import extract_images
from companion_v01.computer_use.session import dispatch_scope, scope_key
from companion_v01.tool_handlers.computer_use import ComputerUseToolHandler
from companion_v01.capability_discovery import build_capability_discovery_handlers
from companion_v01.tool_invocation import ToolInvocation
from companion_v01.tool_orchestration_engine import _execute_satellite_with_broker


class ComputerUseTests(unittest.TestCase):
    def test_message_recipient_diagnostics_survive_device_projection(self):
        from companion_v01.computer_use.media import sanitize_device_result
        data = {"ok": False, "reason": "message_recipient_mismatch",
                "message_target": {"status": "conflict", "header_recipient": "Phone",
                                   "editor_label": "Test group", "send_control_status": "identified",
                                   "send_enabled_reported": False},
                "recovery_hint": "尚未生成发送审批", "token": "must-not-project"}
        projected = sanitize_device_result(data)
        self.assertEqual(projected["message_target"], data["message_target"])
        self.assertEqual(projected["recovery_hint"], data["recovery_hint"])
        self.assertNotIn("token", projected)

    def test_send_attempt_and_occlusion_remain_distinct_after_projection(self):
        from companion_v01.computer_use.media import sanitize_device_result
        for state in ("not_started", "unknown"):
            data = {"ok": False, "action_state": state, "reason": "target_occluded",
                    "next_action": "observe_occlusion_then_review_action" if state == "not_started" else "observe_or_status_do_not_replay"}
            self.assertEqual(sanitize_device_result(data), data)
        data = {"ok": True, "action_state": "executed",
                "effect_evidence": {"stage": "send_attempted", "delivery_confirmed": "unavailable"},
                "next_action": "verify_chat_delivery_do_not_replay"}
        self.assertEqual(sanitize_device_result(data), data)

    def test_strict_evidence_and_no_model_supplied_grants(self):
        base = dict(action="click", control_session_id="c", device_epoch="e", observation_id="o")
        self.assertIsNotNone(normalize_arguments(dict(base, screenshot_id="s", x=0, y=3)))
        for bad in (dict(base), dict(base, element_id="a", x=2),
                    dict(base, screenshot_id="s", x=True, y=1), dict(base, element_id="a", approved=True),
                    dict(base, element_id="a", permission_mode="trusted_auto_allow"),
                    dict(base, element_id="a", _permission_mode="trusted_auto_allow")):
            self.assertIsNone(normalize_arguments(bad))
        self.assertIsNone(normalize_arguments(dict(action="type_text", control_session_id="c", device_epoch="e",
                                                 observation_id="o", text="draft\n")))

    def test_rust_contract_hash_matches_host(self):
        source = (Path(__file__).parents[1] / "desktop_pet_next/src-tauri/src/computer_use/protocol.rs").read_text()
        self.assertIn(COMPUTER_USE_TOOL_SPEC.schema_hash, source)

    def test_guide_only_repeats_after_leaving_projection(self):
        handlers, catalog = build_capability_discovery_handlers({"computer_use": ComputerUseToolHandler()},
            profile_user_id="p", session_id="s")
        call = dict(type="capability_load", capability_ids=["computer_use"])
        first = handlers["capability_load"].execute(call=call, context=SimpleNamespace(request_context={}))
        self.assertIn("usage_guide", first.followup_context)
        visible = [{"role": "tool", "content": first.followup_context}]
        second = handlers["capability_load"].execute(call=call, context=SimpleNamespace(request_context={"_host_visible_tool_history": visible}))
        self.assertIn('"already_visible":true', second.followup_context)
        self.assertNotIn(f"computer_use guide v{GUIDE_VERSION}", second.followup_context)
        third = handlers["capability_load"].execute(call=call, context=SimpleNamespace(request_context={}))
        self.assertIn(f"computer_use guide v{GUIDE_VERSION}", third.followup_context)
        self.assertTrue(catalog.load(["computer_use"])["capabilities"][0]["contract_ref"])

    @staticmethod
    def image_data():
        buffer = io.BytesIO(); Image.new("RGB", (2, 3), "white").save(buffer, "PNG")
        return {"ok": True, "action_state": "not_started", "observation_state": "complete", "observation_id": "o",
                "screenshots": [{"screenshot_id": "s", "width": 2, "height": 3,
                                 "imageBase64": base64.b64encode(buffer.getvalue()).decode()}]}

    def test_images_are_ephemeral_bounded_and_removed_even_on_invalid_dimensions(self):
        data = self.image_data(); data["screenshots"].append(dict(data["screenshots"][0]))
        self.assertEqual(len(extract_images(data)), 1)
        self.assertNotIn("imageBase64", json.dumps(data))
        broken = self.image_data(); broken["screenshots"][0]["width"] = 4
        with self.assertRaises(ValueError): extract_images(broken)
        self.assertNotIn("imageBase64", json.dumps(broken))

    def test_broker_receives_trusted_scope_and_keeps_action_fact_if_observation_fails(self):
        seen = []
        def execute(**kwargs):
            seen.append(dispatch_scope.get())
            data = self.image_data(); data["screenshots"][0]["width"] = 4
            data["action_state"] = "executed"
            return SimpleNamespace(status="succeeded", reason="", data=data, model_feedback="")
        engine = SimpleNamespace(executor_broker=SimpleNamespace(execute=execute))
        result, envelope = _execute_satellite_with_broker(engine, spec=COMPUTER_USE_TOOL_SPEC,
            invocation=ToolInvocation(id="call", name="computer_use", arguments={"action":"list_windows"}),
            profile_user_id="p", session_id="s")
        self.assertEqual(seen, [scope_key("p", "s")]); self.assertEqual(dispatch_scope.get(), "")
        self.assertEqual(envelope.data["result"]["action_state"], "executed")
        self.assertEqual(envelope.data["result"]["observation_state"], "failed")
        self.assertNotIn("imageBase64", result.followup_context)

    def test_non_owner_cannot_dispatch_desktop_reads(self):
        engine = SimpleNamespace(executor_broker=SimpleNamespace(execute=lambda **_: self.fail("must not dispatch")))
        with patch("config.MASTER_QQ", "12345"):
            _, envelope = _execute_satellite_with_broker(engine, spec=COMPUTER_USE_TOOL_SPEC,
                invocation=ToolInvocation(id="call", name="computer_use", arguments={"action":"list_windows"}),
                profile_user_id="group", session_id="s", request_context={"qq_delivery_context":{"user_id":"67890","is_group":True}})
        self.assertEqual(envelope.data["code"], "device_action_requires_owner")


if __name__ == "__main__": unittest.main()
