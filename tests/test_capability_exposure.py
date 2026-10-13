from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from companion_v01.capability_exposure import apply_published_contracts, directory_text, published_contracts, route_invocation
from companion_v01.capability_exposure_config import read_preferences, save_preferences
from companion_v01.engine_services.tool_rounds import resolve_capability_selection
from companion_v01.local_capability_config import load_capability_config, write_capability_config
from companion_v01.native_tool_schema import build_openai_native_tool_specs
from companion_v01.tool_handlers.core import ToolExecutionContext
from companion_v01.tool_orchestration_engine import execute_tool_invocation, normalize_tool_invocation
from tests.test_capability_discovery import _DemoHandler


class CountingTool(_DemoHandler):
    policy_accepted_native_tool = True

    def __init__(self, name, *, revision="1"):
        super().__init__(name, name)
        self.contract_revision = revision
        self.calls = []

    def execute(self, *, call, context):
        self.calls.append(dict(call))
        return super().execute(call=call, context=context)


class CapabilityExposureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.alpha, self.beta = CountingTool("demo.alpha"), CountingTool("demo.beta")
        self.engine = SimpleNamespace(tool_handlers={"demo.alpha": self.alpha, "demo.beta": self.beta},
                                      capability_config_base_dir=self.root)
        self.engine._resolve_tool_handlers = lambda capability_selection=None, **_: dict(capability_selection.resolved_handlers)

    def select(self, session="s", character="a"):
        return resolve_capability_selection(self.engine, profile_user_id="alice", session_id=session,
                                             character_pack_id=character)

    def save(self, **changes):
        current = read_preferences(base_dir=self.root, profile_user_id="alice")
        result = save_preferences(base_dir=self.root, profile_user_id="alice", payload={**current, **changes})
        self.assertTrue(result["ok"], result)
        return result

    def execute(self, selection, call):
        if call["type"] == "capability_invoke":
            call, selection = route_invocation(call, selection)
        invocation = normalize_tool_invocation(self.engine, call, profile_user_id="alice", session_id="s", capability_selection=selection)
        return execute_tool_invocation(self.engine, invocation=invocation, profile_user_id="alice", session_id="s",
                                        character_pack_id="a", visual_payload={}, now_ts=1)[0]

    def test_resident_direct_and_deferred_load_invoke_share_real_execution(self):
        self.save(toolModes={"demo.beta": "on_demand"}, searchEnabled=False)
        selection = self.select()
        self.assertIn("demo.alpha", selection.schema_tool_names)
        self.assertNotIn("demo.beta", selection.schema_tool_names)
        self.assertNotIn("capability_search", selection.schema_tool_names)
        undeclared = self.execute(selection, {"type": "demo.beta", "text": "guessed"})
        self.assertFalse(json.loads(undeclared.followup_context)["executed"])
        self.assertEqual(self.beta.calls, [])
        page = json.loads(self.execute(selection, {"type": "capability_list", "limit": 1}).followup_context.split("\n", 1)[1])
        self.assertEqual(len(page["items"]), 1)
        self.assertTrue(page["next_cursor"])
        self.assertIn("demo.beta", directory_text(selection))
        before = build_openai_native_tool_specs(selection.resolved_handlers, allowed_tool_names=selection.schema_tool_names)
        self.assertEqual(self.execute(selection, {"type": "demo.alpha", "text": "direct"}).followup_context, "direct")
        loaded_result = self.execute(selection, {"type": "capability_load", "capability_ids": ["demo.beta"]})
        loaded = json.loads(loaded_result.followup_context.split("\n", 1)[1])["capabilities"][0]
        self.assertEqual(loaded_result.state_updates, {})
        self.assertNotIn("contract_ref", loaded)
        self.assertNotIn("native_schema", loaded)
        call = {"type": "capability_invoke", "capability_id": "demo.beta", "arguments": {"text": "deferred"}}
        self.assertEqual(self.execute(selection, call).followup_context, "deferred")
        after_selection = self.select()
        self.assertEqual(before, build_openai_native_tool_specs(after_selection.resolved_handlers, allowed_tool_names=after_selection.schema_tool_names))
        self.assertEqual(len(self.alpha.calls), 1)
        self.assertEqual(len(self.beta.calls), 1)
        self.assertEqual(self.execute(after_selection, call).followup_context, "deferred")

    def test_compatible_upgrade_uses_current_handler_and_keeps_published_schema(self):
        initial = self.select()
        published = published_contracts(initial)
        updated = CountingTool("demo.alpha", revision="2")
        self.assertEqual(self.alpha.tool_spec(), updated.tool_spec())
        self.engine.tool_handlers["demo.alpha"] = updated
        current = apply_published_contracts(self.select(), published)
        result = self.execute(current, {"type": "demo.alpha", "text": "10"})
        self.assertEqual(result.followup_context, "10")
        self.assertEqual(len(updated.calls), 1)
        self.assertEqual(published_contracts(current), published)
        self.execute(current, {"type": "capability_invoke", "capability_id": "demo.alpha", "arguments": {"text": "10"}})
        self.assertEqual(len(updated.calls), 2)

    def test_identical_contract_rebinds_to_fresh_handler_without_reloading(self):
        initial = self.select()
        published = published_contracts(initial)
        updated = CountingTool("demo.alpha", revision="1")
        self.engine.tool_handlers["demo.alpha"] = updated
        # Even a selection constructed before replacement must resolve the new executor.
        frozen = apply_published_contracts(initial, published)
        self.assertEqual(self.execute(frozen, {"type": "demo.alpha", "text": "new runtime"}).followup_context,
                         "new runtime")
        self.assertEqual(len(updated.calls), 1)
        self.assertEqual(self.alpha.calls, [])
        self.assertEqual(published_contracts(self.select()), published)

    def test_legacy_carrier_reference_is_ignored_but_live_removal_blocks_execution(self):
        selection = self.select()
        call = {"type": "capability_invoke", "capability_id": "demo.alpha", "contract_ref": "old-public-reference", "arguments": {"text": "x"}}
        self.alpha.contract_revision = "2"
        result = self.execute(selection, call)
        self.assertEqual(result.followup_context, "x")
        self.assertEqual(len(self.alpha.calls), 1)
        self.engine.tool_handlers.pop("demo.alpha")
        result = self.execute(selection, {"type": "demo.alpha", "text": "x"})
        self.assertIn("capability_not_available", result.followup_context)
        self.assertEqual(len(self.alpha.calls), 1)

    def test_reference_is_not_authority_or_a_cross_scope_requirement(self):
        selection = self.select()
        for other in (self.select(session="other"), self.select(character="other")):
            result = self.execute(other, {"type": "capability_invoke", "capability_id": "demo.alpha", "arguments": {"text": "x"}})
            self.assertEqual(result.followup_context, "x")
        self.assertEqual(len(self.alpha.calls), 2)

    def test_admitted_call_pins_implementation_but_still_obeys_removal(self):
        selection = self.select()
        bound = selection.resolved_handlers["demo.alpha"].bind_for_execution()
        ctx = ToolExecutionContext(profile_user_id="alice", session_id="s", now_ts=1, visual_payload={})
        admission = bound.admit_execution(call={"type": "demo.alpha", "text": "x"}, context=ctx)
        updated = CountingTool("demo.alpha", revision="2")
        self.engine.tool_handlers["demo.alpha"] = updated
        result = bound.execute_admitted(call=admission.call, context=ctx)
        self.assertEqual(result.followup_context, "x")
        self.assertEqual(len(self.alpha.calls), 1)
        self.assertEqual(updated.calls, [])
        self.engine.tool_handlers.pop("demo.alpha")
        self.assertIn("capability_not_available", bound.execute_admitted(call=admission.call, context=ctx).followup_context)
        self.assertEqual(len(self.alpha.calls), 1)

    def test_preferences_use_cas_and_old_settings_writers_cannot_undo_them(self):
        stale_settings = load_capability_config(base_dir=self.root, profile_user_id="alice")
        saved = self.save(toolModes={"demo.alpha": "on_demand"})
        conflict = save_preferences(base_dir=self.root, profile_user_id="alice", payload={"revision": 0, "toolModes": {}})
        self.assertEqual(conflict["status"], "conflict")
        write_capability_config(base_dir=self.root, profile_user_id="alice", config=stale_settings)
        self.assertEqual(read_preferences(base_dir=self.root, profile_user_id="alice"), saved["preferences"])

    def test_changing_residency_does_not_revoke_valid_calls(self):
        old = self.select()
        self.save(toolModes={"demo.alpha": "on_demand"})
        self.assertEqual(self.execute(old, {"type": "demo.alpha", "text": "still valid"}).followup_context, "still valid")
        self.assertNotIn("demo.alpha", self.select().schema_tool_names)

    def test_concurrent_preferences_have_one_winner_without_lost_update(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        barrier = Barrier(2)
        def save(name):
            barrier.wait(timeout=3)
            return save_preferences(base_dir=self.root,profile_user_id="alice",payload={
                "revision":0,"toolModes":{name:"on_demand"}})
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(save,("demo.alpha","demo.beta")))
        self.assertEqual(sum(result["ok"] for result in results),1)
        winner = next(result for result in results if result["ok"])
        self.assertEqual(read_preferences(base_dir=self.root,profile_user_id="alice"),winner["preferences"])
        self.assertEqual(next(result for result in results if not result["ok"])["status"],"conflict")

    def test_child_ceiling_restricts_directory_load_and_invoke_together(self):
        from companion_v01.engine_services.tool_rounds import restrict_capability_selection
        parent = self.select()
        child = restrict_capability_selection(parent,allowed_tool_names=("demo.alpha","capability_list","capability_load","capability_invoke"))
        result = self.execute(child,{"type":"capability_list"})
        self.assertNotIn("demo.beta",result.followup_context)
        result = self.execute(child,{"type":"capability_load","capability_ids":["demo.beta"]})
        self.assertIn("capability_not_available",result.followup_context)
        result = self.execute(child,{"type":"capability_invoke","capability_id":"demo.beta",
                                     "arguments":{"text":"forbidden"}})
        self.assertEqual(self.beta.calls,[])
        self.assertNotEqual(result.followup_context,"forbidden")

    def test_execution_does_not_hash_catalog_for_admission_or_dispatch(self):
        from unittest.mock import patch
        selection = self.select()
        with patch("companion_v01.capability_discovery.contract_snapshot", side_effect=AssertionError("no execution snapshots")):
            result = self.execute(selection, {"type":"demo.alpha", "text":"one"})
            invoked = self.execute(selection, {"type":"capability_invoke", "capability_id":"demo.alpha", "arguments":{"text":"two"}})
        self.assertEqual(result.followup_context, "one")
        self.assertEqual(invoked.followup_context, "two")

    def test_removed_target_returns_a_paired_rejected_envelope(self):
        selection = self.select()
        self.engine.tool_handlers.pop("demo.alpha")
        invocation = normalize_tool_invocation(self.engine, {"type":"demo.alpha", "text":"gone",
            "_tool_invocation_id":"removed-call"}, profile_user_id="alice", session_id="s", capability_selection=selection)
        result, envelope = execute_tool_invocation(self.engine, invocation=invocation,
            profile_user_id="alice", session_id="s", character_pack_id="a", visual_payload={}, now_ts=1)
        self.assertEqual(envelope.invocation_id, "removed-call")
        self.assertEqual(envelope.status, "rejected")
        self.assertFalse(envelope.data["executed"])
        self.assertTrue(result.capability_result.is_error)
        self.assertEqual(self.alpha.calls, [])

    def test_current_schema_and_risk_apply_while_display_is_frozen(self):
        from unittest.mock import patch
        from capcore import validate_tool_spec_args
        from companion_v01.capability_registry import ExecutorBroker
        original = self.select()
        published = published_contracts(original)

        class CurrentTool(CountingTool):
            def tool_spec(self):
                return replace(super().tool_spec(), risk="high", confirm="always", schema_hash="",
                    input_schema={"type":"object", "additionalProperties":False,
                        "properties":{"text":{"type":"string"}, "count":{"type":"integer"}},
                        "required":["text","count"]})

            def normalize_call(self, value):
                args = self.call_arguments(value)
                return dict(value) if validate_tool_spec_args(self.tool_spec(), args).ok else None

        current = CurrentTool("demo.alpha", revision="2")
        self.engine.tool_handlers["demo.alpha"] = current
        frozen = apply_published_contracts(original, published)
        rejected = self.execute(frozen, {"type":"demo.alpha", "text":"missing count"})
        self.assertIn("count", rejected.followup_context)
        self.assertEqual(current.calls, [])
        broker = ExecutorBroker(None)
        self.engine.executor_broker = broker
        with patch.object(broker, "execute_server_local", wraps=broker.execute_server_local) as dispatch:
            result = self.execute(frozen, {"type":"demo.alpha", "text":"valid", "count":1})
        self.assertEqual(result.followup_context, "valid")
        spec = dispatch.call_args.kwargs["policy_spec"]
        self.assertEqual((spec.risk, spec.confirm), ("high", "always"))
        self.assertEqual(published_contracts(frozen), published)

    def test_public_reference_removal_preserves_same_named_business_argument(self):
        class BusinessTool(CountingTool):
            def tool_spec(self):
                spec = super().tool_spec()
                schema = {**spec.input_schema, "properties":{**spec.input_schema["properties"],
                    "contract_ref":{"type":"string"}}}
                return replace(spec, input_schema=schema, schema_hash="")

            def normalize_call(self, value):
                normalized = super().normalize_call(value)
                if normalized is not None:
                    normalized["contract_ref"] = value["contract_ref"]
                return normalized

        handler = BusinessTool("demo.alpha")
        self.engine.tool_handlers["demo.alpha"] = handler
        result = self.execute(self.select(), {"type":"capability_invoke", "capability_id":"demo.alpha",
            "contract_ref":"obsolete-public-reference", "arguments":{"text":"ok", "contract_ref":"business-17"}})
        self.assertEqual(result.followup_context, "ok")
        self.assertEqual(handler.calls[0]["contract_ref"], "business-17")

    def test_scheduling_uses_current_schema_and_preserves_new_business_background(self):
        original = self.select()

        class BusinessBackground(CountingTool):
            def tool_spec(self):
                spec = super().tool_spec()
                return replace(spec, schema_hash="", input_schema={**spec.input_schema,
                    "properties":{**spec.input_schema["properties"], "background":{"type":"string"}},
                    "required":["text", "background"]})

            def normalize_call(self, value):
                return dict(value)

        current = BusinessBackground("demo.alpha", revision="2")
        self.engine.tool_handlers["demo.alpha"] = current
        result = self.execute(original, {"type":"demo.alpha", "text":"ok",
            "background":"business value", "host_background":False})
        self.assertEqual(result.followup_context, "ok")
        self.assertEqual(current.calls[0]["background"], "business value")
        self.assertNotIn("host_background", current.calls[0])
