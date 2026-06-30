from __future__ import annotations

from types import SimpleNamespace
import tempfile
import unittest

from companion_v01.capcore_runtime import (
    approval_required_event,
    invocation_context_from_execution,
    manual_permission_request,
    resolve_permission_for_profile,
    sanitize_permission_preview,
)


class CapcoreRuntimeTests(unittest.TestCase):
    def test_manual_permission_request_uses_execution_context(self) -> None:
        context = SimpleNamespace(profile_user_id="alice", session_id="s1", client_mode="desktop_pet")

        request = manual_permission_request(
            context=context,
            required=True,
            capability_id="tool.browser_page",
            display_name="Browser Page",
            risk="high",
            confirm="always",
            effects=("browser_action",),
            reason="browser_control_requires_approval",
            args_preview={"action": "click"},
        )

        invocation_context = invocation_context_from_execution(context)
        self.assertEqual(invocation_context.profile_user_id, "alice")
        self.assertEqual(request.profile_user_id, "alice")
        self.assertEqual(request.session_id, "s1")
        self.assertEqual(request.client_mode, "desktop_pet")
        self.assertEqual(request.args_preview, {"action": "click"})

    def test_permission_preview_redacts_secret_fields_and_local_paths(self) -> None:
        preview = sanitize_permission_preview(
            {
                "action": "upload",
                "api_key": "plain-secret-value",
                "localPath": r"C:\Users\ExampleUser\secret.txt",
                "url": "https://example.com/callback?token=secret-value",
                "nested": {
                    "token": "nested-secret",
                    "label": "公开标签",
                },
            }
        )

        self.assertEqual(preview["action"], "upload")
        self.assertEqual(preview["api_key"], "[redacted]")
        self.assertEqual(preview["localPath"], "[local_path]")
        self.assertEqual(preview["url"], "https://example.com/callback?token=[redacted]")
        self.assertEqual(preview["nested"], {"type": "object", "keys": ["token", "label"]})

    def test_resolve_and_event_shape_match_akane_approval_contract(self) -> None:
        context = SimpleNamespace(profile_user_id="alice", session_id="s1", client_mode="desktop_pet")
        request = manual_permission_request(
            context=context,
            required=True,
            capability_id="tool.browser_page",
            display_name="Browser Page",
            risk="high",
            confirm="always",
            effects=("browser_action",),
            reason="browser_control_requires_approval",
            args_preview={"action": "click", "localPath": r"C:\Users\ExampleUser\secret.txt"},
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            decision = resolve_permission_for_profile(request, base_dir=temp_dir, profile_user_id="alice")

        event = approval_required_event(
            decision=decision,
            capability_id="tool.browser_page",
            action_id="browser_page.click",
            title="浏览器控制需要确认",
            summary="Akane 想对托管网页执行点击、输入或按键动作。",
            client_mode="desktop_pet",
        )

        self.assertFalse(decision.allowed)
        self.assertTrue(decision.requires_user_decision)
        self.assertEqual(event["type"], "capability_approval_required")
        self.assertEqual(event["risk"], "high")
        self.assertEqual(event["approvalMode"], "ask_each_time")
        self.assertEqual(event["approvalReason"], "browser_control_requires_approval")
        self.assertEqual(event["payloadPreview"], {"action": "click", "localPath": "[local_path]"})


if __name__ == "__main__":
    unittest.main()
