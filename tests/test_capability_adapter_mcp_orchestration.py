from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from companion_v01.client_protocol import ClientMode, ClientProtocolContext
from companion_v01.capability_approval import CapabilityApprovalStore
from companion_v01.local_capability_config import get_mcp_server_runtime_config, save_capability_approval_mode
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.tool_runtime import BaseToolHandler, ToolExecutionResult, ToolExecutionContext


class StubStore:
    def list_attachment_inbox_items(self, **kwargs):
        return []

    def list_generated_files(self, **kwargs):
        return []


class StubWebSearchHandler(BaseToolHandler):
    tool_type = "web_search"

    def build_prompt_instruction(self) -> str:
        return "- web_search：stub"

    def normalize_call(self, value):
        return value if isinstance(value, dict) and value.get("type") == self.tool_type else None

    def execute(self, *, call: dict, context: ToolExecutionContext) -> ToolExecutionResult:
        return ToolExecutionResult(tool_type=self.tool_type, followup_context="ok")


def context() -> ClientProtocolContext:
    return ClientProtocolContext(
        requested_mode=ClientMode.DESKTOP_PET,
        effective_mode=ClientMode.DESKTOP_PET,
    )


def write_profile_config(
    root: Path,
    profile: str,
    *,
    prompt_exposed: bool,
    risk: str = "low",
    allowlist=None,
    activation_mode: str = "on_demand",
    pinned_tools=None,
) -> None:
    path = root / profile / "capabilities" / "capabilities.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schemaVersion": 1,
        "mcpServers": {
            "demo": {
                "enabled": True,
                "displayName": "Demo MCP",
                "transport": "stdio",
                "command": "python",
                "activationMode": activation_mode,
                "pinnedTools": list(pinned_tools or []),
                "lowRiskAllowlist": list(allowlist or []),
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echo text token=secret C:/Users/Akane/file.txt",
                        "risk": risk,
                        "confirm": "never",
                        "promptExposed": prompt_exposed,
                        "inputSchema": {
                            "type": "object",
                            "properties": {"text": {"type": "string", "description": "Text"}},
                            "required": ["text"],
                        },
                    }
                ],
                "lastDiscovery": {"status": "ready", "toolCount": 1},
            }
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def build_engine(config_base_dir: Path) -> AkaneMemoryEngine:
    engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
    engine.tool_handlers = {"web_search": StubWebSearchHandler()}
    engine.store = StubStore()
    engine.capability_config_base_dir = Path(config_base_dir)

    async def live_mcp_probe(*, server):
        del server
        return {"tools": [{"name": "echo"}]}

    engine.mcp_liveness_probe = live_mcp_probe
    return engine


