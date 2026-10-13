from __future__ import annotations

from copy import deepcopy
import unittest

from tests import test_tool_exposure_memcore as fixture


class CapabilityCatalogDeltaTests(unittest.TestCase):
    setUp = fixture.ToolExposureMemcoreTests.setUp
    open_manager = fixture.ToolExposureMemcoreTests.open_manager

    def write(self, state, *, client_scope="", text="unchanged full directory", visible=None):
        sources = getattr(self, "sources", [])
        result = self.manager.reconcile_capability_catalog(content=text, capability_state=state,
            visible_source_ids=sources if visible is None else visible, compaction_generation=0,
            anchor_source_id=f"user-{len(sources)}", timestamp=100 + len(sources),
            client_scope=client_scope, **self.scope)
        self.assertTrue(result["ok"], result)
        if result["changed"]:
            self.sources = [*sources, result["source_id"]]
        return result

    def notice(self, result):
        return self.manager._store.get_record_by_source_id(result["source_id"])["semantic_text"]

    def state(self):
        return {"version": 2, "tools": {
            "demo.alpha": {"fingerprint": "a", "mode": "resident", "description": "Alpha manual " * 1000},
            "demo.beta": {"fingerprint": "b", "mode": "on_demand", "description": "Beta purpose"}},
            "skills": {"first": {"description": "First Skill", "revision": "v1", "required_tools": []}},
            "plugin_blocks": "blocks-1"}

    def test_new_skill_is_discoverable_without_repeating_tools_or_other_skills(self):
        state = self.state()
        base = self.write(state, text="Alpha manual\nBeta purpose\nFirst Skill")
        original = self.notice(base)
        state["skills"]["second"] = {"description": "Prepare a paper", "revision": "v1", "required_tools": []}
        result = self.write(state, text="Alpha manual\nBeta purpose\nFirst Skill\nPrepare a paper")
        notice = self.notice(result)
        self.assertIn('load_skill(name="second")', notice)
        self.assertIn("Prepare a paper", notice)
        self.assertNotIn("Alpha", notice)
        self.assertNotIn("First Skill", notice)
        self.assertEqual(self.notice(base), original)
        self.assertFalse(self.write(state)["changed"])

    def test_tool_upgrade_mode_and_removal_only_publish_affected_facts(self):
        state = self.state()
        self.write(state)
        state["tools"]["demo.gamma"] = {"fingerprint": "c", "mode": "on_demand", "description": "Inspect widgets"}
        added = self.notice(self.write(state))
        self.assertIn("demo.gamma", added)
        self.assertIn("Inspect widgets", added)
        self.assertNotIn("demo.alpha", added)
        state["tools"]["demo.gamma"]["fingerprint"] = "c2"
        upgraded = self.notice(self.write(state))
        self.assertIn("契约已更新", upgraded)
        self.assertNotIn("Inspect widgets", upgraded)
        state["tools"]["demo.gamma"]["mode"] = "resident"
        self.assertIn("resident", self.notice(self.write(state)))
        state["tools"].pop("demo.gamma")
        removed = self.notice(self.write(state))
        self.assertIn("demo.gamma：已退出", removed)
        self.assertNotIn("demo.beta", removed)
        self.assertFalse(self.write(state)["changed"])

    def test_prompt_block_and_diagnostic_changes_do_not_republish_directory(self):
        state = self.state()
        self.write(state)
        state["plugin_blocks"] = "blocks-2"
        state["connection_health"] = "offline"
        self.assertFalse(self.write(state, text="full directory with new diagnostic")["changed"])
        state["connection_health"] = "ready"
        self.assertFalse(self.write(state)["changed"])
        self.assertEqual(len(self.sources), 1)

    def test_skill_instruction_change_and_removal_are_not_lost(self):
        state = self.state()
        self.write(state)
        state["skills"]["first"]["revision"] = "v2"
        self.assertIn("Skill first：已更新", self.notice(self.write(state)))
        state["skills"] = {}
        self.assertIn("Skill first：当前作用域不再可加载", self.notice(self.write(state)))

    def test_old_tool_snapshot_does_not_require_a_full_catalog_repost(self):
        state = self.state()
        old = {"tools": {name: {key: value[key] for key in ("fingerprint", "mode")}
                         for name, value in state["tools"].items()}, "plugin_blocks": "old"}
        self.write(old)
        migration = self.notice(self.write(state))
        self.assertIn("Skill first", migration)
        self.assertNotIn("demo.alpha", migration)
        self.assertFalse(self.write(state)["changed"])

    def test_client_catalogs_keep_independent_baselines_and_updates(self):
        state = self.state()
        qq = self.write(state, client_scope="qq_text")
        original = self.notice(qq)
        desktop = self.write(state, client_scope="desktop_pet")
        self.assertEqual(desktop["catalog_role"], "base")
        changed = deepcopy(state)
        changed["skills"] = {}
        update = self.write(changed, client_scope="desktop_pet")
        self.assertEqual(update["catalog_role"], "update")
        self.assertFalse(self.write(state, client_scope="qq_text")["changed"])
        self.assertEqual(self.notice(qq), original)
