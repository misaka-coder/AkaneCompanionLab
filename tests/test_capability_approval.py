from __future__ import annotations

import unittest

from companion_v01.capability_approval import CapabilityApprovalStore, normalize_approval_request_payload


class CapabilityApprovalTests(unittest.TestCase):
    def test_payload_preview_uses_capcore_redaction_and_drops_secret_keys(self) -> None:
        normalized = normalize_approval_request_payload(
            {
                "capabilityId": "mcp.browser.browser_click",
                "actionId": "browser_click",
                "risk": "high",
                "approvalMode": "ask_each_time",
                "payloadPreview": {
                    "selector": "#play",
                    "localPath": r"C:\Users\ExampleUser\secret.txt",
                    "api_key": "real-secret-value",
                    "nested": {"token": "real-token", "label": "public"},
                },
            }
        )

        self.assertTrue(normalized["ok"])
        preview = normalized["payloadPreview"]
        self.assertEqual(preview["selector"], "#play")
        self.assertEqual(preview["localPath"], "[local_path]")
        self.assertNotIn("api_key", preview)
        self.assertEqual(preview["nested"], {"type": "object", "keys": ["label"]})

    def test_public_request_keeps_preview_summaries_idempotent(self) -> None:
        store = CapabilityApprovalStore()

        created = store.create_request(
            profile_user_id="alice",
            session_id="s1",
            payload={
                "capabilityId": "mcp.browser.browser_click",
                "actionId": "browser_click",
                "risk": "high",
                "approvalMode": "ask_each_time",
                "payloadPreview": {
                    "nested": {"token": "real-token", "label": "public"},
                },
            },
        )
        listed = store.list_requests(profile_user_id="alice")

        self.assertTrue(created["ok"])
        self.assertEqual(created["request"]["payloadPreview"]["nested"], {"type": "object", "keys": ["label"]})
        self.assertEqual(
            listed["approvalRequests"][0]["payloadPreview"]["nested"],
            {"type": "object", "keys": ["label"]},
        )


if __name__ == "__main__":
    unittest.main()
