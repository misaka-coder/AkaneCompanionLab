"""Real MCP SDK stdio subprocess through the production manager and broker."""
from __future__ import annotations
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from companion_v01.capability_exposure import apply_published_contracts, published_contracts, route_invocation
from companion_v01.capability_exposure_config import save_preferences
from companion_v01.engine_services.tool_rounds import resolve_capability_selection
from companion_v01.local_capability_config import get_mcp_server_runtime_config, save_capability_approval_mode
from companion_v01.mcp_host_manager import McpHostManager, McpManagementService
from companion_v01.tool_orchestration_engine import normalize_tool_invocation, execute_tool_invocation
from tests.test_capability_exposure import CountingTool

SERVER = r"""
import asyncio, json, sys
from pathlib import Path
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, ToolAnnotations, TextContent
state_path, log_path = map(Path, sys.argv[1:])
app = Server("controlled-exposure")
@app.list_tools()
async def list_tools():
    state = json.loads(state_path.read_text())
    return [Tool(name=name, description="Amount unit: " + state["unit"],
                 annotations=ToolAnnotations(**state["annotations"]) if state.get("annotations") else None,
                 inputSchema={"type":"object", "properties":{state.get("argument","amount"):{"type":"integer"}}, "required":[state.get("argument","amount")]})
            for name in ("resident", "deferred")]
@app.call_tool()
async def call_tool(name, arguments):
    state = json.loads(state_path.read_text())
    with log_path.open("a") as file:
        file.write(json.dumps({"name":name,"arguments":arguments,"unit":state["unit"]}) + "\n")
    return [TextContent(type="text", text=json.dumps({"amount":arguments[state.get("argument","amount")],"unit":state["unit"]}))]
async def main():
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())
asyncio.run(main())
"""