class CapabilityAdapterMcpOrchestrationTests(unittest.TestCase):
    def test_host_manager_client_replaces_per_call_stdio_caller(self) -> None:
        class ManagedClient:
            async def list_tools(self, server):
                del server
                return ()

            async def call_tool(self, server, tool_name, arguments):
                del server, tool_name, arguments
                return {}

            async def aclose(self):
                return None

        class Manager:
            def __init__(self) -> None:
                self.managed_client = ManagedClient()
                self.client_calls = []

            def client(self, **kwargs):
                self.client_calls.append(kwargs)
                return self.managed_client

            def discover(self, **kwargs):
                del kwargs
                return {"tools": [{"name": "echo"}]}

        with tempfile.TemporaryDirectory() as temp_dir, patch("config.DATA_DIR", temp_dir):
            write_profile_config(
                Path(temp_dir), "alice", prompt_exposed=True, allowlist=["echo"],
                activation_mode="pinned", pinned_tools=["echo"],
            )
            engine = build_engine(Path(temp_dir))
            manager = Manager()
            engine.mcp_host_manager = manager
            handler = engine._resolve_tool_handlers(client_context=context(), profile_user_id="alice", session_id="s1")[
                "mcp.demo.echo"
            ]
            self.assertIs(handler.adapter._client, manager.managed_client)
            self.assertEqual(manager.client_calls[0]["server_id"], "demo")

    def test_no_prompt_exposed_mcp_tools_keeps_old_handlers_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("config.DATA_DIR", temp_dir):
            write_profile_config(Path(temp_dir), "alice", prompt_exposed=False)
            handlers = build_engine(Path(temp_dir))._resolve_tool_handlers(
                client_context=context(),
                profile_user_id="alice",
                session_id="s1",
            )
            self.assertIn("web_search", handlers)
            self.assertNotIn("mcp.demo.echo", handlers)

    def test_legacy_prompt_exposed_flag_does_not_bypass_on_demand_loading(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("config.DATA_DIR", temp_dir):
            write_profile_config(Path(temp_dir), "alice", prompt_exposed=True)
            engine = build_engine(Path(temp_dir))
            without_load = engine._resolve_tool_handlers(
                client_context=context(), profile_user_id="alice", session_id="s1"
            )
            runtime = get_mcp_server_runtime_config(
                base_dir=temp_dir, profile_user_id="alice", server_id="demo"
            )
            selection = engine._resolve_capability_selection(
                client_context=context(),
                profile_user_id="alice",
                session_id="s1",
                mcp_activations={"demo": runtime},
            )
            with_load = engine._resolve_tool_handlers(
                client_context=context(),
                profile_user_id="alice",
                session_id="s1",
                capability_selection=selection,
            )
            next_turn = engine._resolve_tool_handlers(
                client_context=context(), profile_user_id="alice", session_id="s2"
            )
            self.assertNotIn("mcp.demo.echo", without_load)
            self.assertIn("mcp.demo.echo", with_load)
            self.assertNotIn("mcp.demo.echo", next_turn)

    def test_explicit_pinned_mcp_tool_is_profile_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("config.DATA_DIR", temp_dir):
            write_profile_config(
                Path(temp_dir), "alice", prompt_exposed=True, allowlist=["echo"],
                activation_mode="pinned", pinned_tools=["echo"],
            )
            write_profile_config(Path(temp_dir), "bob", prompt_exposed=False, allowlist=["echo"])
            engine = build_engine(Path(temp_dir))
            alice = engine._resolve_tool_handlers(client_context=context(), profile_user_id="alice", session_id="s1")
            bob = engine._resolve_tool_handlers(client_context=context(), profile_user_id="bob", session_id="s1")
            self.assertIn("mcp.demo.echo", alice)
            self.assertNotIn("mcp.demo.echo", bob)

    def test_mcp_family_off_removes_tools_from_model_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("config.DATA_DIR", temp_dir):
            write_profile_config(
                Path(temp_dir), "alice", prompt_exposed=True, allowlist=["echo"],
                activation_mode="pinned", pinned_tools=["echo"],
            )
            saved = save_capability_approval_mode(
                base_dir=temp_dir,
                profile_user_id="alice",
                capability_id="mcp",
                mode="disabled",
            )
            self.assertTrue(saved["ok"])

            handlers = build_engine(Path(temp_dir))._resolve_tool_handlers(
                client_context=context(),
                profile_user_id="alice",
                session_id="s1",
            )

            self.assertIn("web_search", handlers)
            self.assertNotIn("mcp.demo.echo", handlers)

    def test_yaml_profile_config_loads_explicit_pinned_mcp_tool(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("config.DATA_DIR", temp_dir):
            path = Path(temp_dir) / "alice" / "capabilities" / "capabilities.yaml"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                """
schemaVersion: 1
mcpServers:
  demo:
    enabled: true
    displayName: Demo MCP
    transport: stdio
    command: python
    activationMode: pinned
    pinnedTools:
      - echo
    lowRiskAllowlist:
      - echo
    lastDiscovery:
      status: ready
      toolCount: 1
    tools:
      - name: echo
        description: Echo text
        risk: low
        confirm: never
        promptExposed: true
        inputSchema:
          type: object
          properties:
            text:
              type: string
              description: Text
          required:
            - text
""".lstrip(),
                encoding="utf-8",
            )
            handlers = build_engine(Path(temp_dir))._resolve_tool_handlers(
                client_context=context(),
                profile_user_id="alice",
                session_id="s1",
            )
            self.assertIn("mcp.demo.echo", handlers)

    def test_high_risk_mcp_tool_requires_approval_without_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("config.DATA_DIR", temp_dir):
            write_profile_config(
                Path(temp_dir), "alice", prompt_exposed=True, risk="high",
                activation_mode="pinned", pinned_tools=["echo"],
            )
            engine = build_engine(Path(temp_dir))
            approval_store = CapabilityApprovalStore()
            engine._get_approval_store = lambda: approval_store
            handler = engine._resolve_tool_handlers(
                client_context=context(),
                profile_user_id="alice",
                session_id="s1",
            )["mcp.demo.echo"]
            result = handler.execute(
                call={"type": "mcp.demo.echo", "arguments": {"text": "hello"}},
                context=ToolExecutionContext(
                    profile_user_id="alice",
                    session_id="s1",
                    now_ts=1,
                    visual_payload={},
                    client_mode="desktop_pet",
                ),
            )
            self.assertEqual(result.stream_events[0]["type"], "capability_approval_required")
            request_id = str(result.stream_events[0].get("requestId") or "")
            self.assertTrue(request_id.startswith("approvalreq_"), result.stream_events[0])
            pending = approval_store.list_requests(profile_user_id="alice")
            self.assertEqual(pending["pendingCount"], 1)
            self.assertEqual(pending["approvalRequests"][0]["requestId"], request_id)

    def test_prompt_instruction_redacts_secret_and_local_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("config.DATA_DIR", temp_dir):
            write_profile_config(
                Path(temp_dir), "alice", prompt_exposed=True, allowlist=["echo"],
                activation_mode="pinned", pinned_tools=["echo"],
            )
            prompt = build_engine(Path(temp_dir))._build_tool_prompt_context(
                allow_tool_call=True,
                client_context=context(),
                profile_user_id="alice",
                session_id="s1",
            )
            self.assertIn("mcp.demo.echo", prompt)
            self.assertNotIn("token=secret", prompt)
            self.assertNotIn("C:/Users/Akane", prompt)
            self.assertIn("web_search", prompt)


if __name__ == "__main__":
    unittest.main()
