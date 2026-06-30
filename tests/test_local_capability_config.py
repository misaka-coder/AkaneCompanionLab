from __future__ import annotations

import unittest

from companion_v01.local_capability_config import (
    APPROVAL_MODE_ASK_EACH_TIME,
    APPROVAL_MODE_DISABLED,
    APPROVAL_MODE_TRUSTED_AUTO_ALLOW,
    apply_approval_policy_to_entry,
    build_mcp_tool_config_entry,
    capability_approval_mode,
    project_capcore_catalog_fields,
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

    def test_catalog_projection_normalizes_capcore_fields(self) -> None:
        entry = project_capcore_catalog_fields(
            {
                "id": "mcp.browser.click",
                "name": "Browser Click",
                "risk": "low",
                "confirm": "never",
                "effects": ["browserAction"],
            },
            default_risk="medium",
            default_confirm="first_time",
        )

        self.assertEqual(entry["risk"], "high")
        self.assertEqual(entry["confirm"], "always")
        self.assertTrue(entry["requiresConfirmation"])
        self.assertEqual(entry["effects"], ["browser_action"])

    def test_mcp_tool_entry_projects_effect_aliases_through_capcore(self) -> None:
        entry = build_mcp_tool_config_entry(
            "browser",
            {
                "name": "safe_looking_click",
                "description": "Run the requested interaction.",
                "risk": "low",
                "confirm": "never",
                "effects": ["browserAction"],
            },
        )

        self.assertEqual(entry["risk"], "high")
        self.assertEqual(entry["confirm"], "always")
        self.assertTrue(entry["requiresConfirmation"])
        self.assertEqual(entry["approvalMode"], APPROVAL_MODE_ASK_EACH_TIME)
        self.assertEqual(entry["effects"], ["browser_action"])

    def test_mcp_tool_entry_unknown_effect_does_not_auto_allow_never_confirm(self) -> None:
        entry = build_mcp_tool_config_entry(
            "custom",
            {
                "name": "custom_action",
                "description": "Custom local action.",
                "risk": "low",
                "confirm": "never",
                "effects": ["customEffect"],
            },
        )

        self.assertEqual(entry["risk"], "low")
        self.assertEqual(entry["confirm"], "first_time")
        self.assertTrue(entry["requiresConfirmation"])
        self.assertEqual(entry["approvalMode"], APPROVAL_MODE_ASK_EACH_TIME)
        self.assertEqual(entry["effects"], ["custom_effect"])

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
