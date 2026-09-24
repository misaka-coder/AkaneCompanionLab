from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from companion_v01.capability_exposure import freeze_for_projection, published_contracts
from companion_v01.capability_exposure_config import read_preferences, save_preferences
from companion_v01.engine_services.tool_rounds import resolve_capability_selection
from companion_v01.memcore_integration.manager import MemcoreManager
from tests.test_capability_exposure import CountingTool
from tests.test_memcore_integration import _FakeLLM, _FakeEmbeddingProvider


class ToolExposureMemcoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "memory.db"
        self.scope = dict(profile_user_id="alice", session_id="s", character_pack_id="a")
        self.engine = SimpleNamespace(tool_handlers={"demo.alpha": CountingTool("demo.alpha")},
            capability_config_base_dir=Path(self.temp.name))
        self.manager = self.open_manager()
        self.addCleanup(lambda: self.manager.close())

    def open_manager(self):
        manager = MemcoreManager(backend="memcore", storage_path=self.path,
            visible_scope="conversation", enable_flavor=True, shadow_compare=False,
            llm=_FakeLLM(), embedding_provider=_FakeEmbeddingProvider())
        self.engine.memcore_manager = manager
        return manager

    def freeze(self, blocks=(), scope=None):
        scope = scope or self.scope
        selection = resolve_capability_selection(self.engine, **scope)
        projection = self.manager.build_context_projection(provider_profile="openai", **scope)
        self.assertTrue(projection["ok"], projection)
        return freeze_for_projection(self.engine, selection, projection, plugin_blocks=blocks, **scope)

    def test_real_store_restores_published_declarations_across_host_restart(self):
        original, blocks, receipt = self.freeze(("old rule",))
        self.assertTrue(receipt["ok"], receipt)
        self.engine.tool_handlers["demo.beta"] = CountingTool("demo.beta")
        changed, _, pending = self.freeze(("old rule", "new rule"))
        self.assertTrue(pending["pending"])
        self.assertEqual(original.schema_tool_names, changed.schema_tool_names)
        self.assertNotIn("demo.beta", changed.schema_tool_names)
        self.manager.close()
        self.manager = self.open_manager()
        restored, restored_blocks, receipt = self.freeze(("old rule", "new rule"))
        self.assertTrue(receipt["ok"], receipt)
        self.assertEqual(restored.schema_tool_names, original.schema_tool_names)
        self.assertEqual(restored_blocks, ("old rule",))
        # These provenance records never enter prompt history or retrieval.
        projection = self.manager.build_context_projection(provider_profile="openai", **self.scope)
        self.assertNotIn("Published tool declarations", str(projection["payloads"]))

    def test_new_session_gets_new_baseline_old_session_remains_pending(self):
        original, _, _ = self.freeze()
        self.engine.tool_handlers["demo.beta"] = CountingTool("demo.beta")
        old, _, receipt = self.freeze()
        new, _, _ = self.freeze(scope={**self.scope, "session_id": "new"})
        self.assertEqual(old.schema_tool_names, original.schema_tool_names)
        self.assertTrue(receipt["pending"])
        self.assertIn("demo.beta", new.schema_tool_names)

    def test_preferences_publish_next_request_and_survive_restart_without_refreshing_system(self):
        initial, _, first = self.freeze(("old rule",))
        def save(**changes):
            prefs = read_preferences(base_dir=self.engine.capability_config_base_dir, profile_user_id="alice")
            result = save_preferences(base_dir=self.engine.capability_config_base_dir, profile_user_id="alice",
                payload={**prefs, **changes})
            self.assertTrue(result["ok"], result)
        save(toolModes={"demo.alpha": "on_demand"}, searchEnabled=False)
        deferred, blocks, receipt = self.freeze(("old rule", "new rule"))
        self.assertEqual(receipt["compaction_generation"], first["compaction_generation"])
        self.assertNotIn("demo.alpha", deferred.schema_tool_names)
        self.assertNotIn("capability_search", deferred.schema_tool_names)
        self.assertEqual(blocks, ("old rule",))
        self.manager.close()
        self.manager = self.open_manager()
        restored, _, _ = self.freeze(("old rule", "new rule"))
        self.assertEqual(restored.published_contracts, deferred.published_contracts)
        # Quick toggles use the latest saved version, including a return to an old mode.
        save(toolModes={"demo.alpha": "resident"}, searchEnabled=True)
        save(toolModes={"demo.alpha": "on_demand"})
        save(toolModes={"demo.alpha": "resident"})
        resident, blocks, receipt = self.freeze(("old rule", "new rule"))
        self.assertEqual(resident.published_contracts, initial.published_contracts)
        self.assertEqual(receipt["compaction_generation"], first["compaction_generation"])
        self.assertEqual(blocks, ("old rule",))
        self.assertTrue(receipt["pending"])  # Only the new plugin rule remains pending.

    def test_noop_save_does_not_publish_pending_installation(self):
        initial, _, _ = self.freeze()
        self.engine.tool_handlers["demo.beta"] = CountingTool("demo.beta")
        prefs = read_preferences(base_dir=self.engine.capability_config_base_dir, profile_user_id="alice")
        saved = save_preferences(base_dir=self.engine.capability_config_base_dir, profile_user_id="alice", payload=prefs)
        self.assertEqual(saved["preferences"]["revision"], prefs["revision"])
        after, _, receipt = self.freeze()
        self.assertEqual(after.published_contracts, initial.published_contracts)
        self.assertTrue(receipt["pending"])

    def test_pure_prompt_revocation_removes_old_system_rule_immediately(self):
        self.freeze(("rule A", "rule B"))
        _, blocks, receipt = self.freeze(("rule A", "new rule C"))
        self.assertEqual(blocks, ("rule A",))
        self.assertTrue(receipt["system_revocation_exception"])

    def test_fake_generation_cannot_replace_real_baseline(self):
        selection, _, _ = self.freeze()
        result = self.manager.resolve_tool_exposure_snapshot(snapshot={"contracts": {}, "plugin_blocks": []},
            scope=selection.capability_catalog.scope, compaction_generation=99, **self.scope)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "compaction_generation_changed")
        after, _, receipt = self.freeze()
        self.assertTrue(receipt["ok"])
        self.assertEqual(after.schema_tool_names, selection.schema_tool_names)

    def test_no_change_and_net_zero_keep_exact_snapshot(self):
        initial, _, _ = self.freeze()
        expected = deepcopy(initial.published_contracts)
        self.engine.tool_handlers["demo.beta"] = CountingTool("demo.beta")
        self.freeze()
        self.engine.tool_handlers.pop("demo.beta")
        final, _, receipt = self.freeze()
        self.assertEqual(final.published_contracts, expected)
        self.assertFalse(receipt["pending"])

    def test_concurrent_first_requests_share_one_published_snapshot(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        selection = resolve_capability_selection(self.engine,**self.scope)
        projection = self.manager.build_context_projection(provider_profile="openai",**self.scope)
        barrier = Barrier(2)
        def publish(rule):
            barrier.wait(timeout=3)
            return self.manager.resolve_tool_exposure_snapshot(snapshot={"contracts":{},"plugin_blocks":[rule]},
                scope=selection.capability_catalog.scope,compaction_generation=projection["compaction_generation"],**self.scope)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(publish,("rule-a","rule-b")))
        self.assertTrue(all(result["ok"] for result in results),results)
        self.assertEqual(sorted(result["status"] for result in results),["created","restored"])
        self.assertEqual(results[0]["snapshot"],results[1]["snapshot"])

    def test_text_only_baseline_cannot_poison_tool_enabled_context(self):
        projection = self.manager.build_context_projection(provider_profile="openai",**self.scope)
        _,_,receipt = freeze_for_projection(self.engine,None,projection,plugin_blocks=("text rule",),**self.scope)
        self.assertTrue(receipt["ok"],receipt)
        selected,blocks,_ = self.freeze(("tool rule",))
        self.assertIn("demo.alpha",selected.schema_tool_names)
        self.assertEqual(blocks,("tool rule",))
