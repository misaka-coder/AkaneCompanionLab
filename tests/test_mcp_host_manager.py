from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from companion_v01.local_capability_config import (
    get_mcp_server_runtime_config,
    load_capability_config,
    load_host_mcp_server_configs,
    normalize_mcp_tool_discovery_payload,
    save_capability_approval_mode,
    write_capability_config,
)
from companion_v01.mcp_host_manager import McpManagementService
from companion_v01.capability_registry import CapabilityRegistry, CapabilitySnapshot
from companion_v01.client_protocol import ClientMode
from companion_v01.routes.capabilities import build_capabilities_router
from companion_v01.tool_handlers.core import ToolExecutionContext
from companion_v01.tool_handlers.mcp_management import LoadMcpToolHandler, McpManageToolHandler
from capcore_adapter_mcp import McpClientError


class _FakeManager:
    def __init__(self) -> None:
        self.fail = False
        self.discoveries: list[tuple[str, str, dict]] = []
        self.stops: list[tuple[str, str, dict, bool]] = []

    def discover(self, *, profile_user_id, server_id, server_config):
        self.discoveries.append((profile_user_id, server_id, dict(server_config)))
        if self.fail:
            raise RuntimeError("candidate failed")
        return {
            "tools": [
                {
                    "name": "echo",
                    "description": "Echo text",
                    "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
                }
            ]
        }

    def stop_server(self, *, profile_user_id, server_id, server_config, disabled=False, mark_status=True):
        del mark_status
        self.stops.append((profile_user_id, server_id, dict(server_config), bool(disabled)))
        return {"ok": True}

    def status(self, *, profile_user_id, server_id):
        del profile_user_id, server_id
        return {"status": "ready", "reason": ""}


class McpManagementServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp.name)
        self.manager = _FakeManager()
        self.service = McpManagementService(base_dir=self.base_dir, manager=self.manager)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_configure_starts_candidate_before_atomic_save_and_defaults_to_on_demand(self) -> None:
        result = self.service.configure(
            profile_user_id="owner",
            server_id="demo",
            payload={
                "enabled": True,
                "transport": "stdio",
                "command": "demo-mcp",
                "args": ["--stdio"],
                "catalogDescription": "Demo tools",
            },
        )
        saved = get_mcp_server_runtime_config(
            base_dir=self.base_dir,
            profile_user_id="owner",
            server_id="demo",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(self.manager.discoveries[0][2]["command"], "demo-mcp")
        self.assertEqual(saved["tools"][0]["name"], "echo")
        self.assertFalse(saved["tools"][0]["promptExposed"])
        self.assertEqual(saved["activationMode"], "on_demand")
        self.assertIn("demo：Demo tools", self.service.prompt_catalog(profile_user_id="owner"))

    def test_group_install_is_host_visible_in_private_while_enablement_is_profile_scoped(self) -> None:
        installed = self.service.configure(
            profile_user_id="qq_group_shared_872732158",
            server_id="reimu",
            payload={
                "enabled": True,
                "transport": "stdio",
                "command": "reimu-mcp",
                "catalogDescription": "Reimu QQ tools",
            },
        )

        private = get_mcp_server_runtime_config(
            base_dir=self.base_dir,
            profile_user_id="master",
            server_id="reimu",
        )
        private_discovery = self.service.discover(
            profile_user_id="master",
            server_id="reimu",
        )
        disabled_group = self.service.set_enabled(
            profile_user_id="qq_group_shared_872732158",
            server_id="reimu",
            enabled=False,
        )
        group_after = get_mcp_server_runtime_config(
            base_dir=self.base_dir,
            profile_user_id="qq_group_shared_872732158",
            server_id="reimu",
        )
        private_after = get_mcp_server_runtime_config(
            base_dir=self.base_dir,
            profile_user_id="master",
            server_id="reimu",
        )

        self.assertTrue(installed["ok"])
        self.assertEqual(installed["configScope"]["registry"], "host")
        self.assertEqual(private["command"], "reimu-mcp")
        self.assertTrue(private["enabled"])
        self.assertTrue(private_discovery["ok"])
        self.assertEqual(disabled_group["scope"], "profile")
        self.assertFalse(group_after["enabled"])
        self.assertTrue(private_after["enabled"])
        self.assertNotIn("reimu：", self.service.prompt_catalog(profile_user_id="qq_group_shared_872732158"))
        self.assertIn("reimu：Reimu QQ tools", self.service.prompt_catalog(profile_user_id="master"))

        save_capability_approval_mode(
            base_dir=self.base_dir,
            profile_user_id="master",
            capability_id="mcp.family",
            mode="disabled",
        )
        self.assertEqual(self.service.prompt_catalog(profile_user_id="master"), "")
        from types import SimpleNamespace
        from companion_v01.engine_services.tool_rounds import resolve_capability_selection
        engine = SimpleNamespace(tool_handlers={}, capability_config_base_dir=self.base_dir)
        selected = resolve_capability_selection(engine, profile_user_id="master", session_id="private")
        self.assertFalse(any(name.startswith("mcp.") for name in selected.capability_catalog.capability_ids))

    def test_legacy_profile_mcp_is_promoted_without_copying_it_to_other_profiles(self) -> None:
        legacy_profile = "qq_group_shared_872732158"
        write_capability_config(
            base_dir=self.base_dir,
            profile_user_id=legacy_profile,
            config={
                "schemaVersion": 1,
                "mcpServers": {
                    "legacy": {
                        "enabled": True,
                        "transport": "stdio",
                        "command": "legacy-mcp",
                    }
                },
            },
        )

        service = McpManagementService(base_dir=self.base_dir, manager=self.manager)
        host = load_host_mcp_server_configs(base_dir=self.base_dir)
        migrated_profile = load_capability_config(
            base_dir=self.base_dir,
            profile_user_id=legacy_profile,
        )

        self.assertEqual(service.migration_status["status"], "migrated")
        self.assertEqual(host["mcpServers"]["legacy"]["command"], "legacy-mcp")
        self.assertEqual(migrated_profile["mcpServers"], {})
        self.assertEqual(
            get_mcp_server_runtime_config(
                base_dir=self.base_dir,
                profile_user_id="master",
                server_id="legacy",
            )["command"],
            "legacy-mcp",
        )

    def test_conflicting_legacy_server_ids_are_not_silently_promoted(self) -> None:
        for profile_id, command in (("alice", "alice-mcp"), ("bob", "bob-mcp")):
            write_capability_config(
                base_dir=self.base_dir,
                profile_user_id=profile_id,
                config={
                    "schemaVersion": 1,
                    "mcpServers": {
                        "shared-name": {
                            "enabled": True,
                            "transport": "stdio",
                            "command": command,
                        }
                    },
                },
            )

        service = McpManagementService(base_dir=self.base_dir, manager=self.manager)

        self.assertEqual(service.migration_status["conflicts"], ["shared-name"])
        self.assertNotIn(
            "shared-name",
            load_host_mcp_server_configs(base_dir=self.base_dir)["mcpServers"],
        )
        self.assertEqual(
            get_mcp_server_runtime_config(
                base_dir=self.base_dir,
                profile_user_id="alice",
                server_id="shared-name",
            )["command"],
            "alice-mcp",
        )
        self.assertEqual(
            get_mcp_server_runtime_config(
                base_dir=self.base_dir,
                profile_user_id="bob",
                server_id="shared-name",
            )["command"],
            "bob-mcp",
        )

    def test_secret_named_env_accepts_placeholder_but_rejects_literal(self) -> None:
        accepted = self.service.configure(
            profile_user_id="owner",
            server_id="demo",
            payload={
                "enabled": True,
                "transport": "stdio",
                "command": "demo-mcp",
                "env": {"DEMO_API_KEY": "${DEMO_API_KEY}"},
            },
        )
        rejected = self.service.configure(
            profile_user_id="owner",
            server_id="literal",
            payload={
                "enabled": False,
                "transport": "stdio",
                "command": "demo-mcp",
                "env": {"DEMO_API_KEY": "secret-value"},
            },
        )
        self.assertTrue(accepted["ok"])
        self.assertFalse(rejected["ok"])

    def test_failed_candidate_preserves_last_good_config(self) -> None:
        first = self.service.configure(
            profile_user_id="owner",
            server_id="demo",
            payload={"enabled": True, "transport": "stdio", "command": "old-mcp"},
        )
        self.assertTrue(first["ok"])
        self.manager.fail = True
        failed = self.service.configure(
            profile_user_id="owner",
            server_id="demo",
            payload={"enabled": True, "transport": "stdio", "command": "broken-mcp"},
        )
        saved = get_mcp_server_runtime_config(
            base_dir=self.base_dir,
            profile_user_id="owner",
            server_id="demo",
        )
        self.assertFalse(failed["ok"])
        self.assertTrue(failed["lastGoodPreserved"])
        self.assertEqual(saved["command"], "old-mcp")
        self.assertEqual(failed["diagnostic"]["stage"], "initialize_and_list_tools")
        self.assertEqual(failed["diagnostic"]["category"], "startup_failed")
        self.assertIn("candidate failed", failed["diagnostic"]["detail"])
        self.assertEqual(failed["recommendedAction"], failed["diagnostic"]["recommendedAction"])

    def test_failed_candidate_reports_actionable_sanitized_transport_cause(self) -> None:
        class MissingCommandManager(_FakeManager):
            def discover(self, **kwargs):
                del kwargs
                try:
                    raise FileNotFoundError("token=secret-value missing-mcp")
                except FileNotFoundError as cause:
                    raise McpClientError("mcp_tools_list_failed") from cause

        service = McpManagementService(base_dir=self.base_dir, manager=MissingCommandManager())
        result = service.configure(
            profile_user_id="owner",
            server_id="missing",
            payload={"enabled": True, "transport": "stdio", "command": "missing-mcp"},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "mcp_tools_list_failed")
        self.assertEqual(result["diagnostic"]["category"], "command_not_found")
        self.assertIn("token=[redacted]", result["diagnostic"]["detail"])
        self.assertNotIn("secret-value", str(result))

    def test_failed_candidate_identifies_deprecated_distribution(self) -> None:
        class DeprecatedPackageManager(_FakeManager):
            def discover(self, **kwargs):
                del kwargs
                try:
                    raise RuntimeError("npm warn deprecated package: no longer supported")
                except RuntimeError as cause:
                    raise McpClientError("mcp_tools_list_failed") from cause

        result = McpManagementService(
            base_dir=self.base_dir,
            manager=DeprecatedPackageManager(),
        ).configure(
            profile_user_id="owner",
            server_id="deprecated",
            payload={"enabled": True, "transport": "stdio", "command": "npx"},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["diagnostic"]["category"], "deprecated_distribution")
        self.assertIn("official repository or registry", result["recommendedAction"])

    def test_successful_replacement_closes_only_old_session_after_candidate_is_ready(self) -> None:
        self.service.configure(
            profile_user_id="owner",
            server_id="demo",
            payload={"enabled": True, "transport": "stdio", "command": "old-mcp"},
        )
        replaced = self.service.configure(
            profile_user_id="owner",
            server_id="demo",
            payload={"enabled": True, "transport": "stdio", "command": "new-mcp"},
        )
        self.assertTrue(replaced["ok"])
        self.assertEqual([item[2]["command"] for item in self.manager.stops], ["old-mcp"])
        self.assertEqual([item[2]["command"] for item in self.manager.discoveries], ["old-mcp", "new-mcp"])

    def test_remove_only_removes_akane_registration(self) -> None:
        external = self.base_dir / "external-package.txt"
        external.write_text("keep", encoding="utf-8")
        self.service.configure(
            profile_user_id="owner",
            server_id="demo",
            payload={"enabled": True, "transport": "stdio", "command": "demo-mcp"},
        )
        result = self.service.remove(profile_user_id="owner", server_id="demo")
        self.assertTrue(result["ok"])
        self.assertFalse(result["removedExternalPackage"])
        self.assertTrue(external.exists())
        self.assertFalse(
            get_mcp_server_runtime_config(
                base_dir=self.base_dir,
                profile_user_id="owner",
                server_id="demo",
            )
        )

    def test_management_tool_requires_owner_and_never_claims_uninstall(self) -> None:
        handler = McpManageToolHandler(service=self.service)
        denied = handler.execute(
            call={"type": "mcp_manage", "action": "list"},
            context=ToolExecutionContext(
                profile_user_id="member",
                session_id="s",
                now_ts=1,
                visual_payload={},
                client_mode="qq",
                request_context={"qq_delivery_context": {"user_id": "123"}},
            ),
        )
        self.assertIn("permission_denied", denied.followup_context)
        self.assertIn("remove", handler.tool_spec().description.lower())
        self.assertIn("never uninstalls", handler.tool_spec().description.lower())
        self.assertIn("official repository or registry", handler.tool_spec().description.lower())
        self.assertIn("不要只凭训练记忆或搜索摘要", handler.build_prompt_instruction())
        registry = CapabilityRegistry()
        desktop = registry.select(CapabilitySnapshot(client_mode=ClientMode.DESKTOP_PET, execution_enabled=True))
        qq = registry.select(
            CapabilitySnapshot(
                client_mode=ClientMode.QQ_TEXT,
                execution_enabled=True,
                execution_qq_enabled=True,
            )
        )
        web = registry.select(CapabilitySnapshot(client_mode=ClientMode.SCENE_STATIC))
        self.assertIn("mcp_manage", desktop.tool_names)
        self.assertIn("mcp_manage", qq.tool_names)
        self.assertNotIn("mcp_manage", web.tool_names)
        self.assertIn("load_mcp", desktop.tool_names)
        self.assertIn("load_mcp", qq.tool_names)

    def test_load_mcp_returns_full_contract_through_shared_catalog(self) -> None:
        from companion_v01.engine_services.tool_rounds import resolve_capability_selection
        from types import SimpleNamespace
        self.service.configure(profile_user_id="owner", server_id="demo",
            payload={"enabled": True, "transport": "stdio", "command": "demo-mcp"})
        engine = SimpleNamespace(tool_handlers={}, capability_config_base_dir=self.base_dir,
                                 mcp_liveness_probe=lambda **kw: self.manager.discover(
                                     profile_user_id="owner", server_id="demo", server_config=kw["server"]))
        selection = resolve_capability_selection(engine, profile_user_id="owner", session_id="s")
        before = selection.schema_tool_names
        result = LoadMcpToolHandler(service=self.service).execute(
            call={"type": "load_mcp", "server_ids": ["demo"]},
            context=ToolExecutionContext(profile_user_id="owner", session_id="s", now_ts=1,
                visual_payload={}, client_mode="qq", capability_selection=selection))
        self.assertIn('"input_schema"', result.followup_context)
        self.assertIn('"contract_ref"', result.followup_context)
        self.assertEqual(result.state_updates, {})
        self.assertEqual(before, selection.schema_tool_names)

    def test_discovery_does_not_silently_truncate_more_than_64_tools(self) -> None:
        payload = normalize_mcp_tool_discovery_payload(
            "large",
            {
                "tools": [
                    {"name": f"tool_{index}", "inputSchema": {"type": "object"}}
                    for index in range(70)
                ]
            },
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["tools"]), 70)

    def test_discovery_preserves_full_nested_tool_schema(self) -> None:
        properties = {f"field_{index}": {"type": "string"} for index in range(30)}
        properties["options"] = {
            "type": "array",
            "items": {"type": "string", "enum": ["open", "closed"]},
        }
        payload = normalize_mcp_tool_discovery_payload(
            "nested",
            {
                "tools": [
                    {
                        "name": "complex",
                        "inputSchema": {
                            "type": "object",
                            "properties": properties,
                            "required": ["field_29", "options"],
                            "additionalProperties": False,
                        },
                    }
                ]
            },
        )
        schema = payload["tools"][0]["inputSchema"]
        self.assertEqual(len(schema["properties"]), 31)
        self.assertEqual(schema["properties"]["options"]["items"]["enum"], ["open", "closed"])
        self.assertEqual(schema["required"], ["field_29", "options"])
        self.assertFalse(schema["additionalProperties"])

    def test_prompt_catalog_is_stable_until_config_changes(self) -> None:
        self.service.configure(
            profile_user_id="owner",
            server_id="demo",
            payload={
                "enabled": True,
                "transport": "stdio",
                "command": "demo-mcp",
                "catalogDescription": "First description",
            },
        )
        first = self.service.prompt_catalog(profile_user_id="owner")
        second = self.service.prompt_catalog(profile_user_id="owner")
        self.assertEqual(first, second)
        self.service.configure(
            profile_user_id="owner",
            server_id="demo",
            payload={
                "enabled": True,
                "transport": "stdio",
                "command": "demo-mcp",
                "catalogDescription": "Second description",
            },
        )
        self.assertNotEqual(first, self.service.prompt_catalog(profile_user_id="owner"))

    def test_lifecycle_routes_use_host_management_service(self) -> None:
        self.service.configure(
            profile_user_id="owner",
            server_id="demo",
            payload={"enabled": True, "transport": "stdio", "command": "demo-mcp"},
        )
        app = FastAPI()
        app.include_router(
            build_capabilities_router(
                engine=type("Engine", (), {"mcp_management_service": self.service, "tool_handlers": {}})(),
                capability_config_base_dir=self.base_dir,
                resolve_identity_from_query=lambda _request: ("session", "owner"),
            )
        )
        client = TestClient(app)
        disabled = client.post("/capabilities/mcp-servers/demo/disable").json()
        enabled = client.post("/capabilities/mcp-servers/demo/enable").json()
        restarted = client.post("/capabilities/mcp-servers/demo/restart").json()
        removed = client.delete("/capabilities/mcp-servers/demo").json()
        self.assertEqual(disabled["status"], "disabled")
        self.assertEqual(enabled["status"], "ready")
        self.assertTrue(restarted["ok"])
        self.assertEqual(removed["status"], "removed")
        self.assertFalse(removed["removedExternalPackage"])


if __name__ == "__main__":
    unittest.main()
