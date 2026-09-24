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
        call = {"type": "capability_invoke", "capability_id": "demo.beta", "contract_ref": loaded["contract_ref"], "arguments": {"text": "deferred"}}
        self.assertEqual(self.execute(selection, call).followup_context, "deferred")
        after_selection = self.select()
        self.assertEqual(before, build_openai_native_tool_specs(after_selection.resolved_handlers, allowed_tool_names=after_selection.schema_tool_names))
        self.assertEqual(len(self.alpha.calls), 1)
        self.assertEqual(len(self.beta.calls), 1)
        self.assertEqual(self.execute(after_selection, call).followup_context, "deferred")

    def test_same_schema_new_semantics_rejects_old_native_then_load_invoke_recovers(self):
        initial = self.select()
        published = published_contracts(initial)
        updated = CountingTool("demo.alpha", revision="2")
        self.assertEqual(self.alpha.tool_spec(), updated.tool_spec())
        self.engine.tool_handlers["demo.alpha"] = updated
        current = apply_published_contracts(self.select(), published)
        failure = self.execute(current, {"type": "demo.alpha", "text": "10"})
        self.assertEqual(json.loads(failure.followup_context)["reason"], "capability_contract_stale")
        self.assertEqual(updated.calls, [])
        loaded = current.capability_catalog.current().load(["demo.alpha"])["capabilities"][0]
        self.execute(current, {"type": "capability_invoke", "capability_id": "demo.alpha", "contract_ref": loaded["contract_ref"], "arguments": {"text": "10"}})
        self.assertEqual(len(updated.calls), 1)
        # Loading the new contract did not change the old native binding.
        self.assertFalse(json.loads(self.execute(current, {"type": "demo.alpha", "text": "10"}).followup_context)["executed"])
        new_boundary = self.select()
        self.assertEqual(self.execute(new_boundary, {"type": "demo.alpha", "text": "10"}).followup_context, "10")
        self.assertEqual(len(updated.calls), 2)

    def test_load_then_upgrade_or_revoke_never_executes_stale_binding(self):
        selection = self.select()
        reference = selection.capability_catalog.load(["demo.alpha"])["capabilities"][0]["contract_ref"]
        call = {"type": "capability_invoke", "capability_id": "demo.alpha", "contract_ref": reference, "arguments": {"text": "x"}}
        self.alpha.contract_revision = "2"
        result = self.execute(selection, call)
        self.assertIn("capability_contract_stale", result.followup_context)
        self.assertEqual(self.alpha.calls, [])
        self.engine.tool_handlers.pop("demo.alpha")
        result = self.execute(selection, {"type": "demo.alpha", "text": "x"})
        self.assertIn("capability_not_available", result.followup_context)
        self.assertEqual(self.alpha.calls, [])

    def test_contract_reference_does_not_cross_session_or_character(self):
        selection = self.select()
        reference = selection.capability_catalog.load(["demo.alpha"])["capabilities"][0]["contract_ref"]
        for other in (self.select(session="other"), self.select(character="other")):
            result = self.execute(other, {"type": "capability_invoke", "capability_id": "demo.alpha", "contract_ref": reference, "arguments": {"text": "x"}})
            self.assertIn("capability_contract_stale", result.followup_context)
        self.assertEqual(self.alpha.calls, [])

    def test_actual_execution_rechecks_after_admission(self):
        selection = self.select()
        bound = selection.resolved_handlers["demo.alpha"]
        ctx = ToolExecutionContext(profile_user_id="alice", session_id="s", now_ts=1, visual_payload={})
        admission = bound.admit_execution(call={"type": "demo.alpha", "text": "x"}, context=ctx)
        self.alpha.contract_revision = "2"
        result = bound.execute_admitted(call=admission.call, context=ctx)
        self.assertIn("capability_contract_stale", result.followup_context)
        self.assertEqual(self.alpha.calls, [])

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
        reference = parent.capability_catalog.load(["demo.beta"])["capabilities"][0]["contract_ref"]
        child = restrict_capability_selection(parent,allowed_tool_names=("demo.alpha","capability_list","capability_load","capability_invoke"))
        result = self.execute(child,{"type":"capability_list"})
        self.assertNotIn("demo.beta",result.followup_context)
        result = self.execute(child,{"type":"capability_load","capability_ids":["demo.beta"]})
        self.assertIn("capability_not_available",result.followup_context)
        result = self.execute(child,{"type":"capability_invoke","capability_id":"demo.beta",
                                     "contract_ref":reference,"arguments":{"text":"forbidden"}})
        self.assertEqual(self.beta.calls,[])
        self.assertNotEqual(result.followup_context,"forbidden")

    def test_contract_ref_changes_with_authorization_actor(self):
        old = self.select()
        reference = old.capability_catalog.load(["demo.alpha"])["capabilities"][0]["contract_ref"]
        other = resolve_capability_selection(self.engine,profile_user_id="alice",session_id="s",
            character_pack_id="a",authorization_profile_user_id="other-actor")
        result = self.execute(other,{"type":"capability_invoke","capability_id":"demo.alpha",
                                     "contract_ref":reference,"arguments":{"text":"forbidden"}})
        self.assertIn("capability_contract_stale",result.followup_context)
        self.assertEqual(self.alpha.calls,[])
