from __future__ import annotations

import json
import tempfile
import unittest
import threading
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from companion_v01.client_protocol import ClientMode, ClientProtocolContext
from companion_v01.capability_approval import CapabilityApprovalStore
from companion_v01.local_capability_config import get_mcp_server_runtime_config, save_capability_approval_mode
from companion_v01.engine import AkaneMemoryEngine
from companion_v01 import tool_orchestration_engine
from companion_v01.engine_services.tool_rounds import build_mcp_adapter_tool_handlers
from companion_v01.native_tool_schema import build_openai_native_tool_specs
from companion_v01.tool_invocation import (
    NATIVE_OPENAI,
    NATIVE_TOOL_CALL_FIELD,
    TOOL_CAPABILITY_SELECTION_FIELD,
    TOOL_INVOCATION_ID_FIELD,
    TOOL_MODEL_ARGUMENTS_FIELD,
    TOOL_MODEL_NAME_FIELD,
    TOOL_SOURCE_FIELD,
)
from companion_v01.tool_handlers.mcp_management import InvokeMcpToolHandler
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
    def test_independent_provider_discovery_is_parallel_and_ordered(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_profile_config(root, "alice", prompt_exposed=True, allowlist=["echo"])
            path = root / "alice/capabilities/capabilities.yaml"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["mcpServers"]["second"] = deepcopy(payload["mcpServers"]["demo"])
            path.write_text(json.dumps(payload), encoding="utf-8")
            engine = build_engine(root)
            both_started = threading.Barrier(2)
            def probe(*, server):
                both_started.wait(timeout=3)
                return {"tools": [{"name": "echo", "inputSchema": {"type": "object", "properties": {}}}]}
            engine.mcp_liveness_probe = probe
            handlers = build_mcp_adapter_tool_handlers(engine, profile_user_id="alice")
            self.assertEqual(list(handlers), ["mcp.demo.echo", "mcp.second.echo"])

    def test_invoke_mcp_reuses_exact_history_contract_without_loading_server_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("config.DATA_DIR", temp_dir):
            write_profile_config(Path(temp_dir), "alice", prompt_exposed=False, allowlist=["echo"])
            engine = build_engine(Path(temp_dir))
            engine.tool_handlers["invoke_mcp"] = InvokeMcpToolHandler()
            selection = engine._resolve_capability_selection(
                client_context=context(), profile_user_id="alice", session_id="s1"
            )
            self.assertIn("invoke_mcp", selection.schema_tool_names)
            self.assertNotIn("mcp.demo.echo", selection.schema_tool_names)

            wrapper = {
                "server_id": "demo",
                "tool_name": "echo",
                "contract_ref": selection.capability_catalog.load(["mcp.demo.echo"])["capabilities"][0]["contract_ref"],
                "arguments": {"text": "hello"},
            }
            _final_output, calls, rejections = engine._prepare_tool_round_decisions(
                final_output={
                    NATIVE_TOOL_CALL_FIELD: {
                        "type": "invoke_mcp",
                        **wrapper,
                        TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                        TOOL_INVOCATION_ID_FIELD: "call_invoke",
                    },
                    TOOL_CAPABILITY_SELECTION_FIELD: selection,
                },
                user_message="repeat the known MCP call",
                client_context=context(),
                profile_user_id="alice",
                session_id="s1",
            )

            self.assertEqual(rejections, [])
            self.assertEqual(len(calls), 1)
            call = calls[0]
            self.assertEqual(call["type"], "mcp.demo.echo")
            self.assertEqual(call["arguments"], {"text": "hello"})
            self.assertEqual(call[TOOL_MODEL_NAME_FIELD], "invoke_mcp")
            self.assertEqual(call[TOOL_MODEL_ARGUMENTS_FIELD], wrapper)
            self.assertEqual(engine._tool_call_model_arguments(call), wrapper)
            routed_selection = call[TOOL_CAPABILITY_SELECTION_FIELD]
            self.assertIn("mcp.demo.echo", routed_selection.tool_names)
            self.assertNotIn("mcp.demo.echo", routed_selection.schema_tool_names)
            validation = tool_orchestration_engine.validate_legacy_tool_call(
                engine,
                call,
                client_context=context(),
                profile_user_id="alice",
                session_id="s1",
            )
            self.assertTrue(validation.ok, validation.message)

    def test_invoke_mcp_unknown_target_returns_protocol_feedback_without_schema_expansion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("config.DATA_DIR", temp_dir):
            write_profile_config(Path(temp_dir), "alice", prompt_exposed=False)
            engine = build_engine(Path(temp_dir))
            engine.tool_handlers["invoke_mcp"] = InvokeMcpToolHandler()
            selection = engine._resolve_capability_selection(
                client_context=context(), profile_user_id="alice", session_id="s1"
            )
            _final_output, calls, rejections = engine._prepare_tool_round_decisions(
                final_output={
                    NATIVE_TOOL_CALL_FIELD: {
                        "type": "invoke_mcp",
                        "server_id": "demo",
                        "tool_name": "missing",
                        "arguments": {},
                        TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                        TOOL_INVOCATION_ID_FIELD: "call_missing",
                    },
                    TOOL_CAPABILITY_SELECTION_FIELD: selection,
                },
                user_message="call it",
                client_context=context(),
                profile_user_id="alice",
                session_id="s1",
            )
            self.assertEqual(rejections, [])
            self.assertEqual(len(calls), 1)
            result = engine.tool_handlers["invoke_mcp"].execute(
                call=calls[0],
                context=ToolExecutionContext(
                    profile_user_id="alice",
                    session_id="s1",
                    now_ts=1,
                    visual_payload={},
                    client_mode="desktop_pet",
                ),
            )
            self.assertIn("invoke_mcp_target_not_resolved", result.followup_context)
            self.assertIn("capability_load", result.followup_context)
            self.assertNotIn("mcp.demo.echo", selection.schema_tool_names)

    def test_invoke_mcp_preserves_the_selected_tool_approval_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("config.DATA_DIR", temp_dir):
            write_profile_config(Path(temp_dir), "alice", prompt_exposed=False, risk="high")
            engine = build_engine(Path(temp_dir))
            engine.tool_handlers["invoke_mcp"] = InvokeMcpToolHandler()
            approval_store = CapabilityApprovalStore()
            engine._get_approval_store = lambda: approval_store
            selection = engine._resolve_capability_selection(
                client_context=context(), profile_user_id="alice", session_id="s1"
            )
            _final_output, calls, rejections = engine._prepare_tool_round_decisions(
                final_output={
                    NATIVE_TOOL_CALL_FIELD: {
                        "type": "invoke_mcp",
                        "server_id": "demo",
                        "tool_name": "echo",
                        "contract_ref": selection.capability_catalog.load(["mcp.demo.echo"])["capabilities"][0]["contract_ref"],
                        "arguments": {"text": "hello"},
                        TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                        TOOL_INVOCATION_ID_FIELD: "call_approval",
                    },
                    TOOL_CAPABILITY_SELECTION_FIELD: selection,
                },
                user_message="call it",
                client_context=context(),
                profile_user_id="alice",
                session_id="s1",
            )
            self.assertEqual(rejections, [])
            call = calls[0]
            routed_handler = call[TOOL_CAPABILITY_SELECTION_FIELD].resolved_handlers[call["type"]]
            result = routed_handler.execute(
                call=call,
                context=ToolExecutionContext(
                    profile_user_id="alice",
                    session_id="s1",
                    now_ts=1,
                    visual_payload={},
                    client_mode="desktop_pet",
                ),
            )
            self.assertEqual(result.stream_events[0]["type"], "capability_approval_required")
            self.assertEqual(approval_store.list_requests(profile_user_id="alice")["pendingCount"], 1)

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
            self.assertIn("mcp.demo.echo", handlers)

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
            self.assertIn("mcp.demo.echo", without_load)
            self.assertNotIn("mcp.demo.echo", selection.schema_tool_names)
            self.assertIn("mcp.demo.echo", with_load)
            self.assertIn("mcp.demo.echo", next_turn)

    def test_historical_native_mcp_alias_requires_current_contract_without_loading_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("config.DATA_DIR", temp_dir):
            write_profile_config(Path(temp_dir), "alice", prompt_exposed=False, allowlist=["echo"])
            engine = build_engine(Path(temp_dir))
            selection = engine._resolve_capability_selection(
                client_context=context(),
                profile_user_id="alice",
                session_id="s1",
            )
            self.assertIn("mcp.demo.echo", selection.tool_names)
            self.assertNotIn("mcp.demo.echo", selection.schema_tool_names)

            dispatch_handlers = build_mcp_adapter_tool_handlers(
                engine,
                profile_user_id="alice",
                client_context=context(),
            )
            native = build_openai_native_tool_specs(dispatch_handlers)
            model_name = str(native[0]["function"]["name"])

            final_output, calls, rejections = engine._prepare_tool_round_decisions(
                final_output={
                    NATIVE_TOOL_CALL_FIELD: {
                        "type": model_name,
                        "text": "hello",
                        TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                        TOOL_INVOCATION_ID_FIELD: "call_historical",
                    },
                    TOOL_CAPABILITY_SELECTION_FIELD: selection,
                },
                user_message="use the same echo tool",
                client_context=context(),
                profile_user_id="alice",
                session_id="s1",
            )

            self.assertEqual(rejections, [])
            self.assertEqual(len(calls), 1)
            call = calls[0]
            self.assertEqual(call["type"], "mcp.demo.echo")
            self.assertEqual(call["text"], "hello")
            binding = call[TOOL_CAPABILITY_SELECTION_FIELD].resolved_handlers[call["type"]]
            self.assertEqual(binding.contract_error(), "capability_contract_stale")
            result = binding.execute(call=call, context=ToolExecutionContext(
                profile_user_id="alice", session_id="s1", now_ts=1, visual_payload={}))
            self.assertIn('"executed":false', result.followup_context)
            self.assertEqual(call[TOOL_MODEL_NAME_FIELD], model_name)
            self.assertEqual(call[TOOL_SOURCE_FIELD], NATIVE_OPENAI)
            self.assertEqual(call[TOOL_INVOCATION_ID_FIELD], "call_historical")
            dispatch_selection = call[TOOL_CAPABILITY_SELECTION_FIELD]
            self.assertIn("mcp.demo.echo", dispatch_selection.tool_names)
            self.assertNotIn("mcp.demo.echo", dispatch_selection.schema_tool_names)
            self.assertNotIn("mcp.demo.echo", dispatch_selection.native_tool_names)
            self.assertIn("mcp.demo.echo", selection.tool_names)
            validation = tool_orchestration_engine.validate_legacy_tool_call(
                engine,
                call,
                client_context=context(),
                profile_user_id="alice",
                session_id="s1",
            )
            self.assertTrue(validation.ok, validation.message)

    def test_unknown_native_mcp_alias_stays_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("config.DATA_DIR", temp_dir):
            write_profile_config(Path(temp_dir), "alice", prompt_exposed=False)
            engine = build_engine(Path(temp_dir))
            selection = engine._resolve_capability_selection(
                client_context=context(),
                profile_user_id="alice",
                session_id="s1",
            )
            _final_output, calls, rejections = engine._prepare_tool_round_decisions(
                final_output={
                    NATIVE_TOOL_CALL_FIELD: {
                        "type": "mcp_missing_tool_deadbeef00",
                        TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                        TOOL_INVOCATION_ID_FIELD: "call_missing",
                    },
                    TOOL_CAPABILITY_SELECTION_FIELD: selection,
                },
                user_message="call it",
                client_context=context(),
                profile_user_id="alice",
                session_id="s1",
            )
            self.assertEqual(rejections, [])
            self.assertEqual(len(calls), 1)
            validation = tool_orchestration_engine.validate_legacy_tool_call(
                engine,
                calls[0],
                client_context=context(),
                profile_user_id="alice",
                session_id="s1",
            )
            self.assertFalse(validation.ok)
            self.assertIn('"reason":"unknown_tool"', validation.message)
            self.assertIn('"executed":false', validation.message)
            self.assertIn("capability_load", validation.message)

    def test_retired_mcp_family_override_does_not_disable_historical_native_alias(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("config.DATA_DIR", temp_dir):
            write_profile_config(Path(temp_dir), "alice", prompt_exposed=False)
            engine = build_engine(Path(temp_dir))
            dispatch_handlers = build_mcp_adapter_tool_handlers(
                engine,
                profile_user_id="alice",
                client_context=context(),
            )
            model_name = str(build_openai_native_tool_specs(dispatch_handlers)[0]["function"]["name"])
            save_capability_approval_mode(
                base_dir=temp_dir,
                profile_user_id="alice",
                capability_id="mcp",
                mode="disabled",
            )
            selection = engine._resolve_capability_selection(
                client_context=context(),
                profile_user_id="alice",
                session_id="s1",
            )
            _final_output, calls, rejections = engine._prepare_tool_round_decisions(
                final_output={
                    NATIVE_TOOL_CALL_FIELD: {
                        "type": model_name,
                        "text": "hello",
                        TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                        TOOL_INVOCATION_ID_FIELD: "call_disabled",
                    },
                    TOOL_CAPABILITY_SELECTION_FIELD: selection,
                },
                user_message="call it",
                client_context=context(),
                profile_user_id="alice",
                session_id="s1",
            )
            self.assertEqual(rejections, [])
            self.assertEqual(len(calls), 1)
            validation = tool_orchestration_engine.validate_legacy_tool_call(
                engine,
                calls[0],
                client_context=context(),
                profile_user_id="alice",
                session_id="s1",
            )
            # The synthetic v1 mcp switch is retired. Explicit server exposure
            # and concrete action policy remain authoritative.
            self.assertTrue(validation.ok)

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
            self.assertIn("mcp.demo.echo", bob)
            self.assertIn("mcp.demo.echo", engine._resolve_capability_selection(client_context=context(), profile_user_id="alice", session_id="s1").schema_tool_names)
            self.assertNotIn("mcp.demo.echo", engine._resolve_capability_selection(client_context=context(), profile_user_id="bob", session_id="s1").schema_tool_names)

    def test_retired_mcp_family_override_keeps_explicit_pinned_tool_selection(self) -> None:
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
            self.assertIn("mcp.demo.echo", handlers)

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
