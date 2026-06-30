from __future__ import annotations

import unittest

from companion_v01.local_capability_config import (
    APPROVAL_MODE_ASK_EACH_TIME,
    APPROVAL_MODE_DISABLED,
    APPROVAL_MODE_TRUSTED_AUTO_ALLOW,
    apply_approval_policy_to_entry,
    capability_approval_mode,
    with_capability_approval_metadata,
)


class LocalCapabilityApprovalTests(unittest.TestCase):
    def test_capability_approval_mode_preserves_legacy_public_modes(self) -> None:
        self.assertEqual(capability_approval_mode(risk="low"), APPROVAL_MODE_TRUSTED_AUTO_ALLOW)
        self.assertEqual(capability_approval_mode(risk="high"), APPROVAL_MODE_ASK_EACH_TIME)
        self.assertEqual(capability_approval_mode(enabled=False, risk="high"), APPROVAL_MODE_DISABLED)

    def test_entry_approval_metadata_uses_capcore_effect_requirement(self) -> None:
        entry = {
            "id": "tool.browser_control",
            "name": "Browser Control",
            "enabled": True,
            "status": "ready",
            "risk": "low",
            "requiresConfirmation": False,
            "effects": ["browser_action"],
        }

        public_entry = with_capability_approval_metadata(entry)

        self.assertEqual(public_entry["approvalMode"], APPROVAL_MODE_ASK_EACH_TIME)
        self.assertEqual(public_entry["approvalReason"], "requires_confirmation")

    def test_trusted_policy_auto_allows_required_entry_without_enabling_disabled_entry(self) -> None:
        trusted = apply_approval_policy_to_entry(
            {
                "id": "mcp.browser.browser_click",
                "name": "browser_click",
                "enabled": True,
                "status": "ready",
                "risk": "high",
                "requiresConfirmation": True,
            },
            {"defaultMode": APPROVAL_MODE_TRUSTED_AUTO_ALLOW},
        )
        disabled = apply_approval_policy_to_entry(
            {
                "id": "workflow.cutout",
                "name": "cutout",
                "enabled": False,
                "status": "missing_config",
                "risk": "medium",
                "requiresConfirmation": True,
            },
            {"defaultMode": APPROVAL_MODE_TRUSTED_AUTO_ALLOW},
        )

        self.assertEqual(trusted["approvalMode"], APPROVAL_MODE_TRUSTED_AUTO_ALLOW)
        self.assertEqual(trusted["approvalReason"], "user_policy_trusted_auto_allow")
        self.assertFalse(trusted["requiresConfirmation"])
        self.assertEqual(disabled["approvalMode"], APPROVAL_MODE_DISABLED)


if __name__ == "__main__":
    unittest.main()
