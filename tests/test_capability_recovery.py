"""Recovery uses current contracts without broadening the execution scope."""
from copy import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from companion_v01.capability_contracts import contract_failure
from companion_v01.capability_discovery import CapabilityDiscoveryCatalog
from companion_v01.capability_exposure import route_invocation
from companion_v01.capability_exposure_config import save_preferences
from companion_v01.engine_services.tool_rounds import resolve_capability_selection
from companion_v01.tool_orchestration_engine import (
    execute_tool_invocation, normalize_tool_invocation, validate_tool_invocation,
)
from tests.test_capability_exposure import CountingTool
from tests.test_capability_adapter_python_orchestration import build_python_handler, execution_context


class RetainableTool(CountingTool):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.grant = {"active": True, "released": 0}

    def retain_invocation(self):
        return copy(self)

    def invocation_grant_active(self):
        return self.grant["active"]

    def release_invocation(self):
        self.grant["released"] += 1


class CapabilityRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.tool = CountingTool("demo.alpha")
        self.engine = SimpleNamespace(tool_handlers={self.tool.tool_type: self.tool}, capability_config_base_dir=self.root)
        self.engine._resolve_tool_handlers = lambda capability_selection=None, **_: dict(capability_selection.resolved_handlers)
        save_preferences(base_dir=self.root, profile_user_id="alice",
                         payload={"revision": 0, "toolModes": {"demo.alpha": "on_demand"}})
        self.selection = resolve_capability_selection(self.engine, profile_user_id="alice", session_id="s")

    def execute(self, call):
        selection = self.selection
        if call["type"] == "capability_invoke":
            call, selection = route_invocation(call, selection)
        invocation = normalize_tool_invocation(self.engine, call, profile_user_id="alice", session_id="s", capability_selection=selection)
        return execute_tool_invocation(self.engine, invocation=invocation, profile_user_id="alice", session_id="s",
                                       visual_payload={}, now_ts=1)[0]

    def test_mixed_load_preserves_usable_contract_and_can_execute_immediately(self):
        result = self.execute({"type": "capability_load", "capability_ids": ["removed.tool", "demo.alpha", "demo.alpha"]})
        payload = json.loads(result.followup_context.split("\n", 1)[1])
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(result.stream_events[0]["status"], "partial")
        self.assertEqual(payload["missing_capability_ids"], ["removed.tool"])
        self.assertEqual(len(payload["capabilities"]), 1)
        self.assertFalse(self.tool.calls)
        contract = payload["capabilities"][0]
        value = self.execute({"type": "capability_invoke", "capability_id": "demo.alpha",
                              "contract_ref": contract["contract_ref"], "arguments": {"text": "recovered"}})
        self.assertEqual(value.followup_context, "recovered")
        self.assertEqual(len(self.tool.calls), 1)
        self.assertNotIn("demo.alpha", self.selection.schema_tool_names)

    def test_unknown_tool_feedback_does_not_advertise_deferred_ids_as_declared(self):
        invocation = normalize_tool_invocation(self.engine, {"type": "missing.tool"},
                                              capability_selection=self.selection)
        result = validate_tool_invocation(self.engine, invocation)
        self.assertFalse(result.ok)
        self.assertIn("capability_load", result.message)
        self.assertNotIn("demo.alpha", result.message)
        self.assertIn('"executed":false', result.message)
        self.assertFalse(self.tool.calls)
        from companion_v01.tool_orchestration_engine import _unknown_tool_recovery_message
        legacy = _unknown_tool_recovery_message("missing.tool", self.selection.resolved_handlers, None)
        self.assertNotIn("demo.alpha", legacy)
        self.assertIn("capability_load", legacy)

    def test_restricted_catalog_cannot_load_hidden_ids_in_partial_batch(self):
        catalog = CapabilityDiscoveryCatalog({"demo.alpha": self.tool, "hidden.tool": CountingTool("hidden.tool")},
                                             profile_user_id="alice", session_id="s").restricted({"demo.alpha"})
        payload = catalog.current().load(["hidden.tool", "demo.alpha"])
        self.assertEqual([c["capability_id"] for c in payload["capabilities"]], ["demo.alpha"])
        self.assertEqual(payload["missing_capability_ids"], ["hidden.tool"])

    def test_search_miss_and_stale_cursor_supply_recovery_without_changing_search(self):
        catalog = CapabilityDiscoveryCatalog({"demo.alpha": self.tool, "demo.beta": CountingTool("demo.beta")},
                                             profile_user_id="alice", session_id="s")
        miss = catalog.search(query="no such capability")
        self.assertEqual(miss["reason"], "no_matches")
        self.assertIn("capability_list", miss["recovery_hint"])
        cursor = catalog.search(limit=1)["next_cursor"]
        current = catalog.restricted({"demo.alpha"})
        rejected = current.search(cursor=cursor)
        self.assertEqual(rejected["reason"], "cursor_stale")
        self.assertIn("cursor", rejected["recovery_hint"])
        self.assertEqual(len(current.search()["items"]), 1)

    def test_removed_target_does_not_instruct_endless_reload(self):
        payload = json.loads(contract_failure("removed.tool", "capability_not_available").followup_context)
        self.assertFalse(payload["executed"])
        self.assertNotIn("load", payload["recovery"])
        self.assertEqual(payload["recovery"]["list"]["tool"], "capability_list")
        self.assertIn("does not establish that the user revoked permission", payload["recovery"]["hint"])

    def test_adapter_schema_failure_supplies_actual_contract_then_valid_call_succeeds(self):
        handler = build_python_handler("python.akane.normalize_text", config_base_dir=self.root)
        failed = handler.execute(call={"type": handler.tool_type, "arguments": {}}, context=execution_context())
        self.assertTrue(failed.capability_result.is_error)
        self.assertFalse(failed.capability_result.content["executed"])
        self.assertEqual(failed.capability_result.content["input_schema"], handler.tool_spec().input_schema)
        self.assertIn("当前输入契约", failed.followup_context)
        valid = handler.execute(call={"type": handler.tool_type, "arguments": {"text": "  hello   world  "}}, context=execution_context())
        self.assertFalse(valid.capability_result.is_error)

    def test_policy_block_feedback_preserves_reason(self):
        handler = build_python_handler("python.akane.normalize_text", config_base_dir=self.root)
        result = handler._blocked_by_policy("capability_disabled")
        self.assertIn("capability_disabled", result.followup_context)
        self.assertIn("这次调用没有执行", result.followup_context)
        self.assertTrue(result.capability_result.is_error)

    def test_retained_contract_survives_publication_but_not_revocation(self):
        original = RetainableTool("demo.alpha")
        self.engine.tool_handlers["demo.alpha"] = original
        current = resolve_capability_selection(self.engine, profile_user_id="alice", session_id="s")
        contract = current.capability_catalog.load(["demo.alpha"])["capabilities"][0]
        _, routed = route_invocation({"type": "capability_invoke", "capability_id": "demo.alpha",
            "contract_ref": contract["contract_ref"], "arguments": {"text": "old"}}, current)
        binding = routed.resolved_handlers["demo.alpha"]
        retained = binding.retain_invocation()
        updated = RetainableTool("demo.alpha", revision="2")
        self.engine.tool_handlers["demo.alpha"] = updated
        self.assertEqual(binding.contract_error(), "capability_contract_stale")
        result = retained.execute(call={"type": "demo.alpha", "text": "old"}, context=execution_context())
        self.assertEqual(result.followup_context, "old")
        self.assertEqual(len(original.calls), 1)
        self.assertFalse(updated.calls)
        # Retaining an already stale declaration does not make it valid.
        too_late = binding.retain_invocation()
        self.assertEqual(too_late.contract_error(), "capability_contract_stale")
        too_late.release_invocation()
        # Real provider grant withdrawal remains effective during admission.
        original.grant["active"] = False
        self.assertEqual(retained.contract_error(), "capability_not_available")
        retained.release_invocation()
        self.assertEqual(original.grant["released"], 2)

    def test_retained_contract_still_obeys_current_scope_removal(self):
        original = RetainableTool("demo.alpha")
        self.engine.tool_handlers["demo.alpha"] = original
        current = resolve_capability_selection(self.engine, profile_user_id="alice", session_id="s")
        contract = current.capability_catalog.load(["demo.alpha"])["capabilities"][0]
        _, routed = route_invocation({"type": "capability_invoke", "capability_id": "demo.alpha",
            "contract_ref": contract["contract_ref"], "arguments": {"text": "old"}}, current)
        retained = routed.resolved_handlers["demo.alpha"].retain_invocation()
        self.engine.tool_handlers.clear()
        self.assertEqual(retained.contract_error(), "capability_not_available")
        self.assertFalse(original.calls)
        retained.release_invocation()


if __name__ == "__main__":
    unittest.main()
