from __future__ import annotations

import tempfile
import unittest

from companion_v01.local_capability_config import (
    APPROVAL_MODE_ASK_EACH_TIME,
    APPROVAL_MODE_DISABLED,
    APPROVAL_MODE_TRUSTED_AUTO_ALLOW,
    CONFIGURABLE_PROVIDER_BY_ID,
    CONFIGURABLE_WORKFLOW_BY_ID,
    apply_approval_policy_to_entry,
    approval_mode_for_capability,
    build_approval_policy_entry,
    build_mcp_server_config_entry,
    build_mcp_tool_config_entry,
    build_provider_config_entry,
    build_workflow_config_entry,
    capability_approval_mode,
    normalize_mcp_server_config_payload,
    normalize_mcp_tool_discovery_payload,
    project_capcore_catalog_fields,
    save_capability_approval_mode,
    save_capability_approval_modes,
    with_capability_approval_metadata,
)


class LocalCapabilityApprovalTests(unittest.TestCase):
    def test_permission_families_override_default_without_hiding_exact_overrides(self) -> None:
        policy = {
            "capabilityModes": {
                "ops": APPROVAL_MODE_TRUSTED_AUTO_ALLOW,
                "extensions": APPROVAL_MODE_DISABLED,
                "exec_run": APPROVAL_MODE_ASK_EACH_TIME,
            },
        }

        self.assertEqual(
            approval_mode_for_capability(policy, "browser_page", family_id="ops"),
            APPROVAL_MODE_TRUSTED_AUTO_ALLOW,
        )
        self.assertEqual(
            approval_mode_for_capability(policy, "manage_skill", family_id="extensions"),
            APPROVAL_MODE_DISABLED,
        )
        self.assertEqual(
            approval_mode_for_capability(policy, "exec_run", family_id="ops"),
            APPROVAL_MODE_ASK_EACH_TIME,
        )
        entry = build_approval_policy_entry(policy)
        self.assertEqual([item["id"] for item in entry["families"]], ["ops", "extensions"])
        self.assertEqual(entry["families"][0]["mode"], APPROVAL_MODE_TRUSTED_AUTO_ALLOW)
        self.assertEqual(entry["families"][1]["mode"], APPROVAL_MODE_DISABLED)

    def test_saving_family_preserves_exact_capability_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = save_capability_approval_mode(
                base_dir=temp_dir,
                profile_user_id="master",
                capability_id="exec_run",
                mode=APPROVAL_MODE_DISABLED,
            )
            self.assertTrue(result["ok"])
            saved = save_capability_approval_modes(
                base_dir=temp_dir,
                profile_user_id="master",
                modes={"ops": APPROVAL_MODE_TRUSTED_AUTO_ALLOW},
            )

        self.assertTrue(saved["ok"])
        family_modes = {item["id"]: item["mode"] for item in saved["approvalPolicy"]["families"]}
        self.assertEqual(family_modes["ops"], APPROVAL_MODE_TRUSTED_AUTO_ALLOW)
        self.assertEqual(
            approval_mode_for_capability(
                {"capabilityModes": {"ops": APPROVAL_MODE_TRUSTED_AUTO_ALLOW, "exec_run": APPROVAL_MODE_DISABLED}},
                "exec_run",
                family_id="ops",
            ),
            APPROVAL_MODE_DISABLED,
        )

    def test_mcp_execution_location_is_derived_from_transport(self) -> None:
        stdio = build_mcp_server_config_entry(
            "browser",
            {"enabled": True, "transport": "stdio", "command": "browser-mcp"},
        )
        remote = build_mcp_server_config_entry(
            "search",
            {"enabled": True, "transport": "streamable_http", "url": "https://mcp.example.test/rpc"},
        )

        self.assertEqual(stdio["executionLocation"], "host")
        self.assertEqual(stdio["adapter"], "mcp_stdio")
        self.assertEqual(remote["executionLocation"], "remote")
        self.assertEqual(remote["adapter"], "mcp_streamable_http")

    def test_remote_mcp_tool_preserves_provider_transport_location(self) -> None:
        entry = build_mcp_tool_config_entry(
            "search",
            {"name": "search_web", "description": "Search public sources."},
            transport="streamable_http",
        )

        self.assertEqual(entry["executionLocation"], "remote")
        self.assertEqual(entry["adapter"], "mcp_streamable_http")

    def test_streamable_http_mcp_config_accepts_placeholder_headers(self) -> None:
        result = normalize_mcp_server_config_payload(
            "search",
            {
                "enabled": True,
                "transport": "streamable_http",
                "url": "https://mcp.example.test/rpc",
                "headers": {"Authorization": "Bearer ${ANYSEARCH_API_KEY}"},
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["transport"], "streamable_http")
        self.assertEqual(result["url"], "https://mcp.example.test/rpc")
        self.assertEqual(result["headers"], {"Authorization": "Bearer ${ANYSEARCH_API_KEY}"})
        self.assertEqual(result["command"], "")

    def test_streamable_http_mcp_config_rejects_literal_header_secret(self) -> None:
        result = normalize_mcp_server_config_payload(
            "search",
            {
                "enabled": True,
                "transport": "streamable_http",
                "url": "https://mcp.example.test/rpc",
                "headers": {"Authorization": "Bearer literal-secret"},
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "mcp_server_headers_invalid")

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

    def test_mcp_tool_entry_projects_singular_hyphen_effect_through_capcore(self) -> None:
        entry = build_mcp_tool_config_entry(
            "shell",
            {
                "name": "run_script",
                "description": "Run a script through the local command adapter.",
                "risk": "low",
                "confirm": "never",
                "effect": "command-execution",
            },
        )

        self.assertEqual(entry["risk"], "high")
        self.assertEqual(entry["confirm"], "always")
        self.assertTrue(entry["requiresConfirmation"])
        self.assertEqual(entry["approvalMode"], APPROVAL_MODE_ASK_EACH_TIME)
        self.assertEqual(entry["effects"], ["command_exec"])

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

    def test_mcp_tool_discovery_drops_invalid_effect_tokens_via_capcore(self) -> None:
        payload = normalize_mcp_tool_discovery_payload(
            "custom",
            {
                "tools": [
                    {
                        "name": "custom_action",
                        "description": "Custom local action.",
                        "risk": "low",
                        "confirm": "never",
                        "effects": ["../bad effect"],
                    }
                ]
            },
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["tools"][0]["risk"], "low")
        self.assertEqual(payload["tools"][0]["confirm"], "never")
        self.assertNotIn("effects", payload["tools"][0])

    def test_provider_and_workflow_entries_project_capcore_fields(self) -> None:
        provider = build_provider_config_entry(
            CONFIGURABLE_PROVIDER_BY_ID["provider.comfyui.local"],
            {
                "enabled": True,
                "endpoint": "http://127.0.0.1:8188",
                "lastHealth": {"status": "ready"},
            },
        )
        workflow = build_workflow_config_entry(
            CONFIGURABLE_WORKFLOW_BY_ID["workflow.workshop.portrait.cutout"],
            {
                "enabled": True,
                "workflowPath": "workflows/comfyui/portrait_cutout.json",
                "slotMapping": {
                    "input_image_handle": "input_image",
                    "output_image_handle": "output_image",
                },
            },
            provider,
        )

        self.assertEqual(provider["risk"], "medium")
        self.assertEqual(provider["confirm"], "never")
        self.assertFalse(provider["requiresConfirmation"])
        self.assertEqual(provider["approvalMode"], APPROVAL_MODE_TRUSTED_AUTO_ALLOW)
        self.assertEqual(workflow["risk"], "medium")
        self.assertEqual(workflow["confirm"], "never")
        self.assertFalse(workflow["requiresConfirmation"])
        self.assertEqual(workflow["approvalMode"], APPROVAL_MODE_DISABLED)

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
            {"capabilityModes": {"ops": APPROVAL_MODE_TRUSTED_AUTO_ALLOW}},
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
            {"capabilityModes": {"ops": APPROVAL_MODE_TRUSTED_AUTO_ALLOW}},
        )

        self.assertEqual(trusted["approvalMode"], APPROVAL_MODE_TRUSTED_AUTO_ALLOW)
        self.assertEqual(trusted["approvalReason"], "user_policy_trusted_auto_allow")
        self.assertFalse(trusted["requiresConfirmation"])
        self.assertEqual(disabled["approvalMode"], APPROVAL_MODE_DISABLED)

    def test_family_mode_requires_explicit_runtime_classification(self) -> None:
        policy = {
            "capabilityModes": {"ops": APPROVAL_MODE_TRUSTED_AUTO_ALLOW},
        }

        self.assertEqual(
            approval_mode_for_capability(policy, "mcp.demo.echo", family_id="ops"),
            APPROVAL_MODE_TRUSTED_AUTO_ALLOW,
        )
        self.assertEqual(
            approval_mode_for_capability(policy, "exec_run"),
            APPROVAL_MODE_ASK_EACH_TIME,
        )

        exact_override = {
            **policy,
            "capabilityModes": {
                **policy["capabilityModes"],
                "mcp.demo.echo": APPROVAL_MODE_DISABLED,
            },
        }
        self.assertEqual(
            approval_mode_for_capability(exact_override, "mcp.demo.echo", family_id="ops"),
            APPROVAL_MODE_DISABLED,
        )


if __name__ == "__main__":
    unittest.main()
