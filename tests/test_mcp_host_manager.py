from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from companion_v01.local_capability_config import get_mcp_server_runtime_config
from companion_v01.mcp_host_manager import McpManagementService
from companion_v01.capability_registry import CapabilityRegistry, CapabilitySnapshot
from companion_v01.client_protocol import ClientMode
from companion_v01.routes.capabilities import build_capabilities_router
from companion_v01.tool_handlers.core import ToolExecutionContext
from companion_v01.tool_handlers.mcp_management import McpManageToolHandler
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

    def test_configure_starts_candidate_before_atomic_save_and_exposes_selected_tools(self) -> None:
        result = self.service.configure(
            profile_user_id="owner",
            server_id="demo",
            payload={"enabled": True, "transport": "stdio", "command": "demo-mcp", "args": ["--stdio"]},
            prompt_exposed_tools=["echo"],
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
        self.assertTrue(saved["tools"][0]["promptExposed"])

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
            prompt_exposed_tools=["echo"],
        )
        self.assertTrue(first["ok"])
        self.manager.fail = True
        failed = self.service.configure(
            profile_user_id="owner",
            server_id="demo",
            payload={"enabled": True, "transport": "stdio", "command": "broken-mcp"},
            prompt_exposed_tools=["echo"],
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
