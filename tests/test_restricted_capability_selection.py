from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import json
import unittest
from unittest.mock import patch

from companion_v01 import tool_orchestration_engine as orchestration
from companion_v01.capability_approval import CapabilityApprovalStore
from companion_v01.capability_registry import CapabilityDisclosure, ExecutorBroker
from companion_v01.engine_services.tool_rounds import (
    build_mcp_adapter_tool_handlers,
    restrict_capability_selection,
)
from companion_v01.native_tool_schema import build_openai_native_tool_specs, native_tool_model_name_map
from companion_v01.tool_handlers.mcp_management import InvokeMcpToolHandler
from companion_v01.tool_invocation import (
    NATIVE_OPENAI, NATIVE_TOOL_CALL_FIELD, TOOL_CAPABILITY_SELECTION_FIELD,
    TOOL_INVOCATION_ID_FIELD, TOOL_SOURCE_FIELD,
)
from companion_v01.tool_runtime import TOOL_SPEC_BY_TYPE
from tests.test_capability_adapter_mcp_orchestration import build_engine, context, write_profile_config


class RestrictedCapabilitySelectionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.config_patch = patch("config.DATA_DIR", directory.name)
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)
        write_profile_config(self.root, "alice", prompt_exposed=False, allowlist=["echo"])
        self.engine = build_engine(self.root)
        self.engine.tool_handlers["invoke_mcp"] = InvokeMcpToolHandler()
        self.parent = self.engine._resolve_capability_selection(
            client_context=context(), profile_user_id="alice", session_id="s1",
        )
        self.dispatch = build_mcp_adapter_tool_handlers(
            self.engine, profile_user_id="alice", client_context=context(),
        )
        self.alias = next(iter(native_tool_model_name_map(build_openai_native_tool_specs(self.dispatch))))

    def prepare(self, selection, name, **arguments):
        _output, calls, rejections = self.engine._prepare_tool_round_decisions(
            final_output={
                NATIVE_TOOL_CALL_FIELD: {
                    "type": name, **arguments,
                    TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                    TOOL_INVOCATION_ID_FIELD: "call_restricted",
                },
                TOOL_CAPABILITY_SELECTION_FIELD: selection,
            },
            user_message="execute assigned task", client_context=context(),
            profile_user_id="alice", session_id="s1",
        )
        self.assertEqual(rejections, [])
        self.assertEqual(len(calls), 1)
        return calls[0]

    def execute(self, call):
        return orchestration.execute_tool_call(
            self.engine, tool_call=call, profile_user_id="alice", session_id="s1",
            visual_payload={}, now_ts=1, client_context=context(),
        )

    def test_projection_drops_hidden_specs_aliases_receipts_and_hints(self):
        search = self.engine.tool_handlers["web_search"]
        parent = replace(
            self.parent,
            native_tool_names=("web_search", "mcp.demo.echo"),
            native_tool_aliases={"web_search": "web_search", self.alias: "mcp.demo.echo"},
            light_hints=("hidden tool instructions",),
            tool_specs=(TOOL_SPEC_BY_TYPE["web_search"], TOOL_SPEC_BY_TYPE["invoke_mcp"]),
            execution_receipts={"web_search": {"id": "a"}, "invoke_mcp": {"id": "b"}},
            disclosures=(
                CapabilityDisclosure("search", "ready", "Search", tool_names=("web_search",)),
                CapabilityDisclosure("mixed", "ready", "Hidden MCP hint", tool_names=("web_search", "invoke_mcp")),
            ),
        )
        child = restrict_capability_selection(parent, allowed_tool_names=("web_search",))
        self.assertEqual(child.tool_names, ("web_search",))
        self.assertEqual(child.schema_tool_names, ("web_search",))
        self.assertEqual(child.native_tool_names, ("web_search",))
        self.assertEqual(child.native_tool_aliases, {"web_search": "web_search"})
        self.assertEqual(tuple(child.execution_receipts), ("web_search",))
        self.assertEqual([item.capability_id for item in child.tool_specs], ["web_search"])
        self.assertEqual([item.capability_id for item in child.disclosures], ["search"])
        self.assertEqual(child.light_hints, ())
        self.assertIs(child.resolved_handlers["web_search"]._handler, search)
        self.assertIn("invoke_mcp", parent.resolved_handlers)
        self.assertEqual(parent.execution_allowlist, None)
        with self.assertRaises(TypeError):
            child.resolved_handlers["invoke_mcp"] = self.engine.tool_handlers["invoke_mcp"]

    def test_restricting_twice_cannot_expand_or_affect_sibling(self):
        first = restrict_capability_selection(self.parent, allowed_tool_names=("web_search",))
        second = restrict_capability_selection(first, allowed_tool_names=("web_search", "invoke_mcp"))
        sibling = restrict_capability_selection(self.parent, allowed_tool_names=("invoke_mcp",))
        self.assertEqual(second.execution_allowlist, frozenset({"web_search"}))
        self.assertEqual(second.tool_names, first.tool_names)
        self.assertEqual(sibling.tool_names, ("invoke_mcp",))

    def test_known_native_alias_cannot_reintroduce_hidden_mcp(self):
        child = restrict_capability_selection(self.parent, allowed_tool_names=("web_search",))
        call = self.prepare(child, self.alias, text="hello")
        self.assertNotIn("mcp.demo.echo", call[TOOL_CAPABILITY_SELECTION_FIELD].resolved_handlers)
        result = self.execute(call)
        self.assertEqual(result.stream_events[0]["reason"], "tool_not_allowed")

    def test_router_preserves_denial_as_paired_tool_result(self):
        child = restrict_capability_selection(self.parent, allowed_tool_names=("invoke_mcp",))
        call = self.prepare(child, "invoke_mcp", server_id="demo", tool_name="echo", arguments={"text": "hello"})
        self.assertEqual(call["type"], "invoke_mcp")
        self.assertNotIn("mcp.demo.echo", call[TOOL_CAPABILITY_SELECTION_FIELD].resolved_handlers)
        result = self.execute(call)
        self.assertEqual(result.stream_events[0]["reason"], "invoke_mcp_target_not_resolved")
        self.assertNotIn("load_mcp", result.followup_context)

    def test_hidden_router_cannot_route_even_when_target_is_allowed(self):
        child = restrict_capability_selection(self.parent, allowed_tool_names=("mcp.demo.echo",))
        call = self.prepare(child, "invoke_mcp", server_id="demo", tool_name="echo", arguments={"text": "hello"})
        result = self.execute(call)
        self.assertEqual(result.stream_events[0]["reason"], "tool_not_allowed")

    def test_hidden_generic_entry_cannot_route_with_valid_target_contract(self):
        child = restrict_capability_selection(self.parent, allowed_tool_names=("mcp.demo.echo",))
        reference = child.capability_catalog.current_snapshot("mcp.demo.echo")["contract_ref"]
        call = self.prepare(child, "capability_invoke", capability_id="mcp.demo.echo",
                            contract_ref=reference, arguments={"text": "hello"})
        self.assertEqual(call["type"], "capability_invoke")
        self.assertEqual(self.execute(call).stream_events[0]["reason"], "tool_not_allowed")

    def test_allowed_lazy_mcp_keeps_ceiling_without_expanding_schema(self):
        child = restrict_capability_selection(self.parent, allowed_tool_names=("invoke_mcp", "mcp.demo.echo"))
        reference = child.capability_catalog.current_snapshot("mcp.demo.echo")["contract_ref"]
        direct = self.prepare(child, self.alias, text="hello")
        direct_result = self.execute(direct)
        self.assertFalse(json.loads(direct_result.followup_context)["executed"])
        call = self.prepare(child, "invoke_mcp", server_id="demo", tool_name="echo",
                            contract_ref=reference, arguments={"text": "hello"})
        routed = call[TOOL_CAPABILITY_SELECTION_FIELD]
        self.assertEqual(call["type"], "mcp.demo.echo")
        self.assertIn("mcp.demo.echo", routed.resolved_handlers)
        self.assertNotIn("mcp.demo.echo", routed.schema_tool_names)
        self.assertEqual(routed.execution_allowlist, child.execution_allowlist)
        validation = orchestration.validate_legacy_tool_call(
            self.engine, call, client_context=context(), profile_user_id="alice", session_id="s1")
        self.assertTrue(validation.ok, validation.message)
        self.assertEqual(routed.resolved_handlers["mcp.demo.echo"].contract_error(), "")
        self.assertEqual(child.resolved_handlers["mcp.demo.echo"].contract_error(), "capability_contract_stale")

    def test_empty_selection_does_not_fall_back_to_global_alias_discovery(self):
        child = restrict_capability_selection(self.parent, allowed_tool_names=())
        with patch.object(self.engine, "_resolve_tool_handlers", wraps=self.engine._resolve_tool_handlers) as resolver:
            call = self.prepare(child, "guessed_native_alias")
            for invocation in resolver.call_args_list:
                self.assertIsNotNone(invocation.kwargs.get("capability_selection"))
        self.assertEqual(self.execute(call).stream_events[0]["reason"], "tool_not_allowed")

    def test_execution_checks_ceiling_even_if_handler_metadata_is_overbroad(self):
        child = restrict_capability_selection(self.parent, allowed_tool_names=())
        inconsistent = replace(
            child, tool_names=self.parent.tool_names,
            schema_tool_names=self.parent.schema_tool_names,
            resolved_handlers=self.parent.resolved_handlers,
        )
        handler = self.engine.tool_handlers["web_search"]
        with patch.object(handler, "execute", side_effect=AssertionError("hidden handler executed")) as dispatch:
            call = self.prepare(inconsistent, "web_search", query="hello")
            result = self.execute(call)
            self.assertEqual(result.stream_events[0]["reason"], "tool_not_allowed")
            dispatch.assert_not_called()

    def test_allowed_tool_runs_through_normal_broker(self):
        child = restrict_capability_selection(self.parent, allowed_tool_names=("web_search",))
        call = self.prepare(child, "web_search", query="hello")
        broker = ExecutorBroker(None)
        self.engine.executor_broker = broker
        handler = self.engine.tool_handlers["web_search"]
        with patch.object(handler, "execute", wraps=handler.execute) as dispatch:
            first = self.execute(call)
            second = self.execute(call)
        self.assertEqual(first.followup_context, "ok")
        self.assertEqual(second.followup_context, "ok")
        dispatch.assert_called_once()

    def test_task_broker_ledgers_are_separate_but_permission_owner_is_parent(self):
        from companion_v01.tool_handlers.core import TaskExecutionScope
        child = restrict_capability_selection(self.parent, allowed_tool_names=("web_search",))
        call = self.prepare(child, "web_search", query="hello")
        self.engine.executor_broker = ExecutorBroker(None)
        handler = self.engine.tool_handlers["web_search"]
        with patch.object(handler, "execute", wraps=handler.execute) as dispatch:
            for task in ("child-a", "child-b", "child-a"):
                orchestration.execute_tool_call(
                    self.engine, tool_call=call, profile_user_id="alice", session_id="parent",
                    visual_payload={}, now_ts=1, client_context=context(),
                    request_context={"_task_execution_scope": TaskExecutionScope(str(self.root), task)},
                )
        self.assertEqual(dispatch.call_count, 2)
        self.assertEqual(dispatch.call_args.kwargs["context"].session_id, "parent")

    def test_allowlisted_mcp_still_requires_normal_approval(self):
        write_profile_config(self.root, "alice", prompt_exposed=False, risk="high")
        approvals = CapabilityApprovalStore()
        self.engine._get_approval_store = lambda: approvals
        parent = self.engine._resolve_capability_selection(
            client_context=context(), profile_user_id="alice", session_id="s1")
        child = restrict_capability_selection(
            parent, allowed_tool_names=("invoke_mcp", "mcp.demo.echo"),
        )
        reference = child.capability_catalog.current_snapshot("mcp.demo.echo")["contract_ref"]
        call = self.prepare(child, "invoke_mcp", server_id="demo", tool_name="echo",
                            contract_ref=reference, arguments={"text": "hello"})
        result = self.execute(call)
        self.assertEqual(result.stream_events[0]["type"], "capability_approval_required")
        self.assertEqual(approvals.list_requests(profile_user_id="alice")["pendingCount"], 1)


if __name__ == "__main__":
    unittest.main()