class ToolExposureMcpTests(unittest.TestCase):
    def test_reconnect_checks_real_arguments_and_changed_policy_before_dispatch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            server, state, log = root/"server.py", root/"state.json", root/"calls.jsonl"
            server.write_text(SERVER, encoding="utf-8")
            hints = {"readOnlyHint":True, "destructiveHint":False, "openWorldHint":False}
            def update(argument="amount", annotations=hints):
                state.write_text(json.dumps({"unit":"yuan", "argument":argument, "annotations":annotations}),encoding="utf-8")
            update()
            manager = McpHostManager(timeout_seconds=8)
            try:
                service = McpManagementService(base_dir=root, manager=manager)
                configured = service.configure(profile_user_id="alice", server_id="demo", payload={
                    "enabled":True, "transport":"stdio", "command":sys.executable, "args":[str(server),str(state),str(log)]})
                self.assertTrue(configured["ok"], configured)
                saved = save_capability_approval_mode(base_dir=root, profile_user_id="alice",
                    capability_id="mcp.demo.resident", mode="trusted_auto_allow")
                self.assertTrue(saved["ok"], saved)
                engine = SimpleNamespace(tool_handlers={}, capability_config_base_dir=root, mcp_host_manager=manager)
                engine._resolve_tool_handlers = lambda capability_selection=None, **_:dict(capability_selection.resolved_handlers)
                selection = resolve_capability_selection(engine, profile_user_id="alice", session_id="s")
                def execute(arguments):
                    call, chosen = route_invocation({"type":"capability_invoke", "capability_id":"mcp.demo.resident",
                        "arguments":arguments}, selection)
                    invocation = normalize_tool_invocation(engine, call, profile_user_id="alice", session_id="s", capability_selection=chosen)
                    return execute_tool_invocation(engine, invocation=invocation, profile_user_id="alice", session_id="s", visual_payload={}, now_ts=1)[0]
                first = execute({"amount":10})
                self.assertFalse(first.capability_result.is_error, first.followup_context)
                runtime = get_mcp_server_runtime_config(base_dir=root, profile_user_id="alice", server_id="demo")
                update("quantity")
                manager.stop_server(profile_user_id="alice", server_id="demo", server_config=runtime)
                rejected = execute({"amount":10})
                self.assertTrue(rejected.capability_result.is_error)
                self.assertIn("quantity", rejected.followup_context)
                self.assertEqual(len(log.read_text().splitlines()), 1)
                self.assertFalse(execute({"quantity":10}).capability_result.is_error)
                self.assertEqual(len(log.read_text().splitlines()), 2)
                update("quantity", {**hints, "readOnlyHint":False})
                manager.stop_server(profile_user_id="alice", server_id="demo", server_config=runtime)
                self.assertFalse(execute({"quantity":10}).capability_result.is_error)
                self.assertEqual(len(log.read_text().splitlines()), 3)
                update("quantity", {**hints, "readOnlyHint":False, "destructiveHint":True})
                manager.stop_server(profile_user_id="alice", server_id="demo", server_config=runtime)
                rejected = execute({"quantity":10})
                self.assertTrue(rejected.capability_result.is_error)
                self.assertIn("mcp_tool_policy_changed", rejected.followup_context)
                self.assertEqual(len(log.read_text().splitlines()), 3)
            finally:
                self.assertTrue(manager.close())

    def test_real_server_mixed_residency_upgrade_recovery_and_revocation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            server = root / "server.py"
            state, log = root / "state.json", root / "calls.jsonl"
            server.write_text(SERVER, encoding="utf-8")
            state.write_text('{"unit":"yuan"}', encoding="utf-8")
            manager = McpHostManager(timeout_seconds=8)
            try:
                service = McpManagementService(base_dir=root, manager=manager)
                configured = service.configure(profile_user_id="alice", server_id="demo", payload={
                    "enabled": True, "command": sys.executable, "args": [str(server), str(state), str(log)],
                    "transport": "stdio", "activationMode": "pinned", "pinnedTools": ["resident"],
                    "lowRiskAllowlist": ["resident", "deferred"]})
                self.assertTrue(configured["ok"], configured)
                save_capability_approval_mode(base_dir=root, profile_user_id="alice", capability_id="mcp.family", mode="trusted_auto_allow")
                for name in ("resident", "deferred"):
                    saved = save_capability_approval_mode(base_dir=root, profile_user_id="alice",
                        capability_id="mcp.demo." + name, mode="trusted_auto_allow")
                    self.assertTrue(saved["ok"], saved)
                host = CountingTool("demo.host")
                engine = SimpleNamespace(tool_handlers={host.tool_type: host}, capability_config_base_dir=root, mcp_host_manager=manager)
                engine._resolve_tool_handlers = lambda capability_selection=None, **_: dict(capability_selection.resolved_handlers)
                def select():
                    return resolve_capability_selection(engine, profile_user_id="alice", session_id="mcp-session")
                def execute(chosen, call):
                    if call["type"] == "capability_invoke":
                        call, chosen = route_invocation(call, chosen)
                    invocation = normalize_tool_invocation(engine, call, capability_selection=chosen,
                        profile_user_id="alice", session_id="mcp-session")
                    return execute_tool_invocation(engine, invocation=invocation, profile_user_id="alice",
                        session_id="mcp-session", character_pack_id="", visual_payload={}, now_ts=1)[0]
                def calls():
                    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
                first = select()
                with patch.object(manager, "discover", wraps=manager.discover) as discovery:
                    result = execute(first, {"type": "demo.host", "text": "local"})
                    self.assertEqual(result.followup_context, "local")
                    loaded_host = execute(first, {"type": "capability_load", "capability_ids": ["demo.host"]})
                    host_contract = json.loads(loaded_host.followup_context.split("\n", 1)[1])["capabilities"][0]
                    execute(first, {"type": "capability_invoke", "capability_id": "demo.host",
                        "arguments": {"text": "invoked"}})
                    self.assertEqual(len(host.calls), 2)
                    discovery.assert_not_called()
                self.assertIn("mcp.demo.resident", first.schema_tool_names)
                self.assertNotIn("mcp.demo.deferred", first.schema_tool_names)
                direct = execute(first, {"type":"mcp.demo.resident", "arguments":{"amount":10}})
                self.assertFalse(direct.capability_result.is_error, direct)
                loaded = execute(first, {"type":"capability_load", "capability_ids":["mcp.demo.deferred"]})
                contract = json.loads(loaded.followup_context.split("\n",1)[1])["capabilities"][0]
                result = execute(first, {"type":"capability_invoke", "capability_id":"mcp.demo.deferred",
                    "arguments":{"amount":10}})
                self.assertFalse(result.capability_result.is_error, result)
                self.assertEqual([row["name"] for row in calls()], ["resident", "deferred"])
                baseline = published_contracts(first)
                state.write_text('{"unit":"cent"}', encoding="utf-8")
                self.assertTrue(service.discover(profile_user_id="alice", server_id="demo")["ok"])
                frozen = apply_published_contracts(select(), baseline)
                accepted = execute(frozen, {"type":"mcp.demo.resident", "arguments":{"amount":10}})
                self.assertFalse(accepted.capability_result.is_error)
                self.assertEqual(calls()[-1]["unit"], "cent")
                self.assertEqual(len(calls()), 3)
                loaded = execute(frozen, {"type":"capability_load", "capability_ids":["mcp.demo.resident"]})
                current = json.loads(loaded.followup_context.split("\n",1)[1])["capabilities"][0]
                self.assertEqual(current["input_schema"], contract["input_schema"])
                call = {"type":"capability_invoke", "capability_id":"mcp.demo.resident",
                        "arguments":{"amount":10}}
                recovered = execute(frozen, call)
                self.assertFalse(recovered.capability_result.is_error, recovered)
                self.assertEqual(calls()[-1]["unit"], "cent")
                self.assertEqual(first.schema_tool_names, frozen.schema_tool_names)
                # A changed wire input schema follows the same recovery path.
                state.write_text('{"unit":"cent","argument":"quantity"}',encoding="utf-8")
                self.assertTrue(service.discover(profile_user_id="alice", server_id="demo")["ok"])
                self.assertIn("quantity",execute(frozen,call).followup_context)
                self.assertEqual(len(calls()),4)
                loaded = execute(frozen,{"type":"capability_load","capability_ids":["mcp.demo.resident"]})
                current = json.loads(loaded.followup_context.split("\n",1)[1])["capabilities"][0]
                self.assertEqual(current["input_schema"]["required"],["quantity"])
                call = {"type":"capability_invoke","capability_id":"mcp.demo.resident",
                        "arguments":{"quantity":10}}
                updated = execute(frozen,call)
                self.assertFalse(updated.capability_result.is_error,updated)
                self.assertEqual(calls()[-1]["arguments"],{"quantity":10})
                runtime = get_mcp_server_runtime_config(base_dir=root, profile_user_id="alice", server_id="demo")
                revision = manager.contract_revision(profile_user_id="alice", server_id="demo")
                before_restart = select().capability_catalog.snapshot("mcp.demo.resident")["contract_ref"]
                manager.stop_server(profile_user_id="alice", server_id="demo", server_config=runtime)
                self.assertEqual(select().capability_catalog.snapshot("mcp.demo.resident")["contract_ref"], before_restart)
                self.assertFalse(execute(frozen, call).capability_result.is_error)
                with self.assertRaisesRegex(Exception, "executor_runtime_changed"):
                    asyncio.run(manager.acall_tool(profile_user_id="alice", server_id="demo", server_config=runtime,
                        tool_name="resident", arguments={"amount":10}, expected_revision=revision))
                self.assertEqual(len(calls()), 6)
                # A description change discovered on reconnect is compatible;
                # its preflight has not dispatched the action and can continue.
                state.write_text('{"unit":"dollar","argument":"quantity"}', encoding="utf-8")
                manager.stop_server(profile_user_id="alice", server_id="demo", server_config=runtime)
                accepted = execute(frozen, call)
                self.assertFalse(accepted.capability_result.is_error, accepted)
                self.assertEqual(calls()[-1]["unit"], "dollar")
                self.assertEqual(len(calls()), 7)
                loaded = execute(frozen, {"type":"capability_load", "capability_ids":["mcp.demo.resident"]})
                current = json.loads(loaded.followup_context.split("\n",1)[1])["capabilities"][0]
                self.assertNotIn("contract_ref", current)
                self.assertFalse(execute(frozen, call).capability_result.is_error)
                self.assertEqual(calls()[-1]["unit"], "dollar")
                self.assertEqual(len(calls()), 8)
                disabled = service.set_enabled(profile_user_id="alice", server_id="demo", enabled=False)
                self.assertTrue(disabled["ok"], disabled)
                self.assertIn("capability_not_available", execute(frozen, call).followup_context)
                self.assertEqual(len(calls()), 8)
            finally:
                self.assertTrue(manager.close())
