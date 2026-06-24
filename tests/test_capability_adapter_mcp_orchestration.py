from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from companion_v01.client_protocol import ClientMode, ClientProtocolContext
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


def write_profile_config(root: Path, profile: str, *, prompt_exposed: bool, risk: str = "low", allowlist=None) -> None:
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


def build_engine() -> AkaneMemoryEngine:
    engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
    engine.tool_handlers = {"web_search": StubWebSearchHandler()}
    engine.store = StubStore()
    return engine


class CapabilityAdapterMcpOrchestrationTests(unittest.TestCase):
    def test_no_prompt_exposed_mcp_tools_keeps_old_handlers_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("config.DATA_DIR", temp_dir):
            write_profile_config(Path(temp_dir), "alice", prompt_exposed=False)
            handlers = build_engine()._resolve_tool_handlers(
                client_context=context(),
                profile_user_id="alice",
                session_id="s1",
            )
            self.assertIn("web_search", handlers)
            self.assertNotIn("mcp.demo.echo", handlers)

    def test_prompt_exposed_mcp_tool_is_profile_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("config.DATA_DIR", temp_dir):
            write_profile_config(Path(temp_dir), "alice", prompt_exposed=True, allowlist=["echo"])
            write_profile_config(Path(temp_dir), "bob", prompt_exposed=False, allowlist=["echo"])
            engine = build_engine()
            alice = engine._resolve_tool_handlers(client_context=context(), profile_user_id="alice", session_id="s1")
            bob = engine._resolve_tool_handlers(client_context=context(), profile_user_id="bob", session_id="s1")
            self.assertIn("mcp.demo.echo", alice)
            self.assertNotIn("mcp.demo.echo", bob)

    def test_yaml_profile_config_loads_prompt_exposed_mcp_tool(self) -> None:
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
            handlers = build_engine()._resolve_tool_handlers(
                client_context=context(),
                profile_user_id="alice",
                session_id="s1",
            )
            self.assertIn("mcp.demo.echo", handlers)

    def test_high_risk_mcp_tool_requires_approval_without_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("config.DATA_DIR", temp_dir):
            write_profile_config(Path(temp_dir), "alice", prompt_exposed=True, risk="high")
            handler = build_engine()._resolve_tool_handlers(
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

    def test_prompt_instruction_redacts_secret_and_local_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("config.DATA_DIR", temp_dir):
            write_profile_config(Path(temp_dir), "alice", prompt_exposed=True, allowlist=["echo"])
            prompt = build_engine()._build_tool_prompt_context(
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
